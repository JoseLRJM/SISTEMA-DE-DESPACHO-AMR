from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from fifo_chain_service import resolve_fifo_request_by_material_any_area
from fifo_service import FifoSelection, _find_destination_cell, execute_fifo_request, execute_fifo_request_any_available, resolve_fifo_request, resolve_fifo_request_any_available
from logging_config import get_logger
from models import Area, Location, MaterialGroup, MovementOrder, QrActionRule, Rack, ScanEvent, ScannerStation, ScanTerminal, apply_rack_reservation_status

logger = get_logger("app.scanner")
SCAN_DEDUP_SECONDS = 7
SCAN_EXECUTE_RECENT_STATUSES = {"success", "created", "dispatched", "pending"}


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


def _operator_entity_location(area: Optional[Area], cell: Optional[Location]) -> dict:
    return {
        "area_id": getattr(area, "id", None),
        "area_code": getattr(area, "code", None),
        "area_name": getattr(area, "name", None),
        "cell_id": getattr(cell, "id", None),
        "cell_code": getattr(cell, "code", None),
    }


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
        if ("material" in lower_message and ("no hay" in lower_message or "requiere" in lower_message or "requerido" in lower_message)) or ("no hay racks disponibles" in lower_message and material):
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

    checked_source_locations = []
    same_material_elsewhere = []
    if error_code in {"source_material_not_available", "source_area_no_operational_locations"} and source_area:
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
        if material:
            elsewhere = db.execute(select(Location, Rack, Area).join(Rack, Location.rack_id == Rack.id).join(Area, Location.area_id == Area.id).where(Rack.material_group_id == material.id, Location.area_id != source_area.id, Location.enabled == 1, Location.is_visible == 1).limit(10)).all()
            same_material_elsewhere = [{"rack_id": rack.id, "rack_code": rack.code, "area_id": area.id, "area_name": area.name, "location_id": cell.id, "location_code": cell.code} for cell, rack, area in elsewhere if str(rack.status or "").strip().lower() in {"available", "disponible"}]

    checked_destination = list(details.get("checked_destination_locations") or [])[:20]
    projection_notes = list(details.get("projection_notes") or [])
    if details.get("destination_released_by_previous_step"):
        projection_notes.append({"step": step, "message": details.get("message"), "destination_cell_code": getattr(destination_cell, "code", None), "released_by_step": details.get("destination_released_by_previous_step")})

    material_label = getattr(material, "code", None) or getattr(material, "name", None) or "requerido"
    source_label = getattr(source_area, "name", None) or getattr(source_cell, "code", None) or "configurado"
    if error_code == "source_material_not_available":
        message_text = f"No hay rack con material {material_label} en el origen {source_label}."
        suggested_action = "Revisa que el origen del tramo sea el área donde realmente está el rack con ese material, o mueve un rack con ese material al origen."
    elif error_code == "destination_area_full":
        destination_label = getattr(destination_cell, "code", None) or getattr(destination_area, "name", None) or details.get("destination_area_name") or "configurado"
        message_text = f"No hay espacio libre en el destino {destination_label}."
        suggested_action = "Libera una ubicación destino o cambia el destino configurado en el QR."
    elif error_code == "destination_occupied_not_released":
        destination_label = getattr(destination_cell, "code", None) or details.get("destination_label") or getattr(destination_area, "name", None) or "configurado"
        message_text = f"El destino {destination_label} está ocupado y no será liberado antes del tramo {step}."
        suggested_action = "Libera esa ubicación o cambia el destino del tramo."
    elif error_code == "source_area_no_operational_locations":
        suggested_action = "Configura ubicaciones visibles y habilitadas para el área origen."
    elif error_code == "destination_area_no_operational_locations":
        suggested_action = "Configura ubicaciones visibles y habilitadas para el área destino."
    elif error_code == "required_step_material_missing":
        suggested_action = "Configura el material requerido para este tramo en el QR o scanner."
    else:
        suggested_action = details.get("suggested_action") or "Revisa la configuración operativa del QR y vuelve a intentar."

    return {
        "error_code": error_code,
        "title": "No se puede ejecutar",
        "operator_message": message_text,
        "suggested_action": suggested_action,
        "step": step,
        "total_steps": total_steps,
        "qr_value": qr_value,
        "material": _material_payload(material),
        "source": _operator_entity_location(source_area, source_cell),
        "destination": _operator_entity_location(destination_area, destination_cell),
        "checked_source_locations": checked_source_locations,
        "checked_destination_locations": checked_destination,
        "same_material_elsewhere": same_material_elsewhere,
        "projection_notes": projection_notes,
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
    if value not in {"configured_area", "any_area_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step2_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step3_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step3_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step3_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step3_source_mode invalido: {value}", "rejected")
    return value


def _fifo_chain_step4_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step4_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step4_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material"}:
        raise ScanPreviewError(f"fifo_chain_step4_source_mode invalido: {value}", "rejected")
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
    return total if total in {2, 3, 4} else 2


def _fifo_chain_step1_source_mode_value(station: Optional[ScannerStation], qr_rule: Optional[QrActionRule]) -> str:
    value = _first_value(
        getattr(qr_rule, "fifo_chain_step1_source_mode", None) if qr_rule else None,
        getattr(station, "fifo_chain_step1_source_mode", None) if station else None,
        "configured_area",
    )
    value = str(value or "configured_area").strip() or "configured_area"
    if value not in {"configured_area", "any_area_by_material"}:
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
    source_cell = None if step1_source_mode == "any_area_by_material" else _ensure_operational_route_cell(entities.get("source_cell"), "Punto origen 1")
    destination_cell = _ensure_operational_route_cell(entities.get("destination_cell"), "Punto destino 1")
    second_source_cell = None if step2_source_mode == "any_area_by_material" else _ensure_operational_route_cell(entities.get("second_source_cell"), "Punto origen 2")
    second_destination_cell = _ensure_operational_route_cell(entities.get("second_destination_cell"), "Punto destino 2")
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
        if step1_source_mode == "any_area_by_material"
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
        if step2_source_mode == "any_area_by_material"
        else {"area": _area_payload(second_source_area), "cell": _cell_payload(second_source_cell), "route_point": _route_point_payload(1, "source_2", second_source_area, second_source_cell)}
    )
    steps = [
        {
            "step": 1,
            "source_mode": step1_source_mode,
            "step1_material": _material_payload(step1_material),
            "source": step1_source_payload,
            "destination": {"area": _area_payload(destination_area), "cell": _cell_payload(destination_cell), "route_point": _route_point_payload(2, "destination_1", destination_area, destination_cell)},
        },
        {
            "step": 2,
            "source_mode": step2_source_mode,
            "step2_material": _material_payload(step2_material),
            "source": step2_source_payload,
            "destination": {"area": _area_payload(second_destination_area), "cell": _cell_payload(second_destination_cell), "route_point": _route_point_payload(2, "destination_2", second_destination_area, second_destination_cell)},
        },
    ]
    total_steps = int(entities.get("fifo_chain_total_steps") or 2)
    if total_steps >= 3:
        step3_source_mode = entities.get("fifo_chain_step3_source_mode") or "configured_area"
        step3_material = entities.get("fifo_chain_step3_material")
        step3_source_area = entities.get("fifo_chain_step3_source_area")
        step3_destination_area = entities.get("fifo_chain_step3_destination_area")
        step3_candidate = entities.get("fifo_chain_step3_candidate") or {}
        step3_source_cell = None if step3_source_mode == "any_area_by_material" else _ensure_operational_route_cell(entities.get("fifo_chain_step3_source_cell"), "Punto origen 3")
        step3_destination_cell = _ensure_operational_route_cell(entities.get("fifo_chain_step3_destination_cell"), "Punto destino 3")
        step3_source_payload = (
            {"mode": "any_area_by_material", "label": "Cualquier area", "material": _material_payload(step3_material), "area": _area_payload(step3_candidate.get("area")), "cell": _cell_payload(step3_candidate.get("cell")), "rack": _rack_payload(step3_candidate.get("rack")), "route_point": None, "message": "Selección proyectada; se revalidará al crear el tramo."}
            if step3_source_mode == "any_area_by_material"
            else {"area": _area_payload(step3_source_area), "cell": _cell_payload(step3_source_cell), "route_point": _route_point_payload(1, "source_3", step3_source_area, step3_source_cell)}
        )
        steps.append({
            "step": 3,
            "source_mode": step3_source_mode,
            "step3_material": _material_payload(step3_material),
            "source": step3_source_payload,
            "destination": {"area": _area_payload(step3_destination_area), "cell": _cell_payload(step3_destination_cell), "route_point": _route_point_payload(2, "destination_3", step3_destination_area, step3_destination_cell)},
        })
    if total_steps == 4:
        step4_source_mode = entities.get("fifo_chain_step4_source_mode") or "configured_area"
        step4_material = entities.get("fifo_chain_step4_material")
        step4_source_area = entities.get("fifo_chain_step4_source_area")
        step4_destination_area = entities.get("fifo_chain_step4_destination_area")
        step4_candidate = entities.get("fifo_chain_step4_candidate") or {}
        step4_source_cell = None if step4_source_mode == "any_area_by_material" else _ensure_operational_route_cell(entities.get("fifo_chain_step4_source_cell"), "Punto origen 4")
        step4_destination_cell = _ensure_operational_route_cell(entities.get("fifo_chain_step4_destination_cell"), "Punto destino 4")
        step4_source_payload = (
            {"mode": "any_area_by_material", "label": "Cualquier area", "material": _material_payload(step4_material), "area": _area_payload(step4_candidate.get("area")), "cell": _cell_payload(step4_candidate.get("cell")), "rack": _rack_payload(step4_candidate.get("rack")), "route_point": None, "message": "Selección proyectada; se revalidará al crear el tramo."}
            if step4_source_mode == "any_area_by_material"
            else {"area": _area_payload(step4_source_area), "cell": _cell_payload(step4_source_cell), "route_point": _route_point_payload(1, "source_4", step4_source_area, step4_source_cell)}
        )
        steps.append({
            "step": 4,
            "source_mode": step4_source_mode,
            "step4_material": _material_payload(step4_material),
            "source": step4_source_payload,
            "destination": {"area": _area_payload(step4_destination_area), "cell": _cell_payload(step4_destination_cell), "route_point": _route_point_payload(2, "destination_4", step4_destination_area, step4_destination_cell)},
        })
    return steps


