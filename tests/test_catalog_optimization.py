import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, Location, MaterialGroup, MovementOrder, Rack


class CatalogOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        active_area = Area(code="A-ACT", name="Área activa", is_active=1, priority=1)
        inactive_area = Area(code="A-INACT", name="Área inactiva", is_active=0, priority=2)
        active_material = MaterialGroup(code="M-ACT", name="Material activo", is_active=1)
        inactive_material = MaterialGroup(code="M-INACT", name="Material inactivo", is_active=0)
        self.db.add_all([active_area, inactive_area, active_material, inactive_material])
        self.db.flush()

        reserved = Rack(
            code="R-001", status="available", material_group_id=inactive_material.id,
            lot="L1", quantity=4, rack_custom_fields_json='[{"key":"quality","label":"Calidad","value":"A"}]',
        )
        status_reserved = Rack(code="R-002", status="reserved", material_group_id=active_material.id)
        no_location = Rack(code="R-003", status="", material_group_id=None)
        self.db.add_all([reserved, status_reserved, no_location])
        self.db.flush()
        source = Location(x=1, y=1, area_id=inactive_area.id, rack_id=reserved.id)
        destination = Location(x=2, y=1, area_id=active_area.id, rack_id=status_reserved.id)
        self.db.add_all([source, destination])
        self.db.flush()

        now = datetime.utcnow()
        for index, remote_code in enumerate((None, "REMOTE-LATEST")):
            self.db.add(MovementOrder(
                order_code=f"ACTIVE-{index}", remote_task_code=remote_code,
                source_area_id=active_area.id, destination_area_id=inactive_area.id,
                material_group_id=active_material.id, rack_id=reserved.id,
                source_cell_id=source.id, destination_cell_id=destination.id,
                status="dispatched", created_at=now + timedelta(seconds=index), updated_at=now,
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _legacy_catalog(self):
        return main.CatalogOut(
            areas=[main._to_area_out(row) for row in self.db.execute(
                select(Area).where(Area.is_active == 1).order_by(Area.priority.asc(), Area.name.asc())
            ).scalars().all()],
            materials=[main._to_material_out(row) for row in self.db.execute(
                select(MaterialGroup).where(MaterialGroup.is_active == 1).order_by(MaterialGroup.name.asc())
            ).scalars().all()],
            racks=[main._rack_out(self.db, row) for row in self.db.execute(
                select(Rack).order_by(Rack.code.asc())
            ).scalars().all()],
        )

    def test_set_based_catalog_exactly_matches_legacy_payload(self):
        expected = self._legacy_catalog().model_dump(mode="json")
        actual = main._catalog_out(self.db).model_dump(mode="json")
        self.assertEqual(actual, expected)
        self.assertEqual(actual["racks"][0]["material_group_name"], "Material inactivo")
        self.assertEqual(actual["racks"][0]["area_name"], "Área inactiva")
        self.assertEqual(actual["racks"][0]["reservation_task_identifier"], "REMOTE-LATEST")

    def test_catalog_uses_four_fixed_lightweight_queries(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            result = main._catalog_out(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(len(result.racks), 3)
        self.assertEqual(len(statements), 4)
        self.assertFalse(any("status_query_log_json" in statement for statement in statements))
        self.assertEqual(sum("from movement_orders" in statement for statement in statements), 1)

    def test_initial_frontend_load_requests_catalog_once(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        init_block = app_js[app_js.index("(async function init()") : app_js.index('window.addEventListener("beforeunload"')]
        self.assertEqual(init_block.count("loadCatalog("), 1)


if __name__ == "__main__":
    unittest.main()
