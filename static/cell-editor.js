/* Independent draft: runtime events update caches, never these form controls. */
const contextCellDialog = document.getElementById('contextCellDialog');
const contextCellForm = document.getElementById('contextCellForm');
const contextCellOffer = document.getElementById('contextCellOffer');
let contextCellDraft = null;
let contextCellOpening = false;
let contextCellPointer = null;
const contextLocationKeys = ['code', 'area_id', 'status', 'enabled', 'is_visible', 'rack_id', 'note'];
const contextRackKeys = ['code', 'name', 'material_group_id', 'lot', 'quantity', 'manufacturer_code', 'fifo_entered_at', 'last_moved_at', 'comment', 'custom_fields'];
// Presentation modes share authorization; User writes have an explicit allowlist.
const contextUserLocationKeys = ['rack_id'];
const contextUserRackKeys = ['lot', 'material_group_id', 'reserved'];
const contextAdminLocationKeys = contextLocationKeys.filter(key => !contextUserLocationKeys.includes(key));
const contextAdminRackKeys = [...contextRackKeys, 'reserved'].filter(key => !contextUserRackKeys.includes(key));
const contextControl = name => contextCellForm.elements.namedItem(name);
const contextMessage = text => { document.getElementById('contextCellError').textContent = text; };

function contextReadFields(keys, prefix = '') {
  const result = {};
  for (const key of keys) {
    const raw = contextControl(prefix + key).value;
    if (['status', 'enabled', 'is_visible', 'quantity'].includes(key)) result[key] = Number(raw);
    else if (['area_id', 'rack_id', 'material_group_id'].includes(key)) result[key] = raw ? Number(raw) : null;
    // Preserve the original timestamp (including seconds/timezone) unless edited.
    else result[key] = raw;
  }
  return result;
}

function contextReadRack() {
  return { ...contextReadFields(contextRackKeys, 'rack_'), reserved: Number(contextControl('rack_reserved').value) };
}

