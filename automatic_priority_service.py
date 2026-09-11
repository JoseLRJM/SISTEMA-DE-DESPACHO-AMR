"""QR-only immediate batches. Planning never advances physical/logical inventory."""
import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from fifo_service import ACTIVE_RACK_STATUSES, RACK_RESERVATION_ACTIVE_STATUSES
from models import Location, MovementOrder, QrActionRule, Rack, apply_rack_reservation_status
from rcs_client import generate_request_code

POLICY = "automatic_priority"


def priority_for_step(step):
    return str(max(1, 128 - int(step)))


def priority_preview(rule, entities):
    if not bool(getattr(rule, "automatic_priority", False)):
        return {}
    count = int(entities.get("fifo_chain_total_steps") or 2) if entities.get("route_mode") == "fifo_chain" else 1
    return {"automatic_priority": True, "send_mode": "immediate",
            "priority_plan": [{"step": n, "priority": priority_for_step(n)} for n in range(1, count + 1)]}


def prepare_group(db, rule, entities, *, created_by, agv_code):
    # SQLite's writer lock serializes reservation with other groups/direct/FIFO.
    db.execute(update(QrActionRule).where(QrActionRule.id == rule.id).values(updated_at=QrActionRule.updated_at))
    db.expire_all()
    if not rule.is_active or not rule.automatic_priority:
        raise HTTPException(409, "La configuración QR cambió; vuelve a validar.")
    steps = (entities.get("fifo_chain_projection") or {}).get("steps") or []
    if len(steps) != int(entities.get("fifo_chain_total_steps") or 0):
        raise HTTPException(409, "El grupo requiere una proyección completa de todos los tramos.")
    rack_ids = {s["rack_id"] for s in steps}
    cell_ids = {s[k] for s in steps for k in ("source_cell_id", "destination_cell_id")}
    active = db.scalars(select(MovementOrder).where(MovementOrder.status.in_(RACK_RESERVATION_ACTIVE_STATUSES))).all()
    if any({o.rack_id, o.pickup_rack_id, o.dropoff_rack_id} & rack_ids or
           {o.source_cell_id, o.destination_cell_id} & cell_ids for o in active):
        raise HTTPException(409, "Un recurso del grupo está reservado por otra operación independiente.")
    racks = {r.id:r for r in db.scalars(select(Rack).where(Rack.id.in_(rack_ids)))}
    cells = {c.id:c for c in db.scalars(select(Location).where(Location.id.in_(cell_ids)))}
    if len(racks) != len(rack_ids) or len(cells) != len(cell_ids):
        raise HTTPException(409, "Los recursos de la proyección ya no existen.")
    if any(str(r.status or "").lower() not in ACTIVE_RACK_STATUSES for r in racks.values()):
        raise HTTPException(409, "Un rack del grupo ya no está disponible.")
    occupancy = {c.id:c.rack_id for c in cells.values()}
    seen_racks = set()
    for n, step in enumerate(steps, 1):
        source, dest, rack = cells[step["source_cell_id"]], cells[step["destination_cell_id"]], racks[step["rack_id"]]
        if step["step"] != n or not source.enabled or not dest.enabled or not source.code or not dest.code:
            raise HTTPException(409, "Ruta proyectada inválida o deshabilitada.")
        if source.code != step["source_cell_code"] or dest.code != step["destination_cell_code"]:
            raise HTTPException(409, "Los códigos de ruta cambiaron después del preview.")
        if rack.id not in seen_racks and rack.material_group_id != step["material_group_id"]:
            raise HTTPException(409, "El material del rack cambió después del preview.")
        seen_racks.add(rack.id)
        if occupancy[source.id] != rack.id or (source.id != dest.id and occupancy[dest.id] is not None):
            raise HTTPException(409, "El inventario cambió después de proyectar el grupo.")
        if source.id == dest.id and not entities.get("allow_same_cell_move"):
            raise HTTPException(409, "Movimiento en la misma celda no permitido.")
        occupancy[source.id] = None
        occupancy[dest.id] = rack.id
    group = str(uuid4())
    rows = []
    now = datetime.utcnow()
    for step in steps:
        n = step["step"]
        source, dest = cells[step["source_cell_id"]], cells[step["destination_cell_id"]]
        row = MovementOrder(order_code=f"MO-AUTO-{group}-{n}", order_type="material_request", status="pending_dispatch",
            rack_id=step["rack_id"], source_cell_id=source.id, destination_cell_id=dest.id,
            source_area_id=source.area_id, destination_area_id=dest.area_id, material_group_id=step["material_group_id"],
            priority=priority_for_step(n), agv_code=agv_code, task_typ=entities.get(f"fifo_chain_step{n}_task_typ"),
            req_code=generate_request_code(), dispatch_status="not_sent", created_by=created_by,
            route_mode="fifo_chain", route_points_json=json.dumps([
                {"positionCode": source.code, "type":"00"}, {"positionCode":dest.code, "type":"00"}]),
            fifo_chain_group_id=group, fifo_chain_step=n, fifo_chain_total_steps=len(steps),
            fifo_chain_select_policy=POLICY, fifo_chain_status="batch_sending",
            fifo_chain_next_config_json=json.dumps({"automatic_priority":True, "qr_action_rule_id":rule.id}),
            fifo_chain_parent_order_id=rows[-1].id if rows else None,
            comment=f"QR {rule.qr_value}: prioridad automática, envío inmediato", created_at=now, updated_at=now)
        db.add(row); db.flush(); rows.append(row)
    for rack in racks.values():
        apply_rack_reservation_status(rack, True, updated_at=now, order_id=rows[0].id,
                                      source="qr_auto_priority", reason="immediate_group_reserved")
        db.add(rack)
    db.commit()
    return rows


