"""Differential tests use an explicit Git-exported baseline, never a production DB."""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import main
from models import MaterialGroup, MovementOrder, Rack
import test_dispatch_backend_baseline as baseline
import test_phase1b_frontend as frontend


class OriginalRackEndpointRegressionTests(unittest.TestCase):
    setUp = baseline.DispatchBackendBaselineTests.setUp
    tearDown = baseline.DispatchBackendBaselineTests.tearDown

    def original_update(self):
        filename = os.environ.get('AMR_REGRESSION_BASELINE_MAIN')
        if not filename:
            self.skipTest('Set AMR_REGRESSION_BASELINE_MAIN to the Git-exported main.py')
        tree = ast.parse(Path(filename).read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'update_rack')
        node.decorator_list = []
        namespace = dict(main.__dict__)
        namespace['SessionLocal'] = self.factory
        namespace['require_admin'] = lambda *_args: None
        exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
        return namespace['update_rack']

    def compare(self, changes, *, status='available'):
        original = self.original_update()
        with self.factory() as db:
            initial = {column.name: getattr(db.get(Rack, self.ids['rack']), column.name) for column in Rack.__table__.columns}
        body = main.RackIn(**{'code':'RACK-EDIT', 'material_group_id':self.ids['material'], 'status':status, **changes})
        def invoke(handler):
            try:
                with patch.object(main, 'SessionLocal', self.factory), patch.object(main, 'require_admin'), patch.object(main, '_get_rcs_client_from_settings', side_effect=AssertionError('RCS forbidden')):
                    result = handler(self.ids['rack'], body, 'fixture')
                values = result.model_dump(mode='json'); values.pop('updated_at')
                return 200, values
            except HTTPException as exc:
                return exc.status_code, exc.detail
        previous = invoke(original)
        with self.factory() as db:
            row = db.get(Rack, self.ids['rack'])
            for key, value in initial.items():
                setattr(row, key, value)
            db.commit()
        current = invoke(main.update_rack)
        self.assertEqual(current, previous)

    def test_legacy_regular_save_matches_git_baseline(self):
        self.compare({'name':' Name ', 'lot':' Lot ', 'quantity':9, 'manufacturer_code':' M ', 'comment':' Comment ', 'custom_fields':[{'key':'q','label':' Quality ','value':' A '}]})

    def test_legacy_reservation_matches_git_baseline(self):
        self.compare({}, status='reserved')

    def test_legacy_sin_material_matches_git_baseline(self):
        self.compare({'material_group_id':None})

    def test_legacy_duplicate_rack_code_matches_git_baseline(self):
        with self.factory() as db:
            db.add(Rack(code='DUP', material_group_id=self.ids['material']))
            db.commit()
        self.compare({'code':'DUP'})

    def test_legacy_unknown_material_matches_git_baseline(self):
        self.compare({'material_group_id':9999})

    def test_legacy_inactive_material_rejection_matches_git_baseline(self):
        with self.factory() as db:
            db.get(MaterialGroup, self.ids['material']).is_active = 0
            db.commit()
        self.compare({})

    def test_legacy_active_order_guard_matches_git_baseline(self):
        with self.factory() as db:
            db.add(MovementOrder(order_code='ACTIVE', source_area_id=self.ids['source_area'], destination_area_id=self.ids['destination_area'], material_group_id=self.ids['material'], rack_id=self.ids['rack'], source_cell_id=self.ids['source'], destination_cell_id=self.ids['destination'], status='dispatched'))
            db.commit()
        self.compare({'quantity':9})

    def test_legacy_whitespace_input_is_hardened_without_partial_commit(self):
        # RackIn's original min_length rule accepts spaces; this is a legacy API
        # compatibility check, not permission to create a new business rule.
        original = self.original_update()
        body = main.RackIn(code='   ', material_group_id=self.ids['material'])
        with patch.object(main, 'SessionLocal', self.factory), patch.object(main, 'require_admin'):
            # Baseline commits an empty code then fails response validation.
            # Do not restore that invalid-data defect merely to claim equivalence.
            with self.assertRaises(ValidationError):
                original(self.ids['rack'], body, 'fixture')
            with self.factory() as db:
                self.assertEqual(db.get(Rack, self.ids['rack']).code, '')
                db.get(Rack, self.ids['rack']).code = 'RACK-001'
                db.commit()
            with self.assertRaises(HTTPException) as caught:
                main.update_rack(self.ids['rack'], body, 'fixture')
            self.assertEqual(caught.exception.status_code, 400)
            with self.factory() as db:
                self.assertEqual(db.get(Rack, self.ids['rack']).code, 'RACK-001')


class EditorRuntimeOrderingRegressionTests(unittest.TestCase):
    block = frontend.FrontendPhase1BTests.block
    run_js = frontend.FrontendPhase1BTests.run_js

    @classmethod
    def setUpClass(cls):
        cls.source = (Path(main.__file__).parent / 'static/app.js').read_text(encoding='utf-8')

    def test_location_sequence_duplicate_old_gap_and_original_resync(self):
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'location_updated',revision:105,payload:{location:{id:4,x:2,y:2,code:'NEW',rack_id:3,updated_at:newTime},locations_revision:2}});
assert.equal(resyncs,1); assert.equal(runtimeRevision,105); assert.equal(locationsRevision,null);
await applyRuntimeIncrementalMessage({type:'location_updated',revision:105,payload:{location:{id:4,x:2,y:2,code:'DUPLICATE',updated_at:oldTime},locations_revision:1}});
await applyRuntimeIncrementalMessage({type:'location_updated',revision:103,payload:{location:{id:4,x:2,y:2,code:'OLD',updated_at:oldTime},locations_revision:1}});
assert.equal(locationById.get(4).code,'NEW'); assert.equal(resyncs,1);
await assert.rejects(() => applyRuntimeIncrementalMessage({type:'location_updated',revision:107,payload:{location:{id:4,x:2,y:2,code:'GAP'}}}),/salto de revisión/);
assert.equal(runtimeRevision,105); assert.equal(locationById.get(4).code,'NEW');
""", 'let resyncs=0; function requestRuntimeResync(){resyncs++;}')

    def test_editor_http_and_websocket_order_keeps_latest_location_and_racks(self):
        self.run_js("""
