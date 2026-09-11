from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Union
import asyncio
import contextlib
import contextvars
import shutil
import stat
import threading
import time
import random
import re
import json
import hashlib
import hmac
import io
import os
import platform
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import uuid
import zipfile
from pathlib import PurePosixPath
from types import SimpleNamespace

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, delete, text, or_, event, update

from logging_config import ensure_log_file, get_logger
from rcs_observability import sanitize, sanitize_text, sensitive_key, trace_call, protect_payload, unprotect_payload, restore_redactions, install_persistence_sanitizer
install_persistence_sanitizer()
from db_diagnostics import EXPECTED_INDEXES, check_expected_indexes, open_read_only
from performance_observability import metrics as performance_metrics
from rcs_client import RcsClient, RcsError, RcsNotSentError, RcsSimpleResponse, RcsTaskRequest, RcsTaskResponse, RcsTaskStatusResponse
from monitor_service import TaskMonitor, build_auto_status_query_payload, generate_req_code_ms, apply_remote_status_to_order, _normalize_remote_status, _single_task_response_item, _task_code_from_error_message, local_reconciliation_pending, TERMINAL_LOCAL_STATUSES
from scanner_service import ScanPreviewError, execute_scan, preview_scan, resolve_cancel_return_target, resolve_scan_terminal, terminal_preview_scan, _rule_matches, normalize_qr_value
from qr_transition_service import apply_transition_for_completed_order, diagnose_transition_rule_payload, enforce_transition_rule_diagnostics, preview_transition_for_order, transition_match_metadata, transition_match_tier, transition_selection_reason, transition_specificity, validate_transition_rule_payload
from fifo_chain_service import set_fifo_chain_default_dispatch_func, recover_fifo_chains, stop_fifo_chain

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
    ConfigAuditLog,
    RackSyncEvent,
    Location,
    MaterialGroup,
    MovementOrder,
    Rack,
    QrActionRule,
    QrTransitionLog,
    QrTransitionRule,
    ScanEvent,
    ScannerStation,
    ScanTerminal,
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




RUNTIME_RECENT_ORDER_LIMIT = 100
ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS = 5.0
NO_MATERIAL_CODE = "SIN_MATERIAL"
NO_MATERIAL_NAME = "Sin material"
NO_MATERIAL_COLOR = "#94a3b8"
RACK_RESERVATION_ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo", "cancel_error")
logger = get_logger("app.main")
HTTP_SLOW_WARNING_MS = float(os.getenv("PERF_HTTP_SLOW_MS", "500"))
SQL_SLOW_WARNING_MS = float(os.getenv("PERF_SQL_SLOW_MS", "100"))
RCS_SLOW_WARNING_MS = float(os.getenv("PERF_RCS_SLOW_MS", "5000"))
WEBSOCKET_LARGE_WARNING_BYTES = int(os.getenv("PERF_WS_LARGE_BYTES", str(1024 * 1024)))
SOFTWARE_UPDATE_LOCK = threading.Lock()
CONFIG_AUDIT_CONTEXT = contextvars.ContextVar("CONFIG_AUDIT_CONTEXT", default={})


def _performance_endpoint_label(method: str, path: str) -> str:
    important = {
        "/api/fifo/execute": "fifo_execute",
        "/api/direct-move/execute": "direct_move_execute",
        "/api/locations": "locations",
        "/api/catalog": "catalog",
        "/api/movement-orders": "movement_orders",
        "/api/runtime-snapshot": "runtime_snapshot",
        "/api/robot-status-monitor": "robot_monitor",
        "/api/admin/performance-health": "admin_performance_health",
    }
    if path in important:
        return important[path]
    if path.startswith("/api/operator-windows/"):
        return "operator_window"
    return f"{method.upper()} other"


def _sql_before_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
    context._performance_started_at = time.perf_counter()


def _sql_finish(context) -> None:
    started = getattr(context, "_performance_started_at", None)
    if started is None:
        return
    duration_ms = (time.perf_counter() - started) * 1000.0
    performance_metrics.record("sql", duration_ms, label="statement")
    if duration_ms > SQL_SLOW_WARNING_MS:
        logger.warning("SLOW_SQL duration_ms=%.1f", duration_ms)


def _sql_after_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
    _sql_finish(context)


def _sql_handle_error(exception_context):
    if exception_context.execution_context is not None:
        _sql_finish(exception_context.execution_context)


event.listen(engine, "before_cursor_execute", _sql_before_cursor_execute)
event.listen(engine, "after_cursor_execute", _sql_after_cursor_execute)
event.listen(engine, "handle_error", _sql_handle_error)


def _set_config_audit_context(**values):
    current = dict(CONFIG_AUDIT_CONTEXT.get() or {})
    current.update({k: v for k, v in values.items() if v is not None})
    return CONFIG_AUDIT_CONTEXT.set(current)


def _reset_config_audit_context(token):
    CONFIG_AUDIT_CONTEXT.reset(token)


def _config_audit_context() -> dict:
    return dict(CONFIG_AUDIT_CONTEXT.get() or {})
SOFTWARE_BUILD_INFO_REL_PATH = "static/build_info.json"
RACK_POSITION_POLL_INTERVAL_SECONDS = 10
RACK_POSITION_POLL_TIMEOUT_SECONDS = 10 * 60
_rack_position_poll_lock = threading.Lock()
_rack_position_poll_active: set[tuple[int, int]] = set()
RACK_SYNC_SCHEDULER_INTERVAL_SECONDS = 60
_rack_sync_scheduler_stop_event = threading.Event()
_rack_sync_scheduler_lock = threading.Lock()
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
CLEANUP_RCS_TERMINAL_INPUT_STATUSES = tuple(CLEANUP_RCS_TERMINAL_STATUS_MAP)


def _app_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))
STUCK_CANCEL_RETURN_AUTO_RELEASE_INTERVAL_SECONDS = 30
_stuck_cancel_return_auto_release_lock = threading.Lock()
_stuck_cancel_return_auto_release_last_ts = 0.0


class RuntimeWebSocketManager:
    def __init__(self):
        self._connections: dict[WebSocket, asyncio.Queue] = {}
        self._sender_tasks: dict[WebSocket, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def accept(self, websocket: WebSocket):
        await websocket.accept()

    async def connect(self, websocket: WebSocket):
        queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._connections[websocket] = queue
            self._sender_tasks[websocket] = asyncio.create_task(self._sender(websocket, queue))

    async def _sender(self, websocket: WebSocket, queue: asyncio.Queue):
        try:
            while True:
                payload = await queue.get()
                started = time.perf_counter()
                if hasattr(websocket, "send"):
                    encoded_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                    payload_size = len(encoded_payload.encode("utf-8"))
                    await websocket.send({"type": "websocket.send", "text": encoded_payload})
                else:
                    # Small test doubles may only implement send_json.
                    payload_size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
                    await websocket.send_json(payload)
                duration_ms = (time.perf_counter() - started) * 1000.0
                performance_metrics.record("websocket", duration_ms, label="send_json", size_bytes=payload_size)
                if payload_size > WEBSOCKET_LARGE_WARNING_BYTES:
                    logger.warning("SLOW_WEBSOCKET_PAYLOAD size_bytes=%s duration_ms=%.1f", payload_size, duration_ms)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.disconnect(websocket, from_sender=True)

    async def disconnect(self, websocket: WebSocket, from_sender: bool = False):
        async with self._lock:
            self._connections.pop(websocket, None)
            task = self._sender_tasks.pop(websocket, None)
        if task is not None and not from_sender:
            task.cancel()

    async def broadcast_json(self, payload: dict):
        async with self._lock:
            targets = list(self._connections.items())
        for _ws, queue in targets:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                while not queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait({"type": "runtime_resync_required", "revision": _runtime_current_revision()})

    async def send_json(self, websocket: WebSocket, payload: dict):
        async with self._lock:
            queue = self._connections.get(websocket)
        if queue is None:
            await websocket.send_json(payload)
        else:
            await queue.put(payload)

    async def count(self) -> int:
        async with self._lock:
            return len(self._connections)


runtime_ws_manager = RuntimeWebSocketManager()
_runtime_revision_lock = threading.Lock()
_runtime_revision = 0
_runtime_event_history = []
_runtime_event_queue = None
_runtime_event_loop = None
_runtime_queue_overflow = False
_locations_revision_lock = threading.Lock()
_locations_revision = int(time.time() * 1000)
_locations_count_lock = threading.Lock()
_locations_count_cache = None
_robot_monitor_cache_lock = threading.Lock()
_robot_monitor_refresh_lock = threading.Lock()
_robot_monitor_background_stop_event = threading.Event()
_robot_monitor_cache = {"timestamp": 0.0, "payload": None, "last_error": ""}


def _runtime_current_revision() -> int:
    with _runtime_revision_lock:
        return _runtime_revision


def _locations_current_revision() -> int:
    with _locations_revision_lock:
        return _locations_revision


def _increment_locations_revision() -> int:
    global _locations_revision
    with _locations_revision_lock:
        _locations_revision += 1
        return _locations_revision


def _locations_current_count() -> int:
    global _locations_count_cache
    with _locations_count_lock:
        if _locations_count_cache is not None:
            return _locations_count_cache
        with SessionLocal() as db:
            _locations_count_cache = int(db.execute(select(func.count()).select_from(Location)).scalar_one() or 0)
        return _locations_count_cache


def _adjust_locations_count(changes: dict) -> None:
    global _locations_count_cache
    with _locations_count_lock:
        if _locations_count_cache is None:
            return
        created = sum(1 for (kind, _entity_id), action in changes.items() if kind == "location" and action == "created")
        deleted = sum(1 for (kind, _entity_id), action in changes.items() if kind == "location" and action == "deleted")
        _locations_count_cache = max(0, _locations_count_cache + created - deleted)


def publish_runtime_event(event_type: str, payload: dict, *, enqueue: bool = True) -> dict:
    """Publish a small runtime event without blocking the committing thread."""
    global _runtime_revision, _runtime_queue_overflow
    with _runtime_revision_lock:
        _runtime_revision += 1
        message = {"type": event_type, "revision": _runtime_revision, "payload": payload}
        _runtime_event_history.append(message)
        del _runtime_event_history[:-1000]
        loop = _runtime_event_loop
        queue = _runtime_event_queue
    if enqueue and loop is not None and queue is not None and loop.is_running():
        def enqueue():
            global _runtime_queue_overflow
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                _runtime_queue_overflow = True
        loop.call_soon_threadsafe(enqueue)
    return message


def _queue_runtime_change(change: dict) -> None:
    global _runtime_queue_overflow
    with _runtime_revision_lock:
        loop = _runtime_event_loop
        queue = _runtime_event_queue
    if loop is None or queue is None or not loop.is_running():
        return
    def enqueue():
        global _runtime_queue_overflow
        try:
            queue.put_nowait({"_change": change})
        except asyncio.QueueFull:
            _runtime_queue_overflow = True
    loop.call_soon_threadsafe(enqueue)


def _runtime_events_since(revision: int) -> list:
    with _runtime_revision_lock:
        return [message for message in _runtime_event_history if int(message.get("revision") or 0) > revision]


@event.listens_for(SessionLocal, "after_flush")
def _capture_runtime_session_changes(session, _flush_context):
    changes = session.info.setdefault("runtime_changes", {})
    tracked = (
        (MovementOrder, "order"), (Location, "location"), (Rack, "rack"),
        (DebugConsoleEvent, "debug_event"), (Area, "catalog"), (MaterialGroup, "catalog"),
    )
    for collection, action in ((session.new, "created"), (session.dirty, "updated"), (session.deleted, "deleted")):
        for row in collection:
            if action == "updated" and not session.is_modified(row, include_collections=False):
                continue
            for model, kind in tracked:
                if isinstance(row, model) and getattr(row, "id", None) is not None:
                    changes[(kind, int(row.id))] = action
                    if kind == "order" and getattr(row, "rack_id", None):
                        changes[("rack", int(row.rack_id))] = "updated"
                    break


@event.listens_for(SessionLocal, "after_commit")
def _publish_runtime_session_changes(session):
    changes = session.info.pop("runtime_changes", {})
    if any(kind == "location" for kind, _entity_id in changes):
        _adjust_locations_count(changes)
        _increment_locations_revision()
    for (kind, entity_id), action in changes.items():
        _queue_runtime_change({"kind": kind, "id": entity_id, "action": action})


@event.listens_for(SessionLocal, "after_rollback")
def _discard_runtime_session_changes(session):
    session.info.pop("runtime_changes", None)

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
    web_base_url: str = Field(default="", max_length=512)
    web_login_endpoint: str = Field(default="/rcms/web/login/login.action", max_length=256)
    web_charge_endpoint: str = Field(default="/rcms/web/agvControl/agvChargeTask.action", max_length=256)
    web_username: str = Field(default="", max_length=128)
    web_ecs_password: str = Field(default="", max_length=2048)
    web_charge_code: Literal["1"] = "1"
    base_url: str = Field(default="", max_length=512)
    simulator_enabled: int = Field(default=0, ge=0, le=1)
    create_task_endpoint: str = Field(default="/rcs/task/create", max_length=256)
    query_task_status_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/queryTaskStatus", max_length=256)
    cancel_task_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/cancelTask", max_length=256)
    stop_robot_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/stopRobot", max_length=256)
    resume_robot_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/resumeRobot", max_length=256)
    agv_status_endpoint: str = Field(default="/rcms-dps/rest/queryAgvStatus", max_length=256)
    pod_position_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/queryPodPosition", max_length=256)
    rack_sync_query_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/queryPodBerthAndMat", max_length=256)
    rack_sync_bind_endpoint: str = Field(default="/rcms/services/rest/hikRpcService/bindPodAndBerth", max_length=256)
    rack_sync_schedule_enabled: int = Field(default=0, ge=0, le=1)
    rack_sync_schedule_time: str = Field(default="12:00", max_length=5)
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
        "rack_sync_schedule_enabled",
        "verify_tls",
        "simulator_enabled",
        mode="before",
    )
    @classmethod
    def _coerce_int_flag_fields(cls, v, info):
        defaults = {
            "enable_map_short_name": 1,
            "enable_map_code": 0,
            "enable_amr_monitor": 1,
            "rack_sync_schedule_enabled": 0,
            "verify_tls": 0,
            "simulator_enabled": 0,
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

    @field_validator("rack_sync_schedule_time", mode="before")
    @classmethod
    def _coerce_rack_sync_schedule_time(cls, value):
        text = str(value or "12:00").strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", text):
            raise ValueError("rack_sync_schedule_time debe tener formato HH:MM")
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour > 23 or minute > 59:
            raise ValueError("rack_sync_schedule_time debe estar entre 00:00 y 23:59")
        return f"{hour:02d}:{minute:02d}"


class RcsConfigOut(RcsConfigIn):
    resolved_base_url: str = ""
    resolved_token_code: str = ""
    resolved_auth_header: str = ""
    rack_sync_schedule_last_run_date: str = ""
    simulator_base_url: str = "sim://local-rcs"


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
    confirm_local_only: bool = False
    reason: Optional[str] = Field(default=None, max_length=256)


class SimulateCompleteIn(BaseModel):
    confirm_simulation: bool = False
    reason: str = Field(default="", max_length=256)


class RepairInconsistentLocationsIn(BaseModel):
    location_ids: List[int] = Field(default_factory=list)
    repair_mode: str = Field(default="safe", pattern="^safe$")
    reason: str = Field(min_length=1, max_length=256)


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


class RackSyncPreviewItem(BaseModel):
    location_id: int
    location_code: Optional[str] = None
    location_x: int
    location_y: int
    rack_id: int
    rack_code: Optional[str] = None
    status: str
    errors: List[str] = Field(default_factory=list)
    query_payload: dict = Field(default_factory=dict)
    bind_payload: dict = Field(default_factory=dict)


class RackSyncQueryItem(RackSyncPreviewItem):
    rcs_position_code: Optional[str] = None
    rcs_map_data_code: Optional[str] = None
    action: str = "none"
    response_payload: dict = Field(default_factory=dict)
    query_error: Optional[str] = None


class RackSyncPreviewOut(BaseModel):
    ok: bool
    mode: str = "preview_only"
    blocked: bool = False
    active_tasks_count: int = 0
    query_endpoint: str
    bind_endpoint: str
    map_short_name: str = ""
    total_assigned_racks: int = 0
    ready_count: int = 0
    error_count: int = 0
    message: str = ""
    items: List[RackSyncPreviewItem] = Field(default_factory=list)


class RackSyncQueryOut(BaseModel):
    ok: bool
    mode: str = "query_compare_only"
    active_tasks_count: int = 0
    query_endpoint: str
    bind_endpoint: str
    map_short_name: str = ""
    total_assigned_racks: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    missing_count: int = 0
    invalid_count: int = 0
    error_count: int = 0
    message: str = ""
    items: List[RackSyncQueryItem] = Field(default_factory=list)


class RackSyncBindItem(RackSyncQueryItem):
    bind_status: str = "not_sent"
    bind_response_payload: dict = Field(default_factory=dict)
    bind_error: Optional[str] = None


class RackSyncBindOut(BaseModel):
    ok: bool
    mode: str = "bind_mismatches_manual"
    blocked: bool = False
    active_tasks_count: int = 0
    query_endpoint: str
    bind_endpoint: str
    total_assigned_racks: int = 0
    mismatch_count: int = 0
    attempted_count: int = 0
    success_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    message: str = ""
    items: List[RackSyncBindItem] = Field(default_factory=list)


class RackSyncEventOut(BaseModel):
    id: int
    action: str
    ok: bool
    blocked: bool = False
    total_assigned_racks: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    missing_count: int = 0
    invalid_count: int = 0
    attempted_count: int = 0
    success_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    active_tasks_count: int = 0
    duration_ms: int = 0
    message: str = ""
    payload: dict = Field(default_factory=dict)
    created_at: datetime


class RackSyncScheduleRunOut(BaseModel):
    ok: bool
    ran: bool = False
    message: str = ""

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
    state: str


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
    state: str


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


class ScanPreviewIn(BaseModel):
    scanner_code: str = Field(min_length=1, max_length=128)
    qr_value: str = Field(min_length=1, max_length=256)
    created_by: Optional[str] = Field(default=None, max_length=128)


class ScanTerminalRequestIn(BaseModel):
    terminal_code: str = Field(min_length=1, max_length=128)
    qr_value: str = Field(min_length=1, max_length=256)
    mode: str = Field(default="preview", min_length=1, max_length=32)


class ScanPreviewOut(BaseModel):
    ok: bool
    automatic_priority: bool = False
    priority_plan: list[dict] = Field(default_factory=list)
    movement_orders: list[dict] = Field(default_factory=list)
    send_mode: Optional[str] = None
    mode: str
    status: Optional[str] = None
    scanner: dict
    qr: dict
    action: Optional[str] = None
    material: dict = {}
    rack_selected: dict = {}
    source: dict = {}
    destination: dict = {}
    parsed: dict = {}
    message: str
    error_code: Optional[str] = None
    operator_message: Optional[str] = None
    technical_message: Optional[str] = None
    destination_area: dict = {}
    destination_area_name: Optional[str] = None
    checked_destination_locations: list[dict] = []
    suggested_action: Optional[str] = None
    step: Optional[int] = None
    source_label: Optional[str] = None
    destination_label: Optional[str] = None
    operator_diagnosis: dict = {}
    scan_event_id: Optional[int] = None
    terminal: dict = {}
    terminal_code: Optional[str] = None
    scanner_code: Optional[str] = None
    qr_value: Optional[str] = None
    movement_order_id: Optional[int] = None
    movement_order: dict = {}
    dispatch: dict = {}
    rollback: dict = {}
    dispatch_status: Optional[str] = None
    rcs_status: Optional[str] = None
    existing_scan_event_id: Optional[int] = None
    existing_movement_order_id: Optional[int] = None
    route_mode: Optional[str] = None
    route_points: list[dict] = []
    fifo_chain_steps: list[dict] = []
    fifo_chain_projection: dict = {}
    fifo_chain_preflight_checklist: dict = {}
    fifo_chain_total_steps: Optional[int] = None
    fifo_chain_step1_source_mode: Optional[str] = None
    fifo_chain_step2_source_mode: Optional[str] = None
    fifo_chain_step3_source_mode: Optional[str] = None
    fifo_chain_step1_material: dict = {}
    fifo_chain_step2_material: dict = {}
    fifo_chain_step3_material: dict = {}
    fifo_chain_step3_source: dict = {}
    fifo_chain_step3_destination: dict = {}
    fifo_chain_step4_source_mode: Optional[str] = None
    fifo_chain_step5_source_mode: Optional[str] = None
    fifo_chain_step4_material_group_id: Optional[int] = None
    fifo_chain_step5_material_group_id: Optional[int] = None
    fifo_chain_step4_material_group_name: Optional[str] = None
    fifo_chain_step5_material_group_name: Optional[str] = None
    fifo_chain_step4_material: dict = {}
    fifo_chain_step5_material: dict = {}
    fifo_chain_step4_source: dict = {}
    fifo_chain_step5_source: dict = {}
    fifo_chain_step4_destination: dict = {}
    fifo_chain_step5_destination: dict = {}
    fifo_chain_group_id: Optional[str] = None
    fifo_chain_step: Optional[int] = None
    fifo_chain_status: Optional[str] = None
    fifo_chain_next_config_saved: Optional[bool] = None


class ScanEventOut(BaseModel):
    id: int
    scanner_code: Optional[str] = None
    scanner_station_id: Optional[int] = None
    scanner_station_name: Optional[str] = None
    terminal_id: Optional[int] = None
    terminal_code: Optional[str] = None
    terminal_name: Optional[str] = None
    qr_value: Optional[str] = None
    qr_action_rule_id: Optional[int] = None
    qr_alias: Optional[str] = None
    parsed_type: Optional[str] = None
    resolved_action: Optional[str] = None
    rack_id: Optional[int] = None
    rack_code: Optional[str] = None
    material_group_id: Optional[int] = None
    material_group_name: Optional[str] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    source_area_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    movement_order_id: Optional[int] = None
    mode: str
    status: str
    error_message: Optional[str] = None
    request: dict = {}
    result: dict = {}
    created_by: Optional[str] = None
    created_at: datetime


VALID_SCANNER_STATION_TYPES = {"line_request", "storage_in", "empty_request", "full_rack_return", "direct_move", "generic"}
VALID_SCANNER_ACTIONS = {"fifo_request", "store_rack", "request_empty", "return_full", "direct_move", "point_to_area", "preview_only"}
VALID_QR_TYPES = {"material", "rack", "request", "move", "store", "generic"}
VALID_QR_MATCH_TYPES = {"exact", "contains", "prefix", "regex"}
VALID_QR_ACTION_TYPES = {"fifo_request", "store_rack", "request_empty", "return_full", "direct_move", "point_to_area", "use_scanner_default"}
VALID_SCAN_TERMINAL_MODES = {"preview", "execute"}
VALID_ROUTE_MODES = {"simple_area", "double_area", "fifo_chain", "trmx_doble"}
VALID_FIFO_MATERIAL_POLICIES = {"specific_material", "any_available_from_source"}
VALID_FIFO_CHAIN_SOURCE_MODES = {"configured_area", "any_area_by_material", "selected_areas_by_material"}
VALID_FIFO_CHAIN_DESTINATION_MODES = {"configured_area", "any_area_with_space", "selected_areas_with_space"}
VALID_FIFO_CHAIN_STEP2_SOURCE_MODES = VALID_FIFO_CHAIN_SOURCE_MODES
VALID_FIFO_CHAIN_TOTAL_STEPS = {2, 3, 4, 5}
VALID_FIFO_CHAIN_STEP3_SOURCE_MODES = VALID_FIFO_CHAIN_SOURCE_MODES
VALID_FIFO_CHAIN_STEP4_SOURCE_MODES = VALID_FIFO_CHAIN_SOURCE_MODES
VALID_FIFO_CHAIN_STEP5_SOURCE_MODES = VALID_FIFO_CHAIN_SOURCE_MODES


def _normalize_route_mode(value: Optional[str]) -> str:
    mode = (str(value or "simple_area").strip() or "simple_area")
    if mode == "trmx_doble":
        return "fifo_chain"
    return mode


def _normalize_fifo_chain_step2_source_mode(value: Optional[str]) -> str:
    mode = (str(value or "configured_area").strip() or "configured_area")
    return mode


def _normalize_fifo_chain_step1_source_mode(value: Optional[str]) -> str:
    mode = (str(value or "configured_area").strip() or "configured_area")
    return mode


def _normalize_fifo_chain_step3_source_mode(value: Optional[str]) -> str:
    mode = (str(value or "configured_area").strip() or "configured_area")
    return mode


def _normalize_fifo_chain_step4_source_mode(value: Optional[str]) -> str:
    mode = (str(value or "configured_area").strip() or "configured_area")
    return mode


def _normalize_fifo_chain_step5_source_mode(value: Optional[str]) -> str:
    mode = (str(value or "configured_area").strip() or "configured_area")
    return mode


def _normalize_fifo_chain_total_steps(value) -> int:
    if value in (None, ""):
        return 2
    try:
        total = int(value)
    except (TypeError, ValueError):
        return 2
    return total if total in VALID_FIFO_CHAIN_TOTAL_STEPS else 2


def _normalize_area_ids_json(value) -> tuple[list[int], Optional[str]]:
    if value in (None, "", []):
        return [], None
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            raise HTTPException(status_code=400, detail="La lista de areas seleccionadas no es JSON valido")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="La lista de areas seleccionadas debe ser una lista")
    result = []
    for item in raw:
        try:
            area_id = int(item)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="La lista de areas seleccionadas contiene un ID invalido")
        if area_id <= 0 or area_id in result:
            if area_id <= 0:
                raise HTTPException(status_code=400, detail="La lista de areas seleccionadas contiene un ID invalido")
            continue
        result.append(area_id)
    return result, json.dumps(result, ensure_ascii=False) if result else None


def _validate_selected_area_ids(db, area_ids: list[int], label: str) -> None:
    if not area_ids:
        raise HTTPException(status_code=400, detail=f"{label}: no se configuraron areas validas para esta seleccion")
    rows = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all()
    valid = {row.id for row in rows if int(row.is_active or 0) == 1}
    missing = [area_id for area_id in area_ids if area_id not in valid]
    if missing:
        raise HTTPException(status_code=400, detail=f"{label}: areas inexistentes o inactivas: {missing}")


def _validate_fifo_chain_area_modes(db, body, route_mode: str) -> None:
    total_steps = _validate_fifo_chain_total_steps_input(body.fifo_chain_total_steps)
    for step in range(1, 6):
        source_mode = str(getattr(body, f"fifo_chain_step{step}_source_mode", None) or "configured_area").strip()
        destination_mode = str(getattr(body, f"fifo_chain_step{step}_destination_mode", None) or "configured_area").strip()
        _validate_choice(source_mode, VALID_FIFO_CHAIN_SOURCE_MODES, f"fifo_chain_step{step}_source_mode")
        _validate_choice(destination_mode, VALID_FIFO_CHAIN_DESTINATION_MODES, f"fifo_chain_step{step}_destination_mode")
        source_ids, _ = _normalize_area_ids_json(getattr(body, f"fifo_chain_step{step}_source_area_ids_json", None))
        destination_ids, _ = _normalize_area_ids_json(getattr(body, f"fifo_chain_step{step}_destination_area_ids_json", None))
        if route_mode != "fifo_chain" or step > total_steps:
            continue
        material_id = getattr(body, f"fifo_chain_step{step}_material_group_id", None)
        if step == 1:
            source_area_id, source_cell_id = body.source_area_id, body.source_cell_id
            destination_area_id, destination_cell_id = body.destination_area_id, body.destination_cell_id
        elif step == 2:
            source_area_id, source_cell_id = body.second_source_area_id, body.second_source_cell_id
            destination_area_id, destination_cell_id = body.second_destination_area_id, body.second_destination_cell_id
        else:
            source_area_id = getattr(body, f"fifo_chain_step{step}_source_area_id", None)
            source_cell_id = getattr(body, f"fifo_chain_step{step}_source_cell_id", None)
            destination_area_id = getattr(body, f"fifo_chain_step{step}_destination_area_id", None)
            destination_cell_id = getattr(body, f"fifo_chain_step{step}_destination_cell_id", None)
        if source_mode == "configured_area":
            if not source_area_id:
                raise HTTPException(status_code=400, detail=f"Origen tramo {step}: requiere area configurada")
            if source_cell_id:
                cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
                if not cell or cell.area_id != source_area_id:
                    raise HTTPException(status_code=400, detail=f"Origen tramo {step}: la celda no pertenece al area")
        if source_mode == "selected_areas_by_material":
            if not material_id:
                raise HTTPException(status_code=400, detail=f"El tramo {step} por areas seleccionadas requiere material")
            _validate_selected_area_ids(db, source_ids, f"Origen tramo {step}")
        if destination_mode == "configured_area":
            if not destination_area_id:
                raise HTTPException(status_code=400, detail=f"Destino tramo {step}: requiere area configurada")
            if destination_cell_id:
                cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none()
                if not cell or cell.area_id != destination_area_id:
                    raise HTTPException(status_code=400, detail=f"Destino tramo {step}: la celda no pertenece al area")
        if destination_mode == "selected_areas_with_space":
            _validate_selected_area_ids(db, destination_ids, f"Destino tramo {step}")


def _apply_fifo_chain_area_modes(row, body) -> None:
    total_steps = _normalize_fifo_chain_total_steps(getattr(row, "fifo_chain_total_steps", None))
    route_mode = _normalize_route_mode(getattr(row, "route_mode", None))
    for step in range(1, 6):
        applies = route_mode == "fifo_chain" and step <= total_steps
        source_mode = str(getattr(body, f"fifo_chain_step{step}_source_mode", None) or "configured_area").strip()
        destination_mode = str(getattr(body, f"fifo_chain_step{step}_destination_mode", None) or "configured_area").strip()
        _, source_json = _normalize_area_ids_json(getattr(body, f"fifo_chain_step{step}_source_area_ids_json", None))
        _, destination_json = _normalize_area_ids_json(getattr(body, f"fifo_chain_step{step}_destination_area_ids_json", None))
        setattr(row, f"fifo_chain_step{step}_source_mode", source_mode if applies else "configured_area")
        setattr(row, f"fifo_chain_step{step}_source_area_ids_json", source_json if applies and source_mode == "selected_areas_by_material" else None)
        setattr(row, f"fifo_chain_step{step}_destination_mode", destination_mode if applies else "configured_area")
        setattr(row, f"fifo_chain_step{step}_destination_area_ids_json", destination_json if applies and destination_mode == "selected_areas_with_space" else None)
        if not applies:
            continue
        if step == 1:
            source_area_field, source_cell_field = "source_area_id", "source_cell_id"
            destination_area_field, destination_cell_field = "destination_area_id", "destination_cell_id"
        elif step == 2:
            source_area_field, source_cell_field = "second_source_area_id", "second_source_cell_id"
            destination_area_field, destination_cell_field = "second_destination_area_id", "second_destination_cell_id"
        else:
            source_area_field = f"fifo_chain_step{step}_source_area_id"
            source_cell_field = f"fifo_chain_step{step}_source_cell_id"
            destination_area_field = f"fifo_chain_step{step}_destination_area_id"
            destination_cell_field = f"fifo_chain_step{step}_destination_cell_id"
        if source_mode != "configured_area":
            setattr(row, source_area_field, None)
            setattr(row, source_cell_field, None)
        if destination_mode != "configured_area":
            setattr(row, destination_area_field, None)
            setattr(row, destination_cell_field, None)


def _fifo_chain_area_modes_out(row) -> dict:
    result = {}
    for step in range(1, 6):
        result[f"fifo_chain_step{step}_source_area_ids_json"] = getattr(row, f"fifo_chain_step{step}_source_area_ids_json", None)
        result[f"fifo_chain_step{step}_destination_mode"] = getattr(row, f"fifo_chain_step{step}_destination_mode", None) or "configured_area"
        result[f"fifo_chain_step{step}_destination_area_ids_json"] = getattr(row, f"fifo_chain_step{step}_destination_area_ids_json", None)
    return result


def _validate_fifo_chain_total_steps_input(value) -> int:
    if value in (None, ""):
        raise HTTPException(status_code=400, detail="fifo_chain_total_steps debe ser 2, 3, 4 o 5")
    try:
        total = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="fifo_chain_total_steps debe ser 2, 3, 4 o 5")
    return _validate_choice(total, VALID_FIFO_CHAIN_TOTAL_STEPS, "fifo_chain_total_steps")


def _clear_fifo_chain_step5_if_unused(row) -> None:
    if _normalize_fifo_chain_total_steps(getattr(row, "fifo_chain_total_steps", None)) >= 5:
        return
    row.fifo_chain_step5_source_mode = "configured_area"
    row.fifo_chain_step5_material_group_id = None
    row.fifo_chain_step5_task_typ = None
    row.fifo_chain_step5_source_area_id = None
    row.fifo_chain_step5_source_cell_id = None
    row.fifo_chain_step5_destination_area_id = None
    row.fifo_chain_step5_destination_cell_id = None


def _clean_task_typ_value(value) -> Optional[str]:
    text = str(value or "").strip()
    return text[:64] or None


def _apply_fifo_chain_task_types(row, body) -> None:
    route_mode = _normalize_route_mode(getattr(row, "route_mode", None))
    total_steps = _normalize_fifo_chain_total_steps(getattr(row, "fifo_chain_total_steps", None))
    for step in range(1, 6):
        applies = route_mode == "fifo_chain" and step <= total_steps
        value = _clean_task_typ_value(getattr(body, f"fifo_chain_step{step}_task_typ", None))
        setattr(row, f"fifo_chain_step{step}_task_typ", value if applies else None)


VALID_CANCEL_RETURN_MODES = {"configured_area", "current_step_source"}


def _normalize_cancel_return_mode(value) -> str:
    mode = str(value or "").strip().lower()
    if not mode:
        return "configured_area"
    if mode not in VALID_CANCEL_RETURN_MODES:
        raise ValueError("cancel_return_mode debe ser configured_area o current_step_source")
    return mode


class ScannerStationIn(BaseModel):
    scanner_code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    station_type: str = Field(default="generic", min_length=1, max_length=64)
    default_action: str = Field(default="preview_only", min_length=1, max_length=64)
    source_area_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    route_mode: str = Field(default="simple_area", min_length=1, max_length=32)
    fifo_material_policy: str = Field(default="specific_material", min_length=1, max_length=32)
    fifo_chain_total_steps: Optional[Union[int, str]] = Field(default=2)
    fifo_chain_step1_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step1_material_group_id: Optional[int] = None
    fifo_chain_step1_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step1_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step1_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step1_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step2_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step2_material_group_id: Optional[int] = None
    fifo_chain_step2_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step2_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step2_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step2_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step3_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step3_material_group_id: Optional[int] = None
    fifo_chain_step3_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step3_source_area_id: Optional[int] = None
    fifo_chain_step3_source_cell_id: Optional[int] = None
    fifo_chain_step3_destination_area_id: Optional[int] = None
    fifo_chain_step3_destination_cell_id: Optional[int] = None
    fifo_chain_step3_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step3_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step3_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step4_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step5_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step4_material_group_id: Optional[int] = None
    fifo_chain_step5_material_group_id: Optional[int] = None
    fifo_chain_step4_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step5_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step4_source_area_id: Optional[int] = None
    fifo_chain_step5_source_area_id: Optional[int] = None
    fifo_chain_step4_source_cell_id: Optional[int] = None
    fifo_chain_step5_source_cell_id: Optional[int] = None
    fifo_chain_step4_destination_area_id: Optional[int] = None
    fifo_chain_step5_destination_area_id: Optional[int] = None
    fifo_chain_step4_destination_cell_id: Optional[int] = None
    fifo_chain_step4_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step4_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step4_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step5_destination_cell_id: Optional[int] = None
    fifo_chain_step5_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step5_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step5_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    second_source_area_id: Optional[int] = None
    second_destination_area_id: Optional[int] = None
    second_source_cell_id: Optional[int] = None
    second_destination_cell_id: Optional[int] = None
    storage_area_id: Optional[int] = None
    empty_rack_area_id: Optional[int] = None
    cancel_return_area_id: Optional[int] = None
    cancel_return_mode: str = Field(default="configured_area", max_length=32)
    default_material_group_id: Optional[int] = None
    agv_code: Optional[str] = Field(default=None, max_length=128)
    task_typ: Optional[str] = Field(default=None, max_length=64)
    priority: int = Field(default=0, ge=0, le=9999)
    require_preview: int = Field(default=0, ge=0, le=1)
    allow_execute: int = Field(default=1, ge=0, le=1)
    allow_same_cell_move: int = Field(default=0, ge=0, le=1)
    is_active: int = Field(default=1, ge=0, le=1)

    @field_validator("cancel_return_mode")
    @classmethod
    def validate_cancel_return_mode(cls, value: str):
        return _normalize_cancel_return_mode(value)


class ScannerStationOut(ScannerStationIn):
    id: int
    source_area_name: Optional[str] = None
    destination_area_name: Optional[str] = None
    source_cell_code: Optional[str] = None
    destination_cell_code: Optional[str] = None
    second_source_area_name: Optional[str] = None
    second_source_area_code: Optional[str] = None
    second_destination_area_name: Optional[str] = None
    second_destination_area_code: Optional[str] = None
    second_source_cell_code: Optional[str] = None
    second_destination_cell_code: Optional[str] = None
    fifo_chain_step1_material_group_name: Optional[str] = None
    fifo_chain_step2_material_group_name: Optional[str] = None
    fifo_chain_step3_material_group_name: Optional[str] = None
    fifo_chain_step3_source_area_name: Optional[str] = None
    fifo_chain_step3_source_cell_code: Optional[str] = None
    fifo_chain_step3_destination_area_name: Optional[str] = None
    fifo_chain_step3_destination_cell_code: Optional[str] = None
    fifo_chain_step4_material_group_name: Optional[str] = None
    fifo_chain_step5_material_group_name: Optional[str] = None
    fifo_chain_step4_source_area_name: Optional[str] = None
    fifo_chain_step5_source_area_name: Optional[str] = None
    fifo_chain_step4_source_cell_code: Optional[str] = None
    fifo_chain_step5_source_cell_code: Optional[str] = None
    fifo_chain_step4_destination_area_name: Optional[str] = None
    fifo_chain_step5_destination_area_name: Optional[str] = None
    fifo_chain_step4_destination_cell_code: Optional[str] = None
    fifo_chain_step5_destination_cell_code: Optional[str] = None
    storage_area_name: Optional[str] = None
    empty_rack_area_name: Optional[str] = None
    cancel_return_area_code: Optional[str] = None
    cancel_return_area_name: Optional[str] = None
    cancel_return_area_matter_area: Optional[str] = None
    cancel_return_area_is_active: Optional[int] = None
    default_material_group_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class QrActionRuleIn(BaseModel):
    qr_value: str = Field(min_length=1, max_length=256)
    automatic_priority: bool = False
    qr_alias: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    qr_type: str = Field(default="generic", min_length=1, max_length=64)
    match_type: str = Field(default="exact", min_length=1, max_length=32)
    action_type: str = Field(default="use_scanner_default", min_length=1, max_length=64)
    material_group_id: Optional[int] = None
    rack_id: Optional[int] = None
    source_area_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    route_mode: str = Field(default="simple_area", min_length=1, max_length=32)
    fifo_material_policy: str = Field(default="specific_material", min_length=1, max_length=32)
    fifo_chain_total_steps: Optional[Union[int, str]] = Field(default=2)
    fifo_chain_step1_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step1_material_group_id: Optional[int] = None
    fifo_chain_step1_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step1_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step1_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step1_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step2_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step2_material_group_id: Optional[int] = None
    fifo_chain_step2_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step2_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step2_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step2_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step3_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step3_material_group_id: Optional[int] = None
    fifo_chain_step3_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step3_source_area_id: Optional[int] = None
    fifo_chain_step3_source_cell_id: Optional[int] = None
    fifo_chain_step3_destination_area_id: Optional[int] = None
    fifo_chain_step3_destination_cell_id: Optional[int] = None
    fifo_chain_step3_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step3_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step3_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step4_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step5_source_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step4_material_group_id: Optional[int] = None
    fifo_chain_step5_material_group_id: Optional[int] = None
    fifo_chain_step4_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step5_task_typ: Optional[str] = Field(default=None, max_length=64)
    fifo_chain_step4_source_area_id: Optional[int] = None
    fifo_chain_step5_source_area_id: Optional[int] = None
    fifo_chain_step4_source_cell_id: Optional[int] = None
    fifo_chain_step5_source_cell_id: Optional[int] = None
    fifo_chain_step4_destination_area_id: Optional[int] = None
    fifo_chain_step5_destination_area_id: Optional[int] = None
    fifo_chain_step4_destination_cell_id: Optional[int] = None
    fifo_chain_step4_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step4_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step4_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step5_destination_cell_id: Optional[int] = None
    fifo_chain_step5_source_area_ids_json: Optional[Union[str, list[int]]] = None
    fifo_chain_step5_destination_mode: str = Field(default="configured_area", min_length=1, max_length=32)
    fifo_chain_step5_destination_area_ids_json: Optional[Union[str, list[int]]] = None
    second_source_area_id: Optional[int] = None
    second_destination_area_id: Optional[int] = None
    second_source_cell_id: Optional[int] = None
    second_destination_cell_id: Optional[int] = None
    priority: Optional[int] = Field(default=None, ge=0, le=9999)
    task_typ: Optional[str] = Field(default=None, max_length=64)
    agv_code: Optional[str] = Field(default=None, max_length=128)
    requires_scanner_station: int = Field(default=1, ge=0, le=1)
    allow_same_cell_move: int = Field(default=0, ge=0, le=1)
    is_active: int = Field(default=1, ge=0, le=1)


class QrActionRuleOut(QrActionRuleIn):
    id: int
    material_group_name: Optional[str] = None
    rack_code: Optional[str] = None
    source_area_name: Optional[str] = None
    destination_area_name: Optional[str] = None
    source_cell_code: Optional[str] = None
    destination_cell_code: Optional[str] = None
    second_source_area_name: Optional[str] = None
    second_source_area_code: Optional[str] = None
    second_destination_area_name: Optional[str] = None
    second_destination_area_code: Optional[str] = None
    second_source_cell_code: Optional[str] = None
    second_destination_cell_code: Optional[str] = None
    fifo_chain_step1_material_group_name: Optional[str] = None
    fifo_chain_step2_material_group_name: Optional[str] = None
    fifo_chain_step3_material_group_name: Optional[str] = None
    fifo_chain_step3_source_area_name: Optional[str] = None
    fifo_chain_step3_source_cell_code: Optional[str] = None
    fifo_chain_step3_destination_area_name: Optional[str] = None
    fifo_chain_step3_destination_cell_code: Optional[str] = None
    fifo_chain_step4_material_group_name: Optional[str] = None
    fifo_chain_step5_material_group_name: Optional[str] = None
    fifo_chain_step4_source_area_name: Optional[str] = None
    fifo_chain_step5_source_area_name: Optional[str] = None
    fifo_chain_step4_source_cell_code: Optional[str] = None
    fifo_chain_step5_source_cell_code: Optional[str] = None
    fifo_chain_step4_destination_area_name: Optional[str] = None
    fifo_chain_step5_destination_area_name: Optional[str] = None
    fifo_chain_step4_destination_cell_code: Optional[str] = None
    fifo_chain_step5_destination_cell_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class QrTransitionRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    qr_action_rule_id: Optional[int] = None
    scanner_station_id: Optional[int] = None
    source_area_id: Optional[int] = None
    destination_area_id: Optional[int] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
    current_material_group_id: Optional[int] = None
    current_rack_status: Optional[str] = Field(default=None, max_length=32)
    next_material_group_id: Optional[int] = None
    next_rack_status: Optional[str] = Field(default=None, max_length=32)
    next_quantity: Optional[int] = Field(default=None, ge=0)
    clear_quantity: int = Field(default=0, ge=0, le=1)
    next_comment: Optional[str] = Field(default=None, max_length=512)
    append_comment: int = Field(default=1, ge=0, le=1)
    apply_on: str = Field(default="movement_completed", min_length=1, max_length=64)
    scope: str = Field(default="qr_pda", min_length=1, max_length=64)
    match_mode: str = Field(default="advanced", min_length=1, max_length=64)
    source_match_mode: str = Field(default="configured_source", min_length=1, max_length=64)
    ignore_current_material: int = Field(default=0, ge=0, le=1)
    priority: int = Field(default=0, ge=0, le=9999)
    is_active: int = Field(default=1, ge=0, le=1)


class QrTransitionRuleValidateIn(QrTransitionRuleIn):
    rule_id: Optional[int] = None


class QrTransitionRuleOut(QrTransitionRuleIn):
    id: int
    qr_action_rule_value: Optional[str] = None
    qr_action_rule_alias: Optional[str] = None
    scanner_station_code: Optional[str] = None
    scanner_station_name: Optional[str] = None
    source_area_code: Optional[str] = None
    source_area_name: Optional[str] = None
    destination_area_code: Optional[str] = None
    destination_area_name: Optional[str] = None
    source_cell_code: Optional[str] = None
    destination_cell_code: Optional[str] = None
    current_material_group_code: Optional[str] = None
    current_material_group_name: Optional[str] = None
    next_material_group_code: Optional[str] = None
    next_material_group_name: Optional[str] = None
    applied_count: int = 0
    last_applied_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class QrTransitionLogOut(BaseModel):
    id: int
    transition_rule_id: Optional[int] = None
    transition_rule_name: Optional[str] = None
    movement_order_id: Optional[int] = None
    order_code: Optional[str] = None
    movement_order_code: Optional[str] = None
    remote_task_code: Optional[str] = None
    scan_event_id: Optional[int] = None
    qr_value: Optional[str] = None
    qr_action_rule_id: Optional[int] = None
    qr_alias: Optional[str] = None
    qr_action_rule_name: Optional[str] = None
    scanner_station_id: Optional[int] = None
    scanner_code: Optional[str] = None
    scanner_name: Optional[str] = None
    origin_scanner_code: Optional[str] = None
    origin_scanner_name: Optional[str] = None
    rule_scanner_station_id: Optional[int] = None
    rule_scanner_code: Optional[str] = None
    rule_scanner_name: Optional[str] = None
    terminal_code: Optional[str] = None
    execution_id: Optional[str] = None
    selected_match_tier: Optional[int] = None
    selection_reason: Optional[str] = None
    priority: Optional[int] = None
    specificity: Optional[int] = None
    specificity_source: str = "unavailable"
    direct_qr_match: Optional[bool] = None
    direct_scanner_match: Optional[bool] = None
    rack_id: Optional[int] = None
    rack_code: Optional[str] = None
    previous_material_group_id: Optional[int] = None
    previous_material_group_name: Optional[str] = None
    next_material_group_id: Optional[int] = None
    next_material_group_name: Optional[str] = None
    previous_rack_status: Optional[str] = None
    next_rack_status: Optional[str] = None
    previous_quantity: Optional[int] = None
    next_quantity: Optional[int] = None
    previous_comment: Optional[str] = None
    next_comment: Optional[str] = None
    status: str
    message: Optional[str] = None
    created_at: datetime


class PdaQrRuleOut(BaseModel):
    id: int
    qr_value: str
    qr_alias: Optional[str] = None
    description: Optional[str] = None
    qr_type: str
    match_type: str
    action_type: str
    is_active: bool
    requires_scanner_station: bool
    material_group_name: Optional[str] = None
    source_area_name: Optional[str] = None
    destination_area_name: Optional[str] = None


class PdaScanHistoryItemOut(BaseModel):
    scan_event_id: int
    created_at: datetime
    terminal_code: Optional[str] = None
    scanner_code: Optional[str] = None
    qr_value: Optional[str] = None
    mode: str
    scan_status: str
    error_message: Optional[str] = None
    movement_order_id: Optional[int] = None
    order_code: Optional[str] = None
    rack_id: Optional[int] = None
    rack_code: Optional[str] = None
    source_cell_id: Optional[int] = None
    source_cell_code: Optional[str] = None
    source_area_id: Optional[int] = None
    source_area_code: Optional[str] = None
    source_area_name: Optional[str] = None
    destination_cell_id: Optional[int] = None
    destination_cell_code: Optional[str] = None
    destination_area_id: Optional[int] = None
    destination_area_code: Optional[str] = None
    destination_area_name: Optional[str] = None
    route_mode: Optional[str] = None
    fifo_chain_group_id: Optional[str] = None
    fifo_chain_step: Optional[int] = None
    fifo_chain_total_steps: Optional[int] = None
    fifo_chain_parent_order_id: Optional[int] = None
    fifo_chain_status: Optional[str] = None
    status: Optional[str] = None
    dispatch_status: Optional[str] = None
    rcs_status: Optional[str] = None
    rcs_message: Optional[str] = None
    can_undo: bool = False
    action_available: bool = False
    action_unavailable_reason: Optional[str] = None
    cancel_return_mode: Optional[str] = None
    cancel_return_area_id: Optional[int] = None
    cancel_return_area_code: Optional[str] = None
    cancel_return_area_name: Optional[str] = None
    cancel_return_area_matter_area: Optional[str] = None
    cancel_return_area_is_active: Optional[int] = None


class PdaMovementOrderCancelIn(BaseModel):
    action: str = Field(min_length=1, max_length=32)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str):
        normalized = (value or "").strip().lower()
        if normalized not in {"cancel", "cancel_return"}:
            raise ValueError("action debe ser cancel o cancel_return")
        return normalized


class PdaMovementOrderCancelOut(BaseModel):
    success: bool
    action: str
    movement_order_id: int
    status: str
    dispatch_status: Optional[str] = None
    rcs_status: Optional[str] = None
    message: Optional[str] = None
    can_undo: bool = False
    cancel_return_mode: Optional[str] = None
    return_area_id: Optional[int] = None
    return_area_name: Optional[str] = None
    return_source_cell_code: Optional[str] = None
    matter_area: Optional[str] = None


class ScanTerminalIn(BaseModel):
    terminal_code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    scanner_station_id: int
    api_key: Optional[str] = Field(default=None, max_length=256)
    mode: str = Field(default="preview", min_length=1, max_length=32)
    allow_execute: int = Field(default=0, ge=0, le=1)
    require_preview: int = Field(default=1, ge=0, le=1)
    is_active: int = Field(default=1, ge=0, le=1)


class ScanTerminalOut(ScanTerminalIn):
    id: int
    scanner_code: Optional[str] = None
    scanner_station_name: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    last_ip: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ScanTerminalScannerConfigOut(BaseModel):
    scanner_code: str
    scanner_name: str
    is_active: bool
    allow_execute: bool


class ScanTerminalConfigOut(BaseModel):
    terminal_code: str
    terminal_name: str
    is_active: bool
    mode: str
    allow_execute: bool
    require_preview: bool
    scanner: ScanTerminalScannerConfigOut


class MovementOrderOut(BaseModel):
    updated_at: Optional[datetime] = None
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
    rack_id: Optional[int] = None
    rack_code: Optional[str] = None
    rack_missing: bool = False
    material_group_id: Optional[int] = None
    material_group_name: Optional[str] = None
    source_area_id: Optional[int] = None
    source_area_name: Optional[str] = None
    destination_area_id: Optional[int] = None
    destination_area_name: Optional[str] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
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
    rcs_status_changed_at: Optional[datetime] = None
    status_query_request_payload: Optional[dict] = None
    status_query_response_payload: Optional[dict] = None
    status_query_log_entries: List[dict] = []
    route_mode: str = "simple_area"
    route_points: Optional[List[dict]] = None
    trmx_group_id: Optional[str] = None
    trmx_step: Optional[int] = None
    trmx_total_steps: Optional[int] = None
    trmx_parent_order_id: Optional[int] = None
    trmx_status: Optional[str] = None
    trmx_select_policy: Optional[str] = None
    fifo_chain_group_id: Optional[str] = None
    fifo_chain_step: Optional[int] = None
    fifo_chain_total_steps: Optional[int] = None
    fifo_chain_parent_order_id: Optional[int] = None
    fifo_chain_status: Optional[str] = None
    fifo_chain_select_policy: Optional[str] = None
    can_simulate_complete: bool = False
    can_undo: bool = False


class MovementOrderSummaryOut(BaseModel):
    updated_at: Optional[datetime] = None
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
    rack_id: Optional[int] = None
    rack_code: Optional[str] = None
    rack_missing: bool = False
    material_group_id: Optional[int] = None
    material_group_name: Optional[str] = None
    source_area_id: Optional[int] = None
    source_area_name: Optional[str] = None
    destination_area_id: Optional[int] = None
    destination_area_name: Optional[str] = None
    source_cell_id: Optional[int] = None
    destination_cell_id: Optional[int] = None
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
    rcs_status_changed_at: Optional[datetime] = None
    route_mode: str = "simple_area"
    trmx_group_id: Optional[str] = None
    trmx_step: Optional[int] = None
    trmx_total_steps: Optional[int] = None
    trmx_parent_order_id: Optional[int] = None
    trmx_status: Optional[str] = None
    trmx_select_policy: Optional[str] = None
    fifo_chain_group_id: Optional[str] = None
    fifo_chain_step: Optional[int] = None
    fifo_chain_total_steps: Optional[int] = None
    fifo_chain_parent_order_id: Optional[int] = None
    fifo_chain_status: Optional[str] = None
    fifo_chain_select_policy: Optional[str] = None
    can_simulate_complete: bool = False
    can_undo: bool = False


class MovementOrderDetailOut(MovementOrderOut):
    dispatch_request_payload: Optional[dict] = None
    dispatch_response_payload: Optional[dict] = None
    rcs_response_payload: Optional[dict] = None
    override_payload: Optional[dict] = None


class MovementOrderPageOut(BaseModel):
    items: List[MovementOrderSummaryOut]
    total: int
    limit: int
    offset: int




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
    orders: List[MovementOrderSummaryOut] = []
    orders_total: int = 0
    recent_order_limit: int = RUNTIME_RECENT_ORDER_LIMIT
    locations: List[LocationOut] = []
    debug_log: List[DebugConsoleEventOut] = []
    robot_monitor: Optional["RobotMonitorResponse"] = None


class ConfigAuditLogOut(BaseModel):
    id: int
    created_at: datetime
    table_name: str
    record_key: str
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    source: str
    endpoint: Optional[str] = None
    client_ip: Optional[str] = None
    request_id: Optional[str] = None
    related_order_id: Optional[int] = None
    related_qr_rule_id: Optional[int] = None
    note: Optional[str] = None


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()



def get_setting(db, key: str, default: str) -> str:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return row.value if row else default



def _audit_config_change(
    db,
    *,
    table_name: str,
    record_key: str,
    field_name: str,
    old_value,
    new_value,
    source: Optional[str] = None,
    endpoint: Optional[str] = None,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    related_order_id: Optional[int] = None,
    related_qr_rule_id: Optional[int] = None,
    note: Optional[str] = None,
):
    context = _config_audit_context()
    source = source or context.get("source") or "unknown"
    endpoint = endpoint if endpoint is not None else context.get("endpoint")
    client_ip = client_ip if client_ip is not None else context.get("client_ip")
    request_id = request_id if request_id is not None else context.get("request_id")
    related_order_id = related_order_id if related_order_id is not None else context.get("related_order_id")
    related_qr_rule_id = related_qr_rule_id if related_qr_rule_id is not None else context.get("related_qr_rule_id")
    note = note if note is not None else context.get("note")
    if sensitive_key(record_key) or sensitive_key(field_name):
        sanitize({"secret": [old_value, new_value]})
        old_value = "[REDACTED]" if old_value else old_value
        new_value = "[REDACTED]" if new_value else new_value
    else:
        old_value, new_value = sanitize(old_value), sanitize(new_value)
    logger.info(
        "CONFIG_CHANGE table=%s key=%s field=%s old=%r new=%r source=%s endpoint=%s client_ip=%s request_id=%s related_order_id=%s related_qr_rule_id=%s note=%s",
        table_name,
        record_key,
        field_name,
        old_value,
        new_value,
        source,
        endpoint or "-",
        client_ip or "-",
        request_id or "-",
        related_order_id if related_order_id is not None else "-",
        related_qr_rule_id if related_qr_rule_id is not None else "-",
        note or "-",
    )
    db.add(ConfigAuditLog(
        table_name=table_name,
        record_key=str(record_key),
        field_name=str(field_name),
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
        source=str(source or ""),
        endpoint=endpoint,
        client_ip=client_ip,
        request_id=request_id,
        related_order_id=related_order_id,
        related_qr_rule_id=related_qr_rule_id,
        note=note,
        created_at=datetime.utcnow(),
    ))


def set_setting(db, key: str, value: str, *, source: Optional[str] = None, note: Optional[str] = None):
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    new_value = "" if value is None else str(value)
    old_value = row.value if row else None
    changed = old_value != new_value
    if not row:
        row = Setting(key=key, value=new_value, updated_at=datetime.utcnow())
        db.add(row)
    else:
        row.value = new_value
        row.updated_at = datetime.utcnow()
        db.add(row)
    if changed:
        _audit_config_change(
            db,
            table_name="settings",
            record_key=key,
            field_name="value",
            old_value=old_value,
            new_value=new_value,
            source=source,
            note=note,
        )
    db.commit()


def _snapshot_fields(row, field_names: List[str]) -> dict:
    return {name: getattr(row, name, None) for name in field_names}


def _audit_model_field_changes(
    db,
    *,
    table_name: str,
    record_key: str,
    old_values: dict,
    row,
    field_names: List[str],
    source: str,
    note: Optional[str] = None,
    related_qr_rule_id: Optional[int] = None,
):
    for field_name in field_names:
        old_value = old_values.get(field_name)
        new_value = getattr(row, field_name, None)
        if old_value != new_value:
            _audit_config_change(
                db,
                table_name=table_name,
                record_key=record_key,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                source=source,
                note=note,
                related_qr_rule_id=related_qr_rule_id,
            )



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
        if "scanner_stations" in tables:
            try:
                scanner_station_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(scanner_stations);").fetchall()]
                if "cancel_return_area_id" not in scanner_station_cols:
                    conn.exec_driver_sql("ALTER TABLE scanner_stations ADD COLUMN cancel_return_area_id INTEGER NULL;")
                    logger.info("SQLite migration added scanner_stations.cancel_return_area_id")
                else:
                    logger.info("SQLite migration scanner_stations.cancel_return_area_id already exists")
                if "cancel_return_mode" not in scanner_station_cols:
                    conn.exec_driver_sql("ALTER TABLE scanner_stations ADD COLUMN cancel_return_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';")
                    logger.info("SQLite migration added scanner_stations.cancel_return_mode")
                scanner_route_missing_sql = {
                    "route_mode": "ALTER TABLE scanner_stations ADD COLUMN route_mode VARCHAR(32) NOT NULL DEFAULT 'simple_area';",
                    "fifo_material_policy": "ALTER TABLE scanner_stations ADD COLUMN fifo_material_policy VARCHAR(32) NOT NULL DEFAULT 'specific_material';",
                    "fifo_chain_total_steps": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_total_steps INTEGER NOT NULL DEFAULT 2;",
                    "fifo_chain_step1_source_mode": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step1_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step1_material_group_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step1_material_group_id INTEGER;",
                    "fifo_chain_step2_source_mode": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step2_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step2_material_group_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step2_material_group_id INTEGER;",
                    "fifo_chain_step3_source_mode": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step3_material_group_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_material_group_id INTEGER;",
                    "fifo_chain_step3_source_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_source_area_id INTEGER;",
                    "fifo_chain_step3_source_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_source_cell_id INTEGER;",
                    "fifo_chain_step3_destination_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_destination_area_id INTEGER;",
                    "fifo_chain_step3_destination_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step3_destination_cell_id INTEGER;",
                    "fifo_chain_step4_source_mode": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step5_source_mode": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step4_material_group_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_material_group_id INTEGER;",
                    "fifo_chain_step5_material_group_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_material_group_id INTEGER;",
                    "fifo_chain_step4_source_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_source_area_id INTEGER;",
                    "fifo_chain_step5_source_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_source_area_id INTEGER;",
                    "fifo_chain_step4_source_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_source_cell_id INTEGER;",
                    "fifo_chain_step5_source_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_source_cell_id INTEGER;",
                    "fifo_chain_step4_destination_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_destination_area_id INTEGER;",
                    "fifo_chain_step5_destination_area_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_destination_area_id INTEGER;",
                    "fifo_chain_step4_destination_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step4_destination_cell_id INTEGER;",
                    "fifo_chain_step5_destination_cell_id": "ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step5_destination_cell_id INTEGER;",
                    "allow_same_cell_move": "ALTER TABLE scanner_stations ADD COLUMN allow_same_cell_move INTEGER NOT NULL DEFAULT 0;",
                    "second_source_area_id": "ALTER TABLE scanner_stations ADD COLUMN second_source_area_id INTEGER;",
                    "second_destination_area_id": "ALTER TABLE scanner_stations ADD COLUMN second_destination_area_id INTEGER;",
                    "second_source_cell_id": "ALTER TABLE scanner_stations ADD COLUMN second_source_cell_id INTEGER;",
                    "second_destination_cell_id": "ALTER TABLE scanner_stations ADD COLUMN second_destination_cell_id INTEGER;",
                }
                for step in range(1, 6):
                    scanner_route_missing_sql.update({
                        f"fifo_chain_step{step}_task_typ": f"ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step{step}_task_typ VARCHAR(64);",
                        f"fifo_chain_step{step}_source_area_ids_json": f"ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step{step}_source_area_ids_json TEXT;",
                        f"fifo_chain_step{step}_destination_mode": f"ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step{step}_destination_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                        f"fifo_chain_step{step}_destination_area_ids_json": f"ALTER TABLE scanner_stations ADD COLUMN fifo_chain_step{step}_destination_area_ids_json TEXT;",
                    })
                for name, stmt in scanner_route_missing_sql.items():
                    if name not in scanner_station_cols:
                        conn.exec_driver_sql(stmt)
                        logger.info("SQLite migration added scanner_stations.%s", name)
                scanner_station_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(scanner_stations);").fetchall()]
                if "route_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET route_mode = 'fifo_chain' WHERE route_mode = 'trmx_doble';")
                if "fifo_chain_step1_source_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_step1_source_mode = 'configured_area' WHERE fifo_chain_step1_source_mode IS NULL OR trim(fifo_chain_step1_source_mode) = '';")
                if "fifo_chain_step2_source_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_step2_source_mode = 'configured_area' WHERE fifo_chain_step2_source_mode IS NULL OR trim(fifo_chain_step2_source_mode) = '';")
                if "fifo_chain_step3_source_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_step3_source_mode = 'configured_area' WHERE fifo_chain_step3_source_mode IS NULL OR trim(fifo_chain_step3_source_mode) = '';")
                if "fifo_chain_step4_source_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_step4_source_mode = 'configured_area' WHERE fifo_chain_step4_source_mode IS NULL OR trim(fifo_chain_step4_source_mode) = '';")
                if "fifo_chain_step5_source_mode" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_step5_source_mode = 'configured_area' WHERE fifo_chain_step5_source_mode IS NULL OR trim(fifo_chain_step5_source_mode) = '';")
                if "fifo_chain_total_steps" in scanner_station_cols:
                    conn.exec_driver_sql("UPDATE scanner_stations SET fifo_chain_total_steps = 2 WHERE fifo_chain_total_steps IS NULL OR fifo_chain_total_steps NOT IN (2, 3, 4, 5);")
            except Exception:
                logger.exception("SQLite migration failed for scanner_stations route columns")
                raise

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "qr_action_rules" in tables:
            try:
                qr_action_rule_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(qr_action_rules);").fetchall()]
                qr_route_missing_sql = {
                    "automatic_priority": "ALTER TABLE qr_action_rules ADD COLUMN automatic_priority BOOLEAN NOT NULL DEFAULT 0;",
                    "route_mode": "ALTER TABLE qr_action_rules ADD COLUMN route_mode VARCHAR(32) NOT NULL DEFAULT 'simple_area';",
                    "fifo_material_policy": "ALTER TABLE qr_action_rules ADD COLUMN fifo_material_policy VARCHAR(32) NOT NULL DEFAULT 'specific_material';",
                    "fifo_chain_total_steps": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_total_steps INTEGER NOT NULL DEFAULT 2;",
                    "fifo_chain_step1_source_mode": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step1_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step1_material_group_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step1_material_group_id INTEGER;",
                    "fifo_chain_step2_source_mode": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step2_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step2_material_group_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step2_material_group_id INTEGER;",
                    "fifo_chain_step3_source_mode": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step3_material_group_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_material_group_id INTEGER;",
                    "fifo_chain_step3_source_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_source_area_id INTEGER;",
                    "fifo_chain_step3_source_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_source_cell_id INTEGER;",
                    "fifo_chain_step3_destination_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_destination_area_id INTEGER;",
                    "fifo_chain_step3_destination_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step3_destination_cell_id INTEGER;",
                    "fifo_chain_step4_source_mode": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step5_source_mode": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_source_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                    "fifo_chain_step4_material_group_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_material_group_id INTEGER;",
                    "fifo_chain_step5_material_group_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_material_group_id INTEGER;",
                    "fifo_chain_step4_source_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_source_area_id INTEGER;",
                    "fifo_chain_step5_source_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_source_area_id INTEGER;",
                    "fifo_chain_step4_source_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_source_cell_id INTEGER;",
                    "fifo_chain_step5_source_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_source_cell_id INTEGER;",
                    "fifo_chain_step4_destination_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_destination_area_id INTEGER;",
                    "fifo_chain_step5_destination_area_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_destination_area_id INTEGER;",
                    "fifo_chain_step4_destination_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step4_destination_cell_id INTEGER;",
                    "fifo_chain_step5_destination_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step5_destination_cell_id INTEGER;",
                    "allow_same_cell_move": "ALTER TABLE qr_action_rules ADD COLUMN allow_same_cell_move INTEGER NOT NULL DEFAULT 0;",
                    "second_source_area_id": "ALTER TABLE qr_action_rules ADD COLUMN second_source_area_id INTEGER;",
                    "second_destination_area_id": "ALTER TABLE qr_action_rules ADD COLUMN second_destination_area_id INTEGER;",
                    "second_source_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN second_source_cell_id INTEGER;",
                    "second_destination_cell_id": "ALTER TABLE qr_action_rules ADD COLUMN second_destination_cell_id INTEGER;",
                }
                for step in range(1, 6):
                    qr_route_missing_sql.update({
                        f"fifo_chain_step{step}_task_typ": f"ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step{step}_task_typ VARCHAR(64);",
                        f"fifo_chain_step{step}_source_area_ids_json": f"ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step{step}_source_area_ids_json TEXT;",
                        f"fifo_chain_step{step}_destination_mode": f"ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step{step}_destination_mode VARCHAR(32) NOT NULL DEFAULT 'configured_area';",
                        f"fifo_chain_step{step}_destination_area_ids_json": f"ALTER TABLE qr_action_rules ADD COLUMN fifo_chain_step{step}_destination_area_ids_json TEXT;",
                    })
                for name, stmt in qr_route_missing_sql.items():
                    if name not in qr_action_rule_cols:
                        conn.exec_driver_sql(stmt)
                        logger.info("SQLite migration added qr_action_rules.%s", name)
                qr_action_rule_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(qr_action_rules);").fetchall()]
                if "route_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET route_mode = 'fifo_chain' WHERE route_mode = 'trmx_doble';")
                if "fifo_chain_step1_source_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_step1_source_mode = 'configured_area' WHERE fifo_chain_step1_source_mode IS NULL OR trim(fifo_chain_step1_source_mode) = '';")
                if "fifo_chain_step2_source_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_step2_source_mode = 'configured_area' WHERE fifo_chain_step2_source_mode IS NULL OR trim(fifo_chain_step2_source_mode) = '';")
                if "fifo_chain_step3_source_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_step3_source_mode = 'configured_area' WHERE fifo_chain_step3_source_mode IS NULL OR trim(fifo_chain_step3_source_mode) = '';")
                if "fifo_chain_step4_source_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_step4_source_mode = 'configured_area' WHERE fifo_chain_step4_source_mode IS NULL OR trim(fifo_chain_step4_source_mode) = '';")
                if "fifo_chain_step5_source_mode" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_step5_source_mode = 'configured_area' WHERE fifo_chain_step5_source_mode IS NULL OR trim(fifo_chain_step5_source_mode) = '';")
                if "fifo_chain_total_steps" in qr_action_rule_cols:
                    conn.exec_driver_sql("UPDATE qr_action_rules SET fifo_chain_total_steps = 2 WHERE fifo_chain_total_steps IS NULL OR fifo_chain_total_steps NOT IN (2, 3, 4, 5);")
            except Exception:
                logger.exception("SQLite migration failed for qr_action_rules route columns")
                raise


        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS qr_transition_rules (
                id INTEGER PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description VARCHAR(512),
                qr_action_rule_id INTEGER,
                scanner_station_id INTEGER,
                source_area_id INTEGER,
                destination_area_id INTEGER,
                source_cell_id INTEGER,
                destination_cell_id INTEGER,
                current_material_group_id INTEGER,
                current_rack_status VARCHAR(32),
                next_material_group_id INTEGER,
                next_rack_status VARCHAR(32),
                next_quantity INTEGER,
                clear_quantity INTEGER NOT NULL DEFAULT 0,
                next_comment VARCHAR(512),
                append_comment INTEGER NOT NULL DEFAULT 1,
                apply_on VARCHAR(64) NOT NULL DEFAULT 'movement_completed',
                scope VARCHAR(64) NOT NULL DEFAULT 'qr_pda',
                match_mode VARCHAR(64) NOT NULL DEFAULT 'advanced',
                source_match_mode VARCHAR(64) NOT NULL DEFAULT 'configured_source',
                ignore_current_material INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                applied_count INTEGER NOT NULL DEFAULT 0,
                last_applied_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        transition_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(qr_transition_rules);").fetchall()]
        transition_missing_sql = {
            "description": "ALTER TABLE qr_transition_rules ADD COLUMN description VARCHAR(512);",
            "qr_action_rule_id": "ALTER TABLE qr_transition_rules ADD COLUMN qr_action_rule_id INTEGER;",
            "scanner_station_id": "ALTER TABLE qr_transition_rules ADD COLUMN scanner_station_id INTEGER;",
            "source_area_id": "ALTER TABLE qr_transition_rules ADD COLUMN source_area_id INTEGER;",
            "destination_area_id": "ALTER TABLE qr_transition_rules ADD COLUMN destination_area_id INTEGER;",
            "source_cell_id": "ALTER TABLE qr_transition_rules ADD COLUMN source_cell_id INTEGER;",
            "destination_cell_id": "ALTER TABLE qr_transition_rules ADD COLUMN destination_cell_id INTEGER;",
            "current_material_group_id": "ALTER TABLE qr_transition_rules ADD COLUMN current_material_group_id INTEGER;",
            "current_rack_status": "ALTER TABLE qr_transition_rules ADD COLUMN current_rack_status VARCHAR(32);",
            "next_material_group_id": "ALTER TABLE qr_transition_rules ADD COLUMN next_material_group_id INTEGER;",
            "next_rack_status": "ALTER TABLE qr_transition_rules ADD COLUMN next_rack_status VARCHAR(32);",
            "next_quantity": "ALTER TABLE qr_transition_rules ADD COLUMN next_quantity INTEGER;",
            "clear_quantity": "ALTER TABLE qr_transition_rules ADD COLUMN clear_quantity INTEGER NOT NULL DEFAULT 0;",
            "next_comment": "ALTER TABLE qr_transition_rules ADD COLUMN next_comment VARCHAR(512);",
            "append_comment": "ALTER TABLE qr_transition_rules ADD COLUMN append_comment INTEGER NOT NULL DEFAULT 1;",
            "apply_on": "ALTER TABLE qr_transition_rules ADD COLUMN apply_on VARCHAR(64) NOT NULL DEFAULT 'movement_completed';",
            "scope": "ALTER TABLE qr_transition_rules ADD COLUMN scope VARCHAR(64) NOT NULL DEFAULT 'qr_pda';",
            "match_mode": "ALTER TABLE qr_transition_rules ADD COLUMN match_mode VARCHAR(64) NOT NULL DEFAULT 'advanced';",
            "source_match_mode": "ALTER TABLE qr_transition_rules ADD COLUMN source_match_mode VARCHAR(64) NOT NULL DEFAULT 'configured_source';",
            "ignore_current_material": "ALTER TABLE qr_transition_rules ADD COLUMN ignore_current_material INTEGER NOT NULL DEFAULT 0;",
            "priority": "ALTER TABLE qr_transition_rules ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;",
            "is_active": "ALTER TABLE qr_transition_rules ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;",
            "applied_count": "ALTER TABLE qr_transition_rules ADD COLUMN applied_count INTEGER NOT NULL DEFAULT 0;",
            "last_applied_at": "ALTER TABLE qr_transition_rules ADD COLUMN last_applied_at DATETIME;",
            "created_at": "ALTER TABLE qr_transition_rules ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;",
            "updated_at": "ALTER TABLE qr_transition_rules ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;",
        }
        for name, stmt in transition_missing_sql.items():
            if name not in transition_cols:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_is_active ON qr_transition_rules(is_active);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_apply_on ON qr_transition_rules(apply_on);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_qr_action_rule_id ON qr_transition_rules(qr_action_rule_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_scanner_station_id ON qr_transition_rules(scanner_station_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_route ON qr_transition_rules(source_area_id, destination_area_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_cells ON qr_transition_rules(source_cell_id, destination_cell_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_scope ON qr_transition_rules(scope);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_match_mode ON qr_transition_rules(match_mode);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_rules_source_match_mode ON qr_transition_rules(source_match_mode);")
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS qr_transition_logs (
                id INTEGER PRIMARY KEY,
                transition_rule_id INTEGER,
                movement_order_id INTEGER,
                scan_event_id INTEGER,
                rack_id INTEGER,
                previous_material_group_id INTEGER,
                next_material_group_id INTEGER,
                previous_rack_status VARCHAR(32),
                next_rack_status VARCHAR(32),
                previous_quantity INTEGER,
                next_quantity INTEGER,
                previous_comment VARCHAR(512),
                next_comment VARCHAR(512),
                status VARCHAR(32) NOT NULL DEFAULT 'skipped',
                message VARCHAR(512),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        transition_log_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(qr_transition_logs);").fetchall()]
        transition_log_missing_sql = {
            "transition_rule_id": "ALTER TABLE qr_transition_logs ADD COLUMN transition_rule_id INTEGER;",
            "movement_order_id": "ALTER TABLE qr_transition_logs ADD COLUMN movement_order_id INTEGER;",
            "scan_event_id": "ALTER TABLE qr_transition_logs ADD COLUMN scan_event_id INTEGER;",
            "rack_id": "ALTER TABLE qr_transition_logs ADD COLUMN rack_id INTEGER;",
            "previous_material_group_id": "ALTER TABLE qr_transition_logs ADD COLUMN previous_material_group_id INTEGER;",
            "next_material_group_id": "ALTER TABLE qr_transition_logs ADD COLUMN next_material_group_id INTEGER;",
            "previous_rack_status": "ALTER TABLE qr_transition_logs ADD COLUMN previous_rack_status VARCHAR(32);",
            "next_rack_status": "ALTER TABLE qr_transition_logs ADD COLUMN next_rack_status VARCHAR(32);",
            "previous_quantity": "ALTER TABLE qr_transition_logs ADD COLUMN previous_quantity INTEGER;",
            "next_quantity": "ALTER TABLE qr_transition_logs ADD COLUMN next_quantity INTEGER;",
            "previous_comment": "ALTER TABLE qr_transition_logs ADD COLUMN previous_comment VARCHAR(512);",
            "next_comment": "ALTER TABLE qr_transition_logs ADD COLUMN next_comment VARCHAR(512);",
            "status": "ALTER TABLE qr_transition_logs ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'skipped';",
            "message": "ALTER TABLE qr_transition_logs ADD COLUMN message VARCHAR(512);",
            "created_at": "ALTER TABLE qr_transition_logs ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;",
        }
        for name, stmt in transition_log_missing_sql.items():
            if name not in transition_log_cols:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_logs_movement_order_id ON qr_transition_logs(movement_order_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_logs_transition_rule_id ON qr_transition_logs(transition_rule_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_logs_rack_id ON qr_transition_logs(rack_id);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_logs_status ON qr_transition_logs(status);")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_qr_transition_logs_created_at ON qr_transition_logs(created_at);")

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
                "rcs_status_changed_at": "ALTER TABLE movement_orders ADD COLUMN rcs_status_changed_at DATETIME;",
                "status_query_log_json": "ALTER TABLE movement_orders ADD COLUMN status_query_log_json TEXT;",
                "agv_code": "ALTER TABLE movement_orders ADD COLUMN agv_code VARCHAR(128);",
                "task_typ": "ALTER TABLE movement_orders ADD COLUMN task_typ VARCHAR(64);",
                "route_mode": "ALTER TABLE movement_orders ADD COLUMN route_mode VARCHAR(32) NOT NULL DEFAULT 'simple_area';",
                "route_points_json": "ALTER TABLE movement_orders ADD COLUMN route_points_json TEXT;",
                "trmx_group_id": "ALTER TABLE movement_orders ADD COLUMN trmx_group_id VARCHAR(64);",
                "trmx_step": "ALTER TABLE movement_orders ADD COLUMN trmx_step INTEGER;",
                "trmx_total_steps": "ALTER TABLE movement_orders ADD COLUMN trmx_total_steps INTEGER;",
                "trmx_parent_order_id": "ALTER TABLE movement_orders ADD COLUMN trmx_parent_order_id INTEGER;",
                "trmx_status": "ALTER TABLE movement_orders ADD COLUMN trmx_status VARCHAR(32);",
                "trmx_next_config_json": "ALTER TABLE movement_orders ADD COLUMN trmx_next_config_json TEXT;",
                "trmx_select_policy": "ALTER TABLE movement_orders ADD COLUMN trmx_select_policy VARCHAR(32);",
                "fifo_chain_group_id": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_group_id VARCHAR(64);",
                "fifo_chain_step": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_step INTEGER;",
                "fifo_chain_total_steps": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_total_steps INTEGER;",
                "fifo_chain_parent_order_id": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_parent_order_id INTEGER;",
                "fifo_chain_status": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_status VARCHAR(32);",
                "fifo_chain_next_config_json": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_next_config_json TEXT;",
                "fifo_chain_select_policy": "ALTER TABLE movement_orders ADD COLUMN fifo_chain_select_policy VARCHAR(32);",
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
            mo_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(movement_orders);").fetchall()]
            fifo_chain_legacy_pairs = (
                ("fifo_chain_group_id", "trmx_group_id"),
                ("fifo_chain_step", "trmx_step"),
                ("fifo_chain_total_steps", "trmx_total_steps"),
                ("fifo_chain_parent_order_id", "trmx_parent_order_id"),
                ("fifo_chain_status", "trmx_status"),
                ("fifo_chain_next_config_json", "trmx_next_config_json"),
                ("fifo_chain_select_policy", "trmx_select_policy"),
            )
            for new_col, old_col in fifo_chain_legacy_pairs:
                if new_col in mo_cols and old_col in mo_cols:
                    conn.exec_driver_sql(
                        f"UPDATE movement_orders SET {new_col} = {old_col} "
                        f"WHERE ({new_col} IS NULL OR {new_col} = '') AND {old_col} IS NOT NULL;"
                    )
            if "route_mode" in mo_cols:
                conn.exec_driver_sql("UPDATE movement_orders SET route_mode = 'fifo_chain' WHERE route_mode = 'trmx_doble';")

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

        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if "scan_events" in tables:
            scan_event_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(scan_events);").fetchall()]
            if "terminal_id" not in scan_event_cols:
                conn.exec_driver_sql("ALTER TABLE scan_events ADD COLUMN terminal_id INTEGER;")



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


def ensure_config_audit_schema():
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS config_audit_log (
                id INTEGER NOT NULL PRIMARY KEY,
                created_at DATETIME NOT NULL,
                table_name VARCHAR(64) NOT NULL,
                record_key VARCHAR(128) NOT NULL,
                field_name VARCHAR(128) NOT NULL,
                old_value TEXT,
                new_value TEXT,
                source VARCHAR(128) NOT NULL DEFAULT '',
                endpoint VARCHAR(256),
                client_ip VARCHAR(64),
                request_id VARCHAR(64),
                related_order_id INTEGER,
                related_qr_rule_id INTEGER,
                note VARCHAR(512)
            )
            """
        )
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_config_audit_created_at ON config_audit_log (created_at)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_config_audit_record_key ON config_audit_log (record_key)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_config_audit_request_id ON config_audit_log (request_id)")



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
            "rcs_simulator_enabled": "0",
            "rcs_base_url": "",
            "rcs_create_task_endpoint": "/rcs/task/create",
            "rcs_query_task_status_endpoint": "/rcms/services/rest/hikRpcService/queryTaskStatus",
            "rcs_cancel_task_endpoint": "/rcms/services/rest/hikRpcService/cancelTask",
            "rcs_rack_sync_query_endpoint": "/rcms/services/rest/hikRpcService/queryPodBerthAndMat",
            "rcs_rack_sync_bind_endpoint": "/rcms/services/rest/hikRpcService/bindPodAndBerth",
            "rcs_rack_sync_schedule_enabled": "0",
            "rcs_rack_sync_schedule_time": "12:00",
            "rcs_rack_sync_schedule_last_run_date": "",
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
                set_setting(db, k, v, source="startup_defaults", note="ensure_default_settings_missing_or_empty")


ADMIN_TOKENS = {}



def _admin_token_secret() -> str:
    with SessionLocal() as db:
        secret = get_setting(db, "admin_token_secret", "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
            secret = secrets.token_hex(32)
            set_setting(db, "admin_token_secret", secret, source="admin_token_secret_init")
        return secret


def _create_admin_token(expires_hours: int) -> tuple[str, datetime]:
    expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
    payload = f"{int(expires_at.timestamp()):x}.{secrets.token_hex(16)}"
    signature = hmac.new(bytes.fromhex(_admin_token_secret()), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", expires_at


def require_admin(token: Optional[str]):
    token_prefix = token[:6] if token else "-"
    if not token:
        logger.warning("Admin token rejected reason=missing header_present=false token_prefix=-")
        raise HTTPException(status_code=401, detail="Admin token inválido")
    expires_at = ADMIN_TOKENS.get(token)
    if expires_at is None:
        parts = token.split(".")
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-fA-F]+", parts[0]) or not re.fullmatch(r"[0-9a-fA-F]{32}", parts[1]) or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[2]):
            reason = "not_found" if re.fullmatch(r"[0-9a-fA-F]{32}", token) else "malformed"
            logger.warning("Admin token rejected reason=%s header_present=true token_prefix=%s", reason, token_prefix)
            raise HTTPException(status_code=401, detail="Admin token inválido")
        payload = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(bytes.fromhex(_admin_token_secret()), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(parts[2].lower(), expected.lower()):
            logger.warning("Admin token rejected reason=invalid_signature header_present=true token_prefix=%s", token_prefix)
            raise HTTPException(status_code=401, detail="Admin token inválido")
        try:
            expires_at = datetime.utcfromtimestamp(int(parts[0], 16))
        except (OverflowError, OSError, ValueError):
            logger.warning("Admin token rejected reason=malformed header_present=true token_prefix=%s", token_prefix)
            raise HTTPException(status_code=401, detail="Admin token inválido")
        ADMIN_TOKENS[token] = expires_at
    if expires_at < datetime.utcnow():
        ADMIN_TOKENS.pop(token, None)
        logger.warning("Admin token rejected reason=expired header_present=true token_prefix=%s", token_prefix)
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
        state="active" if int(r.is_active or 0) == 1 else "inactive",
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
        state="active" if int(r.is_active or 0) == 1 else "inactive",
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


def _apply_pending_point_inventory(db, order):
    changes = getattr(order, "_pending_point_inventory", None)
    if changes is None:
        return
    rack = db.get(Rack, order.rack_id)
    for name, value in changes.items():
        setattr(rack, name, value)
    rack.updated_at = datetime.utcnow()
    order.material_group_id = rack.material_group_id
    db.add(rack)
    _append_status_query_log(db, order, kind="point_inventory_applied", request_payload=None,
                             response_payload=changes, message="Inventario confirmado junto con aceptación RCS")


def _rack_reservation_order(db, rack_id: Optional[int]):
    if not rack_id:
        return None
    return (
        db.execute(
            select(MovementOrder.id, MovementOrder.remote_task_code, MovementOrder.order_code)
            .where(MovementOrder.rack_id == rack_id, MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))
            .order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())
        )
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



def _catalog_out(db) -> CatalogOut:
    """Build the catalog with a fixed query count, independent of rack count."""
    area_rows = db.execute(
        select(
            Area.id, Area.code, Area.name, Area.description, Area.matter_area,
            Area.color, Area.area_type, Area.is_active, Area.priority, Area.updated_at,
        )
        .where(Area.is_active == 1)
        .order_by(Area.priority.asc(), Area.name.asc())
    ).all()
    material_rows = db.execute(
        select(
            MaterialGroup.id, MaterialGroup.code, MaterialGroup.name,
            MaterialGroup.description, MaterialGroup.color,
            MaterialGroup.is_active, MaterialGroup.updated_at,
        )
        .where(MaterialGroup.is_active == 1)
        .order_by(MaterialGroup.name.asc())
    ).all()
    rack_rows = db.execute(
        select(
            Rack.id, Rack.code, Rack.name, Rack.status, Rack.material_group_id,
            Rack.lot, Rack.manufacturer_code, Rack.quantity, Rack.comment,
            Rack.fifo_entered_at, Rack.last_moved_at, Rack.rack_custom_fields_json,
            Rack.updated_at,
            MaterialGroup.name.label("material_group_name"),
            Location.x.label("location_x"), Location.y.label("location_y"),
            Location.area_id.label("area_id"), Area.name.label("area_name"),
        )
        .outerjoin(Location, Location.rack_id == Rack.id)
        .outerjoin(MaterialGroup, MaterialGroup.id == Rack.material_group_id)
        .outerjoin(Area, Area.id == Location.area_id)
        .order_by(Rack.code.asc())
    ).all()

    rack_ids = [row.id for row in rack_rows]
    latest_order_by_rack = {}
    if rack_ids:
        active_order_rows = db.execute(
            select(
                MovementOrder.id, MovementOrder.rack_id,
                MovementOrder.remote_task_code, MovementOrder.order_code,
            )
            .where(
                MovementOrder.rack_id.in_(rack_ids),
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            )
            .order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())
        ).all()
        for order in active_order_rows:
            latest_order_by_rack.setdefault(order.rack_id, order)

    racks = []
    for row in rack_rows:
        order = latest_order_by_rack.get(row.id)
        is_reserved = bool(order) or rack_status_is_reserved(row.status)
        task_identifier = None
        if order:
            task_identifier = (order.remote_task_code or "").strip() or (order.order_code or "").strip() or str(order.id)
        rack_status = row.status if isinstance(row.status, str) and row.status.strip() else ("occupied" if row.location_x is not None else "free")
        racks.append(
            RackOut(
                id=row.id, code=row.code, name=row.name, status=rack_status,
                material_group_id=row.material_group_id, lot=row.lot,
                manufacturer_code=row.manufacturer_code, quantity=row.quantity,
                comment=row.comment, fifo_entered_at=row.fifo_entered_at,
                last_moved_at=row.last_moved_at,
                custom_fields=_rack_custom_fields_for_row(row), updated_at=row.updated_at,
                material_group_name=row.material_group_name,
                location_x=row.location_x, location_y=row.location_y,
                area_id=row.area_id, area_name=row.area_name,
                reservation_status="Reservado" if is_reserved else "No reservado",
                reservation_task_id=order.id if order else None,
                reservation_task_identifier=task_identifier,
            )
        )
    return CatalogOut(
        areas=[_to_area_out(row) for row in area_rows],
        materials=[_to_material_out(row) for row in material_rows],
        racks=racks,
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
        if int(area.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="El área está inactiva; restáurala antes de asignarla")
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


def _validate_location_can_mark_free(db, row: Location) -> None:
    if row.rack_id is not None:
        rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none()
        rack_label = rack.code if rack and rack.code else row.rack_id
        raise HTTPException(status_code=409, detail=f"No se puede marcar la celda como libre porque tiene asociado el rack {rack_label}.")
    active_order = db.execute(
        select(MovementOrder).where(
            MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            or_(MovementOrder.source_cell_id == row.id, MovementOrder.destination_cell_id == row.id),
        ).order_by(MovementOrder.id.asc())
    ).scalars().first()
    if active_order:
        order_label = active_order.order_code or active_order.id
        raise HTTPException(
            status_code=409,
            detail=f"No se puede marcar la celda como libre porque participa en la orden activa {order_label}. Cierra o recupera primero la orden desde Diagnóstico y limpieza.",
        )


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
    rack = rack_by_id.get(row.rack_id) if rack_by_id is not None and row.rack_id else (db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none() if row.rack_id else None)
    source_cell = location_by_id.get(row.source_cell_id) if location_by_id is not None and row.source_cell_id else (db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none() if row.source_cell_id else None)
    destination_cell = location_by_id.get(row.destination_cell_id) if location_by_id is not None and row.destination_cell_id else (db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one_or_none() if row.destination_cell_id else None)
    source_area = area_by_id.get(row.source_area_id) if area_by_id is not None and row.source_area_id else (db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None)
    destination_area = area_by_id.get(row.destination_area_id) if area_by_id is not None and row.destination_area_id else (db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None)
    material = material_by_id.get(row.material_group_id) if material_by_id is not None and row.material_group_id else (db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None)
    current_cell_row = current_location_by_rack_id.get(row.rack_id) if current_location_by_rack_id is not None and row.rack_id else (db.execute(select(Location).where(Location.rack_id == row.rack_id)).scalar_one_or_none() if row.rack_id else None)
    current_cell = None
    if current_cell_row:
        current_cell = {"id": current_cell_row.id, "x": current_cell_row.x, "y": current_cell_row.y, "code": current_cell_row.code}
    if row.rack_id and rack is None:
        logger.warning(
            "MOVEMENT_ORDER_MISSING_RACK movement_order_id=%s order_code=%s rack_id=%s status=%s remote_task_code=%s",
            row.id,
            row.order_code,
            row.rack_id,
            row.status,
            row.remote_task_code,
        )
    source_cell_payload = (
        {"id": source_cell.id, "x": source_cell.x, "y": source_cell.y, "code": source_cell.code, "area_id": source_cell.area_id}
        if source_cell else {}
    )
    destination_cell_payload = (
        {"id": destination_cell.id, "x": destination_cell.x, "y": destination_cell.y, "code": destination_cell.code, "area_id": destination_cell.area_id}
        if destination_cell else {}
    )
    route_points = None
    if getattr(row, "route_points_json", None):
        try:
            loaded_route_points = json.loads(row.route_points_json)
            route_points = loaded_route_points if isinstance(loaded_route_points, list) else None
        except Exception:
            route_points = None
    return MovementOrderOut(
        updated_at=row.updated_at,
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
        rack_code=rack.code if rack else None,
        rack_missing=bool(row.rack_id and rack is None),
        material_group_id=row.material_group_id,
        material_group_name=material.name if material else None,
        source_area_id=row.source_area_id,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=row.source_cell_id,
        destination_cell_id=row.destination_cell_id,
        source_cell=source_cell_payload,
        destination_cell=destination_cell_payload,
        current_cell=current_cell,
        dispatch_status=row.dispatch_status or "not_sent",
        remote_task_code=row.remote_task_code,
        req_code=row.req_code,
        rcs_status=row.rcs_status,
        rcs_message=sanitize(row.rcs_message),
        cancel_source=row.cancel_source,
        cancel_reason=row.cancel_reason,
        closed_by=row.closed_by,
        closed_at=row.closed_at,
        release_source=row.release_source,
        dispatched_at=row.dispatched_at,
        rcs_last_update=row.rcs_last_update,
        status_query_checked_at=row.status_query_checked_at,
        rcs_status_changed_at=getattr(row, "rcs_status_changed_at", None),
        status_query_request_payload=sanitize(_safe_json_loads(row.status_query_request_json)),
        status_query_response_payload=sanitize(_safe_json_loads(row.status_query_response_json)),
        status_query_log_entries=sanitize(_safe_json_loads_any(row.status_query_log_json) or []),
        route_mode=_normalize_route_mode(getattr(row, "route_mode", None)),
        route_points=route_points,
        trmx_group_id=getattr(row, "trmx_group_id", None),
        trmx_step=getattr(row, "trmx_step", None),
        trmx_total_steps=getattr(row, "trmx_total_steps", None),
        trmx_parent_order_id=getattr(row, "trmx_parent_order_id", None),
        trmx_status=getattr(row, "trmx_status", None),
        trmx_select_policy=getattr(row, "trmx_select_policy", None),
        fifo_chain_group_id=getattr(row, "fifo_chain_group_id", None),
        fifo_chain_step=getattr(row, "fifo_chain_step", None),
        fifo_chain_total_steps=getattr(row, "fifo_chain_total_steps", None),
        fifo_chain_parent_order_id=getattr(row, "fifo_chain_parent_order_id", None),
        fifo_chain_status=getattr(row, "fifo_chain_status", None),
        fifo_chain_select_policy=getattr(row, "fifo_chain_select_policy", None),
        can_simulate_complete=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
        can_undo=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
    )


_mo_source_location = Location.__table__.alias("mo_source_location")
_mo_destination_location = Location.__table__.alias("mo_destination_location")
_mo_source_area = Area.__table__.alias("mo_source_area")
_mo_destination_area = Area.__table__.alias("mo_destination_area")
_mo_current_location = Location.__table__.alias("mo_current_location")
_MO_RESPONSE_RELATED_SELECT = (
    select(
        Rack.id.label("rack_id"), Rack.code.label("rack_code"),
        _mo_source_location.c.id.label("source_id"), _mo_source_location.c.x.label("source_x"), _mo_source_location.c.y.label("source_y"), _mo_source_location.c.code.label("source_code"), _mo_source_location.c.area_id.label("source_location_area_id"),
        _mo_destination_location.c.id.label("destination_id"), _mo_destination_location.c.x.label("destination_x"), _mo_destination_location.c.y.label("destination_y"), _mo_destination_location.c.code.label("destination_code"), _mo_destination_location.c.area_id.label("destination_location_area_id"),
        _mo_source_area.c.id.label("source_area_id"), _mo_source_area.c.name.label("source_area_name"),
        _mo_destination_area.c.id.label("destination_area_id"), _mo_destination_area.c.name.label("destination_area_name"),
        MaterialGroup.id.label("material_id"), MaterialGroup.name.label("material_name"),
        _mo_current_location.c.id.label("current_id"), _mo_current_location.c.x.label("current_x"), _mo_current_location.c.y.label("current_y"), _mo_current_location.c.code.label("current_code"), _mo_current_location.c.rack_id.label("current_rack_id"),
    )
    .select_from(MovementOrder.__table__)
    .outerjoin(Rack.__table__, Rack.id == MovementOrder.rack_id)
    .outerjoin(_mo_source_location, _mo_source_location.c.id == MovementOrder.source_cell_id)
    .outerjoin(_mo_destination_location, _mo_destination_location.c.id == MovementOrder.destination_cell_id)
    .outerjoin(_mo_source_area, _mo_source_area.c.id == MovementOrder.source_area_id)
    .outerjoin(_mo_destination_area, _mo_destination_area.c.id == MovementOrder.destination_area_id)
    .outerjoin(MaterialGroup.__table__, MaterialGroup.id == MovementOrder.material_group_id)
    .outerjoin(_mo_current_location, _mo_current_location.c.rack_id == MovementOrder.rack_id)
)


def _movement_order_out_read_optimized(db, row: MovementOrder) -> MovementOrderOut:
    related = db.execute(_MO_RESPONSE_RELATED_SELECT.where(MovementOrder.id == row.id)).mappings().one()
    rack = SimpleNamespace(id=related["rack_id"], code=related["rack_code"]) if related["rack_id"] is not None else None
    source = SimpleNamespace(id=related["source_id"], x=related["source_x"], y=related["source_y"], code=related["source_code"], area_id=related["source_location_area_id"]) if related["source_id"] is not None else None
    destination = SimpleNamespace(id=related["destination_id"], x=related["destination_x"], y=related["destination_y"], code=related["destination_code"], area_id=related["destination_location_area_id"]) if related["destination_id"] is not None else None
    source_area = SimpleNamespace(id=related["source_area_id"], name=related["source_area_name"]) if related["source_area_id"] is not None else None
    destination_area = SimpleNamespace(id=related["destination_area_id"], name=related["destination_area_name"]) if related["destination_area_id"] is not None else None
    material = SimpleNamespace(id=related["material_id"], name=related["material_name"]) if related["material_id"] is not None else None
    current = SimpleNamespace(id=related["current_id"], x=related["current_x"], y=related["current_y"], code=related["current_code"], rack_id=related["current_rack_id"]) if related["current_id"] is not None else None
    return _movement_order_out(
        db, row,
        rack_by_id={rack.id: rack} if rack else {},
        location_by_id={item.id: item for item in (source, destination) if item},
        area_by_id={item.id: item for item in (source_area, destination_area) if item},
        material_by_id={material.id: material} if material else {},
        current_location_by_rack_id={current.rack_id: current} if current else {},
    )


def _movement_order_detail_out(db, row: MovementOrder) -> MovementOrderDetailOut:
    base = _movement_order_out_read_optimized(db, row).model_dump()
    return MovementOrderDetailOut(
        **base,
        dispatch_request_payload=sanitize(_safe_json_loads(row.dispatch_request_json)),
        dispatch_response_payload=sanitize(_safe_json_loads(row.dispatch_response_json)),
        rcs_response_payload=sanitize(_safe_json_loads(row.rcs_response_json)),
        override_payload=sanitize(unprotect_payload(row.override_payload_json)) if row.override_payload_json else None,
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

def _movement_order_cell_position_code(cell: Location) -> str:
    return (cell.code or '').strip()


def _simple_position_code_path_for_order(db, row: MovementOrder) -> list[dict]:
    cells_by_id = {
        cell_id: code
        for cell_id, code in db.execute(
            select(Location.id, Location.code).where(Location.id.in_((row.source_cell_id, row.destination_cell_id)))
        ).all()
    }
    return [
        {
            "positionCode": (cells_by_id[row.source_cell_id] or '').strip(),
            "type": "00",
        },
        {
            "positionCode": (cells_by_id[row.destination_cell_id] or '').strip(),
            "type": "00",
        },
    ]


def _build_position_code_path_for_order(db, row: MovementOrder) -> list[dict]:
    route_points_text = str(getattr(row, "route_points_json", "") or "").strip()
    route_mode = _normalize_route_mode(getattr(row, "route_mode", None))
    if not route_points_text:
        return _simple_position_code_path_for_order(db, row)

    try:
        raw_points = json.loads(route_points_text)
        if not isinstance(raw_points, list):
            raise ValueError("route_points_json no es una lista")

        def _sequence_value(item_with_index):
            index, item = item_with_index
            if not isinstance(item, dict):
                return index
            try:
                return int(item.get("sequence", index + 1))
            except Exception:
                return index

        position_code_path = []
        for _, point in sorted(enumerate(raw_points), key=_sequence_value):
            if not isinstance(point, dict):
                raise ValueError("route_points_json contiene un punto no valido")
            position_code = str(point.get("positionCode") or point.get("position_code") or "").strip()
            if not position_code:
                raise ValueError("route_points_json contiene un punto sin positionCode")
            position_code_path.append({
                "positionCode": position_code,
                "type": str(point.get("type") or "00").strip() or "00",
            })

        if len(position_code_path) < 2:
            raise ValueError("route_points_json debe tener al menos 2 puntos")
        if route_mode == "double_area" and len(position_code_path) != 4:
            raise ValueError("route_mode double_area requiere 4 puntos")

        logger.info(
            "RCS_MULTIPOINT_ROUTE movement_order_id=%s route_mode=%s points=%s positionCodePath=%s",
            getattr(row, "id", None),
            route_mode,
            len(position_code_path),
            position_code_path,
        )
        return position_code_path
    except Exception as exc:
        logger.warning(
            "RCS_MULTIPOINT_ROUTE_FALLBACK movement_order_id=%s route_mode=%s error=%s",
            getattr(row, "id", None),
            route_mode,
            exc,
        )
        return _simple_position_code_path_for_order(db, row)


def _movement_order_json_payload(db, row: MovementOrder) -> dict:
    req_code = row.req_code or _generate_req_code_ms()

    priority_value = ""
    normalized_priority = (row.priority or "").strip().lower()
    if normalized_priority == "alta":
        priority_value = "1"
    elif normalized_priority == "urgente":
        priority_value = "2"
    if row.fifo_chain_select_policy == "automatic_priority":
        priority_value = str(max(1, 128 - int(row.fifo_chain_step or 1)))

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
        "positionCodePath": _build_position_code_path_for_order(db, row),
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


def _parse_scan_event_date(value: Optional[str]) -> Optional[datetime]:
    text_value = (value or "").strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Fecha invalida: {value}")


def _scan_event_out(db, row: ScanEvent) -> ScanEventOut:
    station = db.execute(select(ScannerStation).where(ScannerStation.id == row.scanner_station_id)).scalar_one_or_none() if row.scanner_station_id else None
    terminal = db.execute(select(ScanTerminal).where(ScanTerminal.id == row.terminal_id)).scalar_one_or_none() if getattr(row, "terminal_id", None) else None
    rule = db.execute(select(QrActionRule).where(QrActionRule.id == row.qr_action_rule_id)).scalar_one_or_none() if row.qr_action_rule_id else None
    rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none() if row.rack_id else None
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None
    return ScanEventOut(
        id=row.id,
        scanner_code=row.scanner_code,
        scanner_station_id=row.scanner_station_id,
        scanner_station_name=station.name if station else None,
        terminal_id=getattr(row, "terminal_id", None),
        terminal_code=terminal.terminal_code if terminal else None,
        terminal_name=terminal.name if terminal else None,
        qr_value=row.qr_value,
        qr_action_rule_id=row.qr_action_rule_id,
        qr_alias=rule.qr_alias if rule else None,
        parsed_type=row.parsed_type,
        resolved_action=row.resolved_action,
        rack_id=row.rack_id,
        rack_code=rack.code if rack else None,
        material_group_id=row.material_group_id,
        material_group_name=material.name if material else None,
        source_cell_id=row.source_cell_id,
        destination_cell_id=row.destination_cell_id,
        source_area_id=row.source_area_id,
        destination_area_id=row.destination_area_id,
        movement_order_id=row.movement_order_id,
        mode=row.mode,
        status=row.status,
        error_message=row.error_message,
        request=_safe_json_loads(row.request_json) or {},
        result=_safe_json_loads(row.result_json) or {},
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _movement_order_cell_code(cell: Optional[dict]) -> Optional[str]:
    if not cell:
        return None
    code = str(cell.get("code") or "").strip()
    if code:
        return code
    x = cell.get("x")
    y = cell.get("y")
    if x is not None and y is not None:
        return f"({x},{y})"
    return None


def _pda_action_unavailable_reason(order_out: Optional[MovementOrderOut]) -> Optional[str]:
    if not order_out:
        return None
    status = (order_out.status or "").strip().lower()
    dispatch_status = (order_out.dispatch_status or "").strip().lower()
    if status == "dispatch_error" or dispatch_status == "dispatch_error":
        return "La tarea no fue aceptada por el RCS y el rollback local ya fue ejecutado."
    if status == "completed":
        return "La tarea ya fue completada."
    if status in {"cancelled", "canceled"}:
        return "La tarea ya fue cancelada."
    if status == "undone":
        return "La tarea ya fue devuelta."
    if status == "forced_local_closed":
        return "La tarea fue cerrada localmente."
    if status == "cancel_requested_total":
        return "La cancelacion de esta tarea ya esta en proceso."
    if status == "cancel_requested_undo":
        return "La cancelacion y devolucion de esta tarea ya esta en proceso."
    if not order_out.can_undo:
        return "Esta tarea no admite cancelacion en su estado actual."
    return None


def _utc_datetime_for_pda(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pda_scan_history_item_out(
    db,
    row: ScanEvent,
    order_by_id: Optional[dict] = None,
    movement_lookup: Optional[dict] = None,
    station: Optional[ScannerStation] = None,
) -> PdaScanHistoryItemOut:
    terminal = db.execute(select(ScanTerminal).where(ScanTerminal.id == row.terminal_id)).scalar_one_or_none() if getattr(row, "terminal_id", None) else None
    order = order_by_id.get(row.movement_order_id) if order_by_id is not None and row.movement_order_id else None
    if order is None and row.movement_order_id:
        order = db.execute(select(MovementOrder).where(MovementOrder.id == row.movement_order_id)).scalar_one_or_none()

    order_out = None
    source_area = None
    destination_area = None
    cancel_return_area = None
    if order is not None:
        lookup = movement_lookup or {}
        order_out = _movement_order_out(db, order, **lookup)
        location_by_id = lookup.get("location_by_id") or {}
        area_by_id = lookup.get("area_by_id") or {}
        source_location = location_by_id.get(order.source_cell_id)
        destination_location = location_by_id.get(order.destination_cell_id)
        source_area_id = getattr(source_location, "area_id", None) or order.source_area_id
        destination_area_id = getattr(destination_location, "area_id", None) or order.destination_area_id
        source_area = area_by_id.get(source_area_id) if source_area_id else None
        destination_area = area_by_id.get(destination_area_id) if destination_area_id else None

    cancel_return_area_id = getattr(station, "cancel_return_area_id", None) if station else None
    if cancel_return_area_id:
        area_by_id = (movement_lookup or {}).get("area_by_id") or {}
        cancel_return_area = area_by_id.get(cancel_return_area_id)
        if cancel_return_area is None:
            cancel_return_area = db.execute(select(Area).where(Area.id == cancel_return_area_id)).scalar_one_or_none()

    unavailable_reason = _pda_action_unavailable_reason(order_out)

    return PdaScanHistoryItemOut(
        scan_event_id=row.id,
        created_at=_utc_datetime_for_pda(row.created_at),
        terminal_code=terminal.terminal_code if terminal else None,
        scanner_code=row.scanner_code,
        qr_value=row.qr_value,
        mode=row.mode,
        scan_status=row.status,
        error_message=row.error_message,
        movement_order_id=row.movement_order_id,
        order_code=order_out.order_code if order_out else None,
        rack_id=order_out.rack_id if order_out else None,
        rack_code=order_out.rack_code if order_out else None,
        source_cell_id=order_out.source_cell_id if order_out else None,
        source_cell_code=_movement_order_cell_code(order_out.source_cell) if order_out else None,
        source_area_id=source_area.id if source_area else None,
        source_area_code=source_area.code if source_area else None,
        source_area_name=source_area.name if source_area else None,
        destination_cell_id=order_out.destination_cell_id if order_out else None,
        destination_cell_code=_movement_order_cell_code(order_out.destination_cell) if order_out else None,
        destination_area_id=destination_area.id if destination_area else None,
        destination_area_code=destination_area.code if destination_area else None,
        destination_area_name=destination_area.name if destination_area else None,
        route_mode=order_out.route_mode if order_out else None,
        fifo_chain_group_id=order_out.fifo_chain_group_id if order_out else None,
        fifo_chain_step=order_out.fifo_chain_step if order_out else None,
        fifo_chain_total_steps=order_out.fifo_chain_total_steps if order_out else None,
        fifo_chain_parent_order_id=order_out.fifo_chain_parent_order_id if order_out else None,
        fifo_chain_status=order_out.fifo_chain_status if order_out else None,
        status=order_out.status if order_out else None,
        dispatch_status=order_out.dispatch_status if order_out else None,
        rcs_status=order_out.rcs_status if order_out else None,
        rcs_message=order_out.rcs_message if order_out else None,
        can_undo=bool(order_out.can_undo) if order_out else False,
        action_available=bool(order_out and not unavailable_reason),
        action_unavailable_reason=unavailable_reason,
        cancel_return_mode=_normalize_cancel_return_mode(getattr(station, "cancel_return_mode", None)) if station else "configured_area",
        cancel_return_area_id=cancel_return_area.id if cancel_return_area else cancel_return_area_id,
        cancel_return_area_code=cancel_return_area.code if cancel_return_area else None,
        cancel_return_area_name=cancel_return_area.name if cancel_return_area else None,
        cancel_return_area_matter_area=cancel_return_area.matter_area if cancel_return_area else None,
        cancel_return_area_is_active=int(cancel_return_area.is_active) if cancel_return_area else None,
    )


def _pda_cancel_response(db, action: str, row: MovementOrder, cancel_return_target: Optional[dict] = None) -> PdaMovementOrderCancelOut:
    order_out = _movement_order_out_read_optimized(db, row)
    target = cancel_return_target or {}
    return PdaMovementOrderCancelOut(
        success=True,
        action=action,
        movement_order_id=order_out.order_id,
        status=order_out.status,
        dispatch_status=order_out.dispatch_status,
        rcs_status=order_out.rcs_status,
        message=order_out.rcs_message or order_out.comment or "Cancelacion procesada.",
        can_undo=bool(order_out.can_undo),
        cancel_return_mode=target.get("mode") if action == "cancel_return" else None,
        return_area_id=target.get("return_area_id") if action == "cancel_return" else None,
        return_area_name=target.get("return_area_name") if action == "cancel_return" else None,
        return_source_cell_code=target.get("source_cell_code") if action == "cancel_return" else None,
        matter_area=target.get("matter_area") if action == "cancel_return" else None,
    )


def _ensure_pda_order_can_cancel(db, row: MovementOrder):
    order_out = _movement_order_out_read_optimized(db, row)
    status = (row.status or "").strip().lower()
    dispatch_status = (row.dispatch_status or "").strip().lower()
    if status == "dispatch_error" or dispatch_status == "dispatch_error":
        raise HTTPException(status_code=400, detail="La tarea no fue aceptada por el RCS y el rollback local ya fue ejecutado.")
    if status in {"cancel_requested_total", "cancel_requested_undo"}:
        raise HTTPException(status_code=409, detail="La cancelacion de esta orden ya esta en proceso.")
    if status == "completed":
        raise HTTPException(status_code=400, detail="La orden ya esta completada y no puede cancelarse desde PDA.")
    if status in {"cancelled", "canceled", "undone", "forced_local_closed"}:
        raise HTTPException(status_code=400, detail="La orden ya esta cerrada y no puede cancelarse desde PDA.")
    if not order_out.can_undo:
        raise HTTPException(status_code=400, detail="La orden no esta en un estado cancelable.")


def _validate_choice(value, valid_values: set, label: str):
    if any(isinstance(item, int) for item in valid_values):
        normalized = value
    else:
        normalized = (value or "").strip()
    if normalized not in valid_values:
        raise HTTPException(status_code=400, detail=f"{label} invalido")
    return normalized


def _require_area(db, area_id: Optional[int], label: str) -> Optional[Area]:
    if not area_id:
        return None
    row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail=f"{label} no encontrada")
    if int(row.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail=f"{label} está inactiva; restáurala antes de utilizarla")
    return row


def _require_scanner_cancel_return_area(db, area_id: Optional[int]) -> Optional[Area]:
    if not area_id:
        return None
    row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="El área configurada para regresar material al cancelar no existe.")
    if int(row.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail="El área configurada para regresar material al cancelar está inactiva.")
    return row


def _require_location(db, cell_id: Optional[int], label: str) -> Optional[Location]:
    if not cell_id:
        return None
    row = db.execute(select(Location).where(Location.id == cell_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail=f"{label} no encontrada")
    return row


def _require_material_group(db, material_group_id: Optional[int], label: str = "Material") -> Optional[MaterialGroup]:
    if not material_group_id:
        return None
    row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_group_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail=f"{label} no encontrado")
    if int(row.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail=f"{label} está inactivo; restáuralo antes de utilizarlo")
    return row


def _require_rack(db, rack_id: Optional[int]) -> Optional[Rack]:
    if not rack_id:
        return None
    row = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Rack no encontrado")
    return row


def _cell_code(row: Optional[Location]) -> Optional[str]:
    if not row:
        return None
    return (row.code or "").strip() or f"({row.x}, {row.y})"


def _scanner_station_out(db, row: ScannerStation, area_by_id: Optional[dict] = None) -> ScannerStationOut:
    def area_for(area_id):
        if not area_id:
            return None
        if area_by_id is not None:
            return area_by_id.get(area_id)
        return db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()

    source_area = area_for(row.source_area_id)
    destination_area = area_for(row.destination_area_id)
    storage_area = area_for(row.storage_area_id)
    empty_rack_area = area_for(row.empty_rack_area_id)
    cancel_return_area_id = getattr(row, "cancel_return_area_id", None)
    cancel_return_area = area_for(cancel_return_area_id)
    second_source_area = area_for(getattr(row, "second_source_area_id", None))
    second_destination_area = area_for(getattr(row, "second_destination_area_id", None))
    step3_source_area = area_for(getattr(row, "fifo_chain_step3_source_area_id", None))
    step3_destination_area = area_for(getattr(row, "fifo_chain_step3_destination_area_id", None))
    source_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none() if row.source_cell_id else None
    destination_cell = db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one_or_none() if row.destination_cell_id else None
    second_source_cell = db.execute(select(Location).where(Location.id == row.second_source_cell_id)).scalar_one_or_none() if getattr(row, "second_source_cell_id", None) else None
    second_destination_cell = db.execute(select(Location).where(Location.id == row.second_destination_cell_id)).scalar_one_or_none() if getattr(row, "second_destination_cell_id", None) else None
    step3_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step3_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_source_cell_id", None) else None
    step3_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step3_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_destination_cell_id", None) else None
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.default_material_group_id)).scalar_one_or_none() if row.default_material_group_id else None
    step1_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step1_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step1_material_group_id", None) else None
    step2_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step2_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step2_material_group_id", None) else None
    step3_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step3_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_material_group_id", None) else None
    step4_source_area = area_for(getattr(row, "fifo_chain_step4_source_area_id", None))
    step5_source_area = area_for(getattr(row, "fifo_chain_step5_source_area_id", None))
    step4_destination_area = area_for(getattr(row, "fifo_chain_step4_destination_area_id", None))
    step5_destination_area = area_for(getattr(row, "fifo_chain_step5_destination_area_id", None))
    step4_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step4_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_source_cell_id", None) else None
    step5_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step5_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_source_cell_id", None) else None
    step4_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step4_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_destination_cell_id", None) else None
    step5_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step5_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_destination_cell_id", None) else None
    step4_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step4_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_material_group_id", None) else None
    step5_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step5_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_material_group_id", None) else None
    return ScannerStationOut(
        id=row.id,
        scanner_code=row.scanner_code,
        name=row.name,
        description=row.description,
        station_type=row.station_type,
        default_action=row.default_action,
        source_area_id=row.source_area_id,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=row.source_cell_id,
        source_cell_code=_cell_code(source_cell),
        destination_cell_id=row.destination_cell_id,
        destination_cell_code=_cell_code(destination_cell),
        route_mode=_normalize_route_mode(getattr(row, "route_mode", None)),
        fifo_material_policy=getattr(row, "fifo_material_policy", None) or "specific_material",
        fifo_chain_total_steps=_normalize_fifo_chain_total_steps(getattr(row, "fifo_chain_total_steps", None)),
        fifo_chain_step1_source_mode=_normalize_fifo_chain_step1_source_mode(getattr(row, "fifo_chain_step1_source_mode", None)),
        fifo_chain_step1_material_group_id=getattr(row, "fifo_chain_step1_material_group_id", None),
        fifo_chain_step1_task_typ=getattr(row, "fifo_chain_step1_task_typ", None),
        fifo_chain_step1_material_group_name=step1_material.name if step1_material else None,
        fifo_chain_step2_source_mode=_normalize_fifo_chain_step2_source_mode(getattr(row, "fifo_chain_step2_source_mode", None)),
        fifo_chain_step2_material_group_id=getattr(row, "fifo_chain_step2_material_group_id", None),
        fifo_chain_step2_task_typ=getattr(row, "fifo_chain_step2_task_typ", None),
        fifo_chain_step2_material_group_name=step2_material.name if step2_material else None,
        fifo_chain_step3_source_mode=_normalize_fifo_chain_step3_source_mode(getattr(row, "fifo_chain_step3_source_mode", None)),
        fifo_chain_step3_material_group_id=getattr(row, "fifo_chain_step3_material_group_id", None),
        fifo_chain_step3_task_typ=getattr(row, "fifo_chain_step3_task_typ", None),
        fifo_chain_step3_material_group_name=step3_material.name if step3_material else None,
        fifo_chain_step3_source_area_id=getattr(row, "fifo_chain_step3_source_area_id", None),
        fifo_chain_step3_source_area_name=step3_source_area.name if step3_source_area else None,
        fifo_chain_step3_source_cell_id=getattr(row, "fifo_chain_step3_source_cell_id", None),
        fifo_chain_step3_source_cell_code=_cell_code(step3_source_cell),
        fifo_chain_step3_destination_area_id=getattr(row, "fifo_chain_step3_destination_area_id", None),
        fifo_chain_step3_destination_area_name=step3_destination_area.name if step3_destination_area else None,
        fifo_chain_step3_destination_cell_id=getattr(row, "fifo_chain_step3_destination_cell_id", None),
        fifo_chain_step3_destination_cell_code=_cell_code(step3_destination_cell),
        fifo_chain_step4_source_mode=_normalize_fifo_chain_step4_source_mode(getattr(row, "fifo_chain_step4_source_mode", None)),
        fifo_chain_step5_source_mode=_normalize_fifo_chain_step5_source_mode(getattr(row, "fifo_chain_step5_source_mode", None)),
        fifo_chain_step4_material_group_id=getattr(row, "fifo_chain_step4_material_group_id", None),
        fifo_chain_step5_material_group_id=getattr(row, "fifo_chain_step5_material_group_id", None),
        fifo_chain_step4_task_typ=getattr(row, "fifo_chain_step4_task_typ", None),
        fifo_chain_step5_task_typ=getattr(row, "fifo_chain_step5_task_typ", None),
        fifo_chain_step4_material_group_name=step4_material.name if step4_material else None,
        fifo_chain_step5_material_group_name=step5_material.name if step5_material else None,
        fifo_chain_step4_source_area_id=getattr(row, "fifo_chain_step4_source_area_id", None),
        fifo_chain_step5_source_area_id=getattr(row, "fifo_chain_step5_source_area_id", None),
        fifo_chain_step4_source_area_name=step4_source_area.name if step4_source_area else None,
        fifo_chain_step5_source_area_name=step5_source_area.name if step5_source_area else None,
        fifo_chain_step4_source_cell_id=getattr(row, "fifo_chain_step4_source_cell_id", None),
        fifo_chain_step5_source_cell_id=getattr(row, "fifo_chain_step5_source_cell_id", None),
        fifo_chain_step4_source_cell_code=_cell_code(step4_source_cell),
        fifo_chain_step5_source_cell_code=_cell_code(step5_source_cell),
        fifo_chain_step4_destination_area_id=getattr(row, "fifo_chain_step4_destination_area_id", None),
        fifo_chain_step5_destination_area_id=getattr(row, "fifo_chain_step5_destination_area_id", None),
        fifo_chain_step4_destination_area_name=step4_destination_area.name if step4_destination_area else None,
        fifo_chain_step5_destination_area_name=step5_destination_area.name if step5_destination_area else None,
        fifo_chain_step4_destination_cell_id=getattr(row, "fifo_chain_step4_destination_cell_id", None),
        fifo_chain_step5_destination_cell_id=getattr(row, "fifo_chain_step5_destination_cell_id", None),
        fifo_chain_step4_destination_cell_code=_cell_code(step4_destination_cell),
        fifo_chain_step5_destination_cell_code=_cell_code(step5_destination_cell),
        second_source_area_id=getattr(row, "second_source_area_id", None),
        second_source_area_code=second_source_area.code if second_source_area else None,
        second_source_area_name=second_source_area.name if second_source_area else None,
        second_destination_area_id=getattr(row, "second_destination_area_id", None),
        second_destination_area_code=second_destination_area.code if second_destination_area else None,
        second_destination_area_name=second_destination_area.name if second_destination_area else None,
        second_source_cell_id=getattr(row, "second_source_cell_id", None),
        second_source_cell_code=_cell_code(second_source_cell),
        second_destination_cell_id=getattr(row, "second_destination_cell_id", None),
        second_destination_cell_code=_cell_code(second_destination_cell),
        storage_area_id=row.storage_area_id,
        storage_area_name=storage_area.name if storage_area else None,
        empty_rack_area_id=row.empty_rack_area_id,
        empty_rack_area_name=empty_rack_area.name if empty_rack_area else None,
        cancel_return_area_id=cancel_return_area_id,
        cancel_return_mode=_normalize_cancel_return_mode(getattr(row, "cancel_return_mode", None)),
        cancel_return_area_code=cancel_return_area.code if cancel_return_area else None,
        cancel_return_area_name=cancel_return_area.name if cancel_return_area else None,
        cancel_return_area_matter_area=cancel_return_area.matter_area if cancel_return_area else None,
        cancel_return_area_is_active=int(cancel_return_area.is_active) if cancel_return_area else None,
        default_material_group_id=row.default_material_group_id,
        default_material_group_name=material.name if material else None,
        agv_code=row.agv_code,
        task_typ=row.task_typ,
        priority=int(row.priority or 0),
        require_preview=int(row.require_preview or 0),
        allow_execute=int(row.allow_execute or 0),
        allow_same_cell_move=int(bool(getattr(row, "allow_same_cell_move", False))),
        is_active=int(row.is_active or 0),
        **_fifo_chain_area_modes_out(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _scanner_stations_out(db, rows: List[ScannerStation]) -> List[ScannerStationOut]:
    area_ids = sorted({
        int(area_id)
        for row in rows
        for area_id in (
            row.source_area_id,
            row.destination_area_id,
            row.storage_area_id,
            row.empty_rack_area_id,
            getattr(row, "cancel_return_area_id", None),
            getattr(row, "second_source_area_id", None),
            getattr(row, "second_destination_area_id", None),
            getattr(row, "fifo_chain_step3_source_area_id", None),
            getattr(row, "fifo_chain_step3_destination_area_id", None),
        )
        if area_id
    })
    area_by_id = {}
    if area_ids:
        area_rows = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all()
        area_by_id = {row.id: row for row in area_rows}
    return [_scanner_station_out(db, row, area_by_id=area_by_id) for row in rows]


def _qr_action_rule_out(db, row: QrActionRule) -> QrActionRuleOut:
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None
    rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none() if row.rack_id else None
    source_area = db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None
    destination_area = db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None
    source_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none() if row.source_cell_id else None
    destination_cell = db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one_or_none() if row.destination_cell_id else None
    second_source_area = db.execute(select(Area).where(Area.id == row.second_source_area_id)).scalar_one_or_none() if getattr(row, "second_source_area_id", None) else None
    second_destination_area = db.execute(select(Area).where(Area.id == row.second_destination_area_id)).scalar_one_or_none() if getattr(row, "second_destination_area_id", None) else None
    step3_source_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step3_source_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_source_area_id", None) else None
    step3_destination_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step3_destination_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_destination_area_id", None) else None
    step4_source_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step4_source_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_source_area_id", None) else None
    step5_source_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step5_source_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_source_area_id", None) else None
    step4_destination_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step4_destination_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_destination_area_id", None) else None
    step5_destination_area = db.execute(select(Area).where(Area.id == row.fifo_chain_step5_destination_area_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_destination_area_id", None) else None
    second_source_cell = db.execute(select(Location).where(Location.id == row.second_source_cell_id)).scalar_one_or_none() if getattr(row, "second_source_cell_id", None) else None
    second_destination_cell = db.execute(select(Location).where(Location.id == row.second_destination_cell_id)).scalar_one_or_none() if getattr(row, "second_destination_cell_id", None) else None
    step3_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step3_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_source_cell_id", None) else None
    step3_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step3_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_destination_cell_id", None) else None
    step4_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step4_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_source_cell_id", None) else None
    step5_source_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step5_source_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_source_cell_id", None) else None
    step4_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step4_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_destination_cell_id", None) else None
    step5_destination_cell = db.execute(select(Location).where(Location.id == row.fifo_chain_step5_destination_cell_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_destination_cell_id", None) else None
    step1_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step1_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step1_material_group_id", None) else None
    step2_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step2_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step2_material_group_id", None) else None
    step3_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step3_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step3_material_group_id", None) else None
    step4_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step4_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step4_material_group_id", None) else None
    step5_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.fifo_chain_step5_material_group_id)).scalar_one_or_none() if getattr(row, "fifo_chain_step5_material_group_id", None) else None
    return QrActionRuleOut(
        id=row.id,
        qr_value=row.qr_value,
        qr_alias=row.qr_alias,
        description=row.description,
        qr_type=row.qr_type,
        match_type=row.match_type,
        action_type=row.action_type,
        material_group_id=row.material_group_id,
        material_group_name=material.name if material else None,
        rack_id=row.rack_id,
        rack_code=rack.code if rack else None,
        source_area_id=row.source_area_id,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=row.source_cell_id,
        source_cell_code=_cell_code(source_cell),
        destination_cell_id=row.destination_cell_id,
        destination_cell_code=_cell_code(destination_cell),
        route_mode=getattr(row, "route_mode", None) or "simple_area",
        fifo_material_policy=getattr(row, "fifo_material_policy", None) or "specific_material",
        fifo_chain_total_steps=_normalize_fifo_chain_total_steps(getattr(row, "fifo_chain_total_steps", None)),
        fifo_chain_step1_source_mode=_normalize_fifo_chain_step1_source_mode(getattr(row, "fifo_chain_step1_source_mode", None)),
        fifo_chain_step1_material_group_id=getattr(row, "fifo_chain_step1_material_group_id", None),
        fifo_chain_step1_task_typ=getattr(row, "fifo_chain_step1_task_typ", None),
        fifo_chain_step1_material_group_name=step1_material.name if step1_material else None,
        fifo_chain_step2_source_mode=_normalize_fifo_chain_step2_source_mode(getattr(row, "fifo_chain_step2_source_mode", None)),
        fifo_chain_step2_material_group_id=getattr(row, "fifo_chain_step2_material_group_id", None),
        fifo_chain_step2_task_typ=getattr(row, "fifo_chain_step2_task_typ", None),
        fifo_chain_step2_material_group_name=step2_material.name if step2_material else None,
        fifo_chain_step3_source_mode=_normalize_fifo_chain_step3_source_mode(getattr(row, "fifo_chain_step3_source_mode", None)),
        fifo_chain_step3_material_group_id=getattr(row, "fifo_chain_step3_material_group_id", None),
        fifo_chain_step3_task_typ=getattr(row, "fifo_chain_step3_task_typ", None),
        fifo_chain_step3_material_group_name=step3_material.name if step3_material else None,
        fifo_chain_step3_source_area_id=getattr(row, "fifo_chain_step3_source_area_id", None),
        fifo_chain_step3_source_area_name=step3_source_area.name if step3_source_area else None,
        fifo_chain_step3_source_cell_id=getattr(row, "fifo_chain_step3_source_cell_id", None),
        fifo_chain_step3_source_cell_code=_cell_code(step3_source_cell),
        fifo_chain_step3_destination_area_id=getattr(row, "fifo_chain_step3_destination_area_id", None),
        fifo_chain_step3_destination_area_name=step3_destination_area.name if step3_destination_area else None,
        fifo_chain_step3_destination_cell_id=getattr(row, "fifo_chain_step3_destination_cell_id", None),
        fifo_chain_step3_destination_cell_code=_cell_code(step3_destination_cell),
        fifo_chain_step4_source_mode=_normalize_fifo_chain_step4_source_mode(getattr(row, "fifo_chain_step4_source_mode", None)),
        fifo_chain_step5_source_mode=_normalize_fifo_chain_step5_source_mode(getattr(row, "fifo_chain_step5_source_mode", None)),
        fifo_chain_step4_material_group_id=getattr(row, "fifo_chain_step4_material_group_id", None),
        fifo_chain_step5_material_group_id=getattr(row, "fifo_chain_step5_material_group_id", None),
        fifo_chain_step4_task_typ=getattr(row, "fifo_chain_step4_task_typ", None),
        fifo_chain_step5_task_typ=getattr(row, "fifo_chain_step5_task_typ", None),
        fifo_chain_step4_material_group_name=step4_material.name if step4_material else None,
        fifo_chain_step5_material_group_name=step5_material.name if step5_material else None,
        fifo_chain_step4_source_area_id=getattr(row, "fifo_chain_step4_source_area_id", None),
        fifo_chain_step5_source_area_id=getattr(row, "fifo_chain_step5_source_area_id", None),
        fifo_chain_step4_source_area_name=step4_source_area.name if step4_source_area else None,
        fifo_chain_step5_source_area_name=step5_source_area.name if step5_source_area else None,
        fifo_chain_step4_source_cell_id=getattr(row, "fifo_chain_step4_source_cell_id", None),
        fifo_chain_step5_source_cell_id=getattr(row, "fifo_chain_step5_source_cell_id", None),
        fifo_chain_step4_source_cell_code=_cell_code(step4_source_cell),
        fifo_chain_step5_source_cell_code=_cell_code(step5_source_cell),
        fifo_chain_step4_destination_area_id=getattr(row, "fifo_chain_step4_destination_area_id", None),
        fifo_chain_step5_destination_area_id=getattr(row, "fifo_chain_step5_destination_area_id", None),
        fifo_chain_step4_destination_area_name=step4_destination_area.name if step4_destination_area else None,
        fifo_chain_step5_destination_area_name=step5_destination_area.name if step5_destination_area else None,
        fifo_chain_step4_destination_cell_id=getattr(row, "fifo_chain_step4_destination_cell_id", None),
        fifo_chain_step5_destination_cell_id=getattr(row, "fifo_chain_step5_destination_cell_id", None),
        fifo_chain_step4_destination_cell_code=_cell_code(step4_destination_cell),
        fifo_chain_step5_destination_cell_code=_cell_code(step5_destination_cell),
        second_source_area_id=getattr(row, "second_source_area_id", None),
        second_source_area_code=second_source_area.code if second_source_area else None,
        second_source_area_name=second_source_area.name if second_source_area else None,
        second_destination_area_id=getattr(row, "second_destination_area_id", None),
        second_destination_area_code=second_destination_area.code if second_destination_area else None,
        second_destination_area_name=second_destination_area.name if second_destination_area else None,
        second_source_cell_id=getattr(row, "second_source_cell_id", None),
        second_source_cell_code=_cell_code(second_source_cell),
        second_destination_cell_id=getattr(row, "second_destination_cell_id", None),
        second_destination_cell_code=_cell_code(second_destination_cell),
        priority=row.priority,
        task_typ=row.task_typ,
        agv_code=row.agv_code,
        automatic_priority=bool(getattr(row, "automatic_priority", False)),
        requires_scanner_station=int(row.requires_scanner_station or 0),
        allow_same_cell_move=int(bool(getattr(row, "allow_same_cell_move", False))),
        is_active=int(row.is_active or 0),
        **_fifo_chain_area_modes_out(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _qr_transition_rule_out(db, row: QrTransitionRule) -> QrTransitionRuleOut:
    qr_rule = db.execute(select(QrActionRule).where(QrActionRule.id == row.qr_action_rule_id)).scalar_one_or_none() if row.qr_action_rule_id else None
    scanner = db.execute(select(ScannerStation).where(ScannerStation.id == row.scanner_station_id)).scalar_one_or_none() if row.scanner_station_id else None
    source_area = db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None
    destination_area = db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None
    source_cell = db.execute(select(Location).where(Location.id == row.source_cell_id)).scalar_one_or_none() if getattr(row, "source_cell_id", None) else None
    destination_cell = db.execute(select(Location).where(Location.id == row.destination_cell_id)).scalar_one_or_none() if getattr(row, "destination_cell_id", None) else None
    current_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.current_material_group_id)).scalar_one_or_none() if row.current_material_group_id else None
    next_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.next_material_group_id)).scalar_one_or_none() if row.next_material_group_id else None
    return QrTransitionRuleOut(
        id=row.id,
        name=row.name,
        description=row.description,
        qr_action_rule_id=row.qr_action_rule_id,
        qr_action_rule_value=qr_rule.qr_value if qr_rule else None,
        qr_action_rule_alias=qr_rule.qr_alias if qr_rule else None,
        scanner_station_id=row.scanner_station_id,
        scanner_station_code=scanner.scanner_code if scanner else None,
        scanner_station_name=scanner.name if scanner else None,
        source_area_id=row.source_area_id,
        source_area_code=source_area.code if source_area else None,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_code=destination_area.code if destination_area else None,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=getattr(row, "source_cell_id", None),
        source_cell_code=_cell_code(source_cell),
        destination_cell_id=getattr(row, "destination_cell_id", None),
        destination_cell_code=_cell_code(destination_cell),
        current_material_group_id=row.current_material_group_id,
        current_material_group_code=current_material.code if current_material else None,
        current_material_group_name=current_material.name if current_material else None,
        current_rack_status=row.current_rack_status,
        next_material_group_id=row.next_material_group_id,
        next_material_group_code=next_material.code if next_material else None,
        next_material_group_name=next_material.name if next_material else None,
        next_rack_status=row.next_rack_status,
        next_quantity=row.next_quantity,
        clear_quantity=int(row.clear_quantity or 0),
        next_comment=row.next_comment,
        append_comment=int(row.append_comment or 0),
        apply_on=row.apply_on,
        scope=getattr(row, "scope", "qr_pda") or "qr_pda",
        match_mode=getattr(row, "match_mode", "advanced") or "advanced",
        source_match_mode=getattr(row, "source_match_mode", "configured_source") or "configured_source",
        ignore_current_material=int(getattr(row, "ignore_current_material", 0) or 0),
        priority=int(row.priority or 0),
        is_active=int(row.is_active or 0),
        applied_count=int(row.applied_count or 0),
        last_applied_at=row.last_applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _transition_history_json(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _transition_history_json_value(payloads, key):
    def find(value, depth=0):
        if depth > 5:
            return None
        if isinstance(value, dict):
            if value.get(key) is not None:
                return value.get(key)
            for nested in value.values():
                found = find(nested, depth + 1)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value[:20]:
                found = find(nested, depth + 1)
                if found is not None:
                    return found
        return None
    for payload in payloads:
        found = find(payload)
        if found is not None:
            return found
    return None


def _qr_transition_logs_out(db, rows) -> List[QrTransitionLogOut]:
    def ids(field):
        return {getattr(row, field, None) for row in rows if getattr(row, field, None) is not None}
    rule_ids, order_ids, rack_ids = ids("transition_rule_id"), ids("movement_order_id"), ids("rack_id")
    material_ids = ids("previous_material_group_id") | ids("next_material_group_id")
    scan_event_ids = ids("scan_event_id")
    rules = {row.id: row for row in db.execute(select(QrTransitionRule).where(QrTransitionRule.id.in_(rule_ids))).scalars().all()} if rule_ids else {}
    orders = {row.id: row for row in db.execute(select(MovementOrder).where(MovementOrder.id.in_(order_ids))).scalars().all()} if order_ids else {}
    racks = {row.id: row for row in db.execute(select(Rack).where(Rack.id.in_(rack_ids))).scalars().all()} if rack_ids else {}
    materials = {row.id: row for row in db.execute(select(MaterialGroup).where(MaterialGroup.id.in_(material_ids))).scalars().all()} if material_ids else {}
    event_conditions = []
    if scan_event_ids:
        event_conditions.append(ScanEvent.id.in_(scan_event_ids))
    if order_ids:
        event_conditions.append(ScanEvent.movement_order_id.in_(order_ids))
    events = db.execute(select(ScanEvent).where(or_(*event_conditions)).order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())).scalars().all() if event_conditions else []
    events_by_id = {row.id: row for row in events}
    events_by_order = {}
    for event in events:
        if event.movement_order_id is not None and event.movement_order_id not in events_by_order:
            events_by_order[event.movement_order_id] = event
    selected_events = [events_by_id.get(row.scan_event_id) or events_by_order.get(row.movement_order_id) for row in rows]
    qr_ids = {event.qr_action_rule_id for event in selected_events if event and event.qr_action_rule_id}
    scanner_ids = {event.scanner_station_id for event in selected_events if event and event.scanner_station_id} | {rule.scanner_station_id for rule in rules.values() if rule.scanner_station_id}
    terminal_ids = {event.terminal_id for event in selected_events if event and event.terminal_id}
    qr_rules = {row.id: row for row in db.execute(select(QrActionRule).where(QrActionRule.id.in_(qr_ids))).scalars().all()} if qr_ids else {}
    scanners = {row.id: row for row in db.execute(select(ScannerStation).where(ScannerStation.id.in_(scanner_ids))).scalars().all()} if scanner_ids else {}
    terminals = {row.id: row for row in db.execute(select(ScanTerminal).where(ScanTerminal.id.in_(terminal_ids))).scalars().all()} if terminal_ids else {}
    output = []
    for row, scan_event in zip(rows, selected_events):
        rule, order, rack = rules.get(row.transition_rule_id), orders.get(row.movement_order_id), racks.get(row.rack_id)
        previous_material, next_material = materials.get(row.previous_material_group_id), materials.get(row.next_material_group_id)
        request_json = _transition_history_json(getattr(scan_event, "request_json", None)) if scan_event else {}
        result_json = _transition_history_json(getattr(scan_event, "result_json", None)) if scan_event else {}
        persisted = (request_json, result_json)
        execution_id = _transition_history_json_value(persisted, "execution_id")
        persisted_tier = _transition_history_json_value(persisted, "selected_match_tier")
        persisted_reason = _transition_history_json_value(persisted, "selection_reason")
        persisted_priority = _transition_history_json_value(persisted, "selected_priority")
        persisted_specificity = _transition_history_json_value(persisted, "selected_specificity")
        origin_scanner = scanners.get(scan_event.scanner_station_id) if scan_event else None
        rule_scanner = scanners.get(rule.scanner_station_id) if rule and rule.scanner_station_id else None
        qr_rule = qr_rules.get(scan_event.qr_action_rule_id) if scan_event else None
        context = {"qr_action_rule_id": getattr(scan_event, "qr_action_rule_id", None), "scanner_station_id": getattr(scan_event, "scanner_station_id", None)}
        can_derive_tier = bool(rule and (scan_event or (getattr(rule, "match_mode", None) == "route_simple" or (not rule.qr_action_rule_id and not rule.scanner_station_id))))
        derived_tier = transition_match_tier(rule, context) if can_derive_tier else None
        selected_tier = int(persisted_tier) if persisted_tier is not None else derived_tier
        derived_meta = transition_match_metadata(rule, context) if rule and selected_tier is not None else {}
        specificity = int(persisted_specificity) if persisted_specificity is not None else (transition_specificity(rule) if rule else None)
        specificity_source = "persisted" if persisted_specificity is not None else ("derived" if rule else "unavailable")
        selection_reason = str(persisted_reason) if persisted_reason is not None else (transition_selection_reason(rule, context) if selected_tier is not None else None)
        terminal_code = getattr(terminals.get(scan_event.terminal_id), "terminal_code", None) if scan_event else None
        terminal_code = terminal_code or _transition_history_json_value(persisted, "terminal_code")
        output.append(QrTransitionLogOut(
        id=row.id,
        transition_rule_id=row.transition_rule_id,
        transition_rule_name=rule.name if rule else None,
        movement_order_id=row.movement_order_id,
        order_code=order.order_code if order else None,
        movement_order_code=order.order_code if order else None,
        remote_task_code=order.remote_task_code if order else None,
        scan_event_id=row.scan_event_id,
        qr_value=scan_event.qr_value if scan_event else None,
        qr_action_rule_id=scan_event.qr_action_rule_id if scan_event else None,
        qr_alias=qr_rule.qr_alias if qr_rule else None,
        qr_action_rule_name=(qr_rule.qr_alias or qr_rule.qr_value) if qr_rule else None,
        scanner_station_id=scan_event.scanner_station_id if scan_event else None,
        scanner_code=(scan_event.scanner_code or getattr(origin_scanner, "scanner_code", None)) if scan_event else None,
        scanner_name=origin_scanner.name if origin_scanner else None,
        origin_scanner_code=(scan_event.scanner_code or getattr(origin_scanner, "scanner_code", None)) if scan_event else None,
        origin_scanner_name=origin_scanner.name if origin_scanner else None,
        rule_scanner_station_id=rule.scanner_station_id if rule else None,
        rule_scanner_code=rule_scanner.scanner_code if rule_scanner else None,
        rule_scanner_name=rule_scanner.name if rule_scanner else None,
        terminal_code=terminal_code,
        execution_id=str(execution_id) if execution_id is not None else None,
        selected_match_tier=selected_tier,
        selection_reason=selection_reason,
        priority=int(persisted_priority) if persisted_priority is not None else (int(rule.priority or 0) if rule else None),
        specificity=specificity,
        specificity_source=specificity_source,
        direct_qr_match=bool(derived_meta.get("direct_qr_match")) if derived_meta else None,
        direct_scanner_match=bool(derived_meta.get("direct_scanner_match")) if derived_meta else None,
        rack_id=row.rack_id,
        rack_code=rack.code if rack else None,
        previous_material_group_id=row.previous_material_group_id,
        previous_material_group_name=previous_material.name if previous_material else None,
        next_material_group_id=row.next_material_group_id,
        next_material_group_name=next_material.name if next_material else None,
        previous_rack_status=row.previous_rack_status,
        next_rack_status=row.next_rack_status,
        previous_quantity=row.previous_quantity,
        next_quantity=row.next_quantity,
        previous_comment=row.previous_comment,
        next_comment=row.next_comment,
        status=row.status,
        message=row.message,
        created_at=row.created_at,
        ))
    return output


def _qr_transition_log_out(db, row: QrTransitionLog) -> QrTransitionLogOut:
    return _qr_transition_logs_out(db, [row])[0]


def _pda_qr_rule_out(db, row: QrActionRule) -> PdaQrRuleOut:
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == row.material_group_id)).scalar_one_or_none() if row.material_group_id else None
    source_area = db.execute(select(Area).where(Area.id == row.source_area_id)).scalar_one_or_none() if row.source_area_id else None
    destination_area = db.execute(select(Area).where(Area.id == row.destination_area_id)).scalar_one_or_none() if row.destination_area_id else None
    return PdaQrRuleOut(
        id=row.id,
        qr_value=str(row.qr_value or "").strip(),
        qr_alias=row.qr_alias,
        description=row.description,
        qr_type=row.qr_type or "generic",
        match_type=row.match_type or "exact",
        action_type=row.action_type or "use_scanner_default",
        is_active=int(row.is_active or 0) == 1,
        requires_scanner_station=int(row.requires_scanner_station or 0) == 1,
        material_group_name=material.name if material else None,
        source_area_name=source_area.name if source_area else None,
        destination_area_name=destination_area.name if destination_area else None,
    )


def _available_pda_qr_rules_stmt():
    return (
        select(QrActionRule)
        .where(QrActionRule.is_active == 1)
        .where(func.trim(func.coalesce(QrActionRule.qr_value, "")) != "")
        .order_by(
            func.lower(func.coalesce(QrActionRule.qr_alias, "")),
            func.lower(QrActionRule.qr_value),
            QrActionRule.id,
        )
    )


def _generate_qr_png_buffer(qr_value: str, size: int = 240) -> io.BytesIO:
    value = str(qr_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="La regla QR no tiene valor para generar imagen")
    safe_size = max(80, min(int(size or 240), 1000))

    import qrcode
    from PIL import Image
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    resample = getattr(getattr(Image, "Resampling", Image), "NEAREST")
    image = image.resize((safe_size, safe_size), resample=resample)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _scan_terminal_out(db, row: ScanTerminal) -> ScanTerminalOut:
    station = db.execute(select(ScannerStation).where(ScannerStation.id == row.scanner_station_id)).scalar_one_or_none() if row.scanner_station_id else None
    return ScanTerminalOut(
        id=row.id,
        terminal_code=row.terminal_code,
        name=row.name,
        description=row.description,
        scanner_station_id=row.scanner_station_id,
        scanner_code=station.scanner_code if station else None,
        scanner_station_name=station.name if station else None,
        api_key=row.api_key,
        mode=row.mode,
        allow_execute=int(row.allow_execute or 0),
        require_preview=int(row.require_preview or 0),
        is_active=int(row.is_active or 0),
        last_seen_at=row.last_seen_at,
        last_ip=row.last_ip,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_scanner_station_body(db, body: ScannerStationIn) -> tuple[str, str]:
    station_type = _validate_choice(body.station_type, VALID_SCANNER_STATION_TYPES, "station_type")
    default_action = _validate_choice(body.default_action, VALID_SCANNER_ACTIONS, "default_action")
    route_mode = _validate_choice(_normalize_route_mode(body.route_mode), VALID_ROUTE_MODES, "route_mode")
    _validate_fifo_chain_area_modes(db, body, route_mode)
    _validate_choice(body.fifo_material_policy or "specific_material", VALID_FIFO_MATERIAL_POLICIES, "fifo_material_policy")
    _validate_fifo_chain_total_steps_input(body.fifo_chain_total_steps)
    step1_source_mode = _validate_choice(_normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode), VALID_FIFO_CHAIN_SOURCE_MODES, "fifo_chain_step1_source_mode")
    step2_source_mode = _validate_choice(_normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode), VALID_FIFO_CHAIN_STEP2_SOURCE_MODES, "fifo_chain_step2_source_mode")
    step3_source_mode = _validate_choice(_normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode), VALID_FIFO_CHAIN_STEP3_SOURCE_MODES, "fifo_chain_step3_source_mode")
    step4_source_mode = _validate_choice(_normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode), VALID_FIFO_CHAIN_STEP4_SOURCE_MODES, "fifo_chain_step4_source_mode")
    step5_source_mode = _validate_choice(_normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode), VALID_FIFO_CHAIN_STEP5_SOURCE_MODES, "fifo_chain_step5_source_mode")
    _require_area(db, body.source_area_id, "Area origen")
    _require_area(db, body.destination_area_id, "Area destino")
    _require_area(db, body.second_source_area_id, "Area origen secundaria")
    _require_area(db, body.second_destination_area_id, "Area destino secundaria")
    _require_area(db, body.storage_area_id, "Area de almacenamiento")
    _require_area(db, body.empty_rack_area_id, "Area de rack vacio")
    _validate_choice(_normalize_cancel_return_mode(body.cancel_return_mode), VALID_CANCEL_RETURN_MODES, "cancel_return_mode")
    _require_scanner_cancel_return_area(db, body.cancel_return_area_id)
    _require_location(db, body.source_cell_id, "Celda origen")
    _require_location(db, body.destination_cell_id, "Celda destino")
    _require_location(db, body.second_source_cell_id, "Celda origen secundaria")
    _require_location(db, body.second_destination_cell_id, "Celda destino secundaria")
    _require_area(db, body.fifo_chain_step3_source_area_id, "Area origen tramo 3")
    _require_area(db, body.fifo_chain_step3_destination_area_id, "Area destino tramo 3")
    _require_location(db, body.fifo_chain_step3_source_cell_id, "Celda origen tramo 3")
    _require_location(db, body.fifo_chain_step3_destination_cell_id, "Celda destino tramo 3")
    _require_area(db, body.fifo_chain_step4_source_area_id, "Area origen tramo 4")
    _require_area(db, body.fifo_chain_step5_source_area_id, "Area origen tramo 5")
    _require_area(db, body.fifo_chain_step4_destination_area_id, "Area destino tramo 4")
    _require_area(db, body.fifo_chain_step5_destination_area_id, "Area destino tramo 5")
    _require_location(db, body.fifo_chain_step4_source_cell_id, "Celda origen tramo 4")
    _require_location(db, body.fifo_chain_step5_source_cell_id, "Celda origen tramo 5")
    _require_location(db, body.fifo_chain_step4_destination_cell_id, "Celda destino tramo 4")
    _require_location(db, body.fifo_chain_step5_destination_cell_id, "Celda destino tramo 5")
    if route_mode != "fifo_chain":
        _require_material_group(db, body.default_material_group_id)
    _require_material_group(db, body.fifo_chain_step1_material_group_id)
    _require_material_group(db, body.fifo_chain_step2_material_group_id)
    _require_material_group(db, body.fifo_chain_step3_material_group_id)
    _require_material_group(db, body.fifo_chain_step4_material_group_id)
    _require_material_group(db, body.fifo_chain_step5_material_group_id)
    total_steps = _normalize_fifo_chain_total_steps(body.fifo_chain_total_steps)
    if route_mode == "fifo_chain" and not body.fifo_chain_step1_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 1 requiere Material requerido tramo 1.")
    if route_mode == "fifo_chain" and step2_source_mode != "configured_area" and not body.fifo_chain_step2_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 2 por material requiere Material requerido tramo 2.")
    if route_mode == "fifo_chain" and total_steps >= 3 and step3_source_mode != "configured_area" and not body.fifo_chain_step3_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 3 por material requiere Material requerido tramo 3.")
    if route_mode == "fifo_chain" and total_steps >= 4 and step4_source_mode != "configured_area" and not body.fifo_chain_step4_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 4 por material requiere Material requerido tramo 4.")
    if route_mode == "fifo_chain" and total_steps == 5 and step5_source_mode != "configured_area" and not body.fifo_chain_step5_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 5 por material requiere Material requerido tramo 5.")
    return station_type, default_action


def _validate_qr_rule_unique(db, body, *, existing=None):
    """The resolver's namespace is global, active and case-sensitive, not per scanner."""
    if int(body.is_active or 0) != 1:
        return
    value = body.qr_value.strip()
    match_type = (body.match_type or "exact").strip().lower()
    # Keep historical ambiguities editable without silently creating new ones.
    if existing is not None and int(existing.is_active or 0) == 1 and (
        existing.qr_value == value and (existing.match_type or "exact").strip().lower() == match_type
    ):
        return
    # SQLite writer arbitration makes validation + save atomic across requests.
    db.execute(update(QrActionRule).where(QrActionRule.id == -1).values(updated_at=QrActionRule.updated_at))
    rows = db.execute(select(QrActionRule).where(QrActionRule.is_active == 1, QrActionRule.qr_value == value)).scalars().all()
    for row in rows:
        if existing is not None and row.id == existing.id:
            continue
        if (row.match_type or "exact").strip().lower() == match_type or (
            _rule_matches(row, normalize_qr_value(value)) and _rule_matches(body, normalize_qr_value(value))
        ):
            raise HTTPException(status_code=409, detail=f"Código QR duplicado: ya existe la regla activa {row.id} con el mismo valor y coincidencia operativa.")


def _validate_qr_action_rule_body(db, body: QrActionRuleIn) -> tuple[str, str, str]:
    qr_type = _validate_choice(body.qr_type, VALID_QR_TYPES, "qr_type")
    match_type = _validate_choice(body.match_type, VALID_QR_MATCH_TYPES, "match_type")
    action_type = _validate_choice(body.action_type, VALID_QR_ACTION_TYPES, "action_type")
    route_mode = _validate_choice(_normalize_route_mode(body.route_mode), VALID_ROUTE_MODES, "route_mode")
    _validate_fifo_chain_area_modes(db, body, route_mode)
    fifo_material_policy = _validate_choice(body.fifo_material_policy or "specific_material", VALID_FIFO_MATERIAL_POLICIES, "fifo_material_policy")
    _validate_fifo_chain_total_steps_input(body.fifo_chain_total_steps)
    step1_source_mode = _validate_choice(_normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode), VALID_FIFO_CHAIN_SOURCE_MODES, "fifo_chain_step1_source_mode")
    step2_source_mode = _validate_choice(_normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode), VALID_FIFO_CHAIN_STEP2_SOURCE_MODES, "fifo_chain_step2_source_mode")
    step3_source_mode = _validate_choice(_normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode), VALID_FIFO_CHAIN_STEP3_SOURCE_MODES, "fifo_chain_step3_source_mode")
    step4_source_mode = _validate_choice(_normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode), VALID_FIFO_CHAIN_STEP4_SOURCE_MODES, "fifo_chain_step4_source_mode")
    step5_source_mode = _validate_choice(_normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode), VALID_FIFO_CHAIN_STEP5_SOURCE_MODES, "fifo_chain_step5_source_mode")
    if match_type == "regex":
        try:
            re.compile(body.qr_value)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"Regex invalido: {exc}")
    if route_mode != "fifo_chain":
        _require_material_group(db, body.material_group_id)
    _require_material_group(db, body.fifo_chain_step1_material_group_id)
    _require_material_group(db, body.fifo_chain_step2_material_group_id)
    if route_mode != "fifo_chain":
        _require_rack(db, body.rack_id)
    _require_area(db, body.source_area_id, "Area origen")
    _require_area(db, body.destination_area_id, "Area destino")
    _require_area(db, body.second_source_area_id, "Area origen secundaria")
    _require_area(db, body.second_destination_area_id, "Area destino secundaria")
    _require_location(db, body.source_cell_id, "Celda origen")
    _require_location(db, body.destination_cell_id, "Celda destino")
    _require_location(db, body.second_source_cell_id, "Celda origen secundaria")
    _require_location(db, body.second_destination_cell_id, "Celda destino secundaria")
    _require_area(db, body.fifo_chain_step3_source_area_id, "Area origen tramo 3")
    _require_area(db, body.fifo_chain_step3_destination_area_id, "Area destino tramo 3")
    _require_location(db, body.fifo_chain_step3_source_cell_id, "Celda origen tramo 3")
    _require_location(db, body.fifo_chain_step3_destination_cell_id, "Celda destino tramo 3")
    _require_material_group(db, body.fifo_chain_step3_material_group_id)
    _require_area(db, body.fifo_chain_step4_source_area_id, "Area origen tramo 4")
    _require_area(db, body.fifo_chain_step5_source_area_id, "Area origen tramo 5")
    _require_area(db, body.fifo_chain_step4_destination_area_id, "Area destino tramo 4")
    _require_area(db, body.fifo_chain_step5_destination_area_id, "Area destino tramo 5")
    _require_location(db, body.fifo_chain_step4_source_cell_id, "Celda origen tramo 4")
    _require_location(db, body.fifo_chain_step5_source_cell_id, "Celda origen tramo 5")
    _require_location(db, body.fifo_chain_step4_destination_cell_id, "Celda destino tramo 4")
    _require_location(db, body.fifo_chain_step5_destination_cell_id, "Celda destino tramo 5")
    _require_material_group(db, body.fifo_chain_step4_material_group_id)
    _require_material_group(db, body.fifo_chain_step5_material_group_id)
    total_steps = _normalize_fifo_chain_total_steps(body.fifo_chain_total_steps)
    if route_mode == "fifo_chain" and not body.fifo_chain_step1_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 1 requiere Material requerido tramo 1.")
    if action_type == "fifo_request" and fifo_material_policy == "specific_material" and not body.material_group_id and not body.qr_value.strip().upper().startswith("MAT:") and route_mode != "fifo_chain":
        raise HTTPException(status_code=400, detail="fifo_request requiere material_group_id o un qr_value con prefijo MAT:")
    if action_type == "fifo_request" and fifo_material_policy == "any_available_from_source" and not (body.source_area_id or body.source_cell_id) and route_mode != "fifo_chain":
        raise HTTPException(status_code=400, detail="any_available_from_source requiere area origen o celda origen")
    if route_mode == "fifo_chain" and step2_source_mode != "configured_area" and not body.fifo_chain_step2_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 2 por material requiere Material requerido tramo 2.")
    if route_mode == "fifo_chain" and total_steps >= 3 and step3_source_mode != "configured_area" and not body.fifo_chain_step3_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 3 por material requiere Material requerido tramo 3.")
    if route_mode == "fifo_chain" and total_steps >= 4 and step4_source_mode != "configured_area" and not body.fifo_chain_step4_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 4 por material requiere Material requerido tramo 4.")
    if route_mode == "fifo_chain" and total_steps == 5 and step5_source_mode != "configured_area" and not body.fifo_chain_step5_material_group_id:
        raise HTTPException(status_code=400, detail="El tramo 5 por material requiere Material requerido tramo 5.")
    return qr_type, match_type, action_type


def _validate_scan_terminal_body(db, body: ScanTerminalIn) -> str:
    mode = _validate_choice(body.mode, VALID_SCAN_TERMINAL_MODES, "mode")
    station = db.execute(select(ScannerStation).where(ScannerStation.id == body.scanner_station_id)).scalar_one_or_none()
    if not station:
        raise HTTPException(status_code=400, detail="Scanner / estacion asociada no encontrada")
    if int(station.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail="Scanner / estación asociada inactiva")
    return mode


_LOG_SENSITIVE_KEYWORDS = ("password", "token", "authorization", "auth_header", "secret", "api_key", "apikey")


def _is_log_sensitive_key(key: str) -> bool:
    return sensitive_key(key)


def _sanitize_log_payload(value):
    return sanitize(value)


def _append_status_query_log(db, row: MovementOrder, *, kind: str, request_payload: Optional[dict], response_payload, message: str = "", arrived_at: Optional[datetime] = None):
    logs = _safe_json_loads_any(row.status_query_log_json)
    if not isinstance(logs, list):
        logs = []
    when = arrived_at or datetime.utcnow()
    logs.append({
        "kind": kind,
        "arrived_at": when.isoformat(),
        "message": sanitize_text(message or ""),
        "request": _sanitize_log_payload(request_payload),
        "response": _sanitize_log_payload(response_payload),
        "movement_order_id": row.id, "q1": row.req_code, "t1": row.remote_task_code,
        "transport": getattr(row, "_rcs_audit_events", []),
    })
    row._rcs_audit_events = []
    lifecycle = [item for item in logs if item.get("kind") in {"create_task", "create_task_requested", "cancel_task", "cancel_task_requested"}][:20]
    recent = [item for item in logs if item not in lifecycle][-180:]
    logs = lifecycle + recent
    row.status_query_log_json = json.dumps(sanitize(logs), ensure_ascii=False)
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

    max_console_events = 1000
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
        base_url=sanitize(row.base_url),
        endpoint=sanitize(row.endpoint),
        payload=sanitize(_safe_json_loads_any(row.payload_json)),
        message=sanitize(row.message),
        created_at=row.created_at,
    )




def _active_orders_for_rack(db, rack_id: Optional[int], *, exclude_order_id: Optional[int] = None) -> List[MovementOrder]:
    if not rack_id:
        return []
    stmt = select(MovementOrder).where(
        or_(MovementOrder.rack_id == rack_id, MovementOrder.pickup_rack_id == rack_id, MovementOrder.dropoff_rack_id == rack_id),
        MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
    )
    if exclude_order_id:
        stmt = stmt.where(MovementOrder.id != exclude_order_id)
    return db.execute(stmt).scalars().all()


def _manual_release_rack_or_raise(db, rack: Rack, *, actor: str, reason: str, now: Optional[datetime] = None) -> List[dict]:
    now = now or datetime.utcnow()
    db.execute(update(Rack).where(Rack.id == rack.id).values(updated_at=Rack.updated_at))
    db.refresh(rack)
    closed_orders = []
    active_orders = _active_orders_for_rack(db, rack.id)
    cleanup_min_age_minutes = _cleanup_min_age_minutes(db)
    confirmed_failed_orders = [
        order for order in active_orders
        if _normalized_cleanup_rcs_terminal_status(order.rcs_status) == "failed"
        and _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=cleanup_min_age_minutes)[0]
    ]
    for order in confirmed_failed_orders:
        closed_orders.append(_force_close_old_active_order(db, order, now=now, admin_user=actor, reason=reason))
    if confirmed_failed_orders:
        db.flush()
        active_orders = _active_orders_for_rack(db, rack.id)

    cancel_min_age_minutes = _cancel_undo_auto_recovery_min_age_minutes(db)
    releasable_orders = [
        order for order in active_orders
        if (
            ((order.status or "") == "cancel_requested_undo" and _cancel_undo_recovery_candidate(db, order, now=now, min_age_minutes=cancel_min_age_minutes)[0])
            or _validate_cleanup_order_eligible(db, order, now=now, min_age_minutes=cleanup_min_age_minutes)[0]
        )
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
    simulator_enabled_raw = (get_setting(db, "rcs_simulator_enabled", "0") or "0").strip().lower()
    if simulator_enabled_raw in {"1", "true", "yes", "si", "sí", "sÃ­"}:
        simulated_endpoints = {
            "create": "/simulated/createTask",
            "cancel": "/simulated/cancelTask",
            "query": "/simulated/queryTaskStatus",
        }
        return "sim://local-rcs", simulated_endpoints.get(mode, "/simulated/queryTaskStatus")
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
    override_payload = unprotect_payload(row.override_payload_json) if row.override_payload_json else None
    if override_payload:
        return override_payload, "edited"
    return (generated_payload or {}), "generated"


def _save_movement_order_payload_override(db, row: MovementOrder, payload: dict):
    previous = unprotect_payload(row.override_payload_json) if row.override_payload_json else {}
    payload = restore_redactions(payload, previous)
    _validate_movement_payload(db, row, payload)
    req_code = str((payload or {}).get("reqCode") or "").strip()
    try:
        protected = protect_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    values = {"override_payload_json": protected, "updated_at": datetime.utcnow()}
    if req_code:
        values["req_code"] = req_code
    changed = db.execute(update(MovementOrder).where(
        MovementOrder.id == row.id, MovementOrder.dispatch_request_json.is_(None),
        MovementOrder.remote_task_code.is_(None), MovementOrder.dispatch_status == "not_sent",
    ).values(**values).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="La identidad de una creación intentada no puede editarse.")
    db.commit()
    db.refresh(row)
    return row


def _validate_movement_payload(db, row: MovementOrder, payload: dict):
    expected = RcsTaskRequest.from_payload(_movement_order_json_payload(db, row)).to_payload()
    candidate = RcsTaskRequest.from_payload(payload).to_payload()
    if row.fifo_chain_select_policy == "automatic_priority" and candidate["priority"] != expected["priority"]:
        raise HTTPException(status_code=409, detail="El payload no conserva la prioridad automática del tramo")
    for key in ("positionCodePath", "agvCode", "taskTyp", "taskCode", "podCode"):
        if candidate.get(key) != expected.get(key):
            raise HTTPException(status_code=409, detail=f"El payload no corresponde a la orden: {key}")
    code = str(candidate.get("reqCode") or "").strip()
    if not code or len(code) > 32 or (row.req_code and code != row.req_code):
        raise HTTPException(status_code=409, detail="El payload no conserva la identidad Q1 de la orden")


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
        request_payload=sanitize(_safe_json_loads(row.dispatch_request_json)),
        response_payload=sanitize(_safe_json_loads(row.dispatch_response_json)),
        remote_task_code=row.remote_task_code,
        rcs_message=sanitize(row.rcs_message),
        dispatched_at=row.dispatched_at,
    )


def _mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return ("*" * max(0, len(value) - 4)) + value[-4:]


SIMULATED_RCS_TASKS: dict[str, dict] = {}
SIMULATED_RCS_RUNNING_SECONDS = 10.0


class SimulatedRcsClient:
    _token_code = ""

    def __init__(self) -> None:
        self.base_url = "sim://local-rcs"
        self.endpoint_create_task = "/simulated/createTask"
        self.endpoint_query_task_status = "/simulated/queryTaskStatus"
        self.endpoint_cancel_task = "/simulated/cancelTask"

    def create_task(self, task: RcsTaskRequest) -> RcsTaskResponse:
        payload = task.to_payload()
        task_typ = str(payload.get("taskTyp") or "TASK").strip() or "TASK"
        remote_task_code = f"{task_typ}_{uuid.uuid4()}"
        SIMULATED_RCS_TASKS[remote_task_code] = {
            "taskCode": remote_task_code,
            "taskTyp": task_typ,
            "agvCode": str(payload.get("agvCode") or "SIM-AGV"),
            "created_monotonic": time.monotonic(),
            "payload": payload,
        }
        raw = {
            "code": 0,
            "data": remote_task_code,
            "message": "Success",
            "reqCode": str(payload.get("reqCode") or generate_req_code_ms()),
            "simulated": True,
        }
        logger.info("RCS_SIM createTask remote_task_code=%s taskTyp=%s", remote_task_code, task_typ)
        return RcsTaskResponse(code=0, data=remote_task_code, message="Success", reqCode=raw["reqCode"], raw=raw)

    def query_task_status_with_payload(self, payload: dict) -> RcsTaskStatusResponse:
        requested = payload.get("taskCodes") or []
        if isinstance(requested, str):
            requested = [requested]
        if not requested and payload.get("taskCode"):
            requested = [payload.get("taskCode")]
        items = []
        for raw_code in requested:
            task_code = str(raw_code or "").strip()
            task = SIMULATED_RCS_TASKS.get(task_code, {})
            elapsed = time.monotonic() - float(task.get("created_monotonic") or 0.0) if task else SIMULATED_RCS_RUNNING_SECONDS
            status = "2" if elapsed < SIMULATED_RCS_RUNNING_SECONDS else "9"
            items.append({
                "taskCode": task_code,
                "taskStatus": status,
                "taskTyp": str(task.get("taskTyp") or ""),
                "agvCode": str(task.get("agvCode") or "SIM-AGV"),
                "elapsedSeconds": round(elapsed, 2),
            })
        if not items:
            items.append({"taskCode": "", "taskStatus": "9", "taskTyp": "", "agvCode": "SIM-AGV"})
        req_code = str(payload.get("reqCode") or generate_req_code_ms())
        raw = {"code": 0, "message": "Success", "reqCode": req_code, "data": items, "simulated": True}
        selected = items[0]
        logger.info("RCS_SIM queryTaskStatus tasks=%s", [item.get("taskCode") for item in items])
        return RcsTaskStatusResponse(
            code=0,
            task_code=str(selected.get("taskCode") or ""),
            task_status=str(selected.get("taskStatus") or "9"),
            message="Success",
            reqCode=req_code,
            raw=raw,
            task_statuses=items,
        )

    def query_task_status(self, task_code: str, req_code: str = "", token_code: str = "", client_code: str = "") -> RcsTaskStatusResponse:
        return self.query_task_status_with_payload({"reqCode": req_code or generate_req_code_ms(), "taskCodes": [task_code]})

    def query_agv_status_with_payload(self, payload: dict, endpoint_override: Optional[str] = None) -> dict:
        req_code = str(payload.get("reqCode") or generate_req_code_ms())
        return {
            "code": 0,
            "message": "Success",
            "reqCode": req_code,
            "data": [{
                "agvCode": "SIM-AGV",
                "robotCode": "SIM-AGV",
                "status": "idle",
                "battery": 100,
                "mapShortName": str(payload.get("mapShortName") or "SIM"),
            }],
            "simulated": True,
        }

    def cancel_task_with_payload(self, payload: dict, endpoint_override: Optional[str] = None) -> RcsSimpleResponse:
        task_code = str(payload.get("taskCode") or "").strip()
        if task_code in SIMULATED_RCS_TASKS:
            SIMULATED_RCS_TASKS[task_code]["created_monotonic"] = time.monotonic() - SIMULATED_RCS_RUNNING_SECONDS
        raw = {"code": 0, "message": "Success", "reqCode": str(payload.get("reqCode") or generate_req_code_ms()), "simulated": True}
        return RcsSimpleResponse(code=0, message="Success", reqCode=raw["reqCode"], raw=raw)

    def cancel_task(self, task_code: str = "", agv_code: str = "", req_code: str = "", token_code: str = "", client_code: str = "", force_cancel: str = "0", matter_area: str = "") -> RcsSimpleResponse:
        return self.cancel_task_with_payload({"reqCode": req_code or generate_req_code_ms(), "taskCode": task_code, "agvCode": agv_code})


def _get_rcs_config(db) -> RcsConfigOut:
    simulator_enabled_raw = (get_setting(db, "rcs_simulator_enabled", "0") or "0").strip().lower()
    simulator_enabled = 1 if simulator_enabled_raw in {"1", "true", "yes", "si", "sí", "sÃ­"} else 0
    base_url = (get_setting(db, "rcs_base_url", "") or "").strip()
    endpoint = (get_setting(db, "rcs_create_task_endpoint", "/rcs/task/create") or "/rcs/task/create").strip() or "/rcs/task/create"
    query_endpoint = (get_setting(db, "rcs_query_task_status_endpoint", "/rcms/services/rest/hikRpcService/queryTaskStatus") or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip() or "/rcms/services/rest/hikRpcService/queryTaskStatus"
    cancel_endpoint = (get_setting(db, "rcs_cancel_task_endpoint", "/rcms/services/rest/hikRpcService/cancelTask") or "/rcms/services/rest/hikRpcService/cancelTask").strip() or "/rcms/services/rest/hikRpcService/cancelTask"
    stop_robot_endpoint = (get_setting(db, "rcs_stop_robot_endpoint", "/rcms/services/rest/hikRpcService/stopRobot") or "/rcms/services/rest/hikRpcService/stopRobot").strip() or "/rcms/services/rest/hikRpcService/stopRobot"
    resume_robot_endpoint = (get_setting(db, "rcs_resume_robot_endpoint", "/rcms/services/rest/hikRpcService/resumeRobot") or "/rcms/services/rest/hikRpcService/resumeRobot").strip() or "/rcms/services/rest/hikRpcService/resumeRobot"
    agv_status_endpoint = (get_setting(db, "rcs_agv_status_endpoint", "/rcms-dps/rest/queryAgvStatus") or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    pod_position_endpoint = (get_setting(db, "rcs_pod_position_endpoint", "/rcms/services/rest/hikRpcService/queryPodPosition") or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
    rack_sync_query_endpoint = (get_setting(db, "rcs_rack_sync_query_endpoint", "/rcms/services/rest/hikRpcService/queryPodBerthAndMat") or "/rcms/services/rest/hikRpcService/queryPodBerthAndMat").strip() or "/rcms/services/rest/hikRpcService/queryPodBerthAndMat"
    rack_sync_bind_endpoint = (get_setting(db, "rcs_rack_sync_bind_endpoint", "/rcms/services/rest/hikRpcService/bindPodAndBerth") or "/rcms/services/rest/hikRpcService/bindPodAndBerth").strip() or "/rcms/services/rest/hikRpcService/bindPodAndBerth"
    rack_sync_schedule_enabled_raw = (get_setting(db, "rcs_rack_sync_schedule_enabled", "0") or "0").strip().lower()
    rack_sync_schedule_time = (get_setting(db, "rcs_rack_sync_schedule_time", "12:00") or "12:00").strip() or "12:00"
    if not re.fullmatch(r"\d{1,2}:\d{2}", rack_sync_schedule_time):
        rack_sync_schedule_time = "12:00"
    else:
        hour_text, minute_text = rack_sync_schedule_time.split(":", 1)
        hour = min(23, max(0, int(hour_text)))
        minute = min(59, max(0, int(minute_text)))
        rack_sync_schedule_time = f"{hour:02d}:{minute:02d}"
    rack_sync_schedule_last_run_date = (get_setting(db, "rcs_rack_sync_schedule_last_run_date", "") or "").strip()
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

    resolved_base_url = "sim://local-rcs" if simulator_enabled else (base_url or os.getenv("RCS_BASE_URL", "").strip())
    resolved_token_code = token_code or os.getenv("RCS_TOKEN_CODE", "").strip()
    resolved_auth_header = auth_header or os.getenv("RCS_AUTH_HEADER", "").strip()

    return RcsConfigOut(
        web_base_url=get_setting(db, "rcs_web_base_url", ""),
        web_login_endpoint=get_setting(db, "rcs_web_login_endpoint", "/rcms/web/login/login.action"),
        web_charge_endpoint=get_setting(db, "rcs_web_charge_endpoint", "/rcms/web/agvControl/agvChargeTask.action"),
        web_username=get_setting(db, "rcs_web_username", ""),
        web_ecs_password="",
        base_url=base_url,
        simulator_enabled=simulator_enabled,
        create_task_endpoint=endpoint,
        query_task_status_endpoint=query_endpoint,
        cancel_task_endpoint=cancel_endpoint,
        stop_robot_endpoint=stop_robot_endpoint,
        resume_robot_endpoint=resume_robot_endpoint,
        agv_status_endpoint=agv_status_endpoint,
        pod_position_endpoint=pod_position_endpoint,
        rack_sync_query_endpoint=rack_sync_query_endpoint,
        rack_sync_bind_endpoint=rack_sync_bind_endpoint,
        rack_sync_schedule_enabled=1 if rack_sync_schedule_enabled_raw in {"1", "true", "yes", "si", "sÃ­"} else 0,
        rack_sync_schedule_time=rack_sync_schedule_time,
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
        rack_sync_schedule_last_run_date=rack_sync_schedule_last_run_date,
        simulator_base_url="sim://local-rcs",
    )


def _save_rcs_config(db, payload: RcsConfigIn) -> RcsConfigOut:
    from rcs_charge_service import web_url
    web_base = payload.web_base_url if "web_base_url" in payload.model_fields_set else get_setting(db, "rcs_web_base_url", "")
    if web_base:
        try:
            for field, default in (("web_login_endpoint", "/rcms/web/login/login.action"), ("web_charge_endpoint", "/rcms/web/agvControl/agvChargeTask.action")):
                endpoint = getattr(payload, field) if field in payload.model_fields_set else get_setting(db, "rcs_" + field, default)
                web_url(web_base.strip(), endpoint.strip())
        except ValueError:
            raise HTTPException(400, "URL o endpoint RCS Web inválido; no incluir credenciales en la URL")
    simulator_enabled = 1 if int(payload.simulator_enabled or 0) else 0
    endpoint = (payload.create_task_endpoint or "/rcs/task/create").strip() or "/rcs/task/create"
    query_endpoint = (payload.query_task_status_endpoint or "/rcms/services/rest/hikRpcService/queryTaskStatus").strip() or "/rcms/services/rest/hikRpcService/queryTaskStatus"
    cancel_endpoint = (payload.cancel_task_endpoint or "/rcms/services/rest/hikRpcService/cancelTask").strip() or "/rcms/services/rest/hikRpcService/cancelTask"
    stop_robot_endpoint = (payload.stop_robot_endpoint or "/rcms/services/rest/hikRpcService/stopRobot").strip() or "/rcms/services/rest/hikRpcService/stopRobot"
    resume_robot_endpoint = (payload.resume_robot_endpoint or "/rcms/services/rest/hikRpcService/resumeRobot").strip() or "/rcms/services/rest/hikRpcService/resumeRobot"
    agv_status_endpoint = (payload.agv_status_endpoint or "/rcms-dps/rest/queryAgvStatus").strip() or "/rcms-dps/rest/queryAgvStatus"
    pod_position_endpoint = (payload.pod_position_endpoint or "/rcms/services/rest/hikRpcService/queryPodPosition").strip() or "/rcms/services/rest/hikRpcService/queryPodPosition"
    rack_sync_query_endpoint = (payload.rack_sync_query_endpoint or "/rcms/services/rest/hikRpcService/queryPodBerthAndMat").strip() or "/rcms/services/rest/hikRpcService/queryPodBerthAndMat"
    rack_sync_bind_endpoint = (payload.rack_sync_bind_endpoint or "/rcms/services/rest/hikRpcService/bindPodAndBerth").strip() or "/rcms/services/rest/hikRpcService/bindPodAndBerth"
    rack_sync_schedule_enabled = 1 if int(payload.rack_sync_schedule_enabled or 0) else 0
    rack_sync_schedule_time = (payload.rack_sync_schedule_time or "12:00").strip() or "12:00"
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
    if not rack_sync_query_endpoint.startswith("/"):
        rack_sync_query_endpoint = "/" + rack_sync_query_endpoint
    if not rack_sync_bind_endpoint.startswith("/"):
        rack_sync_bind_endpoint = "/" + rack_sync_bind_endpoint
    source = "admin_rcs_config"
    for field in ("web_base_url", "web_login_endpoint", "web_charge_endpoint", "web_username"):
        if field in payload.model_fields_set:
            set_setting(db, "rcs_" + field, getattr(payload, field).strip(), source=source)
    if payload.web_ecs_password:
        set_setting(db, "rcs_web_ecs_password", payload.web_ecs_password, source=source)
    set_setting(db, "rcs_simulator_enabled", "1" if simulator_enabled else "0", source=source)
    set_setting(db, "rcs_base_url", (payload.base_url or "").strip(), source=source)
    set_setting(db, "rcs_create_task_endpoint", endpoint, source=source)
    set_setting(db, "rcs_query_task_status_endpoint", query_endpoint, source=source)
    set_setting(db, "rcs_cancel_task_endpoint", cancel_endpoint, source=source)
    set_setting(db, "rcs_stop_robot_endpoint", stop_robot_endpoint, source=source)
    set_setting(db, "rcs_resume_robot_endpoint", resume_robot_endpoint, source=source)
    set_setting(db, "rcs_agv_status_endpoint", agv_status_endpoint, source=source)
    set_setting(db, "rcs_pod_position_endpoint", pod_position_endpoint, source=source)
    set_setting(db, "rcs_rack_sync_query_endpoint", rack_sync_query_endpoint, source=source)
    set_setting(db, "rcs_rack_sync_bind_endpoint", rack_sync_bind_endpoint, source=source)
    set_setting(db, "rcs_rack_sync_schedule_enabled", "1" if rack_sync_schedule_enabled else "0", source=source)
    set_setting(db, "rcs_rack_sync_schedule_time", rack_sync_schedule_time, source=source)
    set_setting(db, "rcs_task_monitor_interval_seconds", str(task_monitor_interval_seconds), source=source)
    set_setting(db, "rcs_agv_monitor_interval_seconds", str(agv_monitor_interval_seconds), source=source)
    set_setting(db, "rcs_enable_map_short_name", "1" if enable_map_short_name else "0", source=source)
    set_setting(db, "rcs_map_short_name", map_short_name, source=source)
    set_setting(db, "rcs_enable_map_code", "1" if enable_map_code else "0", source=source)
    set_setting(db, "rcs_map_code", map_code, source=source)
    set_setting(db, "rcs_enable_amr_monitor", "1" if enable_amr_monitor else "0", source=source)
    set_setting(db, "rcs_verify_tls", "1" if int(payload.verify_tls or 0) else "0", source=source)
    set_setting(db, "rcs_token_code", payload.token_code or "", source=source)
    set_setting(db, "rcs_auth_header", payload.auth_header or "", source=source)
    set_setting(db, "cleanup_min_age_minutes", str(cleanup_min_age_minutes), source=source)
    set_setting(db, "force_release_min_age_minutes", str(force_release_min_age_minutes), source=source)
    set_setting(db, "cancel_undo_auto_recovery_enabled", str(cancel_undo_auto_recovery_enabled), source=source)
    set_setting(db, "cancel_undo_auto_recovery_min_age_minutes", str(cancel_undo_auto_recovery_min_age_minutes), source=source)
    try:
        app.state.task_monitor.interval_seconds = task_monitor_interval_seconds
    except Exception:
        pass
    return _get_rcs_config(db)


def _get_rcs_client_from_settings(db) -> RcsClient:
    simulator_enabled_raw = (get_setting(db, "rcs_simulator_enabled", "0") or "0").strip().lower()
    if simulator_enabled_raw in {"1", "true", "yes", "si", "sí", "sÃ­"}:
        return SimulatedRcsClient()
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


def _rack_sync_endpoint(value: str, default: str) -> str:
    endpoint = (value or default).strip() or default
    if not endpoint.startswith("/") and not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = "/" + endpoint
    return endpoint


def _rack_sync_active_tasks_count(db) -> int:
    active_statuses = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo")
    return int(db.execute(select(func.count()).select_from(MovementOrder).where(MovementOrder.status.in_(active_statuses))).scalar() or 0)


def _rack_sync_event_out(row: RackSyncEvent) -> RackSyncEventOut:
    return RackSyncEventOut(
        id=row.id,
        action=row.action,
        ok=bool(row.ok),
        blocked=bool(row.blocked),
        total_assigned_racks=int(row.total_assigned_racks or 0),
        match_count=int(row.match_count or 0),
        mismatch_count=int(row.mismatch_count or 0),
        missing_count=int(row.missing_count or 0),
        invalid_count=int(row.invalid_count or 0),
        attempted_count=int(row.attempted_count or 0),
        success_count=int(row.success_count or 0),
        error_count=int(row.error_count or 0),
        skipped_count=int(row.skipped_count or 0),
        active_tasks_count=int(row.active_tasks_count or 0),
        duration_ms=int(row.duration_ms or 0),
        message=sanitize(row.message or ""),
        payload=sanitize(_safe_json_loads(row.payload_json) or {}),
        created_at=row.created_at,
    )


def _append_rack_sync_event(db, *, action: str, result, duration_ms: int = 0):
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
    items = payload.get("items") if isinstance(payload, dict) else []
    if isinstance(items, list) and len(items) > 80:
        payload["items"] = items[:80]
        payload["items_truncated"] = len(items) - 80
    row = RackSyncEvent(
        action=(action or "unknown").strip()[:32],
        ok=1 if bool(getattr(result, "ok", False)) else 0,
        blocked=1 if bool(getattr(result, "blocked", False)) else 0,
        total_assigned_racks=int(getattr(result, "total_assigned_racks", 0) or 0),
        match_count=int(getattr(result, "match_count", 0) or 0),
        mismatch_count=int(getattr(result, "mismatch_count", 0) or 0),
        missing_count=int(getattr(result, "missing_count", 0) or 0),
        invalid_count=int(getattr(result, "invalid_count", 0) or 0),
        attempted_count=int(getattr(result, "attempted_count", 0) or 0),
        success_count=int(getattr(result, "success_count", 0) or 0),
        error_count=int(getattr(result, "error_count", 0) or 0),
        skipped_count=int(getattr(result, "skipped_count", 0) or 0),
        active_tasks_count=int(getattr(result, "active_tasks_count", 0) or 0),
        duration_ms=max(0, int(duration_ms or 0)),
        message=str(getattr(result, "message", "") or "")[:512],
        payload_json=json.dumps(_sanitize_log_payload(payload), ensure_ascii=False),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    logger.info(
        "Rack sync event action=%s ok=%s blocked=%s total=%s mismatches=%s errors=%s duration_ms=%s",
        row.action,
        row.ok,
        row.blocked,
        row.total_assigned_racks,
        row.mismatch_count,
        row.error_count,
        row.duration_ms,
    )
    return row


def _list_rack_sync_events_out(db, limit: int = 50) -> List[RackSyncEventOut]:
    rows = db.execute(
        select(RackSyncEvent)
        .order_by(RackSyncEvent.created_at.desc(), RackSyncEvent.id.desc())
        .limit(max(1, min(200, int(limit or 50))))
    ).scalars().all()
    return [_rack_sync_event_out(row) for row in rows]


def _build_rack_sync_preview(db) -> RackSyncPreviewOut:
    cfg = _get_rcs_config(db)
    query_endpoint = _rack_sync_endpoint(cfg.rack_sync_query_endpoint, "/rcms/services/rest/hikRpcService/queryPodBerthAndMat")
    bind_endpoint = _rack_sync_endpoint(cfg.rack_sync_bind_endpoint, "/rcms/services/rest/hikRpcService/bindPodAndBerth")
    map_short_name = cfg.map_short_name if int(cfg.enable_map_short_name or 0) == 1 else ""
    active_tasks_count = _rack_sync_active_tasks_count(db)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = (
        db.execute(
            select(Location, Rack)
            .join(Rack, Location.rack_id == Rack.id)
            .where(Location.rack_id.is_not(None))
            .order_by(Location.y.asc(), Location.x.asc(), Rack.code.asc())
        )
        .all()
    )
    location_code_counts = {}
    for loc, _rack in rows:
        code = (loc.code or "").strip()
        if code:
            location_code_counts[code] = location_code_counts.get(code, 0) + 1
    items: List[RackSyncPreviewItem] = []
    for index, (loc, rack) in enumerate(rows, start=1):
        rack_code = (rack.code or "").strip()
        position_code = (loc.code or "").strip()
        errors = []
        if not rack_code:
            errors.append("missing_rack_code")
        if not position_code:
            errors.append("missing_location_code")
        elif location_code_counts.get(position_code, 0) > 1:
            errors.append("duplicate_location_code")
        req_suffix = f"{index:04d}"
        query_payload = {
            "reqCode": f"{generate_req_code_ms()}Q{req_suffix}",
            "reqTime": now_text,
            "clientCode": "",
            "tokenCode": "",
            "podCode": rack_code,
            "materialLot": "",
            "positionCode": "",
            "areaCode": "",
            "mapShortName": map_short_name,
        }
        bind_payload = {
            "reqCode": f"{generate_req_code_ms()}B{req_suffix}",
            "reqTime": now_text,
            "clientCode": "",
            "tokenCode": "",
            "podCode": rack_code,
            "positionCode": position_code,
            "podDir": "0",
            "characterValue": "",
            "indBind": "1",
        }
        status = "ready" if not errors else "invalid"
        items.append(RackSyncPreviewItem(
            location_id=loc.id,
            location_code=position_code or None,
            location_x=loc.x,
            location_y=loc.y,
            rack_id=rack.id,
            rack_code=rack_code or None,
            status=status,
            errors=errors,
            query_payload=query_payload,
            bind_payload=bind_payload,
        ))
    ready_count = sum(1 for item in items if item.status == "ready")
    error_count = len(items) - ready_count
    blocked = active_tasks_count > 0
    message = "Vista previa generada. No se envio nada al RCS."
    if blocked:
        message += f" Hay {active_tasks_count} tarea(s) activa(s); futuras ejecuciones deben bloquearse."
    if error_count:
        message += f" {error_count} rack(s) tienen errores de configuracion."
    return RackSyncPreviewOut(
        ok=error_count == 0,
        blocked=blocked,
        active_tasks_count=active_tasks_count,
        query_endpoint=query_endpoint,
        bind_endpoint=bind_endpoint,
        map_short_name=map_short_name,
        total_assigned_racks=len(items),
        ready_count=ready_count,
        error_count=error_count,
        message=message,
        items=items,
    )


def _rack_sync_response_items(response_payload: Any) -> List[dict]:
    if not isinstance(response_payload, dict):
        return []
    data = response_payload.get("data")
    if isinstance(data, str):
        data = data.strip()
        if not data:
            return []
        try:
            data = json.loads(data)
        except Exception:
            return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _rack_sync_find_response_item(response_payload: Any, rack_code: str) -> Optional[dict]:
    rows = _rack_sync_response_items(response_payload)
    if not rows:
        return None
    rack_code_norm = str(rack_code or "").strip()
    for row in rows:
        response_rack = str(row.get("podCode") or row.get("pod_code") or row.get("rackCode") or row.get("rack_code") or "").strip()
        if response_rack and response_rack == rack_code_norm:
            return row
    if len(rows) == 1:
        return rows[0]
    return None


def _rack_sync_position_from_response(row: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(row, dict):
        return None, None
    position_code = str(
        row.get("positionCode")
        or row.get("position_code")
        or row.get("locationCode")
        or row.get("location_code")
        or row.get("cellCode")
        or row.get("cell_code")
        or row.get("posCode")
        or row.get("position")
        or ""
    ).strip()
    map_data_code = str(row.get("mapDataCode") or row.get("map_data_code") or "").strip()
    return position_code or None, map_data_code or None


def _query_rack_sync_comparison(db) -> RackSyncQueryOut:
    preview = _build_rack_sync_preview(db)
    client = _get_rcs_client_from_settings(db)
    token_code = getattr(client, "_token_code", "") or ""
    query_endpoint = preview.query_endpoint
    debug_base_url, debug_endpoint = _resolve_rcs_target(db, endpoint=query_endpoint, mode="query")
    items: List[RackSyncQueryItem] = []

    for item in preview.items:
        base_data = item.model_dump()
        if item.status != "ready":
            items.append(RackSyncQueryItem(**base_data, status="invalid", action="skip"))
            continue

        request_payload = dict(item.query_payload or {})
        request_payload["reqCode"] = generate_req_code_ms()
        request_payload["reqTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        request_payload["tokenCode"] = token_code
        bind_payload = dict(item.bind_payload or {})
        bind_payload["tokenCode"] = token_code
        base_data["query_payload"] = request_payload
        base_data["bind_payload"] = bind_payload
        context_payload = {
            **request_payload,
            "location_id": item.location_id,
            "rack_id": item.rack_id,
            "expected_position_code": item.location_code,
        }

        try:
            _append_debug_console_event(
                db,
                direction="sent",
                module="rack_sync_query",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload=context_payload,
                message=f"Consulta rack/posicion para {item.rack_code}",
                auto_commit=False,
            )
            response_payload = client.post_json_payload(request_payload, endpoint_override=query_endpoint)
            _append_debug_console_event(
                db,
                direction="received",
                module="rack_sync_query",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload=response_payload,
                message=str(response_payload.get("message") or f"Respuesta rack/posicion para {item.rack_code}"),
                auto_commit=False,
            )
        except RcsError as exc:
            _append_debug_console_event(
                db,
                direction="received",
                module="rack_sync_query",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload={"error": str(exc), "rack_id": item.rack_id, "rack_code": item.rack_code},
                message=str(exc),
                auto_commit=False,
            )
            items.append(RackSyncQueryItem(**base_data, status="query_error", action="retry", query_error=str(exc)))
            continue

        response_item = _rack_sync_find_response_item(response_payload, item.rack_code or "")
        rcs_position_code, rcs_map_data_code = _rack_sync_position_from_response(response_item)
        expected_position = str(item.location_code or "").strip()
        actual_position = (rcs_position_code or rcs_map_data_code or "").strip()
        if not response_item or not actual_position:
            status = "not_found"
            action = "manual_review"
        elif actual_position == expected_position:
            status = "match"
            action = "none"
        else:
            status = "mismatch"
            action = "reassign_needed"

        items.append(RackSyncQueryItem(
            **base_data,
            status=status,
            action=action,
            rcs_position_code=rcs_position_code,
            rcs_map_data_code=rcs_map_data_code,
            response_payload=sanitize(response_payload),
        ))

    db.commit()
    match_count = sum(1 for item in items if item.status == "match")
    mismatch_count = sum(1 for item in items if item.status == "mismatch")
    missing_count = sum(1 for item in items if item.status == "not_found")
    invalid_count = sum(1 for item in items if item.status == "invalid")
    error_count = sum(1 for item in items if item.status == "query_error")
    message = "Consulta RCS terminada. No se envio ningun bind/reasignacion."
    if mismatch_count:
        message += f" {mismatch_count} rack(s) requieren reasignacion en una etapa posterior."
    if missing_count:
        message += f" {missing_count} rack(s) no regresaron posicion en RCS."
    if error_count:
        message += f" {error_count} consulta(s) fallaron."

    return RackSyncQueryOut(
        ok=(mismatch_count == 0 and missing_count == 0 and invalid_count == 0 and error_count == 0),
        active_tasks_count=preview.active_tasks_count,
        query_endpoint=preview.query_endpoint,
        bind_endpoint=preview.bind_endpoint,
        map_short_name=preview.map_short_name,
        total_assigned_racks=preview.total_assigned_racks,
        match_count=match_count,
        mismatch_count=mismatch_count,
        missing_count=missing_count,
        invalid_count=invalid_count,
        error_count=error_count,
        message=message,
        items=items,
    )


def _bind_rack_sync_mismatches(db) -> RackSyncBindOut:
    comparison = _query_rack_sync_comparison(db)
    if comparison.active_tasks_count > 0:
        return RackSyncBindOut(
            ok=False,
            blocked=True,
            active_tasks_count=comparison.active_tasks_count,
            query_endpoint=comparison.query_endpoint,
            bind_endpoint=comparison.bind_endpoint,
            total_assigned_racks=comparison.total_assigned_racks,
            mismatch_count=comparison.mismatch_count,
            skipped_count=comparison.mismatch_count,
            message=f"Reasignacion bloqueada: hay {comparison.active_tasks_count} tarea(s) activa(s). No se envio bindPodAndBerth.",
            items=[
                RackSyncBindItem(**item.model_dump(), bind_status="blocked")
                for item in comparison.items
                if item.status == "mismatch"
            ],
        )

    client = _get_rcs_client_from_settings(db)
    token_code = getattr(client, "_token_code", "") or ""
    bind_endpoint = comparison.bind_endpoint
    debug_base_url, debug_endpoint = _resolve_rcs_target(db, endpoint=bind_endpoint, mode="query")
    items: List[RackSyncBindItem] = []

    for item in comparison.items:
        if item.status != "mismatch":
            if item.status in {"invalid", "query_error", "not_found"}:
                items.append(RackSyncBindItem(**item.model_dump(), bind_status="skipped"))
            continue

        bind_payload = dict(item.bind_payload or {})
        bind_payload["reqCode"] = generate_req_code_ms()
        bind_payload["reqTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        bind_payload["tokenCode"] = token_code
        bind_payload["indBind"] = "1"
        item_data = item.model_dump()
        item_data["bind_payload"] = bind_payload
        context_payload = {
            **bind_payload,
            "location_id": item.location_id,
            "rack_id": item.rack_id,
            "previous_rcs_position_code": item.rcs_position_code or item.rcs_map_data_code,
        }

        try:
            _append_debug_console_event(
                db,
                direction="sent",
                module="rack_sync_bind",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload=context_payload,
                message=f"Bind rack {item.rack_code} a posicion {item.location_code}",
                auto_commit=False,
            )
            response_payload = client.post_json_payload(bind_payload, endpoint_override=bind_endpoint)
            response_code = str(response_payload.get("code") or response_payload.get("statusCode") or "").strip()
            bind_ok = response_code in {"", "0", "200", "success", "successful"} or str(response_payload.get("message") or "").strip().lower() == "successful"
            _append_debug_console_event(
                db,
                direction="received",
                module="rack_sync_bind",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload=response_payload,
                message=str(response_payload.get("message") or f"Respuesta bind para {item.rack_code}"),
                auto_commit=False,
            )
            items.append(RackSyncBindItem(
                **item_data,
                bind_status="bind_ok" if bind_ok else "bind_rejected",
                bind_response_payload=sanitize(response_payload),
                bind_error=None if bind_ok else sanitize_text(response_payload.get("message") or response_payload),
            ))
        except RcsError as exc:
            _append_debug_console_event(
                db,
                direction="received",
                module="rack_sync_bind",
                base_url=debug_base_url,
                endpoint=debug_endpoint,
                payload={"error": str(exc), "rack_id": item.rack_id, "rack_code": item.rack_code, "position_code": item.location_code},
                message=str(exc),
                auto_commit=False,
            )
            items.append(RackSyncBindItem(**item_data, bind_status="bind_error", bind_error=str(exc)))

    db.commit()
    attempted_count = sum(1 for item in items if item.bind_status in {"bind_ok", "bind_rejected", "bind_error"})
    success_count = sum(1 for item in items if item.bind_status == "bind_ok")
    error_count = sum(1 for item in items if item.bind_status in {"bind_rejected", "bind_error"})
    skipped_count = sum(1 for item in items if item.bind_status == "skipped")
    message = "Reasignacion manual terminada."
    if attempted_count == 0:
        message = "No habia discrepancias para reasignar."
    elif error_count:
        message += f" {success_count} exitosas, {error_count} con error."
    else:
        message += f" {success_count} bind(s) enviados correctamente."

    return RackSyncBindOut(
        ok=(error_count == 0),
        active_tasks_count=comparison.active_tasks_count,
        query_endpoint=comparison.query_endpoint,
        bind_endpoint=comparison.bind_endpoint,
        total_assigned_racks=comparison.total_assigned_racks,
        mismatch_count=comparison.mismatch_count,
        attempted_count=attempted_count,
        success_count=success_count,
        error_count=error_count,
        skipped_count=skipped_count,
        message=message,
        items=items,
    )


def _rack_sync_schedule_due(db, now: Optional[datetime] = None) -> tuple[bool, str]:
    now = now or datetime.now()
    cfg = _get_rcs_config(db)
    if int(cfg.rack_sync_schedule_enabled or 0) != 1:
        return False, "programacion deshabilitada"
    schedule_time = (cfg.rack_sync_schedule_time or "12:00").strip() or "12:00"
    try:
        hour, minute = [int(part) for part in schedule_time.split(":", 1)]
    except Exception:
        return False, "hora programada invalida"
    scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    today_text = now.strftime("%Y-%m-%d")
    last_run_date = (get_setting(db, "rcs_rack_sync_schedule_last_run_date", "") or "").strip()
    if last_run_date == today_text:
        return False, "ya ejecutada hoy"
    if now < scheduled_at:
        return False, "aun no es hora"
    return True, "ejecucion programada pendiente"


def _run_scheduled_rack_sync_once() -> bool:
    if not _rack_sync_scheduler_lock.acquire(blocking=False):
        return False
    started = time.monotonic()
    try:
        with SessionLocal() as db:
            due, reason = _rack_sync_schedule_due(db)
            if not due:
                return False
            today_text = datetime.now().strftime("%Y-%m-%d")
            logger.info("Scheduled rack sync triggered reason=%s date=%s", reason, today_text)
            result = _bind_rack_sync_mismatches(db)
            if result.blocked:
                result.message = f"Sincronizacion programada omitida: {result.message}"
            else:
                result.message = f"Sincronizacion programada: {result.message}"
            set_setting(db, "rcs_rack_sync_schedule_last_run_date", today_text, source="scheduled_rack_sync")
            _append_rack_sync_event(db, action="scheduled_bind", result=result, duration_ms=int((time.monotonic() - started) * 1000))
            return True
    except Exception as exc:
        logger.exception("Scheduled rack sync failed")
        with SessionLocal() as db:
            error_result = RackSyncBindOut(
                ok=False,
                query_endpoint=_get_rcs_config(db).rack_sync_query_endpoint,
                bind_endpoint=_get_rcs_config(db).rack_sync_bind_endpoint,
                error_count=1,
                message=f"Sincronizacion programada fallo: {exc}",
            )
            _append_rack_sync_event(db, action="scheduled_bind", result=error_result, duration_ms=int((time.monotonic() - started) * 1000))
        return False
    finally:
        _rack_sync_scheduler_lock.release()


def _rack_sync_scheduler_worker():
    while not _rack_sync_scheduler_stop_event.wait(RACK_SYNC_SCHEDULER_INTERVAL_SECONDS):
        _run_scheduled_rack_sync_once()


def _start_rack_sync_scheduler():
    _rack_sync_scheduler_stop_event.clear()
    thread = threading.Thread(target=_rack_sync_scheduler_worker, daemon=True, name="rack-sync-scheduler")
    thread.start()
    return thread


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
        related = db.get(MovementOrder, related_order_id) if related_order_id else None
        response_payload = trace_call("query", [related] if related else [], client.post_json_payload, request_payload, endpoint_override=endpoint)
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


def _reconcile_cancel_return_position(db, order: MovementOrder):
    rack = db.get(Rack, order.rack_id)
    if rack is None:
        raise ValueError("Rack local inexistente")
    result = _query_pod_position_from_rcs(db, rack, module="cancel_return_reconciliation", related_order_id=order.id)
    response = result.get("response_payload") or {}
    request = result.get("request_payload") or {}
    if str(response.get("code")) != "0" or (response.get("reqCode") and response["reqCode"] != request.get("reqCode")):
        raise ValueError("Respuesta de posición sin confirmación o correlación fiable")
    nested = response.get("data")
    records = nested if isinstance(nested, list) else [nested] if isinstance(nested, dict) else []
    if len(records) > 1 or any(item.get("podCode") and item["podCode"] != rack.code for item in records if isinstance(item, dict)):
        raise ValueError("Posición ambigua o correspondiente a otro rack")
    if response.get("podCode") and response["podCode"] != rack.code:
        raise ValueError("Respuesta de posición para otro rack")
    db.execute(update(MovementOrder).where(MovementOrder.id == order.id).values(id=MovementOrder.id))
    db.expire_all()
    db.refresh(order)
    if order.status != "cancel_requested_undo":
        raise ValueError("La orden cambió durante la reconciliación")
    position = result.get("position_code") or ""
    target = _location_by_position_code(db, position)
    if target is None or target.rack_id not in (None, rack.id):
        raise ValueError("Posición final desconocida o ocupada por otro rack")
    competing = db.execute(select(MovementOrder.id).where(
        MovementOrder.id != order.id, MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
        or_(MovementOrder.rack_id == rack.id, MovementOrder.destination_cell_id == target.id,
            MovementOrder.source_cell_id == target.id),
    )).first()
    if competing:
        raise ValueError("Posición final o rack protegido por otra orden")
    previous_cells = db.execute(select(Location.id).where(Location.rack_id == rack.id)).scalars().all()
    applied, message, _ = _apply_rcs_rack_position(db, rack, position, source="cancel_return_reconciliation", related_order_id=order.id)
    if not applied:
        raise ValueError(message)
    return set(previous_cells) != {target.id}


def _apply_rcs_rack_position(db, rack: Rack, position_code: str, *, source: str, related_order_id: Optional[int] = None, actor: str = "") -> tuple[bool, str, Optional[Location]]:
    if not _position_code_is_defined(position_code):
        return False, "posicion vacia", None
    if related_order_id and source != "manual_pod_position_query":
        db.execute(update(MovementOrder).where(MovementOrder.id == related_order_id).values(id=MovementOrder.id))
        related = db.get(MovementOrder, related_order_id, populate_existing=True)
        if related and related.status in TERMINAL_LOCAL_STATUSES:
            return False, "Orden ya terminal; observación de posición descartada", None
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
    related = db.get(MovementOrder, related_order_id) if related_order_id else None
    if related and related.status == "cancel_requested_undo":
        if target_cell.rack_id not in (None, rack.id):
            raise HTTPException(status_code=409, detail="Retorno: posición ocupada por otro rack")
        if local_reconciliation_pending(related) and source != "cancel_return_reconciliation":
            raise HTTPException(status_code=409, detail="Retorno: requiere consulta final de reconciliación")
    old_cells = db.execute(select(Location).where(Location.rack_id == rack.id)).scalars().all()
    if source == "manual_pod_position_query":
        _guard_admin_inventory_edit(db, rack_ids=[rack.id, target_cell.rack_id], location_ids=[target_cell.id] + [loc.id for loc in old_cells])
        if target_cell.rack_id not in (None, rack.id):
            raise HTTPException(status_code=409, detail="La posición RCS contiene otro rack local; requiere reconciliación, no se sobrescribirá.")
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
    related_order = db.get(MovementOrder, related_order_id) if related_order_id else None
    if not related_order or related_order.status not in CANCEL_REQUEST_STATUSES:
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
                if order.status not in CANCEL_REQUEST_STATUSES:
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
                # A position timeout is not confirmation of cancellation/return.
                order.rcs_message = ((order.rcs_message or "").strip() + " | Timeout consultando posicion RCS; recursos retenidos hasta confirmacion terminal.").strip(" |")[:512]
                order.updated_at = now
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
    db.refresh(row)
    if row.dispatch_request_json and row.dispatch_status == "not_sent" and row.status == "pending_dispatch":
        # Un intento histórico interrumpido no es una nueva creación tras reinicio.
        row.dispatch_status = "uncertain"
        row.rcs_status = "dispatch_unknown"
        row.rcs_message = "Intento previo sin confirmación; recursos retenidos, requiere revisión en RCS."
        db.commit()
    if row.dispatch_request_json or row.remote_task_code or row.dispatch_status != "not_sent" or row.status != "pending_dispatch":
        return _movement_order_dispatch_out(row)
    if row.fifo_chain_select_policy == "automatic_priority" and row.fifo_chain_group_id and db.info.get("automatic_priority_order_id") != row.id:
        raise HTTPException(status_code=409, detail="Este tramo solo puede enviarse en el proceso inicial de su grupo QR.")
    logger.info("Dispatching movement order id=%s order_code=%s", row.id, row.order_code)
    previous_status = row.status or ""
    generated_payload = _movement_order_json_payload(db, row)
    payload, _payload_source = _current_movement_order_payload(row, generated_payload)
    if _payload_source == "edited":
        _validate_movement_payload(db, row, payload)
    client = _get_rcs_client_from_settings(db)
    debug_base_url, debug_endpoint = _resolve_rcs_target(db, mode="create")
    token_code = getattr(client, "_token_code", "")
    if token_code and not payload.get("tokenCode"):
        payload["tokenCode"] = token_code
    payload = RcsTaskRequest.from_payload(payload).to_payload()
    if not payload["reqCode"] or len(payload["reqCode"]) > 32:
        raise HTTPException(status_code=409, detail="reqCode de creación vacío o mayor de 32 caracteres")
    other_creation = MovementOrder.__table__.alias("other_creation")
    same_identity = select(other_creation.c.id).where(
        other_creation.c.id != row.id, other_creation.c.req_code == payload["reqCode"],
        other_creation.c.dispatch_request_json.is_not(None),
    ).exists()
    claim = db.execute(update(MovementOrder).where(
        MovementOrder.id == row.id,
        MovementOrder.status == "pending_dispatch",
        MovementOrder.dispatch_status == "not_sent",
        MovementOrder.dispatch_request_json.is_(None),
        MovementOrder.remote_task_code.is_(None),
        ~same_identity,
    ).values(req_code=payload["reqCode"], dispatch_request_json=json.dumps(sanitize(payload), ensure_ascii=False),
             dispatch_status="uncertain", rcs_status="dispatch_unknown",
             rcs_message="Creación sin confirmar; conservar recursos y revisar en RCS antes de reenviar.",
             updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(row)
    if claim.rowcount != 1:
        if row.dispatch_status == "not_sent":
            raise HTTPException(status_code=409, detail="La orden cambió o el reqCode ya fue utilizado; no se envió la tarea")
        return _movement_order_dispatch_out(row)
    logger.info("[CREATE TASK] order_id=%s order_code=%s source=api_internal status=%s rack_id=%s", row.id, row.order_code, row.status, row.rack_id)
    try:
        _append_debug_console_event(db, direction="sent", module="dispatch", base_url=debug_base_url, endpoint=debug_endpoint, payload=payload, message=f"Envío createTask para {row.order_code}")
        response = trace_call("create", [row], client.create_task, RcsTaskRequest.from_payload(payload))
        accepted = _rcs_create_task_accepted(response)
        _append_debug_console_event(db, direction="received", module="dispatch", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or f"Respuesta createTask para {row.order_code}")
        identity_matches = not response.reqCode or response.reqCode == payload["reqCode"]
        task_code = response.data.strip() if isinstance(response.data, str) else ""
        uncertain = not identity_matches or (accepted and not task_code) or (not accepted and bool(task_code))
        row.dispatch_status = "uncertain" if uncertain else ("success" if accepted else "error")
        row.dispatch_response_json = json.dumps(response.raw, ensure_ascii=False)
        row.rcs_response_json = json.dumps(response.raw, ensure_ascii=False)
        if identity_matches and task_code and not row.remote_task_code:
            row.remote_task_code = task_code
        _append_status_query_log(db, row, kind="create_task", request_payload=payload, response_payload=response.raw, message=response.message)
        row.rcs_status = "dispatch_unknown" if uncertain else ("sent" if accepted else "error")
        row.rcs_message = (f"Resultado incierto: {response.message or 'identidad remota insuficiente'}. Recursos retenidos; requiere reconciliación en RCS." if uncertain else response.message or None)
        row.dispatched_at = datetime.utcnow()
        row.rcs_last_update = row.dispatched_at
        if accepted and not uncertain and row.status == "pending_dispatch":
            row.status = "dispatched"
            _apply_pending_point_inventory(db, row)
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
        not_sent = isinstance(exc, RcsNotSentError)
        row.dispatch_status = "error" if not_sent else "uncertain"
        row.dispatch_response_json = json.dumps({"error": str(exc), "confirmed_not_sent": not_sent}, ensure_ascii=False)
        row.rcs_response_json = row.dispatch_response_json
        row.rcs_status = "error" if not_sent else "dispatch_unknown"
        row.rcs_message = str(exc) if not_sent else f"Resultado incierto: {exc}. Recursos retenidos; requiere reconciliación en RCS."
        _append_status_query_log(db, row, kind="create_task", request_payload=payload, response_payload={"error": str(exc)}, message=str(exc))
        row.dispatched_at = datetime.utcnow()
        row.rcs_last_update = row.dispatched_at
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.exception("Task dispatch failed | task_id=%s | order_code=%s | rack_id=%s | agv=%s | error=%s", row.id, row.order_code, row.rack_id, row.agv_code or "-", exc)
        if not_sent:
            _rollback_unsuccessful_order_dispatch(db, row, row.rcs_message)
        return _movement_order_dispatch_out(row)


set_fifo_chain_default_dispatch_func(_dispatch_movement_order)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".json", ".html")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


from runtime_single_worker import RuntimeLease, RuntimeLeaseMiddleware, validate_worker_configuration

_runtime_lease = RuntimeLease(engine.url.database or os.path.join(_app_root(), "runtime"))


def _acquire_runtime_lease():
    validate_worker_configuration()
    _runtime_lease.acquire()


app = FastAPI(title="AGV Orders App")
app.add_middleware(RuntimeLeaseMiddleware, lease=_runtime_lease)
app.mount("/static", NoCacheStaticFiles(directory=os.path.join(_app_root(), "static")), name="static")
app.state.task_monitor = TaskMonitor(interval_seconds=3)


@app.middleware("http")
async def log_unhandled_http_errors(request: Request, call_next):
    performance_started = time.perf_counter()
    response = None
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    client_ip = request.client.host if request.client else ""
    token = _set_config_audit_context(
        request_id=request_id,
        endpoint=str(request.url.path),
        client_ip=client_ip,
        source=f"http:{request.method}",
    )
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        logger.error(
            "%s %s - %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        raise
    finally:
        duration_ms = (time.perf_counter() - performance_started) * 1000.0
        response_size = None
        if response is not None:
            try:
                response_size = int(response.headers.get("content-length"))
            except (TypeError, ValueError):
                response_size = None
        endpoint_label = _performance_endpoint_label(request.method, request.url.path)
        performance_metrics.record("http", duration_ms, label=endpoint_label, size_bytes=response_size)
        if duration_ms > HTTP_SLOW_WARNING_MS:
            logger.warning("SLOW_HTTP method=%s path=%s duration_ms=%.1f", request.method, request.url.path, duration_ms)
        _reset_config_audit_context(token)


def _performance_database_path() -> str:
    configured = engine.url.database or DB_URL.replace("sqlite:///", "", 1)
    return os.path.abspath(configured)


def _index_health_snapshot() -> dict:
    db_path = _performance_database_path()
    with open_read_only(db_path) as connection:
        expected = check_expected_indexes(connection, EXPECTED_INDEXES)
        physical_by_table = {}
        for table_name in Base.metadata.tables:
            physical_by_table[table_name] = {
                row[1] for row in connection.execute(f'PRAGMA index_list("{table_name.replace(chr(34), chr(34) * 2)}")')
            }
    declared = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.name
    }
    physical = {name for names in physical_by_table.values() for name in names if not name.startswith("sqlite_autoindex_")}
    return {
        **expected,
        "drift": {
            "declared_and_present": sorted(declared & physical),
            "declared_but_missing": sorted(declared - physical),
            "present_but_not_declared": sorted(physical - declared),
        },
    }


@app.get("/api/admin/performance-health")
async def performance_health(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    db_path = _performance_database_path()
    wal_path = db_path + "-wal"
    with SessionLocal() as db:
        orders_total = int(db.execute(select(func.count()).select_from(MovementOrder)).scalar_one() or 0)
        orders_active = int(db.execute(select(func.count()).select_from(MovementOrder).where(MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))).scalar_one() or 0)
        scan_events_total = int(db.execute(select(func.count()).select_from(ScanEvent)).scalar_one() or 0)
        transition_logs_total = int(db.execute(select(func.count()).select_from(QrTransitionLog)).scalar_one() or 0)
    with _robot_monitor_cache_lock:
        robot_timestamp = float(_robot_monitor_cache.get("timestamp") or 0.0)
        robot_last_error = str(_robot_monitor_cache.get("last_error") or "")
        robot_available = _robot_monitor_cache.get("payload") is not None
    return {
        "database": {
            "filename": os.path.basename(db_path),
            "size_bytes": os.path.getsize(db_path) if os.path.exists(db_path) else 0,
            "wal_bytes": os.path.getsize(wal_path) if os.path.exists(wal_path) else 0,
            "integrity_last_check": None,
            "integrity_note": "Run db_diagnostics.py manually for a full integrity check.",
        },
        "orders": {"total": orders_total, "active": orders_active},
        "events": {"scan_events": scan_events_total, "qr_transition_logs": transition_logs_total},
        "websocket": {"clients": await runtime_ws_manager.count(), "last_revision": _runtime_current_revision()},
        "robot_monitor": {
            "cache_available": robot_available,
            "stale": not robot_timestamp or (time.time() - robot_timestamp) > ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS,
            "last_success": datetime.fromtimestamp(robot_timestamp, tz=timezone.utc).isoformat() if robot_timestamp else None,
            "last_error": robot_last_error or None,
        },
        "performance": {**performance_metrics.snapshot(), "rcs": {"status": "not_instrumented", "reason": "critical transport path intentionally unchanged"}},
        "indexes": _index_health_snapshot(),
        "thresholds": {
            "http_slow_ms": HTTP_SLOW_WARNING_MS,
            "sql_slow_ms": SQL_SLOW_WARNING_MS,
            "rcs_slow_ms": RCS_SLOW_WARNING_MS,
            "websocket_large_bytes": WEBSOCKET_LARGE_WARNING_BYTES,
        },
    }


@app.on_event("startup")
def on_startup():
    # Must precede migrations, recovery, monitors and schedulers.
    _acquire_runtime_lease()
    ensure_log_file()
    logger.info("Application startup initiated")
    cleanup_legacy_schema()
    init_db()
    ensure_config_audit_schema()
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
    # Completed parents are absent from the active-order monitor after restart.
    with SessionLocal() as db:
        from automatic_priority_service import recover_interrupted_groups
        recover_interrupted_groups(db)
        recover_fifo_chains(db)
    try:
        app.state.task_monitor.start()
    except Exception:
        logger.exception("Failed to start task monitor")
    try:
        app.state.rack_sync_scheduler_thread = _start_rack_sync_scheduler()
    except Exception:
        logger.exception("Failed to start rack sync scheduler")
    try:
        _robot_monitor_background_stop_event.clear()
        app.state.robot_monitor_cache_thread = _start_robot_monitor_cache_updater()
    except Exception:
        logger.exception("Failed to start robot monitor cache updater")
    logger.info("Application startup completed | supported_workers=1 | runtime_owner_pid=%s", os.getpid())


@app.on_event("startup")
async def on_startup_runtime_ws():
    global _runtime_event_loop, _runtime_event_queue
    _runtime_event_loop = asyncio.get_running_loop()
    _runtime_event_queue = asyncio.Queue(maxsize=2048)
    app.state.runtime_broadcast_task = asyncio.create_task(_runtime_broadcast_loop())
    _runtime_lease.ready = True


@app.on_event("shutdown")
def on_shutdown():
    _runtime_lease.ready = False
    logger.info("Application shutdown initiated")
    try:
        app.state.task_monitor.stop()
    except Exception:
        logger.exception("Failed to stop task monitor")
    try:
        _rack_sync_scheduler_stop_event.set()
    except Exception:
        logger.exception("Failed to stop rack sync scheduler")
    try:
        _robot_monitor_background_stop_event.set()
    except Exception:
        logger.exception("Failed to stop robot monitor cache updater")
    task = getattr(app.state, "runtime_broadcast_task", None)
    if task is not None:
        task.cancel()
    logger.info("Application shutdown completed")
    # Keep the OS lease until process exit: background threads may still unwind.
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


def _render_pda_page(terminal_code: str = ""):
    pda_path = os.path.join(_app_root(), "static", "pda.html")
    build_id = _software_build_id()
    terminal_json = json.dumps((terminal_code or "").strip())
    bootstrap_script = f"<script>window.PDA_TERMINAL_CODE_FROM_PATH={terminal_json};window.APP_BUILD_ID={json.dumps(build_id)};</script>"
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}
    try:
        with open(pda_path, "r", encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r'href="/static/pda\.css(?:\?v=[^"]*)?"', f'href="/static/pda.css?v={build_id}"', html)
        html = re.sub(r'src="/static/pda\.js(?:\?v=[^"]*)?"', f'src="/static/pda.js?v={build_id}"', html)
        html = html.replace("</head>", f"{bootstrap_script}</head>")
        return HTMLResponse(html, headers=headers)
    except Exception:
        logger.exception("Failed to render PDA page build_id=%s path=%s", build_id, pda_path)
        return FileResponse(pda_path, headers=headers)


@app.get("/pda")
def pda_root():
    return _render_pda_page("")


@app.get("/pda/{terminal_code}")
def pda_terminal(terminal_code: str):
    return _render_pda_page(terminal_code)


@app.get("/api/logs/download")
def download_logs(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    log_path = ensure_log_file()
    log_dir = os.path.dirname(str(log_path))
    base_log_names = ("app.log", "error.log", "rcs.log", "programming.log", "io.log", "qr.log")
    log_names = tuple(name for base_name in base_log_names for name in (base_name, *(f"{base_name}.{index}" for index in range(1, 6))))
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()
    included_logs = []
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for log_name in log_names:
            candidate = os.path.join(log_dir, log_name)
            if not os.path.isfile(candidate):
                continue
            with open(candidate, "r", encoding="utf-8", errors="replace") as source:
                zf.writestr(log_name, sanitize_text(source.read()))
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


@app.get("/api/admin/config-audit", response_model=List[ConfigAuditLogOut])
def admin_config_audit(
    table_name: Optional[str] = None,
    record_key: Optional[str] = None,
    field_name: Optional[str] = None,
    limit: int = 200,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    require_admin(x_admin_token)
    limit = max(1, min(1000, int(limit or 200)))
    with SessionLocal() as db:
        stmt = select(ConfigAuditLog)
        if table_name:
            stmt = stmt.where(ConfigAuditLog.table_name == table_name.strip())
        if record_key:
            stmt = stmt.where(ConfigAuditLog.record_key == record_key.strip())
        if field_name:
            stmt = stmt.where(ConfigAuditLog.field_name == field_name.strip())
        rows = db.execute(stmt.order_by(ConfigAuditLog.created_at.desc(), ConfigAuditLog.id.desc()).limit(limit)).scalars().all()
        return [
            ConfigAuditLogOut(
                id=row.id,
                created_at=row.created_at,
                table_name=row.table_name,
                record_key=row.record_key,
                field_name=row.field_name,
                old_value="[REDACTED]" if (sensitive_key(row.record_key) or sensitive_key(row.field_name)) and row.old_value else sanitize(row.old_value),
                new_value="[REDACTED]" if (sensitive_key(row.record_key) or sensitive_key(row.field_name)) and row.new_value else sanitize(row.new_value),
                source=row.source,
                endpoint=row.endpoint,
                client_ip=row.client_ip,
                request_id=row.request_id,
                related_order_id=row.related_order_id,
                related_qr_rule_id=row.related_qr_rule_id,
                note=row.note,
            )
            for row in rows
        ]


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
    logger.info(
        "Admin updating grid config rows=%s cols=%s map_layout_mode=%s agv_offset=(%s,%s) agv_scale=(%s,%s) rotation=%s",
        payload.display_rows,
        payload.display_cols,
        payload.map_layout_mode,
        payload.agv_overlay_offset_x,
        payload.agv_overlay_offset_y,
        payload.agv_overlay_scale_x,
        payload.agv_overlay_scale_y,
        payload.agv_overlay_rotation_deg,
    )
    with SessionLocal() as db:
        source = "admin_grid_config"
        set_setting(db, "display_rows", str(payload.display_rows), source=source)
        set_setting(db, "display_cols", str(payload.display_cols), source=source)
        set_setting(db, "map_layout_mode", payload.map_layout_mode, source=source)
        set_setting(db, "agv_overlay_scale_x", str(payload.agv_overlay_scale_x), source=source)
        set_setting(db, "agv_overlay_scale_y", str(payload.agv_overlay_scale_y), source=source)
        set_setting(db, "agv_overlay_offset_x", str(payload.agv_overlay_offset_x), source=source)
        set_setting(db, "agv_overlay_offset_y", str(payload.agv_overlay_offset_y), source=source)
        set_setting(db, "agv_overlay_rotation_deg", str(payload.agv_overlay_rotation_deg), source=source)
        set_setting(db, "agv_orientation_offset_deg", str(payload.agv_orientation_offset_deg), source=source)
        set_setting(db, "agv_overlay_mirror_x", str(int(payload.agv_overlay_mirror_x or 0)), source=source)
        set_setting(db, "agv_overlay_mirror_y", str(int(payload.agv_overlay_mirror_y or 0)), source=source)
        set_setting(db, "agv_icon_angle_mirror", str(int(payload.agv_icon_angle_mirror or 0)), source=source)
        set_setting(db, "runtime_refresh_seconds", str(payload.runtime_refresh_seconds), source=source)
        set_setting(db, "runtime_reconnect_seconds", str(payload.runtime_reconnect_seconds), source=source)
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
        set_setting(db, "bg_filename", safe_name, source="admin_background_upload")
        scale_x = float(get_setting(db, "bg_scale_x", "1.0") or 1.0)
        scale_y = float(get_setting(db, "bg_scale_y", "1.0") or 1.0)
        offset_x = float(get_setting(db, "bg_offset_x", "0.0") or 0.0)
        offset_y = float(get_setting(db, "bg_offset_y", "0.0") or 0.0)
    return BackgroundOut(filename=safe_name, url=f"/static/uploads/{safe_name}", scale_x=scale_x, scale_y=scale_y, offset_x=offset_x, offset_y=offset_y, scale=(scale_x + scale_y) / 2.0)


@app.post("/api/admin/background/transform", response_model=BackgroundOut)
def admin_set_background_transform(payload: BackgroundTransformIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    logger.info("Admin updating background transform scale=(%s,%s) offset=(%s,%s)", payload.scale_x, payload.scale_y, payload.offset_x, payload.offset_y)
    with SessionLocal() as db:
        source = "admin_background_transform"
        set_setting(db, "bg_scale_x", str(payload.scale_x), source=source)
        set_setting(db, "bg_scale_y", str(payload.scale_y), source=source)
        set_setting(db, "bg_scale", str((payload.scale_x + payload.scale_y) / 2.0), source=source)
        set_setting(db, "bg_offset_x", str(payload.offset_x), source=source)
        set_setting(db, "bg_offset_y", str(payload.offset_y), source=source)
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
        set_setting(db, "client_ip", payload.client_ip.strip(), source="admin_client_ip")
        return ClientIPOut(client_ip=get_setting(db, "client_ip", ""))


@app.get("/api/admin/rcs/rack-sync/preview", response_model=RackSyncPreviewOut)
def preview_rack_sync(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    with SessionLocal() as db:
        result = _build_rack_sync_preview(db)
        _append_rack_sync_event(db, action="preview", result=result, duration_ms=int((time.monotonic() - started) * 1000))
        return result


@app.post("/api/admin/rcs/rack-sync/query", response_model=RackSyncQueryOut)
def query_rack_sync(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    with SessionLocal() as db:
        result = _query_rack_sync_comparison(db)
        _append_rack_sync_event(db, action="query", result=result, duration_ms=int((time.monotonic() - started) * 1000))
        return result


@app.post("/api/admin/rcs/rack-sync/bind-mismatches", response_model=RackSyncBindOut)
def bind_rack_sync_mismatches(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    started = time.monotonic()
    with SessionLocal() as db:
        result = _bind_rack_sync_mismatches(db)
        _append_rack_sync_event(db, action="bind", result=result, duration_ms=int((time.monotonic() - started) * 1000))
        return result


@app.get("/api/admin/rcs/rack-sync/history", response_model=List[RackSyncEventOut])
def list_rack_sync_history(limit: int = 50, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return _list_rack_sync_events_out(db, limit=limit)


@app.post("/api/admin/rcs/rack-sync/schedule/run-due", response_model=RackSyncScheduleRunOut)
def run_due_rack_sync_schedule(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    ran = _run_scheduled_rack_sync_once()
    return RackSyncScheduleRunOut(ok=True, ran=ran, message="Sincronizacion programada ejecutada." if ran else "No habia sincronizacion programada pendiente.")


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
            try:
                applied, _apply_message, matched_cell = _apply_rcs_rack_position(db, rack, position_code, source="manual_pod_position_query", actor="admin")
            except HTTPException as exc:
                _append_debug_console_event(db, direction="received", module="query_pod_position", payload={"action": "local_update_blocked", "rack_id": rack.id, "position_code": position_code}, message=str(exc.detail), auto_commit=False)
                db.commit()
                raise
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
    cacheAvailable: bool = False
    stale: bool = False
    lastUpdated: Optional[str] = None
    refreshing: bool = False


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


@app.post("/api/robot-control/charge")
def charge_robot_control(payload: RobotControlIn):
    from rcs_charge_service import connected, send_charge
    code = payload.robot_code.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
        raise HTTPException(400, "Código AMR inválido")
    with SessionLocal() as db:
        # Direct query, deliberately not the monitor cache or simulator.
        cfg = _get_rcs_config(db)
        if cfg.simulator_enabled:
            raise HTTPException(409, "Charge Web no disponible en modo simulado")
        state = _get_robot_status_monitor_response(db)
        robot = next((r for r in state.robots if (r.agvCode or r.robotCode).strip() == code), None)
        if not state.ok or state.stale or robot is None or not connected(robot):
            raise HTTPException(409, "AMR desconectado o estado actual no confirmado")
        try:
            result = send_charge(base_url=cfg.web_base_url, login_endpoint=cfg.web_login_endpoint,
                charge_endpoint=cfg.web_charge_endpoint, username=cfg.web_username,
                password=get_setting(db, "rcs_web_ecs_password", ""), agv_code=code,
                verify_tls=bool(cfg.verify_tls))
        except ValueError:
            raise HTTPException(400, "Configuración RCS Web inválida")
        _append_debug_console_event(db, direction="received", module="charge",
            base_url=cfg.web_base_url, endpoint=cfg.web_charge_endpoint,
            payload={"action": "charge", "agvCode": code, "result": result["result"]},
            message=result["message"])
        return {**result, "action": "charge", "robot_code": code}


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
    await runtime_ws_manager.accept(websocket)
    try:
        has_locations = str(websocket.query_params.get("has_locations") or "").strip().lower() in {"1", "true", "yes"}
        try:
            client_locations_revision = int(websocket.query_params.get("locations_revision")) if has_locations else None
        except (TypeError, ValueError):
            client_locations_revision = None
        snapshot_message = await asyncio.to_thread(_build_runtime_snapshot_message, client_locations_revision)
        snapshot_revision = snapshot_message["revision"]
        await websocket.send_json(snapshot_message)
        await runtime_ws_manager.connect(websocket)
        for message in _runtime_events_since(snapshot_revision):
            await runtime_ws_manager.send_json(websocket, message)
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                message = {}
            if message.get("type") == "resync":
                try:
                    current_locations_revision = int(message.get("locations_revision")) if message.get("has_locations") else None
                except (TypeError, ValueError):
                    current_locations_revision = None
                resync_message = await asyncio.to_thread(_build_runtime_snapshot_message, current_locations_revision)
                await runtime_ws_manager.send_json(websocket, resync_message)
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
    rows = db.execute(select(
        Location.id,
        Location.x,
        Location.y,
        Location.status,
        Location.is_visible,
        Location.updated_at,
        Location.code,
        Location.enabled,
        Location.note,
        Location.area_id,
        Location.rack_id,
        Location.free_enabled,
        Location.free_x,
        Location.free_y,
        Location.free_w,
        Location.free_h,
    )).all()
    area_ids = {row.area_id for row in rows if row.area_id is not None}
    rack_ids = {row.rack_id for row in rows if row.rack_id is not None}
    area_name_by_id = dict(db.execute(
        select(Area.id, Area.name).where(Area.id.in_(area_ids))
    ).all()) if area_ids else {}
    rack_by_id = {
        row.id: row for row in db.execute(
            select(Rack.id, Rack.code, Rack.status).where(Rack.id.in_(rack_ids))
        ).all()
    } if rack_ids else {}
    active_order_by_rack = {}
    if rack_ids:
        order_rows = db.execute(
            select(
                MovementOrder.rack_id,
                MovementOrder.id,
                MovementOrder.remote_task_code,
                MovementOrder.order_code,
            )
            .where(
                MovementOrder.rack_id.in_(rack_ids),
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            )
            .order_by(MovementOrder.rack_id, MovementOrder.created_at.desc(), MovementOrder.id.desc())
        ).all()
        for order in order_rows:
            active_order_by_rack.setdefault(order.rack_id, order)

    result = []
    for row in rows:
        rack = rack_by_id.get(row.rack_id)
        order = active_order_by_rack.get(row.rack_id)
        is_reserved = bool(order) or bool(rack and rack_status_is_reserved(rack.status))
        task_identifier = None
        if order:
            task_identifier = (order.remote_task_code or "").strip() or (order.order_code or "").strip() or str(order.id)
        result.append(LocationOut(
            id=row.id,
            x=row.x,
            y=row.y,
            status=int(row.status or 0),
            is_visible=row.is_visible,
            updated_at=row.updated_at,
            code=row.code,
            enabled=row.enabled,
            note=row.note,
            area_id=row.area_id,
            rack_id=row.rack_id,
            free_enabled=int(row.free_enabled or 0),
            free_x=row.free_x,
            free_y=row.free_y,
            free_w=row.free_w,
            free_h=row.free_h,
            area_name=area_name_by_id.get(row.area_id),
            rack_code=rack.code if rack else None,
            reservation_status="Reservado" if is_reserved else "No reservado",
            reservation_task_id=order.id if order else None,
            reservation_task_identifier=task_identifier,
            reservation_rack_id=rack.id if rack else None,
            reservation_rack_code=rack.code if rack else None,
        ))
    return result


MOVEMENT_ORDER_SUMMARY_COLUMNS = (
    MovementOrder.updated_at,
    MovementOrder.id,
    MovementOrder.order_code,
    MovementOrder.order_type,
    MovementOrder.status,
    MovementOrder.priority,
    MovementOrder.agv_code,
    MovementOrder.task_typ,
    MovementOrder.comment,
    MovementOrder.created_at,
    MovementOrder.created_by,
    MovementOrder.rack_id,
    MovementOrder.material_group_id,
    MovementOrder.source_area_id,
    MovementOrder.destination_area_id,
    MovementOrder.source_cell_id,
    MovementOrder.destination_cell_id,
    MovementOrder.dispatch_status,
    MovementOrder.remote_task_code,
    MovementOrder.req_code,
    MovementOrder.rcs_status,
    MovementOrder.rcs_message,
    MovementOrder.cancel_source,
    MovementOrder.cancel_reason,
    MovementOrder.closed_by,
    MovementOrder.closed_at,
    MovementOrder.release_source,
    MovementOrder.dispatched_at,
    MovementOrder.rcs_last_update,
    MovementOrder.status_query_checked_at,
    MovementOrder.rcs_status_changed_at,
    MovementOrder.route_mode,
    MovementOrder.trmx_group_id,
    MovementOrder.trmx_step,
    MovementOrder.trmx_total_steps,
    MovementOrder.trmx_parent_order_id,
    MovementOrder.trmx_status,
    MovementOrder.trmx_select_policy,
    MovementOrder.fifo_chain_group_id,
    MovementOrder.fifo_chain_step,
    MovementOrder.fifo_chain_total_steps,
    MovementOrder.fifo_chain_parent_order_id,
    MovementOrder.fifo_chain_status,
    MovementOrder.fifo_chain_select_policy,
)


def _movement_order_summary_out(
    row,
    *,
    rack_by_id: dict,
    location_by_id: dict,
    area_by_id: dict,
    material_by_id: dict,
    current_location_by_rack_id: dict,
) -> MovementOrderSummaryOut:
    rack = rack_by_id.get(row.rack_id) if row.rack_id else None
    source_cell = location_by_id.get(row.source_cell_id) if row.source_cell_id else None
    destination_cell = location_by_id.get(row.destination_cell_id) if row.destination_cell_id else None
    source_area = area_by_id.get(row.source_area_id) if row.source_area_id else None
    destination_area = area_by_id.get(row.destination_area_id) if row.destination_area_id else None
    material = material_by_id.get(row.material_group_id) if row.material_group_id else None
    current_cell_row = current_location_by_rack_id.get(row.rack_id) if row.rack_id else None
    return MovementOrderSummaryOut(
        updated_at=row.updated_at,
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
        rack_code=rack.code if rack else None,
        rack_missing=bool(row.rack_id and rack is None),
        material_group_id=row.material_group_id,
        material_group_name=material.name if material else None,
        source_area_id=row.source_area_id,
        source_area_name=source_area.name if source_area else None,
        destination_area_id=row.destination_area_id,
        destination_area_name=destination_area.name if destination_area else None,
        source_cell_id=row.source_cell_id,
        destination_cell_id=row.destination_cell_id,
        source_cell={"id": source_cell.id, "x": source_cell.x, "y": source_cell.y, "code": source_cell.code, "area_id": source_cell.area_id} if source_cell else {},
        destination_cell={"id": destination_cell.id, "x": destination_cell.x, "y": destination_cell.y, "code": destination_cell.code, "area_id": destination_cell.area_id} if destination_cell else {},
        current_cell={"id": current_cell_row.id, "x": current_cell_row.x, "y": current_cell_row.y, "code": current_cell_row.code} if current_cell_row else None,
        dispatch_status=row.dispatch_status or "not_sent",
        remote_task_code=row.remote_task_code,
        req_code=row.req_code,
        rcs_status=row.rcs_status,
        rcs_message=sanitize(row.rcs_message),
        cancel_source=row.cancel_source,
        cancel_reason=row.cancel_reason,
        closed_by=row.closed_by,
        closed_at=row.closed_at,
        release_source=row.release_source,
        dispatched_at=row.dispatched_at,
        rcs_last_update=row.rcs_last_update,
        status_query_checked_at=row.status_query_checked_at,
        rcs_status_changed_at=row.rcs_status_changed_at,
        route_mode=_normalize_route_mode(row.route_mode),
        trmx_group_id=row.trmx_group_id,
        trmx_step=row.trmx_step,
        trmx_total_steps=row.trmx_total_steps,
        trmx_parent_order_id=row.trmx_parent_order_id,
        trmx_status=row.trmx_status,
        trmx_select_policy=row.trmx_select_policy,
        fifo_chain_group_id=row.fifo_chain_group_id,
        fifo_chain_step=row.fifo_chain_step,
        fifo_chain_total_steps=row.fifo_chain_total_steps,
        fifo_chain_parent_order_id=row.fifo_chain_parent_order_id,
        fifo_chain_status=row.fifo_chain_status,
        fifo_chain_select_policy=row.fifo_chain_select_policy,
        can_simulate_complete=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
        can_undo=row.status in {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"},
    )


def _movement_order_summaries_from_rows(db, rows) -> List[MovementOrderSummaryOut]:
    lookup = _build_movement_order_lookup_maps(db, rows)
    return [_movement_order_summary_out(row, **lookup) for row in rows]


def _list_movement_orders_out(db) -> List[MovementOrderSummaryOut]:
    active_rows = db.execute(
        select(*MOVEMENT_ORDER_SUMMARY_COLUMNS)
        .where(MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))
    ).all()
    recent_terminal_rows = db.execute(
        select(*MOVEMENT_ORDER_SUMMARY_COLUMNS)
        .where(MovementOrder.status.notin_(RACK_RESERVATION_ACTIVE_STATUSES))
        .order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())
        .limit(RUNTIME_RECENT_ORDER_LIMIT)
    ).all()
    rows_by_id = {row.id: row for row in active_rows}
    rows_by_id.update({row.id: row for row in recent_terminal_rows})
    rows = sorted(
        rows_by_id.values(),
        key=lambda row: (row.created_at or datetime.min, row.id or 0),
        reverse=True,
    )
    return _movement_order_summaries_from_rows(db, rows)


def _list_movement_orders_page_out(db, *, limit: int, offset: int) -> MovementOrderPageOut:
    total = int(db.execute(select(func.count()).select_from(MovementOrder)).scalar_one() or 0)
    rows = db.execute(
        select(*MOVEMENT_ORDER_SUMMARY_COLUMNS)
        .order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return MovementOrderPageOut(
        items=_movement_order_summaries_from_rows(db, rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def _list_debug_console_events_out(db, limit: int = 200) -> List[DebugConsoleEventOut]:
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = db.execute(
        select(DebugConsoleEvent)
        .order_by(DebugConsoleEvent.created_at.desc(), DebugConsoleEvent.id.desc())
        .limit(safe_limit)
    ).scalars().all()
    return [_debug_console_event_out(row) for row in rows]


def _get_robot_status_monitor_response(db) -> RobotMonitorResponse:
    query_started = time.perf_counter()
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
        result = RobotMonitorResponse(
            ok=response_code == "0",
            code=response_code,
            message=sanitize_text(response_message),
            reqCode=str(response.get("reqCode", payload.get("reqCode", "")) or ""),
            robots=items,
        )
        logger.info(
            "RCS robot status query completed | duration_ms=%.1f | ok=%s | robots=%s",
            (time.perf_counter() - query_started) * 1000.0,
            result.ok,
            len(result.robots),
        )
        return result
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
        logger.warning(
            "RCS robot status query failed | duration_ms=%.1f | error=%s",
            (time.perf_counter() - query_started) * 1000.0,
            exc,
        )
        return RobotMonitorResponse(ok=False, code="", message=str(exc), reqCode=payload["reqCode"], robots=[])


def _get_cached_robot_status_monitor_response(db, force: bool = False, max_age_seconds: Optional[float] = None) -> Optional[RobotMonitorResponse]:
    if max_age_seconds is None:
        try:
            max_age_seconds = float(get_setting(db, "rcs_agv_monitor_interval_seconds", str(ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)) or ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)
        except Exception:
            max_age_seconds = ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS
    if force:
        return _refresh_robot_monitor_cache(db, wait_if_busy=False)
    now_ts = time.time()
    with _robot_monitor_cache_lock:
        cached_payload = _robot_monitor_cache.get("payload")
        cached_ts = float(_robot_monitor_cache.get("timestamp") or 0.0)
        last_error = str(_robot_monitor_cache.get("last_error") or "")
    if cached_payload is None:
        return RobotMonitorResponse(
            ok=False,
            message=last_error or "Sin información de robots en caché todavía.",
            cacheAvailable=False,
            stale=True,
            refreshing=_robot_monitor_refresh_lock.locked(),
        )
    response = RobotMonitorResponse(**cached_payload)
    response.cacheAvailable = True
    response.stale = (now_ts - cached_ts) > float(max_age_seconds)
    response.lastUpdated = datetime.fromtimestamp(cached_ts, tz=timezone.utc).isoformat() if cached_ts else None
    response.refreshing = _robot_monitor_refresh_lock.locked()
    if last_error:
        response.message = f"Último estado conocido. Error al actualizar: {last_error}"
    return response


def _refresh_robot_monitor_cache(db, wait_if_busy: bool = False) -> RobotMonitorResponse:
    acquired = _robot_monitor_refresh_lock.acquire(blocking=wait_if_busy)
    if not acquired:
        cached = _get_cached_robot_status_monitor_response(db, force=False)
        cached.refreshing = True
        if cached.cacheAvailable and not cached.message:
            cached.message = "Actualización de robots en curso; mostrando el último estado conocido."
        return cached
    try:
        response = _get_robot_status_monitor_response(db)
        if response.ok:
            cache_ts = time.time()
            stored = response.model_copy(update={
                "cacheAvailable": True,
                "stale": False,
                "lastUpdated": datetime.fromtimestamp(cache_ts, tz=timezone.utc).isoformat(),
                "refreshing": False,
            })
            with _robot_monitor_cache_lock:
                _robot_monitor_cache["timestamp"] = cache_ts
                _robot_monitor_cache["payload"] = stored.model_dump()
                _robot_monitor_cache["last_error"] = ""
            publish_runtime_event("robot_monitor_updated", stored.model_dump(mode="json"))
            return stored
        with _robot_monitor_cache_lock:
            _robot_monitor_cache["last_error"] = response.message or "Error desconocido al consultar RCS."
        cached = _get_cached_robot_status_monitor_response(db, force=False)
        cached.refreshing = False
        result = cached if cached.cacheAvailable else response
        publish_runtime_event("robot_monitor_updated", result.model_dump(mode="json"))
        return result
    finally:
        _robot_monitor_refresh_lock.release()


def _robot_monitor_cache_update_once() -> float:
    interval_seconds = ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS
    with SessionLocal() as db:
        try:
            interval_seconds = max(1.0, float(get_setting(db, "rcs_agv_monitor_interval_seconds", str(ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS)) or ROBOT_MONITOR_CACHE_MAX_AGE_SECONDS))
            cfg = _get_rcs_config(db)
            if int(cfg.enable_amr_monitor or 0) == 1:
                _refresh_robot_monitor_cache(db, wait_if_busy=False)
        except Exception:
            logger.exception("Robot monitor background cache update failed")
    return interval_seconds


def _robot_monitor_cache_update_loop():
    while not _robot_monitor_background_stop_event.is_set():
        interval_seconds = _robot_monitor_cache_update_once()
        _robot_monitor_background_stop_event.wait(interval_seconds)


def _start_robot_monitor_cache_updater() -> threading.Thread:
    existing = getattr(app.state, "robot_monitor_cache_thread", None)
    if existing is not None and existing.is_alive():
        return existing
    thread = threading.Thread(target=_robot_monitor_cache_update_loop, name="robot-monitor-cache", daemon=True)
    thread.start()
    return thread


def _build_runtime_snapshot_out(db, debug_limit: int = 200, include_robot_monitor: int = 1, include_locations: int = 1) -> RuntimeSnapshotOut:
    _maybe_auto_release_stuck_cancel_return_orders()
    robot_monitor = _get_cached_robot_status_monitor_response(db, force=False) if int(include_robot_monitor or 0) == 1 else None
    orders_total = int(db.execute(select(func.count()).select_from(MovementOrder)).scalar_one() or 0)
    return RuntimeSnapshotOut(
        orders=_list_movement_orders_out(db),
        orders_total=orders_total,
        recent_order_limit=RUNTIME_RECENT_ORDER_LIMIT,
        locations=_list_locations_out(db) if int(include_locations or 0) == 1 else [],
        debug_log=_list_debug_console_events_out(db, limit=debug_limit),
        robot_monitor=robot_monitor,
    )


def _build_runtime_snapshot_payload(debug_limit: int = 200, include_robot_monitor: int = 1, include_locations: int = 1) -> dict:
    started = time.perf_counter()
    with SessionLocal() as db:
        snapshot = _build_runtime_snapshot_out(db, debug_limit=debug_limit, include_robot_monitor=include_robot_monitor, include_locations=include_locations)
        serialization_started = time.perf_counter()
        payload = snapshot.model_dump(mode="json")
        serialization_ms = (time.perf_counter() - serialization_started) * 1000.0
        performance_metrics.record("serialization", serialization_ms, label="runtime_snapshot")
        performance_metrics.record("runtime_snapshot", (time.perf_counter() - started) * 1000.0, label="build")
        return payload


def _build_runtime_snapshot_message(client_locations_revision: Optional[int] = None) -> dict:
    for _attempt in range(3):
        server_locations_revision = _locations_current_revision()
        locations_match = client_locations_revision is not None and int(client_locations_revision) == server_locations_revision
        snapshot_revision = _runtime_current_revision()
        payload = _build_runtime_snapshot_payload(300, 1, 0 if locations_match else 1)
        if server_locations_revision == _locations_current_revision():
            break
        client_locations_revision = None
    else:
        return {"type": "runtime_resync_required", "revision": _runtime_current_revision(), "reason": "locations changed during snapshot"}
    return {
        "type": "runtime_snapshot",
        "revision": snapshot_revision,
        "locations_revision": server_locations_revision,
        "locations_included": not locations_match,
        "payload": payload,
    }


def _runtime_related_payload(db, rack_id: Optional[int]) -> dict:
    if not rack_id:
        return {"rack": None, "location": None}
    rack = db.get(Rack, rack_id)
    location = db.execute(select(Location).where(Location.rack_id == rack_id)).scalar_one_or_none()
    return {
        "rack": _rack_out(db, rack).model_dump(mode="json") if rack else None,
        "location": _location_out(db, location).model_dump(mode="json") if location else None,
    }


def _runtime_change_to_event(change: dict) -> Optional[tuple[str, dict]]:
    kind = str(change.get("kind") or "")
    entity_id = int(change.get("id") or 0)
    action = str(change.get("action") or "updated")
    if not entity_id:
        return None
    if action == "deleted":
        event_type = {
            "order": "order_deleted", "location": "location_deleted",
            "rack": "rack_deleted", "debug_event": "debug_event_deleted",
            "catalog": "catalog_invalidated",
        }.get(kind)
        payload = {"id": entity_id}
        if kind == "location":
            payload["locations_revision"] = _locations_current_revision()
        return (event_type, payload) if event_type else None
    with SessionLocal() as db:
        if kind == "order":
            row = db.execute(select(*MOVEMENT_ORDER_SUMMARY_COLUMNS).where(MovementOrder.id == entity_id)).first()
            if not row:
                return "order_deleted", {"id": entity_id}
            order = _movement_order_summaries_from_rows(db, [row])[0]
            payload = {"order": order.model_dump(mode="json"), "action": action}
            payload.update(_runtime_related_payload(db, row.rack_id))
            return ("order_created" if action == "created" else "order_updated"), payload
        if kind == "location":
            row = db.get(Location, entity_id)
            location_revision = _locations_current_revision()
            return ("location_updated", {"location": _location_out(db, row).model_dump(mode="json"), "locations_revision": location_revision}) if row else ("location_deleted", {"id": entity_id, "locations_revision": location_revision})
        if kind == "rack":
            row = db.get(Rack, entity_id)
            if not row:
                return "rack_deleted", {"id": entity_id}
            payload = {"rack": _rack_out(db, row).model_dump(mode="json")}
            location = db.execute(select(Location).where(Location.rack_id == entity_id)).scalar_one_or_none()
            payload["location"] = _location_out(db, location).model_dump(mode="json") if location else None
            return "rack_updated", payload
        if kind == "debug_event":
            row = db.get(DebugConsoleEvent, entity_id)
            return ("debug_event_added", {"event": _debug_console_event_out(row).model_dump(mode="json")}) if row else None
        if kind == "catalog":
            return "catalog_invalidated", {"id": entity_id}
    return None


async def _runtime_broadcast_loop():
    global _runtime_queue_overflow
    while True:
        try:
            if _runtime_queue_overflow:
                _runtime_queue_overflow = False
                await runtime_ws_manager.broadcast_json({"type": "runtime_resync_required", "revision": _runtime_current_revision()})
            try:
                item = await asyncio.wait_for(_runtime_event_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                if await runtime_ws_manager.count() > 0:
                    await runtime_ws_manager.broadcast_json({"type": "heartbeat", "revision": _runtime_current_revision()})
                continue
            if "_change" in item:
                if await runtime_ws_manager.count() == 0:
                    continue
                event_data = await asyncio.to_thread(_runtime_change_to_event, item["_change"])
                if event_data:
                    item = publish_runtime_event(event_data[0], event_data[1], enqueue=False)
                else:
                    continue
            await runtime_ws_manager.broadcast_json(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Runtime incremental broadcast failed")


@app.get("/api/runtime-snapshot", response_model=RuntimeSnapshotOut)
def get_runtime_snapshot(debug_limit: int = 200, include_robot_monitor: int = 0, include_locations: int = 1):
    with SessionLocal() as db:
        return _build_runtime_snapshot_out(db, debug_limit=debug_limit, include_robot_monitor=include_robot_monitor, include_locations=include_locations)


@app.get("/api/locations", response_model=List[LocationOut], response_model_exclude_none=True)
def list_locations(response: Response):
    rows = []
    for _attempt in range(3):
        revision_before = _locations_current_revision()
        with SessionLocal() as db:
            _auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow())
            rows = _list_locations_out(db)
        revision_after = _locations_current_revision()
        if revision_before == revision_after:
            response.headers["X-Locations-Revision"] = str(revision_after)
            return rows
    # A client receiving revision 0 must request the full snapshot fallback.
    response.headers["X-Locations-Revision"] = "0"
    return rows


@app.get("/api/locations-revision")
def get_locations_revision():
    return {"revision": _locations_current_revision(), "count": _locations_current_count()}


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
    token, expires_at = _create_admin_token(24)
    ADMIN_TOKENS[token] = expires_at
    logger.info("Admin login successful token_prefix=%s", token[:6])
    return {"token": token, "expires_hours": 24}


@app.get("/api/admin/session")
def admin_session(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    return {"authenticated": True}


@app.post("/api/admin/change-password")
def admin_change_password(body: AdminChangePassword, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stored = get_setting(db, "admin_password_hash", "")
        if _sha256(body.old_password) != stored:
            raise HTTPException(status_code=400, detail="Contraseña anterior incorrecta")
        set_setting(db, "admin_password_hash", _sha256(body.new_password), source="admin_change_password")
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
        set_setting(db, "restore_pending_restart", "1" if value else "0", source="backup_restore")
        if value:
            set_setting(db, "last_restore_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), source="backup_restore")


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
        return _catalog_out(db)


@app.post("/api/fifo/validate", response_model=FifoPreviewOut)
def validate_fifo_request(body: FifoRequestIn):
    with SessionLocal() as db:
        logger.info("FIFO validate source_area_id=%s destination_area_id=%s material_group_id=%s priority=%s", body.source_area_id, body.destination_area_id, body.material_group_id, body.priority)
        selection = resolve_fifo_request(db, body.source_area_id, body.destination_area_id, body.material_group_id, body.priority)
        return FifoPreviewOut(**build_fifo_preview_payload(selection))


@app.post("/api/scan/preview", response_model=ScanPreviewOut)
def scan_preview(body: ScanPreviewIn):
    with SessionLocal() as db:
        logger.info("Scan preview scanner_code=%s created_by=%s", body.scanner_code, body.created_by or "-")
        return ScanPreviewOut(**preview_scan(db, body.scanner_code, body.qr_value, body.created_by))


@app.post("/api/scan/execute", response_model=ScanPreviewOut)
def scan_execute(body: ScanPreviewIn):
    with SessionLocal() as db:
        logger.info("Scan execute scanner_code=%s created_by=%s", body.scanner_code, body.created_by or "-")
        result = execute_scan(
            db,
            body.scanner_code,
            body.qr_value,
            body.created_by,
            dispatch_func=_dispatch_movement_order,
            rollback_func=_rollback_unsuccessful_order_dispatch,
            audit_rack_func=_audit_rack_reservation_change,
        )
        return ScanPreviewOut(**result)


def _terminal_config_http_error(exc: ScanPreviewError) -> HTTPException:
    raw = str(getattr(exc, "message", "") or "")
    normalized = raw.strip().lower()
    if "terminal_code requerido" in normalized:
        return HTTPException(status_code=400, detail="terminal_code requerido")
    if "terminal_code no registrado" in normalized:
        return HTTPException(status_code=404, detail="Terminal no encontrada.")
    if "terminal inactivo" in normalized:
        return HTTPException(status_code=403, detail="Terminal inactiva.")
    if "x-terminal-key invalido" in normalized or "x-terminal-key inválido" in normalized:
        return HTTPException(status_code=401, detail="X-Terminal-Key inválido.")
    if "scanner asociado" in normalized:
        return HTTPException(status_code=409, detail="Scanner asociado no disponible.")
    return HTTPException(status_code=400, detail=raw or "Terminal no disponible.")


@app.get("/api/scan/terminal-config/{terminal_code}", response_model=ScanTerminalConfigOut)
def scan_terminal_config(terminal_code: str, x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key")):
    normalized_code = str(terminal_code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="terminal_code requerido")
    with SessionLocal() as db:
        try:
            terminal, station = resolve_scan_terminal(db, normalized_code, x_terminal_key)
        except ScanPreviewError as exc:
            http_exc = _terminal_config_http_error(exc)
            logger.info(
                "Scan terminal config rejected terminal_code=%s status=%s reason=%s",
                normalized_code,
                http_exc.status_code,
                http_exc.detail,
            )
            raise http_exc
        logger.info("Scan terminal config ok terminal_code=%s scanner_code=%s", terminal.terminal_code, station.scanner_code)
        return ScanTerminalConfigOut(
            terminal_code=terminal.terminal_code,
            terminal_name=terminal.name,
            is_active=int(terminal.is_active or 0) == 1,
            mode=terminal.mode or "preview",
            allow_execute=int(terminal.allow_execute or 0) == 1,
            require_preview=int(terminal.require_preview or 0) == 1,
            scanner=ScanTerminalScannerConfigOut(
                scanner_code=station.scanner_code,
                scanner_name=station.name,
                is_active=int(station.is_active or 0) == 1,
                allow_execute=int(station.allow_execute or 0) == 1,
            ),
        )


@app.get("/api/scan/available-qr-rules/{terminal_code}", response_model=List[PdaQrRuleOut])
def scan_available_qr_rules(terminal_code: str, x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key")):
    normalized_code = str(terminal_code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="terminal_code requerido")
    with SessionLocal() as db:
        try:
            terminal, station = resolve_scan_terminal(db, normalized_code, x_terminal_key)
        except ScanPreviewError as exc:
            http_exc = _terminal_config_http_error(exc)
            logger.info(
                "PDA QR catalog rejected terminal_code=%s status=%s reason=%s",
                normalized_code,
                http_exc.status_code,
                http_exc.detail,
            )
            raise http_exc

        rows = db.execute(_available_pda_qr_rules_stmt()).scalars().all()
        logger.info(
            "PDA QR catalog ok terminal_code=%s scanner_code=%s count=%s",
            terminal.terminal_code,
            station.scanner_code,
            len(rows),
        )
        return [_pda_qr_rule_out(db, row) for row in rows]


@app.get("/api/scan/available-qr-rules/{terminal_code}/{qr_action_rule_id}/image")
def scan_available_qr_rule_image(
    terminal_code: str,
    qr_action_rule_id: int,
    size: int = 240,
    x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key"),
):
    normalized_code = str(terminal_code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="terminal_code requerido")
    with SessionLocal() as db:
        try:
            terminal, station = resolve_scan_terminal(db, normalized_code, x_terminal_key)
        except ScanPreviewError as exc:
            http_exc = _terminal_config_http_error(exc)
            logger.info(
                "PDA QR image rejected terminal_code=%s rule_id=%s status=%s reason=%s",
                normalized_code,
                qr_action_rule_id,
                http_exc.status_code,
                http_exc.detail,
            )
            raise http_exc

        row = db.execute(
            _available_pda_qr_rules_stmt().where(QrActionRule.id == qr_action_rule_id)
        ).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no disponible para esta terminal")

        logger.info(
            "PDA QR image ok terminal_code=%s scanner_code=%s rule_id=%s",
            terminal.terminal_code,
            station.scanner_code,
            row.id,
        )
        return StreamingResponse(
            _generate_qr_png_buffer(row.qr_value, size),
            media_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0"},
        )


@app.get("/api/scan/terminal/{terminal_code}/history", response_model=List[PdaScanHistoryItemOut])
def scan_terminal_history(
    terminal_code: str,
    limit: int = 20,
    x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key"),
):
    normalized_code = str(terminal_code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="terminal_code requerido")
    safe_limit = max(1, min(int(limit or 20), 50))
    with SessionLocal() as db:
        try:
            terminal, station = resolve_scan_terminal(db, normalized_code, x_terminal_key)
        except ScanPreviewError as exc:
            http_exc = _terminal_config_http_error(exc)
            logger.info(
                "PDA_HISTORY_REQUEST terminal_code=%s terminal_id=- scanner_code=- result_count=0 error=%s",
                normalized_code,
                http_exc.detail,
            )
            raise http_exc

        rows = db.execute(
            select(ScanEvent)
            .where(ScanEvent.terminal_id == terminal.id)
            .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
            .limit(safe_limit)
        ).scalars().all()
        order_ids = sorted({row.movement_order_id for row in rows if row.movement_order_id})
        order_by_id = {}
        movement_lookup = {}
        if order_ids:
            orders = db.execute(select(MovementOrder).where(MovementOrder.id.in_(order_ids))).scalars().all()
            order_by_id = {row.id: row for row in orders}
            movement_lookup = _build_movement_order_lookup_maps(db, orders)
            area_by_id = movement_lookup.setdefault("area_by_id", {})
            location_by_id = movement_lookup.get("location_by_id") or {}
            extra_area_ids = {
                getattr(location, "area_id", None)
                for location in location_by_id.values()
                if getattr(location, "area_id", None)
            }
            cancel_return_area_id = getattr(station, "cancel_return_area_id", None)
            if cancel_return_area_id:
                extra_area_ids.add(cancel_return_area_id)
            missing_area_ids = sorted(area_id for area_id in extra_area_ids if area_id not in area_by_id)
            if missing_area_ids:
                area_rows = db.execute(select(Area).where(Area.id.in_(missing_area_ids))).scalars().all()
                area_by_id.update({row.id: row for row in area_rows})
        elif getattr(station, "cancel_return_area_id", None):
            area = db.execute(select(Area).where(Area.id == station.cancel_return_area_id)).scalar_one_or_none()
            movement_lookup = {"area_by_id": {area.id: area} if area else {}}
        result = [_pda_scan_history_item_out(db, row, order_by_id, movement_lookup, station) for row in rows]
        logger.info(
            "PDA_HISTORY_REQUEST terminal_code=%s terminal_id=%s scanner_code=%s result_count=%s",
            terminal.terminal_code,
            terminal.id,
            station.scanner_code,
            len(result),
        )
        return result


@app.post("/api/scan/terminal/{terminal_code}/movement-orders/{order_id}/cancel", response_model=PdaMovementOrderCancelOut)
def scan_terminal_cancel_movement_order(
    terminal_code: str,
    order_id: int,
    body: PdaMovementOrderCancelIn,
    x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key"),
):
    normalized_code = str(terminal_code or "").strip()
    if not normalized_code:
        raise HTTPException(status_code=400, detail="terminal_code requerido")
    with SessionLocal() as db:
        terminal = None
        station = None
        scan_event = None
        return_area_id = None
        cancel_return_target = None
        matter_area = ""
        try:
            try:
                terminal, station = resolve_scan_terminal(db, normalized_code, x_terminal_key)
            except ScanPreviewError as exc:
                raise _terminal_config_http_error(exc)

            row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
            if not row:
                raise HTTPException(status_code=404, detail="Orden no encontrada")

            scan_event = db.execute(
                select(ScanEvent)
                .where(ScanEvent.terminal_id == terminal.id)
                .where(ScanEvent.movement_order_id == order_id)
                .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
            ).scalars().first()
            if not scan_event:
                raise HTTPException(status_code=403, detail="La orden no pertenece al historial de esta terminal PDA.")

            _ensure_pda_order_can_cancel(db, row)

            action = body.action
            if action == "cancel_return":
                cancel_return_target = resolve_cancel_return_target(db, station, row)
                return_area_id = cancel_return_target["return_area_id"]
                matter_area = cancel_return_target["matter_area"]
                logger.info(
                    "PDA_CANCEL_RETURN_TARGET movement_order_id=%s order_code=%s fifo_chain_step=%s/%s cancel_return_mode=%s scanner_code=%s source_cell_id=%s source_cell_code=%s source_area_id=%s source_area_name=%s matter_area=%s",
                    row.id,
                    row.order_code,
                    getattr(row, "fifo_chain_step", None) or "-",
                    getattr(row, "fifo_chain_total_steps", None) or "-",
                    cancel_return_target.get("mode"),
                    station.scanner_code,
                    cancel_return_target.get("source_cell_id") or getattr(row, "source_cell_id", None) or "-",
                    cancel_return_target.get("source_cell_code") or "-",
                    cancel_return_target.get("source_area_id") or cancel_return_target.get("return_area_id") or "-",
                    cancel_return_target.get("source_area_name") or cancel_return_target.get("return_area_name") or "-",
                    matter_area or "",
                )
                row = _execute_cancel_for_order(
                    db,
                    row,
                    "1",
                    matter_area,
                    undo_on_accept=True,
                    source="pda",
                    actor=f"pda:{terminal.terminal_code}",
                )
            else:
                row = _execute_cancel_for_order(
                    db,
                    row,
                    "0",
                    "",
                    undo_on_accept=False,
                    source="pda",
                    actor=f"pda:{terminal.terminal_code}",
                )
            response = _pda_cancel_response(db, action, row, cancel_return_target)
            logger.info(
                "PDA_CANCEL_REQUEST terminal_code=%s scanner_code=%s scan_event_id=%s movement_order_id=%s action=%s return_area_id=%s matter_area=%s result_status=%s rcs_status=%s error=-",
                terminal.terminal_code,
                station.scanner_code,
                scan_event.id,
                order_id,
                action,
                return_area_id or "-",
                matter_area or "",
                response.status,
                response.rcs_status or "-",
            )
            return response
        except ScanPreviewError as exc:
            logger.info(
                "PDA_CANCEL_REQUEST terminal_code=%s scanner_code=%s scan_event_id=%s movement_order_id=%s action=%s return_area_id=%s matter_area=%s result_status=- rcs_status=- error=%s",
                normalized_code,
                station.scanner_code if station else "-",
                scan_event.id if scan_event else "-",
                order_id,
                getattr(body, "action", "-"),
                return_area_id or "-",
                matter_area or "",
                exc.message,
            )
            raise HTTPException(status_code=400, detail=exc.message)
        except HTTPException as exc:
            logger.info(
                "PDA_CANCEL_REQUEST terminal_code=%s scanner_code=%s scan_event_id=%s movement_order_id=%s action=%s return_area_id=%s matter_area=%s result_status=- rcs_status=- error=%s",
                normalized_code,
                station.scanner_code if station else "-",
                scan_event.id if scan_event else "-",
                order_id,
                getattr(body, "action", "-"),
                return_area_id or "-",
                matter_area or "",
                exc.detail,
            )
            raise


@app.post("/api/scan/terminal", response_model=ScanPreviewOut)
def scan_terminal(body: ScanTerminalRequestIn, request: Request, x_terminal_key: Optional[str] = Header(default=None, alias="X-Terminal-Key")):
    with SessionLocal() as db:
        client_ip = request.client.host if request.client else None
        logger.info("Scan terminal preview terminal_code=%s mode=%s client_ip=%s", body.terminal_code, body.mode, client_ip or "-")
        try:
            return ScanPreviewOut(**terminal_preview_scan(
                db,
                body.terminal_code,
                body.qr_value,
                body.mode,
                x_terminal_key,
                client_ip,
                dispatch_func=_dispatch_movement_order,
                rollback_func=_rollback_unsuccessful_order_dispatch,
                audit_rack_func=_audit_rack_reservation_change,
            ))
        except ScanPreviewError as exc:
            raise HTTPException(status_code=400, detail=exc.message)


@app.get("/api/scan/events", response_model=List[ScanEventOut])
def list_scan_events(
    scanner_code: Optional[str] = None,
    qr_value: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    movement_order_id: Optional[int] = None,
):
    with SessionLocal() as db:
        stmt = select(ScanEvent)
        if scanner_code:
            stmt = stmt.where(ScanEvent.scanner_code == scanner_code.strip())
        if qr_value:
            stmt = stmt.where(ScanEvent.qr_value == qr_value.strip())
        if status:
            stmt = stmt.where(ScanEvent.status == status.strip())
        parsed_from = _parse_scan_event_date(date_from)
        parsed_to = _parse_scan_event_date(date_to)
        if parsed_from:
            stmt = stmt.where(ScanEvent.created_at >= parsed_from)
        if parsed_to:
            stmt = stmt.where(ScanEvent.created_at <= parsed_to)
        if movement_order_id:
            stmt = stmt.where(ScanEvent.movement_order_id == movement_order_id)
        rows = db.execute(stmt.order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc()).limit(200)).scalars().all()
        return [_scan_event_out(db, row) for row in rows]


def _admin_soft_delete_actor(token: Optional[str]) -> str:
    return f"admin:{(token or '')[-6:]}" if token else "admin"


def _qr_admin_dependencies(db, entity_type: str, entity_id: int) -> dict:
    if entity_type == "scanner_station":
        active_terminals = db.execute(select(ScanTerminal).where(ScanTerminal.scanner_station_id == entity_id, ScanTerminal.is_active == 1).order_by(ScanTerminal.terminal_code.asc())).scalars().all()
        active_transitions = db.execute(select(QrTransitionRule).where(QrTransitionRule.scanner_station_id == entity_id, QrTransitionRule.is_active == 1).order_by(QrTransitionRule.id.asc())).scalars().all()
        return {
            "active_terminals": [{"id": row.id, "code": row.terminal_code} for row in active_terminals],
            "active_transitions": [{"id": row.id, "name": row.name} for row in active_transitions],
            "scan_events": int(db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.scanner_station_id == entity_id)).scalar_one() or 0),
        }
    if entity_type == "qr_action_rule":
        active_transitions = db.execute(select(QrTransitionRule).where(QrTransitionRule.qr_action_rule_id == entity_id, QrTransitionRule.is_active == 1).order_by(QrTransitionRule.id.asc())).scalars().all()
        event_count = int(db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.qr_action_rule_id == entity_id)).scalar_one() or 0)
        order_count = int(db.execute(select(func.count(func.distinct(ScanEvent.movement_order_id))).where(ScanEvent.qr_action_rule_id == entity_id, ScanEvent.movement_order_id.is_not(None))).scalar_one() or 0)
        return {
            "active_transitions": [{"id": row.id, "name": row.name} for row in active_transitions],
            "scan_events": event_count,
            "movement_orders": order_count,
        }
    if entity_type == "scan_terminal":
        return {"scan_events": int(db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.terminal_id == entity_id)).scalar_one() or 0)}
    if entity_type == "qr_transition_rule":
        return {"transition_logs": int(db.execute(select(func.count()).select_from(QrTransitionLog).where(QrTransitionLog.transition_rule_id == entity_id)).scalar_one() or 0)}
    raise ValueError(f"Tipo de dependencia no soportado: {entity_type}")


def _audit_qr_admin_state(db, *, event: str, entity_type: str, entity_id: int, label: str, actor: str, dependencies: dict, at: datetime) -> None:
    payload = {"event": event, "entity_type": entity_type, "id": entity_id, "label": label, "actor": actor, "dependencies": dependencies, "timestamp": at.isoformat()}
    _append_debug_console_event(db, direction="received", module="qr_admin", endpoint=event, payload=payload, message=f"{event} id={entity_id} label={label}", created_at=at, auto_commit=False)
    logger.info("%s entity_type=%s id=%s label=%s actor=%s dependencies=%s timestamp=%s", event, entity_type, entity_id, label, actor, json.dumps(dependencies, ensure_ascii=False), at.isoformat())


def _require_existing_refs(db, row, refs: list[tuple[str, object, str]]) -> None:
    missing = []
    for attr, model, label in refs:
        value = getattr(row, attr, None)
        if value is not None and db.execute(select(model.id).where(model.id == value)).scalar_one_or_none() is None:
            missing.append(f"{label} {value}")
    if missing:
        raise HTTPException(status_code=409, detail=f"No se puede restaurar porque faltan dependencias: {', '.join(missing)}.")


@app.get("/api/admin/scanner-stations", response_model=List[ScannerStationOut])
def admin_list_scanner_stations(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(ScannerStation)
        if not include_inactive:
            stmt = stmt.where(ScannerStation.is_active == 1)
        rows = db.execute(stmt.order_by(ScannerStation.scanner_code.asc())).scalars().all()
        return _scanner_stations_out(db, rows)


@app.post("/api/admin/scanner-stations", response_model=ScannerStationOut)
def admin_create_scanner_station(body: ScannerStationIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        scanner_code = body.scanner_code.strip()
        if db.execute(select(ScannerStation).where(ScannerStation.scanner_code == scanner_code)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Ya existe un scanner con ese codigo")
        station_type, default_action = _validate_scanner_station_body(db, body)
        step3_source_mode = _normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode)
        step4_source_mode = _normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode)
        step5_source_mode = _normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode)
        route_mode = _normalize_route_mode(body.route_mode)
        now = datetime.utcnow()
        row = ScannerStation(
            scanner_code=scanner_code,
            name=body.name.strip(),
            description=(body.description or "").strip() or None,
            station_type=station_type,
            default_action=default_action,
            source_area_id=body.source_area_id,
            destination_area_id=body.destination_area_id,
            source_cell_id=body.source_cell_id,
            destination_cell_id=body.destination_cell_id,
            route_mode=route_mode,
            fifo_material_policy="specific_material" if route_mode == "fifo_chain" else ((body.fifo_material_policy or "specific_material").strip() or "specific_material"),
            fifo_chain_total_steps=_normalize_fifo_chain_total_steps(body.fifo_chain_total_steps),
            fifo_chain_step1_source_mode=_normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode),
            fifo_chain_step1_material_group_id=body.fifo_chain_step1_material_group_id,
            fifo_chain_step2_source_mode=_normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode),
            fifo_chain_step2_material_group_id=body.fifo_chain_step2_material_group_id,
            fifo_chain_step3_source_mode=step3_source_mode,
            fifo_chain_step3_material_group_id=body.fifo_chain_step3_material_group_id,
            fifo_chain_step3_source_area_id=None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_area_id,
            fifo_chain_step3_source_cell_id=None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_cell_id,
            fifo_chain_step3_destination_area_id=body.fifo_chain_step3_destination_area_id,
            fifo_chain_step3_destination_cell_id=body.fifo_chain_step3_destination_cell_id,
            fifo_chain_step4_source_mode=step4_source_mode,
            fifo_chain_step5_source_mode=step5_source_mode,
            fifo_chain_step4_material_group_id=body.fifo_chain_step4_material_group_id,
            fifo_chain_step5_material_group_id=body.fifo_chain_step5_material_group_id,
            fifo_chain_step4_source_area_id=None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_area_id,
            fifo_chain_step5_source_area_id=None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_area_id,
            fifo_chain_step4_source_cell_id=None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_cell_id,
            fifo_chain_step5_source_cell_id=None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_cell_id,
            fifo_chain_step4_destination_area_id=body.fifo_chain_step4_destination_area_id,
            fifo_chain_step5_destination_area_id=body.fifo_chain_step5_destination_area_id,
            fifo_chain_step4_destination_cell_id=body.fifo_chain_step4_destination_cell_id,
            fifo_chain_step5_destination_cell_id=body.fifo_chain_step5_destination_cell_id,
            second_source_area_id=body.second_source_area_id,
            second_destination_area_id=body.second_destination_area_id,
            second_source_cell_id=body.second_source_cell_id,
            second_destination_cell_id=body.second_destination_cell_id,
            storage_area_id=body.storage_area_id,
            empty_rack_area_id=body.empty_rack_area_id,
            cancel_return_area_id=body.cancel_return_area_id,
            cancel_return_mode=_normalize_cancel_return_mode(body.cancel_return_mode),
            default_material_group_id=None if route_mode == "fifo_chain" else body.default_material_group_id,
            agv_code=(body.agv_code or "").strip() or None,
            task_typ=(body.task_typ or "").strip() or None,
            priority=int(body.priority or 0),
            require_preview=int(body.require_preview or 0),
            allow_execute=int(body.allow_execute or 0),
            allow_same_cell_move=bool(int(body.allow_same_cell_move or 0)),
            is_active=int(body.is_active),
            created_at=now,
            updated_at=now,
        )
        _apply_fifo_chain_area_modes(row, body)
        _apply_fifo_chain_task_types(row, body)
        _clear_fifo_chain_step5_if_unused(row)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scanner station created scanner_id=%s scanner_code=%s", row.id, row.scanner_code)
        return _scanner_station_out(db, row)


@app.put("/api/admin/scanner-stations/{scanner_station_id}", response_model=ScannerStationOut)
def admin_update_scanner_station(scanner_station_id: int, body: ScannerStationIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScannerStation).where(ScannerStation.id == scanner_station_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Scanner no encontrado")
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="No se puede editar un Scanner eliminado. Restáuralo primero.")
        scanner_code = body.scanner_code.strip()
        dup = db.execute(select(ScannerStation).where(ScannerStation.scanner_code == scanner_code, ScannerStation.id != scanner_station_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe un scanner con ese codigo")
        station_type, default_action = _validate_scanner_station_body(db, body)
        step3_source_mode = _normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode)
        step4_source_mode = _normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode)
        step5_source_mode = _normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode)
        route_mode = _normalize_route_mode(body.route_mode)
        row.scanner_code = scanner_code
        row.name = body.name.strip()
        row.description = (body.description or "").strip() or None
        row.station_type = station_type
        row.default_action = default_action
        row.source_area_id = body.source_area_id
        row.destination_area_id = body.destination_area_id
        row.source_cell_id = body.source_cell_id
        row.destination_cell_id = body.destination_cell_id
        row.route_mode = route_mode
        row.fifo_material_policy = "specific_material" if route_mode == "fifo_chain" else ((body.fifo_material_policy or "specific_material").strip() or "specific_material")
        row.fifo_chain_total_steps = _normalize_fifo_chain_total_steps(body.fifo_chain_total_steps)
        row.fifo_chain_step1_source_mode = _normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode)
        row.fifo_chain_step1_material_group_id = body.fifo_chain_step1_material_group_id
        row.fifo_chain_step2_source_mode = _normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode)
        row.fifo_chain_step2_material_group_id = body.fifo_chain_step2_material_group_id
        row.fifo_chain_step3_source_mode = step3_source_mode
        row.fifo_chain_step3_material_group_id = body.fifo_chain_step3_material_group_id
        row.fifo_chain_step3_source_area_id = None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_area_id
        row.fifo_chain_step3_source_cell_id = None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_cell_id
        row.fifo_chain_step3_destination_area_id = body.fifo_chain_step3_destination_area_id
        row.fifo_chain_step3_destination_cell_id = body.fifo_chain_step3_destination_cell_id
        row.fifo_chain_step4_source_mode = step4_source_mode
        row.fifo_chain_step5_source_mode = step5_source_mode
        row.fifo_chain_step4_material_group_id = body.fifo_chain_step4_material_group_id
        row.fifo_chain_step5_material_group_id = body.fifo_chain_step5_material_group_id
        row.fifo_chain_step4_source_area_id = None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_area_id
        row.fifo_chain_step5_source_area_id = None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_area_id
        row.fifo_chain_step4_source_cell_id = None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_cell_id
        row.fifo_chain_step5_source_cell_id = None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_cell_id
        row.fifo_chain_step4_destination_area_id = body.fifo_chain_step4_destination_area_id
        row.fifo_chain_step5_destination_area_id = body.fifo_chain_step5_destination_area_id
        row.fifo_chain_step4_destination_cell_id = body.fifo_chain_step4_destination_cell_id
        row.fifo_chain_step5_destination_cell_id = body.fifo_chain_step5_destination_cell_id
        row.second_source_area_id = body.second_source_area_id
        row.second_destination_area_id = body.second_destination_area_id
        row.second_source_cell_id = body.second_source_cell_id
        row.second_destination_cell_id = body.second_destination_cell_id
        row.storage_area_id = body.storage_area_id
        row.empty_rack_area_id = body.empty_rack_area_id
        row.cancel_return_area_id = body.cancel_return_area_id
        row.cancel_return_mode = _normalize_cancel_return_mode(body.cancel_return_mode)
        row.default_material_group_id = None if route_mode == "fifo_chain" else body.default_material_group_id
        row.agv_code = (body.agv_code or "").strip() or None
        row.task_typ = (body.task_typ or "").strip() or None
        row.priority = int(body.priority or 0)
        row.require_preview = int(body.require_preview or 0)
        row.allow_execute = int(body.allow_execute or 0)
        row.allow_same_cell_move = bool(int(body.allow_same_cell_move or 0))
        row.is_active = int(body.is_active or 0)
        row.updated_at = datetime.utcnow()
        _apply_fifo_chain_area_modes(row, body)
        _apply_fifo_chain_task_types(row, body)
        _clear_fifo_chain_step5_if_unused(row)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scanner station updated scanner_id=%s scanner_code=%s", row.id, row.scanner_code)
        return _scanner_station_out(db, row)


@app.delete("/api/admin/scanner-stations/{scanner_station_id}", response_model=ScannerStationOut)
def admin_delete_scanner_station(scanner_station_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScannerStation).where(ScannerStation.id == scanner_station_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Scanner no encontrado")
        dependencies = _qr_admin_dependencies(db, "scanner_station", row.id)
        terminals = dependencies["active_terminals"]
        transitions = dependencies["active_transitions"]
        if terminals:
            labels = ", ".join(item["code"] for item in terminals)
            raise HTTPException(status_code=409, detail=f"No se puede eliminar el Scanner porque tiene {len(terminals)} Terminales activas: {labels}.")
        if transitions:
            labels = ", ".join(item["name"] for item in transitions)
            raise HTTPException(status_code=409, detail=f"No se puede eliminar el Scanner porque tiene {len(transitions)} Transiciones activas: {labels}.")
        if int(row.is_active or 0) == 0:
            return _scanner_station_out(db, row)
        now = datetime.utcnow()
        row.is_active = 0
        row.updated_at = now
        _audit_qr_admin_state(db, event="SCANNER_STATION_DEACTIVATED", entity_type="scanner_station", entity_id=row.id, label=row.scanner_code, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scanner station deactivated scanner_id=%s scanner_code=%s", row.id, row.scanner_code)
        return _scanner_station_out(db, row)


@app.get("/api/admin/scanner-stations/{scanner_station_id}/dependencies")
def admin_scanner_station_dependencies(scanner_station_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScannerStation).where(ScannerStation.id == scanner_station_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Scanner no encontrado")
        return _qr_admin_dependencies(db, "scanner_station", row.id)


@app.post("/api/admin/scanner-stations/{scanner_station_id}/restore", response_model=ScannerStationOut)
def admin_restore_scanner_station(scanner_station_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScannerStation).where(ScannerStation.id == scanner_station_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Scanner no encontrado")
        if int(row.is_active or 0) == 1:
            return _scanner_station_out(db, row)
        if not (row.scanner_code or "").strip() or not (row.name or "").strip():
            raise HTTPException(status_code=409, detail="No se puede restaurar el Scanner porque faltan scanner_code o name.")
        now = datetime.utcnow()
        dependencies = _qr_admin_dependencies(db, "scanner_station", row.id)
        row.is_active = 1
        row.updated_at = now
        _audit_qr_admin_state(db, event="SCANNER_STATION_RESTORED", entity_type="scanner_station", entity_id=row.id, label=row.scanner_code, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row); db.commit(); db.refresh(row)
        return _scanner_station_out(db, row)


@app.get("/api/admin/qr-action-rules", response_model=List[QrActionRuleOut])
def admin_list_qr_action_rules(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(QrActionRule)
        if not include_inactive:
            stmt = stmt.where(QrActionRule.is_active == 1)
        rows = db.execute(stmt.order_by(QrActionRule.qr_value.asc(), QrActionRule.id.asc())).scalars().all()
        return [_qr_action_rule_out(db, row) for row in rows]


@app.get("/api/admin/qr-action-rules/{qr_action_rule_id}/qr-image")
def admin_qr_action_rule_image(
    qr_action_rule_id: int,
    size: int = 240,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrActionRule).where(QrActionRule.id == qr_action_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no encontrada")
        buffer = _generate_qr_png_buffer(row.qr_value, size)
        return StreamingResponse(
            buffer,
            media_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0"},
        )


@app.post("/api/admin/qr-action-rules", response_model=QrActionRuleOut)
def admin_create_qr_action_rule(body: QrActionRuleIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        qr_value = body.qr_value.strip()
        qr_type, match_type, action_type = _validate_qr_action_rule_body(db, body)
        _validate_qr_rule_unique(db, body)
        step3_source_mode = _normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode)
        step4_source_mode = _normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode)
        step5_source_mode = _normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode)
        route_mode = _normalize_route_mode(body.route_mode)
        now = datetime.utcnow()
        row = QrActionRule(
            qr_value=qr_value,
            qr_alias=(body.qr_alias or "").strip() or None,
            description=(body.description or "").strip() or None,
            qr_type=qr_type,
            match_type=match_type,
            action_type=action_type,
            material_group_id=None if route_mode == "fifo_chain" else body.material_group_id,
            rack_id=None if route_mode == "fifo_chain" else body.rack_id,
            source_area_id=body.source_area_id,
            destination_area_id=body.destination_area_id,
            source_cell_id=body.source_cell_id,
            destination_cell_id=body.destination_cell_id,
            route_mode=route_mode,
            fifo_material_policy="specific_material" if route_mode == "fifo_chain" else ((body.fifo_material_policy or "specific_material").strip() or "specific_material"),
            fifo_chain_total_steps=_normalize_fifo_chain_total_steps(body.fifo_chain_total_steps),
            fifo_chain_step1_source_mode=_normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode),
            fifo_chain_step1_material_group_id=body.fifo_chain_step1_material_group_id,
            fifo_chain_step2_source_mode=_normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode),
            fifo_chain_step2_material_group_id=body.fifo_chain_step2_material_group_id,
            fifo_chain_step3_source_mode=step3_source_mode,
            fifo_chain_step3_material_group_id=body.fifo_chain_step3_material_group_id,
            fifo_chain_step3_source_area_id=None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_area_id,
            fifo_chain_step3_source_cell_id=None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_cell_id,
            fifo_chain_step3_destination_area_id=body.fifo_chain_step3_destination_area_id,
            fifo_chain_step3_destination_cell_id=body.fifo_chain_step3_destination_cell_id,
            fifo_chain_step4_source_mode=step4_source_mode,
            fifo_chain_step5_source_mode=step5_source_mode,
            fifo_chain_step4_material_group_id=body.fifo_chain_step4_material_group_id,
            fifo_chain_step5_material_group_id=body.fifo_chain_step5_material_group_id,
            fifo_chain_step4_source_area_id=None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_area_id,
            fifo_chain_step5_source_area_id=None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_area_id,
            fifo_chain_step4_source_cell_id=None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_cell_id,
            fifo_chain_step5_source_cell_id=None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_cell_id,
            fifo_chain_step4_destination_area_id=body.fifo_chain_step4_destination_area_id,
            fifo_chain_step5_destination_area_id=body.fifo_chain_step5_destination_area_id,
            fifo_chain_step4_destination_cell_id=body.fifo_chain_step4_destination_cell_id,
            fifo_chain_step5_destination_cell_id=body.fifo_chain_step5_destination_cell_id,
            second_source_area_id=body.second_source_area_id,
            second_destination_area_id=body.second_destination_area_id,
            second_source_cell_id=body.second_source_cell_id,
            second_destination_cell_id=body.second_destination_cell_id,
            priority=body.priority,
            task_typ=(body.task_typ or "").strip() or None,
            automatic_priority=body.automatic_priority,
            agv_code=(body.agv_code or "").strip() or None,
            requires_scanner_station=int(body.requires_scanner_station or 0),
            allow_same_cell_move=bool(int(body.allow_same_cell_move or 0)),
            is_active=int(body.is_active),
            created_at=now,
            updated_at=now,
        )
        _apply_fifo_chain_area_modes(row, body)
        _apply_fifo_chain_task_types(row, body)
        _clear_fifo_chain_step5_if_unused(row)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR action rule created qr_rule_id=%s qr_value=%s", row.id, row.qr_value)
        return _qr_action_rule_out(db, row)


@app.put("/api/admin/qr-action-rules/{qr_action_rule_id}", response_model=QrActionRuleOut)
def admin_update_qr_action_rule(qr_action_rule_id: int, body: QrActionRuleIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrActionRule).where(QrActionRule.id == qr_action_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no encontrada")
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="No se puede editar una Regla QR eliminada. Restáurala primero.")
        audit_fields = [name for name in QrActionRuleIn.model_fields.keys() if hasattr(row, name)]
        old_values = _snapshot_fields(row, audit_fields)
        qr_type, match_type, action_type = _validate_qr_action_rule_body(db, body)
        _validate_qr_rule_unique(db, body, existing=row)
        step3_source_mode = _normalize_fifo_chain_step3_source_mode(body.fifo_chain_step3_source_mode)
        step4_source_mode = _normalize_fifo_chain_step4_source_mode(body.fifo_chain_step4_source_mode)
        step5_source_mode = _normalize_fifo_chain_step5_source_mode(body.fifo_chain_step5_source_mode)
        route_mode = _normalize_route_mode(body.route_mode)
        row.qr_value = body.qr_value.strip()
        row.automatic_priority = body.automatic_priority
        row.qr_alias = (body.qr_alias or "").strip() or None
        row.description = (body.description or "").strip() or None
        row.qr_type = qr_type
        row.match_type = match_type
        row.action_type = action_type
        row.material_group_id = None if route_mode == "fifo_chain" else body.material_group_id
        row.rack_id = None if route_mode == "fifo_chain" else body.rack_id
        row.source_area_id = body.source_area_id
        row.destination_area_id = body.destination_area_id
        row.source_cell_id = body.source_cell_id
        row.destination_cell_id = body.destination_cell_id
        row.route_mode = route_mode
        row.fifo_material_policy = "specific_material" if route_mode == "fifo_chain" else ((body.fifo_material_policy or "specific_material").strip() or "specific_material")
        row.fifo_chain_total_steps = _normalize_fifo_chain_total_steps(body.fifo_chain_total_steps)
        row.fifo_chain_step1_source_mode = _normalize_fifo_chain_step1_source_mode(body.fifo_chain_step1_source_mode)
        row.fifo_chain_step1_material_group_id = body.fifo_chain_step1_material_group_id
        row.fifo_chain_step2_source_mode = _normalize_fifo_chain_step2_source_mode(body.fifo_chain_step2_source_mode)
        row.fifo_chain_step2_material_group_id = body.fifo_chain_step2_material_group_id
        row.fifo_chain_step3_source_mode = step3_source_mode
        row.fifo_chain_step3_material_group_id = body.fifo_chain_step3_material_group_id
        row.fifo_chain_step3_source_area_id = None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_area_id
        row.fifo_chain_step3_source_cell_id = None if step3_source_mode == "any_area_by_material" else body.fifo_chain_step3_source_cell_id
        row.fifo_chain_step3_destination_area_id = body.fifo_chain_step3_destination_area_id
        row.fifo_chain_step3_destination_cell_id = body.fifo_chain_step3_destination_cell_id
        row.fifo_chain_step4_source_mode = step4_source_mode
        row.fifo_chain_step5_source_mode = step5_source_mode
        row.fifo_chain_step4_material_group_id = body.fifo_chain_step4_material_group_id
        row.fifo_chain_step5_material_group_id = body.fifo_chain_step5_material_group_id
        row.fifo_chain_step4_source_area_id = None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_area_id
        row.fifo_chain_step5_source_area_id = None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_area_id
        row.fifo_chain_step4_source_cell_id = None if step4_source_mode == "any_area_by_material" else body.fifo_chain_step4_source_cell_id
        row.fifo_chain_step5_source_cell_id = None if step5_source_mode == "any_area_by_material" else body.fifo_chain_step5_source_cell_id
        row.fifo_chain_step4_destination_area_id = body.fifo_chain_step4_destination_area_id
        row.fifo_chain_step5_destination_area_id = body.fifo_chain_step5_destination_area_id
        row.fifo_chain_step4_destination_cell_id = body.fifo_chain_step4_destination_cell_id
        row.fifo_chain_step5_destination_cell_id = body.fifo_chain_step5_destination_cell_id
        row.second_source_area_id = body.second_source_area_id
        row.second_destination_area_id = body.second_destination_area_id
        row.second_source_cell_id = body.second_source_cell_id
        row.second_destination_cell_id = body.second_destination_cell_id
        row.priority = body.priority
        row.task_typ = (body.task_typ or "").strip() or None
        row.agv_code = (body.agv_code or "").strip() or None
        row.requires_scanner_station = int(body.requires_scanner_station or 0)
        row.allow_same_cell_move = bool(int(body.allow_same_cell_move or 0))
        row.is_active = int(body.is_active or 0)
        row.updated_at = datetime.utcnow()
        _apply_fifo_chain_area_modes(row, body)
        _apply_fifo_chain_task_types(row, body)
        _clear_fifo_chain_step5_if_unused(row)
        _audit_model_field_changes(
            db,
            table_name="qr_action_rules",
            record_key=str(row.id),
            old_values=old_values,
            row=row,
            field_names=audit_fields,
            source="admin_qr_action_rule_update",
            related_qr_rule_id=row.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR action rule updated qr_rule_id=%s qr_value=%s", row.id, row.qr_value)
        return _qr_action_rule_out(db, row)


@app.delete("/api/admin/qr-action-rules/{qr_action_rule_id}", response_model=QrActionRuleOut)
def admin_delete_qr_action_rule(qr_action_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrActionRule).where(QrActionRule.id == qr_action_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no encontrada")
        dependencies = _qr_admin_dependencies(db, "qr_action_rule", row.id)
        transitions = dependencies["active_transitions"]
        if transitions:
            labels = ", ".join(item["name"] for item in transitions)
            raise HTTPException(status_code=409, detail=f"No se puede eliminar la Regla QR porque tiene {len(transitions)} Transiciones activas: {labels}.")
        if int(row.is_active or 0) == 0:
            return _qr_action_rule_out(db, row)
        now = datetime.utcnow()
        row.is_active = 0
        row.updated_at = now
        _audit_qr_admin_state(db, event="QR_ACTION_RULE_DEACTIVATED", entity_type="qr_action_rule", entity_id=row.id, label=row.qr_value, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR action rule deactivated qr_rule_id=%s qr_value=%s", row.id, row.qr_value)
        return _qr_action_rule_out(db, row)


@app.get("/api/admin/qr-action-rules/{qr_action_rule_id}/dependencies")
def admin_qr_action_rule_dependencies(qr_action_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrActionRule).where(QrActionRule.id == qr_action_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no encontrada")
        return _qr_admin_dependencies(db, "qr_action_rule", row.id)


@app.post("/api/admin/qr-action-rules/{qr_action_rule_id}/restore", response_model=QrActionRuleOut)
def admin_restore_qr_action_rule(qr_action_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrActionRule).where(QrActionRule.id == qr_action_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla QR no encontrada")
        if int(row.is_active or 0) == 1:
            return _qr_action_rule_out(db, row)
        if not (row.qr_value or "").strip():
            raise HTTPException(status_code=409, detail="No se puede restaurar la Regla QR porque qr_value está vacío.")
        _validate_qr_rule_unique(db, QrActionRuleIn(qr_value=row.qr_value, match_type=row.match_type or "exact", is_active=1), existing=row)
        _require_existing_refs(db, row, [
            ("material_group_id", MaterialGroup, "material"), ("rack_id", Rack, "rack"),
            ("source_area_id", Area, "área origen"), ("destination_area_id", Area, "área destino"),
            ("source_cell_id", Location, "celda origen"), ("destination_cell_id", Location, "celda destino"),
        ])
        now = datetime.utcnow(); dependencies = _qr_admin_dependencies(db, "qr_action_rule", row.id)
        row.is_active = 1; row.updated_at = now
        _audit_qr_admin_state(db, event="QR_ACTION_RULE_RESTORED", entity_type="qr_action_rule", entity_id=row.id, label=row.qr_value, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row); db.commit(); db.refresh(row)
        return _qr_action_rule_out(db, row)


@app.get("/api/admin/qr-transition-rules", response_model=List[QrTransitionRuleOut])
def admin_list_qr_transition_rules(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(QrTransitionRule)
        if not include_inactive:
            stmt = stmt.where(QrTransitionRule.is_active == 1)
        rows = db.execute(stmt.order_by(QrTransitionRule.priority.desc(), QrTransitionRule.id.asc())).scalars().all()
        return [_qr_transition_rule_out(db, row) for row in rows]


@app.post("/api/admin/qr-transition-rules", response_model=QrTransitionRuleOut)
def admin_create_qr_transition_rule(body: QrTransitionRuleIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        apply_on = validate_transition_rule_payload(db, body)
        enforce_transition_rule_diagnostics(db, body)
        source_match_mode = (body.source_match_mode or "configured_source").strip() or "configured_source"
        source_area_id = None if source_match_mode == "any_source" else body.source_area_id
        source_cell_id = None if source_match_mode == "any_source" else body.source_cell_id
        now = datetime.utcnow()
        row = QrTransitionRule(
            name=body.name.strip(),
            description=(body.description or "").strip() or None,
            qr_action_rule_id=body.qr_action_rule_id,
            scanner_station_id=body.scanner_station_id,
            source_area_id=source_area_id,
            destination_area_id=body.destination_area_id,
            source_cell_id=source_cell_id,
            destination_cell_id=body.destination_cell_id,
            current_material_group_id=body.current_material_group_id,
            current_rack_status=(body.current_rack_status or "").strip() or None,
            next_material_group_id=body.next_material_group_id,
            next_rack_status=(body.next_rack_status or "").strip() or None,
            next_quantity=body.next_quantity,
            clear_quantity=int(body.clear_quantity or 0),
            next_comment=(body.next_comment or "").strip() or None,
            append_comment=int(body.append_comment or 0),
            apply_on=apply_on,
            scope=(body.scope or "qr_pda").strip() or "qr_pda",
            match_mode=(body.match_mode or "advanced").strip() or "advanced",
            source_match_mode=source_match_mode,
            ignore_current_material=int(body.ignore_current_material or 0),
            priority=int(body.priority or 0),
            is_active=int(body.is_active or 0),
            applied_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR transition rule created transition_rule_id=%s name=%s", row.id, row.name)
        return _qr_transition_rule_out(db, row)


@app.post("/api/admin/qr-transition-rules/validate")
def admin_validate_qr_transition_rule(body: QrTransitionRuleValidateIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        validate_transition_rule_payload(db, body)
        return diagnose_transition_rule_payload(db, body, body.rule_id)


@app.get("/api/admin/qr-transition-rules/preview/{movement_order_id}")
def admin_preview_qr_transition_rule(movement_order_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return preview_transition_for_order(db, movement_order_id)


@app.post("/api/admin/qr-transition-rules/apply/{movement_order_id}")
def admin_apply_qr_transition_rule(movement_order_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        result = apply_transition_for_completed_order(db, movement_order_id, reason="admin_manual_apply")
        db.commit()
        return result


@app.get("/api/admin/qr-transition-logs", response_model=List[QrTransitionLogOut])
def admin_list_qr_transition_logs(
    movement_order_id: Optional[int] = None,
    rack_id: Optional[int] = None,
    transition_rule_id: Optional[int] = None,
    status: Optional[str] = None,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(QrTransitionLog)
        if movement_order_id is not None:
            stmt = stmt.where(QrTransitionLog.movement_order_id == movement_order_id)
        if rack_id is not None:
            stmt = stmt.where(QrTransitionLog.rack_id == rack_id)
        if transition_rule_id is not None:
            stmt = stmt.where(QrTransitionLog.transition_rule_id == transition_rule_id)
        clean_status = (status or "").strip()
        if clean_status:
            stmt = stmt.where(QrTransitionLog.status == clean_status)
        rows = db.execute(stmt.order_by(QrTransitionLog.created_at.desc(), QrTransitionLog.id.desc()).limit(200)).scalars().all()
        return _qr_transition_logs_out(db, rows)


@app.put("/api/admin/qr-transition-rules/{transition_rule_id}", response_model=QrTransitionRuleOut)
def admin_update_qr_transition_rule(transition_rule_id: int, body: QrTransitionRuleIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrTransitionRule).where(QrTransitionRule.id == transition_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla de transici?n no encontrada")
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="No se puede editar una Transición eliminada. Restáurala primero.")
        apply_on = validate_transition_rule_payload(db, body)
        enforce_transition_rule_diagnostics(db, body, transition_rule_id)
        source_match_mode = (body.source_match_mode or "configured_source").strip() or "configured_source"
        row.name = body.name.strip()
        row.description = (body.description or "").strip() or None
        row.qr_action_rule_id = body.qr_action_rule_id
        row.scanner_station_id = body.scanner_station_id
        row.source_area_id = None if source_match_mode == "any_source" else body.source_area_id
        row.destination_area_id = body.destination_area_id
        row.source_cell_id = None if source_match_mode == "any_source" else body.source_cell_id
        row.destination_cell_id = body.destination_cell_id
        row.current_material_group_id = body.current_material_group_id
        row.current_rack_status = (body.current_rack_status or "").strip() or None
        row.next_material_group_id = body.next_material_group_id
        row.next_rack_status = (body.next_rack_status or "").strip() or None
        row.next_quantity = body.next_quantity
        row.clear_quantity = int(body.clear_quantity or 0)
        row.next_comment = (body.next_comment or "").strip() or None
        row.append_comment = int(body.append_comment or 0)
        row.apply_on = apply_on
        row.scope = (body.scope or "qr_pda").strip() or "qr_pda"
        row.match_mode = (body.match_mode or "advanced").strip() or "advanced"
        row.source_match_mode = source_match_mode
        row.ignore_current_material = int(body.ignore_current_material or 0)
        row.priority = int(body.priority or 0)
        row.is_active = int(body.is_active or 0)
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR transition rule updated transition_rule_id=%s name=%s", row.id, row.name)
        return _qr_transition_rule_out(db, row)


@app.delete("/api/admin/qr-transition-rules/{transition_rule_id}", response_model=QrTransitionRuleOut)
def admin_delete_qr_transition_rule(transition_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrTransitionRule).where(QrTransitionRule.id == transition_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla de transici?n no encontrada")
        if int(row.is_active or 0) == 0:
            return _qr_transition_rule_out(db, row)
        dependencies = _qr_admin_dependencies(db, "qr_transition_rule", row.id)
        now = datetime.utcnow()
        row.is_active = 0
        row.updated_at = now
        _audit_qr_admin_state(db, event="QR_TRANSITION_RULE_DEACTIVATED", entity_type="qr_transition_rule", entity_id=row.id, label=row.name, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("QR transition rule deactivated transition_rule_id=%s name=%s", row.id, row.name)
        return _qr_transition_rule_out(db, row)


@app.get("/api/admin/qr-transition-rules/{transition_rule_id}/dependencies")
def admin_qr_transition_rule_dependencies(transition_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrTransitionRule).where(QrTransitionRule.id == transition_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla de transición no encontrada")
        return _qr_admin_dependencies(db, "qr_transition_rule", row.id)


@app.post("/api/admin/qr-transition-rules/{transition_rule_id}/restore", response_model=QrTransitionRuleOut)
def admin_restore_qr_transition_rule(transition_rule_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(QrTransitionRule).where(QrTransitionRule.id == transition_rule_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Regla de transición no encontrada")
        if int(row.is_active or 0) == 1:
            return _qr_transition_rule_out(db, row)
        inactive_dependencies = []
        if row.qr_action_rule_id:
            qr = db.execute(select(QrActionRule).where(QrActionRule.id == row.qr_action_rule_id)).scalar_one_or_none()
            if not qr or int(qr.is_active or 0) != 1:
                inactive_dependencies.append(f"QR {qr.qr_value if qr else row.qr_action_rule_id}")
        if row.scanner_station_id:
            scanner = db.execute(select(ScannerStation).where(ScannerStation.id == row.scanner_station_id)).scalar_one_or_none()
            if not scanner or int(scanner.is_active or 0) != 1:
                inactive_dependencies.append(f"Scanner {scanner.scanner_code if scanner else row.scanner_station_id}")
        if inactive_dependencies:
            raise HTTPException(status_code=409, detail=f"No se puede restaurar la Transición porque depende de configuraciones eliminadas: {', '.join(inactive_dependencies)}.")
        _require_existing_refs(db, row, [
            ("source_area_id", Area, "área origen"), ("destination_area_id", Area, "área destino"),
            ("source_cell_id", Location, "celda origen"), ("destination_cell_id", Location, "celda destino"),
            ("current_material_group_id", MaterialGroup, "material actual"), ("next_material_group_id", MaterialGroup, "material siguiente"),
        ])
        now = datetime.utcnow(); dependencies = _qr_admin_dependencies(db, "qr_transition_rule", row.id)
        row.is_active = 1; row.updated_at = now
        _audit_qr_admin_state(db, event="QR_TRANSITION_RULE_RESTORED", entity_type="qr_transition_rule", entity_id=row.id, label=row.name, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row); db.commit(); db.refresh(row)
        return _qr_transition_rule_out(db, row)


@app.get("/api/admin/scan-terminals", response_model=List[ScanTerminalOut])
def admin_list_scan_terminals(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(ScanTerminal)
        if not include_inactive:
            stmt = stmt.where(ScanTerminal.is_active == 1)
        rows = db.execute(stmt.order_by(ScanTerminal.terminal_code.asc(), ScanTerminal.id.asc())).scalars().all()
        return [_scan_terminal_out(db, row) for row in rows]


@app.post("/api/admin/scan-terminals", response_model=ScanTerminalOut)
def admin_create_scan_terminal(body: ScanTerminalIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        terminal_code = body.terminal_code.strip()
        if db.execute(select(ScanTerminal).where(ScanTerminal.terminal_code == terminal_code)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Ya existe un terminal con ese codigo")
        mode = _validate_scan_terminal_body(db, body)
        now = datetime.utcnow()
        row = ScanTerminal(
            terminal_code=terminal_code,
            name=body.name.strip(),
            description=(body.description or "").strip() or None,
            scanner_station_id=body.scanner_station_id,
            api_key=(body.api_key or "").strip() or None,
            mode=mode,
            allow_execute=int(body.allow_execute or 0),
            require_preview=int(body.require_preview or 0),
            is_active=int(body.is_active),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scan terminal created terminal_id=%s terminal_code=%s", row.id, row.terminal_code)
        return _scan_terminal_out(db, row)


@app.put("/api/admin/scan-terminals/{terminal_id}", response_model=ScanTerminalOut)
def admin_update_scan_terminal(terminal_id: int, body: ScanTerminalIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScanTerminal).where(ScanTerminal.id == terminal_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Terminal no encontrado")
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="No se puede editar una Terminal eliminada. Restáurala primero.")
        terminal_code = body.terminal_code.strip()
        dup = db.execute(select(ScanTerminal).where(ScanTerminal.terminal_code == terminal_code, ScanTerminal.id != terminal_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe un terminal con ese codigo")
        mode = _validate_scan_terminal_body(db, body)
        row.terminal_code = terminal_code
        row.name = body.name.strip()
        row.description = (body.description or "").strip() or None
        row.scanner_station_id = body.scanner_station_id
        row.api_key = (body.api_key or "").strip() or None
        row.mode = mode
        row.allow_execute = int(body.allow_execute or 0)
        row.require_preview = int(body.require_preview or 0)
        row.is_active = int(body.is_active or 0)
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scan terminal updated terminal_id=%s terminal_code=%s", row.id, row.terminal_code)
        return _scan_terminal_out(db, row)


@app.delete("/api/admin/scan-terminals/{terminal_id}", response_model=ScanTerminalOut)
def admin_delete_scan_terminal(terminal_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScanTerminal).where(ScanTerminal.id == terminal_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Terminal no encontrado")
        if int(row.is_active or 0) == 0:
            return _scan_terminal_out(db, row)
        dependencies = _qr_admin_dependencies(db, "scan_terminal", row.id)
        now = datetime.utcnow()
        row.is_active = 0
        row.updated_at = now
        _audit_qr_admin_state(db, event="SCAN_TERMINAL_DEACTIVATED", entity_type="scan_terminal", entity_id=row.id, label=row.terminal_code, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Scan terminal deactivated terminal_id=%s terminal_code=%s", row.id, row.terminal_code)
        return _scan_terminal_out(db, row)


@app.get("/api/admin/scan-terminals/{terminal_id}/dependencies")
def admin_scan_terminal_dependencies(terminal_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScanTerminal).where(ScanTerminal.id == terminal_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Terminal no encontrado")
        return _qr_admin_dependencies(db, "scan_terminal", row.id)


@app.post("/api/admin/scan-terminals/{terminal_id}/restore", response_model=ScanTerminalOut)
def admin_restore_scan_terminal(terminal_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(ScanTerminal).where(ScanTerminal.id == terminal_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Terminal no encontrado")
        if int(row.is_active or 0) == 1:
            return _scan_terminal_out(db, row)
        scanner = db.execute(select(ScannerStation).where(ScannerStation.id == row.scanner_station_id)).scalar_one_or_none()
        if not scanner or int(scanner.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="No se puede restaurar la Terminal porque su Scanner no existe o está inactivo.")
        if not (row.terminal_code or "").strip() or not (row.name or "").strip():
            raise HTTPException(status_code=409, detail="No se puede restaurar la Terminal porque faltan terminal_code o name.")
        now = datetime.utcnow(); dependencies = _qr_admin_dependencies(db, "scan_terminal", row.id)
        row.is_active = 1; row.updated_at = now
        _audit_qr_admin_state(db, event="SCAN_TERMINAL_RESTORED", entity_type="scan_terminal", entity_id=row.id, label=row.terminal_code, actor=_admin_soft_delete_actor(x_admin_token), dependencies=dependencies, at=now)
        db.add(row); db.commit(); db.refresh(row)
        return _scan_terminal_out(db, row)


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
        if dispatch_result.dispatch_status == "uncertain":
            raise HTTPException(status_code=409, detail=f"Orden {order.id}: creación incierta; recursos retenidos. Revisar en RCS antes de reenviar.")
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or "El RCS rechazó la creación de la tarea"
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f"El RCS no aceptó la tarea: {message}")
        return _movement_order_out_read_optimized(db, order)


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
        if dispatch_result.dispatch_status == "uncertain":
            raise HTTPException(status_code=409, detail=f"Orden {order.id}: creación incierta; recursos retenidos. Revisar en RCS antes de reenviar.")
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or "El RCS rechazó la creación de la tarea"
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f"El RCS no aceptó la tarea: {message}")
        return _movement_order_out_read_optimized(db, order)


@app.get("/api/movement-orders", response_model=MovementOrderPageOut)
def list_movement_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with SessionLocal() as db:
        return _list_movement_orders_page_out(db, limit=limit, offset=offset)


@app.get("/api/movement-orders/{order_id}", response_model=MovementOrderDetailOut)
def get_movement_order_detail(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        return _movement_order_detail_out(db, row)


@app.get("/api/movement-orders/{order_id}/json", response_model=MovementOrderJsonOut)
def get_movement_order_json(order_id: int):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        generated_payload = _movement_order_json_payload(db, row)
        payload, source = _current_movement_order_payload(row, generated_payload)
        return MovementOrderJsonOut(order_id=row.id, order_code=row.order_code, payload=sanitize(payload), source=source)


@app.post("/api/movement-orders/{order_id}/json", response_model=MovementOrderJsonOut)
def save_movement_order_json(order_id: int, body: MovementOrderJsonIn):
    with SessionLocal() as db:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        row = _save_movement_order_payload_override(db, row, body.payload)
        logger.info("Task JSON updated | task_id=%s | order_code=%s | status=%s | rack_id=%s", row.id, row.order_code, row.status, row.rack_id)
        return MovementOrderJsonOut(order_id=row.id, order_code=row.order_code, payload=sanitize(body.payload), source="edited")


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
        result = _dispatch_movement_order(db, row)
        if result.dispatch_status == "error" and not row.remote_task_code:
            _rollback_unsuccessful_order_dispatch(db, row, result.rcs_message)
        return _movement_order_dispatch_out(row)


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
            response = trace_call("query", [row], client.query_task_status_with_payload, request_payload)
            _append_debug_console_event(db, direction="received", module="status_query", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or f"Respuesta consulta para {row.order_code}")
            now = datetime.utcnow()
            remote_task_code = (row.remote_task_code or "").strip()
            matched_item = next((item for item in (response.task_statuses or []) if remote_task_code and str(item.get("taskCode") or item.get("task_code") or "").strip() == remote_task_code), None)
            request_task_code = str(request_payload.get("taskCode") or "").strip()
            request_task_codes = request_payload.get("taskCodes")
            if not isinstance(request_task_codes, list):
                request_task_codes = []
            request_task_codes = [str(code or "").strip() for code in request_task_codes if str(code or "").strip()]
            if matched_item is None and remote_task_code and len(request_task_codes) == 1 and request_task_codes[0] == remote_task_code:
                matched_item = _single_task_response_item(response, remote_task_code)
            response_task_code = str((matched_item or {}).get("taskCode") or (matched_item or {}).get("task_code") or "").strip()
            failed_task_code = "" if response.ok else _task_code_from_error_message(response.message, request_task_codes or ([request_task_code] if request_task_code else []))
            response_matches_order = bool(response.ok and remote_task_code and response_task_code == remote_task_code)
            # Un error de observación nunca demuestra un fallo físico de la tarea.
            failed_message_matches_order = False
            matched_response_payload = matched_item.get("raw") if isinstance(matched_item, dict) else response.raw
            matched_status = _normalize_remote_status(str((matched_item or {}).get("taskStatus") or (matched_item or {}).get("task_status") or "").strip())
            if matched_status in {"not_found", "not found"}:
                # Keep the actual success/identity envelope required by recovery;
                # a detached task item cannot prove a successful query.
                matched_response_payload = response.raw
            db.execute(update(MovementOrder).where(MovementOrder.id == row.id).values(id=MovementOrder.id))
            db.refresh(row)
            if row.status in TERMINAL_LOCAL_STATUSES or (local_reconciliation_pending(row) and matched_status != row.rcs_status):
                response_matches_order = False
            matched_message = str((matched_item or {}).get("message") or (response.message if response_matches_order or failed_message_matches_order else "") or "")
            row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
            row.status_query_response_json = json.dumps(matched_response_payload, ensure_ascii=False)
            row.status_query_checked_at = now
            row.rcs_last_update = now
            # La identidad de consulta se conserva únicamente en JSON/historial.
            if response_matches_order:
                row.rcs_status = matched_status or row.rcs_status
            elif failed_message_matches_order:
                row.rcs_status = "failed"
            if (response_matches_order or failed_message_matches_order) and not local_reconciliation_pending(row):
                row.rcs_message = matched_message or row.rcs_message
            _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=matched_response_payload, message=matched_message or "", arrived_at=now)
            db.commit()
            db.refresh(row)
            if response_matches_order and matched_status:
                if row.dispatch_status == "uncertain":
                    row.dispatch_status = "success"
                row = apply_remote_status_to_order(db, row, matched_status, source="rcs_callback")
            elif failed_message_matches_order:
                row = apply_remote_status_to_order(db, row, "failed", source="rcs_callback")
            else:
                logger.warning(
                    "TASK_STATUS_SKIP_MISMATCH movement_order_id=%s order_remote_task_code=%s response_taskCode=%s reason=manual status query no exact taskCode match",
                    row.id,
                    remote_task_code,
                    response_task_code or failed_task_code or "-",
                )
            row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
            if response_matches_order:
                row.rcs_status = matched_status or row.rcs_status
            elif failed_message_matches_order:
                row.rcs_status = "failed"
            if (response_matches_order or failed_message_matches_order) and not local_reconciliation_pending(row):
                row.rcs_message = matched_message or row.rcs_message
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return _movement_order_out_read_optimized(db, row)
        except RcsError as exc:
            _append_debug_console_event(db, direction="received", module="status_query", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
            now = datetime.utcnow()
            error_payload = {"error": str(exc)}
            row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
            row.status_query_response_json = json.dumps(error_payload, ensure_ascii=False)
            row.status_query_checked_at = now
            row.rcs_last_update = now
            # No sobrescribir el reqCode de creación al fallar una consulta.
            if not local_reconciliation_pending(row):
                row.rcs_message = str(exc)
            _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=error_payload, message=str(exc), arrived_at=now)
            db.commit()
            db.refresh(row)
            return _movement_order_out_read_optimized(db, row)


@app.get("/api/rcs/debug-log", response_model=List[DebugConsoleEventOut])
def get_rcs_debug_log(limit: int = 200):
    limit = max(1, min(int(limit or 200), 1000))
    with SessionLocal() as db:
        rows = db.execute(select(DebugConsoleEvent).order_by(DebugConsoleEvent.id.desc()).limit(limit)).scalars().all()
        rows = list(reversed(rows))
        return [_debug_console_event_out(row) for row in rows]


@app.post("/api/rcs/debug-send")
def send_debug_console_json(body: DebugConsoleSendIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        payload = body.payload or {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="El JSON de consola debe ser un objeto.")
        base_url, endpoint = _resolve_rcs_target(db, base_url=body.base_url, endpoint=body.endpoint, mode="query")
        if not base_url:
            raise HTTPException(status_code=400, detail="Configura la URL base del RCS o escríbela en la consola.")
        client = _get_rcs_client_with_overrides(db, base_url=base_url, query_endpoint=endpoint)
        _append_debug_console_event(db, direction="sent", module="debug_console", base_url=base_url, endpoint=endpoint, payload=payload, message="Envío extraordinario autorizado por administrador; sin MovementOrder")
        try:
            raw_response = trace_call("debug", [], client.post_json_payload, payload, endpoint_override=endpoint)
            _append_debug_console_event(db, direction="received", module="debug_console", base_url=base_url, endpoint=endpoint, payload=raw_response, message=str(raw_response.get("message") or "Respuesta recibida"))
            return sanitize({"ok": True, "base_url": base_url, "endpoint": endpoint, "response": raw_response})
        except RcsError as exc:
            error_payload = {"error": str(exc)}
            _append_debug_console_event(db, direction="received", module="debug_console", base_url=base_url, endpoint=endpoint, payload=error_payload, message=str(exc))
            raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/movement-orders/{order_id}/simulate-complete", response_model=MovementOrderOut)
def simulate_complete_movement_order(order_id: int, body: Optional[SimulateCompleteIn] = None, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    if not body or not body.confirm_simulation or not body.reason.strip():
        raise HTTPException(status_code=409, detail="Simulación local: modifica inventario y libera recursos; no confirma RCS ni continúa FIFO Chain. Requiere confirm_simulation y motivo.")
    with SessionLocal() as db:
        db.execute(update(MovementOrder).where(MovementOrder.id == order_id).values(updated_at=MovementOrder.updated_at))
        before_order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not before_order:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        if not _truthy_config_value(get_setting(db, "rcs_simulator_enabled", "0")):
            raise HTTPException(status_code=409, detail="Simulate complete está disponible únicamente con el simulador explícitamente habilitado.")
        creation = _safe_json_loads_any(before_order.dispatch_response_json)
        if (before_order.remote_task_code or before_order.dispatch_request_json or before_order.dispatch_status != "not_sent") and not (isinstance(creation, dict) and creation.get("simulated") is True):
            raise HTTPException(status_code=409, detail="La orden tiene un envío real o incierto; activar el simulador no permite finalizarla localmente.")
        _append_status_query_log(db, before_order, kind="admin_simulate_complete", request_payload={"actor": "admin", "reason": body.reason.strip(), "confirmed": True, "reqCode": before_order.req_code, "taskCode": before_order.remote_task_code}, response_payload={"previous_local_status": before_order.status, "remote_status_preserved": before_order.rcs_status})
        db.flush()
        before_rack_id = before_order.rack_id if before_order else None
        before_rack = db.execute(select(Rack).where(Rack.id == before_rack_id)).scalar_one_or_none() if before_rack_id else None
        old_rack_status = before_rack.status if before_rack else None
        row = simulate_order_completed(db, order_id, local_simulation=True)
        logger.info("Task completed | task_id=%s | order_code=%s | rack_id=%s | status=%s | source=simulate_complete", row.id, row.order_code, row.rack_id, row.status)
        if before_rack_id and old_rack_status and not rack_status_is_available(old_rack_status):
            rack = db.execute(select(Rack).where(Rack.id == before_rack_id)).scalar_one_or_none()
            if rack and rack_status_is_available(rack.status):
                _log_rack_status_change(db, rack=rack, old_status=old_rack_status, new_status=rack.status, reason="order_completed", order_id=row.id, auto_commit=True)
        return _movement_order_out_read_optimized(db, row)


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
        return _movement_order_out_read_optimized(db, row)


@app.delete("/api/admin/movement-orders/{order_id}")
def admin_delete_movement_order(order_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        db.execute(update(MovementOrder).where(MovementOrder.id == order_id).values(updated_at=MovementOrder.updated_at))
        row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        order_code = row.order_code
        if row.status in RACK_RESERVATION_ACTIVE_STATUSES or row.remote_task_code or row.req_code or row.dispatch_request_json or row.forced_local_close or row.fifo_chain_group_id:
            raise HTTPException(status_code=409, detail="No se puede borrar una orden activa o con trazabilidad RCS/cierre local/cadena. Usa diagnóstico y conserva el historial.")
        db.delete(row)
        db.commit()
        logger.info("Movement order deleted by admin order_id=%s order_code=%s", order_id, order_code)
        return {"ok": True, "order_id": order_id, "order_code": order_code, "message": f"Orden {order_code} borrada permanentemente del historial y del monitoreo."}


def _guard_admin_inventory_edit(db, *, rack_ids=(), location_ids=()) -> None:
    rack_ids = [value for value in rack_ids if value]
    location_ids = [value for value in location_ids if value]
    # Keep validation and mutation in one writer transaction against dispatch.
    if rack_ids:
        db.execute(update(Rack).where(Rack.id.in_(rack_ids)).values(updated_at=Rack.updated_at))
    if location_ids:
        db.execute(update(Location).where(Location.id.in_(location_ids)).values(updated_at=Location.updated_at))
    db.expire_all()
    active = db.execute(select(MovementOrder.id).where(
        MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
        or_(MovementOrder.rack_id.in_(rack_ids), MovementOrder.pickup_rack_id.in_(rack_ids),
            MovementOrder.dropoff_rack_id.in_(rack_ids), MovementOrder.source_cell_id.in_(location_ids),
            MovementOrder.destination_cell_id.in_(location_ids)),
    ).limit(1)).first()
    if active:
        raise HTTPException(status_code=409, detail=f"Inventario protegido por orden activa {active[0]}; usa recuperación con evidencia RCS o cierre extraordinario confirmado.")


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
        _guard_admin_inventory_edit(db, location_ids=[r.id], rack_ids=[r.rack_id, patch.rack_id])
        if patch.status is not None and int(patch.status) == 0:
            _validate_location_can_mark_free(db, r)
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
        _guard_admin_inventory_edit(db, location_ids=[r.id], rack_ids=[r.rack_id, body.rack_id])
        if int(body.status) == 0:
            _validate_location_can_mark_free(db, r)
        _validate_foreign_keys(db, body.area_id, body.rack_id, ignore_xy=(x, y))
        r.code = (body.code or "").strip() or None
        r.status = body.status
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


def _catalog_ids_from_json(value) -> set[int]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    result = set()
    for item in parsed:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _catalog_ref(row, label_attr: str = "name") -> dict:
    return {"id": row.id, "code": getattr(row, "code", None), "name": getattr(row, label_attr, None)}


def _material_dependencies(db, material_id: int) -> dict:
    racks = db.execute(select(Rack).where(Rack.material_group_id == material_id).order_by(Rack.code.asc())).scalars().all()
    qr_rows = db.execute(select(QrActionRule).where(QrActionRule.is_active == 1)).scalars().all()
    qr_material_fields = ("material_group_id", "fifo_chain_step1_material_group_id", "fifo_chain_step2_material_group_id", "fifo_chain_step3_material_group_id", "fifo_chain_step4_material_group_id", "fifo_chain_step5_material_group_id")
    qr_rules = [row for row in qr_rows if any(getattr(row, field, None) == material_id for field in qr_material_fields)]
    scanner_rows = db.execute(select(ScannerStation).where(ScannerStation.is_active == 1)).scalars().all()
    scanner_material_fields = ("default_material_group_id", "fifo_chain_step1_material_group_id", "fifo_chain_step2_material_group_id", "fifo_chain_step3_material_group_id", "fifo_chain_step4_material_group_id", "fifo_chain_step5_material_group_id")
    scanners = [row for row in scanner_rows if any(getattr(row, field, None) == material_id for field in scanner_material_fields)]
    transitions = db.execute(select(QrTransitionRule).where(QrTransitionRule.is_active == 1, or_(QrTransitionRule.current_material_group_id == material_id, QrTransitionRule.next_material_group_id == material_id))).scalars().all()
    active_orders = db.execute(select(MovementOrder).where(MovementOrder.material_group_id == material_id, MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))).scalars().all()
    window_buttons = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.is_active == 1, OperatorWindowButton.material_group_id == material_id)).scalars().all()
    active = {
        "racks": [_catalog_ref(row) for row in racks],
        "qr_rules": [{"id": row.id, "code": row.qr_value, "name": row.qr_alias} for row in qr_rules],
        "scanners": [{"id": row.id, "code": row.scanner_code, "name": row.name} for row in scanners],
        "transitions": [{"id": row.id, "name": row.name} for row in transitions],
        "active_orders": [{"id": row.id, "code": row.order_code, "status": row.status} for row in active_orders],
        "operator_buttons": [{"id": row.id, "name": row.label} for row in window_buttons],
    }
    historical = {
        "movement_orders": int(db.execute(select(func.count()).select_from(MovementOrder).where(MovementOrder.material_group_id == material_id, MovementOrder.status.notin_(RACK_RESERVATION_ACTIVE_STATUSES))).scalar_one() or 0),
        "scan_events": int(db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.material_group_id == material_id)).scalar_one() or 0),
        "transition_logs": int(db.execute(select(func.count()).select_from(QrTransitionLog).where(or_(QrTransitionLog.previous_material_group_id == material_id, QrTransitionLog.next_material_group_id == material_id))).scalar_one() or 0),
    }
    return {"active_dependencies": active, "historical_references": historical, "can_delete": not any(active.values())}


def _area_dependencies(db, area_id: int) -> dict:
    locations = db.execute(select(Location).where(Location.area_id == area_id, or_(Location.enabled == 1, Location.is_visible == 1))).scalars().all()
    rack_locations = [row for row in locations if row.rack_id is not None]
    active_orders = db.execute(select(MovementOrder).where(or_(MovementOrder.source_area_id == area_id, MovementOrder.destination_area_id == area_id), MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))).scalars().all()
    scanner_rows = db.execute(select(ScannerStation).where(ScannerStation.is_active == 1)).scalars().all()
    qr_rows = db.execute(select(QrActionRule).where(QrActionRule.is_active == 1)).scalars().all()
    direct_area_fields = ("source_area_id", "destination_area_id", "second_source_area_id", "second_destination_area_id", "storage_area_id", "empty_rack_area_id", "cancel_return_area_id", "fifo_chain_step3_source_area_id", "fifo_chain_step3_destination_area_id", "fifo_chain_step4_source_area_id", "fifo_chain_step4_destination_area_id", "fifo_chain_step5_source_area_id", "fifo_chain_step5_destination_area_id")
    json_area_fields = tuple(f"fifo_chain_step{step}_{kind}_area_ids_json" for step in range(1, 6) for kind in ("source", "destination"))
    def uses_area(row):
        return any(getattr(row, field, None) == area_id for field in direct_area_fields) or any(area_id in _catalog_ids_from_json(getattr(row, field, None)) for field in json_area_fields)
    scanners = [row for row in scanner_rows if uses_area(row)]
    qr_rules = [row for row in qr_rows if uses_area(row)]
    transitions = db.execute(select(QrTransitionRule).where(QrTransitionRule.is_active == 1, or_(QrTransitionRule.source_area_id == area_id, QrTransitionRule.destination_area_id == area_id))).scalars().all()
    window_buttons = db.execute(select(OperatorWindowButton).where(OperatorWindowButton.is_active == 1, or_(OperatorWindowButton.source_area_id == area_id, OperatorWindowButton.destination_area_id == area_id))).scalars().all()
    active = {
        "locations": [{"id": row.id, "code": _cell_code(row), "enabled": row.enabled, "is_visible": row.is_visible} for row in locations],
        "racks": [{"location_id": row.id, "rack_id": row.rack_id, "location": _cell_code(row)} for row in rack_locations],
        "active_orders": [{"id": row.id, "code": row.order_code, "status": row.status} for row in active_orders],
        "scanners": [{"id": row.id, "code": row.scanner_code, "name": row.name} for row in scanners],
        "qr_rules": [{"id": row.id, "code": row.qr_value, "name": row.qr_alias} for row in qr_rules],
        "transitions": [{"id": row.id, "name": row.name} for row in transitions],
        "cancel_return_configs": [{"id": row.id, "code": row.scanner_code, "name": row.name} for row in scanner_rows if row.cancel_return_area_id == area_id],
        "operator_buttons": [{"id": row.id, "name": row.label} for row in window_buttons],
    }
    historical = {
        "movement_orders": int(db.execute(select(func.count()).select_from(MovementOrder).where(or_(MovementOrder.source_area_id == area_id, MovementOrder.destination_area_id == area_id), MovementOrder.status.notin_(RACK_RESERVATION_ACTIVE_STATUSES))).scalar_one() or 0),
        "scan_events": int(db.execute(select(func.count()).select_from(ScanEvent).where(or_(ScanEvent.source_area_id == area_id, ScanEvent.destination_area_id == area_id))).scalar_one() or 0),
    }
    return {"active_dependencies": active, "historical_references": historical, "can_delete": not any(active.values())}


def _audit_catalog_state(db, event: str, row, actor: str, dependencies: dict, now: datetime) -> None:
    payload = {"event": event, "id": row.id, "code": row.code, "name": row.name, "actor": actor, **dependencies, "timestamp": now.isoformat()}
    _append_debug_console_event(db, direction="received", module="catalog_admin", endpoint=event, payload=payload, message=f"{event} id={row.id} code={row.code}", created_at=now, auto_commit=False)
    logger.info("%s id=%s code=%s actor=%s dependencies=%s timestamp=%s", event, row.id, row.code, actor, json.dumps(dependencies, ensure_ascii=False), now.isoformat())


@app.get("/api/admin/areas", response_model=List[AreaOut])
def list_areas(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(Area)
        if not include_inactive:
            stmt = stmt.where(Area.is_active == 1)
        return [_to_area_out(r) for r in db.execute(stmt.order_by(Area.priority.asc(), Area.name.asc())).scalars().all()]


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
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="El área está inactiva; restáurala antes de editarla")
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
        dependencies = _area_dependencies(db, area_id)
        if int(row.is_active or 0) == 0:
            return {"ok": True, "already_inactive": True, **dependencies}
        if not dependencies["can_delete"]:
            raise HTTPException(status_code=409, detail={"message": "El área tiene dependencias operativas activas", **dependencies})
        now = datetime.utcnow()
        row.is_active = 0
        row.updated_at = now
        _audit_catalog_state(db, "AREA_DEACTIVATED", row, _admin_soft_delete_actor(x_admin_token), dependencies, now)
        db.commit()
        return {"ok": True, "is_active": 0, **dependencies}


@app.get("/api/admin/areas/{area_id}/dependencies")
def get_area_dependencies(area_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Área no encontrada")
        return {"entity": "area", "id": row.id, "code": row.code, "name": row.name, **_area_dependencies(db, area_id)}


@app.post("/api/admin/areas/{area_id}/restore", response_model=AreaOut)
def restore_area(area_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Área no encontrada")
        if int(row.is_active or 0) == 1:
            return _to_area_out(row)
        conflict = db.execute(select(Area).where(Area.id != area_id, Area.is_active == 1, or_(Area.code == row.code, Area.name == row.name))).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail="Existe otra área activa con el mismo código o nombre")
        dependencies = _area_dependencies(db, area_id)
        now = datetime.utcnow(); row.is_active = 1; row.updated_at = now
        _audit_catalog_state(db, "AREA_RESTORED", row, _admin_soft_delete_actor(x_admin_token), dependencies, now)
        db.commit(); db.refresh(row)
        return _to_area_out(row)


@app.get("/api/admin/materials", response_model=List[MaterialGroupOut])
@app.get("/api/admin/material-groups", response_model=List[MaterialGroupOut])
def list_materials(include_inactive: bool = False, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        stmt = select(MaterialGroup)
        if not include_inactive:
            stmt = stmt.where(MaterialGroup.is_active == 1)
        return [_to_material_out(r) for r in db.execute(stmt.order_by(MaterialGroup.name.asc())).scalars().all()]


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
        if int(row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="El material está inactivo; restáuralo antes de editarlo")
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
@app.delete("/api/admin/material-groups/{material_id}")
def delete_material(material_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Material no encontrado")
        dependencies = _material_dependencies(db, material_id)
        if int(row.is_active or 0) == 0:
            return {"ok": True, "already_inactive": True, **dependencies}
        if not dependencies["can_delete"]:
            raise HTTPException(status_code=409, detail={"message": "El material tiene dependencias operativas activas", **dependencies})
        now = datetime.utcnow(); row.is_active = 0; row.updated_at = now
        _audit_catalog_state(db, "MATERIAL_GROUP_DEACTIVATED", row, _admin_soft_delete_actor(x_admin_token), dependencies, now)
        db.commit()
        return {"ok": True, "is_active": 0, **dependencies}


@app.get("/api/admin/materials/{material_id}/dependencies")
@app.get("/api/admin/material-groups/{material_id}/dependencies")
def get_material_dependencies(material_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Material no encontrado")
        return {"entity": "material", "id": row.id, "code": row.code, "name": row.name, **_material_dependencies(db, material_id)}


@app.post("/api/admin/materials/{material_id}/restore", response_model=MaterialGroupOut)
@app.post("/api/admin/material-groups/{material_id}/restore", response_model=MaterialGroupOut)
def restore_material(material_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    with SessionLocal() as db:
        row = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Material no encontrado")
        if int(row.is_active or 0) == 1:
            return _to_material_out(row)
        conflict = db.execute(select(MaterialGroup).where(MaterialGroup.id != material_id, MaterialGroup.is_active == 1, or_(MaterialGroup.code == row.code, MaterialGroup.name == row.name))).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail="Existe otro material activo con el mismo código o nombre")
        dependencies = _material_dependencies(db, material_id)
        now = datetime.utcnow(); row.is_active = 1; row.updated_at = now
        _audit_catalog_state(db, "MATERIAL_GROUP_RESTORED", row, _admin_soft_delete_actor(x_admin_token), dependencies, now)
        db.commit(); db.refresh(row)
        return _to_material_out(row)


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


def _fresh_recovery_status(order: MovementOrder, *, now: Optional[datetime] = None) -> str:
    """Positive task-specific observation, never absence of cached activity."""
    now = now or datetime.utcnow()
    checked = order.status_query_checked_at
    task_code = str(order.remote_task_code or "").strip()
    if not task_code or not checked or not 0 <= (now - checked).total_seconds() <= 60:
        return ""
    if order.dispatched_at and checked < order.dispatched_at:
        return ""
    if order.rcs_last_update and checked < order.rcs_last_update:
        return ""
    request = _safe_json_loads_any(order.status_query_request_json)
    payload = _safe_json_loads_any(order.status_query_response_json)
    if not isinstance(request, dict) or not isinstance(payload, dict) or payload.get("error"):
        return ""
    codes = request.get("taskCodes") or [request.get("taskCode")]
    if not isinstance(codes, list) or task_code not in codes:
        return ""
    if "code" in payload and str(payload["code"]) != "0":
        return ""
    if payload.get("reqCode") and payload["reqCode"] != request.get("reqCode"):
        return ""
    data = payload.get("data")
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else [payload]
    for item in items:
        if not isinstance(item, dict) or item.get("error") or str(item.get("taskCode") or item.get("task_code") or "") != task_code:
            continue
        if "code" in item and str(item["code"]) != "0":
            continue
        status = str(item.get("taskStatus") or item.get("task_status") or "").strip().lower()
        if status in {"not_found", "not found"}:
            # A successful task-scoped not-found, not a transport 404 or [] result.
            return "not_found" if str(payload.get("code", item.get("code", ""))) == "0" else ""
        return _normalized_cleanup_rcs_terminal_status(status)
    return ""


def _status_query_missing_task(order: MovementOrder) -> bool:
    return _fresh_recovery_status(order) == "not_found"


def _safe_local_release_evidence(order: MovementOrder, *, now: Optional[datetime] = None) -> bool:
    if local_reconciliation_pending(order) or order.status == "cancel_requested_undo":
        return False
    return _fresh_recovery_status(order, now=now) in {"completed", "cancelled", "failed", "not_found"}


def _cancel_undo_robot_recovery_reason(order: MovementOrder, *, now: datetime, min_age_minutes: int) -> tuple[bool, str]:
    if order.status == "cancel_requested_undo":
        return False, "POSICION_FINAL_REQUIERE_RECONCILIACION"
    observed = _fresh_recovery_status(order, now=now)
    if observed == "not_found":
        return True, "SAFE_NOT_FOUND_IN_RCS"
    if observed in CANCEL_RETURN_RCS_TERMINAL_STATUSES and not local_reconciliation_pending(order):
        return True, "SAFE_CANCEL_COMPLETED"
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
    if not _safe_local_release_evidence(order, now=now):
        return False, "Se requiere consulta RCS reciente y fiable de esta tarea; caché/timeout no confirma liberación"
    local_status = str(order.status or "").strip().lower()
    allowed_statuses = {"cancel_requested_undo", "cancel_requested_total", "dispatched"}
    rcs_status = str(order.rcs_status or "").strip().lower()
    normalized_terminal = _normalized_cleanup_rcs_terminal_status(rcs_status)
    confirmed_failed_active_order = local_status in RACK_RESERVATION_ACTIVE_STATUSES and normalized_terminal == "failed"
    if local_status not in allowed_statuses and not confirmed_failed_active_order:
        return False, "status no elegible"
    if not normalized_terminal:
        return False, "rcs_status no es terminal confirmado"
    created_at = order.created_at or order.updated_at or now
    if created_at > now - timedelta(minutes=min_age_minutes):
        return False, f"menor a {min_age_minutes} minutos"
    if local_status == "in_progress" and normalized_terminal != "failed":
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
                | ((MovementOrder.created_at >= recent_cutoff) & (MovementOrder.status.notin_(("completed", "cancelled", "undone", "failed", "forced_local_closed", "dispatch_error"))))
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
    if not _safe_local_release_evidence(order, now=now):
        raise HTTPException(status_code=409, detail="Recuperación sin evidencia RCS fiable; recursos retenidos.")
    old_status = order.status or ""
    if _normalized_cleanup_rcs_terminal_status(order.rcs_status) == "failed":
        return _force_close_old_active_order(db, order, now=now, admin_user=admin_user, reason=reason)
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


FORCE_RELEASE_ACTIVE_LOCAL_STATUSES = {
    "pending_dispatch", "dispatched", "in_progress",
    "cancel_requested_total", "cancel_requested_undo", "cancel_error",
}
FORCE_RELEASE_ACTIVE_RCS_STATUSES = {
    "2", "dispatched", "in_progress", "executing", "running", "moving", "working", "canceling",
}
FORCE_RELEASE_RCS_ACTIVE_WARNING = (
    "RCS puede seguir ejecutando la tarea. Este cierre es solo local: no detiene al robot "
    "ni cancela RCS y hará reutilizable el rack. Requiere confirm_local_only=true y un motivo explícito."
)


def _order_age_anchor(order: MovementOrder) -> datetime:
    return order.dispatched_at or order.created_at or datetime.utcnow()


def _age_minutes(now: datetime, value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds() // 60))


def _rcs_status_still_active(order: MovementOrder) -> bool:
    return str(order.rcs_status or "").strip().lower() in FORCE_RELEASE_ACTIVE_RCS_STATUSES


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
    if (order.status or "") not in FORCE_RELEASE_ACTIVE_LOCAL_STATUSES:
        return False, f"orden no activa ID {order.id}"
    if _safe_local_release_evidence(order, now=now):
        return True, "Consulta RCS reciente y fiable: recuperación inmediata sin espera por antigüedad."
    age = _age_minutes(now, _order_age_anchor(order)) or 0
    if age < min_age_minutes:
        return False, (
            f"La orden tiene {age} minutos. Podrá revisarse para cierre local después de "
            f"{min_age_minutes} minutos."
        )
    rcs_status = str(order.rcs_status or "desconocido").strip().lower()
    if _rcs_status_still_active(order):
        return True, (
            f"Orden activa desde hace {age} minutos. RCS todavía reporta {rcs_status}. "
            "Se permite únicamente cierre local manual con confirmación."
        )
    return True, f"Orden activa desde hace {age} minutos; elegible para cierre local manual."


def _force_close_old_active_order(db, order: MovementOrder, *, now: datetime, admin_user: str, reason: str = "", confirm_local_only: bool = False) -> dict:
    evidence = _safe_local_release_evidence(order, now=now)
    if not evidence and not (confirm_local_only and reason.strip()):
        raise HTTPException(status_code=409, detail=FORCE_RELEASE_RCS_ACTIVE_WARNING)
    old_status = order.status or ""
    remote_before = order.rcs_status
    message_before = order.rcs_message
    rcs_status = str(order.rcs_status or "").strip().lower()
    normalized_rcs_terminal = _normalized_cleanup_rcs_terminal_status(rcs_status)
    is_confirmed_cancel_return = old_status == "cancel_requested_undo" and rcs_status in CANCEL_RETURN_RCS_TERMINAL_STATUSES
    is_confirmed_cancel_request = old_status == "cancel_requested_total" and rcs_status == "cancelled"
    if not evidence or normalized_rcs_terminal == "completed":
        order.status = "forced_local_closed"
        order.forced_local_close_reason = (reason or "admin_local_close_without_inventory_reconciliation")[:128]
    elif is_confirmed_cancel_return:
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
        order.forced_local_close_reason = (reason or "admin_force_release_old_active_order")[:128]
    order.rcs_status = remote_before
    order.rcs_message = ((message_before or "") + " | CIERRE LOCAL EXTRAORDINARIO: no cancela RCS ni confirma movimiento lógico.")[:512]
    db.add(order)
    db.flush()
    stop_fifo_chain(db, order)
    _append_status_query_log(db, order, kind="admin_force_local_close", request_payload={"reqCode": order.req_code, "taskCode": order.remote_task_code, "actor": admin_user, "reason": reason, "confirm_local_only": confirm_local_only}, response_payload={"previous_local_status": old_status, "remote_status_preserved": remote_before, "remote_message": message_before, "fresh_evidence": evidence})
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
    order.rcs_status = remote_before
    db.add(order)
    rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none() if order.rack_id else None
    if old_status == "cancel_requested_undo":
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
    age_minutes = _age_minutes(now, _order_age_anchor(order))
    logger.info(
        "FORCE_RELEASE_OLD_ACTIVE_ORDER order_id=%s rack_id=%s rack_code=%s remote_task_code=%s robot_code=%s "
        "order_age_minutes=%s last_rcs_status=%s actor=%s reason=%s rcs_still_active=%s",
        order.id,
        order.rack_id,
        rack.code if rack else None,
        order.remote_task_code,
        order.agv_code,
        age_minutes,
        rcs_status,
        admin_user,
        order.forced_local_close_reason,
        rcs_status in FORCE_RELEASE_ACTIVE_RCS_STATUSES,
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
            "rack_code": rack.code if rack else None,
            "old_status": old_status,
            "new_status": order.status,
            "reason": order.forced_local_close_reason,
            "event": "FORCE_RELEASE_OLD_ACTIVE_ORDER",
            "remote_task_code": order.remote_task_code,
            "robot_code": order.agv_code,
            "order_age_minutes": age_minutes,
            "last_rcs_status": rcs_status,
            "actor": admin_user,
            "rcs_still_active": rcs_status in FORCE_RELEASE_ACTIVE_RCS_STATUSES,
            "at": now.isoformat(),
        },
        message=f"[FORCE RELEASE] Orden {order.id} forced_local_closed",
        created_at=now,
        auto_commit=False,
    )
    return {"order_id": order.id, "order_code": order.order_code, "rack_id": order.rack_id, "old_status": old_status, "new_status": order.status, "order_age_minutes": age_minutes, "local_only": True, "warning": FORCE_RELEASE_RCS_ACTIVE_WARNING, "remote_status": remote_before}


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
        # Idle/not-found/age alone does not confirm cancelTask or its return.
        if str(order.rcs_status or "").strip().lower() not in CANCEL_RETURN_RCS_TERMINAL_STATUSES:
            continue
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


def _diagnose_inconsistent_locations(db, *, location_ids: Optional[set[int]] = None) -> List[dict]:
    """Classify functional Location inconsistencies without changing physical assignments."""
    locations = db.execute(select(Location).order_by(Location.id.asc())).scalars().all()
    if location_ids is not None:
        locations = [row for row in locations if row.id in location_ids]
    rack_ids = {int(row.rack_id) for row in locations if row.rack_id is not None and int(row.rack_id) > 0}
    racks = db.execute(select(Rack).where(Rack.id.in_(rack_ids))).scalars().all() if rack_ids else []
    rack_by_id = {row.id: row for row in racks}
    area_ids = {row.area_id for row in locations if row.area_id}
    areas = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all() if area_ids else []
    area_by_id = {row.id: row for row in areas}
    duplicate_rack_ids = {
        int(rack_id) for rack_id, count in db.execute(
            select(Location.rack_id, func.count(Location.id))
            .where(Location.rack_id.is_not(None))
            .group_by(Location.rack_id)
            .having(func.count(Location.id) > 1)
        ).all() if rack_id is not None
    }
    active_orders = db.execute(
        select(MovementOrder).where(MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))
    ).scalars().all()
    active_by_location: dict[int, list[MovementOrder]] = {}
    for order in active_orders:
        for location_id in {order.source_cell_id, order.destination_cell_id}:
            if location_id:
                active_by_location.setdefault(int(location_id), []).append(order)

    results: List[dict] = []
    for row in locations:
        status = int(row.status or 0)
        rack = rack_by_id.get(row.rack_id) if row.rack_id else None
        issue_code = ""
        reason = ""
        suggested_repair = "manual_review"
        if row.rack_id is not None and (int(row.rack_id) <= 0 or rack is None):
            issue_code = "rack_reference_missing"
            reason = "La celda apunta a un rack inexistente."
        elif row.rack_id in duplicate_rack_ids:
            issue_code = "multiple_locations_same_rack"
            reason = "Más de una Location apunta al mismo rack."
        elif row.rack_id is None and status != 0:
            issue_code = "occupied_without_rack"
            reason = "La celda está marcada como ocupada, pero no tiene rack."
            suggested_repair = "mark_free"
        elif row.rack_id is not None and status != 1:
            issue_code = "free_with_rack"
            reason = "La celda está marcada como libre, pero contiene un rack."
            suggested_repair = "mark_occupied_after_review"
        if not issue_code:
            continue

        blocking_reasons: List[str] = []
        operational_warnings: List[str] = []
        orders = active_by_location.get(row.id, [])
        if orders:
            labels = ", ".join(str(order.order_code or order.id) for order in orders)
            blocking_reasons.append(f"La celda participa en la orden activa {labels}. Cierra o recupera primero la orden desde Recuperación avanzada en Diagnóstico y limpieza.")
        if int(row.enabled or 0) != 1:
            operational_warnings.append("La celda está deshabilitada: se corregirá únicamente su estado de ocupación.")
        if int(row.is_visible or 0) != 1:
            operational_warnings.append("La celda está oculta: se corregirá únicamente su estado de ocupación.")
        if issue_code != "occupied_without_rack":
            blocking_reasons.append("Esta clase requiere revisión manual de la relación física con el rack.")
        safe_to_repair = issue_code == "occupied_without_rack" and not blocking_reasons
        area = area_by_id.get(row.area_id) if row.area_id else None
        results.append({
            "location_id": row.id,
            "code": row.code,
            "x": row.x,
            "y": row.y,
            "area_id": row.area_id,
            "area_name": area.name if area else None,
            "status": status,
            "rack_id": row.rack_id,
            "rack_code": rack.code if rack else None,
            "issue_code": issue_code,
            "reason": reason,
            "safe_to_repair": safe_to_repair,
            "blocking_reasons": blocking_reasons,
            "operational_warnings": operational_warnings,
            "suggested_repair": suggested_repair,
            "location_in_active_order": bool(orders),
        })
    return results


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
        WHERE lower(trim(coalesce(mo.rcs_status, ''))) IN ('completed', 'cancelled', 'canceled', 'failed', 'aborted', 'terminated')
          AND (
              mo.status IN ('cancel_requested_undo', 'cancel_requested_total', 'dispatched')
              OR (mo.status = 'in_progress' AND lower(trim(coalesce(mo.rcs_status, ''))) IN ('failed', 'aborted', 'terminated'))
          )
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
    inconsistent_locations = _diagnose_inconsistent_locations(db)
    old_active_racks = []
    active_order_rows = db.execute(
        select(MovementOrder, Rack)
        .join(Rack, Rack.id == MovementOrder.rack_id)
        .where(MovementOrder.status.in_(active_statuses))
        .order_by(Rack.id.asc(), MovementOrder.created_at.asc(), MovementOrder.id.asc())
    ).all()
    for order, rack in active_order_rows:
        age_minutes = _age_minutes(now, _order_age_anchor(order)) or 0
        last_query_age_minutes = _age_minutes(now, order.status_query_checked_at)
        meaningful_at = getattr(order, "rcs_status_changed_at", None) or order.dispatched_at or order.created_at
        last_meaningful_rcs_change_age_minutes = _age_minutes(now, meaningful_at)
        terminal_rcs_status = _normalized_cleanup_rcs_terminal_status(order.rcs_status)
        safe, safe_reason = _validate_force_release_order(order, now=now, min_age_minutes=force_min_age_minutes)
        active_other = _active_orders_for_rack(db, rack.id, exclude_order_id=order.id)
        if safe and active_other:
            safe = False
            safe_reason = "No se puede liberar: existe otra orden activa asociada al mismo rack."
        rcs_still_active = _rcs_status_still_active(order)
        old_active_racks.append({
            "rack_id": rack.id,
            "rack_code": rack.code,
            "order_id": order.id,
            "order_status": order.status,
            "rcs_status": order.rcs_status,
            "age_minutes": age_minutes,
            "order_age_minutes": age_minutes,
            "last_query_age_minutes": last_query_age_minutes,
            "last_meaningful_rcs_change_age_minutes": last_meaningful_rcs_change_age_minutes,
            "recovery_case": "cancel_return_stuck" if (order.status or "") == "cancel_requested_undo" and str(order.rcs_status or "").strip().lower() in CANCEL_RETURN_RCS_TERMINAL_STATUSES else "old_active_order",
            "safe": bool(safe),
            "safe_to_release": bool(safe),
            "requires_force_confirmation": bool(safe),
            "confirmation_required": bool(safe),
            "rcs_still_active_warning": rcs_still_active,
            "warning": FORCE_RELEASE_RCS_ACTIVE_WARNING if rcs_still_active else "",
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


@app.post("/api/admin/repair-inconsistent-locations")
def repair_inconsistent_locations(body: RepairInconsistentLocationsIn, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin(x_admin_token)
    now = datetime.utcnow()
    actor = f"admin:{(x_admin_token or '')[-6:]}" if x_admin_token else "admin"
    requested_ids = sorted({int(value) for value in body.location_ids if int(value or 0) > 0})
    repaired: List[dict] = []
    skipped: List[dict] = []
    with SessionLocal() as db:
        for location_id in requested_ids:
            row = db.execute(select(Location).where(Location.id == location_id)).scalar_one_or_none()
            if not row:
                skipped.append({"location_id": location_id, "reason": "Location no encontrada."})
                continue
            findings = _diagnose_inconsistent_locations(db, location_ids={location_id})
            finding = findings[0] if findings else None
            if not finding:
                skipped.append({"location_id": location_id, "reason": "La Location ya no presenta una inconsistencia."})
                continue
            if not finding["safe_to_repair"] or finding["suggested_repair"] != "mark_free":
                reasons = finding["blocking_reasons"] or ["No se pudo determinar una reparación segura."]
                skipped.append({"location_id": location_id, "issue_code": finding["issue_code"], "reason": " ".join(reasons)})
                continue
            before_status = int(row.status or 0)
            row.status = 0
            row.updated_at = now
            db.add(row)
            audit_payload = {
                "event": "REPAIR_INCONSISTENT_LOCATION",
                "location_id": row.id,
                "location_code": row.code,
                "area_id": row.area_id,
                "area_name": finding.get("area_name"),
                "issue_code": finding["issue_code"],
                "before_status": before_status,
                "after_status": 0,
                "rack_id": row.rack_id,
                "actor": actor,
                "reason": body.reason.strip(),
                "at": now.isoformat(),
            }
            _append_debug_console_event(db, direction="received", module="cleanup", payload=audit_payload, message=f"REPAIR_INCONSISTENT_LOCATION location_id={row.id} {before_status} -> 0", created_at=now, auto_commit=False)
            repaired.append({"location_id": row.id, "issue_code": finding["issue_code"], "before_status": before_status, "after_status": 0})
        db.commit()
        diagnosis = _run_cleanup_diagnosis(db)
    logger.info("REPAIR_INCONSISTENT_LOCATION completed selected=%s repaired=%s skipped=%s actor=%s", len(requested_ids), len(repaired), len(skipped), actor)
    return {"ok": True, "repaired": repaired, "skipped": skipped, "diagnosis": diagnosis}


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
                    MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
                    func.lower(func.trim(func.coalesce(MovementOrder.rcs_status, ""))).in_(CLEANUP_RCS_TERMINAL_INPUT_STATUSES),
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
            db.flush()
            for related_rack_id in _related_rack_ids_for_order(order):
                released, skipped, kept_location = _release_cleanup_rack_if_safe(
                    db,
                    related_rack_id,
                    closed_order_id=order.id,
                    now=now,
                    min_age_minutes=min_age_minutes,
                    admin_user=admin_user,
                )
                if skipped:
                    skipped_orders.append(skipped)
                elif released:
                    released_racks.append(released)
                if kept_location:
                    kept_locations += 1

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
        db.execute(update(Rack).where(Rack.id.in_(rack_ids)).values(updated_at=Rack.updated_at))
        for rack_id in rack_ids:
            rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
            if not rack:
                skipped.append({"rack_id": rack_id, "reason": "rack no encontrado"})
                continue

            active_orders = _active_orders_for_rack(db, rack_id)
            if not active_orders:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "rack sin orden activa asociada"})
                continue
            confirmed_failed_orders = [
                order for order in active_orders
                if _normalized_cleanup_rcs_terminal_status(order.rcs_status) == "failed"
                and not _robot_monitor_indicates_order_running(order)
                and _validate_force_release_order(order, now=now, min_age_minutes=min_age_minutes)[0]
            ]
            reconciled_failed_orders = bool(len(active_orders) > 1 and confirmed_failed_orders)
            if reconciled_failed_orders:
                for order in confirmed_failed_orders:
                    closed_orders.append(_force_close_old_active_order(db, order, now=now, admin_user=admin_user, reason=(body.reason or "").strip()))
                db.flush()
                active_orders = _active_orders_for_rack(db, rack_id)
            if len(active_orders) > 1:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "No se puede liberar: existe otra orden activa asociada al mismo rack."})
                continue
            if active_orders and reconciled_failed_orders:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "Orden fallida reconciliada; el rack permanece reservado porque existe otra orden activa asociada."})
                continue
            if any(not _safe_local_release_evidence(order, now=now) for order in active_orders) and not (body.confirm_local_only and (body.reason or "").strip()):
                raise HTTPException(status_code=409, detail=FORCE_RELEASE_RCS_ACTIVE_WARNING)

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

            validation_errors = []
            for order in active_orders:
                ok, reason = _validate_force_release_order(order, now=now, min_age_minutes=min_age_minutes)
                if not ok:
                    validation_errors.append(reason)
            if validation_errors:
                skipped.append({"rack_id": rack_id, "rack_code": rack.code, "reason": "; ".join(validation_errors)})
                continue

            for order in active_orders:
                closed_orders.append(_force_close_old_active_order(db, order, now=now, admin_user=admin_user, reason=(body.reason or "").strip(), confirm_local_only=body.confirm_local_only))

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
        if not _truthy_config_value(get_setting(db, "rcs_simulator_enabled", "0")):
            raise HTTPException(status_code=409, detail="La creación de órdenes de prueba requiere el simulador habilitado.")

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
        if int(mat.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="El material está inactivo; restáuralo antes de asignarlo al rack")
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
        if int(mat.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail="El material está inactivo; restáuralo antes de asignarlo al rack")
        _guard_admin_inventory_edit(db, rack_ids=[rack_id])
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
        source_area = db.execute(select(Area).where(Area.id == body.source_area_id)).scalar_one_or_none()
        if not source_area:
            raise HTTPException(status_code=400, detail='Área origen no encontrada')
        if int(source_area.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail='Área origen inactiva')
    if body.destination_area_id:
        destination_area = db.execute(select(Area).where(Area.id == body.destination_area_id)).scalar_one_or_none()
        if not destination_area:
            raise HTTPException(status_code=400, detail='Área destino no encontrada')
        if int(destination_area.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail='Área destino inactiva')
    normalized_material_group_id = body.material_group_id
    if body.action_mode == 'fifo' and not normalized_material_group_id:
        normalized_material_group_id = _ensure_no_material_group_in_db(db).id
    if normalized_material_group_id:
        material_row = db.execute(select(MaterialGroup).where(MaterialGroup.id == normalized_material_group_id)).scalar_one_or_none()
        if not material_row:
            raise HTTPException(status_code=400, detail='Material no encontrado')
        if int(material_row.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail='Material inactivo')
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
        visible_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
        if not visible_material:
            raise HTTPException(status_code=400, detail='Uno de los materiales visibles no existe')
        if int(visible_material.is_active or 0) != 1:
            raise HTTPException(status_code=409, detail='Uno de los materiales visibles está inactivo')
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
    db.refresh(row)
    if row.status in CANCEL_REQUEST_STATUSES:
        # Persisted claim: repeated requests must neither resend nor undo locally.
        stop_fifo_chain(db, row)
        db.commit()
        return row
    if row.dispatch_status == "uncertain" and not row.remote_task_code:
        raise HTTPException(status_code=409, detail="Creación incierta sin taskCode: reconciliar en RCS antes de cancelar o deshacer localmente.")
    logger.info("Cancel requested order_id=%s order_code=%s force_cancel=%s undo_on_accept=%s", row.id, row.order_code, force_cancel, undo_on_accept)
    previous_status = row.status or ""
    remote_task_code = (row.remote_task_code or '').strip()
    force_cancel_value = str(force_cancel or '0')
    # Compatibility argument retained for callers; forceCancel defines the RCS action.
    should_undo_locally = force_cancel_value == '1'
    should_call_remote_cancel = bool(remote_task_code) and row.status in {'pending_dispatch', 'dispatched', 'in_progress'}
    if not should_call_remote_cancel:
        raise HTTPException(status_code=409, detail='Se requiere una tarea remota activa con taskCode; no se ejecutará un deshacer local.')
    if force_cancel_value not in {'0', '1'}:
        raise HTTPException(status_code=400, detail='forceCancel debe ser 0 o 1')
    if should_undo_locally and not str(matter_area or '').strip():
        raise HTTPException(status_code=400, detail='Cancel Return requiere un área de retorno (matterArea).')

    cancel_payload = {
        'reqCode': generate_req_code_ms(),
        'reqTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'clientCode': '',
        'tokenCode': '',
        'forceCancel': force_cancel_value,
        'matterArea': str(matter_area or '').strip() if should_undo_locally else '',
        'agvCode': '',
        'taskCode': remote_task_code,
    }

    if should_call_remote_cancel:
        # Claim before network I/O, across sessions/processes. A crash or timeout
        # leaves the claim and reservations intact, never an automatic resend.
        pending_status = 'cancel_requested_undo' if force_cancel_value == '1' else 'cancel_requested_total'
        claimed = db.execute(update(MovementOrder).where(
            MovementOrder.id == row.id,
            MovementOrder.status == previous_status,
            MovementOrder.remote_task_code == remote_task_code,
        ).values(status=pending_status, rcs_status='cancel_unknown', updated_at=datetime.utcnow())
          .execution_options(synchronize_session=False)).rowcount
        if not claimed:
            db.rollback()
            db.refresh(row)
            if row.status in CANCEL_REQUEST_STATUSES:
                return row
            raise HTTPException(status_code=409, detail='La orden cambió durante la cancelación; actualiza su estado.')
        db.refresh(row)
        stop_fifo_chain(db, row)
        _append_status_query_log(db, row, kind='cancel_task_requested', request_payload=cancel_payload,
                                 response_payload=None, message='Cancelación pendiente de confirmación RCS')
        db.commit()
        debug_base_url = ""
        debug_endpoint = "/rcms/services/rest/hikRpcService/cancelTask"
        try:
            client = _get_rcs_client_from_settings(db)
            debug_base_url, debug_endpoint = _resolve_rcs_target(db, mode="cancel")
            token_code = getattr(client, '_token_code', '')
            cancel_payload['tokenCode'] = token_code or ''
            logger.info("[CANCEL TASK] order_id=%s order_code=%s source=%s admin=%s remote=1 force_cancel=%s reason=cancelTask", row.id, row.order_code, source, actor, force_cancel_value)
            _append_debug_console_event(db, direction="sent", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload={**cancel_payload, "source": source, "admin": actor}, message=f"Cancelación de {row.order_code}")
            cancel_response = trace_call("cancel", [row], client.cancel_task_with_payload, cancel_payload)
            db.refresh(row)
            _append_debug_console_event(db, direction="received", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload=cancel_response.raw, message=cancel_response.message or f"Respuesta cancelTask para {row.order_code}")
            now = datetime.utcnow()
            # El reqCode de cancelación queda en su JSON, no sustituye la creación.
            row.rcs_last_update = now
            row.rcs_message = cancel_response.message or row.rcs_message
            _append_status_query_log(db, row, kind='cancel_task', request_payload=cancel_payload, response_payload=cancel_response.raw, message=cancel_response.message or '', arrived_at=now)
        except Exception as exc:
            db.rollback()
            db.refresh(row)
            logger.exception("Cancel task failed | task_id=%s | order_code=%s | remote_task_code=%s | source=%s | actor=%s", row.id, row.order_code, remote_task_code or "-", source, actor)
            _append_debug_console_event(db, direction="received", module="cancel_task", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
            now = datetime.utcnow()
            error_payload = {'error': str(exc)}
            row.rcs_last_update = now
            row.rcs_message = str(exc)
            if row.status in CANCEL_REQUEST_STATUSES:
                row.rcs_status = 'cancel_unknown'
            _append_status_query_log(db, row, kind='cancel_task', request_payload=cancel_payload, response_payload=error_payload, message=str(exc), arrived_at=now)
            db.commit()
            db.refresh(row)
            raise HTTPException(status_code=502, detail=f'Error cancelando tarea en RCS: {exc}')

    if should_call_remote_cancel:
        if not cancel_response.ok:
            if row.status in CANCEL_REQUEST_STATUSES:
                row.rcs_status = 'cancel_error'
            row.rcs_status_changed_at = datetime.utcnow()
            row.rcs_message = cancel_response.message or 'El RCS rechazó la cancelación'
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            minimum_age = _force_release_min_age_minutes(db)
            order_age = _age_minutes(datetime.utcnow(), _order_age_anchor(row)) or 0
            raise HTTPException(status_code=400, detail={
                "message": f"El RCS rechazó la cancelación: {row.rcs_message}",
                "cancel_recovery": {
                    "eligible": order_age >= minimum_age,
                    "order_age_minutes": order_age,
                    "minimum_age_minutes": minimum_age,
                    "suggested_action": "Revisar en Diagnóstico y limpieza.",
                },
            })
        # A terminal query may have arrived while cancelTask was in flight.
        if row.status not in CANCEL_REQUEST_STATUSES:
            db.commit()
            return row
        row.rcs_status = 'cancel_requested'
        row.rcs_status_changed_at = datetime.utcnow()
        _audit_order_close(db, row, previous_status=previous_status, new_status=row.status, source=source, reason="cancelTask enviado al RCS", actor=actor, closed_at=datetime.utcnow(), auto_commit=False)
        rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none()
        if rack is not None:
            apply_rack_reservation_status(rack, True, updated_at=datetime.utcnow(), order_id=row.id, dispatch_status=row.status, source=source, reason="cancel_requested_waiting_remote_terminal")
            db.add(rack)
        note = 'Cancelación enviada al RCS. El rack permanece reservado mientras se espera la confirmación final de estatus.'

    row.comment = ((row.comment or '').strip() + ' | ' + note).strip(' |')[:512]
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    if should_undo_locally:
        _start_rack_position_poll_after_cancel(row.id, row.rack_id, source=source, actor=actor)
    logger.info("Cancel processed order_id=%s order_code=%s status=%s", row.id, row.order_code, row.status)
    return row


def _operator_direct_cell_ids(button_row, action_body):
    # Explicit UI selection is a pair: never mix it with stale configured defaults.
    if button_row.action_mode == 'direct_move_config' or (action_body is not None and
            {'source_cell_id', 'destination_cell_id'} & action_body.model_fields_set):
        return (action_body.source_cell_id, action_body.destination_cell_id) if action_body else (None, None)
    return button_row.source_cell_id, button_row.destination_cell_id


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
        source_cell_id, destination_cell_id = _operator_direct_cell_ids(button_row, action_body)
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
        source_cell_id, destination_cell_id = _operator_direct_cell_ids(button_row, action_body)
        agv_code = button_row.agv_code
        task_typ = button_row.task_typ
        if button_row.action_mode == 'direct_move_config':
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
            if action_body.material_group_id and not db.get(MaterialGroup, action_body.material_group_id):
                raise HTTPException(status_code=400, detail="Material inexistente")
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
        if action_body:
            # Detached value object: no inventory mutation before confirmed dispatch.
            fields = ("material_group_id", "lot", "quantity", "manufacturer_code", "comment", "rack_custom_fields_json")
            proposal = Rack(**{name: getattr(rack, name) for name in fields})
            proposal.material_group_id = proposal.material_group_id or order.material_group_id
            for name in fields[:-1]:
                if name in action_body.model_fields_set:
                    value = getattr(action_body, name)
                    if name == "material_group_id" and not value:
                        continue
                    if name == "quantity" and value is None:
                        value = 0
                    setattr(proposal, name, value.strip() or None if isinstance(value, str) else value)
            if action_body.custom_field_values:
                _apply_action_custom_fields_to_rack(proposal, action_body.custom_field_values)
            order._pending_point_inventory = {name: getattr(proposal, name) for name in fields}
            _append_status_query_log(db, order, kind="point_inventory_proposed", request_payload=order._pending_point_inventory,
                                     response_payload=None, message="No aplicado hasta aceptación de creación RCS")
            db.commit()
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


def _rollback_unsuccessful_order_dispatch(db, order: MovementOrder, error_message: Optional[str] = None) -> dict:
    db.refresh(order)
    if order.status == "dispatch_error":
        return {"executed": False, "movement_order_id": order.id, "reason": "already_compensated"}
    if order.dispatch_status == "uncertain" or order.remote_task_code:
        return {"executed": False, "movement_order_id": order.id, "reason": "remote_creation_not_safely_rejected"}
    now = datetime.utcnow()
    message = (error_message or order.rcs_message or "Dispatch error").strip()
    previous_status = order.status or ""
    rack_before = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none() if order.rack_id else None
    source_cell = db.execute(select(Location).where(Location.id == order.source_cell_id)).scalar_one_or_none() if order.source_cell_id else None
    destination_cell = db.execute(select(Location).where(Location.id == order.destination_cell_id)).scalar_one_or_none() if order.destination_cell_id else None
    rollback_result = {
        "executed": True,
        "movement_order_id": order.id,
        "order_code": order.order_code,
        "previous_status": previous_status,
        "new_status": "dispatch_error",
        "rack_id": order.rack_id,
        "rack_status_before": rack_before.status if rack_before else None,
        "rack_status_after": None,
        "released_racks": [],
        "source_cell_id": order.source_cell_id,
        "source_cell_status": source_cell.status if source_cell else None,
        "source_cell_rack_id": source_cell.rack_id if source_cell else None,
        "destination_cell_id": order.destination_cell_id,
        "destination_cell_status": destination_cell.status if destination_cell else None,
        "destination_cell_rack_id": destination_cell.rack_id if destination_cell else None,
        "error_message": message,
    }
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

    rollback_result["released_racks"] = released_racks
    db.commit()
    db.refresh(order)
    rack_after = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none() if order.rack_id else None
    rollback_result["rack_status_after"] = rack_after.status if rack_after else None
    logger.warning(
        "ROLLBACK_DISPATCH_ERROR executed=true movement_order_id=%s order_code=%s rack_id=%s previous_status=%s new_status=%s dispatch_status=%s rcs_status=%s rcs_message=%s released_racks=%s source_cell_id=%s source_cell_status=%s source_cell_rack_id=%s destination_cell_id=%s destination_cell_status=%s destination_cell_rack_id=%s rack_status_after=%s",
        order.id,
        order.order_code,
        order.rack_id,
        previous_status,
        order.status,
        order.dispatch_status,
        order.rcs_status,
        message,
        released_racks,
        rollback_result["source_cell_id"],
        rollback_result["source_cell_status"],
        rollback_result["source_cell_rack_id"],
        rollback_result["destination_cell_id"],
        rollback_result["destination_cell_status"],
        rollback_result["destination_cell_rack_id"],
        rollback_result["rack_status_after"],
    )
    return rollback_result


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
        if button_row.action_mode == 'cancel_return':
            # Return is managed by cancelTask in RCS, never by createTask.
            return _movement_order_out_read_optimized(db, order)
        logger.info("Task created | task_id=%s | order_code=%s | source=operator_window | window_id=%s | button_index=%s | rack_id=%s | agv=%s | status=%s", order.id, order.order_code, window_id, button_index, order.rack_id, order.agv_code or "-", order.status)
        rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none()
        if rack:
            _audit_rack_reservation_change(db, rack=rack, previous_status="available", new_status=rack.status, source="operator_window", related_order_id=order.id, reason=f"operator_window:{button_row.action_mode}", actor=f"operator_window:{row.name}", auto_commit=False)
            db.commit()
        dispatch_result = _dispatch_movement_order(db, order)
        if dispatch_result.dispatch_status == "uncertain":
            raise HTTPException(status_code=409, detail=f"Orden {order.id}: creación incierta; recursos retenidos. Revisar en RCS antes de reenviar.")
        if dispatch_result.dispatch_status != "success":
            message = dispatch_result.rcs_message or 'El RCS rechazó la creación de la tarea'
            _rollback_unsuccessful_order_dispatch(db, order, message)
            raise HTTPException(status_code=400, detail=f'El RCS no aceptó la tarea: {message}')
        return _movement_order_out_read_optimized(db, order)