def _stop_remaining(db, rows, message):
    from main import _rollback_unsuccessful_order_dispatch
    for row in rows:
        db.refresh(row)
        if not row.dispatch_request_json and not row.remote_task_code and row.dispatch_status == "not_sent":
            _rollback_unsuccessful_order_dispatch(db, row, message)


def execute_group(db, rows, dispatch_func):
    """One initial sender; never retry/resume a creation from batch status alone."""
    from main import _rollback_unsuccessful_order_dispatch
    group = rows[0].fifo_chain_group_id
    group_status = "batch_sent"
    for index, row in enumerate(rows):
        db.refresh(row)
        if row.fifo_chain_status != "batch_sending":
            group_status = "batch_partial"
            _stop_remaining(db, rows[index:], "Grupo interrumpido; no se envió este tramo.")
            break
        try:
            if not dispatch_func:
                raise ValueError("No se configuró transporte RCS para el grupo")
            db.info["automatic_priority_order_id"] = row.id
            dispatch_func(db, row)
        except Exception as exc:
            db.rollback(); db.refresh(row)
            if row.dispatch_request_json and row.dispatch_status != "success":
                row.dispatch_status = "uncertain"
                row.rcs_status = "dispatch_unknown"
                row.rcs_message = "Envío del grupo interrumpido; revisar traza RCS."
                db.commit()
            elif not row.dispatch_request_json:
                _rollback_unsuccessful_order_dispatch(db, row, str(exc))
        finally:
            db.info.pop("automatic_priority_order_id", None)
        db.refresh(row)
        if row.dispatch_status != "success" or not row.remote_task_code:
            group_status = "batch_uncertain" if row.dispatch_status == "uncertain" else "batch_partial"
            _stop_remaining(db, rows[index+1:], "No enviado: el tramo anterior fue rechazado o quedó incierto.")
            if row.dispatch_status == "error":
                _rollback_unsuccessful_order_dispatch(db, row, row.rcs_message)
            break
    db.execute(update(MovementOrder).where(MovementOrder.fifo_chain_group_id == group,
        MovementOrder.fifo_chain_status != "cancelled").values(fifo_chain_status=group_status, updated_at=datetime.utcnow()))
    db.commit()
    for row in rows: db.refresh(row)
    return {"ok":group_status == "batch_sent", "status":group_status, "fifo_chain_status":group_status,
            "fifo_chain_group_id":group, "fifo_chain_total_steps":len(rows),
            "movement_order_id":rows[0].id, "movement_orders":[{
                "movement_order_id":r.id, "step":r.fifo_chain_step, "reqCode":r.req_code,
                "taskCode":r.remote_task_code, "priority":r.priority, "status":r.status,
                "dispatch_status":r.dispatch_status, "rcs_status":r.rcs_status} for r in rows],
            "message":"Prioridad automática: grupo enviado inmediatamente." if group_status == "batch_sent"
                else "Grupo parcial: se conservan tareas enviadas; los tramos restantes no se enviaron."}


def recover_interrupted_groups(db):
    """Startup only: do not recreate tasks, and do not leave unsent reservations."""
    groups = db.scalars(select(MovementOrder.fifo_chain_group_id).where(
        MovementOrder.fifo_chain_select_policy == POLICY, MovementOrder.fifo_chain_status == "batch_sending"
    ).distinct()).all()
    for group in groups:
        rows = db.scalars(select(MovementOrder).where(MovementOrder.fifo_chain_group_id == group)).all()
        for row in rows:
            if row.dispatch_request_json and row.dispatch_status == "not_sent":
                row.dispatch_status = "uncertain"; row.rcs_status = "dispatch_unknown"
                row.rcs_message = "Reinicio durante envío; no reenviar, reconciliación RCS requerida."
        db.commit()
        _stop_remaining(db, rows, "Grupo interrumpido por reinicio; tramo no enviado.")
        db.execute(update(MovementOrder).where(MovementOrder.fifo_chain_group_id == group).values(fifo_chain_status="batch_interrupted"))
        db.commit()
