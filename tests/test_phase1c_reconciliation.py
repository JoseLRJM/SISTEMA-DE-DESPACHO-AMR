import unittest
from unittest.mock import patch

from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

import fifo_service
import main
import monitor_service as monitor
from models import DebugConsoleEvent, Location, MovementOrder, Rack
from rcs_client import RcsTimeoutError
import test_phase1a_identity_resources as identity


class ReconciliationTests(unittest.TestCase):
    setUp = identity.IdentityTests.setUp
    tearDown = identity.IdentityTests.tearDown
    create = identity.IdentityTests.create
    query_client = identity.IdentityTests.query_client
    monitor_query = identity.IdentityTests.monitor_query
    manual_query = identity.IdentityTests.manual_query

    def occupy_destination(self):
        with self.factory() as db:
            other = Rack(code="OTHER", status="available")
            db.add(other); db.flush()
            db.get(Location, self.ids["destination"]).rack_id = other.id
            db.commit()
            return other.id

    def assert_pending(self, order_id, q1):
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertNotEqual(row.status, "completed")
            self.assertTrue(monitor.local_reconciliation_pending(row))
            self.assertEqual(row.rcs_status, "completed")
            self.assertEqual(row.req_code, q1)
            self.assertEqual(row.remote_task_code, "REMOTE-001")
            self.assertIsNone(row.closed_at)
            self.assertIsNone(row.release_source)
            self.assertEqual(db.get(Rack, row.rack_id).status, "reserved")
            self.assertIn(row.destination_cell_id, fifo_service._reserved_destination_cell_ids(db))
            messages = db.scalars(select(DebugConsoleEvent)).all()
            self.assertTrue(any("local_reconciliation_required" in (item.payload_json or "") for item in messages))

    def test_01_normal_completion_moves_then_releases(self):
        order_id, stub = self.create()
        self.monitor_query(order_id, self.query_client(status="completed"))
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.rcs_status, "completed")
            self.assertIsNone(db.get(Location, row.source_cell_id).rack_id)
            self.assertEqual(db.get(Location, row.destination_cell_id).rack_id, row.rack_id)
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")
            self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])

    def test_02_occupied_destination_preserves_remote_evidence_and_reserves(self):
        order_id, stub = self.create()
        other_id = self.occupy_destination()
        self.monitor_query(order_id, self.query_client(status="completed"))
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        with self.factory() as db:
            self.assertEqual(db.get(Location, self.ids["source"]).rack_id, self.ids["rack"])
            self.assertEqual(db.get(Location, self.ids["destination"]).rack_id, other_id)

    def test_03_missing_rack_at_source_is_not_completed(self):
        order_id, stub = self.create()
        with self.factory() as db:
            db.get(Location, self.ids["source"]).rack_id = None
            db.commit()
        self.monitor_query(order_id, self.query_client(status="completed"))
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        with self.factory() as db:
            self.assertIsNone(db.get(Location, self.ids["destination"]).rack_id)

    def test_04_same_cell_with_same_rack_succeeds(self):
        with self.factory() as db:
            row, _ = fifo_service.execute_fifo_request(
                db, self.ids["source_area"], self.ids["source_area"], self.ids["material"], allow_same_cell_move=True)
            self.assertEqual(row.source_cell_id, row.destination_cell_id)
            row.req_code, row.remote_task_code = "Q-SAME", "T-SAME"
            row.dispatch_status = "success"
            db.commit()
            result = monitor.apply_remote_status_to_order(db, row, "completed")
            self.assertEqual(result.status, "completed")
            self.assertEqual(db.get(Location, row.source_cell_id).rack_id, row.rack_id)
            self.assertEqual(db.get(Location, row.source_cell_id).status, 1)
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")

    def test_05_exception_after_source_flush_rolls_back_partial_inventory(self):
        order_id, stub = self.create()
        with self.factory() as db:
            original_flush = db.flush
            raised = False
            def interrupted_flush(*args, **kwargs):
                nonlocal raised
                destination = db.get(Location, self.ids["destination"])
                if not raised and destination in db.dirty and destination.rack_id == self.ids["rack"]:
                    raised = True
                    raise RuntimeError("injected failure after source flush")
                return original_flush(*args, **kwargs)
            with patch.object(db, "flush", side_effect=interrupted_flush):
                monitor.apply_remote_status_to_order(db, db.get(MovementOrder, order_id), "completed")
            self.assertTrue(raised)
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        with self.factory() as db:
            self.assertEqual(db.get(Location, self.ids["source"]).rack_id, self.ids["rack"])
            self.assertIsNone(db.get(Location, self.ids["destination"]).rack_id)

    def test_06_sql_error_keeps_resources_and_remote_completed(self):
        order_id, stub = self.create()
        raised = False
        def fail_location_sql(connection, cursor, statement, parameters, context, executemany):
            nonlocal raised
            if not raised and statement.lstrip().upper().startswith("UPDATE LOCATIONS"):
                raised = True
                raise OperationalError(statement, parameters, RuntimeError("injected SQLite failure"))
        event.listen(self.engine, "before_cursor_execute", fail_location_sql)
        try:
            self.monitor_query(order_id, self.query_client(status="completed"))
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_location_sql)
        self.assertTrue(raised)
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])

    def test_07_repair_and_next_completed_query_finish_local_movement(self):
        order_id, stub = self.create()
        self.occupy_destination()
        self.monitor_query(order_id, self.query_client(status="completed"))
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        with self.factory() as db:
            db.get(Location, self.ids["destination"]).rack_id = None
            db.commit()
        self.monitor_query(order_id, self.query_client(status="completed"))
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "completed")
            self.assertFalse(monitor.local_reconciliation_pending(row))
            self.assertEqual(db.get(Location, row.destination_cell_id).rack_id, row.rack_id)
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")

    def test_08_already_manually_reconciled_destination_can_finish(self):
        order_id, _ = self.create()
        with self.factory() as db:
            db.get(Location, self.ids["source"]).rack_id = None
            db.flush()
            db.get(Location, self.ids["destination"]).rack_id = self.ids["rack"]
            db.commit()
        self.monitor_query(order_id, self.query_client(status="completed"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).status, "completed")

    def test_09_manual_query_preserves_reconciliation_warning(self):
        order_id, stub = self.create()
        self.occupy_destination()
        result = self.manual_query(order_id, self.query_client(status="completed"))
        self.assertNotEqual(result.status, "completed")
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        for remote_status in ("in_progress", "failed", "cancelled"):
            self.manual_query(order_id, self.query_client(status=remote_status))
            self.monitor_query(order_id, self.query_client(status=remote_status))
            self.assert_pending(order_id, stub.payloads[0]["reqCode"])
        client = self.query_client()
        client.query_task_status_with_payload.side_effect = RcsTimeoutError("query unavailable")
        self.manual_query(order_id, client)
        self.monitor_query(order_id, client)
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])

    def test_10_failed_reconciliation_does_not_run_success_hooks(self):
        order_id, _ = self.create()
        self.occupy_destination()
        with patch.object(fifo_service, "apply_transition_for_completed_order") as transitions, patch("fifo_chain_service.trigger_fifo_chain_next_step_if_needed") as chain:
            self.monitor_query(order_id, self.query_client(status="completed"))
            transitions.assert_not_called()
            chain.assert_not_called()

    def test_11_same_cell_with_other_rack_is_still_a_conflict(self):
        order_id, stub = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.destination_cell_id = row.source_cell_id
            other = Rack(code="OTHER-SAME-CELL", status="available")
            db.add(other); db.flush()
            db.get(Location, row.source_cell_id).rack_id = other.id
            db.commit()
        self.monitor_query(order_id, self.query_client(status="completed"))
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])

    def test_12_batch_hold_survives_later_transaction_rollback(self):
        order_id, stub = self.create()
        self.occupy_destination()
        with self.factory() as db:
            monitor.apply_remote_status_to_order(db, db.get(MovementOrder, order_id), "completed", auto_commit=False)
            db.rollback()
        self.assert_pending(order_id, stub.payloads[0]["reqCode"])
