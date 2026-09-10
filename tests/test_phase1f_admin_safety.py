"""Extraordinary operations retain remote evidence; no real RCS or production DB."""
import json
import subprocess
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from fastapi import HTTPException

import fifo_chain_service as chain
import fifo_service
import main
import monitor_service as monitor
from models import Location, MovementOrder, Rack
import test_dispatch_backend_baseline as baseline
import test_phase1a_identity_resources as identity


class AdminSafetyTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown
    create = identity.IdentityTests.create

    def old_order(self):
        order_id, client = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.created_at = row.dispatched_at = datetime.utcnow()-timedelta(hours=2)
            row.rcs_status = "in_progress"
            db.commit()
        return order_id, client

    def evidence(self, row, status="not_found", *, seconds_old=0, code=0, task_code=None):
        checked = datetime.utcnow()-timedelta(seconds=seconds_old)
        row.status_query_checked_at = row.rcs_last_update = checked
        row.status_query_request_json = json.dumps({"reqCode": "QUERY", "taskCodes": [row.remote_task_code]})
        row.status_query_response_json = json.dumps({"code": code, "reqCode": "QUERY", "data": [{"taskCode": task_code or row.remote_task_code, "taskStatus": status}]})

    def force(self, confirm=False, reason=""):
        with patch.object(main, "require_admin"), patch.object(main, "SessionLocal", self.factory), patch.object(main, "_run_cleanup_diagnosis", return_value={}):
            return main.force_release_old_active_racks(main.CleanupSelectionIn(rack_ids=[self.ids["rack"]], confirm_local_only=confirm, reason=reason), "test-admin")

    def test_force_active_requires_override_and_reason(self):
        order_id, _ = self.old_order()
        for confirmed, reason in ((False, ""), (True, "")):
            with self.assertRaises(HTTPException) as raised:
                self.force(confirmed, reason)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn("no detiene", str(raised.exception.detail))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).status, "dispatched")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_unknown_remote_also_requires_override(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.rcs_status = "dispatch_unknown"
            db.commit()
        with self.assertRaises(HTTPException):
            self.force()

    def test_explicit_override_preserves_q1_t1_remote_and_audit(self):
        order_id, client = self.old_order()
        q1, t1 = client.payloads[0]["reqCode"], "REMOTE-001"
        result = self.force(True, "Robot aislado físicamente; override autorizado")
        self.assertEqual(len(result.released_racks), 1)
        self.assertTrue(result.closed_orders[0]["local_only"])
        self.assertIn("no detiene", result.closed_orders[0]["warning"])
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual((row.req_code, row.remote_task_code), (q1, t1))
            self.assertEqual((row.status, row.rcs_status), ("forced_local_closed", "in_progress"))
            self.assertTrue(row.forced_local_close)
            self.assertIsNotNone(row.forced_local_close_at)
            self.assertEqual(json.loads(row.dispatch_request_json)["reqCode"], q1)
            logs = json.loads(row.status_query_log_json)
            self.assertTrue(any(x["kind"] == "admin_force_local_close" and x["request"]["confirm_local_only"] for x in logs))
            self.assertEqual(db.get(Location, self.ids["source"]).rack_id, self.ids["rack"])
            monitor.apply_remote_status_to_order(db, row, "completed")
            self.assertEqual(row.status, "forced_local_closed")

    def test_fresh_confirmed_not_found_allows_local_recovery(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            self.evidence(db.get(MovementOrder, order_id))
            db.commit()
        result = self.force()
        self.assertEqual(len(result.released_racks), 1)
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "forced_local_closed")
            self.assertEqual(row.remote_task_code, "REMOTE-001")
            self.assertEqual(row.rcs_status, "in_progress")

    def test_stale_not_found_cannot_release(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            self.evidence(db.get(MovementOrder, order_id), seconds_old=120)
            db.commit()
        with self.assertRaises(HTTPException):
            self.force()

    def test_timeout_and_empty_data_are_not_absence(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.status = "cancel_requested_undo"
            row.closed_at = datetime.utcnow()-timedelta(hours=1)
            for payload in ({"error": "read timeout"}, {"code": 0, "data": []}, {"code": 404, "message": "not found"}):
                self.evidence(row)
                row.status_query_response_json = json.dumps(payload)
                self.assertFalse(main._status_query_missing_task(row))
                with patch.object(main, "_get_cached_robot_status_monitor_response", side_effect=TimeoutError):
                    safe, _ = main._cancel_undo_robot_recovery_reason(row, now=datetime.utcnow(), min_age_minutes=5)
                self.assertFalse(safe)
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_stale_idle_robot_cache_is_not_evidence(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.agv_code = "AGV"
            row.status_query_checked_at = datetime.utcnow()-timedelta(hours=2)
            robot = Mock(robotCode="AGV", agvCode="AGV", taskStatus="", statusText="idle", status="4", podCode="", currentStation="")
            with patch.object(main, "_get_cached_robot_status_monitor_response", return_value=Mock(robots=[robot])), patch.object(main, "_robot_monitor_indicates_order_running", return_value=False):
                self.assertFalse(main._cancel_undo_robot_recovery_reason(row, now=datetime.utcnow(), min_age_minutes=5)[0])

    def test_not_found_requires_matching_task_and_success(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            for kwargs in ({"task_code": "OTHER"}, {"code": 1}):
                self.evidence(row, **kwargs)
                self.assertFalse(main._status_query_missing_task(row))
            self.evidence(row)
            self.assertTrue(main._status_query_missing_task(row))

    def test_reconciliation_failure_is_not_safe_release(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.status = "in_progress"
            row.rcs_status = "completed"
            row.rcs_message = monitor.LOCAL_RECONCILIATION_PREFIX + " destination occupied"
            self.evidence(row, "completed")
            self.assertFalse(main._safe_local_release_evidence(row))

    def simulation_setting(self):
        with self.factory() as db:
            main.set_setting(db, "rcs_simulator_enabled", "1", source="test")
            db.commit()

    def simulate(self, order_id, body=None):
        with patch.object(main, "require_admin"), patch.object(main, "SessionLocal", self.factory):
            return main.simulate_complete_movement_order(order_id, body, "test-admin")

    def test_simulation_requires_admin(self):
        order_id, _ = self.create()
        with self.assertRaises(HTTPException) as raised:
            main.simulate_complete_movement_order(order_id, None, None)
        self.assertEqual(raised.exception.status_code, 401)

    def test_simulation_requires_confirmation(self):
        order_id, _ = self.create()
        with self.assertRaises(HTTPException):
            self.simulate(order_id)

    def test_production_simulate_denied_and_chain_not_triggered(self):
        order_id, _ = self.create()
        body = main.SimulateCompleteIn(confirm_simulation=True, reason="test")
        with patch.object(chain, "trigger_fifo_chain_next_step_if_needed") as hook:
            with self.assertRaises(HTTPException):
                self.simulate(order_id, body)
            hook.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).status, "dispatched")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_enabling_simulator_does_not_convert_existing_real_task(self):
        order_id, _ = self.create()
        self.simulation_setting()
        with self.assertRaises(HTTPException):
            self.simulate(order_id, main.SimulateCompleteIn(confirm_simulation=True, reason="test"))

    def test_explicit_simulated_task_allowed_without_chain_or_remote_fabrication(self):
        self.simulation_setting()
        order_id, _ = self.create(main.SimulatedRcsClient())
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.route_mode = "fifo_chain"
            row.fifo_chain_group_id = "SIM-CHAIN"
            row.fifo_chain_step, row.fifo_chain_total_steps = 1, 3
            row.fifo_chain_status = "active"
            remote_before, q1, t1 = row.rcs_status, row.req_code, row.remote_task_code
            db.commit()
        with patch.object(chain, "trigger_fifo_chain_next_step_if_needed") as hook:
            result = self.simulate(order_id, main.SimulateCompleteIn(confirm_simulation=True, reason="Entorno de pruebas aislado"))
            hook.assert_not_called()
        self.assertEqual(result.status, "simulated_completed")
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual((row.rcs_status, row.req_code, row.remote_task_code), (remote_before, q1, t1))
            self.assertEqual(row.forced_local_close_reason, "admin_simulate_complete")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertEqual(chain.recover_fifo_chains(db), [])

    def test_admin_cannot_delete_remote_order(self):
        order_id, _ = self.create()
        with patch.object(main, "require_admin"), patch.object(main, "SessionLocal", self.factory), self.assertRaises(HTTPException):
            main.admin_delete_movement_order(order_id, "test-admin")
        with self.factory() as db:
            self.assertIsNotNone(db.get(MovementOrder, order_id))

    def test_admin_cannot_edit_active_rack(self):
        self.create()
        with patch.object(main, "require_admin"), patch.object(main, "SessionLocal", self.factory), self.assertRaises(HTTPException):
            main.update_rack(self.ids["rack"], main.RackIn(code="CHANGED", status="available", material_group_id=self.ids["material"]), "test-admin")

    def test_admin_cannot_edit_active_destination(self):
        self.create()
        with patch.object(main, "require_admin"), patch.object(main, "SessionLocal", self.factory), self.assertRaises(HTTPException):
            main.admin_patch_location(20, 20, main.LocationPatchAdmin(code="CHANGED"), "test-admin")

    def test_manual_rack_release_with_timeout_is_blocked(self):
        order_id, _ = self.old_order()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.status = "cancel_requested_undo"
            self.evidence(row)
            row.status_query_response_json = json.dumps({"error": "timeout"})
            db.commit()
            with self.assertRaises(HTTPException):
                main._manual_release_rack_or_raise(db, db.get(Rack, self.ids["rack"]), actor="test", reason="manual")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_manual_position_query_cannot_move_active_rack(self):
        self.create()
        with self.factory() as db:
            with self.assertRaises(HTTPException):
                main._apply_rcs_rack_position(db, db.get(Rack, self.ids["rack"]), "DST-01", source="manual_pod_position_query")
            self.assertEqual(db.get(Location, self.ids["source"]).rack_id, self.ids["rack"])
            self.assertIsNone(db.get(Location, self.ids["destination"]).rack_id)

    def test_local_close_between_dispatch_read_and_claim_prevents_send(self):
        client = baseline.StubRcsClient()
        with self.factory() as db:
            row = fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"])
            def concurrent_close(_db):
                row.status = "forced_local_closed"
                row.forced_local_close = 1
                db.commit()
                return client
            with patch.object(main, "_get_rcs_client_from_settings", side_effect=concurrent_close), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")), self.assertRaises(HTTPException):
                main._dispatch_movement_order(db, row)
            self.assertEqual(client.payloads, [])
            self.assertIsNone(row.dispatch_request_json)


class SimulationUiTests(unittest.TestCase):
    def test_simulation_ui_requires_admin_confirmation_and_reason(self):
        source = (Path(__file__).resolve().parents[1]/"static"/"app.js").read_text(encoding="utf-8")
        function = source[source.index("async function simulateSelectedOrderComplete() {"):source.index("async function undoSelectedOrder(")]
        script = """
const assert = require('assert');
let adminToken = '', selectedOrderId = 1;
const movementOrders = [{order_id:1, can_simulate_complete:true}];
let confirm = false, reason = '', sent = [];
const window = {confirm:()=>confirm, prompt:()=>reason};
const API = {movementOrderSimulateComplete:id=>'/simulate/'+id};
const orderMsg = {};
function fetchHeaders(){return {'X-Admin-Token': adminToken};}
async function fetchJson(url, options){sent.push(options);return {order_id:1,order_code:'SIM',destination_cell:{x:1,y:1}};}
async function loadLocations(){} async function loadCatalog(){}
async function loadMovementOrders(){} async function selectCell(){} function draw(){}
""" + function + """
(async()=>{
  await assert.rejects(simulateSelectedOrderComplete(), /Admin/);
  adminToken = 'admin-test';
  await simulateSelectedOrderComplete(); assert.equal(sent.length,0);
  confirm = true; await simulateSelectedOrderComplete(); assert.equal(sent.length,0);
  reason = ' prueba aislada '; await simulateSelectedOrderComplete();
  assert.equal(sent.length,1);
  assert.equal(sent[0].headers['X-Admin-Token'],'admin-test');
  assert.deepEqual(JSON.parse(sent[0].body),{confirm_simulation:true,reason:'prueba aislada'});
  assert.match(orderMsg.textContent,/no es una confirmación RCS/);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