def _validate_fifo_chain_projected_state(db, entities: dict) -> dict:
    locations = db.execute(
        select(Location)
        .where(Location.enabled == 1)
        .where(Location.is_visible == 1)
        .where(Location.code.is_not(None))
        .order_by(Location.y.asc(), Location.x.asc(), Location.id.asc())
    ).scalars().all()
    racks = db.execute(select(Rack)).scalars().all()
    location_by_id = {cell.id: cell for cell in locations}
    rack_by_id = {rack.id: rack for rack in racks}
    occupancy = {cell.id: cell.rack_id for cell in locations}
    rack_location = {cell.rack_id: cell.id for cell in locations if cell.rack_id is not None}
    reserved_ids = set()
    active_reservations = db.execute(
        select(MovementOrder.destination_cell_id)
        .where(MovementOrder.status.in_(("pending_dispatch", "dispatched", "in_progress")))
    ).all()
    reserved_ids.update(row[0] for row in active_reservations if row and row[0] is not None)

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
    ][:total_steps]
    projected_steps = []
    released_by_step: dict[int, int] = {}

    for spec in step_specs:
        step = spec["step"]
        material_id = getattr(spec["material"], "id", None)
        source_candidates = []
        if spec["source_mode"] == "any_area_by_material":
            source_candidates = [cell for cell in locations if occupancy.get(cell.id) is not None]
        elif spec["source_cell_explicit"] and spec["source_cell"]:
            source_candidates = [spec["source_cell"]]
        elif spec["source_area"]:
            source_candidates = [cell for cell in locations if cell.area_id == spec["source_area"].id and occupancy.get(cell.id) is not None]
        elif spec["source_cell"]:
            source_candidates = [spec["source_cell"]]
        source_cell = None
        rack = None
        for candidate in source_candidates:
            rack_id = occupancy.get(candidate.id)
            candidate_rack = rack_by_id.get(rack_id)
            if not candidate_rack:
                continue
            if material_id is not None and candidate_rack.material_group_id != material_id:
                continue
            source_cell, rack = candidate, candidate_rack
            break
        if not source_cell or not rack:
            area_label = getattr(spec["source_area"], "name", None) or "las áreas configuradas"
            material_label = getattr(spec["material"], "code", None) or getattr(spec["material"], "name", None) or "requerido"
            raise ScanPreviewError(
                f"Tramo {step}: no hay rack con material {material_label} en {area_label} después de proyectar los movimientos anteriores.",
                "error",
                {
                    "error_code": "fifo_chain_projected_source_unavailable",
                    "operator_message": f"Tramo {step}: no hay rack con material {material_label} en {area_label} después de proyectar los movimientos anteriores.",
                    "step": step,
                    "source_label": area_label,
                    "suggested_action": "Revisa el material y el origen configurados para este tramo.",
                },
            )

        destination_candidates = []
        if spec["destination_cell_explicit"] and spec["destination_cell"]:
            destination_candidates = [spec["destination_cell"]]
        elif spec["destination_area"]:
            destination_candidates = [cell for cell in locations if cell.area_id == spec["destination_area"].id]
        elif spec["destination_cell"]:
            destination_candidates = [spec["destination_cell"]]
        destination_cell = next(
            (cell for cell in destination_candidates if occupancy.get(cell.id) is None and cell.id not in reserved_ids),
            None,
        )
        if not destination_cell:
            destination_label = getattr(spec["destination_cell"], "code", None) or getattr(spec["destination_area"], "name", None) or "destino configurado"
            checked = []
            for cell in destination_candidates[:20]:
                occupant = rack_by_id.get(occupancy.get(cell.id))
                reason = (
                    f"Ubicación ocupada por rack {occupant.code}"
                    if occupant
                    else "Ubicación reservada por orden activa" if cell.id in reserved_ids else "Ubicación no disponible"
                )
                checked.append({"location_id": cell.id, "location_code": cell.code, "area_id": cell.area_id, "status": "occupied", "rack_id": getattr(occupant, "id", None), "rack_code": getattr(occupant, "code", None), "reason": reason})
            raise ScanPreviewError(
                f"Tramo {step}: el destino {destination_label} está ocupado y no será liberado por ningún tramo anterior.",
                "error",
                {
                    "error_code": "fifo_chain_projected_destination_occupied",
                    "operator_message": f"Tramo {step}: el destino {destination_label} está ocupado y no será liberado por ningún tramo anterior.",
                    "step": step,
                    "destination_label": destination_label,
                    "destination_area_name": getattr(spec["destination_area"], "name", None),
                    "checked_destination_locations": checked,
                    "suggested_action": "Libera el destino o corrige el tramo configurado en el QR.",
                },
            )

        release_step = released_by_step.get(destination_cell.id)
        occupancy[source_cell.id] = None
        released_by_step[source_cell.id] = step
        occupancy[destination_cell.id] = rack.id
        rack_location[rack.id] = destination_cell.id
        resolved_source_area = db.execute(select(Area).where(Area.id == source_cell.area_id)).scalar_one_or_none()
        entities[spec["source_key"]] = source_cell
        entities[spec["source_area_key"]] = resolved_source_area or spec["source_area"]
        entities[spec["destination_key"]] = destination_cell
        entities[f"fifo_chain_step{step}_candidate"] = {"rack": rack, "cell": source_cell, "area": resolved_source_area or spec["source_area"]}
        if step == 1:
            entities["rack"] = rack
            entities["source_cell"] = source_cell
            entities["source_area"] = resolved_source_area or spec["source_area"]
        projected_steps.append({
            "step": step,
            "rack_id": rack.id,
            "rack_code": rack.code,
            "material_group_id": rack.material_group_id,
            "source_cell_id": source_cell.id,
            "source_cell_code": source_cell.code,
            "destination_cell_id": destination_cell.id,
            "destination_cell_code": destination_cell.code,
            "destination_released_by_previous_step": release_step,
            "message": f"El destino del tramo {step} será liberado por el tramo {release_step}." if release_step else "Validado contra el estado proyectado.",
        })
    projection_notes = [
        {
            "step": item["step"],
            "message": item["message"],
            "destination_cell_code": item["destination_cell_code"],
            "released_by_step": item["destination_released_by_previous_step"],
        }
        for item in projected_steps
        if item.get("destination_released_by_previous_step")
    ]
    return {"validation": "projected_sequential", "steps": projected_steps, "projection_notes": projection_notes, "database_modified": False, "future_steps_revalidated_on_creation": True}


