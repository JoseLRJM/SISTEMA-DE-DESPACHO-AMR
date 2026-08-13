from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from fifo_chain_service import (
    resolve_destination_cell_for_step,
    resolve_fifo_request_by_material_any_area,
    resolve_fifo_request_by_material_selected_areas,
)
from fifo_service import ACTIVE_RACK_STATUSES, FifoSelection, _find_destination_cell, _find_fifo_candidate, _normalize_rack_status, execute_fifo_request, execute_fifo_request_any_available, resolve_fifo_request, resolve_fifo_request_any_available
from logging_config import get_logger
from models import Area, Location, MaterialGroup, MovementOrder, QrActionRule, QrTransitionRule, Rack, ScanEvent, ScannerStation, ScanTerminal, apply_rack_reservation_status
from qr_transition_service import transition_match_metadata, transition_match_tier, transition_rank_key, transition_selection_reason, transition_sort_key

logger = get_logger("app.scanner")
SCAN_DEDUP_SECONDS = 7
SCAN_EXECUTE_RECENT_STATUSES = {"success", "created", "dispatched", "pending"}

QR_ERROR_PATTERNS = (
    ("parsing the task template failed", "RCS_TEMPLATE_PARSE_ERROR"),
    ("incorrect station collection quantity", "RCS_STATION_QUANTITY_ERROR"),
    ("timeout", "RCS_CONNECTION_ERROR"),
    ("scanner no encontrado", "SCANNER_NOT_FOUND"),
    ("scanner no configurado", "SCANNER_NOT_FOUND"),
    ("scanner inactivo", "SCANNER_INACTIVE"),
    ("qr no configurado", "QR_NOT_CONFIGURED"),
    ("no configurado", "QR_NOT_CONFIGURED"),
    ("terminal no esta habilitado", "TERMINAL_NOT_ALLOWED"),
    ("terminal no está habilitado", "TERMINAL_NOT_ALLOWED"),
    ("no hay rack", "NO_RACK_AVAILABLE"),
    ("no hay espacio", "NO_DESTINATION_AVAILABLE"),
    ("destino", "NO_DESTINATION_AVAILABLE"),
)


def _new_qr_execution_id() -> str:
    return f"QR-{datetime.utcnow():%Y%m%d-%H%M%S}-{uuid4().hex[:6].upper()}"


def _qr_error_code(message, fallback="UNEXPECTED_QR_ERROR") -> str:
    normalized = str(message or "").strip().lower()
    for fragment, code in QR_ERROR_PATTERNS:
        if fragment in normalized:
            return code
    return fallback


def _redact_qr_value(value, key: str = ""):
    sensitive = ("password", "token", "secret", "api_key", "authorization")
    if any(fragment in str(key or "").lower() for fragment in sensitive):
        return "[REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {item_key: _redact_qr_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_qr_value(item) for item in value]
    return value


def _qr_trace(event: str, execution_id: str, **fields) -> None:
    safe = {"event": event, "execution_id": execution_id}
    for key, value in fields.items():
        if value is None:
            continue
        redacted = _redact_qr_value(value, key)
        if isinstance(redacted, str) and len(redacted) > 512:
            safe[key] = redacted[:509] + "..."
        else:
            safe[key] = redacted
    logger.info("%s %s", event, json.dumps(safe, ensure_ascii=False, default=str, separators=(",", ":")))


class ScanPreviewError(Exception):
    def __init__(self, message: str, status: str = "error", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = dict(details or {})


def _scan_preview_error_from_http(exc: HTTPException, *, step: Optional[int] = None, destination_label: Optional[str] = None) -> ScanPreviewError:
    if isinstance(exc.detail, dict):
        details = dict(exc.detail)
        if step is not None:
            details["step"] = step
            area_name = details.get("destination_area_name") or "el área destino"
            details["operator_message"] = f"Tramo {step}: no hay espacio libre en {area_name}."
        if destination_label:
            details["destination_label"] = destination_label
        return ScanPreviewError(details.get("operator_message") or details.get("technical_message") or "No se puede ejecutar.", "error", details)
    return ScanPreviewError(str(exc.detail), "error")


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
        raise ScanPreviewError("El área de devolución configurada no existe.", "error")
    matter_area = str(area.matter_area or "").strip()
    if not matter_area:
        raise ScanPreviewError("El área de devolución configurada no tiene Matter Area.", "rejected")
    return {
        "mode": "configured_area",
        "return_area_id": area.id,
        "return_area_name": area.name,
        "matter_area": matter_area,
        "source_cell_id": None,
        "source_cell_code": None,
        "area": area,
    }


def _cancel_return_mode_value(scanner) -> str:
    mode = str(getattr(scanner, "cancel_return_mode", None) or "").strip().lower()
    if not mode:
        return "configured_area"
    if mode not in {"configured_area", "current_step_source"}:
        return "configured_area"
    return mode


def resolve_cancel_return_target(db, scanner, movement_order: Optional[MovementOrder]):
    mode = _cancel_return_mode_value(scanner)
    if mode == "configured_area":
        return resolve_scanner_cancel_return_area(db, scanner)
    if movement_order is None:
        raise ScanPreviewError("La orden no existe para calcular el Matter Area automáticamente.", "rejected")
    source_cell_id = getattr(movement_order, "source_cell_id", None)
    if not source_cell_id:
        raise ScanPreviewError("La orden no tiene celda origen para calcular el Matter Area automáticamente.", "rejected")
    source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
    if not source_cell:
        raise ScanPreviewError("No se encontró la celda origen de la orden.", "error")
    source_area_id = getattr(source_cell, "area_id", None)
    if not source_area_id:
        raise ScanPreviewError("La celda origen no tiene área asociada.", "rejected")
    source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none()
    if not source_area:
        raise ScanPreviewError("No se encontró el área origen de la orden.", "error")
    matter_area = str(source_area.matter_area or "").strip()
    if not matter_area:
        raise ScanPreviewError("El área origen del tramo no tiene Matter Area configurada.", "rejected")
    return {
        "mode": "current_step_source",
        "return_area_id": source_area.id,
        "return_area_name": source_area.name,
        "matter_area": matter_area,
        "source_cell_id": source_cell.id,
        "source_cell_code": source_cell.code,
        "source_area_id": source_area.id,
        "source_area_name": source_area.name,
        "area": source_area,
        "source_cell": source_cell,
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


def _allow_same_cell_move_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> bool:
    if qr_rule is not None:
        return bool(getattr(qr_rule, "allow_same_cell_move", False) or False)
    return bool(getattr(station, "allow_same_cell_move", False) or False)


def _operator_entity_location(area: Optional[Area], cell: Optional[Location]) -> dict:
    return {
        "area_id": getattr(area, "id", None),
        "area_code": getattr(area, "code", None),
        "area_name": getattr(area, "name", None),
        "cell_id": getattr(cell, "id", None),
        "cell_code": getattr(cell, "code", None),
    }


def _area_payload_from_row(area: Optional[Area]) -> Optional[dict]:
    if not area:
        return None
    return {"id": area.id, "code": area.code, "name": area.name}


def _area_payloads_by_ids(db, area_ids) -> list[dict]:
    ids = []
    for raw in area_ids or []:
        try:
            value = int(raw)
        except Exception:
            continue
        if value not in ids:
            ids.append(value)
    if not ids:
        return []
    rows = db.execute(select(Area).where(Area.id.in_(ids))).scalars().all()
    by_id = {row.id: row for row in rows}
    return [
        {"id": area_id, "code": getattr(by_id.get(area_id), "code", None), "name": getattr(by_id.get(area_id), "name", None)}
        for area_id in ids
        if by_id.get(area_id)
    ]


def _area_names_for_payloads(areas: list[dict]) -> list[str]:
    names = []
    for area in areas or []:
        label = area.get("name") or area.get("code") or area.get("id")
        if label not in (None, ""):
            names.append(str(label))
    return names


def _join_labels(labels, fallback="-") -> str:
    clean = [str(item).strip() for item in labels or [] if str(item or "").strip()]
    return ", ".join(clean) if clean else fallback


def _build_operator_diagnosis(db, *, message: str, details: Optional[dict], qr_value: str, station=None, qr_rule=None, entities=None) -> dict:
    details = dict(details or {})
    entities = entities or {}
    message_text = str(details.get("operator_message") or message or "No fue posible procesar la lectura.")
    lower_message = message_text.lower()
    step = int(details.get("step") or 1)
    total_steps = int(_first_value(getattr(qr_rule, "fifo_chain_total_steps", None) if qr_rule else None, getattr(station, "fifo_chain_total_steps", None) if station else None, 2) or 2)

    if step == 1:
        material_id = _first_value(getattr(qr_rule, "fifo_chain_step1_material_group_id", None) if qr_rule else None, getattr(station, "fifo_chain_step1_material_group_id", None) if station else None, getattr(qr_rule, "material_group_id", None) if qr_rule else None, getattr(station, "default_material_group_id", None) if station else None)
        source_area_id = _first_value(getattr(qr_rule, "source_area_id", None) if qr_rule else None, getattr(station, "source_area_id", None) if station else None)
        source_cell_id = _first_value(getattr(qr_rule, "source_cell_id", None) if qr_rule else None, getattr(station, "source_cell_id", None) if station else None)
        destination_area_id = _first_value(getattr(qr_rule, "destination_area_id", None) if qr_rule else None, getattr(station, "destination_area_id", None) if station else None)
        destination_cell_id = _first_value(getattr(qr_rule, "destination_cell_id", None) if qr_rule else None, getattr(station, "destination_cell_id", None) if station else None)
    elif step == 2:
        material_id = _first_value(getattr(qr_rule, "fifo_chain_step2_material_group_id", None) if qr_rule else None, getattr(station, "fifo_chain_step2_material_group_id", None) if station else None, getattr(qr_rule, "material_group_id", None) if qr_rule else None, getattr(station, "default_material_group_id", None) if station else None)
        source_area_id = _first_value(getattr(qr_rule, "second_source_area_id", None) if qr_rule else None, getattr(station, "second_source_area_id", None) if station else None)
        source_cell_id = _first_value(getattr(qr_rule, "second_source_cell_id", None) if qr_rule else None, getattr(station, "second_source_cell_id", None) if station else None)
        destination_area_id = _first_value(getattr(qr_rule, "second_destination_area_id", None) if qr_rule else None, getattr(station, "second_destination_area_id", None) if station else None)
        destination_cell_id = _first_value(getattr(qr_rule, "second_destination_cell_id", None) if qr_rule else None, getattr(station, "second_destination_cell_id", None) if station else None)
    else:
        material_id = _first_value(getattr(qr_rule, f"fifo_chain_step{step}_material_group_id", None) if qr_rule else None, getattr(station, f"fifo_chain_step{step}_material_group_id", None) if station else None)
        source_area_id = _first_value(getattr(qr_rule, f"fifo_chain_step{step}_source_area_id", None) if qr_rule else None, getattr(station, f"fifo_chain_step{step}_source_area_id", None) if station else None)
        source_cell_id = _first_value(getattr(qr_rule, f"fifo_chain_step{step}_source_cell_id", None) if qr_rule else None, getattr(station, f"fifo_chain_step{step}_source_cell_id", None) if station else None)
        destination_area_id = _first_value(getattr(qr_rule, f"fifo_chain_step{step}_destination_area_id", None) if qr_rule else None, getattr(station, f"fifo_chain_step{step}_destination_area_id", None) if station else None)
        destination_cell_id = _first_value(getattr(qr_rule, f"fifo_chain_step{step}_destination_cell_id", None) if qr_rule else None, getattr(station, f"fifo_chain_step{step}_destination_cell_id", None) if station else None)

    material = entities.get(f"fifo_chain_step{step}_material") or entities.get("material")
    task_typ = entities.get(f"fifo_chain_step{step}_task_typ") or _fifo_chain_step_task_typ_value(station, qr_rule, step)
    if not material and material_id:
        material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none()
    source_area = entities.get("source_area" if step == 1 else "second_source_area" if step == 2 else f"fifo_chain_step{step}_source_area")
    source_cell = entities.get("source_cell" if step == 1 else "second_source_cell" if step == 2 else f"fifo_chain_step{step}_source_cell")
    destination_area = entities.get("destination_area" if step == 1 else "second_destination_area" if step == 2 else f"fifo_chain_step{step}_destination_area")
    destination_cell = entities.get("destination_cell" if step == 1 else "second_destination_cell" if step == 2 else f"fifo_chain_step{step}_destination_cell")
    if not source_area and source_area_id:
        source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none()
    if not source_cell and source_cell_id:
        source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
    if not destination_area and destination_area_id:
        destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none()
    if not destination_cell and destination_cell_id:
        destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none()

    error_code = str(details.get("error_code") or "").strip()
    if not error_code:
        if "areas seleccionadas" in lower_message and ("no se encontro material" in lower_message or "no hay rack" in lower_message):
            error_code = "source_material_not_available_in_selected_areas"
        elif "no hay espacio disponible en las areas destino seleccionadas" in lower_message:
            error_code = "destination_space_not_available_in_selected_areas"
        elif "no hay espacio disponible en ninguna area operativa" in lower_message:
            error_code = "destination_space_not_available_any_area"
        elif "no se configuraron areas validas" in lower_message:
            error_code = "invalid_selected_area_list"
        elif ("material" in lower_message and ("no hay" in lower_message or "requiere" in lower_message or "requerido" in lower_message)) or ("no hay racks disponibles" in lower_message and material):
            error_code = "required_step_material_missing" if "requiere" in lower_message or "requerido" in lower_message else "source_material_not_available"
        elif "origen" in lower_message and ("no esta habilitado" in lower_message or "no está habilitado" in lower_message or "no configurado" in lower_message):
            error_code = "source_area_no_operational_locations"
        elif "destino" in lower_message and ("no tiene ubicaciones" in lower_message or "no esta habilitado" in lower_message or "no está habilitado" in lower_message):
            error_code = "destination_area_no_operational_locations"
        else:
            error_code = "operational_validation_error"
    if error_code == "fifo_chain_projected_source_unavailable":
        error_code = "source_material_not_available"
    if error_code == "fifo_chain_projected_destination_occupied":
        error_code = "destination_occupied_not_released"

    checked_source_locations = list(details.get("checked_source_locations") or [])[:20]
    same_material_elsewhere = list(details.get("same_material_elsewhere") or [])[:10]
    if not checked_source_locations and error_code in {"source_material_not_available", "source_area_no_operational_locations"} and source_area:
        source_locations = db.execute(select(Location).where(Location.area_id == source_area.id).order_by(Location.y.asc(), Location.x.asc()).limit(20)).scalars().all()
        rack_ids = {cell.rack_id for cell in source_locations if cell.rack_id is not None}
        source_racks = db.execute(select(Rack).where(Rack.id.in_(rack_ids))).scalars().all() if rack_ids else []
        rack_by_id = {rack.id: rack for rack in source_racks}
        material_ids = {rack.material_group_id for rack in source_racks if rack.material_group_id is not None}
        materials = db.execute(select(MaterialGroup).where(MaterialGroup.id.in_(material_ids))).scalars().all() if material_ids else []
        material_by_id = {row.id: row for row in materials}
        reserved_rack_ids = {row[0] for row in db.execute(select(MovementOrder.rack_id).where(MovementOrder.status.in_(("pending_dispatch", "dispatched", "in_progress")))).all() if row and row[0] is not None}
        for cell in source_locations:
            rack = rack_by_id.get(cell.rack_id)
            rack_material = material_by_id.get(getattr(rack, "material_group_id", None))
            if int(cell.enabled or 0) != 1:
                status, reason = "disabled", "Ubicación deshabilitada"
            elif int(cell.is_visible or 0) != 1:
                status, reason = "hidden", "Ubicación oculta"
            elif not str(cell.code or "").strip():
                status, reason = "invalid", "Ubicación sin código operativo"
            elif not rack:
                status, reason = "empty", "Ubicación vacía"
            elif rack.id in reserved_rack_ids:
                status, reason = "reserved", "Rack reservado por orden activa"
            elif material and rack.material_group_id == material.id:
                status, reason = "occupied_matching_material", f"Ocupada por rack {rack.code} con material {getattr(rack_material, 'name', None) or getattr(rack_material, 'code', None) or material.name}"
            else:
                status, reason = "occupied_other_material", f"Ocupada por rack {rack.code} con material {getattr(rack_material, 'name', None) or getattr(rack_material, 'code', None) or 'diferente'}"
            checked_source_locations.append({"location_id": cell.id, "location_code": cell.code, "status": status, "rack_id": getattr(rack, "id", None), "rack_code": getattr(rack, "code", None), "rack_material_id": getattr(rack_material, "id", None), "rack_material_name": getattr(rack_material, "name", None), "reason": reason})
        if material and not same_material_elsewhere:
            elsewhere = db.execute(select(Location, Rack, Area).join(Rack, Location.rack_id == Rack.id).join(Area, Location.area_id == Area.id).where(Rack.material_group_id == material.id, Location.area_id != source_area.id, Location.enabled == 1, Location.is_visible == 1).limit(10)).all()
            same_material_elsewhere = [{"rack_id": rack.id, "rack_code": rack.code, "area_id": area.id, "area_name": area.name, "location_id": cell.id, "location_code": cell.code} for cell, rack, area in elsewhere if str(rack.status or "").strip().lower() in {"available", "disponible"}]

    checked_destination = list(details.get("checked_destination_locations") or [])[:20]
    projection_notes = list(details.get("projection_notes") or [])
    if details.get("destination_released_by_previous_step"):
        projection_notes.append({"step": step, "message": details.get("message"), "destination_cell_code": getattr(destination_cell, "code", None), "released_by_step": details.get("destination_released_by_previous_step")})

    source_mode = details.get("source_mode") or entities.get(f"fifo_chain_step{step}_source_mode")
    destination_mode = details.get("destination_mode") or entities.get(f"fifo_chain_step{step}_destination_mode")
    selected_source_areas = _area_payloads_by_ids(db, details.get("source_area_ids") or entities.get(f"fifo_chain_step{step}_source_area_ids") or [])
    selected_destination_areas = _area_payloads_by_ids(db, details.get("destination_area_ids") or entities.get(f"fifo_chain_step{step}_destination_area_ids") or [])
    material_label = details.get("material_required") or getattr(material, "code", None) or getattr(material, "name", None) or "requerido"
    source_label = getattr(source_area, "name", None) or getattr(source_cell, "code", None) or "configurado"
    destination_label = details.get("destination_label") or getattr(destination_cell, "code", None) or getattr(destination_area, "name", None) or "configurado"
    failure_summary = details.get("failure_summary") or message_text
    blocking_reason = details.get("blocking_reason") or error_code
    if error_code == "same_source_destination_cell":
        message_text = details.get("operator_message") or "La celda origen y destino no pueden ser la misma."
        suggested_action = "Configura una celda destino diferente a la celda origen."
    elif error_code == "same_area_no_free_destination_cell":
        message_text = details.get("operator_message") or "El area origen y destino son iguales, pero no hay una celda destino libre diferente."
        suggested_action = "Libera otra celda dentro del area o cambia el destino configurado."
    elif details.get("transition_conflict"):
        message_text = details.get("operator_message") or message_text
        suggested_action = details.get("suggested_action") or "Corrige la regla de transicion, cambia el material requerido del tramo o desactiva esa transicion para este flujo si no debe aplicar."
    elif blocking_reason == "explicit_source_cell_wrong_material":
        message_text = details.get("operator_message") or message_text
        suggested_action = "Revisa la celda origen configurada o corrige el material requerido del tramo."
    elif error_code == "source_material_not_available":
        if source_mode == "any_area_by_material":
            message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay ningún rack disponible con material {material_label} en áreas operativas."
        else:
            message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay rack disponible con material {material_label} en el área {source_label}."
        suggested_action = "Revisa que el origen del tramo sea el área donde realmente está el rack con ese material, o mueve un rack con ese material al origen."
    elif error_code == "source_material_not_available_in_selected_areas":
        selected_names = _join_labels(_area_names_for_payloads(selected_source_areas), "áreas seleccionadas")
        message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay rack disponible con material {material_label} dentro de las áreas seleccionadas: {selected_names}."
        suggested_action = "Revisa las áreas seleccionadas, el material requerido y la disponibilidad de los racks."
    elif error_code == "destination_space_not_available_in_selected_areas":
        selected_names = _join_labels(_area_names_for_payloads(selected_destination_areas), "áreas seleccionadas")
        message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay espacio disponible dentro de las áreas destino seleccionadas: {selected_names}."
        suggested_action = "Libera una ubicación o revisa las áreas destino seleccionadas."
    elif error_code == "destination_space_not_available_any_area":
        message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay espacio disponible en ninguna área operativa."
        suggested_action = "Libera una ubicación operativa y vuelve a intentar."
    elif error_code == "invalid_selected_area_list":
        message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no se configuraron áreas válidas para buscar el material."
        suggested_action = "Configura al menos un área activa para este tramo."
    elif error_code == "destination_area_full":
        destination_label = getattr(destination_cell, "code", None) or getattr(destination_area, "name", None) or details.get("destination_area_name") or "configurado"
        message_text = f"No hay espacio libre en el destino {destination_label}."
        suggested_action = "Libera una ubicación destino o cambia el destino configurado en el QR."
    elif error_code == "destination_occupied_not_released":
        message_text = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: la celda destino {destination_label} está ocupada y no será liberada por un tramo anterior."
        suggested_action = "Libera esa ubicación o cambia el destino del tramo."
    elif error_code == "source_area_no_operational_locations":
        suggested_action = "Configura ubicaciones visibles y habilitadas para el área origen."
    elif error_code == "destination_area_no_operational_locations":
        suggested_action = "Configura ubicaciones visibles y habilitadas para el área destino."
    elif error_code == "required_step_material_missing":
        suggested_action = "Configura el material requerido para este tramo en el QR o scanner."
    else:
        suggested_action = details.get("suggested_action") or "Revisa la configuración operativa del QR y vuelve a intentar."

    source_payload = _operator_entity_location(source_area, source_cell)
    source_payload.update({
        "mode": source_mode,
        "selected_areas": selected_source_areas,
    })
    destination_payload = _operator_entity_location(destination_area, destination_cell)
    destination_payload.update({
        "mode": destination_mode,
        "selected_areas": selected_destination_areas,
    })
    return {
        "error_code": error_code,
        "title": "No se puede ejecutar la cadena",
        "operator_message": message_text,
        "suggested_action": suggested_action,
        "step": step,
        "total_steps": total_steps,
        "qr_value": qr_value,
        "material": _material_payload(material),
        "task_typ": task_typ,
        "source": source_payload,
        "destination": destination_payload,
        "checked_source_locations": checked_source_locations,
        "checked_destination_locations": checked_destination,
        "same_material_elsewhere": same_material_elsewhere,
        "projection_notes": projection_notes,
        "failure_summary": failure_summary,
        "blocking_reason": blocking_reason,
        "transition_conflict": bool(details.get("transition_conflict")),
        "transition_conflict_step": details.get("transition_conflict_step"),
        "transition_rule_id": details.get("transition_rule_id"),
        "transition_rule_name": details.get("transition_rule_name"),
        "expected_material": details.get("expected_material"),
        "actual_projected_material": details.get("actual_projected_material"),
        "rack_code": details.get("rack_code"),
        "cell_code": details.get("cell_code"),
        "transition_conflict_message": details.get("transition_conflict_message"),
    }


def _route_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "route_mode", None) if qr_rule else None,
        getattr(station, "route_mode", None) if station else None,
        "simple_area",
    )
    value = str(value or "simple_area").strip() or "simple_area"
    if value == "trmx_doble":
        value = "fifo_chain"
    if value not in {"simple_area", "double_area", "fifo_chain"}:
        raise ScanPreviewError(f"route_mode invalido: {value}", "rejected")
    return value