function contextRenderCustomFields(rows) {
  contextCellDraft.customRows = rows.map(row => ({ ...row }));
  contextControl('rack_custom_fields').value = JSON.stringify(contextCellDraft.customRows);
  const container = document.getElementById('contextCustomFields');
  const elements = contextCellDraft.customRows.map((row, index) => {
    const group = document.createElement('div');
    group.className = 'context-cell-grid';
    for (const [key, caption] of [['label', 'Nombre visible'], ['value', 'Valor']]) {
      const label = document.createElement('label');
      label.textContent = caption;
      const input = document.createElement('input');
      input.value = row[key] ?? '';
      input.dataset.contextCustomIndex = String(index);
      input.dataset.contextCustomField = key;
      if (key === 'label') { input.required = true; input.maxLength = 128; }
      label.append(input);
      group.append(label);
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'btn danger'; remove.textContent = 'Quitar característica';
    remove.dataset.contextCustomRemove = String(index);
    group.append(remove);
    return group;
  });
  container.replaceChildren(...elements);
}

function contextDiff(before, after) {
  return Object.fromEntries(Object.entries(after).filter(([key, value]) => JSON.stringify(before[key]) !== JSON.stringify(value)));
}

function contextIsDirty() {
  const draft = contextCellDraft;
  return !!draft && (Object.keys(contextDiff(draft.locationForm, contextReadFields(contextLocationKeys))).length > 0
    || (draft.rackId && Object.keys(contextDiff(draft.rackForm, contextReadRack())).length > 0));
}

function contextHasAdminChanges() {
  const draft = contextCellDraft;
  if (!draft) return false;
  return contextAdminLocationKeys.some(key => contextReadFields([key])[key] !== draft.locationForm[key])
    || (draft.rackId && contextAdminRackKeys.some(key => contextReadRack()[key] !== draft.rackForm[key]));
}

function contextDiscardAdminChanges() {
  const draft = contextCellDraft;
  for (const key of contextAdminLocationKeys) contextControl(key).value = draft.locationForm[key] ?? '';
  for (const key of contextAdminRackKeys) {
    if (key === 'custom_fields') contextRenderCustomFields(JSON.parse(draft.rackForm[key]));
    else contextControl('rack_' + key).value = draft.rackForm[key] ?? '';
  }
  draft.suspendedAdminDraft = false;
}

function changeContextCellView(view) {
  const draft = contextCellDraft;
  if (!draft || draft.busy || !['user', 'admin'].includes(view) || (view === 'admin' && !adminToken)) return false;
  if (view === draft.view) return true;
  if (view === 'user' && contextHasAdminChanges()) {
    if (!window.confirm('Hay cambios exclusivos de Admin pendientes. Aceptar descarta solo esos cambios y pasa a Usuario. Cancelar permanece en Admin.')) return false;
    contextDiscardAdminChanges();
  }
  draft.view = view;
  if (view === 'admin') draft.suspendedAdminDraft = false;
  syncContextCellPermissions();
  return true;
}

function contextSetOptions(control, rows, selectedId, emptyLabel) {
  const options = [new Option(emptyLabel, '')];
  for (const row of rows) options.push(new Option(`${row.code || row.id} - ${row.name || ''}`, String(row.id)));
  if (selectedId && !rows.some(row => Number(row.id) === Number(selectedId))) options.push(new Option(`ID ${selectedId} (actual)`, String(selectedId)));
  control.replaceChildren(...options);
  control.value = selectedId == null ? '' : String(selectedId);
}

function syncContextCellPermissions() {
  if (!contextCellDraft) return;
  const draft = contextCellDraft;
  if (!adminToken && draft.view === 'admin') {
    draft.suspendedAdminDraft = !!contextHasAdminChanges();
    draft.view = 'user'; // Hide Admin immediately; retain every draft value.
  }
  const disabled = !adminToken || draft.busy;
  const adminView = draft.view === 'admin' && !!adminToken;
  document.getElementById('contextViewUser').disabled = draft.busy;
  document.getElementById('contextViewUser').setAttribute('aria-pressed', String(!adminView));
  document.getElementById('contextViewAdmin').hidden = !adminToken;
  document.getElementById('contextViewAdmin').disabled = disabled;
  document.getElementById('contextViewAdmin').setAttribute('aria-pressed', String(adminView));
  contextCellDialog.querySelectorAll('[data-context-admin]').forEach(element => { element.hidden = !adminView; });
  document.getElementById('contextLocationFields').disabled = disabled;
  document.getElementById('contextRackFields').disabled = disabled || !draft.rackId;
  contextUserLocationKeys.forEach(key => { contextControl(key).disabled = disabled; });
  contextUserRackKeys.forEach(key => { contextControl('rack_' + key).disabled = disabled || !draft.rackId; });
  for (const key of contextAdminLocationKeys) contextControl(key).disabled = disabled || !adminView || (key === 'status' && !!draft.rackId);
  for (const key of contextAdminRackKeys) contextControl('rack_' + key).disabled = disabled || !adminView || !draft.rackId;
  document.getElementById('contextCustomAdminFields').disabled = disabled || !adminView || !draft.rackId;
  document.getElementById('contextRackHint').textContent = draft.rackId
    ? (adminView ? 'El estado ocupado se deriva de la asignación del rack.' : '')
    : 'Selecciona un rack existente para editar su reserva, lote y material.';
  document.getElementById('contextCellSave').disabled = disabled || draft.uncertain;
  document.getElementById('contextCellReload').disabled = draft.busy;
  document.getElementById('contextCellCancel').disabled = draft.busy;
  document.getElementById('contextCellPermission').textContent = adminToken
    ? (draft.suspendedAdminDraft ? 'Tu borrador de Admin se conserva. Vuelve a Admin para revisarlo antes de guardar.' : 'Ambas vistas usan la sesión administrativa actual. Los cambios se guardan juntos.')
    : 'Solo consulta. Inicia sesión administrativa para editar. El borrador pendiente se conserva.';
}

function contextFillRack(rack) {
  const draft = contextCellDraft;
  draft.rackId = rack ? Number(rack.id) : null;
  for (const key of contextRackKeys) {
    const control = contextControl('rack_' + key);
    if (key === 'material_group_id') contextSetOptions(control, catalog.materials.filter(item => Number(item.is_active) === 1), rack?.[key], 'SIN_MATERIAL');
    else if (key === 'custom_fields') contextRenderCustomFields(rack?.custom_fields || []);
    else if (key.endsWith('_at')) control.value = toLocalInputValue(rack?.[key]);
    else control.value = rack?.[key] ?? (key === 'quantity' ? 0 : '');
  }
  contextControl('rack_reserved').value = isReservedState(rack?.reservation_status) ? '1' : '0';
  document.getElementById('contextRackTask').textContent = rack
    ? `${rack.reservation_status || 'No reservado'} · Tarea: ${reservationTaskLabel(rack)}` : 'Casilla sin rack';
  draft.rackForm = contextReadRack();
  syncContextCellPermissions();
}

function contextInstallSnapshot(snapshot, { view = contextCellDraft?.view || 'user' } = {}) {
  const previous = contextCellDraft;
  contextCellDraft = {
    id: Number(snapshot.location.id), snapshot,
    racks: new Map(snapshot.racks.map(rack => [Number(rack.id), rack])),
    expectedRacks: { ...snapshot.expected_racks }, busy: false, uncertain: false,
    view, suspendedAdminDraft: false,
    returnFocus: previous?.returnFocus || document.activeElement,
  };
  const location = snapshot.location;
  const area = catalog.areas.find(row => Number(row.id) === Number(location.area_id));
  document.getElementById('contextCellReference').textContent = `Casilla: ${location.code || 'Sin código'}\nID ${location.id} · Coordenadas (${location.x}, ${location.y})\nÁrea: ${location.area_name || area?.name || area?.code || (location.area_id ? `ID ${location.area_id}` : 'Sin área')}`;
  for (const key of contextLocationKeys) {
    const control = contextControl(key);
    if (key === 'area_id') contextSetOptions(control, catalog.areas.filter(item => Number(item.is_active) === 1), location.area_id, 'Sin área');
    else if (key === 'rack_id') {
      const rows = catalog.racks.filter(rack => !locationByRackId.has(Number(rack.id)) || Number(rack.id) === Number(location.rack_id));
      contextSetOptions(control, rows, location.rack_id, 'Sin rack');
    } else control.value = location[key] ?? '';
  }
  contextCellDraft.locationForm = contextReadFields(contextLocationKeys);
  contextFillRack(contextCellDraft.racks.get(Number(location.rack_id)));
  contextMessage('');
}

async function openContextCell(locationId) {
  if (contextCellOpening || contextCellDialog.open || !locationId || isMultiSelectionActive()) return;
  contextCellOpening = true;
  contextCellOffer.hidden = true;
  try {
    const snapshot = await fetchJson(`/api/cell-editor/${Number(locationId)}`);
    contextInstallSnapshot(snapshot, { view: 'user' });
    contextCellDialog.showModal();
    (adminToken ? contextControl('rack_id') : document.getElementById('contextCellCancel')).focus();
  } catch (error) { cellMsg.textContent = `No se pudo abrir la casilla: ${error.message}`; }
  finally { contextCellOpening = false; }
}

function closeContextCell() {
  if (contextCellDraft?.busy) return false;
  if (contextIsDirty() && !window.confirm('Hay cambios pendientes. ¿Descartarlos y cerrar?')) return false;
  const returnFocus = contextCellDraft?.returnFocus;
  contextCellDialog.close();
  contextCellDraft = null;
  returnFocus?.focus();
  return true;
}

async function changeContextRack() {
  const draft = contextCellDraft;
  if (!draft || draft.busy) return;
  const nextId = Number(contextControl('rack_id').value) || null;
  if (nextId === draft.rackId) return;
  if (draft.rackId && Object.keys(contextDiff(draft.rackForm, contextReadRack())).length
      && !window.confirm('El rack anterior tiene cambios pendientes. ¿Descartarlos para cambiar de rack?')) {
    contextControl('rack_id').value = draft.rackId || '';
    return;
  }
  draft.busy = true;
  syncContextCellPermissions();
  try {
    if (nextId && !draft.racks.has(nextId)) {
      const snapshot = await fetchJson(`/api/cell-editor/${draft.id}?rack_id=${nextId}`);
      if (snapshot.expected_location !== draft.snapshot.expected_location) throw new Error('La casilla cambió. Recarga para revisar; tu borrador se conserva.');
      const rack = snapshot.racks.find(item => Number(item.id) === nextId);
      if (!rack) throw new Error('Rack no encontrado');
      draft.racks.set(nextId, rack);
      draft.expectedRacks[nextId] = snapshot.expected_racks[nextId];
    }
    contextFillRack(draft.racks.get(nextId));
    contextMessage('');
  } catch (error) {
    contextControl('rack_id').value = draft.rackId || '';
    contextMessage(error.message);
  } finally { draft.busy = false; syncContextCellPermissions(); }
}

async function saveContextCell(event) {
  event?.preventDefault();
  const draft = contextCellDraft;
  if (!draft || draft.busy || draft.uncertain || !adminToken) return;
  if (draft.view === 'user' && draft.suspendedAdminDraft && contextHasAdminChanges()) {
    contextMessage('El borrador de Admin sigue pendiente. Vuelve a Admin para revisarlo o descartarlo antes de guardar.');
    return;
  }
  if (!contextCellForm.reportValidity()) return;
  let location, rack, reserved;
  try {
    const adminView = draft.view === 'admin';
    location = contextDiff(draft.locationForm, contextReadFields(adminView ? contextLocationKeys : contextUserLocationKeys));
    const rackValues = adminView ? contextReadRack() : { ...contextReadFields(contextUserRackKeys.filter(key => key !== 'reserved'), 'rack_'), reserved: Number(contextControl('rack_reserved').value) };
    rack = draft.rackId ? contextDiff(draft.rackForm, rackValues) : {};
    // An assigned rack determines occupancy. Never submit a hidden/disabled override.
    if (draft.rackId) delete location.status;
    reserved = rack.reserved;
    delete rack.reserved;
    if ('custom_fields' in rack) {
      rack.custom_fields = JSON.parse(rack.custom_fields);
      if (!Array.isArray(rack.custom_fields) || rack.custom_fields.some(item => !item || typeof item !== 'object' || Array.isArray(item) || !String(item.label || '').trim())) {
        throw new Error('Las características deben ser una lista de objetos con label y value.');
      }
    }
    for (const key of ['fifo_entered_at', 'last_moved_at']) if (key in rack) rack[key] = fromLocalInputValue(rack[key]);
    for (const key of ['name', 'lot', 'manufacturer_code', 'comment']) if (key in rack) rack[key] = rack[key].trim() || null;
    if (!Object.keys(location).length && !Object.keys(rack).length && reserved === undefined) { contextMessage('Sin cambios pendientes.'); return; }
  } catch (error) { contextMessage(error.message); return; }
  draft.busy = true;
  syncContextCellPermissions();
  contextMessage('Guardando…');
  try {
    const payload = { expected_location: draft.snapshot.expected_location, expected_racks: draft.expectedRacks, location, rack_id: draft.rackId, rack };
    if (reserved !== undefined) payload.reserved = reserved;
    const result = await fetchJson(`/api/admin/cell-editor/${draft.id}`, {
      method: 'PUT', headers: fetchHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(payload),
    });
    // Merge only affected entities, with existing stale-event protection. One draw.
    for (const row of result.racks) applyRackUpdated(row, { render: false });
    applyLocationUpdated(result.location, { draw: false });
    updateLocationsCacheRecord(result.location, locationsRevision).catch(() => {});
    renderRackOptions();
    renderPodPositionRackOptions();
    renderRackList();
    renderDirectMoveSelection();
    draw();
    contextInstallSnapshot(result);
    contextMessage('Cambios guardados.');
  } catch (error) {
    // An absent/5xx response can follow a committed write. Never repeat it blindly.
    draft.uncertain = !error.status || error.status >= 500;
    contextMessage(draft.uncertain
      ? 'No se pudo confirmar el resultado. Tu borrador se conserva. Recarga para revisar antes de volver a guardar.'
      : `${error.status === 409 ? 'Conflicto: ' : ''}${error.message} Tu borrador se conserva.`);
  } finally { contextCellDraft.busy = false; syncContextCellPermissions(); }
}

async function reloadContextCell() {
  const draft = contextCellDraft;
  if (!draft || draft.busy) return;
  if ((contextIsDirty() || draft.uncertain) && !window.confirm('¿Descartar el borrador y cargar los valores actuales para revisarlos?')) return;
  draft.busy = true;
  syncContextCellPermissions();
  try { contextInstallSnapshot(await fetchJson(`/api/cell-editor/${draft.id}`)); }
  catch (error) { contextMessage(error.message); }
  finally { contextCellDraft.busy = false; syncContextCellPermissions(); }
}

document.getElementById('btnContextCell').addEventListener('click', () => {
  if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && !multiSelectMode && !freeLayoutDrag && !dragging) {
    openContextCell(getLocationAtGrid(selected.x, selected.y)?.id);
  }
});
contextCellForm.addEventListener('submit', saveContextCell);
document.getElementById('contextViewUser').addEventListener('click', () => changeContextCellView('user'));
document.getElementById('contextViewAdmin').addEventListener('click', () => changeContextCellView('admin'));
contextControl('rack_id').addEventListener('change', changeContextRack);
document.getElementById('contextCustomFields').addEventListener('input', event => {
  const draft = contextCellDraft;
  if (!draft || draft.busy || !adminToken || draft.view !== 'admin') return;
  const { contextCustomIndex: index, contextCustomField: key } = event.target.dataset;
  if (index === undefined || !['label', 'value'].includes(key)) return;
  draft.customRows[Number(index)][key] = event.target.value;
  contextControl('rack_custom_fields').value = JSON.stringify(draft.customRows);
});
document.getElementById('contextCustomFields').addEventListener('click', event => {
  const index = event.target.dataset.contextCustomRemove;
  if (index === undefined || !contextCellDraft || contextCellDraft.busy || !adminToken || contextCellDraft.view !== 'admin') return;
  const rows = contextCellDraft.customRows.filter((_, i) => i !== Number(index));
  contextRenderCustomFields(rows);
});
document.getElementById('contextAddCustomField').addEventListener('click', () => {
  const draft = contextCellDraft;
  if (!draft || draft.busy || !adminToken || !draft.rackId || draft.view !== 'admin') return;
  let suffix = draft.customRows.length + 1;
  while (draft.customRows.some(row => row.key === `field_${suffix}`)) suffix++;
  contextRenderCustomFields([...draft.customRows, { key: `field_${suffix}`, label: '', value: '' }]);
});
document.getElementById('contextCellCancel').addEventListener('click', closeContextCell);
document.getElementById('contextCellReload').addEventListener('click', reloadContextCell);
contextCellDialog.addEventListener('cancel', event => { event.preventDefault(); closeContextCell(); });
// Capture intent before the canvas handlers can end a point-picking mode.
canvas.addEventListener('mousedown', event => {
  contextCellOffer.hidden = true;
  contextCellPointer = event.button === 0 && !event.ctrlKey && !event.metaKey && !multiSelectMode
    && !operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && !isFreeLayoutEditing()
    ? { x: event.clientX, y: event.clientY } : null;
}, true);
canvas.addEventListener('click', event => {
  const start = contextCellPointer;
  contextCellPointer = null;
  if (!start || suppressNextCanvasClick || isMultiSelectionActive() || event.ctrlKey || event.metaKey
      || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) return;
  const rect = canvas.getBoundingClientRect();
  const loc = isFreeLayoutMode() ? hitTestFreeLocation(event.clientX - rect.left, event.clientY - rect.top)
    : (() => { const point = canvasToGrid(event.clientX - rect.left, event.clientY - rect.top); return point && getLocationAtGrid(point.x, point.y); })();
  if (!loc?.id) return;
  contextCellOffer.dataset.locationId = String(loc.id);
  contextCellOffer.style.left = `${Math.max(8, Math.min(event.clientX + 12, window.innerWidth - 165))}px`;
  contextCellOffer.style.top = `${Math.max(8, Math.min(event.clientY + 12, window.innerHeight - 50))}px`;
  contextCellOffer.hidden = false;
}, true);
contextCellOffer.addEventListener('click', () => openContextCell(Number(contextCellOffer.dataset.locationId)));
window.addEventListener('beforeunload', event => {
  if (contextIsDirty()) { event.preventDefault(); event.returnValue = ''; }
});
