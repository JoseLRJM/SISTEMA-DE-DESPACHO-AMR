import asyncio
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, DebugConsoleEvent, Location, MaterialGroup, MovementOrder, Rack


class RuntimeIncrementalPayloadTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        with self.Session() as db:
            area = Area(code="A", name="Area", is_active=1)
            material = MaterialGroup(code="M", name="Material", is_active=1)
            rack = Rack(code="R", status="reserved")
            db.add_all([area, material, rack])
            db.flush()
            source = Location(x=1, y=1, area_id=area.id, rack_id=rack.id)
            destination = Location(x=2, y=1, area_id=area.id)
            db.add_all([source, destination])
            db.flush()
            order = MovementOrder(
                order_code="ORDER", source_area_id=area.id, destination_area_id=area.id,
                material_group_id=material.id, rack_id=rack.id,
                source_cell_id=source.id, destination_cell_id=destination.id,
                status="dispatched", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            debug = DebugConsoleEvent(direction="received", module="test", message="ok")
            db.add_all([order, debug])
            db.commit()
            self.ids = {"order": order.id, "location": source.id, "rack": rack.id, "debug_event": debug.id}

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _event(self, kind):
        with patch.object(main, "SessionLocal", self.Session):
            return main._runtime_change_to_event({"kind": kind, "id": self.ids[kind], "action": "updated"})

    def test_incremental_payloads_reconstruct_current_entities_and_stay_small(self):
        expected_types = {
            "order": "order_updated", "location": "location_updated",
            "rack": "rack_updated", "debug_event": "debug_event_added",
        }
        for kind, event_type in expected_types.items():
            result_type, payload = self._event(kind)
            self.assertEqual(result_type, event_type)
            self.assertLess(len(json.dumps(payload, default=str).encode()), 10_000)
        self.assertEqual(self._event("order")[1]["order"]["order_id"], self.ids["order"])
        self.assertEqual(self._event("location")[1]["location"]["rack_id"], self.ids["rack"])
        self.assertEqual(self._event("rack")[1]["rack"]["id"], self.ids["rack"])

    def test_initial_snapshot_remains_complete(self):
        with self.Session() as db, patch.object(main, "_maybe_auto_release_stuck_cancel_return_orders"):
            snapshot = main._build_runtime_snapshot_out(db, include_robot_monitor=0)
        self.assertEqual(len(snapshot.orders), 1)
        self.assertEqual(len(snapshot.locations), 2)
        self.assertEqual(len(snapshot.debug_log), 1)

    def test_location_deleted_event_carries_revision_and_authoritative_absence(self):
        with self.Session() as db:
            location = db.get(Location, self.ids["location"])
            location.rack_id = None
            db.commit()
            db.delete(location)
            db.commit()
        before = main._locations_current_revision()
        main._increment_locations_revision()
        with patch.object(main, "SessionLocal", self.Session):
            event_type, payload = main._runtime_change_to_event({"kind": "location", "id": self.ids["location"], "action": "deleted"})
        self.assertEqual(event_type, "location_deleted")
        self.assertEqual(payload["id"], self.ids["location"])
        self.assertEqual(payload["locations_revision"], before + 1)
        with self.Session() as db:
            self.assertIsNone(db.get(Location, self.ids["location"]))

    def test_revisions_are_monotonic_and_history_supports_reconnect(self):
        before = main._runtime_current_revision()
        first = main.publish_runtime_event("robot_monitor_updated", {"robots": []}, enqueue=False)
        second = main.publish_runtime_event("location_updated", {"location": {"id": 1}}, enqueue=False)
        self.assertEqual(first["revision"], before + 1)
        self.assertEqual(second["revision"], before + 2)
        self.assertEqual([row["revision"] for row in main._runtime_events_since(before)][-2:], [before + 1, before + 2])

    def test_frontend_has_scoped_handlers_and_resync_fallback(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        robot_block = app_js[app_js.index("function applyRobotMonitorUpdated") : app_js.index("function applyLocationUpdated")]
        order_block = app_js[app_js.index("function applyOrderUpdated") : app_js.index("function applyOrderDeleted")]
        location_block = app_js[app_js.index("function applyLocationUpdated") : app_js.index("function applyLocationDeleted")]
        self.assertNotIn("draw()", robot_block)
        self.assertNotIn("renderOrdersList", robot_block)
        self.assertNotIn("replaceLocations", order_block)
        self.assertNotIn("renderOrdersList", location_block)
        self.assertIn("revision !== runtimeRevision + 1", app_js)
        self.assertIn("requestRuntimeResync(String(err))", app_js)


class FakeWebSocket:
    def __init__(self, blocked=False):
        self.messages = []
        self.gate = asyncio.Event()
        if not blocked:
            self.gate.set()

    async def send_json(self, payload):
        await self.gate.wait()
        self.messages.append(payload)


class RuntimeWebSocketManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_clients_receive_event_and_slow_client_does_not_block(self):
        manager = main.RuntimeWebSocketManager()
        slow = FakeWebSocket(blocked=True)
        fast = FakeWebSocket()
        await manager.connect(slow)
        await manager.connect(fast)
        await manager.broadcast_json({"type": "location_updated", "revision": 1, "payload": {}})
        await asyncio.sleep(0.01)
        self.assertEqual(len(fast.messages), 1)
        self.assertEqual(len(slow.messages), 0)
        self.assertEqual(await manager.count(), 2)
        slow.gate.set()
        await asyncio.sleep(0.01)
        self.assertEqual(len(slow.messages), 1)
        await manager.disconnect(slow)
        await manager.disconnect(fast)
        self.assertEqual(await manager.count(), 0)

    async def test_saturated_client_queue_is_replaced_by_resync_required(self):
        manager = main.RuntimeWebSocketManager()
        slow = FakeWebSocket(blocked=True)
        fast = FakeWebSocket()
        await manager.connect(slow)
        await manager.connect(fast)
        for revision in range(1, 130):
            await manager.broadcast_json({"type": "order_updated", "revision": revision, "payload": {}})
            await asyncio.sleep(0)
        await asyncio.sleep(0.02)
        self.assertEqual(len(fast.messages), 129)
        slow.gate.set()
        await asyncio.sleep(0.02)
        self.assertTrue(any(message.get("type") == "runtime_resync_required" for message in slow.messages))
        await manager.disconnect(slow)
        await manager.disconnect(fast)

    async def test_global_change_queue_overflow_sets_resync_flag(self):
        previous_loop = main._runtime_event_loop
        previous_queue = main._runtime_event_queue
        previous_overflow = main._runtime_queue_overflow
        try:
            main._runtime_event_loop = asyncio.get_running_loop()
            main._runtime_event_queue = asyncio.Queue(maxsize=2)
            main._runtime_queue_overflow = False
            for entity_id in range(1, 5):
                main._queue_runtime_change({"kind": "order", "id": entity_id, "action": "updated"})
            await asyncio.sleep(0)
            self.assertTrue(main._runtime_queue_overflow)
            self.assertEqual(main._runtime_event_queue.qsize(), 2)
        finally:
            main._runtime_event_loop = previous_loop
            main._runtime_event_queue = previous_queue
            main._runtime_queue_overflow = previous_overflow


if __name__ == "__main__":
    unittest.main()
