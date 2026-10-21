// Execute the actual editor against a minimal DOM, with deferred network responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(value = '') { this.value = value; this.disabled = false; this.hidden = false; this.listeners = {}; this.dataset = {}; this.style = {}; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  setAttribute(name, value) { (this.attributes ||= {})[name] = value; }
  querySelectorAll(selector) { return selector === '[data-context-admin]' ? adminSections : []; }
  replaceChildren(...options) { this.options = options; }
  append(child) { (this.children ||= []).push(child); }
  focus() { document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  reportValidity() { return true; }
}
const adminSections = Array.from({length: (fs.readFileSync('static/index.html','utf8').match(/data-context-admin/g) || []).length}, () => new Element());
const controls = new Map();
const ids = new Map();
const document = { activeElement: new Element(), createElement() { return new Element(); }, getElementById(id) { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); } };
const form = document.getElementById('contextCellForm');
form.elements = { namedItem(name) { if (!controls.has(name)) controls.set(name, new Element()); return controls.get(name); } };
let confirmAnswer = true, confirmCount = 0;
const window = { innerWidth: 1200, innerHeight: 800, listeners: {}, confirm() { confirmCount++; return confirmAnswer; }, addEventListener(type, fn) { this.listeners[type] = fn; } };
const canvas = new Element();
canvas.getBoundingClientRect = () => ({ left: 0, top: 0 });
const catalog = { areas: [{ id: 1, code: 'AREA', is_active: 1 }], materials: [{ id: 1, code: 'MAT', is_active: 1 }], racks: [{ id: 9, code: 'R9' }, { id: 10, code: 'R10' }] };
let liveLocation = { id: 5, x: 1, y: 2, code: 'L5', area_id: 1, status: 1, enabled: 1, is_visible: 1, note: null, rack_id: 9 };
const baseRack = { id: 9, code: 'R9', name: 'Rack 9', material_group_id: 1, quantity: 4, reservation_status: 'No reservado', fifo_entered_at: '2026-01-01T10:00:23.123456', custom_fields: [{ key: 'a', label: 'Quality', value: 'A' }] };
const copy = value => structuredClone(value);
const snapshot = () => ({ location: copy(liveLocation), racks: [copy(baseRack)], expected_location: 'LOCATION-V1', expected_racks: { 9: 'RACK-V1' } });
const locationByRackId = new Map([[9, liveLocation]]);
const locationById = new Map([[5, liveLocation]]);
let requests = [], response = () => Promise.resolve(snapshot()), draws = 0;
const sandbox = {
  document, window, canvas, catalog, locationByRackId, locationById,
  Option: function(text, value) { this.text = text; this.value = value; },
  adminToken: 'admin', cellMsg: new Element(), selected: { x: 1, y: 2 },
  operatorButtonPickMode: null, operatorActionPickMode: null, directMovePickMode: null,
  multiSelectMode: false, freeLayoutDrag: null, dragging: false, suppressNextCanvasClick: false,
  isMultiSelectionActive: () => sandbox.multi, isFreeLayoutEditing: () => sandbox.freeEditing,
  isFreeLayoutMode: () => sandbox.free, hitTestFreeLocation: () => liveLocation,
  canvasToGrid: () => ({ x: 1, y: 2 }), getLocationAtGrid: () => liveLocation,
  fetchJson: (url, options) => { requests.push({ url, options }); return response(url, options); },
  fetchHeaders: extra => ({ ...extra, 'X-Admin-Token': sandbox.adminToken }),
  toLocalInputValue: value => value ? value.slice(0, 16) : '',
  fromLocalInputValue: value => value ? new Date(value).toISOString() : null,
  isReservedState: value => value === 'Reservado', reservationTaskLabel: rack => rack.reservation_task_identifier || 'Sin tarea',
  applyRackUpdated: row => { const i = catalog.racks.findIndex(item => item.id === row.id); catalog.racks[i] = row; },
  applyLocationUpdated: row => { liveLocation = row; locationById.set(row.id, row); },
  updateLocationsCacheRecord: () => Promise.resolve(), locationsRevision: 1,
  renderRackOptions() {}, renderPodPositionRackOptions() {}, renderRackList() {}, renderDirectMoveSelection() {},
  draw() { draws++; },
  loadLocations() { throw new Error('Global reload forbidden'); }, loadCatalog() { throw new Error('Global reload forbidden'); },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/cell-editor.js', 'utf8'), sandbox);
const run = code => vm.runInContext(code, sandbox);
const field = name => form.elements.namedItem(name);
const error = () => document.getElementById('contextCellError').textContent;
const click = { button: 0, clientX: 50, clientY: 50, ctrlKey: false, metaKey: false };
const pointClick = (down = click, up = down) => { canvas.listeners.mousedown[0](down); canvas.listeners.click[0](up); };

(async () => {
  await run('openContextCell(5)');
  assert.equal(requests.length, 1, 'one detail GET on opening');
  assert.equal(run('contextCellDraft.id'), 5);
  assert.equal(field('rack_quantity').value, 4);
  assert.equal(run('contextCellDraft.view'), 'user', 'default opening view');
  run("changeContextCellView('admin')");
  field('note').value = 'local draft';
  // Simulate WebSocket cache updates with a different global selection.
  sandbox.applyLocationUpdated({ ...liveLocation, note: 'remote note' });
  sandbox.applyRackUpdated({ ...baseRack, quantity: 88 });
  sandbox.selected = { x: 20, y: 30 };
  assert.equal(field('note').value, 'local draft');
  assert.equal(field('rack_quantity').value, 4);
  confirmAnswer = false;
  assert.equal(run('closeContextCell()'), false);
  assert.equal(requests.length, 1, 'cancel never writes');
  confirmAnswer = true;
  assert.equal(run('closeContextCell()'), true);
  assert.equal(requests.length, 1);

  await run('openContextCell(5)');
  assert.equal(run('contextCellDraft.view'), 'user', 'Admin is never remembered between openings');
  run("changeContextCellView('admin')");
  field('rack_quantity').value = '7';
  field('note').value = 'save me';
  let release;
  response = () => new Promise(resolve => { release = resolve; });
  const first = run('saveContextCell()');
  await run('saveContextCell()');
  assert.equal(requests.filter(item => item.options?.method === 'PUT').length, 1, 'double submit blocked');
  const written = requests.at(-1);
  assert.equal(written.url, '/api/admin/cell-editor/5', 'stable ID, independent of global selection');
  const payload = JSON.parse(written.options.body);
  assert.deepEqual(payload.rack, { quantity: 7 }, 'unchanged dates and custom fields omitted');
  assert.equal(payload.location.note, 'save me');
  assert.equal(payload.expected_racks['9'], 'RACK-V1');
  const saved = snapshot(); saved.location.note = 'save me'; saved.racks[0].quantity = 7;
  release(saved); await first;
  assert.equal(draws, 1, 'one grouped canvas redraw');
  assert.equal(run('contextIsDirty()'), false);
  assert.equal(error(), 'Cambios guardados.');

  field('note').value = 'keep on conflict';
  response = () => Promise.reject(Object.assign(new Error('changed'), { status: 409 }));
  await run('saveContextCell()');
  assert.equal(field('note').value, 'keep on conflict');
  assert.match(error(), /Conflicto/);
  response = () => Promise.reject(new TypeError('network lost'));
  await run('saveContextCell()');
  const afterAmbiguous = requests.length;
  await run('saveContextCell()');
  assert.equal(requests.length, afterAmbiguous, 'ambiguous write is not retried');
  assert.equal(field('note').value, 'keep on conflict');
  assert.equal(document.getElementById('contextCellSave').disabled, true);

  confirmAnswer = false;
  await run('reloadContextCell()');
  assert.equal(field('note').value, 'keep on conflict');
  confirmAnswer = true;
  response = () => Promise.resolve(snapshot());
  await run('reloadContextCell()');
  assert.equal(run('contextIsDirty()'), false);

  field('rack_name').value = 'pending name';
  field('rack_id').value = '10';
  confirmAnswer = false;
  await run('changeContextRack()');
  assert.equal(field('rack_id').value, 9);
  assert.equal(field('rack_name').value, 'pending name');
  confirmAnswer = true;
  field('rack_id').value = '10';
  response = () => Promise.resolve({ ...snapshot(), racks: [baseRack, { ...baseRack, id: 10, code: 'R10' }], expected_racks: { 9: 'RACK-V1', 10: 'RACK-V2' } });
  await run('changeContextRack()');
  assert.equal(run('contextCellDraft.rackId'), 10);
  assert.equal(field('rack_code').value, 'R10');
  assert.equal(field('rack_name').value, 'Rack 9', 'previous draft explicitly discarded');
  field('rack_id').value = '';
  await run('changeContextRack()');
  assert.equal(run('contextCellDraft.rackId'), null);
  assert.equal(document.getElementById('contextRackFields').disabled, true);

  sandbox.adminToken = null;
  run('syncContextCellPermissions()');
  const beforeReadonly = requests.length;
  await run('saveContextCell()');
  assert.equal(requests.length, beforeReadonly);
  assert.equal(document.getElementById('contextLocationFields').disabled, true);
  assert.equal(document.getElementById('contextCellSave').disabled, true);
  assert.match(document.getElementById('contextCellPermission').textContent, /Solo consulta/);
  sandbox.adminToken = 'admin';
  run('closeContextCell()');

  // Selection and gesture intent: offer an action, never open a modal implicitly.
  for (const mode of ['operatorButtonPickMode', 'operatorActionPickMode', 'directMovePickMode', 'multiSelectMode', 'freeEditing']) {
    sandbox[mode] = true; pointClick();
    assert.equal(document.getElementById('contextCellOffer').hidden, true, mode);
    sandbox[mode] = false;
  }
  pointClick({ ...click, ctrlKey: true });
  assert.equal(document.getElementById('contextCellOffer').hidden, true);
  pointClick({ ...click, metaKey: true });
  assert.equal(document.getElementById('contextCellOffer').hidden, true);
  sandbox.multi = true; pointClick(); assert.equal(document.getElementById('contextCellOffer').hidden, true); sandbox.multi = false;
  pointClick(click, { ...click, clientX: 100 });
  assert.equal(document.getElementById('contextCellOffer').hidden, true, 'drag end is not edit intent');
  sandbox.suppressNextCanvasClick = true; pointClick();
  assert.equal(document.getElementById('contextCellOffer').hidden, true); sandbox.suppressNextCanvasClick = false;
  // A picking mode ending during mousedown must still suppress the offer.
  sandbox.directMovePickMode = true; canvas.listeners.mousedown[0](click);
  sandbox.directMovePickMode = null; canvas.listeners.click[0](click);
  assert.equal(document.getElementById('contextCellOffer').hidden, true);
  pointClick();
  assert.equal(document.getElementById('contextCellOffer').hidden, false);
  assert.equal(document.getElementById('contextCellDialog').open, false);
  sandbox.free = true; pointClick(); assert.equal(document.getElementById('contextCellOffer').hidden, false);

  const app = fs.readFileSync('static/app.js', 'utf8');
  const selection = app.slice(app.indexOf('function setPrimarySelection'), app.indexOf('function toggleMultiSelectedLocation'));
  assert.doesNotMatch(selection, /openContextCell|showModal/);
  // The original application requires a resync after partial location events.
  // The editor itself still performs no global HTTP reload on saving/switching.
  let resyncs = 0;
  Object.assign(sandbox, {runtimeRevision: 10, locationsRevision: 1, socketLocation: copy(liveLocation), requestRuntimeResync() {resyncs++;}});
  vm.runInContext(app.slice(app.indexOf('async function applyRuntimeIncrementalMessage'), app.indexOf('function connectRuntimeSocket')), sandbox);
  await run("applyRuntimeIncrementalMessage({type:'location_updated', revision:11, payload:{location:socketLocation,locations_revision:2}})");
  assert.equal(resyncs, 1, 'preserve the original incomplete-checkpoint recovery policy');
  assert.equal(sandbox.runtimeRevision, 11);
  assert.equal(sandbox.locationsRevision, null, 'partial IndexedDB checkpoint never advertised as complete');
  await assert.rejects(() => run("applyRuntimeIncrementalMessage({type:'location_updated', revision:13,payload:{}})"), /salto de revisión/);
  assert.equal(form.listeners.submit.length, 1, 'listeners installed only once');
  response = () => Promise.resolve(snapshot());
  await run('openContextCell(5)');
  run("changeContextCellView('admin')");
  document.getElementById('contextAddCustomField').listeners.click[0]();
  assert.equal(JSON.parse(field('rack_custom_fields').value).length, 2);
  document.getElementById('contextCustomFields').listeners.input[0]({ target: {dataset: {contextCustomIndex:'1', contextCustomField:'label'}, value:'Quality'} });
  document.getElementById('contextCustomFields').listeners.input[0]({ target: {dataset: {contextCustomIndex:'1', contextCustomField:'value'}, value:'A'} });
  assert.deepEqual(JSON.parse(field('rack_custom_fields').value)[1], {key:'field_2', label:'Quality', value:'A'});
  assert.equal(run('contextIsDirty()'), true);
  document.getElementById('contextCustomFields').listeners.click[0]({ target: {dataset: {contextCustomRemove:'1'}} });
  assert.equal(run('contextIsDirty()'), false);
  run('closeContextCell()');
  assert.ok(confirmCount >= 4);

  // Two views of the SAME controls/draft. Exactly four editable User fields.
  response = () => Promise.resolve(snapshot());
  await run('openContextCell(5)');
  const allLocationKeys = JSON.parse(run('JSON.stringify(contextLocationKeys)'));
  const allRackKeys = JSON.parse(run("JSON.stringify([...contextRackKeys, 'reserved'])"));
  const userEditable = ['rack_id', 'rack_reserved', 'rack_lot', 'rack_material_group_id'];
  for (const name of [...allLocationKeys, ...allRackKeys.map(key => 'rack_' + key)]) {
    assert.equal(field(name).disabled, !userEditable.includes(name), `User field ${name}`);
  }
  assert.ok(adminSections.every(element => element.hidden));
  assert.match(document.getElementById('contextCellReference').textContent, /Casilla:.*\nID 5 · Coordenadas \(1, 2\)\nÁrea:/);
  const baseline = snapshot();
  // Even injected/leftover admin values in the DOM cannot leak into User payload.
  field('code').value = 'HIDDEN CODE'; field('area_id').value = '999'; field('note').value = 'HIDDEN NOTE';
  field('enabled').value = '0'; field('rack_quantity').value = '999'; field('rack_name').value = 'HIDDEN RACK';
  field('rack_custom_fields').value = 'not even valid JSON';
  field('rack_lot').value = 'LOT-USER'; field('rack_material_group_id').value = ''; field('rack_reserved').value = '1';
  response = (_url, options) => {
    const payload = JSON.parse(options.body);
    assert.deepEqual(payload.location, {});
    assert.deepEqual(payload.rack, {lot:'LOT-USER', material_group_id:null});
    assert.equal(payload.reserved, 1);
    assert.equal(payload.expected_location, baseline.expected_location);
    assert.deepEqual(payload.expected_racks, baseline.expected_racks);
    const result = copy(baseline);
    Object.assign(result.racks[0], payload.rack, {reservation_status:'Reservado'});
    return Promise.resolve(result);
  };
  await run('saveContextCell()');
  assert.equal(field('code').value, baseline.location.code);
  assert.equal(Number(field('area_id').value), baseline.location.area_id);
  assert.equal(field('note').value, baseline.location.note || '');
  assert.equal(field('rack_quantity').value, baseline.racks[0].quantity);
  assert.equal(run('contextCellDraft.view'), 'user');

  const beforeViews = requests.length;
  field('rack_lot').value = 'KEEP LOT'; field('rack_material_group_id').value = '1'; field('rack_reserved').value = '0';
  assert.equal(run("changeContextCellView('admin')"), true);
  assert.ok(adminSections.every(element => !element.hidden));
  for (const name of [...allLocationKeys, ...allRackKeys.map(key => 'rack_' + key)]) {
    assert.equal(field(name).disabled, name === 'status', `Admin field ${name}`);
  }
  field('code').value = 'ADMIN CODE'; field('area_id').value = ''; field('note').value = 'ADMIN NOTE';
  field('rack_name').value = 'ADMIN NAME'; field('rack_quantity').value = '19';
  document.getElementById('contextAddCustomField').listeners.click[0]();
  confirmAnswer = false;
  assert.equal(run("changeContextCellView('user')"), false);
  assert.equal(run('contextCellDraft.view'), 'admin');
  assert.equal(field('code').value, 'ADMIN CODE');
  assert.equal(field('rack_quantity').value, '19');
  confirmAnswer = true;
  assert.equal(run("changeContextCellView('user')"), true);
  assert.equal(run('contextHasAdminChanges()'), false);
  assert.equal(field('code').value, baseline.location.code);
  assert.equal(field('rack_name').value, baseline.racks[0].name);
  assert.equal(field('rack_lot').value, 'KEEP LOT');
  assert.equal(field('rack_material_group_id').value, '1');
  assert.equal(field('rack_reserved').value, '0');
  assert.equal(requests.length, beforeViews, 'view switching/discard never queries or saves');

  run("changeContextCellView('admin')");
  field('code').value = 'SAVE ADMIN'; field('note').value = 'ADMIN NOTES'; field('rack_quantity').value = '12';
  field('status').value = '0'; // Disabled/derived state must never override assignment.
  response = (_url, options) => {
    const payload = JSON.parse(options.body);
    assert.equal(payload.location.code, 'SAVE ADMIN');
    assert.equal(payload.location.note, 'ADMIN NOTES');
    assert.equal(payload.rack.quantity, 12);
    assert.equal(payload.rack.lot, 'KEEP LOT');
    assert.equal(payload.location.status, undefined);
    const result = copy(baseline);
    Object.assign(result.location, payload.location);
    Object.assign(result.racks[0], payload.rack);
    return Promise.resolve(result);
  };
  await run('saveContextCell()');
  assert.equal(run('contextCellDraft.view'), 'admin', 'saving preserves current view until closed');
  assert.equal(error(), 'Cambios guardados.');
  run('closeContextCell()');

  response = () => Promise.resolve(snapshot());
  await run('openContextCell(5)');
  assert.equal(run('contextCellDraft.view'), 'user');
  run("changeContextCellView('admin')");
  const noteBeforeLoss = field('note').value;
  field('note').value = 'KEEP AFTER SESSION LOSS'; field('rack_lot').value = 'KEEP USER AFTER LOSS';
  const beforeLoss = requests.length;
  sandbox.adminToken = null; run('syncContextCellPermissions()');
  assert.equal(run('contextCellDraft.view'), 'user');
  assert.equal(document.getElementById('contextViewAdmin').hidden, true);
  assert.equal(run("changeContextCellView('admin')"), false);
  assert.equal(field('note').value, 'KEEP AFTER SESSION LOSS');
  assert.equal(field('rack_lot').value, 'KEEP USER AFTER LOSS');
  await run('saveContextCell()');
  assert.equal(requests.length, beforeLoss);
  for (const name of [...allLocationKeys, ...allRackKeys.map(key => 'rack_' + key)]) assert.equal(field(name).disabled, true);
  sandbox.adminToken = 'admin'; run('syncContextCellPermissions()');
  await run('saveContextCell()');
  assert.equal(requests.length, beforeLoss, 'restoring session cannot silently drop suspended Admin draft');
  assert.match(error(), /borrador de Admin/);
  assert.equal(run("changeContextCellView('admin')"), true);
  assert.equal(field('note').value, 'KEEP AFTER SESSION LOSS');
  confirmAnswer = false;
  assert.equal(run('closeContextCell()'), false);
  confirmAnswer = true;
  run("changeContextCellView('user')");
  assert.equal(field('note').value, noteBeforeLoss);
  assert.equal(field('rack_lot').value, 'KEEP USER AFTER LOSS');
  run('closeContextCell()');

  // Open without a rack; assignment stays available, dependent fields explain why disabled.
  response = () => Promise.resolve({...snapshot(), location:{...liveLocation, rack_id:null, status:0}, racks:[], expected_racks:{}});
  await run('openContextCell(5)');
  assert.equal(run('contextCellDraft.view'), 'user');
  assert.equal(field('rack_id').disabled, false);
  for (const key of ['reserved', 'lot', 'material_group_id']) assert.equal(field('rack_' + key).disabled, true);
  assert.match(document.getElementById('contextRackHint').textContent, /Selecciona un rack existente/);
  run("changeContextCellView('admin')");
  assert.equal(field('status').disabled, false, 'Admin can set state on an empty location');
  run("changeContextCellView('user')");
  field('rack_id').value = '10';
  response = () => Promise.resolve({...snapshot(), location:{...liveLocation,rack_id:null,status:0}, racks:[{...baseRack,id:10,code:'R10',lot:'LOT-R10'}], expected_racks:{10:'R10-TOKEN'}});
  await run('changeContextRack()');
  assert.equal(field('rack_lot').value, 'LOT-R10', 'selected rack uses its own baseline');
  assert.equal(field('rack_reserved').disabled, false);
  field('rack_lot').value = 'LOT-R10-EDIT';
  response = (_url, options) => {
    const payload = JSON.parse(options.body);
    assert.deepEqual(payload.location, {rack_id:10});
    assert.deepEqual(payload.rack, {lot:'LOT-R10-EDIT'});
    assert.equal(payload.rack_id, 10);
    assert.equal(payload.expected_racks['10'], 'R10-TOKEN');
    return Promise.resolve({...snapshot(), location:{...liveLocation,rack_id:10}, racks:[{...baseRack,id:10,lot:'LOT-R10-EDIT'}], expected_racks:{10:'R10-SAVED'}});
  };
  await run('saveContextCell()');
  run('closeContextCell()');
  sandbox.adminToken = null;
  response = () => Promise.resolve(snapshot());
  await run('openContextCell(5)');
  assert.equal(run('contextCellDraft.view'), 'user');
  assert.equal(document.getElementById('contextViewAdmin').hidden, true);
  assert.equal(run("changeContextCellView('admin')"), false);
  const beforeAnonSave = requests.length;
  await run('saveContextCell()');
  assert.equal(requests.length, beforeAnonSave);
  run('closeContextCell()');

  const beforeRepeat = requests.length;
  const installed = {
    submit: form.listeners.submit.length,
    user: document.getElementById('contextViewUser').listeners.click.length,
    admin: document.getElementById('contextViewAdmin').listeners.click.length,
    rack: field('rack_id').listeners.change.length,
    canvas: canvas.listeners.click.length,
  };
  for (let i=0; i<40; i++) { await run('openContextCell(5)'); run('closeContextCell()'); }
  assert.equal(requests.length - beforeRepeat, 40, 'only one GET for each repeated opening');
  assert.equal(form.listeners.submit.length, installed.submit);
  assert.equal(document.getElementById('contextViewUser').listeners.click.length, installed.user);
  assert.equal(document.getElementById('contextViewAdmin').listeners.click.length, installed.admin);
  assert.equal(field('rack_id').listeners.change.length, installed.rack);
  assert.equal(canvas.listeners.click.length, installed.canvas);

  console.log('PASS: existing editor regressions + User/Admin defaults, four editable fields, payload allowlist, Admin capabilities, selective discard, session loss/recovery, no requests on switching, empty cell and rack-specific baselines');
})().catch(error => { console.error(error); process.exitCode = 1; });
