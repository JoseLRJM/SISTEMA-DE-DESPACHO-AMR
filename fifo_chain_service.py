from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select

from fifo_service import (
    ACTIVE_RACK_STATUSES,
    INACTIVE_RACK_STATUSES,
    RACK_RESERVATION_ACTIVE_STATUSES,
    _find_any_available_fifo_candidate,
    _find_fifo_candidate,
    _normalize_priority,
    _normalize_rack_status,
    _resolve_material_group_for_fifo,
)
from logging_config import get_logger
from models import Area, Location, MaterialGroup, MovementOrder, Rack, apply_rack_reservation_status

logger = get_logger("app.fifo_chain")
_USE_DEFAULT_DISPATCH = object()
_default_dispatch_func: Optional[Callable] = None


def set_fifo_chain_default_dispatch_func(dispatch_func: Optional[Callable]) -> None:
    global _default_dispatch_func
    _default_dispatch_func = dispatch_func


def _as_int(value, field_name: str) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = 0
    if parsed <= 0:
        raise ValueError(f"{field_name} requerido")
    return parsed


def _as_optional_int(value) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _area_ids(value, field_name: str) -> list[int]:
    if value in (None, "", []):
        return []
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception as exc:
            raise ValueError(f"{field_name} no es JSON valido") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} debe ser una lista")
    result = []
    for item in raw:
        area_id = _as_optional_int(item)
        if area_id is None:
            raise ValueError(f"{field_name} contiene un ID invalido")
        if area_id not in result:
            result.append(area_id)
    return result


def _load_area(db, area_id: int, label: str) -> Area:
    area = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
    if not area or int(getattr(area, "is_active", 0) or 0) != 1:
        raise ValueError(f"{label} no encontrada o inactiva")
    return area


def _load_cell(db, cell_id: int, area: Area, label: str, *, destination: bool = False) -> Location:
    cell = db.execute(select(Location).where(Location.id == cell_id)).scalar_one_or_none()
    if not cell:
        raise ValueError(f"{label} no encontrada")
    if int(getattr(cell, "enabled", 0) or 0) != 1:
        raise ValueError(f"{label} no esta habilitada")
    if getattr(cell, "area_id", None) != area.id:
        raise ValueError(f"{label} no pertenece al area configurada")
    if destination and getattr(cell, "rack_id", None) is not None:
        raise ValueError(f"{label} ya esta ocupada")
    if not str(getattr(cell, "code", "") or "").strip():
        raise ValueError(f"{label} no tiene positionCode configurado")
    return cell


def _reserved_destination_cell_ids(db) -> set[int]:
    rows = (
        db.execute(
            select(MovementOrder.destination_cell_id).where(
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES)
            )
        )
        .all()
    )
    return {row[0] for row in rows if row and row[0] is not None}


def _select_destination_cell_for_next_step(
    db,
    *,
    destination_area: Area,
    destination_cell_id: Optional[int],
    next_step: int,
) -> Location:
    reserved_ids = _reserved_destination_cell_ids(db)
    if destination_cell_id:
        cell = _load_cell(
            db,
            destination_cell_id,
            destination_area,
            f"Celda destino paso {next_step}",
            destination=True,
        )
        if cell.id in reserved_ids:
            raise ValueError(f"La celda destino paso {next_step} ya esta reservada")
        return cell

    cells = db.execute(
        select(Location)
        .where(Location.area_id == destination_area.id)
        .where(Location.enabled == 1)
        .where(Location.is_visible == 1)
        .where(Location.rack_id.is_(None))
        .where(Location.code.is_not(None))
        .order_by(Location.y.asc(), Location.x.asc(), Location.id.asc())
    ).scalars().all()
    for cell in cells:
        if cell.id not in reserved_ids and str(cell.code or "").strip():
            return cell
    area_label = destination_area.name or destination_area.code or destination_area.id
    raise ValueError(f"Paso {next_step}: no hay espacio libre en el area destino {area_label}.")


