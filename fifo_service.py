from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select

from logging_config import get_logger
from models import Area, Location, MaterialGroup, MovementOrder, Rack, apply_rack_reservation_status

ACTIVE_RACK_STATUSES = {"available", "disponible"}
INACTIVE_RACK_STATUSES = {"reserved", "moving", "error", "blocked", "reservado", "moviendo", "bloqueado"}
VALID_PRIORITIES = {"normal", "alta", "urgente"}
ORDER_STATUSES_WITH_DESTINATION_RESERVATION = {"pending_dispatch", "dispatched", "in_progress"}
RACK_RESERVATION_ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo")
NO_MATERIAL_CODE = "SIN_MATERIAL"
NO_MATERIAL_CODE_NORMALIZED = "SINMATERIAL"
logger = get_logger("app.fifo")


def _normalize_material_code(value: Optional[str]) -> str:
    text = str(value or "").strip().upper()
    return "".join(ch for ch in text if ch.isalnum())


def _is_no_material_code(value: Optional[str]) -> bool:
    return _normalize_material_code(value) == NO_MATERIAL_CODE_NORMALIZED


def _no_material_group_ids(db) -> set[int]:
    rows = db.execute(select(MaterialGroup.id, MaterialGroup.code)).all()
    ids: set[int] = set()
    for row in rows:
        material_id, code = row
        if material_id and _is_no_material_code(code):
            ids.add(int(material_id))
    return ids


@dataclass
class FifoSelection:
    rack: Rack
    source_cell: Location
    destination_cell: Location
    source_area: Area
    destination_area: Area
    material_group: MaterialGroup


def _normalize_priority(value: Optional[str]) -> str:
    v = (value or "normal").strip().lower()
    if v not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail="Prioridad inválida")
    return v


