import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import scanner_service as scanner
import fifo_service
from models import Base, Area, Location, MaterialGroup, MovementOrder, Rack, ScanEvent, ScannerStation, QrActionRule, ScanTerminal
from rcs_client import RcsClient


class QrPhase1BTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="phase1b-")
        self.path = str(Path(self.directory.name) / "fixture.sqlite")
        self.engine = create_engine("sqlite:///" + self.path, connect_args={"check_same_thread": False, "timeout": 10})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        with self.factory() as db:
            source = Area(code="SRC", name="Source", is_active=1)
            destination = Area(code="DST", name="Destination", is_active=1)
            db.add_all([source, destination]); db.flush()
            for i in range(4):
                rack = Rack(code=f"R{i}", status="available", fifo_entered_at=datetime(2024, 1, 1) + timedelta(seconds=i))
                db.add(rack); db.flush()
                db.add_all([Location(x=i, y=0, code=f"S{i}", enabled=1, is_visible=1, area_id=source.id, rack_id=rack.id),
                            Location(x=i, y=1, code=f"D{i}", enabled=1, is_visible=1, area_id=destination.id)])
            for code in ("SC1", "SC2"):
                db.add(ScannerStation(scanner_code=code, name=code, default_action="fifo_request", source_area_id=source.id,
                                      destination_area_id=destination.id, fifo_material_policy="any_available_from_source", allow_execute=1))
            for code in ("QR1", "QR2"):
                db.add(QrActionRule(qr_value=code, action_type="fifo_request", fifo_material_policy="any_available_from_source"))
            db.commit()
        self.calls = []

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def dispatch(self, db, order):
        self.calls.append(order.id)
        return SimpleNamespace(dispatch_status="success", rcs_message="stub", remote_task_code=f"T{order.id}")

    def execute(self, qr="QR1", station="SC1", dispatch=None):
        with self.factory() as db:
            return scanner.execute_scan(db, station, qr, dispatch_func=dispatch or self.dispatch)

    def operational_snapshot(self, db):
        return {table.name: db.execute(select(table)).all() for table in Base.metadata.sorted_tables if table.name != ScanEvent.__tablename__}

    def test_A_B_C_preview_missing_and_inactive_material_has_no_operational_effect(self):
        for inactive in (False, True):
            with self.subTest(inactive=inactive), self.factory() as db:
                if inactive:
                    db.add(MaterialGroup(code="SIN_MATERIAL", name="", is_active=0)); db.commit()
                before = self.operational_snapshot(db)
                with patch.object(RcsClient, "create_task", side_effect=AssertionError("Preview sent RCS")) as rcs, patch.object(fifo_service, "apply_transition_for_completed_order") as transition:
                    result = scanner.preview_scan(db, "SC1", "QR1")
                    self.assertTrue(result["ok"], result)
                    rcs.assert_not_called()
                    transition.assert_not_called()
                self.assertEqual(self.operational_snapshot(db), before)
                self.assertEqual(db.scalars(select(MovementOrder)).all(), [])
                self.assertTrue(all(rack.status == "available" for rack in db.scalars(select(Rack))))
                self.assertEqual(fifo_service._reserved_destination_cell_ids(db), set())
                self.assertIsNone(scanner._recent_duplicate_scan_event(db, "SC1", "QR1"))
                self.assertNotIn("qr_preview_read_only", db.info)

    def test_D_simultaneous_same_qr_only_one_execution(self):
        entered, release = threading.Event(), threading.Event()
        def slow_dispatch(db, order):
            entered.set()
            self.assertTrue(release.wait(20))
            return self.dispatch(db, order)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, dispatch=slow_dispatch)
            try:
                self.assertTrue(entered.wait(15), first.result() if first.done() else "First execution did not reach the stub")
                second = pool.submit(self.execute).result(timeout=10)
                self.assertEqual(second["status"], "duplicate", second)
                from main import ScanPreviewOut
                self.assertEqual(ScanPreviewOut(**second).status, "duplicate")
            finally:
                release.set()
            self.assertTrue(first.result(timeout=10)["ok"])
        with self.factory() as db:
            self.assertEqual(len(db.scalars(select(MovementOrder)).all()), 1)
        self.assertEqual(len(self.calls), 1)

    def test_E_one_second_is_duplicate(self):
        self.assertTrue(self.execute()["ok"])
        with self.factory() as db:
            event = db.scalars(select(ScanEvent).where(ScanEvent.mode == "execute")).first()
            event.created_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.execute()["status"], "duplicate")
        self.assertEqual(len(self.calls), 1)

    def test_F_eight_seconds_permits_new_execution(self):
        self.assertTrue(self.execute()["ok"])
        with self.factory() as db:
            event = db.scalars(select(ScanEvent).where(ScanEvent.mode == "execute")).first()
            event.created_at = datetime.utcnow() - timedelta(seconds=8)
            db.commit()
        result = self.execute()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.calls), 2)

    def test_distinct_qrs_and_scanners_keep_independent_dedupe(self):
        for qr, station in (("QR1", "SC1"), ("QR2", "SC1"), ("QR1", "SC2")):
            result = self.execute(qr, station)
            self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.calls), 3)

    def test_lock_is_shared_across_processes(self):
        root = Path(__file__).resolve().parents[1]
        script = """
import runpy, sys
runpy.run_path(sys.argv[1])
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from scanner_service import execute_scan
engine = create_engine('sqlite:///' + sys.argv[2])
with Session(engine) as db:
    result = execute_scan(db, 'SC1', 'QR1', dispatch_func=lambda *args: (_ for _ in ()).throw(AssertionError('duplicate executed')))
    assert result['status'] == 'duplicate', result
engine.dispose()
"""
        with self.factory() as db, scanner._scan_execution_lock(db, "SC1", "QR1") as acquired:
            self.assertTrue(acquired)
            result = subprocess.run([sys.executable, "-B", "-c", script, str(root / "tests/run_phase1a_suite.py"), self.path], cwd=root, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_preview_does_not_block_following_execute(self):
        with self.factory() as db:
            self.assertTrue(scanner.preview_scan(db, "SC1", "QR1")["ok"])
        result = self.execute()
        self.assertTrue(result["ok"], result)

    def test_terminal_preview_only_persists_scan_event(self):
        with self.factory() as db:
            station = db.scalars(select(ScannerStation).where(ScannerStation.scanner_code == "SC1")).one()
            db.add(ScanTerminal(terminal_code="PDA1", name="PDA1", scanner_station_id=station.id))
            db.commit()
            before = self.operational_snapshot(db)
            result = scanner.terminal_preview_scan(db, "PDA1", "QR1", client_ip="127.0.0.1", dispatch_func=self.dispatch)
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.operational_snapshot(db), before)
            self.assertEqual(self.calls, [])

    def test_different_keys_can_execute_while_first_is_in_flight(self):
        entered, release = threading.Event(), threading.Event()
        def slow_dispatch(db, order):
            entered.set()
            self.assertTrue(release.wait(30))
            return self.dispatch(db, order)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, dispatch=slow_dispatch)
            try:
                self.assertTrue(entered.wait(15), first.result() if first.done() else "First execution did not reach the stub")
                self.assertTrue(pool.submit(self.execute, "QR2", "SC1").result(timeout=5)["ok"])
                self.assertTrue(pool.submit(self.execute, "QR1", "SC2").result(timeout=5)["ok"])
            finally:
                release.set()
            self.assertTrue(first.result(timeout=5)["ok"])
        self.assertEqual(len(self.calls), 3)

    def test_order_serializers_expose_existing_update_timestamp(self):
        import main
        self.assertTrue(self.execute()["ok"])
        with self.factory() as db:
            order = db.scalars(select(MovementOrder)).one()
            detail = main._movement_order_out_read_optimized(db, order)
            summary = main._movement_order_summary_out(order, rack_by_id={}, location_by_id={}, area_by_id={}, material_by_id={}, current_location_by_rack_id={})
            self.assertEqual(detail.updated_at, order.updated_at)
            self.assertEqual(summary.updated_at, order.updated_at)
            self.assertIn(MovementOrder.updated_at, main.MOVEMENT_ORDER_SUMMARY_COLUMNS)
