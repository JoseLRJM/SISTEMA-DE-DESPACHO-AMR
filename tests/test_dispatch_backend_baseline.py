import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import fifo_service
import main
from models import Area, Base, Location, MaterialGroup, MovementOrder, OperatorWindow, OperatorWindowButton, Rack
from rcs_client import RcsError, RcsSimpleResponse, RcsTaskResponse, RcsTimeoutError


class StubRcsClient:
    _token_code = ""

    def __init__(self, outcome="success"):
        self.outcome = outcome
        self.payloads = []
        self.cancel_payloads = []

    def create_task(self, task):
        payload = task.to_payload()
        self.payloads.append(payload)
        if self.outcome == "timeout":
            raise RcsTimeoutError("stub timeout")
        if self.outcome == "transport":
            raise RcsError("stub transport")
        accepted = self.outcome == "success"
        return RcsTaskResponse(
            code=0 if accepted else 1,
            data="REMOTE-001" if accepted else "",
            message="success" if accepted else "failed by stub",
            reqCode=payload["reqCode"],
            raw={"code": 0 if accepted else 1, "data": "REMOTE-001" if accepted else "", "message": "success" if accepted else "failed by stub", "reqCode": payload["reqCode"]},
        )

    def cancel_task_with_payload(self, payload):
        self.cancel_payloads.append(dict(payload))
        return RcsSimpleResponse(code=0, message="success", reqCode=payload["reqCode"], raw={"code": 0, "message": "success", "reqCode": payload["reqCode"]})


class DispatchBackendBaselineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        now = datetime(2024, 1, 1, 12, 0, 0)
        with self.factory() as db:
            self.source_area = Area(code="SRC", name="Source", is_active=1, priority=1, updated_at=now)
            self.destination_area = Area(code="DST", name="Destination", is_active=1, priority=1, updated_at=now)
            self.material = MaterialGroup(code="MAT", name="Material", is_active=1, updated_at=now)
            db.add_all([self.source_area, self.destination_area, self.material]); db.flush()
            self.rack = Rack(code="RACK-001", status="available", material_group_id=self.material.id, fifo_entered_at=now, quantity=1, updated_at=now)
            db.add(self.rack); db.flush()
            self.source = Location(x=10, y=10, code="SRC-01", enabled=1, is_visible=1, status=1, area_id=self.source_area.id, rack_id=self.rack.id, updated_at=now)
            self.destination = Location(x=20, y=20, code="DST-01", enabled=1, is_visible=1, status=0, area_id=self.destination_area.id, updated_at=now)
            db.add_all([self.source, self.destination]); db.commit()
            self.ids = {
                "source_area": self.source_area.id, "destination_area": self.destination_area.id,
                "material": self.material.id, "rack": self.rack.id,
                "source": self.source.id, "destination": self.destination.id,
            }

    def tearDown(self):
        self.engine.dispose()

    def _expected_payload(self, *, agv="AGV-7", task_typ="A01", priority="1"):
        return {
            "agvCode": agv, "clientCode": "", "ctnrCode": "", "ctnrTyp": "", "data": "",
            "materialLot": "", "podCode": "", "podDir": "", "podTyp": "",
            "positionCodePath": [{"positionCode": "SRC-01", "type": "00"}, {"positionCode": "DST-01", "type": "00"}],
            "priority": priority, "reqTime": "", "taskCode": "", "taskTyp": task_typ,
            "tokenCode": "", "wbCode": "",
        }

    def _run_endpoint(self, call, outcome="success"):
        stub = StubRcsClient(outcome)
        counters = {"commits": 0, "flushes": 0, "refreshes": 0}
        def committed(_session): counters["commits"] += 1
        def flushed(*_args): counters["flushes"] += 1
        original_refresh = Session.refresh
        def refreshed(session, *args, **kwargs):
            counters["refreshes"] += 1
            return original_refresh(session, *args, **kwargs)
        event.listen(Session, "after_commit", committed)
        event.listen(Session, "after_flush", flushed)
        try:
            with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_from_settings", lambda _db: stub), patch.object(main, "_resolve_rcs_target", lambda _db, mode: ("stub://rcs", "/createTask")), patch.object(Session, "refresh", refreshed):
                result = call()
        finally:
            event.remove(Session, "after_commit", committed)
            event.remove(Session, "after_flush", flushed)
        return result, stub, counters

    def _assert_success_state(self, result, stub, expected_type):
        self.assertEqual(len(stub.payloads), 1)
        payload = dict(stub.payloads[0]); req_code = payload.pop("reqCode")
        self.assertTrue(req_code)
        self.assertEqual(payload, self._expected_payload())
        self.assertEqual(result.order_type, expected_type)
        self.assertEqual(result.rack_id, self.ids["rack"])
        self.assertEqual(result.source_cell_id, self.ids["source"])
        self.assertEqual(result.destination_cell_id, self.ids["destination"])
        self.assertEqual(result.dispatch_status, "success")
        self.assertEqual(result.rcs_status, "sent")
        self.assertEqual(result.status, "dispatched")
        with self.factory() as db:
            persisted_order = db.get(MovementOrder, result.order_id)
            legacy_response = main._movement_order_out(db, persisted_order)
            self.assertEqual(result.model_dump(mode="json"), legacy_response.model_dump(mode="json"))
            rack = db.get(Rack, self.ids["rack"]); source = db.get(Location, self.ids["source"]); destination = db.get(Location, self.ids["destination"])
            self.assertEqual(rack.status, "reserved")
            self.assertEqual(source.rack_id, rack.id)
            self.assertIsNone(destination.rack_id)

    def test_fifo_success_payload_and_persistence_baseline(self):
        body = main.FifoRequestIn(source_area_id=self.ids["source_area"], destination_area_id=self.ids["destination_area"], material_group_id=self.ids["material"], priority="alta", agv_code="AGV-7", task_typ="A01", created_by="baseline")
        result, stub, counters = self._run_endpoint(lambda: main.create_fifo_movement(body))
        self._assert_success_state(result, stub, "material_request")
        self.assertEqual(counters["commits"], 6)
        self.assertEqual(counters["flushes"], 7)
        self.assertEqual(counters["refreshes"], 2)

    def test_direct_move_success_payload_and_persistence_baseline(self):
        body = main.DirectMoveRequestIn(source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"], priority="alta", agv_code="AGV-7", task_typ="A01", created_by="baseline")
        result, stub, counters = self._run_endpoint(lambda: main.create_direct_move_movement(body))
        self._assert_success_state(result, stub, "direct_move")
        self.assertEqual(counters["commits"], 6)
        self.assertEqual(counters["flushes"], 7)
        self.assertEqual(counters["refreshes"], 2)

    def test_operator_window_success_delegates_without_payload_change(self):
        with self.factory() as db:
            window = OperatorWindow(name="Baseline", button_count=1, password_hash=None, is_active=1, updated_at=datetime.utcnow())
            db.add(window); db.flush()
            button = OperatorWindowButton(window_id=window.id, button_index=1, label="Move", is_active=1, action_mode="direct_move_config", priority="alta", agv_code="AGV-7", task_typ="A01", updated_at=datetime.utcnow())
            db.add(button); db.commit(); window_id = window.id
        body = main.OperatorWindowActionIn(password="", source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"], agv_code="AGV-7", task_typ="A01")
        result, stub, counters = self._run_endpoint(lambda: main.operator_execute_window_button(window_id, 1, body))
        self._assert_success_state(result, stub, "direct_move")
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, result.order_id).created_window_id, window_id)
        self.assertEqual(counters["commits"], 6)
        self.assertEqual(counters["flushes"], 7)
        self.assertEqual(counters["refreshes"], 2)

    def test_fifo_selection_is_deterministic_with_reserved_destination_and_ineligible_rack(self):
        now = datetime(2024, 1, 1)
        with self.factory() as db:
            ineligible = Rack(code="RACK-000", status="blocked", material_group_id=self.ids["material"], fifo_entered_at=now - timedelta(days=2), updated_at=now)
            second = Rack(code="RACK-002", status="available", material_group_id=self.ids["material"], fifo_entered_at=now + timedelta(days=1), updated_at=now)
            db.add_all([ineligible, second]); db.flush()
            db.add_all([
                Location(x=11, y=10, code="SRC-00", enabled=1, is_visible=1, area_id=self.ids["source_area"], rack_id=ineligible.id, updated_at=now),
                Location(x=12, y=10, code="SRC-02", enabled=1, is_visible=1, area_id=self.ids["source_area"], rack_id=second.id, updated_at=now),
                Location(x=19, y=20, code="DST-00", enabled=1, is_visible=1, area_id=self.ids["destination_area"], updated_at=now),
            ]); db.flush()
            reserved_destination = db.execute(select(Location).where(Location.code == "DST-00")).scalar_one()
            db.add(MovementOrder(order_code="RESERVED-DEST", order_type="direct_move", status="dispatched", priority="normal", rack_id=second.id, source_area_id=self.ids["source_area"], destination_area_id=self.ids["destination_area"], material_group_id=self.ids["material"], source_cell_id=self.ids["source"], destination_cell_id=reserved_destination.id, created_at=now, updated_at=now))
            db.commit()
        observed = []
        for _ in range(3):
            with self.factory() as db:
                selection = fifo_service.resolve_fifo_request(db, self.ids["source_area"], self.ids["destination_area"], self.ids["material"], "alta")
                observed.append((selection.rack.code, selection.source_cell.code, selection.destination_cell.code, selection.material_group.code))
        self.assertEqual(observed, [("RACK-001", "SRC-01", "DST-01", "MAT")] * 3)

    def test_rejected_timeout_and_transport_all_rollback_without_moving_physical_rack(self):
        for outcome in ("rejected", "timeout", "transport"):
            with self.subTest(outcome=outcome):
                # Each subcase gets a fresh instance because rollback intentionally persists terminal state.
                if outcome != "rejected":
                    self.tearDown(); self.setUp()
                body = main.DirectMoveRequestIn(source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"], priority="alta", agv_code="AGV-7", task_typ="A01")
                with self.assertRaises(HTTPException) as raised:
                    self._run_endpoint(lambda: main.create_direct_move_movement(body), outcome)
                self.assertEqual(raised.exception.status_code, 400)
                with self.factory() as db:
                    order = db.execute(select(MovementOrder).order_by(MovementOrder.id.desc())).scalars().first()
                    rack = db.get(Rack, self.ids["rack"]); source = db.get(Location, self.ids["source"]); destination = db.get(Location, self.ids["destination"])
                    self.assertEqual(order.status, "dispatch_error")
                    self.assertEqual(order.dispatch_status, "error")
                    self.assertEqual(order.rcs_status, "error")
                    self.assertEqual(rack.status, "available")
                    self.assertEqual(source.rack_id, rack.id)
                    self.assertIsNone(destination.rack_id)

    def test_remote_cancellation_baseline_keeps_reservation_until_terminal_confirmation(self):
        body = main.DirectMoveRequestIn(source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"], priority="alta", agv_code="AGV-7", task_typ="A01")
        result, _dispatch_stub, _counters = self._run_endpoint(lambda: main.create_direct_move_movement(body))
        cancel_stub = StubRcsClient()
        with self.factory() as db, patch.object(main, "_get_rcs_client_from_settings", lambda _db: cancel_stub), patch.object(main, "_resolve_rcs_target", lambda _db, mode: ("stub://rcs", "/cancelTask")):
            order = db.get(MovementOrder, result.order_id)
            cancelled = main._execute_cancel_for_order(db, order, "0", source="baseline", actor="test")
            self.assertEqual(cancelled.status, "cancel_requested_total")
            self.assertEqual(cancelled.rcs_status, "cancel_requested")
            self.assertEqual(len(cancel_stub.cancel_payloads), 1)
            self.assertEqual(cancel_stub.cancel_payloads[0]["taskCode"], "REMOTE-001")
            self.assertEqual(cancel_stub.cancel_payloads[0]["forceCancel"], "0")
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
            self.assertEqual(db.get(Location, self.ids["source"]).rack_id, self.ids["rack"])
            self.assertIsNone(db.get(Location, self.ids["destination"]).rack_id)


if __name__ == "__main__":
    unittest.main()
