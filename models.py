from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from logging_config import configure_logging, configure_sqlalchemy_logging, get_logger

DB_URL = "sqlite:///./agv.db"
DB_GRID_W = 100
DB_GRID_H = 100
UPLOAD_DIR = "static/uploads"
BG_BASENAME = "matrix_bg"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
configure_logging()
configure_sqlalchemy_logging(SessionLocal)
logger = get_logger("app.main")


def rack_status_from_reservation(reserved: bool) -> str:
    return "reserved" if reserved else "available"


def rack_status_is_reserved(value) -> bool:
    return str(value or "").strip().lower() in {"reserved", "reservado"}


def rack_status_is_available(value) -> bool:
    return str(value or "").strip().lower() in {"available", "disponible", "free", "libre"}


def apply_rack_reservation_status(
    rack,
    reserved: bool,
    *,
    updated_at=None,
    order_id=None,
    dispatch_status=None,
    source: str = "",
    reason: str = "",
):
    old_status = getattr(rack, "status", None)
    old_reserved = rack_status_is_reserved(old_status)
    if not reserved:
        logger.info(
            "RACK_RELEASE_ATTEMPT rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
            getattr(rack, "id", None),
            getattr(rack, "code", None),
            order_id,
            dispatch_status,
            source,
            reason,
        )
    rack.status = rack_status_from_reservation(reserved)
    rack.updated_at = updated_at or datetime.utcnow()
    logger.info(
        "RACK_RESERVATION_CHANGE rack_id=%s rack_code=%s from_reserved=%s to_reserved=%s from_status=%s to_status=%s order_id=%s dispatch_status=%s source=%s reason=%s",
        getattr(rack, "id", None),
        getattr(rack, "code", None),
        old_reserved,
        bool(reserved),
        old_status,
        getattr(rack, "status", None),
        order_id,
        dispatch_status,
        source,
        reason,
    )
    if not reserved:
        logger.info(
            "RACK_RELEASE_COMPLETED rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
            getattr(rack, "id", None),
            getattr(rack, "code", None),
            order_id,
            dispatch_status,
            source,
            reason,
        )
    return rack


class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    status = Column(Integer, nullable=False, default=0)  # 0 libre, 1 ocupado
    is_visible = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    code = Column(String(64), nullable=True)
    enabled = Column(Integer, nullable=False, default=1)
    note = Column(String(512), nullable=True)
    area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True, unique=True)
    free_enabled = Column(Integer, nullable=False, default=0)
    free_x = Column(Float, nullable=True)
    free_y = Column(Float, nullable=True)
    free_w = Column(Float, nullable=True)
    free_h = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("x", "y", name="uq_xy"),
        Index("ix_locations_area_id", "area_id"),
        Index("ix_locations_updated_at", "updated_at"),
    )


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(String(512), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Area(Base):
    __tablename__ = "areas"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    matter_area = Column(String(128), nullable=True)
    color = Column(String(32), nullable=False, default="#4f46e5")
    area_type = Column(String(64), nullable=False, default="almacen")
    is_active = Column(Integer, nullable=False, default=1)
    priority = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MaterialGroup(Base):
    __tablename__ = "material_groups"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    color = Column(String(32), nullable=False, default="#7c3aed")
    is_active = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Rack(Base):
    __tablename__ = "racks"
    __table_args__ = (
        Index("ix_racks_material_group_id", "material_group_id"),
        Index("ix_racks_updated_at", "updated_at"),
    )
    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="disponible")
    material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    lot = Column(String(128), nullable=True)
    manufacturer_code = Column(String(128), nullable=True)
    quantity = Column(Integer, nullable=False, default=0)
    comment = Column(String(512), nullable=True)
    fifo_entered_at = Column(DateTime, nullable=True)
    last_moved_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    rack_custom_fields_json = Column(Text, nullable=True)


