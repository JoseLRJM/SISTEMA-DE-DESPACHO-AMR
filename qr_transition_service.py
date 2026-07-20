from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from logging_config import get_logger
from models import (
    Area,
    Location,
    MaterialGroup,
    MovementOrder,
    QrActionRule,
    QrTransitionLog,
    QrTransitionRule,
    Rack,
    ScanEvent,
    ScannerStation,
)


VALID_TRANSITION_APPLY_ON = {"movement_completed"}
VALID_TRANSITION_SCOPES = {"qr_pda", "any_completed_order"}
VALID_TRANSITION_MATCH_MODES = {"advanced", "route_simple"}
VALID_TRANSITION_SOURCE_MATCH_MODES = {"configured_source", "any_source"}
logger = get_logger("app.qr_transition")

_TRANSITION_BLOCKED_ORDER_STATUSES = {
    "dispatch_error",
    "error",
    "rejected",
    "cancelled",
    "canceled",
    "duplicate",
    "pending",
    "pending_dispatch",
    "dispatched",
    "in_progress",
    "cancel_requested_total",
    "cancel_requested_undo",
}
_TRANSITION_ALLOWED_DISPATCH_STATUSES = {"", "success", "completed", "accepted", "ok", "sent"}


def _clean_text(value) -> str:
    return str(value or "").strip()


def _status_text(value) -> str:
    return _clean_text(value).lower()


def _rule_scope(rule: QrTransitionRule) -> str:
    scope = _clean_text(getattr(rule, "scope", "qr_pda")) or "qr_pda"
    return scope if scope in VALID_TRANSITION_SCOPES else "qr_pda"


def _rule_match_mode(rule: QrTransitionRule) -> str:
    match_mode = _clean_text(getattr(rule, "match_mode", "advanced")) or "advanced"
    return match_mode if match_mode in VALID_TRANSITION_MATCH_MODES else "advanced"


def _rule_source_match_mode(rule: QrTransitionRule) -> str:
    source_match_mode = _clean_text(getattr(rule, "source_match_mode", "configured_source")) or "configured_source"
    return source_match_mode if source_match_mode in VALID_TRANSITION_SOURCE_MATCH_MODES else "configured_source"


def _entity_ref(row, *extra_fields):
    if not row:
        return None
    data = {"id": row.id}
    for field in extra_fields:
        data[field] = getattr(row, field, None)
    return data


def _area_ref(row: Optional[Area]):
    return _entity_ref(row, "code", "name")


def _material_ref(row: Optional[MaterialGroup]):
    return _entity_ref(row, "code", "name")


def _rack_ref(row: Optional[Rack]):
    return _entity_ref(row, "code", "name", "status", "material_group_id", "quantity", "comment")


def _qr_rule_ref(row: Optional[QrActionRule]):
    return _entity_ref(row, "qr_value", "qr_alias", "action_type")


def _scanner_ref(row: Optional[ScannerStation]):
    return _entity_ref(row, "scanner_code", "name")


def _transition_rule_ref(row: Optional[QrTransitionRule]):
    if not row:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "qr_action_rule_id": row.qr_action_rule_id,
        "scanner_station_id": row.scanner_station_id,
        "source_area_id": row.source_area_id,
        "destination_area_id": row.destination_area_id,
        "source_cell_id": getattr(row, "source_cell_id", None),
        "destination_cell_id": getattr(row, "destination_cell_id", None),
        "current_material_group_id": row.current_material_group_id,
        "current_rack_status": row.current_rack_status,
        "next_material_group_id": row.next_material_group_id,
        "next_rack_status": row.next_rack_status,
        "next_quantity": row.next_quantity,
        "clear_quantity": int(row.clear_quantity or 0),
        "next_comment": row.next_comment,
        "append_comment": int(row.append_comment or 0),
        "apply_on": row.apply_on,
        "scope": _rule_scope(row),
        "match_mode": _rule_match_mode(row),
        "source_match_mode": _rule_source_match_mode(row),
        "ignore_current_material": int(getattr(row, "ignore_current_material", 0) or 0),
        "priority": int(row.priority or 0),
        "is_active": int(row.is_active or 0),
        "applied_count": int(row.applied_count or 0),
        "last_applied_at": row.last_applied_at,
    }


def _latest_scan_event_for_order(db, movement_order_id: int) -> Optional[ScanEvent]:
    return db.execute(
        select(ScanEvent)
        .where(ScanEvent.movement_order_id == movement_order_id)
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
    ).scalars().first()


