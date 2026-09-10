"""Cancel/return: temporary SQLite and stubs or loopback, never real RCS."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import fifo_service
import main
import monitor_service as monitor
from models import Base, Location, MovementOrder, OperatorWindow, OperatorWindowButton, Rack
from rcs_client import RcsClient, RcsError, RcsHttpError, RcsParseError, RcsSimpleResponse, RcsTimeoutError
import test_dispatch_backend_baseline as baseline
import test_phase1a_identity_resources as identity


class CancelTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown
    create = identity.IdentityTests.create
    query_client = identity.IdentityTests.query_client
    monitor_query = identity.IdentityTests.monitor_query

    def cancel(self, order_id, client, force="1", area="RETURN-AREA"):
        with self.factory() as db, patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/cancel")), patch.object(main, "_start_rack_position_poll_after_cancel"):
            row = main._execute_cancel_for_order(db, db.get(MovementOrder, order_id), force, area)
            return row.status

    def assert_held(self, order_id, force="1"):
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "cancel_requested_undo" if force == "1" else "cancel_requested_total")
            self.assertEqual(db.get(Rack, row.rack_id).status, "reserved")
            self.assertIn(row.destination_cell_id, fifo_service._reserved_destination_cell_ids(db))
            self.assertEqual(db.get(Location, row.source_cell_id).rack_id, row.rack_id)
            self.assertIsNone(db.get(Location, row.destination_cell_id).rack_id)
            self.assertFalse(row.forced_local_close)

    def test_normal_cancel_one_cancel_zero_create_and_q1_t1_preserved(self):
        order_id, client = self.create()
        q1 = client.payloads[0]["reqCode"]
        client.payloads.clear()
        with patch.object(main, "_dispatch_movement_order", side_effect=AssertionError("must not dispatch")), patch.object(main, "undo_movement_order", side_effect=AssertionError("must not undo")):
            self.cancel(order_id, client, "0")
            self.cancel(order_id, client, "0")
        self.assertEqual(len(client.cancel_payloads), 1)
        self.assertEqual(client.payloads, [])
        payload = client.cancel_payloads[0]
        self.assertEqual((payload["forceCancel"], payload["matterArea"], payload["taskCode"]), ("0", "", "REMOTE-001"))
        self.assertNotEqual(payload["reqCode"], q1)
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual((row.req_code, row.remote_task_code), (q1, "REMOTE-001"))
            self.assertEqual(json.loads(row.dispatch_request_json)["reqCode"], q1)
            logs = json.loads(row.status_query_log_json)
            self.assertTrue(any(x["kind"] == "cancel_task_requested" and x["request"]["reqCode"] == payload["reqCode"] for x in logs))
        self.assert_held(order_id, "0")

    def window(self, order_id):
        with self.factory() as db:
            window = OperatorWindow(name="Cancel tests", button_count=1, is_active=1)
            db.add(window); db.flush()
            db.add(OperatorWindowButton(window_id=window.id, button_index=1, label="Return", is_active=1, action_mode="cancel_return", cancel_matter_area="RETURN-AREA"))
            db.get(MovementOrder, order_id).created_window_id = window.id
            db.commit()
            return window.id

    def test_operator_return_repeated_never_dispatches_or_locally_undoes(self):
        order_id, client = self.create()
        q1 = client.payloads[0]["reqCode"]
        client.payloads.clear()
        window_id = self.window(order_id)
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/cancel")), patch.object(main, "_start_rack_position_poll_after_cancel"), patch.object(main, "_dispatch_movement_order", side_effect=AssertionError("must not dispatch")), patch.object(main, "undo_movement_order", side_effect=AssertionError("must not undo")):
            for _ in range(3):
                result = main.operator_execute_window_button(window_id, 1, main.OperatorWindowActionIn(password=""))
                self.assertEqual(result.order_id, order_id)
        self.assertEqual(client.payloads, [])
        self.assertEqual(len(client.cancel_payloads), 1)
        payload = client.cancel_payloads[0]
        self.assertEqual((payload["forceCancel"], payload["matterArea"], payload["taskCode"]), ("1", "RETURN-AREA", "REMOTE-001"))
        self.assertNotIn("taskTyp", payload)
        self.assertNotIn("positionCodePath", payload)
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual((row.req_code, row.remote_task_code), (q1, "REMOTE-001"))
        self.assert_held(order_id)

    def failure(self, force, error=None):
        order_id, client = self.create()
        client.payloads.clear()
        def failed(payload):
            client.cancel_payloads.append(dict(payload))
            if error:
                raise error
            return RcsSimpleResponse(code=1, message="rejected", reqCode=payload["reqCode"], raw={"code": 1})
        client.cancel_task_with_payload = failed
        with self.assertRaises(HTTPException) as raised:
            self.cancel(order_id, client, force)
        self.assertEqual(raised.exception.status_code, 502 if error else 400)
        self.assert_held(order_id, force)
        # A new session (also after a restart) does not resend an uncertain operation.
        self.cancel(order_id, client, force)
        self.assertEqual(len(client.cancel_payloads), 1)
        self.assertEqual(client.payloads, [])
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).rcs_status, "cancel_unknown" if error else "cancel_error")

    def test_normal_timeout_retains_resources(self):
        self.failure("0", RcsTimeoutError("read timeout"))

    def test_return_timeout_retains_resources(self):
        self.failure("1", RcsTimeoutError("read timeout"))

    def test_ambiguous_connection_cut_retains_resources(self):
        self.failure("1", RcsError("connection cut after send"))

    def test_normal_rejection_retains_resources(self):
        self.failure("0")

    def test_return_rejection_retains_resources(self):
        self.failure("1")

    def test_missing_return_area_does_not_send(self):
        order_id, client = self.create()
        with self.assertRaises(HTTPException):
            self.cancel(order_id, client, area=" ")
        self.assertEqual(client.cancel_payloads, [])

    def test_terminal_task_never_undoes_locally(self):
        order_id, client = self.create()
        with self.factory() as db:
            fifo_service.simulate_order_completed(db, order_id)
        with patch.object(main, "undo_movement_order", side_effect=AssertionError("unsafe undo")), self.assertRaises(HTTPException):
            self.cancel(order_id, client)
        self.assertEqual(client.cancel_payloads, [])

    def test_position_poll_timeout_does_not_close_or_release(self):
        order_id, client = self.create()
        self.cancel(order_id, client)
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "RACK_POSITION_POLL_TIMEOUT_SECONDS", -1):
            main._rack_position_poll_worker(order_id, self.ids["rack"], source="test")
        self.assert_held(order_id)

    def test_known_rack_position_is_not_cancel_confirmation(self):
        order_id, client = self.create()
        self.cancel(order_id, client)
        with self.factory() as db:
            rack = db.get(Rack, self.ids["rack"])
            applied, _, _ = main._apply_rcs_rack_position(db, rack, "SRC-01", source="test", related_order_id=order_id)
            self.assertTrue(applied)
            db.commit()
        self.assert_held(order_id)

    def test_terminal_query_during_cancel_response_is_not_regressed(self):
        order_id, client = self.create()
        original_cancel = client.cancel_task_with_payload
        def terminal_first(payload):
            with self.factory() as db:
                monitor.apply_remote_status_to_order(db, db.get(MovementOrder, order_id), "cancelled")
            return original_cancel(payload)
        client.cancel_task_with_payload = terminal_first
        self.assertEqual(self.cancel(order_id, client, "0"), "cancelled")
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")

    def test_idle_auto_recovery_is_not_cancel_confirmation(self):
        order_id, client = self.create()
        self.cancel(order_id, client)
        with self.factory() as db, patch.object(main, "_cancel_undo_auto_recovery_enabled", return_value=True), patch.object(main, "_cancel_undo_auto_recovery_min_age_minutes", return_value=0), patch.object(main, "_cancel_undo_recovery_candidate", return_value=(True, "SAFE_IDLE")), patch.object(main, "_force_close_old_active_order", side_effect=AssertionError("not confirmed")):
            self.assertEqual(main._auto_release_stuck_cancel_return_orders(db, now=datetime.utcnow()+timedelta(hours=1)), 0)
        self.assert_held(order_id)

    def test_return_remote_failure_holds_resources(self):
        order_id, client = self.create()
        self.cancel(order_id, client)
        self.monitor_query(order_id, self.query_client(status="failed"))
        self.assert_held(order_id)

    def test_return_running_is_pending_then_terminal_confirms(self):
        order_id, client = self.create()
        self.cancel(order_id, client)
        self.monitor_query(order_id, self.query_client(status="in_progress"))
        self.assert_held(order_id)
        with patch.object(main, "_query_pod_position_from_rcs", return_value={
            "request_payload": {"reqCode": "POS"}, "response_payload": {"code": 0, "reqCode": "POS"},
            "position_code": "DST-01",
        }):
            self.monitor_query(order_id, self.query_client(status="completed"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).status, "completed")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")

    def test_normal_terminal_confirms_release(self):
        order_id, client = self.create()
        self.cancel(order_id, client, "0")
        self.monitor_query(order_id, self.query_client(status="cancelled"))
        with self.factory() as db:
            self.assertEqual(db.get(MovementOrder, order_id).status, "cancelled")
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")

    def test_simultaneous_requests_share_one_durable_claim(self):
        # Separate file-backed connections: not a frontend or in-memory lock.
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine("sqlite:///" + str(Path(folder)/"cancel-test.sqlite"), connect_args={"check_same_thread": False, "timeout": 10})
            with self.engine.connect() as src, engine.connect() as dst:
                src.connection.driver_connection.backup(dst.connection.driver_connection)
            original_factory = self.factory
            self.factory = sessionmaker(bind=engine, autoflush=False)
            entered, release = threading.Event(), threading.Event()
            try:
                order_id, client = self.create()
                original_cancel = client.cancel_task_with_payload
                def blocking(payload):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test synchronization timeout")
                    return original_cancel(payload)
                client.cancel_task_with_payload = blocking
                def invoke():
                    with self.factory() as db:
                        return main._execute_cancel_for_order(db, db.get(MovementOrder, order_id), "1", "RETURN-AREA").status
                with patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/cancel")), patch.object(main, "_start_rack_position_poll_after_cancel"), ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(invoke)
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertEqual(pool.submit(invoke).result(timeout=5), "cancel_requested_undo")
                    finally:
                        release.set()
                    first.result(timeout=5)
                self.assertEqual(len(client.cancel_payloads), 1)
                self.assert_held(order_id)
            finally:
                self.factory = original_factory
                engine.dispose()

    def test_two_requests_read_active_before_atomic_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine("sqlite:///" + str(Path(folder)/"claim-test.sqlite"), connect_args={"check_same_thread": False, "timeout": 10})
            with self.engine.connect() as src, engine.connect() as dst:
                src.connection.driver_connection.backup(dst.connection.driver_connection)
            original_factory = self.factory
            self.factory = sessionmaker(bind=engine, autoflush=False)
            try:
                order_id, client = self.create()
                barrier = threading.Barrier(2)
                original_refresh = Session.refresh
                def synchronized_refresh(db, row, *args, **kwargs):
                    original_refresh(db, row, *args, **kwargs)
                    if db.info.pop("synchronize_first_read", False):
                        self.assertEqual(row.status, "dispatched")
                        barrier.wait(timeout=5)
                def invoke():
                    with self.factory() as db:
                        db.info["synchronize_first_read"] = True
                        return main._execute_cancel_for_order(db, db.get(MovementOrder, order_id), "0").status
                with patch.object(Session, "refresh", synchronized_refresh), patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/cancel")), ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(invoke) for _ in range(2)]
                    for future in futures:
                        self.assertEqual(future.result(timeout=10), "cancel_requested_total")
                self.assertEqual(len(client.cancel_payloads), 1)
                self.assert_held(order_id, "0")
            finally:
                self.factory = original_factory
                engine.dispose()


class CancelTransportTests(unittest.TestCase):
    def run_response(self, status, body):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                response = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Location", "/redirect")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        client = RcsClient(f"http://127.0.0.1:{server.server_port}", "/create", max_retries=2, backoff_factor=0)
        client.session.trust_env = False
        try:
            with self.assertRaises((RcsHttpError, RcsParseError)):
                client.cancel_task_with_payload({"reqCode": "C1", "taskCode": "T1", "forceCancel": "1", "matterArea": "RETURN"})
            self.assertEqual(len(received), 1)
        finally:
            client.session.close()
            server.shutdown(); server.server_close(); worker.join(2)

    def test_retryable_http_error_does_not_replay_cancel(self):
        self.run_response(503, {"code": 1})

    def test_redirect_does_not_replay_cancel(self):
        self.run_response(307, {"code": 0})

    def test_empty_response_is_not_confirmation(self):
        self.run_response(200, {})

    def test_mismatched_identity_is_not_confirmation(self):
        self.run_response(200, {"code": 0, "reqCode": "OTHER"})
