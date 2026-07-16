from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from fifo_service import _find_destination_cell, execute_fifo_request, resolve_fifo_request
from logging_config import get_logger
from models import Area, Location, MaterialGroup, MovementOrder, QrActionRule, Rack, ScanEvent, ScannerStation, ScanTerminal

logger = get_logger("app.scanner")
SCAN_DEDUP_SECONDS = 7
SCAN_EXECUTE_RECENT_STATUSES = {"success", "created", "dispatched", "pending"}


class ScanPreviewError(Exception):
    def __init__(self, message: str, status: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status


KNOWN_PREFIX_ACTIONS = {
    "RACK": "direct_move",
    "MAT": "fifo_request",
    "STORE": "store_rack",
    "MOVE": "direct_move",
}


def normalize_qr_value(qr_value: str) -> str:
    text = str(qr_value or "")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    return " ".join(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").split()).strip()


def parse_qr_value(qr_value: str) -> dict:
    value = normalize_qr_value(qr_value)
    upper = value.upper()
    if upper.startswith("RACK:"):
        return {"parsed_type": "rack", "prefix": "RACK", "code": value[5:].strip(), "raw": value}
    if upper.startswith("MAT:"):
        return {"parsed_type": "material", "prefix": "MAT", "code": value[4:].strip(), "raw": value}
    if upper == "REQ:EMPTY_RACK":
        return {"parsed_type": "request", "prefix": "REQ", "request": "EMPTY_RACK", "raw": value}
    if upper == "REQ:FULL_RACK":
        return {"parsed_type": "request", "prefix": "REQ", "request": "FULL_RACK", "raw": value}
    if upper.startswith("STORE:"):
        return {"parsed_type": "store", "prefix": "STORE", "rack_code": value[6:].strip(), "raw": value}
    if upper.startswith("MOVE:"):
        parts = value.split(":", 2)
        return {
            "parsed_type": "move",
            "prefix": "MOVE",
            "rack_code": parts[1].strip() if len(parts) > 1 else "",
            "destination_code": parts[2].strip() if len(parts) > 2 else "",
            "raw": value,
        }
    return {"parsed_type": "generic", "prefix": None, "code": value, "raw": value}


def resolve_scanner_station(db, scanner_code):
    code = str(scanner_code or "").strip()
    if not code:
        raise ScanPreviewError("scanner_code requerido", "rejected")
    row = db.execute(select(ScannerStation).where(ScannerStation.scanner_code == code)).scalar_one_or_none()
    if not row:
        raise ScanPreviewError("Scanner no configurado", "error")
    if int(row.is_active or 0) != 1:
        raise ScanPreviewError("Scanner inactivo", "rejected")
    return row


def resolve_scan_terminal(db, terminal_code, api_key=None):
    code = str(terminal_code or "").strip()
    if not code:
        raise ScanPreviewError("terminal_code requerido", "rejected")
    terminal = db.execute(select(ScanTerminal).where(ScanTerminal.terminal_code == code)).scalar_one_or_none()
    if not terminal:
        raise ScanPreviewError("terminal_code no registrado", "error")
    if int(terminal.is_active or 0) != 1:
        raise ScanPreviewError("Terminal inactivo", "rejected")
    expected_key = str(terminal.api_key or "").strip()
    if expected_key and str(api_key or "").strip() != expected_key:
        raise ScanPreviewError("X-Terminal-Key invalido", "rejected")
    station = db.execute(select(ScannerStation).where(ScannerStation.id == terminal.scanner_station_id)).scalar_one_or_none()
    if not station:
        raise ScanPreviewError("Scanner asociado no encontrado", "error")
    if int(station.is_active or 0) != 1:
        raise ScanPreviewError("Scanner asociado inactivo", "rejected")
    return terminal, station


def resolve_scanner_cancel_return_area(db, scanner):
    area_id = getattr(scanner, "cancel_return_area_id", None)
    if not area_id:
        raise ScanPreviewError("El scanner no tiene configurada un área para regresar material al cancelar.", "rejected")
    area = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none()
    if not area:
        raise ScanPreviewError("El área configurada para devolución ya no existe.", "error")
    matter_area = str(area.matter_area or "").strip()
    if not matter_area:
        raise ScanPreviewError("El área configurada para devolución no tiene Matter Area.", "rejected")
    return {
        "return_area_id": area.id,
        "matter_area": matter_area,
        "area": area,
    }


def _rule_matches(rule: QrActionRule, qr_value: str) -> bool:
    pattern = rule.qr_value or ""
    match_type = (rule.match_type or "exact").strip().lower()
    if match_type == "exact":
        return qr_value == pattern
    if match_type == "prefix":
        return qr_value.startswith(pattern)
    if match_type == "contains":
        return pattern in qr_value
    if match_type == "regex":
        try:
            return re.search(pattern, qr_value) is not None
        except re.error as exc:
            raise ScanPreviewError(f"Regla QR con regex invalido: {exc}", "error")
    return False


def resolve_qr_action_rule(db, qr_value):
    value = normalize_qr_value(qr_value)
    rows = db.execute(select(QrActionRule).where(QrActionRule.is_active == 1)).scalars().all()
    matches = [row for row in rows if _rule_matches(row, value)]
    if len(matches) > 1:
        ids = ", ".join(str(row.id) for row in matches)
        raise ScanPreviewError(f"QR ambiguo: coincide con reglas {ids}", "rejected")
    return matches[0] if matches else None


def resolve_scan_action(db, station, qr_rule, parsed):
    if qr_rule:
        action = (qr_rule.action_type or "use_scanner_default").strip()
        if action and action != "use_scanner_default":
            return action
        station_action = (getattr(station, "default_action", None) or "").strip()
        if station_action:
            return station_action
    prefix = (parsed.get("prefix") or "").upper()
    if prefix == "REQ":
        request = (parsed.get("request") or "").upper()
        if request == "EMPTY_RACK":
            return "request_empty"
        if request == "FULL_RACK":
            return "return_full"
    if prefix in KNOWN_PREFIX_ACTIONS:
        return KNOWN_PREFIX_ACTIONS[prefix]
    raise ScanPreviewError("QR no configurado", "error")


def _row_payload(row, fields):
    if not row:
        return {}
    return {field: getattr(row, field, None) for field in fields}


def _area_payload(row: Optional[Area]) -> dict:
    return _row_payload(row, ("id", "code", "name"))


def _cell_payload(row: Optional[Location]) -> dict:
    if not row:
        return {}
    return {"id": row.id, "x": row.x, "y": row.y, "code": row.code, "area_id": row.area_id, "rack_id": row.rack_id}


def _rack_payload(row: Optional[Rack]) -> dict:
    return _row_payload(row, ("id", "code", "name", "status", "material_group_id"))


def _material_payload(row: Optional[MaterialGroup]) -> dict:
    return _row_payload(row, ("id", "code", "name"))


def _priority_text(value) -> str:
    try:
        priority = int(value or 0)
    except Exception:
        priority = 0
    if priority >= 2:
        return "urgente"
    if priority == 1:
        return "alta"
    return "normal"


def _priority_from_int(value) -> str:
    return _priority_text(value)


def _first_value(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _resolve_entities(db, station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], parsed: dict, action: Optional[str]) -> dict:
    material_id = _first_value(
        getattr(qr_rule, "material_group_id", None) if qr_rule else None,
        getattr(station, "default_material_group_id", None) if station else None,
    )
    rack_id = getattr(qr_rule, "rack_id", None) if qr_rule else None
    source_area_id = _first_value(
        getattr(qr_rule, "source_area_id", None) if qr_rule else None,
        getattr(station, "source_area_id", None) if station else None,
        getattr(station, "storage_area_id", None) if station and action == "store_rack" else None,
    )
    destination_area_id = _first_value(
        getattr(qr_rule, "destination_area_id", None) if qr_rule else None,
        getattr(station, "destination_area_id", None) if station else None,
        getattr(station, "empty_rack_area_id", None) if station and action == "request_empty" else None,
    )
    source_cell_id = _first_value(
        getattr(qr_rule, "source_cell_id", None) if qr_rule else None,
        getattr(station, "source_cell_id", None) if station else None,
    )
    destination_cell_id = _first_value(
        getattr(qr_rule, "destination_cell_id", None) if qr_rule else None,
        getattr(station, "destination_cell_id", None) if station else None,
    )

    if parsed.get("prefix") in {"RACK", "STORE", "MOVE"} and not rack_id:
        rack_code = parsed.get("rack_code") or parsed.get("code")
        if rack_code:
            rack = db.execute(select(Rack).where(Rack.code == rack_code)).scalar_one_or_none()
            rack_id = rack.id if rack else rack_id
    if parsed.get("prefix") == "MAT" and not material_id:
        material = db.execute(select(MaterialGroup).where(MaterialGroup.code == parsed.get("code"))).scalar_one_or_none()
        material_id = material.id if material else material_id
    if parsed.get("prefix") == "MOVE" and parsed.get("destination_code") and not destination_cell_id:
        cell = db.execute(select(Location).where(Location.code == parsed.get("destination_code"))).scalar_one_or_none()
        destination_cell_id = cell.id if cell else destination_cell_id

    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none() if material_id else None
    rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none() if rack_id else None
    source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none() if source_area_id else None
    destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none() if destination_area_id else None
    source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none() if source_cell_id else None
    destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none() if destination_cell_id else None

    if action == "fifo_request" and source_area and destination_area:
        selection = resolve_fifo_request(db, source_area.id, destination_area.id, material.id if material else None, _priority_text(getattr(qr_rule, "priority", None) if qr_rule else getattr(station, "priority", None)))
        rack = selection.rack
        material = selection.material_group
        source_cell = selection.source_cell
        destination_cell = selection.destination_cell
        source_area = selection.source_area
        destination_area = selection.destination_area
    elif action in {"point_to_area", "store_rack", "request_empty", "return_full"} and destination_area and not destination_cell:
        try:
            destination_cell = _find_destination_cell(db, destination_area)
        except HTTPException as exc:
            raise ScanPreviewError(str(exc.detail), "error")

    return {
        "material": material,
        "rack": rack,
        "source_area": source_area,
        "destination_area": destination_area,
        "source_cell": source_cell,
        "destination_cell": destination_cell,
    }


def _json_dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _save_scan_event(db, *, scanner_code, qr_value, created_by, station=None, qr_rule=None, parsed=None, action=None, entities=None, status="preview_ok", error_message=None, request_payload=None, result_payload=None, terminal=None, mode="preview"):
    entities = entities or {}
    event = ScanEvent(
        scanner_code=(scanner_code or "").strip() or None,
        scanner_station_id=getattr(station, "id", None),
        terminal_id=getattr(terminal, "id", None),
        qr_value=qr_value,
        qr_action_rule_id=getattr(qr_rule, "id", None),
        parsed_type=(parsed or {}).get("parsed_type") if parsed else None,
        resolved_action=action,
        rack_id=getattr(entities.get("rack"), "id", None),
        material_group_id=getattr(entities.get("material"), "id", None),
        source_cell_id=getattr(entities.get("source_cell"), "id", None),
        destination_cell_id=getattr(entities.get("destination_cell"), "id", None),
        source_area_id=getattr(entities.get("source_area"), "id", None),
        destination_area_id=getattr(entities.get("destination_area"), "id", None),
        movement_order_id=None,
        mode=(str(mode or "preview").strip().lower() or "preview"),
        status=status,
        error_message=error_message,
        request_json=_json_dumps(request_payload or {}),
        result_json=_json_dumps(result_payload or {}),
        created_by=(created_by or "").strip() or None,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _recent_duplicate_scan_event(db, scanner_code: str, qr_value: str):
    since = datetime.utcnow() - timedelta(seconds=SCAN_DEDUP_SECONDS)
    return (
        db.execute(
            select(ScanEvent)
            .where(ScanEvent.scanner_code == scanner_code)
            .where(ScanEvent.qr_value == qr_value)
            .where(ScanEvent.mode == "execute")
            .where(ScanEvent.status.in_(tuple(sorted(SCAN_EXECUTE_RECENT_STATUSES))))
            .where(ScanEvent.created_at >= since)
            .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
        )
        .scalars()
        .first()
    )


def _movement_order_payload(row) -> dict:
    if not row:
        return {}
    return {
        "id": row.id,
        "order_code": row.order_code,
        "status": row.status,
        "dispatch_status": row.dispatch_status,
        "rcs_status": row.rcs_status,
        "rcs_message": row.rcs_message,
        "remote_task_code": row.remote_task_code,
    }


def _selection_payload(selection) -> dict:
    if not selection:
        return {}
    return {
        "rack": _rack_payload(selection.rack),
        "material": _material_payload(selection.material_group),
        "source": {"area": _area_payload(selection.source_area), "cell": _cell_payload(selection.source_cell)},
        "destination": {"area": _area_payload(selection.destination_area), "cell": _cell_payload(selection.destination_cell)},
    }


def _save_execute_rejected_event(db, *, scanner_code, qr_value, created_by=None, station=None, qr_rule=None, parsed=None, action=None, terminal=None, message="Ejecucion rechazada", status="rejected"):
    result = {
        "ok": False,
        "mode": "execute",
        "status": status,
        "scanner": {"scanner_code": scanner_code},
        "scanner_code": scanner_code,
        "qr": {"qr_value": qr_value},
        "qr_value": qr_value,
        "action": action,
        "message": message,
    }
    if terminal:
        result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name}
        result["terminal_code"] = terminal.terminal_code
    event = _save_scan_event(
        db,
        scanner_code=scanner_code,
        qr_value=qr_value,
        created_by=created_by,
        station=station,
        qr_rule=qr_rule,
        parsed=parsed,
        action=action,
        entities={},
        status=status,
        error_message=message,
        request_payload={"scanner_code": scanner_code, "qr_value": qr_value, "mode": "execute", "terminal_code": getattr(terminal, "terminal_code", None)},
        result_payload=result,
        terminal=terminal,
        mode="execute",
    )
    result["scan_event_id"] = event.id
    return result


def preview_scan(db, scanner_code, qr_value, created_by=None, terminal=None):
    request_payload = {"scanner_code": scanner_code, "qr_value": qr_value, "created_by": created_by}
    normalized_qr = normalize_qr_value(qr_value)
    station = None
    qr_rule = None
    parsed = None
    action = None
    entities = {}
    try:
        if not normalized_qr:
            raise ScanPreviewError("qr_value requerido", "rejected")
        station = resolve_scanner_station(db, scanner_code)
        qr_rule = resolve_qr_action_rule(db, normalized_qr)
        parsed = parse_qr_value(normalized_qr)
        action = resolve_scan_action(db, station, qr_rule, parsed)
        entities = _resolve_entities(db, station, qr_rule, parsed, action)
        result = {
            "ok": True,
            "mode": "preview",
            "scanner": {"id": station.id, "scanner_code": station.scanner_code, "name": station.name},
            "qr": {
                "qr_value": normalized_qr,
                "qr_alias": getattr(qr_rule, "qr_alias", None) if qr_rule else None,
                "qr_type": getattr(qr_rule, "qr_type", None) if qr_rule else parsed.get("parsed_type"),
            },
            "parsed": parsed,
            "action": action,
            "material": _material_payload(entities.get("material")),
            "rack_selected": _rack_payload(entities.get("rack")),
            "source": {"area": _area_payload(entities.get("source_area")), "cell": _cell_payload(entities.get("source_cell"))},
            "destination": {"area": _area_payload(entities.get("destination_area")), "cell": _cell_payload(entities.get("destination_cell"))},
            "message": "Preview correcto. No se creo orden.",
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="preview_ok", request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        return result
    except ScanPreviewError as exc:
        result = {
            "ok": False,
            "mode": "preview",
            "scanner": {"scanner_code": str(scanner_code or "").strip()},
            "qr": {"qr_value": normalized_qr},
            "action": action,
            "message": exc.message,
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=exc.status, error_message=exc.message, request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        return result
    except HTTPException as exc:
        message = str(exc.detail)
        result = {"ok": False, "mode": "preview", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "qr": {"qr_value": normalized_qr}, "action": action, "message": message}
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="error", error_message=message, request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        return result


def execute_scan(db, scanner_code, qr_value, created_by=None, terminal=None, dispatch_func=None, rollback_func=None, audit_rack_func=None):
    request_payload = {"scanner_code": scanner_code, "qr_value": qr_value, "created_by": created_by, "mode": "execute"}
    normalized_qr = normalize_qr_value(qr_value)
    station = None
    qr_rule = None
    parsed = None
    action = None
    entities = {}
    created_order = None
    dispatch_payload = {}
    dispatch_status = "not_sent"
    rollback_result = None
    try:
        if not normalized_qr:
            raise ScanPreviewError("qr_value requerido", "rejected")
        station = resolve_scanner_station(db, scanner_code)
        if int(getattr(station, "allow_execute", 0) or 0) != 1:
            raise ScanPreviewError("El scanner no esta habilitado para ejecucion.", "rejected")
        qr_rule = resolve_qr_action_rule(db, normalized_qr)
        if not qr_rule:
            raise ScanPreviewError("QR no configurado", "error")
        parsed = parse_qr_value(normalized_qr)
        action = resolve_scan_action(db, station, qr_rule, parsed)
        if action != "fifo_request":
            raise ScanPreviewError("En esta fase solo esta habilitada la ejecucion QR para fifo_request.", "rejected")

        duplicate = _recent_duplicate_scan_event(db, station.scanner_code, normalized_qr)
        if duplicate:
            result = {
                "ok": False,
                "mode": "execute",
                "status": "duplicate",
                "scanner": {"id": station.id, "scanner_code": station.scanner_code, "name": station.name},
                "scanner_code": station.scanner_code,
                "qr": {"qr_value": normalized_qr, "qr_alias": getattr(qr_rule, "qr_alias", None), "qr_type": getattr(qr_rule, "qr_type", None)},
                "qr_value": normalized_qr,
                "action": action,
                "message": "Escaneo duplicado detectado. No se creo una segunda orden.",
                "existing_scan_event_id": duplicate.id,
                "existing_movement_order_id": duplicate.movement_order_id,
            }
            event = _save_scan_event(db, scanner_code=station.scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities={}, status="duplicate", error_message=result["message"], request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
            result["scan_event_id"] = event.id
            return result

        material_id = _first_value(getattr(qr_rule, "material_group_id", None), getattr(station, "default_material_group_id", None))
        if not material_id:
            raise ScanPreviewError("fifo_request requiere material_group_id configurado en QR o scanner.", "rejected")
        source_area_id = _first_value(getattr(qr_rule, "source_area_id", None), getattr(station, "source_area_id", None))
        destination_area_id = _first_value(getattr(qr_rule, "destination_area_id", None), getattr(station, "destination_area_id", None))
        if not source_area_id:
            raise ScanPreviewError("Scanner sin area origen configurada para FIFO.", "rejected")
        if not destination_area_id:
            raise ScanPreviewError("Scanner sin destino configurado para FIFO.", "rejected")

        priority = _priority_from_int(_first_value(getattr(qr_rule, "priority", None), getattr(station, "priority", None)))
        agv_code = _first_value(getattr(qr_rule, "agv_code", None), getattr(station, "agv_code", None))
        task_typ = _first_value(getattr(qr_rule, "task_typ", None), getattr(station, "task_typ", None))
        comment = f"QR execute {normalized_qr}"
        order, selection = execute_fifo_request(db, source_area_id, destination_area_id, material_id, priority, comment, created_by or "qr", agv_code, task_typ)
        created_order = order
        entities = {
            "material": selection.material_group,
            "rack": selection.rack,
            "source_area": selection.source_area,
            "destination_area": selection.destination_area,
            "source_cell": selection.source_cell,
            "destination_cell": selection.destination_cell,
        }
        if audit_rack_func:
            try:
                audit_rack_func(db, rack=selection.rack, previous_status="available", new_status=selection.rack.status, source="qr_scan", related_order_id=order.id, reason="qr_fifo_execute", actor=created_by or "qr", auto_commit=False)
                db.commit()
            except Exception:
                logger.exception("QR execute audit failed order_id=%s", order.id)

        if dispatch_func:
            dispatch_result = dispatch_func(db, order)
            dispatch_status = getattr(dispatch_result, "dispatch_status", None) or "unknown"
            dispatch_payload = {
                "dispatch_status": dispatch_status,
                "rcs_message": getattr(dispatch_result, "rcs_message", None),
                "remote_task_code": getattr(dispatch_result, "remote_task_code", None),
                "request_payload": getattr(dispatch_result, "request_payload", None),
                "response_payload": getattr(dispatch_result, "response_payload", None),
            }
            if dispatch_status != "success":
                message = getattr(dispatch_result, "rcs_message", None) or "El RCS rechazo la creacion de la tarea"
                if rollback_func:
                    rollback_result = rollback_func(db, order, message)
                    try:
                        db.refresh(order)
                    except Exception:
                        pass
                logger.error(
                    "QR_EXECUTE_DISPATCH_FAILED rollback_executed=%s movement_order_id=%s rack_id=%s scanner_code=%s terminal_code=%s qr_value=%s dispatch_status=%s rcs_message=%s rollback_result=%s",
                    bool(rollback_result),
                    getattr(order, "id", None),
                    getattr(order, "rack_id", None),
                    getattr(station, "scanner_code", scanner_code),
                    getattr(terminal, "terminal_code", None),
                    normalized_qr,
                    dispatch_status,
                    message,
                    rollback_result,
                )
                raise ScanPreviewError(f"El RCS no acepto la tarea: {message}", "error")
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one_or_none() or order
        result = {
            "ok": True,
            "mode": "execute",
            "status": "dispatched" if dispatch_status == "success" else "created",
            "scanner": {"id": station.id, "scanner_code": station.scanner_code, "name": station.name},
            "scanner_code": station.scanner_code,
            "qr": {"qr_value": normalized_qr, "qr_alias": getattr(qr_rule, "qr_alias", None), "qr_type": getattr(qr_rule, "qr_type", None)},
            "qr_value": normalized_qr,
            "parsed": parsed,
            "action": action,
            "material": _material_payload(selection.material_group),
            "rack_selected": _rack_payload(selection.rack),
            "source": {"area": _area_payload(selection.source_area), "cell": _cell_payload(selection.source_cell)},
            "destination": {"area": _area_payload(selection.destination_area), "cell": _cell_payload(selection.destination_cell)},
            "movement_order_id": order.id,
            "movement_order": _movement_order_payload(order),
            "dispatch": dispatch_payload,
            "dispatch_status": getattr(order, "dispatch_status", None),
            "rcs_status": getattr(order, "rcs_status", None),
            "message": "Orden FIFO creada y enviada al RCS." if dispatch_status == "success" else "Orden FIFO creada.",
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            result["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=station.scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=result["status"], request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
        event.movement_order_id = order.id
        db.add(event)
        db.commit()
        db.refresh(event)
        result["scan_event_id"] = event.id
        return result
    except ScanPreviewError as exc:
        result = {
            "ok": False,
            "mode": "execute",
            "status": exc.status,
            "scanner": {"scanner_code": str(scanner_code or "").strip()},
            "scanner_code": str(scanner_code or "").strip(),
            "qr": {"qr_value": normalized_qr},
            "qr_value": normalized_qr,
            "action": action,
            "message": exc.message,
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name}
            result["terminal_code"] = terminal.terminal_code
        if created_order:
            try:
                db.refresh(created_order)
            except Exception:
                pass
            result["movement_order_id"] = created_order.id
            result["movement_order"] = _movement_order_payload(created_order)
            result["dispatch"] = dispatch_payload
            result["dispatch_status"] = getattr(created_order, "dispatch_status", dispatch_status)
            result["rcs_status"] = getattr(created_order, "rcs_status", None)
            result["rollback"] = rollback_result or {"executed": False}
            logger.error(
                "QR_EXECUTE_ERROR movement_order_id=%s rack_id=%s scanner_code=%s terminal_code=%s qr_value=%s dispatch_status=%s rcs_message=%s rollback_executed=%s rollback_result=%s",
                created_order.id,
                getattr(created_order, "rack_id", None),
                getattr(station, "scanner_code", scanner_code) if station else scanner_code,
                getattr(terminal, "terminal_code", None),
                normalized_qr,
                result["dispatch_status"],
                getattr(created_order, "rcs_message", None) or exc.message,
                bool(rollback_result),
                rollback_result,
            )
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=exc.status, error_message=exc.message, request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
        if created_order:
            event.movement_order_id = created_order.id
            db.add(event)
            db.commit()
            db.refresh(event)
        result["scan_event_id"] = event.id
        return result
    except HTTPException as exc:
        message = str(exc.detail)
        result = {"ok": False, "mode": "execute", "status": "error", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "scanner_code": str(scanner_code or "").strip(), "qr": {"qr_value": normalized_qr}, "qr_value": normalized_qr, "action": action, "message": message}
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name}
            result["terminal_code"] = terminal.terminal_code
        if created_order:
            try:
                db.refresh(created_order)
            except Exception:
                pass
            result["movement_order_id"] = created_order.id
            result["movement_order"] = _movement_order_payload(created_order)
            result["dispatch"] = dispatch_payload
            result["dispatch_status"] = getattr(created_order, "dispatch_status", dispatch_status)
            result["rcs_status"] = getattr(created_order, "rcs_status", None)
            result["rollback"] = rollback_result or {"executed": False}
            logger.error(
                "QR_EXECUTE_HTTP_ERROR movement_order_id=%s rack_id=%s scanner_code=%s terminal_code=%s qr_value=%s dispatch_status=%s rcs_message=%s rollback_executed=%s rollback_result=%s",
                created_order.id,
                getattr(created_order, "rack_id", None),
                getattr(station, "scanner_code", scanner_code) if station else scanner_code,
                getattr(terminal, "terminal_code", None),
                normalized_qr,
                result["dispatch_status"],
                getattr(created_order, "rcs_message", None) or message,
                bool(rollback_result),
                rollback_result,
            )
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="error", error_message=message, request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
        if created_order:
            event.movement_order_id = created_order.id
            db.add(event)
            db.commit()
            db.refresh(event)
        result["scan_event_id"] = event.id
        return result


def terminal_preview_scan(db, terminal_code, qr_value, mode="preview", api_key=None, client_ip=None, dispatch_func=None, rollback_func=None, audit_rack_func=None):
    requested_mode = str(mode or "preview").strip().lower() or "preview"
    if requested_mode not in {"preview", "execute"}:
        raise ScanPreviewError("mode invalido. Solo preview esta habilitado.", "rejected")
    terminal, station = resolve_scan_terminal(db, terminal_code, api_key)
    terminal.last_seen_at = datetime.utcnow()
    terminal.last_ip = (str(client_ip or "").strip() or None)
    terminal.updated_at = datetime.utcnow()
    db.add(terminal)
    db.commit()
    db.refresh(terminal)
    if requested_mode == "execute":
        if str(terminal.mode or "preview").strip().lower() != "execute" or int(terminal.allow_execute or 0) != 1:
            normalized_qr = normalize_qr_value(qr_value)
            return _save_execute_rejected_event(db, scanner_code=station.scanner_code, qr_value=normalized_qr, created_by=terminal.terminal_code, station=station, terminal=terminal, message="El terminal no esta habilitado para ejecucion.")
        result = execute_scan(db, station.scanner_code, qr_value, created_by=terminal.terminal_code, terminal=terminal, dispatch_func=dispatch_func, rollback_func=rollback_func, audit_rack_func=audit_rack_func)
    else:
        result = preview_scan(db, station.scanner_code, qr_value, created_by=terminal.terminal_code, terminal=terminal)
    result["terminal_code"] = terminal.terminal_code
    result["scanner_code"] = station.scanner_code
    result["qr_value"] = normalize_qr_value(qr_value)
    return result