def _is_qr_pda_scan_event(scan_event: Optional[ScanEvent]) -> bool:
    if not scan_event:
        return False
    return bool(
        getattr(scan_event, "terminal_id", None)
        or getattr(scan_event, "scanner_station_id", None)
        or getattr(scan_event, "qr_action_rule_id", None)
        or _clean_text(getattr(scan_event, "scanner_code", None))
        or _clean_text(getattr(scan_event, "terminal_code", None))
    )


def is_movement_order_completed_for_transition(order: MovementOrder) -> bool:
    if not order:
        return False
    status = _status_text(getattr(order, "status", None))
    dispatch_status = _status_text(getattr(order, "dispatch_status", None))
    rcs_status = _status_text(getattr(order, "rcs_status", None))
    if status != "completed":
        return False
    if status in _TRANSITION_BLOCKED_ORDER_STATUSES:
        return False
    if rcs_status and rcs_status != "completed":
        return False
    if dispatch_status in _TRANSITION_BLOCKED_ORDER_STATUSES:
        return False
    if dispatch_status not in _TRANSITION_ALLOWED_DISPATCH_STATUSES:
        return False
    return True


def _order_rack_is_at_destination(db, order: MovementOrder, rack: Optional[Rack]) -> bool:
    if not order or not rack or not getattr(order, "destination_cell_id", None):
        return False
    destination_cell = db.execute(select(Location).where(Location.id == order.destination_cell_id)).scalar_one_or_none()
    return bool(destination_cell and destination_cell.rack_id == rack.id)


def _transition_log(
    *,
    transition_rule_id: Optional[int],
    movement_order_id: Optional[int],
    scan_event_id: Optional[int],
    rack: Optional[Rack],
    previous_material_group_id: Optional[int],
    next_material_group_id: Optional[int],
    previous_rack_status: Optional[str],
    next_rack_status: Optional[str],
    previous_quantity: Optional[int],
    next_quantity: Optional[int],
    previous_comment: Optional[str],
    next_comment: Optional[str],
    status: str,
    message: str,
) -> QrTransitionLog:
    return QrTransitionLog(
        transition_rule_id=transition_rule_id,
        movement_order_id=movement_order_id,
        scan_event_id=scan_event_id,
        rack_id=getattr(rack, "id", None),
        previous_material_group_id=previous_material_group_id,
        next_material_group_id=next_material_group_id,
        previous_rack_status=previous_rack_status,
        next_rack_status=next_rack_status,
        previous_quantity=previous_quantity,
        next_quantity=next_quantity,
        previous_comment=previous_comment,
        next_comment=next_comment,
        status=status,
        message=message[:512] if message else None,
        created_at=datetime.utcnow(),
    )


def _transition_result(status: str, message: str, *, order: Optional[MovementOrder] = None, rule: Optional[QrTransitionRule] = None, rack: Optional[Rack] = None, log: Optional[QrTransitionLog] = None):
    return {
        "ok": status == "applied",
        "status": status,
        "message": message,
        "movement_order_id": getattr(order, "id", None),
        "transition_rule_id": getattr(rule, "id", None),
        "rack_id": getattr(rack, "id", None),
        "log_id": getattr(log, "id", None),
    }


def _location_area_id(db, location_id: Optional[int]) -> Optional[int]:
    if not location_id:
        return None
    location = db.execute(select(Location).where(Location.id == location_id)).scalar_one_or_none()
    return getattr(location, "area_id", None)


def _specificity(rule: QrTransitionRule) -> int:
    score = 0
    if rule.qr_action_rule_id:
        score += 128
    if rule.scanner_station_id:
        score += 64
    source_match_mode = _rule_source_match_mode(rule)
    if source_match_mode == "configured_source" and getattr(rule, "source_cell_id", None):
        score += 32
    if getattr(rule, "destination_cell_id", None):
        score += 16
    if source_match_mode == "configured_source" and rule.source_area_id:
        score += 8
    if rule.destination_area_id:
        score += 4
    if rule.current_material_group_id:
        score += 2
    if _clean_text(rule.current_rack_status):
        score += 1
    return score


def _status_matches(rule_status: Optional[str], rack_status: Optional[str]) -> bool:
    expected = _clean_text(rule_status).lower()
    if not expected:
        return True
    return expected == _clean_text(rack_status).lower()


