import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, Location, MaterialGroup, MovementOrder, Rack


class RuntimeSnapshotHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        with self.Session() as db:
            area = Area(code="A", name="Area", is_active=1)
            material = MaterialGroup(code="M", name="Material", is_active=1)
            rack = Rack(code="R", status="reserved", material_group_id=1)
            db.add_all([area, material, rack])
            db.flush()
            source = Location(x=1, y=1, area_id=area.id, rack_id=rack.id)
            destination = Location(x=2, y=1, area_id=area.id)
            db.add_all([source, destination])
            db.flush()
            db.add(MovementOrder(
                order_code="ORDER", source_area_id=area.id, destination_area_id=area.id,
                material_group_id=material.id, rack_id=rack.id,
                source_cell_id=source.id, destination_cell_id=destination.id,
                status="dispatched", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            ))
            db.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _snapshot(self, client_revision):
        with patch.object(main, "SessionLocal", self.Session), \
             patch.object(main, "_maybe_auto_release_stuck_cancel_return_orders"):
            return main._build_runtime_snapshot_message(client_revision)

    def test_current_locations_revision_omits_duplicate_locations(self):
        revision = main._locations_current_revision()
        message = self._snapshot(revision)
        self.assertFalse(message["locations_included"])
        self.assertEqual(message["locations_revision"], revision)
        self.assertEqual(message["payload"]["locations"], [])
        self.assertEqual(len(message["payload"]["orders"]), 1)
        self.assertIn("debug_log", message["payload"])
        self.assertIn("robot_monitor", message["payload"])

    def test_missing_or_old_revision_gets_full_backward_compatible_snapshot(self):
        revision = main._locations_current_revision()
        for client_revision in (None, revision - 1):
            message = self._snapshot(client_revision)
            self.assertTrue(message["locations_included"])
            self.assertEqual(len(message["payload"]["locations"]), 2)

    def test_location_change_invalidates_previous_revision(self):
        previous = main._locations_current_revision()
        main._increment_locations_revision()
        message = self._snapshot(previous)
        self.assertTrue(message["locations_included"])
        self.assertGreater(message["locations_revision"], previous)

    def test_server_restart_revision_rejects_previous_client_cache(self):
        previous_revision = main._locations_current_revision()
        simulated_restart_revision = previous_revision + 10_000
        original_revision = main._locations_revision
        try:
            main._locations_revision = simulated_restart_revision
            message = self._snapshot(previous_revision)
            self.assertTrue(message["locations_included"])
            self.assertEqual(message["locations_revision"], simulated_restart_revision)
            self.assertEqual(len(message["payload"]["locations"]), 2)
        finally:
            main._locations_revision = original_revision

    def test_locations_http_exposes_revision_without_changing_list_contract(self):
        response = Response()
        with patch.object(main, "SessionLocal", self.Session), \
             patch.object(main, "_auto_release_stuck_cancel_return_orders"):
            rows = main.list_locations(response)
        self.assertEqual(len(rows), 2)
        self.assertEqual(int(response.headers["X-Locations-Revision"]), main._locations_current_revision())

    def test_frontend_waits_for_http_dependencies_and_sends_revision(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        init_block = app_js[app_js.index("(async function init()") : app_js.index('window.addEventListener("beforeunload"')]
        self.assertLess(init_block.index("await Promise.all(["), init_block.index("connectRuntimeSocket()"))
        self.assertLess(init_block.index("await loadMovementOrders"), init_block.index("connectRuntimeSocket()"))
        self.assertIn("X-Locations-Revision", app_js)
        self.assertIn("locations_revision", app_js)
        self.assertIn("preserveLocations: !locationsIncluded", app_js)


if __name__ == "__main__":
    unittest.main()