class MovementOrder(Base):
    __tablename__ = "movement_orders"
    __table_args__ = (
        Index("ix_movement_orders_created_at_id", "created_at", "id"),
        Index("ix_movement_orders_rack_id", "rack_id"),
        Index("ix_movement_orders_source_cell_id", "source_cell_id"),
        Index("ix_movement_orders_destination_cell_id", "destination_cell_id"),
        Index("ix_movement_orders_source_area_id", "source_area_id"),
        Index("ix_movement_orders_destination_area_id", "destination_area_id"),
        Index("ix_movement_orders_material_group_id", "material_group_id"),
        Index("ix_movement_orders_remote_task_code", "remote_task_code"),
        Index("ix_movement_orders_status", "status"),
    )
    id = Column(Integer, primary_key=True)
    order_code = Column(String(64), nullable=False, unique=True)
    order_type = Column(String(32), nullable=False, default="material_request")
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=False)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=False)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    priority = Column(String(32), nullable=False, default="normal")
    agv_code = Column(String(128), nullable=True)
    task_typ = Column(String(64), nullable=True)
    comment = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="pending_dispatch")
    created_by = Column(String(128), nullable=True)
    route_mode = Column(String(32), nullable=False, default="simple_area")
    route_points_json = Column(Text, nullable=True)
    trmx_group_id = Column(String(64), nullable=True)
    trmx_step = Column(Integer, nullable=True)
    trmx_total_steps = Column(Integer, nullable=True)
    trmx_parent_order_id = Column(Integer, ForeignKey("movement_orders.id"), nullable=True)
    trmx_status = Column(String(32), nullable=True)
    trmx_next_config_json = Column(Text, nullable=True)
    trmx_select_policy = Column(String(32), nullable=True)
    fifo_chain_group_id = Column(String(64), nullable=True)
    fifo_chain_step = Column(Integer, nullable=True)
    fifo_chain_total_steps = Column(Integer, nullable=True)
    fifo_chain_parent_order_id = Column(Integer, ForeignKey("movement_orders.id"), nullable=True)
    fifo_chain_status = Column(String(32), nullable=True)
    fifo_chain_next_config_json = Column(Text, nullable=True)
    fifo_chain_select_policy = Column(String(32), nullable=True)
    dispatch_status = Column(String(32), nullable=False, default="not_sent")
    dispatch_request_json = Column(Text, nullable=True)
    dispatch_response_json = Column(Text, nullable=True)
    override_payload_json = Column(Text, nullable=True)
    remote_task_code = Column(String(128), nullable=True)
    req_code = Column(String(128), nullable=True)
    pickup_rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    dropoff_rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    rcs_status = Column(String(64), nullable=True)
    rcs_message = Column(String(512), nullable=True)
    rcs_response_json = Column(Text, nullable=True)
    dispatched_at = Column(DateTime, nullable=True)
    rcs_last_update = Column(DateTime, nullable=True)
    status_query_request_json = Column(Text, nullable=True)
    status_query_response_json = Column(Text, nullable=True)
    status_query_checked_at = Column(DateTime, nullable=True)
    status_query_log_json = Column(Text, nullable=True)
    forced_local_close = Column(Integer, nullable=False, default=0)
    forced_local_close_at = Column(DateTime, nullable=True)
    forced_local_close_reason = Column(String(128), nullable=True)
    cancel_source = Column(String(64), nullable=True)
    cancel_reason = Column(String(256), nullable=True)
    closed_by = Column(String(128), nullable=True)
    closed_at = Column(DateTime, nullable=True)
    release_source = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_window_id = Column(Integer, ForeignKey("operator_windows.id"), nullable=True)


class OperatorWindow(Base):
    __tablename__ = "operator_windows"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)
    bg_color = Column(String(32), nullable=False, default="#0f2747")
    button_count = Column(Integer, nullable=False, default=1)
    password_hash = Column(String(128), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class OperatorWindowButton(Base):
    __tablename__ = "operator_window_buttons"
    id = Column(Integer, primary_key=True)
    window_id = Column(Integer, ForeignKey("operator_windows.id"), nullable=False)
    button_index = Column(Integer, nullable=False)
    label = Column(String(128), nullable=False, default="Botón")
    color = Column(String(32), nullable=False, default="#1f4b99")
    is_active = Column(Integer, nullable=False, default=1)
    action_mode = Column(String(32), nullable=False, default="fifo")
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    priority = Column(String(32), nullable=False, default="normal")
    agv_code = Column(String(128), nullable=True)
    task_typ = Column(String(64), nullable=True)
    comment = Column(String(512), nullable=True)
    cancel_matter_area = Column(String(128), nullable=True)
    point_visible_material_ids_json = Column(Text, nullable=True)
    point_custom_fields_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("window_id", "button_index", name="uq_window_button_index"),
        Index("ix_operator_window_buttons_window_id", "window_id"),
    )