def _fifo_material_policy_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_material_policy", None) if qr_rule else None,
        getattr(station, "fifo_material_policy", None) if station else None,
        "specific_material",
    )
    value = str(value or "specific_material").strip() or "specific_material"
    if value not in {"specific_material", "any_available_from_source"}:
        raise ScanPreviewError(f"fifo_material_policy invalido: {value}", "rejected")
    return value


def _fifo_chain_step2_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step2_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step2_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material", "selected_areas_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step2_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step3_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step3_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step3_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material", "selected_areas_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step3_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step4_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step4_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step4_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material", "selected_areas_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step4_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step5_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step5_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step5_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material", "selected_areas_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step5_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_total_steps_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> int:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_total_steps", None) if qr_rule else None,
        getattr(station, "fifo_chain_total_steps", None) if station else None,
        2,
    )
    try:
        total = int(value)
    except (TypeError, ValueError):
        total = 2
    return total if total in {2, 3, 4, 5} else 2


def _fifo_chain_step1_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step1_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step1_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material", "selected_areas_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step1_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step1_material_group_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]):
    return _first_value(
        getattr(qr_rule, "fifo_chain_step1_material_group_id", None) if qr_rule else None,
        getattr(station, "fifo_chain_step1_material_group_id", None) if station else None,
    )


def _fifo_chain_step2_material_group_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]):
    return _first_value(
        getattr(qr_rule, "fifo_chain_step2_material_group_id", None) if qr_rule else None,
        getattr(station, "fifo_chain_step2_material_group_id", None) if station else None,
    )


def _fifo_chain_step3_material_group_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]):
    return _first_value(
        getattr(qr_rule, "fifo_chain_step3_material_group_id", None) if qr_rule else None,
        getattr(station, "fifo_chain_step3_material_group_id", None) if station else None,
    )


def _fifo_chain_step4_material_group_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]):
    return _first_value(
        getattr(qr_rule, "fifo_chain_step4_material_group_id", None) if qr_rule else None,
        getattr(station, "fifo_chain_step4_material_group_id", None) if station else None,
    )


def _fifo_chain_step5_material_group_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]):
    return _first_value(
        getattr(qr_rule, "fifo_chain_step5_material_group_id", None) if qr_rule else None,
        getattr(station, "fifo_chain_step5_material_group_id", None) if station else None,
    )


def _clean_task_typ_value(value) -> Optional[str]:
    text = str(value or "").strip()
    return text[:64] or None


def _fifo_chain_step_task_typ_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], step: int):
    return _clean_task_typ_value(_first_value(
        getattr(qr_rule, f"fifo_chain_step{step}_task_typ", None) if qr_rule else None,
        getattr(station, f"fifo_chain_step{step}_task_typ", None) if station else None,
        getattr(qr_rule, "task_typ", None) if qr_rule else None,
        getattr(station, "task_typ", None) if station else None,
    ))


def _fifo_chain_step_destination_mode_value(station, qr_rule, step: int) -> str:
    field = f"fifo_chain_step{step}_destination_mode"
    value = _first_value(
        getattr(qr_rule, field, None) if qr_rule else None,
        getattr(station, field, None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_with_space", "selected_areas_with_space"}:
        raise ScanPreviewError(f"{field} invalido: {value}", "rejected")
    return value


def _fifo_chain_step_area_ids_value(station, qr_rule, step: int, kind: str) -> list[int]:
    field = f"fifo_chain_step{step}_{kind}_area_ids_json"
    value = _first_value(
        getattr(qr_rule, field, None) if qr_rule else None,
        getattr(station, field, None) if station else None,
    )
    if value in (None, "", []):
        return []
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            raise ScanPreviewError("No se configuraron areas validas para esta seleccion.", "rejected", {"step": step, "error_code": "invalid_selected_area_list"})
    if not isinstance(raw, list):
        raise ScanPreviewError("No se configuraron areas validas para esta seleccion.", "rejected", {"step": step, "error_code": "invalid_selected_area_list"})
    result = []
    for item in raw:
        try:
            area_id = int(item)
        except Exception:
            area_id = 0
        if area_id <= 0:
            raise ScanPreviewError("No se configuraron areas validas para esta seleccion.", "rejected", {"step": step, "error_code": "invalid_selected_area_list"})
        if area_id not in result:
            result.append(area_id)
    return result


def _fifo_chain_step_flexible_config(station, qr_rule, step: int) -> dict:
    return {
        "source_area_ids_json": _fifo_chain_step_area_ids_value(station, qr_rule, step, "source"),
        "destination_mode": _fifo_chain_step_destination_mode_value(station, qr_rule, step),
        "destination_area_ids_json": _fifo_chain_step_area_ids_value(station, qr_rule, step, "destination"),
    }


def _fifo_chain_step3_area_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], field: str):
    return _first_value(
        getattr(qr_rule, field, None) if qr_rule else None,
        getattr(station, field, None) if station else None,
    )


def _fifo_chain_step3_cell_id_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], field: str):
    return _first_value(
        getattr(qr_rule, field, None) if qr_rule else None,
        getattr(station, field, None) if station else None,
    )


def _cell_position_code(cell: Optional[Location]) -> str:
    return str(getattr(cell, "code", None) or "").strip()


def _cell_label(cell: Optional[Location], area: Optional[Area] = None) -> str:
    code = _cell_position_code(cell)
    area_text = " - ".join(part for part in (str(getattr(area, "code", "") or "").strip(), str(getattr(area, "name", "") or "").strip()) if part)
    return " - ".join(part for part in (code, area_text) if part)


def _ensure_operational_route_cell(cell: Optional[Location], label: str) -> Location:
    if not cell:
        raise ScanPreviewError(f"{label} no configurado.", "rejected")
    if int(getattr(cell, "enabled", 0) or 0) != 1 or int(getattr(cell, "is_visible", 0) or 0) != 1:
        raise ScanPreviewError(f"{label} no esta habilitado/visible.", "rejected")
    if not _cell_position_code(cell):
        raise ScanPreviewError(f"{label} no tiene codigo RCS/positionCode configurado.", "rejected")
    return cell


def _area_for_cell(db, cell: Optional[Location], fallback_area: Optional[Area] = None) -> Optional[Area]:
    if fallback_area:
        return fallback_area
    area_id = getattr(cell, "area_id", None)
    return db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none() if area_id else None


def _resolve_route_cell_from_config(
    db,
    *,
    cell_id,
    area_id,
    label: str,
    destination: bool = False,
    allow_occupied_destination: bool = False,
) -> tuple[Optional[Area], Location]:
    area = db.execute(select(Area).where(Area.id == area_id)).scalar_one_or_none() if area_id else None
    if cell_id:
        cell = db.execute(select(Location).where(Location.id == cell_id)).scalar_one_or_none()
        cell = _ensure_operational_route_cell(cell, label)
        if destination and not allow_occupied_destination and getattr(cell, "rack_id", None) is not None:
            raise ScanPreviewError(f"{label} ya esta ocupado.", "rejected")
        return _area_for_cell(db, cell, area), cell
    if not area:
        raise ScanPreviewError(f"{label} requiere area o celda configurada.", "rejected")
    if destination:
        if allow_occupied_destination:
            cell = db.execute(
                select(Location)
                .where(Location.area_id == area.id)
                .where(Location.enabled == 1)
                .where(Location.is_visible == 1)
                .where(Location.code.is_not(None))
                .order_by(Location.y.asc(), Location.x.asc())
            ).scalars().first()
            if not cell:
                raise ScanPreviewError(f"{label}: el área destino no tiene ubicaciones operativas configuradas.", "error")
        else:
            try:
                cell = _find_destination_cell(db, area)
            except HTTPException as exc:
                step_match = re.search(r"(\d+)$", label)
                step = int(step_match.group(1)) if step_match else None
                raise _scan_preview_error_from_http(exc, step=step, destination_label=label)
        cell = _ensure_operational_route_cell(cell, label)
        return area, cell
    cell = db.execute(
        select(Location)
        .where(Location.area_id == area.id)
        .where(Location.enabled == 1)
        .where(Location.is_visible == 1)
        .where(Location.code.is_not(None))
        .order_by(Location.id.asc())
    ).scalars().first()
    cell = _ensure_operational_route_cell(cell, label)
    return area, cell


def _resolve_fifo_chain_configured_endpoint(
    db,
    *,
    source_mode: Optional[str] = None,
    destination_mode: Optional[str] = None,
    source_area_id=None,
    source_cell_id=None,
    destination_area_id=None,
    destination_cell_id=None,
    source_label: str,
    destination_label: str,
    allow_occupied_destination: bool = True,
) -> tuple[Optional[Area], Optional[Location], Optional[Area], Optional[Location]]:
    source_area = None
    source_cell = None
    destination_area = None
    destination_cell = None
    if (source_mode or "configured_area") == "configured_area":
        source_area, source_cell = _resolve_route_cell_from_config(
            db,
            cell_id=source_cell_id,
            area_id=source_area_id,
            label=source_label,
            destination=False,
        )
    if (destination_mode or "configured_area") == "configured_area":
        destination_area, destination_cell = _resolve_route_cell_from_config(
            db,
            cell_id=destination_cell_id,
            area_id=destination_area_id,
            label=destination_label,
            destination=True,
            allow_occupied_destination=allow_occupied_destination,
        )
    return source_area, source_cell, destination_area, destination_cell


def _route_point_payload(sequence: int, role: str, area: Optional[Area], cell: Optional[Location]) -> dict:
    position_code = _cell_position_code(cell)
    return {
        "sequence": sequence,
        "role": role,
        "area_id": getattr(area, "id", None),
        "area_code": getattr(area, "code", None),
        "area_name": getattr(area, "name", None),
        "cell_id": getattr(cell, "id", None),
        "cell_code": position_code,
        "positionCode": position_code,
        "type": "00",
        "label": _cell_label(cell, area),
    }


def _build_route_points(db, route_mode: str, entities: dict) -> list[dict]:
    source_area = entities.get("source_area")
    destination_area = entities.get("destination_area")
    source_cell = _ensure_operational_route_cell(entities.get("source_cell"), "Punto origen 1")
    destination_cell = _ensure_operational_route_cell(entities.get("destination_cell"), "Punto destino 1")
    points = [
        _route_point_payload(1, "source_1", source_area, source_cell),
        _route_point_payload(2, "destination_1", destination_area, destination_cell),
    ]
    if route_mode != "double_area":
        return points
    second_source_cell = _ensure_operational_route_cell(entities.get("second_source_cell"), "Punto origen 2")
    second_destination_cell = _ensure_operational_route_cell(entities.get("second_destination_cell"), "Punto destino 2")
    points.extend([
        _route_point_payload(3, "source_2", entities.get("second_source_area"), second_source_cell),
        _route_point_payload(4, "destination_2", entities.get("second_destination_area"), second_destination_cell),
    ])
    return points


def _build_fifo_chain_steps(db, entities: dict) -> list[dict]:
    source_area = entities.get("source_area")
    destination_area = entities.get("destination_area")
    second_source_area = entities.get("second_source_area")
    second_destination_area = entities.get("second_destination_area")
    step1_source_mode = entities.get("fifo_chain_step1_source_mode") or "configured_area"
    step1_material = entities.get("fifo_chain_step1_material")
    step2_source_mode = entities.get("fifo_chain_step2_source_mode") or "configured_area"
    step2_material = entities.get("fifo_chain_step2_material")
    source_cell = None if step1_source_mode != "configured_area" else _ensure_operational_route_cell(entities.get("source_cell"), "Punto origen 1")
    destination_cell = entities.get("destination_cell")
    if destination_cell:
        destination_cell = _ensure_operational_route_cell(destination_cell, "Punto destino 1")
    second_source_cell = None if step2_source_mode != "configured_area" else _ensure_operational_route_cell(entities.get("second_source_cell"), "Punto origen 2")
    second_destination_cell = entities.get("second_destination_cell")
    if second_destination_cell:
        second_destination_cell = _ensure_operational_route_cell(second_destination_cell, "Punto destino 2")
    step1_candidate = entities.get("fifo_chain_step1_candidate") or {}
    step2_candidate = entities.get("fifo_chain_step2_candidate") or {}
    step1_source_payload = (
        {
            "mode": "any_area_by_material",
            "label": f"Cualquier area con material {getattr(step1_material, 'code', None) or getattr(step1_material, 'name', None) or ''}".strip(),
            "material": _material_payload(step1_material),
            "area": _area_payload(step1_candidate.get("area")),
            "cell": _cell_payload(step1_candidate.get("cell")),
            "rack": _rack_payload(step1_candidate.get("rack")),
            "route_point": None,
            "message": "El rack del tramo 1 se seleccionara al ejecutar.",
        }
        if step1_source_mode != "configured_area"
        else {"area": _area_payload(source_area), "cell": _cell_payload(source_cell), "route_point": _route_point_payload(1, "source_1", source_area, source_cell)}
    )
    step2_source_payload = (
        {
            "mode": "any_area_by_material",
            "label": "Cualquier area",
            "material": _material_payload(step2_material),
            "area": _area_payload(step2_candidate.get("area")),
            "cell": _cell_payload(step2_candidate.get("cell")),
            "rack": _rack_payload(step2_candidate.get("rack")),
            "route_point": None,
            "message": "Selección proyectada; se revalidará al crear el tramo.",
        }
        if step2_source_mode != "configured_area"
        else {"area": _area_payload(second_source_area), "cell": _cell_payload(second_source_cell), "route_point": _route_point_payload(1, "source_2", second_source_area, second_source_cell)}
    )
    steps = [
        {
            "step": 1,
            "task_typ": entities.get("fifo_chain_step1_task_typ"),
            "source_mode": step1_source_mode,
            "step1_material": _material_payload(step1_material),
            "source": step1_source_payload,
            "destination": {"mode": entities.get("fifo_chain_step1_destination_mode") or "configured_area", "area": _area_payload(destination_area), "cell": _cell_payload(destination_cell), "route_point": _route_point_payload(2, "destination_1", destination_area, destination_cell) if destination_cell else None},
        },
        {
            "step": 2,
            "task_typ": entities.get("fifo_chain_step2_task_typ"),
            "source_mode": step2_source_mode,
            "step2_material": _material_payload(step2_material),
            "source": step2_source_payload,
            "destination": {"mode": entities.get("fifo_chain_step2_destination_mode") or "configured_area", "area": _area_payload(second_destination_area), "cell": _cell_payload(second_destination_cell), "route_point": _route_point_payload(2, "destination_2", second_destination_area, second_destination_cell) if second_destination_cell else None},
        },
    ]
    total_steps = int(entities.get("fifo_chain_total_steps") or 2)
    if total_steps >= 3:
        step3_source_mode = entities.get("fifo_chain_step3_source_mode") or "configured_area"
        step3_material = entities.get("fifo_chain_step3_material")
        step3_source_area = entities.get("fifo_chain_step3_source_area")
        step3_destination_area = entities.get("fifo_chain_step3_destination_area")
        step3_candidate = entities.get("fifo_chain_step3_candidate") or {}
        step3_source_cell = None if step3_source_mode != "configured_area" else _ensure_operational_route_cell(entities.get("fifo_chain_step3_source_cell"), "Punto origen 3")
        step3_destination_cell = entities.get("fifo_chain_step3_destination_cell")
        if step3_destination_cell:
            step3_destination_cell = _ensure_operational_route_cell(step3_destination_cell, "Punto destino 3")
        step3_source_payload = (
            {"mode": "any_area_by_material", "label": "Cualquier area", "material": _material_payload(step3_material), "area": _area_payload(step3_candidate.get("area")), "cell": _cell_payload(step3_candidate.get("cell")), "rack": _rack_payload(step3_candidate.get("rack")), "route_point": None, "message": "Selección proyectada; se revalidará al crear el tramo."}
            if step3_source_mode != "configured_area"
            else {"area": _area_payload(step3_source_area), "cell": _cell_payload(step3_source_cell), "route_point": _route_point_payload(1, "source_3", step3_source_area, step3_source_cell)}
        )
        steps.append({
            "step": 3,
            "task_typ": entities.get("fifo_chain_step3_task_typ"),
            "source_mode": step3_source_mode,
            "step3_material": _material_payload(step3_material),
            "source": step3_source_payload,
            "destination": {"mode": entities.get("fifo_chain_step3_destination_mode") or "configured_area", "area": _area_payload(step3_destination_area), "cell": _cell_payload(step3_destination_cell), "route_point": _route_point_payload(2, "destination_3", step3_destination_area, step3_destination_cell) if step3_destination_cell else None},
        })
    if total_steps >= 4:
        step4_source_mode = entities.get("fifo_chain_step4_source_mode") or "configured_area"
        step4_material = entities.get("fifo_chain_step4_material")
        step4_source_area = entities.get("fifo_chain_step4_source_area")
        step4_destination_area = entities.get("fifo_chain_step4_destination_area")
        step4_candidate = entities.get("fifo_chain_step4_candidate") or {}
        step4_source_cell = None if step4_source_mode != "configured_area" else _ensure_operational_route_cell(entities.get("fifo_chain_step4_source_cell"), "Punto origen 4")
        step4_destination_cell = entities.get("fifo_chain_step4_destination_cell")
        if step4_destination_cell:
            step4_destination_cell = _ensure_operational_route_cell(step4_destination_cell, "Punto destino 4")
        step4_source_payload = (
            {"mode": "any_area_by_material", "label": "Cualquier area", "material": _material_payload(step4_material), "area": _area_payload(step4_candidate.get("area")), "cell": _cell_payload(step4_candidate.get("cell")), "rack": _rack_payload(step4_candidate.get("rack")), "route_point": None, "message": "Selección proyectada; se revalidará al crear el tramo."}
            if step4_source_mode != "configured_area"
            else {"area": _area_payload(step4_source_area), "cell": _cell_payload(step4_source_cell), "route_point": _route_point_payload(1, "source_4", step4_source_area, step4_source_cell)}
        )
        steps.append({
            "step": 4,
            "task_typ": entities.get("fifo_chain_step4_task_typ"),
            "source_mode": step4_source_mode,
            "step4_material": _material_payload(step4_material),
            "source": step4_source_payload,
            "destination": {"mode": entities.get("fifo_chain_step4_destination_mode") or "configured_area", "area": _area_payload(step4_destination_area), "cell": _cell_payload(step4_destination_cell), "route_point": _route_point_payload(2, "destination_4", step4_destination_area, step4_destination_cell) if step4_destination_cell else None},
        })
    if total_steps >= 5:
        step5_source_mode = entities.get("fifo_chain_step5_source_mode") or "configured_area"
        step5_material = entities.get("fifo_chain_step5_material")
        step5_source_area = entities.get("fifo_chain_step5_source_area")
        step5_destination_area = entities.get("fifo_chain_step5_destination_area")
        step5_candidate = entities.get("fifo_chain_step5_candidate") or {}
        step5_source_cell = None if step5_source_mode != "configured_area" else _ensure_operational_route_cell(entities.get("fifo_chain_step5_source_cell"), "Punto origen 5")
        step5_destination_cell = entities.get("fifo_chain_step5_destination_cell")
        if step5_destination_cell:
            step5_destination_cell = _ensure_operational_route_cell(step5_destination_cell, "Punto destino 5")
        step5_source_payload = (
            {"mode": "any_area_by_material", "label": "Cualquier area", "material": _material_payload(step5_material), "area": _area_payload(step5_candidate.get("area")), "cell": _cell_payload(step5_candidate.get("cell")), "rack": _rack_payload(step5_candidate.get("rack")), "route_point": None, "message": "Selección proyectada; se revalidará al crear el tramo."}
            if step5_source_mode != "configured_area"
            else {"area": _area_payload(step5_source_area), "cell": _cell_payload(step5_source_cell), "route_point": _route_point_payload(1, "source_5", step5_source_area, step5_source_cell)}
        )
        steps.append({
            "step": 5,
            "task_typ": entities.get("fifo_chain_step5_task_typ"),
            "source_mode": step5_source_mode,
            "step5_material": _material_payload(step5_material),
            "source": step5_source_payload,
            "destination": {"mode": entities.get("fifo_chain_step5_destination_mode") or "configured_area", "area": _area_payload(step5_destination_area), "cell": _cell_payload(step5_destination_cell), "route_point": _route_point_payload(2, "destination_5", step5_destination_area, step5_destination_cell) if step5_destination_cell else None},
        })
    return steps


def _transition_text(value) -> str:
    return str(value or "").strip()


def _transition_scope(rule: QrTransitionRule) -> str:
    scope = _transition_text(getattr(rule, "scope", "qr_pda")) or "qr_pda"
    return scope if scope in {"qr_pda", "any_completed_order"} else "qr_pda"


def _transition_source_match_mode(rule: QrTransitionRule) -> str:
    mode = _transition_text(getattr(rule, "source_match_mode", "configured_source")) or "configured_source"
    return mode if mode in {"configured_source", "any_source"} else "configured_source"


