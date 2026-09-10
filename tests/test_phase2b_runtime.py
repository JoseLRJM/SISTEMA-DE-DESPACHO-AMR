"""Runtime coordination: local OS leases, disposable SQLite and JS cache contracts."""
import asyncio
import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run_phase1a_suite  # Protect spawned interpreters before application imports.
import main
from runtime_single_worker import RuntimeLease, RuntimeLeaseMiddleware, validate_worker_configuration

ROOT = Path(__file__).resolve().parents[1]


def remote_observer(database, stale, loaded, completed, results):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import MovementOrder
    from monitor_service import apply_remote_status_to_order
    engine = create_engine("sqlite:///" + database, connect_args={"timeout": 15})
    try:
        with sessionmaker(bind=engine, autoflush=False)() as db:
            row = db.get(MovementOrder, 1)
            if stale:
                loaded.set()
                if not completed.wait(25):
                    raise RuntimeError("completion timeout")
                apply_remote_status_to_order(db, row, "in_progress")
            else:
                if not loaded.wait(25):
                    raise RuntimeError("observer timeout")
                apply_remote_status_to_order(db, row, "completed")
                completed.set()
            db.commit()
            results.put(row.status)
    except Exception as exc:
        results.put(repr(exc))
    finally:
        engine.dispose()


class SingleWorkerTests(unittest.TestCase):
    def test_worker_configuration_rejects_multiple_workers(self):
        for argv, env in ((["--workers", "2"], {}), (["--workers=5"], {}), (["-w", "3"], {}), ([], {"WEB_CONCURRENCY": "2"})):
            with self.assertRaises(RuntimeError):
                validate_worker_configuration(argv, env)
        validate_worker_configuration(["--workers", "1"], {"WEB_CONCURRENCY": "1"})

    def test_second_process_cannot_own_runtime_then_can_after_exit(self):
        script = """
import sys
from runtime_single_worker import RuntimeLease
try:
    lease = RuntimeLease(sys.argv[1]); lease.acquire()
except RuntimeError:
    sys.exit(42)
assert lease.owned
"""
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "isolated.sqlite")
            lease = RuntimeLease(database)
            try:
                lease.acquire()
                other = subprocess.run([sys.executable, "-B", "-c", script, database], cwd=ROOT, timeout=15)
                self.assertEqual(other.returncode, 42)
            finally:
                lease.close()
            other = subprocess.run([sys.executable, "-B", "-c", script, database], cwd=ROOT, timeout=15)
            self.assertEqual(other.returncode, 0)

    def test_missing_lease_blocks_http_and_websocket(self):
        async def exercise(kind):
            messages = []
            async def send(message):
                messages.append(message)
            app = Mock()
            guard = RuntimeLeaseMiddleware(app, Mock(owned=False))
            await guard({"type": kind}, None, send)
            app.assert_not_called()
            return messages
        self.assertEqual(asyncio.run(exercise("http"))[0]["status"], 503)
        self.assertEqual(asyncio.run(exercise("websocket"))[0]["code"], 1013)

    def test_startup_claim_precedes_any_database_initialization(self):
        with patch.object(main, "_acquire_runtime_lease", side_effect=RuntimeError("other worker")), patch.object(main, "init_db") as initialize, patch.object(main, "cleanup_legacy_schema") as migrate:
            with self.assertRaises(RuntimeError):
                main.on_startup()
            initialize.assert_not_called()
            migrate.assert_not_called()

    def test_owned_but_incomplete_startup_does_not_serve(self):
        async def exercise():
            messages = []
            async def send(message): messages.append(message)
            app = Mock()
            await RuntimeLeaseMiddleware(app, Mock(owned=True, ready=False))({"type": "http"}, None, send)
            app.assert_not_called()
            self.assertEqual(messages[0]["status"], 503)
        asyncio.run(exercise())

    def test_two_processes_do_not_reopen_terminal_order(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from models import Base, MovementOrder, Location, Rack, Area, MaterialGroup
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "orders.sqlite")
            engine = create_engine("sqlite:///" + path)
            Base.metadata.create_all(engine)
            with sessionmaker(bind=engine)() as db:
                db.add_all([Area(id=1, code="AREA", name="Area"), MaterialGroup(id=1, code="MAT", name="Material"),
                    Rack(id=1, code="R1", status="reserved", material_group_id=1),
                    Location(id=1, x=1, y=1, code="S", rack_id=1, area_id=1, enabled=1),
                    Location(id=2, x=2, y=1, code="D", area_id=1, enabled=1),
                    MovementOrder(id=1, order_code="TEST", rack_id=1, source_area_id=1, destination_area_id=1,
                        material_group_id=1, source_cell_id=1, destination_cell_id=2, status="dispatched", dispatch_status="success", remote_task_code="T1", req_code="Q1")])
                db.commit()
            engine.dispose()
            ctx = multiprocessing.get_context("spawn")
            loaded, completed, results = ctx.Event(), ctx.Event(), ctx.Queue()
            processes = [ctx.Process(target=remote_observer, args=(path, stale, loaded, completed, results)) for stale in (True, False)]
            try:
                for process in processes: process.start()
                for process in processes:
                    process.join(30)
                    self.assertEqual(process.exitcode, 0)
                self.assertEqual([results.get(timeout=5) for _ in processes], ["completed", "completed"])
            finally:
                for process in processes:
                    if process.is_alive(): process.terminate(); process.join(5)
                results.close()

    def test_launchers_explicitly_use_one_worker(self):
        batch = (ROOT / "run.cmd").read_text()
        portable = (ROOT / "run_server_silent.py").read_text()
        self.assertNotIn("5 workers", batch)
        self.assertIn("--workers 1 --lifespan on", batch)
        self.assertIn("workers=1", portable)
        self.assertIn('lifespan="on"', portable)

    def test_snapshot_racing_with_changes_is_not_certified(self):
        with patch.object(main, "_locations_current_revision", side_effect=[100, 101, 102, 103, 104, 105]), patch.object(main, "_build_runtime_snapshot_payload", return_value={"locations": []}):
            result = main._build_runtime_snapshot_message(None)
        self.assertEqual(result["type"], "runtime_resync_required")
        self.assertNotIn("locations_revision", result)


