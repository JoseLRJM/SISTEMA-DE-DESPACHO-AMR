from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime

from sqlalchemy import select

from fifo_service import simulate_order_completed
from logging_config import get_logger
from models import DebugConsoleEvent, MovementOrder, Rack, SessionLocal, apply_rack_reservation_status

ACTIVE_REMOTE_QUERY_STATUSES = {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"}
RACK_RESERVATION_ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo")
_MONITOR_RUN_LOCK = threading.Lock()
logger = get_logger("app.monitor")


def _active_orders_for_rack(db, rack_id: int | None, *, exclude_order_id: int | None = None):
    if not rack_id:
        return []
    stmt = select(MovementOrder).where(
        MovementOrder.rack_id == rack_id,
        MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
    )
    if exclude_order_id:
        stmt = stmt.where(MovementOrder.id != exclude_order_id)
    return db.execute(stmt).scalars().all()


def _append_monitor_debug_event(db, *, payload: dict, message: str, created_at: datetime | None = None):
    now = created_at or datetime.utcnow()
    db.add(DebugConsoleEvent(
        direction="received",
        module="cleanup",
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        message=message,
        created_at=now,
    ))


def _ensure_rack_available(db, order: MovementOrder, *, source: str = "monitor_remote"):
    now = datetime.utcnow()
    rack = db.execute(select(Rack).where(Rack.id == order.rack_id)).scalar_one_or_none()
    active_other = _active_orders_for_rack(db, order.rack_id, exclude_order_id=order.id)
    if rack is not None:
        logger.info(
            "RACK_RELEASE_ATTEMPT rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            order.id,
            order.status,
            source,
            "remote_status_terminal",
        )
    if rack is not None and active_other:
        blocking = active_other[0]
        logger.warning(
            "RACK_RELEASE_BLOCKED_ACTIVE_ORDER rack_id=%s rack_code=%s attempted_order_id=%s blocking_order_id=%s blocking_dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            order.id,
            blocking.id,
            blocking.status,
            source,
            "remote_status_terminal",
        )
    if rack is not None and not active_other and (rack.status or '').strip().lower() != 'available':
        old_status = rack.status or ""
        apply_rack_reservation_status(rack, False, updated_at=now, order_id=order.id, dispatch_status=order.status, source=source, reason="remote_status_terminal")
        db.add(rack)
        logger.info("[CLEANUP] Rack %s %s -> %s reason=remote_status_terminal order_id=%s", rack.id, old_status, rack.status, order.id)
        logger.info(
            "[RACK RELEASE] rack_id=%s rack_code=%s previous_status=%s new_status=%s source=%s related_order=%s",
            rack.id,
            rack.code,
            old_status,
            rack.status,
            source,
            order.id,
        )
        _append_monitor_debug_event(
            db,
            payload={"action": "rack_release", "source": source, "rack_id": rack.id, "rack_code": rack.code, "previous_status": old_status, "new_status": rack.status, "related_order": order.id, "at": now.isoformat()},
            message=f"[RACK RELEASE] rack_id={rack.id} source={source} related_order={order.id}",
            created_at=now,
        )
        logger.info(
            "RACK_RELEASE_COMPLETED rack_id=%s rack_code=%s order_id=%s dispatch_status=%s source=%s reason=%s",
            rack.id,
            rack.code,
            order.id,
            order.status,
            source,
            "remote_status_terminal",
        )


def _finalize_cancel_return_undo(db, row: MovementOrder, *, previous_status: str, remote_terminal_status: str, source: str, auto_commit: bool = True) -> MovementOrder:
    # A terminal cancel-return response does not prove the rack's physical cell, so close without moving local cells.
    now = datetime.utcnow()

    if remote_terminal_status == "completed":
        row.status = "completed"
    elif remote_terminal_status == "cancelled":
        row.status = "cancelled"
    else:
        row.status = "failed"
    row.rcs_status = remote_terminal_status
    row.cancel_source = source
    row.cancel_reason = f"remote_status:{remote_terminal_status}"
    row.closed_by = source
    row.closed_at = now
    row.updated_at = now
    row.release_source = source
    db.add(row)
    _ensure_rack_available(db, row, source=source)
    rack = db.execute(select(Rack).where(Rack.id == row.rack_id)).scalar_one_or_none() if row.rack_id else None

    logger.info(
        "CANCEL_UNDO_TERMINAL_CLOSE order_id=%s rack_id=%s rack_code=%s robot_code=%s previous_dispatch_status=%s new_dispatch_status=%s rcs_status=%s source=%s reason=%s cells_modified=false",
        row.id,
        row.rack_id,
        rack.code if rack else None,
        row.agv_code,
        previous_status,
        row.status,
        row.rcs_status,
        source,
        f"remote_status:{remote_terminal_status}",
    )
    logger.info(
        "[CANCEL ORDER] order_id=%s order_code=%s previous_status=%s new_status=%s source=%s admin=%s reason=%s",
        row.id,
        row.order_code,
        previous_status,
        row.status,
        source,
        source,
        f"remote_status:{remote_terminal_status}",
    )
    _append_monitor_debug_event(
        db,
        payload={"action": "cancel_order", "source": source, "order_id": row.id, "order_code": row.order_code, "previous_status": previous_status, "new_status": row.status, "admin": source, "reason": f"remote_status:{remote_terminal_status}", "cells_modified": False, "at": now.isoformat()},
        message=f"[CANCEL ORDER] order_id={row.id} source={source} reason=remote_status:{remote_terminal_status} cells_modified=false",
        created_at=now,
    )
    if auto_commit:
        db.commit()
    return row


def generate_req_code_ms() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:-3]