def _transition_status_matches(rule_status: Optional[str], rack_status: Optional[str]) -> bool:
    expected = _transition_text(rule_status).lower()
    if not expected:
        return True
    return expected == _transition_text(rack_status).lower()


def _transition_specificity(rule: QrTransitionRule) -> int:
    score = 0
    if getattr(rule, "qr_action_rule_id", None):
        score += 128
    if getattr(rule, "scanner_station_id", None):
        score += 64
    if _transition_source_match_mode(rule) == "configured_source" and getattr(rule, "source_cell_id", None):
        score += 32
    if getattr(rule, "destination_cell_id", None):
        score += 16
    if _transition_source_match_mode(rule) == "configured_source" and getattr(rule, "source_area_id", None):
        score += 8
    if getattr(rule, "destination_area_id", None):
        score += 4
    if getattr(rule, "current_material_group_id", None):
        score += 2
    if _transition_text(getattr(rule, "current_rack_status", None)):
        score += 1
    return score


def _transition_rule_payload(rule: Optional[QrTransitionRule]) -> Optional[dict]:
    if not rule:
        return None
    return {
        "id": rule.id,
        "name": rule.name,
        "qr_action_rule_id": rule.qr_action_rule_id,
        "scanner_station_id": rule.scanner_station_id,
        "source_area_id": rule.source_area_id,
        "destination_area_id": rule.destination_area_id,
        "source_cell_id": getattr(rule, "source_cell_id", None),
        "destination_cell_id": getattr(rule, "destination_cell_id", None),
        "current_material_group_id": rule.current_material_group_id,
        "next_material_group_id": rule.next_material_group_id,
        "current_rack_status": rule.current_rack_status,
        "next_rack_status": rule.next_rack_status,
        "priority": int(rule.priority or 0),
    }


def _virtual_transition_for_fifo_chain_step(
    db,
    *,
    entities: dict,
    rack: Rack,
    source_cell: Location,
    destination_cell: Location,
    current_material_group_id,
    current_rack_status,
) -> dict:
    source_area_id = getattr(source_cell, "area_id", None)
    destination_area_id = getattr(destination_cell, "area_id", None)
    qr_action_rule_id = entities.get("qr_action_rule_id")
    scanner_station_id = entities.get("scanner_station_id")
    candidates = db.execute(
        select(QrTransitionRule)
        .where(QrTransitionRule.is_active == 1)
        .where(QrTransitionRule.apply_on == "movement_completed")
    ).scalars().all()
    matched = []
    for rule in candidates:
        advanced = _transition_text(getattr(rule, "match_mode", "advanced")) != "route_simple"
        if _transition_scope(rule) == "qr_pda" and not (qr_action_rule_id or scanner_station_id):
            continue
        if advanced and getattr(rule, "qr_action_rule_id", None) and rule.qr_action_rule_id != qr_action_rule_id:
            continue
        if advanced and getattr(rule, "scanner_station_id", None) and rule.scanner_station_id != scanner_station_id:
            continue
        if _transition_source_match_mode(rule) == "configured_source":
            if getattr(rule, "source_cell_id", None) and rule.source_cell_id != source_cell.id:
                continue
            if getattr(rule, "source_area_id", None) and rule.source_area_id != source_area_id:
                continue
        if getattr(rule, "destination_cell_id", None) and rule.destination_cell_id != destination_cell.id:
            continue
        if getattr(rule, "destination_area_id", None) and rule.destination_area_id != destination_area_id:
            continue
        if advanced and not int(getattr(rule, "ignore_current_material", 0) or 0) and getattr(rule, "current_material_group_id", None) and rule.current_material_group_id != current_material_group_id:
            continue
        if advanced and not _transition_status_matches(getattr(rule, "current_rack_status", None), current_rack_status):
            continue
        matched.append(rule)
    transition_context = {"qr_action_rule_id": qr_action_rule_id, "scanner_station_id": scanner_station_id}
    matched.sort(key=lambda item: transition_rank_key(item, transition_context))
    rule = matched[0] if matched else None
    if not rule:
        return {
            "applied": False,
            "message": "No aplica transición virtual.",
            "material_group_id": current_material_group_id,
            "rack_status": current_rack_status,
            "matched_rules": [],
            "selected_rule_id": None,
            "selected_match_tier": None,
            "selection_reason": "sin coincidencias",
        }
    next_material_group_id = rule.next_material_group_id or current_material_group_id
    next_status = _transition_text(rule.next_rack_status) or current_rack_status
    material_before = db.execute(select(MaterialGroup).where(MaterialGroup.id == current_material_group_id)).scalar_one_or_none() if current_material_group_id else None
    material_after = db.execute(select(MaterialGroup).where(MaterialGroup.id == next_material_group_id)).scalar_one_or_none() if next_material_group_id else None
    before_label = getattr(material_before, "code", None) or getattr(material_before, "name", None) or current_material_group_id or "sin material"
    after_label = getattr(material_after, "code", None) or getattr(material_after, "name", None) or next_material_group_id or "sin material"
    return {
        "applied": True,
        "rule": _transition_rule_payload(rule),
        "matched_rules": [{**_transition_rule_payload(item), "specificity": -transition_sort_key(item)[1], **transition_match_metadata(item, transition_context)} for item in matched],
        "selected_rule_id": rule.id,
        "selected_match_tier": transition_match_tier(rule, transition_context),
        "selection_reason": transition_selection_reason(rule, transition_context, matched),
        "message": f"{before_label} -> {after_label}",
        "material_group_id": next_material_group_id,
        "rack_status": next_status,
        "previous_material_group_id": current_material_group_id,
        "next_material_group_id": next_material_group_id,
        "previous_rack_status": current_rack_status,
        "next_rack_status": next_status,
    }