const oldLocation={id:4,x:2,y:2,code:'L4',rack_id:3,updated_at:oldTime};
const newLocation={...oldLocation,rack_id:8,updated_at:newTime};
const oldRack={id:3,code:'R3',location_x:2,location_y:2,updated_at:oldTime};
const releasedRack={...oldRack,location_x:null,location_y:null,updated_at:newTime};
const assignedRack={id:8,code:'R8',location_x:2,location_y:2,updated_at:newTime};
for(const order of ['http-first','ws-first']) {
  locationById.clear(); locationByRackId.clear(); locationByCode.clear(); rackById.clear();
  catalog.racks=[]; locations=[]; operationalLocations=[];
  upsertLocation(oldLocation); applyRackUpdated(oldRack);
  if(order==='http-first') { upsertLocation(newLocation); applyRackUpdated(releasedRack); applyRackUpdated(assignedRack); }
  await applyRuntimeIncrementalMessage({type:'rack_updated',revision:runtimeRevision+1,payload:{rack:releasedRack}});
  await applyRuntimeIncrementalMessage({type:'rack_updated',revision:runtimeRevision+1,payload:{rack:assignedRack,location:newLocation}});
  if(order==='ws-first') { upsertLocation(newLocation); applyRackUpdated(releasedRack); applyRackUpdated(assignedRack); }
  // A delayed older HTTP response must not resurrect the old assignment.
  upsertLocation(oldLocation); applyRackUpdated(oldRack);
  assert.equal(locationByRackId.has(3),false); assert.equal(locationByRackId.get(8).id,4);
  assert.equal(rackById.get(3).location_x,null); assert.equal(rackById.get(8).location_x,2);
}
""", 'function requestRuntimeResync(){}')

    def test_disconnect_and_reconnect_requests_full_locations_after_partial_event(self):
        socket = self.block('function cleanupRuntimeSocket', 'function applyRobotMonitorUpdated')
        # Extract the complete function through brace balance without unrelated UI.
        start = self.source.index('function connectRuntimeSocket')
        end = self.source.index('\n}\n', start) + 3
        connect = self.source[start:end]
        self.run_js("""
await applyRuntimeIncrementalMessage({type:'location_updated',revision:105,payload:{location:{id:4,x:2,y:2,code:'NEW',updated_at:newTime},locations_revision:2}});
assert.equal(locationsRevision,null);
connectRuntimeSocket();
assert.equal(lastSocketUrl.includes('has_locations'),false);
runtimeSocket.handlers.close(); assert.equal(reconnectScheduled,1);
connectRuntimeSocket(); assert.equal(lastSocketUrl.includes('locations_revision'),false);
""", socket + connect + """
let runtimeSocket=null,runtimeSocketConnected=false,runtimeSocketReconnectHandle=null,runtimeResyncPending=false;
let runtimeMessageChain=Promise.resolve(),runtimeMetricsEnabled=false,RUNTIME_SOCKET_RECONNECT_MS=1,reconnectScheduled=0,lastSocketUrl='';
const window={setTimeout(){reconnectScheduled++;return 1;},clearTimeout(){}};
function setConn(){} const API={runtimeWs:'ws://127.0.0.1/ws/runtime'};
class WebSocket {static OPEN=1; constructor(url){lastSocketUrl=url;this.handlers={};this.readyState=1;} addEventListener(name,fn){this.handlers[name]=fn;} close(){} send(){} }
""")

    def test_original_middle_right_pan_and_wheel_handlers(self):
        down = self.block('canvas.addEventListener("mousedown"', 'canvas.addEventListener("click"')
        start = self.source.index('window.addEventListener("mousemove"', self.source.index('canvas.addEventListener("mousedown"'))
        end = self.source.index('canvas.addEventListener("mouseleave"', start)
        movement = self.source[start:end]
        wheel = self.block('canvas.addEventListener("wheel"', 'function getTouchDistance')
        setup = """
const hooks={}; const canvas={getBoundingClientRect:()=>({left:0,top:0}),addEventListener:(type,handler)=>hooks['canvas:'+type]=handler};
const window={addEventListener:(type,handler)=>hooks['window:'+type]=handler};
const multiSelectMode=false; let multiSelectBox=null,freeLayoutDrag=null,dragging=false,dragStart=null,cam={offX:10,offY:20,scale:1};
function updateCellHoverTooltip(){} function zoomAtPoint(scale){cam.scale=scale;}
"""
        self.run_js("""
for(const button of [1,2]) {
  cam.offX=10;cam.offY=20;
  hooks['canvas:mousedown']({button,clientX:100,clientY:200,preventDefault(){}});
  assert.equal(dragging,true);
  hooks['window:mousemove']({clientX:130,clientY:240});
  assert.equal(cam.offX,40);assert.equal(cam.offY,60);
  hooks['window:mouseup']();assert.equal(dragging,false);
}
hooks['canvas:wheel']({clientX:100,clientY:200,deltaY:-1,preventDefault(){}});
assert.equal(cam.scale,1.1);
hooks['canvas:wheel']({clientX:100,clientY:200,deltaY:1,preventDefault(){}});
assert.ok(Math.abs(cam.scale-0.99)<1e-10);
""", setup + down + movement + wheel)


if __name__ == '__main__':
    unittest.main()