def build_auto_status_query_payload(task_codes: list[str], req_time: str | None = None) -> dict:
    unique_codes: list[str] = []
    for code in task_codes or []:
        value = str(code or "").strip()
        if value and value not in unique_codes:
            unique_codes.append(value)
    payload = {
        "reqCode": generate_req_code_ms(),
        "taskCodes": unique_codes,
    }
    if req_time:
        payload["reqTime"] = req_time
    return payload


def _normalize_remote_status(value: str) -> str:
    raw = str(value or "").strip().lower()
    status_map = {
        "0": "failed",
        "sending exception": "failed",
        "sending_exception": "failed",
        "1": "dispatched",
        "created": "dispatched",
        "2": "in_progress",
        "9": "completed",
        "completed": "completed",
        "complete": "completed",
        "finished": "completed",
        "done": "completed",
        "success": "completed",
        "5": "cancelled",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "3": "dispatched",
        "sending": "dispatched",
        "4": "canceling",
        "canceling": "canceling",
        "6": "in_progress",
        "resending": "in_progress",
        "10": "failed",
        "interrupted": "failed",
        "executing": "in_progress",
        "running": "in_progress",
        "in_progress": "in_progress",
        "processing": "in_progress",
        "working": "in_progress",
        "waiting": "dispatched",
        "queued": "dispatched",
        "sent": "dispatched",
        "created": "dispatched",
        "dispatch": "dispatched",
        "failed": "failed",
        "error": "failed",
        "abnormal": "failed",
        "aborted": "failed",
        "terminated": "failed",
    }
    return status_map.get(raw, raw)


def _task_status_items_by_code(response) -> dict[str, dict]:
    items_by_code: dict[str, dict] = {}
    for item in response.task_statuses or []:
        code = str(item.get("taskCode") or item.get("task_code") or "").strip()
        if code:
            items_by_code[code] = item
    return items_by_code


def _task_code_from_error_message(message: str, known_task_codes: list[str] | None = None) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    for code in known_task_codes or []:
        value = str(code or "").strip()
        if value and value in text:
            return value
    match = re.search(r"task\s*code\s*\.?\s*:?\s*([A-Za-z0-9_./:-]+)", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"taskcode\s*\.?\s*:?\s*([A-Za-z0-9_./:-]+)", text, flags=re.IGNORECASE)
    return match.group(1).strip(" .,:;") if match else ""


