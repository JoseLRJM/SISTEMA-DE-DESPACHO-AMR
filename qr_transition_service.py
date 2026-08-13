from __future__ import annotations

import json
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


def _scan_execution_id(scan_event: Optional[ScanEvent]) -> Optional[str]:
    if not scan_event:
        return None
    for raw in (getattr(scan_event, "result_json", None), getattr(scan_event, "request_json", None)):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        execution_id = payload.get("execution_id") if isinstance(payload, dict) else None
        if execution_id:
            return str(execution_id)
    return None


def _transition_trace(event: str, execution_id: Optional[str], **fields) -> None:
    payload = {"event": event, "execution_id": execution_id}
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.info("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")))

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
    direct = db.execute(
        select(ScanEvent)
        .where(ScanEvent.movement_order_id == movement_order_id)
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
    ).scalars().first()
    if direct:
        return direct

    movement_order = db.execute(
        select(MovementOrder).where(MovementOrder.id == movement_order_id)
    ).scalar_one_or_none()
    if not movement_order or _clean_text(getattr(movement_order, "route_mode", None)) != "fifo_chain":
        return None
    group_id = _clean_text(getattr(movement_order, "fifo_chain_group_id", None))
    if not group_id:
        return None
    chain_order_ids = [
        row[0]
        for row in db.execute(
            select(MovementOrder.id)
            .where(MovementOrder.fifo_chain_group_id == group_id)
            .order_by(MovementOrder.fifo_chain_step.asc(), MovementOrder.id.asc())
        ).all()
        if row and row[0] is not None
    ]
    if not chain_order_ids:
        return None
    return db.execute(
        select(ScanEvent)
        .where(ScanEvent.movement_order_id.in_(chain_order_ids))
        .order_by(ScanEvent.created_at.asc(), ScanEvent.id.asc())
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
    advanced = _rule_match_mode(rule) == "advanced"
    if advanced and rule.qr_action_rule_id:
        score += 128
    if advanced and rule.scanner_station_id:
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
    if advanced and rule.current_material_group_id:
        score += 2
    if advanced and _clean_text(rule.current_rack_status):
        score += 1
    return score


def transition_specificity(rule: QrTransitionRule) -> int:
    """Valor derivado estable usado para diagnóstico y respuestas administrativas."""
    return _specificity(rule)


def transition_sort_key(rule: QrTransitionRule):
    """Orden canónico: prioridad, especificidad y finalmente ID."""
    return (-int(getattr(rule, "priority", 0) or 0), -_specificity(rule), int(getattr(rule, "id", 0) or 0))


def transition_match_tier(rule: QrTransitionRule, context: dict) -> int:
    """0=QR exacto, 1=Scanner exacto sin QR, 2=fallback general."""
    if _rule_match_mode(rule) == "advanced":
        qr_rule_id = getattr(rule, "qr_action_rule_id", None)
        scanner_id = getattr(rule, "scanner_station_id", None)
        if qr_rule_id and qr_rule_id == context.get("qr_action_rule_id"):
            return 0
        if not qr_rule_id and scanner_id and scanner_id == context.get("scanner_station_id"):
            return 1
    return 2


def transition_rank_key(rule: QrTransitionRule, context: dict):
    return (transition_match_tier(rule, context), *transition_sort_key(rule))


def transition_selection_reason(rule: Optional[QrTransitionRule], context: dict, same_tier_rules=None) -> str:
    if not rule:
        return "sin coincidencias"
    tier = transition_match_tier(rule, context)
    if tier == 0:
        return "coincidencia directa con QR"
    if tier == 1:
        return "coincidencia directa con Scanner"
    return "fallback de ruta general"


def transition_match_metadata(rule: QrTransitionRule, context: dict) -> dict:
    tier = transition_match_tier(rule, context)
    return {
        "match_tier": tier,
        "direct_qr_match": tier == 0,
        "direct_scanner_match": tier == 1,
    }


def configured_transition_match_tier(value) -> int:
    if _rule_match_mode(value) == "advanced":
        if getattr(value, "qr_action_rule_id", None):
            return 0
        if getattr(value, "scanner_station_id", None):
            return 1
    return 2


_TRANSITION_CONDITION_FIELDS = (
    "apply_on", "scope", "match_mode", "source_match_mode", "qr_action_rule_id",
    "scanner_station_id", "source_area_id", "destination_area_id", "source_cell_id",
    "destination_cell_id", "current_material_group_id", "current_rack_status",
    "ignore_current_material", "is_active",
)
_TRANSITION_RESULT_FIELDS = (
    "next_material_group_id", "next_rack_status", "next_quantity", "clear_quantity",
    "next_comment", "append_comment",
)


def _normalized_transition_value(field: str, value):
    if field in {"apply_on", "scope", "match_mode", "source_match_mode"}:
        defaults = {"apply_on": "movement_completed", "scope": "qr_pda", "match_mode": "advanced", "source_match_mode": "configured_source"}
        return _clean_text(value).lower() or defaults[field]
    if field in {"current_rack_status", "next_rack_status"}:
        return _clean_text(value).lower() or None
    if field == "next_comment":
        return _clean_text(value) or None
    if field in {"ignore_current_material", "is_active", "clear_quantity", "append_comment"}:
        return int(value or 0)
    return value


def transition_condition_signature(value) -> dict:
    signature = {field: _normalized_transition_value(field, getattr(value, field, None)) for field in _TRANSITION_CONDITION_FIELDS}
    if signature["source_match_mode"] == "any_source":
        signature["source_area_id"] = None
        signature["source_cell_id"] = None
    return signature


def transition_result_signature(value) -> dict:
    return {field: _normalized_transition_value(field, getattr(value, field, None)) for field in _TRANSITION_RESULT_FIELDS}


def _transition_rules_overlap(left, right) -> bool:
    a, b = transition_condition_signature(left), transition_condition_signature(right)
    if not a["is_active"] or not b["is_active"] or a["apply_on"] != b["apply_on"]:
        return False
    for field in ("qr_action_rule_id", "scanner_station_id", "destination_area_id", "destination_cell_id", "current_material_group_id", "current_rack_status"):
        if a[field] is not None and b[field] is not None and a[field] != b[field]:
            return False
    if a["source_match_mode"] == b["source_match_mode"] == "configured_source":
        for field in ("source_area_id", "source_cell_id"):
            if a[field] is not None and b[field] is not None and a[field] != b[field]:
                return False
    return True


def diagnose_transition_rule_payload(db, payload, rule_id: Optional[int] = None) -> dict:
    signature = transition_condition_signature(payload)
    result = transition_result_signature(payload)
    duplicate = False
    blocking = []
    warnings = []
    configured_tier = configured_transition_match_tier(payload)
    fallback_rules = []
    direct_rules = []
    if int(getattr(payload, "clear_quantity", 0) or 0) and getattr(payload, "next_quantity", None) not in (None, 0):
        blocking.append({"code": "INVALID_TRANSITION_RESULT", "reason": "clear_quantity no puede combinarse con next_quantity distinto de 0."})
    for cell_field, area_field, code, label in (
        ("source_cell_id", "source_area_id", "SOURCE_CELL_OUTSIDE_AREA", "origen"),
        ("destination_cell_id", "destination_area_id", "DESTINATION_CELL_OUTSIDE_AREA", "destino"),
    ):
        cell_id, area_id = getattr(payload, cell_field, None), getattr(payload, area_field, None)
        if cell_id and area_id:
            cell = db.execute(select(Location).where(Location.id == cell_id)).scalar_one_or_none()
            if cell and cell.area_id != area_id:
                blocking.append({"code": code, "reason": f"La celda {label} no pertenece al área {label}."})
    has_result = any(result[field] not in (None, "", 0) for field in ("next_material_group_id", "next_rack_status", "next_quantity", "clear_quantity", "next_comment"))
    if not has_result:
        blocking.append({"code": "NO_EFFECT_TRANSITION_RULE", "reason": "La transición no configura ningún cambio."})
    if getattr(payload, "current_material_group_id", None) and getattr(payload, "next_material_group_id", None) == getattr(payload, "current_material_group_id", None) and not any(result[field] not in (None, "", 0) for field in ("next_rack_status", "next_quantity", "clear_quantity", "next_comment")):
        blocking.append({"code": "NO_EFFECT_TRANSITION_RULE", "reason": "El material actual y siguiente son iguales y no hay otro cambio."})
    if signature["scope"] == "qr_pda" and not signature["qr_action_rule_id"] and not signature["scanner_station_id"]:
        warnings.append({"code": "BROAD_QR_PDA_SCOPE", "reason": "Aplicará a cualquier QR/PDA que cumpla la ruta y condiciones."})
    if signature["scope"] == "any_completed_order" and (signature["qr_action_rule_id"] or signature["scanner_station_id"]):
        warnings.append({"code": "CONTRADICTORY_SCOPE_FILTER", "reason": "El alcance general está restringido por QR o Scanner."})
    if signature["match_mode"] == "route_simple":
        unexpected = [field for field in ("qr_action_rule_id", "scanner_station_id", "current_material_group_id", "current_rack_status") if signature[field] not in (None, "")]
        if unexpected:
            blocking.append({"code": "UNEXPECTED_SIMPLE_CONDITIONS", "reason": "El modo Simple solo usa origen/destino; limpia estas condiciones avanzadas: " + ", ".join(unexpected) + "."})
    existing = db.execute(select(QrTransitionRule).where(QrTransitionRule.is_active == 1)).scalars().all()
    for row in existing:
        if rule_id is not None and int(row.id) == int(rule_id):
            continue
        same_conditions = transition_condition_signature(row) == signature
        same_result = transition_result_signature(row) == result
        if same_conditions:
            code = "DUPLICATE_TRANSITION_RULE" if same_result else "CONFLICTING_TRANSITION_RULE"
            item = {"code": code, "rule_id": row.id, "rule_name": row.name, "reason": "Ya existe una regla activa con las mismas condiciones" + (" y resultado." if same_result else " y un resultado diferente.")}
            blocking.append(item)
            duplicate = duplicate or same_result
            _transition_trace("TRANSITION_RULE_DUPLICATE" if same_result else "TRANSITION_RULE_CONFLICT", None, existing_rule_id=row.id, edited_rule_id=rule_id, reason=item["reason"])
        elif _transition_rules_overlap(payload, row):
            candidate_priority, existing_priority = int(getattr(payload, "priority", 0) or 0), int(row.priority or 0)
            candidate_specificity, existing_specificity = _specificity(payload), _specificity(row)
            existing_tier = configured_transition_match_tier(row)
            if configured_tier == 0 and existing_tier > 0:
                fallback_rules.append(row.id)
                warning = {"code": "GENERAL_RULE_WILL_BE_FALLBACK", "rule_id": row.id, "rule_name": row.name, "reason": "La regla asociada al QR tendrá precedencia cuando ese QR origine la orden.", "selection_reason": "coincidencia directa con QR"}
                warnings.append(warning)
                continue
            if configured_tier == 0 and existing_tier == 0 and getattr(payload, "qr_action_rule_id", None) == row.qr_action_rule_id:
                direct_rules.append(row.id)
            if configured_tier == existing_tier and candidate_priority == existing_priority and candidate_specificity == existing_specificity and not same_result:
                item = {"code": "CONFLICTING_TRANSITION_RULE", "rule_id": row.id, "rule_name": row.name, "reason": "Dos reglas del mismo nivel, prioridad y especificidad pueden producir resultados diferentes."}
                blocking.append(item)
                continue
            reason = "precedencia de nivel" if configured_tier != existing_tier else ("mayor prioridad" if candidate_priority != existing_priority else ("mayor especificidad" if candidate_specificity != existing_specificity else "menor ID"))
            candidate_wins = (candidate_priority, candidate_specificity) > (existing_priority, existing_specificity)
            warning = {"code": "OVERLAPPING_TRANSITION_RULE", "rule_id": row.id, "rule_name": row.name, "reason": "Las condiciones pueden coincidir para una misma orden.", "winner_if_matched": rule_id if candidate_wins and rule_id else row.id, "selection_reason": reason}
            warnings.append(warning)
            _transition_trace("TRANSITION_RULE_OVERLAP", None, transition_rule_id=rule_id, overlapping_rule_id=row.id, selection_reason=reason)
    return {"valid": not blocking, "duplicate": duplicate, "blocking_conflicts": blocking, "warnings": warnings, "normalized_signature": signature, "match_tier": configured_tier, "direct_qr_rule": configured_tier == 0, "fallback_rules": sorted(set(fallback_rules)), "direct_same_qr_rules": sorted(set(direct_rules))}


def enforce_transition_rule_diagnostics(db, payload, rule_id: Optional[int] = None) -> dict:
    diagnostic = diagnose_transition_rule_payload(db, payload, rule_id)
    if diagnostic["blocking_conflicts"]:
        first = diagnostic["blocking_conflicts"][0]
        status_code = 409 if first["code"] in {"DUPLICATE_TRANSITION_RULE", "CONFLICTING_TRANSITION_RULE"} else 400
        raise HTTPException(status_code=status_code, detail={"code": first["code"], "message": first["reason"], "rule_id": first.get("rule_id"), "rule_name": first.get("rule_name"), "diagnostic": diagnostic})
    return diagnostic


def _status_matches(rule_status: Optional[str], rack_status: Optional[str]) -> bool:
    expected = _clean_text(rule_status).lower()
    if not expected:
        return True
    return expected == _clean_text(rack_status).lower()


def _transition_context(db, movement_order: MovementOrder) -> dict:
    source_area_id = movement_order.source_area_id or _location_area_id(db, movement_order.source_cell_id)
    destination_area_id = movement_order.destination_area_id or _location_area_id(db, movement_order.destination_cell_id)
    rack = db.execute(select(Rack).where(Rack.id == movement_order.rack_id)).scalar_one_or_none() if movement_order.rack_id else None
    scan_event = _latest_scan_event_for_order(db, movement_order.id)
    return {
        "source_area_id": source_area_id,
        "destination_area_id": destination_area_id,
        "rack": rack,
        "scan_event": scan_event,
        "is_qr_pda_order": _is_qr_pda_scan_event(scan_event),
        "qr_action_rule_id": getattr(scan_event, "qr_action_rule_id", None),
        "scanner_station_id": getattr(scan_event, "scanner_station_id", None),
        "terminal_id": getattr(scan_event, "terminal_id", None),
        "current_material_group_id": getattr(rack, "material_group_id", None) or movement_order.material_group_id,
        "current_rack_status": getattr(rack, "status", None),
    }


def _transition_rule_mismatch_reason(db, rule: QrTransitionRule, movement_order: MovementOrder, context: dict) -> Optional[str]:
    if _rule_scope(rule) == "qr_pda" and not context["is_qr_pda_order"]:
        return "scope qr_pda requiere ScanEvent o contexto QR/PDA de la cadena"
    advanced = _rule_match_mode(rule) == "advanced"
    if advanced and rule.qr_action_rule_id and rule.qr_action_rule_id != context["qr_action_rule_id"]:
        return f"qr_action_rule_id actual {context['qr_action_rule_id']} no coincide con {rule.qr_action_rule_id}"
    if advanced and rule.scanner_station_id and rule.scanner_station_id != context["scanner_station_id"]:
        return f"scanner_station_id actual {context['scanner_station_id']} no coincide con {rule.scanner_station_id}"
    if _rule_source_match_mode(rule) == "configured_source":
        if getattr(rule, "source_cell_id", None) and rule.source_cell_id != movement_order.source_cell_id:
            return f"celda origen actual {movement_order.source_cell_id} no coincide con {rule.source_cell_id}"
        if rule.source_area_id and rule.source_area_id != context["source_area_id"]:
            return f"area origen actual {context['source_area_id']} no coincide con {rule.source_area_id}"
    if getattr(rule, "destination_cell_id", None) and rule.destination_cell_id != movement_order.destination_cell_id:
        return f"celda destino actual {movement_order.destination_cell_id} no coincide con {rule.destination_cell_id}"
    if rule.destination_area_id and rule.destination_area_id != context["destination_area_id"]:
        return f"area destino actual {context['destination_area_id']} no coincide con {rule.destination_area_id}"
    if advanced and not int(getattr(rule, "ignore_current_material", 0) or 0) and rule.current_material_group_id and rule.current_material_group_id != context["current_material_group_id"]:
        actual = db.execute(select(MaterialGroup).where(MaterialGroup.id == context["current_material_group_id"])).scalar_one_or_none() if context["current_material_group_id"] else None
        expected = db.execute(select(MaterialGroup).where(MaterialGroup.id == rule.current_material_group_id)).scalar_one_or_none()
        actual_label = getattr(actual, "code", None) or context["current_material_group_id"] or "sin material"
        expected_label = getattr(expected, "code", None) or rule.current_material_group_id
        return f"material actual {actual_label} no coincide con current_material_group_id {expected_label}"
    if advanced and not _status_matches(rule.current_rack_status, context["current_rack_status"]):
        return f"status actual {context['current_rack_status']} no coincide con {rule.current_rack_status}"
    return None


def _evaluate_transition_rules_for_order(db, movement_order: MovementOrder) -> dict:
    context = _transition_context(db, movement_order)
    candidates = db.execute(
        select(QrTransitionRule)
        .where(QrTransitionRule.is_active == 1)
        .where(QrTransitionRule.apply_on == "movement_completed")
    ).scalars().all()
    matched = []
    mismatches = []
    for rule in candidates:
        reason = _transition_rule_mismatch_reason(db, rule, movement_order, context)
        if reason:
            mismatches.append((rule, reason))
        else:
            matched.append(rule)
    return {
        "context": context,
        "candidates": candidates,
        "matched": sorted(matched, key=lambda rule: transition_rank_key(rule, context)),
        "mismatches": mismatches,
    }


def find_transition_rules_for_order(db, movement_order: MovementOrder):
    return _evaluate_transition_rules_for_order(db, movement_order)["matched"]


def preview_transition_for_order(db, movement_order_id: int):
    movement_order = db.execute(select(MovementOrder).where(MovementOrder.id == movement_order_id)).scalar_one_or_none()
    if not movement_order:
        raise HTTPException(status_code=404, detail="Orden de movimiento no encontrada")

    rack = db.execute(select(Rack).where(Rack.id == movement_order.rack_id)).scalar_one_or_none() if movement_order.rack_id else None
    scan_event = _latest_scan_event_for_order(db, movement_order.id)
    qr_rule = db.execute(select(QrActionRule).where(QrActionRule.id == scan_event.qr_action_rule_id)).scalar_one_or_none() if scan_event and scan_event.qr_action_rule_id else None
    scanner = db.execute(select(ScannerStation).where(ScannerStation.id == scan_event.scanner_station_id)).scalar_one_or_none() if scan_event and scan_event.scanner_station_id else None
    current_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack and rack.material_group_id else None

    evaluation = _evaluate_transition_rules_for_order(db, movement_order)
    rules = evaluation["matched"]
    context = evaluation["context"]
    matched_rule = rules[0] if rules else None
    tied = [candidate for candidate in rules if matched_rule and transition_match_tier(candidate, context) == transition_match_tier(matched_rule, context) and transition_sort_key(candidate)[:2] == transition_sort_key(matched_rule)[:2]]
    ambiguous = len(tied) > 1 and len({json.dumps(transition_result_signature(candidate), sort_keys=True) for candidate in tied}) > 1
    if ambiguous:
        matched_rule = None
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
        "matched_rules": [{**_transition_rule_ref(rule), "specificity": _specificity(rule), **transition_match_metadata(rule, context)} for rule in rules],
        "selected_rule_id": getattr(matched_rule, "id", None),
        "selected_match_tier": transition_match_tier(matched_rule, context) if matched_rule else None,
        "selection_reason": "TRANSITION_AMBIGUOUS: empate con resultados diferentes" if ambiguous else transition_selection_reason(matched_rule, context, rules),
        "ambiguous": ambiguous,
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

    def add_log(status: str, message: str, *, rule: Optional[QrTransitionRule] = None, prev=None, nxt=None, emit_trace: bool = True):
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
        if emit_trace and status in {"skipped", "error"}:
            _transition_trace("QR_TRANSITION_SKIPPED" if status == "skipped" else "QR_TRANSITION_CONFLICT", _scan_execution_id(scan_event), stage="transition", scan_event_id=getattr(scan_event, "id", None), movement_order_id=movement_order.id, transition_rule_id=getattr(rule, "id", None), rack_id=getattr(rack, "id", None), error_code="TRANSITION_NOT_MATCHED" if status == "skipped" else "TRANSITION_APPLY_ERROR", operator_message=message)
        return log

    previous_values = {
        "material_group_id": getattr(rack, "material_group_id", None),
        "status": getattr(rack, "status", None),
        "quantity": getattr(rack, "quantity", None),
        "comment": getattr(rack, "comment", None),
    } if rack else {}

    evaluation = _evaluate_transition_rules_for_order(db, movement_order)
    context = evaluation["context"]
    execution_id = _scan_execution_id(scan_event)
    material_before = db.execute(
        select(MaterialGroup).where(MaterialGroup.id == previous_values.get("material_group_id"))
    ).scalar_one_or_none() if previous_values.get("material_group_id") else None
    material_before_label = getattr(material_before, "code", None) or previous_values.get("material_group_id")
    logger.info(
        "TRANSITION_CHECK_FOR_COMPLETED_ORDER movement_order_id=%s order_code=%s route_mode=%s fifo_chain_group_id=%s fifo_chain_step=%s fifo_chain_total_steps=%s source_area_id=%s destination_area_id=%s rack_id=%s material_before_transition=%s scanner_station_id=%s qr_action_rule_id=%s terminal_id=%s",
        movement_order.id,
        movement_order.order_code,
        movement_order.route_mode,
        movement_order.fifo_chain_group_id,
        movement_order.fifo_chain_step,
        movement_order.fifo_chain_total_steps,
        context["source_area_id"],
        context["destination_area_id"],
        getattr(rack, "id", None),
        material_before_label,
        context["scanner_station_id"],
        context["qr_action_rule_id"],
        context["terminal_id"],
    )
    matched_rules = evaluation["matched"]
    selected_rule = matched_rules[0] if matched_rules else None
    _transition_trace(
        "QR_TRANSITION_EVALUATED",
        execution_id,
        stage="transition_evaluation",
        scan_event_id=getattr(scan_event, "id", None),
        movement_order_id=movement_order.id,
        order_code=movement_order.order_code,
        candidate_count=len(evaluation["candidates"]),
        matched_count=len(matched_rules),
        matched_rules=[{"id": row.id, "name": row.name, "priority": int(row.priority or 0), "specificity": _specificity(row), **transition_match_metadata(row, context)} for row in matched_rules],
        selected_rule_id=getattr(selected_rule, "id", None),
        selected_priority=int(getattr(selected_rule, "priority", 0) or 0) if selected_rule else None,
        selected_specificity=_specificity(selected_rule) if selected_rule else None,
        selected_match_tier=transition_match_tier(selected_rule, context) if selected_rule else None,
        selection_reason=transition_selection_reason(selected_rule, context, matched_rules),
        discarded=[{"id": row.id, "name": row.name, "reason": mismatch} for row, mismatch in evaluation["mismatches"][:10]],
        source_area_id=context["source_area_id"],
        destination_area_id=context["destination_area_id"],
        rack_id=getattr(rack, "id", None),
        rack_code=getattr(rack, "code", None),
        current_material_group_id=previous_values.get("material_group_id"),
        current_rack_status=previous_values.get("status"),
    )
    _transition_trace(
        "TRANSITION_RULE_SELECTION",
        execution_id,
        movement_order_id=movement_order.id,
        qr_action_rule_id=context["qr_action_rule_id"],
        scanner_station_id=context["scanner_station_id"],
        source_area_id=context["source_area_id"],
        destination_area_id=context["destination_area_id"],
        current_material_group_id=previous_values.get("material_group_id"),
        current_rack_status=previous_values.get("status"),
        matched_rules=[{"id": row.id, "priority": int(row.priority or 0), "specificity": _specificity(row), **transition_match_metadata(row, context)} for row in matched_rules],
        selected_rule_id=getattr(selected_rule, "id", None),
        selected_match_tier=transition_match_tier(selected_rule, context) if selected_rule else None,
        selection_reason=transition_selection_reason(selected_rule, context, matched_rules),
    )

    def log_not_matched(reason_text: str):
        mismatch_summary = "; ".join(
            f"rule {candidate_rule.id}: {mismatch_reason}"
            for candidate_rule, mismatch_reason in evaluation["mismatches"][:10]
        )
        logger.info(
            "TRANSITION_NOT_MATCHED movement_order_id=%s reason=%s candidate_rules_count=%s route=%s->%s material_actual=%s mismatch_summary=%s",
            movement_order.id,
            reason_text,
            len(evaluation["candidates"]),
            context["source_area_id"],
            context["destination_area_id"],
            material_before_label,
            mismatch_summary or "sin reglas coincidentes",
        )
        _transition_trace("QR_TRANSITION_SKIPPED", execution_id, stage="transition", scan_event_id=getattr(scan_event, "id", None), movement_order_id=movement_order.id, error_code="TRANSITION_NOT_MATCHED", operator_message=reason_text, mismatch_reasons=[{"rule_id": row.id, "reason": mismatch} for row, mismatch in evaluation["mismatches"][:10]])

    if not is_movement_order_completed_for_transition(movement_order):
        message = "La orden no estÃ¡ completada correctamente; no se aplica transiciÃ³n."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    any_completed_order_candidate = any(_rule_scope(rule) == "any_completed_order" for rule in evaluation["candidates"])
    if not _is_qr_pda_scan_event(scan_event) and not any_completed_order_candidate:
        message = "La orden no tiene ScanEvent QR/PDA asociado; no se aplica transiciÃ³n."
        log_not_matched("scope qr_pda sin contexto QR/PDA")
        log = add_log("skipped", message, prev=previous_values, emit_trace=False)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    if not rack:
        message = "La orden no tiene rack asociado; no se aplica transiciÃ³n."
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

    if not _order_rack_is_at_destination(db, movement_order, rack):
        message = "La orden esta completed, pero el rack no esta actualizado en la celda destino; no se aplica transicion."
        log = add_log("skipped", message, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    rules = evaluation["matched"]
    rule = rules[0] if rules else None
    if not rule:
        message = "No hay transiciÃ³n configurada para esta orden."
        log_not_matched("ninguna regla coincide con el contexto actual")
        log = add_log("skipped", message, prev=previous_values, emit_trace=False)
        return _transition_result("skipped", message, order=movement_order, rack=rack, log=log)

    tied = [candidate for candidate in rules if transition_match_tier(candidate, context) == transition_match_tier(rule, context) and transition_sort_key(candidate)[:2] == transition_sort_key(rule)[:2]]
    if len(tied) > 1 and len({json.dumps(transition_result_signature(candidate), sort_keys=True) for candidate in tied}) > 1:
        message = "TRANSITION_AMBIGUOUS: reglas con igual prioridad y especificidad producirían resultados distintos; no se modificó el rack."
        _transition_trace("TRANSITION_AMBIGUOUS", execution_id, movement_order_id=movement_order.id, matched_rule_ids=[candidate.id for candidate in tied], priority=int(rule.priority or 0), specificity=_specificity(rule))
        log = add_log("error", message, prev=previous_values)
        return _transition_result("error", message, order=movement_order, rack=rack, log=log)

    if len(tied) > 1:
        _transition_trace("TRANSITION_RULE_DUPLICATE", execution_id, movement_order_id=movement_order.id, inherited=True, matched_rule_ids=[candidate.id for candidate in tied], selected_rule_id=rule.id, selection_reason="resultados iguales; menor ID")

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

    advanced_rule = _rule_match_mode(rule) == "advanced"
    if advanced_rule and not int(getattr(rule, "ignore_current_material", 0) or 0) and rule.current_material_group_id and rack.material_group_id != rule.current_material_group_id:
        message = "El material actual del rack ya no coincide con la regla; no se aplica transiciÃ³n."
        log = add_log("skipped", message, rule=rule, prev=previous_values)
        return _transition_result("skipped", message, order=movement_order, rule=rule, rack=rack, log=log)

    if advanced_rule and not _status_matches(rule.current_rack_status, rack.status):
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
        _transition_trace("QR_TRANSITION_APPLIED", execution_id, stage="transition", scan_event_id=getattr(scan_event, "id", None), movement_order_id=movement_order.id, order_code=movement_order.order_code, transition_rule_id=rule.id, transition_rule_name=rule.name, selected_priority=int(rule.priority or 0), selected_specificity=_specificity(rule), rack_id=rack.id, rack_code=rack.code, previous_material_group_id=previous_values.get("material_group_id"), next_material_group_id=rack.material_group_id, reason=reason)
        logger.info(
            "TRANSITION_APPLIED rule_id=%s movement_order_id=%s rack_id=%s material_before=%s material_after=%s status_before=%s status_after=%s",
            rule.id,
            movement_order.id,
            rack.id,
            previous_values.get("material_group_id"),
            rack.material_group_id,
            previous_values.get("status"),
            rack.status,
        )
        return _transition_result("applied", message, order=movement_order, rule=rule, rack=rack, log=log)
    except Exception as exc:
        message = f"Error aplicando transiciÃ³n: {exc}"
        logger.exception("QR_TRANSITION_ERROR movement_order_id=%s transition_rule_id=%s rack_id=%s reason=%s", movement_order.id, rule.id, rack.id, reason)
        _transition_trace("QR_TRANSITION_CONFLICT", execution_id, stage="transition", scan_event_id=getattr(scan_event, "id", None), movement_order_id=movement_order.id, transition_rule_id=rule.id, rack_id=rack.id, error_code="TRANSITION_APPLY_ERROR", raw_error=str(exc))
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

    qr_rule = _require_row(db, QrActionRule, getattr(payload, "qr_action_rule_id", None), "Regla QR")
    scanner = _require_row(db, ScannerStation, getattr(payload, "scanner_station_id", None), "Scanner")
    if qr_rule is not None and int(qr_rule.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail="Regla QR inactiva")
    if scanner is not None and int(scanner.is_active or 0) != 1:
        raise HTTPException(status_code=409, detail="Scanner inactivo")
    source_area = _require_row(db, Area, getattr(payload, "source_area_id", None), "Área origen")
    destination_area = _require_row(db, Area, getattr(payload, "destination_area_id", None), "Área destino")
    _require_row(db, Location, getattr(payload, "source_cell_id", None), "Celda origen")
    _require_row(db, Location, getattr(payload, "destination_cell_id", None), "Celda destino")
    current_material = _require_row(db, MaterialGroup, getattr(payload, "current_material_group_id", None), "Material actual")
    next_material = _require_row(db, MaterialGroup, getattr(payload, "next_material_group_id", None), "Material siguiente")
    for row, label in ((source_area, "Área origen"), (destination_area, "Área destino"), (current_material, "Material actual"), (next_material, "Material siguiente")):
        if row is not None and int(getattr(row, "is_active", 0) or 0) != 1:
            raise HTTPException(status_code=409, detail=f"{label} inactivo; restáuralo antes de utilizarlo")

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