def _validate_fifo_chain_projected_state(db, entities: dict) -> dict:
    locations = db.execute(
        select(Location)
        .where(Location.enabled == 1)
        .where(Location.is_visible == 1)
        .where(Location.code.is_not(None))
        .order_by(Location.y.asc(), Location.x.asc(), Location.id.asc())
    ).scalars().all()
    racks = db.execute(select(Rack)).scalars().all()
    rack_by_id = {rack.id: rack for rack in racks}
    material_by_id = {
        row.id: row
        for row in db.execute(select(MaterialGroup)).scalars().all()
    }
    area_by_id = {
        row.id: row
        for row in db.execute(select(Area)).scalars().all()
    }
    occupancy = {cell.id: cell.rack_id for cell in locations}
    allow_same_cell_move = bool(entities.get("allow_same_cell_move"))
    rack_location = {cell.rack_id: cell.id for cell in locations if cell.rack_id is not None}
    rack_material = {rack.id: rack.material_group_id for rack in racks}
    rack_status = {rack.id: str(getattr(rack, "status", "") or "").strip() for rack in racks}
    reserved_ids = set()
    active_reservations = db.execute(
        select(MovementOrder.destination_cell_id)
        .where(MovementOrder.status.in_(("pending_dispatch", "dispatched", "in_progress")))
    ).all()
    reserved_ids.update(row[0] for row in active_reservations if row and row[0] is not None)
    active_rack_ids = {
        row[0] for row in db.execute(
            select(MovementOrder.rack_id)
            .where(MovementOrder.rack_id.is_not(None))
            .where(MovementOrder.status.in_(("pending_dispatch", "dispatched", "in_progress")))
        ).all()
        if row and row[0] is not None
    }

    total_steps = int(entities.get("fifo_chain_total_steps") or 2)
    step_specs = [
        {
            "step": 1,
            "source_mode": entities.get("fifo_chain_step1_source_mode") or "configured_area",
            "material": entities.get("fifo_chain_step1_material") or entities.get("material"),
            "source_area": entities.get("source_area"),
            "source_cell": entities.get("source_cell"),
            "source_cell_explicit": bool(entities.get("source_cell_config_id")),
            "destination_area": entities.get("destination_area"),
            "destination_cell": entities.get("destination_cell"),
            "destination_cell_explicit": bool(entities.get("destination_cell_config_id")),
            "source_key": "source_cell",
            "source_area_key": "source_area",
            "destination_key": "destination_cell",
        },
        {
            "step": 2,
            "source_mode": entities.get("fifo_chain_step2_source_mode") or "configured_area",
            "material": entities.get("fifo_chain_step2_material") or entities.get("material"),
            "source_area": entities.get("second_source_area"),
            "source_cell": entities.get("second_source_cell"),
            "source_cell_explicit": bool(entities.get("second_source_cell_config_id")),
            "destination_area": entities.get("second_destination_area"),
            "destination_cell": entities.get("second_destination_cell"),
            "destination_cell_explicit": bool(entities.get("second_destination_cell_config_id")),
            "source_key": "second_source_cell",
            "source_area_key": "second_source_area",
            "destination_key": "second_destination_cell",
        },
        {
            "step": 3,
            "source_mode": entities.get("fifo_chain_step3_source_mode") or "configured_area",
            "material": entities.get("fifo_chain_step3_material") or entities.get("material"),
            "source_area": entities.get("fifo_chain_step3_source_area"),
            "source_cell": entities.get("fifo_chain_step3_source_cell"),
            "source_cell_explicit": bool(entities.get("fifo_chain_step3_source_cell_config_id")),
            "destination_area": entities.get("fifo_chain_step3_destination_area"),
            "destination_cell": entities.get("fifo_chain_step3_destination_cell"),
            "destination_cell_explicit": bool(entities.get("fifo_chain_step3_destination_cell_config_id")),
            "source_key": "fifo_chain_step3_source_cell",
            "source_area_key": "fifo_chain_step3_source_area",
            "destination_key": "fifo_chain_step3_destination_cell",
        },
        {
            "step": 4,
            "source_mode": entities.get("fifo_chain_step4_source_mode") or "configured_area",
            "material": entities.get("fifo_chain_step4_material") or entities.get("material"),
            "source_area": entities.get("fifo_chain_step4_source_area"),
            "source_cell": entities.get("fifo_chain_step4_source_cell"),
            "source_cell_explicit": bool(entities.get("fifo_chain_step4_source_cell_config_id")),
            "destination_area": entities.get("fifo_chain_step4_destination_area"),
            "destination_cell": entities.get("fifo_chain_step4_destination_cell"),
            "destination_cell_explicit": bool(entities.get("fifo_chain_step4_destination_cell_config_id")),
            "source_key": "fifo_chain_step4_source_cell",
            "source_area_key": "fifo_chain_step4_source_area",
            "destination_key": "fifo_chain_step4_destination_cell",
        },
        {
            "step": 5,
            "source_mode": entities.get("fifo_chain_step5_source_mode") or "configured_area",
            "material": entities.get("fifo_chain_step5_material") or entities.get("material"),
            "source_area": entities.get("fifo_chain_step5_source_area"),
            "source_cell": entities.get("fifo_chain_step5_source_cell"),
            "source_cell_explicit": bool(entities.get("fifo_chain_step5_source_cell_config_id")),
            "destination_area": entities.get("fifo_chain_step5_destination_area"),
            "destination_cell": entities.get("fifo_chain_step5_destination_cell"),
            "destination_cell_explicit": bool(entities.get("fifo_chain_step5_destination_cell_config_id")),
            "source_key": "fifo_chain_step5_source_cell",
            "source_area_key": "fifo_chain_step5_source_area",
            "destination_key": "fifo_chain_step5_destination_cell",
        },
    ][:total_steps]
    for spec in step_specs:
        step = spec["step"]
        spec["source_area_ids"] = entities.get(f"fifo_chain_step{step}_source_area_ids") or []
        spec["destination_mode"] = entities.get(f"fifo_chain_step{step}_destination_mode") or "configured_area"
        spec["destination_area_ids"] = entities.get(f"fifo_chain_step{step}_destination_area_ids") or []

    projected_steps = []
    checklist_steps = []
    released_by_step: dict[int, int] = {}
    filled_by_step: dict[int, dict] = {}
    projected_transition_by_rack: dict[int, dict] = {}
    current_transition_conflict = None

    def material_label(material_obj, material_id=None):
        return getattr(material_obj, "code", None) or getattr(material_obj, "name", None) or getattr(material_by_id.get(material_id), "code", None) or material_id or "requerido"

    def source_area_label(spec):
        if spec["source_mode"] == "selected_areas_by_material":
            return "áreas seleccionadas"
        if spec["source_mode"] == "any_area_by_material":
            return "cualquier área operativa"
        return getattr(spec["source_area"], "name", None) or "origen configurado"

    def selected_area_payload(area_ids):
        return _area_payloads_by_ids(db, area_ids)

    def selected_area_names(area_ids):
        return _area_names_for_payloads(selected_area_payload(area_ids))

    def material_payload_label(material_obj, material_id=None):
        return str(material_label(material_obj, material_id))

    def source_check_payload(spec, *, ok, material_id, selected_rack=None, selected_cell=None, checked_locations=None, filled_by_step_info=None):
        checked_locations = checked_locations or []
        filled_info = filled_by_step_info
        if filled_info is None and selected_cell is not None:
            filled_info = filled_by_step.get(getattr(selected_cell, "id", None))
        return {
            "ok": ok,
            "mode": spec.get("source_mode"),
            "material": material_payload_label(spec.get("material"), material_id),
            "area": getattr(spec.get("source_area"), "name", None),
            "cell": getattr(spec.get("source_cell"), "code", None),
            "areas": selected_area_names(spec.get("source_area_ids") or []),
            "selected_rack": getattr(selected_rack, "code", None),
            "selected_cell": getattr(selected_cell, "code", None),
            "filled_by_step": filled_info,
            "selected_from_projected_state": bool(ok and selected_rack and selected_cell),
            "checked_locations_count": len(checked_locations),
        }

    def destination_check_payload(spec, *, ok, selected_cell=None, checked_locations=None, released_by_step=None):
        checked_locations = checked_locations or []
        return {
            "ok": ok,
            "mode": spec.get("destination_mode"),
            "area": getattr(spec.get("destination_area"), "name", None),
            "cell": getattr(spec.get("destination_cell"), "code", None) or getattr(selected_cell, "code", None),
            "areas": selected_area_names(spec.get("destination_area_ids") or []),
            "selected_cell": getattr(selected_cell, "code", None),
            "checked_locations_count": len(checked_locations),
            "released_by_previous_step": released_by_step,
            "selected_destination_from_projected_state": bool(ok and selected_cell),
        }

    def source_diagnostic_candidates(spec):
        if spec["source_mode"] == "any_area_by_material":
            return list(locations)
        if spec["source_mode"] == "selected_areas_by_material":
            selected_source_ids = set(spec["source_area_ids"])
            return [cell for cell in locations if cell.area_id in selected_source_ids]
        if spec["source_cell_explicit"] and spec["source_cell"]:
            return [spec["source_cell"]]
        if spec["source_area"]:
            return [cell for cell in locations if cell.area_id == spec["source_area"].id]
        if spec["source_cell"]:
            return [spec["source_cell"]]
        return []

    def build_source_checks(source_candidates, material_id):
        checked = []
        for cell in source_candidates[:30]:
            rack = rack_by_id.get(occupancy.get(cell.id))
            rack_material_id = rack_material.get(getattr(rack, "id", None)) if rack else None
            if not rack:
                status, reason = "empty", "Ubicación vacía"
            elif rack.id in active_rack_ids:
                status, reason = "reserved", "Rack reservado por orden activa"
            elif str(rack_status.get(rack.id) or "").strip().lower() in {"reserved", "reservado", "inactive", "inactivo", "disabled", "deshabilitado"}:
                status, reason = "unavailable", f"Rack no disponible: {rack_status.get(rack.id)}"
            elif material_id is not None and rack_material_id != material_id:
                status, reason = "occupied_other_material", f"Material distinto: {material_label(None, rack_material_id)}"
            else:
                status, reason = "candidate", "Candidato válido"
            checked.append({
                "location_id": cell.id,
                "location_code": cell.code,
                "area_id": cell.area_id,
                "status": status,
                "rack_id": getattr(rack, "id", None),
                "rack_code": getattr(rack, "code", None),
                "rack_material_id": rack_material_id,
                "rack_material_name": material_label(None, rack_material_id) if rack_material_id else None,
                "reason": reason,
            })
        return checked

    def select_projected_source(source_candidates, material_id):
        """Pick any valid rack from the current projected source state."""
        for candidate in source_candidates:
            rack_id = occupancy.get(candidate.id)
            candidate_rack = rack_by_id.get(rack_id)
            if not candidate_rack:
                continue
            if candidate_rack.id in active_rack_ids:
                continue
            current_status = str(rack_status.get(candidate_rack.id) or "").strip().lower()
            if current_status in {"reserved", "reservado", "inactive", "inactivo", "disabled", "deshabilitado"}:
                continue
            if material_id is not None and rack_material.get(candidate_rack.id) != material_id:
                continue
            return candidate, candidate_rack
        return None, None

    def transition_conflict_for_source(source_candidates, material_id):
        for candidate in source_candidates:
            rack_id = occupancy.get(candidate.id)
            if not rack_id:
                continue
            transition_info = projected_transition_by_rack.get(rack_id)
            if not transition_info:
                continue
            if transition_info.get("material_before_id") != material_id:
                continue
            if transition_info.get("material_after_id") == material_id:
                continue
            conflict_message = (
                f"El rack {transition_info.get('rack_code')} fue cambiado por transicion de "
                f"{transition_info.get('material_before_label')} a {transition_info.get('material_after_label')} "
                f"en el tramo {transition_info.get('step')}."
            )
            return {**transition_info, "transition_conflict": True, "transition_conflict_message": conflict_message}
        return None

    def fail(step, message, *, error_code, spec, material_id=None, checked_source=None, checked_destination=None, same_material_elsewhere=None, extra=None, summary=None, blocking_reason=None):
        failure_summary = summary or message
        source_checked = checked_source or []
        destination_checked = checked_destination or []
        transition_conflict = (extra or {}).get("transition_conflict") or bool(current_transition_conflict)
        transition_conflict_info = current_transition_conflict or {}
        failed_step_payload = {
            "step": step,
            "ok": False,
            "status": "failed",
            "error_code": error_code,
            "message": message,
            "summary": failure_summary,
            "material": material_label(spec.get("material"), material_id),
            "source_mode": spec.get("source_mode"),
            "source_area_ids": spec.get("source_area_ids") or [],
            "source_areas_checked": selected_area_payload(spec.get("source_area_ids") or []),
            "source_check": source_check_payload(spec, ok=False, material_id=material_id, checked_locations=source_checked),
            "destination_mode": spec.get("destination_mode"),
            "destination_area_ids": spec.get("destination_area_ids") or [],
            "destination_check": destination_check_payload(spec, ok=False if destination_checked else None, checked_locations=destination_checked),
            "transition_preview": None,
        }
        if transition_conflict:
            failed_step_payload.update({
                "transition_conflict": True,
                "transition_conflict_step": (extra or {}).get("transition_conflict_step") or transition_conflict_info.get("step"),
                "transition_rule_id": (extra or {}).get("transition_rule_id") or transition_conflict_info.get("transition_rule_id"),
                "transition_rule_name": (extra or {}).get("transition_rule_name") or transition_conflict_info.get("transition_rule_name"),
                "transition_conflict_message": (extra or {}).get("transition_conflict_message") or transition_conflict_info.get("transition_conflict_message"),
            })
        if (extra or {}).get("same_area_move"):
            failed_step_payload.update({
                "same_area_move": True,
                "same_area_move_message": "El area origen y destino son iguales, pero no hay una celda destino libre diferente.",
            })
        checklist = {
            "ok": False,
            "total_steps": total_steps,
            "failed_step": step,
            "steps": checklist_steps + [failed_step_payload],
        }
        details = {
            "error_code": error_code,
            "operator_message": message,
            "step": step,
            "total_steps": total_steps,
            "source_mode": spec.get("source_mode"),
            "source_label": source_area_label(spec),
            "material_required": material_label(spec.get("material"), material_id),
            "source_area_ids": spec.get("source_area_ids") or [],
            "destination_mode": spec.get("destination_mode"),
            "destination_area_ids": spec.get("destination_area_ids") or [],
            "checked_source_locations": checked_source or [],
            "checked_destination_locations": checked_destination or [],
            "same_material_elsewhere": same_material_elsewhere or [],
            "fifo_chain_preflight_checklist": checklist,
            "fifo_chain_projection": {"validation": "projected_sequential", "steps": projected_steps, "database_modified": False},
            "suggested_action": "Revisa el material, origen y destino configurados para este tramo antes de ejecutar la cadena.",
            "failure_summary": failure_summary,
            "blocking_reason": blocking_reason or error_code,
        }
        if extra:
            details.update(extra)
        if current_transition_conflict:
            details.update({
                "transition_conflict": True,
                "transition_conflict_step": current_transition_conflict.get("step"),
                "transition_rule_id": current_transition_conflict.get("transition_rule_id"),
                "transition_rule_name": current_transition_conflict.get("transition_rule_name"),
                "expected_material": current_transition_conflict.get("material_before_label"),
                "actual_projected_material": current_transition_conflict.get("material_after_label"),
                "rack_code": current_transition_conflict.get("rack_code"),
                "cell_code": current_transition_conflict.get("cell_code"),
                "transition_conflict_message": current_transition_conflict.get("transition_conflict_message"),
                "suggested_action": "Corrige la regla de transicion, cambia el material requerido del tramo o desactiva esa transicion para este flujo si no debe aplicar.",
            })
        raise ScanPreviewError(message, "error", details)

    for spec in step_specs:
        step = spec["step"]
        current_transition_conflict = None
        material_id = getattr(spec["material"], "id", None)
        if material_id is None:
            fail(
                step,
                f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no tiene material requerido configurado.",
                error_code="required_step_material_missing",
                spec=spec,
                summary="Material requerido no configurado.",
                blocking_reason="missing_required_material_configuration",
            )

        source_candidates = []
        if spec["source_mode"] == "any_area_by_material":
            source_candidates = [cell for cell in locations if occupancy.get(cell.id) is not None]
        elif spec["source_mode"] == "selected_areas_by_material":
            selected_source_ids = set(spec["source_area_ids"])
            if not selected_source_ids:
                fail(
                    step,
                    f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no se configuraron áreas válidas para buscar el material.",
                    error_code="invalid_selected_area_list",
                    spec=spec,
                    material_id=material_id,
                    summary="No se configuraron áreas válidas para buscar el material.",
                    blocking_reason="empty_selected_source_areas",
                )
            source_candidates = [cell for cell in locations if cell.area_id in selected_source_ids and occupancy.get(cell.id) is not None]
        elif spec["source_cell_explicit"] and spec["source_cell"]:
            source_candidates = [spec["source_cell"]]
        elif spec["source_area"]:
            source_candidates = [cell for cell in locations if cell.area_id == spec["source_area"].id and occupancy.get(cell.id) is not None]
        elif spec["source_cell"]:
            source_candidates = [spec["source_cell"]]
        source_candidates.sort(key=lambda cell: (
            getattr(rack_by_id.get(occupancy.get(cell.id)), "fifo_entered_at", None) is None,
            getattr(rack_by_id.get(occupancy.get(cell.id)), "fifo_entered_at", None),
            getattr(rack_by_id.get(occupancy.get(cell.id)), "code", "") or "",
            cell.y, cell.x, cell.id,
        ))

        # Each step is resolved against the projected occupancy/material/status
        # maps; rack identity from a previous step is intentionally not reused.
        source_cell, rack = select_projected_source(source_candidates, material_id)
        if not source_cell or not rack:
            same_material_elsewhere = []
            selected_source_ids = set(spec.get("source_area_ids") or [])
            for cell in locations:
                rack_id = occupancy.get(cell.id)
                if not rack_id or rack_material.get(rack_id) != material_id:
                    continue
                if spec["source_mode"] == "selected_areas_by_material" and cell.area_id in selected_source_ids:
                    continue
                if spec["source_mode"] == "configured_area" and spec.get("source_area") and cell.area_id == spec["source_area"].id:
                    continue
                same_material_elsewhere.append({
                    "rack_id": rack_id,
                    "rack_code": getattr(rack_by_id.get(rack_id), "code", None),
                    "area_id": cell.area_id,
                    "area_code": getattr(area_by_id.get(cell.area_id), "code", None),
                    "area_name": getattr(area_by_id.get(cell.area_id), "name", None),
                    "location_id": cell.id,
                    "location_code": cell.code,
                })
            diagnostic_source_candidates = source_diagnostic_candidates(spec)
            source_checks = build_source_checks(diagnostic_source_candidates, material_id)
            transition_conflict = transition_conflict_for_source(source_candidates, material_id)
            current_transition_conflict = transition_conflict
            label = source_area_label(spec)
            mat_label = material_label(spec["material"], material_id)
            explicit_wrong_material = None
            explicit_rack = None
            if spec["source_cell_explicit"] and spec["source_cell"]:
                explicit_rack = rack_by_id.get(occupancy.get(spec["source_cell"].id))
                explicit_material_id = rack_material.get(getattr(explicit_rack, "id", None)) if explicit_rack else None
                if explicit_rack and explicit_material_id != material_id:
                    explicit_wrong_material = material_label(None, explicit_material_id)
            if transition_conflict:
                message = (
                    f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: "
                    f"el rack {transition_conflict.get('rack_code')} si fue dejado en {label} por el tramo {transition_conflict.get('step')}, "
                    f"pero una transicion proyectada lo cambio de {transition_conflict.get('material_before_label')} "
                    f"a {transition_conflict.get('material_after_label')}. El tramo {step} requiere {mat_label}."
                )
                summary = (
                    f"El rack esperado fue cambiado por transicion de {transition_conflict.get('material_before_label')} "
                    f"a {transition_conflict.get('material_after_label')}."
                )
                blocking_reason = "projected_transition_changed_required_material"
            elif explicit_wrong_material:
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: la celda origen {spec['source_cell'].code} contiene material {explicit_wrong_material}, pero se requiere {mat_label}."
                summary = f"La celda origen explícita contiene material {explicit_wrong_material}; se requiere {mat_label}."
                blocking_reason = "explicit_source_cell_wrong_material"
            elif spec["source_cell_explicit"] and spec["source_cell"]:
                source_cell_info = spec["source_cell"]
                filled_info = filled_by_step.get(source_cell_info.id)
                released_step = released_by_step.get(source_cell_info.id)
                if filled_info and released_step and released_step > int(filled_info.get("step") or 0) and released_step < step:
                    message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: el punto de origen {source_cell_info.code} fue ocupado por el tramo {filled_info.get('step')}, pero luego fue liberado por el tramo {released_step} antes de este tramo."
                    summary = f"El punto de origen {source_cell_info.code} ya no tiene rack disponible en el estado proyectado."
                    blocking_reason = "explicit_source_cell_filled_then_released"
                elif filled_info:
                    message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: el punto de origen {source_cell_info.code} fue ocupado por el rack {filled_info.get('rack_code') or '-'} desde el tramo {filled_info.get('step')}, pero no cumple el material/status requerido en el estado proyectado."
                    summary = f"El punto de origen {source_cell_info.code} fue llenado, pero el rack no cumple el material/status requerido."
                    blocking_reason = "explicit_source_cell_projected_rack_invalid"
                else:
                    message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: el punto de origen {source_cell_info.code} no tiene rack disponible en el estado proyectado."
                    summary = f"El punto de origen {source_cell_info.code} estaba vacío al inicio y no fue llenado por ningún tramo anterior."
                    blocking_reason = "explicit_source_cell_empty_in_projected_state"
            elif spec["source_mode"] == "selected_areas_by_material":
                selected_names = _join_labels(selected_area_names(spec.get("source_area_ids") or []), "áreas seleccionadas")
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay rack disponible con material {mat_label} dentro de las áreas seleccionadas: {selected_names}."
                summary = f"No hay material {mat_label} en las áreas seleccionadas."
                blocking_reason = "material_not_available_in_selected_areas"
            elif spec["source_mode"] == "any_area_by_material":
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay ningún rack disponible con material {mat_label} en áreas operativas."
                summary = f"No hay ningún rack disponible con material {mat_label} en áreas operativas."
                blocking_reason = "material_not_available_any_area"
            else:
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay rack disponible con material {mat_label} en el área {label}."
                summary = f"No hay material {mat_label} disponible en {label}."
                blocking_reason = "material_not_available_in_configured_origin"
            failure_extra = {"suggested_action": "Revisa el material requerido, las areas origen permitidas y la disponibilidad/reserva del rack."}
            if transition_conflict:
                failure_extra = {
                    "transition_conflict": True,
                    "transition_conflict_step": transition_conflict.get("step"),
                    "transition_rule_id": transition_conflict.get("transition_rule_id"),
                    "transition_rule_name": transition_conflict.get("transition_rule_name"),
                    "expected_material": transition_conflict.get("material_before_label"),
                    "actual_projected_material": transition_conflict.get("material_after_label"),
                    "rack_code": transition_conflict.get("rack_code"),
                    "cell_code": transition_conflict.get("cell_code"),
                    "transition_conflict_message": transition_conflict.get("transition_conflict_message"),
                    "suggested_action": "Corrige la regla de transicion, cambia el material requerido del tramo o desactiva esa transicion para este flujo si no debe aplicar.",
                }
            fail(
                step,
                message,
                error_code="source_material_not_available_in_selected_areas" if spec["source_mode"] == "selected_areas_by_material" else "source_material_not_available",
                spec=spec,
                material_id=material_id,
                checked_source=source_checks,
                same_material_elsewhere=same_material_elsewhere[:10],
                extra={"suggested_action": "Revisa el material requerido, las áreas origen permitidas y la disponibilidad/reserva del rack."},
                summary=summary,
                blocking_reason=blocking_reason,
            )

        if spec["destination_cell_explicit"] and spec["destination_cell"] and spec["destination_cell"].id == source_cell.id and not allow_same_cell_move:
            fail(
                step,
                "No se puede ejecutar. La celda origen y destino no pueden ser la misma, salvo que se habilite Permitir movimiento en la misma celda.",
                error_code="same_source_destination_cell",
                spec=spec,
                material_id=material_id,
                summary="La celda origen y destino no pueden ser la misma, salvo que se habilite Permitir movimiento en la misma celda.",
                blocking_reason="same_source_destination_cell",
            )

        destination_candidates = []
        destination_mode = spec["destination_mode"]
        if destination_mode == "any_area_with_space":
            destination_candidates = sorted(locations, key=lambda cell: (cell.area_id, cell.y, cell.x, cell.id))
        elif destination_mode == "selected_areas_with_space":
            area_rank = {area_id: index for index, area_id in enumerate(spec["destination_area_ids"])}
            if not area_rank:
                fail(
                    step,
                    f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no se configuraron áreas válidas para buscar destino.",
                    error_code="invalid_selected_area_list",
                    spec=spec,
                    material_id=material_id,
                    summary="No se configuraron áreas válidas para buscar destino.",
                    blocking_reason="empty_selected_destination_areas",
                )
            destination_candidates = sorted([cell for cell in locations if cell.area_id in area_rank], key=lambda cell: (area_rank[cell.area_id], cell.y, cell.x, cell.id))
        elif spec["destination_cell_explicit"] and spec["destination_cell"]:
            destination_candidates = [spec["destination_cell"]]
        elif spec["destination_area"]:
            destination_candidates = [cell for cell in locations if cell.area_id == spec["destination_area"].id]
        elif spec["destination_cell"]:
            destination_candidates = [spec["destination_cell"]]

        destination_cell = next((cell for cell in destination_candidates if cell.id != source_cell.id and occupancy.get(cell.id) is None and cell.id not in reserved_ids), None)
        if not destination_cell and allow_same_cell_move:
            destination_cell = next(
                (
                    cell
                    for cell in destination_candidates
                    if cell.id == source_cell.id
                    and cell.id not in reserved_ids
                    and occupancy.get(cell.id) == rack.id
                ),
                None,
            )
        if not destination_cell:
            destination_label = getattr(spec["destination_cell"], "code", None) or getattr(spec["destination_area"], "name", None) or "destino configurado"
            checked = []
            for cell in destination_candidates[:30]:
                occupant = rack_by_id.get(occupancy.get(cell.id))
                if occupant:
                    status = "occupied"
                    reason = f"Ubicación ocupada por rack {occupant.code}"
                elif cell.id in reserved_ids:
                    status = "reserved"
                    reason = "Ubicación reservada por orden activa"
                else:
                    status = "unavailable"
                    reason = "Ubicación no disponible"
                checked.append({"location_id": cell.id, "location_code": cell.code, "area_id": cell.area_id, "status": status, "rack_id": getattr(occupant, "id", None), "rack_code": getattr(occupant, "code", None), "reason": reason})
            if spec["destination_cell_explicit"] and spec["destination_cell"]:
                occupant = rack_by_id.get(occupancy.get(spec["destination_cell"].id))
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: la celda destino {destination_label} está ocupada y no será liberada por un tramo anterior."
                summary = f"La celda destino {destination_label} está ocupada y no será liberada por un tramo anterior."
                blocking_reason = "explicit_destination_cell_occupied_not_released"
                extra_payload = {"destination_cell_occupant": {"rack_id": getattr(occupant, "id", None), "rack_code": getattr(occupant, "code", None)}}
            elif spec.get("destination_area") and source_cell.area_id == spec["destination_area"].id:
                message = "No se puede ejecutar. El area origen y destino son iguales, pero no hay una celda destino libre diferente a la celda origen."
                summary = "El area origen y destino son iguales, pero no hay una celda destino libre diferente."
                blocking_reason = "same_area_no_free_destination_cell"
                extra_payload = {"same_area_move": True}
            elif destination_mode == "selected_areas_with_space":
                selected_names = _join_labels(selected_area_names(spec.get("destination_area_ids") or []), "áreas seleccionadas")
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay espacio disponible dentro de las áreas destino seleccionadas: {selected_names}."
                summary = f"No hay espacio disponible dentro de las áreas destino seleccionadas: {selected_names}."
                blocking_reason = "destination_space_not_available_in_selected_areas"
                extra_payload = {}
            elif destination_mode == "any_area_with_space":
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay espacio disponible en ninguna área operativa."
                summary = "No hay espacio disponible en ninguna área operativa."
                blocking_reason = "destination_space_not_available_any_area"
                extra_payload = {}
            else:
                message = f"No se puede ejecutar la cadena. Falla en tramo {step} de {total_steps}: no hay espacio disponible en el área destino {destination_label}."
                summary = f"No hay espacio disponible en el área destino {destination_label}."
                blocking_reason = "destination_space_not_available_configured_area"
                extra_payload = {}
            destination_error_code = "destination_space_not_available_in_selected_areas" if destination_mode == "selected_areas_with_space" else "destination_space_not_available_any_area" if destination_mode == "any_area_with_space" else "destination_occupied_not_released"
            if blocking_reason == "same_area_no_free_destination_cell":
                destination_error_code = "same_area_no_free_destination_cell"
            fail(
                step,
                message,
                error_code=destination_error_code,
                spec=spec,
                material_id=material_id,
                checked_destination=checked,
                extra={"destination_label": destination_label, "suggested_action": "Libera una ubicación destino o corrige el destino configurado del tramo.", **extra_payload},
                summary=summary,
                blocking_reason=blocking_reason,
            )

        release_step = released_by_step.get(destination_cell.id)
        source_filled_info = filled_by_step.get(source_cell.id)
        source_filled_by_previous_step = bool(
            source_filled_info
            and int(source_filled_info.get("step") or 0) < step
            and source_filled_info.get("rack_id") == rack.id
        )
        source_filled_by_previous_step_message = None
        if source_filled_by_previous_step:
            source_filled_by_previous_step_message = (
                f"El origen {source_cell.code} será ocupado por el rack {rack.code} desde el tramo {source_filled_info.get('step')}."
            )
        previous_material_id = rack_material.get(rack.id)
        previous_status = rack_status.get(rack.id)
        same_cell_move = bool(source_cell.id == destination_cell.id)
        if same_cell_move:
            occupancy[source_cell.id] = rack.id
        else:
            occupancy[source_cell.id] = None
            released_by_step[source_cell.id] = step
            occupancy[destination_cell.id] = rack.id
        rack_location[rack.id] = destination_cell.id
        resolved_source_area = db.execute(select(Area).where(Area.id == source_cell.area_id)).scalar_one_or_none()
        resolved_destination_area = db.execute(select(Area).where(Area.id == destination_cell.area_id)).scalar_one_or_none()
        transition_preview = _virtual_transition_for_fifo_chain_step(
            db,
            entities=entities,
            rack=rack,
            source_cell=source_cell,
            destination_cell=destination_cell,
            current_material_group_id=previous_material_id,
            current_rack_status=previous_status,
        )
        transition_info = None
        if transition_preview.get("applied"):
            transition_rule = transition_preview.get("rule") or {}
            transition_info = {
                "step": step,
                "transition_rule_id": transition_rule.get("id"),
                "transition_rule_name": transition_rule.get("name"),
                "rack_id": rack.id,
                "rack_code": rack.code,
                "cell_id": destination_cell.id,
                "cell_code": destination_cell.code,
                "material_before_id": previous_material_id,
                "material_before_label": material_label(None, previous_material_id),
                "material_after_id": transition_preview.get("material_group_id"),
                "material_after_label": material_label(None, transition_preview.get("material_group_id")),
            }
            projected_transition_by_rack[rack.id] = transition_info
        rack_material[rack.id] = transition_preview.get("material_group_id")
        rack_status[rack.id] = transition_preview.get("rack_status")
        filled_by_step[destination_cell.id] = {
            "step": step,
            "rack_id": rack.id,
            "rack_code": rack.code,
            "material_group_id": rack_material.get(rack.id),
            "destination_cell_code": destination_cell.code,
            **({
                "transition_applied": True,
                "transition_rule_id": transition_info.get("transition_rule_id"),
                "transition_rule_name": transition_info.get("transition_rule_name"),
                "material_before_label": transition_info.get("material_before_label"),
                "material_after_label": transition_info.get("material_after_label"),
            } if transition_info else {"transition_applied": False}),
        }
        entities[spec["source_key"]] = source_cell
        entities[spec["source_area_key"]] = resolved_source_area or spec["source_area"]
        entities[spec["destination_key"]] = destination_cell
        destination_area_key = "destination_area" if step == 1 else "second_destination_area" if step == 2 else f"fifo_chain_step{step}_destination_area"
        entities[destination_area_key] = resolved_destination_area or spec["destination_area"]
        entities[f"fifo_chain_step{step}_candidate"] = {"rack": rack, "cell": source_cell, "area": resolved_source_area or spec["source_area"]}
        if step == 1:
            entities["rack"] = rack
            entities["source_cell"] = source_cell
            entities["source_area"] = resolved_source_area or spec["source_area"]
        same_area_move = bool(source_cell.area_id == destination_cell.area_id and source_cell.id != destination_cell.id)
        same_area_move_message = (
            f"Movimiento dentro de la misma area: origen {source_cell.code}, destino {destination_cell.code}."
            if same_area_move else None
        )
        same_cell_move_message = (
            f"Movimiento en la misma celda permitido por configuraciÃ³n: {source_cell.code} -> {destination_cell.code}."
            if same_cell_move else None
        )
        projected_step = {
            "step": step,
            "ok": True,
            "rack_id": rack.id,
            "rack_code": rack.code,
            "material_group_id": previous_material_id,
            "source_cell_id": source_cell.id,
            "source_cell_code": source_cell.code,
            "destination_cell_id": destination_cell.id,
            "destination_cell_code": destination_cell.code,
            "destination_released_by_previous_step": release_step,
            "source_filled_by_previous_step": 1 if source_filled_by_previous_step else 0,
            "source_filled_by_previous_step_message": source_filled_by_previous_step_message,
            "source_filled_by_step": source_filled_info,
            "selected_from_projected_state": True,
            "selected_destination_from_projected_state": True,
            "same_area_move": same_area_move,
            "same_area_move_message": same_area_move_message,
            "same_cell_move": same_cell_move,
            "same_cell_move_message": same_cell_move_message,
            "source_mode": spec["source_mode"],
            "source_area_ids": spec["source_area_ids"],
            "destination_mode": destination_mode,
            "destination_area_ids": spec["destination_area_ids"],
            "transition_preview": transition_preview,
            "message": f"El destino del tramo {step} será liberado por el tramo {release_step}." if release_step else "Validado contra el estado proyectado.",
        }
        projected_steps.append(projected_step)
        ok_summary = f"Tramo {step} OK: rack {rack.code} {material_label(spec['material'], material_id)} desde {getattr(resolved_source_area, 'name', None) or source_cell.code} hacia {getattr(resolved_destination_area, 'name', None) or destination_cell.code}."
        if source_filled_by_previous_step_message:
            ok_summary = f"Tramo {step} OK: {source_filled_by_previous_step_message}"
        if transition_preview.get("applied"):
            ok_summary += f" Transición proyectada: {transition_preview.get('message')}."
        checklist_steps.append({
            "step": step,
            "ok": True,
            "status": "ok",
            "summary": ok_summary,
            "source_filled_by_previous_step": 1 if source_filled_by_previous_step else 0,
            "source_filled_by_previous_step_message": source_filled_by_previous_step_message,
            "selected_from_projected_state": True,
            "selected_destination_from_projected_state": True,
            "same_area_move": same_area_move,
            "same_area_move_message": same_area_move_message,
            "same_cell_move": same_cell_move,
            "same_cell_move_message": same_cell_move_message,
            "material": material_label(spec["material"], material_id),
            "source": {"cell_code": source_cell.code, "area_id": source_cell.area_id, "rack_code": rack.code},
            "destination": {"cell_code": destination_cell.code, "area_id": destination_cell.area_id},
            "source_mode": spec["source_mode"],
            "destination_mode": destination_mode,
            "source_check": source_check_payload(spec, ok=True, material_id=material_id, selected_rack=rack, selected_cell=source_cell, checked_locations=source_diagnostic_candidates(spec), filled_by_step_info=source_filled_info),
            "destination_check": destination_check_payload(spec, ok=True, selected_cell=destination_cell, checked_locations=destination_candidates, released_by_step=release_step),
            "transition_preview": f"Transicion proyectada: {transition_preview.get('message')}" if transition_preview.get("applied") else transition_preview.get("message"),
            "transition_rule": transition_preview.get("rule"),
        })

    projection_notes = [
        {
            "step": item["step"],
            "message": item["message"],
            "destination_cell_code": item["destination_cell_code"],
            "released_by_step": item["destination_released_by_previous_step"],
            "source_filled_by_previous_step": item.get("source_filled_by_previous_step"),
            "source_filled_by_previous_step_message": item.get("source_filled_by_previous_step_message"),
            "transition_preview": item.get("transition_preview", {}).get("message"),
        }
        for item in projected_steps
        if item.get("destination_released_by_previous_step")
        or item.get("source_filled_by_previous_step")
        or item.get("source_mode") == "selected_areas_by_material"
        or item.get("destination_mode") in {"any_area_with_space", "selected_areas_with_space"}
        or item.get("transition_preview", {}).get("applied")
    ]
    checklist = {"ok": True, "total_steps": total_steps, "failed_step": None, "steps": checklist_steps}
    return {
        "validation": "projected_sequential",
        "ok": True,
        "steps": projected_steps,
        "projection_notes": projection_notes,
        "fifo_chain_preflight_checklist": checklist,
        "database_modified": False,
        "future_steps_revalidated_on_creation": True,
    }