def resolve_destination_cell_for_step(
    db,
    *,
    destination_mode: str,
    destination_area_id=None,
    destination_cell_id=None,
    destination_area_ids=None,
    next_step: int,
) -> tuple[Area, Location]:
    mode = str(destination_mode or "configured_area").strip() or "configured_area"
    if mode == "configured_area":
        area = _load_area(db, _as_int(destination_area_id, "destination_area_id"), f"Area destino paso {next_step}")
        return area, _select_destination_cell_for_next_step(
            db, destination_area=area, destination_cell_id=_as_optional_int(destination_cell_id), next_step=next_step
        )
    if mode not in {"any_area_with_space", "selected_areas_with_space"}:
        raise ValueError(f"destination_mode invalido para paso {next_step}: {mode}")
    selected_ids = _area_ids(destination_area_ids, "destination_area_ids_json") if mode == "selected_areas_with_space" else []
    if mode == "selected_areas_with_space" and not selected_ids:
        raise ValueError("No se configuraron areas validas para esta seleccion de destino")
    areas = db.execute(select(Area).where(Area.is_active == 1).order_by(Area.id.asc())).scalars().all()
    area_by_id = {area.id: area for area in areas}
    ordered_areas = [area_by_id[area_id] for area_id in selected_ids if area_id in area_by_id] if selected_ids else areas
    if mode == "selected_areas_with_space" and len(ordered_areas) != len(selected_ids):
        raise ValueError("La seleccion de destino contiene areas inexistentes o inactivas")
    for area in ordered_areas:
        try:
            cell = _select_destination_cell_for_next_step(
                db, destination_area=area, destination_cell_id=None, next_step=next_step
            )
            return area, cell
        except ValueError:
            continue
    if mode == "selected_areas_with_space":
        raise ValueError("No hay espacio disponible en las areas destino seleccionadas.")
    raise ValueError("No hay espacio disponible en ninguna area operativa.")


def _select_rack_for_next_step(
    db,
    *,
    source_area: Area,
    source_cell: Optional[Location],
    material_group_id,
    fifo_material_policy: str,
    next_step: int,
):
    policy = str(fifo_material_policy or "any_available_from_source").strip() or "any_available_from_source"
    if _as_optional_int(material_group_id) is not None:
        material = _resolve_material_group_for_fifo(db, material_group_id)
        if source_cell is not None:
            rack_id = getattr(source_cell, "rack_id", None)
            if not rack_id:
                raise ValueError(f"Paso {next_step}: no hay rack disponible en la celda origen configurada.")
            rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none()
            if not rack:
                raise ValueError(f"Paso {next_step}: no hay rack disponible en la celda origen configurada.")
            status = _normalize_rack_status(rack.status)
            if status in INACTIVE_RACK_STATUSES or (status and status not in ACTIVE_RACK_STATUSES):
                raise ValueError(f"Paso {next_step}: el rack de la celda origen configurada no esta disponible.")
            if getattr(rack, "material_group_id", None) != material.id:
                actual = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack.material_group_id else None
                required_label = material.code or material.name or material.id
                actual_label = getattr(actual, "code", None) or getattr(actual, "name", None) or "sin material"
                raise ValueError(
                    f"Paso {next_step} requiere material {required_label}, pero el rack disponible en el origen tiene material {actual_label}. "
                    f"Revise Material requerido tramo {next_step} o la transicion aplicada al paso anterior."
                )
            return rack, material, source_cell

        try:
            rack, candidate_cell = _find_fifo_candidate(db, source_area, material)
        except Exception as exc:
            required_label = material.code or material.name or material.id
            area_label = source_area.name or source_area.code or source_area.id
            raise ValueError(
                f"Paso {next_step} requiere material {required_label}, pero no hay un rack disponible con ese material en {area_label}. "
                f"Revise Material requerido tramo {next_step} o la transicion aplicada al paso anterior."
            ) from exc
        return rack, material, candidate_cell

    rack, candidate_cell = _find_any_available_fifo_candidate(
        db,
        source_area,
        source_cell_id=getattr(source_cell, "id", None),
    )
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack.material_group_id else None
    if not material:
        material = _resolve_material_group_for_fifo(db, None)
    return rack, material, candidate_cell


