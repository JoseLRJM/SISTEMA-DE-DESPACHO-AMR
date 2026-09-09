import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Base


class RobotMonitorCacheTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        with main._robot_monitor_cache_lock:
            main._robot_monitor_cache.update(timestamp=0.0, payload=None, last_error="")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        with main._robot_monitor_cache_lock:
            main._robot_monitor_cache.update(timestamp=0.0, payload=None, last_error="")

    def _store_robot(self):
        response = main.RobotMonitorResponse(
            ok=True,
            code="0",
            message="",
            reqCode="cached-request",
            robots=[main.RobotMonitorItem(robotCode="AMR-01")],
        )
        with main._robot_monitor_cache_lock:
            main._robot_monitor_cache.update(
                timestamp=time.time(),
                payload=response.model_dump(),
                last_error="",
            )

    def test_runtime_snapshot_reads_cache_without_calling_rcs(self):
        self._store_robot()

        def slow_rcs_call(_db):
            time.sleep(0.5)
            raise AssertionError("runtime snapshot must never call RCS")

        started = time.perf_counter()
        with patch.object(main, "_get_robot_status_monitor_response", side_effect=slow_rcs_call), \
             patch.object(main, "_maybe_auto_release_stuck_cancel_return_orders"):
            snapshot = main._build_runtime_snapshot_out(self.db, include_robot_monitor=1)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.25)
        self.assertEqual(snapshot.robot_monitor.robots[0].robotCode, "AMR-01")
        self.assertTrue(snapshot.robot_monitor.cacheAvailable)

    def test_empty_or_stale_cache_never_triggers_rcs(self):
        with patch.object(main, "_get_robot_status_monitor_response") as query:
            empty = main._get_cached_robot_status_monitor_response(self.db, force=False)
            query.assert_not_called()
        self.assertFalse(empty.cacheAvailable)

        self._store_robot()
        with main._robot_monitor_cache_lock:
            main._robot_monitor_cache["timestamp"] = time.time() - 3600
        with patch.object(main, "_get_robot_status_monitor_response") as query:
            stale = main._get_cached_robot_status_monitor_response(self.db, force=False, max_age_seconds=5)
            query.assert_not_called()
        self.assertTrue(stale.cacheAvailable)
        self.assertTrue(stale.stale)

    def test_force_refresh_updates_cache(self):
        fresh = main.RobotMonitorResponse(
            ok=True,
            code="0",
            reqCode="fresh-request",
            robots=[main.RobotMonitorItem(robotCode="AMR-02")],
        )
        with patch.object(main, "_get_robot_status_monitor_response", return_value=fresh) as query:
            result = main._get_cached_robot_status_monitor_response(self.db, force=True)
            query.assert_called_once_with(self.db)
        self.assertEqual(result.robots[0].robotCode, "AMR-02")
        self.assertTrue(result.cacheAvailable)

    def test_failed_force_refresh_preserves_last_known_state(self):
        self._store_robot()
        failure = main.RobotMonitorResponse(ok=False, message="RCS no disponible", robots=[])
        with patch.object(main, "_get_robot_status_monitor_response", return_value=failure):
            result = main._get_cached_robot_status_monitor_response(self.db, force=True)
        self.assertEqual(result.robots[0].robotCode, "AMR-01")
        self.assertIn("RCS no disponible", result.message)

    def test_single_flight_returns_cache_when_refresh_is_already_running(self):
        self._store_robot()
        main._robot_monitor_refresh_lock.acquire()
        try:
            with patch.object(main, "_get_robot_status_monitor_response") as query:
                result = main._get_cached_robot_status_monitor_response(self.db, force=True)
                query.assert_not_called()
        finally:
            main._robot_monitor_refresh_lock.release()
        self.assertTrue(result.refreshing)
        self.assertEqual(result.robots[0].robotCode, "AMR-01")

    def test_frontend_startup_uses_cached_refresh_and_manual_button_forces(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        init_block = app_js[app_js.index("(async function init()") : app_js.index("window.addEventListener(\"beforeunload\"")]
        self.assertNotIn("refreshRobotMonitor({ force: true })", init_block)
        self.assertIn("refreshRobotMonitor({ force: false })", init_block)
        self.assertIn("btnRobotMonitorRefresh?.addEventListener('click', () => { refreshRobotMonitor({ force: true })", app_js)


if __name__ == "__main__":
    unittest.main()