def _resolve_entities(db, station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], parsed: dict, action: Optional[str]) -> dict:
    route_mode = _route_mode_value(station, qr_rule)
    allow_same_cell_move = _allow_same_cell_move_value(station, qr_rule)
    fifo_material_policy = _fifo_material_policy_value(station, qr_rule)
    fifo_chain_total_steps = _fifo_chain_total_steps_value(station, qr_rule)
    fifo_chain_step1_source_mode = _fifo_chain_step1_source_mode_value(station, qr_rule)
    fifo_chain_step1_material_group_id = _fifo_chain_step1_material_group_id_value(station, qr_rule)
    fifo_chain_step2_source_mode = _fifo_chain_step2_source_mode_value(station, qr_rule)
    fifo_chain_step2_material_group_id = _fifo_chain_step2_material_group_id_value(station, qr_rule)
    fifo_chain_step3_source_mode = _fifo_chain_step3_source_mode_value(station, qr_rule)
    fifo_chain_step3_material_group_id = _fifo_chain_step3_material_group_id_value(station, qr_rule)
    fifo_chain_step4_source_mode = _fifo_chain_step4_source_mode_value(station, qr_rule)
    fifo_chain_step4_material_group_id = _fifo_chain_step4_material_group_id_value(station, qr_rule)
    fifo_chain_step5_source_mode = _fifo_chain_step5_source_mode_value(station, qr_rule)
    fifo_chain_step5_material_group_id = _fifo_chain_step5_material_group_id_value(station, qr_rule)
    fifo_chain_task_typ_by_step = {
        step: _fifo_chain_step_task_typ_value(station, qr_rule, step) for step in range(1, 6)
    }
    source_modes = {
        1: fifo_chain_step1_source_mode, 2: fifo_chain_step2_source_mode,
        3: fifo_chain_step3_source_mode, 4: fifo_chain_step4_source_mode,
        5: fifo_chain_step5_source_mode,
    }
    source_area_ids_by_step = {
        step: _fifo_chain_step_area_ids_value(station, qr_rule, step, "source") for step in range(1, 6)
    }
    destination_modes = {
        step: _fifo_chain_step_destination_mode_value(station, qr_rule, step) for step in range(1, 6)
    }
    destination_area_ids_by_step = {
        step: _fifo_chain_step_area_ids_value(station, qr_rule, step, "destination") for step in range(1, 6)
    }
    material_id = _first_value(
        getattr(qr_rule, "material_group_id", None) if qr_rule else None,
        getattr(station, "default_material_group_id", None) if station else None,
    )
    rack_id = getattr(qr_rule, "rack_id", None) if qr_rule else None
    if route_mode == "fifo_chain":
        fifo_material_policy = "specific_material"
        material_id = fifo_chain_step1_material_group_id
        rack_id = None
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
    second_source_area_id = _first_value(
        getattr(qr_rule, "second_source_area_id", None) if qr_rule else None,
        getattr(station, "second_source_area_id", None) if station else None,
    )
    second_destination_area_id = _first_value(
        getattr(qr_rule, "second_destination_area_id", None) if qr_rule else None,
        getattr(station, "second_destination_area_id", None) if station else None,
    )
    second_source_cell_id = _first_value(
        getattr(qr_rule, "second_source_cell_id", None) if qr_rule else None,
        getattr(station, "second_source_cell_id", None) if station else None,
    )
    second_destination_cell_id = _first_value(
        getattr(qr_rule, "second_destination_cell_id", None) if qr_rule else None,
        getattr(station, "second_destination_cell_id", None) if station else None,
    )
    fifo_chain_step3_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_source_area_id")
    fifo_chain_step3_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_destination_area_id")
    fifo_chain_step3_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_source_cell_id")
    fifo_chain_step3_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_destination_cell_id")
    fifo_chain_step4_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_source_area_id")
    fifo_chain_step4_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_destination_area_id")
    fifo_chain_step4_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_source_cell_id")
    fifo_chain_step4_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_destination_cell_id")
    fifo_chain_step5_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step5_source_area_id")
    fifo_chain_step5_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step5_destination_area_id")
    fifo_chain_step5_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step5_source_cell_id")
    fifo_chain_step5_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step5_destination_cell_id")
    if fifo_chain_step3_source_mode != "configured_area":
        fifo_chain_step3_source_area_id = None
        fifo_chain_step3_source_cell_id = None
    if fifo_chain_step4_source_mode != "configured_area":
        fifo_chain_step4_source_area_id = None
        fifo_chain_step4_source_cell_id = None
    if fifo_chain_step5_source_mode != "configured_area":
        fifo_chain_step5_source_area_id = None
        fifo_chain_step5_source_cell_id = None

    if route_mode != "fifo_chain" and parsed.get("prefix") in {"RACK", "STORE", "MOVE"} and not rack_id:
        rack_code = parsed.get("rack_code") or parsed.get("code")
        if rack_code:
            rack = db.execute(select(Rack).where(Rack.code == rack_code)).scalar_one_or_none()
            rack_id = rack.id if rack else rack_id
    if route_mode != "fifo_chain" and parsed.get("prefix") == "MAT" and not material_id:
        material = db.execute(select(MaterialGroup).where(MaterialGroup.code == parsed.get("code"))).scalar_one_or_none()
        material_id = material.id if material else material_id
    if parsed.get("prefix") == "MOVE" and parsed.get("destination_code") and not destination_cell_id:
        cell = db.execute(select(Location).where(Location.code == parsed.get("destination_code"))).scalar_one_or_none()
        destination_cell_id = cell.id if cell else destination_cell_id

    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_id)).scalar_one_or_none() if material_id else None
    fifo_chain_step1_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step1_material_group_id)).scalar_one_or_none() if fifo_chain_step1_material_group_id else None
    fifo_chain_step2_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step2_material_group_id)).scalar_one_or_none() if fifo_chain_step2_material_group_id else None
    fifo_chain_step3_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step3_material_group_id)).scalar_one_or_none() if fifo_chain_step3_material_group_id else None
    fifo_chain_step4_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step4_material_group_id)).scalar_one_or_none() if fifo_chain_step4_material_group_id else None
    fifo_chain_step5_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step5_material_group_id)).scalar_one_or_none() if fifo_chain_step5_material_group_id else None
    if route_mode == "fifo_chain" and not fifo_chain_step1_material:
        raise ScanPreviewError("El tramo 1 requiere Material requerido tramo 1.", "rejected")
    rack = db.execute(select(Rack).where(Rack.id == rack_id)).scalar_one_or_none() if rack_id else None
    source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none() if source_area_id else None
    destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none() if destination_area_id else None
    source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none() if source_cell_id else None
    destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none() if destination_cell_id else None
    if source_cell and not source_area and source_cell.area_id:
        source_area = db.execute(select(Area).where(Area.id == source_cell.area_id)).scalar_one_or_none()
    if destination_cell and not destination_area and destination_cell.area_id:
        destination_area = db.execute(select(Area).where(Area.id == destination_cell.area_id)).scalar_one_or_none()
    second_source_area = None
    second_destination_area = None
    second_source_cell = None
    second_destination_cell = None
    fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_area_id)).scalar_one_or_none() if fifo_chain_step3_source_area_id else None
    fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_area_id)).scalar_one_or_none() if fifo_chain_step3_destination_area_id else None
    fifo_chain_step3_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_source_cell_id)).scalar_one_or_none() if fifo_chain_step3_source_cell_id else None
    fifo_chain_step3_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_destination_cell_id)).scalar_one_or_none() if fifo_chain_step3_destination_cell_id else None
    fifo_chain_step4_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_source_area_id)).scalar_one_or_none() if fifo_chain_step4_source_area_id else None
    fifo_chain_step4_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_destination_area_id)).scalar_one_or_none() if fifo_chain_step4_destination_area_id else None
    fifo_chain_step4_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_source_cell_id)).scalar_one_or_none() if fifo_chain_step4_source_cell_id else None
    fifo_chain_step4_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_destination_cell_id)).scalar_one_or_none() if fifo_chain_step4_destination_cell_id else None
    fifo_chain_step5_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_source_area_id)).scalar_one_or_none() if fifo_chain_step5_source_area_id else None
    fifo_chain_step5_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_destination_area_id)).scalar_one_or_none() if fifo_chain_step5_destination_area_id else None
    fifo_chain_step5_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step5_source_cell_id)).scalar_one_or_none() if fifo_chain_step5_source_cell_id else None
    fifo_chain_step5_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step5_destination_cell_id)).scalar_one_or_none() if fifo_chain_step5_destination_cell_id else None
    if fifo_chain_step3_source_cell and not fifo_chain_step3_source_area and fifo_chain_step3_source_cell.area_id:
        fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step3_destination_cell and not fifo_chain_step3_destination_area and fifo_chain_step3_destination_cell.area_id:
        fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step4_source_cell and not fifo_chain_step4_source_area and fifo_chain_step4_source_cell.area_id:
        fifo_chain_step4_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_source_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step4_destination_cell and not fifo_chain_step4_destination_area and fifo_chain_step4_destination_cell.area_id:
        fifo_chain_step4_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_destination_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step5_source_cell and not fifo_chain_step5_source_area and fifo_chain_step5_source_cell.area_id:
        fifo_chain_step5_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_source_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step5_destination_cell and not fifo_chain_step5_destination_area and fifo_chain_step5_destination_cell.area_id:
        fifo_chain_step5_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_destination_cell.area_id)).scalar_one_or_none()

    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_step1_source_mode != "configured_area":
        if not fifo_chain_step1_material:
            raise ScanPreviewError("El tramo 1 requiere Material requerido tramo 1.", "rejected")
        if destination_modes[1] == "configured_area" and not destination_area and not destination_cell:
            raise ScanPreviewError("Punto destino 1 requiere area o celda configurada.", "rejected")
        if destination_modes[1] == "configured_area":
            destination_area, destination_cell = _resolve_route_cell_from_config(
                db, cell_id=destination_cell_id, area_id=destination_area_id,
                label="Punto destino 1", destination=True, allow_occupied_destination=True,
            )
        try:
            if fifo_chain_step1_source_mode == "selected_areas_by_material":
                candidate_rack, candidate_cell, candidate_area = resolve_fifo_request_by_material_selected_areas(
                    db, fifo_chain_step1_material.id, source_area_ids_by_step[1]
                )
            else:
                candidate_rack, candidate_cell, candidate_area = resolve_fifo_request_by_material_any_area(db, fifo_chain_step1_material.id)
        except Exception:
            candidate_rack, candidate_cell, candidate_area = None, None, None
        rack = candidate_rack
        material = fifo_chain_step1_material
        source_cell = candidate_cell
        source_area = candidate_area
        if fifo_chain_step2_source_mode != "configured_area":
            if not fifo_chain_step2_material:
                raise ScanPreviewError("El tramo 2 por material requiere Material requerido tramo 2.", "rejected")
        else:
            second_source_area, second_source_cell = _resolve_route_cell_from_config(
                db,
                cell_id=second_source_cell_id,
                area_id=second_source_area_id,
                label="Punto origen 2",
                destination=False,
            )
        if destination_modes[2] == "configured_area":
            second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=second_destination_cell_id, area_id=second_destination_area_id,
                label="Punto destino 2", destination=True, allow_occupied_destination=True,
            )
    elif action == "fifo_request" and source_area and destination_area:
        resolved_priority = _first_value(
            getattr(qr_rule, "priority", None) if qr_rule else None,
            getattr(station, "priority", None) if station else None,
        )
        try:
            if fifo_material_policy == "any_available_from_source":
                selection = resolve_fifo_request_any_available(db, source_area.id, destination_area.id, _priority_text(resolved_priority), source_cell_id=source_cell_id, allow_same_cell_move=allow_same_cell_move)
            else:
                if not material:
                    raise ScanPreviewError("fifo_request requiere material_group_id cuando la politica es specific_material.", "rejected")
                selection = resolve_fifo_request(db, source_area.id, destination_area.id, material.id, _priority_text(resolved_priority), allow_same_cell_move=allow_same_cell_move)
        except HTTPException as exc:
            raise _scan_preview_error_from_http(exc, step=1 if route_mode == "fifo_chain" else None, destination_label="Punto destino 1")
        rack = selection.rack
        material = selection.material_group
        source_cell = selection.source_cell
        destination_cell = selection.destination_cell
        source_area = selection.source_area
        destination_area = selection.destination_area
        if route_mode in {"double_area", "fifo_chain"}:
            if route_mode == "fifo_chain" and fifo_chain_step2_source_mode != "configured_area":
                if not fifo_chain_step2_material:
                    raise ScanPreviewError("El tramo 2 por material requiere Material requerido tramo 2.", "rejected")
            else:
                second_source_area, second_source_cell = _resolve_route_cell_from_config(
                    db,
                    cell_id=second_source_cell_id,
                    area_id=second_source_area_id,
                    label="Punto origen 2",
                    destination=False,
                )
            if route_mode != "fifo_chain" or destination_modes[2] == "configured_area":
                second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
                    db, cell_id=second_destination_cell_id, area_id=second_destination_area_id,
                    label="Punto destino 2", destination=True,
                    allow_occupied_destination=route_mode == "fifo_chain",
                )
    elif action in {"point_to_area", "store_rack", "request_empty", "return_full"} and destination_area and not destination_cell:
        try:
            destination_cell = _find_destination_cell(db, destination_area)
        except HTTPException as exc:
            raise _scan_preview_error_from_http(exc, destination_label="Destino")

    if action == "fifo_request" and route_mode == "fifo_chain":
        if fifo_chain_step2_source_mode != "configured_area":
            if not fifo_chain_step2_material:
                raise ScanPreviewError("El tramo 2 por material requiere Material requerido tramo 2.", "rejected")
        elif not second_source_area:
            second_source_area, second_source_cell, _, _ = _resolve_fifo_chain_configured_endpoint(
                db,
                source_mode=fifo_chain_step2_source_mode,
                destination_mode="none",
                source_area_id=second_source_area_id,
                source_cell_id=second_source_cell_id,
                source_label="Punto origen 2",
                destination_label="Punto destino 2",
            )
        if destination_modes[2] == "configured_area" and not second_destination_area:
            _, _, second_destination_area, second_destination_cell = _resolve_fifo_chain_configured_endpoint(
                db,
                source_mode="none",
                destination_mode=destination_modes[2],
                destination_area_id=second_destination_area_id,
                destination_cell_id=second_destination_cell_id,
                source_label="Punto origen 2",
                destination_label="Punto destino 2",
                allow_occupied_destination=True,
            )

    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_total_steps >= 3:
        if fifo_chain_step3_source_mode != "configured_area":
            if not fifo_chain_step3_material:
                raise ScanPreviewError("El tramo 3 por material requiere Material requerido tramo 3.", "rejected")
        else:
            fifo_chain_step3_source_area, fifo_chain_step3_source_cell = _resolve_route_cell_from_config(
                db,
                cell_id=fifo_chain_step3_source_cell_id,
                area_id=fifo_chain_step3_source_area_id,
                label="Punto origen 3",
                destination=False,
            )
        if destination_modes[3] == "configured_area":
            fifo_chain_step3_destination_area, fifo_chain_step3_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step3_destination_cell_id, area_id=fifo_chain_step3_destination_area_id,
                label="Punto destino 3", destination=True, allow_occupied_destination=True,
            )
    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_total_steps >= 4:
        if fifo_chain_step4_source_mode != "configured_area":
            if not fifo_chain_step4_material:
                raise ScanPreviewError("El tramo 4 por material requiere Material requerido tramo 4.", "rejected")
        else:
            fifo_chain_step4_source_area, fifo_chain_step4_source_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step4_source_cell_id, area_id=fifo_chain_step4_source_area_id, label="Punto origen 4", destination=False
            )
        if destination_modes[4] == "configured_area":
            fifo_chain_step4_destination_area, fifo_chain_step4_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step4_destination_cell_id, area_id=fifo_chain_step4_destination_area_id,
                label="Punto destino 4", destination=True, allow_occupied_destination=True,
            )
    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_total_steps >= 5:
        if fifo_chain_step5_source_mode != "configured_area":
            if not fifo_chain_step5_material:
                raise ScanPreviewError("El tramo 5 por material requiere Material requerido tramo 5.", "rejected")
        else:
            fifo_chain_step5_source_area, fifo_chain_step5_source_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step5_source_cell_id, area_id=fifo_chain_step5_source_area_id, label="Punto origen 5", destination=False
            )
        if destination_modes[5] == "configured_area":
            fifo_chain_step5_destination_area, fifo_chain_step5_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step5_destination_cell_id, area_id=fifo_chain_step5_destination_area_id,
                label="Punto destino 5", destination=True, allow_occupied_destination=True,
            )

    entities = {
        "material": material,
        "rack": rack,
        "source_area": source_area,
        "destination_area": destination_area,
        "source_cell": source_cell,
        "destination_cell": destination_cell,
        "source_cell_config_id": source_cell_id,
        "destination_cell_config_id": destination_cell_id,
        "route_mode": route_mode,
        "allow_same_cell_move": allow_same_cell_move,
        "fifo_material_policy": fifo_material_policy,
        "fifo_chain_total_steps": fifo_chain_total_steps,
        "fifo_chain_step1_source_mode": fifo_chain_step1_source_mode,
        "fifo_chain_step1_source_area_ids": source_area_ids_by_step[1],
        "fifo_chain_step1_destination_mode": destination_modes[1],
        "fifo_chain_step1_destination_area_ids": destination_area_ids_by_step[1],
        "fifo_chain_step1_material": fifo_chain_step1_material,
        "fifo_chain_step1_material_group_id": fifo_chain_step1_material_group_id,
        "fifo_chain_step1_task_typ": fifo_chain_task_typ_by_step[1],
        "fifo_chain_step1_candidate": {"rack": rack, "cell": source_cell, "area": source_area} if fifo_chain_step1_source_mode == "any_area_by_material" else {},
        "fifo_chain_step2_source_mode": fifo_chain_step2_source_mode,
        "fifo_chain_step2_source_area_ids": source_area_ids_by_step[2],
        "fifo_chain_step2_destination_mode": destination_modes[2],
        "fifo_chain_step2_destination_area_ids": destination_area_ids_by_step[2],
        "fifo_chain_step2_material": fifo_chain_step2_material,
        "fifo_chain_step2_material_group_id": fifo_chain_step2_material_group_id,
        "fifo_chain_step2_task_typ": fifo_chain_task_typ_by_step[2],
        "fifo_chain_step3_source_mode": fifo_chain_step3_source_mode,
        "fifo_chain_step3_source_area_ids": source_area_ids_by_step[3],
        "fifo_chain_step3_destination_mode": destination_modes[3],
        "fifo_chain_step3_destination_area_ids": destination_area_ids_by_step[3],
        "fifo_chain_step3_material": fifo_chain_step3_material,
        "fifo_chain_step3_material_group_id": fifo_chain_step3_material_group_id,
        "fifo_chain_step3_task_typ": fifo_chain_task_typ_by_step[3],
        "fifo_chain_step3_source_area": fifo_chain_step3_source_area,
        "fifo_chain_step3_destination_area": fifo_chain_step3_destination_area,
        "fifo_chain_step3_source_cell": fifo_chain_step3_source_cell,
        "fifo_chain_step3_destination_cell": fifo_chain_step3_destination_cell,
        "fifo_chain_step3_source_cell_config_id": fifo_chain_step3_source_cell_id,
        "fifo_chain_step3_destination_cell_config_id": fifo_chain_step3_destination_cell_id,
        "fifo_chain_step4_source_mode": fifo_chain_step4_source_mode,
        "fifo_chain_step4_source_area_ids": source_area_ids_by_step[4],
        "fifo_chain_step4_destination_mode": destination_modes[4],
        "fifo_chain_step4_destination_area_ids": destination_area_ids_by_step[4],
        "fifo_chain_step4_material": fifo_chain_step4_material,
        "fifo_chain_step4_material_group_id": fifo_chain_step4_material_group_id,
        "fifo_chain_step4_task_typ": fifo_chain_task_typ_by_step[4],
        "fifo_chain_step4_source_area": fifo_chain_step4_source_area,
        "fifo_chain_step4_destination_area": fifo_chain_step4_destination_area,
        "fifo_chain_step4_source_cell": fifo_chain_step4_source_cell,
        "fifo_chain_step4_destination_cell": fifo_chain_step4_destination_cell,
        "fifo_chain_step4_source_cell_config_id": fifo_chain_step4_source_cell_id,
        "fifo_chain_step4_destination_cell_config_id": fifo_chain_step4_destination_cell_id,
        "fifo_chain_step5_source_mode": fifo_chain_step5_source_mode,
        "fifo_chain_step5_source_area_ids": source_area_ids_by_step[5],
        "fifo_chain_step5_destination_mode": destination_modes[5],
        "fifo_chain_step5_destination_area_ids": destination_area_ids_by_step[5],
        "fifo_chain_step5_material": fifo_chain_step5_material,
        "fifo_chain_step5_material_group_id": fifo_chain_step5_material_group_id,
        "fifo_chain_step5_task_typ": fifo_chain_task_typ_by_step[5],
        "fifo_chain_step5_source_area": fifo_chain_step5_source_area,
        "fifo_chain_step5_destination_area": fifo_chain_step5_destination_area,
        "fifo_chain_step5_source_cell": fifo_chain_step5_source_cell,
        "fifo_chain_step5_destination_cell": fifo_chain_step5_destination_cell,
        "fifo_chain_step5_source_cell_config_id": fifo_chain_step5_source_cell_id,
        "fifo_chain_step5_destination_cell_config_id": fifo_chain_step5_destination_cell_id,
        "second_source_area": second_source_area,
        "second_destination_area": second_destination_area,
        "second_source_cell": second_source_cell,
        "second_destination_cell": second_destination_cell,
        "second_source_cell_config_id": second_source_cell_id,
        "second_destination_cell_config_id": second_destination_cell_id,
        "scanner_station_id": getattr(station, "id", None) if station else None,
        "qr_action_rule_id": getattr(qr_rule, "id", None) if qr_rule else None,
    }
    if action == "fifo_request" and route_mode == "fifo_chain":
        entities["fifo_chain_projection"] = _validate_fifo_chain_projected_state(db, entities)
        if fifo_chain_step1_source_mode != "configured_area" and not entities.get("source_cell"):
            entities["route_points"] = []
        else:
            entities["route_points"] = _build_route_points(db, "simple_area", entities)
        entities["fifo_chain_steps"] = _build_fifo_chain_steps(db, entities)
    else:
        entities["route_points"] = _build_route_points(db, route_mode, entities) if action == "fifo_request" else []
        entities["fifo_chain_steps"] = []
    return entities


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
    route_points = None
    if getattr(row, "route_points_json", None):
        try:
            route_points = json.loads(row.route_points_json)
        except Exception:
            route_points = None
    return {
        "id": row.id,
        "order_code": row.order_code,
        "status": row.status,
        "dispatch_status": row.dispatch_status,
        "rcs_status": row.rcs_status,
        "rcs_message": row.rcs_message,
        "remote_task_code": row.remote_task_code,
        "route_mode": "fifo_chain" if getattr(row, "route_mode", None) == "trmx_doble" else (getattr(row, "route_mode", None) or "simple_area"),
        "route_points": route_points,
        "fifo_chain_group_id": getattr(row, "fifo_chain_group_id", None),
        "fifo_chain_step": getattr(row, "fifo_chain_step", None),
        "fifo_chain_total_steps": getattr(row, "fifo_chain_total_steps", None),
        "fifo_chain_status": getattr(row, "fifo_chain_status", None),
        "fifo_chain_select_policy": getattr(row, "fifo_chain_select_policy", None),
    }