def resolve_fifo_request_by_material_any_area(db, material_group_id) -> tuple[Rack, Location, Area]:
    material = _resolve_material_group_for_fifo(db, material_group_id)
    active_rack_ids = {
        int(row[0])
        for row in db.execute(
            select(MovementOrder.rack_id).where(
                MovementOrder.rack_id.is_not(None),
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            )
        ).all()
        if row and row[0] is not None
    }
    rows = (
        db.execute(
            select(Rack, Location, Area)
            .join(Location, Location.rack_id == Rack.id)
            .join(Area, Area.id == Location.area_id)
            .where(Rack.material_group_id == material.id)
            .where(Location.rack_id.is_not(None))
            .where(Location.enabled == 1)
            .where(Location.is_visible == 1)
            .where(Area.is_active == 1)
            .order_by(Rack.fifo_entered_at.asc().nullslast(), Rack.code.asc(), Location.y.asc(), Location.x.asc())
        )
        .all()
    )
    for rack, cell, area in rows:
        if rack.id in active_rack_ids:
            continue
        status = _normalize_rack_status(rack.status)
        if status in INACTIVE_RACK_STATUSES:
            continue
        if status and status not in ACTIVE_RACK_STATUSES:
            continue
        if not str(getattr(cell, "code", "") or "").strip():
            continue
        return rack, cell, area
    raise ValueError(f"No hay rack disponible con material {material.code or material.id} en ninguna area operativa para paso 2.")


def resolve_fifo_request_by_material_selected_areas(
    db, material_group_id, source_area_ids, *, excluded_area_id=None
) -> tuple[Rack, Location, Area]:
    material = _resolve_material_group_for_fifo(db, material_group_id)
    area_ids = _area_ids(source_area_ids, "source_area_ids_json")
    if not area_ids:
        raise ValueError("No se configuraron areas validas para esta seleccion de origen")
    valid_areas = db.execute(select(Area).where(Area.id.in_(area_ids), Area.is_active == 1)).scalars().all()
    if {area.id for area in valid_areas} != set(area_ids):
        raise ValueError("La seleccion de origen contiene areas inexistentes o inactivas")
    active_rack_ids = {
        int(row[0]) for row in db.execute(
            select(MovementOrder.rack_id).where(
                MovementOrder.rack_id.is_not(None),
                MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
            )
        ).all() if row and row[0] is not None
    }
    rows = db.execute(
        select(Rack, Location, Area)
        .join(Location, Location.rack_id == Rack.id)
        .join(Area, Area.id == Location.area_id)
        .where(Rack.material_group_id == material.id)
        .where(Area.id.in_(area_ids))
        .where(Location.enabled == 1)
        .where(Location.is_visible == 1)
        .where(Location.code.is_not(None))
        .where(Area.is_active == 1)
        .order_by(Rack.fifo_entered_at.asc().nullslast(), Rack.code.asc(), Location.y.asc(), Location.x.asc())
    ).all()
    for rack, cell, area in rows:
        if excluded_area_id is not None and area.id == int(excluded_area_id):
            continue
        status = _normalize_rack_status(rack.status)
        if rack.id in active_rack_ids or status in INACTIVE_RACK_STATUSES or (status and status not in ACTIVE_RACK_STATUSES):
            continue
        if not str(cell.code or "").strip():
            continue
        return rack, cell, area
    raise ValueError(f"No se encontro material {material.code or material.id} disponible en las areas seleccionadas.")


