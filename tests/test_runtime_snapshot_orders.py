import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Area, Base, Location, MaterialGroup, MovementOrder, Rack


class RuntimeSnapshotOrderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        area = Area(code="A", name="Area", is_active=1)
        material = MaterialGroup(code="M", name="Material", is_active=1)
        rack = Rack(code="R", status="reserved", material_group_id=1)
        self.db.add_all([area, material, rack])
        self.db.flush()
        source = Location(x=1, y=1, code="SRC", area_id=area.id, rack_id=rack.id)
        destination = Location(x=2, y=1, code="DST", area_id=area.id)
        self.db.add_all([source, destination])
        self.db.flush()
        now = datetime.utcnow()
        for index in range(105):
            self.db.add(MovementOrder(
                order_code=f"DONE-{index:03d}",
                source_area_id=area.id,
                destination_area_id=area.id,
                material_group_id=material.id,
                rack_id=rack.id,
                source_cell_id=source.id,
                destination_cell_id=destination.id,
                status="completed",
                status_query_log_json='[{"payload":"heavy-history-marker"}]',
                created_at=now - timedelta(minutes=index + 10),
                updated_at=now,
            ))
        for index, status in enumerate(("dispatched", "cancel_error")):
            self.db.add(MovementOrder(
                order_code=f"ACTIVE-{index}",
                source_area_id=area.id,
                destination_area_id=area.id,
                material_group_id=material.id,
                rack_id=rack.id,
                source_cell_id=source.id,
                destination_cell_id=destination.id,
                status=status,
                status_query_log_json='[{"payload":"active-heavy-history-marker"}]',
                created_at=now - timedelta(days=10 + index),
                updated_at=now,
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_runtime_returns_all_active_and_only_recent_terminal_summaries(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            items = main._list_movement_orders_out(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(len(items), main.RUNTIME_RECENT_ORDER_LIMIT + 2)
        self.assertEqual({item.status for item in items if item.status != "completed"}, {"dispatched", "cancel_error"})
        payload = [item.model_dump(mode="json") for item in items]
        self.assertNotIn("status_query_log_entries", payload[0])
        self.assertFalse(any("status_query_log_json" in statement.lower() for statement in statements))

    def test_paginated_list_is_light_and_detail_preserves_history(self):
        page = main._list_movement_orders_page_out(self.db, limit=25, offset=25)
        self.assertEqual(page.total, 107)
        self.assertEqual(len(page.items), 25)
        self.assertEqual(page.limit, 25)
        self.assertEqual(page.offset, 25)

        row = self.db.query(MovementOrder).filter(MovementOrder.order_code == "DONE-000").one()
        detail = main._movement_order_detail_out(self.db, row)
        self.assertEqual(detail.status_query_log_entries[0]["payload"], "heavy-history-marker")

    def test_complete_pagination_has_no_missing_or_duplicate_orders(self):
        page_size = 23
        collected = []
        offset = 0
        expected_total = self.db.query(MovementOrder).count()
        while offset < expected_total:
            page = main._list_movement_orders_page_out(self.db, limit=page_size, offset=offset)
            self.assertEqual(page.total, expected_total)
            collected.extend(item.order_id for item in page.items)
            offset += len(page.items)
            self.assertTrue(page.items or offset >= expected_total)
        authoritative = [row[0] for row in self.db.query(MovementOrder.id).order_by(MovementOrder.created_at.desc(), MovementOrder.id.desc()).all()]
        self.assertEqual(collected, authoritative)
        self.assertEqual(len(collected), len(set(collected)))


if __name__ == "__main__":
    unittest.main()
