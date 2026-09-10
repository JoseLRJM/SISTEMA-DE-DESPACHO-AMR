import unittest
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, DebugConsoleEvent, Location, MaterialGroup, MovementOrder, Rack


class FailedRcsCleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.now = datetime.utcnow()
        self.original_robot_check = main._robot_monitor_indicates_order_running
        main._robot_monitor_indicates_order_running = lambda order: False

        area = Area(code="TEST", name="Test", area_type="storage", color="#000000", is_active=1, priority=1)
        material = MaterialGroup(code="MAT", name="Material", color="#000000", is_active=1)
        self.db.add_all([area, material])
        self.db.flush()
        self.area = area
        self.material = material

    def tearDown(self):
        main._robot_monitor_indicates_order_running = self.original_robot_check
        self.db.rollback()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _scenario(self, rack_code, *, local_status="dispatched", rcs_status="failed", rack_status="reserved", with_location=True):
        rack = Rack(code=rack_code, status=rack_status, material_group_id=self.material.id, quantity=0)
        self.db.add(rack)
        self.db.flush()
        source = Location(code=f"{rack_code}-SRC", x=rack.id * 2, y=1, area_id=self.area.id, status=1, rack_id=rack.id if with_location else None)
        destination = Location(code=f"{rack_code}-DST", x=rack.id * 2 + 1, y=2, area_id=self.area.id, status=0)
        self.db.add_all([source, destination])
        self.db.flush()
        order = MovementOrder(
            order_code=f"ORDER-{rack_code}-{local_status}-{rcs_status}",
            source_area_id=self.area.id,
            destination_area_id=self.area.id,
            material_group_id=self.material.id,
            rack_id=rack.id,
            source_cell_id=source.id,
            destination_cell_id=destination.id,
            status=local_status,
            rcs_status=rcs_status,
            dispatch_status="success",
            remote_task_code=f"RCS-{rack_code}",
            status_query_checked_at=self.now,
            status_query_request_json=json.dumps({"taskCodes": [f"RCS-{rack_code}"]}),
            status_query_response_json=json.dumps({"code": 0, "taskCode": f"RCS-{rack_code}", "taskStatus": rcs_status}),
            created_at=self.now - timedelta(minutes=60),
            updated_at=self.now - timedelta(minutes=60),
        )
        self.db.add(order)
        self.db.flush()
        return rack, source, order

    def _reconcile_and_release(self, order, rack):
        eligible, _ = main._validate_cleanup_order_eligible(self.db, order, now=self.now, min_age_minutes=30)
        if eligible:
            main._close_cleanup_order(self.db, order, now=self.now, reason="test_failed_rcs", admin_user="test")
            self.db.flush()
            main._release_rack_if_no_active_orders(
                self.db,
                rack.id,
                related_order_id=order.id,
                reason="test_failed_rcs",
                source="cleanup_test",
                actor="test",
            )
            self.db.flush()
        return eligible

    def test_dispatched_failed_releases_mxm050_and_keeps_location(self):
        rack, location, order = self._scenario("MXM050")
        location_rack_id = location.rack_id
        self.assertTrue(self._reconcile_and_release(order, rack))
        self.assertEqual(order.status, "failed")
        self.assertEqual(order.rcs_status, "failed")
        self.assertEqual(rack.status, "available")
        self.assertEqual(location.rack_id, location_rack_id)
        self.assertEqual(order.forced_local_close_reason, "admin_force_release_rcs_failed")
        self.assertGreater(self.db.query(DebugConsoleEvent).count(), 0)

    def test_in_progress_failed_releases_mxm060(self):
        rack, _, order = self._scenario("MXM060", local_status="in_progress")
        self.assertTrue(self._reconcile_and_release(order, rack))
        self.assertEqual((order.status, rack.status), ("failed", "available"))

    def test_other_active_order_keeps_rack_reserved(self):
        rack, _, failed_order = self._scenario("MULTI")
        other = MovementOrder(
            order_code="ORDER-MULTI-ACTIVE",
            source_area_id=failed_order.source_area_id,
            destination_area_id=failed_order.destination_area_id,
            material_group_id=failed_order.material_group_id,
            rack_id=rack.id,
            source_cell_id=failed_order.source_cell_id,
            destination_cell_id=failed_order.destination_cell_id,
            status="in_progress",
            rcs_status="running",
        )
        self.db.add(other)
        self.db.flush()
        self.assertTrue(self._reconcile_and_release(failed_order, rack))
        self.assertEqual(failed_order.status, "failed")
        self.assertEqual(rack.status, "reserved")

    def test_repeated_cleanup_is_idempotent(self):
        rack, _, order = self._scenario("REPEAT")
        self.assertTrue(self._reconcile_and_release(order, rack))
        self.assertFalse(self._reconcile_and_release(order, rack))
        self.assertEqual((order.status, rack.status), ("failed", "available"))

    def test_active_and_unknown_rcs_states_are_not_eligible(self):
        for suffix, remote_status in (("RUN", "running"), ("UNKNOWN", "")):
            _, _, order = self._scenario(suffix, rcs_status=remote_status)
            eligible, _ = main._validate_cleanup_order_eligible(self.db, order, now=self.now, min_age_minutes=30)
            self.assertFalse(eligible)

    def test_failed_status_is_blocked_when_robot_monitor_still_sees_execution(self):
        _, _, order = self._scenario("ROBOT-ACTIVE")
        main._robot_monitor_indicates_order_running = lambda candidate: candidate.id == order.id
        eligible, reason = main._validate_cleanup_order_eligible(self.db, order, now=self.now, min_age_minutes=30)
        self.assertFalse(eligible)
        self.assertEqual(reason, "AGV ejecutando la orden")

    def test_already_completed_or_failed_and_available_are_unchanged(self):
        rack, _, completed = self._scenario("DONE", local_status="completed", rcs_status="completed", rack_status="available")
        eligible, _ = main._validate_cleanup_order_eligible(self.db, completed, now=self.now, min_age_minutes=30)
        self.assertFalse(eligible)
        self.assertEqual((completed.status, rack.status), ("completed", "available"))

        failed_rack, _, failed = self._scenario("FAILED", local_status="failed", rack_status="available")
        eligible, _ = main._validate_cleanup_order_eligible(self.db, failed, now=self.now, min_age_minutes=30)
        self.assertFalse(eligible)
        self.assertEqual((failed.status, failed_rack.status), ("failed", "available"))

    def test_failed_order_without_rack_does_not_raise(self):
        order = MovementOrder(
            order_code="ORDER-NO-RACK",
            source_area_id=self.area.id,
            destination_area_id=self.area.id,
            material_group_id=self.material.id,
            rack_id=None,
            source_cell_id=1,
            destination_cell_id=2,
            status="dispatched",
            rcs_status="failed",
            remote_task_code="RCS-NO-RACK",
            status_query_checked_at=self.now,
            status_query_request_json=json.dumps({"taskCodes": ["RCS-NO-RACK"]}),
            status_query_response_json=json.dumps({"code": 0, "taskCode": "RCS-NO-RACK", "taskStatus": "failed"}),
            created_at=self.now - timedelta(minutes=60),
        )
        eligible, _ = main._validate_cleanup_order_eligible(self.db, order, now=self.now, min_age_minutes=30)
        self.assertTrue(eligible)
        self.assertFalse(main._release_rack_if_no_active_orders(self.db, None, related_order_id=None, reason="test", source="test"))


if __name__ == "__main__":
    unittest.main()
