"""Isolated SQLite inventory editing. Never starts the application or its RCS workers."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, select

import main
from models import Location, Rack, MaterialGroup, MovementOrder
import test_dispatch_backend_baseline as baseline


class ContextCellEditorTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown

    def snapshot(self, location=None, rack=None):
        with patch.object(main, 'SessionLocal', self.factory):
            return main.get_context_cell(location or self.ids['source'], rack)

    def save(self, snapshot=None, **kwargs):
        snapshot = snapshot or self.snapshot()
        body = main.ContextCellSave(expected_location=snapshot['expected_location'], expected_racks=snapshot['expected_racks'], **kwargs)
        with patch.object(main, 'SessionLocal', self.factory), patch.object(main, 'require_admin'), patch.object(main, '_get_rcs_client_from_settings', side_effect=AssertionError('Real RCS forbidden')):
            return main.save_context_cell(snapshot['location']['id'], body, 'test')

    def test_joint_save_updates_only_requested_fields(self):
        snapshot = self.snapshot()
        result = self.save(snapshot, location={'code': 'NEW', 'enabled': 0}, rack_id=self.ids['rack'], rack={'name': 'New name', 'quantity': 7})
        self.assertEqual(result['location']['code'], 'NEW')
        self.assertEqual(result['location']['enabled'], 0)
        self.assertEqual(result['racks'][0]['quantity'], 7)
        self.assertEqual(result['racks'][0]['material_group_id'], self.ids['material'])
        self.assertEqual(result['racks'][0]['fifo_entered_at'], snapshot['racks'][0]['fifo_entered_at'])
        self.assertEqual(result['location']['area_id'], self.ids['source_area'])

    def test_simplified_view_partial_contract_preserves_admin_configuration(self):
        with self.factory() as db:
            db.get(Rack, self.ids['rack']).rack_custom_fields_json = '[{"key":"quality","label":"Calidad","value":"A"}]'
            db.commit()
        snapshot = self.snapshot()
        result = self.save(snapshot, location={}, rack_id=self.ids['rack'], rack={'lot': 'USER-LOT', 'material_group_id': self.ids['material']}, reserved=1)
        for key in ('code', 'area_id', 'enabled', 'is_visible', 'note', 'status', 'x', 'y'):
            self.assertEqual(result['location'][key], snapshot['location'][key], key)
        for key in ('code', 'name', 'quantity', 'manufacturer_code', 'fifo_entered_at', 'last_moved_at', 'comment', 'custom_fields'):
            self.assertEqual(result['racks'][0][key], snapshot['racks'][0][key], key)
        self.assertEqual(result['racks'][0]['lot'], 'USER-LOT')
        self.assertEqual(result['racks'][0]['reservation_status'], 'Reservado')

    def test_authorization_precedes_database_access(self):
        with patch.object(main, 'SessionLocal', side_effect=AssertionError('Unauthorized DB access')):
            with self.assertRaises(HTTPException) as caught:
                main.save_context_cell(1, main.ContextCellSave(expected_location='x'), None)
        self.assertEqual(caught.exception.status_code, 401)

    def test_location_conflict_including_unversioned_external_change(self):
        snapshot = self.snapshot()
        with self.factory() as db:
            db.get(Location, self.ids['source']).note = 'Another session'
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'code': 'overwrite'})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.snapshot()['location']['note'], 'Another session')

    def test_rack_conflict_and_assignment_conflict(self):
        snapshot = self.snapshot()
        with self.factory() as db:
            db.get(Rack, self.ids['rack']).quantity = 15
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'note': 'stale'}, rack_id=self.ids['rack'], rack={'quantity': 3})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNone(self.snapshot()['location']['note'])
        snapshot = self.snapshot()
        with self.factory() as db:
            db.get(Location, self.ids['source']).rack_id = None
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'note': 'stale'})
        self.assertEqual(caught.exception.status_code, 409)

    def test_missing_rack_baseline_rejected(self):
        snapshot = self.snapshot()
        snapshot['expected_racks'] = {}
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'note': 'no token'})
        self.assertEqual(caught.exception.status_code, 409)

    def test_target_rack_assignment_changed_after_read_returns_409(self):
        with self.factory() as db:
            row = Rack(code='UNASSIGNED', material_group_id=self.ids['material'])
            db.add(row); db.commit(); rack_id = row.id
        snapshot = self.snapshot(self.ids['destination'], rack_id)
        # Simulate an assignment writer that does not update Rack.updated_at.
        with self.factory() as db:
            db.get(Location, self.ids['source']).rack_id = rack_id
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'rack_id': rack_id})
        self.assertEqual(caught.exception.status_code, 409)

    def test_two_sqlite_writers_cannot_both_save_same_baseline(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from threading import Barrier
        from concurrent.futures import ThreadPoolExecutor
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'concurrency.sqlite'
            source = self.engine.raw_connection()
            target = sqlite3.connect(str(filename))
            try:
                source.driver_connection.backup(target)
            finally:
                source.close(); target.close()
            engine = create_engine(f'sqlite:///{filename.as_posix()}', connect_args={'check_same_thread': False, 'timeout': 5})
            factory = sessionmaker(bind=engine, autoflush=False)
            barrier = Barrier(2)
            def writer(note):
                barrier.wait(timeout=5)
                body = main.ContextCellSave(expected_location=snapshot['expected_location'], expected_racks=snapshot['expected_racks'], location={'note': note})
                try:
                    main.save_context_cell(self.ids['source'], body, 'test')
                    return 200
                except HTTPException as exc:
                    return exc.status_code
            try:
                with patch.object(main, 'SessionLocal', factory), patch.object(main, 'require_admin'), ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(writer, ['session one', 'session two']))
                self.assertEqual(sorted(results), [200, 409])
            finally:
                engine.dispose()

    def test_invalid_rack_rolls_back_location(self):
        with self.assertRaises(HTTPException):
            self.save(location={'code': 'partial'}, rack_id=self.ids['rack'], rack={'material_group_id': 9999})
        self.assertEqual(self.snapshot()['location']['code'], 'SRC-01')

    def test_invalid_location_rolls_back_rack(self):
        with self.assertRaises(HTTPException):
            self.save(location={'area_id': 9999}, rack_id=self.ids['rack'], rack={'quantity': 20})
        self.assertEqual(self.snapshot()['racks'][0]['quantity'], 1)

    def test_failure_after_flush_rolls_back_everything_and_publishes_nothing(self):
        snapshot = self.snapshot()
        def fail_after_changes(db, location, racks):
            self.assertEqual(db.get(Rack, self.ids['rack']).quantity, 20)
            raise RuntimeError('serialization failure before commit')
        with patch.object(main, '_context_cell_payload', fail_after_changes), self.assertRaises(RuntimeError):
            self.save(snapshot, location={'code': 'partial'}, rack_id=self.ids['rack'], rack={'quantity': 20})
        self.assertEqual(self.snapshot()['location']['code'], 'SRC-01')
        self.assertEqual(self.snapshot()['racks'][0]['quantity'], 1)

    def test_no_op_does_not_issue_update_insert_or_commit(self):
        snapshot = self.snapshot()
        writes = []
        def statement(_conn, _cursor, sql, *_args):
            if sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')):
                writes.append(sql)
        event.listen(self.engine, 'before_cursor_execute', statement)
        try:
            result = self.save(snapshot, location={'code': 'SRC-01'}, rack_id=self.ids['rack'], rack={'quantity': 1})
        finally:
            event.remove(self.engine, 'before_cursor_execute', statement)
        self.assertEqual(writes, [])
        self.assertEqual(result['expected_location'], snapshot['expected_location'])

    def test_detach_and_mark_free_together_and_nullable_fields(self):
        result = self.save(location={'rack_id': None, 'area_id': None, 'status': 0, 'note': None})
        self.assertIsNone(result['location']['rack_id'])
        self.assertIsNone(result['location']['area_id'])
        self.assertEqual(result['location']['status'], 0)
        self.assertIsNone(result['racks'][0]['location_x'])

    def test_assigned_rack_cannot_be_marked_free(self):
        with self.assertRaises(HTTPException) as caught:
            self.save(location={'status': 0})
        self.assertEqual(caught.exception.status_code, 409)

    def test_exclusive_rack_assignment(self):
        snapshot = self.snapshot(self.ids['destination'], self.ids['rack'])
        with self.assertRaises(HTTPException) as caught:
            self.save(snapshot, location={'rack_id': self.ids['rack']})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIsNone(self.snapshot(self.ids['destination'])['location']['rack_id'])

    def test_active_orders_protect_location_rack_and_reservation(self):
        with self.factory() as db:
            db.add(MovementOrder(order_code='ACTIVE', source_area_id=self.ids['source_area'], destination_area_id=self.ids['destination_area'], material_group_id=self.ids['material'], rack_id=self.ids['rack'], source_cell_id=self.ids['source'], destination_cell_id=self.ids['destination'], status='dispatched'))
            db.get(Rack, self.ids['rack']).status = 'reserved'
            db.commit()
        for change in ({'location': {'note': 'blocked'}}, {'rack_id': self.ids['rack'], 'rack': {'quantity': 2}}, {'rack_id': self.ids['rack'], 'reserved': 0}):
            with self.subTest(change=change), self.assertRaises(HTTPException) as caught:
                self.save(**change)
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.snapshot()['racks'][0]['status'], 'reserved')

    def test_reserved_rack_cannot_be_detached(self):
        with self.factory() as db:
            db.get(Rack, self.ids['rack']).status = 'reserved'
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            self.save(location={'rack_id': None})
        self.assertEqual(caught.exception.status_code, 409)

    def test_reservation_without_active_order_is_atomic_and_audited(self):
        result = self.save(location={'note': 'reserved here'}, rack_id=self.ids['rack'], reserved=1)
        self.assertEqual(result['racks'][0]['status'], 'reserved')
        result = self.save(location={'note': 'released here'}, rack_id=self.ids['rack'], reserved=0)
        self.assertEqual(result['racks'][0]['status'], 'available')

    def test_no_material_initialization_has_no_independent_commit(self):
        snapshot = self.snapshot()
        # Ensure SIN_MATERIAL is absent, then fail after its creation and both edits.
        with self.factory() as db:
            self.assertIsNone(db.execute(select(MaterialGroup).where(MaterialGroup.code == main.NO_MATERIAL_CODE)).scalar_one_or_none())
        with patch.object(main, '_context_cell_payload', side_effect=RuntimeError('rollback')), self.assertRaises(RuntimeError):
            self.save(snapshot, location={'code': 'partial'}, rack_id=self.ids['rack'], rack={'material_group_id': None})
        with self.factory() as db:
            self.assertIsNone(db.execute(select(MaterialGroup).where(MaterialGroup.code == main.NO_MATERIAL_CODE)).scalar_one_or_none())
            self.assertEqual(db.get(Rack, self.ids['rack']).material_group_id, self.ids['material'])
        self.assertEqual(self.snapshot()['location']['code'], 'SRC-01')
        result = self.save(rack_id=self.ids['rack'], rack={'material_group_id': None})
        with self.factory() as db:
            self.assertEqual(db.get(MaterialGroup, result['racks'][0]['material_group_id']).code, main.NO_MATERIAL_CODE)

    def test_inactive_material_and_duplicate_codes_rejected(self):
        with self.factory() as db:
            material = MaterialGroup(code='OFF', name='Inactive', is_active=0)
            other = Rack(code='DUP', material_group_id=self.ids['material'])
            db.add_all([material, other]); db.commit(); inactive_id = material.id
        for changes in ({'material_group_id': inactive_id}, {'code': 'DUP'}, {'code': '   '}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                self.save(location={'note': 'partial'}, rack_id=self.ids['rack'], rack=changes)
        self.assertIsNone(self.snapshot()['location']['note'])

    def test_reject_unknown_fields_and_null_flags(self):
        for changes in ({'material_id': 1}, {'x': 5}, {'enabled': None}, {'status': 2}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                self.save(location=changes)
        with self.assertRaises(HTTPException):
            self.save(rack_id=self.ids['rack'], rack={'status': 'available'})
        for changes in ({'quantity': None}, {'quantity': -1}, {'custom_fields': None}, {'code': 123}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as caught:
                self.save(rack_id=self.ids['rack'], rack=changes)
            self.assertEqual(caught.exception.status_code, 422)

    def test_commit_events_are_incremental_and_deduplicated(self):
        # Test factories normally omit production listeners: attach precisely those hooks.
        listeners = [('after_flush', main._capture_runtime_session_changes), ('after_commit', main._publish_runtime_session_changes), ('after_rollback', main._discard_runtime_session_changes)]
        for name, handler in listeners:
            event.listen(self.factory, name, handler)
        try:
            with patch.object(main, '_queue_runtime_change') as queued:
                self.save(location={'code': 'EVENT'}, rack_id=self.ids['rack'], rack={'quantity': 4})
            events = [call.args[0] for call in queued.call_args_list]
            self.assertEqual(sorted(item['kind'] for item in events), ['location', 'rack'])
            snapshot = self.snapshot()
            with patch.object(main, '_queue_runtime_change') as queued, patch.object(main, '_context_cell_payload', side_effect=RuntimeError('rollback')), self.assertRaises(RuntimeError):
                self.save(snapshot, location={'code': 'FAIL'}, rack_id=self.ids['rack'], rack={'quantity': 9})
            queued.assert_not_called()
            with patch.object(main, '_queue_runtime_change') as queued:
                result = self.save(location={'rack_id': None, 'status': 0})
            events = [call.args[0] for call in queued.call_args_list]
            self.assertEqual(sorted(item['kind'] for item in events), ['location', 'rack'])
            self.assertIsNone(result['racks'][0]['location_x'])
            self.assertNotEqual(result['racks'][0]['updated_at'], snapshot['racks'][0]['updated_at'])
        finally:
            for name, handler in listeners:
                event.remove(self.factory, name, handler)


if __name__ == '__main__':
    unittest.main()
