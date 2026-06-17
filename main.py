from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import asyncio
import contextlib
import shutil
import stat
import threading
import time
import random
import re
import json
import hashlib
import os
import platform
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import PurePosixPath

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, delete, text

from logging_config import ensure_log_file, get_logger
from rcs_client import RcsClient, RcsError, RcsTaskRequest, RcsTaskResponse
from monitor_service import TaskMonitor, build_auto_status_query_payload, generate_req_code_ms, apply_remote_status_to_order

from fifo_service import (
    build_fifo_preview_payload,
    execute_fifo_request,
    execute_direct_move_request,
    resolve_fifo_request,
    simulate_order_completed,
    undo_movement_order,
    _find_destination_cell,
)
from models import (
    Area,
    BG_BASENAME,
    Base,
    DB_URL,
    DB_GRID_H,
    DB_GRID_W,
    DebugConsoleEvent,
    Location,
    MaterialGroup,
    MovementOrder,
    Rack,
    SessionLocal,
    Setting,
    OperatorWindow,
    OperatorWindowButton,
    UPLOAD_DIR,
    apply_rack_reservation_status,
    engine,
    rack_status_from_reservation,
    rack_status_is_available,
    rack_status_is_reserved,
)




RUNTIME_BROADCAST_INTERVAL_SECONDS = 1.0
ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS = 5.0
NO_MATERIAL_CODE = "SIN_MATERIAL"
NO_MATERIAL_NAME = "Sin material"
NO_MATERIAL_COLOR = "#94a3b8"
RACK_RESERVATION_ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo")
logger = get_logger("app.main")
SOFTWARE_UPDATE_LOCK = threading.Lock()
SOFTWARE_BUILD_INFO_REL_PATH = "static/build_info.json"
RACK_POSITION_POLL_INTERVAL_SECONDS = 10
RACK_POSITION_POLL_TIMEOUT_SECONDS = 10 * 60
_rack_position_poll_lock = threading.Lock()
_rack_position_poll_active: set[tuple[int, int]] = set()
CANCEL_REQUEST_STATUSES = {"cancel_requested_total", "cancel_requested_undo"}
CANCEL_RETURN_RCS_TERMINAL_STATUSES = {"cancelled", "completed"}
CLEANUP_RCS_TERMINAL_STATUS_MAP = {
    "completed": "completed",
    "done": "completed",
    "success": "completed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failed",
    "aborted": "failed",
    "terminated": "failed",
}


def _app_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))
STUCK_CANCEL_RETURN_AUTO_RELEASE_INTERVAL_SECONDS = 30
_stuck_cancel_return_auto_release_lock = threading.Lock()
_stuck_cancel_return_auto_release_last_ts = 0.0


class RuntimeWebSocketManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast_json(self, payload: dict):
        async with self._lock:
            targets = list(self._connections)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def count(self) -> int:
        async with self._lock:
            return len(self._connections)


runtime_ws_manager = RuntimeWebSocketManager()
_runtime_snapshot_state_lock = threading.Lock()
_runtime_snapshot_last_hash = ""
_robot_monitor_cache_lock = threading.Lock()
_robot_monitor_cache = {"timestamp": 0.0, "payload": None}

class LocationPatchAdmin(BaseModel):
    status: Optional[int] = Field(default=None, description="0 libre, 1 ocupado")
    is_visible: Optional[int] = Field(default=None, description="1 visible, 0 oculta")
    code: Optional[str] = Field(default=None, max_length=64)
    enabled: Optional[int] = Field(default=None, description="1 habilitada, 0 deshabilitada")
    note: Optional[str] = Field(default=None, max_length=512)
    area_id: Optional[int] = Field(default=None)
    rack_id: Optional[int] = Field(default=None)


class LocationFreeLayoutPatch(BaseModel):
    free_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    free_x: Optional[float] = Field(default=None, ge=-1000000.0, le=1000000.0)
    free_y: Optional[float] = Field(default=None, ge=-1000000.0, le=1000000.0)
    free_w: Optional[float] = Field(default=None, gt=1.0, le=1000000.0)
    free_h: Optional[float] = Field(default=None, gt=1.0, le=1000000.0)


class LocationFreeCreate(BaseModel):
    code: Optional[str] = Field(default=None, max_length=64)
    display_rows: Optional[int] = Field(default=None, ge=1, le=DB_GRID_H)
    display_cols: Optional[int] = Field(default=None, ge=1, le=DB_GRID_W)
    free_x: float = Field(default=0.0, ge=-1000000.0, le=1000000.0)
    free_y: float = Field(default=0.0, ge=-1000000.0, le=1000000.0)
    free_w: float = Field(default=22.0, gt=1.0, le=1000000.0)
    free_h: float = Field(default=22.0, gt=1.0, le=1000000.0)


class LocationFreeLayoutInitIn(BaseModel):
    pitch: float = Field(default=24.0, gt=1.0, le=10000.0)
    cell_size: float = Field(default=22.0, gt=1.0, le=10000.0)
    only_missing: int = Field(default=1, ge=0, le=1)


class LocationOut(BaseModel):
    id: int
    x: int
    y: int
    status: int
    is_visible: int
    updated_at: datetime
    code: Optional[str] = None
    enabled: int = 1
    note: Optional[str] = None
    area_id: Optional[int] = None
    rack_id: Optional[int] = None
    free_enabled: int = 0
    free_x: Optional[float] = None
    free_y: Optional[float] = None
    free_w: Optional[float] = None
    free_h: Optional[float] = None
    area_name: Optional[str] = None
    rack_code: Optional[str] = None
    reservation_status: str = "No reservado"
    reservation_task_id: Optional[int] = None
    reservation_task_identifier: Optional[str] = None
    reservation_rack_id: Optional[int] = None
    reservation_rack_code: Optional[str] = None


class AdminLogin(BaseModel):
    password: str


class AdminChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=1, max_length=128)


class GridDisplayConfig(BaseModel):
    display_rows: int = Field(ge=1, le=DB_GRID_H)
    display_cols: int = Field(ge=1, le=DB_GRID_W)
    map_layout_mode: str = Field(default="grid", max_length=16)
    agv_overlay_scale_x: float = Field(default=1.0, ge=-1000000.0, le=1000000.0)
    agv_overlay_scale_y: float = Field(default=1.0, ge=-1000000.0, le=1000000.0)
    agv_overlay_offset_x: float = Field(default=0.0, ge=-1000000.0, le=1000000.0)
    agv_overlay_offset_y: float = Field(default=0.0, ge=-1000000.0, le=1000000.0)
    agv_overlay_rotation_deg: float = Field(default=0.0, ge=-3600.0, le=3600.0)
    agv_orientation_offset_deg: float = Field(default=0.0, ge=-3600.0, le=3600.0)
    agv_overlay_mirror_x: int = Field(default=0, ge=0, le=1)
    agv_overlay_mirror_y: int = Field(default=0, ge=0, le=1)
    agv_icon_angle_mirror: int = Field(default=0, ge=0, le=1)
    runtime_refresh_seconds: float = Field(default=5.0, ge=2.0, le=120.0)
    runtime_reconnect_seconds: float = Field(default=3.0, ge=1.0, le=60.0)

    @field_validator("map_layout_mode")
    @classmethod
    def validate_map_layout_mode(cls, value: str) -> str:
        normalized = (value or "grid").strip().lower()
        if normalized not in {"grid", "free"}:
            raise ValueError("map_layout_mode debe ser grid o free")
        return normalized


class BackgroundOut(BaseModel):
    filename: str
    url: Optional[str] = None
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    scale: Optional[float] = None


class BackgroundTransformIn(BaseModel):
    scale_x: float = Field(gt=0.05, lt=20.0)
    scale_y: float = Field(gt=0.05, lt=20.0)
    offset_x: float = Field(ge=-1000.0, le=1000.0)
    offset_y: float = Field(ge=-1000.0, le=1000.0)


class ClientIPOut(BaseModel):
    client_ip: str


class ClientIPIn(BaseModel):
    client_ip: str = Field(min_length=0, max_length=64)


class RcsConfigIn(BaseModel):
    base_url: str = Field(default="", max_length=512)
    create_task_endpoint: str = Field(default="/rcs/task/create", max_length=256)
    query_task_status_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/queryTaskStatus", max_length=256)
    cancel_task_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/cancelTask", max_length=256)
    stop_robot_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/stopRobot", max_length=256)
    resume_robot_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/resumeRobot", max_length=256)
    agv_status_endpoint: str = Field(default="/rcms-dps/rest/queryAgvStatus", max_length=256)
    pod_position_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/queryPodPosition", max_length=256)
    task_monitor_interval_seconds: float = Field(default=3.0, ge=0.2, le=3600.0)
    agv_monitor_interval_seconds: float = Field(default=5.0, ge=0.2, le=3600.0)
    enable_map_short_name: int = Field(default=1, ge=0, le=1)
    map_short_name: str = Field(default="AA", max_length=64)
    enable_map_code: int = Field(default=0, ge=0, le=1)
    map_code: str = Field(default="", max_length=128)
    enable_amr_monitor: int = Field(default=1, ge=0, le=1)
    verify_tls: int = Field(default=0, ge=0, le=1)
    token_code: str = Field(default="", max_length=512)
    auth_header: str = Field(default="", max_length=512)
    cleanup_min_age_minutes: int = Field(default=30, ge=1, le=10080)
    force_release_min_age_minutes: int = Field(default=20, ge=1, le=10080)
    cancel_undo_auto_recovery_enabled: int = Field(default=1, ge=0, le=1)
    cancel_undo_auto_recovery_min_age_minutes: int = Field(default=5, ge=1, le=10080)

    @field_validator(
        "task_monitor_interval_seconds",
        "agv_monitor_interval_seconds",
        mode="before",
    )
    @classmethod
    def _coerce_float_fields(cls, v, info):
        defaults = {
            "task_monitor_interval_seconds": 3.0,
            "agv_monitor_interval_seconds": 5.0,
        }
        if v is None or v == "":
            return defaults.get(info.field_name, 0.0)
        try:
            return float(v)
        except Exception:
            return defaults.get(info.field_name, 0.0)

    @field_validator(
        "enable_map_short_name",
        "enable_map_code",
        "enable_amr_monitor",
        "verify_tls",
        mode="before",
    )
    @classmethod
    def _coerce_int_flag_fields(cls, v, info):
        defaults = {
            "enable_map_short_name": 1,
            "enable_map_code": 0,
            "enable_amr_monitor": 1,
            "verify_tls": 0,
        }
        if isinstance(v, bool):
            return 1 if v else 0
        if v is None or v == "":
            return defaults.get(info.field_name, 0)
        if isinstance(v, str):
            text = v.strip().lower()
            if text in {"true", "yes", "si", "sí", "on"}:
                return 1
            if text in {"false", "no", "off"}:
                return 0
        try:
            return 1 if int(float(v)) else 0
        except Exception:
            return defaults.get(info.field_name, 0)


class RcsConfigOut(RcsConfigIn):
    resolved_base_url: str = ""
    resolved_token_code: str = ""
    resolved_auth_header: str = ""


class CleanupCloseOrdersOut(BaseModel):
    ok: bool
    closed_orders: List[dict] = []
    skipped_orders: List[dict] = []
    released_racks: List[dict] = []
    kept_locations: int = 0
    diagnosis: dict = {}


class CleanupSelectionIn(BaseModel):
    order_ids: List[int] = Field(default_factory=list)
    rack_ids: List[int] = Field(default_factory=list)


class TestCreateOldActiveOrderIn(BaseModel):
    rack_id: int = Field(gt=0)


class CleanupResolveInconsistentRacksOut(BaseModel):
    ok: bool
    closed_orders: List[dict] = []
    released_racks: List[dict] = []
    skipped: List[dict] = []
    diagnosis: dict = {}


class CleanupHealthOut(BaseModel):
    total_reserved_racks: int
    total_orphans: int
    inconsistent_orders: int
    last_cleanup: Optional[dict] = None
    integrity_check: List[str] = []


class ForceReleaseOldActiveRacksOut(BaseModel):
    ok: bool
    closed_orders: List[dict] = []
    released_racks: List[dict] = []
    skipped: List[dict] = []
    diagnosis: dict = {}


class TestCreateOldActiveOrderOut(BaseModel):
    ok: bool
    message: str
    order_id: int
    rack_id: int
    diagnosis: dict = {}


class SoftwareUpdateValidationOut(BaseModel):
    ok: bool
    staging_id: str = ""
    staging_dir: str = ""
    max_size_mb: int = 200
    detected_files: List[str] = []
    blocked_files: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []


class SoftwareUpdateApplyIn(BaseModel):
    staging_id: Optional[str] = None


class SoftwareUpdateApplyOut(BaseModel):
    ok: bool
    applied_files: List[str] = []
    skipped_files: List[str] = []
    backup_path: str = ""
    build_info: dict = {}
    rollback: bool = False
    warnings: List[str] = []
    errors: List[str] = []


class SoftwareUpdateRestartOut(BaseModel):
    ok: bool
    mode: str = ""
    configured_mode: str = ""
    detected_mode: str = ""
    os: str = ""
    service: str = ""
    command: str = ""
    message: str = ""
    diagnostics: dict = {}
    errors: List[str] = []


class RcsConfigTestOut(BaseModel):
    ok: bool
    message: str
    resolved_base_url: str = ""
    resolved_endpoint: str = ""
    resolved_query_endpoint: str = ""
    resolved_cancel_endpoint: str = ""
    resolved_stop_endpoint: str = ""
    resolved_resume_endpoint: str = ""
    resolved_agv_status_endpoint: str = ""
    resolved_pod_position_endpoint: str = ""
    verify_tls: bool = False
    has_token_code: bool = False
    has_auth_header: bool = False

class OperatorWindowIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1, max_length=128)
    bg_color: str = Field(default="#0f2747", min_length=4, max_length=32)
    button_count: int = Field(default=1, ge=1, le=24)
    password: Optional[str] = Field(default=None, max_length=128)
    is_active: int = Field(default=1, ge=0, le=1)


class OperatorWindowButtonIn(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    color: str = Field(default="#1f4b99", min_length=4, max_length=32)
    is_active: int = Field(default=1, ge=0, le=1)
    action_mode: str = Field(default="fifo", min_length=1, max_length=32)
    source_area_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    material_group_id: Optional[int] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    priority: str = Field(default="normal", min_length=1, max_length=32)
    agv_code: Optional[str] = Field(default=None, max_length=128)
    task_typ: Optional[str] = Field(default="A01", max_length=64)
    comment: Optional[str] = Field(default=None, max_length=512)
    cancel_matter_area: Optional[str] = Field(default=None, max_length=128)
    point_visible_material_ids: List[int] = []
    point_custom_fields: List[dict] = []


class OperatorWindowButtonOut(OperatorWindowButtonIn):
    id: int
    window_id: int
    button_index: int
    updated_at: datetime
    source_area_name: Optional[str] = None
    destination_area_name: Optional[str] = None
    material_group_name: Optional[str] = None
    source_cell_label: Optional[str] = None
    destination_cell_label: Optional[str] = None


class OperatorWindowOut(BaseModel):
    id: int
    name: str
    bg_color: str
    button_count: int
    is_active: int
    requires_password: bool
    updated_at: datetime
    buttons: List[OperatorWindowButtonOut] = []


class OperatorWindowAccessIn(BaseModel):
    password: str = Field(default="", max_length=128)


class OperatorWindowActionIn(OperatorWindowAccessIn):
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    material_group_id: Optional[int] = None
    lot: Optional[str] = Field(default=None, max_length=128)
    quantity: Optional[int] = Field(default=None, ge=0, le=999999)
    manufacturer_code: Optional[str] = Field(default=None, max_length=128)
    comment: Optional[str] = Field(default=None, max_length=512)
    custom_field_values: List[dict] = []
    agv_code: Optional[str] = Field(default=None, max_length=128)
    task_typ: Optional[str] = Field(default=None, max_length=64)


class OperatorWindowPreviewOut(BaseModel):
    ok: bool
    action_mode: str
    message: str
    summary: dict
    payload: dict



class AreaIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    matter_area: Optional[str] = Field(default=None, max_length=128)
    color: str = Field(default="#4f46e5", min_length=4, max_length=32)
    area_type: str = Field(default="almacen", min_length=1, max_length=64)
    is_active: int = Field(default=1, ge=0, le=1)
    priority: int = Field(default=0, ge=0, le=9999)


class AreaOut(AreaIn):
    id: int
    updated_at: datetime


class MovementOrderUndoIn(BaseModel):
    return_area_id: Optional[int] = None
    matter_area: Optional[str] = Field(default=None, max_length=128)
    return_to_area: bool = True


class MaterialGroupIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    color: Optional[str] = Field(default=None, max_length=32)
    is_active: int = Field(default=1, ge=0, le=1)


class MaterialGroupOut(MaterialGroupIn):
    id: int
    updated_at: datetime


class RackIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    status: str = Field(default="disponible", min_length=1, max_length=32)
    material_group_id: Optional[int] = None
    lot: Optional[str] = Field(default=None, max_length=128)
    manufacturer_code: Optional[str] = Field(default=None, max_length=128)
    quantity: int = Field(default=0, ge=0, le=999999)
    comment: Optional[str] = Field(default=None, max_length=512)
    fifo_entered_at: Optional[datetime] = None
    last_moved_at: Optional[datetime] = None
    custom_fields: List[dict] = []


class RackOut(RackIn):
    id: int
    updated_at: datetime
    material_group_name: Optional[str] = None
    location_x: Optional[int] = None
    location_y: Optional[int] = None
    area_id: Optional[int] = None
    area_name: Optional[str] = None
    reservation_status: str = "No reservado"
    reservation_task_id: Optional[int] = None
    reservation_task_identifier: Optional[str] = None


class RackReservationIn(BaseModel):
    reserved: int = Field(default=0, ge=0, le=1)


class CatalogOut(BaseModel):
    areas: List[AreaOut]
    materials: List[MaterialGroupOut]
    racks: List[RackOut]


class CellDetailSave(BaseModel):
    code: Optional[str] = Field(default=None, max_length=64)
    enabled: int = Field(default=1, ge=0, le=1)
    status: int = Field(default=0, ge=0, le=1)
    is_visible: int = Field(default=1, ge=0, le=1)
    note: Optional[str] = Field(default=None, max_length=512)
    area_id: Optional[int] = None
    rack_id: Optional[int] = None


class FifoRequestIn(BaseModel):
    source_area_id: int
    destination_area_id: int
    material_group_id: Optional[int] = None
    priority: str = Field(default="normal", min_length=1, max_length=32)
    agv_code: Optional[str] = Field(default=None, max_length=128)
    task_typ: Optional[str] = Field(default="A01", max_length=64)
    comment: Optional[str] = Field(default=None, max_length=512)
    created_by: Optional[str] = Field(default="operador", max_length=128)

    @field_validator("material_group_id", mode="before")
    @classmethod
    def normalize_material_group_id(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            text = v.strip().lower()
            if text in {"", "none", "null", "undefined"}:
                return None
            try:
                return int(float(text))
            except Exception:
                return None
        try:
            return int(v)
        except Exception:
            return None


class FifoPreviewOut(BaseModel):
    validation_ok: bool
    message: str
    rack: dict
    material: dict
    source_area: dict
    destination_area: dict
    source_cell: dict
    destination_cell: dict


class DirectMoveRequestIn(BaseModel):
    source_cell_id: int
    destination_cell_id: int
    priority: str = Field(default="normal", min_length=1, max_length=32)
    agv_code: Optional[str] = Field(default=None, max_length=128)
    task_typ: Optional[str] = Field(default="A01", max_length=64)
    comment: Optional[str] = Field(default=None, max_length=512)
    created_by: Optional[str] = Field(default="operador", max_length=128)


class MovementOrderOut(BaseModel):
    order_id: int
    order_code: str
    order_type: str
    status: str
    priority: str
    agv_code: Optional[str] = None
    task_typ: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None
    rack_id: int
    rack_code: str
    material_group_id: int
    material_group_name: Optional[str] = None
    source_area_id: int
    source_area_name: Optional[str] = None
    destination_area_id: int
    destination_area_name: Optional[str] = None
    source_cell_id: int
    destination_cell_id: int
    source_cell: dict
    destination_cell: dict
    current_cell: Optional[dict] = None
    dispatch_status: str = "not_sent"
    remote_task_code: Optional[str] = None
    req_code: Optional[str] = None
    rcs_status: Optional[str] = None
    rcs_message: Optional[str] = None
    cancel_source: Optional[str] = None
    cancel_reason: Optional[str] = None
    closed_by: Optional[str] = None
    closed_at: Optional[datetime] = None
    release_source: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    rcs_last_update: Optional[datetime] = None
    status_query_checked_at: Optional[datetime] = None
    status_query_request_payload: Optional[dict] = None
    status_query_response_payload: Optional[dict] = None
    status_query_log_entries: List[dict] = []
    can_simulate_complete: bool = False
    can_undo: bool = False




class MovementOrderJsonIn(BaseModel):
    payload: dict


class MovementOrderJsonOut(BaseModel):
    order_id: int
    order_code: str
    payload: dict
    source: str = "generated"


class MovementOrderDispatchOut(BaseModel):
    order_id: int
    order_code: str
    dispatch_status: str
    request_payload: Optional[dict] = None
    response_payload: Optional[dict] = None
    remote_task_code: Optional[str] = None
    rcs_message: Optional[str] = None
    dispatched_at: Optional[datetime] = None


class MovementOrderStatusQueryIn(BaseModel):
    payload: dict
    base_url: Optional[str] = Field(default=None, max_length=512)
    endpoint: Optional[str] = Field(default=None, max_length=256)


class MonitorRunIn(BaseModel):
    base_url: Optional[str] = Field(default=None, max_length=512)
    endpoint: Optional[str] = Field(default=None, max_length=256)


class DebugConsoleSendIn(BaseModel):
    payload: dict
    base_url: Optional[str] = Field(default=None, max_length=512)
    endpoint: Optional[str] = Field(default=None, max_length=256)


class DebugConsoleEventOut(BaseModel):
    id: int
    direction: str
    module: str
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    payload: Optional[dict] = None
    message: Optional[str] = None
    created_at: datetime


class RuntimeSnapshotOut(BaseModel):
    orders: List[MovementOrderOut] = []
    locations: List[LocationOut] = []
    debug_log: List[DebugConsoleEventOut] = []
    robot_monitor: Optional["RobotMonitorResponse"] = None


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()



def get_setting(db, key: str, default: str) -> str:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return row.value if row else default



def set_setting(db, key: str, value: str):
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if not row:
        row = Setting(key=key, value=value, updated_at=datetime.utcnow())
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
        db.add(row)
    db.commit()



def validate_xy(x: int, y: int):
    if not (0 <= x < DB_GRID_W and 0 <= y < DB_GRID_H):
        raise HTTPException(status_code=400, detail="Coordenadas fuera de rango 0..99")



def cleanup_legacy_schema():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS tasks;")

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "locations" not in tables:
            return

        cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(locations);").fetchall()]
        desired_min = ["id", "x", "y", "status", "is_visible", "updated_at"]
        if cols[:6] != desired_min:
            conn.exec_driver_sql("DROP TABLE IF EXISTS locations_new;")
            has_status = "status" in cols
            has_is_visible = "is_visible" in cols
            has_updated_at = "updated_at" in cols
            conn.exec_driver_sql(
                """
                CREATE TABLE locations_new (
                    id INTEGER PRIMARY KEY,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    status INTEGER NOT NULL DEFAULT 0,
                    is_visible INTEGER NOT NULL DEFAULT 1,
                    updated_at DATETIME NOT NULL,
                    code VARCHAR(64),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note VARCHAR(512),
                    area_id INTEGER,
                    rack_id INTEGER UNIQUE,
                    CONSTRAINT uq_xy UNIQUE (x, y)
                );
                """
            )
            status_expr = "CASE WHEN COALESCE(status, 0) > 0 THEN 1 ELSE 0 END" if has_status else "0"
            visible_expr = "CASE WHEN COALESCE(is_visible, 1) = 0 THEN 0 ELSE 1 END" if has_is_visible else "1"
            updated_expr = "COALESCE(updated_at, CURRENT_TIMESTAMP)" if has_updated_at else "CURRENT_TIMESTAMP"
            conn.exec_driver_sql(
                f"""
                INSERT INTO locations_new (id, x, y, status, is_visible, updated_at)
                SELECT id, x, y, {status_expr}, {visible_expr}, {updated_expr}
                FROM locations;
                """
            )
            conn.exec_driver_sql("DROP TABLE locations;")
            conn.exec_driver_sql("ALTER TABLE locations_new RENAME TO locations;")

        cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(locations);").fetchall()]
        missing_sql = {
            "code": "ALTER TABLE locations ADD COLUMN code VARCHAR(64);",
            "enabled": "ALTER TABLE locations ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;",
            "note": "ALTER TABLE locations ADD COLUMN note VARCHAR(512);",
            "area_id": "ALTER TABLE locations ADD COLUMN area_id INTEGER;",
            "rack_id": "ALTER TABLE locations ADD COLUMN rack_id INTEGER;",
            "free_enabled": "ALTER TABLE locations ADD COLUMN free_enabled INTEGER NOT NULL DEFAULT 0;",
            "free_x": "ALTER TABLE locations ADD COLUMN free_x FLOAT;",
            "free_y": "ALTER TABLE locations ADD COLUMN free_y FLOAT;",
            "free_w": "ALTER TABLE locations ADD COLUMN free_w FLOAT;",
            "free_h": "ALTER TABLE locations ADD COLUMN free_h FLOAT;",
        }
        for name, stmt in missing_sql.items():
            if name not in cols:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_locations_rack_id ON locations(rack_id);")


        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "areas" in tables:
            area_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(areas);").fetchall()]
            if "matter_area" not in area_cols:
                conn.exec_driver_sql("ALTER TABLE areas ADD COLUMN matter_area VARCHAR(128);")

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "movement_orders" in tables:
            mo_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(movement_orders);").fetchall()]
            movement_missing_sql = {
                "dispatch_status": "ALTER TABLE movement_orders ADD COLUMN dispatch_status VARCHAR(32) NOT NULL DEFAULT 'not_sent';",
                "dispatch_request_json": "ALTER TABLE movement_orders ADD COLUMN dispatch_request_json TEXT;",
                "dispatch_response_json": "ALTER TABLE movement_orders ADD COLUMN dispatch_response_json TEXT;",
                "override_payload_json": "ALTER TABLE movement_orders ADD COLUMN override_payload_json TEXT;",
                "remote_task_code": "ALTER TABLE movement_orders ADD COLUMN remote_task_code VARCHAR(128);",
                "req_code": "ALTER TABLE movement_orders ADD COLUMN req_code VARCHAR(128);",
                "pickup_rack_id": "ALTER TABLE movement_orders ADD COLUMN pickup_rack_id INTEGER;",
                "dropoff_rack_id": "ALTER TABLE movement_orders ADD COLUMN dropoff_rack_id INTEGER;",
                "rcs_status": "ALTER TABLE movement_orders ADD COLUMN rcs_status VARCHAR(64);",
                "rcs_message": "ALTER TABLE movement_orders ADD COLUMN rcs_message VARCHAR(512);",
                "rcs_response_json": "ALTER TABLE movement_orders ADD COLUMN rcs_response_json TEXT;",
                "dispatched_at": "ALTER TABLE movement_orders ADD COLUMN dispatched_at DATETIME;",
                "rcs_last_update": "ALTER TABLE movement_orders ADD COLUMN rcs_last_update DATETIME;",
                "status_query_request_json": "ALTER TABLE movement_orders ADD COLUMN status_query_request_json TEXT;",
                "status_query_response_json": "ALTER TABLE movement_orders ADD COLUMN status_query_response_json TEXT;",
                "status_query_checked_at": "ALTER TABLE movement_orders ADD COLUMN status_query_checked_at DATETIME;",
                "status_query_log_json": "ALTER TABLE movement_orders ADD COLUMN status_query_log_json TEXT;",
                "agv_code": "ALTER TABLE movement_orders ADD COLUMN agv_code VARCHAR(128);",
                "task_typ": "ALTER TABLE movement_orders ADD COLUMN task_typ VARCHAR(64);",
                "created_window_id": "ALTER TABLE movement_orders ADD COLUMN created_window_id INTEGER;",
                "forced_local_close": "ALTER TABLE movement_orders ADD COLUMN forced_local_close INTEGER NOT NULL DEFAULT 0;",
                "forced_local_close_at": "ALTER TABLE movement_orders ADD COLUMN forced_local_close_at DATETIME;",
                "forced_local_close_reason": "ALTER TABLE movement_orders ADD COLUMN forced_local_close_reason VARCHAR(128);",
                "cancel_source": "ALTER TABLE movement_orders ADD COLUMN cancel_source VARCHAR(64);",
                "cancel_reason": "ALTER TABLE movement_orders ADD COLUMN cancel_reason VARCHAR(256);",
                "closed_by": "ALTER TABLE movement_orders ADD COLUMN closed_by VARCHAR(128);",
                "closed_at": "ALTER TABLE movement_orders ADD COLUMN closed_at DATETIME;",
                "release_source": "ALTER TABLE movement_orders ADD COLUMN release_source VARCHAR(64);",
            }
            for name, stmt in movement_missing_sql.items():
                if name not in mo_cols:
                    conn.exec_driver_sql(stmt)

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "material_groups" in tables:
            mat_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(material_groups);").fetchall()]
            if "color" not in mat_cols:
                conn.exec_driver_sql("ALTER TABLE material_groups ADD COLUMN color VARCHAR(32) NOT NULL DEFAULT '#7c3aed';")

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "operator_window_buttons" in tables:
            owb_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(operator_window_buttons);").fetchall()]
            operator_button_missing_sql = {
                "action_mode": "ALTER TABLE operator_window_buttons ADD COLUMN action_mode VARCHAR(32) NOT NULL DEFAULT 'fifo';",
                "source_cell_id": "ALTER TABLE operator_window_buttons ADD COLUMN source_cell_id INTEGER;",
                "destination_cell_id": "ALTER TABLE operator_window_buttons ADD COLUMN destination_cell_id INTEGER;",
                "priority": "ALTER TABLE operator_window_buttons ADD COLUMN priority VARCHAR(32) NOT NULL DEFAULT 'normal';",
                "agv_code": "ALTER TABLE operator_window_buttons ADD COLUMN agv_code VARCHAR(128);",
                "task_typ": "ALTER TABLE operator_window_buttons ADD COLUMN task_typ VARCHAR(64);",
                "comment": "ALTER TABLE operator_window_buttons ADD COLUMN comment VARCHAR(512);",
                "cancel_matter_area": "ALTER TABLE operator_window_buttons ADD COLUMN cancel_matter_area VARCHAR(128);",
                "point_visible_material_ids_json": "ALTER TABLE operator_window_buttons ADD COLUMN point_visible_material_ids_json TEXT;",
                "point_custom_fields_json": "ALTER TABLE operator_window_buttons ADD COLUMN point_custom_fields_json TEXT;",
            }
            for name, stmt in operator_button_missing_sql.items():
                if name not in owb_cols:
                    conn.exec_driver_sql(stmt)

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "racks" in tables:
            rack_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(racks);").fetchall()]
            if "rack_custom_fields_json" not in rack_cols:
                conn.exec_driver_sql("ALTER TABLE racks ADD COLUMN rack_custom_fields_json TEXT;")



def init_db():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        existing = db.execute(select(Location.id).limit(1)).first()
        if not existing:
            batch = []
            for y in range(DB_GRID_H):
                for x in range(DB_GRID_W):
                    batch.append(Location(x=x, y=y, status=0, is_visible=1, enabled=1))
                    if len(batch) >= 2000:
                        db.add_all(batch)
                        db.commit()
                        batch = []
            if batch:
                db.add_all(batch)
                db.commit()



def ensure_default_settings():
    with SessionLocal() as db:
        defaults = {
            "admin_password_hash": _sha256("admin"),
            "display_rows": str(DB_GRID_H),
            "display_cols": str(DB_GRID_W),
            "bg_filename": "",
            "bg_scale": "1.0",
            "bg_scale_x": "1.0",
            "bg_scale_y": "1.0",
            "bg_offset_x": "0.0",
            "bg_offset_y": "0.0",
            "client_ip": "",
            "rcs_base_url": "",
            "rcs_create_task_endpoint": "/rcs/task/create",
            "rcs_query_task_status_endpoint": "/rcms/services/rest/hikRpcService/queryTaskStatus",
            "rcs_cancel_task_endpoint": "/rcms/services/rest/hikRpcService/cancelTask",
            "rcs_task_monitor_interval_seconds": "3.0",
            "rcs_agv_monitor_interval_seconds": "5.0",
            "rcs_enable_map_short_name": "1",
            "rcs_map_short_name": "AA",
            "rcs_enable_map_code": "0",
            "rcs_map_code": "",
            "rcs_enable_amr_monitor": "1",
            "rcs_verify_tls": "0",
            "rcs_token_code": "",
            "rcs_auth_header": "",
            "agv_overlay_scale_x": "1.0",
            "agv_overlay_scale_y": "1.0",
            "agv_overlay_offset_x": "0.0",
            "agv_overlay_offset_y": "0.0",
            "agv_overlay_rotation_deg": "0.0",
            "agv_orientation_offset_deg": "0.0",
            "agv_overlay_mirror_x": "0",
            "agv_overlay_mirror_y": "0",
            "agv_icon_angle_mirror": "0",
            "runtime_refresh_seconds": "5.0",
            "runtime_reconnect_seconds": "3.0",
            "cleanup_min_age_minutes": "30",
            "force_release_min_age_minutes": "20",
            "cancel_undo_auto_recovery_enabled": "1",
            "cancel_undo_auto_recovery_min_age_minutes": "5",
            "software_restart_mode": "auto",
            "software_restart_service_name": "agv-app",
            "software_update_systemd_service": "agv-app",
            "software_restart_script": "restart_app.bat",
            "software_update_keep_backups": "5",
            "software_update_max_uncompressed_mb": "500",
            "pre_restore_backup_keep": "10",
        }
        for k, v in defaults.items():
            if not get_setting(db, k, ""):
                set_setting(db, k, v)


ADMIN_TOKENS = {}



def require_admin(token: Optional[str]):
    if not token or token not in ADMIN_TOKENS:
        raise HTTPException(status_code=401, detail="Admin token inválido")
    if ADMIN_TOKENS[token] < datetime.utcnow():
        ADMIN_TOKENS.pop(token, None)
        raise HTTPException(status_code=401, detail="Admin token expirado")



def _to_area_out(r: Area) -> AreaOut:
    return AreaOut(
        id=r.id,
        code=r.code,
        name=r.name,
        description=r.description,
        matter_area=r.matter_area,
        color=r.color,
        area_type=r.area_type,
        is_active=r.is_active,
        priority=r.priority,
        updated_at=r.updated_at,
    )



def _normalize_hex_color(value: Optional[str], default: str = "#7c3aed") -> str:
    if not isinstance(value, str):
        return default
    value = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    return default


def _random_material_color() -> str:
    palette = [
        "#2563eb", "#7c3aed", "#059669", "#dc2626", "#d97706", "#0891b2",
        "#db2777", "#65a30d", "#ea580c", "#4f46e5", "#0f766e", "#be185d",
    ]
    return random.choice(palette)


def ensure_special_materials():
    with SessionLocal() as db:
        now = datetime.utcnow()
        row = db.execute(select(MaterialGroup).where(MaterialGroup.code == NO_MATERIAL_CODE)).scalar_one_or_none()
        if row is None:
            row = MaterialGroup(
                code=NO_MATERIAL_CODE,
                name=NO_MATERIAL_NAME,
                description="Material especial para racks sin material (icono escalera).",
                color=NO_MATERIAL_COLOR,
                is_active=1,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            return
        changed = False
        if int(getattr(row, "is_active", 0) or 0) != 1:
            row.is_active = 1
            changed = True
        if not (getattr(row, "name", None) or "").strip():
            row.name = NO_MATERIAL_NAME
            changed = True
        if changed:
            row.updated_at = now
            db.add(row)
            db.commit()


def _ensure_no_material_group_in_db(db) -> MaterialGroup:
    row = db.execute(select(MaterialGroup).where(MaterialGroup.code == NO_MATERIAL_CODE)).scalar_one_or_none()
    if row is None:
        now = datetime.utcnow()
        row = MaterialGroup(
            code=NO_MATERIAL_CODE,
            name=NO_MATERIAL_NAME,
            description="Material especial para racks sin material (icono escalera).",
            color=NO_MATERIAL_COLOR,
            is_active=1,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    changed = False
    if int(getattr(row, "is_active", 0) or 0) != 1:
        row.is_active = 1
        changed = True
    if not (getattr(row, "name", None) or "").strip():
        row.name = NO_MATERIAL_NAME
        changed = True
    if changed:
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _to_material_out(r: MaterialGroup) -> MaterialGroupOut:
    return MaterialGroupOut(
        id=r.id,
        code=r.code,
        name=r.name,
        description=r.description,
        color=_normalize_hex_color(getattr(r, "color", None), "#7c3aed"),
        is_active=r.is_active,
        updated_at=r.updated_at,
    )



def _legacy_rack_custom_fields(r: Rack) -> list:
    rows = []
    if getattr(r, "lot", None):
        rows.append({"key": "lot", "label": "Lote", "value": r.lot})
    if getattr(r, "quantity", None) not in (None, ""):
        rows.append({"key": "quantity", "label": "Cantidad", "value": r.quantity})
    if getattr(r, "manufacturer_code", None):
        rows.append({"key": "manufacturer_code", "label": "Cód. fabricante", "value": r.manufacturer_code})
    if getattr(r, "comment", None):
        rows.append({"key": "comment", "label": "Comentario", "value": r.comment})
    return rows


def _normalize_rack_custom_fields(items) -> list:
    out = []
    seen = set()
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = item.get("value")
        if not label:
            continue
        key = str(item.get("key") or f"field_{idx+1}").strip() or f"field_{idx+1}"
        if key in seen:
            key = f"{key}_{idx+1}"
        if isinstance(value, str):
            value = value.strip()
        out.append({"key": key[:64], "label": label[:128], "value": value})
        seen.add(key)
    return out


def _rack_custom_fields_for_row(r: Rack) -> list:
    rows = _normalize_rack_custom_fields(_load_json_list(getattr(r, "rack_custom_fields_json", None)))
    return rows if rows else _legacy_rack_custom_fields(r)


def _merge_rack_custom_field_values(existing_rows, updates) -> list:
    base_rows = _normalize_rack_custom_fields(existing_rows)
    update_map = {}
    for item in _normalize_rack_custom_fields(updates):
        key = str(item.get("key") or "").strip()
        if key:
            update_map[key] = item
    merged = []
    seen = set()
    for row in base_rows:
        key = str(row.get("key") or "").strip()
        if key and key in update_map:
            upd = update_map[key]
            merged.append({
                "key": key,
                "label": str(upd.get("label") or row.get("label") or key).strip()[:128],
                "value": upd.get("value"),
            })
            seen.add(key)
        else:
            merged.append(row)
            if key:
                seen.add(key)
    for key, upd in update_map.items():
        if key in seen:
            continue
        merged.append({
            "key": key,
            "label": str(upd.get("label") or key).strip()[:128],
            "value": upd.get("value"),
        })
    return _normalize_rack_custom_fields(merged)


def _apply_action_custom_fields_to_rack(rack: Rack, custom_rows: list) -> None:
    merged = _merge_rack_custom_field_values(_rack_custom_fields_for_row(rack), custom_rows)
    rack.rack_custom_fields_json = json.dumps(merged)
    by_key = {str(item.get("key") or "").strip(): item.get("value") for item in merged if isinstance(item, dict)}
    if "lot" in by_key:
        rack.lot = str(by_key.get("lot") or "").strip() or None
    if "manufacturer_code" in by_key:
        rack.manufacturer_code = str(by_key.get("manufacturer_code") or "").strip() or None
    if "comment" in by_key:
        rack.comment = str(by_key.get("comment") or "").strip() or None
    if "quantity" in by_key:
        qv = by_key.get("quantity")
        try:
            rack.quantity = int(float(qv)) if qv not in (None, "") else 0
        except Exception:
            rack.quantity = rack.quantity or 0


def _rack_reservation_order(db, rack_id: Optional[int]) -> Optional[MovementOrder]:
    if not rack_id:
        return None
    return (
        db.execute(
            select(MovementOrder)
            .where(MovementOrder.rack_id == rack_id, MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))
            .order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())
        )
        .scalars()
        .first()
    )


def _rack_reservation_payload(db, rack: Optional[Rack], include_task_lookup: bool = True) -> dict:
    if not rack:
        return {
            "reservation_status": "No reservado",
            "reservation_task_id": None,
            "reservation_task_identifier": None,
            "reservation_rack_id": None,
            "reservation_rack_code": None,
        }
    order = _rack_reservation_order(db, rack.id) if include_task_lookup else None
    is_reserved = bool(order) or rack_status_is_reserved(getattr(rack, "status", ""))
    task_identifier = None
    if order:
        task_identifier = (order.remote_task_code or "").strip() or (order.order_code or "").strip() or str(order.id)
    return {
        "reservation_status": "Reservado" if is_reserved else "No reservado",
        "reservation_task_id": order.id if order else None,
        "reservation_task_identifier": task_identifier,
        "rack_id": rack.id,
        "reservation_rack_id": rack.id,
        "reservation_rack_code": rack.code,
    }


def _rack_out(db, r: Rack) -> RackOut:
    loc = db.execute(select(Location).where(Location.rack_id == r.id)).scalar_one_or_none()
    mat = db.execute(select(MaterialGroup).where(MaterialGroup.id == r.material_group_id)).scalar_one_or_none() if r.material_group_id else None
    area = None
    if loc and loc.area_id:
        area = db.execute(select(Area).where(Area.id == loc.area_id)).scalar_one_or_none()
    rack_status = r.status if isinstance(r.status, str) and r.status.strip() else ("occupied" if (loc and loc.rack_id is not None) else "free")
    reservation = _rack_reservation_payload(db, r, include_task_lookup=True)
    return RackOut(
        id=r.id,
        code=r.code,
        name=r.name,
        status=rack_status,
        material_group_id=r.material_group_id,
        lot=r.lot,
        manufacturer_code=r.manufacturer_code,
        quantity=r.quantity,
        comment=r.comment,
        fifo_entered_at=r.fifo_entered_at,
        last_moved_at=r.last_moved_at,
        custom_fields=_rack_custom_fields_for_row(r),
        updated_at=r.updated_at,
        material_group_name=mat.name if mat else None,
        location_x=loc.x if loc else None,
        location_y=loc.y if loc else None,
        area_id=loc.area_id if loc else None,
        area_name=area.name if area else None,
        reservation_status=reservation["reservation_status"],
        reservation_task_id=reservation["reservation_task_id"],
        reservation_task_identifier=reservation["reservation_task_identifier"],
    )



def _location_out(db, r: Location, area_by_id: Optional[dict] = None, rack_by_id: Optional[dict] = None) -> LocationOut:
    area = None
    rack = None
    if area_by_id is not None:
        area = area_by_id.get(r.area_id) if r.area_id else None
    elif r.area_id:
        area = db.execute(select(Area).where(Area.id == r.area_id)).scalar_one_or_none()

    if rack_by_id is not None:
        rack = rack_by_id.get(r.rack_id) if r.rack_id else None
    elif r.rack_id:
        rack = db.execute(select(Rack).where(Rack.id == r.rack_id)).scalar_one_or_none()
    reservation = _rack_reservation_payload(db, rack, include_task_lookup=True)

    return LocationOut(
        id=r.id,
        x=r.x,
        y=r.y,
        status=int(r.status or 0),
        is_visible=r.is_visible,
        updated_at=r.updated_at,
        code=r.code,
        enabled=r.enabled,
        note=r.note,
        area_id=r.area_id,
        rack_id=r.rack_id,
        free_enabled=int(r.free_enabled or 0),
        free_x=r.free_x,
        free_y=r.free_y,
        free_w=r.free_w,
        free_h=r.free_h,
        area_name=area.name if area else None,
        rack_code=rack.code if rack else None,
        reservation_status=reservation["reservation_status"],
        reservation_task_id=reservation["reservation_task_id"],
        reservation_task_identifier=reservation["reservation_task_identifier"],
        reservation_rack_id=reservation["reservation_rack_id"],
        reservation_rack_code=reservation["reservation_rack_code"],
    )


def _build_location_lookup_maps(db, rows: List[Location]) -> tuple[dict, dict]:
    area_ids = sorted({row.area_id for row in rows if row.area_id})
    rack_ids = sorted({row.rack_id for row in rows if row.rack_id})
    area_by_id = {}
    rack_by_id = {}
    if area_ids:
        area_rows = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all()
        area_by_id = {row.id: row for row in area_rows}
    if rack_ids:
        rack_rows = db.execute(select(Rack).where(Rack.id.in_(rack_ids))).scalars().all()
        rack_by_id = {row.id: row for row in rack_rows}
    return area_by_id, rack_by_id


def _validate_foreign_keys(db, area_id: Optional[int], rack_id: Optional[int], ignore_xy: Optional[tuple[int, int]] = None):
    if area_id:
        area = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
        if not area:
            raise HTTPException(status_code=400, detail="Área no encontrada")
    if rack_id:
        rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
        if not rack:
            raise HTTPException(status_code=400, detail="Rack no encontrado")
        occupied = db.execute(select(Location).where(Location.rack_id == rack_id)).scalar_one_or_none()
        if occupied and (ignore_xy is None or (occupied.x, occupied.y) != ignore_xy):
            raise HTTPException(status_code=400, detail=f"El rack ya está asignado a la celda ({occupied.x}, {occupied.y})")




def _derived_location_status(rack_id: Optional[int]) -> int:
    return 1 if rack_id is not None else 0


def _sync_location_status(row: Location) -> Location:
    if row.rack_id is not None:
        row.status = 1
    elif int(row.status or 0) not in (0, 1):
        row.status = 0
    row.updated_at = datetime.utcnow()
    return row


def _sync_all_location_statuses(db):
    changed = False
    now = datetime.utcnow()
    for row in db.execute(select(Location)).scalars().all():
        derived = 1 if row.rack_id is not None else int(row.status or 0)
        if derived not in (0, 1):
            derived = 0
        if int(row.status or 0) != derived:
            row.status = derived
            row.updated_at = now
            db.add(row)
            changed = True
    if changed:
        db.commit()


def _movement_order_out(
    db,
    row: MovementOrder,
    rack_by_id: Optional[dict] = None,
    location_by_id: Optional[dict] = None,
    area_by_id: Optional[dict] = None,
    material_by_id: Optional[dict] = None,
    current_location_by_rack_id: Optional[dict] = None,
) -> MovementOrderOut:
    rack = rack_by_id.get(row.rack_id) if rack_by_id is not None else db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one()
    source_cell = location_by_id.get(row.source_cell_id) if location_by_id is not None else db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one()
    destination_cell = location_by_id.get(row.destination_cell_id) if location_by_id is not None else db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one()
    source_area = area_by_id.get(row.source_area_id) if area_by_id is not None and row.source_area_id else (db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None)
    destination_area = area_by_id.get(row.destination_area_id) if area_by_id is not None and row.destination_area_id else (db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None)
    material = material_by_id.get(row.material_group_id) if material_by_id is not None and row.material_group_id else (db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None)
    current_cell_row = current_location_by_rack_id.get(row.rack_id) if current_location_by_rack_id is not None else db.execute(select(Location).where(Location.rack_id == row.rack_id)).scalar_one_or_none()
    current_cell = None
    if current_cell_row:
        current_cell = {"id": current_cell_row.id, "x": current_cell_row.x, "y": current_cell_row.y, "code": current_cell_row.code}
    return MovementOrderOut(
        order_id=row.id,
        order_code=row.order_code,
        order_type=row.order_type,
        status=row.status,
        priority=row.priority,
        agv_code=row.agv_code,
        task_typ=row.task_typ,
        comment=row.comment,
        created_at=row.created_at,
        created_by=row.created_by,
        rack_id=row.rack_id,
        rack_code=rack.code,
        material_group_id=row.material_group_id,
        material_group_name=material.name if material else None,
        source_area_id=row.source_area_id,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=row.source_cell_id,
        destination_cell_id=row.destination_cell_id,
        source_cell={"id": source_cell.id, "x": source_cell.x, "y": source_cell.y, "code": source_cell.code, "area_id": source_cell.area_id},
        destination_cell={"id": destination_cell.id, "x": destination_cell.x, "y": destination_cell.y, "code": destination_cell.code, "area_id": destination_cell.area_id},
        current_cell=current_cell,
        dispatch_status=row.dispatch_status or "not_sent",
        remote_task_code=row.remote_task_code,
        req_code=row.req_code,
        rcs_status=row.rcs_status,
        rcs_message=row.rcs_message,
        cancel_source=row.cancel_source,
        cancel_reason=row.cancel_reason,
        closed_by=row.closed_by,
        closed_at=row.closed_at,
        release_source=row.release_source,
        dispatched_at=row.dispatched_at,
        rcs_last_update=row.rcs_last_update,
        status_query_checked_at=row.status_query_checked_at,
        status_query_request_payload=_safe_json_loads(row.status_query_request_json),
        status_query_response_payload=_safe_json_loads(row.status_query_response_json),
        status_query_log_entries=_safe_json_loads_any(row.status_query_log_json) or [],
        can_simulate_complete=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
        can_undo=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
    )


def _build_movement_order_lookup_maps(db, rows: List[MovementOrder]) -> dict:
    rack_ids = sorted({row.rack_id for row in rows if row.rack_id})
    location_ids = sorted({cell_id for row in rows for cell_id in (row.source_cell_id, row.destination_cell_id) if cell_id})
    area_ids = sorted({area_id for row in rows for area_id in (row.source_area_id, row.destination_area_id) if area_id})
    material_ids = sorted({row.material_group_id for row in rows if row.material_group_id})

    rack_by_id = {}
    location_by_id = {}
    area_by_id = {}
    material_by_id = {}
    current_location_by_rack_id = {}

    if rack_ids:
        rack_rows = db.execute(select(Rack).where(Rack.id.in_(rack_ids))).scalars().all()
        rack_by_id = {row.id: row for row in rack_rows}

    current_location_rows = []
    if rack_ids:
        current_location_rows = db.execute(select(Location).where(Location.rack_id.in_(rack_ids))).scalars().all()
        current_location_by_rack_id = {row.rack_id: row for row in current_location_rows if row.rack_id is not None}

    if location_ids:
        location_rows = db.execute(select(Location).where(Location.id.in_(location_ids))).scalars().all()
        location_by_id = {row.id: row for row in location_rows}
        for row in current_location_rows:
            location_by_id.setdefault(row.id, row)

    if area_ids:
        area_rows = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all()
        area_by_id = {row.id: row for row in area_rows}

    if material_ids:
        material_rows = db.execute(select(MaterialGroup).where(MaterialGroup.id.in_(material_ids))).scalars().all()
        material_by_id = {row.id: row for row in material_rows}

    return {
        "rack_by_id": rack_by_id,
        "location_by_id": location_by_id,
        "area_by_id": area_by_id,
        "material_by_id": material_by_id,
        "current_location_by_rack_id": current_location_by_rack_id,
    }


def _generate_req_code_ms() -> str:
    return generate_req_code_ms()

def _movement_order_json_payload(db, row: MovementOrder) -> dict:
    source_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one()
    destination_cell = db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one()

    def _cell_code(cell: Location) -> str:
        return (cell.code or '').strip()

    req_code = row.req_code or _generate_req_code_ms()

    priority_value = ""
    normalized_priority = (row.priority or "").strip().lower()
    if normalized_priority == "alta":
        priority_value = "1"
    elif normalized_priority == "urgente":
        priority_value = "2"

    payload = {
        "agvCode": (row.agv_code or "").strip(),
        "clientCode": "",
        "ctnrCode": "",
        "ctnrTyp": "",
        "data": "",
        "materialLot": "",
        "podCode": "",
        "podDir": "",
        "podTyp": "",
        "positionCodePath": [
            {
                "positionCode": _cell_code(source_cell),
                "type": "00",
            },
            {
                "positionCode": _cell_code(destination_cell),
                "type": "00",
            },
        ],
        "priority": priority_value,
        "reqCode": req_code,
        "reqTime": "",
        "taskCode": "",
        "taskTyp": (row.task_typ or "").strip(),
        "tokenCode": "",
        "wbCode": "",
    }
    return payload




def _safe_json_loads(text_value: Optional[str]) -> Optional[dict]:
    if not text_value:
        return None
    try:
        data = json.loads(text_value)
        return data if isinstance(data, dict) else {"data": data}
    except Exception:
        return {"raw": text_value}


def _safe_json_loads_any(text_value: Optional[str]):
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except Exception:
        return {"raw": text_value}


_LOG_SENSITIVE_KEYWORDS = ("password", "token", "authorization", "auth_header", "secret", "api_key", "apikey")


def _is_log_sensitive_key(key: str) -> bool:
    text = str(key or "").strip().lower()
    return text == "key" or any(fragment in text for fragment in _LOG_SENSITIVE_KEYWORDS)


def _sanitize_log_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if _is_log_sensitive_key(str(key or "")):
                sanitized[key] = "[REDACTED]" if item not in (None, "") else item
            else:
                sanitized[key] = _sanitize_log_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_log_payload(item) for item in value]
    return value


def _append_status_query_log(db, row: MovementOrder, *, kind: str, request_payload: Optional[dict], response_payload, message: str = "", arrived_at: Optional[datetime] = None):
    logs = _safe_json_loads_any(row.status_query_log_json)
    if not isinstance(logs, list):
        logs = []
    when = arrived_at or datetime.utcnow()
    logs.append({
        "kind": kind,
        "arrived_at": when.isoformat(),
        "message": message or "",
        "request": _sanitize_log_payload(request_payload),
        "response": _sanitize_log_payload(response_payload),
    })
    row.status_query_log_json = json.dumps(logs[-200:], ensure_ascii=False)
    row.updated_at = when
    db.add(row)
    return logs[-200:]


def _append_debug_console_event(db, *, direction: str, module: str, base_url: str = "", endpoint: str = "", payload=None, message: str = "", created_at: Optional[datetime] = None, auto_commit: bool = True):
    when = created_at or datetime.utcnow()
    payload_json = None
    if payload is not None:
        try:
            payload_json = json.dumps(_sanitize_log_payload(payload), ensure_ascii=False)
        except Exception:
            payload_json = json.dumps({"raw": "[UNSERIALIZABLE_PAYLOAD]"}, ensure_ascii=False)
    row = DebugConsoleEvent(
        direction=(direction or "").strip() or "sent",
        module=(module or "").strip() or "unknown",
        base_url=(base_url or "").strip() or None,
        endpoint=(endpoint or "").strip() or None,
        payload_json=payload_json,
        message=(message or "").strip()[:512] or None,
        created_at=when,
    )
    db.add(row)
    if auto_commit:
        db.commit()

    max_console_events = 20
    if not auto_commit:
        db.flush()
    total_events = db.execute(select(func.count()).select_from(DebugConsoleEvent)).scalar_one() or 0
    if total_events > max_console_events:
        overflow = int(total_events - max_console_events)
        stale_rows = db.execute(
            select(DebugConsoleEvent).order_by(DebugConsoleEvent.created_at.asc(), DebugConsoleEvent.id.asc()).limit(overflow)
        ).scalars().all()
        for stale in stale_rows:
            db.delete(stale)
        if auto_commit:
            db.commit()
        else:
            db.flush()
    return row


def _debug_console_event_out(row: DebugConsoleEvent) -> DebugConsoleEventOut:
    return DebugConsoleEventOut(
        id=row.id,
        direction=row.direction or "sent",
        module=row.module or "unknown",
        base_url=row.base_url,
        endpoint=row.endpoint,
        payload=_safe_json_loads_any(row.payload_json),
        message=row.message,
        created_at=row.created_at,
    )




def _active_orders_for_rack(db, rack_id: Optional[int], *, exclude_order_id: Optional[int] = None) -> List[MovementOrder]:
    if not rack_id:
        return []
    stmt = select(MovementOrder).where(
        MovementOrder.rack_id == rack_id,
        MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
    )
    if exclude_order_id:
        stmt = stmt.where(MovementOrder.id != exclude_order_id)
    return db.execute(stmt).scalars().all()


def _manual_release_rack_or_raise(db, rack: Rack, *, actor: str, reason: str, now: Optional[datetime] = None) -> List[dict]:
    now = now or datetime.utcnow()
    closed_orders = []
    active_orders = _active_orders_for_rack(db, rack.id)
    min_age_minutes = _cancel_undo_auto_recovery_min_age_minutes(db)
    releasable_orders = [
        order for order in active_orders
        if (order.status or "") == "cancel_requested_undo" and _cancel_undo_recovery_candidate(db, order, now=now, min_age_minutes=min_age_minutes)[0]
    ]
    blocking_orders = [order for order in active_orders if order not in releasable_orders]

    if blocking_orders:
        order_labels = ", ".join(f"{order.id}:{order.status}" for order in blocking_orders)
        for blocking_order in blocking_orders:
            logger.warning(
                "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
                rack.id,
                rack.code,
                None,
                blocking_order.id,
                blocking_order.status,
                actor,
                reason,
            )
        logger.warning("[RACK MANUAL RELEASE BLOCKED] rack_id=%s rack_code=%s active_orders=%s actor=%s reason=%s", rack.id, rack.code, order_labels, actor, reason)
        _append_debug_console_event(
            db,
            direction="received",
            module="cleanup",
            payload={"action": "manual_release_blocked", "rack_id": rack.id, "rack_code": rack.code, "active_orders": [{"order_id": order.id, "status": order.status, "rcs_status": order.rcs_status} for order in blocking_orders], "actor": actor, "reason": reason, "at": now.isoformat()},
            message=f"[RACK RELEASE] Bloqueado rack_id={rack.id}: orden activa asociada",
            created_at=now,
            auto_commit=False,
        )
        db.commit()
        logger.info(
            "ADMIN_RACK_MANUAL_RELEASE_RESULT rack_id=%s rack_code=%s success=false reason=%s blocking_order_id=%s",
            rack.id,
            rack.code,
            "active_order_same_rack",
            blocking_orders[0].id if blocking_orders else None,
        )
        raise HTTPException(status_code=409, detail=f"No se puede liberar el rack: tiene orden activa asociada ({order_labels}).")

    for order in releasable_orders:
        closed_orders.append(_force_close_old_active_order(db, order, now=now, admin_user=actor))

    old_status = rack.status or ""
    if not rack_status_is_available(old_status):
        apply_rack_reservation_status(rack, False, updated_at=now, order_id=closed_orders[0]["order_id"] if closed_orders else None, source="admin_racks_manual", reason=reason)
        db.add(rack)
        _audit_rack_release(db, rack=rack, previous_status=old_status, new_status=rack.status, source="admin_racks_manual", related_order_id=None, reason=reason, actor=actor, at=now, auto_commit=False)
        logger.info("[RACK MANUAL RELEASE] rack_id=%s rack_code=%s previous_status=%s new_status=%s actor=%s reason=%s", rack.id, rack.code, old_status, rack.status, actor, reason)
        _append_debug_console_event(
            db,
            direction="received",
            module="cleanup",
            payload={"action": "manual_release_rack", "rack_id": rack.id, "rack_code": rack.code, "old_status": old_status, "new_status": rack.status, "closed_orders": closed_orders, "actor": actor, "reason": reason, "at": now.isoformat()},
            message=f"[RACK RELEASE] Manual rack_id={rack.id} {old_status} -> {rack.status}",
            created_at=now,
            auto_commit=False,
        )
    logger.info(
        "ADMIN_RACK_MANUAL_RELEASE_RESULT rack_id=%s rack_code=%s success=true reason=%s blocking_order_id=%s",
        rack.id,
        rack.code,
        reason,
        None,
    )
    return closed_orders


def _log_rack_status_change(db, *, rack: Rack, old_status: str, new_status: str, reason: str, order_id: Optional[int] = None, auto_commit: bool = False):
    now = datetime.utcnow()
    logger.info("[CLEANUP] Rack %s %s -> %s reason=%s order_id=%s", rack.id, old_status, new_status, reason, order_id)
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={
            "action": "rack_status_change",
            "rack_id": rack.id,
            "rack_code": rack.code,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
            "order_id": order_id,
            "at": now.isoformat(),
        },
        message=f"[CLEANUP] Rack {rack.id} {old_status} -> {new_status}",
        created_at=now,
        auto_commit=auto_commit,
    )