def find_transition_rules_for_order(db, movement_order: MovementOrder):
    source_area_id = movement_order.source_area_id or _location_area_id(db, movement_order.source_cell_id)
    destination_area_id = movement_order.destination_area_id or _location_area_id(db, movement_order.destination_cell_id)
    rack = db.execute(select(Rack).where(Rack.id == movement_order.rack_id)).scalar_one_or_none() if movement_order.rack_id else None
    scan_event = _latest_scan_event_for_order(db, movement_order.id)
    is_qr_pda_order = _is_qr_pda_scan_event(scan_event)
    qr_action_rule_id = getattr(scan_event, "qr_action_rule_id", None)
    scanner_station_id = getattr(scan_event, "scanner_station_id", None)
    current_material_group_id = getattr(rack, "material_group_id", None) or movement_order.material_group_id
    current_rack_status = getattr(rack, "status", None)

    candidates = db.execute(
        select(QrTransitionRule)
        .where(QrTransitionRule.is_active == 1)
        .where(QrTransitionRule.apply_on == "movement_completed")
    ).scalars().all()

    matched = []
    for rule in candidates:
        scope = _rule_scope(rule)
        if scope == "qr_pda" and not is_qr_pda_order:
            continue
        if rule.qr_action_rule_id and rule.qr_action_rule_id != qr_action_rule_id:
            continue
        if rule.scanner_station_id and rule.scanner_station_id != scanner_station_id:
            continue
        source_match_mode = _rule_source_match_mode(rule)
        if source_match_mode == "configured_source":
            if getattr(rule, "source_cell_id", None) and rule.source_cell_id != movement_order.source_cell_id:
                continue
            if rule.source_area_id and rule.source_area_id != source_area_id:
                continue
        if getattr(rule, "destination_cell_id", None) and rule.destination_cell_id != movement_order.destination_cell_id:
            continue
        if rule.destination_area_id and rule.destination_area_id != destination_area_id:
            continue
        if not int(getattr(rule, "ignore_current_material", 0) or 0) and rule.current_material_group_id and rule.current_material_group_id != current_material_group_id:
            continue
        if not _status_matches(rule.current_rack_status, current_rack_status):
            continue
        matched.append(rule)

    return sorted(matched, key=lambda rule: (-_specificity(rule), -int(rule.priority or 0), int(rule.id or 0)))


def preview_transition_for_order(db, movement_order_id: int):
    movement_order = db.execute(select(MovementOrder).where(MovementOrder.id == movement_order_id)).scalar_one_or_none()
    if not movement_order:
        raise HTTPException(status_code=404, detail="Orden de movimiento no encontrada")

    rack = db.execute(select(Rack).where(Rack.id == movement_order.rack_id)).scalar_one_or_none() if movement_order.rack_id else None
    scan_event = _latest_scan_event_for_order(db, movement_order.id)
    qr_rule = db.execute(select(QrActionRule).where(QrActionRule.id == scan_event.qr_action_rule_id)).scalar_one_or_none() if scan_event and scan_event.qr_action_rule_id else None
    scanner = db.execute(select(ScannerStation).where(ScannerStation.id == scan_event.scanner_station_id)).scalar_one_or_none() if scan_event and scan_event.scanner_station_id else None
    current_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack and rack.material_group_id else None

    rules = find_transition_rules_for_order(db, movement_order)
    matched_rule = rules[0] if rules else None
    next_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == matched_rule.next_material_group_id)).scalar_one_or_none() if matched_rule and matched_rule.next_material_group_id else None

    return {
        "ok": True,
        "movement_order_id": movement_order.id,
        "rack": _rack_ref(rack),
        "scan_event": _entity_ref(scan_event, "qr_value", "mode", "status", "scanner_code", "qr_action_rule_id", "scanner_station_id"),
        "qr_action_rule": _qr_rule_ref(qr_rule),
        "scanner_station": _scanner_ref(scanner),
        "current_material": _material_ref(current_material),
        "matched_rule": _transition_rule_ref(matched_rule),
        "next_material": _material_ref(next_material),
        "candidate_count": len(rules),
        "message": "Transición encontrada. No se aplicó ningún cambio." if matched_rule else "No hay transición configurada para esta orden.",
    }