def _select_rack_for_next_step_global_material(db, *, material_group_id, destination_area: Area, next_step: int) -> tuple[Rack, MaterialGroup, Area, Location]:
    material = _resolve_material_group_for_fifo(db, material_group_id)
    rack, cell, area = resolve_fifo_request_by_material_any_area(db, material.id)
    if area.id == destination_area.id:
        # Evitar origen=destino para el paso 2. Si el primer candidato cae en destino,
        # seguir buscando con el mismo filtro FIFO pero saltando esa area.
        active_rack_ids = {
            int(row[0])
            for row in db.execute(
                select(MovementOrder.rack_id).where(
                    MovementOrder.rack_id.is_not(None),
                    MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
                )
            ).all()
            if row and row[0] is not None
        }
        rows = (
            db.execute(
                select(Rack, Location, Area)
                .join(Location, Location.rack_id == Rack.id)
                .join(Area, Area.id == Location.area_id)
                .where(Rack.material_group_id == material.id)
                .where(Location.rack_id.is_not(None))
                .where(Location.enabled == 1)
                .where(Location.is_visible == 1)
                .where(Area.is_active == 1)
                .order_by(Rack.fifo_entered_at.asc().nullslast(), Rack.code.asc(), Location.y.asc(), Location.x.asc())
            )
            .all()
        )
        for candidate_rack, candidate_cell, candidate_area in rows:
            if candidate_area.id == destination_area.id or candidate_rack.id in active_rack_ids:
                continue
            status = _normalize_rack_status(candidate_rack.status)
            if status in INACTIVE_RACK_STATUSES or (status and status not in ACTIVE_RACK_STATUSES):
                continue
            if not str(getattr(candidate_cell, "code", "") or "").strip():
                continue
            return candidate_rack, material, candidate_area, candidate_cell
        raise ValueError(f"No hay rack disponible con material {material.code or material.id} fuera del area destino para paso {next_step}.")
    return rack, material, area, cell


def _route_point(sequence: int, role: str, area: Area, cell: Location) -> dict:
    position_code = str(cell.code or "").strip()
    return {
        "sequence": sequence,
        "role": role,
        "area_id": area.id,
        "area_code": area.code,
        "area_name": area.name,
        "cell_id": cell.id,
        "cell_code": position_code,
        "positionCode": position_code,
        "type": "00",
        "label": " - ".join(part for part in (position_code, area.code, area.name) if part),
    }


def _dispatch_next_order(db, order: MovementOrder, dispatch_func=_USE_DEFAULT_DISPATCH):
    resolved_dispatch_func = _default_dispatch_func if dispatch_func is _USE_DEFAULT_DISPATCH else dispatch_func
    if not resolved_dispatch_func:
        return None
    return resolved_dispatch_func(db, order)