def _audit_order_close(
    db,
    order: MovementOrder,
    *,
    previous_status: str,
    new_status: str,
    source: str,
    reason: str,
    actor: str = "",
    closed_at: Optional[datetime] = None,
    auto_commit: bool = False,
):
    now = closed_at or datetime.utcnow()
    if new_status in {"completed", "cancelled", "undone", "failed"}:
        order.status = new_status
        if str(order.rcs_status or "").strip().lower() not in {"completed", "cancelled", "undone"}:
            order.rcs_status = new_status
        order.release_source = source
        order.updated_at = now
    order.cancel_source = source
    order.cancel_reason = reason
    order.closed_by = actor or None
    order.closed_at = now
    db.add(order)
    logger.info(
        "[CANCEL ORDER] order_id=%s order_code=%s previous_status=%s new_status=%s source=%s admin=%s reason=%s",
        order.id,
        order.order_code,
        previous_status,
        new_status,
        source,
        actor or "-",
        reason,
    )
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={
            "action": "cancel_order",
            "order_id": order.id,
            "order_code": order.order_code,
            "previous_status": previous_status,
            "new_status": new_status,
            "source": source,
            "admin": actor or "",
            "reason": reason,
            "at": now.isoformat(),
        },
        message=f"[CANCEL ORDER] order_id={order.id} source={source} reason={reason}",
        created_at=now,
        auto_commit=auto_commit,
    )


def _audit_rack_release(
    db,
    *,
    rack: Rack,
    previous_status: str,
    new_status: str,
    source: str,
    related_order_id: Optional[int] = None,
    reason: str = "",
    actor: str = "",
    at: Optional[datetime] = None,
    auto_commit: bool = False,
):
    now = at or datetime.utcnow()
    if related_order_id:
        related_order = db.execute(select(MovementOrder).where(MovementOrder.id == related_order_id)).scalar_one_or_none()
        if related_order:
            related_order.release_source = source
            db.add(related_order)
    logger.info(
        "[RACK RELEASE] rack_id=%s rack_code=%s previous_status=%s new_status=%s source=%s related_order=%s reason=%s admin=%s",
        rack.id,
        rack.code,
        previous_status,
        new_status,
        source,
        related_order_id or "-",
        reason,
        actor or "-",
    )
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={
            "action": "rack_release",
            "rack_id": rack.id,
            "rack_code": rack.code,
            "previous_status": previous_status,
            "new_status": new_status,
            "source": source,
            "related_order": related_order_id,
            "reason": reason,
            "admin": actor or "",
            "at": now.isoformat(),
        },
        message=f"[RACK RELEASE] rack_id={rack.id} source={source} related_order={related_order_id or '-'}",
        created_at=now,
        auto_commit=auto_commit,
    )


def _audit_rack_reservation_change(
    db,
    *,
    rack: Rack,
    previous_status: str,
    new_status: str,
    source: str,
    related_order_id: Optional[int] = None,
    reason: str = "",
    actor: str = "",
    at: Optional[datetime] = None,
    auto_commit: bool = False,
):
    if rack_status_is_available(previous_status) and rack_status_is_reserved(new_status):
        label = "[RACK RESERVE]"
    elif rack_status_is_reserved(previous_status) and rack_status_is_available(new_status):
        label = "[RACK RELEASE]"
    else:
        label = "[RACK STATUS]"
    now = at or datetime.utcnow()
    logger.info(
        "%s rack_id=%s rack_code=%s previous_status=%s new_status=%s source=%s related_order=%s reason=%s admin=%s",
        label,
        rack.id,
        rack.code,
        previous_status,
        new_status,
        source,
        related_order_id or "-",
        reason,
        actor or "-",
    )
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={
            "action": "rack_reservation_change",
            "rack_id": rack.id,
            "rack_code": rack.code,
            "previous_status": previous_status,
            "new_status": new_status,
            "source": source,
            "related_order": related_order_id,
            "reason": reason,
            "admin": actor or "",
            "at": now.isoformat(),
        },
        message=f"{label} rack_id={rack.id} source={source} related_order={related_order_id or '-'}",
        created_at=now,
        auto_commit=auto_commit,
    )


def _release_rack_if_no_active_orders(db, rack_id: Optional[int], *, related_order_id: Optional[int], reason: str, source: str = "api_internal", actor: str = "") -> bool:
    if not rack_id:
        return False
    rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
    if not rack:
        return False
    logger.info(
        "RACK_RELEASE_ATTEMPT rack_id=%s rack_code=%s order_id=%s source=%s reason=%s",
        rack.id,
        rack.code,
        related_order_id,
        source,
        reason,
    )
    active_other = _active_orders_for_rack(db, rack_id, exclude_order_id=related_order_id)
    if active_other:
        blocking = active_other[0]
        logger.warning(
            "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            related_order_id,
            blocking.id,
            blocking.status,
            source,
            reason,
        )
        return False
    old_status = rack.status or ""
    if rack_status_is_available(old_status):
        return False
    apply_rack_reservation_status(rack, False, updated_at=datetime.utcnow(), order_id=related_order_id, source=source, reason=reason)
    db.add(rack)
    _log_rack_status_change(db, rack=rack, old_status=old_status, new_status=rack.status, reason=reason, order_id=related_order_id, auto_commit=False)
    _audit_rack_release(db, rack=rack, previous_status=old_status, new_status=rack.status, source=source, related_order_id=related_order_id, reason=reason, actor=actor, auto_commit=False)
    return True