def apply_transition_for_completed_order(db, movement_order_id: int, reason: str = "movement_completed"):
    movement_order = db.execute(select(MovementOrder).where(MovementOrder.id == movement_order_id)).scalar_one_or_none()
    if not movement_order:
        raise HTTPException(status_code=404, detail="Orden de movimiento no encontrada")

    scan_event = _latest_scan_event_for_order(db, movement_order.id)
    rack = db.execute(select(Rack).where(Rack.id == movement_order.rack_id)).scalar_one_or_none() if movement_order.rack_id else None

    def add_log(status: str, message: str, *, rule: Optional[QrTransitionRule] = None, prev=None, nxt=None):
        previous = prev or {}
        next_values = nxt or previous
        log = _transition_log(
            transition_rule_id=getattr(rule, "id", None),
            movement_order_id=movement_order.id,
            scan_event_id=getattr(scan_event, "id", None),
            rack=rack,
            previous_material_group_id=previous.get("material_group_id"),
            next_material_group_id=next_values.get("material_group_id"),
            previous_rack_status=previous.get("status"),
            next_rack_status=next_values.get("status"),
            previous_quantity=previous.get("quantity"),
            next_quantity=next_values.get("quantity"),
            previous_comment=previous.get("comment"),
            next_comment=next_values.get("comment"),
            status=status,
            message=message,
        )
        db.add(log)
        db.flush()
        return log

    previous_values = {
        "material_group_id": getattr(rack, "material_group_id", None),
        "status": getattr(rack, "status", None),
        "quantity": getattr(rack, "quantity", None),
        "comment": getattr(rack, "comment", None),
    } if rack else {}

    if not is_movement_order_completed_for_transition(movement_order):
        message = "La orden no estÃ¡ completada correctamente; no se aplica transiciÃ³n."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    any_completed_order_candidate = any(_rule_scope(rule) == "any_completed_order" for rule in find_transition_rules_for_order(db, movement_order))
    if not _is_qr_pda_scan_event(scan_event) and not any_completed_order_candidate:
        message = "La orden no tiene ScanEvent QR/PDA asociado; no se aplica transiciÃ³n."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    if not rack:
        message = "La orden no tiene rack asociado; no se aplica transiciÃ³n."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    if not _order_rack_is_at_destination(db, movement_order, rack):
        message = "La orden esta completed, pero el rack no esta actualizado en la celda destino; no se aplica transicion."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    applied_for_order = db.execute(
        select(QrTransitionLog)
        .where(QrTransitionLog.movement_order_id == movement_order.id)
        .where(QrTransitionLog.status == "applied")
        .order_by(QrTransitionLog.id.asc())
    ).scalars().first()
    if applied_for_order:
        applied_rule = db.execute(select(QrTransitionRule).where(QrTransitionRule.id == applied_for_order.transition_rule_id)).scalar_one_or_none() if applied_for_order.transition_rule_id else None
        message = "TransiciÃ³n ya aplicada anteriormente."
        log = add_log("skipped", message, rule=applied_rule, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rule=applied_rule, rack=rack, log=log)

    rules = find_transition_rules_for_order(db, movement_order)
    rule = rules[0] if rules else None
    if not rule:
        message = "No hay transiciÃ³n configurada para esta orden."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    applied_before = db.execute(
        select(QrTransitionLog)
        .where(QrTransitionLog.movement_order_id == movement_order.id)
        .where(QrTransitionLog.transition_rule_id == rule.id)
        .where(QrTransitionLog.status == "applied")
        .order_by(QrTransitionLog.id.asc())
    ).scalars().first()
    if applied_before:
        message = "TransiciÃ³n ya aplicada anteriormente."
        log = add_log("skipped", message, rule=rule, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rule=rule, rack=rack, log=log)

    if not int(getattr(rule, "ignore_current_material", 0) or 0) and rule.current_material_group_id and rack.material_group_id != rule.current_material_group_id:
        message = "El material actual del rack ya no coincide con la regla; no se aplica transiciÃ³n."
        log = add_log("skipped", message, rule=rule, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rule=rule, rack=rack, log=log)

    if not _status_matches(rule.current_rack_status, rack.status):
        message = "El status actual del rack ya no coincide con la regla; no se aplica transiciÃ³n."
        log = add_log("skipped", message, rule=rule, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rule=rule, rack=rack, log=log)

    try:
        if rule.next_material_group_id is not None:
            rack.material_group_id = rule.next_material_group_id
        if _clean_text(rule.next_rack_status):
            rack.status = _clean_text(rule.next_rack_status)
        if int(rule.clear_quantity or 0):
            rack.quantity = 0
        elif rule.next_quantity is not None:
            rack.quantity = int(rule.next_quantity)
        if _clean_text(rule.next_comment):
            next_comment = _clean_text(rule.next_comment)
            if int(rule.append_comment or 0) and _clean_text(rack.comment):
                rack.comment = f"{_clean_text(rack.comment)} | {next_comment}"[:512]
            else:
                rack.comment = next_comment[:512]
        rack.updated_at = datetime.utcnow()
        rule.applied_count = int(rule.applied_count or 0) + 1
        rule.last_applied_at = rack.updated_at
        rule.updated_at = rack.updated_at
        db.add(rack)
        db.add(rule)
        next_values = {
            "material_group_id": rack.material_group_id,
            "status": rack.status,
            "quantity": rack.quantity,
            "comment": rack.comment,
        }
        message = f"TransiciÃ³n aplicada por {reason}."
        log = add_log("applied", message, rule=rule, prev=previous_values, nxt=next_values)
        logger.info(
            "QR_TRANSITION_APPLIED movement_order_id=%s transition_rule_id=%s rack_id=%s previous_material_group_id=%s next_material_group_id=%s reason=%s",
            movement_order.id,
            rule.id,
            rack.id,
            previous_values.get("material_group_id"),
            rack.material_group_id,
            reason,
        )
        return _transition_result("applied", message, order=movement_order, rule=rule, rack=rack, log=log)
    except Exception as exc:
        message = f"Error aplicando transiciÃ³n: {exc}"
        logger.exception("QR_TRANSITION_ERROR movement_order_id=%s transition_rule_id=%s rack_id=%s reason=%s", movement_order.id, rule.id, rack.id, reason)
        log = add_log("error", message, rule=rule, prev=previous_values)
        return _transition_result("error", message, order=movement_order, rule=rule, rack=rack, log=log)


