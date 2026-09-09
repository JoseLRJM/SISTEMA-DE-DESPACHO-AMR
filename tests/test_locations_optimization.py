import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, Location, MaterialGroup, MovementOrder, Rack


class LocationOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        area = Area(code="AREA", name="Área de prueba", is_active=1)
        material = MaterialGroup(code="MAT", name="Material", is_active=1)
        reserved_rack = Rack(code="R-RES", status="reserved")
        active_rack = Rack(code="R-ACT", status="available")
        self.db.add_all([area, material, reserved_rack, active_rack])
        self.db.flush()
        source = Location(x=0, y=0, status=0, is_visible=1, enabled=1, area_id=area.id)
        reserved_location = Location(x=1, y=0, status=1, is_visible=1, enabled=1, area_id=area.id, rack_id=reserved_rack.id)
        active_location = Location(x=2, y=0, status=1, is_visible=1, enabled=1, area_id=area.id, rack_id=active_rack.id)
        self.db.add_all([source, reserved_location, active_location])
        self.db.flush()
        self.db.add(MovementOrder(
            order_code="ORDER-ACTIVE",
            remote_task_code="REMOTE-ACTIVE",
            rack_id=active_rack.id,
            source_area_id=area.id,
            destination_area_id=area.id,
            material_group_id=material.id,
            source_cell_id=source.id,
            destination_cell_id=active_location.id,
            status="dispatched",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_location_list_uses_fixed_query_count_and_light_order_columns(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            rows = main._list_locations_out(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(len(rows), 3)
        self.assertLessEqual(len(statements), 4)
        self.assertFalse(any("status_query_log_json" in statement for statement in statements))

    def test_reservation_semantics_match_existing_location_builder(self):
        entity_rows = self.db.query(Location).order_by(Location.id).all()
        area_map, rack_map = main._build_location_lookup_maps(self.db, entity_rows)
        expected = [
            main._location_out(self.db, row, area_by_id=area_map, rack_by_id=rack_map).model_dump()
            for row in entity_rows
        ]
        actual = [row.model_dump() for row in main._list_locations_out(self.db)]
        self.assertEqual(actual, expected)

    def test_snapshot_can_omit_locations_without_changing_default(self):
        without_locations = main._build_runtime_snapshot_out(self.db, include_robot_monitor=0, include_locations=0)
        with_locations = main._build_runtime_snapshot_out(self.db, include_robot_monitor=0)
        self.assertEqual(without_locations.locations, [])
        self.assertEqual(len(with_locations.locations), 3)

    def test_frontend_initial_load_requests_locations_only_once(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        init_block = app_js[app_js.index("(async function init()") : app_js.index("window.addEventListener(\"beforeunload\"")]
        self.assertEqual(init_block.count("loadLocationsCached("), 1)
        self.assertEqual(init_block.count("loadLocations("), 0)
        self.assertIn("loadMovementOrders(null, false, false)", init_block)
        self.assertIn("include_locations=${includeLocations ? 1 : 0}", app_js)
        self.assertIn("let locationById = new Map()", app_js)
        self.assertIn("let operationalLocations = []", app_js)


if __name__ == "__main__":
    unittest.main()
