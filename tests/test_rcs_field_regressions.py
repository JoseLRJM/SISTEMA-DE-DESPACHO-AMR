"""Reproductions from historical evidence; never opens the supplied database/RCS."""
import json
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import main
import fifo_service
import scanner_service
from models import Base, Location, MovementOrder, OperatorWindow, OperatorWindowButton, QrActionRule, Rack
from rcs_client import RcsHttpError, RcsTaskStatusResponse
import test_dispatch_backend_baseline as baseline
import test_phase1a_identity_resources as identity
import test_phase1f_admin_safety as admin


class FieldRegressionTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown
    create = identity.IdentityTests.create
    evidence = admin.AdminSafetyTests.evidence
    force = admin.AdminSafetyTests.force

    def window(self, mode="direct_move", configured=False):
        with self.factory() as db:
            window = OperatorWindow(name="Field test", button_count=1, is_active=1)
            db.add(window); db.flush()
            button = OperatorWindowButton(window_id=window.id, button_index=1, label="Direct", is_active=1,
                action_mode=mode, task_typ="A01", priority="normal",
                source_cell_id=self.ids["source"] if configured else None,
                destination_cell_id=self.ids["destination"] if configured else None)
            db.add(button); db.commit()
            return window.id

    def preview(self, window_id, **fields):
        with patch.object(main, "SessionLocal", self.factory):
            return main.operator_preview_window_button(window_id, 1, main.OperatorWindowActionIn(password="", **fields))

    def test_direct_preview_uses_selected_ids_without_persisted_defaults(self):
        result = self.preview(self.window(), source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"])
        self.assertTrue(result.ok)
        self.assertEqual([x["positionCode"] for x in result.payload["positionCodePath"]], ["SRC-01", "DST-01"])
        with self.factory() as db:
            self.assertEqual(db.scalars(select(MovementOrder)).all(), [])
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertEqual(fifo_service._reserved_destination_cell_ids(db), set())

    def test_direct_preview_missing_source_rejected_even_with_defaults(self):
        with self.assertRaises(HTTPException) as raised:
            self.preview(self.window(configured=True), source_cell_id=None, destination_cell_id=self.ids["destination"])
        self.assertEqual(raised.exception.status_code, 400)

    def test_direct_preview_missing_destination_rejected_even_with_defaults(self):
        with self.assertRaises(HTTPException) as raised:
            self.preview(self.window(configured=True), source_cell_id=self.ids["source"], destination_cell_id=None)
        self.assertEqual(raised.exception.status_code, 400)

    def test_direct_configured_defaults_remain_supported_if_no_selection_supplied(self):
        self.assertTrue(self.preview(self.window(configured=True)).ok)

    def test_duplicate_physical_codes_do_not_erase_distinct_cell_ids(self):
        with self.factory() as db:
            db.get(Location, self.ids["destination"]).code = "SRC-01"
            db.commit()
        result = self.preview(self.window(), source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"])
        self.assertTrue(result.ok)
        self.assertEqual([x["positionCode"] for x in result.payload["positionCodePath"]], ["SRC-01", "SRC-01"])
        self.assertNotEqual(self.ids["source"], self.ids["destination"])

    def test_configurable_preview_keeps_existing_contract(self):
        self.assertTrue(self.preview(self.window("direct_move_config"), source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"]).ok)

    def test_execute_uses_same_selected_pair_as_preview(self):
        wid = self.window()
        body = main.OperatorWindowActionIn(password="", source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"])
        with self.factory() as db:
            window = db.get(OperatorWindow, wid)
            button = db.scalars(select(OperatorWindowButton)).one()
            preview = main._preview_window_button(db, window, button, body)
            order = main._execute_window_button(db, window, button, body)
            self.assertEqual((order.source_cell_id, order.destination_cell_id), (body.source_cell_id, body.destination_cell_id))
            self.assertEqual([p["positionCode"] for p in preview.payload["positionCodePath"]], ["SRC-01", "DST-01"])

    def test_http_404_observed_in_order_3994_is_not_connection_refused(self):
        # The supplied trace has an HTTP response; it cannot prove no POST was sent.
        client = baseline.StubRcsClient()
        with patch.object(client, "create_task", side_effect=RcsHttpError(404, "<h1>404 Not Found</h1> nginx/1.23.4")):
            oid, _ = self.create(client)
        with self.factory() as db:
            order = db.get(MovementOrder, oid)
            self.assertEqual((order.status, order.dispatch_status), ("pending_dispatch", "uncertain"))
            self.assertFalse(json.loads(order.dispatch_response_json)["confirmed_not_sent"])
            self.assertEqual(db.get(Rack, order.rack_id).status, "reserved")
            self.assertTrue(order.req_code)
            self.assertIsNone(order.remote_task_code)

    def test_fresh_task_not_found_releases_immediately_preserving_identity(self):
        oid, client = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            row.status, row.dispatch_status, row.rcs_status = "pending_dispatch", "uncertain", "dispatch_unknown"
            self.evidence(row)
            db.commit()
        result = self.force()
        self.assertEqual(len(result.released_racks), 1)
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual((row.req_code, row.remote_task_code), (client.payloads[0]["reqCode"], "REMOTE-001"))
            self.assertEqual(row.status, "forced_local_closed")
            self.assertEqual(db.get(Rack, row.rack_id).status, "available")
            self.assertNotIn(row.destination_cell_id, fifo_service._reserved_destination_cell_ids(db))

    def test_failed_or_stale_query_never_bypasses_age_protection(self):
        oid, _ = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            for response in ({"error": "read timeout"}, {"code": 0, "data": []}, {"code": 404, "message": "not found"}):
                self.evidence(row)
                row.status_query_response_json = json.dumps(response)
                self.assertFalse(main._validate_force_release_order(row, now=datetime.utcnow(), min_age_minutes=20)[0])
            self.evidence(row, seconds_old=120)
            self.assertFalse(main._validate_force_release_order(row, now=datetime.utcnow(), min_age_minutes=20)[0])
            db.commit()
        with self.assertRaises(HTTPException):
            self.force()
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_real_manual_query_path_preserves_not_found_envelope_for_recovery(self):
        oid, _ = self.create()
        item = {"taskCode": "REMOTE-001", "taskStatus": "not_found"}
        response = RcsTaskStatusResponse(code=0, task_code="REMOTE-001", task_status="not_found", message="success",
            reqCode="QUERY", raw={"code":0, "reqCode":"QUERY", "data":[item]}, task_statuses=[{**item, "raw":item}])
        client = Mock()
        client.query_task_status_with_payload.return_value = response
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_with_overrides", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/query")):
            main.run_manual_status_query(oid, main.MovementOrderStatusQueryIn(payload={"reqCode":"QUERY", "taskCodes":["REMOTE-001"]}))
        self.assertEqual(len(self.force().released_racks), 1)

    def test_monitor_query_path_preserves_not_found_envelope_for_recovery(self):
        import monitor_service
        oid, _ = self.create()
        item = {"taskCode":"REMOTE-001", "taskStatus":"not_found"}
        response = RcsTaskStatusResponse(code=0, task_code="REMOTE-001", task_status="not_found", message="success",
            reqCode="QUERY", raw={"code":0, "reqCode":"QUERY", "data":[item]})
        with self.factory() as db:
            monitor_service._apply_matched_task_status(db, db.get(MovementOrder, oid), item={**item,"raw":item},
                request_payload={"reqCode":"QUERY", "taskCodes":["REMOTE-001"]}, response=response, now=datetime.utcnow(), source="test")
            db.commit()
        self.assertEqual(len(self.force().released_racks), 1)

    def test_recent_unknown_requires_wait_even_with_local_override(self):
        oid, _ = self.create()
        result = self.force(True, "Explicit override")
        self.assertEqual(result.released_racks, [])
        self.assertTrue(result.skipped)

    def test_missing_t1_and_wrong_task_are_not_absence_evidence(self):
        oid, _ = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.evidence(row, task_code="OTHER")
            self.assertFalse(main._safe_local_release_evidence(row))
            row.remote_task_code = None
            self.evidence(row)
            self.assertFalse(main._safe_local_release_evidence(row))

    def qr_create(self, value, **fields):
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "require_admin"):
            return main.admin_create_qr_action_rule(main.QrActionRuleIn(qr_value=value, **fields), "test-admin")

    def test_duplicate_qr_rejected_at_save(self):
        self.qr_create("QR_FIFO_1")
        with self.assertRaises(HTTPException) as raised:
            self.qr_create(" QR_FIFO_1 ")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("duplicado", raised.exception.detail)

    def test_different_qr_values_and_case_are_allowed(self):
        self.qr_create("QR_A")
        self.qr_create("QR_B")
        self.qr_create("qr_a")

    def test_same_value_overlapping_match_types_are_rejected(self):
        self.qr_create("QR_A")
        with self.assertRaises(HTTPException):
            self.qr_create("QR_A", match_type="prefix")

    def test_distinct_pattern_semantics_are_not_globally_forbidden(self):
        self.qr_create("^QR_A$", match_type="regex")
        self.qr_create("^QR_A$", match_type="exact")
        with self.factory() as db:
            self.assertEqual(scanner_service.resolve_qr_action_rule(db, "QR_A").match_type, "regex")
            self.assertEqual(scanner_service.resolve_qr_action_rule(db, "^QR_A$").match_type, "exact")

    def test_edit_own_qr_key_is_allowed_but_collision_is_not(self):
        first, second = self.qr_create("QR_A"), self.qr_create("QR_B")
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "require_admin"):
            result = main.admin_update_qr_action_rule(first.id, main.QrActionRuleIn(qr_value="QR_A", qr_alias="Renamed"), "test-admin")
            self.assertEqual(result.qr_alias, "Renamed")
            with self.assertRaises(HTTPException):
                main.admin_update_qr_action_rule(second.id, main.QrActionRuleIn(qr_value="QR_A"), "test-admin")

    def test_inactive_history_allowed_but_restore_collision_rejected(self):
        inactive = self.qr_create("QR_A", is_active=0)
        self.qr_create("QR_A")
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "require_admin"):
            with self.assertRaises(HTTPException):
                main.admin_restore_qr_action_rule(inactive.id, "test-admin")

    def test_legacy_ambiguity_still_readable_and_editable_without_new_key(self):
        with self.factory() as db:
            db.add_all([QrActionRule(qr_value="LEGACY", is_active=1), QrActionRule(qr_value="LEGACY", is_active=1)])
            db.commit()
            with self.assertRaises(scanner_service.ScanPreviewError):
                scanner_service.resolve_qr_action_rule(db, "LEGACY")
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "require_admin"):
            rows = main.admin_list_qr_action_rules(True, "test-admin")
            self.assertEqual(len(rows), 2)
            main.admin_update_qr_action_rule(rows[0].id, main.QrActionRuleIn(qr_value="LEGACY", description="Historic ambiguity"), "test-admin")

    def test_concurrent_duplicate_qr_save_has_one_winner(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine("sqlite:///" + str(Path(folder) / "test.sqlite"), connect_args={"timeout": 10})
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, autoflush=False)
            barrier = threading.Barrier(2)
            def save():
                with factory() as db:
                    barrier.wait()
                    try:
                        main._validate_qr_rule_unique(db, main.QrActionRuleIn(qr_value="RACE"))
                        db.add(QrActionRule(qr_value="RACE", is_active=1)); db.commit()
                        return "saved"
                    except HTTPException:
                        db.rollback(); return "duplicate"
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    self.assertCountEqual(list(pool.map(lambda _: save(), range(2))), ["saved", "duplicate"])
            finally:
                engine.dispose()

    def test_frontend_selected_ids_survive_request_building(self):
        source = (Path(main.__file__).parent / "static/app.js").read_text(encoding="utf-8")
        start = source.index("function operatorActionRequestBody()")
        end = source.index("function setOperatorButtonCell", start)
        pick = source[source.index("function setOperatorActionCell("):source.index("function syncOperatorActionStateFromInputs()")]
        script = """
const assert = require('assert');
let operatorActionState = {button: {action_mode:'direct_move'}};
let operatorActionPickMode = null;
const operatorWindowPassword = {value:''}, operatorActionAgv = {value:''}, operatorActionTaskTyp = {value:'A01'};
const operatorWindowMsg = {textContent:''}, canvas = {style:{}};
function getLocationById(id) {return {id,code:'SAME'};}
function getRackFromLocation(){return {};}
function getOperatorActionCustomFieldRows(){return [];}
function syncOperatorActionStateFromInputs(){}
function operatorActionShowsAgvTask(){return true;}
function renderOperatorActionCellLabels(){} function renderOperatorActionMaterialOptions(){}
function applyOperatorPointDefaultsFromRack(){} function draw(){}
function operatorActionIsPointToArea(){return false;}
""" + pick + source[start:end] + """
setOperatorActionCell('source', {id:2251,code:'SAME',x:50,y:22});
setOperatorActionCell('destination', {id:2252,code:'SAME',x:51,y:22});
const body = operatorActionRequestBody();
assert.equal(body.source_cell_id,2251); assert.equal(body.destination_cell_id,2252);
"""
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