class DebugConsoleEvent(Base):
    __tablename__ = "debug_console_events"
    id = Column(Integer, primary_key=True)
    direction = Column(String(16), nullable=False, default="sent")
    module = Column(String(64), nullable=False, default="unknown")
    base_url = Column(String(512), nullable=True)
    endpoint = Column(String(256), nullable=True)
    payload_json = Column(Text, nullable=True)
    message = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RackSyncEvent(Base):
    __tablename__ = "rack_sync_events"
    __table_args__ = (
        Index("ix_rack_sync_events_created_at_id", "created_at", "id"),
        Index("ix_rack_sync_events_action", "action"),
    )
    id = Column(Integer, primary_key=True)
    action = Column(String(32), nullable=False)
    ok = Column(Integer, nullable=False, default=0)
    blocked = Column(Integer, nullable=False, default=0)
    total_assigned_racks = Column(Integer, nullable=False, default=0)
    match_count = Column(Integer, nullable=False, default=0)
    mismatch_count = Column(Integer, nullable=False, default=0)
    missing_count = Column(Integer, nullable=False, default=0)
    invalid_count = Column(Integer, nullable=False, default=0)
    attempted_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    active_tasks_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    message = Column(String(512), nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ScannerStation(Base):
    __tablename__ = "scanner_stations"
    __table_args__ = (
        Index("ix_scanner_stations_scanner_code", "scanner_code"),
        Index("ix_scanner_stations_is_active", "is_active"),
    )
    id = Column(Integer, primary_key=True)
    scanner_code = Column(String(128), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    station_type = Column(String(64), nullable=False, default="generic")
    default_action = Column(String(64), nullable=False, default="preview_only")
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    route_mode = Column(String(32), nullable=False, default="simple_area")
    fifo_material_policy = Column(String(32), nullable=False, default="specific_material")
    fifo_chain_total_steps = Column(Integer, nullable=False, default=2)
    fifo_chain_step1_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step1_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step1_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step1_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step1_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step2_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step2_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step2_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step2_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step2_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step3_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step3_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step3_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step3_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step3_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step3_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step3_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step3_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step3_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step4_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step4_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step4_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step4_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step4_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step4_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step4_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step4_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step4_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step5_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step5_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step5_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step5_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step5_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step5_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step5_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step5_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step5_destination_area_ids_json = Column(Text, nullable=True)
    second_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    second_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    second_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    second_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    storage_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    empty_rack_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    cancel_return_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    default_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    agv_code = Column(String(128), nullable=True)
    task_typ = Column(String(64), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    require_preview = Column(Integer, nullable=False, default=0)
    allow_execute = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class QrActionRule(Base):
    __tablename__ = "qr_action_rules"
    __table_args__ = (
        Index("ix_qr_action_rules_qr_value", "qr_value"),
        Index("ix_qr_action_rules_is_active", "is_active"),
        Index("ix_qr_action_rules_match_type", "match_type"),
    )
    id = Column(Integer, primary_key=True)
    qr_value = Column(String(256), nullable=False)
    qr_alias = Column(String(128), nullable=True)
    description = Column(String(512), nullable=True)
    qr_type = Column(String(64), nullable=False, default="generic")
    match_type = Column(String(32), nullable=False, default="exact")
    action_type = Column(String(64), nullable=False, default="use_scanner_default")
    material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    route_mode = Column(String(32), nullable=False, default="simple_area")
    fifo_material_policy = Column(String(32), nullable=False, default="specific_material")
    fifo_chain_total_steps = Column(Integer, nullable=False, default=2)
    fifo_chain_step1_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step1_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step1_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step1_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step1_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step2_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step2_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step2_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step2_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step2_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step3_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step3_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step3_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step3_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step3_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step3_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step3_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step3_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step3_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step4_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step4_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step4_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step4_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step4_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step4_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step4_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step4_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step4_destination_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step5_source_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step5_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    fifo_chain_step5_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step5_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step5_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    fifo_chain_step5_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    fifo_chain_step5_source_area_ids_json = Column(Text, nullable=True)
    fifo_chain_step5_destination_mode = Column(String(32), nullable=False, default="configured_area")
    fifo_chain_step5_destination_area_ids_json = Column(Text, nullable=True)
    second_source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    second_destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    second_source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    second_destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    priority = Column(Integer, nullable=True)
    task_typ = Column(String(64), nullable=True)
    agv_code = Column(String(128), nullable=True)
    requires_scanner_station = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class QrTransitionRule(Base):
    __tablename__ = "qr_transition_rules"
    __table_args__ = (
        Index("ix_qr_transition_rules_is_active", "is_active"),
        Index("ix_qr_transition_rules_apply_on", "apply_on"),
        Index("ix_qr_transition_rules_qr_action_rule_id", "qr_action_rule_id"),
        Index("ix_qr_transition_rules_scanner_station_id", "scanner_station_id"),
        Index("ix_qr_transition_rules_route", "source_area_id", "destination_area_id"),
        Index("ix_qr_transition_rules_cells", "source_cell_id", "destination_cell_id"),
        Index("ix_qr_transition_rules_scope", "scope"),
        Index("ix_qr_transition_rules_match_mode", "match_mode"),
        Index("ix_qr_transition_rules_source_match_mode", "source_match_mode"),
        Index("ix_qr_transition_rules_priority", "priority"),
    )
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    qr_action_rule_id = Column(Integer, ForeignKey("qr_action_rules.id"), nullable=True)
    scanner_station_id = Column(Integer, ForeignKey("scanner_stations.id"), nullable=True)
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    current_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    current_rack_status = Column(String(32), nullable=True)
    next_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    next_rack_status = Column(String(32), nullable=True)
    next_quantity = Column(Integer, nullable=True)
    clear_quantity = Column(Integer, nullable=False, default=0)
    next_comment = Column(String(512), nullable=True)
    append_comment = Column(Integer, nullable=False, default=1)
    apply_on = Column(String(64), nullable=False, default="movement_completed")
    scope = Column(String(64), nullable=False, default="qr_pda")
    match_mode = Column(String(64), nullable=False, default="advanced")
    source_match_mode = Column(String(64), nullable=False, default="configured_source")
    ignore_current_material = Column(Integer, nullable=False, default=0)
    priority = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    applied_count = Column(Integer, nullable=False, default=0)
    last_applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class QrTransitionLog(Base):
    __tablename__ = "qr_transition_logs"
    __table_args__ = (
        Index("ix_qr_transition_logs_movement_order_id", "movement_order_id"),
        Index("ix_qr_transition_logs_transition_rule_id", "transition_rule_id"),
        Index("ix_qr_transition_logs_rack_id", "rack_id"),
        Index("ix_qr_transition_logs_status", "status"),
        Index("ix_qr_transition_logs_created_at", "created_at"),
    )
    id = Column(Integer, primary_key=True)
    transition_rule_id = Column(Integer, ForeignKey("qr_transition_rules.id"), nullable=True)
    movement_order_id = Column(Integer, ForeignKey("movement_orders.id"), nullable=True)
    scan_event_id = Column(Integer, ForeignKey("scan_events.id"), nullable=True)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    previous_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    next_material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    previous_rack_status = Column(String(32), nullable=True)
    next_rack_status = Column(String(32), nullable=True)
    previous_quantity = Column(Integer, nullable=True)
    next_quantity = Column(Integer, nullable=True)
    previous_comment = Column(String(512), nullable=True)
    next_comment = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="skipped")
    message = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ScanTerminal(Base):
    __tablename__ = "scan_terminals"
    __table_args__ = (
        Index("ix_scan_terminals_terminal_code", "terminal_code"),
        Index("ix_scan_terminals_scanner_station_id", "scanner_station_id"),
        Index("ix_scan_terminals_is_active", "is_active"),
    )
    id = Column(Integer, primary_key=True)
    terminal_code = Column(String(128), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    scanner_station_id = Column(Integer, ForeignKey("scanner_stations.id"), nullable=False)
    api_key = Column(String(256), nullable=True)
    mode = Column(String(32), nullable=False, default="preview")
    allow_execute = Column(Integer, nullable=False, default=0)
    require_preview = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)
    last_seen_at = Column(DateTime, nullable=True)
    last_ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ScanEvent(Base):
    __tablename__ = "scan_events"
    __table_args__ = (
        Index("ix_scan_events_created_at", "created_at"),
        Index("ix_scan_events_scanner_code", "scanner_code"),
        Index("ix_scan_events_qr_value", "qr_value"),
        Index("ix_scan_events_status", "status"),
        Index("ix_scan_events_movement_order_id", "movement_order_id"),
    )
    id = Column(Integer, primary_key=True)
    scanner_code = Column(String(128), nullable=True)
    scanner_station_id = Column(Integer, ForeignKey("scanner_stations.id"), nullable=True)
    terminal_id = Column(Integer, ForeignKey("scan_terminals.id"), nullable=True)
    qr_value = Column(String(256), nullable=True)
    qr_action_rule_id = Column(Integer, ForeignKey("qr_action_rules.id"), nullable=True)
    parsed_type = Column(String(64), nullable=True)
    resolved_action = Column(String(64), nullable=True)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    material_group_id = Column(Integer, ForeignKey("material_groups.id"), nullable=True)
    source_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    destination_cell_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    source_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    destination_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    movement_order_id = Column(Integer, ForeignKey("movement_orders.id"), nullable=True)
    mode = Column(String(32), nullable=False, default="preview")
    status = Column(String(32), nullable=False, default="preview_ok")
    error_message = Column(String(512), nullable=True)
    request_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