class CacheCheckpointTests(unittest.TestCase):
    def run_js(self, assertions):
        source = (ROOT / "static/app.js").read_text(encoding="utf-8")
        cache = source[source.index("function locationsCacheRowsAreValid"):source.index("function logLocationsCacheMetric")]
        handler = source[source.index("async function applyRuntimeIncrementalMessage"):source.index("function connectRuntimeSocket")]
        resync = source[source.index("function requestRuntimeResync"):source.index("function applyRobotMonitorUpdated")]
        socket_start = source.index("function connectRuntimeSocket")
        socket = source[socket_start:source.index("document.addEventListener('click', async (ev)", socket_start)]
        script = """
const assert = require('assert');
const LOCATIONS_CACHE_SCHEMA_VERSION = 1;
let stores = {locations:new Map(), metadata:new Map()};
const clone = value => value === undefined ? undefined : structuredClone(value);
const db = {transaction(names, mode) {
  const copies = mode === 'readwrite' ? {locations:clone(stores.locations), metadata:clone(stores.metadata)} : stores;
  return {copies, mode, aborted:false, abort(){this.aborted=true;}, objectStore(name){
    const map = copies[name];
    return {get:key=>clone(map.get(key)), getAll:()=>[...map.values()].map(clone),
      put:(value,key)=>map.set(key ?? value.id,clone(value)), delete:key=>map.delete(key), clear:()=>map.clear()};
  }};
}};
async function openLocationsCacheDb(){return db;}
async function idbRequest(value){return value;}
async function waitForIdbTransaction(tx){if(tx.aborted)throw Error('aborted');if(tx.mode==='readwrite')stores=tx.copies;}
let runtimeRevision=1, locationsRevision=100, locationsLoaded=true, runtimeResyncPending=false;
let runtimeMessageChain=Promise.resolve(), runtimeSocketConnected=false;
const runtimeMetricsEnabled=false, selectedOrderId=null, API={runtimeWs:'ws://test'};
const sent=[];
class WebSocket {
  static OPEN=1;
  constructor(url){this.url=url;this.readyState=1;this.handlers={};}
  addEventListener(type,handler){this.handlers[type]=handler;}
  send(value){sent.push(JSON.parse(value));}
  emit(message){this.handlers.message({data:JSON.stringify(message)});}
}
let runtimeSocket=new WebSocket('ws://test');
function cleanupRuntimeSocket(){runtimeSocket=null;}
function setConn(){} function scheduleRuntimeSocketReconnect(){}
let appliedLocations=[];
function applyRuntimeSnapshotData(payload){appliedLocations=payload.locations;}
function applyLocationUpdated(){} function applyLocationDeleted(){}
const A={id:1,x:1,y:1,code:'A'}, B={id:2,x:2,y:1,code:'B'};
""" + cache + handler + resync + socket + "\n(async()=>{ await writeLocationsCache([A,B],100);\n" + assertions + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_event_never_confirms_global_revision(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'location_updated',revision:2,payload:{location:{...A,code:'A2'},locations_revision:200}});
assert.equal(locationsRevision,null);
assert.equal(stores.metadata.get('state').locationsRevision,100);
assert.equal(stores.metadata.get('state').complete,false);
assert.equal(sent.length,1); assert.equal(sent[0].has_locations,false);
""")

    def test_browser_restart_rejects_partial_cache(self):
        self.run_js("""
