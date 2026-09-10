"""FIFO Chain recovery and inter-process races on disposable SQLite only."""
import json
import multiprocessing
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import fifo_chain_service as chain
import fifo_service
import main
import monitor_service as monitor
from models import Location, MovementOrder, Rack
from rcs_client import RcsTimeoutError
import test_dispatch_backend_baseline as baseline


def process_continuation(database, parent_id, ready, start, results):
    # The safe suite's audit guard is also installed in spawned interpreters.
    import run_phase1a_suite  # noqa: F401
    engine = create_engine("sqlite:///" + database, connect_args={"timeout": 15})
    try:
        ready.put(True)
        if not start.wait(15):
            raise RuntimeError("test start timeout")
        with sessionmaker(bind=engine, autoflush=False)() as db:
            results.put(chain.trigger_fifo_chain_next_step_if_needed(db, parent_id, dispatch_func=None))
    except Exception as exc:
        results.put({"error": repr(exc)})
    finally:
        engine.dispose()


class ChainTests(unittest.TestCase):
    def setUp(self):
        baseline.DispatchBackendBaselineTests.setUp(self)
        self.client = baseline.StubRcsClient()
        original_create = self.client.create_task
        def create(task):
            result = original_create(task)
            if result.ok:
                result.data = f"REMOTE-{len(self.client.payloads)}"
                result.raw["data"] = result.data
            return result
        self.client.create_task = create
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(main, "_get_rcs_client_from_settings", return_value=self.client))
        self.patches.enter_context(patch.object(main, "_resolve_rcs_target", return_value=("stub://rcs", "/task")))
        self.patches.enter_context(patch.object(main, "_start_rack_position_poll_after_cancel"))
        self.patches.enter_context(patch.object(chain, "_default_dispatch_func", main._dispatch_movement_order))
        with self.factory() as db:
            extra = [Location(code=f"DST-{i}", x=30+i, y=30, area_id=self.ids["destination_area"], enabled=1, is_visible=1, status=0) for i in (2, 3, 4)]
            db.add_all(extra); db.commit()
            self.cells = [self.ids["source"], self.ids["destination"]] + [cell.id for cell in extra]

    def tearDown(self):
        self.patches.close()
        baseline.DispatchBackendBaselineTests.tearDown(self)

    def create_chain(self, total=4):
        with self.factory() as db:
            row = fifo_service.execute_direct_move_request(db, self.cells[0], self.cells[1])
            row.route_mode = "fifo_chain"
            row.fifo_chain_group_id = "CHAIN-TEST"
            row.fifo_chain_step = 1
            row.fifo_chain_total_steps = total
            row.fifo_chain_status = "active"
            steps = [{"step": n, "source_area_id": self.ids["destination_area"], "destination_area_id": self.ids["destination_area"], "source_cell_id": self.cells[n-1], "destination_cell_id": self.cells[n], "material_group_id": self.ids["material"], "task_typ": f"STEP-{n}"} for n in range(2, total+1)]
            row.fifo_chain_next_config_json = json.dumps({"steps": steps})
            db.commit()
            main._dispatch_movement_order(db, row)
            return row.id

    def complete(self, order_id, interrupted=False):
        with self.factory() as db:
            if interrupted:
                with patch.object(chain, "trigger_fifo_chain_next_step_if_needed"):
                    fifo_service.simulate_order_completed(db, order_id)
            else:
                fifo_service.simulate_order_completed(db, order_id)

    def recover(self, dispatch_func=chain._USE_DEFAULT_DISPATCH):
        with self.factory() as db:
            return chain.recover_fifo_chains(db, dispatch_func)

    def rows(self):
        with self.factory() as db:
            return db.execute(select(MovementOrder).where(MovementOrder.fifo_chain_group_id == "CHAIN-TEST").order_by(MovementOrder.fifo_chain_step)).scalars().all()

    def test_normal_four_steps_and_sequential_same_rack(self):
        self.create_chain()
        for step in range(1, 5):
            rows = self.rows()
            self.assertEqual(len(rows), step)
            self.assertEqual(rows[-1].status, "dispatched")
            self.complete(rows[-1].id)
        rows = self.rows()
        self.assertEqual([r.status for r in rows], ["completed"]*4)
        self.assertEqual({r.rack_id for r in rows}, {self.ids["rack"]})
        self.assertEqual(rows[-1].fifo_chain_status, "completed")
        self.recover(); self.recover()
        self.assertEqual(len(self.client.payloads), 4)
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertEqual(db.get(Location, self.cells[4]).rack_id, self.ids["rack"])

    def cancel_active(self, force):
        parent = self.create_chain()
        self.complete(parent)
        active_id = self.rows()[1].id
        with self.factory() as db:
            row = db.get(MovementOrder, active_id)
            for _ in range(2):
                main._execute_cancel_for_order(db, row, force, "RETURN")
            # Even RCS completed after a return must never continue the chain.
            monitor.apply_remote_status_to_order(db, row, "completed" if force == "1" else "cancelled")
        self.recover(); self.recover()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].status, "completed")
        self.assertTrue(all(r.fifo_chain_status == "cancelled" for r in rows))
        self.assertEqual(len(self.client.payloads), 2)
        self.assertEqual(len(self.client.cancel_payloads), 1)
        self.assertEqual(self.client.cancel_payloads[0]["taskCode"], "REMOTE-2")

    def test_cancel_active_and_repeat_stops_future_steps(self):
        self.cancel_active("0")

    def test_cancel_return_completed_does_not_continue(self):
        self.cancel_active("1")

    def test_cancel_timeout_persists_chain_stop(self):
        parent = self.create_chain()
        self.complete(parent)
        active_id = self.rows()[1].id
        with patch.object(self.client, "cancel_task_with_payload", side_effect=RcsTimeoutError("cancel timeout")), self.factory() as db:
            with self.assertRaises(main.HTTPException):
                main._execute_cancel_for_order(db, db.get(MovementOrder, active_id), "0")
        self.recover()
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[1].fifo_chain_status, "cancelled")
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_restart_completed_parent_without_child(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        self.assertEqual(len(self.rows()), 1)
        self.recover()
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[1].status, "dispatched")

    def test_repeated_restart_only_one_child_and_one_dispatch(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        for _ in range(4):
            self.recover()
        self.assertEqual([r.fifo_chain_step for r in self.rows()], [1, 2])
        self.assertEqual(len(self.client.payloads), 2)

    def test_restart_after_child_commit_before_dispatch(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        self.recover(dispatch_func=None)
        self.assertEqual(self.rows()[1].dispatch_status, "not_sent")
        self.assertEqual(len(self.client.payloads), 1)
        self.recover(); self.recover()
        self.assertEqual(self.rows()[1].dispatch_status, "success")
        self.assertEqual(len(self.client.payloads), 2)

    def test_rejected_child_compensates_and_stops(self):
        parent = self.create_chain()
        self.client.outcome = "reject"
        self.complete(parent)
        self.recover(); self.recover()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].status, "dispatch_error")
        self.assertEqual([r.fifo_chain_status for r in rows], ["dispatch_error"]*2)
        self.assertEqual(len(self.client.payloads), 2)
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")
            self.assertNotIn(self.cells[2], fifo_service._reserved_destination_cell_ids(db))

    def test_child_timeout_keeps_q1_and_reservations_no_retry(self):
        parent = self.create_chain()
        self.client.outcome = "timeout"
        self.complete(parent)
        q1 = self.rows()[1].req_code
        self.recover(); self.recover()
        child = self.rows()[1]
        self.assertEqual((child.dispatch_status, child.req_code), ("uncertain", q1))
        self.assertEqual(child.fifo_chain_status, "dispatch_uncertain")
        self.assertEqual(len(self.client.payloads), 2)
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")
            self.assertIn(self.cells[2], fifo_service._reserved_destination_cell_ids(db))

    def test_restart_after_rejection_before_compensation(self):
        class SimulatedCrash(BaseException):
            pass
        parent = self.create_chain()
        self.client.outcome = "reject"
        with patch.object(main, "_rollback_unsuccessful_order_dispatch", side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            self.complete(parent)
        child = self.rows()[1]
        self.assertEqual((child.status, child.dispatch_status), ("pending_dispatch", "error"))
        self.recover(); self.recover()
        self.assertEqual(self.rows()[1].status, "dispatch_error")
        self.assertEqual(len(self.client.payloads), 2)
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "available")

    def test_stopped_group_never_dispatches_saved_unsent_child(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        self.recover(dispatch_func=None)
        child_id = self.rows()[1].id
        with self.factory() as db:
            child = db.get(MovementOrder, child_id)
            chain.stop_fifo_chain(db, child)
            db.commit()
            chain._finish_chain_dispatch(db, child)
        self.recover()
        self.assertEqual(len(self.client.payloads), 1)
        self.assertEqual(self.rows()[1].dispatch_status, "not_sent")

    def test_uncertain_dispatch_exception_never_downgrades_to_rejection(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        def interrupted_dispatch(db, row):
            row.req_code = "Q-UNCERTAIN"
            row.dispatch_request_json = '{"reqCode":"Q-UNCERTAIN"}'
            row.dispatch_status = "uncertain"
            db.commit()
            raise RuntimeError("after possible send")
        self.recover(interrupted_dispatch)
        child = self.rows()[1]
        self.assertEqual(child.dispatch_status, "uncertain")
        self.assertEqual(child.fifo_chain_status, "dispatch_uncertain")
        with self.factory() as db:
            self.assertEqual(db.get(Rack, self.ids["rack"]).status, "reserved")

    def test_active_failed_or_unreconciled_parent_never_creates_child(self):
        parent = self.create_chain()
        for state, remote in (("dispatched", "sent"), ("in_progress", "completed"), ("failed", "failed")):
            with self.factory() as db:
                row = db.get(MovementOrder, parent)
                row.status, row.rcs_status = state, remote
                db.commit()
                result = chain.trigger_fifo_chain_next_step_if_needed(db, parent)
                self.assertEqual(result["status"], "skipped")
        self.assertEqual(len(self.rows()), 1)

    def test_selection_failure_is_retryable_after_fix(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        with self.factory() as db:
            db.get(Location, self.cells[2]).enabled = 0
            db.commit()
        self.recover()
        self.assertEqual(self.rows()[0].fifo_chain_status, "retry_needed")
        self.assertEqual(len(self.rows()), 1)
        with self.factory() as db:
            db.get(Location, self.cells[2]).enabled = 1
            db.commit()
        self.recover()
        self.assertEqual(len(self.rows()), 2)

    def test_startup_invokes_recovery_without_active_monitor_rows(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "_acquire_runtime_lease"))
            for name in ("ensure_log_file", "cleanup_legacy_schema", "init_db", "ensure_config_audit_schema", "ensure_default_settings", "ensure_special_materials", "_sync_all_location_statuses", "_auto_release_stuck_cancel_return_orders", "_start_rack_sync_scheduler", "_start_robot_monitor_cache_updater"):
                stack.enter_context(patch.object(main, name))
            stack.enter_context(patch.object(main, "SessionLocal", self.factory))
            stack.enter_context(patch.object(main, "_diagnosis_rows", return_value=[]))
            stack.enter_context(patch.object(main.app.state.task_monitor, "start"))
            main.on_startup()
        self.assertEqual(len(self.rows()), 2)

    def test_two_processes_create_exactly_one_slot(self):
        parent = self.create_chain()
        self.complete(parent, interrupted=True)
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder)/"chain-race.sqlite")
            engine = create_engine("sqlite:///" + database)
            with self.engine.connect() as src, engine.connect() as dst:
                src.connection.driver_connection.backup(dst.connection.driver_connection)
            ctx = multiprocessing.get_context("spawn")
            ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
            processes = [ctx.Process(target=process_continuation, args=(database, parent, ready, start, results)) for _ in range(2)]
            try:
                for process in processes:
                    process.start()
                for _ in processes:
                    self.assertTrue(ready.get(timeout=20))
                start.set()
                outcomes = [results.get(timeout=20) for _ in processes]
                for process in processes:
                    process.join(20)
                    self.assertEqual(process.exitcode, 0)
                self.assertTrue(all(not r.get("error") for r in outcomes), outcomes)
                with sessionmaker(bind=engine)() as db:
                    children = db.execute(select(MovementOrder).where(MovementOrder.fifo_chain_group_id == "CHAIN-TEST", MovementOrder.fifo_chain_step == 2)).scalars().all()
                    self.assertEqual(len(children), 1)
                    self.assertEqual(children[0].fifo_chain_parent_order_id, parent)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate(); process.join(5)
                ready.close(); results.close()
                engine.dispose()