def _resolve_entities(db, station: Optional[ScannerStation], qr_rule: Optional[QrActionRule], parsed: dict, action: Optional[str]) -> dict:
    route_mode = _route_mode_value(station, qr_rule)
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
    if fifo_chain_step3_source_mode == "any_area_by_material":
        fifo_chain_step3_source_area_id = None
        fifo_chain_step3_source_cell_id = None
    if fifo_chain_step4_source_mode == "any_area_by_material":
        fifo_chain_step4_source_area_id = None
        fifo_chain_step4_source_cell_id = None

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
    if fifo_chain_step3_source_cell and not fifo_chain_step3_source_area and fifo_chain_step3_source_cell.area_id:
        fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step3_destination_cell and not fifo_chain_step3_destination_area and fifo_chain_step3_destination_cell.area_id:
        fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step4_source_cell and not fifo_chain_step4_source_area and fifo_chain_step4_source_cell.area_id:
        fifo_chain_step4_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_source_cell.area_id)).scalar_one_or_none()
    if fifo_chain_step4_destination_cell and not fifo_chain_step4_destination_area and fifo_chain_step4_destination_cell.area_id:
        fifo_chain_step4_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_destination_cell.area_id)).scalar_one_or_none()

    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_step1_source_mode == "any_area_by_material":
        if not fifo_chain_step1_material:
            raise ScanPreviewError("El tramo 1 requiere Material requerido tramo 1.", "rejected")
        if not destination_area and not destination_cell:
            raise ScanPreviewError("Punto destino 1 requiere area o celda configurada.", "rejected")
        destination_area, destination_cell = _resolve_route_cell_from_config(
            db,
            cell_id=destination_cell_id,
            area_id=destination_area_id,
            label="Punto destino 1",
            destination=True,
        )
        try:
            candidate_rack, candidate_cell, candidate_area = resolve_fifo_request_by_material_any_area(db, fifo_chain_step1_material.id)
        except Exception:
            candidate_rack, candidate_cell, candidate_area = None, None, None
        rack = candidate_rack
        material = fifo_chain_step1_material
        source_cell = candidate_cell
        source_area = candidate_area
        if fifo_chain_step2_source_mode == "any_area_by_material":
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
        second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
            db,
            cell_id=second_destination_cell_id,
            area_id=second_destination_area_id,
            label="Punto destino 2",
            destination=True,
            allow_occupied_destination=True,
        )
    elif action == "fifo_request" and source_area and destination_area:
        resolved_priority = _first_value(
            getattr(qr_rule, "priority", None) if qr_rule else None,
            getattr(station, "priority", None) if station else None,
        )
        try:
            if fifo_material_policy == "any_available_from_source":
                selection = resolve_fifo_request_any_available(db, source_area.id, destination_area.id, _priority_text(resolved_priority), source_cell_id=source_cell_id)
            else:
                if not material:
                    raise ScanPreviewError("fifo_request requiere material_group_id cuando la politica es specific_material.", "rejected")
                selection = resolve_fifo_request(db, source_area.id, destination_area.id, material.id, _priority_text(resolved_priority))
        except HTTPException as exc:
            raise _scan_preview_error_from_http(exc, step=1 if route_mode == "fifo_chain" else None, destination_label="Punto destino 1")
        rack = selection.rack
        material = selection.material_group
        source_cell = selection.source_cell
        destination_cell = selection.destination_cell
        source_area = selection.source_area
        destination_area = selection.destination_area
        if route_mode in {"double_area", "fifo_chain"}:
            if route_mode == "fifo_chain" and fifo_chain_step2_source_mode == "any_area_by_material":
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
            second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
                db,
                cell_id=second_destination_cell_id,
                area_id=second_destination_area_id,
                label="Punto destino 2",
                destination=True,
                allow_occupied_destination=route_mode == "fifo_chain",
            )
    elif action in {"point_to_area", "store_rack", "request_empty", "return_full"} and destination_area and not destination_cell:
        try:
            destination_cell = _find_destination_cell(db, destination_area)
        except HTTPException as exc:
            raise _scan_preview_error_from_http(exc, destination_label="Destino")

    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_total_steps >= 3:
        if fifo_chain_step3_source_mode == "any_area_by_material":
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
        fifo_chain_step3_destination_area, fifo_chain_step3_destination_cell = _resolve_route_cell_from_config(
            db,
            cell_id=fifo_chain_step3_destination_cell_id,
            area_id=fifo_chain_step3_destination_area_id,
            label="Punto destino 3",
            destination=True,
            allow_occupied_destination=True,
        )
    if action == "fifo_request" and route_mode == "fifo_chain" and fifo_chain_total_steps == 4:
        if fifo_chain_step4_source_mode == "any_area_by_material":
            if not fifo_chain_step4_material:
                raise ScanPreviewError("El tramo 4 por material requiere Material requerido tramo 4.", "rejected")
        else:
            fifo_chain_step4_source_area, fifo_chain_step4_source_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step4_source_cell_id, area_id=fifo_chain_step4_source_area_id, label="Punto origen 4", destination=False
            )
        fifo_chain_step4_destination_area, fifo_chain_step4_destination_cell = _resolve_route_cell_from_config(
            db, cell_id=fifo_chain_step4_destination_cell_id, area_id=fifo_chain_step4_destination_area_id, label="Punto destino 4", destination=True,
            allow_occupied_destination=True,
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
        "fifo_material_policy": fifo_material_policy,
        "fifo_chain_total_steps": fifo_chain_total_steps,
        "fifo_chain_step1_source_mode": fifo_chain_step1_source_mode,
        "fifo_chain_step1_material": fifo_chain_step1_material,
        "fifo_chain_step1_material_group_id": fifo_chain_step1_material_group_id,
        "fifo_chain_step1_candidate": {"rack": rack, "cell": source_cell, "area": source_area} if fifo_chain_step1_source_mode == "any_area_by_material" else {},
        "fifo_chain_step2_source_mode": fifo_chain_step2_source_mode,
        "fifo_chain_step2_material": fifo_chain_step2_material,
        "fifo_chain_step2_material_group_id": fifo_chain_step2_material_group_id,
        "fifo_chain_step3_source_mode": fifo_chain_step3_source_mode,
        "fifo_chain_step3_material": fifo_chain_step3_material,
        "fifo_chain_step3_material_group_id": fifo_chain_step3_material_group_id,
        "fifo_chain_step3_source_area": fifo_chain_step3_source_area,
        "fifo_chain_step3_destination_area": fifo_chain_step3_destination_area,
        "fifo_chain_step3_source_cell": fifo_chain_step3_source_cell,
        "fifo_chain_step3_destination_cell": fifo_chain_step3_destination_cell,
        "fifo_chain_step3_source_cell_config_id": fifo_chain_step3_source_cell_id,
        "fifo_chain_step3_destination_cell_config_id": fifo_chain_step3_destination_cell_id,
        "fifo_chain_step4_source_mode": fifo_chain_step4_source_mode,
        "fifo_chain_step4_material": fifo_chain_step4_material,
        "fifo_chain_step4_material_group_id": fifo_chain_step4_material_group_id,
        "fifo_chain_step4_source_area": fifo_chain_step4_source_area,
        "fifo_chain_step4_destination_area": fifo_chain_step4_destination_area,
        "fifo_chain_step4_source_cell": fifo_chain_step4_source_cell,
        "fifo_chain_step4_destination_cell": fifo_chain_step4_destination_cell,
        "fifo_chain_step4_source_cell_config_id": fifo_chain_step4_source_cell_id,
        "fifo_chain_step4_destination_cell_config_id": fifo_chain_step4_destination_cell_id,
        "second_source_area": second_source_area,
        "second_destination_area": second_destination_area,
        "second_source_cell": second_source_cell,
        "second_destination_cell": second_destination_cell,
        "second_source_cell_config_id": second_source_cell_id,
        "second_destination_cell_config_id": second_destination_cell_id,
    }
    if action == "fifo_request" and route_mode == "fifo_chain":
        entities["fifo_chain_projection"] = _validate_fifo_chain_projected_state(db, entities)
        if fifo_chain_step1_source_mode == "any_area_by_material" and not entities.get("source_cell"):
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
) -> dict:
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
        "material_group_id": step2_material_group_id or material_id,
        "step2_material_group_id": step2_material_group_id,
        "agv_code": agv_code,
        "task_typ": task_typ,
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
    steps = [step2]
    if total_steps >= 3:
        steps.append({
            **{key: value for key, value in step2.items() if key not in {
                "step", "source_mode", "source_area_id", "destination_area_id", "source_cell_id", "destination_cell_id",
                "source_cell_configured", "destination_cell_configured",
                "source_area", "destination_area", "source_cell", "destination_cell", "material_group_id", "step2_material_group_id",
            }},
            "step": 3,
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
        })
    if total_steps == 4:
        steps.append({
            **{key: value for key, value in step2.items() if key not in {
                "step", "source_mode", "source_area_id", "destination_area_id", "source_cell_id", "destination_cell_id",
                "source_cell_configured", "destination_cell_configured",
                "source_area", "destination_area", "source_cell", "destination_cell", "material_group_id", "step2_material_group_id",
            }},
            "step": 4,
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


def _execute_fifo_chain_step1_any_area_by_material(
    db,
    *,
    material_group_id,
    destination_area_id,
    destination_cell_id,
    priority,
    comment,
    created_by,
    agv_code,
    task_typ,
) -> tuple[MovementOrder, FifoSelection]:
    destination_area = db.execute(select(Area).where(Area.id == destination_area_id)).scalar_one_or_none() if destination_area_id else None
    destination_cell = db.execute(select(Location).where(Location.id == destination_cell_id)).scalar_one_or_none() if destination_cell_id else None
    if destination_cell and not destination_area and destination_cell.area_id:
        destination_area = db.execute(select(Area).where(Area.id == destination_cell.area_id)).scalar_one_or_none()
    if not destination_area:
        raise HTTPException(status_code=400, detail="Destino tramo 1 requiere area o celda configurada")
    if not destination_cell:
        destination_cell = _find_destination_cell(db, destination_area)
    destination_cell = _ensure_operational_route_cell(destination_cell, "Punto destino 1")
    if getattr(destination_cell, "rack_id", None) is not None:
        raise HTTPException(status_code=409, detail="La celda destino tramo 1 ya no esta disponible")

    try:
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
    if source_area.id == destination_area.id:
        raise HTTPException(status_code=400, detail="El area origen real y destino del tramo 1 no pueden ser iguales")
    if source_cell.rack_id != rack.id or int(source_cell.enabled or 0) != 1 or int(source_cell.is_visible or 0) != 1:
        raise HTTPException(status_code=409, detail="La celda origen real del tramo 1 ya no es valida")

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

    return order, FifoSelection(
        rack=rack,
        source_cell=source_cell,
        destination_cell=destination_cell,
        source_area=source_area,
        destination_area=destination_area,
        material_group=material,
    )


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
            "route_mode": entities.get("route_mode") or "simple_area",
            "fifo_material_policy": entities.get("fifo_material_policy") or "specific_material",
            "fifo_chain_total_steps": entities.get("fifo_chain_total_steps") or 2,
            "fifo_chain_step1_source_mode": entities.get("fifo_chain_step1_source_mode") or "configured_area",
            "fifo_chain_step1_material": _material_payload(entities.get("fifo_chain_step1_material")),
            "fifo_chain_step2_source_mode": entities.get("fifo_chain_step2_source_mode") or "configured_area",
            "fifo_chain_step2_material": _material_payload(entities.get("fifo_chain_step2_material")),
            "fifo_chain_step3_source_mode": entities.get("fifo_chain_step3_source_mode") or "configured_area",
            "fifo_chain_step3_material": _material_payload(entities.get("fifo_chain_step3_material")),
            "fifo_chain_step3_source": {"area": _area_payload(entities.get("fifo_chain_step3_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_source_cell"))},
            "fifo_chain_step3_destination": {"area": _area_payload(entities.get("fifo_chain_step3_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_destination_cell"))},
            "fifo_chain_step4_source_mode": entities.get("fifo_chain_step4_source_mode") or "configured_area",
            "fifo_chain_step4_material_group_id": entities.get("fifo_chain_step4_material_group_id"),
            "fifo_chain_step4_material_group_name": getattr(entities.get("fifo_chain_step4_material"), "name", None),
            "fifo_chain_step4_material": _material_payload(entities.get("fifo_chain_step4_material")),
            "fifo_chain_step4_source": {"area": _area_payload(entities.get("fifo_chain_step4_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_source_cell"))},
            "fifo_chain_step4_destination": {"area": _area_payload(entities.get("fifo_chain_step4_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_destination_cell"))},
            "route_points": entities.get("route_points") or [],
            "fifo_chain_steps": entities.get("fifo_chain_steps") or [],
            "fifo_chain_projection": entities.get("fifo_chain_projection") or {},
            "message": "Preview correcto. No se creo orden.",
        }
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status="preview_ok", request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        return result
    except ScanPreviewError as exc:
        diagnosis = _build_operator_diagnosis(db, message=exc.message, details=exc.details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {
            "ok": False,
            "mode": "preview",
            "scanner": {"scanner_code": str(scanner_code or "").strip()},
            "qr": {"qr_value": normalized_qr},
            "action": action,
            "message": diagnosis["operator_message"],
        }
        result.update(exc.details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["operator_diagnosis"] = diagnosis
        if terminal:
            result["terminal"] = {"id": terminal.id, "terminal_code": terminal.terminal_code, "name": terminal.name, "last_seen_at": getattr(terminal, "last_seen_at", None), "last_ip": getattr(terminal, "last_ip", None)}
            request_payload["terminal_code"] = terminal.terminal_code
        event = _save_scan_event(db, scanner_code=scanner_code, qr_value=normalized_qr, created_by=created_by, station=station, qr_rule=qr_rule, parsed=parsed, action=action, entities=entities, status=exc.status, error_message=exc.message, request_payload=request_payload, result_payload=result, terminal=terminal)
        result["scan_event_id"] = event.id
        return result
    except HTTPException as exc:
        details = dict(exc.detail) if isinstance(exc.detail, dict) else {}
        message = details.get("operator_message") or str(exc.detail)
        diagnosis = _build_operator_diagnosis(db, message=message, details=details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {"ok": False, "mode": "preview", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "qr": {"qr_value": normalized_qr}, "action": action, "message": diagnosis["operator_message"]}
        result.update(details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
        result["operator_diagnosis"] = diagnosis
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
        if not source_area_id and not (route_mode == "fifo_chain" and fifo_chain_step1_source_mode == "any_area_by_material"):
            raise ScanPreviewError("Scanner sin area origen configurada para FIFO.", "rejected")
        if not destination_area_id:
            raise ScanPreviewError("Scanner sin destino configurado para FIFO.", "rejected")

        priority = _priority_from_int(_first_value(getattr(qr_rule, "priority", None), getattr(station, "priority", None)))
        agv_code = _first_value(getattr(qr_rule, "agv_code", None), getattr(station, "agv_code", None))
        task_typ = _first_value(getattr(qr_rule, "task_typ", None), getattr(station, "task_typ", None))
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
        fifo_chain_step3_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_source_area_id")
        fifo_chain_step3_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step3_destination_area_id")
        fifo_chain_step3_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_source_cell_id")
        fifo_chain_step3_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step3_destination_cell_id")
        fifo_chain_step4_source_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_source_area_id")
        fifo_chain_step4_destination_area_id = _fifo_chain_step3_area_id_value(station, qr_rule, "fifo_chain_step4_destination_area_id")
        fifo_chain_step4_source_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_source_cell_id")
        fifo_chain_step4_destination_cell_id = _fifo_chain_step3_cell_id_value(station, qr_rule, "fifo_chain_step4_destination_cell_id")
        if fifo_chain_step3_source_mode == "any_area_by_material":
            fifo_chain_step3_source_area_id = None
            fifo_chain_step3_source_cell_id = None
        if fifo_chain_step4_source_mode == "any_area_by_material":
            fifo_chain_step4_source_area_id = None
            fifo_chain_step4_source_cell_id = None
        fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_area_id)).scalar_one_or_none() if fifo_chain_step3_source_area_id else None
        fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_area_id)).scalar_one_or_none() if fifo_chain_step3_destination_area_id else None
        fifo_chain_step3_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_source_cell_id)).scalar_one_or_none() if fifo_chain_step3_source_cell_id else None
        fifo_chain_step3_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step3_destination_cell_id)).scalar_one_or_none() if fifo_chain_step3_destination_cell_id else None
        fifo_chain_step4_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_source_area_id)).scalar_one_or_none() if fifo_chain_step4_source_area_id else None
        fifo_chain_step4_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step4_destination_area_id)).scalar_one_or_none() if fifo_chain_step4_destination_area_id else None
        fifo_chain_step4_source_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_source_cell_id)).scalar_one_or_none() if fifo_chain_step4_source_cell_id else None
        fifo_chain_step4_destination_cell = db.execute(select(Location).where(Location.id == fifo_chain_step4_destination_cell_id)).scalar_one_or_none() if fifo_chain_step4_destination_cell_id else None
        if fifo_chain_step3_source_cell and not fifo_chain_step3_source_area and fifo_chain_step3_source_cell.area_id:
            fifo_chain_step3_source_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_source_cell.area_id)).scalar_one_or_none()
        if fifo_chain_step3_destination_cell and not fifo_chain_step3_destination_area and fifo_chain_step3_destination_cell.area_id:
            fifo_chain_step3_destination_area = db.execute(select(Area).where(Area.id == fifo_chain_step3_destination_cell.area_id)).scalar_one_or_none()
        if route_mode == "fifo_chain" and fifo_chain_step2_source_mode == "any_area_by_material" and not fifo_chain_step2_material:
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
            if route_mode == "fifo_chain" and fifo_chain_step2_source_mode == "any_area_by_material":
                second_source_area = None
                second_source_cell = None
            else:
                second_source_area, second_source_cell = _resolve_route_cell_from_config(db, cell_id=second_source_cell_id, area_id=second_source_area_id, label="Punto origen 2", destination=False)
            second_destination_area, second_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=second_destination_cell_id, area_id=second_destination_area_id, label="Punto destino 2", destination=True,
                allow_occupied_destination=route_mode == "fifo_chain",
            )
        if route_mode == "fifo_chain" and fifo_chain_total_steps >= 3:
            if fifo_chain_step3_source_mode == "any_area_by_material":
                if not fifo_chain_step3_material:
                    raise ScanPreviewError("El tramo 3 por material requiere Material requerido tramo 3.", "rejected")
            else:
                fifo_chain_step3_source_area, fifo_chain_step3_source_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step3_source_cell_id, area_id=fifo_chain_step3_source_area_id, label="Punto origen 3", destination=False
                )
            fifo_chain_step3_destination_area, fifo_chain_step3_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step3_destination_cell_id, area_id=fifo_chain_step3_destination_area_id, label="Punto destino 3", destination=True,
                allow_occupied_destination=True,
            )
        if route_mode == "fifo_chain" and fifo_chain_total_steps == 4:
            if fifo_chain_step4_source_mode == "any_area_by_material":
                if not fifo_chain_step4_material:
                    raise ScanPreviewError("El tramo 4 por material requiere Material requerido tramo 4.", "rejected")
            else:
                fifo_chain_step4_source_area, fifo_chain_step4_source_cell = _resolve_route_cell_from_config(
                    db, cell_id=fifo_chain_step4_source_cell_id, area_id=fifo_chain_step4_source_area_id, label="Punto origen 4", destination=False
                )
            fifo_chain_step4_destination_area, fifo_chain_step4_destination_cell = _resolve_route_cell_from_config(
                db, cell_id=fifo_chain_step4_destination_cell_id, area_id=fifo_chain_step4_destination_area_id, label="Punto destino 4", destination=True,
                allow_occupied_destination=True,
            )
        comment = f"QR execute {normalized_qr}"
        if route_mode == "fifo_chain" and fifo_chain_step1_source_mode == "any_area_by_material":
            order, selection = _execute_fifo_chain_step1_any_area_by_material(
                db,
                material_group_id=fifo_chain_step1_material_group_id,
                destination_area_id=destination_area_id,
                destination_cell_id=destination_cell_id,
                priority=priority,
                comment=comment,
                created_by=created_by or "qr",
                agv_code=agv_code,
                task_typ=task_typ,
            )
        elif fifo_material_policy == "any_available_from_source":
            order, selection = execute_fifo_request_any_available(db, source_area_id, destination_area_id, priority, comment, created_by or "qr", agv_code, task_typ, source_cell_id=source_cell_id)
        else:
            order, selection = execute_fifo_request(db, source_area_id, destination_area_id, material_id, priority, comment, created_by or "qr", agv_code, task_typ)
        created_order = order
        entities = {
            "material": selection.material_group,
            "rack": selection.rack,
            "source_area": selection.source_area,
            "destination_area": selection.destination_area,
            "source_cell": selection.source_cell,
            "destination_cell": selection.destination_cell,
            "route_mode": "simple_area",
            "fifo_material_policy": fifo_material_policy,
            "fifo_chain_total_steps": fifo_chain_total_steps,
            "fifo_chain_step1_source_mode": fifo_chain_step1_source_mode,
            "fifo_chain_step1_material": fifo_chain_step1_material,
            "fifo_chain_step1_material_group_id": fifo_chain_step1_material_group_id,
            "fifo_chain_step1_candidate": {"rack": selection.rack, "cell": selection.source_cell, "area": selection.source_area} if fifo_chain_step1_source_mode == "any_area_by_material" else {},
            "fifo_chain_step2_source_mode": fifo_chain_step2_source_mode,
            "fifo_chain_step2_material": fifo_chain_step2_material,
            "fifo_chain_step2_material_group_id": fifo_chain_step2_material_group_id,
            "fifo_chain_step3_source_mode": fifo_chain_step3_source_mode,
            "fifo_chain_step3_material": fifo_chain_step3_material,
            "fifo_chain_step3_material_group_id": fifo_chain_step3_material_group_id,
            "fifo_chain_step3_source_area": fifo_chain_step3_source_area,
            "fifo_chain_step3_destination_area": fifo_chain_step3_destination_area,
            "fifo_chain_step3_source_cell": fifo_chain_step3_source_cell,
            "fifo_chain_step3_destination_cell": fifo_chain_step3_destination_cell,
            "fifo_chain_step4_source_mode": fifo_chain_step4_source_mode,
            "fifo_chain_step4_material": fifo_chain_step4_material,
            "fifo_chain_step4_material_group_id": fifo_chain_step4_material_group_id,
            "fifo_chain_step4_source_area": fifo_chain_step4_source_area,
            "fifo_chain_step4_destination_area": fifo_chain_step4_destination_area,
            "fifo_chain_step4_source_cell": fifo_chain_step4_source_cell,
            "fifo_chain_step4_destination_cell": fifo_chain_step4_destination_cell,
            "route_points": [],
        }
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
            "destination": {"area": _area_payload(entities.get("second_destination_area") or selection.destination_area), "cell": _cell_payload(entities.get("second_destination_cell") or selection.destination_cell)},
            "route_mode": entities.get("route_mode") or "simple_area",
            "fifo_material_policy": entities.get("fifo_material_policy") or "specific_material",
            "fifo_chain_total_steps": entities.get("fifo_chain_total_steps") or 2,
            "fifo_chain_step1_source_mode": entities.get("fifo_chain_step1_source_mode") or "configured_area",
            "fifo_chain_step1_material": _material_payload(entities.get("fifo_chain_step1_material")),
            "fifo_chain_step2_source_mode": entities.get("fifo_chain_step2_source_mode") or "configured_area",
            "fifo_chain_step2_material": _material_payload(entities.get("fifo_chain_step2_material")),
            "fifo_chain_step3_source_mode": entities.get("fifo_chain_step3_source_mode") or "configured_area",
            "fifo_chain_step3_material": _material_payload(entities.get("fifo_chain_step3_material")),
            "fifo_chain_step3_source": {"area": _area_payload(entities.get("fifo_chain_step3_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_source_cell"))},
            "fifo_chain_step3_destination": {"area": _area_payload(entities.get("fifo_chain_step3_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step3_destination_cell"))},
            "fifo_chain_step4_source_mode": entities.get("fifo_chain_step4_source_mode") or "configured_area",
            "fifo_chain_step4_material_group_id": entities.get("fifo_chain_step4_material_group_id"),
            "fifo_chain_step4_material_group_name": getattr(entities.get("fifo_chain_step4_material"), "name", None),
            "fifo_chain_step4_material": _material_payload(entities.get("fifo_chain_step4_material")),
            "fifo_chain_step4_source": {"area": _area_payload(entities.get("fifo_chain_step4_source_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_source_cell"))},
            "fifo_chain_step4_destination": {"area": _area_payload(entities.get("fifo_chain_step4_destination_area")), "cell": _cell_payload(entities.get("fifo_chain_step4_destination_cell"))},
            "route_points": entities.get("route_points") or [],
            "fifo_chain_steps": entities.get("fifo_chain_steps") or [],
            "fifo_chain_projection": entities.get("fifo_chain_projection") or {},
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
        return result
    except ScanPreviewError as exc:
        diagnosis = _build_operator_diagnosis(db, message=exc.message, details=exc.details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {
            "ok": False,
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
        return result
    except HTTPException as exc:
        details = dict(exc.detail) if isinstance(exc.detail, dict) else {}
        message = details.get("operator_message") or str(exc.detail)
        diagnosis = _build_operator_diagnosis(db, message=message, details=details, qr_value=normalized_qr, station=station, qr_rule=qr_rule, entities=entities)
        result = {"ok": False, "mode": "execute", "status": "error", "scanner": {"scanner_code": str(scanner_code or "").strip()}, "scanner_code": str(scanner_code or "").strip(), "qr": {"qr_value": normalized_qr}, "qr_value": normalized_qr, "action": action, "message": diagnosis["operator_message"]}
        result.update(details)
        result.update({"error_code": diagnosis["error_code"], "operator_message": diagnosis["operator_message"], "suggested_action": diagnosis["suggested_action"], "step": diagnosis["step"]})
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