def _single_task_response_item(response, task_code: str) -> dict | None:
    code = str(task_code or "").strip()
    if not code:
        return None
    items_by_code = _task_status_items_by_code(response)
    if code in items_by_code:
        return items_by_code[code]
    if len(response.task_statuses or []) == 1:
        item = dict((response.task_statuses or [])[0])
        response_code = str(item.get("taskCode") or "").strip()
        if not response_code:
            item["taskCode"] = code
            return item
        if response_code == code:
            return item
    if response.task_status:
        return {
            "taskCode": code,
            "taskStatus": response.task_status,
            "message": response.message,
            "raw": response.raw,
        }
    return None


def _mark_order_terminal_without_move(db, order: MovementOrder, terminal_status: str, *, source: str = "monitor_remote", auto_commit: bool = True):
    now = datetime.utcnow()
    _ensure_rack_available(db, order, source=source)
    previous_status = order.status or ""
    order.status = terminal_status
    order.rcs_status = terminal_status
    order.cancel_source = source
    order.cancel_reason = f"remote_status:{terminal_status}"
    order.closed_by = source
    order.closed_at = now
    order.release_source = source
    order.updated_at = now
    db.add(order)
    logger.info(
        "[CANCEL ORDER] order_id=%s order_code=%s previous_status=%s new_status=%s source=%s admin=%s reason=%s",
        order.id,
        order.order_code,
        previous_status,
        terminal_status,
        source,
        source,
        f"remote_status:{terminal_status}",
    )
    _append_monitor_debug_event(
        db,
        payload={"action": "cancel_order", "source": source, "order_id": order.id, "order_code": order.order_code, "previous_status": previous_status, "new_status": terminal_status, "admin": source, "reason": f"remote_status:{terminal_status}", "at": now.isoformat()},
        message=f"[CANCEL ORDER] order_id={order.id} source={source} reason=remote_status:{terminal_status}",
        created_at=now,
    )
    if auto_commit:
        db.commit()


def apply_remote_status_to_order(db, row: MovementOrder, task_status: str, *, source: str = "monitor_remote", auto_commit: bool = True):
    normalized = _normalize_remote_status(task_status)
    if not normalized:
        return row
    logger.info("Applying remote status order_id=%s order_code=%s remote_status=%s raw_status=%s", row.id, row.order_code, normalized, task_status)

    if normalized == "completed":
        if row.status == "cancel_requested_undo":
            previous_status = row.status or ""
            return _finalize_cancel_return_undo(db, row, previous_status=previous_status, remote_terminal_status="completed", source=source, auto_commit=auto_commit)
        order_id = row.id
        if row.status in {"pending_dispatch", "dispatched", "in_progress"}:
            try:
                simulate_order_completed(db, order_id)
                row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one()
            except Exception as exc:
                db.rollback()
                row = db.execute(select(MovementOrder).where(MovementOrder.id == order_id)).scalar_one()
                now = datetime.utcnow()
                row.status = "completed"
                row.rcs_status = "completed"
                row.closed_by = source
                row.closed_at = now
                row.release_source = source
                row.updated_at = now
                _ensure_rack_available(db, row, source=source)
                db.add(row)
                logger.warning(
                    "RCS_COMPLETED_WITHOUT_LOCAL_CELL_MOVE order_id=%s order_code=%s rack_id=%s source=%s cells_modified=false error=%s",
                    row.id,
                    row.order_code,
                    row.rack_id,
                    source,
                    exc,
                )
                if auto_commit:
                    db.commit()
                return row
        _ensure_rack_available(db, row, source=source)
        now = datetime.utcnow()
        row.status = "completed"
        row.rcs_status = "completed"
        row.closed_by = source
        row.closed_at = now
        row.release_source = source
        row.updated_at = now
        db.add(row)
        if auto_commit:
            db.commit()
        return row

    if normalized == "cancelled":
        previous_status = row.status or ""
        if row.status == "cancel_requested_undo":
            row = _finalize_cancel_return_undo(db, row, previous_status=previous_status, remote_terminal_status="cancelled", source=source, auto_commit=auto_commit)
        elif row.status not in {"cancelled", "undone"}:
            _mark_order_terminal_without_move(db, row, "cancelled", source=source, auto_commit=auto_commit)
            row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
        return row

    if normalized == "in_progress":
        if row.status != "cancel_requested_undo" and row.status != "cancel_requested_total":
            row.status = "in_progress"
            row.updated_at = datetime.utcnow()
            db.add(row)
            if auto_commit:
                db.commit()
        return row

    if normalized == "dispatched":
        if row.status == "pending_dispatch":
            row.status = "dispatched"
            row.updated_at = datetime.utcnow()
            db.add(row)
            if auto_commit:
                db.commit()
        return row

    if normalized == "canceling":
        row.rcs_status = "canceling"
        row.updated_at = datetime.utcnow()
        db.add(row)
        if auto_commit:
            db.commit()
        return row

    if normalized == "failed":
        _mark_order_terminal_without_move(db, row, "failed", source=source, auto_commit=auto_commit)
        row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
        return row

    return row