def _normalize_endpoint_for_base(base_url: str, endpoint: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return endpoint
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    try:
        from urllib.parse import urlsplit
        base_path = (urlsplit(base_url).path or "").rstrip("/")
    except Exception:
        base_path = ""
    if base_path and (endpoint == base_path or endpoint.startswith(base_path + "/")):
        trimmed = endpoint[len(base_path):]
        return trimmed or "/"
    return endpoint

def _resolve_rcs_target(db, *, base_url: Optional[str] = None, endpoint: Optional[str] = None, mode: str = "query") -> tuple[str, str]:
    resolved_base = ((base_url or "").strip() or (get_setting(db, "rcs_base_url", "") or "").strip() or os.getenv("RCS_BASE_URL", "").strip()).rstrip("/")
    if mode == "create":
        default_endpoint = "/rcs/task/create"
        setting_key = "rcs_create_task_endpoint"
    elif mode == "cancel":
        default_endpoint = "/rcms/services/rest/hikRpcService/cancelTask"
        setting_key = "rcs_cancel_task_endpoint"
    else:
        default_endpoint = "/rcms/services/rest/hikRpcService/queryTaskStatus"
        setting_key = "rcs_query_task_status_endpoint"
    resolved_endpoint = (endpoint or "").strip() or (get_setting(db, setting_key, default_endpoint) or default_endpoint).strip()
    resolved_endpoint = _normalize_endpoint_for_base(resolved_base, resolved_endpoint)
    if resolved_endpoint and not resolved_endpoint.startswith("/") and not resolved_endpoint.startswith("http://") and not resolved_endpoint.startswith("https://"):
        resolved_endpoint = "/" + resolved_endpoint
    return resolved_base, resolved_endpoint


def _current_movement_order_payload(row: MovementOrder, generated_payload: Optional[dict] = None) -> tuple[dict, str]:
    override_payload = _safe_json_loads(row.override_payload_json)
    if override_payload:
        return override_payload, "edited"
    return (generated_payload or {}), "generated"


def _save_movement_order_payload_override(db, row: MovementOrder, payload: dict):
    row.override_payload_json = json.dumps(payload, ensure_ascii=False)
    req_code = str((payload or {}).get("reqCode") or "").strip()
    if req_code:
        row.req_code = req_code
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _reset_movement_order_payload_override(db, row: MovementOrder):
    row.override_payload_json = None
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _movement_order_dispatch_out(row: MovementOrder) -> MovementOrderDispatchOut:
    return MovementOrderDispatchOut(
        order_id=row.id,
        order_code=row.order_code,
        dispatch_status=row.dispatch_status or "not_sent",
        request_payload=_safe_json_loads(row.dispatch_request_json),
        response_payload=_safe_json_loads(row.dispatch_response_json),
        remote_task_code=row.remote_task_code,
        rcs_message=row.rcs_message,
        dispatched_at=row.dispatched_at,
    )


def _mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return ("*" * max(0, len(value) - 4)) + value[-4:]


def _get_rcs_config(db) -> RcsConfigOut:
    base_url = (get_setting(db, "rcs_base_url", "") or "").strip()
    endpoint = (get_setting(db, "rcs_create_task_endpoint", "/rcs/task/create") or "/rcs/task/create").strip() or "/rcs/task/create"
    query_endpoint = (get_setting(db, "rcs_query_task_status_endpoint", "/rcms/services/rest/hikRpcService/queryTaskStatus") or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip() or "/rcms/services/rest/hikRpcService/queryTaskStatus"
    cancel_endpoint = (get_setting(db, "rcs_cancel_task_endpoint", "/rcms/services/rest/hikRpcService/cancelTask") or "/rcms/services/rest/hikRpcService/cancelTask").strip() or "/rcms/services/rest/hikRpcService/cancelTask"
    stop_robot_endpoint = (get_setting(db, "rcs_stop_robot_endpoint", "/rcms/services/rest/hikRpcService/stopRobot") or "/rcms/services/rest/hikRpcService/stopRobot").strip() or "/rcms/services/rest/hikRpcService/stopRobot"
    resume_robot_endpoint = (get_setting(db, "rcs_resume_robot_endpoint", "/rcms/services/rest/hikRpcService/resumeRobot") or "/rcms/services/rest/hikRpcService/resumeRobot").strip() or "/rcms/services/rest/hikRpcService/resumeRobot"
    agv_status_endpoint = (get_setting(db, "rcs_agv_status_endpoint", "/rcms-dps/rest/queryAgvStatus") or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    pod_position_endpoint = (get_setting(db, "rcs_pod_position_endpoint", "/rcms/services/rest/hikRpcService/queryPodPosition") or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
    task_monitor_interval_seconds = float(get_setting(db, "rcs_task_monitor_interval_seconds", "3.0") or 3.0)
    agv_monitor_interval_seconds = float(get_setting(db, "rcs_agv_monitor_interval_seconds", "5.0") or 5.0)
    enable_map_short_name_raw = (get_setting(db, "rcs_enable_map_short_name", "1") or "1").strip().lower()
    map_short_name = (get_setting(db, "rcs_map_short_name", "AA") or "AA").strip() or "AA"
    enable_map_code_raw = (get_setting(db, "rcs_enable_map_code", "0") or "0").strip().lower()
    map_code = (get_setting(db, "rcs_map_code", "") or "").strip()
    enable_amr_monitor_raw = (get_setting(db, "rcs_enable_amr_monitor", "1") or "1").strip().lower()
    verify_tls_raw = (get_setting(db, "rcs_verify_tls", "0") or "0").strip().lower()
    token_code = (get_setting(db, "rcs_token_code", "") or "").strip()
    auth_header = (get_setting(db, "rcs_auth_header", "") or "").strip()
    try:
        cleanup_min_age_minutes = int(float(get_setting(db, "cleanup_min_age_minutes", "30") or 30))
    except Exception:
        cleanup_min_age_minutes = 30
    try:
        force_release_min_age_minutes = int(float(get_setting(db, "force_release_min_age_minutes", "20") or 20))
    except Exception:
        force_release_min_age_minutes = 20
    cancel_undo_auto_recovery_enabled_raw = (get_setting(db, "cancel_undo_auto_recovery_enabled", "1") or "1").strip().lower()
    try:
        cancel_undo_auto_recovery_min_age_minutes = int(float(get_setting(db, "cancel_undo_auto_recovery_min_age_minutes", "5") or 5))
    except Exception:
        cancel_undo_auto_recovery_min_age_minutes = 5

    resolved_base_url = base_url or os.getenv("RCS_BASE_URL", "").strip()
    resolved_token_code = token_code or os.getenv("RCS_TOKEN_CODE", "").strip()
    resolved_auth_header = auth_header or os.getenv("RCS_AUTH_HEADER", "").strip()

    return RcsConfigOut(
        base_url=base_url,
        create_task_endpoint=endpoint,
        query_task_status_endpoint=query_endpoint,
        cancel_task_endpoint=cancel_endpoint,
        stop_robot_endpoint=stop_robot_endpoint,
        resume_robot_endpoint=resume_robot_endpoint,
        agv_status_endpoint=agv_status_endpoint,
        pod_position_endpoint=pod_position_endpoint,
        task_monitor_interval_seconds=task_monitor_interval_seconds,
        agv_monitor_interval_seconds=agv_monitor_interval_seconds,
        enable_map_short_name=1 if enable_map_short_name_raw in {"1", "true", "yes", "si", "sí"} else 0,
        map_short_name=map_short_name,
        enable_map_code=1 if enable_map_code_raw in {"1", "true", "yes", "si", "sí"} else 0,
        map_code=map_code,
        enable_amr_monitor=1 if enable_amr_monitor_raw in {"1", "true", "yes", "si", "sí"} else 0,
        verify_tls=1 if verify_tls_raw in {"1", "true", "yes", "si", "sí"} else 0,
        token_code=_mask_secret(token_code),
        auth_header=_mask_secret(auth_header),
        cleanup_min_age_minutes=max(1, cleanup_min_age_minutes),
        force_release_min_age_minutes=max(1, force_release_min_age_minutes),
        cancel_undo_auto_recovery_enabled=1 if cancel_undo_auto_recovery_enabled_raw in {"1", "true", "yes", "si", "sí"} else 0,
        cancel_undo_auto_recovery_min_age_minutes=max(1, cancel_undo_auto_recovery_min_age_minutes),
        resolved_base_url=resolved_base_url,
        resolved_token_code=_mask_secret(resolved_token_code),
        resolved_auth_header=_mask_secret(resolved_auth_header),
    )


def _save_rcs_config(db, payload: RcsConfigIn) -> RcsConfigOut:
    endpoint = (payload.create_task_endpoint or "/rcs/task/create").strip() or "/rcs/task/create"
    query_endpoint = (payload.query_task_status_endpoint or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip() or "/rcms/services/rest/hikRpcService/queryTaskStatus"
    cancel_endpoint = (payload.cancel_task_endpoint or "/rcms/services/rest/hikRpcService/cancelTask").strip() or "/rcms/services/rest/hikRpcService/cancelTask"
    stop_robot_endpoint = (payload.stop_robot_endpoint or "/rcms/services/rest/hikRpcService/stopRobot").strip() or "/rcms/services/rest/hikRpcService/stopRobot"
    resume_robot_endpoint = (payload.resume_robot_endpoint or "/rcms/services/rest/hikRpcService/resumeRobot").strip() or "/rcms/services/rest/hikRpcService/resumeRobot"
    agv_status_endpoint = (payload.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    pod_position_endpoint = (payload.pod_position_endpoint or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
    task_monitor_interval_seconds = float(payload.task_monitor_interval_seconds or 3.0)
    agv_monitor_interval_seconds = float(payload.agv_monitor_interval_seconds or 5.0)
    cleanup_min_age_minutes = max(1, int(payload.cleanup_min_age_minutes or 30))
    force_release_min_age_minutes = max(1, int(payload.force_release_min_age_minutes or 20))
    cancel_undo_auto_recovery_enabled = 1 if int(payload.cancel_undo_auto_recovery_enabled or 0) else 0
    cancel_undo_auto_recovery_min_age_minutes = max(1, int(payload.cancel_undo_auto_recovery_min_age_minutes or 5))
    enable_map_short_name = 1 if int(payload.enable_map_short_name or 0) else 0
    map_short_name = (payload.map_short_name or "AA").strip() or "AA"
    enable_map_code = 1 if int(payload.enable_map_code or 0) else 0
    map_code = (payload.map_code or "").strip()
    enable_amr_monitor = 1 if int(payload.enable_amr_monitor or 0) else 0
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    if not query_endpoint.startswith("/"):
        query_endpoint = "/" + query_endpoint
    if not cancel_endpoint.startswith("/"):
        cancel_endpoint = "/" + cancel_endpoint
    if not stop_robot_endpoint.startswith("/"):
        stop_robot_endpoint = "/" + stop_robot_endpoint
    if not resume_robot_endpoint.startswith("/"):
        resume_robot_endpoint = "/" + resume_robot_endpoint
    if not agv_status_endpoint.startswith("/"):
        agv_status_endpoint = "/" + agv_status_endpoint
    if not pod_position_endpoint.startswith("/"):
        pod_position_endpoint = "/" + pod_position_endpoint
    set_setting(db, "rcs_base_url", (payload.base_url or "").strip())
    set_setting(db, "rcs_create_task_endpoint", endpoint)
    set_setting(db, "rcs_query_task_status_endpoint", query_endpoint)
    set_setting(db, "rcs_cancel_task_endpoint", cancel_endpoint)
    set_setting(db, "rcs_stop_robot_endpoint", stop_robot_endpoint)
    set_setting(db, "rcs_resume_robot_endpoint", resume_robot_endpoint)
    set_setting(db, "rcs_agv_status_endpoint", agv_status_endpoint)
    set_setting(db, "rcs_pod_position_endpoint", pod_position_endpoint)
    set_setting(db, "rcs_task_monitor_interval_seconds", str(task_monitor_interval_seconds))
    set_setting(db, "rcs_agv_monitor_interval_seconds", str(agv_monitor_interval_seconds))
    set_setting(db, "rcs_enable_map_short_name", "1" if enable_map_short_name else "0")
    set_setting(db, "rcs_map_short_name", map_short_name)
    set_setting(db, "rcs_enable_map_code", "1" if enable_map_code else "0")
    set_setting(db, "rcs_map_code", map_code)
    set_setting(db, "rcs_enable_amr_monitor", "1" if enable_amr_monitor else "0")
    set_setting(db, "rcs_verify_tls", "1" if int(payload.verify_tls or 0) else "0")
    set_setting(db, "rcs_token_code", payload.token_code or "")
    set_setting(db, "rcs_auth_header", payload.auth_header or "")
    set_setting(db, "cleanup_min_age_minutes", str(cleanup_min_age_minutes))
    set_setting(db, "force_release_min_age_minutes", str(force_release_min_age_minutes))
    set_setting(db, "cancel_undo_auto_recovery_enabled", str(cancel_undo_auto_recovery_enabled))
    set_setting(db, "cancel_undo_auto_recovery_min_age_minutes", str(cancel_undo_auto_recovery_min_age_minutes))
    try:
        app.state.task_monitor.interval_seconds = task_monitor_interval_seconds
    except Exception:
        pass
    return _get_rcs_config(db)


def _get_rcs_client_from_settings(db) -> RcsClient:
    base_url = (get_setting(db, "rcs_base_url", "") or "").strip() or os.getenv("RCS_BASE_URL", "").strip()
    endpoint = (get_setting(db, "rcs_create_task_endpoint", "/rcs/task/create") or "/rcs/task/create").strip()
    query_endpoint = (get_setting(db, "rcs_query_task_status_endpoint", "/rcms/services/rest/hikRpcService/queryTaskStatus") or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip()
    cancel_endpoint = (get_setting(db, "rcs_cancel_task_endpoint", "/rcms/services/rest/hikRpcService/cancelTask") or "/rcms/services/rest/hikRpcService/cancelTask").strip()
    verify_tls_raw = (get_setting(db, "rcs_verify_tls", "0") or "0").strip().lower()
    token_code = (get_setting(db, "rcs_token_code", "") or "").strip() or os.getenv("RCS_TOKEN_CODE", "").strip()
    auth_header = (get_setting(db, "rcs_auth_header", "") or "").strip() or os.getenv("RCS_AUTH_HEADER", "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="Configura rcs_base_url en settings o en la variable de entorno RCS_BASE_URL")
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header
    client = RcsClient(
        base_url=base_url,
        endpoint_create_task=endpoint,
        endpoint_query_task_status=query_endpoint,
        endpoint_cancel_task=cancel_endpoint,
        verify_tls=verify_tls_raw in {"1", "true", "yes", "si", "sí"},
        default_headers=headers or None,
    )
    client._token_code = token_code
    return client


def _get_rcs_client_with_overrides(db, *, base_url: Optional[str] = None, query_endpoint: Optional[str] = None) -> RcsClient:
    client = _get_rcs_client_from_settings(db)
    base_override = (base_url or "").strip()
    endpoint_override = (query_endpoint or "").strip()
    if base_override:
        client.base_url = base_override.rstrip("/")
    if endpoint_override:
        client.endpoint_query_task_status = endpoint_override if endpoint_override.startswith("/") else f"/{endpoint_override}"
    return client


def _maybe_json_dict(value: Any) -> Any:
    if isinstance(value, str):
        text_value = value.strip()
        if text_value.startswith("{") or text_value.startswith("["):
            try:
                return json.loads(text_value)
            except Exception:
                return value
    return value


def _find_first_nested_value(payload: Any, keys: set[str]) -> str:
    payload = _maybe_json_dict(payload)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip() in keys and value not in (None, ""):
                return str(value).strip()
        for value in payload.values():
            found = _find_first_nested_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_nested_value(item, keys)
            if found:
                return found
    return ""


def _extract_pod_position_code(response_payload: dict) -> str:
    keys = {
        "positionCode",
        "currentPositionCode",
        "currentPos",
        "currentPosition",
        "locationCode",
        "cellCode",
        "stationCode",
        "wbCode",
        "posCode",
        "position",
        "podPosition",
        "podPos",
        "posicion",
        "posición",
    }
    found = _find_first_nested_value(response_payload, keys)
    if found:
        return found
    data_value = response_payload.get("data") if isinstance(response_payload, dict) else None
    if isinstance(data_value, str) and data_value.strip():
        return data_value.strip()
    return ""


def _cell_payload(cell: Optional[Location]) -> Optional[dict]:
    if not cell:
        return None
    return {"id": cell.id, "x": cell.x, "y": cell.y, "code": cell.code, "area_id": cell.area_id}


def _position_code_is_defined(position_code: str) -> bool:
    value = str(position_code or "").strip()
    if not value:
        return False
    return value.lower() not in {"0", "null", "none", "undefined", "empty", "vacio", "vacío", "sin posicion", "sin posición"}


def _pod_position_endpoint(db) -> str:
    cfg = _get_rcs_config(db)
    endpoint = (cfg.pod_position_endpoint or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
    if not endpoint.startswith("/") and not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = "/" + endpoint
    return endpoint


def _query_pod_position_from_rcs(db, rack: Rack, *, module: str = "query_pod_position", related_order_id: Optional[int] = None, attempt: Optional[int] = None) -> dict:
    pod_code = (rack.code or "").strip()
    if not pod_code:
        raise HTTPException(status_code=400, detail="El rack seleccionado no tiene codigo para enviar como podCode")
    endpoint = _pod_position_endpoint(db)
    client = _get_rcs_client_from_settings(db)
    request_payload = {
        "reqCode": generate_req_code_ms(),
        "reqTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "clientCode": "",
        "tokenCode": getattr(client, "_token_code", "") or "",
        "podCode": pod_code,
    }
    debug_base_url, debug_endpoint = _resolve_rcs_target(db, endpoint=endpoint, mode="query")
    context_payload = {
        **request_payload,
        "rack_id": rack.id,
        "rack_code": pod_code,
        "related_order_id": related_order_id,
        "attempt": attempt,
    }
    try:
        _append_debug_console_event(db, direction="sent", module=module, base_url=debug_base_url, endpoint=debug_endpoint, payload=context_payload, message=f"Consulta posicion pod {pod_code}", auto_commit=False)
        response_payload = client.post_json_payload(request_payload, endpoint_override=endpoint)
        position_code = _extract_pod_position_code(response_payload)
        _append_debug_console_event(
            db,
            direction="received",
            module=module,
            base_url=debug_base_url,
            endpoint=debug_endpoint,
            payload={**response_payload, "rack_id": rack.id, "related_order_id": related_order_id, "attempt": attempt, "position_code": position_code},
            message=f"Respuesta posicion pod {pod_code}: {position_code or '(vacio)'}",
            auto_commit=False,
        )
        return {"endpoint": endpoint, "request_payload": request_payload, "response_payload": response_payload, "position_code": position_code}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Pod position query failed rack_id=%s rack_code=%s endpoint=%s", rack.id, rack.code, endpoint)
        _append_debug_console_event(db, direction="received", module=module, base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc), "rack_id": rack.id, "related_order_id": related_order_id, "attempt": attempt}, message=str(exc), auto_commit=False)
        raise HTTPException(status_code=502, detail=f"Error consultando posicion del rack en RCS: {exc}")


def _location_by_position_code(db, position_code: str) -> Optional[Location]:
    code = str(position_code or "").strip()
    if not code:
        return None
    return db.execute(
        select(Location).where(func.lower(func.trim(func.coalesce(Location.code, ""))) == code.lower())
    ).scalar_one_or_none()


def _apply_rcs_rack_position(db, rack: Rack, position_code: str, *, source: str, related_order_id: Optional[int] = None, actor: str = "") -> tuple[bool, str, Optional[Location]]:
    if not _position_code_is_defined(position_code):
        return False, "posicion vacia", None
    target_cell = _location_by_position_code(db, position_code)
    if not target_cell:
        _append_debug_console_event(
            db,
            direction="received",
            module="rack_position_sync",
            payload={"action": "rack_position_unknown", "rack_id": rack.id, "rack_code": rack.code, "position_code": position_code, "related_order_id": related_order_id, "source": source},
            message=f"Posicion RCS {position_code} no existe en matriz local",
            auto_commit=False,
        )
        return False, "posicion sin celda local", None

    now = datetime.utcnow()
    old_cells = db.execute(select(Location).where(Location.rack_id == rack.id)).scalars().all()
    previous_cells = [{"id": loc.id, "x": loc.x, "y": loc.y, "code": loc.code} for loc in old_cells]
    displaced_rack_id = target_cell.rack_id if target_cell.rack_id and target_cell.rack_id != rack.id else None

    for loc in old_cells:
        if loc.id == target_cell.id:
            continue
        loc.rack_id = None
        _sync_location_status(loc)
        db.add(loc)
    db.flush()

    target_cell.rack_id = rack.id
    _sync_location_status(target_cell)
    _release_rack_if_no_active_orders(
        db,
        rack.id,
        related_order_id=related_order_id,
        reason="rack_position_sync",
        source=source,
        actor=actor,
    )
    rack.last_moved_at = now
    db.add(target_cell)
    db.add(rack)
    _append_debug_console_event(
        db,
        direction="received",
        module="rack_position_sync",
        payload={
            "action": "rack_position_applied",
            "rack_id": rack.id,
            "rack_code": rack.code,
            "position_code": position_code,
            "target_cell": _cell_payload(target_cell),
            "previous_cells": previous_cells,
            "displaced_rack_id": displaced_rack_id,
            "related_order_id": related_order_id,
            "source": source,
            "admin": actor or "",
            "at": now.isoformat(),
        },
        message=f"Rack {rack.code} actualizado a posicion RCS {position_code}",
        created_at=now,
        auto_commit=False,
    )
    logger.info("Rack position synced from RCS rack_id=%s rack_code=%s position_code=%s cell_id=%s source=%s order_id=%s", rack.id, rack.code, position_code, target_cell.id, source, related_order_id or "-")
    return True, "posicion aplicada", target_cell


def _rack_position_poll_worker(order_id: int, rack_id: int, *, source: str, actor: str = "") -> None:
    key = (int(order_id or 0), int(rack_id or 0))
    deadline = time.monotonic() + RACK_POSITION_POLL_TIMEOUT_SECONDS
    attempt = 0
    try:
        logger.info("Rack position polling started order_id=%s rack_id=%s source=%s timeout_seconds=%s", order_id, rack_id, source, RACK_POSITION_POLL_TIMEOUT_SECONDS)
        while time.monotonic() <= deadline:
            attempt += 1
            with SessionLocal() as db:
                order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
                rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
                if not order or not rack:
                    _append_debug_console_event(
                        db,
                        direction="received",
                        module="rack_position_poll",
                        payload={"action": "rack_position_poll_closed", "order_id": order_id, "rack_id": rack_id, "reason": "orden o rack no encontrado", "attempt": attempt},
                        message=f"Seguimiento posicion rack cerrado order_id={order_id} rack_id={rack_id}: orden o rack no encontrado",
                        auto_commit=False,
                    )
                    db.commit()
                    return
                try:
                    result = _query_pod_position_from_rcs(db, rack, module="rack_position_poll", related_order_id=order_id, attempt=attempt)
                    position_code = result.get("position_code") or ""
                    if _position_code_is_defined(position_code):
                        applied, apply_message, target_cell = _apply_rcs_rack_position(db, rack, position_code, source=source, related_order_id=order_id, actor=actor)
                        if applied:
                            order.comment = ((order.comment or "").strip() + f" | Posicion RCS actualizada: {position_code}").strip(" |")[:512]
                            order.updated_at = datetime.utcnow()
                            db.add(order)
                            db.commit()
                            logger.info("Rack position polling completed order_id=%s rack_id=%s position_code=%s cell_id=%s attempts=%s", order_id, rack_id, position_code, target_cell.id if target_cell else "-", attempt)
                            return
                        logger.info("Rack position polling received unresolved position order_id=%s rack_id=%s position_code=%s reason=%s attempt=%s", order_id, rack_id, position_code, apply_message, attempt)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning("Rack position polling attempt failed order_id=%s rack_id=%s attempt=%s error=%s", order_id, rack_id, attempt, exc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(RACK_POSITION_POLL_INTERVAL_SECONDS, remaining))
        with SessionLocal() as db:
            now = datetime.utcnow()
            order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
            if order and (order.status or "") in CANCEL_REQUEST_STATUSES:
                old_status = order.status or ""
                new_status = "undone" if old_status == "cancel_requested_undo" else "cancelled"
                order.status = new_status
                order.forced_local_close = 1
                order.forced_local_close_at = now
                order.forced_local_close_reason = "rack_position_poll_timeout"
                order.rcs_message = ((order.rcs_message or "").strip() + " | Timeout consultando posicion RCS del rack; cierre local automatico.").strip(" |")[:512]
                order.updated_at = now
                _audit_order_close(
                    db,
                    order,
                    previous_status=old_status,
                    new_status=new_status,
                    source="rack_position_poll_timeout",
                    reason="timeout_posicion_rcs_cancelacion",
                    actor=actor or source,
                    closed_at=now,
                    auto_commit=False,
                )
                _release_rack_if_no_active_orders(db, rack_id, related_order_id=order_id, reason="rack_position_poll_timeout", source="rack_position_poll_timeout", actor=actor or source)
                db.add(order)
            _append_debug_console_event(
                db,
                direction="received",
                module="rack_position_poll",
                payload={"action": "rack_position_poll_timeout", "order_id": order_id, "rack_id": rack_id, "attempts": attempt, "timeout_seconds": RACK_POSITION_POLL_TIMEOUT_SECONDS, "source": source, "admin": actor or "", "closed_order": bool(order and (order.status or "") not in CANCEL_REQUEST_STATUSES), "at": now.isoformat()},
                message=f"Seguimiento posicion rack cerrado por timeout order_id={order_id} rack_id={rack_id}",
                auto_commit=False,
            )
            db.commit()
        logger.warning("Rack position polling timed out order_id=%s rack_id=%s attempts=%s", order_id, rack_id, attempt)
    finally:
        with _rack_position_poll_lock:
            _rack_position_poll_active.discard(key)


def _start_rack_position_poll_after_cancel(order_id: Optional[int], rack_id: Optional[int], *, source: str, actor: str = "") -> bool:
    if not order_id or not rack_id:
        return False
    key = (int(order_id), int(rack_id))
    with _rack_position_poll_lock:
        if key in _rack_position_poll_active:
            return False
        _rack_position_poll_active.add(key)
    thread = threading.Thread(
        target=_rack_position_poll_worker,
        args=(int(order_id), int(rack_id)),
        kwargs={"source": source, "actor": actor},
        daemon=True,
        name=f"rack-position-poll-{order_id}-{rack_id}",
    )
    thread.start()
    return True


def _rcs_create_task_accepted(response: RcsTaskResponse) -> bool:
    message = str(response.message or "").strip().lower()
    if not message:
        return bool(response.code == 0)
    return "success" in message and "fail" not in message and "error" not in message



def _dispatch_movement_order(db, row: MovementOrder) -> MovementOrderDispatchOut:
    logger.info("Dispatching movement order id=%s order_code=%s", row.id, row.order_code)
    previous_status = row.status or ""
    generated_payload = _movement_order_json_payload(db, row)
    payload, _payload_source = _current_movement_order_payload(row, generated_payload)
    client = _get_rcs_client_from_settings(db)
    debug_base_url, debug_endpoint = _resolve_rcs_target(db, mode="create")
    token_code = getattr(client, "_token_code", "")
    if token_code and not payload.get("tokenCode"):
        payload["tokenCode"] = token_code
    row.dispatch_request_json = json.dumps(payload, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    logger.info("[CREATE TASK] order_id=%s order_code=%s source=api_internal status=%s rack_id=%s", row.id, row.order_code, row.status, row.rack_id)
    try:
        _append_debug_console_event(db, direction="sent", module="dispatch", base_url=debug_base_url, endpoint=debug_endpoint, payload=payload, message=f"Envío createTask para {row.order_code}")
        response = client.create_task(RcsTaskRequest.from_payload(payload))
        accepted = _rcs_create_task_accepted(response)
        _append_debug_console_event(db, direction="received", module="dispatch", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or f"Respuesta createTask para {row.order_code}")
        row.dispatch_status = "success" if accepted else "error"
        row.dispatch_response_json = json.dumps(response.raw, ensure_ascii=False)
        row.rcs_response_json = json.dumps(response.raw, ensure_ascii=False)
        row.remote_task_code = response.data or None
        row.req_code = response.reqCode or payload.get("reqCode") or None
        row.rcs_status = "sent" if accepted else "error"
        row.rcs_message = response.message or None
        row.dispatched_at = datetime.utcnow()
        row.rcs_last_update = row.dispatched_at
        if accepted and row.status == "pending_dispatch":
            row.status = "dispatched"
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(
            "Task sent to RCS | task_id=%s | order_code=%s | remote_task_code=%s | rack_id=%s | agv=%s | from=%s | to=%s | dispatch_status=%s",
            row.id,
            row.order_code,
            row.remote_task_code,
            row.rack_id,
            row.agv_code or "-",
            previous_status,
            row.status,
            row.dispatch_status,
        )
        return _movement_order_dispatch_out(row)
    except RcsError as exc:
        _append_debug_console_event(db, direction="received", module="dispatch", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=f"Error createTask para {row.order_code}: {exc}")
        row.dispatch_status = "error"
        row.dispatch_response_json = json.dumps({"error": str(exc)}, ensure_ascii=False)
        row.rcs_response_json = json.dumps({"error": str(exc)}, ensure_ascii=False)
        row.rcs_status = "error"
        row.rcs_message = str(exc)
        row.dispatched_at = datetime.utcnow()
        row.rcs_last_update = row.dispatched_at
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.exception("Task dispatch failed | task_id=%s | order_code=%s | rack_id=%s | agv=%s | error=%s", row.id, row.order_code, row.rack_id, row.agv_code or "-", exc)
        return _movement_order_dispatch_out(row)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".json", ".html")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app = FastAPI(title="AGV Orders App")
app.mount("/static", NoCacheStaticFiles(directory=os.path.join(_app_root(), "static")), name="static")
app.state.task_monitor = TaskMonitor(interval_seconds=3)


@app.middleware("http")
async def log_unhandled_http_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error(
            "%s %s - %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        raise


@app.on_event("startup")
def on_startup():
    ensure_log_file()
    logger.info("Application startup initiated")
    cleanup_legacy_schema()
    init_db()
    ensure_default_settings()
    ensure_special_materials()
    with SessionLocal() as db:
        _sync_all_location_statuses(db)
        _auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow())
        reserved_orphans = _diagnosis_rows(
            db,
            """
            SELECT r.id AS rack_id, r.code AS rack_code
            FROM racks r
            WHERE lower(trim(coalesce(r.status, ''))) IN ('reserved', 'reservado')
              AND NOT EXISTS (
                SELECT 1 FROM movement_orders mo
                WHERE mo.rack_id = r.id
                  AND mo.status IN ('pending_dispatch', 'dispatched', 'in_progress', 'cancel_requested_total', 'cancel_requested_undo')
              )
            ORDER BY r.id
            """,
        )
        for row in reserved_orphans:
            logger.warning("[CLEANUP WARNING] Rack reserved sin orden activa rack_id=%s rack_code=%s", row.get("rack_id"), row.get("rack_code"))
            _append_debug_console_event(
                db,
                direction="received",
                module="cleanup",
                payload={"source": "startup_recovery", "action": "startup_orphan_reserved_warning", "rack_id": row.get("rack_id"), "rack_code": row.get("rack_code"), "at": datetime.utcnow().isoformat()},
                message=f"[CLEANUP WARNING] Rack reserved sin orden activa rack_id={row.get('rack_id')}",
                auto_commit=False,
            )
        if reserved_orphans:
            db.commit()
    try:
        app.state.task_monitor.start()
    except Exception:
        logger.exception("Failed to start task monitor")
    logger.info("Application startup completed")


@app.on_event("startup")
async def on_startup_runtime_ws():
    app.state.runtime_broadcast_task = asyncio.create_task(_runtime_broadcast_loop())


@app.on_event("shutdown")
def on_shutdown():
    logger.info("Application shutdown initiated")
    try:
        app.state.task_monitor.stop()
    except Exception:
        logger.exception("Failed to stop task monitor")
    task = getattr(app.state, "runtime_broadcast_task", None)
    if task is not None:
        task.cancel()
    logger.info("Application shutdown completed")
    return None


@app.get("/")
def root():
    index_path = os.path.join(_app_root(), "static", "index.html")
    build_id = _software_build_id()
    build_script = f'<script>window.APP_BUILD_ID="{build_id}";console.log("APP_BUILD_ID","{build_id}");</script>'
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r'href="/static/styles\.css(?:\?v=[^"]*)?"', f'href="/static/styles.css?v={build_id}"', html)
        html = re.sub(r'src="/static/app\.js(?:\?v=[^"]*)?"', f'src="/static/app.js?v={build_id}"', html)
        if "window.APP_BUILD_ID" in html:
            html = re.sub(r"<script>window\.APP_BUILD_ID=.*?</script>", build_script, html, flags=re.DOTALL)
        else:
            html = html.replace("</head>", f"{build_script}</head>")
        headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}
        return HTMLResponse(html, headers=headers)
    except Exception:
        logger.exception("Failed to render versioned index.html build_id=%s path=%s", build_id, index_path)
        return FileResponse(index_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/api/logs/download")
def download_logs():
    log_path = ensure_log_file()
    log_dir = os.path.dirname(str(log_path))
    log_names = ("app.log", "error.log", "rcs.log", "programming.log", "io.log")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()
    included_logs = []
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for log_name in log_names:
            candidate = os.path.join(log_dir, log_name)
            if not os.path.isfile(candidate):
                continue
            zf.write(candidate, arcname=log_name)
            included_logs.append(log_name)
    logger.info("Log download requested files=%s", ",".join(included_logs))

    def _iter_zip_file():
        try:
            with open(tmp_path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)

    headers = {"Content-Disposition": 'attachment; filename="logs.zip"'}
    return StreamingResponse(_iter_zip_file(), media_type="application/zip", headers=headers)


@app.get("/api/grid-config", response_model=GridDisplayConfig)
def get_grid_config():
    with SessionLocal() as db:
        rows = int(get_setting(db, "display_rows", str(DB_GRID_H)))
        cols = int(get_setting(db, "display_cols", str(DB_GRID_W)))
        map_layout_mode = get_setting(db, "map_layout_mode", "grid")
        agv_overlay_scale_x = float(get_setting(db, "agv_overlay_scale_x", "1.0") or 1.0)
        agv_overlay_scale_y = float(get_setting(db, "agv_overlay_scale_y", "1.0") or 1.0)
        agv_overlay_offset_x = float(get_setting(db, "agv_overlay_offset_x", "0.0") or 0.0)
        agv_overlay_offset_y = float(get_setting(db, "agv_overlay_offset_y", "0.0") or 0.0)
        agv_overlay_rotation_deg = float(get_setting(db, "agv_overlay_rotation_deg", "0.0") or 0.0)
        agv_orientation_offset_deg = float(get_setting(db, "agv_orientation_offset_deg", "0.0") or 0.0)
        agv_overlay_mirror_x = int(float(get_setting(db, "agv_overlay_mirror_x", "0") or 0))
        agv_overlay_mirror_y = int(float(get_setting(db, "agv_overlay_mirror_y", "0") or 0))
        agv_icon_angle_mirror = int(float(get_setting(db, "agv_icon_angle_mirror", "0") or 0))
        runtime_refresh_seconds = float(get_setting(db, "runtime_refresh_seconds", "5.0") or 5.0)
        runtime_reconnect_seconds = float(get_setting(db, "runtime_reconnect_seconds", "3.0") or 3.0)
    return GridDisplayConfig(
        display_rows=rows,
        display_cols=cols,
        map_layout_mode=map_layout_mode,
        agv_overlay_scale_x=agv_overlay_scale_x,
        agv_overlay_scale_y=agv_overlay_scale_y,
        agv_overlay_offset_x=agv_overlay_offset_x,
        agv_overlay_offset_y=agv_overlay_offset_y,
        agv_overlay_rotation_deg=agv_overlay_rotation_deg,
        agv_orientation_offset_deg=agv_orientation_offset_deg,
        agv_overlay_mirror_x=agv_overlay_mirror_x,
        agv_overlay_mirror_y=agv_overlay_mirror_y,
        agv_icon_angle_mirror=agv_icon_angle_mirror,
        runtime_refresh_seconds=runtime_refresh_seconds,
        runtime_reconnect_seconds=runtime_reconnect_seconds,
    )


@app.post("/api/admin/grid-config", response_model=GridDisplayConfig)
def set_grid_config(payload: GridDisplayConfig, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin updating grid config rows=%s cols=%s map_layout_mode=%s", payload.display_rows, payload.display_cols, payload.map_layout_mode)
    with SessionLocal() as db:
        set_setting(db, "display_rows", str(payload.display_rows))
        set_setting(db, "display_cols", str(payload.display_cols))
        set_setting(db, "map_layout_mode", payload.map_layout_mode)
        set_setting(db, "agv_overlay_scale_x", str(payload.agv_overlay_scale_x))
        set_setting(db, "agv_overlay_scale_y", str(payload.agv_overlay_scale_y))
        set_setting(db, "agv_overlay_offset_x", str(payload.agv_overlay_offset_x))
        set_setting(db, "agv_overlay_offset_y", str(payload.agv_overlay_offset_y))
        set_setting(db, "agv_overlay_rotation_deg", str(payload.agv_overlay_rotation_deg))
        set_setting(db, "agv_orientation_offset_deg", str(payload.agv_orientation_offset_deg))
        set_setting(db, "agv_overlay_mirror_x", str(int(payload.agv_overlay_mirror_x or 0)))
        set_setting(db, "agv_overlay_mirror_y", str(int(payload.agv_overlay_mirror_y or 0)))
        set_setting(db, "agv_icon_angle_mirror", str(int(payload.agv_icon_angle_mirror or 0)))
        set_setting(db, "runtime_refresh_seconds", str(payload.runtime_refresh_seconds))
        set_setting(db, "runtime_reconnect_seconds", str(payload.runtime_reconnect_seconds))
    return payload


@app.get("/api/background", response_model=BackgroundOut)
def get_background():
    with SessionLocal() as db:
        filename = get_setting(db, "bg_filename", "")
        scale_x = float(get_setting(db, "bg_scale_x", "1.0") or 1.0)
        scale_y = float(get_setting(db, "bg_scale_y", "1.0") or 1.0)
        offset_x = float(get_setting(db, "bg_offset_x", "0.0") or 0.0)
        offset_y = float(get_setting(db, "bg_offset_y", "0.0") or 0.0)
        url = f"/static/uploads/{filename}" if filename else None
    return BackgroundOut(filename=filename, url=url, scale_x=scale_x, scale_y=scale_y, offset_x=offset_x, offset_y=offset_y, scale=(scale_x + scale_y) / 2.0)


@app.post("/api/admin/background", response_model=BackgroundOut)
async def admin_upload_background(file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin uploading background filename=%s", getattr(file, "filename", ""))
    ctype = (file.content_type or "").lower()
    if not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="Solo se permiten imágenes")
    ext = ".png"
    if "." in (file.filename or ""):
        raw_ext = "." + file.filename.rsplit(".", 1)[-1].lower()
        if raw_ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            ext = raw_ext
    safe_name = f"{BG_BASENAME}{ext}"
    out_path = os.path.join(UPLOAD_DIR, safe_name)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    with open(out_path, "wb") as f:
        f.write(data)
    with SessionLocal() as db:
        set_setting(db, "bg_filename", safe_name)
        scale_x = float(get_setting(db, "bg_scale_x", "1.0") or 1.0)
        scale_y = float(get_setting(db, "bg_scale_y", "1.0") or 1.0)
        offset_x = float(get_setting(db, "bg_offset_x", "0.0") or 0.0)
        offset_y = float(get_setting(db, "bg_offset_y", "0.0") or 0.0)
    return BackgroundOut(filename=safe_name, url=f"/static/uploads/{safe_name}", scale_x=scale_x, scale_y=scale_y, offset_x=offset_x, offset_y=offset_y, scale=(scale_x + scale_y) / 2.0)


@app.post("/api/admin/background/transform", response_model=BackgroundOut)
def admin_set_background_transform(payload: BackgroundTransformIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin updating background transform")
    with SessionLocal() as db:
        set_setting(db, "bg_scale_x", str(payload.scale_x))
        set_setting(db, "bg_scale_y", str(payload.scale_y))
        set_setting(db, "bg_scale", str((payload.scale_x + payload.scale_y) / 2.0))
        set_setting(db, "bg_offset_x", str(payload.offset_x))
        set_setting(db, "bg_offset_y", str(payload.offset_y))
        filename = get_setting(db, "bg_filename", "")
    return BackgroundOut(filename=filename, url=f"/static/uploads/{filename}" if filename else None, scale_x=payload.scale_x, scale_y=payload.scale_y, offset_x=payload.offset_x, offset_y=payload.offset_y, scale=(payload.scale_x + payload.scale_y) / 2.0)


def _software_update_max_size_mb() -> int:
    raw = os.getenv("SOFTWARE_UPDATE_MAX_MB", "")
    try:
        if raw.strip():
            return max(1, min(2048, int(float(raw))))
    except Exception:
        pass
    return 200


def _software_update_int_setting(key: str, default: int, *, env_key: str = "", minimum: int = 1, maximum: int = 100000) -> int:
    raw = os.getenv(env_key or key.upper(), "")
    try:
        if raw.strip():
            return max(minimum, min(maximum, int(float(raw))))
    except Exception:
        pass
    with SessionLocal() as db:
        try:
            value = get_setting(db, key, str(default))
            return max(minimum, min(maximum, int(float(value or default))))
        except Exception:
            return default


def _software_update_keep_backups() -> int:
    return _software_update_int_setting("software_update_keep_backups", 5, minimum=1, maximum=1000)


def _software_update_max_uncompressed_mb() -> int:
    return _software_update_int_setting("software_update_max_uncompressed_mb", 500, env_key="SOFTWARE_UPDATE_MAX_UNCOMPRESSED_MB", minimum=1, maximum=10240)


def _is_dangerous_zip_path(raw_name: str) -> bool:
    name = (raw_name or "").replace("\\", "/").strip()
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return True
    path = PurePosixPath(name)
    return any(part in {"", ".", ".."} for part in path.parts)


def _is_blocked_update_path(rel_path: str) -> Optional[str]:
    normalized = rel_path.replace("\\", "/").strip("/")
    if not normalized:
        return "ruta vacia"
    parts = [p for p in normalized.split("/") if p]
    name = parts[-1].lower()
    lower_parts = [p.lower() for p in parts]
    if name.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")):
        return "base de datos local"
    if name == ".env" or name.startswith(".env."):
        return "archivo .env local"
    if name.startswith(".") and name not in {".gitignore"}:
        return "archivo oculto protegido"
    if any(p in {"data", "logs", "backups", ".venv", "venv", "__pycache__", ".git"} for p in lower_parts):
        return "directorio local protegido"
    if name in {"config.json", "config.local.json", "local_config.json", "settings.local.json"}:
        return "configuracion local protegida"
    return None


def _zip_entry_is_symlink_or_special(info: zipfile.ZipInfo) -> Optional[str]:
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0:
        return None
    if stat.S_ISLNK(mode):
        return "symlink bloqueado"
    if not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
        return "archivo especial bloqueado"
    return None


def _software_update_staging_root() -> str:
    return os.path.abspath(os.path.join(_app_root(), "updates", "staging"))


def _software_update_latest_metadata_path() -> str:
    return os.path.abspath(os.path.join(_app_root(), "updates", "latest_validated.json"))


def _software_update_validation_metadata_path(staging_dir: str) -> str:
    return os.path.abspath(os.path.join(staging_dir, "validation.json"))


def _write_software_update_validation_metadata(staging_dir: str, metadata: dict) -> None:
    os.makedirs(os.path.dirname(_software_update_latest_metadata_path()), exist_ok=True)
    with open(_software_update_validation_metadata_path(staging_dir), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    with open(_software_update_latest_metadata_path(), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def _remove_path_quietly(path: str) -> None:
    with contextlib.suppress(Exception):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


def _prune_old_paths(paths: List[str], keep: int) -> None:
    existing = [p for p in paths if os.path.exists(p)]
    existing.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in existing[keep:]:
        _remove_path_quietly(path)


def _cleanup_software_update_staging() -> None:
    staging_root = _software_update_staging_root()
    os.makedirs(staging_root, exist_ok=True)
    keep = _software_update_keep_backups()
    now = datetime.utcnow().timestamp()
    max_age_seconds = 7 * 24 * 60 * 60
    staging_dirs = []
    for name in os.listdir(staging_root):
        path = os.path.abspath(os.path.join(staging_root, name))
        if os.path.commonpath([staging_root, path]) != staging_root or not os.path.isdir(path):
            continue
        metadata_path = _software_update_validation_metadata_path(path)
        remove = False
        if not os.path.exists(metadata_path):
            remove = True
        else:
            with contextlib.suppress(Exception):
                metadata = _read_json_file(metadata_path)
                if metadata.get("ok") is not True:
                    remove = True
        if now - os.path.getmtime(path) > max_age_seconds:
            remove = True
        if remove:
            _remove_path_quietly(path)
        else:
            staging_dirs.append(path)
    _prune_old_paths(staging_dirs, keep)


def _read_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _software_build_info_path(root_dir: Optional[str] = None) -> str:
    return os.path.abspath(os.path.join(root_dir or _app_root(), SOFTWARE_BUILD_INFO_REL_PATH))


def _read_software_build_info(root_dir: Optional[str] = None) -> dict:
    path = _software_build_info_path(root_dir)
    if not os.path.exists(path):
        return {}
    try:
        return _read_json_file(path)
    except Exception:
        return {}


def _software_build_id(root_dir: Optional[str] = None) -> str:
    info = _read_software_build_info(root_dir)
    build_id = str(info.get("build_id") or info.get("applied_at") or "").strip()
    if build_id:
        return re.sub(r"[^A-Za-z0-9_.:-]", "", build_id)[:80] or "unknown"
    try:
        mtimes = []
        base = root_dir or _app_root()
        for rel_path in ("main.py", "monitor_service.py", "fifo_service.py", "models.py", "rcs_client.py", "static/app.js", "static/styles.css", "static/index.html"):
            path = os.path.join(base, rel_path)
            if os.path.exists(path):
                mtimes.append(os.path.getmtime(path))
        if mtimes:
            return datetime.utcfromtimestamp(max(mtimes)).strftime("%Y%m%d%H%M%S")
    except Exception:
        pass
    return "unknown"


def _write_software_build_info(root_dir: str, *, build_id: str, applied_files: List[str], backup_path: str, staging_id: str = "", staging_dir: str = "") -> dict:
    info = {
        "build_id": build_id,
        "applied_at": datetime.utcnow().isoformat() + "Z",
        "app_root": os.path.abspath(root_dir),
        "main_file": os.path.abspath(__file__),
        "pid": os.getpid(),
        "staging_id": staging_id,
        "staging_dir": os.path.abspath(staging_dir) if staging_dir else "",
        "backup_path": os.path.abspath(backup_path) if backup_path else "",
        "applied_files": list(applied_files),
    }
    path = _software_build_info_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def _resolve_validated_software_staging(staging_id: Optional[str]) -> tuple[str, dict]:
    if staging_id:
        if os.path.basename(staging_id) != staging_id or staging_id in {".", ".."}:
            raise HTTPException(status_code=400, detail="staging_id invalido")
        staging_dir = os.path.abspath(os.path.join(_software_update_staging_root(), staging_id))
        if os.path.commonpath([_software_update_staging_root(), staging_dir]) != _software_update_staging_root():
            raise HTTPException(status_code=400, detail="staging fuera de updates/staging")
        metadata_path = _software_update_validation_metadata_path(staging_dir)
        if not os.path.exists(metadata_path):
            raise HTTPException(status_code=400, detail="staging no validado")
        metadata = _read_json_file(metadata_path)
    else:
        latest_path = _software_update_latest_metadata_path()
        if not os.path.exists(latest_path):
            raise HTTPException(status_code=400, detail="no existe staging validado")
        metadata = _read_json_file(latest_path)
        staging_dir = os.path.abspath(str(metadata.get("staging_dir") or ""))

    if not os.path.exists(staging_dir) or not os.path.isdir(staging_dir):
        raise HTTPException(status_code=400, detail="staging_dir no existe")
    if os.path.commonpath([_software_update_staging_root(), staging_dir]) != _software_update_staging_root():
        raise HTTPException(status_code=400, detail="staging fuera de updates/staging")
    if metadata.get("ok") is not True:
        raise HTTPException(status_code=400, detail="staging no fue validado correctamente")
    if metadata.get("blocked_files"):
        raise HTTPException(status_code=400, detail="staging contiene archivos bloqueados")
    return staging_dir, metadata


def _is_protected_software_path(rel_path: str) -> Optional[str]:
    normalized = rel_path.replace("\\", "/").strip("/")
    blocked = _is_blocked_update_path(normalized)
    if blocked:
        return blocked
    parts = [p.lower() for p in normalized.split("/") if p]
    if any(p in {"updates", "backups", "logs", "data", ".venv", "venv", "__pycache__"} for p in parts):
        return "ruta protegida de la aplicacion"
    return None


def _run_app_py_compile(root_dir: str) -> tuple[bool, List[str], List[str]]:
    warnings: List[str] = []
    errors: List[str] = []
    compile_targets = []
    for name in ("main.py", "models.py", "monitor_service.py", "fifo_service.py"):
        if os.path.exists(os.path.join(root_dir, name)):
            compile_targets.append(name)
        else:
            warnings.append(f"{name} no existe; py_compile omitido para ese archivo.")
    if not compile_targets:
        return True, warnings, errors
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", *compile_targets],
            cwd=root_dir,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            errors.append("py_compile fallo.")
            if proc.stderr:
                errors.append(proc.stderr.strip()[:4000])
            return False, warnings, errors
    except subprocess.TimeoutExpired:
        errors.append("py_compile excedio el tiempo maximo de validacion.")
        return False, warnings, errors
    except Exception as exc:
        errors.append(f"No se pudo ejecutar py_compile: {exc}")
        return False, warnings, errors
    return True, warnings, errors


def _create_software_backup(app_root: str, timestamp: str) -> str:
    backup_dir = os.path.abspath(os.path.join(app_root, "backups", "software"))
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.abspath(os.path.join(backup_dir, f"app_backup_{timestamp}.zip"))
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(app_root):
            rel_root = os.path.relpath(root, app_root)
            rel_root = "" if rel_root == "." else rel_root.replace("\\", "/")
            dirs[:] = [
                d for d in dirs
                if not _is_protected_software_path(f"{rel_root}/{d}".strip("/"))
                and d.lower() not in {".git"}
            ]
            for filename in files:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, app_root).replace("\\", "/")
                if _is_protected_software_path(rel_path):
                    continue
                zf.write(full_path, rel_path)
    _prune_old_paths(
        [os.path.join(backup_dir, name) for name in os.listdir(backup_dir) if name.startswith("app_backup_") and name.endswith(".zip")],
        _software_update_keep_backups(),
    )
    return backup_path


def _restore_software_backup(app_root: str, backup_path: str, created_files: List[str]) -> None:
    for rel_path in created_files:
        target = os.path.abspath(os.path.join(app_root, rel_path))
        if os.path.commonpath([app_root, target]) != app_root:
            continue
        with contextlib.suppress(FileNotFoundError):
            os.remove(target)
    with zipfile.ZipFile(backup_path) as zf:
        for info in zf.infolist():
            rel_path = (info.filename or "").replace("\\", "/").strip("/")
            if not rel_path or info.is_dir() or _is_dangerous_zip_path(rel_path) or _is_protected_software_path(rel_path):
                continue
            target = os.path.abspath(os.path.join(app_root, rel_path))
            if os.path.commonpath([app_root, target]) != app_root:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _software_restart_config() -> tuple[str, str, str]:
    current_os = platform.system()
    with SessionLocal() as db:
        mode = (get_setting(db, "software_restart_mode", "auto") or "auto").strip().lower()
        service_name = (
            get_setting(db, "software_restart_service_name", "")
            or get_setting(db, "software_update_systemd_service", "agv-app")
            or "agv-app"
        ).strip()
        script_name = (get_setting(db, "software_restart_script", "restart_app.bat") or "restart_app.bat").strip()
    if mode not in {"auto", "disabled", "systemd", "windows_script"}:
        mode = "auto"
    return mode, service_name, script_name


def _is_windows_development_runtime() -> bool:
    argv = " ".join(str(arg).lower() for arg in sys.argv)
    if "uvicorn" in argv:
        return True
    if getattr(sys, "frozen", False):
        return False
    dev_env_names = (
        "TERM_PROGRAM",
        "VSCODE_PID",
        "VSCODE_CWD",
        "VSCODE_IPC_HOOK_CLI",
        "WT_SESSION",
    )
    if any(os.getenv(name) for name in dev_env_names):
        return True
    return sys.stdin.isatty() or sys.stdout.isatty()


def _detect_software_restart_mode(configured_mode: str, current_os: str, service_name: str, script_name: str) -> tuple[str, str]:
    if configured_mode != "auto":
        return configured_mode, ""
    if current_os == "Windows":
        if _is_windows_development_runtime():
            return "disabled", "Entorno de desarrollo detectado. Reinicia manualmente la aplicación."
        try:
            _resolve_restart_script(script_name)
            return "windows_script", ""
        except Exception:
            return "disabled", "No se encontró script de reinicio válido. Reinicia manualmente la aplicación."
    if current_os == "Linux":
        if not _validate_systemd_service_name(service_name):
            return "disabled", "Servicio systemd inválido. Reinicia manualmente la aplicación."
        try:
            status_proc = _run_systemctl_check(["status", service_name, "--no-pager"])
            active_proc = _run_systemctl_check(["is-active", service_name])
            missing = status_proc.returncode == 4 or "could not be found" in (status_proc.stderr or "").lower() or "not-found" in (status_proc.stdout or "").lower()
            if not missing and active_proc.returncode in (0, 3):
                return "systemd", ""
        except Exception:
            pass
        return "disabled", "No se detectó servicio systemd disponible. Reinicia manualmente la aplicación."
    return "disabled", "Sistema operativo no soportado. Reinicia manualmente la aplicación."


def _validate_systemd_service_name(service_name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.@:-]+", service_name or ""))


def _run_systemctl_check(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args],
        text=True,
        capture_output=True,
        timeout=8,
    )


def _resolve_restart_script(script_name: str) -> str:
    app_root = _app_root()
    script_path = os.path.abspath(script_name if os.path.isabs(script_name) else os.path.join(app_root, script_name))
    if os.path.commonpath([app_root, script_path]) != app_root:
        raise ValueError("script fuera del directorio de la app")
    if not script_path.lower().endswith((".bat", ".cmd")):
        raise ValueError("software_restart_script debe ser .bat o .cmd")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"script no encontrado: {script_path}")
    return script_path


@app.post("/api/admin/software-update/validate", response_model=SoftwareUpdateValidationOut)
async def admin_validate_software_update(file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    _cleanup_software_update_staging()
    max_size_mb = _software_update_max_size_mb()
    max_uncompressed_mb = _software_update_max_uncompressed_mb()
    max_size_bytes = max_size_mb * 1024 * 1024
    max_uncompressed_bytes = max_uncompressed_mb * 1024 * 1024
    filename = os.path.basename(file.filename or "")
    detected_files: List[str] = []
    blocked_files: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []
    staging_dir = ""
    tmp_path = ""
    upload_size_bytes = 0

    if not filename.lower().endswith(".zip"):
        errors.append("El archivo debe tener extension .zip.")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp_path = tmp.name
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                upload_size_bytes = total
                if total > max_size_bytes:
                    errors.append(f"El ZIP supera el tamano maximo permitido de {max_size_mb} MB.")
                    break
                tmp.write(chunk)
    except Exception as exc:
        logger.exception("[SOFTWARE UPDATE VALIDATE] receive_failed file=%s", filename)
        errors.append(f"No se pudo recibir el archivo: {exc}")

    if errors:
        if tmp_path:
            with contextlib.suppress(Exception):
                os.remove(tmp_path)
        logger.info(
            "[SOFTWARE UPDATE VALIDATE] ok=false file=%s size_bytes=%s staging_dir=%s detected=%s blocked=%s errors=%s warnings=%s duration_ms=%s",
            filename,
            upload_size_bytes,
            staging_dir,
            len(detected_files),
            len(blocked_files),
            len(errors),
            len(warnings),
            int((time.monotonic() - started) * 1000),
        )
        return SoftwareUpdateValidationOut(ok=False, max_size_mb=max_size_mb, detected_files=detected_files, blocked_files=blocked_files, errors=errors, warnings=warnings)

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    staging_id = timestamp
    staging_dir = os.path.abspath(os.path.join(_software_update_staging_root(), timestamp))
    os.makedirs(staging_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(tmp_path) as zf:
            has_main = False
            has_requirements = False
            has_static = False
            safe_infos = []
            root_prefix = ""
            normalized_entries = []

            for info in zf.infolist():
                raw_name = info.filename or ""
                normalized = raw_name.replace("\\", "/").strip("/")
                if not normalized:
                    continue
                normalized_entries.append((info, raw_name, normalized))

            total_uncompressed_size = sum(max(0, int(info.file_size or 0)) for info, _raw_name, _normalized in normalized_entries if not info.is_dir())
            if total_uncompressed_size > max_uncompressed_bytes:
                errors.append(f"El ZIP supera el tamano maximo descomprimido permitido de {max_uncompressed_mb} MB.")

            file_entries = [(info, raw_name, normalized) for info, raw_name, normalized in normalized_entries if not info.is_dir()]
            first_segments = {normalized.split("/", 1)[0] for _info, _raw_name, normalized in file_entries if "/" in normalized}
            if file_entries and len(first_segments) == 1 and all(normalized.startswith(f"{next(iter(first_segments))}/") for _info, _raw_name, normalized in file_entries):
                root_prefix = next(iter(first_segments))
                logger.info("[software-update] detected root folder: %s", root_prefix)

            for info, raw_name, normalized in normalized_entries:
                if _is_dangerous_zip_path(raw_name):
                    blocked_files.append(raw_name)
                    errors.append(f"Ruta peligrosa bloqueada: {raw_name}")
                    continue
                special_reason = _zip_entry_is_symlink_or_special(info)
                if special_reason:
                    blocked_files.append(normalized)
                    errors.append(f"Archivo bloqueado ({special_reason}): {normalized}")
                    continue
                relative_name = normalized
                if root_prefix and (normalized == root_prefix or normalized.startswith(f"{root_prefix}/")):
                    relative_name = normalized[len(root_prefix):].strip("/")
                    if not relative_name:
                        continue
                if info.is_dir():
                    if relative_name == "static" or relative_name.startswith("static/"):
                        has_static = True
                    continue
                detected_files.append(relative_name)
                if relative_name == "main.py":
                    has_main = True
                if relative_name == "requirements.txt":
                    has_requirements = True
                if relative_name.startswith("static/"):
                    has_static = True
                blocked_reason = _is_blocked_update_path(relative_name)
                if blocked_reason:
                    blocked_files.append(relative_name)
                    errors.append(f"Archivo bloqueado ({blocked_reason}): {relative_name}")
                    continue
                safe_infos.append((info, relative_name))

            if not has_main:
                errors.append("El ZIP debe contener main.py.")
            if not has_requirements:
                errors.append("El ZIP debe contener requirements.txt.")
            if not has_static:
                errors.append("El ZIP debe contener static/.")

            if not errors:
                for info, normalized in safe_infos:
                    target = os.path.abspath(os.path.join(staging_dir, normalized))
                    if os.path.commonpath([staging_dir, target]) != staging_dir:
                        blocked_files.append(normalized)
                        errors.append(f"Ruta fuera del staging bloqueada: {normalized}")
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile:
        errors.append("El archivo no es un ZIP valido.")
    except Exception as exc:
        logger.exception("[SOFTWARE UPDATE VALIDATE] zip_validation_failed file=%s staging_dir=%s", filename, staging_dir)
        errors.append(f"Error validando ZIP: {exc}")
    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                os.remove(tmp_path)

    compile_targets = [name for name in ("main.py", "models.py", "monitor_service.py", "fifo_service.py") if os.path.exists(os.path.join(staging_dir, name))]
    if not errors and compile_targets:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", *compile_targets],
                cwd=staging_dir,
                text=True,
                capture_output=True,
                timeout=60,
            )
            if proc.returncode != 0:
                errors.append("py_compile fallo.")
                if proc.stderr:
                    errors.append(proc.stderr.strip()[:4000])
        except subprocess.TimeoutExpired:
            errors.append("py_compile excedio el tiempo maximo de validacion.")
        except Exception as exc:
            errors.append(f"No se pudo ejecutar py_compile: {exc}")
    elif not errors:
        warnings.append("No se encontraron archivos Python para compilar en el staging.")

    ok = not errors
    if ok:
        _write_software_update_validation_metadata(staging_dir, {
            "ok": True,
            "staging_id": staging_id,
            "staging_dir": staging_dir,
            "filename": filename,
            "validated_at": datetime.utcnow().isoformat(),
            "detected_files": detected_files,
            "blocked_files": blocked_files,
            "warnings": warnings,
            "errors": errors,
        })
        _cleanup_software_update_staging()
    else:
        _remove_path_quietly(staging_dir)
    logger.info(
        "[SOFTWARE UPDATE VALIDATE] ok=%s file=%s size_bytes=%s staging_dir=%s detected=%s blocked=%s errors=%s warnings=%s duration_ms=%s",
        ok,
        filename,
        upload_size_bytes,
        staging_dir,
        len(detected_files),
        len(blocked_files),
        len(errors),
        len(warnings),
        int((time.monotonic() - started) * 1000),
    )
    return SoftwareUpdateValidationOut(
        ok=ok,
        staging_id=staging_id if ok else "",
        staging_dir=staging_dir,
        max_size_mb=max_size_mb,
        detected_files=detected_files,
        blocked_files=blocked_files,
        errors=errors,
        warnings=warnings,
    )


@app.post("/api/admin/software-update/apply", response_model=SoftwareUpdateApplyOut)
def admin_apply_software_update(body: Optional[SoftwareUpdateApplyIn] = None, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    if not SOFTWARE_UPDATE_LOCK.acquire(blocking=False):
        logger.info("[SOFTWARE UPDATE] source=admin_upload result=skipped reason=lock_busy")
        return SoftwareUpdateApplyOut(ok=False, errors=["Ya hay una operacion de actualizacion en curso."])
    body = body or SoftwareUpdateApplyIn()
    try:
        app_root = _app_root()
        applied_files: List[str] = []
        skipped_files: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []
        rollback = False
        backup_path = ""
        staging_dir = ""
        build_info: dict = {}

        try:
            staging_dir, metadata = _resolve_validated_software_staging(body.staging_id)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[SOFTWARE UPDATE] source=admin_upload result=fail reason=resolve_staging")
            raise HTTPException(status_code=400, detail=f"no se pudo resolver staging validado: {exc}")

        logger.info(
            "[SOFTWARE UPDATE] apply_start cwd=%s main_file=%s app_root=%s pid=%s staging_id=%s staging_dir=%s",
            os.getcwd(),
            os.path.abspath(__file__),
            app_root,
            os.getpid(),
            body.staging_id or "",
            staging_dir,
        )

        required_errors = []
        if not os.path.exists(os.path.join(staging_dir, "main.py")):
            required_errors.append("staging no contiene main.py")
        if not os.path.exists(os.path.join(staging_dir, "requirements.txt")):
            required_errors.append("staging no contiene requirements.txt")
        if not os.path.isdir(os.path.join(staging_dir, "static")):
            required_errors.append("staging no contiene static/")
        if required_errors:
            logger.info("[SOFTWARE UPDATE] source=admin_upload staging_dir=%s backup=%s result=fail reason=missing_required errors=%s duration_ms=%s", staging_dir, backup_path, len(required_errors), int((time.monotonic() - started) * 1000))
            return SoftwareUpdateApplyOut(ok=False, errors=required_errors, warnings=warnings)

        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        old_requirements = None
        current_requirements_path = os.path.join(app_root, "requirements.txt")
        staging_requirements_path = os.path.join(staging_dir, "requirements.txt")
        if os.path.exists(current_requirements_path) and os.path.exists(staging_requirements_path):
            with contextlib.suppress(Exception):
                with open(current_requirements_path, "rb") as f:
                    old_requirements = f.read()

        try:
            backup_path = _create_software_backup(app_root, timestamp)
            logger.info("[SOFTWARE UPDATE] backup_created path=%s size_bytes=%s", backup_path, os.path.getsize(backup_path) if backup_path and os.path.exists(backup_path) else 0)
        except Exception as exc:
            errors.append(f"No se pudo crear backup obligatorio: {exc}")
            logger.exception("[SOFTWARE UPDATE] source=admin_upload staging_dir=%s backup=%s result=fail reason=backup_error", staging_dir, backup_path)
            return SoftwareUpdateApplyOut(ok=False, backup_path=backup_path, errors=errors, warnings=warnings)

        existed_before: dict[str, bool] = {}
        created_files: List[str] = []

        try:
            for root, dirs, files in os.walk(staging_dir):
                rel_root = os.path.relpath(root, staging_dir)
                rel_root = "" if rel_root == "." else rel_root.replace("\\", "/")
                dirs[:] = [d for d in dirs if not _is_protected_software_path(f"{rel_root}/{d}".strip("/"))]
                for filename in files:
                    source_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(source_path, staging_dir).replace("\\", "/")
                    if rel_path == "validation.json":
                        skipped_files.append(rel_path)
                        continue
                    blocked_reason = _is_protected_software_path(rel_path)
                    if blocked_reason:
                        skipped_files.append(f"{rel_path} ({blocked_reason})")
                        continue
                    target_path = os.path.abspath(os.path.join(app_root, rel_path))
                    if os.path.commonpath([app_root, target_path]) != app_root:
                        skipped_files.append(f"{rel_path} (fuera de app)")
                        continue
                    existed = os.path.exists(target_path)
                    existed_before[rel_path] = existed
                    if not existed:
                        created_files.append(rel_path)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    shutil.copy2(source_path, target_path)
                    applied_files.append(rel_path)

            if old_requirements is not None and os.path.exists(current_requirements_path):
                with contextlib.suppress(Exception):
                    with open(current_requirements_path, "rb") as f:
                        if f.read() != old_requirements:
                            warnings.append("requirements.txt cambio; instalacion manual o fase posterior requerida.")

            build_id = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
            build_info_path = _software_build_info_path(app_root)
            existed_before[SOFTWARE_BUILD_INFO_REL_PATH] = os.path.exists(build_info_path)
            if not existed_before[SOFTWARE_BUILD_INFO_REL_PATH]:
                created_files.append(SOFTWARE_BUILD_INFO_REL_PATH)
            if SOFTWARE_BUILD_INFO_REL_PATH not in applied_files:
                applied_files.append(SOFTWARE_BUILD_INFO_REL_PATH)
            build_info = _write_software_build_info(
                app_root,
                build_id=build_id,
                applied_files=applied_files,
                backup_path=backup_path,
                staging_id=str(metadata.get("staging_id") or body.staging_id or ""),
                staging_dir=staging_dir,
            )

            compile_ok, compile_warnings, compile_errors = _run_app_py_compile(app_root)
            warnings.extend(compile_warnings)
            if not compile_ok:
                errors.extend(compile_errors)
                rollback = True
                _restore_software_backup(app_root, backup_path, [path for path in created_files if not existed_before.get(path)])
                logger.info("[SOFTWARE UPDATE] source=admin_upload staging_dir=%s backup=%s result=rollback reason=py_compile applied=%s skipped=%s errors=%s warnings=%s duration_ms=%s", staging_dir, backup_path, len(applied_files), len(skipped_files), len(errors), len(warnings), int((time.monotonic() - started) * 1000))
                return SoftwareUpdateApplyOut(
                    ok=False,
                    applied_files=applied_files,
                    skipped_files=skipped_files,
                    backup_path=backup_path,
                    build_info=build_info,
                    rollback=True,
                    warnings=warnings,
                    errors=errors,
                )
        except Exception as exc:
            errors.append(f"Error aplicando update: {exc}")
            rollback = True
            with contextlib.suppress(Exception):
                _restore_software_backup(app_root, backup_path, [path for path in created_files if not existed_before.get(path)])
            logger.exception("[SOFTWARE UPDATE] source=admin_upload staging_dir=%s backup=%s result=rollback reason=apply_error applied=%s skipped=%s duration_ms=%s", staging_dir, backup_path, len(applied_files), len(skipped_files), int((time.monotonic() - started) * 1000))
            return SoftwareUpdateApplyOut(
                ok=False,
                applied_files=applied_files,
                skipped_files=skipped_files,
                backup_path=backup_path,
                build_info=build_info,
                rollback=True,
                warnings=warnings,
                errors=errors,
            )

        logger.info(
            "[SOFTWARE UPDATE] source=admin_upload staging_dir=%s app_root=%s main_file=%s pid=%s backup=%s build_id=%s result=success applied=%s skipped=%s warnings=%s duration_ms=%s",
            staging_dir,
            app_root,
            os.path.abspath(__file__),
            os.getpid(),
            backup_path,
            build_info.get("build_id", ""),
            len(applied_files),
            len(skipped_files),
            len(warnings),
            int((time.monotonic() - started) * 1000),
        )
        return SoftwareUpdateApplyOut(
            ok=True,
            applied_files=applied_files,
            skipped_files=skipped_files,
            backup_path=backup_path,
            build_info=build_info,
            rollback=rollback,
            warnings=warnings,
            errors=errors,
        )
    finally:
        SOFTWARE_UPDATE_LOCK.release()


def _schedule_software_restart() -> SoftwareUpdateRestartOut:
    current_os = platform.system()
    configured_mode, service_name, script_name = _software_restart_config()
    mode, detected_message = _detect_software_restart_mode(configured_mode, current_os, service_name, script_name)
    app_root = _app_root()
    service_command = f"systemctl restart {service_name} --no-block"
    diagnostics = {
        "cwd": os.getcwd(),
        "main_file": os.path.abspath(__file__),
        "app_root": app_root,
        "pid": os.getpid(),
        "configured_mode": configured_mode,
        "detected_mode": mode,
        "os": current_os,
    }
    logger.info(
        "[SOFTWARE UPDATE RESTART] cwd=%s main_file=%s app_root=%s pid=%s os=%s configured_mode=%s detected_mode=%s",
        diagnostics["cwd"],
        diagnostics["main_file"],
        diagnostics["app_root"],
        diagnostics["pid"],
        current_os,
        configured_mode,
        mode,
    )

    if mode == "disabled":
        message = detected_message or "Reinicio automático deshabilitado. Reinicia manualmente la aplicación."
        logger.warning(
            "[SOFTWARE UPDATE RESTART] cwd=%s main_file=%s app_root=%s pid=%s os=%s configured_mode=%s detected_mode=%s command=%s result=disabled message=%s",
            diagnostics["cwd"],
            diagnostics["main_file"],
            diagnostics["app_root"],
            diagnostics["pid"],
            current_os,
            configured_mode,
            mode,
            "",
            message,
        )
        return SoftwareUpdateRestartOut(ok=False, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, service=service_name, command="", message=message, diagnostics=diagnostics, errors=[message])

    try:
        if current_os == "Linux":
            if mode != "systemd":
                return SoftwareUpdateRestartOut(ok=False, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, service=service_name, command=service_command, errors=["En Linux configure software_restart_mode=systemd, auto o disabled."])
            if not _validate_systemd_service_name(service_name):
                return SoftwareUpdateRestartOut(ok=False, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, service=service_name, command=service_command, errors=["Nombre de servicio systemd invalido."])

            status_proc = _run_systemctl_check(["status", service_name, "--no-pager"])
            active_proc = _run_systemctl_check(["is-active", service_name])
            if status_proc.returncode == 4 or "could not be found" in (status_proc.stderr or "").lower() or "not-found" in (status_proc.stdout or "").lower():
                return SoftwareUpdateRestartOut(
                    ok=False,
                    mode=mode,
                    configured_mode=configured_mode,
                    detected_mode=mode,
                    os=current_os,
                    service=service_name,
                    command=service_command,
                    errors=[f"El servicio systemd {service_name} no existe o no esta disponible."],
                )
            if active_proc.returncode not in (0, 3):
                detail = (active_proc.stderr or active_proc.stdout or status_proc.stderr or status_proc.stdout or "").strip()
                return SoftwareUpdateRestartOut(
                    ok=False,
                    mode=mode,
                    configured_mode=configured_mode,
                    detected_mode=mode,
                    os=current_os,
                    service=service_name,
                    command=service_command,
                    errors=[f"El servicio systemd {service_name} no existe o no esta disponible. {detail}".strip()],
                )

            proc = subprocess.run(["systemctl", "restart", service_name, "--no-block"], text=True, capture_output=True, timeout=8)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                return SoftwareUpdateRestartOut(
                    ok=False,
                    mode=mode,
                    configured_mode=configured_mode,
                    detected_mode=mode,
                    os=current_os,
                    service=service_name,
                    command=service_command,
                    errors=[f"No se pudo reiniciar. Configure permisos del servicio o use modo disabled. {detail}".strip()],
                )
            diagnostics["command"] = service_command
            logger.info("[SOFTWARE UPDATE RESTART] cwd=%s main_file=%s app_root=%s pid=%s os=%s mode=systemd service=%s command=%s result=scheduled", diagnostics["cwd"], diagnostics["main_file"], diagnostics["app_root"], diagnostics["pid"], current_os, service_name, service_command)
            return SoftwareUpdateRestartOut(ok=True, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, service=service_name, command=service_command, message="Se solicitó reinicio por systemd.")

        if current_os == "Windows":
            if mode != "windows_script":
                diagnostics["command"] = script_name
                return SoftwareUpdateRestartOut(ok=False, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, command=script_name, diagnostics=diagnostics, errors=["En Windows configure software_restart_mode=windows_script, auto o disabled."])
            script_path = _resolve_restart_script(script_name)
            diagnostics["command"] = script_path
            subprocess.Popen(
                ["cmd", "/c", "start", "", script_path, str(os.getpid())],
                cwd=app_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            logger.info("[SOFTWARE UPDATE RESTART] cwd=%s main_file=%s app_root=%s pid=%s os=%s mode=windows_script command=%s result=scheduled", diagnostics["cwd"], diagnostics["main_file"], diagnostics["app_root"], diagnostics["pid"], current_os, script_path)
            return SoftwareUpdateRestartOut(ok=True, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, command=script_path, message="La aplicación se reiniciará automáticamente.")

        return SoftwareUpdateRestartOut(ok=False, mode=mode, configured_mode=configured_mode, detected_mode=mode, os=current_os, errors=["Sistema operativo no soportado. Use modo disabled."])
    except Exception as exc:
        logger.exception("[SOFTWARE UPDATE RESTART] os=%s mode=%s result=fail", current_os, mode)
        return SoftwareUpdateRestartOut(
            ok=False,
            mode=mode,
            configured_mode=configured_mode,
            detected_mode=mode,
            os=current_os,
            service=service_name,
            command=service_command if current_os == "Linux" else script_name,
            errors=[f"No se pudo reiniciar. Configure permisos del servicio o use modo disabled. Detalle: {exc}"],
        )


@app.post("/api/admin/software-update/restart", response_model=SoftwareUpdateRestartOut)
def admin_restart_after_software_update(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("[SOFTWARE UPDATE RESTART] source=admin_request result=requested")
    return _schedule_software_restart()


@app.get("/api/health")
def health_check():
    return {
        "ok": True,
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "main_file": os.path.abspath(__file__),
        "app_root": _app_root(),
        "build_info": _read_software_build_info(),
        "build_id": _software_build_id(),
    }


@app.get("/api/admin/client-ip", response_model=ClientIPOut)
def admin_get_client_ip(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return ClientIPOut(client_ip=get_setting(db, "client_ip", ""))


@app.post("/api/admin/client-ip", response_model=ClientIPOut)
def admin_set_client_ip(payload: ClientIPIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin updating client IP")
    with SessionLocal() as db:
        set_setting(db, "client_ip", payload.client_ip.strip())
        return ClientIPOut(client_ip=get_setting(db, "client_ip", ""))


@app.get("/api/admin/rcs-config", response_model=RcsConfigOut)
def admin_get_rcs_config(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return _get_rcs_config(db)


@app.post("/api/admin/rcs-config", response_model=RcsConfigOut)
def admin_set_rcs_config(payload: RcsConfigIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin updating RCS config base_url=%s", payload.base_url)
    with SessionLocal() as db:
        return _save_rcs_config(db, payload)


@app.post("/api/admin/rcs-config/test", response_model=RcsConfigTestOut)
def admin_test_rcs_config(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin testing RCS config")
    with SessionLocal() as db:
        cfg = _get_rcs_config(db)
        endpoint = (cfg.create_task_endpoint or "/rcs/task/create").strip() or "/rcs/task/create"
        query_endpoint = (cfg.query_task_status_endpoint or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip() or "/rcms/services/rest/hikRpcService/queryTaskStatus"
        agv_status_endpoint = (cfg.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
        pod_position_endpoint = (cfg.pod_position_endpoint or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
        stop_endpoint = (cfg.stop_robot_endpoint or "/rcms/services/rest/hikRpcService/stopRobot").strip() or "/rcms/services/rest/hikRpcService/stopRobot"
        resume_endpoint = (cfg.resume_robot_endpoint or "/rcms/services/rest/hikRpcService/resumeRobot").strip() or "/rcms/services/rest/hikRpcService/resumeRobot"
        verify_tls = bool(int(cfg.verify_tls or 0))
        if not cfg.resolved_base_url:
            return RcsConfigTestOut(
                ok=False,
                message="Falta capturar la URL base del RCS.",
                resolved_base_url="",
                resolved_endpoint=endpoint,
                resolved_query_endpoint=query_endpoint,
                resolved_cancel_endpoint=cfg.cancel_task_endpoint,
                resolved_stop_endpoint=stop_endpoint,
                resolved_resume_endpoint=resume_endpoint,
                resolved_agv_status_endpoint=agv_status_endpoint,
                resolved_pod_position_endpoint=pod_position_endpoint,
                verify_tls=verify_tls,
                has_token_code=bool(cfg.resolved_token_code),
                has_auth_header=bool(cfg.resolved_auth_header),
            )
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        if not query_endpoint.startswith("/"):
            query_endpoint = "/" + query_endpoint
        if not agv_status_endpoint.startswith("/"):
            agv_status_endpoint = "/" + agv_status_endpoint
        if not pod_position_endpoint.startswith("/"):
            pod_position_endpoint = "/" + pod_position_endpoint
        if not stop_endpoint.startswith("/"):
            stop_endpoint = "/" + stop_endpoint
        if not resume_endpoint.startswith("/"):
            resume_endpoint = "/" + resume_endpoint
        return RcsConfigTestOut(
            ok=True,
            message="Configuración válida para envío y consulta de estatus. Puedes consultar en modo automático o manual desde la pantalla de tareas.",
            resolved_base_url=cfg.resolved_base_url,
            resolved_endpoint=endpoint,
            resolved_query_endpoint=query_endpoint,
            resolved_cancel_endpoint=cfg.cancel_task_endpoint,
            resolved_stop_endpoint=stop_endpoint,
            resolved_resume_endpoint=resume_endpoint,
            resolved_agv_status_endpoint=agv_status_endpoint,
            resolved_pod_position_endpoint=pod_position_endpoint,
            verify_tls=verify_tls,
            has_token_code=bool(cfg.resolved_token_code),
            has_auth_header=bool(cfg.resolved_auth_header),
        )


class PodPositionQueryIn(BaseModel):
    rack_id: int = Field(gt=0)


class PodPositionQueryOut(BaseModel):
    ok: bool
    message: str = ""
    rack_id: int
    rack_code: str
    endpoint: str = ""
    rcs_position_code: str = ""
    local_cell: Optional[dict] = None
    local_rack_cell: Optional[dict] = None
    request_payload: dict = {}
    response_payload: dict = {}


@app.post("/api/rcs/pod-position", response_model=PodPositionQueryOut)
def query_pod_position(payload: PodPositionQueryIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        rack = db.execute(select(Rack).where(Rack.id == payload.rack_id)).scalar_one_or_none()
        if not rack:
            raise HTTPException(status_code=404, detail="Rack no encontrado")
        pod_code = (rack.code or "").strip()
        if not pod_code:
            raise HTTPException(status_code=400, detail="El rack seleccionado no tiene codigo para enviar como podCode")

        try:
            result = _query_pod_position_from_rcs(db, rack, module="query_pod_position")
        except HTTPException:
            db.commit()
            raise
        except Exception as exc:
            db.commit()
            raise HTTPException(status_code=502, detail=f"Error consultando posicion del rack en RCS: {exc}")

        endpoint = result["endpoint"]
        request_payload = result["request_payload"]
        response_payload = result["response_payload"]
        position_code = result["position_code"]
        matched_cell = _location_by_position_code(db, position_code) if _position_code_is_defined(position_code) else None
        applied = False
        if matched_cell:
            applied, _apply_message, matched_cell = _apply_rcs_rack_position(db, rack, position_code, source="manual_pod_position_query", actor="admin")
        local_rack_cell = db.execute(select(Location).where(Location.rack_id == rack.id)).scalar_one_or_none()
        if position_code and applied and matched_cell:
            message = f"RCS reporta posicion {position_code}; se actualizo el rack en la matriz local ({matched_cell.x}, {matched_cell.y})."
        elif position_code and matched_cell:
            message = f"RCS reporta posicion {position_code}; corresponde a la celda local ({matched_cell.x}, {matched_cell.y})."
        elif position_code:
            message = f"RCS reporta posicion {position_code}, pero no existe una celda local con ese codigo."
        else:
            message = "El RCS respondio, pero no se encontro un campo de posicion reconocible en la respuesta."
        db.commit()
        return PodPositionQueryOut(
            ok=bool(position_code),
            message=message,
            rack_id=rack.id,
            rack_code=pod_code,
            endpoint=endpoint,
            rcs_position_code=position_code,
            local_cell=_cell_payload(matched_cell),
            local_rack_cell=_cell_payload(local_rack_cell),
            request_payload=request_payload,
            response_payload=response_payload,
        )



class RobotControlIn(BaseModel):
    robot_code: str = Field(min_length=1, max_length=64)


class RobotControlResponse(BaseModel):
    ok: bool
    code: str = ""
    message: str = ""
    reqCode: str = ""
    robot_code: str = ""
    action: str = ""


class RobotMonitorItem(BaseModel):
    robotCode: str = ""
    agvCode: str = ""
    robotDir: str = ""
    robotIp: str = ""
    battery: str = ""
    posX: str = ""
    posY: str = ""
    mapCode: str = ""
    mapShortName: str = ""
    speed: str = ""
    velocity: str = ""
    status: str = ""
    agvStatus: str = ""
    statusText: str = ""
    taskStatus: str = ""
    exclType: str = ""
    stop: str = ""
    charging: str = ""
    errorCode: str = ""
    errorMsg: str = ""
    positionCode: str = ""
    currentStation: str = ""
    podCode: str = ""
    podDir: str = ""
    path: list = []


class RobotMonitorResponse(BaseModel):
    ok: bool
    code: str = ""
    message: str = ""
    reqCode: str = ""
    robots: List[RobotMonitorItem] = []


_AMR_STATUS_MAP = {
    "1": "Task completed",
    "2": "Executing task",
    "3": "Abnormal task",
    "4": "Idle task",
    "5": "Robot stopped",
    "6": "Lifting the rack",
    "7": "Charging status",
    "8": "Curve movement",
    "9": "Full charge maintenance",
    "11": "Rack not recognized",
    "12": "Rack angle deflected",
    "13": "Motion library exception",
    "14": "Rack code unrecognized",
    "15": "Rack code mismatch",
    "16": "Lifting exception",
    "17": "Charging station exception",
    "18": "Battery not charging",
    "20": "Charging direction error",
    "21": "Platform command error",
    "23": "Abnormal unloading",
    "24": "The rack position deviated",
    "25": "Robot not in the block zone",
    "26": "Retry putting down failed",
    "27": "Incorrect rack location",
    "28": "Low battery for lifting",
    "29": "Robot reversing angle deflected",
    "30": "Lifting without rack",
    "31": "Blocking zone failed",
    "33": "Rotation request temporarily failed",
    "34": "Map switching code unrecognized",
}


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_robot_monitor_items(raw_data, *, fallback_map_short_name: str = ""):
    if isinstance(raw_data, list):
        rows = raw_data
    elif isinstance(raw_data, dict):
        rows = raw_data.get("list") or raw_data.get("items") or raw_data.get("records") or []
        if not rows and any(k in raw_data for k in ("robotCode", "status", "battery")):
            rows = [raw_data]
    else:
        rows = []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status_code = str(row.get("status", "") or "").strip()
        robot_code = _first_non_empty(row.get("robotCode"), row.get("agvCode"), row.get("robot_code"), row.get("agv_code"))
        speed_value = _first_non_empty(row.get("speed"), row.get("velocity"), row.get("vel"))
        battery_value = _first_non_empty(row.get("battery"), row.get("batteryPower"), row.get("batteryPercent"), row.get("electricity"))
        charging_value = _first_non_empty(row.get("charging"), row.get("isCharging"), row.get("chargeState"), row.get("chargeStatus"))
        map_short_name = _first_non_empty(row.get("mapShortName"), row.get("map_short_name"), fallback_map_short_name)
        position_code = _first_non_empty(row.get("positionCode"), row.get("currentPositionCode"), row.get("stationCode"))
        current_station = _first_non_empty(row.get("currentStation"), row.get("stationName"), row.get("currentSite"))
        task_status = _first_non_empty(row.get("taskStatus"), row.get("task_state"), row.get("taskState"), row.get("task_status"))
        error_code = _first_non_empty(row.get("errorCode"), row.get("error_code"))
        error_msg = _first_non_empty(row.get("errorMsg"), row.get("error_msg"), row.get("errorMessage"), row.get("message"))
        items.append(RobotMonitorItem(
            robotCode=robot_code,
            agvCode=robot_code,
            robotDir=str(row.get("robotDir", "") or ""),
            robotIp=str(row.get("robotIp", "") or ""),
            battery=battery_value,
            posX=str(row.get("posX", "") or ""),
            posY=str(row.get("posY", "") or ""),
            mapCode=str(row.get("mapCode", "") or ""),
            mapShortName=map_short_name,
            speed=speed_value,
            velocity=speed_value,
            status=status_code,
            agvStatus=status_code,
            statusText=_AMR_STATUS_MAP.get(status_code, "Unknown status" if status_code else "Sin status"),
            taskStatus=task_status,
            exclType=str(row.get("exclType", "") or ""),
            stop=str(row.get("stop", "") or ""),
            charging=charging_value,
            errorCode=error_code,
            errorMsg=error_msg,
            positionCode=position_code,
            currentStation=current_station,
            podCode=str(row.get("podCode", "") or ""),
            podDir=str(row.get("podDir", "") or ""),
            path=row.get("path") if isinstance(row.get("path"), list) else [],
        ))
    return items



def _fetch_robot_map_code(db, cfg, robot_code: str) -> str:
    endpoint = (cfg.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    client = _get_rcs_client_from_settings(db)
    payload = {
        "reqCode": generate_req_code_ms(),
        "reqTime": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "clientCode": "",
        "tokenCode": (get_setting(db, "rcs_token_code", "") or "").strip() or os.getenv("RCS_TOKEN_CODE", "").strip(),
    }
    if int(getattr(cfg, "enable_map_short_name", 1) or 0) == 1:
        payload["mapShortName"] = str(cfg.map_short_name or "AA")
    if int(getattr(cfg, "enable_map_code", 0) or 0) == 1 and str(getattr(cfg, "map_code", "") or "").strip():
        payload["mapCode"] = str(cfg.map_code or "").strip()
    try:
        response = client.query_agv_status_with_payload(payload, endpoint_override=endpoint)
        for item in _normalize_robot_monitor_items(response.get("data"), fallback_map_short_name=str(getattr(cfg, "map_short_name", "") or "")):
            if str(item.robotCode or "").strip() == str(robot_code or "").strip():
                return str(item.mapCode or "").strip()
    except Exception:
        pass
    return ""


def _build_robot_control_payload(db, cfg, robot_code: str) -> dict:
    return {
        "reqCode": generate_req_code_ms(),
        "reqTime": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "clientCode": "",
        "tokenCode": (get_setting(db, "rcs_token_code", "") or "").strip() or os.getenv("RCS_TOKEN_CODE", "").strip(),
        "robotCount": "1",
        "mapCode": (str(cfg.map_code or "").strip() if int(getattr(cfg, "enable_map_code", 0) or 0) == 1 and str(cfg.map_code or "").strip() else _fetch_robot_map_code(db, cfg, robot_code)),
        "robots": [str(robot_code or "").strip()],
    }


def _send_robot_control(db, action: str, robot_code: str) -> RobotControlResponse:
    cfg = _get_rcs_config(db)
    if not cfg.resolved_base_url:
        return RobotControlResponse(ok=False, message="Falta capturar la URL base del RCS.", robot_code=robot_code, action=action)
    endpoint = (cfg.stop_robot_endpoint if action == "stop" else cfg.resume_robot_endpoint).strip()
    default_endpoint = "/rcms/services/rest/hikRpcService/stopRobot" if action == "stop" else "/rcms/services/rest/hikRpcService/resumeRobot"
    endpoint = endpoint or default_endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    client = _get_rcs_client_from_settings(db)
    payload = _build_robot_control_payload(db, cfg, robot_code)
    module_name = "stop_robot" if action == "stop" else "resume_robot"
    action_text = "Stop" if action == "stop" else "Resume"
    try:
        _append_debug_console_event(
            db,
            direction="sent",
            module=module_name,
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload=payload,
            message=f"{action_text} robot {robot_code}",
        )
        response = client.query_agv_status_with_payload(payload, endpoint_override=endpoint)
        _append_debug_console_event(
            db,
            direction="received",
            module=module_name,
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload=response,
            message=str(response.get("message", "") or f"Respuesta {action_text.lower()} robot"),
        )
        return RobotControlResponse(
            ok=str(response.get("code", "")) == "0",
            code=str(response.get("code", "") or ""),
            message=str(response.get("message", "") or ""),
            reqCode=str(response.get("reqCode", payload.get("reqCode", "")) or ""),
            robot_code=robot_code,
            action=action,
        )
    except RcsError as exc:
        _append_debug_console_event(
            db,
            direction="received",
            module=module_name,
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload={"error": str(exc)},
            message=str(exc),
        )
        return RobotControlResponse(ok=False, message=str(exc), reqCode=payload.get("reqCode", ""), robot_code=robot_code, action=action)




@app.get("/api/rcs-config-public")
def get_rcs_config_public():
    with SessionLocal() as db:
        cfg = _get_rcs_config(db)
        return {
            "enable_amr_monitor": int(cfg.enable_amr_monitor or 0),
            "agv_status_endpoint": cfg.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus",
            "pod_position_endpoint": cfg.pod_position_endpoint or "/rcms/services/rest/hikRpcService/queryPodPosition",
            "task_monitor_interval_seconds": float(cfg.task_monitor_interval_seconds or 3.0),
            "agv_monitor_interval_seconds": float(cfg.agv_monitor_interval_seconds or 5.0),
            "enable_map_short_name": int(cfg.enable_map_short_name or 0),
            "map_short_name": cfg.map_short_name or "AA",
            "enable_map_code": int(cfg.enable_map_code or 0),
            "map_code": cfg.map_code or "",
            "resolved_base_url": cfg.resolved_base_url,
            "cancel_undo_auto_recovery_enabled": int(cfg.cancel_undo_auto_recovery_enabled or 0),
            "cancel_undo_auto_recovery_min_age_minutes": int(cfg.cancel_undo_auto_recovery_min_age_minutes or 5),
        }


@app.post("/api/robot-control/stop", response_model=RobotControlResponse)
def stop_robot_control(payload: RobotControlIn):
    with SessionLocal() as db:
        return _send_robot_control(db, "stop", payload.robot_code.strip())


@app.post("/api/robot-control/resume", response_model=RobotControlResponse)
def resume_robot_control(payload: RobotControlIn):
    with SessionLocal() as db:
        return _send_robot_control(db, "resume", payload.robot_code.strip())


@app.get("/api/robot-status-monitor", response_model=RobotMonitorResponse)
def get_robot_status_monitor(force: int = 0):
    with SessionLocal() as db:
        return _get_cached_robot_status_monitor_response(db, force=int(force or 0) == 1)


@app.websocket("/ws/runtime")
async def runtime_websocket(websocket: WebSocket):
    await runtime_ws_manager.connect(websocket)
    try:
        payload = await asyncio.to_thread(_build_runtime_snapshot_payload, 300, 1)
        await websocket.send_json({"type": "runtime_snapshot", "payload": payload})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close()
    finally:
        await runtime_ws_manager.disconnect(websocket)


@app.post("/api/admin/hide-configured-range")
def admin_hide_configured_range(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        rows = int(get_setting(db, "display_rows", str(DB_GRID_H)))
        cols = int(get_setting(db, "display_cols", str(DB_GRID_W)))
        changed = 0
        for loc in db.execute(select(Location).where(Location.x < cols, Location.y < rows)).scalars().all():
            loc.is_visible = 0
            loc.updated_at = datetime.utcnow()
            changed += 1
        db.commit()
    logger.info("Visible fields updated | action=hide | rows=%s | cols=%s | locations=%s", rows, cols, changed)
    return {"ok": True}


@app.post("/api/admin/show-configured-range")
def admin_show_configured_range(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        rows = int(get_setting(db, "display_rows", str(DB_GRID_H)))
        cols = int(get_setting(db, "display_cols", str(DB_GRID_W)))
        changed = 0
        for loc in db.execute(select(Location).where(Location.x < cols, Location.y < rows)).scalars().all():
            loc.is_visible = 1
            loc.updated_at = datetime.utcnow()
            changed += 1
        db.commit()
    logger.info("Visible fields updated | action=show | rows=%s | cols=%s | locations=%s", rows, cols, changed)
    return {"ok": True}


def _list_locations_out(db) -> List[LocationOut]:
    rows = db.execute(select(Location)).scalars().all()
    area_by_id, rack_by_id = _build_location_lookup_maps(db, rows)
    return [_location_out(db, r, area_by_id=area_by_id, rack_by_id=rack_by_id) for r in rows]


def _list_movement_orders_out(db) -> List[MovementOrderOut]:
    rows = db.execute(select(MovementOrder).order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())).scalars().all()
    lookup = _build_movement_order_lookup_maps(db, rows)
    return [_movement_order_out(db, row, **lookup) for row in rows]


def _list_debug_console_events_out(db, limit: int = 200) -> List[DebugConsoleEventOut]:
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = db.execute(
        select(DebugConsoleEvent)
        .order_by(DebugConsoleEvent.created_at.desc(), DebugConsoleEvent.id.desc())
        .limit(safe_limit)
    ).scalars().all()
    return [_debug_console_event_out(row) for row in rows]


def _get_robot_status_monitor_response(db) -> RobotMonitorResponse:
    cfg = _get_rcs_config(db)
    if not cfg.resolved_base_url:
        return RobotMonitorResponse(ok=False, code="", message="Falta capturar la URL base del RCS.", reqCode="", robots=[])
    endpoint = (cfg.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    client = _get_rcs_client_from_settings(db)
    if int(cfg.enable_amr_monitor or 0) != 1:
        return RobotMonitorResponse(ok=False, code="", message="Monitoreo AMR deshabilitado desde la configuración RCS.", reqCode="", robots=[])
    payload = {
        "reqCode": generate_req_code_ms(),
        "reqTime": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    if int(getattr(cfg, "enable_map_short_name", 1) or 0) == 1:
        payload["mapShortName"] = str(cfg.map_short_name or "AA")
    if int(getattr(cfg, "enable_map_code", 0) or 0) == 1 and str(getattr(cfg, "map_code", "") or "").strip():
        payload["mapCode"] = str(cfg.map_code or "").strip()
    try:
        _append_debug_console_event(
            db,
            direction="sent",
            module="robot_status_monitor",
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload=payload,
            message="Consulta automática de estatus AMR",
        )
        response = client.query_agv_status_with_payload(payload, endpoint_override=endpoint)
        items = _normalize_robot_monitor_items(
            response.get("data"),
            fallback_map_short_name=(str(cfg.map_short_name or "AA") if int(getattr(cfg, "enable_map_short_name", 1) or 0) == 1 else ""),
        )
        response_code = str(response.get("code", "") or "")
        response_message = str(response.get("message", "") or "")
        if response_code == "0" and not items:
            response_message = "Consulta exitosa, pero RCS no devolviÃ³ robots."
        _append_debug_console_event(
            db,
            direction="received",
            module="robot_status_monitor",
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload=response,
            message=response_message or "Respuesta de monitoreo AMR",
        )
        return RobotMonitorResponse(
            ok=response_code == "0",
            code=response_code,
            message=response_message,
            reqCode=str(response.get("reqCode", payload.get("reqCode", "")) or ""),
            robots=items,
        )
    except RcsError as exc:
        _append_debug_console_event(
            db,
            direction="received",
            module="robot_status_monitor",
            base_url=cfg.resolved_base_url,
            endpoint=endpoint,
            payload={"error": str(exc)},
            message=str(exc),
        )
        return RobotMonitorResponse(ok=False, code="", message=str(exc), reqCode=payload["reqCode"], robots=[])


def _get_cached_robot_status_monitor_response(db, force: bool = False, max_age_seconds: Optional[float] = None) -> Optional[RobotMonitorResponse]:
    if max_age_seconds is None:
        try:
            max_age_seconds = float(get_setting(db, "rcs_agv_monitor_interval_seconds", str(ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)) or ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)
        except Exception:
            max_age_seconds = ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS
    if force:
        response = _get_robot_status_monitor_response(db)
        with _robot_monitor_cache_lock:
            _robot_monitor_cache["timestamp"] = __import__("time").time()
            _robot_monitor_cache["payload"] = response.model_dump()
        return response
    now_ts = __import__("time").time()
    with _robot_monitor_cache_lock:
        cached_payload = _robot_monitor_cache.get("payload")
        cached_ts = float(_robot_monitor_cache.get("timestamp") or 0.0)
    if cached_payload is not None and (now_ts - cached_ts) <= float(max_age_seconds):
        return RobotMonitorResponse(**cached_payload)
    response = _get_robot_status_monitor_response(db)
    with _robot_monitor_cache_lock:
        _robot_monitor_cache["timestamp"] = now_ts
        _robot_monitor_cache["payload"] = response.model_dump()
    return response


def _build_runtime_snapshot_out(db, debug_limit: int = 200, include_robot_monitor: int = 1) -> RuntimeSnapshotOut:
    _maybe_auto_release_stuck_cancel_return_orders()
    robot_monitor = _get_cached_robot_status_monitor_response(db, force=False) if int(include_robot_monitor or 0) == 1 else None
    return RuntimeSnapshotOut(
        orders=_list_movement_orders_out(db),
        locations=_list_locations_out(db),
        debug_log=_list_debug_console_events_out(db, limit=debug_limit),
        robot_monitor=robot_monitor,
    )


def _build_runtime_snapshot_payload(debug_limit: int = 200, include_robot_monitor: int = 1) -> dict:
    with SessionLocal() as db:
        snapshot = _build_runtime_snapshot_out(db, debug_limit=debug_limit, include_robot_monitor=include_robot_monitor)
        return snapshot.model_dump(mode="json")


def _payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _runtime_broadcast_loop():
    global _runtime_snapshot_last_hash
    while True:
        try:
            if await runtime_ws_manager.count() > 0:
                payload = await asyncio.to_thread(_build_runtime_snapshot_payload, 300, 1)
                payload_hash = _payload_hash(payload)
                should_broadcast = False
                with _runtime_snapshot_state_lock:
                    if payload_hash != _runtime_snapshot_last_hash:
                        _runtime_snapshot_last_hash = payload_hash
                        should_broadcast = True
                if should_broadcast:
                    await runtime_ws_manager.broadcast_json({"type": "runtime_snapshot", "payload": payload})
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(RUNTIME_BROADCAST_INTERVAL_SECONDS)


@app.get("/api/runtime-snapshot", response_model=RuntimeSnapshotOut)
def get_runtime_snapshot(debug_limit: int = 200, include_robot_monitor: int = 1):
    with SessionLocal() as db:
        return _build_runtime_snapshot_out(db, debug_limit=debug_limit, include_robot_monitor=include_robot_monitor)


@app.get("/api/locations", response_model=List[LocationOut])
def list_locations():
    with SessionLocal() as db:
        _auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow())
        return _list_locations_out(db)


@app.get("/api/locations/{x}/{y}", response_model=LocationOut)
def get_location(x: int, y: int):
    validate_xy(x, y)
    with SessionLocal() as db:
        r = db.execute(select(Location).where(Location.x == x, Location.y == y)).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada")
        return _location_out(db, r)


@app.post("/api/admin/login")
def admin_login(body: AdminLogin):
    with SessionLocal() as db:
        stored = get_setting(db, "admin_password_hash", "")
        if _sha256(body.password) != stored:
            logger.warning("Admin login failed")
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")
    token = secrets.token_hex(16)
    ADMIN_TOKENS[token] = datetime.utcnow() + timedelta(hours=8)
    logger.info("Admin login successful token_prefix=%s", token[:6])
    return {"token": token, "expires_hours": 8}


@app.post("/api/admin/change-password")
def admin_change_password(body: AdminChangePassword, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stored = get_setting(db, "admin_password_hash", "")
        if _sha256(body.old_password) != stored:
            raise HTTPException(status_code=400, detail="Contraseña anterior incorrecta")
        set_setting(db, "admin_password_hash", _sha256(body.new_password))
    logger.info("Admin changed password")
    return {"ok": True}


@app.get("/api/admin/database/download")
def admin_download_database(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    started = time.monotonic()
    try:
        require_admin(x_admin_token)
        db_path = engine.url.database or DB_URL.replace("sqlite:///", "", 1)
        db_abs_path = os.path.abspath(db_path)
        if not os.path.isfile(db_abs_path):
            raise HTTPException(status_code=404, detail="Base de datos no encontrada")
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL);")
        filename = f"agv_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        logger.info("Database download requested filename=%s size_bytes=%s duration_ms=%s", filename, os.path.getsize(db_abs_path), int((time.monotonic() - started) * 1000))
        return FileResponse(
            db_abs_path,
            media_type="application/octet-stream",
            filename=filename,
        )
    except HTTPException:
        logger.warning("Database download failed")
        raise
    except Exception:
        logger.exception("Database download failed")
        raise HTTPException(status_code=500, detail="No se pudo descargar la base de datos")


@app.get("/api/admin/backup/full")
def admin_download_full_backup(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    tmp_path = None
    started = time.monotonic()
    try:
        require_admin(x_admin_token)
        logger.info("Full backup requested")
        db_path = engine.url.database or DB_URL.replace("sqlite:///", "", 1)
        db_abs_path = os.path.abspath(db_path)
        uploads_abs_path = os.path.abspath(UPLOAD_DIR)
        if not os.path.isfile(db_abs_path):
            raise HTTPException(status_code=404, detail="Base de datos no encontrada")

        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL);")

        included_files = []
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        tmp.close()

        manifest = {
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "app_name": "AGV Orders App",
            "database_size_bytes": os.path.getsize(db_abs_path),
            "files": [],
        }

        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            db_arcname = os.path.basename(db_abs_path)
            zf.write(db_abs_path, arcname=db_arcname)
            included_files.append(db_arcname)

            if os.path.isdir(uploads_abs_path) and not os.path.islink(uploads_abs_path):
                for root, dirs, files in os.walk(uploads_abs_path, followlinks=False):
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith(".")
                        and d != "__pycache__"
                        and not os.path.islink(os.path.join(root, d))
                    ]
                    for name in files:
                        if name.startswith(".") or name == "__pycache__":
                            continue
                        file_abs_path = os.path.join(root, name)
                        if os.path.islink(file_abs_path) or not os.path.isfile(file_abs_path):
                            continue
                        rel_path = os.path.relpath(file_abs_path, uploads_abs_path)
                        arcname = os.path.join("static", "uploads", rel_path).replace(os.sep, "/")
                        zf.write(file_abs_path, arcname=arcname)
                        included_files.append(arcname)

            manifest["files"] = included_files + ["backup_manifest.json"]
            zf.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        logger.info("Full backup generated files=%s size_bytes=%s duration_ms=%s", len(included_files), os.path.getsize(tmp_path), int((time.monotonic() - started) * 1000))

        def _iter_backup_zip():
            try:
                with open(tmp_path, "rb") as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)

        filename = f"agv_full_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(_iter_backup_zip(), media_type="application/zip", headers=headers)
    except HTTPException:
        logger.warning("Full backup failed")
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        raise
    except Exception:
        logger.exception("Full backup failed")
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        raise HTTPException(status_code=500, detail="No se pudo descargar el backup completo")


def _backup_validation_failure(message: str) -> dict:
    logger.warning("Backup validation failed reason=%s", message)
    return {"ok": False, "message": f"Backup inválido: {message}"}


def _is_blocked_backup_entry(name: str) -> Optional[str]:
    normalized = (name or "").replace("\\", "/").strip("/")
    parts = [p for p in normalized.split("/") if p]
    lower_parts = [p.lower() for p in parts]
    if not parts:
        return "ruta vacía"
    if any(part.startswith(".") for part in parts):
        return "archivo oculto protegido"
    if any(part in {".env", ".git", ".venv", "venv", "logs", "backups", "__pycache__"} for part in lower_parts):
        return "ruta protegida"
    if len(lower_parts) >= 2 and lower_parts[0] == "updates" and lower_parts[1] == "staging":
        return "ruta protegida"
    if normalized in {"agv.db", "backup_manifest.json", "static", "static/uploads"}:
        return None
    if normalized.startswith("static/uploads/"):
        return None
    return "ruta no permitida"


def _active_database_abs_path() -> str:
    db_path = engine.url.database or DB_URL.replace("sqlite:///", "", 1)
    return os.path.abspath(db_path)


def _write_current_state_backup(dest_zip_path: str) -> None:
    db_abs_path = _active_database_abs_path()
    uploads_abs_path = os.path.abspath(UPLOAD_DIR)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL);")
    with zipfile.ZipFile(dest_zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        if os.path.isfile(db_abs_path):
            zf.write(db_abs_path, arcname=os.path.basename(db_abs_path))
        if os.path.isdir(uploads_abs_path) and not os.path.islink(uploads_abs_path):
            for root, dirs, files in os.walk(uploads_abs_path, followlinks=False):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d != "__pycache__"
                    and not os.path.islink(os.path.join(root, d))
                ]
                for name in files:
                    if name.startswith(".") or name == "__pycache__":
                        continue
                    file_abs_path = os.path.join(root, name)
                    if os.path.islink(file_abs_path) or not os.path.isfile(file_abs_path):
                        continue
                    rel_path = os.path.relpath(file_abs_path, uploads_abs_path)
                    arcname = os.path.join("static", "uploads", rel_path).replace(os.sep, "/")
                    zf.write(file_abs_path, arcname=arcname)


def _create_pre_restore_backup() -> str:
    backup_dir = os.path.abspath(os.path.join("backups", "pre_restore"))
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"pre_restore_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
    _write_current_state_backup(backup_path)
    logger.info("Pre-restore backup created name=%s size_bytes=%s", os.path.basename(backup_path), os.path.getsize(backup_path) if os.path.exists(backup_path) else 0)
    cleanup_pre_restore_backups()
    return backup_path


def _pre_restore_backup_dir() -> str:
    return os.path.abspath(os.path.join("backups", "pre_restore"))


def _safe_pre_restore_backup_path(filename: str) -> str:
    name = (filename or "").strip()
    if (
        not name
        or "/" in name
        or "\\" in name
        or ".." in name
        or not name.lower().endswith(".zip")
        or os.path.isabs(name)
    ):
        raise HTTPException(status_code=400, detail="Nombre de backup inválido")
    backup_dir = _pre_restore_backup_dir()
    candidate = os.path.abspath(os.path.join(backup_dir, name))
    if os.path.commonpath([backup_dir, candidate]) != backup_dir:
        raise HTTPException(status_code=400, detail="Nombre de backup inválido")
    return candidate


def _pre_restore_backup_keep() -> int:
    with SessionLocal() as db:
        try:
            raw = get_setting(db, "pre_restore_backup_keep", "10")
            return max(1, min(1000, int(float(raw or 10))))
        except Exception:
            return 10


def _is_safe_pre_restore_zip_name(filename: str) -> bool:
    name = (filename or "").strip()
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and ".." not in name
        and not os.path.isabs(name)
        and name.lower().endswith(".zip")
    )


def cleanup_pre_restore_backups() -> None:
    logger.info("Pre-restore backup cleanup started")
    try:
        backup_dir = _pre_restore_backup_dir()
        keep = _pre_restore_backup_keep()
        if not os.path.isdir(backup_dir):
            logger.info("Pre-restore backup cleanup finished")
            return
        candidates = []
        for entry in os.scandir(backup_dir):
            if (
                not entry.is_file(follow_symlinks=False)
                or not _is_safe_pre_restore_zip_name(entry.name)
            ):
                continue
            candidates.append((entry.name, entry.stat(follow_symlinks=False).st_mtime))
        candidates.sort(key=lambda item: item[1], reverse=True)
        for name, _mtime in candidates[keep:]:
            target = _safe_pre_restore_backup_path(name)
            with contextlib.suppress(FileNotFoundError):
                os.remove(target)
                logger.info("Pre-restore backup cleanup deleted old backup name=%s", name)
        logger.info("Pre-restore backup cleanup finished")
    except Exception:
        logger.exception("Pre-restore backup cleanup failed")


def _set_restore_pending_restart(value: bool) -> None:
    with SessionLocal() as db:
        set_setting(db, "restore_pending_restart", "1" if value else "0")
        if value:
            set_setting(db, "last_restore_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))


def _restore_pending_restart_status() -> dict:
    with SessionLocal() as db:
        pending = get_setting(db, "restore_pending_restart", "0") == "1"
        last_restore_at = get_setting(db, "last_restore_at", "")
    return {
        "restore_pending_restart": pending,
        "last_restore_at": last_restore_at or None,
        "message": "Reinicio pendiente después de restauración." if pending else "",
    }


def _validate_backup_zip_to_staging(zip_path: str, staging_dir: str) -> dict:
    manifest = {}
    uploads_count = 0
    files_count = 0
    db_staging_path = os.path.join(staging_dir, "agv.db")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad_entry = zf.testzip()
            if bad_entry:
                return {"ok": False, "message": "Backup inválido: ZIP corrupto."}
            names = set()
            db_info = None
            for info in zf.infolist():
                raw_name = info.filename or ""
                normalized = raw_name.replace("\\", "/").strip("/")
                if not normalized:
                    continue
                if _is_dangerous_zip_path(raw_name):
                    return {"ok": False, "message": "Backup inválido: contiene rutas peligrosas."}
                special_reason = _zip_entry_is_symlink_or_special(info)
                if special_reason:
                    return {"ok": False, "message": "Backup inválido: contiene symlinks o archivos especiales."}
                blocked_reason = _is_blocked_backup_entry(normalized)
                if blocked_reason:
                    return {"ok": False, "message": f"Backup inválido: contiene archivo no permitido: {normalized}."}
                names.add(normalized)
                if info.is_dir():
                    continue
                files_count += 1
                if normalized.startswith("static/uploads/"):
                    uploads_count += 1
                if normalized == "agv.db":
                    db_info = info

            if "agv.db" not in names or db_info is None:
                return {"ok": False, "message": "Backup inválido: falta agv.db."}
            if "backup_manifest.json" not in names:
                return {"ok": False, "message": "Backup inválido: falta backup_manifest.json."}
            try:
                manifest = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
            except Exception:
                return {"ok": False, "message": "Backup inválido: manifest inválido."}

            for info in zf.infolist():
                normalized = (info.filename or "").replace("\\", "/").strip("/")
                if not normalized or info.is_dir() or normalized == "backup_manifest.json":
                    continue
                if normalized == "agv.db":
                    target = db_staging_path
                elif normalized.startswith("static/uploads/"):
                    target = os.path.abspath(os.path.join(staging_dir, normalized))
                else:
                    continue
                if os.path.commonpath([staging_dir, os.path.abspath(target)]) != staging_dir:
                    return {"ok": False, "message": "Backup inválido: ruta fuera de staging."}
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile:
        return {"ok": False, "message": "Backup inválido: no es un ZIP válido."}

    try:
        con = sqlite3.connect(db_staging_path)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
    except Exception:
        return {"ok": False, "message": "Backup inválido: agv.db no es SQLite válido."}
    if not integrity or str(integrity[0]).lower() != "ok":
        return {"ok": False, "message": "Backup inválido: integrity_check falló."}
    return {
        "ok": True,
        "message": "Backup válido.",
        "db_ok": True,
        "uploads_count": uploads_count,
        "files_count": files_count,
        "manifest": manifest,
        "db_path": db_staging_path,
        "uploads_path": os.path.join(staging_dir, "static", "uploads"),
    }


@app.post("/api/admin/backup/validate")
async def admin_validate_backup(file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    tmp_path = None
    temp_dir = None
    started = time.monotonic()
    upload_size_bytes = 0
    try:
        require_admin(x_admin_token)
        filename = (file.filename or "").strip()
        logger.info("Backup validation requested filename=%s", os.path.basename(filename))
        if not filename.lower().endswith(".zip"):
            return _backup_validation_failure("el archivo debe ser .zip.")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                upload_size_bytes += len(chunk)
                tmp.write(chunk)
        finally:
            tmp.close()

        manifest = {}
        uploads_count = 0
        files_count = 0
        db_info = None

        try:
            with zipfile.ZipFile(tmp_path) as zf:
                bad_entry = zf.testzip()
                if bad_entry:
                    return _backup_validation_failure("ZIP corrupto.")

                names = set()
                for info in zf.infolist():
                    raw_name = info.filename or ""
                    normalized = raw_name.replace("\\", "/").strip("/")
                    if not normalized:
                        continue
                    if _is_dangerous_zip_path(raw_name):
                        return _backup_validation_failure("contiene rutas peligrosas.")
                    special_reason = _zip_entry_is_symlink_or_special(info)
                    if special_reason:
                        return _backup_validation_failure("contiene symlinks o archivos especiales.")
                    blocked_reason = _is_blocked_backup_entry(normalized)
                    if blocked_reason:
                        return _backup_validation_failure(f"contiene archivo no permitido: {normalized}.")
                    names.add(normalized)
                    if not info.is_dir():
                        files_count += 1
                        if normalized.startswith("static/uploads/"):
                            uploads_count += 1
                        if normalized == "agv.db":
                            db_info = info

                if "agv.db" not in names or db_info is None:
                    return _backup_validation_failure("falta agv.db.")
                if "backup_manifest.json" not in names:
                    return _backup_validation_failure("falta backup_manifest.json.")

                try:
                    manifest = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
                except Exception:
                    return _backup_validation_failure("manifest inválido.")

                temp_dir = tempfile.mkdtemp(prefix="agv_backup_validate_")
                db_tmp_path = os.path.join(temp_dir, "agv.db")
                with zf.open(db_info) as src, open(db_tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

            try:
                con = sqlite3.connect(db_tmp_path)
                try:
                    integrity = con.execute("PRAGMA integrity_check").fetchone()
                finally:
                    con.close()
            except Exception:
                return _backup_validation_failure("agv.db no es SQLite válido.")

            if not integrity or str(integrity[0]).lower() != "ok":
                return _backup_validation_failure("integrity_check falló.")

        except zipfile.BadZipFile:
            return _backup_validation_failure("no es un ZIP válido.")

        logger.info("Backup validation passed filename=%s size_bytes=%s files=%s uploads=%s duration_ms=%s", os.path.basename(filename), upload_size_bytes, files_count, uploads_count, int((time.monotonic() - started) * 1000))
        return {
            "ok": True,
            "message": "Backup válido.",
            "db_ok": True,
            "uploads_count": uploads_count,
            "files_count": files_count,
            "manifest": manifest,
        }
    except HTTPException:
        logger.warning("Backup validation failed")
        raise
    except Exception:
        logger.exception("Backup validation failed")
        return {"ok": False, "message": "Backup inválido: no se pudo validar."}
    finally:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        if temp_dir:
            with contextlib.suppress(OSError):
                shutil.rmtree(temp_dir)


@app.post("/api/admin/backup/restore")
async def admin_restore_backup(file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    tmp_path = None
    staging_dir = None
    uploads_old_path = None
    uploads_new_path = None
    db_restore_tmp_path = None
    started = time.monotonic()
    upload_size_bytes = 0
    try:
        require_admin(x_admin_token)
        filename = (file.filename or "").strip()
        logger.info("Backup restore requested filename=%s", os.path.basename(filename))
        if not filename.lower().endswith(".zip"):
            logger.warning("Backup restore failed")
            return {"ok": False, "message": "Backup inválido: el archivo debe ser .zip."}

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                upload_size_bytes += len(chunk)
                tmp.write(chunk)
        finally:
            tmp.close()

        staging_dir = tempfile.mkdtemp(prefix="agv_backup_restore_")
        validation = _validate_backup_zip_to_staging(tmp_path, staging_dir)
        if not validation.get("ok"):
            logger.warning("Backup restore failed")
            return {"ok": False, "message": validation.get("message") or "Backup inválido."}

        pre_restore_path = _create_pre_restore_backup()

        db_abs_path = _active_database_abs_path()
        uploads_abs_path = os.path.abspath(UPLOAD_DIR)
        static_abs_path = os.path.abspath("static")
        if os.path.commonpath([static_abs_path, uploads_abs_path]) != static_abs_path:
            raise RuntimeError("uploads fuera de static")

        db_dir = os.path.dirname(db_abs_path)
        db_restore_tmp_path = os.path.join(db_dir, f".agv_restore_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.db")
        shutil.copy2(validation["db_path"], db_restore_tmp_path)

        uploads_parent = os.path.dirname(uploads_abs_path)
        uploads_new_path = os.path.join(uploads_parent, f".uploads_restore_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        staged_uploads_path = validation.get("uploads_path")
        if staged_uploads_path and os.path.isdir(staged_uploads_path):
            shutil.copytree(staged_uploads_path, uploads_new_path, symlinks=False)
        else:
            os.makedirs(uploads_new_path, exist_ok=True)

        with contextlib.suppress(Exception):
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL);")
        engine.dispose()

        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(OSError):
                os.remove(db_abs_path + suffix)
        os.replace(db_restore_tmp_path, db_abs_path)
        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(OSError):
                os.remove(db_abs_path + suffix)

        if os.path.exists(uploads_abs_path):
            uploads_old_path = os.path.join(uploads_parent, f".uploads_before_restore_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
            os.replace(uploads_abs_path, uploads_old_path)
        os.replace(uploads_new_path, uploads_abs_path)
        if uploads_old_path:
            with contextlib.suppress(OSError):
                shutil.rmtree(uploads_old_path)

        _set_restore_pending_restart(True)
        logger.info("Restore pending restart set")
        logger.info("Backup restore restart requested")
        restart_result = _schedule_software_restart()
        restart_mode = restart_result.detected_mode or restart_result.mode
        restart_scheduled = restart_result.ok and restart_mode in {"windows_script", "systemd"}
        if restart_scheduled:
            logger.info("Backup restore restart scheduled")
        elif not restart_result.ok:
            logger.warning("Backup restore restart failed")
        if restart_mode == "disabled":
            restore_message = f"Backup restaurado correctamente.\n{restart_result.message}"
        elif restart_mode == "systemd" and restart_scheduled:
            restore_message = "Backup restaurado correctamente.\nSe solicitó reinicio por systemd."
        elif restart_scheduled:
            restore_message = "Backup restaurado correctamente. La aplicación se reiniciará automáticamente."
        else:
            restore_message = "Backup restaurado correctamente.\nReinicio pendiente: reinicia la aplicación manualmente para aplicar completamente los cambios."
        logger.info("Backup restore applied filename=%s size_bytes=%s files=%s uploads=%s pre_restore=%s restart_scheduled=%s duration_ms=%s", os.path.basename(filename), upload_size_bytes, validation.get("files_count"), validation.get("uploads_count"), os.path.basename(pre_restore_path), restart_scheduled, int((time.monotonic() - started) * 1000))
        return {
            "ok": True,
            "message": restore_message,
            "pre_restore_backup": os.path.basename(pre_restore_path),
            "restore_pending_restart": True,
            "restart_scheduled": restart_scheduled,
            "restart": restart_result.model_dump(),
        }
    except HTTPException:
        logger.warning("Backup restore failed")
        raise
    except Exception:
        logger.exception("Backup restore failed")
        return {"ok": False, "message": "No se pudo restaurar el backup."}
    finally:
        if db_restore_tmp_path:
            with contextlib.suppress(OSError):
                os.remove(db_restore_tmp_path)
        if uploads_new_path:
            with contextlib.suppress(OSError):
                shutil.rmtree(uploads_new_path)
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        if staging_dir:
            with contextlib.suppress(OSError):
                shutil.rmtree(staging_dir)


@app.get("/api/admin/backup/status")
def admin_backup_status(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Restore pending restart status requested")
    return _restore_pending_restart_status()


@app.post("/api/admin/backup/mark-restarted")
def admin_backup_mark_restarted(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    _set_restore_pending_restart(False)
    logger.info("Restore marked as restarted manually")
    return _restore_pending_restart_status()


@app.get("/api/admin/backup/pre-restore/list")
def admin_list_pre_restore_backups(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Pre-restore backup list requested")
    backup_dir = _pre_restore_backup_dir()
    backups = []
    if os.path.isdir(backup_dir):
        for entry in os.scandir(backup_dir):
            if not entry.is_file(follow_symlinks=False) or not entry.name.lower().endswith(".zip"):
                continue
            stat_result = entry.stat(follow_symlinks=False)
            backups.append({
                "name": entry.name,
                "size": stat_result.st_size,
                "modified_at": datetime.fromtimestamp(stat_result.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    backups.sort(key=lambda item: item["modified_at"], reverse=True)
    return {"ok": True, "backups": backups}


@app.get("/api/admin/backup/pre-restore/download/{filename}")
def admin_download_pre_restore_backup(filename: str, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    try:
        require_admin(x_admin_token)
        logger.info("Pre-restore backup download requested")
        backup_path = _safe_pre_restore_backup_path(filename)
        if not os.path.isfile(backup_path):
            raise HTTPException(status_code=404, detail="Backup no encontrado")
        return FileResponse(
            backup_path,
            media_type="application/zip",
            filename=os.path.basename(backup_path),
        )
    except HTTPException:
        logger.warning("Pre-restore backup download failed")
        raise
    except Exception:
        logger.exception("Pre-restore backup download failed")
        raise HTTPException(status_code=500, detail="No se pudo descargar el backup previo")


@app.get("/api/catalog", response_model=CatalogOut)
def get_catalog():
    with SessionLocal() as db:
        _auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow())
        areas = [_to_area_out(r) for r in db.execute(select(Area).order_by(Area.priority.asc(), Area.name.asc())).scalars().all()]
        materials = [_to_material_out(r) for r in db.execute(select(MaterialGroup).order_by(MaterialGroup.name.asc())).scalars().all()]
        racks = [_rack_out(db, r) for r in db.execute(select(Rack).order_by(Rack.code.asc())).scalars().all()]
        return CatalogOut(areas=areas, materials=materials, racks=racks)


@app.post("/api/fifo/validate", response_model=FifoPreviewOut)
def validate_fifo_request(body: FifoRequestIn):
    with SessionLocal() as db:
        logger.info("FIFO validate source_area_id=%s destination_area_id=%s material_group_id=%s priority=%s", body.source_area_id, body.destination_area_id, body.material_group_id, body.priority)
        selection = resolve_fifo_request(db, body.source_area_id, body.destination_area_id, body.material_group_id, body.priority)
        return FifoPreviewOut(**build_fifo_preview_payload(selection))


@app.post("/api/fifo/execute", response_model=MovementOrderOut)
def create_fifo_movement(body: FifoRequestIn):
    with SessionLocal() as db:
        logger.info("FIFO execute source_area_id=%s destination_area_id=%s material_group_id=%s priority=%s created_by=%s", body.source_area_id, body.destination_area_id, body.material_group_id, body.priority, body.created_by)
        order, _selection = execute_fifo_request(
            db,
            body.source_area_id,
            body.destination_area_id,
            body.material_group_id,
            body.priority,
            body.comment,
            body.created_by,
            body.agv_code,
            body.task_typ,
        )
        logger.info("Task created | task_id=%s | order_code=%s | source=fifo | rack_id=%s | agv=%s | status=%s", order.id, order.order_code, order.rack_id, order.agv_code or "-", order.status)
        rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none()
        if rack:
            _audit_rack_reservation_change(db, rack=rack, previous_status="available", new_status=rack.status, source="local_ui", related_order_id=order.id, reason="create_fifo_order", actor=body.created_by or "operador", auto_commit=False)
            db.commit()
        dispatch_result = _dispatch_movement_order(db, order)
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or "El RCS rechazó la creación de la tarea"
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f"El RCS no aceptó la tarea: {message}")
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=500, detail="La orden enviada ya no está disponible")
        return _movement_order_out(db, order)


@app.post("/api/direct-move/execute", response_model=MovementOrderOut)
def create_direct_move_movement(body: DirectMoveRequestIn):
    with SessionLocal() as db:
        logger.info("Direct move execute source_cell_id=%s destination_cell_id=%s priority=%s created_by=%s", body.source_cell_id, body.destination_cell_id, body.priority, body.created_by)
        order = execute_direct_move_request(
            db,
            body.source_cell_id,
            body.destination_cell_id,
            body.priority,
            body.comment,
            body.created_by,
            body.agv_code,
            body.task_typ,
        )
        logger.info("Task created | task_id=%s | order_code=%s | source=direct_move | rack_id=%s | agv=%s | status=%s", order.id, order.order_code, order.rack_id, order.agv_code or "-", order.status)
        rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none()
        if rack:
            _audit_rack_reservation_change(db, rack=rack, previous_status="available", new_status=rack.status, source="local_ui", related_order_id=order.id, reason="create_direct_move_order", actor=body.created_by or "operador", auto_commit=False)
            db.commit()
        dispatch_result = _dispatch_movement_order(db, order)
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or "El RCS rechazó la creación de la tarea"
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f"El RCS no aceptó la tarea: {message}")
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=500, detail="La orden enviada ya no está disponible")
        return _movement_order_out(db, order)


@app.get("/api/movement-orders", response_model=List[MovementOrderOut])
def list_movement_orders():
    with SessionLocal() as db:
        return _list_movement_orders_out(db)


@app.get("/api/movement-orders/{order_id}/json", response_model=MovementOrderJsonOut)
def get_movement_order_json(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        generated_payload = _movement_order_json_payload(db, row)
        payload, source = _current_movement_order_payload(row, generated_payload)
        return MovementOrderJsonOut(order_id=row.id, order_code=row.order_code, payload=payload, source=source)


@app.post("/api/movement-orders/{order_id}/json", response_model=MovementOrderJsonOut)
def save_movement_order_json(order_id: int, body: MovementOrderJsonIn):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        row = _save_movement_order_payload_override(db, row, body.payload)
        logger.info("Task JSON updated | task_id=%s | order_code=%s | status=%s | rack_id=%s", row.id, row.order_code, row.status, row.rack_id)
        return MovementOrderJsonOut(order_id=row.id, order_code=row.order_code, payload=body.payload, source="edited")


@app.post("/api/movement-orders/{order_id}/json/reset", response_model=MovementOrderJsonOut)
def reset_movement_order_json(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        row = _reset_movement_order_payload_override(db, row)
        payload = _movement_order_json_payload(db, row)
        logger.info("Task JSON reset | task_id=%s | order_code=%s | status=%s | rack_id=%s", row.id, row.order_code, row.status, row.rack_id)
        return MovementOrderJsonOut(order_id=row.id, order_code=row.order_code, payload=payload, source="generated")


@app.get("/api/movement-orders/{order_id}/dispatch-response", response_model=MovementOrderDispatchOut)
def get_movement_order_dispatch_response(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        return _movement_order_dispatch_out(row)


@app.post("/api/movement-orders/{order_id}/dispatch", response_model=MovementOrderDispatchOut)
def dispatch_movement_order(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        logger.info("Manual dispatch requested order_id=%s order_code=%s", row.id, row.order_code)
        return _dispatch_movement_order(db, row)


@app.post("/api/movement-orders/monitor/run")
def run_task_monitor_once(body: Optional[MonitorRunIn] = None, x_admin_token: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    from monitor_service import check_active_tasks
    logger.info("Manual task monitor run requested")
    checked = check_active_tasks(
        apply_delay_seconds=0.0,
        base_url_override=(body.base_url if body else None),
        endpoint_override=(body.endpoint if body else None),
        wait_for_lock=False,
    )
    return {
        "ok": True,
        "checked": checked,
        "mode": "manual_admin",
        "message": "Monitoreo ejecutado manualmente por admin. El monitoreo automático normal lo realiza TaskMonitor en backend.",
    }


@app.get("/api/movement-orders/{order_id}/status-query-template")
def get_movement_order_status_query_template(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        active_rows = db.execute(
            select(MovementOrder).where(MovementOrder.status.in_(("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"))).order_by(MovementOrder.id.asc())
        ).scalars().all()
        task_codes = []
        for item in active_rows:
            code = (item.remote_task_code or "").strip()
            if code and code not in task_codes and (item.dispatch_status or "") == "success":
                task_codes.append(code)
        payload = build_auto_status_query_payload(task_codes=task_codes)
        return {"order_id": row.id, "order_code": row.order_code, "payload": payload}


@app.post("/api/movement-orders/{order_id}/status-query", response_model=MovementOrderOut)
def run_manual_status_query(order_id: int, body: MovementOrderStatusQueryIn):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        payload = body.payload or {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="El JSON de consulta debe ser un objeto.")
        client = _get_rcs_client_with_overrides(db, base_url=body.base_url, query_endpoint=body.endpoint)
        debug_base_url, debug_endpoint = _resolve_rcs_target(db, base_url=body.base_url, endpoint=body.endpoint, mode="query")
        request_payload = dict(payload)
        req_code_from_request = str(request_payload.get("reqCode") or "").strip()
        try:
            _append_debug_console_event(db, direction="sent", module="status_query", base_url=debug_base_url, endpoint=debug_endpoint, payload=request_payload, message=f"Consulta manual para {row.order_code}")
            response = client.query_task_status_with_payload(request_payload)
            _append_debug_console_event(db, direction="received", module="status_query", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or f"Respuesta consulta para {row.order_code}")
            now = datetime.utcnow()
            remote_task_code = (row.remote_task_code or "").strip()
            matched_item = next((item for item in (response.task_statuses or []) if remote_task_code and str(item.get("taskCode") or item.get("task_code") or "").strip() == remote_task_code), None)
            request_task_code = str(request_payload.get("taskCode") or "").strip()
            request_task_codes = request_payload.get("taskCodes")
            if not isinstance(request_task_codes, list):
                request_task_codes = []
            request_task_codes = [str(code or "").strip() for code in request_task_codes if str(code or "").strip()]
            response_matches_order = bool(
                matched_item
                or (remote_task_code and request_task_code == remote_task_code)
                or (remote_task_code and len(request_task_codes) == 1 and request_task_codes[0] == remote_task_code)
            )
            matched_response_payload = matched_item.get("raw") if isinstance(matched_item, dict) else response.raw
            matched_status = str((matched_item or {}).get("taskStatus") or (matched_item or {}).get("task_status") or (response.task_status if response_matches_order else "") or "").strip().lower()
            matched_message = str((matched_item or {}).get("message") or response.message or row.rcs_message or "")
            row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
            row.status_query_response_json = json.dumps(matched_response_payload, ensure_ascii=False)
            row.status_query_checked_at = now
            row.rcs_last_update = now
            if req_code_from_request:
                row.req_code = req_code_from_request
            elif response.reqCode:
                row.req_code = str(response.reqCode)
            if response_matches_order:
                row.rcs_status = matched_status or row.rcs_status
            row.rcs_message = matched_message or row.rcs_message
            _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=matched_response_payload, message=matched_message or "", arrived_at=now)
            db.commit()
            db.refresh(row)
            if response_matches_order and matched_status:
                row = apply_remote_status_to_order(db, row, matched_status, source="rcs_callback")
            else:
                logger.warning(
                    "RCS_STATUS_UNMATCHED_IGNORED order_id=%s order_code=%s remote_task_code=%s request_task_code=%s request_task_codes=%s source=manual_status_query",
                    row.id,
                    row.order_code,
                    remote_task_code,
                    request_task_code,
                    request_task_codes,
                )
            row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
            if response_matches_order:
                row.rcs_status = matched_status or row.rcs_status
            row.rcs_message = matched_message or row.rcs_message
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return _movement_order_out(db, row)
        except RcsError as exc:
            _append_debug_console_event(db, direction="received", module="status_query", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
            now = datetime.utcnow()
            error_payload = {"error": str(exc)}
            row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
            row.status_query_response_json = json.dumps(error_payload, ensure_ascii=False)
            row.status_query_checked_at = now
            row.rcs_last_update = now
            if req_code_from_request:
                row.req_code = req_code_from_request
            row.rcs_message = str(exc)
            _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=error_payload, message=str(exc), arrived_at=now)
            db.commit()
            db.refresh(row)
            return _movement_order_out(db, row)


@app.get("/api/rcs/debug-log", response_model=List[DebugConsoleEventOut])
def get_rcs_debug_log(limit: int = 200):
    limit = max(1, min(int(limit or 200), 1000))
    with SessionLocal() as db:
        rows = db.execute(select(DebugConsoleEvent).order_by(DebugConsoleEvent.id.desc()).limit(limit)).scalars().all()
        rows = list(reversed(rows))
        return [_debug_console_event_out(row) for row in rows]


@app.post("/api/rcs/debug-send")
def send_debug_console_json(body: DebugConsoleSendIn):
    with SessionLocal() as db:
        payload = body.payload or {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="El JSON de consola debe ser un objeto.")
        base_url, endpoint = _resolve_rcs_target(db, base_url=body.base_url, endpoint=body.endpoint, mode="query")
        if not base_url:
            raise HTTPException(status_code=400, detail="Configura la URL base del RCS o escríbela en la consola.")
        client = _get_rcs_client_with_overrides(db, base_url=base_url, query_endpoint=endpoint)
        _append_debug_console_event(db, direction="sent", module="debug_console", base_url=base_url, endpoint=endpoint, payload=payload, message="Envío libre desde consola")
        try:
            raw_response = client.post_json_payload(payload, endpoint_override=endpoint)
            _append_debug_console_event(db, direction="received", module="debug_console", base_url=base_url, endpoint=endpoint, payload=raw_response, message=str(raw_response.get("message") or "Respuesta recibida"))
            return {"ok": True, "base_url": base_url, "endpoint": endpoint, "response": raw_response}
        except RcsError as exc:
            error_payload = {"error": str(exc)}
            _append_debug_console_event(db, direction="received", module="debug_console", base_url=base_url, endpoint=endpoint, payload=error_payload, message=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/movement-orders/{order_id}/simulate-complete", response_model=MovementOrderOut)
def simulate_complete_movement_order(order_id: int):
    with SessionLocal() as db:
        before_order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        before_rack_id = before_order.rack_id if before_order else None
        before_rack = db.execute(select(Rack).where(Rack.id == before_rack_id)).scalar_one_or_none() if before_rack_id else None
        old_rack_status = before_rack.status if before_rack else None
        row = simulate_order_completed(db, order_id)
        logger.info("Task completed | task_id=%s | order_code=%s | rack_id=%s | status=%s | source=simulate_complete", row.id, row.order_code, row.rack_id, row.status)
        if before_rack_id and old_rack_status and not rack_status_is_available(old_rack_status):
            rack = db.execute(select(Rack).where(Rack.id == before_rack_id)).scalar_one_or_none()
            if rack and rack_status_is_available(rack.status):
                _log_rack_status_change(db, rack=rack, old_status=old_rack_status, new_status=rack.status, reason="order_completed", order_id=row.id, auto_commit=True)
        return _movement_order_out(db, row)


@app.post("/api/movement-orders/{order_id}/undo", response_model=MovementOrderOut)
def undo_selected_movement_order(order_id: int, body: Optional[MovementOrderUndoIn] = None):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        if (row.status or "").strip().lower() == "completed":
            raise HTTPException(status_code=400, detail="La orden ya esta completada y no puede cancelarse desde historial")
        return_area = None
        if body and body.return_area_id:
            return_area = db.execute(select(Area).where(Area.id == body.return_area_id)).scalar_one_or_none()
            if not return_area:
                raise HTTPException(status_code=404, detail="Area de devolucion no encontrada")
        source_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none()
        return_to_area = bool(body.return_to_area) if body is not None else True
        if return_to_area and return_area:
            matter_area = (return_area.matter_area or '').strip()
        elif return_to_area and body and body.matter_area is not None:
            matter_area = (body.matter_area or '').strip()
        else:
            matter_area = (source_cell.code or '').strip() if source_cell else ''
        if not return_to_area:
            matter_area = ''
        force_cancel = '1' if return_to_area else '0'
        row = _execute_cancel_for_order(db, row, force_cancel, matter_area, undo_on_accept=return_to_area)
        return _movement_order_out(db, row)


@app.delete("/api/admin/movement-orders/{order_id}")
def admin_delete_movement_order(order_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        order_code = row.order_code
        db.delete(row)
        db.commit()
        logger.info("Movement order deleted by admin order_id=%s order_code=%s", order_id, order_code)
        return {"ok": True, "order_id": order_id, "order_code": order_code, "message": f"Orden {order_code} borrada permanentemente del historial y del monitoreo."}


def _first_available_location_xy(db, max_cols: int = DB_GRID_W, max_rows: int = DB_GRID_H) -> tuple[int, int]:
    used = {
        (int(row.x), int(row.y))
        for row in db.execute(select(Location.x, Location.y)).all()
    }
    for y in range(max_rows):
        for x in range(max_cols):
            if (x, y) not in used:
                return x, y
    raise HTTPException(status_code=409, detail="No hay coordenadas internas disponibles dentro del rango configurado")


@app.post("/api/admin/locations/free", response_model=LocationOut)
def admin_create_free_location(body: LocationFreeCreate, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        max_rows = int(body.display_rows or get_setting(db, "display_rows", str(DB_GRID_H)))
        max_cols = int(body.display_cols or get_setting(db, "display_cols", str(DB_GRID_W)))
        max_rows = max(1, min(DB_GRID_H, max_rows))
        max_cols = max(1, min(DB_GRID_W, max_cols))
        r = db.execute(
            select(Location)
            .where(
                Location.x >= 0,
                Location.y >= 0,
                Location.x < max_cols,
                Location.y < max_rows,
                Location.is_visible == 0,
                Location.free_enabled == 0,
                Location.rack_id.is_(None),
                Location.area_id.is_(None),
                Location.code.is_(None),
            )
            .order_by(Location.y.asc(), Location.x.asc())
        ).scalars().first()
        if not r:
            x, y = _first_available_location_xy(db, max_cols=max_cols, max_rows=max_rows)
            r = Location(x=x, y=y, status=0, is_visible=0, enabled=1)
        r.code = (body.code or "").strip() or r.code
        r.is_visible = 1
        r.free_enabled = 0
        r.free_x = None
        r.free_y = None
        r.free_w = None
        r.free_h = None
        _sync_location_status(r)
        db.add(r)
        db.commit()
        db.refresh(r)
        logger.info("Free-layout cell created location_id=%s x=%s y=%s free_x=%s free_y=%s", r.id, r.x, r.y, r.free_x, r.free_y)
        return _location_out(db, r)


@app.post("/api/admin/locations/free-layout/from-grid")
def admin_init_free_layout_from_grid(body: LocationFreeLayoutInitIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    updated = 0
    with SessionLocal() as db:
        rows = db.execute(select(Location)).scalars().all()
        for r in rows:
            if int(r.is_visible or 0) != 1:
                continue
            if int(body.only_missing or 0) == 1 and int(r.free_enabled or 0) == 1 and r.free_x is not None and r.free_y is not None:
                continue
            r.free_enabled = 1
            r.free_x = float(r.x) * float(body.pitch)
            r.free_y = float(r.y) * float(body.pitch)
            r.free_w = float(body.cell_size)
            r.free_h = float(body.cell_size)
            db.add(r)
            updated += 1
        db.commit()
    logger.info("Free-layout initialized from grid updated=%s only_missing=%s", updated, body.only_missing)
    return {"ok": True, "updated": updated}


@app.patch("/api/admin/locations/{location_id}/free-layout", response_model=LocationOut)
def admin_patch_location_free_layout(location_id: int, patch: LocationFreeLayoutPatch, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        r = db.get(Location, location_id)
        if not r:
            raise HTTPException(status_code=404, detail="UbicaciÃ³n no encontrada")
        if patch.free_enabled is not None:
            r.free_enabled = patch.free_enabled
        if patch.free_x is not None:
            r.free_x = patch.free_x
        if patch.free_y is not None:
            r.free_y = patch.free_y
        if patch.free_w is not None:
            r.free_w = patch.free_w
        if patch.free_h is not None:
            r.free_h = patch.free_h
        db.add(r)
        db.commit()
        db.refresh(r)
        logger.info("Free-layout cell patched location_id=%s free_enabled=%s free_x=%s free_y=%s free_w=%s free_h=%s", r.id, r.free_enabled, r.free_x, r.free_y, r.free_w, r.free_h)
        return _location_out(db, r)


@app.get("/api/admin/locations/{x}/{y}", response_model=LocationOut)
def admin_get_location(x: int, y: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    validate_xy(x, y)
    with SessionLocal() as db:
        r = db.execute(select(Location).where(Location.x == x, Location.y == y)).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada")
        return _location_out(db, r)


@app.patch("/api/admin/locations/{x}/{y}", response_model=LocationOut)
def admin_patch_location(x: int, y: int, patch: LocationPatchAdmin, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    validate_xy(x, y)
    with SessionLocal() as db:
        r = db.execute(select(Location).where(Location.x == x, Location.y == y)).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada")
        area_id = patch.area_id if patch.area_id is not None else r.area_id
        rack_id = patch.rack_id if patch.rack_id is not None else r.rack_id
        _validate_foreign_keys(db, area_id, rack_id, ignore_xy=(x, y))
        if patch.is_visible is not None:
            r.is_visible = patch.is_visible
        if patch.status is not None:
            r.status = patch.status
        if patch.code is not None:
            r.code = patch.code.strip() or None
        if patch.enabled is not None:
            r.enabled = patch.enabled
        if patch.note is not None:
            r.note = patch.note.strip() or None
        if patch.area_id is not None:
            r.area_id = patch.area_id
        if patch.rack_id is not None:
            r.rack_id = patch.rack_id
        _sync_location_status(r)
        db.add(r)
        db.commit()
        db.refresh(r)
        logger.info("Cell patched x=%s y=%s location_id=%s rack_id=%s area_id=%s", x, y, r.id, r.rack_id, r.area_id)
        return _location_out(db, r)


@app.put("/api/admin/locations/{x}/{y}", response_model=LocationOut)
def admin_save_location(x: int, y: int, body: CellDetailSave, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    validate_xy(x, y)
    with SessionLocal() as db:
        r = db.execute(select(Location).where(Location.x == x, Location.y == y)).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada")
        _validate_foreign_keys(db, body.area_id, body.rack_id, ignore_xy=(x, y))
        r.code = (body.code or "").strip() or None
        r.enabled = body.enabled
        r.is_visible = body.is_visible
        r.note = (body.note or "").strip() or None
        r.area_id = body.area_id
        r.rack_id = body.rack_id
        _sync_location_status(r)
        db.add(r)
        db.commit()
        db.refresh(r)
        logger.info("Cell saved x=%s y=%s location_id=%s rack_id=%s area_id=%s", x, y, r.id, r.rack_id, r.area_id)
        return _location_out(db, r)


@app.get("/api/admin/areas", response_model=List[AreaOut])
def list_areas(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return [_to_area_out(r) for r in db.execute(select(Area).order_by(Area.priority.asc(), Area.name.asc())).scalars().all()]


@app.post("/api/admin/areas", response_model=AreaOut)
def create_area(body: AreaIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        exists = db.execute(select(Area).where(Area.code == body.code.strip())).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="Ya existe un área con ese código")
        row = Area(code=body.code.strip(), name=body.name.strip(), description=(body.description or "").strip() or None, matter_area=(body.matter_area or "").strip() or None, color=body.color.strip(), area_type=body.area_type.strip(), is_active=body.is_active, priority=body.priority, updated_at=datetime.utcnow())
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Area created area_id=%s code=%s", row.id, row.code)
        return _to_area_out(row)


@app.put("/api/admin/areas/{area_id}", response_model=AreaOut)
def update_area(area_id: int, body: AreaIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Área no encontrada")
        dup = db.execute(select(Area).where(Area.code == body.code.strip(), Area.id != area_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe un área con ese código")
        row.code = body.code.strip()
        row.name = body.name.strip()
        row.description = (body.description or "").strip() or None
        row.matter_area = (body.matter_area or "").strip() or None
        row.color = body.color.strip()
        row.area_type = body.area_type.strip()
        row.is_active = body.is_active
        row.priority = body.priority
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Area updated area_id=%s code=%s", row.id, row.code)
        return _to_area_out(row)


@app.delete("/api/admin/areas/{area_id}")
def delete_area(area_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Área no encontrada")
        _ensure_area_not_in_use(db, area_id)
        db.delete(row)
        db.commit()
        logger.info("Area deleted area_id=%s code=%s", area_id, row.code)
        return {"ok": True}


@app.get("/api/admin/materials", response_model=List[MaterialGroupOut])
def list_materials(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return [_to_material_out(r) for r in db.execute(select(MaterialGroup).order_by(MaterialGroup.name.asc())).scalars().all()]


@app.post("/api/admin/materials", response_model=MaterialGroupOut)
def create_material(body: MaterialGroupIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        exists = db.execute(select(MaterialGroup).where(MaterialGroup.code == body.code.strip())).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="Ya existe un material con ese código")
        row = MaterialGroup(code=body.code.strip(), name=body.name.strip(), description=(body.description or "").strip() or None, color=_normalize_hex_color(body.color, _random_material_color()), is_active=body.is_active, updated_at=datetime.utcnow())
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Material created material_id=%s code=%s", row.id, row.code)
        return _to_material_out(row)


@app.put("/api/admin/materials/{material_id}", response_model=MaterialGroupOut)
def update_material(material_id: int, body: MaterialGroupIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Material no encontrado")
        dup = db.execute(select(MaterialGroup).where(MaterialGroup.code == body.code.strip(), MaterialGroup.id != material_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe un material con ese código")
        row.code = body.code.strip()
        row.name = body.name.strip()
        row.description = (body.description or "").strip() or None
        row.color = _normalize_hex_color(body.color, getattr(row, "color", None) or _random_material_color())
        row.is_active = body.is_active
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Material updated material_id=%s code=%s", row.id, row.code)
        return _to_material_out(row)


@app.delete("/api/admin/materials/{material_id}")
def delete_material(material_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Material no encontrado")
        _ensure_material_not_in_use(db, material_id)
        db.delete(row)
        db.commit()
        logger.info("Material deleted material_id=%s code=%s", material_id, row.code)
        return {"ok": True}


def _diagnosis_rows(db, sql: str, params: Optional[dict] = None) -> List[dict]:
    return [dict(row) for row in db.execute(text(sql), params or {}).mappings().all()]


def _cleanup_min_age_minutes(db) -> int:
    try:
        return max(1, int(float(get_setting(db, "cleanup_min_age_minutes", "30") or 30)))
    except Exception:
        return 30


def _force_release_min_age_minutes(db) -> int:
    try:
        return max(1, int(float(get_setting(db, "force_release_min_age_minutes", "20") or 20)))
    except Exception:
        return 20


def _cancel_undo_auto_recovery_enabled(db) -> bool:
    try:
        return _truthy_config_value(get_setting(db, "cancel_undo_auto_recovery_enabled", "1"))
    except Exception:
        return True


def _cancel_undo_auto_recovery_min_age_minutes(db) -> int:
    try:
        return max(1, int(float(get_setting(db, "cancel_undo_auto_recovery_min_age_minutes", "5") or 5)))
    except Exception:
        return 5


def _truthy_config_value(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "s\u00ed", "on", "debug", "development", "dev"}


def _dev_tools_enabled(db=None) -> bool:
    app_env = (os.getenv("APP_ENV") or os.getenv("app_env") or "").strip().lower()
    if app_env in {"development", "dev"}:
        return True
    if _truthy_config_value(os.getenv("DEBUG") or os.getenv("debug")):
        return True
    if db is not None:
        try:
            stored_env = (get_setting(db, "app_env", "") or "").strip().lower()
            if stored_env in {"development", "dev"}:
                return True
            return _truthy_config_value(get_setting(db, "debug", ""))
        except Exception:
            return False
    return False


def _related_rack_ids_for_order(order: MovementOrder) -> List[int]:
    ids = []
    for value in (
        getattr(order, "rack_id", None),
        getattr(order, "pickup_rack_id", None),
        getattr(order, "dropoff_rack_id", None),
    ):
        if value and int(value) not in ids:
            ids.append(int(value))
    return ids


def _robot_monitor_indicates_order_running(order: MovementOrder) -> bool:
    try:
        monitor = _get_cached_robot_status_monitor_response(None, force=False, max_age_seconds=ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)
    except Exception:
        monitor = None
    if not monitor or not getattr(monitor, "robots", None):
        return False
    agv_code = str(order.agv_code or "").strip()
    remote_task_code = str(order.remote_task_code or "").strip()
    order_code = str(order.order_code or "").strip()
    running_status_codes = {"2", "3", "6", "8"}
    running_task_words = ("execut", "running", "moving", "in_progress", "working")
    for robot in monitor.robots:
        robot_code = str(robot.robotCode or robot.agvCode or "").strip()
        task_text = " ".join([
            str(robot.taskStatus or ""),
            str(robot.statusText or ""),
            str(robot.podCode or ""),
            str(robot.currentStation or ""),
        ]).strip().lower()
        same_agv = bool(agv_code and robot_code and agv_code == robot_code)
        mentions_task = bool(remote_task_code and remote_task_code.lower() in task_text) or bool(order_code and order_code.lower() in task_text)
        running = str(robot.status or "").strip() in running_status_codes or any(word in task_text for word in running_task_words)
        if running and (same_agv or mentions_task):
            return True
    return False


def _status_query_missing_task(order: MovementOrder) -> bool:
    task_code = str(order.remote_task_code or "").strip()
    if not task_code or not order.status_query_checked_at:
        return False
    payload = _safe_json_loads_any(order.status_query_response_json)
    if not isinstance(payload, dict):
        return False
    text_payload = json.dumps(payload, ensure_ascii=False).lower()
    if task_code.lower() in text_payload:
        return False
    data = payload.get("data")
    if isinstance(data, list) and not data:
        return True
    for key in ("taskStatus", "task_status", "status", "code"):
        value = str(payload.get(key, "") or "").strip().lower()
        if value in {"not_found", "not found", "missing", "none", "null", "404", "-1"}:
            return True
    message = str(payload.get("message", "") or payload.get("msg", "") or "").strip().lower()
    return any(fragment in message for fragment in ("not found", "no existe", "inexistente", "not exist", "empty", "sin datos"))


def _cancel_undo_robot_recovery_reason(order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    rcs_status = str(order.rcs_status or "").strip().lower()
    if rcs_status in CANCEL_RETURN_RCS_TERMINAL_STATUSES:
        return True, "SAFE_CANCEL_COMPLETED"
    task_code = str(order.remote_task_code or "").strip().lower()
    agv_code = str(order.agv_code or "").strip()
    try:
        monitor = _get_cached_robot_status_monitor_response(None, force=False, max_age_seconds=ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)
    except Exception:
        monitor = None
    if monitor and getattr(monitor, "robots", None):
        for robot in monitor.robots:
            robot_code = str(robot.robotCode or robot.agvCode or "").strip()
            same_agv = bool(agv_code and robot_code and agv_code == robot_code)
            task_text = " ".join([
                str(robot.taskStatus or ""),
                str(robot.statusText or ""),
                str(robot.podCode or ""),
                str(robot.currentStation or ""),
            ]).strip().lower()
            mentions_task = bool(task_code and task_code in task_text)
            running = str(robot.status or "").strip() in {"2", "3", "6", "8"} or any(word in task_text for word in ("execut", "running", "moving", "in_progress", "working"))
            if mentions_task and running:
                return False, "ROBOT_RUNNING_TASK"
            if same_agv:
                if str(robot.status or "").strip() == "4" or "idle" in task_text:
                    return True, "SAFE_IDLE"
                if not str(robot.taskStatus or "").strip() and not mentions_task:
                    return True, "SAFE_NO_TASK"
                if not running and not mentions_task:
                    return True, "SAFE_NO_TASK"
    if _status_query_missing_task(order):
        return True, "SAFE_NOT_FOUND_IN_RCS"
    if order.status_query_checked_at and order.status_query_checked_at < now - timedelta(minutes=min_age_minutes):
        if not _robot_monitor_indicates_order_running(order):
            return True, "SAFE_NO_ACTIVITY"
    return False, "SIN_EVIDENCIA_SEGURA"


def _cancel_undo_recovery_candidate(db, order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    if (order.status or "") != "cancel_requested_undo":
        return False, "NO_CANCEL_REQUESTED_UNDO"
    created_at = order.closed_at or order.updated_at or order.created_at or now
    age_minutes = int(max(0, (now - created_at).total_seconds() // 60))
    if age_minutes < min_age_minutes:
        return False, f"EDAD_INSUFICIENTE_{age_minutes}_MIN"
    safe, reason = _cancel_undo_robot_recovery_reason(order, now=now, min_age_minutes=min_age_minutes)
    if not safe:
        return False, reason
    if db is not None and _active_orders_for_rack(db, order.rack_id, exclude_order_id=order.id):
        return False, "ACTIVE_ORDER_SAME_RACK"
    return True, reason


def _validate_cleanup_order_eligible(db, order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    allowed_statuses = {"cancel_requested_undo", "cancel_requested_total", "dispatched"}
    if (order.status or "") not in allowed_statuses:
        return False, "status no elegible"
    rcs_status = str(order.rcs_status or "").strip().lower()
    if rcs_status not in CANCEL_RETURN_RCS_TERMINAL_STATUSES:
        return False, "rcs_status no es completed/cancelled"
    created_at = order.created_at or order.updated_at or now
    if created_at > now - timedelta(minutes=min_age_minutes):
        return False, f"menor a {min_age_minutes} minutos"
    if (order.status or "") == "in_progress":
        return False, "movimiento activo"
    if _robot_monitor_indicates_order_running(order):
        return False, "AGV ejecutando la orden"
    if (order.status or "") == "cancel_requested_undo":
        return True, "SAFE_CANCEL_COMPLETED"
    return True, f"SAFE_RCS_{rcs_status.upper()}"


def _rack_safe_to_release_after_cleanup(db, rack_id: int, closed_order_id: int, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    recent_cutoff = now - timedelta(minutes=min_age_minutes)
    active_or_recent = db.execute(
        select(MovementOrder).where(
            MovementOrder.id != closed_order_id,
            (
                (MovementOrder.rack_id == rack_id)
                | (MovementOrder.pickup_rack_id == rack_id)
                | (MovementOrder.dropoff_rack_id == rack_id)
            ),
            (
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES)
                | (MovementOrder.status == "in_progress")
                | ((MovementOrder.created_at >= recent_cutoff) & (MovementOrder.status.notin_(("cancelled", "undone"))))
            ),
        )
    ).scalars().first()
    if active_or_recent:
        return False, f"rack asociado a orden activa/reciente {active_or_recent.id}"
    active_task_code = db.execute(
        select(MovementOrder).where(
            MovementOrder.id != closed_order_id,
            (
                (MovementOrder.rack_id == rack_id)
                | (MovementOrder.pickup_rack_id == rack_id)
                | (MovementOrder.dropoff_rack_id == rack_id)
            ),
            MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            MovementOrder.remote_task_code.is_not(None),
            MovementOrder.remote_task_code != "",
        )
    ).scalars().first()
    if active_task_code:
        return False, f"task_code activo en orden {active_task_code.id}"
    return True, ""


def _release_cleanup_rack_if_safe(db, rack_id: int, *, closed_order_id: int = 0, now: datetime, min_age_minutes: int, admin_user: str = "admin") -> tuple[Optional[dict], Optional[dict], bool]:
    rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
    if not rack:
        return None, {"rack_id": rack_id, "reason": "rack no encontrado"}, False
    safe, rack_reason = _rack_safe_to_release_after_cleanup(db, rack_id, closed_order_id, now=now, min_age_minutes=min_age_minutes)
    if not safe:
        blocking_order = db.execute(
            select(MovementOrder).where(
                MovementOrder.id != closed_order_id,
                (
                    (MovementOrder.rack_id == rack_id)
                    | (MovementOrder.pickup_rack_id == rack_id)
                    | (MovementOrder.dropoff_rack_id == rack_id)
                ),
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            )
        ).scalars().first()
        if blocking_order:
            logger.warning(
                "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
                rack.id,
                rack.code,
                closed_order_id or None,
                blocking_order.id,
                blocking_order.status,
                "cleanup_manual",
                rack_reason,
            )
        return None, {"rack_id": rack_id, "rack_code": rack.code, "reason": rack_reason}, False
    old_rack_status = rack.status or ""
    if not rack_status_is_available(old_rack_status):
        apply_rack_reservation_status(rack, False, updated_at=now, order_id=closed_order_id or None, source="cleanup_manual", reason="admin_cleanup")
        db.add(rack)
        released = {"rack_id": rack.id, "rack_code": rack.code, "old_status": old_rack_status, "new_status": rack.status}
        _audit_rack_release(db, rack=rack, previous_status=old_rack_status, new_status=rack.status, source="cleanup_manual", related_order_id=closed_order_id or None, reason="admin_cleanup", actor=admin_user, at=now, auto_commit=False)
        logger.info("[CLEANUP] Rack %s liberado %s -> %s user=%s", rack.id, old_rack_status, rack.status, admin_user)
        _append_debug_console_event(
            db,
            direction="received",
            module="cleanup",
            payload={"user": admin_user, "action": "release_rack", "rack_id": rack.id, "old_status": old_rack_status, "new_status": rack.status, "at": now.isoformat()},
            message=f"[CLEANUP] Rack {rack.id} liberado {old_rack_status} -> {rack.status}",
            created_at=now,
            auto_commit=False,
        )
        kept_location = True
    else:
        released = {"rack_id": rack.id, "rack_code": rack.code, "old_status": old_rack_status, "new_status": rack.status, "already_available": True}
        kept_location = False
    if kept_location:
        logger.info("[CLEANUP] Location conservada por seguridad rack_id=%s user=%s", rack_id, admin_user)
        _append_debug_console_event(
            db,
            direction="received",
            module="cleanup",
            payload={"user": admin_user, "action": "keep_location", "rack_id": rack_id, "at": now.isoformat()},
            message="[CLEANUP] Location conservada por seguridad",
            created_at=now,
            auto_commit=False,
        )
    return released, None, kept_location


def _close_cleanup_order(db, order: MovementOrder, *, now: datetime, reason: str, admin_user: str) -> dict:
    old_status = order.status or ""
    new_status = "undone" if old_status == "cancel_requested_undo" else "cancelled"
    order.status = new_status
    order.forced_local_close = 1
    order.forced_local_close_at = now
    order.forced_local_close_reason = reason
    order.updated_at = now
    _audit_order_close(db, order, previous_status=old_status, new_status=new_status, source="cleanup_manual", reason=reason, actor=admin_user, closed_at=now, auto_commit=False)
    db.add(order)
    logger.info("[CLEANUP] Orden %s cerrada localmente %s -> %s user=%s", order.id, old_status, new_status, admin_user)
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={"user": admin_user, "action": "close_order", "order_id": order.id, "old_status": old_status, "new_status": new_status, "reason": reason, "at": now.isoformat()},
        message=f"[CLEANUP] Orden {order.id} cerrada localmente",
        created_at=now,
        auto_commit=False,
    )
    return {"order_id": order.id, "order_code": order.order_code, "old_status": old_status, "new_status": new_status}


def _recent_order_activity_at(order: MovementOrder) -> Optional[datetime]:
    dates = [
        order.updated_at,
        order.rcs_last_update,
        order.status_query_checked_at,
        order.dispatched_at,
    ]
    return max([dt for dt in dates if dt is not None], default=None)


def _cancel_return_recovery_eligible(order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    if (order.status or "") != "cancel_requested_undo":
        return False, f"orden no es cancel_requested_undo ID {order.id}"
    if str(order.rcs_status or "").strip().lower() not in CANCEL_RETURN_RCS_TERMINAL_STATUSES:
        return False, f"No se puede liberar: cancelacion sin terminal RCS ID {order.id}."
    cancel_requested_at = order.closed_at or order.updated_at or order.created_at or now
    if cancel_requested_at > now - timedelta(minutes=min_age_minutes):
        return False, f"No se puede liberar: cancelacion reciente ID {order.id}."
    if _robot_monitor_indicates_order_running(order):
        return False, f"No se puede liberar: AGV ejecutando cancelacion ID {order.id}."
    return True, "Cancelacion con regreso finalizada por RCS; robot idle o sin tarea activa."


def _normalized_cleanup_rcs_terminal_status(value) -> str:
    return CLEANUP_RCS_TERMINAL_STATUS_MAP.get(str(value or "").strip().lower(), "")


def _validate_force_release_order(order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    cutoff = now - timedelta(minutes=min_age_minutes)
    if (order.status or "") not in RACK_RESERVATION_ACTIVE_STATUSES:
        return False, f"orden no activa ID {order.id}"
    if _normalized_cleanup_rcs_terminal_status(order.rcs_status):
        return True, "SAFE_RCS_TERMINAL"
    created_at = order.created_at or order.updated_at or now
    if created_at > cutoff:
        return False, f"No se puede liberar: orden activa reciente ID {order.id}."
    if (order.status or "") == "cancel_requested_undo":
        eligible, reason = _cancel_undo_recovery_candidate(None, order, now=now, min_age_minutes=min_age_minutes)
        return eligible, reason
    if (order.status or "") in CANCEL_REQUEST_STATUSES:
        if str(order.rcs_status or "").strip().lower() != "cancelled":
            return False, f"No se puede liberar: cancelacion sin confirmacion RCS ID {order.id}."
        cancel_requested_at = order.closed_at or order.updated_at or order.created_at or now
        if cancel_requested_at > cutoff:
            return False, f"No se puede liberar: cancelacion reciente ID {order.id}."
        if _robot_monitor_indicates_order_running(order):
            return False, f"No se puede liberar: AGV ejecutando cancelacion ID {order.id}."
        return True, ""
    recent_at = _recent_order_activity_at(order)
    if recent_at and recent_at > cutoff:
        return False, f"No se puede liberar: orden con actualización reciente ID {order.id}."
    return True, ""


def _force_close_old_active_order(db, order: MovementOrder, *, now: datetime, admin_user: str) -> dict:
    old_status = order.status or ""
    rcs_status = str(order.rcs_status or "").strip().lower()
    normalized_rcs_terminal = _normalized_cleanup_rcs_terminal_status(rcs_status)
    is_confirmed_cancel_return = old_status == "cancel_requested_undo" and rcs_status in CANCEL_RETURN_RCS_TERMINAL_STATUSES
    is_confirmed_cancel_request = old_status == "cancel_requested_total" and rcs_status == "cancelled"
    if is_confirmed_cancel_return:
        order.status = normalized_rcs_terminal
        order.rcs_status = normalized_rcs_terminal
        order.dispatch_status = order.dispatch_status or "success"
        order.forced_local_close_reason = "admin_force_release_cancel_return_terminal"
        order.rcs_message = ((order.rcs_message or "").strip() + " | Cierre local por recuperacion avanzada de cancelacion con regreso finalizada por RCS.").strip(" |")[:512]
    elif is_confirmed_cancel_request:
        order.status = "cancelled"
        order.rcs_status = "cancelled"
        order.dispatch_status = order.dispatch_status or "success"
        order.forced_local_close_reason = "admin_force_release_cancelled_cancel_request"
        order.rcs_message = ((order.rcs_message or "").strip() + " | Cierre local por recuperacion avanzada de cancelacion confirmada.").strip(" |")[:512]
    elif normalized_rcs_terminal == "completed":
        order.status = "completed"
        order.rcs_status = "completed"
        order.dispatch_status = order.dispatch_status or "success"
        order.forced_local_close_reason = "admin_force_release_rcs_completed"
        order.rcs_message = ((order.rcs_message or "").strip() + " | Cierre local por recuperacion avanzada: RCS ya confirmo completed.").strip(" |")[:512]
    elif normalized_rcs_terminal == "cancelled":
        order.status = "cancelled"
        order.rcs_status = "cancelled"
        order.dispatch_status = order.dispatch_status or "success"
        order.forced_local_close_reason = "admin_force_release_rcs_cancelled"
        order.rcs_message = ((order.rcs_message or "").strip() + " | Cierre local por recuperacion avanzada: RCS ya confirmo cancelled.").strip(" |")[:512]
    elif normalized_rcs_terminal == "failed":
        order.status = "failed"
        order.rcs_status = "failed"
        order.dispatch_status = order.dispatch_status or "success"
        order.forced_local_close_reason = "admin_force_release_rcs_failed"
        order.rcs_message = ((order.rcs_message or "").strip() + " | Cierre local por recuperacion avanzada: RCS ya confirmo failed.").strip(" |")[:512]
    else:
        order.status = "forced_local_closed"
        order.rcs_status = "unknown_or_not_found"
        order.dispatch_status = "forced_closed"
        order.forced_local_close_reason = "admin_force_release_old_active_order"
        order.rcs_message = "Cierre local forzado por admin: orden vieja no confirmada en RCS"
    order.forced_local_close = 1
    order.forced_local_close_at = now
    order.updated_at = now
    _audit_order_close(
        db,
        order,
        previous_status=old_status,
        new_status=order.status,
        source="cleanup_force_release",
        reason=order.forced_local_close_reason,
        actor=admin_user,
        closed_at=now,
        auto_commit=False,
    )
    db.add(order)
    if old_status == "cancel_requested_undo":
        rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none() if order.rack_id else None
        logger.info(
            "CANCEL_UNDO_TERMINAL_CLOSE order_id=%s rack_id=%s rack_code=%s robot_code=%s previous_dispatch_status=%s new_dispatch_status=%s rcs_status=%s source=%s reason=%s cells_modified=false",
            order.id,
            order.rack_id,
            rack.code if rack else None,
            order.agv_code,
            old_status,
            order.status,
            order.rcs_status,
            "cleanup_force_release",
            order.forced_local_close_reason,
        )
    logger.info(
        "[FORCE RELEASE] order_id=%s rack_id=%s previous_status=%s new_status=%s source=cleanup_force_release reason=%s",
        order.id,
        order.rack_id,
        old_status,
        order.status,
        order.forced_local_close_reason,
    )
    logger.info("[FORCE RELEASE] Orden %s %s user=%s old_status=%s", order.id, order.status, admin_user, old_status)
    _append_debug_console_event(
        db,
        direction="received",
        module="cleanup",
        payload={
            "user": admin_user,
            "action": "force_release_close_order",
            "order_id": order.id,
            "rack_id": order.rack_id,
            "old_status": old_status,
            "new_status": order.status,
            "reason": order.forced_local_close_reason,
            "at": now.isoformat(),
        },
        message=f"[FORCE RELEASE] Orden {order.id} forced_local_closed",
        created_at=now,
        auto_commit=False,
    )
    return {"order_id": order.id, "order_code": order.order_code, "rack_id": order.rack_id, "old_status": old_status, "new_status": order.status}


def _auto_release_stuck_cancel_return_orders(db, *, now: datetime) -> int:
    if not _cancel_undo_auto_recovery_enabled(db):
        return 0
    min_age_minutes = _cancel_undo_auto_recovery_min_age_minutes(db)
    rows = db.execute(
        select(MovementOrder, Rack)
        .join(Rack, Rack.id == MovementOrder.rack_id)
        .where(
            MovementOrder.status == "cancel_requested_undo",
        )
        .order_by(MovementOrder.updated_at.asc(), MovementOrder.id.asc())
    ).all()
    released_count = 0
    for order, rack in rows:
        age_source = order.closed_at or order.updated_at or order.created_at or now
        age_minutes = int(max(0, (now - age_source).total_seconds() // 60))
        eligible, reason = _cancel_undo_recovery_candidate(db, order, now=now, min_age_minutes=min_age_minutes)
        if not eligible:
            if reason == "ACTIVE_ORDER_SAME_RACK":
                logger.warning("AUTO_RECOVERY_BLOCKED_ACTIVE_ORDER order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=%s safe_recovery=false", order.id, order.rack_id, rack.code if rack else None, order.agv_code or "-", age_minutes, reason)
            logger.info("AUTO_RECOVERY_SKIPPED order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=%s safe_recovery=false", order.id, order.rack_id, rack.code if rack else None, order.agv_code or "-", age_minutes, reason)
            continue
        logger.info("AUTO_RECOVERY_CANDIDATE order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=%s safe_recovery=true", order.id, order.rack_id, rack.code if rack else None, order.agv_code or "-", age_minutes, reason)
        closed = _force_close_old_active_order(db, order, now=now, admin_user="auto_cleanup")
        old_status = rack.status or ""
        if not rack_status_is_available(old_status):
            apply_rack_reservation_status(rack, False, updated_at=now, order_id=order.id, dispatch_status=order.status, source="auto_cleanup", reason=reason)
            db.add(rack)
            _audit_rack_release(db, rack=rack, previous_status=old_status, new_status=rack.status, source="auto_cleanup", related_order_id=order.id, reason="auto_release_cancel_return_terminal", actor="auto_cleanup", at=now, auto_commit=False)
            _append_debug_console_event(
                db,
                direction="received",
                module="cleanup",
                payload={"action": "auto_release_cancel_return_terminal", "order": closed, "rack_id": rack.id, "rack_code": rack.code, "old_status": old_status, "new_status": rack.status, "safe_reason": reason, "at": now.isoformat()},
                message=f"[AUTO CLEANUP] Cancelacion con regreso liberada rack_id={rack.id}",
                created_at=now,
                auto_commit=False,
            )
            logger.info("AUTO_RECOVERY_RACK_RELEASED order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=%s safe_recovery=true", order.id, rack.id, rack.code, order.agv_code or "-", age_minutes, reason)
            released_count += 1
        else:
            logger.info("AUTO_RECOVERY_RACK_NOT_RELEASED order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=already_available safe_recovery=true", order.id, rack.id, rack.code, order.agv_code or "-", age_minutes)
        logger.info("AUTO_RECOVERY_COMPLETED order_id=%s rack_id=%s rack_code=%s robot_code=%s age_minutes=%s reason=%s safe_recovery=true", order.id, rack.id, rack.code, order.agv_code or "-", age_minutes, reason)
    if released_count:
        db.commit()
    return released_count


def _maybe_auto_release_stuck_cancel_return_orders() -> None:
    global _stuck_cancel_return_auto_release_last_ts
    now_ts = time.monotonic()
    with _stuck_cancel_return_auto_release_lock:
        if now_ts - _stuck_cancel_return_auto_release_last_ts < STUCK_CANCEL_RETURN_AUTO_RELEASE_INTERVAL_SECONDS:
            return
        _stuck_cancel_return_auto_release_last_ts = now_ts
    try:
        with SessionLocal() as db:
            _auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow())
    except Exception:
        logger.exception("Auto release stuck cancel_return cleanup failed")


def _annotate_cleanup_safety(db, diagnosis: dict) -> dict:
    now = datetime.utcnow()
    min_age_minutes = _cleanup_min_age_minutes(db)
    for row in diagnosis.get("orphan_reserved_racks", []) or []:
        safe, reason = _rack_safe_to_release_after_cleanup(db, int(row.get("rack_id") or 0), 0, now=now, min_age_minutes=min_age_minutes)
        row["safe"] = bool(safe)
        row["safe_reason"] = "" if safe else reason

    for row in diagnosis.get("inconsistent_orders", []) or []:
        order = db.execute(select(MovementOrder).where(MovementOrder.id == int(row.get("order_id") or 0))).scalar_one_or_none()
        safe, reason = _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=min_age_minutes) if order else (False, "orden no encontrada")
        row["safe"] = bool(safe)
        row["safe_reason"] = reason

    for row in diagnosis.get("active_order_available_racks", []) or []:
        order = db.execute(select(MovementOrder).where(MovementOrder.id == int(row.get("order_id") or 0))).scalar_one_or_none()
        safe, reason = _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=min_age_minutes) if order else (False, "orden no encontrada")
        row["safe"] = bool(safe)
        row["safe_reason"] = reason
    return diagnosis


def _run_cleanup_diagnosis(db) -> dict:
    active_statuses = tuple(RACK_RESERVATION_ACTIVE_STATUSES)
    active_status_list = ", ".join(f":active_status_{idx}" for idx, _ in enumerate(active_statuses))
    params = {f"active_status_{idx}": value for idx, value in enumerate(active_statuses)}
    now = datetime.utcnow()
    force_min_age_minutes = _force_release_min_age_minutes(db)
    orphan_reserved_racks = _diagnosis_rows(
        db,
        f"""
        SELECT
            r.id AS rack_id,
            r.code AS rack_code,
            r.name AS rack_name,
            r.status AS rack_status,
            l.id AS location_id,
            l.x AS location_x,
            l.y AS location_y
        FROM racks r
        LEFT JOIN locations l ON l.rack_id = r.id
        WHERE lower(trim(coalesce(r.status, ''))) IN ('reserved', 'reservado')
          AND NOT EXISTS (
            SELECT 1
            FROM movement_orders mo
            WHERE mo.rack_id = r.id
              AND mo.status IN ({active_status_list})
          )
        ORDER BY r.id
        """,
        params,
    )
    inconsistent_orders = _diagnosis_rows(
        db,
        """
        SELECT
            mo.id AS order_id,
            mo.order_code,
            mo.rack_id,
            r.code AS rack_code,
            mo.status,
            mo.rcs_status,
            mo.remote_task_code,
            mo.created_at,
            mo.updated_at
        FROM movement_orders mo
        LEFT JOIN racks r ON r.id = mo.rack_id
        WHERE lower(trim(coalesce(mo.rcs_status, ''))) IN ('completed', 'cancelled')
          AND mo.status IN ('cancel_requested_undo', 'cancel_requested_total', 'dispatched')
        ORDER BY mo.id
        """,
    )
    active_order_available_racks = _diagnosis_rows(
        db,
        f"""
        SELECT
            r.id AS rack_id,
            r.code AS rack_code,
            r.name AS rack_name,
            r.status AS rack_status,
            mo.id AS order_id,
            mo.order_code,
            mo.status AS order_status,
            mo.rcs_status,
            mo.remote_task_code
        FROM racks r
        JOIN movement_orders mo ON mo.rack_id = r.id
        WHERE lower(trim(coalesce(r.status, ''))) IN ('available', 'disponible', 'free', 'libre')
          AND mo.status IN ({active_status_list})
        ORDER BY r.id, mo.id
        """,
        params,
    )
    inconsistent_locations = _diagnosis_rows(
        db,
        """
        SELECT
            l.id AS location_id,
            l.x,
            l.y,
            l.status,
            l.rack_id,
            r.code AS rack_code,
            CASE
                WHEN l.rack_id IS NOT NULL AND l.rack_id <= 0 THEN 'rack_id invalido'
                WHEN l.rack_id IS NOT NULL AND r.id IS NULL THEN 'rack inexistente'
                WHEN l.rack_id IS NULL AND coalesce(l.status, 0) <> 0 THEN 'status ocupado sin rack'
                WHEN l.rack_id IS NOT NULL AND coalesce(l.status, 0) <> 1 THEN 'status libre con rack'
                ELSE 'inconsistente'
            END AS reason
        FROM locations l
        LEFT JOIN racks r ON r.id = l.rack_id
        WHERE (l.rack_id IS NOT NULL AND (l.rack_id <= 0 OR r.id IS NULL))
           OR (l.rack_id IS NULL AND coalesce(l.status, 0) <> 0)
           OR (l.rack_id IS NOT NULL AND coalesce(l.status, 0) <> 1)
        ORDER BY l.id
        """,
    )
    old_active_racks = []
    active_order_rows = db.execute(
        select(MovementOrder, Rack)
        .join(Rack, Rack.id == MovementOrder.rack_id)
        .where(MovementOrder.status.in_(active_statuses))
        .order_by(Rack.id.asc(), MovementOrder.created_at.asc(), MovementOrder.id.asc())
    ).all()
    for order, rack in active_order_rows:
        created_at = order.created_at or order.updated_at or now
        age_minutes = max(0, int((now - created_at).total_seconds() // 60))
        terminal_rcs_status = _normalized_cleanup_rcs_terminal_status(order.rcs_status)
        safe, safe_reason = _validate_force_release_order(order, now=now, min_age_minutes=force_min_age_minutes)
        if safe and not terminal_rcs_status and (order.status or "") in CANCEL_REQUEST_STATUSES:
            if (order.status or "") != "cancel_requested_undo" and not rack_status_is_reserved(rack.status or ""):
                safe = False
                safe_reason = "rack no esta reservado"
            active_other = _active_orders_for_rack(db, rack.id, exclude_order_id=order.id)
            if safe and active_other:
                safe = False
                safe_reason = "otra orden activa asociada al mismo rack"
        if safe and not terminal_rcs_status and (order.status or "") not in CANCEL_REQUEST_STATUSES and rack.last_moved_at and rack.last_moved_at > (now - timedelta(minutes=force_min_age_minutes)):
            safe = False
            safe_reason = "rack con movimiento reciente"
        old_active_racks.append({
            "rack_id": rack.id,
            "rack_code": rack.code,
            "order_id": order.id,
            "order_status": order.status,
            "rcs_status": order.rcs_status,
            "age_minutes": age_minutes,
            "recovery_case": "cancel_return_stuck" if (order.status or "") == "cancel_requested_undo" and str(order.rcs_status or "").strip().lower() in CANCEL_RETURN_RCS_TERMINAL_STATUSES else "old_active_order",
            "safe": bool(safe),
            "safe_reason": safe_reason,
            "motivo": "RCS terminal confirmado" if safe_reason == "SAFE_RCS_TERMINAL" else safe_reason,
        })
    cancel_undo_auto_enabled = _cancel_undo_auto_recovery_enabled(db)
    cancel_undo_min_age = _cancel_undo_auto_recovery_min_age_minutes(db)
    stuck_cancel_recoverable = []
    cancel_undo_rows = db.execute(
        select(MovementOrder, Rack)
        .join(Rack, Rack.id == MovementOrder.rack_id)
        .where(MovementOrder.status == "cancel_requested_undo")
        .order_by(MovementOrder.updated_at.asc(), MovementOrder.id.asc())
    ).all()
    for order, rack in cancel_undo_rows:
        age_anchor = order.closed_at or order.updated_at or order.created_at or now
        age_minutes = max(0, int((now - age_anchor).total_seconds() // 60))
        safe_recovery, detected_reason = _cancel_undo_recovery_candidate(db, order, now=now, min_age_minutes=cancel_undo_min_age)
        if not cancel_undo_auto_enabled and safe_recovery:
            safe_recovery = False
            detected_reason = "AUTO_RECOVERY_DISABLED"
        stuck_cancel_recoverable.append({
            "order_id": order.id,
            "rack_id": rack.id,
            "rack_code": rack.code,
            "robot_code": order.agv_code or "",
            "age_minutes": age_minutes,
            "motivo_detectado": detected_reason,
            "rcs_status": order.rcs_status,
            "rack_status": rack.status,
            "safe": bool(safe_recovery),
            "safe_recovery": bool(safe_recovery),
            "safe_reason": detected_reason if safe_recovery else detected_reason,
        })
    integrity_check = [str(value) for value in db.execute(text("PRAGMA integrity_check")).scalars().all()]
    diagnosis = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "active_order_statuses": list(active_statuses),
        "cleanup_min_age_minutes": _cleanup_min_age_minutes(db),
        "force_release_min_age_minutes": force_min_age_minutes,
        "cancel_undo_auto_recovery_enabled": cancel_undo_auto_enabled,
        "cancel_undo_auto_recovery_min_age_minutes": cancel_undo_min_age,
        "debug_tools_enabled": _dev_tools_enabled(db),
        "orphan_reserved_racks": orphan_reserved_racks,
        "inconsistent_orders": inconsistent_orders,
        "active_order_available_racks": active_order_available_racks,
        "old_active_racks": old_active_racks,
        "stuck_cancel_recoverable": stuck_cancel_recoverable,
        "inconsistent_locations": inconsistent_locations,
        "integrity_check": integrity_check,
    }
    return _annotate_cleanup_safety(db, diagnosis)


@app.get("/api/admin/cleanup-diagnosis")
def cleanup_diagnosis(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    with SessionLocal() as db:
        diagnosis = _run_cleanup_diagnosis(db)
    logger.info(
        "Cleanup diagnosis completed action=cleanup_diagnosis orphan_reserved=%s inconsistent_orders=%s active_available=%s old_active=%s stuck_cancel=%s inconsistent_locations=%s duration_ms=%s",
        len(diagnosis.get("orphan_reserved_racks") or []),
        len(diagnosis.get("inconsistent_orders") or []),
        len(diagnosis.get("active_order_available_racks") or []),
        len(diagnosis.get("old_active_racks") or []),
        len(diagnosis.get("stuck_cancel_recoverable") or []),
        len(diagnosis.get("inconsistent_locations") or []),
        int((time.monotonic() - started) * 1000),
    )
    return diagnosis


@app.get("/api/admin/cleanup-health", response_model=CleanupHealthOut)
def cleanup_health(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        diagnosis = _run_cleanup_diagnosis(db)
        last_cleanup_row = db.execute(
            select(DebugConsoleEvent)
            .where(DebugConsoleEvent.module == "cleanup")
            .order_by(DebugConsoleEvent.created_at.desc(), DebugConsoleEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        last_cleanup = _debug_console_event_out(last_cleanup_row).model_dump(mode="json") if last_cleanup_row else None
        total_reserved = db.execute(
            select(func.count()).select_from(Rack).where(func.lower(func.trim(func.coalesce(Rack.status, ""))).in_(("reserved", "reservado")))
        ).scalar_one() or 0
        return CleanupHealthOut(
            total_reserved_racks=int(total_reserved),
            total_orphans=len(diagnosis.get("orphan_reserved_racks") or []),
            inconsistent_orders=len(diagnosis.get("inconsistent_orders") or []),
            last_cleanup=last_cleanup,
            integrity_check=diagnosis.get("integrity_check") or [],
        )


@app.post("/api/admin/cleanup-close-inconsistent-orders", response_model=CleanupCloseOrdersOut)
def cleanup_close_inconsistent_orders(body: CleanupSelectionIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    now = datetime.utcnow()
    closed_orders = []
    skipped_orders = []
    released_racks = []
    kept_locations = 0
    admin_user = f"admin:{(x_admin_token or '')[-6:]}" if x_admin_token else "admin"

    with SessionLocal() as db:
        min_age_minutes = _cleanup_min_age_minutes(db)
        order_ids = sorted({int(value) for value in (body.order_ids or []) if int(value or 0) > 0})
        rack_ids = sorted({int(value) for value in (body.rack_ids or []) if int(value or 0) > 0})
        candidates = []
        if order_ids:
            candidates = db.execute(
                select(MovementOrder).where(
                    MovementOrder.id.in_(order_ids),
                    MovementOrder.status.in_(("cancel_requested_undo", "cancel_requested_total", "dispatched")),
                    func.lower(func.trim(func.coalesce(MovementOrder.rcs_status, ""))).in_(CANCEL_RETURN_RCS_TERMINAL_STATUSES),
                ).order_by(MovementOrder.id.asc())
            ).scalars().all()
            found_order_ids = {order.id for order in candidates}
            for missing_id in order_ids:
                if missing_id not in found_order_ids:
                    skipped_orders.append({"order_id": missing_id, "reason": "orden no encontrada o no elegible"})

        for order in candidates:
            eligible, reason = _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=min_age_minutes)
            if not eligible:
                skipped_orders.append({"order_id": order.id, "order_code": order.order_code, "reason": reason})
                continue

            closed_orders.append(_close_cleanup_order(db, order, now=now, reason="admin_cleanup", admin_user=admin_user))

        for rack_id in rack_ids:
            released, skipped, kept_location = _release_cleanup_rack_if_safe(db, rack_id, closed_order_id=0, now=now, min_age_minutes=min_age_minutes, admin_user=admin_user)
            if skipped:
                skipped_orders.append(skipped)
                continue
            if released:
                released_racks.append(released)
            if kept_location:
                kept_locations += 1

        db.commit()
        diagnosis = _run_cleanup_diagnosis(db)

    logger.info(
        "Cleanup action completed action=close_inconsistent_orders selected_orders=%s selected_racks=%s closed=%s released=%s skipped=%s kept_locations=%s duration_ms=%s",
        len(order_ids),
        len(rack_ids),
        len(closed_orders),
        len(released_racks),
        len(skipped_orders),
        kept_locations,
        int((time.monotonic() - started) * 1000),
    )
    return CleanupCloseOrdersOut(ok=True, closed_orders=closed_orders, skipped_orders=skipped_orders, released_racks=released_racks, kept_locations=kept_locations, diagnosis=diagnosis)


@app.post("/api/admin/cleanup-resolve-inconsistent-racks", response_model=CleanupResolveInconsistentRacksOut)
def cleanup_resolve_inconsistent_racks(body: CleanupSelectionIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    now = datetime.utcnow()
    rack_ids = sorted({int(value) for value in (body.rack_ids or []) if int(value or 0) > 0})
    closed_orders = []
    released_racks = []
    skipped = []
    admin_user = f"admin:{(x_admin_token or '')[-6:]}" if x_admin_token else "admin"

    with SessionLocal() as db:
        min_age_minutes = _cleanup_min_age_minutes(db)
        if rack_ids:
            for rack_id in rack_ids:
                rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
                if not rack:
                    skipped.append({"rack_id": rack_id, "reason": "rack no encontrado"})
                    continue
                order = db.execute(
                    select(MovementOrder).where(
                        MovementOrder.rack_id == rack_id,
                        MovementOrder.status.in_(("cancel_requested_undo", "cancel_requested_total", "dispatched")),
                        func.lower(func.trim(func.coalesce(MovementOrder.rcs_status, ""))).in_(CANCEL_RETURN_RCS_TERMINAL_STATUSES),
                    ).order_by(MovementOrder.id.asc())
                ).scalars().first()
                if not order:
                    skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "orden asociada no elegible"})
                    continue
                eligible, reason = _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=min_age_minutes)
                if not eligible:
                    skipped.append({"rack_id": rack_id, "rack_code": rack.code, "order_id": order.id, "reason": reason})
                    continue
                closed_orders.append(_close_cleanup_order(db, order, now=now, reason="admin_cleanup_inconsistent_rack", admin_user=admin_user))
                released, release_skip, _kept_location = _release_cleanup_rack_if_safe(db, rack_id, closed_order_id=order.id, now=now, min_age_minutes=min_age_minutes, admin_user=admin_user)
                if release_skip:
                    skipped.append(release_skip)
                    continue
                if released:
                    released_racks.append(released)

        db.commit()
        diagnosis = _run_cleanup_diagnosis(db)

    logger.info(
        "Cleanup action completed action=resolve_inconsistent_racks selected_racks=%s closed=%s released=%s skipped=%s duration_ms=%s",
        len(rack_ids),
        len(closed_orders),
        len(released_racks),
        len(skipped),
        int((time.monotonic() - started) * 1000),
    )
    return CleanupResolveInconsistentRacksOut(ok=True, closed_orders=closed_orders, released_racks=released_racks, skipped=skipped, diagnosis=diagnosis)


@app.post("/api/admin/force-release-old-active-racks", response_model=ForceReleaseOldActiveRacksOut)
def force_release_old_active_racks(body: CleanupSelectionIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    now = datetime.utcnow()
    rack_ids = sorted({int(value) for value in (body.rack_ids or []) if int(value or 0) > 0})
    closed_orders = []
    released_racks = []
    skipped = []
    admin_user = f"admin:{(x_admin_token or '')[-6:]}" if x_admin_token else "admin"

    with SessionLocal() as db:
        min_age_minutes = _force_release_min_age_minutes(db)
        for rack_id in rack_ids:
            rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
            if not rack:
                skipped.append({"rack_id": rack_id, "reason": "rack no encontrado"})
                continue

            active_orders = _active_orders_for_rack(db, rack_id)
            if not active_orders:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "rack sin orden activa asociada"})
                continue
            if len(active_orders) > 1:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "No se puede liberar: existe otra orden activa asociada al mismo rack."})
                continue

            confirmed_cancel_requests = [
                order for order in active_orders
                if (
                    ((order.status or "") == "cancel_requested_undo" and str(order.rcs_status or "").strip().lower() in CANCEL_RETURN_RCS_TERMINAL_STATUSES)
                    or ((order.status or "") == "cancel_requested_total" and str(order.rcs_status or "").strip().lower() == "cancelled")
                )
            ]
            if confirmed_cancel_requests and len(confirmed_cancel_requests) != len(active_orders):
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "No se puede liberar: existe otra orden activa asociada al mismo rack."})
                continue

            only_cancel_requests = all((order.status or "") in CANCEL_REQUEST_STATUSES for order in active_orders)
            all_rcs_terminal = all(_normalized_cleanup_rcs_terminal_status(order.rcs_status) for order in active_orders)
            rack_recent_move = rack.last_moved_at and rack.last_moved_at > (now - timedelta(minutes=min_age_minutes))
            if rack_recent_move and not all_rcs_terminal:
                if not only_cancel_requests:
                    skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "No se puede liberar: rack con movimiento reciente."})
                    continue
                if any((order.closed_at or order.updated_at or order.created_at or now) > (now - timedelta(minutes=min_age_minutes)) for order in active_orders):
                    skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "No se puede liberar: cancelacion reciente."})
                    continue

            validation_errors = []
            for order in active_orders:
                ok, reason = _validate_force_release_order(order, now=now, min_age_minutes=min_age_minutes)
                if not ok:
                    validation_errors.append(reason)
            if validation_errors:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "; ".join(validation_errors)})
                continue

            for order in active_orders:
                closed_orders.append(_force_close_old_active_order(db, order, now=now, admin_user=admin_user))

            old_status = rack.status or ""
            related_order_id = active_orders[0].id if active_orders else None
            released = _release_rack_if_no_active_orders(
                db,
                rack.id,
                related_order_id=related_order_id,
                reason="admin_force_release_old_active_order",
                source="cleanup_force_release",
                actor=admin_user,
            )
            if released:
                logger.info("[FORCE RELEASE] Rack %s liberado user=%s old_status=%s new_status=%s", rack.id, admin_user, old_status, rack.status)
                _append_debug_console_event(
                    db,
                    direction="received",
                    module="cleanup",
                    payload={
                        "user": admin_user,
                        "action": "force_release_rack",
                        "rack_id": rack.id,
                        "old_status": old_status,
                        "new_status": rack.status,
                        "reason": "admin_force_release_old_active_order",
                        "at": now.isoformat(),
                    },
                    message=f"[FORCE RELEASE] Rack {rack.id} liberado",
                    created_at=now,
                    auto_commit=False,
                )
                released_racks.append({"rack_id": rack.id, "rack_code": rack.code, "old_status": old_status, "new_status": rack.status})
            logger.info("[FORCE RELEASE] Locations intactas rack_id=%s user=%s", rack.id, admin_user)
            _append_debug_console_event(
                db,
                direction="received",
                module="cleanup",
                payload={"user": admin_user, "action": "force_release_keep_locations", "rack_id": rack.id, "at": now.isoformat()},
                message="[FORCE RELEASE] Locations intactas",
                created_at=now,
                auto_commit=False,
            )

        db.commit()
        diagnosis = _run_cleanup_diagnosis(db)

    logger.info(
        "Cleanup action completed action=force_release_old_active_racks selected_racks=%s closed=%s released=%s skipped=%s duration_ms=%s",
        len(rack_ids),
        len(closed_orders),
        len(released_racks),
        len(skipped),
        int((time.monotonic() - started) * 1000),
    )
    return ForceReleaseOldActiveRacksOut(ok=True, closed_orders=closed_orders, released_racks=released_racks, skipped=skipped, diagnosis=diagnosis)


@app.post("/api/admin/test/create-old-active-order", response_model=TestCreateOldActiveOrderOut, include_in_schema=False)
def create_old_active_order_for_force_release_test(body: TestCreateOldActiveOrderIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    now = datetime.utcnow()
    old_time = now - timedelta(minutes=25)
    message = "Orden vieja de prueba creada para validaci\u00f3n de force release"
    admin_user = f"admin:{(x_admin_token or '')[-6:]}" if x_admin_token else "admin"

    with SessionLocal() as db:
        if not _dev_tools_enabled(db):
            raise HTTPException(status_code=404, detail="No encontrado")

        rack = db.execute(select(Rack).where(Rack.id == body.rack_id)).scalar_one_or_none()
        if not rack:
            raise HTTPException(status_code=404, detail="Rack no encontrado")
        if _active_orders_for_rack(db, rack.id):
            raise HTTPException(status_code=400, detail="El rack ya tiene una orden activa asociada")

        rack_location = db.execute(select(Location).where(Location.rack_id == rack.id)).scalar_one_or_none()
        cell = rack_location or db.execute(select(Location).order_by(Location.id.asc())).scalars().first()
        if not cell:
            raise HTTPException(status_code=400, detail="No hay locations disponibles para asociar la orden de prueba")

        area_id = cell.area_id
        if not area_id:
            area = db.execute(select(Area).order_by(Area.id.asc())).scalars().first()
            if not area:
                raise HTTPException(status_code=400, detail="No hay \u00e1reas disponibles para asociar la orden de prueba")
            area_id = area.id

        material_group_id = rack.material_group_id or _ensure_no_material_group_in_db(db).id
        order_code = f"TEST-OLD-{now.strftime('%Y%m%d%H%M%S')}-{rack.id}-{secrets.token_hex(3)}"
        order = MovementOrder(
            order_code=order_code[:64],
            order_type="dev_force_release_test",
            source_area_id=area_id,
            destination_area_id=area_id,
            material_group_id=material_group_id,
            rack_id=rack.id,
            pickup_rack_id=rack.id,
            dropoff_rack_id=None,
            source_cell_id=cell.id,
            destination_cell_id=cell.id,
            priority="test",
            comment=message,
            status="dispatched",
            dispatch_status="dispatched",
            remote_task_code=None,
            req_code=None,
            rcs_status="unknown",
            rcs_message=message,
            dispatched_at=old_time,
            rcs_last_update=old_time,
            status_query_checked_at=None,
            created_by="admin_dev_tool",
            created_at=old_time,
            updated_at=old_time,
        )
        old_status = rack.status or ""
        db.add(order)
        db.add(rack)
        db.flush()
        apply_rack_reservation_status(rack, True, updated_at=now, order_id=order.id, dispatch_status=order.status, source="test_debug_tool", reason="create_old_active_order_test")
        if rack.last_moved_at and rack.last_moved_at > old_time:
            rack.last_moved_at = old_time
        db.add(rack)
        order.release_source = "test_debug_tool"
        _audit_rack_reservation_change(db, rack=rack, previous_status=old_status, new_status=rack.status, source="test_debug_tool", related_order_id=order.id, reason="create_old_active_order_test", actor=admin_user, at=now, auto_commit=False)
        logger.warning("[DEV TEST] Orden vieja activa creada order_id=%s rack_id=%s user=%s", order.id, rack.id, admin_user)
        _append_debug_console_event(
            db,
            direction="received",
            module="cleanup",
            payload={
                "user": admin_user,
                "action": "dev_create_old_active_order",
                "rack_id": rack.id,
                "order_id": order.id,
                "old_rack_status": old_status,
                "new_rack_status": rack.status,
                "created_at": old_time.isoformat(),
                "at": now.isoformat(),
            },
            message=f"[DEV TEST] {message}",
            created_at=now,
            auto_commit=False,
        )
        db.commit()
        diagnosis = _run_cleanup_diagnosis(db)
        return TestCreateOldActiveOrderOut(ok=True, message=message, order_id=order.id, rack_id=rack.id, diagnosis=diagnosis)


@app.get("/api/admin/racks", response_model=List[RackOut])
def list_racks(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return [_rack_out(db, r) for r in db.execute(select(Rack).order_by(Rack.code.asc())).scalars().all()]


@app.post("/api/admin/racks", response_model=RackOut)
def create_rack(body: RackIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        exists = db.execute(select(Rack).where(Rack.code == body.code.strip())).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="Ya existe un rack con ese código")
        normalized_material_group_id = body.material_group_id if body.material_group_id else _ensure_no_material_group_in_db(db).id
        mat = db.execute(select(MaterialGroup).where(MaterialGroup.id == normalized_material_group_id)).scalar_one_or_none()
        if not mat:
            raise HTTPException(status_code=400, detail="Material no encontrado")
        requested_status = (body.status or "").strip()
        if rack_status_is_reserved(requested_status):
            requested_status = rack_status_from_reservation(True)
        elif rack_status_is_available(requested_status) or not requested_status:
            requested_status = rack_status_from_reservation(False)
        row = Rack(code=body.code.strip(), name=(body.name or "").strip() or None, status=requested_status, material_group_id=normalized_material_group_id, lot=(body.lot or "").strip() or None, manufacturer_code=(body.manufacturer_code or "").strip() or None, quantity=body.quantity, comment=(body.comment or "").strip() or None, fifo_entered_at=body.fifo_entered_at, last_moved_at=body.last_moved_at, rack_custom_fields_json=json.dumps(_normalize_rack_custom_fields(body.custom_fields)), updated_at=datetime.utcnow())
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Rack created rack_id=%s code=%s status=%s", row.id, row.code, row.status)
        return _rack_out(db, row)


@app.put("/api/admin/racks/{rack_id}", response_model=RackOut)
def update_rack(rack_id: int, body: RackIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Rack no encontrado")
        previous_code = row.code
        previous_status = row.status or ""
        dup = db.execute(select(Rack).where(Rack.code == body.code.strip(), Rack.id != rack_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe un rack con ese código")
        normalized_material_group_id = body.material_group_id if body.material_group_id else _ensure_no_material_group_in_db(db).id
        mat = db.execute(select(MaterialGroup).where(MaterialGroup.id == normalized_material_group_id)).scalar_one_or_none()
        if not mat:
            raise HTTPException(status_code=400, detail="Material no encontrado")
        requested_status = (body.status or "").strip()
        if rack_status_is_reserved(requested_status):
            requested_status = rack_status_from_reservation(True)
        elif rack_status_is_available(requested_status) or not requested_status:
            logger.info(
                "ADMIN_RACK_SAVE_REQUEST rack_id=%s rack_code=%s requested_status=%s requested_reserved=%s current_status=%s current_reserved=%s",
                row.id,
                row.code,
                rack_status_from_reservation(False),
                False,
                previous_status,
                rack_status_is_reserved(previous_status),
            )
            if rack_status_is_reserved(previous_status):
                logger.info(
                    "ADMIN_RACK_MANUAL_RELEASE_REQUEST rack_id=%s rack_code=%s requested_status=%s requested_reserved=%s current_status=%s current_reserved=%s",
                    row.id,
                    row.code,
                    rack_status_from_reservation(False),
                    False,
                    previous_status,
                    True,
                )
                _manual_release_rack_or_raise(db, row, actor="admin", reason="admin_rack_save_available")
            requested_status = rack_status_from_reservation(False)
        else:
            logger.info(
                "ADMIN_RACK_SAVE_REQUEST rack_id=%s rack_code=%s requested_status=%s requested_reserved=%s current_status=%s current_reserved=%s",
                row.id,
                row.code,
                requested_status,
                rack_status_is_reserved(requested_status),
                previous_status,
                rack_status_is_reserved(previous_status),
            )
        row.code = body.code.strip()
        row.name = (body.name or "").strip() or None
        if (row.status or "") != requested_status:
            apply_rack_reservation_status(row, rack_status_is_reserved(requested_status), updated_at=datetime.utcnow(), source="admin_racks_manual", reason="admin_rack_save_status")
        else:
            row.status = requested_status
        row.material_group_id = normalized_material_group_id
        row.lot = (body.lot or "").strip() or None
        row.manufacturer_code = (body.manufacturer_code or "").strip() or None
        row.quantity = body.quantity
        row.comment = (body.comment or "").strip() or None
        row.fifo_entered_at = body.fifo_entered_at
        row.last_moved_at = body.last_moved_at
        row.rack_custom_fields_json = json.dumps(_normalize_rack_custom_fields(body.custom_fields))
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Rack updated | rack_id=%s | from_code=%s | to_code=%s | from_status=%s | to_status=%s | material_group_id=%s | manual_available=%s", row.id, previous_code, row.code, previous_status, row.status, row.material_group_id, rack_status_is_available(requested_status))
        return _rack_out(db, row)


@app.patch("/api/admin/racks/{rack_id}/reservation", response_model=RackOut)
def update_rack_reservation(rack_id: int, body: RackReservationIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Rack no encontrado")
        now = datetime.utcnow()
        should_reserve = int(body.reserved or 0) == 1
        old_status = row.status or ""
        logger.info(
            "ADMIN_RACK_SAVE_REQUEST rack_id=%s rack_code=%s requested_status=%s requested_reserved=%s current_status=%s current_reserved=%s",
            row.id,
            row.code,
            rack_status_from_reservation(should_reserve),
            should_reserve,
            old_status,
            rack_status_is_reserved(old_status),
        )
        if not should_reserve and rack_status_is_reserved(old_status):
            logger.info(
                "ADMIN_RACK_MANUAL_RELEASE_REQUEST rack_id=%s rack_code=%s requested_status=%s requested_reserved=%s current_status=%s current_reserved=%s",
                row.id,
                row.code,
                rack_status_from_reservation(False),
                False,
                old_status,
                rack_status_is_reserved(old_status),
            )
            closed_orders = _manual_release_rack_or_raise(db, row, actor="admin", reason="admin_rack_reservation_false", now=now)
        elif should_reserve:
            apply_rack_reservation_status(row, True, updated_at=now, source="admin_racks_manual", reason="admin_rack_reservation_true")
            closed_orders = []
        else:
            closed_orders = []
        db.add(row)
        _audit_rack_reservation_change(db, rack=row, previous_status=old_status, new_status=row.status, source="local_ui", reason="admin_rack_reservation_toggle", actor="admin", at=now, auto_commit=False)
        db.commit()
        db.refresh(row)
        logger.info("Rack reservation changed | rack_id=%s | rack_code=%s | reserved=%s | from=%s | to=%s | closed_orders=%s", row.id, row.code, should_reserve, old_status, row.status, len(closed_orders))
        return _rack_out(db, row)


@app.delete("/api/admin/racks/{rack_id}")
def delete_rack(rack_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Rack no encontrado")
        _ensure_rack_not_in_use(db, rack_id)
        db.delete(row)
        db.commit()
        logger.info("Rack deleted rack_id=%s code=%s", rack_id, row.code)
        return {"ok": True}




def _exists_row(db, stmt) -> bool:
    return db.execute(stmt.limit(1)).first() is not None


def _ensure_area_not_in_use(db, area_id: int):
    if _exists_row(db, select(Location.id).where(Location.area_id == area_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el área porque está asignada a una o más celdas.')
    if _exists_row(db, select(MovementOrder.id).where((MovementOrder.source_area_id == area_id) | (MovementOrder.destination_area_id == area_id))):
        raise HTTPException(status_code=409, detail='No se puede borrar el área porque está referenciada en el historial.')
    if _exists_row(db, select(OperatorWindowButton.id).where((OperatorWindowButton.source_area_id == area_id) | (OperatorWindowButton.destination_area_id == area_id))):
        raise HTTPException(status_code=409, detail='No se puede borrar el área porque está usada en una pantalla configurable.')


def _ensure_material_not_in_use(db, material_id: int):
    if _exists_row(db, select(Rack.id).where(Rack.material_group_id == material_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el material porque está asignado a uno o más racks.')
    if _exists_row(db, select(MovementOrder.id).where(MovementOrder.material_group_id == material_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el material porque está referenciado en el historial.')
    if _exists_row(db, select(OperatorWindowButton.id).where(OperatorWindowButton.material_group_id == material_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el material porque está usado en una pantalla configurable.')


def _ensure_rack_not_in_use(db, rack_id: int):
    if _exists_row(db, select(Location.id).where(Location.rack_id == rack_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el rack porque está asignado a una o más celdas.')
    if _exists_row(db, select(MovementOrder.id).where(MovementOrder.rack_id == rack_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar el rack porque está referenciado en el historial.')


def _ensure_operator_window_not_in_use(db, window_id: int):
    if _exists_row(db, select(MovementOrder.id).where(MovementOrder.created_window_id == window_id)):
        raise HTTPException(status_code=409, detail='No se puede borrar la pantalla porque está referenciada en el historial.')

def _location_label(cell: Optional[Location]) -> Optional[str]:
    if not cell:
        return None
    code = (cell.code or '').strip()
    return code if code else f"({cell.x}, {cell.y})"


POINT_FIELD_DEFS = {
    "lot": {"label": "Lote"},
    "quantity": {"label": "Cantidad"},
    "manufacturer_code": {"label": "Número de fabricante"},
    "comment": {"label": "Comentario"},
}


def _load_json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _normalize_point_custom_fields(items) -> list:
    rows = []
    seen = set()
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip()
        if key not in POINT_FIELD_DEFS or key in seen:
            continue
        label = str(item.get('label') or POINT_FIELD_DEFS[key]['label']).strip() or POINT_FIELD_DEFS[key]['label']
        rows.append({'key': key, 'label': label[:128]})
        seen.add(key)
    return rows


def _default_point_custom_fields() -> list:
    return [
        {'key': 'lot', 'label': POINT_FIELD_DEFS['lot']['label']},
        {'key': 'quantity', 'label': POINT_FIELD_DEFS['quantity']['label']},
        {'key': 'manufacturer_code', 'label': POINT_FIELD_DEFS['manufacturer_code']['label']},
    ]


def _window_button_out(db, row: OperatorWindowButton) -> OperatorWindowButtonOut:
    src_area = db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None
    dst_area = db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None
    src_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none() if row.source_cell_id else None
    dst_cell = db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one_or_none() if row.destination_cell_id else None
    return OperatorWindowButtonOut(
        id=row.id,
        window_id=row.window_id,
        button_index=row.button_index,
        label=row.label,
        color=row.color,
        is_active=row.is_active,
        action_mode=(row.action_mode or 'fifo'),
        source_area_id=row.source_area_id,
        destination_area_id=row.destination_area_id,
        material_group_id=row.material_group_id,
        source_cell_id=row.source_cell_id,
        destination_cell_id=row.destination_cell_id,
        priority=row.priority or 'normal',
        agv_code=row.agv_code,
        task_typ=row.task_typ,
        comment=row.comment,
        cancel_matter_area=row.cancel_matter_area,
        point_visible_material_ids=[int(x) for x in _load_json_list(row.point_visible_material_ids_json) if str(x).isdigit()],
        point_custom_fields=_normalize_point_custom_fields(_load_json_list(row.point_custom_fields_json)),
        updated_at=row.updated_at,
        source_area_name=src_area.name if src_area else None,
        destination_area_name=dst_area.name if dst_area else None,
        material_group_name=material.name if material else None,
        source_cell_label=_location_label(src_cell),
        destination_cell_label=_location_label(dst_cell),
    )


def _sync_operator_window_buttons(db, window_row: OperatorWindow):
    desired = max(1, min(24, int(window_row.button_count or 1)))
    existing = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.window_id == window_row.id).order_by(OperatorWindowButton.button_index.asc())).scalars().all()
    by_index = {row.button_index: row for row in existing}
    now = datetime.utcnow()
    for index in range(1, desired + 1):
        if index not in by_index:
            db.add(OperatorWindowButton(window_id=window_row.id, button_index=index, label=f'Botón {index}', color='#1f4b99', action_mode='fifo', priority='normal', task_typ='A01', is_active=1, updated_at=now))
    for row in existing:
        if row.button_index > desired:
            db.delete(row)
    db.commit()


def _operator_window_out(db, row: OperatorWindow, include_buttons: bool = True) -> OperatorWindowOut:
    buttons = []
    if include_buttons:
        buttons = [_window_button_out(db, btn) for btn in db.execute(select(OperatorWindowButton).where(OperatorWindowButton.window_id == row.id).order_by(OperatorWindowButton.button_index.asc())).scalars().all()]
    return OperatorWindowOut(
        id=row.id,
        name=row.name,
        bg_color=row.bg_color,
        button_count=row.button_count,
        is_active=row.is_active,
        requires_password=bool((row.password_hash or '').strip()),
        updated_at=row.updated_at,
        buttons=buttons,
    )


def _save_operator_window(db, body: OperatorWindowIn) -> OperatorWindow:
    if body.id:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == body.id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
    else:
        row = OperatorWindow(updated_at=datetime.utcnow())
        db.add(row)
    dup = db.execute(select(OperatorWindow).where(OperatorWindow.name == body.name.strip(), OperatorWindow.id != (body.id or 0))).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=400, detail='Ya existe una ventana con ese nombre')
    row.name = body.name.strip()
    row.bg_color = (body.bg_color or '#0f2747').strip()
    row.button_count = max(1, min(24, int(body.button_count or 1)))
    row.is_active = int(body.is_active or 0)
    if body.password is not None:
        pwd = (body.password or '').strip()
        row.password_hash = _sha256(pwd) if pwd else None
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    _sync_operator_window_buttons(db, row)
    db.refresh(row)
    return row


def _save_operator_window_button(db, window_id: int, button_index: int, body: OperatorWindowButtonIn) -> OperatorWindowButton:
    window_row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id)).scalar_one_or_none()
    if not window_row:
        raise HTTPException(status_code=404, detail='Ventana no encontrada')
    if not (1 <= button_index <= int(window_row.button_count or 1)):
        raise HTTPException(status_code=400, detail='Índice de botón fuera del rango de la ventana')
    if body.action_mode not in {'fifo', 'direct_move', 'direct_move_config', 'point_to_area', 'cancel_return'}:
        raise HTTPException(status_code=400, detail='Modo de botón inválido')
    if body.source_area_id:
        if not db.execute(select(Area).where(Area.id == body.source_area_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Área origen no encontrada')
    if body.destination_area_id:
        if not db.execute(select(Area).where(Area.id == body.destination_area_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Área destino no encontrada')
    normalized_material_group_id = body.material_group_id
    if body.action_mode == 'fifo' and not normalized_material_group_id:
        normalized_material_group_id = _ensure_no_material_group_in_db(db).id
    if normalized_material_group_id:
        if not db.execute(select(MaterialGroup).where(MaterialGroup.id == normalized_material_group_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Material no encontrado')
    if body.source_cell_id:
        if not db.execute(select(Location).where(Location.id == body.source_cell_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Celda origen no encontrada')
    if body.destination_cell_id:
        if not db.execute(select(Location).where(Location.id == body.destination_cell_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Celda destino no encontrada')
    normalized_point_material_ids = []
    for material_id in (body.point_visible_material_ids or []):
        try:
            material_id = int(material_id)
        except Exception:
            continue
        if material_id <= 0:
            continue
        if not db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Uno de los materiales visibles no existe')
        if material_id not in normalized_point_material_ids:
            normalized_point_material_ids.append(material_id)
    normalized_point_custom_fields = _normalize_point_custom_fields(body.point_custom_fields)
    row = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.window_id == window_id, OperatorWindowButton.button_index == button_index)).scalar_one_or_none()
    if not row:
        row = OperatorWindowButton(window_id=window_id, button_index=button_index, updated_at=datetime.utcnow())
        db.add(row)
    row.label = body.label.strip()
    row.color = (body.color or '#1f4b99').strip()
    row.is_active = int(body.is_active or 0)
    row.action_mode = body.action_mode
    row.source_area_id = body.source_area_id
    row.destination_area_id = body.destination_area_id
    row.material_group_id = normalized_material_group_id
    row.source_cell_id = body.source_cell_id
    row.destination_cell_id = body.destination_cell_id
    row.priority = body.priority
    row.agv_code = (body.agv_code or '').strip() or None
    row.task_typ = (body.task_typ or '').strip() or None
    row.comment = (body.comment or '').strip() or None
    row.cancel_matter_area = (body.cancel_matter_area or '').strip() or None if body.action_mode == 'cancel_return' else None
    row.point_visible_material_ids_json = json.dumps(normalized_point_material_ids)
    row.point_custom_fields_json = json.dumps(normalized_point_custom_fields)
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _validate_window_password(window_row: OperatorWindow, password: str) -> bool:
    stored = (window_row.password_hash or '').strip()
    if not stored:
        return True
    return _sha256((password or '').strip()) == stored


def _build_payload_from_cells(source_cell: Location, destination_cell: Location, priority: str, agv_code: Optional[str], task_typ: Optional[str]) -> dict:
    req_code = _generate_req_code_ms()
    priority_value = ''
    normalized_priority = (priority or '').strip().lower()
    if normalized_priority == 'alta':
        priority_value = '1'
    elif normalized_priority == 'urgente':
        priority_value = '2'
    return {
        'agvCode': (agv_code or '').strip(),
        'clientCode': '',
        'ctnrCode': '',
        'ctnrTyp': '',
        'data': '',
        'materialLot': '',
        'podCode': '',
        'podDir': '',
        'podTyp': '',
        'positionCodePath': [
            {'positionCode': (source_cell.code or '').strip(), 'type': '00'},
            {'positionCode': (destination_cell.code or '').strip(), 'type': '00'},
        ],
        'priority': priority_value,
        'reqCode': req_code,
        'reqTime': '',
        'taskCode': '',
        'taskTyp': (task_typ or '').strip(),
        'tokenCode': '',
        'wbCode': '',
    }


def _resolve_cancel_button_target_order(db, button_row: OperatorWindowButton) -> MovementOrder:
    primary_statuses = ('pending_dispatch', 'dispatched', 'in_progress', 'cancel_requested_total', 'cancel_requested_undo')
    fallback_statuses = ('completed',)
    window_row = db.execute(select(OperatorWindow).where(OperatorWindow.id == button_row.window_id)).scalar_one_or_none()
    legacy_created_by_like = None
    if window_row and (window_row.name or '').strip():
        legacy_created_by_like = f"ventana:{window_row.name.strip()} /%"

    def _window_stmt(statuses, apply_button_filters: bool = True):
        stmt = select(MovementOrder).where(MovementOrder.status.in_(statuses))
        if legacy_created_by_like:
            stmt = stmt.where(
                (MovementOrder.created_window_id == button_row.window_id)
                | ((MovementOrder.created_window_id.is_(None)) & (MovementOrder.created_by.like(legacy_created_by_like)))
            )
        else:
            stmt = stmt.where(MovementOrder.created_window_id == button_row.window_id)
        if apply_button_filters:
            if button_row.source_cell_id:
                stmt = stmt.where(MovementOrder.source_cell_id == button_row.source_cell_id)
            if button_row.destination_cell_id:
                stmt = stmt.where(MovementOrder.destination_cell_id == button_row.destination_cell_id)
            if button_row.agv_code:
                stmt = stmt.where(MovementOrder.agv_code == button_row.agv_code)
        return stmt.order_by(MovementOrder.updated_at.desc(), MovementOrder.created_at.desc(), MovementOrder.id.desc())

    # 1) Primero intentamos con la coincidencia más estricta dentro de la misma ventana.
    row = db.execute(_window_stmt(primary_statuses, apply_button_filters=True)).scalars().first()
    if row:
        return row

    # 2) Si el botón de cancelación no tiene filtros configurados o la orden no coincide
    # exactamente con AGV/celdas, aún así tomamos la última orden activa de esa misma ventana.
    row = db.execute(_window_stmt(primary_statuses, apply_button_filters=False)).scalars().first()
    if row:
        return row

    # 3) Para cancelar/deshacer también aceptamos la última orden completada de la misma ventana.
    row = db.execute(_window_stmt(fallback_statuses, apply_button_filters=True)).scalars().first()
    if row:
        return row

    row = db.execute(_window_stmt(fallback_statuses, apply_button_filters=False)).scalars().first()
    if row:
        return row

    raise HTTPException(status_code=404, detail='No se encontró una tarea coincidente de esta misma ventana para cancelar o deshacer')


def _ensure_rack_available(db, order_row: MovementOrder, *, source: str = "api_internal", actor: str = "") -> None:
    _release_rack_if_no_active_orders(db, order_row.rack_id, related_order_id=order_row.id, reason="order_cancelled", source=source, actor=actor)


def _mark_order_cancelled_without_undo(db, order_row: MovementOrder, *, source: str = "api_internal", actor: str = "", reason: str = "cancel_without_undo") -> MovementOrder:
    now = datetime.utcnow()
    previous_status = order_row.status or ""
    _ensure_rack_available(db, order_row, source=source, actor=actor)
    order_row.status = 'cancelled'
    order_row.updated_at = now
    _audit_order_close(db, order_row, previous_status=previous_status, new_status=order_row.status, source=source, reason=reason, actor=actor, closed_at=now, auto_commit=False)
    db.add(order_row)
    db.commit()
    db.refresh(order_row)
    logger.info("Movement order cancelled without undo order_id=%s order_code=%s", order_row.id, order_row.order_code)
    return order_row


def _execute_cancel_for_order(db, row: MovementOrder, force_cancel: str, matter_area: str = '', undo_on_accept: Optional[bool] = None, source: str = "local_ui", actor: str = "local_ui") -> MovementOrder:
    logger.info("Cancel requested order_id=%s order_code=%s force_cancel=%s undo_on_accept=%s", row.id, row.order_code, force_cancel, undo_on_accept)
    previous_status = row.status or ""
    remote_task_code = (row.remote_task_code or '').strip()
    force_cancel_value = str(force_cancel or '0')
    should_undo_locally = bool(undo_on_accept) if undo_on_accept is not None else (force_cancel_value == '1')
    should_call_remote_cancel = bool(remote_task_code) and row.status in {'pending_dispatch', 'dispatched', 'in_progress'}

    cancel_payload = {
        'reqCode': generate_req_code_ms(),
        'reqTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'clientCode': '',
        'tokenCode': '',
        'forceCancel': force_cancel_value,
        'matterArea': matter_area or '',
        'agvCode': '',
        'taskCode': remote_task_code,
    }

    if should_call_remote_cancel:
        debug_base_url = ""
        debug_endpoint = "/rcms/services/rest/hikRpcService/cancelTask"
        try:
            client = _get_rcs_client_from_settings(db)
            debug_base_url, debug_endpoint = _resolve_rcs_target(db, mode="cancel")
            token_code = getattr(client, '_token_code', '')
            cancel_payload['tokenCode'] = token_code or ''
            logger.info("[CANCEL TASK] order_id=%s order_code=%s source=%s admin=%s remote=1 force_cancel=%s reason=cancelTask", row.id, row.order_code, source, actor, force_cancel_value)
            _append_debug_console_event(db, direction="sent", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload={**cancel_payload, "source": source, "admin": actor}, message=f"Cancelación de {row.order_code}")
            cancel_response = client.cancel_task_with_payload(cancel_payload)
            _append_debug_console_event(db, direction="received", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload=cancel_response.raw, message=cancel_response.message or f"Respuesta cancelTask para {row.order_code}")
            now = datetime.utcnow()
            row.req_code = cancel_response.reqCode or cancel_payload['reqCode']
            row.rcs_last_update = now
            row.rcs_message = cancel_response.message or row.rcs_message
            _append_status_query_log(db, row, kind='cancel_task', request_payload=cancel_payload, response_payload=cancel_response.raw, message=cancel_response.message or '', arrived_at=now)
        except Exception as exc:
            logger.exception("Cancel task failed | task_id=%s | order_code=%s | remote_task_code=%s | source=%s | actor=%s", row.id, row.order_code, remote_task_code or "-", source, actor)
            _append_debug_console_event(db, direction="received", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
            now = datetime.utcnow()
            error_payload = {'error': str(exc)}
            row.rcs_last_update = now
            row.rcs_message = str(exc)
            _append_status_query_log(db, row, kind='cancel_task', request_payload=cancel_payload, response_payload=error_payload, message=str(exc), arrived_at=now)
            db.commit()
            db.refresh(row)
            raise HTTPException(status_code=502, detail=f'Error cancelando tarea en RCS: {exc}')
    elif not should_undo_locally:
        # Cancelación total requiere taskCode remoto activo.
        raise HTTPException(status_code=400, detail='La orden seleccionada ya no está activa en el RCS y no puede cancelarse de forma total')

    if should_call_remote_cancel:
        if not cancel_response.ok:
            row.rcs_status = 'cancel_error'
            row.rcs_message = cancel_response.message or 'El RCS rechazó la cancelación'
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            raise HTTPException(status_code=400, detail=f'El RCS rechazó la cancelación: {row.rcs_message}')
        row.status = 'cancel_requested_undo' if should_undo_locally else 'cancel_requested_total'
        row.rcs_status = 'cancel_requested'
        _audit_order_close(db, row, previous_status=previous_status, new_status=row.status, source=source, reason="cancelTask enviado al RCS", actor=actor, closed_at=datetime.utcnow(), auto_commit=False)
        rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none()
        if rack is not None:
            apply_rack_reservation_status(rack, True, updated_at=datetime.utcnow(), order_id=row.id, dispatch_status=row.status, source=source, reason="cancel_requested_waiting_remote_terminal")
            db.add(rack)
        note = 'Cancelación enviada al RCS. El rack permanece reservado mientras se espera la confirmación final de estatus.'
    elif should_undo_locally:
        logger.info("[UNDO TASK] order_id=%s order_code=%s source=%s admin=%s reason=local_undo", row.id, row.order_code, source, actor)
        row = undo_movement_order(db, row.id)
        _audit_order_close(db, row, previous_status=previous_status, new_status=row.status, source=source, reason="cancelación local con deshacer", actor=actor, closed_at=datetime.utcnow(), auto_commit=False)
        note = 'Cancelación local con deshacer: el rack se regresó a la celda de origen en el sistema. No se llamó cancelTask porque la orden ya no estaba activa en el RCS.'
    else:
        row = _mark_order_cancelled_without_undo(db, row, source=source, actor=actor, reason="cancelación total local")
        note = 'RCS cancelTask forceCancel=0 (cancelación total).'

    row.comment = ((row.comment or '').strip() + ' | ' + note).strip(' |')[:512]
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    if should_undo_locally:
        _start_rack_position_poll_after_cancel(row.id, row.rack_id, source=source, actor=actor)
    logger.info("Cancel processed order_id=%s order_code=%s status=%s", row.id, row.order_code, row.status)
    return row


def _preview_window_button(db, window_row: OperatorWindow, button_row: OperatorWindowButton, action_body: Optional[OperatorWindowActionIn] = None) -> OperatorWindowPreviewOut:
    if button_row.action_mode == 'fifo':
        if not button_row.source_area_id or not button_row.destination_area_id:
            raise HTTPException(status_code=400, detail='El botón FIFO no tiene configurada su acción completa')
        selection = resolve_fifo_request(db, button_row.source_area_id, button_row.destination_area_id, button_row.material_group_id)
        source_cell = selection.source_cell
        destination_cell = selection.destination_cell
        rack = selection.rack
        payload = _build_payload_from_cells(source_cell, destination_cell, button_row.priority, button_row.agv_code, button_row.task_typ)
        summary = {
            'tipo': 'Solicitud FIFO',
            'rack': rack.code,
            'material': selection.material_group.name if selection.material_group else '',
            'origen': _location_label(source_cell),
            'destino': _location_label(destination_cell),
            'agv': button_row.agv_code or '',
            'prioridad': button_row.priority or 'normal',
            'taskTyp': button_row.task_typ or '',
            'comentario': button_row.comment or '',
        }
        return OperatorWindowPreviewOut(ok=True, action_mode='fifo', message='Se generará una orden FIFO.', summary=summary, payload=payload)
    if button_row.action_mode in {'direct_move', 'direct_move_config'}:
        source_cell_id = button_row.source_cell_id
        destination_cell_id = button_row.destination_cell_id
        if button_row.action_mode == 'direct_move_config':
            source_cell_id = action_body.source_cell_id if action_body else None
            destination_cell_id = action_body.destination_cell_id if action_body else None
        if not source_cell_id or not destination_cell_id:
            raise HTTPException(status_code=400, detail='Debes seleccionar celda origen y celda destino para el movimiento directo')
        source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
        destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none()
        if not source_cell or not destination_cell:
            raise HTTPException(status_code=400, detail='Las celdas seleccionadas ya no existen')
        rack = db.execute(select(Rack).where(Rack.id == source_cell.rack_id)).scalar_one_or_none() if source_cell.rack_id else None
        if not rack:
            raise HTTPException(status_code=400, detail='La celda origen no tiene rack actualmente')
        material = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack.material_group_id else None
        payload = _build_payload_from_cells(source_cell, destination_cell, button_row.priority, (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code), (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ))
        summary = {
            'tipo': 'Movimiento directo configurable' if button_row.action_mode == 'direct_move_config' else 'Movimiento directo',
            'rack': rack.code,
            'material': material.name if material else '',
            'origen': _location_label(source_cell),
            'destino': _location_label(destination_cell),
            'agv': (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code) or '',
            'prioridad': button_row.priority or 'normal',
            'taskTyp': (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ) or '',
            'comentario': button_row.comment or '',
        }
        return OperatorWindowPreviewOut(ok=True, action_mode=button_row.action_mode, message='Se generará una orden de movimiento directo.', summary=summary, payload=payload)
    if button_row.action_mode == 'cancel_return':
        target_order = _resolve_cancel_button_target_order(db, button_row)
        source_cell = db.execute(select(Location).where(Location.id == target_order.source_cell_id)).scalar_one_or_none() if target_order.source_cell_id else None
        destination_cell = db.execute(select(Location).where(Location.id == target_order.destination_cell_id)).scalar_one_or_none() if target_order.destination_cell_id else None
        rack = db.execute(select(Rack).where(Rack.id == target_order.rack_id)).scalar_one_or_none() if target_order.rack_id else None
        material = db.execute(select(MaterialGroup).where(MaterialGroup.id == target_order.material_group_id)).scalar_one_or_none() if target_order.material_group_id else None
        payload = {
            'reqCode': generate_req_code_ms(),
            'reqTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'clientCode': '',
            'tokenCode': '',
            'forceCancel': '1',
            'matterArea': (button_row.cancel_matter_area or '').strip(),
            'agvCode': '',
            'taskCode': (target_order.remote_task_code or '').strip(),
        }
        summary = {
            'tipo': 'Cancelar / devolver',
            'rack': rack.code if rack else '',
            'material': material.name if material else '',
            'origen': _location_label(source_cell),
            'destino': _location_label(destination_cell),
            'agv': target_order.agv_code or '',
            'prioridad': target_order.priority or '',
            'taskTyp': target_order.task_typ or '',
            'comentario': button_row.comment or '',
            'matterArea': (button_row.cancel_matter_area or '').strip(),
            'taskCode': (target_order.remote_task_code or '').strip(),
            'orderCode': target_order.order_code or '',
            'orderStatus': target_order.status or '',
        }
        return OperatorWindowPreviewOut(ok=True, action_mode='cancel_return', message='Se enviará cancelTask con forceCancel=1 usando la última tarea de esta ventana.', summary=summary, payload=payload)
    if button_row.action_mode == 'point_to_area':
        if not button_row.destination_area_id:
            raise HTTPException(status_code=400, detail='El botón punto a área no tiene configurada un área destino')
        source_cell_id = action_body.source_cell_id if action_body else None
        if not source_cell_id:
            raise HTTPException(status_code=400, detail='Debes seleccionar la celda origen para mover a área')
        source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
        if not source_cell:
            raise HTTPException(status_code=400, detail='La celda origen seleccionada ya no existe')
        rack = db.execute(select(Rack).where(Rack.id == source_cell.rack_id)).scalar_one_or_none() if source_cell.rack_id else None
        if not rack:
            raise HTTPException(status_code=400, detail='La celda origen no tiene rack actualmente')
        visible_material_ids = [int(x) for x in _load_json_list(button_row.point_visible_material_ids_json) if str(x).isdigit()]
        destination_area_id = action_body.destination_area_id if action_body and action_body.destination_area_id else button_row.destination_area_id
        destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none()
        if not destination_area:
            raise HTTPException(status_code=400, detail='El área destino configurada ya no existe')
        destination_cell = _find_destination_cell(db, destination_area)
        visible_material_ids = [int(x) for x in _load_json_list(button_row.point_visible_material_ids_json) if str(x).isdigit()]
        material_group_id = action_body.material_group_id if action_body and action_body.material_group_id else rack.material_group_id
        if visible_material_ids and material_group_id and material_group_id not in visible_material_ids:
            raise HTTPException(status_code=400, detail='El material seleccionado no está habilitado para este botón')
        material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_group_id)).scalar_one_or_none() if material_group_id else None
        payload = _build_payload_from_cells(source_cell, destination_cell, button_row.priority, (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code), (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ))
        payload['materialLot'] = (action_body.lot if action_body and action_body.lot is not None else rack.lot) or ''
        summary = {
            'tipo': 'Mover de punto a área',
            'rack': rack.code,
            'material': material.name if material else '',
            'origen': _location_label(source_cell),
            'destino': _location_label(destination_cell),
            'areaDestino': destination_area.name or destination_area.code or '',
            'agv': (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code) or '',
            'prioridad': button_row.priority or 'normal',
            'taskTyp': (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ) or '',
            'comentario': (action_body.comment if action_body and action_body.comment is not None else rack.comment if rack.comment is not None else button_row.comment) or '',
            'lote': (action_body.lot if action_body and action_body.lot is not None else rack.lot) or '',
            'cantidad': int(action_body.quantity if action_body and action_body.quantity is not None else (rack.quantity or 0)),
            'codigoFabricante': (action_body.manufacturer_code if action_body and action_body.manufacturer_code is not None else rack.manufacturer_code) or '',
            'camposRack': _merge_rack_custom_field_values(_rack_custom_fields_for_row(rack), action_body.custom_field_values if action_body else []),
        }
        return OperatorWindowPreviewOut(ok=True, action_mode=button_row.action_mode, message='Se generará una orden para mover el rack seleccionado al área configurada.', summary=summary, payload=payload)
    raise HTTPException(status_code=400, detail='Modo de botón inválido')


def _execute_window_button(db, window_row: OperatorWindow, button_row: OperatorWindowButton, action_body: Optional[OperatorWindowActionIn] = None) -> MovementOrder:
    if int(window_row.is_active or 0) != 1:
        raise HTTPException(status_code=400, detail='La ventana no está activa')
    if int(button_row.is_active or 0) != 1:
        raise HTTPException(status_code=400, detail='El botón no está activo')
    if button_row.action_mode == 'fifo':
        if not button_row.source_area_id or not button_row.destination_area_id:
            raise HTTPException(status_code=400, detail='El botón FIFO no tiene configurada su acción completa')
        order, _selection = execute_fifo_request(
            db,
            button_row.source_area_id,
            button_row.destination_area_id,
            button_row.material_group_id,
            button_row.priority,
            button_row.comment,
            f'ventana:{window_row.name} / botón:{button_row.label}',
            button_row.agv_code,
            button_row.task_typ,
            window_row.id,
        )
        return order
    if button_row.action_mode in {'direct_move', 'direct_move_config'}:
        source_cell_id = button_row.source_cell_id
        destination_cell_id = button_row.destination_cell_id
        agv_code = button_row.agv_code
        task_typ = button_row.task_typ
        if button_row.action_mode == 'direct_move_config':
            source_cell_id = action_body.source_cell_id if action_body else None
            destination_cell_id = action_body.destination_cell_id if action_body else None
            agv_code = (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code)
            task_typ = (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ)
        if not source_cell_id or not destination_cell_id:
            raise HTTPException(status_code=400, detail='Debes seleccionar celda origen y celda destino para el movimiento directo')
        order = execute_direct_move_request(
            db,
            source_cell_id,
            destination_cell_id,
            button_row.priority,
            button_row.comment,
            f'ventana:{window_row.name} / botón:{button_row.label}',
            agv_code,
            task_typ,
            window_row.id,
        )
        return order
    if button_row.action_mode == 'cancel_return':
        target_order = _resolve_cancel_button_target_order(db, button_row)
        return _execute_cancel_for_order(db, target_order, '1', (button_row.cancel_matter_area or '').strip(), undo_on_accept=True, source="operator_window", actor=f"operator_window:{window_row.name}")
    if button_row.action_mode == 'point_to_area':
        if not button_row.destination_area_id:
            raise HTTPException(status_code=400, detail='El botón punto a área no tiene configurada un área destino')
        source_cell_id = action_body.source_cell_id if action_body else None
        if not source_cell_id:
            raise HTTPException(status_code=400, detail='Debes seleccionar la celda origen para mover a área')
        source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
        if not source_cell:
            raise HTTPException(status_code=400, detail='La celda origen seleccionada ya no existe')
        rack = db.execute(select(Rack).where(Rack.id == source_cell.rack_id)).scalar_one_or_none() if source_cell.rack_id else None
        if not rack:
            raise HTTPException(status_code=400, detail='La celda origen no tiene rack actualmente')
        destination_area_id = action_body.destination_area_id if action_body and action_body.destination_area_id else button_row.destination_area_id
        destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none()
        if not destination_area:
            raise HTTPException(status_code=400, detail='El área destino configurada ya no existe')
        destination_cell = _find_destination_cell(db, destination_area)
        visible_material_ids = [int(x) for x in _load_json_list(button_row.point_visible_material_ids_json) if str(x).isdigit()]
        if action_body:
            if visible_material_ids and action_body.material_group_id and action_body.material_group_id not in visible_material_ids:
                raise HTTPException(status_code=400, detail='El material seleccionado no está habilitado para este botón')
            material_for_rack = action_body.material_group_id if action_body.material_group_id else rack.material_group_id
            if not material_for_rack:
                material_for_rack = _ensure_no_material_group_in_db(db).id
            rack.material_group_id = material_for_rack
            rack.lot = (action_body.lot or '').strip() or None
            rack.quantity = int(action_body.quantity or 0)
            rack.manufacturer_code = (action_body.manufacturer_code or '').strip() or None
            rack.comment = (action_body.comment or '').strip() or None
            if action_body.custom_field_values:
                _apply_action_custom_fields_to_rack(rack, action_body.custom_field_values)
            rack.updated_at = datetime.utcnow()
            db.add(rack)
            db.commit()
            db.refresh(rack)
        order = execute_direct_move_request(
            db,
            source_cell.id,
            destination_cell.id,
            button_row.priority,
            (action_body.comment if action_body and action_body.comment is not None else button_row.comment),
            f'ventana:{window_row.name} / botón:{button_row.label}',
            (action_body.agv_code if action_body and action_body.agv_code else button_row.agv_code),
            (action_body.task_typ if action_body and action_body.task_typ else button_row.task_typ),
            window_row.id,
        )
        return order
    raise HTTPException(status_code=400, detail='Modo de botón inválido')


@app.get('/api/operator-windows', response_model=List[OperatorWindowOut])
def list_operator_windows():
    with SessionLocal() as db:
        rows = db.execute(select(OperatorWindow).where(OperatorWindow.is_active == 1).order_by(OperatorWindow.name.asc())).scalars().all()
        return [_operator_window_out(db, row, include_buttons=False) for row in rows]


@app.get('/api/admin/operator-windows', response_model=List[OperatorWindowOut])
def admin_list_operator_windows(x_admin_token: Optional[str] = Header(default=None, alias='X-Admin-Token')):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        rows = db.execute(select(OperatorWindow).order_by(OperatorWindow.name.asc())).scalars().all()
        return [_operator_window_out(db, row, include_buttons=False) for row in rows]


@app.get('/api/admin/operator-windows/{window_id}', response_model=OperatorWindowOut)
def admin_get_operator_window(window_id: int, x_admin_token: Optional[str] = Header(default=None, alias='X-Admin-Token')):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
        _sync_operator_window_buttons(db, row)
        logger.info("Operator window access granted window_id=%s name=%s", row.id, row.name)
        return _operator_window_out(db, row, include_buttons=True)


@app.post('/api/admin/operator-windows', response_model=OperatorWindowOut)
def admin_save_operator_window(body: OperatorWindowIn, x_admin_token: Optional[str] = Header(default=None, alias='X-Admin-Token')):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = _save_operator_window(db, body)
        logger.info("Operator window saved window_id=%s name=%s", row.id, row.name)
        return _operator_window_out(db, row, include_buttons=True)


@app.post('/api/admin/operator-windows/{window_id}/buttons/{button_index}', response_model=OperatorWindowButtonOut)
def admin_save_operator_window_button(window_id: int, button_index: int, body: OperatorWindowButtonIn, x_admin_token: Optional[str] = Header(default=None, alias='X-Admin-Token')):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = _save_operator_window_button(db, window_id, button_index, body)
        logger.info("Operator window button saved window_id=%s button_index=%s mode=%s", window_id, button_index, row.action_mode)
        return _window_button_out(db, row)


@app.delete('/api/admin/operator-windows/{window_id}')
def admin_delete_operator_window(window_id: int, x_admin_token: Optional[str] = Header(default=None, alias='X-Admin-Token')):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
        db.execute(delete(OperatorWindowButton).where(OperatorWindowButton.window_id == window_id))
        db.execute(delete(MovementOrder).where(MovementOrder.created_window_id == window_id))
        db.delete(row)
        db.commit()
        logger.info("Operator window deleted window_id=%s name=%s", window_id, row.name)
        return {'ok': True, 'deleted_window_id': window_id}


@app.post('/api/operator-windows/{window_id}/access', response_model=OperatorWindowOut)
def operator_window_access(window_id: int, body: OperatorWindowAccessIn):
    with SessionLocal() as db:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id, OperatorWindow.is_active == 1)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
        if not _validate_window_password(row, body.password):
            logger.warning("Operator window access failed window_id=%s", window_id)
            raise HTTPException(status_code=401, detail='Contraseña de ventana incorrecta')
        _sync_operator_window_buttons(db, row)
        return _operator_window_out(db, row, include_buttons=True)


@app.post('/api/operator-windows/{window_id}/buttons/{button_index}/preview', response_model=OperatorWindowPreviewOut)
def operator_preview_window_button(window_id: int, button_index: int, body: OperatorWindowActionIn):
    with SessionLocal() as db:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id, OperatorWindow.is_active == 1)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
        if not _validate_window_password(row, body.password):
            raise HTTPException(status_code=401, detail='Contraseña de ventana incorrecta')
        button_row = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.window_id == window_id, OperatorWindowButton.button_index == button_index)).scalar_one_or_none()
        if not button_row:
            raise HTTPException(status_code=404, detail='Botón no encontrado')
        return _preview_window_button(db, row, button_row, body)


def _rollback_unsuccessful_order_dispatch(db, order: MovementOrder, error_message: Optional[str] = None) -> None:
    now = datetime.utcnow()
    message = (error_message or order.rcs_message or "Dispatch error").strip()
    previous_status = order.status or ""
    order.status = "dispatch_error"
    order.dispatch_status = "error"
    order.rcs_status = "error"
    order.rcs_message = message
    order.updated_at = now
    _audit_order_close(db, order, previous_status=previous_status, new_status=order.status, source="rollback_dispatch_error", reason=message, actor="api_internal", closed_at=now, auto_commit=False)
    db.add(order)

    released_racks = []
    for rack_id in _related_rack_ids_for_order(order):
        rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
        if not rack:
            continue
        old_status = rack.status or ""
        released = _release_rack_if_no_active_orders(
            db,
            rack.id,
            related_order_id=order.id,
            reason=message,
            source="rollback_dispatch_error",
            actor="api_internal",
        )
        if released:
            released_racks.append({"rack_id": rack.id, "old_status": old_status, "new_status": rack.status})

    db.commit()
    db.refresh(order)
    logger.warning(
        "Preserved unsuccessful dispatch order_id=%s order_code=%s status=%s released_racks=%s error=%s",
        order.id,
        order.order_code,
        order.status,
        released_racks,
        message,
    )


@app.post('/api/operator-windows/{window_id}/buttons/{button_index}/execute', response_model=MovementOrderOut)
def operator_execute_window_button(window_id: int, button_index: int, body: OperatorWindowActionIn):
    with SessionLocal() as db:
        row = db.execute(select(OperatorWindow).where(OperatorWindow.id == window_id, OperatorWindow.is_active == 1)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail='Ventana no encontrada')
        if not _validate_window_password(row, body.password):
            raise HTTPException(status_code=401, detail='Contraseña de ventana incorrecta')
        button_row = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.window_id == window_id, OperatorWindowButton.button_index == button_index)).scalar_one_or_none()
        if not button_row:
            raise HTTPException(status_code=404, detail='Botón no encontrado')
        logger.info("Operator button execute window_id=%s button_index=%s mode=%s", window_id, button_index, button_row.action_mode)
        order = _execute_window_button(db, row, button_row, body)
        if order is None:
            raise HTTPException(status_code=400, detail='No se pudo generar la orden')
        logger.info("Task created | task_id=%s | order_code=%s | source=operator_window | window_id=%s | button_index=%s | rack_id=%s | agv=%s | status=%s", order.id, order.order_code, window_id, button_index, order.rack_id, order.agv_code or "-", order.status)
        rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none()
        if rack:
            _audit_rack_reservation_change(db, rack=rack, previous_status="available", new_status=rack.status, source="operator_window", related_order_id=order.id, reason=f"operator_window:{button_row.action_mode}", actor=f"operator_window:{row.name}", auto_commit=False)
            db.commit()
        dispatch_result = _dispatch_movement_order(db, order)
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or 'El RCS rechazó la creación de la tarea'
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f'El RCS no aceptó la tarea: {message}')
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=500, detail='La orden enviada ya no está disponible')
        return _movement_order_out(db, order)