def _require_row(db, model, row_id: Optional[int], label: str):
    if row_id is None:
        return None
    row = db.execute(select(model).where(model.id == row_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail=f"{label} no encontrado")
    return row


def validate_transition_rule_payload(db, payload):
    name = _clean_text(getattr(payload, "name", ""))
    if not name:
        raise HTTPException(status_code=400, detail="name requerido")
    apply_on = _clean_text(getattr(payload, "apply_on", "movement_completed")) or "movement_completed"
    if apply_on not in VALID_TRANSITION_APPLY_ON:
        raise HTTPException(status_code=400, detail="apply_on inválido")

    scope = _clean_text(getattr(payload, "scope", "qr_pda")) or "qr_pda"
    if scope not in VALID_TRANSITION_SCOPES:
        raise HTTPException(status_code=400, detail="scope invalido")
    match_mode = _clean_text(getattr(payload, "match_mode", "advanced")) or "advanced"
    if match_mode not in VALID_TRANSITION_MATCH_MODES:
        raise HTTPException(status_code=400, detail="match_mode invalido")
    source_match_mode = _clean_text(getattr(payload, "source_match_mode", "configured_source")) or "configured_source"
    if source_match_mode not in VALID_TRANSITION_SOURCE_MATCH_MODES:
        raise HTTPException(status_code=400, detail="source_match_mode invalido")
    if source_match_mode == "any_source":
        try:
            payload.source_area_id = None
            payload.source_cell_id = None
        except Exception:
            pass

    _require_row(db, QrActionRule, getattr(payload, "qr_action_rule_id", None), "Regla QR")
    _require_row(db, ScannerStation, getattr(payload, "scanner_station_id", None), "Scanner")
    _require_row(db, Area, getattr(payload, "source_area_id", None), "Área origen")
    _require_row(db, Area, getattr(payload, "destination_area_id", None), "Área destino")
    _require_row(db, Location, getattr(payload, "source_cell_id", None), "Celda origen")
    _require_row(db, Location, getattr(payload, "destination_cell_id", None), "Celda destino")
    _require_row(db, MaterialGroup, getattr(payload, "current_material_group_id", None), "Material actual")
    _require_row(db, MaterialGroup, getattr(payload, "next_material_group_id", None), "Material siguiente")

    next_quantity = getattr(payload, "next_quantity", None)
    if next_quantity is not None and int(next_quantity) < 0:
        raise HTTPException(status_code=400, detail="next_quantity no puede ser negativo")
    if source_match_mode == "any_source":
        has_destination = bool(getattr(payload, "destination_area_id", None) or getattr(payload, "destination_cell_id", None))
        has_next_change = bool(getattr(payload, "next_material_group_id", None) or _clean_text(getattr(payload, "next_rack_status", None)))
        ignore_current_material = int(getattr(payload, "ignore_current_material", 0) or 0) == 1
        if not (has_destination and has_next_change):
            raise HTTPException(
                status_code=400,
                detail="source_match_mode any_source requiere destino y cambio siguiente",
            )
        if not ignore_current_material and not getattr(payload, "current_material_group_id", None):
            raise HTTPException(status_code=400, detail="source_match_mode any_source requiere current_material_group_id cuando ignore_current_material es false")

    return apply_on