def _record_status_query_for_order(
    db,
    row: MovementOrder,
    *,
    request_payload: dict,
    response_payload: dict,
    req_code: str,
    message: str,
    now: datetime,
    normalized_status: str = "",
    update_message: bool = True,
):
    from main import _append_status_query_log

    row.req_code = req_code or row.req_code
    row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
    row.status_query_response_json = json.dumps(response_payload or {}, ensure_ascii=False)
    row.status_query_checked_at = now
    row.rcs_last_update = now
    if normalized_status:
        row.rcs_status = normalized_status
    if update_message and message:
        row.rcs_message = message
    row.updated_at = now
    _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=response_payload or {}, message=message or "", arrived_at=now)
    db.add(row)


def _apply_matched_task_status(db, row: MovementOrder, *, item: dict, request_payload: dict, response, now: datetime, source: str) -> tuple[int, str] | None:
    response_task_code = str(item.get("taskCode") or item.get("task_code") or "").strip()
    order_task_code = str(row.remote_task_code or "").strip()
    raw_status = str(item.get("taskStatus") or item.get("task_status") or "").strip()
    normalized_status = _normalize_remote_status(raw_status)
    logger.info(
        "TASK_STATUS_RAW movement_order_id=%s remote_task_code=%s raw_taskStatus=%s normalized_status=%s agvCode=%s taskTyp=%s",
        row.id,
        order_task_code,
        raw_status,
        normalized_status,
        item.get("agvCode") or item.get("agv_code") or "",
        item.get("taskTyp") or item.get("task_type") or "",
    )
    if response_task_code != order_task_code:
        logger.warning(
            "TASK_STATUS_SKIP_MISMATCH movement_order_id=%s order_remote_task_code=%s response_taskCode=%s reason=taskCode mismatch",
            row.id,
            order_task_code,
            response_task_code,
        )
        return None
    logger.info(
        "TASK_STATUS_APPLY movement_order_id=%s order_remote_task_code=%s response_taskCode=%s raw_taskStatus=%s normalized_status=%s",
        row.id,
        order_task_code,
        response_task_code,
        raw_status,
        normalized_status,
    )
    response_payload = item.get("raw") if isinstance(item.get("raw"), dict) else (response.raw or {})
    message = str(item.get("message") or response.message or "")
    _record_status_query_for_order(
        db,
        row,
        request_payload=request_payload,
        response_payload=response_payload,
        req_code=response.reqCode or request_payload.get("reqCode") or "",
        message=message,
        now=now,
        normalized_status=normalized_status,
        update_message=True,
    )
    return (row.id, normalized_status) if normalized_status else None


def _apply_touched_remote_statuses(db, touched_rows: list[tuple[int, str]]):
    for row_id, task_status in touched_rows:
        row = db.execute(select(MovementOrder).where(MovementOrder.id == row_id)).scalar_one()
        row = apply_remote_status_to_order(db, row, task_status, auto_commit=False)
        row.rcs_status = task_status or row.rcs_status
        row.updated_at = datetime.utcnow()
        db.add(row)