def _normalize_rack_status(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _resolve_material_group_for_fifo(db, material_group_id: Optional[int]) -> MaterialGroup:
    parsed_id = None
    try:
        parsed_id = int(material_group_id) if material_group_id not in (None, "") else None
    except Exception:
        parsed_id = None
    if not parsed_id or parsed_id <= 0:
        return _ensure_default_material_group(db, code=NO_MATERIAL_CODE, name="Sin material")
    material_group = db.execute(select(MaterialGroup).where(MaterialGroup.id == parsed_id)).scalar_one_or_none()
    if not material_group or int(material_group.is_active or 0) != 1:
        raise HTTPException(status_code=400, detail="Material no encontrado o inactivo")
    return material_group


def _validate_request(db, source_area_id: int, destination_area_id: int, material_group_id: Optional[int]):
    if source_area_id == destination_area_id:
        raise HTTPException(status_code=400, detail="El área origen y destino no pueden ser iguales")

    source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none()
    if not source_area or int(source_area.is_active or 0) != 1:
        raise HTTPException(status_code=400, detail="Área origen no encontrada o inactiva")

    destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none()
    if not destination_area or int(destination_area.is_active or 0) != 1:
        raise HTTPException(status_code=400, detail="Área destino no encontrada o inactiva")

    material_group = _resolve_material_group_for_fifo(db, material_group_id)

    return source_area, destination_area, material_group


def _find_fifo_candidate(db, source_area: Area, material_group: MaterialGroup) -> tuple[Rack, Location]:
    material_code = getattr(material_group, "code", None)
    if _is_no_material_code(material_code):
        no_material_ids = _no_material_group_ids(db)
        if material_group.id:
            no_material_ids.add(int(material_group.id))
        if no_material_ids:
            material_filter = or_(Rack.material_group_id.in_(tuple(sorted(no_material_ids))), Rack.material_group_id.is_(None))
        else:
            material_filter = Rack.material_group_id.is_(None)
    else:
        material_filter = Rack.material_group_id == material_group.id

    rows = (
        db.execute(
            select(Rack, Location)
            .join(Location, Location.rack_id == Rack.id)
            .where(Location.area_id == source_area.id)
            .where(Location.rack_id.is_not(None))
            .where(Location.enabled == 1)
            .where(material_filter)
            .order_by(Rack.fifo_entered_at.asc().nullslast(), Rack.code.asc(), Location.y.asc(), Location.x.asc())
        )
        .all()
    )

    for rack, cell in rows:
        status = _normalize_rack_status(rack.status)
        if status in INACTIVE_RACK_STATUSES:
            continue
        if status and status not in ACTIVE_RACK_STATUSES:
            continue
        if cell.area_id != source_area.id:
            continue
        return rack, cell

    raise HTTPException(status_code=400, detail="No hay material disponible para ese grupo en el área origen")


def _reserved_destination_cell_ids(db) -> set[int]:
    rows = (
        db.execute(
            select(MovementOrder.destination_cell_id)
            .where(MovementOrder.status.in_(tuple(ORDER_STATUSES_WITH_DESTINATION_RESERVATION)))
        )
        .all()
    )
    return {row[0] for row in rows if row and row[0] is not None}


def _find_destination_cell(db, destination_area: Area) -> Location:
    reserved_cell_ids = _reserved_destination_cell_ids(db)
    rows = (
        db.execute(
            select(Location)
            .where(Location.area_id == destination_area.id)
            .where(Location.enabled == 1)
            .where(Location.rack_id.is_(None))
            .order_by(Location.y.asc(), Location.x.asc())
        )
        .scalars()
        .all()
    )
    for cell in rows:
        if cell.id in reserved_cell_ids:
            continue
        return cell
    raise HTTPException(status_code=400, detail="No hay espacio disponible en el área destino")


def resolve_fifo_request(db, source_area_id: int, destination_area_id: int, material_group_id: Optional[int], priority: Optional[str] = None) -> FifoSelection:
    _normalize_priority(priority)
    source_area, destination_area, material_group = _validate_request(db, source_area_id, destination_area_id, material_group_id)
    rack, source_cell = _find_fifo_candidate(db, source_area, material_group)
    destination_cell = _find_destination_cell(db, destination_area)
    return FifoSelection(
        rack=rack,
        source_cell=source_cell,
        destination_cell=destination_cell,
        source_area=source_area,
        destination_area=destination_area,
        material_group=material_group,
    )


def build_fifo_preview_payload(selection: FifoSelection, message: str = "Selección FIFO válida") -> dict:
    return {
        "validation_ok": True,
        "message": message,
        "rack": {
            "id": selection.rack.id,
            "code": selection.rack.code,
            "name": selection.rack.name,
            "status": selection.rack.status,
            "fifo_entered_at": selection.rack.fifo_entered_at,
            "quantity": selection.rack.quantity,
        },
        "material": {
            "id": selection.material_group.id,
            "code": selection.material_group.code,
            "name": selection.material_group.name,
        },
        "source_area": {
            "id": selection.source_area.id,
            "code": selection.source_area.code,
            "name": selection.source_area.name,
        },
        "destination_area": {
            "id": selection.destination_area.id,
            "code": selection.destination_area.code,
            "name": selection.destination_area.name,
        },
        "source_cell": {
            "id": selection.source_cell.id,
            "x": selection.source_cell.x,
            "y": selection.source_cell.y,
            "code": selection.source_cell.code,
        },
        "destination_cell": {
            "id": selection.destination_cell.id,
            "x": selection.destination_cell.x,
            "y": selection.destination_cell.y,
            "code": selection.destination_cell.code,
        },
    }




def _ensure_default_area(db, code: str = "SIN_AREA", name: str = "Sin área") -> Area:
    area = db.execute(select(Area).where(Area.code == code)).scalar_one_or_none()
    if area:
        return area
    now = datetime.utcnow()
    area = Area(code=code, name=name, description="Área automática para movimientos directos sin área configurada", color="#6b7280", area_type="sistema", is_active=1, priority=9999, updated_at=now)
    db.add(area)
    db.commit()
    db.refresh(area)
    return area


def _ensure_default_material_group(db, code: str = "SIN_MATERIAL", name: str = "Sin material") -> MaterialGroup:
    material = db.execute(select(MaterialGroup).where(MaterialGroup.code == code)).scalar_one_or_none()
    if material:
        changed = False
        if int(getattr(material, "is_active", 0) or 0) != 1:
            material.is_active = 1
            changed = True
        if not (getattr(material, "name", None) or "").strip():
            material.name = name
            changed = True
        if changed:
            material.updated_at = datetime.utcnow()
            db.add(material)
            db.commit()
            db.refresh(material)
        return material
    now = datetime.utcnow()
    material = MaterialGroup(code=code, name=name, description="Material automático para racks sin material configurado", is_active=1, updated_at=now)
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


def execute_direct_move_request(
    db,
    source_cell_id: int,
    destination_cell_id: int,
    priority: Optional[str] = None,
    comment: Optional[str] = None,
    created_by: Optional[str] = None,
    agv_code: Optional[str] = None,
    task_typ: Optional[str] = None,
    created_window_id: Optional[int] = None,
) -> MovementOrder:
    normalized_priority = _normalize_priority(priority)
    if source_cell_id == destination_cell_id:
        raise HTTPException(status_code=400, detail="La celda origen y destino no pueden ser iguales")

    source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
    destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none()
    if not source_cell or not destination_cell:
        raise HTTPException(status_code=404, detail="Celda origen o destino no encontrada")
    if int(source_cell.enabled or 0) != 1:
        raise HTTPException(status_code=400, detail="La celda origen no está habilitada")
    if int(destination_cell.enabled or 0) != 1:
        raise HTTPException(status_code=400, detail="La celda destino no está habilitada")
    if source_cell.rack_id is None:
        raise HTTPException(status_code=400, detail="La celda origen no tiene rack para mover")
    if destination_cell.rack_id is not None:
        raise HTTPException(status_code=400, detail="La celda destino ya está ocupada")

    rack = db.execute(select(Rack).where(Rack.id == source_cell.rack_id)).scalar_one_or_none()
    if not rack:
        raise HTTPException(status_code=404, detail="El rack origen no existe")
    status = _normalize_rack_status(rack.status)
    if status in INACTIVE_RACK_STATUSES:
        raise HTTPException(status_code=409, detail="El rack no está disponible para movimiento directo")

    source_area = db.execute(select(Area).where(Area.id == source_cell.area_id)).scalar_one_or_none() if source_cell.area_id else None
    destination_area = db.execute(select(Area).where(Area.id == destination_cell.area_id)).scalar_one_or_none() if destination_cell.area_id else None
    material_group = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack.material_group_id else None

    source_area = source_area or _ensure_default_area(db, code="SIN_AREA_ORIGEN", name="Sin área origen")
    destination_area = destination_area or _ensure_default_area(db, code="SIN_AREA_DESTINO", name="Sin área destino")
    material_group = material_group or _ensure_default_material_group(db)

    reserved_cell_ids = _reserved_destination_cell_ids(db)
    if destination_cell.id in reserved_cell_ids:
        raise HTTPException(status_code=409, detail="La celda destino ya está reservada por otra orden")

    now = datetime.utcnow()

    order = MovementOrder(
        order_code=f"DM-{now.strftime('%Y%m%d%H%M%S')}-{rack.id}",
        order_type="direct_move",
        source_area_id=source_area.id,
        destination_area_id=destination_area.id,
        material_group_id=material_group.id,
        rack_id=rack.id,
        source_cell_id=source_cell.id,
        destination_cell_id=destination_cell.id,
        priority=normalized_priority,
        agv_code=(agv_code or "").strip() or None,
        task_typ=(task_typ or "").strip() or None,
        comment=(comment or "").strip() or None,
        status="pending_dispatch",
        created_by=(created_by or "operador").strip() or None,
        created_window_id=created_window_id,
        created_at=now,
        updated_at=now,
    )
    db.add(rack)
    db.add(order)
    db.flush()
    apply_rack_reservation_status(rack, True, updated_at=now, order_id=order.id, dispatch_status=order.status, source="fifo_service", reason="direct_move_created")
    db.add(rack)
    db.commit()
    db.refresh(order)
    logger.info("Direct move order created order_id=%s order_code=%s rack_id=%s source_cell_id=%s destination_cell_id=%s", order.id, order.order_code, rack.id, source_cell.id, destination_cell.id)
    return order

def execute_fifo_request(
    db,
    source_area_id: int,
    destination_area_id: int,
    material_group_id: Optional[int],
    priority: Optional[str] = None,
    comment: Optional[str] = None,
    created_by: Optional[str] = None,
    agv_code: Optional[str] = None,
    task_typ: Optional[str] = None,
    created_window_id: Optional[int] = None,
) -> tuple[MovementOrder, FifoSelection]:
    normalized_priority = _normalize_priority(priority)
    selection = resolve_fifo_request(db, source_area_id, destination_area_id, material_group_id, normalized_priority)

    # Revalidar estados justo antes de reservar.
    rack = db.execute(select(Rack).where(Rack.id == selection.rack.id)).scalar_one()
    source_cell = db.execute(select(Location).where(Location.id == selection.source_cell.id)).scalar_one()
    destination_cell = db.execute(select(Location).where(Location.id == selection.destination_cell.id)).scalar_one()

    if _normalize_rack_status(rack.status) not in ACTIVE_RACK_STATUSES:
        raise HTTPException(status_code=409, detail="El rack FIFO seleccionado ya no está disponible")
    if source_cell.rack_id != rack.id or int(source_cell.enabled or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda origen ya no es válida")
    if destination_cell.rack_id is not None or int(destination_cell.enabled or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda destino ya no está disponible")

    now = datetime.utcnow()

    order_code = f"MO-{now.strftime('%Y%m%d%H%M%S')}-{rack.id}"
    order = MovementOrder(
        order_code=order_code,
        order_type="material_request",
        source_area_id=source_area_id,
        destination_area_id=destination_area_id,
        material_group_id=selection.material_group.id,
        rack_id=rack.id,
        source_cell_id=source_cell.id,
        destination_cell_id=destination_cell.id,
        priority=normalized_priority,
        agv_code=(agv_code or "").strip() or None,
        task_typ=(task_typ or "").strip() or None,
        comment=(comment or "").strip() or None,
        status="pending_dispatch",
        created_by=(created_by or "operador").strip() or None,
        created_window_id=created_window_id,
        created_at=now,
        updated_at=now,
    )
    db.add(rack)
    db.add(source_cell)
    db.add(destination_cell)
    db.add(order)
    db.flush()
    apply_rack_reservation_status(rack, True, updated_at=now, order_id=order.id, dispatch_status=order.status, source="fifo_service", reason="fifo_order_created")
    db.add(rack)
    db.commit()
    db.refresh(order)
    logger.info("FIFO order created order_id=%s order_code=%s rack_id=%s source_cell_id=%s destination_cell_id=%s", order.id, order.order_code, rack.id, source_cell.id, destination_cell.id)

    selection.rack = rack
    selection.source_cell = source_cell
    selection.destination_cell = destination_cell
    return order, selection



def simulate_order_completed(db, order_id: int) -> MovementOrder:
    order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    if order.status == "completed":
        raise HTTPException(status_code=409, detail="La orden ya fue finalizada")
    if order.status not in ORDER_STATUSES_WITH_DESTINATION_RESERVATION:
        raise HTTPException(status_code=409, detail=f"La orden no puede finalizarse en estado {order.status}")

    rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one()
    source_cell = db.execute(select(Location).where(Location.id == order.source_cell_id)).scalar_one()
    destination_cell = db.execute(select(Location).where(Location.id == order.destination_cell_id)).scalar_one()

    if source_cell.rack_id != rack.id:
        raise HTTPException(status_code=409, detail="La celda origen ya no contiene el rack de la orden")
    if destination_cell.rack_id is not None:
        raise HTTPException(status_code=409, detail="La celda destino ya no está libre")

    now = datetime.utcnow()
    # Importante: liberar primero la celda origen y hacer flush antes de asignar el rack
    # al destino. En SQLite, si ambos UPDATE se mandan juntos, puede intentar escribir
    # primero el destino mientras el origen todavía conserva el mismo rack_id, lo que
    # dispara UNIQUE constraint failed: locations.rack_id.
    source_cell.rack_id = None
    source_cell.status = 0
    source_cell.updated_at = now
    db.add(source_cell)
    db.flush()

    destination_cell.rack_id = rack.id
    destination_cell.status = 1
    destination_cell.updated_at = now

    active_other = db.execute(
        select(MovementOrder).where(
            MovementOrder.rack_id == rack.id,
            MovementOrder.id != order.id,
            MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
        )
    ).scalars().first()
    logger.info(
        "RACK_RELEASE_ATTEMPT rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
        rack.id,
        rack.code,
        order.id,
        order.status,
        "fifo_service",
        "movement_order_completed",
    )
    if active_other:
        apply_rack_reservation_status(rack, True, updated_at=now, order_id=active_other.id, dispatch_status=active_other.status, source="fifo_service", reason="active_order_same_rack")
        logger.warning(
            "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            order.id,
            active_other.id,
            active_other.status,
            "fifo_service",
            "movement_order_completed",
        )
    else:
        apply_rack_reservation_status(rack, False, updated_at=now, order_id=order.id, dispatch_status=order.status, source="fifo_service", reason="movement_order_completed")
    rack.last_moved_at = now

    order.status = "completed"
    order.rcs_status = "completed"
    order.closed_by = "fifo_service"
    order.closed_at = now
    order.release_source = "fifo_service"
    order.updated_at = now

    db.add(source_cell)
    db.add(destination_cell)
    db.add(rack)
    db.add(order)
    db.commit()
    db.refresh(order)
    logger.info("Movement order completed order_id=%s order_code=%s rack_id=%s destination_cell_id=%s", order.id, order.order_code, rack.id, destination_cell.id)
    return order



def undo_movement_order(db, order_id: int) -> MovementOrder:
    order = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    if order.status in {"cancelled", "undone"}:
        raise HTTPException(status_code=409, detail="La orden ya fue revertida")

    rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one()
    source_cell = db.execute(select(Location).where(Location.id == order.source_cell_id)).scalar_one()
    destination_cell = db.execute(select(Location).where(Location.id == order.destination_cell_id)).scalar_one()
    now = datetime.utcnow()

    if source_cell.rack_id not in (None, rack.id):
        raise HTTPException(status_code=409, detail="La celda origen ya está ocupada por otro rack y no se puede restaurar la orden")

    rack_locations = db.execute(select(Location).where(Location.rack_id == rack.id)).scalars().all()
    for loc in rack_locations:
        if loc.id == source_cell.id:
            continue
        loc.rack_id = None
        loc.status = 0
        loc.updated_at = now
        db.add(loc)
    db.flush()

    if destination_cell.id != source_cell.id:
        destination_cell.rack_id = None if destination_cell.rack_id == rack.id else destination_cell.rack_id
        destination_cell.status = 0 if destination_cell.rack_id is None else destination_cell.status
        destination_cell.updated_at = now
        db.add(destination_cell)

    source_cell.rack_id = rack.id
    source_cell.status = 1
    source_cell.updated_at = now

    order.status = "cancelled" if order.status in ORDER_STATUSES_WITH_DESTINATION_RESERVATION or order.status == "cancel_requested_undo" else "undone"
    order.rcs_status = order.status
    order.closed_by = "fifo_service"
    order.closed_at = now
    order.release_source = "fifo_service"
    order.updated_at = now

    active_other = db.execute(
        select(MovementOrder).where(
            MovementOrder.rack_id == rack.id,
            MovementOrder.id != order.id,
            MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
        )
    ).scalars().first()
    logger.info(
        "RACK_RELEASE_ATTEMPT rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
        rack.id,
        rack.code,
        order.id,
        order.status,
        "fifo_service",
        "movement_order_undone",
    )
    if active_other:
        apply_rack_reservation_status(rack, True, updated_at=now, order_id=active_other.id, dispatch_status=active_other.status, source="fifo_service", reason="active_order_same_rack")
        logger.warning(
            "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            order.id,
            active_other.id,
            active_other.status,
            "fifo_service",
            "movement_order_undone",
        )
    else:
        apply_rack_reservation_status(rack, False, updated_at=now, order_id=order.id, dispatch_status=order.status, source="fifo_service", reason="movement_order_undone")
    rack.last_moved_at = now

    db.add(source_cell)
    db.add(rack)
    db.add(order)
    db.commit()
    db.refresh(order)
    logger.info("Movement order undone order_id=%s order_code=%s rack_id=%s source_cell_id=%s", order.id, order.order_code, rack.id, source_cell.id)
    return order
