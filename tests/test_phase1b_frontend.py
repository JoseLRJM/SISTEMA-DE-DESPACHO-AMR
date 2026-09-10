import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendPhase1BTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static/app.js").read_text(encoding="utf-8")

    def block(self, start, end):
        return self.source[self.source.index(start):self.source.index(end, self.source.index(start) + len(start))]

    def run_js(self, assertions, extra=""):
        helpers = self.block("function entityUpdateIsOlder", "function applyOrderDeleted")
        racks = self.block("function applyRackUpdated", "function applyRackDeleted")
        locations = self.block("function upsertLocation", "function rebuildCatalogIndexes")
        handler = self.block("async function applyRuntimeIncrementalMessage", "function connectRuntimeSocket")
        http = self.block("function applyDispatchHttpResult", "function runtimeSocketIsHealthy")
        script = """
const assert = require('assert');
let movementOrders = [], movementOrdersTotal = 0, movementOrdersNextOffset = 0, selectedOrderId = null;
const movementOrderDetailCache = new Map(), historicalOrderSummaries = new Map(), movementOrderDetailRequests = new Map();
let catalog = { racks: [], areas: [], materials: [] }, runtimeRevision = 104, locationsRevision = 104, locationsLoaded = true;
let locations = [], operationalLocations = [], debugConsoleEvents = [];
const locationById = new Map(), locationByCode = new Map(), locationByRackId = new Map(), rackById = new Map();
const cellOptionsCache = {clear(){}}, idx = (x,y) => y*10+x;
function isOperationalConfiguredLocation() { return true; }
function renderOrdersList(){} function updateOlderOrdersButton(){} function renderRackOptions(){}
function renderPodPositionRackOptions(){} function renderRackList(){} function draw(){}
function renderOrderStatusQuery(){} function renderSelectedOrderDetail(){}
const robotMonitorEnabled = false; let latestRobotMonitorItems = [];
function applyLocationUpdated(location) { upsertLocation(location); }
function updateLocationsCacheRecord() { return Promise.resolve(true); }
function logLocationsCacheMetric(){}
const oldTime = '2026-09-09T12:00:00.000102', newTime = '2026-09-09T12:00:00.000105';
""" + helpers + racks + locations + handler + http + extra + "\n(async () => {\n" + assertions + "\n})().catch(error => {console.error(error); process.exit(1);});"
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_G_websocket_completed_then_old_http_stays_completed(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'order_created', revision:105, payload:{order:{order_id:7, order_code:'MO7', status:'completed', updated_at:newTime}}});
applyDispatchHttpResult({order_id:7, order_code:'MO7', status:'dispatched', updated_at:oldTime});
assert.equal(movementOrders[0].status, 'completed');
assert.equal(movementOrders.length, 1); assert.equal(movementOrdersTotal, 1);
applyDispatchHttpResult({order_id:7, order_code:'MO7', status:'dispatched', updated_at:newTime});
assert.equal(movementOrders[0].status, 'completed');
""")

    def test_H_old_rack_update_is_ignored(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'rack_updated', revision:105, payload:{rack:{id:3, code:'R3', status:'available', updated_at:newTime}}});
applyRackUpdated({id:3, code:'R3', status:'reserved', updated_at:oldTime});
assert.equal(rackById.get(3).status, 'available');
assert.equal(catalog.racks[0].status, 'available');
""")

    def test_I_old_location_update_preserves_all_indexes(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'location_updated', revision:105, payload:{location:{id:4,x:2,y:2,code:'D4',rack_id:3,updated_at:newTime}}});
upsertLocation({id:4,x:1,y:1,code:'OLD',rack_id:null,updated_at:oldTime});
assert.equal(locationById.get(4).code, 'D4');
assert.equal(locationByRackId.get(3).id, 4);
assert.equal(locationByCode.has('OLD'), false);
assert.equal(locations[idx(2,2)].rack_id, 3);
""")

    def test_J_genuinely_newer_order_rack_location_are_applied(self):
        self.run_js("""
applyOrderUpdated({order:{order_id:7,status:'dispatched',updated_at:oldTime}},true);
applyOrderUpdated({order:{order_id:7,status:'completed',updated_at:newTime}});
applyRackUpdated({id:3,status:'reserved',updated_at:oldTime});
applyRackUpdated({id:3,status:'available',updated_at:newTime});
upsertLocation({id:4,x:2,y:2,rack_id:null,updated_at:oldTime});
upsertLocation({id:4,x:2,y:2,rack_id:3,updated_at:newTime});
assert.equal(movementOrders[0].status,'completed');
assert.equal(rackById.get(3).status,'available');
assert.equal(locationById.get(4).rack_id,3);
assert.equal(entityUpdateIsOlder({revision:105},{revision:102}),true);
assert.equal(entityUpdateIsOlder({revision:105},{revision:106}),false);
// New evidence, not a permanent terminal-state lock.
assert.equal(entityUpdateIsOlder({status:'completed',updated_at:oldTime},{status:'dispatched',updated_at:newTime}),false);
""")

    def test_old_snapshot_and_detail_do_not_replace_new_order(self):
        snapshot = self.block("function applyRuntimeSnapshotData", "async function refreshMatrixViewData")
        detail = self.block("function rememberMovementOrderDetail", "async function loadOlderMovementOrders")
        self.run_js("""
applyOrderUpdated({order:{order_id:7,status:'completed',updated_at:newTime}},true);
applyRuntimeSnapshotData({orders:[{order_id:7,status:'dispatched',updated_at:oldTime}]}, null, {preserveLocations:true});
assert.equal(movementOrders[0].status,'completed');
global.fetchJson = async () => ({order_id:7,status:'dispatched',updated_at:oldTime});
const result = await loadMovementOrderDetail(7);
assert.equal(result.status,'completed');
assert.equal(movementOrders[0].status,'completed');
assert.equal(movementOrderDetailCache.has(7), false);
""", snapshot + detail + "\nconst API = {movementOrderDetail: id => '/orders/' + id};")

    def test_http_snapshot_in_flight_is_ignored_after_ws_advance(self):
        load = self.block("async function loadMovementOrders", "async function autoRefreshRuntimeSections")
        self.run_js("""
let finish;
global.fetchJson = () => new Promise(resolve => finish=resolve);
let snapshots=0; global.applyRuntimeSnapshotData = () => snapshots++;
const pending = loadMovementOrders();
await applyRuntimeIncrementalMessage({type:'order_updated',revision:105,payload:{order:{order_id:7,status:'completed',updated_at:newTime}}});
finish({orders:[{order_id:7,status:'dispatched',updated_at:oldTime}]});
await pending;
assert.equal(snapshots,0); assert.equal(movementOrders[0].status,'completed');
""", load + "\nconst API = {runtimeSnapshot:'/snapshot'};")