def _run_single_status_query(db, *, client, task_code: str, rows_for_code: list[MovementOrder], source: str) -> list[tuple[int, str]]:
    single_payload = build_auto_status_query_payload(task_codes=[task_code])
    single_now = datetime.utcnow()
    touched_rows: list[tuple[int, str]] = []
    try:
        logger.info("QUERY_TASK_STATUS_FALLBACK_SINGLE taskCode=%s", task_code)
        single_response = client.query_task_status_with_payload(single_payload)
        single_item = _single_task_response_item(single_response, task_code)
        raw_single_status = str((single_item or {}).get("taskStatus") or (single_item or {}).get("task_status") or "").strip()
        logger.info(
            "QUERY_TASK_STATUS_FALLBACK_SINGLE taskCode=%s code=%s message=%s taskStatus=%s",
            task_code,
            single_response.code,
            single_response.message,
            raw_single_status,
        )
        if single_response.ok and single_item:
            for row in rows_for_code:
                applied = _apply_matched_task_status(db, row, item=single_item, request_payload=single_payload, response=single_response, now=single_now, source=source)
                if applied:
                    touched_rows.append(applied)
        else:
            error_payload = single_response.raw or {"code": single_response.code, "message": single_response.message}
            for row in rows_for_code:
                _record_status_query_for_order(
                    db,
                    row,
                    request_payload=single_payload,
                    response_payload=error_payload,
                    req_code=single_response.reqCode or single_payload.get("reqCode") or "",
                    message=single_response.message or "",
                    now=single_now,
                    normalized_status="failed",
                    update_message=True,
                )
    except Exception as single_exc:
        logger.warning("QUERY_TASK_STATUS_FALLBACK_SINGLE taskCode=%s code=- message=%s taskStatus=-", task_code, single_exc)
        error_payload = {"error": str(single_exc)}
        for row in rows_for_code:
            _record_status_query_for_order(
                db,
                row,
                request_payload=single_payload,
                response_payload=error_payload,
                req_code=single_payload.get("reqCode") or "",
                message=str(single_exc),
                now=single_now,
                normalized_status="failed",
                update_message=True,
            )
    return touched_rows


