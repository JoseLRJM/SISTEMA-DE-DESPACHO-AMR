import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def function_block(source: str, name: str, next_name: str) -> str:
    start = source.index(f"async function {name}")
    async_marker = f"async function {next_name}"
    sync_marker = f"function {next_name}"
    end = source.find(async_marker, start + 1)
    if end < 0:
        end = source.index(sync_marker, start + 1)
    return source[start:end]


class DispatchFrontendOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_fifo_request_is_unchanged_and_has_no_success_reload(self):
        block = function_block(self.source, "executeFifoRequest", "refreshAfterDispatchError")
        expected = 'fetchJson(API.fifoExecute, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })'
        self.assertEqual(block.count(expected), 1)
        self.assertNotIn("await loadLocations", block)
        self.assertNotIn("await loadCatalog", block)
        self.assertNotIn("await loadMovementOrders", block)
        self.assertIn("beginDispatchUiAction('fifo'", block)
        self.assertIn("applyDispatchHttpResult(result)", block)

    def test_direct_request_is_unchanged_and_has_no_success_reload(self):
        block = function_block(self.source, "executeDirectMoveRequest", "fillCellForm")
        expected = "fetchJson(API.directMoveExecute, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })"
        self.assertEqual(block.count(expected), 1)
        self.assertNotIn("await loadLocations", block)
        self.assertNotIn("await loadCatalog", block)
        self.assertNotIn("await loadMovementOrders", block)
        self.assertIn("beginDispatchUiAction('direct-move'", block)
        self.assertIn("applyDispatchHttpResult(result)", block)

    def test_operator_request_is_unchanged_and_has_no_success_reload(self):
        block = function_block(self.source, "confirmOperatorActionModal", "getAreaByIdForCancel")
        normal_flow = block[block.index("beginDispatchUiAction('operator-window'"):]
        expected = "fetchJson(API.operatorWindowExecute(activeOperatorWindow.id, operatorActionState.button.button_index), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operatorActionRequestBody()) })"
        self.assertEqual(normal_flow.count(expected), 1)
        self.assertNotIn("await loadLocations", normal_flow)
        self.assertNotIn("await loadCatalog", normal_flow)
        self.assertNotIn("await loadMovementOrders", normal_flow)
        self.assertIn("applyDispatchHttpResult(result)", normal_flow)

    def test_busy_guard_precedes_fetch_and_always_restores(self):
        helper = self.source[self.source.index("const dispatchUiActionsInFlight"):self.source.index("async function executeDirectMoveRequest")]
        self.assertIn("if (dispatchUiActionsInFlight.has(key)) return null", helper)
        self.assertLess(helper.index("button.disabled = true"), helper.index("messageElement.textContent = 'Enviando...'"))
        self.assertIn("dispatchUiActionsInFlight.delete(key)", helper)
        for name, next_name in (("executeDirectMoveRequest", "fillCellForm"), ("executeFifoRequest", "refreshAfterDispatchError"), ("confirmOperatorActionModal", "getAreaByIdForCancel")):
            block = function_block(self.source, name, next_name)
            if name == "confirmOperatorActionModal":
                block = block[block.index("beginDispatchUiAction('operator-window'"):]
            self.assertIn("finally {", block)
            self.assertIn("finishUiAction();", block)

    def test_http_and_websocket_share_idempotent_order_upsert(self):
        apply_block = self.source[self.source.index("function applyOrderUpdated"):self.source.index("function applyOrderDeleted")]
        self.assertIn("order.order_id ?? order.id", apply_block)
        self.assertIn("order.order_code", apply_block)
        self.assertIn("movementOrders = movementOrders.filter", apply_block)
        self.assertIn("applyOrderUpdated({ order: result }, true)", self.source)
        self.assertIn("case 'order_created': applyOrderUpdated(payload, true)", self.source)

    def test_existing_resync_and_incremental_handlers_remain(self):
        self.assertIn("runtime_resync_required", self.source)
        self.assertIn("requestRuntimeResync", self.source)
        self.assertIn("case 'rack_updated':", self.source)
        self.assertIn("case 'location_updated':", self.source)
        self.assertNotRegex(
            function_block(self.source, "executeFifoRequest", "refreshAfterDispatchError"),
            re.compile(r"runtimeSnapshot|requestRuntimeResync"),
        )

    def _run_node(self, script: str):
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_double_click_guard_is_dynamic_not_only_static(self):
        helper = self.source[self.source.index("const dispatchUiActionsInFlight"):self.source.index("function applyDispatchHttpResult")]
        self._run_node(f"""
{helper}
const assert = require('assert');
const button = {{ disabled: false, textContent: 'Enviar' }};
const message = {{ textContent: '' }};
let fetches = 0;
let release;
const pending = new Promise(resolve => release = resolve);
async function click() {{
  const finish = beginDispatchUiAction('fifo', button, message);
  if (!finish) return;
  try {{ fetches += 1; await pending; }} finally {{ finish(); }}
}}
(async () => {{
  const first = click(); const second = click();
  assert.equal(fetches, 1); assert.equal(button.disabled, true); assert.equal(button.textContent, 'Enviando...');
  release(); await Promise.all([first, second]);
  assert.equal(fetches, 1); assert.equal(button.disabled, false); assert.equal(button.textContent, 'Enviar');
}})().catch(error => {{ console.error(error); process.exit(1); }});
""")

    def test_post_dispatch_recovery_healthy_disconnected_and_resync(self):
        recovery = self.source[self.source.index("function runtimeSocketIsHealthy"):self.source.index("async function executeDirectMoveRequest")]
        self._run_node(f"""
const assert = require('assert'); const WebSocket = {{ OPEN: 1 }}; const window = {{ setTimeout }};
const POST_DISPATCH_RESYNC_TIMEOUT_MS = 100;
let runtimeSocketConnected = true, runtimeSocket = {{ readyState: 1 }}, runtimeResyncPending = false;
let loads = 0, resyncs = 0;
function requestRuntimeResync() {{ resyncs += 1; runtimeResyncPending = true; setTimeout(() => runtimeResyncPending = false, 5); }}
async function loadMovementOrders() {{ loads += 1; }}
{recovery}
(async () => {{
  let message = {{ textContent: 'Enviada' }};
  assert.equal(await recoverPostDispatchCoherence({{order_id: 1}}, message), 'websocket');
  assert.equal(loads, 0); assert.equal(resyncs, 0);
  runtimeSocketConnected = false; runtimeResyncPending = false;
  setTimeout(() => runtimeSocketConnected = true, 5);
  assert.equal(await recoverPostDispatchCoherence({{order_id: 1}}, message), 'websocket-resync');
  assert.equal(loads, 0); assert.equal(resyncs, 1);
  runtimeSocketConnected = false; runtimeSocket = {{ readyState: 3 }}; runtimeResyncPending = false;
  assert.equal(await recoverPostDispatchCoherence({{order_id: 1}}, message), 'http-snapshot');
  assert.equal(loads, 1);
}})().catch(error => {{ console.error(error); process.exit(1); }});
""")

    def test_http_first_and_websocket_first_are_idempotent_dynamically(self):
        upsert = self.source[self.source.index("function applyOrderUpdated"):self.source.index("function applyOrderDeleted")]
        self._run_node(f"""
const assert = require('assert');
let movementOrders = [], movementOrdersTotal = 0; const movementOrderDetailCache = new Map();
function applyRackUpdated() {{}} function applyLocationUpdated() {{}} function renderOrdersList() {{}} function updateOlderOrdersButton() {{}}
{upsert}
const http = {{ order_id: 7, order_code: 'MO-7', status: 'dispatched', created_at: '2024-01-01' }};
const ws = {{ order_id: 7, order_code: 'MO-7', status: 'dispatched', rcs_status: 'sent', created_at: '2024-01-01' }};
applyOrderUpdated({{order:http}}, true); applyOrderUpdated({{order:ws}}, true);
assert.equal(movementOrders.length, 1); assert.equal(movementOrdersTotal, 1); assert.equal(movementOrders[0].rcs_status, 'sent');
movementOrders = []; movementOrdersTotal = 0;
applyOrderUpdated({{order:ws}}, true); applyOrderUpdated({{order:http}}, true);
assert.equal(movementOrders.length, 1); assert.equal(movementOrdersTotal, 1); assert.equal(movementOrders[0].rcs_status, 'sent');
""")

    def test_revision_gap_is_rejected_before_any_incremental_state_is_applied(self):
        handler = self.source[self.source.index("async function applyRuntimeIncrementalMessage"):self.source.index("function connectRuntimeSocket")]
        self._run_node(f"""
const assert = require('assert'); let runtimeRevision = 10, applied = 0, locationsRevision = 1, locationsLoaded = true;
function applyOrderUpdated() {{ applied += 1; }} function applyOrderDeleted() {{ applied += 1; }}
function applyLocationUpdated() {{ applied += 1; }} function applyLocationDeleted() {{ applied += 1; }}
function applyRackUpdated() {{ applied += 1; }} function applyRackDeleted() {{ applied += 1; }}
function applyRobotMonitorUpdated() {{ applied += 1; }} function applyDebugEvent() {{ applied += 1; }}
async function loadCatalog() {{ applied += 1; }}
function updateLocationsCacheRecord() {{ return Promise.resolve(true); }} function logLocationsCacheMetric() {{}}
{handler}
(async () => {{
  await assert.rejects(() => applyRuntimeIncrementalMessage({{type:'order_updated',revision:12,payload:{{order:{{order_id:1}}}}}}), /salto de revisión/);
  assert.equal(runtimeRevision, 10); assert.equal(applied, 0);
}})().catch(error => {{ console.error(error); process.exit(1); }});
""")


if __name__ == "__main__":
    unittest.main()
