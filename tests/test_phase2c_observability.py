"""RCS auditing with synthetic secrets, loopback HTTP and disposable SQLite."""
import io
import asyncio
import json
import logging
import os
import threading
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import requests
from sqlalchemy import select, text

import run_phase1a_suite
import main
import rcs_observability as audit
from logging_config import get_logger
from models import MovementOrder, DebugConsoleEvent, ConfigAuditLog
from rcs_client import RcsClient, RcsTaskRequest, RcsHttpError, RcsError
import test_dispatch_backend_baseline as baseline
import test_phase1a_identity_resources as identity

SECRETS = {"tokenCode": "TOKEN-SENTINEL-2C", "password": "PASSWORD-SENTINEL-2C",
           "Cookie": "sid=COOKIE-SENTINEL-2C", "Authorization": "Bearer AUTH-SENTINEL-2C",
           "X-API-Key": "API-SENTINEL-2C"}


@contextmanager
def server(responses):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.path, body, dict(self.headers)))
            status, payload = responses[min(len(received)-1, len(responses)-1)]
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(encoded)
        def log_message(self, *_): pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try: yield f"http://127.0.0.1:{http.server_port}", received
    finally: http.shutdown(); http.server_close(); thread.join(5)


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        baseline.DispatchBackendBaselineTests.setUp(self)
        self.previous_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.output = io.StringIO()
        self.handler = logging.StreamHandler(self.output)
        self.logger = logging.getLogger("app")
        self.previous_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous_level)
        logging.disable(self.previous_disable)
        baseline.DispatchBackendBaselineTests.tearDown(self)

    create = identity.IdentityTests.create

    def assert_secret_free(self, value):
        value = str(value)
        for secret in ("TOKEN-SENTINEL-2C", "PASSWORD-SENTINEL-2C", "COOKIE-SENTINEL-2C", "AUTH-SENTINEL-2C", "API-SENTINEL-2C"):
            self.assertNotIn(secret, value)

    def records(self):
        return [json.loads(line.split("RCS_AUDIT ", 1)[1]) for line in self.output.getvalue().splitlines() if "RCS_AUDIT " in line]

    def test_nested_headers_cookie_variants_and_echoes_are_redacted(self):
        value = {"message": "echo COOKIE-SENTINEL-2C", "nested": [[SECRETS]],
                 "set-cookie": "sid=COOKIE-SENTINEL-2C", "api_key": "API-SENTINEL-2C"}
        self.assert_secret_free(audit.sanitize(value))
        get_logger("app.rcs_client").warning("payload=%s", value)
        self.assert_secret_free(self.output.getvalue())

    def test_robot_monitor_administrative_message_is_sanitized(self):
        cfg = SimpleNamespace(resolved_base_url="http://127.0.0.1", agv_status_endpoint="/query",
                              enable_amr_monitor=1, enable_map_short_name=0, enable_map_code=0)
        client = SimpleNamespace(query_agv_status_with_payload=lambda *a, **k: {
            "code": "1", "message": "password=PASSWORD-SENTINEL-2C", "data": []})
        with self.factory() as db, patch.object(main, "_get_rcs_config", return_value=cfg), patch.object(
                main, "_get_rcs_client_from_settings", return_value=client):
            response = main._get_robot_status_monitor_response(db)
        self.assertFalse(response.ok)
        self.assert_secret_free(response.message)
        self.assertIn(audit.REDACTED, response.message)

    def test_http_error_body_is_sanitized(self):
        with server([(400, {"message": "failed", **SECRETS})]) as (url, received):
            client = RcsClient(url, "/create", max_retries=0)
            with self.assertRaises(RcsHttpError) as raised:
                client.create_task(RcsTaskRequest(reqCode="Q1", tokenCode=SECRETS["tokenCode"]))
            self.assert_secret_free(str(raised.exception))
            self.assert_secret_free(raised.exception.body)
            self.assertEqual(json.loads(received[0][1])["tokenCode"], SECRETS["tokenCode"])
        self.assert_secret_free(self.output.getvalue())

    def test_transport_exception_and_traceback_do_not_leak(self):
        client = RcsClient("http://127.0.0.1:1", "/create", max_retries=0,
                           default_headers={"Authorization": SECRETS["Authorization"], "Cookie": SECRETS["Cookie"]})
        with patch.object(client.session, "post", side_effect=requests.ConnectionError("http://user:PASSWORD-SENTINEL-2C@host/?token=TOKEN-SENTINEL-2C Cookie: sid=COOKIE-SENTINEL-2C")):
            try:
                client.create_task(RcsTaskRequest(reqCode="Q1", tokenCode=SECRETS["tokenCode"]))
            except RcsError as exc:
                get_logger("app.test").exception("transport exception: %s", exc)
                self.assert_secret_free(str(exc))
            else: self.fail("expected transport failure")
        self.assert_secret_free(self.output.getvalue())

    def test_three_attempts_final_body_q1_and_t1_are_correlated(self):
        replies = [(503, {"message": "retry"}), (503, {"message": "retry"}),
                   (200, {"code": 0, "message": "success", "data": "T1", "reqCode": "Q1"})]
        row = SimpleNamespace(id=42, req_code="Q1", remote_task_code=None)
        with server(replies) as (url, received):
            client = RcsClient(url, "/create", max_retries=2, backoff_factor=0,
                               default_headers={"Authorization": SECRETS["Authorization"]})
            task = RcsTaskRequest.from_payload({"reqCode": "Q1", "taskTyp": "A01", "tokenCode": SECRETS["tokenCode"],
                                               "positionCodePath": [{"positionCode": "S"}, {"positionCode": "D"}]})
            result = audit.trace_call("create", [row], client.create_task, task)
        self.assertEqual(result.data, "T1")
        self.assertEqual(len(received), 3)
        self.assertEqual(len(set(body for _, body, _ in received)), 1)
        records = self.records()
        attempts = [r for r in records if r["event"] == "transport_attempt"]
        self.assertEqual([r["attempt"] for r in attempts], [1, 2, 3])
        self.assertEqual([r["http_status"] for r in attempts], [503, 503, 200])
        expected = audit.sanitize(json.loads(received[0][1]))
        for record in attempts:
            self.assertEqual(record["payload"], expected)
            self.assertEqual(record["request_id"], "Q1")
            self.assertEqual(record["orders"][0]["movement_order_id"], 42)
            self.assertEqual(record["orders"][0]["q1"], "Q1")
            self.assertIn("timestamp", record)
        self.assertEqual(len({r["trace_id"] for r in records}), 1)
        self.assertEqual(records[-1]["t1"], "T1")
        self.assert_secret_free(self.output.getvalue())

    def test_query_and_cancel_keep_q1_and_separate_request_identity(self):
        row = SimpleNamespace(id=42, req_code="Q1", remote_task_code="T1")
        with server([(200, {"code": 0, "message": "success", "data": [{"taskCode": "T1", "taskStatus": "in_progress"}]}),
                     (200, {"code": 0, "message": "success", "reqCode": "C1"})]) as (url, received):
            client = RcsClient(url, "/create", endpoint_query_task_status="/query", endpoint_cancel_task="/cancel", max_retries=2)
            audit.trace_call("query", [row], client.query_task_status_with_payload, {"reqCode": "Q2", "taskCodes": ["T1"]})
            audit.trace_call("cancel", [row], client.cancel_task_with_payload, {"reqCode": "C1", "taskCode": "T1", "forceCancel": "0"})
        attempts = [r for r in self.records() if r["event"] == "transport_attempt"]
        self.assertEqual([(r["operation"], r["request_id"], r["attempt"]) for r in attempts], [("query", "Q2", 1), ("cancel", "C1", 1)])
        self.assertTrue(all(r["orders"] == [{"movement_order_id": 42, "q1": "Q1", "t1": "T1"}] for r in attempts))
        self.assertEqual((row.req_code, row.remote_task_code), ("Q1", "T1"))

    def test_operational_request_persisted_without_token_and_payload_unchanged(self):
        client = baseline.StubRcsClient()
        client._token_code = SECRETS["tokenCode"]
        oid, client = self.create(client)
        self.assertEqual(client.payloads[0]["tokenCode"], SECRETS["tokenCode"])
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual(json.loads(row.dispatch_request_json), audit.sanitize(client.payloads[0]))
            self.assert_secret_free(row.dispatch_request_json)
            logs = json.loads(row.status_query_log_json)
            self.assertEqual(logs[-1]["kind"], "create_task")
            self.assertEqual(logs[-1]["q1"], row.req_code)
            self.assertEqual(logs[-1]["t1"], "REMOTE-001")

    def test_persisted_request_matches_real_prepared_body(self):
        with server([(200, {"code": 0, "message": "success", "data": "T1"})]) as (url, received):
            client = RcsClient(url, "/create", max_retries=0)
            client._token_code = SECRETS["tokenCode"]
            oid, _ = self.create(client)
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            self.assertEqual(json.loads(row.dispatch_request_json), audit.sanitize(json.loads(received[0][1])))
            self.assertEqual(row.remote_task_code, "T1")
            attempts = [r for r in self.records() if r["event"] == "transport_attempt"]
            self.assertEqual(attempts[0]["payload"], json.loads(row.dispatch_request_json))

    def test_long_http_body_is_sanitized_before_truncation(self):
        with server([(400, {"password": SECRETS["password"]*60})]) as (url, _):
            with self.assertRaises(RcsHttpError):
                RcsClient(url, "/create", max_retries=0).create_task(RcsTaskRequest(reqCode="Q1"))
        self.assert_secret_free(self.output.getvalue())

    def test_unquoted_and_truncated_error_fields_are_redacted(self):
        for value in ('password=PASSWORD-SENTINEL-2C', '{"tokenCode": "TOKEN-SENTINEL-2C', 'API Key: API-SENTINEL-2C'):
            self.assert_secret_free(audit.sanitize_text(value))

    def test_persisted_events_history_and_http_errors_have_no_secrets(self):
        oid, _ = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            row.status_query_response_json = json.dumps(SECRETS)
            row.rcs_message = "Authorization: " + SECRETS["Authorization"]
            main._append_debug_console_event(db, direction="received", module="test", payload=SECRETS, message="Cookie: " + SECRETS["Cookie"])
            db.commit()
            raw = db.execute(text("SELECT status_query_response_json, rcs_message FROM movement_orders WHERE id=:id"), {"id": oid}).one()
            self.assert_secret_free(raw)
            events = db.execute(select(DebugConsoleEvent)).scalars().all()
            self.assert_secret_free([(event.payload_json, event.message) for event in events])

    def test_configuration_change_audit_never_stores_secret_values(self):
        with self.factory() as db:
            main._audit_config_change(db, table_name="settings", record_key="rcs_token_code", field_name="value", old_value=SECRETS["tokenCode"], new_value=SECRETS["password"])
            db.commit()
            row = db.execute(select(ConfigAuditLog)).scalar_one()
            self.assertEqual((row.old_value, row.new_value), (audit.REDACTED, audit.REDACTED))
        self.assert_secret_free(self.output.getvalue())

    def test_historical_debug_response_is_sanitized_on_read(self):
        row = DebugConsoleEvent(id=1, direction="received", module="test", payload_json=json.dumps(SECRETS), message="Cookie: " + SECRETS["Cookie"])
        from datetime import datetime
        row.created_at = datetime.utcnow()
        self.assert_secret_free(main._debug_console_event_out(row).model_dump())

    def test_recent_history_retains_creation_after_many_queries(self):
        oid, _ = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, oid)
            main._append_status_query_log(db, row, kind="cancel_task", request_payload={"reqCode": "C1"}, response_payload={"code": 0})
            for i in range(220):
                main._append_status_query_log(db, row, kind="status_query", request_payload={"reqCode": str(i)}, response_payload={"code": 0})
            db.commit()
            logs = json.loads(row.status_query_log_json)
            self.assertLessEqual(len(logs), 200)
            self.assertTrue(any(item["kind"] == "create_task" for item in logs))
            self.assertTrue(any(item["kind"] == "cancel_task" for item in logs))
            self.assertEqual(logs[-1]["request"]["reqCode"], "219")

    def test_console_retains_more_than_twenty_but_is_bounded(self):
        with self.factory() as db:
            for i in range(1005):
                db.add(DebugConsoleEvent(direction="received", module="test", message=str(i)))
            db.commit()
            main._append_debug_console_event(db, direction="received", module="test", message="last")
            rows = db.execute(select(DebugConsoleEvent)).scalars().all()
            self.assertEqual(len(rows), 1000)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI operational storage")
    def test_encrypted_override_roundtrip_preserves_actual_secret(self):
        import fifo_service
        with self.factory() as db:
            row = fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"])
            payload = main._movement_order_json_payload(db, row)
            payload["tokenCode"] = SECRETS["tokenCode"]
            main._save_movement_order_payload_override(db, row, payload)
            self.assert_secret_free(row.override_payload_json)
            self.assertEqual(main._current_movement_order_payload(row)[0], payload)
            public = audit.sanitize(payload)
            public["priority"] = "2"
            main._save_movement_order_payload_override(db, row, public)
            actual = main._current_movement_order_payload(row)[0]
            self.assertEqual(actual["tokenCode"], SECRETS["tokenCode"])
            self.assertEqual(actual["priority"], "2")

    def test_logs_download_requires_admin_before_reading_files(self):
        with patch.object(main, "ensure_log_file") as files:
            with self.assertRaises(main.HTTPException): main.download_logs(None)
            files.assert_not_called()

    def test_download_sanitizes_historical_files_without_modifying_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.log"
            original = json.dumps(SECRETS)
            path.write_text(original, encoding="utf-8")
            with patch.object(main, "ensure_log_file", return_value=path), patch.object(main, "require_admin"):
                response = main.download_logs("authorized-test")
                async def consume():
                    return b"".join([chunk async for chunk in response.body_iterator])
                archive = asyncio.run(consume())
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                self.assert_secret_free(zipped.read("app.log").decode())
            self.assertEqual(path.read_text(encoding="utf-8"), original)
