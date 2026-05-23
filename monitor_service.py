from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

from sqlalchemy import select

from fifo_service import simulate_order_completed, undo_movement_order
from logging_config import get_logger
from models import DebugConsoleEvent, MovementOrder, Rack, SessionLocal, apply_rack_reservation_status

ACTIVE_REMOTE_QUERY_STATUSES = {"pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo"}
RACK_RESERVATION_ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo")
_MONITOR_RUN_LOCK = threading.Lock()
logger = get_logger("app.monitor")


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
    active_other = db.execute(
        select(MovementOrder).where(
            MovementOrder.rack_id == order.rack_id,
            MovementOrder.id != order.id,
            MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES),
        )
    ).scalars().first()
    if rack is not None and not active_other and (rack.status or '').strip().lower() != 'available':
        old_status = rack.status or ""
        apply_rack_reservation_status(rack, False, updated_at=now)
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


def generate_req_code_ms() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:-3]


def build_auto_status_query_payload(task_codes: list[str], req_time: str | None = None) -> dict:
    unique_codes: list[str] = []
    for code in task_codes or []:
        value = str(code or "").strip()
        if value and value not in unique_codes:
            unique_codes.append(value)
    return {
        "reqCode": generate_req_code_ms(),
        "reqTime": req_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "clientCode": "",
        "tokenCode": "",
        "agvCode": "",
        "taskCodes": unique_codes,
    }


def _normalize_remote_status(value: str) -> str:
    raw = str(value or "").strip().lower()
    status_map = {
        "9": "completed",
        "completed": "completed",
        "complete": "completed",
        "finished": "completed",
        "done": "completed",
        "success": "completed",
        "5": "cancelled",
        "canceled": "cancelled",
        "cancelled": "cancelled",
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
    }
    return status_map.get(raw, raw)


def _mark_order_terminal_without_move(db, order: MovementOrder, terminal_status: str, *, source: str = "monitor_remote", auto_commit: bool = True):
    now = datetime.utcnow()
    _ensure_rack_available(db, order, source=source)
    previous_status = order.status or ""
    order.status = terminal_status
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
    logger.info("Applying remote status order_id=%s order_code=%s remote_status=%s", row.id, row.order_code, normalized)

    if normalized == "completed":
        if row.status != "completed":
            simulate_order_completed(db, row.id)
            row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
        _ensure_rack_available(db, row, source=source)
        row.status = "completed"
        row.updated_at = datetime.utcnow()
        db.add(row)
        if auto_commit:
            db.commit()
        return row

    if normalized == "cancelled":
        previous_status = row.status or ""
        if row.status == "cancel_requested_undo":
            undo_movement_order(db, row.id)
            row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
            row.cancel_source = source
            row.cancel_reason = "remote_status:cancelled"
            row.closed_by = source
            row.closed_at = datetime.utcnow()
            row.release_source = source
            row.updated_at = row.closed_at
            db.add(row)
            logger.info(
                "[CANCEL ORDER] order_id=%s order_code=%s previous_status=%s new_status=%s source=%s admin=%s reason=%s",
                row.id,
                row.order_code,
                previous_status,
                row.status,
                source,
                source,
                "remote_status:cancelled",
            )
            _append_monitor_debug_event(
                db,
                payload={"action": "cancel_order", "source": source, "order_id": row.id, "order_code": row.order_code, "previous_status": previous_status, "new_status": row.status, "admin": source, "reason": "remote_status:cancelled", "at": row.closed_at.isoformat()},
                message=f"[CANCEL ORDER] order_id={row.id} source={source} reason=remote_status:cancelled",
                created_at=row.closed_at,
            )
            if auto_commit:
                db.commit()
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

    if normalized == "failed":
        if row.status in {"cancel_requested_undo", "cancel_requested_total"}:
            _ensure_rack_available(db, row, source=source)
            row.status = "failed"
            row.updated_at = datetime.utcnow()
            db.add(row)
            if auto_commit:
                db.commit()
            return row
        _mark_order_terminal_without_move(db, row, "failed", source=source, auto_commit=auto_commit)
        row = db.execute(select(MovementOrder).where(MovementOrder.id == row.id)).scalar_one()
        return row

    return row


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
                    item = normalized_map.get(task_code, {})
                    raw_item = item.get("raw") if isinstance(item, dict) else None
                    task_status = _normalize_remote_status(str(item.get("taskStatus") or item.get("task_status") or response.task_status or ""))
                    message = str(item.get("message") or response.message or "")
                    response_payload = raw_item if isinstance(raw_item, dict) else (response.raw or {})

                    for row in rows_for_code:
                        row.req_code = response.reqCode or request_payload.get("reqCode") or row.req_code
                        row.status_query_request_json = json.dumps(request_payload, ensure_ascii=False)
                        row.status_query_response_json = json.dumps(response_payload, ensure_ascii=False)
                        row.status_query_checked_at = now
                        row.rcs_last_update = now
                        row.rcs_status = task_status or row.rcs_status
                        row.rcs_message = message or row.rcs_message
                        _append_status_query_log(db, row, kind="status_query", request_payload=request_payload, response_payload=response_payload, message=message, arrived_at=now)
                        db.add(row)
                        touched_rows.append((row.id, task_status))

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