def create_fifo_chain_next_step(db, completed_order: MovementOrder, config: dict, dispatch_func=_USE_DEFAULT_DISPATCH) -> MovementOrder:
    if (completed_order.route_mode or "") != "fifo_chain":
        raise ValueError("La orden completada no pertenece a fifo_chain")
    current_step = int(completed_order.fifo_chain_step or 0)
    total_steps = int(completed_order.fifo_chain_total_steps or 0)
    if current_step <= 0 or total_steps <= 0:
        raise ValueError("La orden completada no tiene paso/total de fifo_chain")
    next_step = current_step + 1
    if next_step > total_steps:
        raise ValueError("La cadena fifo_chain ya no tiene pasos pendientes")
    configured_step = int(config.get("step") or next_step)
    if configured_step != next_step:
        raise ValueError(f"Configuracion esperada para paso {next_step}, recibida para paso {configured_step}")
    source_mode = str(config.get("source_mode") or "configured_area").strip() or "configured_area"
    destination_mode = str(config.get("destination_mode") or "configured_area").strip() or "configured_area"
    fifo_material_policy = str(config.get("fifo_material_policy") or "any_available_from_source").strip() or "any_available_from_source"
    priority = _normalize_priority(str(config.get("priority") or "normal"))

    destination_area, destination_cell = resolve_destination_cell_for_step(
        db, destination_mode=destination_mode,
        destination_area_id=config.get("destination_area_id"),
        destination_cell_id=config.get("destination_cell_id"),
        destination_area_ids=config.get("destination_area_ids_json"),
        next_step=next_step,
    )

    if source_mode == "any_area_by_material":
        rack, material, source_area, source_cell = _select_rack_for_next_step_global_material(
            db,
            material_group_id=config.get("step2_material_group_id") or config.get("material_group_id"),
            destination_area=destination_area,
            next_step=next_step,
        )
    elif source_mode == "selected_areas_by_material":
        rack, source_cell, source_area = resolve_fifo_request_by_material_selected_areas(
            db, config.get("material_group_id"), config.get("source_area_ids_json"),
            excluded_area_id=destination_area.id,
        )
        material = _resolve_material_group_for_fifo(db, config.get("material_group_id"))
    else:
        source_area_id = _as_int(config.get("source_area_id"), "source_area_id")
        source_cell_id = _as_optional_int(config.get("source_cell_id"))
        source_area = _load_area(db, source_area_id, f"Area origen paso {next_step}")
        if source_area.id == destination_area.id:
            raise ValueError(f"El area origen y destino del paso {next_step} no pueden ser iguales")
        source_cell = (
            _load_cell(db, source_cell_id, source_area, f"Celda origen paso {next_step}")
            if source_cell_id else None
        )
        rack, material, source_cell = _select_rack_for_next_step(
            db,
            source_area=source_area,
            source_cell=source_cell,
            material_group_id=config.get("material_group_id"),
            fifo_material_policy=fifo_material_policy,
            next_step=next_step,
        )

    now = datetime.utcnow()
    order = MovementOrder(
        order_code=f"FIFOCHAIN-{now.strftime('%Y%m%d%H%M%S%f')}-{rack.id}",
        order_type="material_request",
        source_area_id=source_area.id,
        destination_area_id=destination_area.id,
        material_group_id=material.id,
        rack_id=rack.id,
        source_cell_id=source_cell.id,
        destination_cell_id=destination_cell.id,
        priority=priority,
        agv_code=(str(config.get("agv_code") or "").strip() or None),
        task_typ=(str(config.get("task_typ") or "").strip() or None),
        comment=f"Flujo FIFO paso {next_step} de orden {completed_order.id}",
        status="pending_dispatch",
        created_by=(str(config.get("terminal_code") or config.get("scanner_code") or "fifo_chain").strip() or "fifo_chain"),
        route_mode="fifo_chain",
        route_points_json=json.dumps([
            _route_point(1, f"source_{next_step}", source_area, source_cell),
            _route_point(2, f"destination_{next_step}", destination_area, destination_cell),
        ], ensure_ascii=False),
        fifo_chain_group_id=completed_order.fifo_chain_group_id,
        fifo_chain_step=next_step,
        fifo_chain_total_steps=total_steps,
        fifo_chain_parent_order_id=completed_order.id,
        fifo_chain_status="active",
        fifo_chain_next_config_json=(
            json.dumps({
                "current_step": next_step,
                "next_step": next_step + 1,
                "total_steps": total_steps,
                "steps": config.get("_remaining_steps") or [],
                "chain": config.get("_chain") or {},
            }, ensure_ascii=False, default=str)
            if config.get("_remaining_steps") else None
        ),
        fifo_chain_select_policy=str(config.get("fifo_chain_select_policy") or completed_order.fifo_chain_select_policy or "any_available").strip() or "any_available",
        created_at=now,
        updated_at=now,
    )
    db.add(rack)
    db.add(source_cell)
    db.add(destination_cell)
    db.add(order)
    db.flush()
    apply_rack_reservation_status(
        rack,
        True,
        updated_at=now,
        order_id=order.id,
        dispatch_status=order.status,
        source="fifo_chain_service",
        reason="fifo_chain_next_step_created",
    )
    db.add(rack)
    db.commit()
    db.refresh(order)

    logger.info(
        "FIFO_CHAIN_NEXT_STEP_CREATED fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s step=%s source_mode=%s source_cell_id=%s destination_cell_id=%s rack_id=%s fifo_material_policy=%s",
        completed_order.fifo_chain_group_id,
        completed_order.id,
        order.id,
        order.fifo_chain_step,
        source_mode,
        source_cell.id,
        destination_cell.id,
        rack.id,
        fifo_material_policy,
    )

    try:
        dispatch_result = _dispatch_next_order(db, order, dispatch_func)
        if dispatch_result:
            db.refresh(order)
            logger.info(
                "FIFO_CHAIN_NEXT_STEP_DISPATCHED fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s dispatch_status=%s rcs_message=%s",
                completed_order.fifo_chain_group_id,
                completed_order.id,
                order.id,
                getattr(dispatch_result, "dispatch_status", None),
                getattr(dispatch_result, "rcs_message", None),
            )
            if getattr(dispatch_result, "dispatch_status", None) != "success":
                logger.error(
                    "FIFO_CHAIN_NEXT_STEP_DISPATCH_FAILED fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s dispatch_status=%s rcs_message=%s",
                    completed_order.fifo_chain_group_id,
                    completed_order.id,
                    order.id,
                    getattr(dispatch_result, "dispatch_status", None),
                    getattr(dispatch_result, "rcs_message", None),
                )
    except Exception as exc:
        db.rollback()
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one()
        order.dispatch_status = "error"
        order.rcs_status = "error"
        order.rcs_message = str(exc)[:512]
        order.updated_at = datetime.utcnow()
        db.add(order)
        db.commit()
        db.refresh(order)
        logger.exception(
            "FIFO_CHAIN_NEXT_STEP_DISPATCH_FAILED fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s error=%s",
            completed_order.fifo_chain_group_id,
            completed_order.id,
            order.id,
            exc,
        )
    return order


