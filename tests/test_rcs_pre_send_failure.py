import errno
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests
from fastapi import HTTPException
from sqlalchemy import select
from urllib3.exceptions import NewConnectionError, ReadTimeoutError
from urllib3.response import HTTPResponse

import fifo_service
import main
from models import Location, MovementOrder, Rack
from rcs_client import RcsClient
import test_dispatch_backend_baseline as baseline


class PreSendFailureTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown

    def client(self, url="http://127.0.0.1:1"):
        client = RcsClient(url, "/create", max_retries=2, backoff_factor=0, timeout=(1, 0.05))
        client.session.trust_env = False
        self.addCleanup(client.session.close)
        return client

    def dispatch(self, client, *, not_sent):
        body = main.DirectMoveRequestIn(source_cell_id=self.ids["source"], destination_cell_id=self.ids["destination"])
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=(client.base_url, "/create")):
            with self.assertRaises(HTTPException) as raised:
                main.create_direct_move_movement(body)
        self.assertEqual(raised.exception.status_code, 400 if not_sent else 409)
        with self.factory() as db:
            order = db.scalars(select(MovementOrder)).one()
            self.assertEqual(order.status, "dispatch_error" if not_sent else "pending_dispatch")
            self.assertEqual(order.dispatch_status, "error" if not_sent else "uncertain")
            self.assertEqual(db.get(Rack, order.rack_id).status, "available" if not_sent else "reserved")
            self.assertEqual(order.destination_cell_id in fifo_service._reserved_destination_cell_ids(db), not not_sent)
            self.assertEqual(db.get(Location, order.source_cell_id).rack_id, order.rack_id)
            self.assertIsNone(db.get(Location, order.destination_cell_id).rack_id)
            self.assertEqual(order.req_code, json.loads(order.dispatch_request_json)["reqCode"])
            self.assertEqual(json.loads(order.dispatch_response_json)["confirmed_not_sent"], not_sent)
            self.assertIsNone(order.remote_task_code)
            if not_sent:
                self.assertNotIn("reconcili", order.rcs_message.lower())
                self.assertNotIn("reconcili", str(raised.exception.detail).lower())
            else:
                self.assertFalse(main._rollback_unsuccessful_order_dispatch(db, order)["executed"])
            return order.req_code

    def test_01_real_connection_refused_releases_resources(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))  # Bound but not listening: no peer can receive a POST.
            self.dispatch(self.client(f"http://127.0.0.1:{unused.getsockname()[1]}"), not_sent=True)

    def test_02_connect_timeout_through_real_retry_stack_releases(self):
        with patch("urllib3.util.connection.create_connection", side_effect=socket.timeout("connect timed out")) as connect:
            self.dispatch(self.client(), not_sent=True)
        self.assertEqual(connect.call_count, 3)

    def ambiguous_http(self, read_timeout):
        bodies, received = [], threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                if len(bodies) == 3:
                    received.set()
                if read_timeout:
                    threading.Event().wait(0.25)
                self.close_connection = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            q1 = self.dispatch(self.client(f"http://127.0.0.1:{server.server_port}"), not_sent=False)
            self.assertTrue(received.wait(3))
            self.assertEqual(len(bodies), 3)
            self.assertEqual(bodies, [bodies[0]] * 3)
            self.assertEqual(json.loads(bodies[0])["reqCode"], q1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_03_read_timeout_after_post_keeps_reservation_and_q1(self):
        self.ambiguous_http(read_timeout=True)

    def test_04_peer_closes_after_post_is_uncertain(self):
        self.ambiguous_http(read_timeout=False)

    def test_05_read_then_refused_is_not_classified_by_last_error(self):
        responses = [ReadTimeoutError(None, "/create", "response lost"),
                     NewConnectionError(None, "connection refused"), NewConnectionError(None, "connection refused")]
        with patch("urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=responses) as attempts:
            q1 = self.dispatch(self.client(), not_sent=False)
        bodies = [call.kwargs["body"] for call in attempts.call_args_list]
        self.assertEqual(bodies, [bodies[0]] * 3)
        self.assertEqual(json.loads(bodies[0])["reqCode"], q1)

    def test_06_http_response_then_refused_is_still_uncertain(self):
        response = HTTPResponse(body=b"", status=503, preload_content=False)
        with patch("urllib3.connectionpool.HTTPConnectionPool._make_request", side_effect=[response, NewConnectionError(None, "refused"), NewConnectionError(None, "refused")]):
            self.dispatch(self.client(), not_sent=False)

    def test_07_generic_connection_error_is_not_proof_of_no_send(self):
        client = self.client()
        with patch.object(client.session, "post", side_effect=requests.ConnectionError("connection refused text alone is not evidence")):
            self.dispatch(client, not_sent=False)

    def test_08_unreachable_host_during_connect_is_confirmed_not_sent(self):
        with patch("urllib3.util.connection.create_connection", side_effect=OSError(errno.EHOSTUNREACH, "host unreachable")):
            self.dispatch(self.client(), not_sent=True)

    def test_09_shared_manual_dispatch_also_compensates(self):
        client = self.client()
        with self.factory() as db:
            order = fifo_service.execute_direct_move_request(db, self.ids["source"], self.ids["destination"])
            with patch.object(main, "_get_rcs_client_from_settings", return_value=client), patch.object(main, "_resolve_rcs_target", return_value=(client.base_url, "/create")), patch("urllib3.util.connection.create_connection", side_effect=socket.timeout("connect timeout")):
                result = main._dispatch_movement_order(db, order)
            self.assertEqual(result.dispatch_status, "error")
            self.assertEqual(order.status, "dispatch_error")
            self.assertEqual(db.get(Rack, order.rack_id).status, "available")

    def test_10_redirect_response_then_connect_failure_is_uncertain(self):
        client = self.client()
        response = requests.Response()
        response.status_code = 307
        def redirected_failure(*args, **kwargs):
            kwargs["hooks"]["response"](response)
            raise requests.ConnectTimeout("redirect target refused connection")
        with patch.object(client.session, "post", side_effect=redirected_failure):
            self.dispatch(client, not_sent=False)