def _run_status_query_batch_safely(
    db,
    *,
    client,
    task_codes: list[str],
    task_map: dict[str, list[MovementOrder]],
    debug_base_url: str,
    debug_endpoint: str,
    apply_delay_seconds: float,
) -> int:
    from main import _append_debug_console_event

    request_payload = build_auto_status_query_payload(task_codes=task_codes)
    now = datetime.utcnow()
    touched_rows: list[tuple[int, str]] = []
    try:
        logger.info(
            "QUERY_TASK_STATUS_BATCH_REQUEST reqCode=%s taskCodes_count=%s taskCodes=%s",
            request_payload.get("reqCode"),
            len(task_codes),
            task_codes,
        )
        _append_debug_console_event(db, direction="sent", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload=request_payload, message=f"Consulta automatica de {len(task_codes)} tareas activas")
        response = client.query_task_status_with_payload(request_payload)
        _append_debug_console_event(db, direction="received", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or "Respuesta del monitor")
        items_by_code = _task_status_items_by_code(response)
        logger.info(
            "QUERY_TASK_STATUS_BATCH_RESPONSE code=%s message=%s data_count=%s found_taskCodes=%s",
            response.code,
            response.message,
            len(response.task_statuses or []),
            list(items_by_code.keys()),
        )
        if response.ok:
            for task_code, rows_for_code in task_map.items():
                item = items_by_code.get(task_code)
                if item is None and len(task_codes) == 1:
                    item = _single_task_response_item(response, task_code)
                if not item:
                    for row in rows_for_code:
                        logger.warning(
                            "TASK_STATUS_SKIP_MISMATCH movement_order_id=%s order_remote_task_code=%s response_taskCode=- reason=no exact taskCode in batch response",
                            row.id,
                            row.remote_task_code,
                        )
                        _record_status_query_for_order(
                            db,
                            row,
                            request_payload=request_payload,
                            response_payload=response.raw or {},
                            req_code=response.reqCode or request_payload.get("reqCode") or "",
                            message="",
                            now=now,
                            update_message=False,
                        )
                    continue
                for row in rows_for_code:
                    applied = _apply_matched_task_status(db, row, item=item, request_payload=request_payload, response=response, now=now, source="task_monitor")
                    if applied:
                        touched_rows.append(applied)
        else:
            failed_task_code = _task_code_from_error_message(response.message, task_codes)
            logger.warning(
                "QUERY_TASK_STATUS_BATCH_RESPONSE code=%s message=%s failed_taskCode=%s action=fallback_single",
                response.code,
                response.message,
                failed_task_code or "-",
            )
            for task_code, rows_for_code in task_map.items():
                if failed_task_code and task_code == failed_task_code:
                    error_payload = response.raw or {"code": response.code, "message": response.message}
                    for row in rows_for_code:
                        _record_status_query_for_order(
                            db,
                            row,
                            request_payload=request_payload,
                            response_payload=error_payload,
                            req_code=response.reqCode or request_payload.get("reqCode") or "",
                            message=response.message or "",
                            now=now,
                            normalized_status="failed",
                            update_message=True,
                        )
                    continue
                touched_rows.extend(_run_single_status_query(db, client=client, task_code=task_code, rows_for_code=rows_for_code, source="task_monitor"))
    except Exception as exc:
        logger.error("Task monitor batch failed, using individual fallback: %s", exc)
        _append_debug_console_event(db, direction="received", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
        for task_code, rows_for_code in task_map.items():
            touched_rows.extend(_run_single_status_query(db, client=client, task_code=task_code, rows_for_code=rows_for_code, source="task_monitor"))

    db.commit()
    _apply_touched_remote_statuses(db, touched_rows)
    db.commit()
    time.sleep(apply_delay_seconds)
    logger.info("Task monitor completed active task check count=%s touched=%s", len(task_codes), len(touched_rows))
    return len(task_codes)


def check_active_tasks(
    apply_delay_seconds: float = 0.3,
    base_url_override: str | None = None,
    endpoint_override: str | None = None,
    *,
    wait_for_lock: bool = False,
):
    from main import _append_debug_console_event, _append_status_query_log, _get_rcs_client_with_overrides, _resolve_rcs_target

    acquired = _MONITOR_RUN_LOCK.acquire(blocking=wait_for_lock)
    if not acquired:
        return 0

    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(MovementOrder).where(MovementOrder.status.in_(tuple(ACTIVE_REMOTE_QUERY_STATUSES))).order_by(MovementOrder.id.asc())
            ).scalars().all()
            for row in rows:
                if not str(row.remote_task_code or "").strip():
                    logger.warning(
                        "RCS_STATUS_UNMATCHED_IGNORED order_id=%s order_code=%s remote_task_code=%s queried_task_codes=%s source=task_monitor reason=empty_remote_task_code",
                        row.id,
                        row.order_code,
                        row.remote_task_code,
                        [],
                    )
            target_rows = [row for row in rows if row.remote_task_code and (row.dispatch_status or "") == "success"]
            if not target_rows:
                return 0

            task_codes = []
            task_map = {}
            for row in target_rows:
                code = (row.remote_task_code or "").strip()
                if not code:
                    continue
                if code not in task_map:
                    task_codes.append(code)
                    task_map[code] = []
                task_map[code].append(row)
            if not task_codes:
                return 0

            client = _get_rcs_client_with_overrides(db, base_url=base_url_override, query_endpoint=endpoint_override)
            debug_base_url, debug_endpoint = _resolve_rcs_target(db, base_url=base_url_override, endpoint=endpoint_override, mode="query")
            return _run_status_query_batch_safely(
                db,
                client=client,
                task_codes=task_codes,
                task_map=task_map,
                debug_base_url=debug_base_url,
                debug_endpoint=debug_endpoint,
                apply_delay_seconds=apply_delay_seconds,
            )
            request_payload = build_auto_status_query_payload(task_codes=task_codes)
            now = datetime.utcnow()
            try:
                logger.info("Task monitor checking active tasks count=%s", len(task_codes))
                _append_debug_console_event(db, direction="sent", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload=request_payload, message=f"Consulta automática de {len(task_codes)} tareas activas")
                response = client.query_task_status_with_payload(request_payload)
                _append_debug_console_event(db, direction="received", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload=response.raw, message=response.message or "Respuesta del monitor")
                normalized_map = {}
                for item in response.task_statuses or []:
                    code = str(item.get("taskCode") or item.get("task_code") or "").strip()
                    if code:
                        normalized_map[code] = item

                touched_rows = []
                for task_code, rows_for_code in task_map.items():
                    item = normalized_map.get(task_code)
                    has_exact_match = isinstance(item, dict)
                    has_single_code_fallback = len(task_codes) == 1 and not normalized_map and bool(response.task_status)
                    reliable_status = has_exact_match or has_single_code_fallback
                    item = item or {}
                    raw_item = item.get("raw") if isinstance(item, dict) else None
                    task_status = _normalize_remote_status(str(item.get("taskStatus") or item.get("task_status") or (response.task_status if has_single_code_fallback else "") or ""))
                    message = str(item.get("message") or response.message or "")
                    response_payload = raw_item if isinstance(raw_item, dict) else (response.raw or {})

                    for row in rows_for_code:
                        row.req_code = response.reqCode or request_payload.get("reqCode") or row.req_code
                        row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
                        row.status_query_response_json = json.dumps(response_payload, ensure_ascii=False)
                        row.status_query_checked_at = now
                        row.rcs_last_update = now
                        if reliable_status:
                            row.rcs_status = task_status or row.rcs_status
                        row.rcs_message = message or row.rcs_message
                        _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=response_payload, message=message, arrived_at=now)
                        db.add(row)
                        if reliable_status and task_status:
                            touched_rows.append((row.id, task_status))
                        else:
                            logger.warning(
                                "RCS_STATUS_UNMATCHED_IGNORED order_id=%s order_code=%s remote_task_code=%s queried_task_codes=%s source=task_monitor",
                                row.id,
                                row.order_code,
                                row.remote_task_code,
                                task_codes,
                            )

                db.commit()

                for row_id, task_status in touched_rows:
                    row = db.execute(select(MovementOrder).where(MovementOrder.id == row_id)).scalar_one()
                    row = apply_remote_status_to_order(db, row, task_status, auto_commit=False)
                    row.rcs_status = task_status or row.rcs_status
                    row.updated_at = datetime.utcnow()
                    db.add(row)

                db.commit()
                time.sleep(apply_delay_seconds)
                logger.info("Task monitor completed active task check count=%s", len(task_codes))
                return len(task_codes)
            except Exception as exc:
                logger.error("Task monitor failed: %s", exc)
                _append_debug_console_event(db, direction="received", module="task_monitor", base_url=debug_base_url, endpoint=debug_endpoint, payload={"error": str(exc)}, message=str(exc))
                error_payload = {"error": str(exc)}
                for row in target_rows:
                    row.req_code = request_payload.get("reqCode") or row.req_code
                    row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
                    row.status_query_response_json = json.dumps(error_payload, ensure_ascii=False)
                    row.status_query_checked_at = now
                    row.rcs_message = str(exc)
                    row.updated_at = now
                    _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=error_payload, message=str(exc), arrived_at=now)
                    db.add(row)
                db.commit()
                return 0
    finally:
        _MONITOR_RUN_LOCK.release()


class TaskMonitor:
    def __init__(self, interval_seconds: int = 3):
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = None
        self._loop_count = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="rcs-task-monitor", daemon=True)
        self._thread.start()
        logger.info("Task monitor thread started interval_seconds=%s", self.interval_seconds)

    def stop(self):
        self._stop_event.set()
        logger.info("Task monitor stop requested")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                check_active_tasks(wait_for_lock=False)
            except Exception:
                pass
            self._loop_count += 1
            if self._loop_count >= 20:
                self._loop_count = 0
                try:
                    os.system("cls" if os.name == "nt" else "clear")
                except Exception:
                    pass
            self._stop_event.wait(self.interval_seconds)