def _result(status: str, reason: str, *, completed_order_id: Optional[int] = None, next_order: Optional[MovementOrder] = None, error: Optional[str] = None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "completed_order_id": completed_order_id,
        "next_order_id": getattr(next_order, "id", None),
        "fifo_chain_group_id": getattr(next_order, "fifo_chain_group_id", None),
        "error": error,
    }


def trigger_fifo_chain_next_step_if_needed(db, completed_order_id: int, dispatch_func=_USE_DEFAULT_DISPATCH) -> dict:
    order = db.execute(select(MovementOrder).where(MovementOrder.id == completed_order_id)).scalar_one_or_none()
    if not order:
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP completed_order_id=%s reason=order_not_found", completed_order_id)
        return _result("skipped", "order_not_found", completed_order_id=completed_order_id)

    logger.info(
        "FIFO_CHAIN_NEXT_STEP_CHECK fifo_chain_group_id=%s completed_order_id=%s route_mode=%s step=%s status=%s fifo_chain_status=%s",
        getattr(order, "fifo_chain_group_id", None),
        order.id,
        getattr(order, "route_mode", None),
        getattr(order, "fifo_chain_step", None),
        getattr(order, "status", None),
        getattr(order, "fifo_chain_status", None),
    )

    if (order.route_mode or "") != "fifo_chain" or int(order.fifo_chain_step or 0) <= 0 or int(order.fifo_chain_total_steps or 0) <= 0:
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=not_fifo_chain", order.fifo_chain_group_id, order.id)
        return _result("skipped", "not_fifo_chain", completed_order_id=order.id)
    if (order.status or "") != "completed":
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=not_completed", order.fifo_chain_group_id, order.id)
        return _result("skipped", "not_completed", completed_order_id=order.id)
    if (order.dispatch_status or "") in {"error", "dispatch_error"}:
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=dispatch_error", order.fifo_chain_group_id, order.id)
        return _result("skipped", "dispatch_error", completed_order_id=order.id)
    if str(order.rcs_status or "").strip().lower() in {"error", "failed", "cancelled", "canceled"}:
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=rcs_terminal_error", order.fifo_chain_group_id, order.id)
        return _result("skipped", "rcs_terminal_error", completed_order_id=order.id)
    if not (order.fifo_chain_group_id or "").strip():
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP completed_order_id=%s reason=missing_group", order.id)
        return _result("skipped", "missing_group", completed_order_id=order.id)
    current_step = int(order.fifo_chain_step)
    total_steps = int(order.fifo_chain_total_steps)
    if current_step >= total_steps:
        order.fifo_chain_status = "completed"
        order.updated_at = datetime.utcnow()
        db.add(order)
        db.commit()
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=chain_completed", order.fifo_chain_group_id, order.id)
        return _result("skipped", "chain_completed", completed_order_id=order.id)
    if not (order.fifo_chain_next_config_json or "").strip():
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=missing_next_config", order.fifo_chain_group_id, order.id)
        return _result("skipped", "missing_next_config", completed_order_id=order.id)

    next_step = current_step + 1
    existing = db.execute(
        select(MovementOrder)
        .where(MovementOrder.fifo_chain_group_id == order.fifo_chain_group_id)
        .where(MovementOrder.fifo_chain_step == next_step)
    ).scalars().first()
    if existing:
        order.fifo_chain_status = "completed"
        order.updated_at = datetime.utcnow()
        db.add(order)
        db.commit()
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s reason=next_step_exists", order.fifo_chain_group_id, order.id, existing.id)
        return _result("existing_next_step", "next_step_exists", completed_order_id=order.id, next_order=existing)
    if str(order.fifo_chain_status or "").strip().lower() in {"completed", "failed", "cancelled", "canceled"}:
        logger.info("FIFO_CHAIN_NEXT_STEP_SKIP fifo_chain_group_id=%s completed_order_id=%s reason=already_processed", order.fifo_chain_group_id, order.id)
        return _result("skipped", "already_processed", completed_order_id=order.id)

    try:
        payload = json.loads(order.fifo_chain_next_config_json)
        if not isinstance(payload, dict):
            raise ValueError("fifo_chain_next_config_json no es un objeto")
        if isinstance(payload.get("steps"), list):
            pending_steps = [step for step in payload["steps"] if isinstance(step, dict)]
            config = next((step for step in pending_steps if int(step.get("step") or 0) == next_step), None)
            if not config:
                raise ValueError(f"No existe configuracion pendiente para paso {next_step}")
            config = dict(config)
            config["_remaining_steps"] = [step for step in pending_steps if int(step.get("step") or 0) > next_step]
            config["_chain"] = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
        else:
            # Compatibilidad con ordenes existentes cuyo JSON era directamente el paso 2.
            config = payload
        next_order = create_fifo_chain_next_step(db, order, config, dispatch_func=dispatch_func)
        order = db.execute(select(MovementOrder).where(MovementOrder.id == completed_order_id)).scalar_one()
        order.fifo_chain_status = "completed"
        order.updated_at = datetime.utcnow()
        db.add(order)
        db.commit()
        logger.info("FIFO_CHAIN_NEXT_STEP_CREATED fifo_chain_group_id=%s completed_order_id=%s next_order_id=%s result=step1_marked_completed", order.fifo_chain_group_id, order.id, next_order.id)
        return _result("created", "next_step_created", completed_order_id=order.id, next_order=next_order)
    except Exception as exc:
        db.rollback()
        order = db.execute(select(MovementOrder).where(MovementOrder.id == completed_order_id)).scalar_one_or_none()
        if order:
            order.fifo_chain_status = "failed"
            failed_step = int(order.fifo_chain_step or 0) + 1
            order.rcs_message = ((order.rcs_message or "").strip() + f" | Flujo FIFO paso {failed_step} fallido: {exc}").strip(" |")[:512]
            order.updated_at = datetime.utcnow()
            db.add(order)
            db.commit()
        logger.exception("FIFO_CHAIN_NEXT_STEP_FAILED fifo_chain_group_id=%s completed_order_id=%s error=%s", getattr(order, "fifo_chain_group_id", None), completed_order_id, exc)
        return _result("failed", "next_step_failed", completed_order_id=completed_order_id, error=str(exc))
