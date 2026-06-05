from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine
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
