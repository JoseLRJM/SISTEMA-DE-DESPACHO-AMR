"""Phase 1A: isolated fixtures, loopback HTTP and file-backed SQLite races."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import fifo_service
import main
import monitor_service as monitor
from models import Area, Base, Location, MaterialGroup, MovementOrder, Rack
from rcs_client import RcsClient, RcsTaskRequest, RcsTaskStatusResponse, RcsTimeoutError, generate_request_code, _extract_task_status_items
import test_dispatch_backend_baseline as baseline


class IdentityTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown

    def create(self, stub=None):
        stub = stub or baseline.StubRcsClient()
        with self.factory() as db:
            row = fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"])
            with patch.object(main, "_get_rcs_client_from_settings", return_value=stub), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")):
                main._dispatch_movement_order(db, row)
            return row.id, stub

    def query_client(self, code="REMOTE-001", status="in_progress", error=False):
        client = Mock()
        item = {"taskCode": code, "taskStatus": status}
        client.query_task_status_with_payload.return_value = RcsTaskStatusResponse(
            code=1 if error else 0, task_code=code, task_status=status, message="query error" if error else "success",
            reqCode="QUERY-ECHO", raw={"data": [item]}, task_statuses=[item])
        return client

    def manual_query(self, order_id, client):
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_with_overrides", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/query")):
            return main.run_manual_status_query(order_id, main.MovementOrderStatusQueryIn(payload={"reqCode": generate_request_code(), "taskCodes": ["REMOTE-001"]}))

    def monitor_query(self, order_id, client):
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            monitor._run_status_query_batch_safely(db, client=client, task_codes=[row.remote_task_code], task_map={row.remote_task_code: [row]}, debug_base_url="stub://rcs", debug_endpoint="/query", apply_delay_seconds=0)

    def test_A_create_identity_survives_queries_cancel_and_resend(self):
        order_id, stub = self.create()
        q1 = stub.payloads[0]["reqCode"]
        for _ in range(3):
            self.manual_query(order_id, self.query_client())
        self.monitor_query(order_id, self.query_client())
        with self.factory() as db, patch.object(main, "_get_rcs_client_from_settings", return_value=stub), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/cancel")):
            row = db.get(MovementOrder, order_id)
            main._execute_cancel_for_order(db, row, "0")
            main._dispatch_movement_order(db, row)
            self.assertEqual(row.req_code, q1)
            self.assertEqual(json.loads(row.dispatch_request_json)["reqCode"], q1)
            self.assertEqual(row.remote_task_code, "REMOTE-001")
            self.assertNotEqual(stub.cancel_payloads[0]["reqCode"], q1)
            self.assertEqual(stub.cancel_payloads[0]["taskCode"], "REMOTE-001")
            self.assertEqual(len(stub.payloads), 1)
            with self.assertRaises(HTTPException):
                main._save_movement_order_payload_override(db, row, {"reqCode": "REPLACEMENT"})

    def test_B_1000_unique_parallel_request_codes(self):
        with ThreadPoolExecutor(max_workers=16) as pool:
            codes = list(pool.map(lambda _: monitor.generate_req_code_ms(), range(1000)))
        self.assertEqual(len(set(codes)), 1000)
        for code in codes:
            self.assertRegex(code, r"^[0-9a-f]{20}$")

    def test_C_three_real_http_attempts_have_identical_body(self):
        bodies = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                request = json.loads(bodies[-1])
                response = json.dumps({"code": 0, "message": "success", "data": "T1", "reqCode": request["reqCode"]}).encode()
                self.send_response(503 if len(bodies) < 3 else 200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        client = RcsClient(f"http://127.0.0.1:{server.server_port}", "/create", max_retries=2, backoff_factor=0)
        client.session.trust_env = False
        try:
            q1 = generate_request_code()
            result = client.create_task(RcsTaskRequest.from_payload({"reqCode": q1}))
            self.assertEqual(result.data, "T1")
            self.assertEqual(len(bodies), 3)
            self.assertEqual(bodies, [bodies[0]] * 3)
            self.assertEqual([json.loads(body)["reqCode"] for body in bodies], [q1] * 3)
        finally:
            client.session.close()
            server.shutdown()
            server.server_close()
            worker.join(2)

    def assert_uncertain(self, accepted):
        ledger = {}
        stub = baseline.StubRcsClient("timeout")
        original = stub.create_task
        def create(task):
            if accepted:
                ledger[task.reqCode] = "T1"
            return original(task)
        stub.create_task = create
        order_id, _ = self.create(stub)
        # A new session represents restart: no volatile identity is needed.
        with self.factory() as db, patch.object(main, "_get_rcs_client_from_settings", return_value=stub):
            row = db.get(MovementOrder, order_id)
            main._dispatch_movement_order(db, row)
            self.assertFalse(main._rollback_unsuccessful_order_dispatch(db, row)["executed"])
            self.assertEqual(row.status, "pending_dispatch")
            self.assertEqual(row.dispatch_status, "uncertain")
            self.assertEqual(row.rcs_status, "dispatch_unknown")
            self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])
            self.assertEqual(json.loads(row.dispatch_request_json), stub.payloads[0])
            self.assertEqual(db.get(Rack, row.rack_id).status, "reserved")
            self.assertIn(row.destination_cell_id, fifo_service._reserved_destination_cell_ids(db))
            for force in ("0", "1"):
                with self.assertRaises(HTTPException) as raised:
                    main._execute_cancel_for_order(db, row, force)
                self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(stub.payloads), 1)
        self.assertEqual(len(ledger), 1 if accepted else 0)

    def test_D_timeout_before_acceptance(self):
        self.assert_uncertain(False)

    def test_E_timeout_after_acceptance_and_restart(self):
        self.assert_uncertain(True)

    def test_F_other_task_or_missing_identity_never_applies(self):
        order_id, _ = self.create()
        for code in ("T200", ""):
            client = self.query_client(code, "completed")
            self.manual_query(order_id, client)
            self.monitor_query(order_id, client)
            with self.factory() as db:
                self.assertEqual(db.get(MovementOrder, order_id).status, "dispatched")
                self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
        items = _extract_task_status_items({"data": [{"taskCode": "T200", "taskStatus": "completed"}]}, {"taskCodes": ["T100"]})
        self.assertEqual(items[0]["taskCode"], "T200")
        items = _extract_task_status_items({"data": [{"taskStatus": "completed"}]}, {"taskCodes": ["T100"]})
        self.assertFalse(any(item.get("taskCode") == "T100" for item in items))

    def test_G_query_timeout_and_error_preserve_active_resources(self):
        order_id, _ = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.status = "in_progress"
            row.rcs_status = "in_progress"
            db.commit()
        timeout_client = Mock()
        timeout_client.query_task_status_with_payload.side_effect = RcsTimeoutError("query timeout")
        for client in (timeout_client, self.query_client(status="failed", error=True)):
            self.manual_query(order_id, client)
            self.monitor_query(order_id, client)
            with self.factory() as db:
                row = db.get(MovementOrder, order_id)
                self.assertEqual(row.status, "in_progress")
                self.assertEqual(row.rcs_status, "in_progress")
                self.assertEqual(db.get(Rack, row.rack_id).status, "reserved")

    def test_I_sequential_rack_reuse_after_completion(self):
        order_id, _ = self.create()
        with self.factory() as db:
            fifo_service.simulate_order_completed(db, order_id)
            next_order = fifo_service.execute_direct_move_request(db, self.ids["destination"], self.ids["source"])
            self.assertNotEqual(next_order.id, order_id)
            self.assertEqual(next_order.rack_id, self.ids["rack"])
            self.assertEqual(next_order.status, "pending_dispatch")

    def test_J_all_pending_cancel_states_protect_even_stale_available_rack(self):
        order_id, _ = self.create()
        for status in ("cancel_requested_total", "cancel_requested_undo", "cancel_error"):
            with self.subTest(status=status), self.factory() as db:
                row = db.get(MovementOrder, order_id)
                row.status = status
                db.get(Rack, row.rack_id).status = "available"
                db.commit()
                self.assertIn(row.destination_cell_id, fifo_service._reserved_destination_cell_ids(db))
                with self.assertRaises(HTTPException):
                    fifo_service._claim_movement_resources(db, db.get(Rack, row.rack_id), db.get(Location, row.source_cell_id), db.get(Location, row.destination_cell_id))

    def test_known_task_uncertain_reconciles_without_create(self):
        order_id, stub = self.create()
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            row.status = "pending_dispatch"
            row.dispatch_status = "uncertain"
            db.commit()
        self.monitor_query(order_id, self.query_client())
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "in_progress")
            self.assertEqual(row.dispatch_status, "success")
            self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])

    def test_ambiguous_create_identity_keeps_resources(self):
        for mode in ("wrong_req", "missing_task"):
            with self.subTest(mode=mode):
                if mode == "missing_task":
                    self.tearDown(); self.setUp()
                stub = baseline.StubRcsClient()
                original = stub.create_task
                def create(task):
                    response = original(task)
                    if mode == "wrong_req":
                        response.reqCode = "OTHER-CREATION"
                    else:
                        response.data = ""
                    return response
                stub.create_task = create
                order_id, _ = self.create(stub)
                with self.factory() as db:
                    row = db.get(MovementOrder, order_id)
                    self.assertEqual(row.dispatch_status, "uncertain")
                    self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])
                    self.assertIsNone(row.remote_task_code)
                    self.assertEqual(db.get(Rack, row.rack_id).status, "reserved")

    def test_historical_interrupted_request_is_not_resent(self):
        with self.factory() as db:
            row = fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"])
            row.dispatch_request_json = json.dumps({"reqCode": "HISTORICAL-Q1"})
            db.commit()
            with patch.object(main, "_get_rcs_client_from_settings") as client:
                main._dispatch_movement_order(db, row)
                client.assert_not_called()
            self.assertEqual(row.dispatch_status, "uncertain")
            self.assertFalse(main._rollback_unsuccessful_order_dispatch(db, row)["executed"])

    def test_exact_remote_completion_applies(self):
        order_id, stub = self.create()
        self.monitor_query(order_id, self.query_client(status="completed"))
        with self.factory() as db:
            row = db.get(MovementOrder, order_id)
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])
            self.assertEqual(db.get(Location, self.ids["destination"]).rack_id, row.rack_id)

    def test_reused_custom_creation_code_is_blocked(self):
        order_id, stub = self.create()
        with self.factory() as db:
            fifo_service.simulate_order_completed(db, order_id)
            row = fifo_service.execute_direct_move_request(db, self.ids["destination"], self.ids["source"])
            payload = main._movement_order_json_payload(db, row)
            payload["reqCode"] = stub.payloads[0]["reqCode"]
            main._save_movement_order_payload_override(db, row, payload)
            with patch.object(main, "_get_rcs_client_from_settings", return_value=stub), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")), self.assertRaises(HTTPException) as raised:
                main._dispatch_movement_order(db, row)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(len(stub.payloads), 1)
            self.assertIsNone(row.dispatch_request_json)

    def test_create_parser_accepts_explicit_identity_not_arbitrary_objects(self):
        client = RcsClient("http://127.0.0.1", "/create", max_retries=0)
        try:
            for remote_data, top_code, expected in (("T1", "", "T1"), ({"taskCode": "T1"}, "", "T1"), ({"unrelated": "T1"}, "", ""), ([], "", ""), ("T1", "T2", "")):
                with self.subTest(remote_data=remote_data, top_code=top_code):
                    response = Mock(status_code=200)
                    response.json.return_value = {"code": 0, "message": "success", "data": remote_data, "taskCode": top_code}
                    with patch.object(client.session, "post", return_value=response):
                        parsed = client.create_task(RcsTaskRequest.from_payload({"reqCode": "Q1"}))
                    self.assertEqual(parsed.data, expected)
        finally:
            client.session.close()


class ResourceConcurrencyTests(unittest.TestCase):
    def test_H_concurrent_sessions_same_rack_or_destination(self):
        for mode, same_rack in (("direct", True), ("direct", False), ("fifo", True)):
            with self.subTest(mode=mode, same_rack=same_rack), tempfile.TemporaryDirectory(prefix="phase1a-") as directory:
                engine = create_engine("sqlite:///" + str(Path(directory) / "fixture.sqlite"), connect_args={"check_same_thread": False, "timeout": 10})
                Base.metadata.create_all(engine)
                factory = sessionmaker(bind=engine, autoflush=False)
                with factory() as db:
                    area = Area(code="A", name="A")
                    material = MaterialGroup(code="M", name="M")
                    db.add_all([area, material]); db.flush()
                    racks = [Rack(code=f"R{i}", status="available", material_group_id=material.id) for i in range(2)]
                    db.add_all(racks); db.flush()
                    cells = [Location(x=i, y=0, code=f"L{i}", enabled=1, is_visible=1, area_id=area.id, rack_id=racks[i].id if i < 2 else None) for i in range(4)]
                    db.add_all(cells); db.commit()
                    ids = [cell.id for cell in cells]
                    area_id, material_id = area.id, material.id
                barrier = threading.Barrier(2)
                original = fifo_service._claim_movement_resources
                def race(db, rack, source, destination, **kwargs):
                    # Both callers have already selected the same available resources.
                    barrier.wait(timeout=5)
                    return original(db, rack, source, destination, **kwargs)
                def attempt(index):
                    with factory() as db:
                        try:
                            if mode == "fifo":
                                row, _ = fifo_service.execute_fifo_request(db, area_id, area_id, material_id)
                            else:
                                row = fifo_service.execute_direct_move_request(db, ids[0 if same_rack else index], ids[2 + index if same_rack else 2])
                            return row.id
                        except HTTPException as exc:
                            self.assertEqual(exc.status_code, 409)
                            return None
                try:
                    with patch.object(fifo_service, "_claim_movement_resources", side_effect=race), ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(attempt, range(2)))
                    self.assertEqual(sum(value is not None for value in results), 1)
                    with factory() as db:
                        self.assertEqual(len(db.scalars(select(MovementOrder)).all()), 1)
                    order_id = next(value for value in results if value is not None)
                    dispatch_barrier = threading.Barrier(2)
                    payload_function = main._current_movement_order_payload
                    def payload_race(*args):
                        result = payload_function(*args)
                        dispatch_barrier.wait(timeout=5)
                        return result
                    stub = baseline.StubRcsClient()
                    def dispatch(_index):
                        with factory() as db:
                            main._dispatch_movement_order(db, db.get(MovementOrder, order_id))
                    with patch.object(main, "_current_movement_order_payload", side_effect=payload_race), patch.object(main, "_get_rcs_client_from_settings", return_value=stub), patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/create")), ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(dispatch, range(2)))
                    self.assertEqual(len(stub.payloads), 1)
                    with factory() as db:
                        row = db.get(MovementOrder, order_id)
                        self.assertEqual(row.req_code, stub.payloads[0]["reqCode"])
                        self.assertEqual(row.remote_task_code, "REMOTE-001")
                finally:
                    engine.dispose()