def _fifo_chain_next_config_payload(
    *,
    station: ScannerStation,
    qr_rule: QrActionRule,
    terminal: Optional[ScanTerminal],
    qr_value: str,
    fifo_material_policy: str,
    material_id,
    source_mode: str,
    step2_material_group_id,
    second_source_area: Optional[Area],
    second_destination_area: Optional[Area],
    second_source_cell: Optional[Location],
    second_destination_cell: Optional[Location],
    second_source_cell_config_id,
    second_destination_cell_config_id,
    agv_code,
    task_typ,
    priority,
    fifo_chain_group_id: str,
    total_steps: int,
    step3_source_mode: str,
    step3_material_group_id,
    step3_source_area: Optional[Area],
    step3_destination_area: Optional[Area],
    step3_source_cell: Optional[Location],
    step3_destination_cell: Optional[Location],
    step3_source_cell_config_id,
    step3_destination_cell_config_id,
    step4_source_mode: str,
    step4_material_group_id,
    step4_source_area: Optional[Area],
    step4_destination_area: Optional[Area],
    step4_source_cell: Optional[Location],
    step4_destination_cell: Optional[Location],
    step4_source_cell_config_id,
    step4_destination_cell_config_id,
    step5_source_mode: str,
    step5_material_group_id,
    step5_source_area: Optional[Area],
    step5_destination_area: Optional[Area],
    step5_source_cell: Optional[Location],
    step5_destination_cell: Optional[Location],
    step5_source_cell_config_id,
    step5_destination_cell_config_id,
    allow_same_cell_move: bool = False,
) -> dict:
    step_task_typs = {
        step: _fifo_chain_step_task_typ_value(station, qr_rule, step) or _clean_task_typ_value(task_typ)
        for step in range(1, 6)
    }
    step2 = {
        "route_mode": "fifo_chain",
        "step": 2,
        "source_mode": source_mode,
        "source_area_id": getattr(second_source_area, "id", None) if source_mode == "configured_area" else None,
        "destination_area_id": getattr(second_destination_area, "id", None),
        "source_cell_id": second_source_cell_config_id if source_mode == "configured_area" else None,
        "destination_cell_id": second_destination_cell_config_id,
        "source_cell_configured": bool(second_source_cell_config_id) if source_mode == "configured_area" else False,
        "destination_cell_configured": bool(second_destination_cell_config_id),
        "source_area": _area_payload(second_source_area) if source_mode == "configured_area" else {},
        "destination_area": _area_payload(second_destination_area),
        "source_cell": _cell_payload(second_source_cell) if source_mode == "configured_area" and second_source_cell_config_id else {},
        "destination_cell": _cell_payload(second_destination_cell) if second_destination_cell_config_id else {},
        "fifo_material_policy": fifo_material_policy,
        "allow_same_cell_move": bool(allow_same_cell_move),
        "material_group_id": step2_material_group_id or material_id,
        "step2_material_group_id": step2_material_group_id,
        "agv_code": agv_code,
        "task_typ": step_task_typs[2],
        "priority": priority,
        "scanner_station_id": getattr(station, "id", None),
        "scanner_code": getattr(station, "scanner_code", None),
        "qr_action_rule_id": getattr(qr_rule, "id", None),
        "qr_value": qr_value,
        "terminal_id": getattr(terminal, "id", None) if terminal else None,
        "terminal_code": getattr(terminal, "terminal_code", None) if terminal else None,
        "fifo_chain_group_id": fifo_chain_group_id,
        "fifo_chain_parent_order_id": None,
    }
    step2.update(_fifo_chain_step_flexible_config(station, qr_rule, 2))
    steps = [step2]
    if total_steps >= 3:
        steps.append({
            **{key: value for key, value in step2.items() if key not in {
                "step", "source_mode", "source_area_id", "destination_area_id", "source_cell_id", "destination_cell_id",
                "source_cell_configured", "destination_cell_configured",
                "source_area", "destination_area", "source_cell", "destination_cell", "material_group_id", "step2_material_group_id",
                "task_typ",
            }},
            "step": 3,
            "task_typ": step_task_typs[3],
            "source_mode": step3_source_mode,
            "source_area_id": getattr(step3_source_area, "id", None) if step3_source_mode == "configured_area" else None,
            "destination_area_id": getattr(step3_destination_area, "id", None),
            "source_cell_id": step3_source_cell_config_id if step3_source_mode == "configured_area" else None,
            "destination_cell_id": step3_destination_cell_config_id,
            "source_cell_configured": bool(step3_source_cell_config_id) if step3_source_mode == "configured_area" else False,
            "destination_cell_configured": bool(step3_destination_cell_config_id),
            "source_area": _area_payload(step3_source_area) if step3_source_mode == "configured_area" else {},
            "destination_area": _area_payload(step3_destination_area),
            "source_cell": _cell_payload(step3_source_cell) if step3_source_mode == "configured_area" and step3_source_cell_config_id else {},
            "destination_cell": _cell_payload(step3_destination_cell) if step3_destination_cell_config_id else {},
            "material_group_id": step3_material_group_id or material_id,
            **_fifo_chain_step_flexible_config(station, qr_rule, 3),
        })
    if total_steps >= 4:
        steps.append({
            **{key: value for key, value in step2.items() if key not in {
                "step", "source_mode", "source_area_id", "destination_area_id", "source_cell_id", "destination_cell_id",
                "source_cell_configured", "destination_cell_configured",
                "source_area", "destination_area", "source_cell", "destination_cell", "material_group_id", "step2_material_group_id",
                "task_typ",
            }},
            "step": 4,
            "task_typ": step_task_typs[4],
            "source_mode": step4_source_mode,
            "source_area_id": getattr(step4_source_area, "id", None) if step4_source_mode == "configured_area" else None,
            "destination_area_id": getattr(step4_destination_area, "id", None),
            "source_cell_id": step4_source_cell_config_id if step4_source_mode == "configured_area" else None,
            "destination_cell_id": step4_destination_cell_config_id,
            "source_cell_configured": bool(step4_source_cell_config_id) if step4_source_mode == "configured_area" else False,
            "destination_cell_configured": bool(step4_destination_cell_config_id),
            "source_area": _area_payload(step4_source_area) if step4_source_mode == "configured_area" else {},
            "destination_area": _area_payload(step4_destination_area),
            "source_cell": _cell_payload(step4_source_cell) if step4_source_mode == "configured_area" and step4_source_cell_config_id else {},
            "destination_cell": _cell_payload(step4_destination_cell) if step4_destination_cell_config_id else {},
            "material_group_id": step4_material_group_id or material_id,
            **_fifo_chain_step_flexible_config(station, qr_rule, 4),
        })
    if total_steps >= 5:
        steps.append({
            **{key: value for key, value in step2.items() if key not in {
                "step", "source_mode", "source_area_id", "destination_area_id", "source_cell_id", "destination_cell_id",
                "source_cell_configured", "destination_cell_configured",
                "source_area", "destination_area", "source_cell", "destination_cell", "material_group_id", "step2_material_group_id",
                "task_typ",
            }},
            "step": 5,
            "task_typ": step_task_typs[5],
            "source_mode": step5_source_mode,
            "source_area_id": getattr(step5_source_area, "id", None) if step5_source_mode == "configured_area" else None,
            "destination_area_id": getattr(step5_destination_area, "id", None),
            "source_cell_id": step5_source_cell_config_id if step5_source_mode == "configured_area" else None,
            "destination_cell_id": step5_destination_cell_config_id,
            "source_cell_configured": bool(step5_source_cell_config_id) if step5_source_mode == "configured_area" else False,
            "destination_cell_configured": bool(step5_destination_cell_config_id),
            "source_area": _area_payload(step5_source_area) if step5_source_mode == "configured_area" else {},
            "destination_area": _area_payload(step5_destination_area),
            "source_cell": _cell_payload(step5_source_cell) if step5_source_mode == "configured_area" and step5_source_cell_config_id else {},
            "destination_cell": _cell_payload(step5_destination_cell) if step5_destination_cell_config_id else {},
            "material_group_id": step5_material_group_id or material_id,
            **_fifo_chain_step_flexible_config(station, qr_rule, 5),
        })
    return {
        "current_step": 1,
        "next_step": 2,
        "total_steps": total_steps,
        "steps": steps,
        "chain": {
            "group_id": fifo_chain_group_id,
            "scanner_station_id": getattr(station, "id", None),
            "qr_action_rule_id": getattr(qr_rule, "id", None),
            "terminal_id": getattr(terminal, "id", None) if terminal else None,
        },
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


def _select_configured_source_for_fifo_chain_step1(db, *, source_area_id, source_cell_id, material_group_id) -> tuple[Rack, Location, Area, MaterialGroup]:
    source_area = db.execute(select(Area).where(Area.id == source_area_id)).scalar_one_or_none() if source_area_id else None
    if not source_area or int(getattr(source_area, "is_active", 0) or 0) != 1:
        raise HTTPException(status_code=400, detail="Area origen tramo 1 no encontrada o inactiva")
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_group_id)).scalar_one_or_none() if material_group_id else None
    if not material:
        raise HTTPException(status_code=400, detail="Material tramo 1 no encontrado")
    if source_cell_id:
        source_cell = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
        if not source_cell or getattr(source_cell, "area_id", None) != source_area.id:
            raise HTTPException(status_code=400, detail="Celda origen tramo 1 no encontrada en el area configurada")
        if int(getattr(source_cell, "enabled", 0) or 0) != 1 or int(getattr(source_cell, "is_visible", 0) or 0) != 1:
            raise HTTPException(status_code=400, detail="Celda origen tramo 1 no esta habilitada/visible")
        if not getattr(source_cell, "rack_id", None):
            raise HTTPException(status_code=400, detail="Celda origen tramo 1 no tiene rack disponible")
        rack = db.execute(select(Rack).where(Rack.id == source_cell.rack_id)).scalar_one_or_none()
        if not rack:
            raise HTTPException(status_code=400, detail="Rack origen tramo 1 no encontrado")
        if _normalize_rack_status(rack.status) not in ACTIVE_RACK_STATUSES:
            raise HTTPException(status_code=409, detail="Rack origen tramo 1 no esta disponible")
        if getattr(rack, "material_group_id", None) != material.id:
            raise HTTPException(status_code=400, detail="Rack origen tramo 1 no cumple el material requerido")
        return rack, source_cell, source_area, material
    rack, source_cell = _find_fifo_candidate(db, source_area, material)
    return rack, source_cell, source_area, material


