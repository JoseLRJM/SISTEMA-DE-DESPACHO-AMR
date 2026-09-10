"""Phase 2A: isolated SQLite, RCS stubs, no operational systems."""
import json
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import select

import main
import fifo_service
import monitor_service as monitor
from models import Location, MaterialGroup, MovementOrder, Rack, OperatorWindow, OperatorWindowButton
import test_dispatch_backend_baseline as baseline
import test_phase1a_identity_resources as identity
import test_phase1d_cancel as cancel
import test_phase1f_admin_safety as admin


class Phase2ATests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown
    create = identity.IdentityTests.create
    query_client = identity.IdentityTests.query_client
    monitor_query = identity.IdentityTests.monitor_query
    manual_query = identity.IdentityTests.manual_query
    assert_held = cancel.CancelTests.assert_held
    cancel = cancel.CancelTests.cancel
    old_order = admin.AdminSafetyTests.old_order
    evidence = admin.AdminSafetyTests.evidence
    force = admin.AdminSafetyTests.force

    def position(self, code="DST-01", **response):
        return {"position_code": code, "request_payload": {"reqCode": "POSITION"},
                "response_payload": {"code": 0, "reqCode": "POSITION", **response}}

    def test_terminal_does_not_reopen_on_old_monitor_or_manual_observation(self):
        oid, _ = self.create()
        self.monitor_query(oid, self.query_client(status="completed"))
        self.monitor_query(oid, self.query_client(status="in_progress"))
        self.manual_query(oid, self.query_client(status="in_progress"))
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual((row.status, row.rcs_status), ("completed", "completed"))
            self.assertEqual(db.get(Location, self.ids["destination"]).rack_id, row.rack_id)

    def test_valid_new_active_progress_and_completion_apply(self):
        oid, _ = self.create()
        self.monitor_query(oid, self.query_client(status="in_progress"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, oid).status, "in_progress")
        self.monitor_query(oid, self.query_client(status="completed"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, oid).status, "completed")

    def test_other_terminal_states_do_not_reopen(self):
        oid, _ = self.create()
        for terminal in ("cancelled", "failed", "undone", "dispatch_error"):
            with self.factory() as db:
                row = db.get(MovementOrder, oid)
                row.status = terminal
                db.commit()
                monitor.apply_remote_status_to_order(db, row, "in_progress")
                self.assertEqual(row.status, terminal)

    def test_return_unknown_position_holds_remote_evidence_and_identity(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position("UNKNOWN")):
            self.monitor_query(oid, self.query_client(status="completed"))
        self.assert_held(oid)
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual(row.rcs_status, "completed")
            self.assertTrue(monitor.local_reconciliation_pending(row))
            self.assertEqual(row.req_code, client.payloads[0]["reqCode"])
            self.assertEqual(row.remote_task_code, "REMOTE-001")
            self.assertFalse(main._safe_local_release_evidence(row))
        self.monitor_query(oid, self.query_client(status="in_progress"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, oid).rcs_status, "completed")

    def test_return_retry_reconciles_and_releases_without_creation(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with patch.object(main, "_query_pod_position_from_rcs", side_effect=TimeoutError("position timeout")):
            self.monitor_query(oid, self.query_client(status="completed"))
        self.assert_held(oid)
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position()):
            self.monitor_query(oid, self.query_client(status="completed"))
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual(row.status, "completed")
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")
            self.assertIsNone(db.get(Location, row.source_cell_id).rack_id)
            self.assertEqual(db.get(Location, row.destination_cell_id).rack_id, row.rack_id)
        self.assertEqual(len(client.payloads), 1)
        self.assertEqual(len(client.cancel_payloads), 1)

    def test_return_cancelled_also_requires_final_position(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position("")):
            self.monitor_query(oid, self.query_client(status="cancelled"))
        self.assert_held(oid)
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position("SRC-01")):
            self.monitor_query(oid, self.query_client(status="cancelled"))
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual(row.status, "cancelled")
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")

    def test_return_occupied_position_does_not_displace_other_rack(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with self.factory() as db:
            other = Rack(code="OTHER", status="available")
            db.add(other); db.flush()
            other_id = other.id
            db.get(Location, self.ids["destination"]).rack_id = other_id
            db.commit()
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position()):
            self.monitor_query(oid, self.query_client(status="completed"))
        with self.factory() as db:
            self.assertEqual(db.get(Location, self.ids["destination"]).rack_id, other_id)
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_return_bad_position_response_is_not_evidence(self):
        oid, client = self.create()
        self.cancel(oid, client)
        for response in ({"code": 5}, {"reqCode": "OTHER"}, {"podCode": "OTHER"}, {"data": [{"podCode": "OTHER"}]}):
            with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position(**response)):
                self.monitor_query(oid, self.query_client(status="completed"))
            self.assert_held(oid)

    def test_return_sql_failure_rolls_back_location_and_keeps_hold(self):
        oid, client = self.create()
        self.cancel(oid, client)
        def fail_after_mutation(db, rack, position, **kwargs):
            db.get(Location, self.ids["source"]).rack_id = None
            db.flush()
            raise RuntimeError("SQL reconciliation failure")
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position()), patch.object(main, "_apply_rcs_rack_position", side_effect=fail_after_mutation):
            self.monitor_query(oid, self.query_client(status="completed"))
        self.assert_held(oid)
        with self.factory() as db:
            self.assertTrue(monitor.local_reconciliation_pending(db.get(MovementOrder, oid)))

    def test_old_position_poll_cannot_undo_final_reconciliation(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with patch.object(main, "_query_pod_position_from_rcs", return_value=self.position()):
            self.monitor_query(oid, self.query_client(status="completed"))
        with self.factory() as db:
            applied, _, _ = main._apply_rcs_rack_position(db, db.get(Rack, self.ids["rack"]), "SRC-01", source="operator_window", related_order_id=oid)
            self.assertFalse(applied)
            self.assertEqual(db.get(Location, self.ids["destination"]).rack_id, self.ids["rack"])

    def test_return_not_found_cannot_bypass_position_reconciliation(self):
        oid, client = self.create()
        self.cancel(oid, client)
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.evidence(row, "not_found")
            self.assertFalse(main._safe_local_release_evidence(row))
            self.assertFalse(main._cancel_undo_robot_recovery_reason(row, now=datetime.utcnow(), min_age_minutes=0)[0])

    def test_stale_orm_order_cannot_reopen_completed(self):
        oid, _ = self.create()
        with self.factory() as stale:
            row = stale.get(MovementOrder, oid)
            self.monitor_query(oid, self.query_client(status="completed"))
            monitor.apply_remote_status_to_order(stale, row, "in_progress")
            self.assertEqual(row.status, "completed")

    def pending(self):
        with self.factory() as db:
            return fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"]).id

    def dispatch_manual(self, oid, outcome):
        client = baseline.StubRcsClient(outcome)
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")):
            return main.dispatch_movement_order(oid), client

    def test_manual_rejection_compensates(self):
        oid = self.pending()
        self.dispatch_manual(oid, "rejected")
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, oid).status, "dispatch_error")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertNotIn(self.ids["destination"], fifo_service._reserved_destination_cell_ids(db))

    def test_manual_uncertain_keeps_reservations_and_no_resend(self):
        oid = self.pending()
        result, client = self.dispatch_manual(oid, "timeout")
        self.assertEqual(result.dispatch_status, "uncertain")
        result, second = self.dispatch_manual(oid, "success")
        self.assertEqual(second.payloads, [])
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
            self.assertIn(self.ids["destination"], fifo_service._reserved_destination_cell_ids(db))

    def test_force_single_confirmed_failure_releases(self):
        oid, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            row.rcs_status = "failed"
            self.evidence(row, "failed")
            db.commit()
        result = self.force()
        self.assertEqual(len(result.closed_orders), 1)
        self.assertEqual(len(result.released_racks), 1)
        self.assertEqual(result.skipped, [])

    def window(self):
        with self.factory() as db:
            window = OperatorWindow(name="Point", is_active=1)
            db.add(window); db.flush()
            button = OperatorWindowButton(window_id=window.id, button_index=1, label="Point", action_mode="point_to_area", destination_area_id=self.ids["destination_area"], is_active=1)
            db.add(button); db.commit()
            return window.id

    def point(self, wid, outcome="success", **values):
        body = main.OperatorWindowActionIn(password="", source_cell_id=self.ids["source"], **values)
        client = baseline.StubRcsClient(outcome)
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_validate_window_password", return_value=True), patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")):
            return main.operator_execute_window_button(wid, 1, body)

    def test_point_validation_failure_preserves_original_inventory(self):
        wid = self.window()
        with patch.object(main, "execute_direct_move_request", side_effect=HTTPException(409, "conflict")):
            with self.assertRaises(HTTPException):
                self.point(wid, lot="NEW", quantity=17)
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            self.assertEqual((rack.lot, rack.quantity), (None, 1))

    def test_point_rejection_preserves_original_inventory(self):
        wid = self.window()
        with self.assertRaises(HTTPException):
            self.point(wid, "rejected", lot="NEW", quantity=17, custom_field_values=[{"key": "comment", "value": "NEW"}])
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            self.assertEqual((rack.lot, rack.quantity, rack.comment), (None, 1, None))
            self.assertEqual(rack.status, "available")

    def test_point_uncertainty_preserves_inventory_and_holds_resource(self):
        wid = self.window()
        with self.assertRaises(HTTPException):
            self.point(wid, "timeout", lot="NEW", quantity=17)
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            self.assertEqual((rack.lot, rack.quantity, rack.status), (None, 1, "reserved"))

    def test_point_success_applies_inventory_with_dispatch(self):
        wid = self.window()
        with self.factory() as db:
            material = MaterialGroup(code="NEW-MAT", name="New", is_active=1)
            db.add(material); db.commit()
            material_id = material.id
        result = self.point(wid, lot="NEW", quantity=17, manufacturer_code="MFG", material_group_id=material_id)
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            self.assertEqual((rack.lot, rack.quantity, rack.manufacturer_code), ("NEW", 17, "MFG"))
            self.assertEqual(rack.material_group_id, material_id)
            self.assertEqual(db.get(MovementOrder, result.order_id).material_group_id, material_id)
            self.assertEqual(db.get(MovementOrder, result.order_id).dispatch_status, "success")

    def test_point_omitted_fields_are_not_cleared(self):
        wid = self.window()
        with self.factory() as db:
            db.get(Rack, self.ids["rack"]).lot = "ORIGINAL"
            db.commit()
        self.point(wid, quantity=3)
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).lot, "ORIGINAL")

    def test_point_missing_material_is_assigned_only_after_success(self):
        wid = self.window()
        with self.factory() as db:
            db.get(Rack, self.ids["rack"]).material_group_id = None
            db.commit()
        with self.assertRaises(HTTPException):
            self.point(wid, "rejected", quantity=2)
        with self.factory() as db:
            self.assertIsNone(db.get(Rack, self.ids["rack"]).material_group_id)
        result = self.point(wid, quantity=2)
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            self.assertIsNotNone(rack.material_group_id)
            self.assertEqual(rack.material_group_id, db.get(MovementOrder, result.order_id).material_group_id)

    def test_payload_wrong_route_robot_or_q1_rejected(self):
        oid = self.pending()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            row.req_code = "Q1"; db.commit()
            valid = main._movement_order_json_payload(db, row)
            for key, value in (("positionCodePath", [{"positionCode": "OTHER", "type": "00"}]), ("agvCode", "OTHER"), ("reqCode", "Q2")):
                with self.assertRaises(HTTPException):
                    main._save_movement_order_payload_override(db, row, {**valid, key: value})
            self.assertIsNone(row.override_payload_json)

    def test_historical_incompatible_override_rejected_before_rcs(self):
        oid = self.pending()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            payload = main._movement_order_json_payload(db, row)
            payload["positionCodePath"] = []
            row.override_payload_json = json.dumps(payload); db.commit()
        with self.assertRaises(HTTPException):
            self.dispatch_manual(oid, "success")
        with self.factory() as db:
            self.assertIsNone(db.get(MovementOrder, oid).dispatch_request_json)

    def test_valid_edited_payload_is_allowed(self):
        oid = self.pending()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            payload = main._movement_order_json_payload(db, row)
            payload["priority"] = "2"
            main._save_movement_order_payload_override(db, row, payload)
        result, client = self.dispatch_manual(oid, "success")
        self.assertEqual(result.dispatch_status, "success")
        self.assertEqual(client.payloads[0]["priority"], "2")
        self.assertEqual(client.payloads[0]["reqCode"], payload["reqCode"])

    def test_debug_without_admin_is_rejected_before_client(self):
        with patch.object(main, "_get_rcs_client_with_overrides") as client:
            with self.assertRaises(HTTPException) as raised:
                main.send_debug_console_json(main.DebugConsoleSendIn(payload={}), None)
            self.assertEqual(raised.exception.status_code, 401)
            client.assert_not_called()

    def test_debug_authorized_preserves_tool_and_audits(self):
        client = Mock()
        client.post_json_payload.return_value = {"code": 0, "message": "success"}
        with patch.dict(main.ADMIN_TOKENS, {"test-admin": datetime.utcnow()+timedelta(hours=1)}), patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_with_overrides", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/query")):
            result = main.send_debug_console_json(main.DebugConsoleSendIn(payload={"test": 1}), "test-admin")
        self.assertTrue(result["ok"])
        client.post_json_payload.assert_called_once()
        from models import DebugConsoleEvent
        with self.factory() as db:
            messages = db.execute(select(DebugConsoleEvent.message)).scalars().all()
            self.assertTrue(any("autorizado por administrador" in message for message in messages))
