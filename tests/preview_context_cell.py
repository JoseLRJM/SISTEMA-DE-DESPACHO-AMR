"""Optional browser fixture: isolated in-memory DB, no startup workers or RCS.

Run from repository root: python tests/preview_context_cell.py
Open http://127.0.0.1:8766 (test-only admin token; never production data).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Base, Area, Location, MaterialGroup, Rack

engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
main.SessionLocal = sessionmaker(bind=engine, autoflush=False)
with main.SessionLocal() as db:
    db.add_all([Area(id=1, code='TEST', name='Área de prueba', is_active=1), MaterialGroup(id=1, code='MAT', name='Material ficticio', is_active=1)])
    db.flush()
    db.add_all([Rack(id=1, code='TEST-001', name='Rack de prueba', status='available', material_group_id=1, quantity=3), Rack(id=2, code='TEST-002', name='Rack libre', status='available', material_group_id=1, quantity=0)])
    db.flush()
    db.add(Location(id=1, x=2, y=3, code='TEST-A', area_id=1, rack_id=1, status=1, enabled=1, is_visible=1))
    db.commit()
    catalog = {'areas': [{'id': 1, 'code': 'TEST', 'name': 'Área de prueba', 'is_active': 1}], 'materials': [{'id': 1, 'code': 'MAT', 'name': 'Material ficticio', 'is_active': 1}], 'racks': [main._rack_out(db, db.get(Rack, i)).model_dump(mode='json') for i in (1, 2)]}
    location = main._location_out(db, db.get(Location, 1)).model_dump(mode='json')

def authorize(token):
    if token != 'fixture-only':
        raise HTTPException(401, 'Sesión de prueba requerida')

main.require_admin = authorize
def forbid_rcs(*_args, **_kwargs):
    raise AssertionError('RCS is forbidden in the preview fixture')
main._get_rcs_client_from_settings = forbid_rcs
app = FastAPI()
app.add_api_route('/api/cell-editor/{location_id}', main.get_context_cell, methods=['GET'])
app.add_api_route('/api/admin/cell-editor/{location_id}', main.save_context_cell, methods=['PUT'])
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')

@app.get('/', response_class=HTMLResponse)
def preview():
    source = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    modal = source[source.index('<button class="btn context-cell-offer"'):source.index('<script src="/static/app.js?v=context-cell')]
    bootstrap = '''
let adminToken = 'fixture-only';
const catalog = CATALOG, fixtureLocation = LOCATION;
const locationByRackId = new Map([[1, fixtureLocation]]), selected = {x:2,y:3};
const cellMsg = document.getElementById('cellMsg'), canvas = document.querySelector('canvas');
const operatorButtonPickMode = null, operatorActionPickMode = null, directMovePickMode = null;
const multiSelectMode = false, freeLayoutDrag = null, dragging = false, suppressNextCanvasClick = false;
const locationsRevision = 1;
const getLocationAtGrid = () => fixtureLocation;
const isMultiSelectionActive = () => false, isFreeLayoutEditing = () => false, isFreeLayoutMode = () => false;
const canvasToGrid = () => selected;
const fetchHeaders = extra => ({...extra, 'X-Admin-Token': adminToken});
async function fetchJson(url, options) { const r=await fetch(url,options), body=await r.json(); if(!r.ok) throw Object.assign(new Error(body.detail), {status:r.status}); return body; }
const toLocalInputValue = value => value ? value.slice(0,16) : '';
const fromLocalInputValue = value => value ? new Date(value).toISOString() : null;
const isReservedState = value => value === 'Reservado';
const reservationTaskLabel = rack => rack.reservation_task_identifier || 'Sin tarea';
function applyRackUpdated(rack) { catalog.racks[catalog.racks.findIndex(row=>row.id===rack.id)] = rack; }
function applyLocationUpdated(row) { Object.assign(fixtureLocation,row); }
const updateLocationsCacheRecord = () => Promise.resolve();
function renderRackOptions() {} function renderPodPositionRackOptions() {} function renderRackList() {} function renderDirectMoveSelection() {} function draw() {}
'''.replace('CATALOG', json.dumps(catalog)).replace('LOCATION', json.dumps(location))
    return f'<!doctype html><html lang="es"><meta charset="utf-8"><title>Editor · Prueba aislada</title><link rel="stylesheet" href="/static/styles.css"><body><main style="padding:24px"><h2>Prueba aislada: datos ficticios</h2><button id="btnContextCell" class="btn">Editar casilla</button><p id="cellMsg"></p><canvas width="300" height="100"></canvas></main>{modal}<script>{bootstrap}</script><script src="/static/cell-editor.js"></script></body></html>'

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8766)
