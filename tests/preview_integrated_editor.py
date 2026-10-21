"""Regression-only full UI harness. No production startup, DB, workers or RCS.

Run from an isolated copy of the repository. http://127.0.0.1:8767
The fixture's temporary admin password is local-review-only.
"""
import sys
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def protect(event, args):
    if event == 'sqlite3.connect' and 'agv.db' in str(args[0]).lower():
        raise RuntimeError('Regression fixture forbids operational agv.db')
    if event == 'socket.connect':
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('Regression fixture forbids external connections')

sys.addaudithook(protect)
import logging_config
logging_config.configure_logging = lambda: None
logging_config.ensure_log_file = lambda: None
import main
import models
import uvicorn
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

def forbidden(*_args, **_kwargs):
    raise AssertionError('Real startup/workers/RCS forbidden in regression fixture')

# The real app's startup is deliberately NOT installed on the test app.
assert main.app.router.on_startup, 'Audit expected the production startup hooks'
main.on_startup = forbidden
main._get_rcs_client_from_settings = forbidden
main._start_rack_sync_scheduler = forbidden
main._start_robot_monitor_cache_updater = forbidden
main.app.state.task_monitor.start = forbidden
main._maybe_auto_release_stuck_cancel_return_orders = lambda: None
main._auto_release_stuck_cancel_return_orders = lambda *_args, **_kwargs: []
engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
old_factory, old_engine = main.SessionLocal, main.engine
for module in list(sys.modules.values()):
    if getattr(module, '__file__', None) and str(ROOT) in str(module.__file__):
        if getattr(module, 'SessionLocal', None) is old_factory:
            module.SessionLocal = factory
        if getattr(module, 'engine', None) is old_engine:
            module.engine = engine
models.Base.metadata.create_all(engine)
main.ensure_default_settings()
main.ensure_special_materials()
with factory() as db:
    main.set_setting(db, 'admin_password_hash', main._sha256('local-review-only'))
    main.set_setting(db, 'display_rows', '8')
    main.set_setting(db, 'display_cols', '8')
    main.set_setting(db, 'rcs_agv_monitor_enabled', '0')
    area = models.Area(code='TEST', name='Área temporal', is_active=1)
    material = models.MaterialGroup(code='TEST-MAT', name='Material temporal', is_active=1)
    db.add_all([area, material]); db.flush()
    rack = models.Rack(code='TEST-R1', name='Rack temporal 1', material_group_id=material.id, status='available', quantity=3)
    other = models.Rack(code='TEST-R2', name='Rack temporal 2', material_group_id=material.id, status='available', lot='LOTE-R2')
    db.add_all([rack, other]); db.flush()
    for y in range(8):
        for x in range(8):
            db.add(models.Location(x=x, y=y, code=f'TEST-{x}-{y}', status=1 if (x,y)==(0,0) else 0, enabled=1, is_visible=1, area_id=area.id, rack_id=rack.id if (x,y)==(0,0) else None))
    db.commit()
for name, handler in [('after_flush', main._capture_runtime_session_changes), ('after_commit', main._publish_runtime_session_changes), ('after_rollback', main._discard_runtime_session_changes)]:
    event.listen(factory, name, handler)
main._locations_count_cache = 64
counts = Counter()

@asynccontextmanager
async def lifespan(_app):
    main._runtime_event_loop = asyncio.get_running_loop()
    main._runtime_event_queue = asyncio.Queue(maxsize=2048)
    task = asyncio.create_task(main._runtime_broadcast_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        main._runtime_event_loop = main._runtime_event_queue = None
        engine.dispose()

app = FastAPI(lifespan=lifespan)
app.router.routes.extend(main.app.router.routes)
assert not app.router.on_startup, 'Production startup must never run'

@app.middleware('http')
async def observed(request, call_next):
    counts[f'{request.method} {request.url.path}'] += 1
    return await call_next(request)

@app.get('/__review__/evidence')
def evidence():
    return {'requests':dict(counts), 'workers_started':False, 'database':'sqlite:// (memory)', 'runtime_revision':main._runtime_current_revision()}

@app.post('/__review__/remote-location')
def remote_location(body: dict):
    allowed = {'code','note','rack_id','area_id'}
    if set(body) - allowed:
        raise HTTPException(422, 'Fixture only accepts location metadata')
    from datetime import datetime
    with factory() as db:
        location = db.get(models.Location, 1)
        for key, value in body.items():
            setattr(location, key, value)
        location.updated_at = datetime.utcnow()
        db.commit()
        return main._location_out(db, location)

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8767)