def _execute_fifo_chain_step1_configured_source(
    db,
    *,
    source_area_id,
    source_cell_id,
    material_group_id,
    destination_area_id,
    destination_cell_id,
    destination_mode,
    destination_area_ids,
    priority,
    comment,
    created_by,
    agv_code,
    task_typ,
    allow_same_cell_move: bool = False,
) -> tuple[MovementOrder, FifoSelection]:
    rack, source_cell, source_area, material = _select_configured_source_for_fifo_chain_step1(
        db,
        source_area_id=source_area_id,
        source_cell_id=source_cell_id,
        material_group_id=material_group_id,
    )
    try:
        destination_area, destination_cell = resolve_destination_cell_for_step(
            db,
            destination_mode=destination_mode,
            destination_area_id=destination_area_id,
            destination_cell_id=destination_cell_id,
            destination_area_ids=destination_area_ids,
            next_step=1,
            allow_same_cell_move=allow_same_cell_move,
            same_cell_source_cell=source_cell,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    same_cell_move = source_cell.id == destination_cell.id
    if same_cell_move and not allow_same_cell_move:
        raise HTTPException(status_code=400, detail={"error_code": "same_source_destination_cell", "operator_message": "La celda origen y destino no pueden ser la misma, salvo que se habilite Permitir movimiento en la misma celda."})
    if source_cell.rack_id != rack.id or int(source_cell.enabled or 0) != 1 or int(source_cell.is_visible or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda origen real del tramo 1 ya no es valida")
    if (destination_cell.rack_id is not None and not (same_cell_move and destination_cell.rack_id == rack.id)) or int(destination_cell.enabled or 0) != 1 or int(destination_cell.is_visible or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda destino real del tramo 1 ya no esta disponible")

    now = datetime.utcnow()
    order = MovementOrder(
        order_code=f"MO-{now.strftime('%Y%m%d%H%M%S')}-{rack.id}",
        order_type="material_request",
        source_area_id=source_area.id,
        destination_area_id=destination_area.id,
        material_group_id=material.id,
        rack_id=rack.id,
        source_cell_id=source_cell.id,
        destination_cell_id=destination_cell.id,
        priority=priority,
        agv_code=(agv_code or "").strip() or None,
        task_typ=(task_typ or "").strip() or None,
        comment=(comment or "").strip() or None,
        status="pending_dispatch",
        created_by=(created_by or "qr").strip() or None,
        created_at=now,
        updated_at=now,
    )
    db.add(rack)
    db.add(source_cell)
    db.add(destination_cell)
    db.add(order)
    db.flush()
    apply_rack_reservation_status(rack, True, updated_at=now, order_id=order.id, dispatch_status=order.status, source="qr_fifo_chain", reason="fifo_chain_step1_configured_source_created")
    db.add(rack)
    db.commit()
    db.refresh(order)
    if same_cell_move:
        logger.info(
            "SAME_CELL_MOVE_ALLOWED movement_order_id=%s order_code=%s route_mode=%s source_cell_code=%s destination_cell_code=%s allow_same_cell_move=%s",
            order.id, order.order_code, "fifo_chain", source_cell.code, destination_cell.code, True,
        )
    return order, FifoSelection(
        rack=rack,
        source_cell=source_cell,
        destination_cell=destination_cell,
        source_area=source_area,
        destination_area=destination_area,
        material_group=material,
    )


def _execute_fifo_chain_step1_any_area_by_material(
    db,
    *,
    material_group_id,
    destination_area_id,
    destination_cell_id,
    source_mode,
    source_area_ids,
    destination_mode,
    destination_area_ids,
    priority,
    comment,
    created_by,
    agv_code,
    task_typ,
    allow_same_cell_move: bool = False,
) -> tuple[MovementOrder, FifoSelection]:
    try:
        if source_mode == "selected_areas_by_material":
            selected_rack, selected_source_cell, selected_source_area = resolve_fifo_request_by_material_selected_areas(
                db, material_group_id, source_area_ids
            )
        else:
            selected_rack, selected_source_cell, selected_source_area = resolve_fifo_request_by_material_any_area(db, material_group_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rack = db.execute(select(Rack).where(Rack.id == selected_rack.id)).scalar_one()
    source_cell = db.execute(select(Location).where(Location.id == selected_source_cell.id)).scalar_one()
    source_area = db.execute(select(Area).where(Area.id == selected_source_area.id)).scalar_one()
    material = db.execute(select(MaterialGroup).where(MaterialGroup.id == rack.material_group_id)).scalar_one_or_none() if rack.material_group_id else None
    if not material:
        material = db.execute(select(MaterialGroup).where(MaterialGroup.id == material_group_id)).scalar_one_or_none()
    if not material:
        raise HTTPException(status_code=400, detail="Material tramo 1 no encontrado")
    if source_cell.rack_id != rack.id or int(source_cell.enabled or 0) != 1 or int(source_cell.is_visible or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda origen real del tramo 1 ya no es valida")
    try:
        destination_area, destination_cell = resolve_destination_cell_for_step(
            db,
            destination_mode=destination_mode,
            destination_area_id=destination_area_id,
            destination_cell_id=destination_cell_id,
            destination_area_ids=destination_area_ids,
            next_step=1,
            allow_same_cell_move=allow_same_cell_move,
            same_cell_source_cell=source_cell,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    same_cell_move = source_cell.id == destination_cell.id
    if same_cell_move and not allow_same_cell_move:
        raise HTTPException(status_code=400, detail={"error_code": "same_source_destination_cell", "operator_message": "La celda origen y destino no pueden ser la misma, salvo que se habilite Permitir movimiento en la misma celda."})

    now = datetime.utcnow()
    order = MovementOrder(
        order_code=f"MO-{now.strftime('%Y%m%d%H%M%S')}-{rack.id}",
        order_type="material_request",
        source_area_id=source_area.id,
        destination_area_id=destination_area.id,
        material_group_id=material.id,
        rack_id=rack.id,
        source_cell_id=source_cell.id,
        destination_cell_id=destination_cell.id,
        priority=priority,
        agv_code=(agv_code or "").strip() or None,
        task_typ=(task_typ or "").strip() or None,
        comment=(comment or "").strip() or None,
        status="pending_dispatch",
        created_by=(created_by or "qr").strip() or None,
        created_at=now,
        updated_at=now,
    )
    db.add(rack)
    db.add(source_cell)
    db.add(destination_cell)
    db.add(order)
    db.flush()
    apply_rack_reservation_status(rack, True, updated_at=now, order_id=order.id, dispatch_status=order.status, source="qr_fifo_chain", reason="fifo_chain_step1_any_area_by_material_created")
    db.add(rack)
    db.commit()
    db.refresh(order)
    if same_cell_move:
        logger.info(
            "SAME_CELL_MOVE_ALLOWED movement_order_id=%s order_code=%s route_mode=%s source_cell_code=%s destination_cell_code=%s allow_same_cell_move=%s",
            order.id, order.order_code, "fifo_chain", source_cell.code, destination_cell.code, True,
        )

    return order, FifoSelection(
        rack=rack,
        source_cell=source_cell,
        destination_cell=destination_cell,
        source_area=source_area,
        destination_area=destination_area,
        material_group=material,
    )


def _save_execute_rejected_event(db, *, scanner_code, qr_value, created_by=None, station=None, qr_rule=None, parsed=None, action=None, terminal=None, message="Ejecucion rechazada", status="rejected"):
    execution_id = _new_qr_execution_id()
    started_at = time.perf_counter()
    _qr_trace("QR_SCAN_RECEIVED", execution_id, stage="received", mode="execute", scanner_code=scanner_code, terminal_id=getattr(terminal, "id", None), terminal_code=getattr(terminal, "terminal_code", None), qr_value=qr_value)
    result = {
        "ok": False,
        "execution_id": execution_id,
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
        request_payload={"scanner_code": scanner_code, "qr_value": qr_value, "mode": "execute", "terminal_code": getattr(terminal, "terminal_code", None), "execution_id": execution_id},
        result_payload=result,
        terminal=terminal,
        mode="execute",
    )
    result["scan_event_id"] = event.id
    _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="terminal_rejected", mode="execute", scan_event_id=event.id, scanner_code=scanner_code, terminal_id=getattr(terminal, "id", None), terminal_code=getattr(terminal, "terminal_code", None), error_code="TERMINAL_NOT_ALLOWED", operator_message=message, total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
    return result


def preview_scan(db, scanner_code, qr_value, created_by=None, terminal=None):
    execution_id = _new_qr_execution_id()
    started_at = time.perf_counter()
    preflight_started_at = None
    request_payload = {"scanner_code": scanner_code, "qr_value": qr_value, "created_by": created_by, "execution_id": execution_id, "mode": "preview"}
    normalized_qr = normalize_qr_value(qr_value)
    station = None
    qr_rule = None
    parsed = None
    action = None
    entities = {}
    _qr_trace("QR_SCAN_RECEIVED", execution_id, stage="received", mode="preview", scanner_code=scanner_code, terminal_id=getattr(terminal, "id", None), terminal_code=getattr(terminal, "terminal_code", None), qr_value=normalized_qr)
    try:
        if not normalized_qr:
            raise ScanPreviewError("qr_value requerido", "rejected")
        station = resolve_scanner_station(db, scanner_code)
        qr_rule = resolve_qr_action_rule(db, normalized_qr)
        parsed = parse_qr_value(normalized_qr)
        action = resolve_scan_action(db, station, qr_rule, parsed)
        _qr_trace("QR_RULE_RESOLVED", execution_id, stage="rule_resolved", scanner_station_id=station.id, scanner_code=station.scanner_code, qr_action_rule_id=getattr(qr_rule, "id", None), qr_alias=getattr(qr_rule, "qr_alias", None), qr_value=normalized_qr, action=action)
        preflight_started_at = time.perf_counter()
        _qr_trace("QR_PREFLIGHT_STARTED", execution_id, stage="preflight", route_mode=getattr(qr_rule, "route_mode", None) or getattr(station, "route_mode", None))
        entities = _resolve_entities(db, station, qr_rule, parsed, action)
        preflight_duration_ms = round((time.perf_counter() - preflight_started_at) * 1000, 2)
        _qr_trace("QR_PREFLIGHT_OK", execution_id, stage="preflight", preflight_duration_ms=preflight_duration_ms, rack_id=getattr(entities.get("rack"), "id", None), rack_code=getattr(entities.get("rack"), "code", None), source_area_id=getattr(entities.get("source_area"), "id", None), source_cell_id=getattr(entities.get("source_cell"), "id", None), destination_area_id=getattr(entities.get("destination_area"), "id", None), destination_cell_id=getattr(entities.get("destination_cell"), "id", None))
        result = {
            "ok": True,
            "execution_id": execution_id,
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
            "route_mode": entities.get("route_mode") or "simple_area",
            "allow_same_cell_move": bool(entities.get("allow_same_cell_move")),
            "same_cell_move": bool(
                getattr(entities.get("source_cell"), "id", None)
                and getattr(entities.get("source_cell"), "id", None) == getattr(entities.get("destination_cell"), "id", None)
            ),
            "same_cell_move_message": (
                f"Movimiento en la misma celda permitido por configuraciÃ³n: {getattr(entities.get('source_cell'), 'code', None)} -> {getattr(entities.get('destination_cell'), 'code', None)}."
                if getattr(entities.get("source_cell"), "id", None)
                and getattr(entities.get("source_cell"), "id", None) == getattr(entities.get("destination_cell"), "id", None)
                else None
            ),
            "fifo_material_policy": entities.get("fifo_material_policy") or "specific_material",
            "fifo_chain_total_steps": entities.get("fifo_chain_total_steps") or 2,
            "fifo_chain_step1_source_mode": entities.get("fifo_chain_step1_source_mode") or "configured_area",
            "fifo_chain_step1_task_typ": entities.get("fifo_chain_step1_task_typ"),
            "fifo_chain_step1_material": _material_payload(entities.get("fifo_chain_step1_material")),
            "fifo_chain_step2_source_mode": entities.get("fifo_chain_step2_source_mode") or "configured_area",
            "fifo_chain_step2_task_typ": entities.get("fifo_chain_step2_task_typ"),
            "fifo_chain_step2_material": _material_payload(entities.get("fifo_chain_step2_material")),
            "fifo_chain_step3_source_mode": entities.get("fifo_chain_step3_source_mode") or "configured_area",
            "fifo_chain_step3_task_typ": entities.get("fifo_chain_step3_task_typ"),
            "fifo_chain_step3_material": _material_payload(entities.get("fifo_chain_step3_material")),
            "fifo_chain_step3_source": {"area": _area_payload(entities.get("fifo_chain_step3_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_source_cell"))},
            "fifo_chain_step3_destination": {"area": _area_payload(entities.get("fifo_chain_step3_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_destination_cell"))},
            "fifo_chain_step4_source_mode": entities.get("fifo_chain_step4_source_mode") or "configured_area",
            "fifo_chain_step4_task_typ": entities.get("fifo_chain_step4_task_typ"),
            "fifo_chain_step4_material_group_id": entities.get("fifo_chain_step4_material_group_id"),
            "fifo_chain_step4_material_group_name": getattr(entities.get("fifo_chain_step4_material"), "name", None),
            "fifo_chain_step4_material": _material_payload(entities.get("fifo_chain_step4_material")),
            "fifo_chain_step4_source": {"area": _area_payload(entities.get("fifo_chain_step4_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_source_cell"))},
            "fifo_chain_step4_destination": {"area": _area_payload(entities.get("fifo_chain_step4_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_destination_cell"))},
            "fifo_chain_step5_source_mode": entities.get("fifo_chain_step5_source_mode") or "configured_area",
            "fifo_chain_step5_task_typ": entities.get("fifo_chain_step5_task_typ"),
            "fifo_chain_step5_material_group_id": entities.get("fifo_chain_step5_material_group_id"),
            "fifo_chain_step5_material_group_name": getattr(entities.get("fifo_chain_step5_material"), "name", None),
            "fifo_chain_step5_material": _material_payload(entities.get("fifo_chain_step5_material")),
            "fifo_chain_step5_source": {"area": _area_payload(entities.get("fifo_chain_step5_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step5_source_cell"))},
            "fifo_chain_step5_destination": {"area": _area_payload(entities.get("fifo_chain_step5_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step5_destination_cell"))},
            "route_points": entities.get("route_points") or [],
            "fifo_chain_steps": entities.get("fifo_chain_steps") or [],
            "fifo_chain_projection": entities.get("fifo_chain_projection") or {},
            "fifo_chain_preflight_checklist": (entities.get("fifo_chain_projection") or {}).get("fifo_chain_preflight_checklist") or {},
            "message": "Preview correcto. No se creo orden.",
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="preview_ok", request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        _qr_trace("QR_EXECUTION_COMPLETED", execution_id, stage="completed", mode="preview", scan_event_id=event.id, qr_value=normalized_qr, qr_action_rule_id=getattr(qr_rule, "id", None), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2), preflight_duration_ms=preflight_duration_ms)
        return result
    except ScanPreviewError as exc:
        diagnosis = _build_operator_diagnosis(db, message=exc.message, details=exc.details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {
            "ok": False,
            "execution_id": execution_id,
            "mode": "preview",
            "scanner": {"scanner_code": str(scanner_code or "").strip()},
            "qr": {"qr_value": normalized_qr},
            "action": action,
            "message": diagnosis["operator_message"],
        }
        result.update(exc.details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["error_code"] = _qr_error_code(exc.message, result["error_code"])
        result["operator_diagnosis"] = diagnosis
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=exc.status, error_message=exc.message, request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        _qr_trace("QR_PREFLIGHT_FAILED", execution_id, stage="preflight", scan_event_id=event.id, error_code=result.get("error_code") or _qr_error_code(exc.message), operator_message=diagnosis["operator_message"], raw_error=exc.message, preflight_duration_ms=round((time.perf_counter() - (preflight_started_at or started_at)) * 1000, 2))
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="failed", mode="preview", scan_event_id=event.id, error_code=result.get("error_code") or _qr_error_code(exc.message), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result
    except HTTPException as exc:
        details = dict(exc.detail) if isinstance(exc.detail, dict) else {}
        message = details.get("operator_message") or str(exc.detail)
        diagnosis = _build_operator_diagnosis(db, message=message, details=details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {"ok": False, "execution_id": execution_id, "mode": "preview", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "qr": {"qr_value": normalized_qr}, "action": action, "message": diagnosis["operator_message"]}
        result.update(details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["error_code"] = _qr_error_code(message, result["error_code"])
        result["operator_diagnosis"] = diagnosis
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="error", error_message=message, request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        _qr_trace("QR_PREFLIGHT_FAILED", execution_id, stage="preflight", scan_event_id=event.id, error_code=result.get("error_code") or _qr_error_code(message), operator_message=diagnosis["operator_message"], raw_error=message)
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="failed", mode="preview", scan_event_id=event.id, error_code=result.get("error_code") or _qr_error_code(message), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result
    except Exception as exc:
        error_code = _qr_error_code(exc, "UNEXPECTED_QR_ERROR")
        logger.exception("QR_EXECUTION_FAILED execution_id=%s mode=preview stage=unexpected error_code=%s", execution_id, error_code)
        result = {"ok": False, "execution_id": execution_id, "mode": "preview", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "qr": {"qr_value": normalized_qr}, "action": action, "error_code": error_code, "operator_message": "No se pudo completar el Preview QR.", "message": "No se pudo completar el Preview QR."}
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="error", error_message=str(exc)[:512], request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="unexpected", mode="preview", scan_event_id=event.id, error_code=error_code, operator_message=result["operator_message"], raw_error=str(exc), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result


def execute_scan(db, scanner_code, qr_value, created_by=None, terminal=None, dispatch_func=None, rollback_func=None, audit_rack_func=None):
    execution_id = _new_qr_execution_id()
    started_at = time.perf_counter()
    preflight_started_at = None
    order_started_at = None
    dispatch_started_at = None
    rollback_started_at = None
    request_payload = {"scanner_code": scanner_code, "qr_value": qr_value, "created_by": created_by, "mode": "execute", "execution_id": execution_id}
    normalized_qr = normalize_qr_value(qr_value)
    station = None
    qr_rule = None
    parsed = None
    action = None
    entities = {}
    preflight_entities = {}
    created_order = None
    dispatch_payload = {}
    dispatch_status = "not_sent"
    rollback_result = None
    _qr_trace("QR_SCAN_RECEIVED", execution_id, stage="received", mode="execute", scanner_code=scanner_code, terminal_id=getattr(terminal, "id", None), terminal_code=getattr(terminal, "terminal_code", None), qr_value=normalized_qr)
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
        _qr_trace("QR_RULE_RESOLVED", execution_id, stage="rule_resolved", scanner_station_id=station.id, scanner_code=station.scanner_code, qr_action_rule_id=qr_rule.id, qr_alias=qr_rule.qr_alias, qr_value=normalized_qr, action=action, route_mode=getattr(qr_rule, "route_mode", None) or getattr(station, "route_mode", None))
        preflight_started_at = time.perf_counter()
        _qr_trace("QR_PREFLIGHT_STARTED", execution_id, stage="preflight", scanner_station_id=station.id, qr_action_rule_id=qr_rule.id, qr_value=normalized_qr)
        if action != "fifo_request":
            raise ScanPreviewError("En esta fase solo esta habilitada la ejecucion QR para fifo_request.", "rejected")

        duplicate = _recent_duplicate_scan_event(db, station.scanner_code, normalized_qr)
        if duplicate:
            result = {
                "ok": False,
                "execution_id": execution_id,
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
            _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="deduplication", scan_event_id=event.id, error_code="DUPLICATE_SCAN", operator_message=result["message"], total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
            return result

        fifo_material_policy = _fifo_material_policy_value(station, qr_rule)
        route_mode = _route_mode_value(station, qr_rule)
        fifo_chain_total_steps = _fifo_chain_total_steps_value(station, qr_rule)
        fifo_chain_step1_source_mode = _fifo_chain_step1_source_mode_value(station, qr_rule)
        fifo_chain_step1_material_group_id = _fifo_chain_step1_material_group_id_value(station, qr_rule)
        material_id = _first_value(getattr(qr_rule, "material_group_id", None), getattr(station, "default_material_group_id", None))
        if route_mode == "fifo_chain":
            fifo_material_policy = "specific_material"
            material_id = fifo_chain_step1_material_group_id
        if route_mode == "fifo_chain" and not fifo_chain_step1_material_group_id:
            raise ScanPreviewError("El tramo 1 requiere Material requerido tramo 1.", "rejected")
        if fifo_material_policy == "specific_material" and not material_id:
            raise ScanPreviewError("fifo_request requiere material_group_id configurado en QR o scanner.", "rejected")
        source_area_id = _first_value(getattr(qr_rule, "source_area_id", None), getattr(station, "source_area_id", None))
        destination_area_id = _first_value(getattr(qr_rule, "destination_area_id", None), getattr(station, "destination_area_id", None))
        source_cell_id = _first_value(getattr(qr_rule, "source_cell_id", None), getattr(station, "source_cell_id", None))
        destination_cell_id = _first_value(getattr(qr_rule, "destination_cell_id", None), getattr(station, "destination_cell_id", None))
        if not source_area_id and source_cell_id:
            source_cell_for_area = db.execute(select(Location).where(Location.id == source_cell_id)).scalar_one_or_none()
            source_area_id = getattr(source_cell_for_area, "area_id", None)
        if not destination_area_id and destination_cell_id:
            destination_cell_for_area = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none()
            destination_area_id = getattr(destination_cell_for_area, "area_id", None)
        if not source_area_id and not (route_mode == "fifo_chain" and fifo_chain_step1_source_mode != "configured_area"):
            raise ScanPreviewError("Scanner sin area origen configurada para FIFO.", "rejected")
        if not destination_area_id and not (
            route_mode == "fifo_chain"
            and _fifo_chain_step_destination_mode_value(station, qr_rule, 1) != "configured_area"
        ):
            raise ScanPreviewError("Scanner sin destino configurado para FIFO.", "rejected")

        priority = _priority_from_int(_first_value(getattr(qr_rule, "priority", None), getattr(station, "priority", None)))
        agv_code = _first_value(getattr(qr_rule, "agv_code", None), getattr(station, "agv_code", None))
        task_typ = _clean_task_typ_value(_first_value(getattr(qr_rule, "task_typ", None), getattr(station, "task_typ", None)))
        fifo_chain_task_typ_by_step = {
            step: _fifo_chain_step_task_typ_value(station, qr_rule, step) for step in range(1, 6)
        }
        step1_task_typ = fifo_chain_task_typ_by_step[1] if route_mode == "fifo_chain" else task_typ
        fifo_chain_step1_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step1_material_group_id)).scalar_one_or_none() if fifo_chain_step1_material_group_id else None
        if route_mode == "fifo_chain" and not fifo_chain_step1_material:
            raise ScanPreviewError("El tramo 1 requiere Material requerido tramo 1.", "rejected")
        fifo_chain_step2_source_mode = _fifo_chain_step2_source_mode_value(station, qr_rule)
        fifo_chain_step2_material_group_id = _fifo_chain_step2_material_group_id_value(station, qr_rule)
        fifo_chain_step2_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step2_material_group_id)).scalar_one_or_none() if fifo_chain_step2_material_group_id else None
        fifo_chain_step3_source_mode = _fifo_chain_step3_source_mode_value(station, qr_rule)
        fifo_chain_step3_material_group_id = _fifo_chain_step3_material_group_id_value(station, qr_rule)
        fifo_chain_step3_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step3_material_group_id)).scalar_one_or_none() if fifo_chain_step3_material_group_id else None
        fifo_chain_step4_source_mode = _fifo_chain_step4_source_mode_value(station, qr_rule)
        fifo_chain_step4_material_group_id = _fifo_chain_step4_material_group_id_value(station, qr_rule)
        fifo_chain_step4_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step4_material_group_id)).scalar_one_or_none() if fifo_chain_step4_material_group_id else None
        fifo_chain_step5_source_mode = _fifo_chain_step5_source_mode_value(station, qr_rule)
        fifo_chain_step5_material_group_id = _fifo_chain_step5_material_group_id_value(station, qr_rule)
        fifo_chain_step5_material = db.execute(select(MaterialGroup).where(MaterialGroup.id == fifo_chain_step5_material_group_id)).scalar_one_or_none() if fifo_chain_step5_material_group_id else None
        fifo_chain_step3_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_source_area_id")
        fifo_chain_step3_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_destination_area_id")
        fifo_chain_step3_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_source_cell_id")
        fifo_chain_step3_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_destination_cell_id")
        fifo_chain_step4_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_source_area_id")
        fifo_chain_step4_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_destination_area_id")
        fifo_chain_step4_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_source_cell_id")
        fifo_chain_step4_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_destination_cell_id")
        fifo_chain_step5_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step5_source_area_id")
        fifo_chain_step5_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step5_destination_area_id")
        fifo_chain_step5_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step5_source_cell_id")
        fifo_chain_step5_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step5_destination_cell_id")
        if fifo_chain_step3_source_mode != "configured_area":
            fifo_chain_step3_source_area_id = None
            fifo_chain_step3_source_cell_id = None
        if fifo_chain_step4_source_mode != "configured_area":
            fifo_chain_step4_source_area_id = None
            fifo_chain_step4_source_cell_id = None
        if fifo_chain_step5_source_mode != "configured_area":
            fifo_chain_step5_source_area_id = None
            fifo_chain_step5_source_cell_id = None
        fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_area_id)).scalar_one_or_none() if fifo_chain_step3_source_area_id else None
        fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_area_id)).scalar_one_or_none() if fifo_chain_step3_destination_area_id else None
        fifo_chain_step3_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_source_cell_id)).scalar_one_or_none() if fifo_chain_step3_source_cell_id else None
        fifo_chain_step3_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_destination_cell_id)).scalar_one_or_none() if fifo_chain_step3_destination_cell_id else None
        fifo_chain_step4_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_source_area_id)).scalar_one_or_none() if fifo_chain_step4_source_area_id else None
        fifo_chain_step4_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_destination_area_id)).scalar_one_or_none() if fifo_chain_step4_destination_area_id else None
        fifo_chain_step4_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_source_cell_id)).scalar_one_or_none() if fifo_chain_step4_source_cell_id else None
        fifo_chain_step4_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_destination_cell_id)).scalar_one_or_none() if fifo_chain_step4_destination_cell_id else None
        fifo_chain_step5_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_source_area_id)).scalar_one_or_none() if fifo_chain_step5_source_area_id else None
        fifo_chain_step5_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step5_destination_area_id)).scalar_one_or_none() if fifo_chain_step5_destination_area_id else None
        fifo_chain_step5_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step5_source_cell_id)).scalar_one_or_none() if fifo_chain_step5_source_cell_id else None
        fifo_chain_step5_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step5_destination_cell_id)).scalar_one_or_none() if fifo_chain_step5_destination_cell_id else None
        if fifo_chain_step3_source_cell and not fifo_chain_step3_source_area and fifo_chain_step3_source_cell.area_id:
            fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_cell.area_id)).scalar_one_or_none()
        if fifo_chain_step3_destination_cell and not fifo_chain_step3_destination_area and fifo_chain_step3_destination_cell.area_id:
            fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_cell.area_id)).scalar_one_or_none()
        if route_mode == "fifo_chain" and fifo_chain_step2_source_mode != "configured_area" and not fifo_chain_step2_material:
            raise ScanPreviewError("El tramo 2 por material requiere Material requerido tramo 2.", "rejected")
        second_source_area = None
        second_destination_area = None
        second_source_cell = None
        second_destination_cell = None
        if route_mode in {"double_area", "fifo_chain"}:
            second_source_area_id = _first_value(getattr(qr_rule, "second_source_area_id", None), getattr(station, "second_source_area_id", None))
            second_destination_area_id = _first_value(getattr(qr_rule, "second_destination_area_id", None), getattr(station, "second_destination_area_id", None))
            second_source_cell_id = _first_value(getattr(qr_rule, "second_source_cell_id", None), getattr(station, "second_source_cell_id", None))
            second_destination_cell_id = _first_value(getattr(qr_rule, "second_destination_cell_id", None), getattr(station, "second_destination_cell_id", None))
            if route_mode == "fifo_chain" and fifo_chain_step2_source_mode != "configured_area":
                second_source_area = None
                second_source_cell = None
            else:
                second_source_area, second_source_cell = _resolve_route_cell_from_config(db, cell_id=second_source_cell_id, area_id=second_source_area_id, label="Punto origen 2", destination=False)
            if route_mode != "fifo_chain" or _fifo_chain_step_destination_mode_value(station, qr_rule, 2) == "configured_area":
                second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
                    db, cell_id=second_destination_cell_id, area_id=second_destination_area_id,
                    label="Punto destino 2", destination=True,
                    allow_occupied_destination=route_mode == "fifo_chain",
                )
        if route_mode == "fifo_chain" and fifo_chain_total_steps >= 3:
            if fifo_chain_step3_source_mode != "configured_area":
                if not fifo_chain_step3_material:
                    raise ScanPreviewError("El tramo 3 por material requiere Material requerido tramo 3.", "rejected")
            else:
                fifo_chain_step3_source_area, fifo_chain_step3_source_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step3_source_cell_id, area_id=fifo_chain_step3_source_area_id, label="Punto origen 3", destination=False
                )
            if _fifo_chain_step_destination_mode_value(station, qr_rule, 3) == "configured_area":
                fifo_chain_step3_destination_area, fifo_chain_step3_destination_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step3_destination_cell_id, area_id=fifo_chain_step3_destination_area_id,
                    label="Punto destino 3", destination=True, allow_occupied_destination=True,
                )
        if route_mode == "fifo_chain" and fifo_chain_total_steps >= 4:
            if fifo_chain_step4_source_mode != "configured_area":
                if not fifo_chain_step4_material:
                    raise ScanPreviewError("El tramo 4 por material requiere Material requerido tramo 4.", "rejected")
            else:
                fifo_chain_step4_source_area, fifo_chain_step4_source_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step4_source_cell_id, area_id=fifo_chain_step4_source_area_id, label="Punto origen 4", destination=False
                )
            if _fifo_chain_step_destination_mode_value(station, qr_rule, 4) == "configured_area":
                fifo_chain_step4_destination_area, fifo_chain_step4_destination_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step4_destination_cell_id, area_id=fifo_chain_step4_destination_area_id,
                    label="Punto destino 4", destination=True, allow_occupied_destination=True,
                )
        if route_mode == "fifo_chain" and fifo_chain_total_steps >= 5:
            if fifo_chain_step5_source_mode != "configured_area":
                if not fifo_chain_step5_material:
                    raise ScanPreviewError("El tramo 5 por material requiere Material requerido tramo 5.", "rejected")
            else:
                fifo_chain_step5_source_area, fifo_chain_step5_source_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step5_source_cell_id, area_id=fifo_chain_step5_source_area_id, label="Punto origen 5", destination=False
                )
            if _fifo_chain_step_destination_mode_value(station, qr_rule, 5) == "configured_area":
                fifo_chain_step5_destination_area, fifo_chain_step5_destination_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step5_destination_cell_id, area_id=fifo_chain_step5_destination_area_id,
                    label="Punto destino 5", destination=True, allow_occupied_destination=True,
                )
        if route_mode == "fifo_chain":
            preflight_entities = _resolve_entities(db, station, qr_rule, parsed, action)
            entities = preflight_entities
        preflight_duration_ms = round((time.perf_counter() - (preflight_started_at or started_at)) * 1000, 2)
        _qr_trace("QR_PREFLIGHT_OK", execution_id, stage="preflight", route_mode=route_mode, taskTyp=step1_task_typ, preflight_duration_ms=preflight_duration_ms, source_area_id=source_area_id, source_cell_id=source_cell_id, destination_area_id=destination_area_id, destination_cell_id=destination_cell_id)
        comment = f"QR execute {normalized_qr}"
        order_started_at = time.perf_counter()
        if route_mode == "fifo_chain" and fifo_chain_step1_source_mode != "configured_area":
            order, selection = _execute_fifo_chain_step1_any_area_by_material(
                db,
                material_group_id=fifo_chain_step1_material_group_id,
                destination_area_id=destination_area_id,
                destination_cell_id=destination_cell_id,
                source_mode=fifo_chain_step1_source_mode,
                source_area_ids=_fifo_chain_step_area_ids_value(station, qr_rule, 1, "source"),
                destination_mode=_fifo_chain_step_destination_mode_value(station, qr_rule, 1),
                destination_area_ids=_fifo_chain_step_area_ids_value(station, qr_rule, 1, "destination"),
                priority=priority,
                comment=comment,
                created_by=created_by or "qr",
                agv_code=agv_code,
                task_typ=step1_task_typ,
                allow_same_cell_move=_allow_same_cell_move_value(station, qr_rule),
            )
        elif route_mode == "fifo_chain" and _fifo_chain_step_destination_mode_value(station, qr_rule, 1) != "configured_area":
            order, selection = _execute_fifo_chain_step1_configured_source(
                db,
                source_area_id=source_area_id,
                source_cell_id=source_cell_id,
                material_group_id=material_id,
                destination_area_id=destination_area_id,
                destination_cell_id=destination_cell_id,
                destination_mode=_fifo_chain_step_destination_mode_value(station, qr_rule, 1),
                destination_area_ids=_fifo_chain_step_area_ids_value(station, qr_rule, 1, "destination"),
                priority=priority,
                comment=comment,
                created_by=created_by or "qr",
                agv_code=agv_code,
                task_typ=step1_task_typ,
                allow_same_cell_move=_allow_same_cell_move_value(station, qr_rule),
            )
        elif fifo_material_policy == "any_available_from_source":
            order, selection = execute_fifo_request_any_available(db, source_area_id, destination_area_id, priority, comment, created_by or "qr", agv_code, step1_task_typ, source_cell_id=source_cell_id, allow_same_cell_move=_allow_same_cell_move_value(station, qr_rule))
        else:
            order, selection = execute_fifo_request(db, source_area_id, destination_area_id, material_id, priority, comment, created_by or "qr", agv_code, step1_task_typ, allow_same_cell_move=_allow_same_cell_move_value(station, qr_rule))
        created_order = order
        order_creation_duration_ms = round((time.perf_counter() - order_started_at) * 1000, 2)
        _qr_trace("QR_ORDER_CREATED", execution_id, stage="order_created", movement_order_id=order.id, order_code=order.order_code, route_mode=route_mode, fifo_chain_group_id=getattr(order, "fifo_chain_group_id", None), fifo_chain_step=getattr(order, "fifo_chain_step", None), rack_id=selection.rack.id, rack_code=selection.rack.code, source_area_id=selection.source_area.id, source_cell_id=selection.source_cell.id, destination_area_id=selection.destination_area.id, destination_cell_id=selection.destination_cell.id, taskTyp=step1_task_typ, order_creation_duration_ms=order_creation_duration_ms)
        entities = {
            "material": selection.material_group,
            "rack": selection.rack,
            "source_area": selection.source_area,
            "destination_area": selection.destination_area,
            "source_cell": selection.source_cell,
            "destination_cell": selection.destination_cell,
            "route_mode": "simple_area",
            "allow_same_cell_move": _allow_same_cell_move_value(station, qr_rule),
            "fifo_material_policy": fifo_material_policy,
            "fifo_chain_total_steps": fifo_chain_total_steps,
            "fifo_chain_step1_source_mode": fifo_chain_step1_source_mode,
            "fifo_chain_step1_material": fifo_chain_step1_material,
            "fifo_chain_step1_material_group_id": fifo_chain_step1_material_group_id,
            "fifo_chain_step1_task_typ": fifo_chain_task_typ_by_step[1],
            "fifo_chain_step1_candidate": {"rack": selection.rack, "cell": selection.source_cell, "area": selection.source_area} if fifo_chain_step1_source_mode != "configured_area" else {},
            "fifo_chain_step2_source_mode": fifo_chain_step2_source_mode,
            "fifo_chain_step2_material": fifo_chain_step2_material,
            "fifo_chain_step2_material_group_id": fifo_chain_step2_material_group_id,
            "fifo_chain_step2_task_typ": fifo_chain_task_typ_by_step[2],
            "fifo_chain_step3_source_mode": fifo_chain_step3_source_mode,
            "fifo_chain_step3_material": fifo_chain_step3_material,
            "fifo_chain_step3_material_group_id": fifo_chain_step3_material_group_id,
            "fifo_chain_step3_task_typ": fifo_chain_task_typ_by_step[3],
            "fifo_chain_step3_source_area": fifo_chain_step3_source_area,
            "fifo_chain_step3_destination_area": fifo_chain_step3_destination_area,
            "fifo_chain_step3_source_cell": fifo_chain_step3_source_cell,
            "fifo_chain_step3_destination_cell": fifo_chain_step3_destination_cell,
            "fifo_chain_step4_source_mode": fifo_chain_step4_source_mode,
            "fifo_chain_step4_material": fifo_chain_step4_material,
            "fifo_chain_step4_material_group_id": fifo_chain_step4_material_group_id,
            "fifo_chain_step4_task_typ": fifo_chain_task_typ_by_step[4],
            "fifo_chain_step4_source_area": fifo_chain_step4_source_area,
            "fifo_chain_step4_destination_area": fifo_chain_step4_destination_area,
            "fifo_chain_step4_source_cell": fifo_chain_step4_source_cell,
            "fifo_chain_step4_destination_cell": fifo_chain_step4_destination_cell,
            "fifo_chain_step5_source_mode": fifo_chain_step5_source_mode,
            "fifo_chain_step5_material": fifo_chain_step5_material,
            "fifo_chain_step5_material_group_id": fifo_chain_step5_material_group_id,
            "fifo_chain_step5_task_typ": fifo_chain_task_typ_by_step[5],
            "fifo_chain_step5_source_area": fifo_chain_step5_source_area,
            "fifo_chain_step5_destination_area": fifo_chain_step5_destination_area,
            "fifo_chain_step5_source_cell": fifo_chain_step5_source_cell,
            "fifo_chain_step5_destination_cell": fifo_chain_step5_destination_cell,
            "route_points": [],
        }
        for step in range(1, 6):
            entities.update({
                f"fifo_chain_step{step}_source_area_ids": _fifo_chain_step_area_ids_value(station, qr_rule, step, "source"),
                f"fifo_chain_step{step}_destination_mode": _fifo_chain_step_destination_mode_value(station, qr_rule, step),
                f"fifo_chain_step{step}_destination_area_ids": _fifo_chain_step_area_ids_value(station, qr_rule, step, "destination"),
            })
        if route_mode == "fifo_chain" and preflight_entities.get("fifo_chain_projection"):
            entities["fifo_chain_projection"] = preflight_entities.get("fifo_chain_projection")
        if route_mode == "double_area":
            entities.update({
                "route_mode": "double_area",
                "second_source_area": second_source_area,
                "second_destination_area": second_destination_area,
                "second_source_cell": second_source_cell,
                "second_destination_cell": second_destination_cell,
            })
            route_points = _build_route_points(db, "double_area", entities)
            entities["route_points"] = route_points
            order.route_mode = "double_area"
            order.route_points_json = json.dumps(route_points, ensure_ascii=False)
            order.destination_area_id = second_destination_area.id if second_destination_area else order.destination_area_id
            order.destination_cell_id = second_destination_cell.id
            db.add(order)
            db.commit()
            db.refresh(order)
        elif route_mode == "fifo_chain":
            entities.update({
                "route_mode": "fifo_chain",
                "second_source_area": second_source_area,
                "second_destination_area": second_destination_area,
                "second_source_cell": second_source_cell,
                "second_destination_cell": second_destination_cell,
            })
            route_points = _build_route_points(db, "simple_area", entities)
            fifo_chain_steps = _build_fifo_chain_steps(db, entities)
            fifo_chain_group_id = str(uuid4())
            material_config_id = selection.material_group.id if fifo_material_policy == "specific_material" and selection.material_group else material_id
            next_config = _fifo_chain_next_config_payload(
                station=station,
                qr_rule=qr_rule,
                terminal=terminal,
                qr_value=normalized_qr,
                fifo_material_policy=fifo_material_policy,
                material_id=material_config_id,
                source_mode=fifo_chain_step2_source_mode,
                step2_material_group_id=fifo_chain_step2_material_group_id,
                second_source_area=second_source_area,
                second_destination_area=second_destination_area,
                second_source_cell=second_source_cell,
                second_destination_cell=second_destination_cell,
                second_source_cell_config_id=second_source_cell_id,
                second_destination_cell_config_id=second_destination_cell_id,
                agv_code=agv_code,
                task_typ=task_typ,
                priority=priority,
                fifo_chain_group_id=fifo_chain_group_id,
                total_steps=fifo_chain_total_steps,
                step3_source_mode=fifo_chain_step3_source_mode,
                step3_material_group_id=fifo_chain_step3_material_group_id,
                step3_source_area=fifo_chain_step3_source_area,
                step3_destination_area=fifo_chain_step3_destination_area,
                step3_source_cell=fifo_chain_step3_source_cell,
                step3_destination_cell=fifo_chain_step3_destination_cell,
                step3_source_cell_config_id=fifo_chain_step3_source_cell_id,
                step3_destination_cell_config_id=fifo_chain_step3_destination_cell_id,
                step4_source_mode=fifo_chain_step4_source_mode,
                step4_material_group_id=fifo_chain_step4_material_group_id,
                step4_source_area=fifo_chain_step4_source_area,
                step4_destination_area=fifo_chain_step4_destination_area,
                step4_source_cell=fifo_chain_step4_source_cell,
                step4_destination_cell=fifo_chain_step4_destination_cell,
                step4_source_cell_config_id=fifo_chain_step4_source_cell_id,
                step4_destination_cell_config_id=fifo_chain_step4_destination_cell_id,
                step5_source_mode=fifo_chain_step5_source_mode,
                step5_material_group_id=fifo_chain_step5_material_group_id,
                step5_source_area=fifo_chain_step5_source_area,
                step5_destination_area=fifo_chain_step5_destination_area,
                step5_source_cell=fifo_chain_step5_source_cell,
                step5_destination_cell=fifo_chain_step5_destination_cell,
                step5_source_cell_config_id=fifo_chain_step5_source_cell_id,
                step5_destination_cell_config_id=fifo_chain_step5_destination_cell_id,
                allow_same_cell_move=_allow_same_cell_move_value(station, qr_rule),
            )
            entities["route_points"] = route_points
            entities["fifo_chain_steps"] = fifo_chain_steps
            order.route_mode = "fifo_chain"
            order.route_points_json = json.dumps(route_points, ensure_ascii=False)
            order.fifo_chain_group_id = fifo_chain_group_id
            order.fifo_chain_step = 1
            order.fifo_chain_total_steps = fifo_chain_total_steps
            order.fifo_chain_parent_order_id = None
            order.fifo_chain_status = "active"
            order.fifo_chain_select_policy = "any_available"
            order.fifo_chain_next_config_json = json.dumps(next_config, ensure_ascii=False, default=str)
            db.add(order)
            db.commit()
            db.refresh(order)
        else:
            entities["route_points"] = _build_route_points(db, "simple_area", entities)
            order.route_mode = "simple_area"
            order.route_points_json = None
            db.add(order)
            db.commit()
            db.refresh(order)
        if audit_rack_func:
            try:
                audit_rack_func(db, rack=selection.rack, previous_status="available", new_status=selection.rack.status, source="qr_scan", related_order_id=order.id, reason="qr_fifo_execute", actor=created_by or "qr", auto_commit=False)
                db.commit()
            except Exception:
                logger.exception("QR execute audit failed order_id=%s", order.id)

        if dispatch_func:
            dispatch_started_at = time.perf_counter()
            _qr_trace("QR_RCS_REQUEST", execution_id, stage="rcs_dispatch", movement_order_id=order.id, order_code=order.order_code, rack_id=order.rack_id, route_mode=route_mode, taskTyp=step1_task_typ)
            dispatch_result = dispatch_func(db, order)
            rcs_dispatch_duration_ms = round((time.perf_counter() - dispatch_started_at) * 1000, 2)
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
                rcs_error_code = _qr_error_code(message, "RCS_REJECTED")
                _qr_trace("QR_RCS_REJECTED", execution_id, stage="rcs_dispatch", movement_order_id=order.id, order_code=order.order_code, remote_task_code=getattr(dispatch_result, "remote_task_code", None), dispatch_status=dispatch_status, rcs_message=message, error_code=rcs_error_code, rcs_dispatch_duration_ms=rcs_dispatch_duration_ms)
                if rollback_func:
                    rollback_started_at = time.perf_counter()
                    _qr_trace("QR_ROLLBACK_STARTED", execution_id, stage="rollback", movement_order_id=order.id, order_code=order.order_code, error_code=rcs_error_code)
                    try:
                        rollback_result = rollback_func(db, order, message)
                        _qr_trace("QR_ROLLBACK_COMPLETED", execution_id, stage="rollback", movement_order_id=order.id, order_code=order.order_code, rollback_executed=True, rollback_result=rollback_result, rollback_duration_ms=round((time.perf_counter() - rollback_started_at) * 1000, 2))
                    except Exception as rollback_exc:
                        _qr_trace("QR_ROLLBACK_FAILED", execution_id, stage="rollback", movement_order_id=order.id, order_code=order.order_code, error_code="ROLLBACK_FAILED", raw_error=str(rollback_exc), rollback_duration_ms=round((time.perf_counter() - rollback_started_at) * 1000, 2))
                        raise
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
            _qr_trace("QR_RCS_ACCEPTED", execution_id, stage="rcs_dispatch", movement_order_id=order.id, order_code=order.order_code, remote_task_code=getattr(dispatch_result, "remote_task_code", None), dispatch_status=dispatch_status, rcs_message=getattr(dispatch_result, "rcs_message", None), rcs_dispatch_duration_ms=rcs_dispatch_duration_ms)
        order = db.execute(select(MovementOrder).where(MovementOrder.id == order.id)).scalar_one_or_none() or order
        result = {
            "ok": True,
            "execution_id": execution_id,
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
            "destination": {"area": _area_payload(entities.get("second_destination_area") or selection.destination_area), "cell": _cell_payload(entities.get("second_destination_cell") or selection.destination_cell)},
            "route_mode": entities.get("route_mode") or "simple_area",
            "allow_same_cell_move": bool(entities.get("allow_same_cell_move")),
            "same_cell_move": bool(selection.source_cell.id == selection.destination_cell.id),
            "same_cell_move_message": (
                f"Movimiento en la misma celda permitido por configuraciÃ³n: {selection.source_cell.code} -> {selection.destination_cell.code}."
                if selection.source_cell.id == selection.destination_cell.id
                else None
            ),
            "fifo_material_policy": entities.get("fifo_material_policy") or "specific_material",
            "fifo_chain_total_steps": entities.get("fifo_chain_total_steps") or 2,
            "fifo_chain_step1_source_mode": entities.get("fifo_chain_step1_source_mode") or "configured_area",
            "fifo_chain_step1_task_typ": entities.get("fifo_chain_step1_task_typ"),
            "fifo_chain_step1_material": _material_payload(entities.get("fifo_chain_step1_material")),
            "fifo_chain_step2_source_mode": entities.get("fifo_chain_step2_source_mode") or "configured_area",
            "fifo_chain_step2_task_typ": entities.get("fifo_chain_step2_task_typ"),
            "fifo_chain_step2_material": _material_payload(entities.get("fifo_chain_step2_material")),
            "fifo_chain_step3_source_mode": entities.get("fifo_chain_step3_source_mode") or "configured_area",
            "fifo_chain_step3_task_typ": entities.get("fifo_chain_step3_task_typ"),
            "fifo_chain_step3_material": _material_payload(entities.get("fifo_chain_step3_material")),
            "fifo_chain_step3_source": {"area": _area_payload(entities.get("fifo_chain_step3_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_source_cell"))},
            "fifo_chain_step3_destination": {"area": _area_payload(entities.get("fifo_chain_step3_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_destination_cell"))},
            "fifo_chain_step4_source_mode": entities.get("fifo_chain_step4_source_mode") or "configured_area",
            "fifo_chain_step4_task_typ": entities.get("fifo_chain_step4_task_typ"),
            "fifo_chain_step4_material_group_id": entities.get("fifo_chain_step4_material_group_id"),
            "fifo_chain_step4_material_group_name": getattr(entities.get("fifo_chain_step4_material"), "name", None),
            "fifo_chain_step4_material": _material_payload(entities.get("fifo_chain_step4_material")),
            "fifo_chain_step4_source": {"area": _area_payload(entities.get("fifo_chain_step4_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_source_cell"))},
            "fifo_chain_step4_destination": {"area": _area_payload(entities.get("fifo_chain_step4_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_destination_cell"))},
            "fifo_chain_step5_source_mode": entities.get("fifo_chain_step5_source_mode") or "configured_area",
            "fifo_chain_step5_task_typ": entities.get("fifo_chain_step5_task_typ"),
            "fifo_chain_step5_material_group_id": entities.get("fifo_chain_step5_material_group_id"),
            "fifo_chain_step5_material_group_name": getattr(entities.get("fifo_chain_step5_material"), "name", None),
            "fifo_chain_step5_material": _material_payload(entities.get("fifo_chain_step5_material")),
            "fifo_chain_step5_source": {"area": _area_payload(entities.get("fifo_chain_step5_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step5_source_cell"))},
            "fifo_chain_step5_destination": {"area": _area_payload(entities.get("fifo_chain_step5_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step5_destination_cell"))},
            "route_points": entities.get("route_points") or [],
            "fifo_chain_steps": entities.get("fifo_chain_steps") or [],
            "fifo_chain_projection": entities.get("fifo_chain_projection") or {},
            "fifo_chain_preflight_checklist": (entities.get("fifo_chain_projection") or {}).get("fifo_chain_preflight_checklist") or {},
            "movement_order_id": order.id,
            "movement_order": _movement_order_payload(order),
            "dispatch": dispatch_payload,
            "dispatch_status": getattr(order, "dispatch_status", None),
            "rcs_status": getattr(order, "rcs_status", None),
            "message": "Orden FIFO creada y enviada al RCS." if dispatch_status == "success" else "Orden FIFO creada.",
        }
        if route_mode == "fifo_chain":
            result.update({
                "fifo_chain_group_id": getattr(order, "fifo_chain_group_id", None),
                "fifo_chain_step": getattr(order, "fifo_chain_step", None),
                "fifo_chain_total_steps": getattr(order, "fifo_chain_total_steps", None),
                "fifo_chain_status": getattr(order, "fifo_chain_status", None),
                "fifo_chain_next_config_saved": bool(getattr(order, "fifo_chain_next_config_json", None)),
                "message": "Flujo doble FIFO: paso 1 creado. El paso 2 se enviara cuando el paso 1 finalice.",
            })
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            result["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=station.scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=result["status"], request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
        event.movement_order_id = order.id
        db.add(event)
        db.commit()
        db.refresh(event)
        result["scan_event_id"] = event.id
        _qr_trace("QR_EXECUTION_COMPLETED", execution_id, stage="completed", scan_event_id=event.id, movement_order_id=order.id, order_code=order.order_code, remote_task_code=getattr(order, "remote_task_code", None), qr_value=normalized_qr, qr_action_rule_id=qr_rule.id, qr_alias=qr_rule.qr_alias, scanner_station_id=station.id, scanner_code=station.scanner_code, terminal_id=getattr(terminal, "id", None), terminal_code=getattr(terminal, "terminal_code", None), route_mode=route_mode, fifo_chain_group_id=getattr(order, "fifo_chain_group_id", None), fifo_chain_step=getattr(order, "fifo_chain_step", None), rack_id=selection.rack.id, rack_code=selection.rack.code, dispatch_status=dispatch_status, rcs_status=getattr(order, "rcs_status", None), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2), preflight_duration_ms=preflight_duration_ms, order_creation_duration_ms=order_creation_duration_ms, rcs_dispatch_duration_ms=round((time.perf_counter() - dispatch_started_at) * 1000, 2) if dispatch_started_at else 0)
        return result
    except ScanPreviewError as exc:
        diagnosis = _build_operator_diagnosis(db, message=exc.message, details=exc.details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {
            "ok": False,
            "execution_id": execution_id,
            "mode": "execute",
            "status": exc.status,
            "scanner": {"scanner_code": str(scanner_code or "").strip()},
            "scanner_code": str(scanner_code or "").strip(),
            "qr": {"qr_value": normalized_qr},
            "qr_value": normalized_qr,
            "action": action,
            "message": diagnosis["operator_message"],
        }
        result.update(exc.details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["error_code"] = _qr_error_code(exc.message, result["error_code"])
        result["operator_diagnosis"] = diagnosis
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
        _qr_trace("QR_PREFLIGHT_FAILED", execution_id, stage="preflight" if not created_order else "post_order", scan_event_id=event.id, movement_order_id=getattr(created_order, "id", None), order_code=getattr(created_order, "order_code", None), error_code=result.get("error_code") or _qr_error_code(exc.message), operator_message=diagnosis["operator_message"], raw_error=exc.message, dispatch_status=result.get("dispatch_status", dispatch_status), rcs_status=result.get("rcs_status"), rollback_executed=bool(rollback_result), rollback_result=rollback_result, preflight_duration_ms=round((time.perf_counter() - (preflight_started_at or started_at)) * 1000, 2) if not created_order else None)
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="failed", scan_event_id=event.id, movement_order_id=getattr(created_order, "id", None), error_code=result.get("error_code") or _qr_error_code(exc.message), total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result
    except HTTPException as exc:
        details = dict(exc.detail) if isinstance(exc.detail, dict) else {}
        message = details.get("operator_message") or str(exc.detail)
        diagnosis = _build_operator_diagnosis(db, message=message, details=details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {"ok": False, "execution_id": execution_id, "mode": "execute", "status": "error", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "scanner_code": str(scanner_code or "").strip(), "qr": {"qr_value": normalized_qr}, "qr_value": normalized_qr, "action": action, "message": diagnosis["operator_message"]}
        result.update(details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["error_code"] = _qr_error_code(message, result["error_code"])
        result["operator_diagnosis"] = diagnosis
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
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="http_error", scan_event_id=event.id, movement_order_id=getattr(created_order, "id", None), order_code=getattr(created_order, "order_code", None), error_code=result.get("error_code") or _qr_error_code(message), operator_message=diagnosis["operator_message"], raw_error=message, dispatch_status=result.get("dispatch_status", dispatch_status), rcs_status=result.get("rcs_status"), rollback_executed=bool(rollback_result), rollback_result=rollback_result, total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
        return result
    except Exception as exc:
        error_code = "ROLLBACK_FAILED" if rollback_started_at else _qr_error_code(exc, "UNEXPECTED_QR_ERROR")
        logger.exception("QR_EXECUTION_FAILED execution_id=%s mode=execute stage=unexpected error_code=%s", execution_id, error_code)
        result = {"ok": False, "execution_id": execution_id, "mode": "execute", "status": "error", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "scanner_code": str(scanner_code or "").strip(), "qr": {"qr_value": normalized_qr}, "qr_value": normalized_qr, "action": action, "error_code": error_code, "operator_message": "No se pudo completar la ejecución QR.", "message": "No se pudo completar la ejecución QR.", "movement_order_id": getattr(created_order, "id", None), "rollback": rollback_result or {"executed": False}}
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="error", error_message=str(exc)[:512], request_payload=request_payload, result_payload=result, terminal=terminal, mode="execute")
        if created_order:
            event.movement_order_id = created_order.id; db.add(event); db.commit(); db.refresh(event)
        result["scan_event_id"] = event.id
        _qr_trace("QR_EXECUTION_FAILED", execution_id, stage="unexpected", scan_event_id=event.id, movement_order_id=getattr(created_order, "id", None), order_code=getattr(created_order, "order_code", None), error_code=error_code, operator_message=result["operator_message"], raw_error=str(exc), rollback_executed=bool(rollback_result), rollback_result=rollback_result, total_duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
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