await updateLocationsCacheRecord({...A,code:'A2'},200);
assert.equal(await readLocationsCache(200,2),null);
assert.equal(await readLocationsCache(100,2),null);
""")

    def test_full_snapshot_confirms_all_rows_atomically(self):
        self.run_js("""
await updateLocationsCacheRecord({...A,code:'A2'},200);
assert.equal(await writeLocationsCache([{...A,code:'A2'},{...B,code:'B2'}],200),true);
assert.equal(stores.metadata.get('state').complete,true);
assert.equal(stores.metadata.get('state').locationsRevision,200);
assert.deepEqual((await readLocationsCache(200,2)).map(x=>x.code),['A2','B2']);
""")

    def test_multiple_deletions_need_complete_snapshot(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'location_deleted',revision:2,payload:{id:1,locations_revision:200}});
assert.equal(await readLocationsCache(200,1),null);
await applyRuntimeIncrementalMessage({type:'location_deleted',revision:3,payload:{id:2,locations_revision:200}});
assert.equal(stores.metadata.get('state').locationsRevision,100);
assert.equal(sent.length,1);
await writeLocationsCache([],200);
assert.deepEqual(await readLocationsCache(200,0),[]);
""")

    def test_old_snapshot_cannot_certify_new_partial_changes(self):
        self.run_js("""
await updateLocationsCacheRecord({...A,code:'A2'},200);
assert.equal(await writeLocationsCache([A,B],150),false);
assert.equal(stores.metadata.get('state').complete,false);
""")

    def test_legacy_cache_without_complete_marker_is_not_trusted(self):
        self.run_js("""
delete stores.metadata.get('state').complete;
assert.equal(await readLocationsCache(100,2),null);
""")

    def test_malformed_snapshot_does_not_confirm_revision(self):
        self.run_js("""
assert.equal(await writeLocationsCache([A,A],200),false);
assert.equal(stores.metadata.get('state').locationsRevision,100);
""")

    def test_disconnect_with_pending_write_resyncs_and_serializes_new_connection(self):
        self.run_js("""
connectRuntimeSocket();
const oldConnection=runtimeSocket;
let release, entered;
const gate=new Promise(resolve=>release=resolve), started=new Promise(resolve=>entered=resolve);
const commit=waitForIdbTransaction;
let delayed=true;
waitForIdbTransaction=async tx=>{if(delayed){delayed=false;entered();await gate;}await commit(tx);};
oldConnection.emit({type:'location_updated',revision:2,payload:{location:{...A,code:'A2'},locations_revision:200}});
await started;
connectRuntimeSocket();
assert.equal(runtimeSocket.url.includes('has_locations'),false);
runtimeSocket.emit({type:'runtime_snapshot',revision:50,locations_revision:300,locations_included:true,
  payload:{locations:[{...A,code:'A3'},{...B,code:'B3'}]}});
release(); await runtimeMessageChain;
assert.equal(runtimeRevision,50); assert.equal(locationsRevision,300);
assert.deepEqual((await readLocationsCache(300,2)).map(x=>x.code),['A3','B3']);
assert.equal(appliedLocations[1].code,'B3');
""")

    def test_snapshot_cannot_omit_locations_when_client_is_partial(self):
        self.run_js("""
locationsRevision=null; connectRuntimeSocket();
runtimeSocket.emit({type:'runtime_snapshot',revision:2,locations_revision:200,locations_included:false,payload:{locations:[]}});
await runtimeMessageChain;
assert.equal(locationsRevision,null); assert.equal(sent[0].has_locations,false);
""")
