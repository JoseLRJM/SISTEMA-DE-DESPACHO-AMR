"""Focused QR automatic-priority regressions; mocks and temporary DB only."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

import automatic_priority_service as automatic
import fifo_chain_service as chain
import fifo_service
import main
import monitor_service as monitor
import scanner_service as scanner
from models import Base, Location, MaterialGroup, MovementOrder, QrActionRule, QrTransitionRule, Rack, ScannerStation
import test_phase1e_fifo_chain as chain_tests


class AutomaticPriorityTests(unittest.TestCase):
    def setUp(self):
        chain_tests.ChainTests.setUp(self)
        with self.factory() as db:
            db.add(ScannerStation(scanner_code="SC", name="SC", default_action="fifo_request", allow_execute=1))
            rule = QrActionRule(qr_value="AUTO", action_type="fifo_request", automatic_priority=True,
                route_mode="fifo_chain", fifo_chain_total_steps=3, fifo_material_policy="specific_material",
                source_area_id=self.ids["source_area"], destination_area_id=self.ids["destination_area"],
                source_cell_id=self.cells[0], destination_cell_id=self.cells[1],
                second_source_area_id=self.ids["destination_area"], second_destination_area_id=self.ids["destination_area"],
                second_source_cell_id=self.cells[1], second_destination_cell_id=self.cells[2],
                fifo_chain_step3_source_area_id=self.ids["destination_area"], fifo_chain_step3_destination_area_id=self.ids["destination_area"],
                fifo_chain_step3_source_cell_id=self.cells[2], fifo_chain_step3_destination_cell_id=self.cells[3],
                fifo_chain_step1_material_group_id=self.ids["material"], fifo_chain_step2_material_group_id=self.ids["material"],
                fifo_chain_step3_material_group_id=self.ids["material"], material_group_id=self.ids["material"], task_typ="A01",
                priority=1)
            db.add(rule); db.commit(); self.rule_id = rule.id

    tearDown = chain_tests.ChainTests.tearDown

    def execute(self, qr="AUTO"):
        with self.factory() as db:
            return scanner.execute_scan(db, "SC", qr, dispatch_func=main._dispatch_movement_order,
                                        rollback_func=main._rollback_unsuccessful_order_dispatch)

    def rows(self):
        with self.factory() as db:
            return db.scalars(select(MovementOrder).order_by(MovementOrder.id)).all()

    def test_1_off_preserves_legacy_priority_and_sequential_chain(self):
        with self.factory() as db:
            db.get(QrActionRule, self.rule_id).automatic_priority = False
            db.commit()
        result = self.execute()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.client.payloads), 1)
        self.assertEqual(self.client.payloads[0]["priority"], "1")
        with self.factory() as db:
            fifo_service.simulate_order_completed(db, self.rows()[0].id)
        self.assertEqual(len(self.client.payloads), 2)

    def test_2_on_sends_three_immediately_without_advancing_inventory(self):
        result = self.execute()
        self.assertTrue(result["ok"], result)
        self.assertEqual([p["priority"] for p in self.client.payloads], ["127", "126", "125"])
        self.assertEqual([r.status for r in self.rows()], ["dispatched"]*3)
        with self.factory() as db:
            self.assertEqual(db.get(Location, self.cells[0]).rack_id, self.ids["rack"])
            self.assertTrue(all(db.get(Location, c).rack_id is None for c in self.cells[1:]))
            # Out-of-order terminal cannot pretend inventory is already at step 3.
            monitor.apply_remote_status_to_order(db, db.get(MovementOrder, self.rows()[2].id), "completed")
            self.assertIn("tramo anterior", db.get(MovementOrder, self.rows()[2].id).rcs_message)
            self.assertEqual(db.get(Location, self.cells[0]).rack_id, self.ids["rack"])
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_3_each_step_has_independent_identity_payload_and_priority(self):
        self.assertTrue(self.execute()["ok"])
        rows = self.rows()
        self.assertEqual(len({r.req_code for r in rows}), 3)
        self.assertEqual(len({r.remote_task_code for r in rows}), 3)
        self.assertEqual(len({r.fifo_chain_group_id for r in rows}), 1)
        for n, row in enumerate(rows):
            self.assertEqual((row.source_cell_id, row.destination_cell_id), (self.cells[n], self.cells[n+1]))
            self.assertEqual(json.loads(row.dispatch_request_json), self.client.payloads[n])
            with self.factory() as db:
                main._dispatch_movement_order(db, db.get(MovementOrder, row.id))
        self.assertEqual(len(self.client.payloads), 3)

    def test_4_same_rack_shared_only_within_batch_and_reconciled_per_step(self):
        self.assertTrue(self.execute()["ok"])
        rows = self.rows()
        self.assertEqual({r.rack_id for r in rows}, {self.ids["rack"]})
        with self.factory() as db:
            with self.assertRaises(HTTPException):
                fifo_service.execute_direct_move_request(db, self.cells[0], self.cells[4])
            for n, row in enumerate(rows):
                monitor.apply_remote_status_to_order(db, db.get(MovementOrder, row.id), "completed")
                self.assertEqual(db.get(Location, self.cells[n+1]).rack_id, self.ids["rack"])
                self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available" if n == 2 else "reserved")
        self.assertEqual(len(self.client.payloads), 3)

    def test_5_reject_or_uncertain_second_stops_future_sends_without_global_rollback(self):
        for outcome in ("rejected", "timeout"):
            with self.subTest(outcome=outcome):
                if outcome == "timeout":
                    self.tearDown(); self.setUp()
                original = self.client.create_task
                def fail_second(task):
                    if len(self.client.payloads) == 1:
                        self.client.outcome = outcome
                    return original(task)
                self.client.create_task = fail_second
                result = self.execute()
                self.assertFalse(result["ok"], result)
                self.assertEqual(len(self.client.payloads), 2)
                rows = self.rows()
                self.assertEqual(rows[0].remote_task_code, "REMOTE-1")
                self.assertEqual(rows[0].dispatch_status, "success")
                self.assertEqual(rows[1].status, "dispatch_error" if outcome == "rejected" else "pending_dispatch")
                self.assertEqual(rows[1].dispatch_status, "error" if outcome == "rejected" else "uncertain")
                self.assertEqual(rows[2].status, "dispatch_error")
                self.assertIsNone(rows[2].dispatch_request_json)
                with self.factory() as db:
                    self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
                    self.assertEqual(db.get(Location, self.cells[0]).rack_id, self.ids["rack"])
                    automatic.recover_interrupted_groups(db)
                    chain.recover_fifo_chains(db)
                self.assertEqual(len(self.client.payloads), 2)

    def test_6_restart_does_not_recreate_sent_or_interrupted_tasks(self):
        self.assertTrue(self.execute()["ok"])
        with self.factory() as db:
            automatic.recover_interrupted_groups(db)
            chain.recover_fifo_chains(db)
            for row in db.scalars(select(MovementOrder)):
                main._dispatch_movement_order(db, row)
        self.assertEqual(len(self.client.payloads), 3)
        # Simulate durable markers left by crash after claiming the third send.
        with self.factory() as db:
            rows = db.scalars(select(MovementOrder).order_by(MovementOrder.id)).all()
            for row in rows: row.fifo_chain_status = "batch_sending"
            rows[2].dispatch_status = "not_sent"; rows[2].remote_task_code = None
            db.commit()
            automatic.recover_interrupted_groups(db)
            self.assertEqual(rows[2].dispatch_status, "uncertain")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
        self.assertEqual(len(self.client.payloads), 3)

    def test_7_preview_shows_plan_and_has_no_operational_effect(self):
        with self.factory() as db:
            before = {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"}
            result = scanner.preview_scan(db, "SC", "AUTO")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["priority_plan"], [{"step":1,"priority":"127"},{"step":2,"priority":"126"},{"step":3,"priority":"125"}])
            self.assertEqual(db.scalars(select(MovementOrder)).all(), [])
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertEqual(before, {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"})
        self.assertEqual(self.client.payloads, [])

    def test_projected_material_transition_and_reserved_batch_reconciliation(self):
        with self.factory() as db:
            empty = MaterialGroup(code="SIN_MATERIAL", name="Sin material", is_active=1)
            db.add(empty); db.flush()
            empty_id = empty.id
            rule = db.get(QrActionRule, self.rule_id)
            rule.fifo_chain_step2_material_group_id = empty_id
            rule.fifo_chain_step3_material_group_id = empty_id
            # Resolve future origins by material/area, not by a fixed rack/cell.
            rule.second_source_cell_id = None
            rule.fifo_chain_step3_source_cell_id = None
            db.add(QrTransitionRule(name="Unload at B", is_active=1,
                apply_on="movement_completed", qr_action_rule_id=rule.id,
                source_cell_id=self.cells[0], destination_cell_id=self.cells[1],
                current_material_group_id=self.ids["material"], current_rack_status="available",
                next_material_group_id=empty_id, next_rack_status="available", clear_quantity=1))
            db.commit()
            before = {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"}
            preview = scanner.preview_scan(db, "SC", "AUTO")
            self.assertTrue(preview["ok"], preview)
            steps = preview["fifo_chain_preflight_checklist"]["steps"]
            self.assertEqual(len(steps), 3)
            self.assertTrue(all(step["ok"] for step in steps))
            projected = preview["fifo_chain_projection"]["steps"]
            self.assertEqual([step["rack_id"] for step in projected], [self.ids["rack"]] * 3)
            self.assertEqual([step["source_cell_id"] for step in projected], self.cells[:3])
            self.assertEqual([step["destination_cell_id"] for step in projected], self.cells[1:4])
            self.assertEqual(before, {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"})
            # An explicit origin area is still a constraint, not an instruction
            # to follow a rack to a different area. Exercise the reported error
            # without silently rerouting, and preserve ON in its API response.
            rule.second_source_area_id = self.ids["source_area"]
            db.commit()
            failed_before = {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"}
            failed = scanner.preview_scan(db, "SC", "AUTO")
            self.assertFalse(failed["ok"], failed)
            self.assertEqual(failed["step"], 2)
            self.assertIn("SIN_MATERIAL", failed["message"])
            serialized = main.ScanPreviewOut(**failed)
            self.assertTrue(serialized.automatic_priority)
            self.assertEqual(serialized.priority_plan, preview["priority_plan"])
            self.assertEqual(failed_before, {t.name:db.execute(select(t)).all() for t in Base.metadata.sorted_tables if t.name != "scan_events"})
            rule.second_source_area_id = self.ids["destination_area"]
            db.commit()
        self.assertTrue(self.execute()["ok"])
        rows = self.rows()
        self.assertEqual([p["priority"] for p in self.client.payloads], ["127", "126", "125"])
        self.assertEqual([r.material_group_id for r in rows], [self.ids["material"], empty_id, empty_id])
        with self.factory() as db:
            self.assertEqual(db.get(Location, self.cells[0]).rack_id, self.ids["rack"])
            self.assertTrue(all(db.get(Location, c).rack_id is None for c in self.cells[1:]))
            self.assertEqual(db.get(Rack, self.ids["rack"]).material_group_id, self.ids["material"])
            for n, row in enumerate(rows):
                with self.assertRaises(HTTPException):
                    fifo_service.execute_direct_move_request(db, self.cells[n], self.cells[4])
                monitor.apply_remote_status_to_order(db, db.get(MovementOrder, row.id), "completed")
                rack = db.get(Rack, self.ids["rack"])
                self.assertEqual(db.get(Location, self.cells[n+1]).rack_id, rack.id)
                self.assertEqual(rack.material_group_id, empty_id)
                self.assertEqual(rack.quantity, 0)
                self.assertEqual(rack.status, "available" if n == 2 else "reserved")
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(len(self.client.payloads), 3)

    def test_8_single_task_routes_use_one_priority_without_splitting(self):
        with self.factory() as db:
            rule = db.get(QrActionRule, self.rule_id)
            rule.route_mode = "simple_area"
            db.commit()
        self.assertTrue(self.execute()["ok"])
        self.assertEqual(len(self.client.payloads), 1)
        self.assertEqual(self.client.payloads[0]["priority"], "127")
        self.assertEqual(len(self.client.payloads[0]["positionCodePath"]), 2)
        with self.factory() as db:
            monitor.apply_remote_status_to_order(db, db.get(MovementOrder, self.rows()[0].id), "completed")
            rule = db.get(QrActionRule,self.rule_id)
            rule.qr_value = "DOUBLE"
            rule.route_mode = "double_area"
            rule.source_area_id = self.ids["destination_area"]
            rule.source_cell_id = self.cells[1]
            rule.destination_cell_id = self.cells[2]
            rule.second_source_cell_id = self.cells[2]
            rule.second_destination_cell_id = self.cells[3]
            db.commit()
        result = self.execute("DOUBLE")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.client.payloads), 2)
        self.assertEqual(self.client.payloads[1]["priority"], "127")
        self.assertEqual(len(self.client.payloads[1]["positionCodePath"]), 4)
