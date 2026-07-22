console.log("[startup] app.js cargado");
console.log("APP_BUILD_ID", window.APP_BUILD_ID || "unknown");
window.addEventListener("error", function(e) {
  console.error("[GLOBAL ERROR]", e.error || e.message);
});

const API = {
  locations: "/api/locations",
  location: (x, y) => `/api/locations/${x}/${y}`,
  gridConfig: "/api/grid-config",
  catalog: "/api/catalog",
  adminLogin: "/api/admin/login",
  adminLocPatch: (x, y) => `/api/admin/locations/${x}/${y}`,
  adminLocSave: (x, y) => `/api/admin/locations/${x}/${y}`,
  adminLocFreeCreate: "/api/admin/locations/free",
  adminLocFreeLayout: (id) => `/api/admin/locations/${id}/free-layout`,
  adminLocFreeLayoutFromGrid: "/api/admin/locations/free-layout/from-grid",
  adminGrid: "/api/admin/grid-config",
  adminChangePwd: "/api/admin/change-password",
  background: "/api/background",
  adminBgUpload: "/api/admin/background",
  adminBgTransform: "/api/admin/background/transform",
  adminClientIpGet: "/api/admin/client-ip",
  adminClientIpSet: "/api/admin/client-ip",
  adminRcsConfigGet: "/api/admin/rcs-config",
  adminRcsConfigSet: "/api/admin/rcs-config",
  adminRcsConfigTest: "/api/admin/rcs-config/test",
  adminRackSyncPreview: "/api/admin/rcs/rack-sync/preview",
  adminRackSyncQuery: "/api/admin/rcs/rack-sync/query",
  adminRackSyncBind: "/api/admin/rcs/rack-sync/bind-mismatches",
  adminRackSyncHistory: "/api/admin/rcs/rack-sync/history",
  rcsConfigPublic: "/api/rcs-config-public",
  adminHideConfiguredRange: "/api/admin/hide-configured-range",
  adminShowConfiguredRange: "/api/admin/show-configured-range",
  adminAreas: "/api/admin/areas",
  adminArea: (id) => `/api/admin/areas/${id}`,
  adminMaterials: "/api/admin/materials",
  adminMaterial: (id) => `/api/admin/materials/${id}`,
  adminRacks: "/api/admin/racks",
  adminRack: (id) => `/api/admin/racks/${id}`,
  adminRackReservation: (id) => `/api/admin/racks/${id}/reservation`,
  adminCleanupDiagnosis: "/api/admin/cleanup-diagnosis",
  adminCleanupHealth: "/api/admin/cleanup-health",
  adminCleanupCloseInconsistentOrders: "/api/admin/cleanup-close-inconsistent-orders",
  adminCleanupResolveInconsistentRacks: "/api/admin/cleanup-resolve-inconsistent-racks",
  adminForceReleaseOldActiveRacks: "/api/admin/force-release-old-active-racks",
  adminCreateOldActiveOrderTest: "/api/admin/test/create-old-active-order",
  adminSoftwareUpdateValidate: "/api/admin/software-update/validate",
  adminSoftwareUpdateApply: "/api/admin/software-update/apply",
  adminSoftwareUpdateRestart: "/api/admin/software-update/restart",
  adminDatabaseDownload: "/api/admin/database/download",
  adminFullBackupDownload: "/api/admin/backup/full",
  adminBackupValidate: "/api/admin/backup/validate",
  adminBackupRestore: "/api/admin/backup/restore",
  adminBackupStatus: "/api/admin/backup/status",
  adminBackupMarkRestarted: "/api/admin/backup/mark-restarted",
  adminPreRestoreBackups: "/api/admin/backup/pre-restore/list",
  adminPreRestoreBackupDownload: (filename) => `/api/admin/backup/pre-restore/download/${encodeURIComponent(filename)}`,
  health: "/api/health",
  fifoValidate: "/api/fifo/validate",
  fifoExecute: "/api/fifo/execute",
  directMoveExecute: "/api/direct-move/execute",
  movementOrders: "/api/movement-orders",
  movementOrderJson: (id) => `/api/movement-orders/${id}/json`,
  movementOrderJsonSave: (id) => `/api/movement-orders/${id}/json`,
  movementOrderJsonReset: (id) => `/api/movement-orders/${id}/json/reset`,
  movementOrderDispatch: (id) => `/api/movement-orders/${id}/dispatch`,
  movementOrderDispatchResponse: (id) => `/api/movement-orders/${id}/dispatch-response`,
  movementOrderSimulateComplete: (id) => `/api/movement-orders/${id}/simulate-complete`,
  movementOrderUndo: (id) => `/api/movement-orders/${id}/undo`,
  movementOrderMonitorRun: '/api/movement-orders/monitor/run',
  movementOrderStatusQueryTemplate: (id) => `/api/movement-orders/${id}/status-query-template`,
  movementOrderStatusQuery: (id) => `/api/movement-orders/${id}/status-query`,
  podPositionQuery: "/api/rcs/pod-position",
  rcsDebugLog: '/api/rcs/debug-log',
  rcsDebugSend: '/api/rcs/debug-send',
  robotStatusMonitor: '/api/robot-status-monitor',
  robotControlStop: '/api/robot-control/stop',
  robotControlResume: '/api/robot-control/resume',
  runtimeSnapshot: '/api/runtime-snapshot',
  runtimeWs: (() => { const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'; return `${proto}://${window.location.host}/ws/runtime`; })(),
  operatorWindows: "/api/operator-windows",
  operatorWindowAccess: (id) => `/api/operator-windows/${id}/access`,
  operatorWindowPreview: (id, idx) => `/api/operator-windows/${id}/buttons/${idx}/preview`,
  operatorWindowExecute: (id, idx) => `/api/operator-windows/${id}/buttons/${idx}/execute`,
  adminOperatorWindows: "/api/admin/operator-windows",
  adminOperatorWindow: (id) => `/api/admin/operator-windows/${id}`,
  adminOperatorWindowButton: (id, idx) => `/api/admin/operator-windows/${id}/buttons/${idx}`,
  adminDeleteOperatorWindow: (id) => `/api/admin/operator-windows/${id}`,
  adminDeleteMovementOrder: (id) => `/api/admin/movement-orders/${id}`,
  adminScannerStations: "/api/admin/scanner-stations",
  adminScannerStation: (id) => `/api/admin/scanner-stations/${id}`,
  adminQrActionRules: "/api/admin/qr-action-rules",
  adminQrActionRule: (id) => `/api/admin/qr-action-rules/${id}`,
  adminQrActionRuleImage: (id, size = 240) => `/api/admin/qr-action-rules/${id}/qr-image?size=${encodeURIComponent(size)}`,
  adminQrTransitionRules: "/api/admin/qr-transition-rules",
  adminQrTransitionRule: (id) => `/api/admin/qr-transition-rules/${id}`,
  adminQrTransitionPreview: (id) => `/api/admin/qr-transition-rules/preview/${encodeURIComponent(id)}`,
  adminQrTransitionApply: (id) => `/api/admin/qr-transition-rules/apply/${encodeURIComponent(id)}`,
  adminQrTransitionLogs: "/api/admin/qr-transition-logs",
  adminScanTerminals: "/api/admin/scan-terminals",
  adminScanTerminal: (id) => `/api/admin/scan-terminals/${id}`,
  scanPreview: "/api/scan/preview",
  scanExecute: "/api/scan/execute",
  scanEvents: "/api/scan/events",
};

const DB_W = 100;
const DB_H = 100;
let GRID_W = 100;
let GRID_H = 100;
let locations = new Array(DB_W * DB_H).fill(null);
let selected = { x: 0, y: 0 };
const KEEP_VALUE = "__keep";
let multiSelectedLocationIds = new Set();
let multiSelectMode = false;
let multiSelectBox = null;
let adminToken = null;
const ADMIN_TOKEN_STORAGE_KEY = "agvAdminToken";
const ADMIN_EXPIRES_STORAGE_KEY = "agvAdminExpiresAt";
let lastRackSyncData = null;
let mapLayoutMode = "grid";
let freeLayoutDrag = null;
let suppressNextCanvasClick = false;
let catalog = { areas: [], materials: [], racks: [] };
let hoverCell = null;
let hoverPointer = { x: 0, y: 0 };
let runtimeAutoRefreshHandle = null;
let runtimeRefreshInFlight = false;
let robotMonitorRefreshInFlight = false;
let robotMonitorEnabled = true;
let latestRobotMonitorItems = [];
const robotVisualState = new Map();
let robotAnimationFrame = null;
const ROBOT_TRAIL_MAX_POINTS = 18;
const ROBOT_TRAIL_MIN_MOVE = 6;
const agvOverlayConfig = { scale_x: 1, scale_y: 1, offset_x: 0, offset_y: 0, rotation_deg: 0, orientation_offset_deg: 0, mirror_x: 0, mirror_y: 0, icon_angle_mirror: 0 };
let runtimeSocket = null;
let runtimeSocketReconnectHandle = null;
let runtimeSocketConnected = false;
let debugConsoleHoverPaused = false;
let debugConsolePendingEntries = null;
let RUNTIME_AUTO_REFRESH_MS = 5000;
let RUNTIME_SOCKET_RECONNECT_MS = 3000;
const EDITING_GRACE_MS = 10000;
let editingLock = {
  active: false,
  section: null,
  since: 0,
  lastUserEditAt: 0,
  pendingRuntimeSnapshot: null,
  pendingCatalogRender: false,
};
const ROBOT_MONITOR_POS_KEY = "agv_robot_monitor_pos_v1";
const ROBOT_MONITOR_SIZE_KEY = "agv_robot_monitor_size_v1";

const $ = (id) => document.getElementById(id);
const canvas = $("gridCanvas");
const ctx = canvas.getContext("2d");
const connStatus = $("connStatus");
const layoutEl = document.querySelector(".layout");
const splitterEl = $("splitter");
const splitterVEl = $("splitterV");
const btnCenterGrid = $("btnCenterGrid");
const cellHoverTooltip = $("cellHoverTooltip");
const SIDE_PANEL_WIDTH_KEY = "agv_side_panel_width";
const CARD_COLLAPSE_KEY_PREFIX = "agv_card_v3_collapsed_";

const adminPwd = $("adminPwd");
const btnAdminLogin = $("btnAdminLogin");
const btnAdminLock = $("btnAdminLock");
const adminState = $("adminState");
const adminActions = $("adminActions");
const adminMsg = $("adminMsg");
const cleanupDiagnosisBtn = $("cleanupDiagnosisBtn");
const cleanupHealthBadge = $("cleanupHealthBadge");
const cleanupDiagnosisModal = $("cleanupDiagnosisModal");
const btnRefreshCleanupDiagnosis = $("btnRefreshCleanupDiagnosis");
const btnSelectSafeCleanup = $("btnSelectSafeCleanup");
const btnCleanSelected = $("btnCleanSelected");
const btnCloseSelectedOrders = $("btnCloseSelectedOrders");
const btnReleaseSelectedRacks = $("btnReleaseSelectedRacks");
const btnResolveSelectedInconsistentRacks = $("btnResolveSelectedInconsistentRacks");
const cleanupConfirmModal = $("cleanupConfirmModal");
const btnCancelCleanupClose = $("btnCancelCleanupClose");
const btnConfirmCleanupClose = $("btnConfirmCleanupClose");
const cleanupDiagnosisGeneratedAt = $("cleanupDiagnosisGeneratedAt");
const cleanupDiagnosisMsg = $("cleanupDiagnosisMsg");
const cleanupConfirmMsg = $("cleanupConfirmMsg");
const diagnosisOrphanRacks = $("diagnosisOrphanRacks");
const diagnosisInconsistentOrders = $("diagnosisInconsistentOrders");
const diagnosisInconsistentRacks = $("diagnosisInconsistentRacks");
const diagnosisOldActiveRacks = $("diagnosisOldActiveRacks");
const diagnosisStuckCancelRecoverable = $("diagnosisStuckCancelRecoverable");
const diagnosisInconsistentLocations = $("diagnosisInconsistentLocations");
const diagnosisIntegrityCheck = $("diagnosisIntegrityCheck");
const btnForceReleaseOldActiveRacks = $("btnForceReleaseOldActiveRacks");
const createOldActiveOrderTestBtn = $("createOldActiveOrderTestBtn");
const softwareUpdateZip = $("softwareUpdateZip");
const btnValidateSoftwareUpdate = $("btnValidateSoftwareUpdate");
const btnApplySoftwareUpdate = $("btnApplySoftwareUpdate");
const btnRestartSoftwareUpdate = $("btnRestartSoftwareUpdate");
const softwareUpdateResult = $("softwareUpdateResult");
const btnOpenDbBackupsModal = $("btnOpenDbBackupsModal");
const dbBackupsModal = $("dbBackupsModal");
const btnCloseDbBackupsModal = $("btnCloseDbBackupsModal");
const btnDownloadDb = $("btnDownloadDb");
const btnDownloadFullBackup = $("btnDownloadFullBackup");
const backupZipFile = $("backupZipFile");
const btnChooseBackupFile = $("btnChooseBackupFile");
const btnValidateBackup = $("btnValidateBackup");
const btnRestoreBackup = $("btnRestoreBackup");
const backupRestartPendingBadge = $("backupRestartPendingBadge");
const btnMarkBackupRestarted = $("btnMarkBackupRestarted");
const backupSelectedFileName = $("backupSelectedFileName");
const dbBackupsMsg = $("dbBackupsMsg");
const preRestoreBackupsList = $("preRestoreBackupsList");
const backupRestoreConfirmModal = $("backupRestoreConfirmModal");
const btnCancelBackupRestore = $("btnCancelBackupRestore");
const btnConfirmBackupRestore = $("btnConfirmBackupRestore");
const backupRestoreConfirmMsg = $("backupRestoreConfirmMsg");
let selectedBackupFile = null;
let lastBackupValidationOk = false;
let restorePendingRestart = false;
let lastSoftwareUpdateValidation = null;

const dispRows = $("dispRows");
const dispCols = $("dispCols");
const mapLayoutModeSelect = $("mapLayoutMode");
const freeLayoutEditEnabled = $("freeLayoutEditEnabled");
const btnAddFreeCell = $("btnAddFreeCell");
const freeLayoutMsg = $("freeLayoutMsg");
const btnSaveGeneralConfig = $("btnSaveGeneralConfig");
const btnHideConfiguredRange = $("btnHideConfiguredRange");
const btnShowConfiguredRange = $("btnShowConfiguredRange");
const rangeMsg = $("rangeMsg");
const agvOverlayScaleX = $("agvOverlayScaleX");
const agvOverlayScaleY = $("agvOverlayScaleY");
const agvOverlayOffsetX = $("agvOverlayOffsetX");
const agvOverlayOffsetY = $("agvOverlayOffsetY");
const agvOverlayRotationDeg = $("agvOverlayRotationDeg");
const agvOrientationOffsetDeg = $("agvOrientationOffsetDeg");
const agvOverlayMirrorX = $("agvOverlayMirrorX");
const agvOverlayMirrorY = $("agvOverlayMirrorY");
const agvIconAngleMirror = $("agvIconAngleMirror");
const btnResetAgvOverlaySettings = $("btnResetAgvOverlaySettings");
const runtimeRefreshSeconds = $("runtimeRefreshSeconds");
const runtimeReconnectSeconds = $("runtimeReconnectSeconds");
const btnResetRuntimeRefreshSettings = $("btnResetRuntimeRefreshSettings");
const runtimeSettingsMsg = $("runtimeSettingsMsg");

function safeNumberInput(el, fallback) {
  const raw = el?.value;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function safeFlagValue(el, fallback) {
  const raw = el?.value;
  if (raw === undefined || raw === null || raw === '') return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? (n ? 1 : 0) : fallback;
}

const bgFile = $("bgFile");
const btnUploadBg = $("btnUploadBg");
const bgScaleX = $("bgScaleX");
const bgScaleY = $("bgScaleY");
const bgOffX = $("bgOffX");
const bgOffY = $("bgOffY");
const btnSaveBgTransform = $("btnSaveBgTransform");
const btnResetBgTransform = $("btnResetBgTransform");
const bgStateLbl = $("bgStateLbl");
const clientIp = $("clientIp");
const btnSaveClientIp = $("btnSaveClientIp");
const rcsBaseUrl = $("rcsBaseUrl");
const rcsEndpoint = $("rcsEndpoint");
const rcsQueryEndpoint = $("rcsQueryEndpoint");
const rcsCancelEndpoint = $("rcsCancelEndpoint");
const rcsStopEndpoint = $("rcsStopEndpoint");
const rcsResumeEndpoint = $("rcsResumeEndpoint");
const rcsAgvStatusEndpoint = $("rcsAgvStatusEndpoint");
const rcsPodPositionEndpoint = $("rcsPodPositionEndpoint");
const rcsRackSyncQueryEndpoint = $("rcsRackSyncQueryEndpoint");
const rcsRackSyncBindEndpoint = $("rcsRackSyncBindEndpoint");
const rcsRackSyncScheduleEnabled = $("rcsRackSyncScheduleEnabled");
const rcsRackSyncScheduleTime = $("rcsRackSyncScheduleTime");
const rcsTaskMonitorInterval = $("rcsTaskMonitorInterval");
const rcsAgvMonitorInterval = $("rcsAgvMonitorInterval");
const cleanupMinAgeMinutes = $("cleanupMinAgeMinutes");
const forceReleaseMinAgeMinutes = $("forceReleaseMinAgeMinutes");
const cancelUndoAutoRecoveryEnabled = $("cancelUndoAutoRecoveryEnabled");
const cancelUndoAutoRecoveryMinAge = $("cancelUndoAutoRecoveryMinAge");
const rcsEnableMapShortName = $("rcsEnableMapShortName");
const rcsMapShortName = $("rcsMapShortName");
const rcsEnableMapCode = $("rcsEnableMapCode");
const rcsMapCode = $("rcsMapCode");
const rcsEnableAmrMonitor = $("rcsEnableAmrMonitor");
const rcsTokenCode = $("rcsTokenCode");
const rcsVerifyTls = $("rcsVerifyTls");
const rcsAuthHeader = $("rcsAuthHeader");
const btnSaveRcsConfig = $("btnSaveRcsConfig");
const btnTestRcsConfig = $("btnTestRcsConfig");
const rcsResolvedInfo = $("rcsResolvedInfo");
const rcsConfigMsg = $("rcsConfigMsg");
const podPositionRack = $("podPositionRack");
const btnQueryPodPosition = $("btnQueryPodPosition");
const podPositionResult = $("podPositionResult");
const podPositionMsg = $("podPositionMsg");
const btnRackSyncPreview = $("btnRackSyncPreview");
const btnRackSyncQuery = $("btnRackSyncQuery");
const btnRackSyncBind = $("btnRackSyncBind");
const btnRackSyncHistory = $("btnRackSyncHistory");
const rackSyncResult = $("rackSyncResult");
const rackSyncMsg = $("rackSyncMsg");
const oldPwd = $("oldPwd");
const newPwd = $("newPwd");
const btnChangePwd = $("btnChangePwd");

const selectedCellTitle = $("selectedCellTitle");
const cellX = $("cellX");
const cellY = $("cellY");
const cellCode = $("cellCode");
const cellStatus = $("cellStatus");
const cellEnabled = $("cellEnabled");
const cellVisible = $("cellVisible");
const cellArea = $("cellArea");
const cellRack = $("cellRack");
const cellReservationState = $("cellReservationState");
const cellReservationRack = $("cellReservationRack");
const cellReservationTask = $("cellReservationTask");
const cellNote = $("cellNote");
const btnSelectCellsByArea = $("btnSelectCellsByArea");
const btnSaveCell = $("btnSaveCell");
const cellMsg = $("cellMsg");
const cellEditRefreshNotice = $("cellEditRefreshNotice");
const cellEditRefreshActions = $("cellEditRefreshActions");
const btnRefreshCellAfterEdit = $("btnRefreshCellAfterEdit");

const areaId = $("areaId");
const areaCode = $("areaCode");
const areaName = $("areaName");
const areaType = $("areaType");
const areaColor = $("areaColor");
const areaPriority = $("areaPriority");
const areaActive = $("areaActive");
const areaMatterArea = $("areaMatterArea");
const areaDescription = $("areaDescription");
const btnSaveArea = $("btnSaveArea");
const btnNewArea = $("btnNewArea");
const btnDeleteArea = $("btnDeleteArea");
const areasList = $("areasList");
const areaMsg = $("areaMsg");
const areaEditRefreshNotice = $("areaEditRefreshNotice");
const areaEditRefreshActions = $("areaEditRefreshActions");
const btnRefreshAreasAfterEdit = $("btnRefreshAreasAfterEdit");

function editingSectionElements(section) {
  if (section === "cell") {
    return [cellCode, cellStatus, cellEnabled, cellVisible, cellArea, cellRack, cellReservationState, cellNote].filter(Boolean);
  }
  if (section === "areas") {
    return [areaCode, areaName, areaType, areaColor, areaPriority, areaActive, areaMatterArea, areaDescription].filter(Boolean);
  }
  return [];
}
function editingSectionHasFocus(section) {
  const activeEl = document.activeElement;
  return !!activeEl && editingSectionElements(section).some(el => el === activeEl || el.contains?.(activeEl));
}
function updateEditRefreshNotices() {
  const pending = !!(editingLock.pendingRuntimeSnapshot || editingLock.pendingCatalogRender);
  const text = pending
    ? "Edicion activa: actualizacion automatica pausada. Hay datos nuevos; se actualizaran al guardar o salir de edicion."
    : "Edicion activa: actualizacion automatica pausada.";
  if (cellEditRefreshNotice) {
    const active = editingLock.active && editingLock.section === "cell";
    cellEditRefreshNotice.textContent = text;
    cellEditRefreshNotice.classList.toggle("hidden", !active);
    cellEditRefreshActions?.classList.toggle("hidden", !active);
  }
  if (areaEditRefreshNotice) {
    const active = editingLock.active && editingLock.section === "areas";
    areaEditRefreshNotice.textContent = text;
    areaEditRefreshNotice.classList.toggle("hidden", !active);
    areaEditRefreshActions?.classList.toggle("hidden", !active);
  }
}
function markEditingActivity(section, dirty = true) {
  const now = Date.now();
  editingLock.active = true;
  editingLock.section = section;
  if (!editingLock.since) editingLock.since = now;
  if (dirty || !editingLock.lastUserEditAt) editingLock.lastUserEditAt = now;
  updateEditRefreshNotices();
}
function finishEditingLock(section = null, options = {}) {
  if (section && editingLock.section !== section) return;
  const pendingSnapshot = editingLock.pendingRuntimeSnapshot;
  const pendingCatalogRender = editingLock.pendingCatalogRender;
  editingLock = {
    active: false,
    section: null,
    since: 0,
    lastUserEditAt: 0,
    pendingRuntimeSnapshot: null,
    pendingCatalogRender: false,
  };
  updateEditRefreshNotices();
  if (options.applyPending === false) return;
  window.setTimeout(() => {
    if (pendingCatalogRender) renderDeferredCatalogParts();
    if (pendingSnapshot) applyRuntimeSnapshotData(pendingSnapshot, selectedOrderId, { forceAdminRender: true });
  }, 0);
}
async function refreshAfterEditingLock(section = null) {
  finishEditingLock(section, { applyPending: false });
  if (section === "areas") {
    await loadCatalog();
    return;
  }
  if (section === "cell") {
    await Promise.all([loadCatalog(), loadMovementOrders(selectedOrderId)]);
    return;
  }
  await Promise.all([loadCatalog(), loadMovementOrders(selectedOrderId)]);
}
function isEditingLockEffective(section = null) {
  if (!editingLock.active) return false;
  if (section && editingLock.section !== section) return false;
  const hasFocus = editingSectionHasFocus(editingLock.section);
  const lastEdit = Number(editingLock.lastUserEditAt || editingLock.since || 0);
  const withinGrace = Date.now() - lastEdit < EDITING_GRACE_MS;
  if (hasFocus || withinGrace) return true;
  finishEditingLock(editingLock.section);
  return false;
}
function deferAdminRefresh() {
  editingLock.pendingCatalogRender = true;
  updateEditRefreshNotices();
}
function renderDeferredCatalogParts() {
  renderAreaOptions({ force: true });
  renderRackOptions({ force: true });
  renderAreaList({ force: true });
  updateEditRefreshNotices();
}
function bindEditingLockEvents(section, elements) {
  elements.forEach(el => {
    el.addEventListener("focus", () => markEditingActivity(section, false));
    el.addEventListener("input", () => markEditingActivity(section, true));
    el.addEventListener("change", () => markEditingActivity(section, true));
  });
}

const materialId = $("materialId");
const materialCode = $("materialCode");
const materialName = $("materialName");
const materialColor = $("materialColor");
const materialActive = $("materialActive");
const materialDescription = $("materialDescription");
const btnSaveMaterial = $("btnSaveMaterial");
const btnNewMaterial = $("btnNewMaterial");
const btnDeleteMaterial = $("btnDeleteMaterial");
const materialsList = $("materialsList");
const materialMsg = $("materialMsg");

const rackId = $("rackId");
const rackCode = $("rackCode");
const rackName = $("rackName");
const rackStatus = $("rackStatus");
const rackMaterial = $("rackMaterial");
const rackReservationState = $("rackReservationState");
const rackReservationTask = $("rackReservationTask");
const rackLot = $("rackLot");
const rackQty = $("rackQty");
const rackMfgCode = $("rackMfgCode");
const rackFifo = $("rackFifo");
const rackMoved = $("rackMoved");
const rackComment = $("rackComment");
const rackCustomFields = $("rackCustomFields");
const btnAddRackCustomField = $("btnAddRackCustomField");
const btnSaveRack = $("btnSaveRack");
const btnNewRack = $("btnNewRack");
const btnDeleteRack = $("btnDeleteRack");
const racksList = $("racksList");
const rackMsg = $("rackMsg");
const scannerStationId = $("scannerStationId");
const scannerCode = $("scannerCode");
const scannerName = $("scannerName");
const scannerDescription = $("scannerDescription");
const scannerStationType = $("scannerStationType");
const scannerDefaultAction = $("scannerDefaultAction");
const scannerRouteMode = $("scannerRouteMode");
const scannerSourceArea = $("scannerSourceArea");
const scannerDestinationArea = $("scannerDestinationArea");
const scannerSourceCell = $("scannerSourceCell");
const scannerDestinationCell = $("scannerDestinationCell");
const scannerSourceCellSummary = $("scannerSourceCellSummary");
const scannerDestinationCellSummary = $("scannerDestinationCellSummary");
const scannerSecondSourceArea = $("scannerSecondSourceArea");
const scannerSecondDestinationArea = $("scannerSecondDestinationArea");
const scannerSecondSourceCell = $("scannerSecondSourceCell");
const scannerSecondDestinationCell = $("scannerSecondDestinationCell");
const scannerSecondSourceCellSummary = $("scannerSecondSourceCellSummary");
const scannerSecondDestinationCellSummary = $("scannerSecondDestinationCellSummary");
const scannerStorageArea = $("scannerStorageArea");
const scannerEmptyRackArea = $("scannerEmptyRackArea");
const scannerCancelReturnArea = $("scannerCancelReturnArea");
const scannerCancelReturnAreaWarning = $("scannerCancelReturnAreaWarning");
const scannerDefaultMaterial = $("scannerDefaultMaterial");
const scannerFifoMaterialPolicy = $("scannerFifoMaterialPolicy");
const scannerFifoChainTotalSteps = $("scannerFifoChainTotalSteps");
const scannerFifoChainStep1SourceMode = $("scannerFifoChainStep1SourceMode");
const scannerFifoChainStep1Material = $("scannerFifoChainStep1Material");
const scannerFifoChainStep2SourceMode = $("scannerFifoChainStep2SourceMode");
const scannerFifoChainStep2Material = $("scannerFifoChainStep2Material");
const scannerFifoChainStep3SourceMode = $("scannerFifoChainStep3SourceMode");
const scannerFifoChainStep3Material = $("scannerFifoChainStep3Material");
const scannerFifoChainStep3SourceArea = $("scannerFifoChainStep3SourceArea");
const scannerFifoChainStep3DestinationArea = $("scannerFifoChainStep3DestinationArea");
const scannerFifoChainStep3SourceCell = $("scannerFifoChainStep3SourceCell");
const scannerFifoChainStep3DestinationCell = $("scannerFifoChainStep3DestinationCell");
const scannerFifoChainStep3SourceCellSummary = $("scannerFifoChainStep3SourceCellSummary");
const scannerFifoChainStep3DestinationCellSummary = $("scannerFifoChainStep3DestinationCellSummary");
const scannerFifoChainStep4SourceMode = $("scannerFifoChainStep4SourceMode");
const scannerFifoChainStep4Material = $("scannerFifoChainStep4Material");
const scannerFifoChainStep4SourceArea = $("scannerFifoChainStep4SourceArea");
const scannerFifoChainStep4DestinationArea = $("scannerFifoChainStep4DestinationArea");
const scannerFifoChainStep4SourceCell = $("scannerFifoChainStep4SourceCell");
const scannerFifoChainStep4DestinationCell = $("scannerFifoChainStep4DestinationCell");
const scannerFifoChainStep4SourceCellSummary = $("scannerFifoChainStep4SourceCellSummary");
const scannerFifoChainStep4DestinationCellSummary = $("scannerFifoChainStep4DestinationCellSummary");
const scannerAgvCode = $("scannerAgvCode");
const scannerTaskTyp = $("scannerTaskTyp");
const scannerPriority = $("scannerPriority");
const scannerRequirePreview = $("scannerRequirePreview");
const scannerAllowExecute = $("scannerAllowExecute");
const scannerActive = $("scannerActive");
const btnSaveScannerStation = $("btnSaveScannerStation");
const btnNewScannerStation = $("btnNewScannerStation");
const btnDisableScannerStation = $("btnDisableScannerStation");
const scannerStationsList = $("scannerStationsList");
const qrRuleId = $("qrRuleId");
const qrValue = $("qrValue");
const qrAlias = $("qrAlias");
const qrDescription = $("qrDescription");
const qrType = $("qrType");
const qrMatchType = $("qrMatchType");
const qrActionType = $("qrActionType");
const qrMaterial = $("qrMaterial");
const qrFifoMaterialPolicy = $("qrFifoMaterialPolicy");
const qrFifoMaterialPolicyHelp = $("qrFifoMaterialPolicyHelp");
const qrFifoChainTotalSteps = $("qrFifoChainTotalSteps");
const qrFifoChainStep1SourceMode = $("qrFifoChainStep1SourceMode");
const qrFifoChainStep1Material = $("qrFifoChainStep1Material");
const qrFifoChainStep2SourceMode = $("qrFifoChainStep2SourceMode");
const qrFifoChainStep2Material = $("qrFifoChainStep2Material");
const qrFifoChainStep3SourceMode = $("qrFifoChainStep3SourceMode");
const qrFifoChainStep3Material = $("qrFifoChainStep3Material");
const qrFifoChainStep3SourceArea = $("qrFifoChainStep3SourceArea");
const qrFifoChainStep3DestinationArea = $("qrFifoChainStep3DestinationArea");
const qrFifoChainStep3SourceCell = $("qrFifoChainStep3SourceCell");
const qrFifoChainStep3DestinationCell = $("qrFifoChainStep3DestinationCell");
const qrFifoChainStep3SourceCellSummary = $("qrFifoChainStep3SourceCellSummary");
const qrFifoChainStep3DestinationCellSummary = $("qrFifoChainStep3DestinationCellSummary");
const qrFifoChainStep4SourceMode = $("qrFifoChainStep4SourceMode");
const qrFifoChainStep4Material = $("qrFifoChainStep4Material");
const qrFifoChainStep4SourceArea = $("qrFifoChainStep4SourceArea");
const qrFifoChainStep4DestinationArea = $("qrFifoChainStep4DestinationArea");
const qrFifoChainStep4SourceCell = $("qrFifoChainStep4SourceCell");
const qrFifoChainStep4DestinationCell = $("qrFifoChainStep4DestinationCell");
const qrFifoChainStep4SourceCellSummary = $("qrFifoChainStep4SourceCellSummary");
const qrFifoChainStep4DestinationCellSummary = $("qrFifoChainStep4DestinationCellSummary");
const qrRack = $("qrRack");
const qrRouteMode = $("qrRouteMode");
const qrSourceArea = $("qrSourceArea");
const qrDestinationArea = $("qrDestinationArea");
const qrSourceCell = $("qrSourceCell");
const qrDestinationCell = $("qrDestinationCell");
const qrSourceCellSummary = $("qrSourceCellSummary");
const qrDestinationCellSummary = $("qrDestinationCellSummary");
const qrSecondSourceArea = $("qrSecondSourceArea");
const qrSecondDestinationArea = $("qrSecondDestinationArea");
const qrSecondSourceCell = $("qrSecondSourceCell");
const qrSecondDestinationCell = $("qrSecondDestinationCell");
const qrSecondSourceCellSummary = $("qrSecondSourceCellSummary");
const qrSecondDestinationCellSummary = $("qrSecondDestinationCellSummary");
const qrPriority = $("qrPriority");
const qrAgvCode = $("qrAgvCode");
const qrTaskTyp = $("qrTaskTyp");
const qrRequiresScanner = $("qrRequiresScanner");
const qrActive = $("qrActive");
const btnSaveQrRule = $("btnSaveQrRule");
const btnNewQrRule = $("btnNewQrRule");
const btnDisableQrRule = $("btnDisableQrRule");
const qrRulesList = $("qrRulesList");
const qrRulePreviewModal = $("qrRulePreviewModal");
const qrRuleModalTitle = $("qrRuleModalTitle");
const qrRuleModalAlias = $("qrRuleModalAlias");
const qrRuleModalValue = $("qrRuleModalValue");
const qrRuleModalImage = $("qrRuleModalImage");
const qrRuleModalImageError = $("qrRuleModalImageError");
const qrRuleModalMeta = $("qrRuleModalMeta");
const qrRulePrintLabel = $("qrRulePrintLabel");
const btnCloseQrRuleModal = $("btnCloseQrRuleModal");
const btnCloseQrRuleModalX = $("btnCloseQrRuleModalX");
const btnPrintQrRuleLabel = $("btnPrintQrRuleLabel");
const btnRefreshScanEvents = $("btnRefreshScanEvents");
const scanEventsList = $("scanEventsList");
const scanEventDetailBox = $("scanEventDetailBox");
const scanEventDetailJson = $("scanEventDetailJson");
const qrAdminMsg = $("qrAdminMsg");
const qrTransitionRuleId = $("qrTransitionRuleId");
const qrTransitionName = $("qrTransitionName");
const qrTransitionDescription = $("qrTransitionDescription");
const qrTransitionScope = $("qrTransitionScope");
const qrTransitionMatchMode = $("qrTransitionMatchMode");
const qrTransitionIgnoreCurrentMaterial = $("qrTransitionIgnoreCurrentMaterial");
const qrTransitionSourceMatchMode = $("qrTransitionSourceMatchMode");
const qrTransitionAnySourceHelp = $("qrTransitionAnySourceHelp");
const qrTransitionSimpleHelp = $("qrTransitionSimpleHelp");
const qrTransitionQrRule = $("qrTransitionQrRule");
const qrTransitionScanner = $("qrTransitionScanner");
const qrTransitionSourceArea = $("qrTransitionSourceArea");
const qrTransitionDestinationArea = $("qrTransitionDestinationArea");
const qrTransitionSourceCell = $("qrTransitionSourceCell");
const qrTransitionDestinationCell = $("qrTransitionDestinationCell");
const qrTransitionCurrentMaterialLabel = $("qrTransitionCurrentMaterialLabel");
const qrTransitionCurrentMaterial = $("qrTransitionCurrentMaterial");
const qrTransitionCurrentRackStatus = $("qrTransitionCurrentRackStatus");
const qrTransitionNextMaterial = $("qrTransitionNextMaterial");
const qrTransitionNextRackStatus = $("qrTransitionNextRackStatus");
const qrTransitionNextQuantity = $("qrTransitionNextQuantity");
const qrTransitionClearQuantity = $("qrTransitionClearQuantity");
const qrTransitionNextComment = $("qrTransitionNextComment");
const qrTransitionAppendComment = $("qrTransitionAppendComment");
const qrTransitionApplyOn = $("qrTransitionApplyOn");
const qrTransitionPriority = $("qrTransitionPriority");
const qrTransitionActive = $("qrTransitionActive");
const btnSaveQrTransitionRule = $("btnSaveQrTransitionRule");
const btnNewQrTransitionRule = $("btnNewQrTransitionRule");
const btnDisableQrTransitionRule = $("btnDisableQrTransitionRule");
const qrTransitionRulesList = $("qrTransitionRulesList");
const qrTransitionPreviewOrderId = $("qrTransitionPreviewOrderId");
const btnPreviewQrTransition = $("btnPreviewQrTransition");
const btnApplyQrTransition = $("btnApplyQrTransition");
const qrTransitionPreviewResult = $("qrTransitionPreviewResult");
const btnRefreshQrTransitionLogs = $("btnRefreshQrTransitionLogs");
const qrTransitionLogsList = $("qrTransitionLogsList");
const scanTerminalId = $("scanTerminalId");
const scanTerminalCode = $("scanTerminalCode");
const scanTerminalName = $("scanTerminalName");
const scanTerminalDescription = $("scanTerminalDescription");
const scanTerminalScannerStation = $("scanTerminalScannerStation");
const scanTerminalApiKey = $("scanTerminalApiKey");
const scanTerminalMode = $("scanTerminalMode");
const scanTerminalAllowExecute = $("scanTerminalAllowExecute");
const scanTerminalRequirePreview = $("scanTerminalRequirePreview");
const scanTerminalActive = $("scanTerminalActive");
const scanTerminalLastSeen = $("scanTerminalLastSeen");
const scanTerminalLastIp = $("scanTerminalLastIp");
const btnSaveScanTerminal = $("btnSaveScanTerminal");
const btnNewScanTerminal = $("btnNewScanTerminal");
const btnRefreshScanTerminals = $("btnRefreshScanTerminals");
const btnDisableScanTerminal = $("btnDisableScanTerminal");
const scanTerminalsList = $("scanTerminalsList");
const scanQrScannerSelect = $("scanQrScannerSelect");
const scanQrScannerManual = $("scanQrScannerManual");
const scanQrScannerHelp = $("scanQrScannerHelp");
const scanQrValue = $("scanQrValue");
const btnScanQrPreview = $("btnScanQrPreview");
const btnScanQrExecute = $("btnScanQrExecute");
const scanQrResultPanel = $("scanQrResultPanel");
const btnRefreshScanQrHistory = $("btnRefreshScanQrHistory");
const scanQrHistoryList = $("scanQrHistoryList");
const scanQrMsg = $("scanQrMsg");
const fifoSourceArea = $("fifoSourceArea");
const fifoDestinationArea = $("fifoDestinationArea");
const fifoMaterial = $("fifoMaterial");
const fifoPriority = $("fifoPriority");
const fifoAgvCode = $("fifoAgvCode");
const fifoTaskTyp = $("fifoTaskTyp");
const fifoComment = $("fifoComment");
const btnValidateFifo = $("btnValidateFifo");
const btnExecuteFifo = $("btnExecuteFifo");
const fifoPreviewBox = $("fifoPreviewBox");
const fifoMsg = $("fifoMsg");
const directSourceCellLabel = $("directSourceCellLabel");
const directDestinationCellLabel = $("directDestinationCellLabel");
const btnDirectPickSource = $("btnDirectPickSource");
const btnDirectPickDestination = $("btnDirectPickDestination");
const btnDirectClearSelection = $("btnDirectClearSelection");
const directPriority = $("directPriority");
const directAgvCode = $("directAgvCode");
const directTaskTyp = $("directTaskTyp");
const directComment = $("directComment");
const btnExecuteDirectMove = $("btnExecuteDirectMove");
const directMsg = $("directMsg");
const ordersList = $("ordersList");
const orderDetailBox = $("orderDetailBox");
const orderMsg = $("orderMsg");
const btnRefreshOrders = $("btnRefreshOrders");
const btnSendStatusQuery = $("btnSendStatusQuery");
const btnRefreshOrderJson = $("btnRefreshOrderJson");
const btnSaveOrderJson = $("btnSaveOrderJson");
const btnResetOrderJson = $("btnResetOrderJson");
const btnCopyOrderJson = $("btnCopyOrderJson");
const btnDispatchOrder = $("btnDispatchOrder");
const btnRefreshOrderResponse = $("btnRefreshOrderResponse");
const btnSimulateComplete = $("btnSimulateComplete");
const btnUndoOrder = $("btnUndoOrder");
const btnDeleteOrder = $("btnDeleteOrder");
const orderJsonBox = $("orderJsonBox");
const orderJsonSource = $("orderJsonSource");
const orderResponseBox = $("orderResponseBox");
const orderStatusQueryMode = $("orderStatusQueryMode");
const orderStatusQueryModeHelp = $("orderStatusQueryModeHelp");
const orderStatusQueryRequestBox = $("orderStatusQueryRequestBox");
const orderStatusQueryResponseBox = $("orderStatusQueryResponseBox");
const btnFormatStatusQuery = $("btnFormatStatusQuery");
const btnCopyStatusQuery = $("btnCopyStatusQuery");
const btnClearStatusQuery = $("btnClearStatusQuery");
const statusQueryManualActions = $("statusQueryManualActions");
const statusQueryBaseUrl = $("statusQueryBaseUrl");
const statusQueryEndpoint = $("statusQueryEndpoint");
const robotMonitorPanel = $("robotMonitorPanel");
const robotMonitorHeader = $("robotMonitorHeader");
const robotMonitorBody = $("robotMonitorBody");
const robotMonitorSubtitle = $("robotMonitorSubtitle");
const robotMonitorResizeHandle = $("robotMonitorResizeHandle");
const btnRobotMonitorRefresh = $("btnRobotMonitorRefresh");
const operatorWindowSelect = $("operatorWindowSelect");
const operatorWindowPassword = $("operatorWindowPassword");
const btnOpenOperatorWindow = $("btnOpenOperatorWindow");
const operatorWindowTitle = $("operatorWindowTitle");
const operatorButtonsBox = $("operatorButtonsBox");
const operatorWindowMsg = $("operatorWindowMsg");
const adminWindowSelect = $("adminWindowSelect");
const btnNewOperatorWindow = $("btnNewOperatorWindow");
const operatorWindowId = $("operatorWindowId");
const operatorWindowName = $("operatorWindowName");
const operatorWindowActive = $("operatorWindowActive");
const operatorWindowBgColor = $("operatorWindowBgColor");
const operatorWindowButtonCount = $("operatorWindowButtonCount");
const operatorWindowPasswordAdmin = $("operatorWindowPasswordAdmin");
const btnSaveOperatorWindow = $("btnSaveOperatorWindow");
const btnDeleteOperatorWindow = $("btnDeleteOperatorWindow");
const operatorButtonList = $("operatorButtonList");
const operatorButtonIndex = $("operatorButtonIndex");
const operatorButtonActive = $("operatorButtonActive");
const operatorButtonLabel = $("operatorButtonLabel");
const operatorButtonColor = $("operatorButtonColor");
const operatorButtonMode = $("operatorButtonMode");
const operatorButtonPriority = $("operatorButtonPriority");
const operatorButtonSourceArea = $("operatorButtonSourceArea");
const operatorButtonDestinationArea = $("operatorButtonDestinationArea");
const operatorButtonMaterial = $("operatorButtonMaterial");
const operatorButtonSourceCell = $("operatorButtonSourceCell");
const operatorButtonDestinationCell = $("operatorButtonDestinationCell");
const btnOperatorButtonPickSource = $("btnOperatorButtonPickSource");
const btnOperatorButtonPickDestination = $("btnOperatorButtonPickDestination");
const operatorButtonSourceCellLabel = $("operatorButtonSourceCellLabel");
const operatorButtonDestinationCellLabel = $("operatorButtonDestinationCellLabel");
const operatorButtonAgv = $("operatorButtonAgv");
const operatorButtonTaskTyp = $("operatorButtonTaskTyp");
const operatorButtonComment = $("operatorButtonComment");
const operatorButtonCancelMatterArea = $("operatorButtonCancelMatterArea");
const btnSaveOperatorButton = $("btnSaveOperatorButton");
const operatorWindowAdminMsg = $("operatorWindowAdminMsg");
const operatorButtonFifoFields = $("operatorButtonFifoFields");
const operatorButtonPointAreaFields = $("operatorButtonPointAreaFields");
const operatorButtonPointDestinationArea = $("operatorButtonPointDestinationArea");
const operatorButtonPointMaterialList = $("operatorButtonPointMaterialList");
const operatorButtonPointCustomFields = $("operatorButtonPointCustomFields");
const btnAddOperatorPointField = $("btnAddOperatorPointField");
const operatorButtonDirectFields = $("operatorButtonDirectFields");
const operatorButtonCancelFields = $("operatorButtonCancelFields");
const operatorActionPanel = $("operatorActionPanel");
const operatorActionPanelTitle = $("operatorActionPanelTitle");
const operatorActionPanelModeLabel = $("operatorActionPanelModeLabel");
const operatorActionInlineCells = $("operatorActionInlineCells");
const operatorActionInlineAreaRow = $("operatorActionInlineAreaRow");
const operatorActionAreaDestinationLabel = $("operatorActionAreaDestinationLabel");
const operatorActionAreaSelect = $("operatorActionAreaSelect");
const operatorActionAreaWrap = $("operatorActionAreaWrap");
const operatorActionDestinationCellWrap = $("operatorActionDestinationCellWrap");
const operatorActionRackInfoRow = $("operatorActionRackInfoRow");
const operatorActionRackInfo = $("operatorActionRackInfo");
const operatorActionPointFields = $("operatorActionPointFields");
const operatorActionMaterial = $("operatorActionMaterial");
const operatorActionAgv = $("operatorActionAgv");
const operatorActionTaskTyp = $("operatorActionTaskTyp");
const operatorActionAgvTaskRow = $("operatorActionAgvTaskRow");
const btnOperatorActionPreview = $("btnOperatorActionPreview");
const btnOperatorActionPanelClear = $("btnOperatorActionPanelClear");
const operatorActionModal = $("operatorActionModal");
const operatorActionModalTitle = $("operatorActionModalTitle");
const operatorActionPreview = $("operatorActionPreview");
const operatorActionModalMsg = $("operatorActionModalMsg");
const operatorCancelModeRow = $("operatorCancelModeRow");
const operatorCancelMode = $("operatorCancelMode");
const operatorCancelModeHint = $("operatorCancelModeHint");
const operatorCancelReturnAreaRow = $("operatorCancelReturnAreaRow");
const operatorCancelReturnArea = $("operatorCancelReturnArea");
const operatorCancelReturnAreaHint = $("operatorCancelReturnAreaHint");
const btnOperatorActionCancel = $("btnOperatorActionCancel");
const btnOperatorActionConfirm = $("btnOperatorActionConfirm");
const operatorActionDynamicFields = $("operatorActionDynamicFields");
const operatorActionSourceLabel = $("operatorActionSourceLabel");
const operatorActionDestinationLabel = $("operatorActionDestinationLabel");
const btnOperatorActionPickSource = $("btnOperatorActionPickSource");
const btnOperatorActionPickDestination = $("btnOperatorActionPickDestination");
let lastFifoPreview = null;
let movementOrders = [];
let selectedOrderId = null;
let selectedOrderJsonPayload = null;
let selectedOrderJsonSource = "generated";
let selectedOrderResponsePayload = null;
let debugConsoleEvents = [];
const CARD_ORDER = [
  "card-workstation",
  "card-scan-qr",
  "card-history",
  "card-cell",
  "card-areas",
  "card-materials",
  "card-racks",
  "card-operator-windows",
  "card-qr-scanners",
  "card-general",
  "card-direct-move",
  "card-fifo",
  "card-client-bg",
  "card-debug-rcs",
  "card-pod-position",
  "card-rack-sync",
  "card-config-rcs",
  "card-admin-password",
  "card-admin-login",
];
const NON_ADMIN_VISIBLE_CARD_IDS = new Set(["card-workstation", "card-scan-qr", "card-history", "card-admin-login"]);
const DEFAULT_EXPANDED_CARD_IDS = new Set(["card-workstation", "card-scan-qr", "card-history", "card-admin-login"]);

let isEditingOrderJson = false;
let isEditingStatusQueryRequest = false;
let directMovePickMode = null;
let directMoveSourceCellId = null;
let directMoveDestinationCellId = null;
let operatorWindows = [];
let activeOperatorWindow = null;
let selectedAdminWindowId = null;
let selectedAdminWindowButtonIndex = null;
const POINT_FIELD_DEFS = {
  lot: { label: 'Lote', type: 'text', placeholder: 'Lote' },
  quantity: { label: 'Cantidad', type: 'number', placeholder: '0' },
  manufacturer_code: { label: 'Número de fabricante', type: 'text', placeholder: 'Número de fabricante' },
  comment: { label: 'Comentario', type: 'text', placeholder: 'Comentario' },
};
let operatorActionState = { button: null, preview: null, source_cell_id: null, destination_cell_id: null, destination_area_id: null, material_group_id: null, point_field_values: {} };
let operatorActionPickMode = null;
let actionModalContext = { mode: null, orderId: null };
let operatorButtonPickMode = null;
let rackReservationOriginal = "0";
const OPERATOR_LAST_POINT_KEY = "agv_operator_point_area_defaults_v1";
let scannerStations = [];
let qrActionRules = [];
let qrTransitionRules = [];
let qrTransitionLogs = [];
const qrRuleImageObjectUrls = new Map();
let qrRuleModalImageObjectUrl = null;
let scanEvents = [];
let scanTerminals = [];
let scanQrPreviewInFlight = false;

const BASE_CELL = 22;
const BASE_GAP = 2;
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 10;
let canvasCssW = 1000;
let canvasCssH = 1000;
let dragging = false;
let dragStart = { x: 0, y: 0, offX: 0, offY: 0 };
let bgSaveTimer = null;
const bgState = { img: null, loaded: false, url: null, scale_x: 1, scale_y: 1, offset_x: 0, offset_y: 0 };
const cam = { offX: 0, offY: 0, scale: 1 };
const touchState = { mode: null, lastPan: null, pinchStartDistance: 0, pinchStartScale: 1, pinchCenter: null };

function idx(x, y) { return y * DB_W + x; }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function setConn(msg) { if (connStatus) connStatus.textContent = msg; }
function isTypingInInput() { const el = document.activeElement; return !!el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName); }
function fetchHeaders(extra = {}) { return adminToken ? { ...extra, "X-Admin-Token": adminToken } : { ...extra }; }
function clearAdminSession(reason = "") {
  adminToken = null;
  sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(ADMIN_EXPIRES_STORAGE_KEY);
  // Eliminar residuos de versiones anteriores que persistían la sesión entre pestañas.
  localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
  localStorage.removeItem(ADMIN_EXPIRES_STORAGE_KEY);
  restorePendingRestart = false;
  setAdminUI(false);
  // Mantener los datos cargados visibles; al bloquear solo se deshabilita su edición.
  renderScanQrScannerOptions();
  if (cleanupHealthBadge) cleanupHealthBadge.classList.add("hidden");
  if (reason && adminMsg) adminMsg.textContent = reason;
}
function persistAdminSession(token, expiresHours) {
  const hours = Number(expiresHours || 0);
  // Sesión aislada a la pestaña actual. Sobrevive un refresh, no el cierre de la pestaña.
  sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token);
  sessionStorage.setItem(ADMIN_EXPIRES_STORAGE_KEY, String(Date.now() + Math.max(hours, 1) * 3600000));
}
function adminRequestInfo(url, options = {}) {
  const parsed = new URL(String(url), window.location.origin);
  const isAdminEndpoint = parsed.pathname.startsWith("/api/admin/");
  return {
    parsed,
    isAdminEndpoint,
    isLogin: parsed.pathname === "/api/admin/login",
    method: String(options.method || "GET").toUpperCase(),
  };
}
async function fetchWithAdminSession(url, options = {}) {
  const info = adminRequestInfo(url, options);
  const headers = new Headers(options.headers || {});
  if (info.isAdminEndpoint && !info.isLogin && adminToken && !headers.has("X-Admin-Token")) {
    headers.set("X-Admin-Token", adminToken);
  }
  const sentAdminToken = headers.has("X-Admin-Token");
  const tokenPrefix = adminToken ? adminToken.slice(0, 6) : "-";
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401 && info.isAdminEndpoint && !info.isLogin && sentAdminToken) {
    let detail = "";
    try {
      const rejectedText = await response.clone().text();
      const rejectedBody = rejectedText ? JSON.parse(rejectedText) : null;
      detail = String(rejectedBody?.detail || rejectedText || "").trim();
    } catch (_) {
      detail = "";
    }
    const normalizedDetail = detail.toLowerCase();
    const isTokenRejection = normalizedDetail.includes("admin token")
      && (normalizedDetail.includes("inválido") || normalizedDetail.includes("invalido") || normalizedDetail.includes("expirado"));
    console.warn("[admin-auth] rejected", {
      endpoint: info.parsed.pathname,
      method: info.method,
      sentAdminToken,
      tokenPrefix,
      detail,
    });
    if (isTokenRejection) {
      clearAdminSession("La sesión administrativa expiró. Vuelve a iniciar sesión.");
    }
  }
  return response;
}

async function loadAllAdminDataAfterLogin(reason = "login") {
  if (!adminToken) return false;
  console.info("[admin-auth] loading admin data", { reason });
  await adminLoadClientIp();
  await adminLoadRcsConfig();
  await loadCatalog();
  await loadCleanupHealth();
  await loadAdminOperatorWindows();
  await loadQrAdminData();
  await loadBackupStatus();
  const selectedLocation = locations[idx(selected.x, selected.y)];
  if (selectedLocation) fillCellForm(selectedLocation);
  repairActionTabsLayout();
  return true;
}
async function restoreAdminSession() {
  const storedToken = sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || "";
  const expiresAt = Number(sessionStorage.getItem(ADMIN_EXPIRES_STORAGE_KEY) || 0);
  if (!storedToken || !expiresAt || expiresAt <= Date.now()) {
    clearAdminSession();
    return false;
  }
  adminToken = storedToken;
  try {
    const session = await fetchJson("/api/admin/session", { cache: "no-store" });
    if (!session?.authenticated) throw new Error("Sesión administrativa no autenticada.");
  } catch (err) {
    if (adminToken) clearAdminSession("No fue posible restaurar la sesión administrativa. Vuelve a iniciar sesión.");
    console.warn("[admin-auth] restore failed", err);
    return false;
  }
  setAdminUI(true);
  try {
    await loadAllAdminDataAfterLogin("restore");
    if (adminMsg) adminMsg.textContent = "Admin habilitado.";
  } catch (err) {
    // Un fallo de red o de carga no invalida una sesión que ya fue validada.
    console.warn("[admin-auth] admin data reload failed", err);
    if (adminToken && adminMsg) adminMsg.textContent = "Admin habilitado; algunos datos no pudieron actualizarse. Intenta nuevamente.";
  }
  return !!adminToken;
}
function toLocalInputValue(dt) {
  if (!dt) return "";
  const d = new Date(dt);
  if (Number.isNaN(d.getTime())) return "";
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().slice(0, 16);
}
function fromLocalInputValue(v) { return v ? new Date(v).toISOString() : null; }
function escapeHtml(value) { return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function isReservedState(value) { return String(value || "").trim().toLowerCase() === "reservado"; }
function getLiveReservationForRack(rackId) {
  const id = Number(rackId || 0);
  if (!id) return null;
  const loc = (locations || []).find(l => l && Number(l.rack_id) === id);
  if (!loc) return null;
  return {
    reservation_status: loc.reservation_status,
    reservation_task_id: loc.reservation_task_id,
    reservation_task_identifier: loc.reservation_task_identifier,
    reservation_rack_id: loc.reservation_rack_id,
    reservation_rack_code: loc.reservation_rack_code,
  };
}
function getRackReservationSnapshot(rack) {
  const live = getLiveReservationForRack(rack?.id);
  return {
    reservation_status: live?.reservation_status || rack?.reservation_status || "No reservado",
    reservation_task_id: live?.reservation_task_id ?? rack?.reservation_task_id ?? null,
    reservation_task_identifier: live?.reservation_task_identifier || rack?.reservation_task_identifier || null,
    reservation_rack_id: live?.reservation_rack_id ?? rack?.id ?? null,
    reservation_rack_code: live?.reservation_rack_code || rack?.code || null,
  };
}
function rackReservationText(rack) {
  const snap = getRackReservationSnapshot(rack);
  return isReservedState(snap.reservation_status) ? "Reservado" : "No reservado";
}
function reservationTaskLabel(reservation) {
  const identifier = String(reservation?.reservation_task_identifier || "").trim();
  if (identifier) return identifier;
  const taskId = reservation?.reservation_task_id;
  if (taskId !== null && taskId !== undefined && String(taskId).trim() !== "") return String(taskId);
  return "Sin tarea";
}
function rackReservationTaskText(rack) {
  const snap = getRackReservationSnapshot(rack);
  return reservationTaskLabel(snap);
}
function defaultPointCustomFields() {
  return [
    { key: 'lot', label: POINT_FIELD_DEFS.lot.label },
    { key: 'quantity', label: POINT_FIELD_DEFS.quantity.label },
    { key: 'manufacturer_code', label: POINT_FIELD_DEFS.manufacturer_code.label },
  ];
}
function normalizePointCustomFields(rows) {
  const seen = new Set();
  const out = [];
  (rows || []).forEach(row => {
    const key = String(row?.key || '').trim();
    if (!POINT_FIELD_DEFS[key] || seen.has(key)) return;
    const label = String(row?.label || POINT_FIELD_DEFS[key].label).trim() || POINT_FIELD_DEFS[key].label;
    out.push({ key, label });
    seen.add(key);
  });
  return out;
}
function getButtonPointCustomFields(btn = operatorActionState?.button) {
  const rows = normalizePointCustomFields(btn?.point_custom_fields);
  return rows.length ? rows : defaultPointCustomFields();
}
function getButtonVisibleMaterialIds(btn = operatorActionState?.button) {
  return Array.isArray(btn?.point_visible_material_ids) ? btn.point_visible_material_ids.map(v => Number(v)).filter(Boolean) : [];
}
function getPointFieldValue(key) {
  return operatorActionState?.point_field_values?.[key] ?? '';
}
function setPointFieldValue(key, value) {
  if (!operatorActionState.point_field_values) operatorActionState.point_field_values = {};
  operatorActionState.point_field_values[key] = value;
}


let lastRackCustomFieldTemplate = [];
function normalizeRackCustomFields(rows) {
  const out = [];
  const seen = new Set();
  (rows || []).forEach((row, idx) => {
    const rawLabel = row?.label ?? '';
    const rawValue = row?.value ?? '';
    const label = String(rawLabel).trim();
    const hasMeaningfulData = label || String(rawValue).trim() || row?.key;
    if (!hasMeaningfulData) return;
    let key = String(row?.key || `field_${idx + 1}`).trim() || `field_${idx + 1}`;
    if (seen.has(key)) key = `${key}_${idx + 1}`;
    let value = row?.value;
    if (typeof value === 'string') value = value.trim();
    out.push({ key, label, value });
    seen.add(key);
  });
  return out;
}
function getRackCustomFieldEditorRows() {
  if (!rackCustomFields) return Array.isArray(lastRackCustomFieldTemplate) ? [...lastRackCustomFieldTemplate] : [];
  const rows = [...rackCustomFields.querySelectorAll('[data-rack-custom-row]')].map((row, idx) => ({
    key: row.dataset.rackCustomKey || `field_${idx + 1}`,
    label: row.querySelector(`[data-rack-custom-label="${idx}"]`)?.value || '',
    value: row.querySelector(`[data-rack-custom-value="${idx}"]`)?.value || '',
  }));
  return rows.length ? rows : (Array.isArray(lastRackCustomFieldTemplate) ? [...lastRackCustomFieldTemplate] : []);
}
function renderRackCustomFieldEditor(rows) {
  if (!rackCustomFields) return;
  const editorRows = (rows || []).map((row, idx) => ({
    key: String(row?.key || `field_${idx + 1}`).trim() || `field_${idx + 1}`,
    label: row?.label ?? '',
    value: row?.value ?? '',
  }));
  lastRackCustomFieldTemplate = editorRows.map(row => ({ ...row }));
  rackCustomFields.innerHTML = editorRows.length ? editorRows.map((row, idx) => `
    <div class="row two-col" data-rack-custom-row="${idx}" data-rack-custom-key="${escapeHtml(row.key)}">
      <div><label>Nombre visible</label><input ${adminToken ? '' : 'disabled'} data-rack-custom-label="${idx}" type="text" value="${escapeHtml(row.label || '')}"/></div>
      <div><label>Valor</label><div style="display:flex;gap:8px;"><input ${adminToken ? '' : 'disabled'} data-rack-custom-value="${idx}" type="text" value="${escapeHtml(row.value ?? '')}"/><button class="btn danger" ${adminToken ? '' : 'disabled'} type="button" data-remove-rack-custom="${idx}">Quitar</button></div></div>
    </div>`).join('') : `<div class="small">Sin características configuradas.</div>`;
  rackCustomFields.querySelectorAll('[data-remove-rack-custom]').forEach(btn => btn.addEventListener('click', () => {
    const idx = Number(btn.dataset.removeRackCustom);
    const current = getRackCustomFieldEditorRows();
    current.splice(idx, 1);
    renderRackCustomFieldEditor(current);
  }));
  if (btnAddRackCustomField) btnAddRackCustomField.disabled = !adminToken;
}
function getRackCustomFieldRows() {
  return normalizeRackCustomFields(getRackCustomFieldEditorRows());
}
function addRackCustomFieldRow() {
  const rows = getRackCustomFieldEditorRows();
  rows.push({ key: `field_${rows.length + 1}`, label: '', value: '' });
  renderRackCustomFieldEditor(rows);
}
function getRackDisplayFields(rack) {
  const rows = normalizeRackCustomFields(rack?.custom_fields || []);
  if (rows.length) return rows;
  const fallback = [];
  if (rack?.lot) fallback.push({ key: 'lot', label: 'Lote', value: rack.lot });
  if (rack?.quantity !== undefined && rack?.quantity !== null && rack?.quantity !== '') fallback.push({ key: 'quantity', label: 'Cantidad', value: rack.quantity });
  if (rack?.manufacturer_code) fallback.push({ key: 'manufacturer_code', label: 'Cód. fabricante', value: rack.manufacturer_code });
  if (rack?.comment) fallback.push({ key: 'comment', label: 'Comentario', value: rack.comment });
  return fallback;
}

function getRobotErrorCodeValue(robot) {
  const raw = String(robot?.errorCode ?? robot?.error_code ?? '').trim();
  if (!raw) return '';
  const normalized = raw.toLowerCase();
  if (['0', '0.0', 'null', 'none', 'false', 'ok'].includes(normalized)) return '0';
  return raw;
}

function hasRobotErrorCode(robot) {
  return getRobotErrorCodeValue(robot) !== '' && getRobotErrorCodeValue(robot) !== '0';
}

function isRobotStoppedPauseState(robot) {
  const status = String(robot?.status || '').trim();
  const stopped = String(robot?.stop || '').trim() === '1';
  return status === '5' || stopped;
}

function normalizeRobotBooleanFlag(value) {
  const text = String(value ?? '').trim().toLowerCase();
  if (!text) return false;
  return ['1', 'true', 'yes', 'si', 'sí', 'charging', 'on'].includes(text);
}

function getRobotAgvStateCode(robot) {
  return String(robot?.agvStatus ?? robot?.status ?? '').trim();
}

function getRobotTaskStateValue(robot) {
  const value = String(robot?.taskStatus ?? '').trim();
  return value || '-';
}

function getRobotLocationValue(robot) {
  return String(robot?.positionCode || robot?.currentStation || '').trim() || '-';
}

function getRobotMapShortNameValue(robot) {
  return String(robot?.mapShortName || '').trim() || '-';
}

function getRobotBatteryValue(robot) {
  const value = String(robot?.battery ?? '').trim();
  if (!value) return '-';
  return value.includes('%') ? value : `${value}%`;
}

function getRobotVelocityValue(robot) {
  const value = String(robot?.velocity ?? robot?.speed ?? '').trim();
  return value || '-';
}

function getRobotErrorText(robot) {
  const errorCode = getRobotErrorCodeValue(robot);
  const errorMsg = String(robot?.errorMsg || '').trim();
  if (!errorCode || errorCode === '0') return 'Ninguno';
  return errorMsg ? `${errorCode} - ${errorMsg}` : errorCode;
}

function getRobotMonitorClientState(robot) {
  const status = getRobotAgvStateCode(robot);
  const taskStatus = String(robot?.taskStatus || '').trim().toUpperCase();
  const charging = normalizeRobotBooleanFlag(robot?.charging) || status === '7' || status === '9';
  if (!String(robot?.robotCode || robot?.agvCode || '').trim()) return 'offline';
  if (hasRobotErrorCode(robot)) return 'error';
  if (charging) return 'charging';
  if (['2', '6', '8'].includes(status) || ['EXECUTING', 'BUSY', 'RUNNING', 'IN_PROGRESS'].includes(taskStatus)) return 'busy';
  if (['1', '4'].includes(status) || ['IDLE', 'FREE', 'WAITING'].includes(taskStatus)) return 'idle';
  if (isRobotStoppedPauseState(robot)) return 'offline';
  return 'idle';
}

function getRobotMonitorStateLabel(robot) {
  const state = getRobotMonitorClientState(robot);
  if (state === 'error') return 'ERROR';
  if (state === 'charging') return 'CHARGING';
  if (state === 'busy') return 'BUSY';
  if (state === 'offline') return 'OFFLINE';
  return 'IDLE';
}

function formatRobotStatusTone(robot) {
  const state = getRobotMonitorClientState(robot);
  if (state === 'error') return 'error';
  if (state === 'charging') return 'charging';
  if (state === 'busy') return 'busy';
  if (state === 'offline') return 'offline';
  return 'idle';
}


function translateRobotStatusToSpanish(robot) {
  const raw = String(robot?.statusText || '').trim();
  const code = String(robot?.status || '').trim();
  const map = {
    'Executing task': 'Ejecutando tarea',
    'Lifting the rack': 'Levantando rack',
    'Curve movement': 'Movimiento en curva',
    'Idle': 'Libre',
    'Free': 'Libre',
    'Charging': 'Cargando',
    'Offline': 'Sin conexión',
    'Stopped': 'Pausado',
    'Stop': 'Pausado',
    'Paused': 'Pausado',
    'Emergency stop': 'Paro de emergencia',
    'Error': 'Error',
    'Without task': 'Sin tarea',
  };
  if (map[raw]) return map[raw];
  const byCode = {
    '1': 'Libre',
    '2': 'En tarea',
    '3': 'Error',
    '4': 'Libre',
    '5': 'Pausado',
    '6': 'En tarea',
    '7': 'En espera',
    '8': 'En tarea',
    '9': 'Pausado',
  };
  return byCode[code] || raw || 'Sin estado';
}

function renderRobotMonitorItems(items, message = '') {
  if (!robotMonitorBody) return;
  if (!Array.isArray(items) || !items.length) {
    robotMonitorBody.innerHTML = `<div class="robot-monitor-empty">${escapeHtml(message || 'No hay robots reportados por el RCS.')}</div>`;
    return;
  }
  robotMonitorBody.innerHTML = items.map((robot) => {
    const tone = formatRobotStatusTone(robot);
    const robotCode = String(robot?.agvCode || robot?.robotCode || '').trim() || 'Robot';
    const agvStateLabel = getRobotMonitorStateLabel(robot);
    const taskSummary = getRobotTaskStateValue(robot) !== '-' ? getRobotTaskStateValue(robot) : getRobotTaskSummary(robot);
    const locationValue = getRobotLocationValue(robot);
    const mapShortName = getRobotMapShortNameValue(robot);
    const battery = getRobotBatteryValue(robot);
    const charging = normalizeRobotBooleanFlag(robot?.charging) ? 'Sí' : 'No';
    const velocity = getRobotVelocityValue(robot);
    const errorText = getRobotErrorText(robot);
    return `
      <div class="robot-monitor-card">
        <div class="robot-monitor-card-head">
          <div class="robot-monitor-robot-code">${escapeHtml(robotCode)}</div>
          <div class="robot-monitor-status-badge ${tone}">${escapeHtml(agvStateLabel)}</div>
        </div>
        <div class="robot-monitor-monitor-grid">
          <div class="robot-monitor-meta-item"><b>Estado:</b> ${escapeHtml(translateRobotStatusToSpanish(robot))}</div>
          <div class="robot-monitor-meta-item"><b>Tarea:</b> ${escapeHtml(taskSummary)}</div>
          <div class="robot-monitor-meta-item"><b>Ubicación:</b> ${escapeHtml(locationValue)}</div>
          <div class="robot-monitor-meta-item"><b>Mapa:</b> ${escapeHtml(mapShortName)}</div>
          <div class="robot-monitor-meta-item"><b>Batería:</b> ${escapeHtml(battery)}</div>
          <div class="robot-monitor-meta-item"><b>Cargando:</b> ${escapeHtml(charging)}</div>
          <div class="robot-monitor-meta-item"><b>Velocidad:</b> ${escapeHtml(velocity)}</div>
          <div class="robot-monitor-meta-item"><b>Error:</b> ${escapeHtml(errorText)}</div>
        </div>
        <div class="robot-monitor-controls">
          <button class="btn robot-monitor-action-btn warn" type="button" data-robot-action="stop" data-robot-code="${escapeHtml(robotCode)}">Pausa</button>
          <button class="btn robot-monitor-action-btn primary" type="button" data-robot-action="resume" data-robot-code="${escapeHtml(robotCode)}">Continuar</button>
        </div>
      </div>`;
  }).join('');
}

async function sendRobotControl(action, robotCode, btn) {
  const code = String(robotCode || '').trim();
  if (!code) return;
  const originalText = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '...';
  }
  try {
    const url = action === 'stop' ? API.robotControlStop : API.robotControlResume;
    const data = await fetchJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ robot_code: code }),
    });
    if (!data.ok) throw new Error(data.message || `No se pudo ejecutar ${action}.`);
    if (robotMonitorSubtitle) robotMonitorSubtitle.textContent = `${action === 'stop' ? 'Pausa' : 'Continuar'} enviado a ${code}`;
    await refreshRobotMonitor();
  } catch (err) {
    if (robotMonitorSubtitle) robotMonitorSubtitle.textContent = `Error ${action === 'stop' ? 'pausa' : 'continuar'} ${code}`;
    alert(String(err?.message || err || `Error al ejecutar ${action}.`));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}

function clampRobotMonitorToWrap() {
  const wrap = document.querySelector('.canvas-wrap');
  if (!robotMonitorPanel || !wrap) return;
  const wrapRect = wrap.getBoundingClientRect();
  const panelRect = robotMonitorPanel.getBoundingClientRect();
  const minLeft = 0;
  const minTop = 0;
  const maxLeft = Math.max(0, wrapRect.width - panelRect.width);
  const maxTop = Math.max(0, wrapRect.height - panelRect.height);
  const currentLeft = parseFloat(robotMonitorPanel.style.left || '') || (wrapRect.width * 0.44);
  const currentTop = parseFloat(robotMonitorPanel.style.top || '') || (wrapRect.height * 0.5);
  robotMonitorPanel.style.left = `${clamp(currentLeft, minLeft, maxLeft)}px`;
  robotMonitorPanel.style.top = `${clamp(currentTop, minTop, maxTop)}px`;
  robotMonitorPanel.style.transform = 'none';
}

function saveRobotMonitorLayout() {
  if (!robotMonitorPanel) return;
  const pos = { left: robotMonitorPanel.style.left || '', top: robotMonitorPanel.style.top || '' };
  const size = { width: robotMonitorPanel.style.width || '', height: robotMonitorPanel.style.height || '' };
  localStorage.setItem(ROBOT_MONITOR_POS_KEY, JSON.stringify(pos));
  localStorage.setItem(ROBOT_MONITOR_SIZE_KEY, JSON.stringify(size));
}

function restoreRobotMonitorLayout() {
  if (!robotMonitorPanel) return;
  try {
    const pos = JSON.parse(localStorage.getItem(ROBOT_MONITOR_POS_KEY) || '{}');
    const size = JSON.parse(localStorage.getItem(ROBOT_MONITOR_SIZE_KEY) || '{}');
    if (size.width) robotMonitorPanel.style.width = size.width;
    if (size.height) robotMonitorPanel.style.height = size.height;
    if (pos.left) robotMonitorPanel.style.left = pos.left;
    if (pos.top) robotMonitorPanel.style.top = pos.top;
    if (pos.left || pos.top) robotMonitorPanel.style.transform = 'none';
  } catch (_) {}
  requestAnimationFrame(clampRobotMonitorToWrap);
}

function initRobotMonitorInteractions() {
  if (!robotMonitorPanel || !robotMonitorHeader || !robotMonitorResizeHandle) return;
  restoreRobotMonitorLayout();
  let drag = null;
  let resize = null;

  const onMove = (ev) => {
    const point = ev.touches?.[0] || ev;
    const wrap = document.querySelector('.canvas-wrap');
    if (!wrap) return;
    const wrapRect = wrap.getBoundingClientRect();
    if (drag) {
      const newLeft = point.clientX - wrapRect.left - drag.offsetX;
      const newTop = point.clientY - wrapRect.top - drag.offsetY;
      robotMonitorPanel.style.left = `${newLeft}px`;
      robotMonitorPanel.style.top = `${newTop}px`;
      robotMonitorPanel.style.transform = 'none';
      clampRobotMonitorToWrap();
      ev.preventDefault();
      return;
    }
    if (resize) {
      const minWidth = 220;
      const minHeight = 140;
      const maxWidth = Math.max(minWidth, wrapRect.width - resize.startLeft);
      const maxHeight = Math.max(minHeight, wrapRect.height - resize.startTop);
      const width = clamp(resize.startWidth + (point.clientX - resize.startX), minWidth, maxWidth);
      const height = clamp(resize.startHeight + (point.clientY - resize.startY), minHeight, maxHeight);
      robotMonitorPanel.style.width = `${width}px`;
      robotMonitorPanel.style.height = `${height}px`;
      clampRobotMonitorToWrap();
      ev.preventDefault();
    }
  };

  const onUp = () => {
    if (drag || resize) saveRobotMonitorLayout();
    drag = null;
    resize = null;
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
    window.removeEventListener('touchmove', onMove, { passive: false });
    window.removeEventListener('touchend', onUp);
  };

  const beginListen = () => {
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('touchend', onUp);
  };

  const startDrag = (ev) => {
    const point = ev.touches?.[0] || ev;
    const panelRect = robotMonitorPanel.getBoundingClientRect();
    drag = { offsetX: point.clientX - panelRect.left, offsetY: point.clientY - panelRect.top };
    beginListen();
    ev.preventDefault();
  };

  const startResize = (ev) => {
    const point = ev.touches?.[0] || ev;
    const wrap = document.querySelector('.canvas-wrap');
    if (!wrap) return;
    const wrapRect = wrap.getBoundingClientRect();
    const panelRect = robotMonitorPanel.getBoundingClientRect();
    resize = {
      startX: point.clientX,
      startY: point.clientY,
      startWidth: panelRect.width,
      startHeight: panelRect.height,
      startLeft: panelRect.left - wrapRect.left,
      startTop: panelRect.top - wrapRect.top,
    };
    beginListen();
    ev.preventDefault();
  };

  robotMonitorHeader.addEventListener('mousedown', startDrag);
  robotMonitorHeader.addEventListener('touchstart', startDrag, { passive: false });
  robotMonitorResizeHandle.addEventListener('mousedown', startResize);
  robotMonitorResizeHandle.addEventListener('touchstart', startResize, { passive: false });
  window.addEventListener('resize', clampRobotMonitorToWrap);
}

async function refreshRobotMonitor(options = {}) {
  if (!robotMonitorEnabled) {
    syncRobotVisualTargets([]);
    draw();
    if (robotMonitorSubtitle) robotMonitorSubtitle.textContent = 'Monitoreo deshabilitado';
    renderRobotMonitorItems([], 'Monitoreo AMR deshabilitado desde la configuración RCS.');
    return;
  }
  if (!robotMonitorSubtitle) return;
  const force = !!options.force;
  if (robotMonitorRefreshInFlight && !force) return;
  robotMonitorRefreshInFlight = true;
  try {
    robotMonitorSubtitle.textContent = 'Consultando RCS…';
    const data = await fetchJson(`${API.robotStatusMonitor}?force=${force ? 1 : 0}`, { cache: 'no-store' });
    const robots = Array.isArray(data.robots) ? data.robots : [];
    const count = robots.length;
    syncRobotVisualTargets(robots);
    robotMonitorSubtitle.textContent = count > 0 ? '' : (data.message || (data.ok ? 'Consulta exitosa sin robots.' : 'Sin conexión'));
    renderRobotMonitorItems(latestRobotMonitorItems, data.message || 'Sin datos todavía.');
    draw();
  } catch (err) {
    syncRobotVisualTargets([]);
    draw();
    robotMonitorSubtitle.textContent = 'Error de monitoreo';
    renderRobotMonitorItems([], `Error: ${String(err)}`);
  } finally {
    robotMonitorRefreshInFlight = false;
  }
}

function cleanupRuntimeSocket() {
  if (runtimeSocketReconnectHandle) {
    window.clearTimeout(runtimeSocketReconnectHandle);
    runtimeSocketReconnectHandle = null;
  }
  if (runtimeSocket) {
    try { runtimeSocket.close(); } catch (_) {}
    runtimeSocket = null;
  }
  runtimeSocketConnected = false;
}

function scheduleRuntimeSocketReconnect() {
  if (runtimeSocketReconnectHandle) return;
  runtimeSocketReconnectHandle = window.setTimeout(() => {
    runtimeSocketReconnectHandle = null;
    connectRuntimeSocket();
  }, RUNTIME_SOCKET_RECONNECT_MS);
}

function connectRuntimeSocket() {
  cleanupRuntimeSocket();
  try {
    runtimeSocket = new WebSocket(API.runtimeWs);
  } catch (err) {
    setConn('Error WebSocket');
    scheduleRuntimeSocketReconnect();
    return;
  }

  runtimeSocket.addEventListener('open', () => {
    runtimeSocketConnected = true;
    setConn('Conectado (WebSocket)');
  });

  runtimeSocket.addEventListener('message', (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message?.type === 'runtime_snapshot' && message.payload) {
        applyRuntimeSnapshotData(message.payload, selectedOrderId);
      }
    } catch (_) {}
  });

  runtimeSocket.addEventListener('close', () => {
    runtimeSocketConnected = false;
    setConn('Reconectando WebSocket…');
    scheduleRuntimeSocketReconnect();
  });

  runtimeSocket.addEventListener('error', () => {
    runtimeSocketConnected = false;
    setConn('Error WebSocket');
    try { runtimeSocket?.close(); } catch (_) {}
  });
}






document.addEventListener('click', async (ev) => {
  const btn = ev.target?.closest?.('[data-robot-action]');
  if (!btn) return;
  const action = btn.getAttribute('data-robot-action');
  const robotCode = btn.getAttribute('data-robot-code');
  await sendRobotControl(action, robotCode, btn);
});

const ACTION_CARD_ORDER = [
  "card-workstation",
  "card-scan-qr",
  "card-history",
  "card-cell",
  "card-areas",
  "card-materials",
  "card-racks",
  "card-operator-windows",
  "card-qr-scanners",
  "card-general",
  "card-direct-move",
  "card-fifo",
  "card-client-bg",
  "card-debug-rcs",
  "card-pod-position",
  "card-rack-sync",
  "card-config-rcs",
  "card-admin-password",
  "card-admin-login",
];
const PUBLIC_CARD_IDS = new Set(["card-workstation", "card-scan-qr", "operatorActionPanel", "card-history", "card-admin-login"]);
const ACTION_TAB_STORAGE_KEY = "agv_side_panel_active_tab_v1";
const ACTION_CARD_TABS = [
  {
    key: "operation",
    label: "Operaci\u00f3n",
    cards: ["card-workstation", "card-scan-qr", "card-history"],
  },
  {
    key: "configuration",
    label: "Configuraci\u00f3n",
    cards: ["card-cell", "card-areas", "card-materials", "card-racks", "card-operator-windows", "card-qr-scanners"],
  },
  {
    key: "advanced",
    label: "Configuraci\u00f3n avanzada",
    cards: ["card-general", "card-direct-move", "card-fifo", "card-client-bg", "card-debug-rcs", "card-pod-position", "card-rack-sync", "card-config-rcs", "card-admin-password", "card-admin-login"],
  },
];
let activeActionTabId = safeStorageGet(ACTION_TAB_STORAGE_KEY) || "operation";

function reorderActionCards() {
  const sidePanel = document.querySelector('.side-panel');
  if (!(sidePanel instanceof HTMLElement)) return;
  const title = sidePanel.querySelector('.panel-title');
  let tabsRoot = sidePanel.querySelector(':scope > .action-tabs-root');
  if (!tabsRoot) {
    tabsRoot = document.createElement('div');
    tabsRoot.className = 'action-tabs-root';

    const tabsHeader = document.createElement('div');
    tabsHeader.className = 'action-tabs-header';
    tabsHeader.setAttribute('role', 'tablist');
    tabsHeader.addEventListener('click', function (event) {
      const button = event.target.closest('.action-tab-button');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      setActiveActionTab(button.dataset.tab);
    });

    const panels = document.createElement('div');
    panels.className = 'action-tab-panels';

    ACTION_CARD_TABS.forEach((tab) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'action-tab-button';
      button.dataset.tab = tab.key;
      button.setAttribute('role', 'tab');
      button.textContent = tab.label;
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        setActiveActionTab(tab.key);
      });
      tabsHeader.appendChild(button);

      const panel = document.createElement('div');
      panel.className = 'action-tab-panel';
      panel.dataset.tab = tab.key;
      panel.hidden = true;
      panel.setAttribute('role', 'tabpanel');
      panels.appendChild(panel);
    });

    tabsRoot.appendChild(tabsHeader);
    tabsRoot.appendChild(panels);
  }

  const cards = new Map(Array.from(sidePanel.querySelectorAll('.card')).map((card) => [card.id, card]));
  ACTION_CARD_TABS.forEach((tab) => {
    const panel = tabsRoot.querySelector(`.action-tab-panel[data-tab="${tab.key}"]`);
    if (!(panel instanceof HTMLElement)) return;
    tab.cards.forEach((cardId) => {
      const card = cards.get(cardId);
      if (card) panel.appendChild(card);
    });
  });

  const assignedCardIds = new Set(ACTION_CARD_TABS.flatMap((tab) => tab.cards));
  ACTION_CARD_ORDER.forEach((cardId) => {
    if (assignedCardIds.has(cardId)) return;
    const card = cards.get(cardId);
    const fallbackPanel = tabsRoot.querySelector('.action-tab-panel[data-tab="advanced"]');
    if (card && fallbackPanel) fallbackPanel.appendChild(card);
  });

  if (title) {
    sidePanel.insertBefore(title, sidePanel.firstChild);
    if (tabsRoot.parentNode !== sidePanel) sidePanel.appendChild(tabsRoot);
    if (title.nextSibling !== tabsRoot) sidePanel.insertBefore(tabsRoot, title.nextSibling);
  } else if (tabsRoot.parentNode !== sidePanel) {
    sidePanel.prepend(tabsRoot);
  }
  updateActionTabsVisibility();
  const tabsHeader = tabsRoot.querySelector(".action-tabs-header");
  const firstVisibleButton = tabsHeader?.querySelector(".action-tab-button:not([hidden])");
  if (firstVisibleButton) {
    setActiveActionTab(firstVisibleButton.dataset.tab);
  }
}

function updateCardVisibility(isAdminEnabled) {
  document.querySelectorAll('.side-panel .card').forEach((card) => {
    if (!(card instanceof HTMLElement)) return;
    const shouldShow = isAdminEnabled || PUBLIC_CARD_IDS.has(card.id);
    card.style.display = shouldShow ? '' : 'none';
  });
  ensureActionTabsLayout();
  forceShowActionTabsHeader();
  refreshActionTabsAfterVisibilityChange();
  scheduleCanvasResize();
}

function ensureActionTabsLayout() {
  const sidePanel = document.querySelector(".side-panel");
  if (!sidePanel) return;

  let tabsRoot = sidePanel.querySelector(".action-tabs-root");
  let tabsHeader = sidePanel.querySelector(".action-tabs-header");

  if (!tabsRoot || !tabsHeader) {
    reorderActionCards();
    return;
  }

  tabsRoot.hidden = false;
  tabsRoot.style.display = "";
  tabsHeader.hidden = false;
  tabsHeader.style.display = "";
}

function refreshActionTabsAfterVisibilityChange() {
  const root = document.querySelector(".action-tabs-root");
  if (!root) return;

  const buttons = root.querySelectorAll(".action-tab-button");
  buttons.forEach((button) => {
    button.hidden = false;
    button.style.display = "";
    button.disabled = false;
  });

  const activeButton = root.querySelector(".action-tab-button.active");
  const firstButton = root.querySelector(".action-tab-button");

  if (!activeButton && firstButton) {
    setActiveActionTab(firstButton.dataset.tab);
    return;
  }

  if (activeButton) {
    setActiveActionTab(activeButton.dataset.tab);
  }
}

function forceShowActionTabsHeader() {
  const root = document.querySelector(".action-tabs-root");
  const header = document.querySelector(".action-tabs-header");
  if (!root || !header) return;

  root.hidden = false;
  root.style.display = "flex";

  header.hidden = false;
  header.style.display = "flex";
  header.style.visibility = "visible";
  header.style.opacity = "1";
  header.style.pointerEvents = "auto";

  header.querySelectorAll(".action-tab-button").forEach((button) => {
    button.hidden = false;
    button.disabled = false;
    button.style.display = "";
    button.style.visibility = "visible";
    button.style.opacity = "1";
    button.style.pointerEvents = "auto";
  });
}

function repairActionTabsLayout() {
  const root = document.querySelector(".action-tabs-root");
  const header = document.querySelector(".action-tabs-header");
  if (!root || !header) return;

  root.hidden = false;
  root.style.display = "flex";

  header.hidden = false;
  header.style.display = "flex";
  header.style.visibility = "visible";
  header.style.opacity = "1";
  header.style.pointerEvents = "auto";

  document.querySelectorAll(".action-tab-button").forEach((button) => {
    button.hidden = false;
    button.disabled = false;
    button.style.pointerEvents = "auto";
  });
}

function setActiveActionTab(tabId) {
  const tabsRoot = document.querySelector(".action-tabs-root");
  if (!tabsRoot) return;

  activeActionTabId = tabId || "";
  if (activeActionTabId) safeStorageSet(ACTION_TAB_STORAGE_KEY, activeActionTabId);

  const buttons = tabsRoot.querySelectorAll(".action-tab-button");
  const panels = tabsRoot.querySelectorAll(".action-tab-panel");

  buttons.forEach((btn) => {
    const isActive = btn.dataset.tab === activeActionTabId;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  panels.forEach((panel) => {
    const isActive = panel.dataset.tab === activeActionTabId;
    panel.classList.toggle("active", isActive);
    panel.hidden = !isActive;
  });

  if (activeActionTabId === "operation") focusScanQrInput();
  scheduleCanvasResize();
}

function cardIsVisibleInPanel(card) {
  return card instanceof HTMLElement && card.style.display !== 'none';
}

function updateActionTabsVisibility() {
  const tabsRoot = document.querySelector('.side-panel .action-tabs-root');
  if (!(tabsRoot instanceof HTMLElement)) return;
  const tabKeys = ACTION_CARD_TABS.map((tab) => tab.key);

  ACTION_CARD_TABS.forEach((tab) => {
    const button = tabsRoot.querySelector(`.action-tab-button[data-tab="${tab.key}"]`);
    const panel = tabsRoot.querySelector(`.action-tab-panel[data-tab="${tab.key}"]`);
    if (!(button instanceof HTMLElement) || !(panel instanceof HTMLElement)) return;
    button.hidden = false;
  });

  if (!tabKeys.includes(activeActionTabId)) {
    activeActionTabId = tabKeys[0] || "";
    if (activeActionTabId) safeStorageSet(ACTION_TAB_STORAGE_KEY, activeActionTabId);
  }

  setActiveActionTab(activeActionTabId);
}

function safeStorageGet(key) {
  try {
    return window.localStorage ? window.localStorage.getItem(key) : null;
  } catch (_) {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    if (window.localStorage) window.localStorage.setItem(key, value);
  } catch (_) {}
}

function applySavedSidePanelWidth() {
  const raw = safeStorageGet(SIDE_PANEL_WIDTH_KEY);
  if (!raw) return;
  const width = Number.parseFloat(String(raw).replace('px', ''));
  if (!Number.isFinite(width)) return;
  const clamped = clamp(width, 300, Math.max(380, Math.floor(window.innerWidth * 0.65)));
  document.documentElement.style.setProperty('--sideW', `${Math.round(clamped)}px`);
}

function initActionCardsLayout() {
  const cards = Array.from(document.querySelectorAll('.side-panel .card'));
  cards.forEach((card, index) => {
    if (!(card instanceof HTMLElement)) return;
    if (card.dataset.layoutReady === '1') return;
    const titleEl = card.querySelector('h3');
    const title = titleEl ? titleEl.textContent.trim() : `Sección ${index + 1}`;
    const cardId = card.id || `card-${index + 1}`;
    card.dataset.cardId = cardId;

    const header = document.createElement('div');
    header.className = 'card-header';

    const titleNode = document.createElement('h3');
    titleNode.textContent = title;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'card-toggle';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = '<span class="card-toggle-label">Expandir</span><span class="card-toggle-icon">▼</span>';

    const body = document.createElement('div');
    body.className = 'card-body';

    const children = Array.from(card.childNodes);
    children.forEach((node) => {
      if (node === titleEl) return;
      body.appendChild(node);
    });

    if (titleEl && titleEl.parentNode === card) {
      card.removeChild(titleEl);
    }

    header.appendChild(titleNode);
    header.appendChild(toggle);
    card.prepend(body);
    card.prepend(header);

    const storageKey = `${CARD_COLLAPSE_KEY_PREFIX}${cardId}`;
    const storedCollapsed = safeStorageGet(storageKey);
    const initialCollapsed = storedCollapsed === null ? !DEFAULT_EXPANDED_CARD_IDS.has(cardId) : storedCollapsed !== '0';
    const applyState = (collapsed) => {
      card.classList.toggle('collapsed', collapsed);
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      const label = toggle.querySelector('.card-toggle-label');
      if (label) label.textContent = collapsed ? 'Expandir' : 'Colapsar';
      safeStorageSet(storageKey, collapsed ? '1' : '0');
    };

    toggle.addEventListener('click', () => {
      applyState(!card.classList.contains('collapsed'));
      repairActionTabsLayout();
      scheduleCanvasResize();
    });

    applyState(initialCollapsed);
    card.dataset.layoutReady = '1';
  });
}

function gridMetrics() {
  const scale = clamp(cam.scale, MIN_ZOOM, MAX_ZOOM);
  const cell = BASE_CELL * scale;
  const gap = BASE_GAP * scale;
  return { scale, cell, gap, pitch: cell + gap };
}
function resizeCanvasToContainer() {
  const wrap = document.querySelector(".canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  canvasCssW = Math.max(220, Math.floor(rect.width));
  canvasCssH = Math.max(220, Math.floor(rect.height));
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.floor(canvasCssW * dpr);
  canvas.height = Math.floor(canvasCssH * dpr);
  canvas.style.width = `${canvasCssW}px`;
  canvas.style.height = `${canvasCssH}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
function scheduleCanvasResize() {
  resizeCanvasToContainer();
  draw();
}
function initSplitter() {
  if (!splitterEl || !layoutEl) return;
  let active = false;
  let startY = 0;
  let startPct = 70;
  splitterEl.addEventListener("mousedown", (e) => {
    active = true;
    splitterEl.classList.add("dragging");
    startY = e.clientY;
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--matrixH").trim();
    startPct = parseFloat(raw || "70") || 70;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!active) return;
    const total = layoutEl.clientHeight || 1;
    const pct = clamp(startPct + ((e.clientY - startY) / total) * 100, 35, 90);
    document.documentElement.style.setProperty("--matrixH", `${pct}%`);
    scheduleCanvasResize();
  });
  window.addEventListener("mouseup", () => {
    active = false;
    splitterEl.classList.remove("dragging");
  });
}

function initVerticalSplitter() {
  if (!splitterVEl || !layoutEl) return;
  let active = false;
  let startX = 0;
  let startWidth = 420;
  let currentWidth = 420;
  applySavedSidePanelWidth();
  splitterVEl.addEventListener("mousedown", (e) => {
    active = true;
    splitterVEl.classList.add("dragging");
    startX = e.clientX;
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--sideW").trim();
    startWidth = parseFloat((raw || "420").replace("px", "")) || 420;
    currentWidth = startWidth;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!active) return;
    currentWidth = clamp(startWidth - (e.clientX - startX), 300, Math.max(380, Math.floor(window.innerWidth * 0.65)));
    document.documentElement.style.setProperty("--sideW", `${currentWidth}px`);
    scheduleCanvasResize();
  });
  window.addEventListener("mouseup", () => {
    if (active) safeStorageSet(SIDE_PANEL_WIDTH_KEY, String(Math.round(currentWidth)));
    active = false;
    splitterVEl.classList.remove("dragging");
  });
}
function ensureSplitterMode() {
  const portraitLike = window.matchMedia("(max-width: 980px), (max-aspect-ratio: 4/5)").matches;
  if (portraitLike && !getComputedStyle(document.documentElement).getPropertyValue("--matrixH").trim()) {
    document.documentElement.style.setProperty("--matrixH", "70%");
  }
}
function canvasToGrid(mx, my) {
  const { pitch } = gridMetrics();
  const x = Math.floor((mx - cam.offX) / pitch);
  const y = Math.floor((my - cam.offY) / pitch);
  if (x < 0 || y < 0 || x >= GRID_W || y >= GRID_H) return null;
  return { x, y };
}
function isFreeLayoutMode() {
  return mapLayoutMode === "free";
}
function isFreeLayoutEditing() {
  return isFreeLayoutMode() && adminToken && String(freeLayoutEditEnabled?.value || "0") === "1";
}
function updateAddFreeCellAvailability() {
  if (btnAddFreeCell) btnAddFreeCell.disabled = !isFreeLayoutEditing();
}
function canvasToWorld(mx, my) {
  const scale = clamp(cam.scale, MIN_ZOOM, MAX_ZOOM);
  return { x: (mx - cam.offX) / scale, y: (my - cam.offY) / scale };
}
function freeLayoutLocations() {
  return locations.filter(loc => (
    loc &&
    Number(loc.x) >= 0 &&
    Number(loc.y) >= 0 &&
    Number(loc.x) < GRID_W &&
    Number(loc.y) < GRID_H &&
    (Number(loc.free_enabled || 0) === 1 || Number(loc.is_visible ?? 1) === 1)
  ));
}
function freeRectForLocation(loc) {
  const basePitch = BASE_CELL + BASE_GAP;
  const hasFreePosition = Number(loc?.free_enabled || 0) === 1;
  const x = hasFreePosition && Number.isFinite(Number(loc?.free_x)) ? Number(loc.free_x) : Number(loc?.x || 0) * basePitch;
  const y = hasFreePosition && Number.isFinite(Number(loc?.free_y)) ? Number(loc.free_y) : Number(loc?.y || 0) * basePitch;
  const w = hasFreePosition && Number.isFinite(Number(loc?.free_w)) ? Math.max(4, Number(loc.free_w)) : BASE_CELL;
  const h = hasFreePosition && Number.isFinite(Number(loc?.free_h)) ? Math.max(4, Number(loc.free_h)) : BASE_CELL;
  return { x, y, w, h };
}
function freeRectToCanvas(rect) {
  const scale = clamp(cam.scale, MIN_ZOOM, MAX_ZOOM);
  return {
    x: cam.offX + rect.x * scale,
    y: cam.offY + rect.y * scale,
    w: rect.w * scale,
    h: rect.h * scale,
  };
}
function hitTestFreeLocation(mx, my) {
  const world = canvasToWorld(mx, my);
  const rows = freeLayoutLocations();
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const loc = rows[i];
    const rect = freeRectForLocation(loc);
    if (world.x >= rect.x && world.x <= rect.x + rect.w && world.y >= rect.y && world.y <= rect.y + rect.h) {
      return loc;
    }
  }
  return null;
}
function normalizedCanvasRect(box) {
  if (!box) return null;
  const left = Math.min(box.startX, box.endX);
  const top = Math.min(box.startY, box.endY);
  const right = Math.max(box.startX, box.endX);
  const bottom = Math.max(box.startY, box.endY);
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}
function rectsIntersect(a, b) {
  return a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top;
}
function locationCanvasRect(loc) {
  if (!loc) return null;
  if (isFreeLayoutMode()) {
    const rect = freeRectToCanvas(freeRectForLocation(loc));
    return { left: rect.x, top: rect.y, right: rect.x + rect.w, bottom: rect.y + rect.h };
  }
  const { cell, pitch } = gridMetrics();
  const x = cam.offX + Number(loc.x) * pitch;
  const y = cam.offY + Number(loc.y) * pitch;
  return { left: x, top: y, right: x + cell, bottom: y + cell };
}
function selectableLocationsForCurrentView() {
  if (isFreeLayoutMode()) return freeLayoutLocations();
  return locations.filter(loc => (
    loc &&
    Number(loc.is_visible ?? 1) === 1 &&
    Number(loc.x) >= 0 &&
    Number(loc.y) >= 0 &&
    Number(loc.x) < GRID_W &&
    Number(loc.y) < GRID_H
  ));
}
function locationsInsideSelectionBox(box) {
  const rect = normalizedCanvasRect(box);
  if (!rect || rect.width < 2 || rect.height < 2) return [];
  return selectableLocationsForCurrentView().filter(loc => {
    const locRect = locationCanvasRect(loc);
    return locRect && rectsIntersect(rect, locRect);
  });
}
function drawMultiSelectBox() {
  const rect = normalizedCanvasRect(multiSelectBox);
  if (!rect) return;
  ctx.save();
  ctx.fillStyle = "rgba(56,189,248,0.12)";
  ctx.strokeStyle = "rgba(56,189,248,0.95)";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([6, 4]);
  ctx.fillRect(rect.left, rect.top, rect.width, rect.height);
  ctx.strokeRect(rect.left, rect.top, rect.width, rect.height);
  ctx.restore();
}
function cellColor(loc) {
  if (!loc) return "rgba(0,0,0,0)";
  if (Number(loc.enabled ?? 1) !== 1) return "rgba(91,104,131,0.40)";
  return Number(loc.status) === 1 ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.035)";
}
function areaColorFor(loc) {
  if (!loc || !loc.area_id) return null;
  const area = catalog.areas.find(a => Number(a.id) === Number(loc.area_id));
  return area ? area.color : null;
}
function randomMaterialColor() {
  const palette = ["#2563eb","#7c3aed","#059669","#dc2626","#d97706","#0891b2","#db2777","#65a30d","#ea580c","#4f46e5","#0f766e","#be185d"];
  return palette[Math.floor(Math.random() * palette.length)];
}
function materialColorById(id) {
  const material = getMaterialById(id);
  return material?.color || "#7c3aed";
}
function normalizeMaterialToken(value) {
  const text = String(value ?? "").trim().toUpperCase();
  return text ? text.replace(/[^A-Z0-9]/g, "") : "";
}
function isEmptyMaterialValue(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return true;
  const normalized = normalizeMaterialToken(raw);
  return !normalized || normalized === "NULL" || normalized === "UNDEFINED" || normalized === "NONE";
}
function isNoMaterialKeyword(value) {
  if (isEmptyMaterialValue(value)) return false;
  return normalizeMaterialToken(value) === "SINMATERIAL";
}
function parseValidMaterialId(value) {
  if (isEmptyMaterialValue(value)) return null;
  const n = Number(String(value).trim());
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}
function isRackWithoutMaterial(rackOrMaterialId) {
  const rack = rackOrMaterialId && typeof rackOrMaterialId === "object" ? rackOrMaterialId : null;
  const materialGroupId = rack ? rack.material_group_id : rackOrMaterialId;
  const rackTokens = [
    materialGroupId,
    rack?.material_group_code,
    rack?.material_code,
    rack?.material_group_name,
    rack?.material_name,
  ];
  if (rackTokens.some(v => isNoMaterialKeyword(v))) return true;

  const parsedId = parseValidMaterialId(materialGroupId);
  if (!parsedId) return true;

  const material = getMaterialById(parsedId);
  if (!material) return true;
  if (isNoMaterialKeyword(material.code) || isNoMaterialKeyword(material.name)) return true;
  return false;
}
function hexToRgba(hex, alpha = 1) {
  if (!hex || !/^#[0-9a-fA-F]{6}$/.test(hex)) return `rgba(124,58,237,${alpha})`;
  const num = parseInt(hex.slice(1), 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}
function drawRackIcon(px, py, cell, rack, scale) {
  const size = Math.max(10, cell * 0.56);
  const ix = px + (cell - size) / 2;
  const iy = py + (cell - size) / 2;
  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  if (rack && !isRackWithoutMaterial(rack)) {
    const fill = materialColorById(rack.material_group_id);
    ctx.fillStyle = hexToRgba(fill, 0.95);
    ctx.strokeStyle = "rgba(255,255,255,0.88)";
    ctx.lineWidth = Math.max(1.3, 1.7 * scale);
    const topLift = size * 0.16;
    ctx.beginPath();
    ctx.moveTo(ix + size * 0.12, iy + topLift);
    ctx.lineTo(ix + size * 0.5, iy);
    ctx.lineTo(ix + size * 0.88, iy + topLift);
    ctx.lineTo(ix + size * 0.88, iy + size * 0.76);
    ctx.lineTo(ix + size * 0.5, iy + size * 0.92);
    ctx.lineTo(ix + size * 0.12, iy + size * 0.76);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ix + size * 0.12, iy + topLift);
    ctx.lineTo(ix + size * 0.5, iy + size * 0.3);
    ctx.lineTo(ix + size * 0.88, iy + topLift);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ix + size * 0.5, iy + size * 0.3);
    ctx.lineTo(ix + size * 0.5, iy + size * 0.92);
    ctx.stroke();
  } else {
    ctx.strokeStyle = "rgba(214,222,235,0.95)";
    ctx.lineWidth = Math.max(1.4, 1.8 * scale);
    const left = ix + size * 0.18;
    const right = ix + size * 0.82;
    const top = iy + size * 0.16;
    const bottom = iy + size * 0.84;
    ctx.beginPath();
    ctx.moveTo(left, top);
    ctx.lineTo(left, bottom);
    ctx.moveTo(right, top);
    ctx.lineTo(right, bottom);
    ctx.moveTo(left, top + size * 0.1);
    ctx.lineTo(right, top + size * 0.1);
    ctx.moveTo(left, iy + size * 0.5);
    ctx.lineTo(right, iy + size * 0.5);
    ctx.moveTo(left, bottom - size * 0.1);
    ctx.lineTo(right, bottom - size * 0.1);
    ctx.stroke();
  }
  ctx.restore();
}
function drawBackgroundImage() {
  if (!bgState.img || !bgState.loaded) return;
  const { pitch } = gridMetrics();
  const x = cam.offX + (Number(bgState.offset_x || 0) * pitch);
  const y = cam.offY + (Number(bgState.offset_y || 0) * pitch);
  const w = GRID_W * pitch * Number(bgState.scale_x || 1);
  const h = GRID_H * pitch * Number(bgState.scale_y || 1);
  ctx.globalAlpha = 0.85;
  try { ctx.drawImage(bgState.img, x, y, w, h); } catch (_) {}
  ctx.globalAlpha = 1;
}
function getDisplayedBackgroundRect() {
  const { pitch } = gridMetrics();
  return {
    x: cam.offX + (Number(bgState.offset_x || 0) * pitch),
    y: cam.offY + (Number(bgState.offset_y || 0) * pitch),
    w: GRID_W * pitch * Number(bgState.scale_x || 1),
    h: GRID_H * pitch * Number(bgState.scale_y || 1),
  };
}
function normalizeRadians(angle) {
  if (!Number.isFinite(angle)) return 0;
  let value = angle;
  while (value > Math.PI) value -= Math.PI * 2;
  while (value < -Math.PI) value += Math.PI * 2;
  return value;
}
function getRobotAnimDurationMs() {
  const refreshMs = Math.max(400, Number(RUNTIME_AUTO_REFRESH_MS) || 5000);
  return clamp(Math.round(refreshMs * 0.9), 300, 12000);
}
function pruneRobotVisualState(validCodes) {
  for (const code of Array.from(robotVisualState.keys())) {
    if (!validCodes.has(code)) robotVisualState.delete(code);
  }
}
function getActiveMissionForRobot(robotCode) {
  const code = String(robotCode || '').trim();
  if (!code || !Array.isArray(movementOrders)) return null;
  const activeStatuses = new Set(['pending_dispatch','dispatched','in_progress','cancel_requested_total','cancel_requested_undo']);
  return movementOrders.find((order) => String(order?.agv_code || '').trim() === code && activeStatuses.has(String(order?.status || '').trim())) || null;
}
function getRobotTaskSummary(robot) {
  const mission = getActiveMissionForRobot(robot?.robotCode);
  if (mission) {
    return String(mission.remote_task_code || mission.order_code || mission.status || '').trim() || 'En ejecución';
  }
  const statusText = String(robot?.statusText || '').trim();
  const taskMap = {'Executing task':'Ejecutando tarea','Lifting the rack':'Levantando rack','Curve movement':'Movimiento en curva'};
  return taskMap[statusText] || 'Sin tarea activa';
}
function getMissionPriorityLevel(priority) {
  const value = String(priority || '').trim().toLowerCase();
  if (value === 'urgent' || value === '2' || value === 'alta') return 'urgent';
  if (value === 'high' || value === '1' || value === 'media') return 'high';
  return 'normal';
}
function getMissionPriorityColor(priority) {
  const level = getMissionPriorityLevel(priority);
  if (level === 'urgent') return 'rgba(239,68,68,0.95)';
  if (level === 'high') return 'rgba(245,158,11,0.95)';
  return 'rgba(34,197,94,0.9)';
}
function resolveAgvVisualState(robot) {
  if (!robot) return 'offline';
  const status = String(robot?.status || '').trim();
  const excluded = String(robot?.exclType || '') === '1';
  if (!status && !String(robot?.robotCode || '').trim()) return 'offline';
  if (hasRobotErrorCode(robot)) return 'error';
  if (excluded || ['3','11','12','13','14','15','16','17','18','20','21','23','24','25','26','27','28','29','30','31','33','34'].includes(status)) return 'error';
  if (['2','6','8'].includes(status)) return 'working';
  if (isRobotStoppedPauseState(robot) || ['5','7','9'].includes(status)) return 'waiting';
  if (['1','4'].includes(status)) return 'idle';
  return 'idle';
}
function getAgvVisualPalette(state, priority) {
  const priorityColor = getMissionPriorityColor(priority);
  switch (state) {
    case 'offline':
      return { bodyFill: 'rgba(107,114,128,0.92)', bodyStroke: 'rgba(229,231,235,0.95)', glow: 'rgba(107,114,128,0.45)', priorityColor };
    case 'error':
      return { bodyFill: 'rgba(220,38,38,0.96)', bodyStroke: 'rgba(255,255,255,0.98)', glow: 'rgba(248,113,113,0.68)', priorityColor };
    case 'working':
      return { bodyFill: 'rgba(37,99,235,0.94)', bodyStroke: 'rgba(255,255,255,0.96)', glow: 'rgba(59,130,246,0.52)', priorityColor };
    case 'waiting':
      return { bodyFill: 'rgba(245,158,11,0.94)', bodyStroke: 'rgba(255,255,255,0.96)', glow: 'rgba(251,191,36,0.5)', priorityColor };
    default:
      return { bodyFill: 'rgba(22,163,74,0.94)', bodyStroke: 'rgba(255,255,255,0.96)', glow: 'rgba(74,222,128,0.48)', priorityColor };
  }
}
function shouldBlinkErrorState(state) {
  if (state !== 'error') return false;
  const phase = (performance.now() % 900) / 900;
  return phase < 0.5;
}
function appendRobotTrailPoint(state, x, y) {
  if (!state) return;
  if (!Array.isArray(state.trail)) state.trail = [];
  const last = state.trail[state.trail.length - 1];
  if (last) {
    const dx = Number(last.x || 0) - Number(x || 0);
    const dy = Number(last.y || 0) - Number(y || 0);
    if (Math.hypot(dx, dy) < ROBOT_TRAIL_MIN_MOVE) return;
  }
  state.trail.push({ x, y, at: performance.now() });
  if (state.trail.length > ROBOT_TRAIL_MAX_POINTS) state.trail.splice(0, state.trail.length - ROBOT_TRAIL_MAX_POINTS);
}
function drawRobotTrail(state, priority) {
  if (!state || !Array.isArray(state.trail) || state.trail.length < 2) return;
  const pts = [];
  for (const raw of state.trail) {
    const pt = getRobotCanvasPoint({ posX: raw.x, posY: raw.y });
    if (pt) pts.push(pt);
  }
  const livePt = getRobotCanvasPoint({ posX: state.displayX, posY: state.displayY });
  if (livePt) pts.push(livePt);
  if (pts.length < 2) return;
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = getMissionPriorityColor(priority);
  for (let i = 1; i < pts.length; i += 1) {
    const alpha = i / pts.length;
    ctx.globalAlpha = Math.max(0.14, alpha * 0.75);
    ctx.lineWidth = 2 + (alpha * 4);
    ctx.beginPath();
    ctx.moveTo(pts[i - 1].x, pts[i - 1].y);
    ctx.lineTo(pts[i].x, pts[i].y);
    ctx.stroke();
  }
  ctx.restore();
}
function ensureRobotAnimationLoop() {
  if (robotAnimationFrame !== null) return;
  const step = (ts) => {
    let hasActive = false;
    for (const state of robotVisualState.values()) {
      const startAt = Number(state.startAt || 0);
      const duration = Math.max(1, Number(state.durationMs || 1));
      if (startAt <= 0) continue;
      const progress = clamp((ts - startAt) / duration, 0, 1);
      state.displayX = state.startX + (state.targetX - state.startX) * progress;
      state.displayY = state.startY + (state.targetY - state.startY) * progress;
      const delta = normalizeRadians(state.targetDir - state.startDir);
      state.displayDir = normalizeRadians(state.startDir + (delta * progress));
      if (progress < 1) {
        hasActive = true;
      } else {
        state.displayX = state.targetX;
        state.displayY = state.targetY;
        state.displayDir = state.targetDir;
        state.startAt = 0;
      }
    }
    draw();
    if (hasActive) {
      robotAnimationFrame = requestAnimationFrame(step);
    } else {
      robotAnimationFrame = null;
    }
  };
  robotAnimationFrame = requestAnimationFrame(step);
}
function syncRobotVisualTargets(items) {
  const list = Array.isArray(items) ? items : [];
  latestRobotMonitorItems = list;
  const validCodes = new Set();
  const now = performance.now();
  const durationMs = getRobotAnimDurationMs();
  for (const robot of list) {
    const code = String(robot?.robotCode || '').trim() || `__idx_${validCodes.size}`;
    validCodes.add(code);
    const rawX = Number(robot?.posX ?? NaN);
    const rawY = Number(robot?.posY ?? NaN);
    const nextDir = getRobotHeadingRadians(robot);
    const existing = robotVisualState.get(code);
    if (!Number.isFinite(rawX) || !Number.isFinite(rawY)) {
      if (existing) existing.robot = robot;
      continue;
    }
    if (!existing) {
      robotVisualState.set(code, {
        robot,
        displayX: rawX, displayY: rawY, displayDir: nextDir,
        startX: rawX, startY: rawY, startDir: nextDir,
        targetX: rawX, targetY: rawY, targetDir: nextDir,
        startAt: 0, durationMs,
        trail: [{ x: rawX, y: rawY, at: performance.now() }],
      });
      continue;
    }
    const currentX = Number(existing.displayX ?? existing.targetX ?? rawX);
    const currentY = Number(existing.displayY ?? existing.targetY ?? rawY);
    const currentDir = Number.isFinite(existing.displayDir) ? existing.displayDir : nextDir;
    appendRobotTrailPoint(existing, existing.targetX ?? currentX, existing.targetY ?? currentY);
    existing.robot = robot;
    existing.startX = currentX;
    existing.startY = currentY;
    existing.startDir = currentDir;
    existing.targetX = rawX;
    existing.targetY = rawY;
    existing.targetDir = nextDir;
    existing.durationMs = durationMs;
    existing.startAt = now;
  }
  pruneRobotVisualState(validCodes);
  if (list.length) ensureRobotAnimationLoop();
}
function getAnimatedRobotForDraw(robot, idxCode) {
  const code = String(robot?.robotCode || '').trim() || idxCode;
  const state = robotVisualState.get(code);
  if (!state) return robot;
  return { ...robot, posX: state.displayX, posY: state.displayY, robotDir: state.displayDir };
}
function getRobotCanvasPoint(robot) {
  const rawX = Number(robot?.posX ?? NaN);
  const rawY = Number(robot?.posY ?? NaN);
  if (!Number.isFinite(rawX) || !Number.isFinite(rawY)) return null;

  const scaleX = Number(agvOverlayConfig.scale_x ?? 1) || 1;
  const scaleY = Number(agvOverlayConfig.scale_y ?? 1) || 1;
  const offsetX = Number(agvOverlayConfig.offset_x ?? 0) || 0;
  const offsetY = Number(agvOverlayConfig.offset_y ?? 0) || 0;
  const rotationDeg = Number(agvOverlayConfig.rotation_deg ?? 0) || 0;
  const mirrorX = Number(agvOverlayConfig.mirror_x ?? 0) === 1 ? -1 : 1;
  const mirrorY = Number(agvOverlayConfig.mirror_y ?? 0) === 1 ? -1 : 1;
  const rad = rotationDeg * Math.PI / 180;
  const scaledX = rawX * scaleX * mirrorX;
  const scaledY = rawY * scaleY * mirrorY;
  const rotatedX = (scaledX * Math.cos(rad)) - (scaledY * Math.sin(rad));
  const rotatedY = (scaledX * Math.sin(rad)) + (scaledY * Math.cos(rad));
  const localX = rotatedX + offsetX;
  const localY = rotatedY + offsetY;

  if (bgState.img && bgState.loaded && bgState.img.naturalWidth > 0 && bgState.img.naturalHeight > 0) {
    const bgRect = getDisplayedBackgroundRect();
    return {
      x: bgRect.x + (localX / bgState.img.naturalWidth) * bgRect.w,
      y: bgRect.y + (localY / bgState.img.naturalHeight) * bgRect.h,
    };
  }

  const { pitch } = gridMetrics();
  return {
    x: cam.offX + (localX * pitch),
    y: cam.offY + (localY * pitch),
  };
}
function getRobotHeadingRadians(robot) {
  const raw = Number(robot?.robotDir ?? NaN);
  if (!Number.isFinite(raw)) return 0;
  if (Math.abs(raw) <= (Math.PI * 2) + 0.01) return raw;
  return raw * Math.PI / 180;
}
function drawAgvRobotIcon(robot) {
  const point = getRobotCanvasPoint(robot);
  if (!point) return;
  const { cell } = gridMetrics();
  const size = clamp(cell * 0.72, 18, 42);
  const heading = getRobotHeadingRadians(robot);
  const mirroredHeading = (Number(agvOverlayConfig.icon_angle_mirror ?? 0) === 1 ? -heading : heading);
  const headingWithOffset = mirroredHeading + ((Number(agvOverlayConfig.orientation_offset_deg ?? 0) || 0) * Math.PI / 180);
  const mission = getActiveMissionForRobot(robot?.robotCode);
  const visualState = resolveAgvVisualState(robot);
  const palette = getAgvVisualPalette(visualState, mission?.priority);
  const blinkOff = shouldBlinkErrorState(visualState);

  ctx.save();
  ctx.translate(point.x, point.y);
  ctx.rotate(headingWithOffset);
  ctx.shadowColor = palette.glow;
  ctx.shadowBlur = blinkOff ? 3 : 14;
  ctx.fillStyle = blinkOff ? 'rgba(255,255,255,0.22)' : palette.bodyFill;
  ctx.strokeStyle = palette.bodyStroke;
  ctx.lineWidth = Math.max(1.8, size * 0.08);

  const bodyW = size * 1.18;
  const bodyH = size * 0.82;
  const radius = size * 0.18;

  if (mission) {
    ctx.save();
    ctx.strokeStyle = palette.priorityColor;
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = Math.max(2, size * 0.1);
    ctx.beginPath();
    ctx.roundRect(-bodyW * 0.66, -bodyH * 0.66, bodyW * 1.32, bodyH * 1.32, size * 0.22);
    ctx.stroke();
    ctx.restore();
  }

  ctx.beginPath();
  ctx.moveTo(-bodyW / 2 + radius, -bodyH / 2);
  ctx.lineTo(bodyW / 2 - radius, -bodyH / 2);
  ctx.quadraticCurveTo(bodyW / 2, -bodyH / 2, bodyW / 2, -bodyH / 2 + radius);
  ctx.lineTo(bodyW / 2, bodyH / 2 - radius);
  ctx.quadraticCurveTo(bodyW / 2, bodyH / 2, bodyW / 2 - radius, bodyH / 2);
  ctx.lineTo(-bodyW / 2 + radius, bodyH / 2);
  ctx.quadraticCurveTo(-bodyW / 2, bodyH / 2, -bodyW / 2, bodyH / 2 - radius);
  ctx.lineTo(-bodyW / 2, -bodyH / 2 + radius);
  ctx.quadraticCurveTo(-bodyW / 2, -bodyH / 2, -bodyW / 2 + radius, -bodyH / 2);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  ctx.shadowBlur = 0;

  ctx.save();
  ctx.fillStyle = blinkOff ? 'rgba(255,255,255,0.8)' : 'rgba(255,255,255,0.96)';
  ctx.beginPath();
  ctx.moveTo(bodyW * 0.08, 0);
  ctx.lineTo(-bodyW * 0.2, -bodyH * 0.22);
  ctx.lineTo(-bodyW * 0.2, bodyH * 0.22);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  ctx.fillStyle = 'rgba(8,15,28,0.92)';
  const wheelW = bodyW * 0.18;
  const wheelH = Math.max(3, bodyH * 0.14);
  ctx.fillRect(-bodyW * 0.32, -bodyH * 0.62, wheelW, wheelH);
  ctx.fillRect(bodyW * 0.14, -bodyH * 0.62, wheelW, wheelH);
  ctx.fillRect(-bodyW * 0.32, bodyH * 0.48, wheelW, wheelH);
  ctx.fillRect(bodyW * 0.14, bodyH * 0.48, wheelW, wheelH);

  ctx.restore();

  ctx.save();
  ctx.fillStyle = 'rgba(255,255,255,0.98)';
  ctx.font = `bold ${Math.max(10, size * 0.34)}px ui-sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  ctx.strokeStyle = 'rgba(8,15,28,0.8)';
  ctx.lineWidth = 3;
  const label = String(robot?.robotCode || '').trim();
  if (label) {
    ctx.strokeText(label, point.x, point.y - size * 0.72);
    ctx.fillText(label, point.x, point.y - size * 0.72);
  }
  if (mission?.priority) {
    const priorityText = String(mission.priority).trim().toUpperCase();
    ctx.font = `bold ${Math.max(9, size * 0.24)}px ui-sans-serif`;
    ctx.textBaseline = 'top';
    ctx.fillStyle = palette.priorityColor;
    ctx.strokeStyle = 'rgba(8,15,28,0.82)';
    ctx.lineWidth = 2.5;
    ctx.strokeText(priorityText, point.x, point.y + size * 0.55);
    ctx.fillText(priorityText, point.x, point.y + size * 0.55);
  }
  ctx.restore();
}
function drawAgvOverlay() {
  if (!robotMonitorEnabled || !Array.isArray(latestRobotMonitorItems) || !latestRobotMonitorItems.length) return;
  latestRobotMonitorItems.forEach((robot, index) => {
    const animated = getAnimatedRobotForDraw(robot, `__idx_${index}`);
    const code = String(robot?.robotCode || '').trim() || `__idx_${index}`;
    const state = robotVisualState.get(code);
    const mission = getActiveMissionForRobot(robot?.robotCode);
    if (state) drawRobotTrail(state, mission?.priority);
    drawAgvRobotIcon(animated);
  });
}
function drawFreeLocationOutline(loc, color, inset = 2, width = 3) {
  if (!loc) return;
  const canvasRect = freeRectToCanvas(freeRectForLocation(loc));
  ctx.lineWidth = width;
  ctx.strokeStyle = color;
  ctx.strokeRect(
    canvasRect.x + inset,
    canvasRect.y + inset,
    Math.max(1, canvasRect.w - inset * 2),
    Math.max(1, canvasRect.h - inset * 2),
  );
}
function drawFreeLayout() {
  const { scale } = gridMetrics();
  const rows = freeLayoutLocations();
  for (const loc of rows) {
    const rect = freeRectToCanvas(freeRectForLocation(loc));
    ctx.strokeStyle = "rgba(36,50,74,0.32)";
    ctx.lineWidth = 1;
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    ctx.fillStyle = cellColor(loc);
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    const aColor = areaColorFor(loc);
    if (aColor) {
      ctx.strokeStyle = aColor;
      ctx.lineWidth = Math.max(2, 3.2 * scale);
      ctx.strokeRect(rect.x + 1.5, rect.y + 1.5, Math.max(1, rect.w - 3), Math.max(1, rect.h - 3));
    }
    const rack = getRackById(loc.rack_id);
    if (rack) drawRackIcon(rect.x, rect.y, Math.min(rect.w, rect.h), rack, scale);
    if (isFreeLayoutEditing()) {
      ctx.fillStyle = "rgba(214,222,235,0.82)";
      ctx.font = `${Math.max(10, 11 * scale)}px ui-sans-serif`;
      const label = String(loc.code || `${loc.x},${loc.y}`).slice(0, 18);
      ctx.fillText(label, rect.x + 4, rect.y + Math.max(12, 13 * scale));
    }
  }

  drawAgvOverlay();
  drawFreeLocationOutline(getLocationById(directMoveSourceCellId), "rgba(34,197,94,1)", 2, 3);
  drawFreeLocationOutline(getLocationById(directMoveDestinationCellId), "rgba(96,165,250,1)", 4, 3);
  drawFreeLocationOutline(operatorButtonSourceCell?.value ? getLocationById(Number(operatorButtonSourceCell.value)) : null, "rgba(16,185,129,1)", 6, 3);
  drawFreeLocationOutline(operatorButtonDestinationCell?.value ? getLocationById(Number(operatorButtonDestinationCell.value)) : null, "rgba(59,130,246,1)", 8, 3);
  if (hoverCell) drawFreeLocationOutline(hoverCell, "rgba(255,255,255,0.7)", 3, 2);
  for (const loc of selectedLocations()) {
    drawFreeLocationOutline(loc, "rgba(56,189,248,0.95)", 5, 2);
  }
  const selectedLoc = getLocationAtGrid(selected.x, selected.y);
  drawFreeLocationOutline(selectedLoc, "rgba(255,209,102,1)", 1, 2);

  ctx.fillStyle = "rgba(147,164,199,0.9)";
  ctx.font = `${Math.max(12, 12 * scale)}px ui-sans-serif`;
  ctx.fillText(`Vista libre: ${rows.length} celdas | Zoom: ${scale.toFixed(2)}x`, 10, 18);
  drawMultiSelectBox();
}
function draw() {
  ctx.clearRect(0, 0, canvasCssW, canvasCssH);
  ctx.fillStyle = "#0a0f18";
  ctx.fillRect(0, 0, canvasCssW, canvasCssH);
  drawBackgroundImage();
  if (isFreeLayoutMode()) {
    drawFreeLayout();
    return;
  }
  const { scale, cell, pitch } = gridMetrics();
  const sx = clamp(Math.floor((-cam.offX) / pitch) - 1, 0, GRID_W - 1);
  const sy = clamp(Math.floor((-cam.offY) / pitch) - 1, 0, GRID_H - 1);
  const ex = clamp(Math.ceil((canvasCssW - cam.offX) / pitch) + 1, 0, GRID_W - 1);
  const ey = clamp(Math.ceil((canvasCssH - cam.offY) / pitch) + 1, 0, GRID_H - 1);

  for (let y = sy; y <= ey; y++) {
    for (let x = sx; x <= ex; x++) {
      const loc = locations[idx(x, y)];
      if (!loc || Number(loc.is_visible ?? 1) !== 1) continue;
      const px = cam.offX + x * pitch;
      const py = cam.offY + y * pitch;
      ctx.strokeStyle = "rgba(36,50,74,0.24)";
      ctx.lineWidth = 1;
      ctx.strokeRect(px, py, cell, cell);
      ctx.fillStyle = cellColor(loc);
      ctx.fillRect(px, py, cell, cell);
      const aColor = areaColorFor(loc);
      if (aColor) {
        ctx.strokeStyle = aColor;
        ctx.lineWidth = Math.max(2, 3.2 * scale);
        ctx.strokeRect(px + 1.5, py + 1.5, cell - 3, cell - 3);
      }
      const rack = getRackById(loc.rack_id);
      if (rack) drawRackIcon(px, py, cell, rack, scale);
    }
  }

  drawAgvOverlay();

  const directSourceLoc = getLocationById(directMoveSourceCellId);
  if (directSourceLoc) {
    const px = cam.offX + Number(directSourceLoc.x) * pitch;
    const py = cam.offY + Number(directSourceLoc.y) * pitch;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(34,197,94,1)";
    ctx.strokeRect(px + 2, py + 2, cell - 4, cell - 4);
  }

  const directDestinationLoc = getLocationById(directMoveDestinationCellId);
  if (directDestinationLoc) {
    const px = cam.offX + Number(directDestinationLoc.x) * pitch;
    const py = cam.offY + Number(directDestinationLoc.y) * pitch;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(96,165,250,1)";
    ctx.strokeRect(px + 4, py + 4, cell - 8, cell - 8);
  }

  const operatorSourceLoc = operatorButtonSourceCell?.value ? getLocationById(Number(operatorButtonSourceCell.value)) : null;
  if (operatorSourceLoc) {
    const px = cam.offX + Number(operatorSourceLoc.x) * pitch;
    const py = cam.offY + Number(operatorSourceLoc.y) * pitch;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(16,185,129,1)";
    ctx.strokeRect(px + 6, py + 6, cell - 12, cell - 12);
  }

  const operatorDestinationLoc = operatorButtonDestinationCell?.value ? getLocationById(Number(operatorButtonDestinationCell.value)) : null;
  if (operatorDestinationLoc) {
    const px = cam.offX + Number(operatorDestinationLoc.x) * pitch;
    const py = cam.offY + Number(operatorDestinationLoc.y) * pitch;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(59,130,246,1)";
    ctx.strokeRect(px + 8, py + 8, cell - 16, cell - 16);
  }

  if (hoverCell && hoverCell.x >= 0 && hoverCell.x < GRID_W && hoverCell.y >= 0 && hoverCell.y < GRID_H) {
    const hoverLoc = getLocationAtGrid(hoverCell.x, hoverCell.y);
    if (hoverLoc && Number(hoverLoc.is_visible ?? 1) === 1) {
      const px = cam.offX + hoverCell.x * pitch;
      const py = cam.offY + hoverCell.y * pitch;
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.strokeRect(px + 3, py + 3, cell - 6, cell - 6);
    }
  }

  if (selected.x >= 0 && selected.x < GRID_W && selected.y >= 0 && selected.y < GRID_H) {
    const px = cam.offX + selected.x * pitch;
    const py = cam.offY + selected.y * pitch;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(255,209,102,1)";
    ctx.strokeRect(px + 1, py + 1, cell - 2, cell - 2);
  }

  for (const loc of selectedLocations()) {
    if (!loc || Number(loc.is_visible ?? 1) !== 1) continue;
    const px = cam.offX + Number(loc.x) * pitch;
    const py = cam.offY + Number(loc.y) * pitch;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(56,189,248,0.95)";
    ctx.strokeRect(px + 5, py + 5, cell - 10, cell - 10);
  }

  ctx.fillStyle = "rgba(147,164,199,0.9)";
  ctx.font = `${Math.max(12, 12 * scale)}px ui-sans-serif`;
  ctx.fillText(`Vista: ${GRID_W}×${GRID_H} | Zoom: ${scale.toFixed(2)}x`, 10, 18);
  drawMultiSelectBox();
}
function fitGridToScreen() {
  const basePitch = BASE_CELL + BASE_GAP;
  const scaleX = canvasCssW / Math.max(1, GRID_W * basePitch);
  const scaleY = canvasCssH / Math.max(1, GRID_H * basePitch);
  cam.scale = clamp(Math.min(scaleX, scaleY), MIN_ZOOM, MAX_ZOOM);
  const { pitch } = gridMetrics();
  cam.offX = (canvasCssW - GRID_W * pitch) / 2;
  cam.offY = (canvasCssH - GRID_H * pitch) / 2;
  draw();
}
function fitFreeLayoutToScreen() {
  const rows = freeLayoutLocations();
  if (!rows.length) {
    fitMapToScreen();
    return;
  }
  const bounds = rows.reduce((acc, loc) => {
    const rect = freeRectForLocation(loc);
    return {
      minX: Math.min(acc.minX, rect.x),
      minY: Math.min(acc.minY, rect.y),
      maxX: Math.max(acc.maxX, rect.x + rect.w),
      maxY: Math.max(acc.maxY, rect.y + rect.h),
    };
  }, { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
  const pad = 48;
  const worldW = Math.max(1, bounds.maxX - bounds.minX);
  const worldH = Math.max(1, bounds.maxY - bounds.minY);
  cam.scale = clamp(Math.min((canvasCssW - pad) / worldW, (canvasCssH - pad) / worldH), MIN_ZOOM, MAX_ZOOM);
  cam.offX = (canvasCssW - worldW * cam.scale) / 2 - bounds.minX * cam.scale;
  cam.offY = (canvasCssH - worldH * cam.scale) / 2 - bounds.minY * cam.scale;
  draw();
}
function fitMapToScreen() {
  if (isFreeLayoutMode()) fitFreeLayoutToScreen();
  else fitGridToScreen();
}
function zoomAtPoint(nextScale, mx, my) {
  if (isFreeLayoutMode()) {
    const before = canvasToWorld(mx, my);
    cam.scale = clamp(nextScale, MIN_ZOOM, MAX_ZOOM);
    cam.offX = mx - before.x * cam.scale;
    cam.offY = my - before.y * cam.scale;
    return;
  }
  const before = canvasToGrid(mx, my);
  cam.scale = clamp(nextScale, MIN_ZOOM, MAX_ZOOM);
  const after = canvasToGrid(mx, my);
  if (before && after) {
    const { pitch } = gridMetrics();
    cam.offX += (after.x - before.x) * pitch;
    cam.offY += (after.y - before.y) * pitch;
  }
}
async function fetchJson(url, options = {}) {
  const finalOptions = { cache: 'no-store', ...options };
  const res = await fetchWithAdminSession(url, finalOptions);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error((data && data.detail) || text || `HTTP ${res.status}`);
  return data;
}

function renderDiagnosisTable(target, rows, columns, emptyText = "Sin hallazgos.") {
  if (!target) return;
  if (!Array.isArray(rows) || rows.length === 0) {
    target.innerHTML = `<div class="small">${escapeHtml(emptyText)}</div>`;
    return;
  }
  const header = columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join("");
  const body = rows.map(row => `<tr>${columns.map(col => `<td>${escapeHtml(row?.[col.key] ?? "-")}</td>`).join("")}</tr>`).join("");
  target.innerHTML = `<table class="diagnosis-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function setCleanupActionButtonsDisabled(disabled) {
  [btnSelectSafeCleanup, btnCleanSelected, btnCloseSelectedOrders, btnReleaseSelectedRacks, btnResolveSelectedInconsistentRacks].forEach((btn) => {
    if (btn) btn.disabled = !!disabled || !adminToken;
  });
  if (btnForceReleaseOldActiveRacks) btnForceReleaseOldActiveRacks.disabled = !!disabled || !adminToken;
  const devBtn = document.getElementById("createOldActiveOrderTestBtn") || createOldActiveOrderTestBtn;
  if (devBtn) devBtn.disabled = !!disabled || !adminToken || devBtn.classList.contains("hidden");
}

function renderSelectableDiagnosisTable(target, rows, columns, options = {}) {
  if (!target) return;
  const idKey = options.idKey || "id";
  const type = options.type || "";
  const section = options.section || type;
  if (!Array.isArray(rows) || rows.length === 0) {
    target.innerHTML = `<div class="small">${escapeHtml(options.emptyText || "Sin hallazgos.")}</div>`;
    return;
  }
  const header = `<th></th>${columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join("")}<th>Seguro</th>`;
  const body = rows.map(row => {
    const idValue = row?.[idKey] ?? "";
    const safe = row?.safe === true;
    const reason = row?.safe_reason || (safe ? "Sí" : "No seguro");
    const checkbox = `<input type="checkbox" data-cleanup-select="1" data-cleanup-type="${escapeHtml(type)}" data-cleanup-section="${escapeHtml(section)}" data-cleanup-id="${escapeHtml(idValue)}" ${safe ? "" : "disabled"} />`;
    const cells = columns.map(col => `<td>${escapeHtml(row?.[col.key] ?? "-")}</td>`).join("");
    return `<tr class="${safe ? "" : "diagnosis-row-disabled"}"><td>${checkbox}</td>${cells}<td>${escapeHtml(safe ? "Sí" : reason)}</td></tr>`;
  }).join("");
  target.innerHTML = `<table class="diagnosis-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function getSelectedCleanupIds() {
  const selected = { order_ids: [], rack_ids: [] };
  document.querySelectorAll('[data-cleanup-select="1"]:checked').forEach((input) => {
    const id = Number(input.dataset.cleanupId || 0);
    if (!id) return;
    if (input.dataset.cleanupType === "order") selected.order_ids.push(id);
    if (input.dataset.cleanupType === "rack") selected.rack_ids.push(id);
  });
  selected.order_ids = [...new Set(selected.order_ids)];
  selected.rack_ids = [...new Set(selected.rack_ids)];
  return selected;
}

function getSelectedInconsistentRackIds() {
  return [...document.querySelectorAll('[data-cleanup-select="1"][data-cleanup-section="inconsistent-rack"]:checked')]
    .map(input => Number(input.dataset.cleanupId || 0))
    .filter(Boolean);
}

function getSelectedOldActiveRackIds() {
  return [...document.querySelectorAll('[data-cleanup-select="1"][data-cleanup-section="old-active-rack"]:checked')]
    .map(input => Number(input.dataset.cleanupId || 0))
    .filter(Boolean);
}

function getCurrentSelectedRackIdForDevTest() {
  const rackFormId = Number(rackId?.value || 0);
  if (rackFormId) return rackFormId;
  const selectedLocationRackId = Number(locations[idx(selected.x, selected.y)]?.rack_id || 0);
  if (selectedLocationRackId) return selectedLocationRackId;
  const cellRackId = Number(cellRack?.value || 0);
  if (cellRackId) return cellRackId;
  return 0;
}

function renderIntegrityCheck(target, rows) {
  if (!target) return;
  const values = Array.isArray(rows) ? rows : [];
  if (!values.length) {
    target.innerHTML = `<div class="small">Sin resultado.</div>`;
    return;
  }
  target.innerHTML = `<table class="diagnosis-table"><thead><tr><th>Resultado</th></tr></thead><tbody>${values.map(value => `<tr><td>${escapeHtml(value)}</td></tr>`).join("")}</tbody></table>`;
}

function renderCleanupDiagnosis(data = {}) {
  if (cleanupDiagnosisGeneratedAt) {
    const age = data.cleanup_min_age_minutes ? ` · edad mínima ${data.cleanup_min_age_minutes} min` : "";
    cleanupDiagnosisGeneratedAt.textContent = data.generated_at ? `Generado: ${data.generated_at}${age}` : "Solo lectura. No se ejecutan acciones automáticas.";
  }
  console.log("[cleanup] debug_tools_enabled =", data.debug_tools_enabled);
  const devBtn = document.getElementById("createOldActiveOrderTestBtn") || createOldActiveOrderTestBtn;
  if (devBtn) {
    const enabled = data.debug_tools_enabled === true;
    devBtn.classList.toggle("hidden", !enabled);
    devBtn.style.display = enabled ? "" : "none";
    devBtn.disabled = !enabled || !adminToken;
    devBtn.title = enabled ? "Herramienta temporal dev/debug" : "Disponible solo con debug=true o app_env=development";
  }
  renderSelectableDiagnosisTable(diagnosisOrphanRacks, data.orphan_reserved_racks, [
    { key: "rack_id", label: "Rack ID" },
    { key: "rack_code", label: "Rack" },
    { key: "rack_status", label: "Status" },
    { key: "location_id", label: "Location ID" },
    { key: "location_x", label: "X" },
    { key: "location_y", label: "Y" },
  ], { type: "rack", section: "orphan-rack", idKey: "rack_id" });
  renderSelectableDiagnosisTable(diagnosisInconsistentOrders, data.inconsistent_orders, [
    { key: "order_id", label: "Orden ID" },
    { key: "order_code", label: "Orden" },
    { key: "rack_code", label: "Rack" },
    { key: "status", label: "Status" },
    { key: "rcs_status", label: "RCS" },
    { key: "remote_task_code", label: "Task code" },
    { key: "updated_at", label: "Actualizada" },
  ], { type: "order", idKey: "order_id" });
  renderSelectableDiagnosisTable(diagnosisInconsistentRacks, data.active_order_available_racks, [
    { key: "rack_id", label: "Rack ID" },
    { key: "rack_code", label: "Rack" },
    { key: "rack_status", label: "Status rack" },
    { key: "order_id", label: "Orden ID" },
    { key: "order_code", label: "Orden" },
    { key: "order_status", label: "Status orden" },
    { key: "rcs_status", label: "RCS" },
  ], { type: "rack", section: "inconsistent-rack", idKey: "rack_id" });
  renderSelectableDiagnosisTable(diagnosisOldActiveRacks, data.old_active_racks, [
    { key: "rack_id", label: "Rack ID" },
    { key: "rack_code", label: "Rack" },
    { key: "order_id", label: "Orden ID" },
    { key: "order_status", label: "Status orden" },
    { key: "rcs_status", label: "RCS status" },
    { key: "age_minutes", label: "Edad (min)" },
    { key: "motivo", label: "Motivo" },
  ], { type: "rack", section: "old-active-rack", idKey: "rack_id", emptyText: "Sin racks bloqueados por órdenes activas viejas." });
  renderSelectableDiagnosisTable(diagnosisStuckCancelRecoverable, data.stuck_cancel_recoverable, [
    { key: "order_id", label: "Orden ID" },
    { key: "rack_id", label: "Rack ID" },
    { key: "robot_code", label: "Robot" },
    { key: "age_minutes", label: "Edad (min)" },
    { key: "motivo_detectado", label: "Motivo" },
    { key: "safe_recovery", label: "safe_recovery" },
  ], { type: "rack", section: "old-active-rack", idKey: "rack_id", emptyText: "Sin cancelaciones atoradas recuperables." });
  renderDiagnosisTable(diagnosisInconsistentLocations, data.inconsistent_locations, [
    { key: "location_id", label: "Location ID" },
    { key: "x", label: "X" },
    { key: "y", label: "Y" },
    { key: "status", label: "Status" },
    { key: "rack_id", label: "Rack ID" },
    { key: "rack_code", label: "Rack" },
    { key: "reason", label: "Motivo" },
  ]);
  renderIntegrityCheck(diagnosisIntegrityCheck, data.integrity_check);
  setCleanupActionButtonsDisabled(false);
}

async function loadCleanupHealth() {
  if (!adminToken || !cleanupHealthBadge) return null;
  const health = await fetchJson(API.adminCleanupHealth, { headers: fetchHeaders() });
  cleanupHealthBadge.classList.toggle("hidden", Number(health.total_orphans || 0) <= 0);
  return health;
}

async function loadCleanupDiagnosis() {
  console.log("[cleanup] loading diagnosis");
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Consultando diagnóstico...";
  if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = true;
  setCleanupActionButtonsDisabled(true);
  try {
    const response = await fetchWithAdminSession("/api/admin/cleanup-diagnosis", {
      headers: adminToken ? { "X-Admin-Token": adminToken } : {}
    });
    const data = await response.json();
    if (!response.ok) throw new Error((data && data.detail) || `HTTP ${response.status}`);
    console.log("[cleanup] diagnosis loaded", data);
    renderCleanupDiagnosis(data || {});
    loadCleanupHealth().catch(err => console.warn("[cleanup-health] No se pudo actualizar el indicador.", err));
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Diagnóstico actualizado.";
    return data;
  } finally {
    if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = false;
  }
}

const refreshCleanupDiagnosis = loadCleanupDiagnosis;

function openCleanupConfirmModal(action = "all") {
  pendingCleanupAction = action;
  if (cleanupConfirmMsg) cleanupConfirmMsg.textContent = "";
  if (cleanupConfirmModal) cleanupConfirmModal.classList.remove("hidden");
}

function closeCleanupConfirmModal() {
  if (cleanupConfirmModal) cleanupConfirmModal.classList.add("hidden");
}

let pendingCleanupAction = "all";

function buildCleanupPayloadForAction(action) {
  const selected = getSelectedCleanupIds();
  if (action === "orders") return { order_ids: selected.order_ids, rack_ids: [] };
  if (action === "racks") return { order_ids: [], rack_ids: selected.rack_ids };
  return selected;
}

async function executeSelectedCleanup(action = pendingCleanupAction) {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const payload = buildCleanupPayloadForAction(action);
  if (!payload.order_ids.length && !payload.rack_ids.length) throw new Error("Selecciona al menos un registro seguro.");
  if (cleanupConfirmMsg) cleanupConfirmMsg.textContent = "Ejecutando cierre local...";
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Ejecutando cierre local...";
  if (btnConfirmCleanupClose) btnConfirmCleanupClose.disabled = true;
  if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = true;
  setCleanupActionButtonsDisabled(true);
  try {
    const result = await fetchJson(API.adminCleanupCloseInconsistentOrders, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
    closeCleanupConfirmModal();
    renderCleanupDiagnosis(result?.diagnosis || {});
    const closed = Array.isArray(result?.closed_orders) ? result.closed_orders.length : 0;
    const racks = Array.isArray(result?.released_racks) ? result.released_racks.length : 0;
    const skipped = Array.isArray(result?.skipped_orders) ? result.skipped_orders.length : 0;
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Cierre local terminado. Órdenes cerradas: ${closed}. Racks liberados: ${racks}. Omitidas: ${skipped}.`;
    await Promise.all([loadLocations(), loadCatalog(), loadMovementOrders()]);
    draw();
  } finally {
    if (btnConfirmCleanupClose) btnConfirmCleanupClose.disabled = false;
    if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = false;
  }
}

async function resolveSelectedInconsistentRacks() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const rackIds = [...new Set(getSelectedInconsistentRackIds())];
  if (!rackIds.length) throw new Error("Selecciona al menos un rack inconsistente seguro.");
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Resolviendo racks inconsistentes...";
  if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = true;
  setCleanupActionButtonsDisabled(true);
  try {
    const result = await fetchJson(API.adminCleanupResolveInconsistentRacks, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ rack_ids: rackIds }) });
    renderCleanupDiagnosis(result?.diagnosis || {});
    const closed = Array.isArray(result?.closed_orders) ? result.closed_orders.length : 0;
    const racks = Array.isArray(result?.released_racks) ? result.released_racks.length : 0;
    const skipped = Array.isArray(result?.skipped) ? result.skipped.length : 0;
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Resolución terminada. Órdenes cerradas: ${closed}. Racks liberados: ${racks}. Omitidos: ${skipped}.`;
    await Promise.all([loadLocations(), loadCatalog(), loadMovementOrders()]);
    draw();
  } finally {
    if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = false;
    setCleanupActionButtonsDisabled(false);
  }
}

async function forceReleaseSelectedOldActiveRacks() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const rackIds = [...new Set(getSelectedOldActiveRackIds())];
  if (!rackIds.length) throw new Error("Selecciona al menos un rack seguro para recuperación avanzada.");
  const confirmed = window.confirm("Esta acción cerrará localmente órdenes activas viejas y liberará sus racks SOLO en el despacho local. No cancela tareas dentro del RCS. Úsese únicamente si confirmaste que la tarea ya no existe o quedó atascada. ¿Continuar?");
  if (!confirmed) return;
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Ejecutando recuperación avanzada...";
  if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = true;
  setCleanupActionButtonsDisabled(true);
  try {
    const result = await fetchJson(API.adminForceReleaseOldActiveRacks, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ rack_ids: rackIds }) });
    await loadCleanupDiagnosis();
    const closed = Array.isArray(result?.closed_orders) ? result.closed_orders.length : 0;
    const racks = Array.isArray(result?.released_racks) ? result.released_racks.length : 0;
    const skipped = Array.isArray(result?.skipped) ? result.skipped.length : 0;
    const skippedText = skipped ? ` Omitidos: ${skipped}. ${result.skipped.map(row => row.reason || "").filter(Boolean).join(" | ")}` : " Omitidos: 0.";
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Recuperación avanzada terminada. Órdenes cerradas localmente: ${closed}. Racks liberados: ${racks}.${skippedText}`;
    await Promise.all([loadMovementOrders(), loadCatalog(), loadLocations()]);
    draw();
  } finally {
    if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = false;
    setCleanupActionButtonsDisabled(false);
  }
}

async function createOldActiveOrderTestForSelectedRack() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const devBtn = document.getElementById("createOldActiveOrderTestBtn") || createOldActiveOrderTestBtn;
  if (!devBtn || devBtn.classList.contains("hidden")) {
    throw new Error("Herramienta de prueba disponible solo en debug/dev.");
  }
  const selectedRackId = getCurrentSelectedRackIdForDevTest();
  if (!selectedRackId) {
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Selecciona primero un rack de prueba.";
    console.warn("[cleanup-dev] No hay rack seleccionado para crear orden vieja de prueba.");
    return;
  }
  const confirmed = window.confirm("Crear\u00e1 una orden activa vieja falsa SOLO para pruebas locales de force release sobre el rack seleccionado. No se enviar\u00e1 nada al RCS. \u00bfContinuar?");
  if (!confirmed) return;
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = "Creando orden activa vieja de prueba...";
  if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = true;
  setCleanupActionButtonsDisabled(true);
  try {
    const result = await fetchJson(API.adminCreateOldActiveOrderTest, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...fetchHeaders() },
      body: JSON.stringify({ rack_id: selectedRackId }),
    });
    renderCleanupDiagnosis(result?.diagnosis || {});
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = result?.message || "Orden vieja de prueba creada para validaci\u00f3n de force release.";
    await Promise.all([loadCleanupDiagnosis(), loadMovementOrders(), loadCatalog(), loadLocations()]);
    draw();
  } finally {
    if (btnRefreshCleanupDiagnosis) btnRefreshCleanupDiagnosis.disabled = false;
    setCleanupActionButtonsDisabled(false);
  }
}

async function openCleanupDiagnosisModal() {
  console.log("[cleanup] open modal");
  const modal = document.getElementById("cleanupDiagnosisModal");
  if (!modal) {
    console.error("[cleanup] cleanupDiagnosisModal no encontrado");
    return;
  }
  modal.classList.remove("hidden");
  modal.style.display = "flex";
  try {
    await loadCleanupDiagnosis();
  } catch (err) {
    console.error("[cleanup] error cargando diagnóstico", err);
    if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
    if (adminMsg) adminMsg.textContent = `Error: ${String(err)}`;
  }
}

window.loadCleanupDiagnosis = loadCleanupDiagnosis;
window.openCleanupDiagnosisModal = openCleanupDiagnosisModal;

function closeCleanupDiagnosisModal() {
  const modal = document.getElementById("cleanupDiagnosisModal");
  if (!modal) {
    console.warn("[cleanup-diagnosis] No se encontró el modal #cleanupDiagnosisModal.");
    return;
  }
  modal.classList.add("hidden");
  modal.style.display = "";
}

function openDbBackupsModal() {
  if (!dbBackupsModal) return;
  updateBackupControls();
  if (dbBackupsMsg) dbBackupsMsg.textContent = "";
  dbBackupsModal.classList.remove("hidden");
  dbBackupsModal.style.display = "flex";
  loadBackupStatus().catch(err => {
    console.warn("[backup-status] failed", err);
  });
  loadPreRestoreBackups().catch(err => {
    console.warn("[pre-restore-backups] failed", err);
  });
}

function closeDbBackupsModal() {
  if (!dbBackupsModal) return;
  dbBackupsModal.classList.add("hidden");
  dbBackupsModal.style.display = "";
}

function filenameFromContentDisposition(value) {
  const match = String(value || "").match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  return match ? decodeURIComponent(match[1].replace(/"/g, "")) : "";
}

function updateBackupControls() {
  const enabled = !!adminToken;
  if (btnDownloadDb) btnDownloadDb.disabled = !enabled;
  if (btnDownloadFullBackup) btnDownloadFullBackup.disabled = !enabled;
  if (btnChooseBackupFile) btnChooseBackupFile.disabled = !enabled || restorePendingRestart;
  if (btnValidateBackup) btnValidateBackup.disabled = !enabled || restorePendingRestart || !selectedBackupFile;
  if (btnRestoreBackup) btnRestoreBackup.disabled = !enabled || restorePendingRestart || !selectedBackupFile || !lastBackupValidationOk;
  if (backupRestartPendingBadge) {
    backupRestartPendingBadge.classList.toggle("hidden", !restorePendingRestart);
  }
  if (btnMarkBackupRestarted) {
    btnMarkBackupRestarted.classList.toggle("hidden", !restorePendingRestart);
    btnMarkBackupRestarted.style.display = restorePendingRestart ? "inline-block" : "none";
    btnMarkBackupRestarted.disabled = !enabled || !restorePendingRestart;
  }
}

function applyBackupStatus(data = {}) {
  restorePendingRestart = data?.restore_pending_restart === true;
  if (restorePendingRestart) {
    lastBackupValidationOk = false;
    if (dbBackupsMsg && data.message) dbBackupsMsg.textContent = data.message;
  }
  updateBackupControls();
}

async function loadBackupStatus() {
  if (!adminToken) {
    restorePendingRestart = false;
    updateBackupControls();
    return;
  }
  const data = await fetchJson(API.adminBackupStatus, { headers: fetchHeaders() });
  applyBackupStatus(data);
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function renderPreRestoreBackups(backups = []) {
  if (!preRestoreBackupsList) return;
  const rows = (backups || []).slice(0, 10);
  if (!rows.length) {
    preRestoreBackupsList.textContent = "No hay backups previos.";
    return;
  }
  preRestoreBackupsList.innerHTML = rows.map((item) => `
    <div class="backup-history-row">
      <span class="backup-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
      <span>${escapeHtml(formatBytes(item.size))}</span>
      <span>${escapeHtml(item.modified_at || "")}</span>
      <button class="btn" type="button" data-pre-restore-download="${escapeHtml(item.name)}">Descargar</button>
    </div>
  `).join("");
  preRestoreBackupsList.querySelectorAll("[data-pre-restore-download]").forEach((btn) => {
    btn.addEventListener("click", () => downloadPreRestoreBackup(btn.dataset.preRestoreDownload, btn));
  });
}

async function loadPreRestoreBackups() {
  if (!adminToken) {
    renderPreRestoreBackups([]);
    return;
  }
  const data = await fetchJson(API.adminPreRestoreBackups, { headers: fetchHeaders() });
  renderPreRestoreBackups(data?.backups || []);
}

function chooseBackupFile() {
  if (!adminToken || restorePendingRestart || !backupZipFile) return;
  backupZipFile.click();
}

function handleBackupFileSelected() {
  selectedBackupFile = backupZipFile?.files?.[0] || null;
  lastBackupValidationOk = false;
  if (backupSelectedFileName) {
    backupSelectedFileName.textContent = selectedBackupFile ? selectedBackupFile.name : "Sin archivo seleccionado.";
  }
  if (dbBackupsMsg) dbBackupsMsg.textContent = "";
  updateBackupControls();
}

async function downloadDatabaseBackup() {
  if (!adminToken) {
    if (dbBackupsMsg) dbBackupsMsg.textContent = "No se pudo descargar la base de datos.";
    return;
  }
  await downloadAdminFile(API.adminDatabaseDownload, btnDownloadDb, "agv_backup.db", "No se pudo descargar la base de datos.");
}

async function downloadFullBackup() {
  if (!adminToken) {
    if (dbBackupsMsg) dbBackupsMsg.textContent = "No se pudo descargar el backup completo.";
    return;
  }
  await downloadAdminFile(API.adminFullBackupDownload, btnDownloadFullBackup, "agv_full_backup.zip", "No se pudo descargar el backup completo.");
}

async function downloadPreRestoreBackup(filename, button) {
  if (!adminToken || !filename) return;
  await downloadAdminFile(
    API.adminPreRestoreBackupDownload(filename),
    button,
    filename,
    "No se pudo descargar el backup previo."
  );
}

async function validateSelectedBackup() {
  if (!adminToken || restorePendingRestart || !selectedBackupFile) return;
  if (dbBackupsMsg) dbBackupsMsg.textContent = "Validando backup...";
  if (btnValidateBackup) btnValidateBackup.disabled = true;
  try {
    const formData = new FormData();
    formData.append("file", selectedBackupFile);
    const response = await fetch(API.adminBackupValidate, {
      method: "POST",
      headers: fetchHeaders(),
      body: formData,
    });
    const data = await response.json();
    const details = data?.ok
      ? `DB: ${data.db_ok ? "ok" : "error"} | uploads: ${data.uploads_count ?? 0} | archivos: ${data.files_count ?? 0}`
      : "";
    lastBackupValidationOk = data?.ok === true;
    if (dbBackupsMsg) dbBackupsMsg.textContent = [data?.message || "No se pudo validar el backup.", details].filter(Boolean).join(" ");
  } catch (err) {
    lastBackupValidationOk = false;
    console.warn("[backup-validate] failed", err);
    if (dbBackupsMsg) dbBackupsMsg.textContent = "No se pudo validar el backup.";
  } finally {
    updateBackupControls();
  }
}

function openBackupRestoreConfirmModal() {
  if (restorePendingRestart) return;
  if (!backupRestoreConfirmModal || !lastBackupValidationOk || !selectedBackupFile) return;
  if (backupRestoreConfirmMsg) backupRestoreConfirmMsg.textContent = "";
  backupRestoreConfirmModal.classList.remove("hidden");
  backupRestoreConfirmModal.style.display = "flex";
}

function closeBackupRestoreConfirmModal() {
  if (!backupRestoreConfirmModal) return;
  backupRestoreConfirmModal.classList.add("hidden");
  backupRestoreConfirmModal.style.display = "";
}

async function restoreSelectedBackup() {
  if (!adminToken || restorePendingRestart || !selectedBackupFile || !lastBackupValidationOk) return;
  if (backupRestoreConfirmMsg) backupRestoreConfirmMsg.textContent = "Restaurando backup...";
  if (btnConfirmBackupRestore) btnConfirmBackupRestore.disabled = true;
  if (btnRestoreBackup) btnRestoreBackup.disabled = true;
  try {
    const formData = new FormData();
    formData.append("file", selectedBackupFile);
    const response = await fetch(API.adminBackupRestore, {
      method: "POST",
      headers: fetchHeaders(),
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || data?.ok !== true) {
      throw new Error(data?.message || `HTTP ${response.status}`);
    }
    closeBackupRestoreConfirmModal();
    if (dbBackupsMsg) {
      dbBackupsMsg.textContent = data?.restart_scheduled
        ? "Backup restaurado correctamente.\nLa aplicación se reiniciará automáticamente.\nEspera unos segundos y vuelve a abrir la página si no recarga sola."
        : (data.message || data?.restart?.message || "Backup restaurado correctamente.\nReinicia la aplicación manualmente para aplicar completamente los cambios.");
    }
    restorePendingRestart = data?.restore_pending_restart === true;
    lastBackupValidationOk = false;
    await loadPreRestoreBackups();
    if (data?.restart_scheduled) {
      await waitForSoftwareReconnect(dbBackupsMsg);
    }
  } catch (err) {
    console.warn("[backup-restore] failed", err);
    if (backupRestoreConfirmMsg) backupRestoreConfirmMsg.textContent = "No se pudo restaurar el backup.";
  } finally {
    if (btnConfirmBackupRestore) btnConfirmBackupRestore.disabled = false;
    updateBackupControls();
  }
}

async function markBackupRestarted() {
  if (!adminToken || !restorePendingRestart) return;
  if (dbBackupsMsg) dbBackupsMsg.textContent = "Marcando como reiniciado...";
  if (btnMarkBackupRestarted) btnMarkBackupRestarted.disabled = true;
  try {
    const data = await fetchJson(API.adminBackupMarkRestarted, {
      method: "POST",
      headers: fetchHeaders(),
    });
    applyBackupStatus(data);
    if (dbBackupsMsg) dbBackupsMsg.textContent = "Estado de reinicio limpiado.";
  } catch (err) {
    console.warn("[backup-mark-restarted] failed", err);
    if (dbBackupsMsg) dbBackupsMsg.textContent = "No se pudo marcar como reiniciado.";
  } finally {
    updateBackupControls();
  }
}

async function downloadAdminFile(downloadUrl, button, fallbackName, failureMessage) {
  if (dbBackupsMsg) dbBackupsMsg.textContent = "";
  if (button) button.disabled = true;
  try {
    const response = await fetchWithAdminSession(downloadUrl, {
      method: "GET",
      headers: fetchHeaders(),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filenameFromContentDisposition(response.headers.get("Content-Disposition")) || fallbackName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (err) {
    console.warn("[admin-file-download] failed", err);
    if (dbBackupsMsg) dbBackupsMsg.textContent = failureMessage;
  } finally {
    if (button) button.disabled = !adminToken;
  }
}

function setAdminUI(enabled) {
  if (adminState) adminState.textContent = enabled ? "Activo" : "Bloqueado";
  if (adminActions) adminActions.style.display = enabled ? "block" : "none";
  if (btnAdminLogin) btnAdminLogin.style.display = enabled ? "none" : "inline-block";
  if (btnAdminLock) btnAdminLock.style.display = enabled ? "inline-block" : "none";
  if (btnSimulateComplete) btnSimulateComplete.style.display = enabled ? "inline-block" : "none";
  if (btnDeleteOrder) btnDeleteOrder.style.display = enabled ? "inline-block" : "none";
  if (orderDetailBox) orderDetailBox.style.display = enabled ? "block" : "none";
  updateCardVisibility(enabled);

  const alwaysEnabledIds = new Set([
    "adminPwd", "btnAdminLogin", "btnAdminLock", "btnCenterGrid",
    "fifoSourceArea", "fifoDestinationArea", "fifoMaterial", "fifoPriority", "fifoComment", "fifoAgvCode", "fifoTaskTyp", "btnValidateFifo", "btnExecuteFifo",
    "btnDirectPickSource", "btnDirectPickDestination", "btnDirectClearSelection", "directPriority", "directAgvCode", "directTaskTyp", "directComment", "btnExecuteDirectMove",
    "operatorWindowSelect", "operatorWindowPassword",
    "btnOperatorActionCancel", "btnOperatorActionConfirm", "btnOperatorActionPreview", "btnOperatorActionPanelClear", "btnOperatorActionPickSource", "btnOperatorActionPickDestination",
    "operatorActionAreaSelect", "operatorActionMaterial", "operatorActionAgv", "operatorActionTaskTyp", "operatorCancelMode", "operatorCancelReturnArea",
    "scanQrScannerSelect", "scanQrScannerManual", "scanQrValue", "btnScanQrPreview", "btnScanQrExecute", "btnRefreshScanQrHistory"
  ]);

  const adminOnlyIds = new Set([
    "operatorWindowId", "operatorWindowName", "operatorWindowActive", "operatorWindowBgColor", "operatorWindowButtonCount", "operatorWindowPasswordAdmin", "btnSaveOperatorWindow", "btnNewOperatorWindow", "adminWindowSelect",
    "btnSelectCellsByArea",
    "mapLayoutMode", "freeLayoutEditEnabled", "btnAddFreeCell",
    "podPositionRack", "btnQueryPodPosition", "rcsPodPositionEndpoint", "rcsRackSyncQueryEndpoint", "rcsRackSyncBindEndpoint", "rcsRackSyncScheduleEnabled", "rcsRackSyncScheduleTime", "btnRackSyncPreview", "btnRackSyncQuery", "btnRackSyncBind", "btnRackSyncHistory",
    "operatorButtonIndex", "operatorButtonActive", "operatorButtonLabel", "operatorButtonColor", "operatorButtonMode", "operatorButtonPriority", "operatorButtonSourceArea", "operatorButtonDestinationArea", "operatorButtonPointDestinationArea", "operatorButtonMaterial", "operatorButtonSourceCell", "operatorButtonDestinationCell", "btnOperatorButtonPickSource", "btnOperatorButtonPickDestination", "operatorButtonAgv", "operatorButtonTaskTyp", "operatorButtonComment", "btnSaveOperatorButton", "btnAddOperatorPointField",
    "btnRefreshScanTerminals",
    "qrTransitionName", "qrTransitionDescription", "qrTransitionScope", "qrTransitionMatchMode", "qrTransitionIgnoreCurrentMaterial", "qrTransitionSourceMatchMode", "qrTransitionQrRule", "qrTransitionScanner", "qrTransitionSourceArea", "qrTransitionDestinationArea", "qrTransitionSourceCell", "qrTransitionDestinationCell", "qrTransitionCurrentMaterial", "qrTransitionCurrentRackStatus", "qrTransitionNextMaterial", "qrTransitionNextRackStatus", "qrTransitionNextQuantity", "qrTransitionClearQuantity", "qrTransitionNextComment", "qrTransitionAppendComment", "qrTransitionApplyOn", "qrTransitionPriority", "qrTransitionActive", "btnSaveQrTransitionRule", "btnNewQrTransitionRule", "btnDisableQrTransitionRule", "qrTransitionPreviewOrderId", "btnPreviewQrTransition", "btnApplyQrTransition", "btnRefreshQrTransitionLogs"
  ]);

  const disabledPlaceholderIds = new Set([
  ]);

  document.querySelectorAll("input, textarea, select, button").forEach((el) => {
    if (
      el.closest?.(".action-tabs-header") ||
      el.classList?.contains("action-tab-button")
    ) {
      return;
    }
    if (el.closest?.("#operatorButtonsBox") || el.closest?.("#operatorActionPanel")) {
      el.disabled = false;
      return;
    }
    if (!el.id) return;
    if (disabledPlaceholderIds.has(el.id)) {
      el.disabled = true;
      return;
    }
    if (alwaysEnabledIds.has(el.id)) {
      el.disabled = false;
      return;
    }
    if (adminOnlyIds.has(el.id)) {
      el.disabled = !enabled;
      return;
    }
    el.disabled = !enabled;
  });
  ensureActionTabsLayout();
  forceShowActionTabsHeader();
  refreshActionTabsAfterVisibilityChange();
  repairActionTabsLayout();
  updateBackupControls();
  setCellBulkControlsState();
  updateAddFreeCellAvailability();
  updateRackSyncButtons();
  syncRouteModeSections();
}
function applyAgvOverlayConfigFromGrid(cfg = {}) {
  agvOverlayConfig.scale_x = Number(cfg.agv_overlay_scale_x ?? 1) || 1;
  agvOverlayConfig.scale_y = Number(cfg.agv_overlay_scale_y ?? 1) || 1;
  agvOverlayConfig.offset_x = Number(cfg.agv_overlay_offset_x ?? 0) || 0;
  agvOverlayConfig.offset_y = Number(cfg.agv_overlay_offset_y ?? 0) || 0;
  agvOverlayConfig.rotation_deg = Number(cfg.agv_overlay_rotation_deg ?? 0) || 0;
  agvOverlayConfig.orientation_offset_deg = Number(cfg.agv_orientation_offset_deg ?? 0) || 0;
  agvOverlayConfig.mirror_x = Number(cfg.agv_overlay_mirror_x ?? 0) === 1 ? 1 : 0;
  agvOverlayConfig.mirror_y = Number(cfg.agv_overlay_mirror_y ?? 0) === 1 ? 1 : 0;
  agvOverlayConfig.icon_angle_mirror = Number(cfg.agv_icon_angle_mirror ?? 0) === 1 ? 1 : 0;
  if (agvOverlayScaleX) agvOverlayScaleX.value = String(agvOverlayConfig.scale_x);
  if (agvOverlayScaleY) agvOverlayScaleY.value = String(agvOverlayConfig.scale_y);
  if (agvOverlayOffsetX) agvOverlayOffsetX.value = String(agvOverlayConfig.offset_x);
  if (agvOverlayOffsetY) agvOverlayOffsetY.value = String(agvOverlayConfig.offset_y);
  if (agvOverlayRotationDeg) agvOverlayRotationDeg.value = String(agvOverlayConfig.rotation_deg);
  if (agvOrientationOffsetDeg) agvOrientationOffsetDeg.value = String(agvOverlayConfig.orientation_offset_deg);
  if (agvOverlayMirrorX) agvOverlayMirrorX.value = String(agvOverlayConfig.mirror_x);
  if (agvOverlayMirrorY) agvOverlayMirrorY.value = String(agvOverlayConfig.mirror_y);
  if (agvIconAngleMirror) agvIconAngleMirror.value = String(agvOverlayConfig.icon_angle_mirror);
}
function syncAgvOverlayConfigFromInputs() {
  agvOverlayConfig.scale_x = Number(agvOverlayScaleX?.value || 1) || 1;
  agvOverlayConfig.scale_y = Number(agvOverlayScaleY?.value || 1) || 1;
  agvOverlayConfig.offset_x = Number(agvOverlayOffsetX?.value || 0) || 0;
  agvOverlayConfig.offset_y = Number(agvOverlayOffsetY?.value || 0) || 0;
  agvOverlayConfig.rotation_deg = Number(agvOverlayRotationDeg?.value || 0) || 0;
  agvOverlayConfig.orientation_offset_deg = Number(agvOrientationOffsetDeg?.value || 0) || 0;
  agvOverlayConfig.mirror_x = Number(agvOverlayMirrorX?.value || 0) === 1 ? 1 : 0;
  agvOverlayConfig.mirror_y = Number(agvOverlayMirrorY?.value || 0) === 1 ? 1 : 0;
  agvOverlayConfig.icon_angle_mirror = Number(agvIconAngleMirror?.value || 0) === 1 ? 1 : 0;
}
async function loadGridConfig() {
  const cfg = await fetchJson(API.gridConfig);
  GRID_H = Number(cfg.display_rows) || DB_H;
  GRID_W = Number(cfg.display_cols) || DB_W;
  mapLayoutMode = String(cfg.map_layout_mode || "grid") === "free" ? "free" : "grid";
  dispRows.value = GRID_H;
  dispCols.value = GRID_W;
  if (mapLayoutModeSelect) mapLayoutModeSelect.value = mapLayoutMode;
  applyAgvOverlayConfigFromGrid(cfg);
  RUNTIME_AUTO_REFRESH_MS = Math.max(2000, Math.round((Number(cfg.runtime_refresh_seconds) || 5) * 1000));
  RUNTIME_SOCKET_RECONNECT_MS = Math.max(1000, Math.round((Number(cfg.runtime_reconnect_seconds) || 3) * 1000));
  if (runtimeRefreshSeconds) runtimeRefreshSeconds.value = String(Number(cfg.runtime_refresh_seconds) || 5);
  if (runtimeReconnectSeconds) runtimeReconnectSeconds.value = String(Number(cfg.runtime_reconnect_seconds) || 3);
  setConn("Conectado");
}
async function loadLocations() {
  const rows = await fetchJson(API.locations);
  locations = new Array(DB_W * DB_H).fill(null);
  for (const loc of rows) locations[idx(loc.x, loc.y)] = loc;
  renderOperatorCellOptions();
  renderQrCatalogOptions();
}
async function loadCatalog() {
  catalog = await fetchJson(API.catalog);
  renderAreaOptions();
  renderMaterialOptions();
  renderRackOptions();
  renderPodPositionRackOptions();
  renderAreaList();
  renderMaterialList();
  renderRackList();
  renderQrCatalogOptions();
}

function applyRuntimeSnapshotData(snapshot, preferredOrderId = null, options = {}) {
  const rows = Array.isArray(snapshot?.locations) ? snapshot.locations : [];
  const orders = Array.isArray(snapshot?.orders) ? snapshot.orders : [];
  const debugLog = Array.isArray(snapshot?.debug_log) ? snapshot.debug_log : [];

  locations = new Array(DB_W * DB_H).fill(null);
  for (const loc of rows) locations[idx(loc.x, loc.y)] = loc;
  movementOrders = orders;
  debugConsoleEvents = debugLog;

  if (preferredOrderId && movementOrders.some(x => x.order_id === preferredOrderId)) {
    selectedOrderId = preferredOrderId;
  } else if (selectedOrderId && movementOrders.some(x => x.order_id === selectedOrderId)) {
  } else {
    selectedOrderId = movementOrders.length ? movementOrders[0].order_id : null;
  }

  renderOperatorCellOptions();
  renderOrdersList();
  renderOrderStatusQuery(null, null, debugConsoleEvents, true);
  renderDirectMoveSelection();
  const deferCellRender = !options.forceAdminRender && isEditingLockEffective("cell");
  if (deferCellRender) {
    editingLock.pendingRuntimeSnapshot = snapshot;
    updateEditRefreshNotices();
  } else {
    const selectedLoc = getLocationAtGrid(selected.x, selected.y);
    if (isMultiSelectionActive()) {
      multiSelectedLocationIds = new Set(Array.from(multiSelectedLocationIds).filter(id => getLocationById(id)));
      fillMultiCellForm();
    } else if (selectedLoc) {
      fillCellForm(selectedLoc);
    }
    setCellBulkControlsState();
    refreshRackReservationFieldsForSelection();
  }

  draw();

  if (robotMonitorEnabled) {
    const data = snapshot?.robot_monitor || { ok: false, robots: [], message: 'Sin datos todavía.' };
    const robots = Array.isArray(data.robots) ? data.robots : [];
    const count = robots.length;
    syncRobotVisualTargets(robots);
    if (robotMonitorSubtitle) {
      robotMonitorSubtitle.textContent = count > 0 ? '' : (data.message || (data.ok ? 'Consulta exitosa sin robots.' : 'Sin conexión'));
    }
    renderRobotMonitorItems(latestRobotMonitorItems, data.message || 'Sin datos todavía.');
  } else {
    latestRobotMonitorItems = [];
  }
}
async function refreshMatrixViewData() {
  await loadLocations();
  renderDirectMoveSelection();
  draw();
}

function renderAreaOptions(renderOptions = {}) {
  if (!renderOptions.force && isEditingLockEffective("cell")) {
    deferAdminRefresh();
  } else {
    const current = cellArea.value;
    cellArea.innerHTML = `<option value="">Sin area</option>` + catalog.areas.map(a => `<option value="${a.id}">${a.code} - ${a.name}</option>`).join("");
    if ([...cellArea.options].some(o => o.value === current)) cellArea.value = current;
  }
  const activeAreas = catalog.areas.filter(a => Number(a.is_active) === 1);
  const currentSource = fifoSourceArea.value;
  const currentDestination = fifoDestinationArea.value;
  const options = `<option value="">Selecciona</option>` + activeAreas.map(a => `<option value="${a.id}">${a.code} - ${a.name}</option>`).join("");
  fifoSourceArea.innerHTML = options;
  fifoDestinationArea.innerHTML = options;
  if ([...fifoSourceArea.options].some(o => o.value === currentSource)) fifoSourceArea.value = currentSource;
  if ([...fifoDestinationArea.options].some(o => o.value === currentDestination)) fifoDestinationArea.value = currentDestination;
  if (operatorButtonSourceArea) {
    const cur = operatorButtonSourceArea.value;
    operatorButtonSourceArea.innerHTML = options;
    if ([...operatorButtonSourceArea.options].some(o => o.value === cur)) operatorButtonSourceArea.value = cur;
  }
  if (operatorButtonDestinationArea) {
    const cur = operatorButtonDestinationArea.value;
    operatorButtonDestinationArea.innerHTML = options;
    if ([...operatorButtonDestinationArea.options].some(o => o.value === cur)) operatorButtonDestinationArea.value = cur;
  }
  if (operatorButtonPointDestinationArea) {
    const cur = operatorButtonPointDestinationArea.value;
    operatorButtonPointDestinationArea.innerHTML = options;
    if ([...operatorButtonPointDestinationArea.options].some(o => o.value === cur)) operatorButtonPointDestinationArea.value = cur;
  }
}
function renderMaterialOptions() {
  const current = rackMaterial.value;
  rackMaterial.innerHTML = `<option value="">Sin material</option>` + catalog.materials.map(m => `<option value="${m.id}">${m.code} - ${m.name}</option>`).join("");
  if ([...rackMaterial.options].some(o => o.value === current)) rackMaterial.value = current;

  const activeMaterials = catalog.materials.filter(m => Number(m.is_active) === 1);
  const currentFifo = fifoMaterial.value;
  fifoMaterial.innerHTML = `<option value="">Selecciona</option>` + activeMaterials.map(m => `<option value="${m.id}">${m.code} - ${m.name}</option>`).join("");
  if ([...fifoMaterial.options].some(o => o.value === currentFifo)) fifoMaterial.value = currentFifo;
  if (operatorButtonMaterial) {
    const cur = operatorButtonMaterial.value;
    operatorButtonMaterial.innerHTML = `<option value="">Selecciona</option>` + activeMaterials.map(m => `<option value="${m.id}">${m.code} - ${m.name}</option>`).join("");
    if ([...operatorButtonMaterial.options].some(o => o.value === cur)) operatorButtonMaterial.value = cur;
  }
  renderOperatorPointMaterialSelector(getSelectedPointMaterialIds());
  renderOperatorActionMaterialOptions();
}
function availableRacksForCell() {
  const currentRack = locations[idx(selected.x, selected.y)]?.rack_id;
  return catalog.racks.filter(r => r.location_x == null || Number(r.id) === Number(currentRack));
}
function renderRackOptions(renderOptions = {}) {
  if (!renderOptions.force && isEditingLockEffective("cell")) {
    deferAdminRefresh();
    return;
  }
  const current = cellRack.value;
  const racks = availableRacksForCell();
  cellRack.innerHTML = `<option value="">Sin rack</option>` + racks.map(r => `<option value="${r.id}">${r.code}${r.material_group_name ? ` - ${r.material_group_name}` : ""}</option>`).join("");
  if ([...cellRack.options].some(o => o.value === current)) cellRack.value = current;
}
function renderPodPositionRackOptions() {
  if (!podPositionRack) return;
  const current = podPositionRack.value;
  const racks = (catalog.racks || []).filter(r => (r.code || '').trim());
  podPositionRack.innerHTML = `<option value="">Selecciona rack</option>` + racks.map(r => {
    const loc = r.location_x != null ? ` · local (${r.location_x},${r.location_y})` : ' · sin celda local';
    return `<option value="${r.id}">${escapeHtml(r.code || '')}${r.name ? ` - ${escapeHtml(r.name)}` : ''}${loc}</option>`;
  }).join("");
  if ([...podPositionRack.options].some(o => o.value === current)) podPositionRack.value = current;
}
function renderAreaList(renderOptions = {}) {
  if (!renderOptions.force && isEditingLockEffective("areas")) {
    deferAdminRefresh();
    return;
  }
  areasList.innerHTML = catalog.areas.map(a => `<button type="button" class="list-item" data-kind="area" data-id="${a.id}"><span class="swatch" style="background:${a.color}"></span><b>${a.code}</b> ${a.name}<small>${a.area_type} · prioridad ${a.priority} · Matter Area: ${escapeHtml(a.matter_area || '-')}</small></button>`).join("") || `<div class="small">Sin áreas capturadas.</div>`;
  areasList.querySelectorAll("[data-kind='area']").forEach(btn => btn.addEventListener("click", () => loadAreaForm(Number(btn.dataset.id))));
}
function renderMaterialList() {
  materialsList.innerHTML = catalog.materials.map(m => `<button type="button" class="list-item" data-kind="material" data-id="${m.id}"><span class="swatch" style="background:${escapeHtml(m.color || "#7c3aed")}"></span><b>${m.code}</b> ${m.name}<small>${m.is_active ? "activo" : "inactivo"}</small></button>`).join("") || `<div class="small">Sin materiales capturados.</div>`;
  materialsList.querySelectorAll("[data-kind='material']").forEach(btn => btn.addEventListener("click", () => loadMaterialForm(Number(btn.dataset.id))));
}
function renderRackList() {
  racksList.innerHTML = catalog.racks.map(r => {
    const reservation = rackReservationText(r);
    const task = rackReservationTaskText(r);
    const taskPart = task ? ` · tarea ${escapeHtml(task)}` : "";
    return `<button type="button" class="list-item" data-kind="rack" data-id="${r.id}"><b>${r.code}</b> ${r.name || ""}<small>${r.material_group_name || "sin material"}${r.location_x != null ? ` · (${r.location_x},${r.location_y})` : " · sin celda"} · ${reservation}${taskPart}</small></button>`;
  }).join("") || `<div class="small">Sin racks capturados.</div>`;
  racksList.querySelectorAll("[data-kind='rack']").forEach(btn => btn.addEventListener("click", () => loadRackForm(Number(btn.dataset.id))));
}
function buildAreaOptions(selectedValue = "") {
  return `<option value="">Sin area</option>` + (catalog.areas || []).map(a => `<option value="${a.id}" ${String(selectedValue) === String(a.id) ? "selected" : ""}>${escapeHtml(a.code || "")} - ${escapeHtml(a.name || "")}</option>`).join("");
}
function scannerCancelReturnAreaLabel(area) {
  if (!area) return "";
  const parts = [
    String(area.code || "").trim() || `ID ${area.id}`,
    String(area.name || "").trim() || "Sin nombre",
  ];
  if (Number(area.is_active ?? 1) !== 1) parts.push("Inactiva");
  const matterArea = String(area.matter_area || "").trim();
  parts.push(matterArea ? `Matter Area: ${matterArea}` : "Sin Matter Area");
  return parts.join(" — ");
}
function buildScannerCancelReturnAreaOptions(selectedValue = "", scanner = null) {
  const selectedText = String(selectedValue || "");
  const rows = catalog.areas || [];
  const hasSelected = selectedText && rows.some(a => String(a.id) === selectedText);
  const staleOption = selectedText && !hasSelected
    ? `<option value="${escapeHtml(selectedText)}" selected>${escapeHtml(`Area configurada no disponible — ID ${selectedText}`)}</option>`
    : "";
  return `<option value="">Sin configurar</option>${staleOption}` + rows.map(a => `<option value="${a.id}" ${selectedText === String(a.id) ? "selected" : ""}>${escapeHtml(scannerCancelReturnAreaLabel(a))}</option>`).join("");
}
function scannerCancelReturnAreaFromScanner(scanner) {
  if (!scanner || !scanner.cancel_return_area_id) return null;
  const fromCatalog = (catalog.areas || []).find(a => Number(a.id) === Number(scanner.cancel_return_area_id));
  if (fromCatalog) return fromCatalog;
  if (scanner.cancel_return_area_code || scanner.cancel_return_area_name || scanner.cancel_return_area_matter_area || scanner.cancel_return_area_is_active != null) {
    return {
      id: scanner.cancel_return_area_id,
      code: scanner.cancel_return_area_code || `ID ${scanner.cancel_return_area_id}`,
      name: scanner.cancel_return_area_name || "",
      matter_area: scanner.cancel_return_area_matter_area || "",
      is_active: scanner.cancel_return_area_is_active ?? 1,
      unavailable: true,
    };
  }
  return null;
}
function scannerCancelReturnAreaListSummary(scanner) {
  const areaId = scanner?.cancel_return_area_id;
  if (!areaId) return `<small><b>Area de devolucion:</b> Sin configurar</small>`;
  const area = scannerCancelReturnAreaFromScanner(scanner);
  if (!area) return `<small><b>Area de devolucion:</b> Area configurada no disponible - ID ${escapeHtml(areaId)}</small>`;
  const title = `${area.code || `ID ${areaId}`}${area.name ? ` - ${area.name}` : ""}`;
  const matter = String(area.matter_area || "").trim();
  const inactive = Number(area.is_active ?? 1) !== 1 ? `<small>Inactiva</small>` : "";
  return `
      <small><b>Area de devolucion:</b> ${escapeHtml(title)}</small>
      ${inactive}
      <small>${matter ? `Matter Area: ${escapeHtml(matter)}` : "Sin Matter Area"}</small>`;
}
function renderScannerCancelReturnAreaWarning(scanner = null) {
  if (!scannerCancelReturnAreaWarning) return;
  const areaId = scannerCancelReturnArea?.value ? Number(scannerCancelReturnArea.value) : null;
  if (!areaId) {
    scannerCancelReturnAreaWarning.classList.add("hidden");
    scannerCancelReturnAreaWarning.classList.remove("warn");
    scannerCancelReturnAreaWarning.innerHTML = "";
    return;
  }
  const area = (catalog.areas || []).find(a => Number(a.id) === Number(areaId)) || scannerCancelReturnAreaFromScanner(scanner);
  const messages = [];
  if (!area) {
    messages.push(`Area configurada no disponible — ID ${areaId}.`);
  } else {
    if (!String(area.matter_area || "").trim()) messages.push("Esta area no tiene Matter Area. Cancelar/Devolver desde el PDA no podra utilizarla hasta completar esa configuracion.");
    if (Number(area.is_active ?? 1) !== 1) messages.push("El area seleccionada esta inactiva.");
  }
  scannerCancelReturnAreaWarning.classList.toggle("hidden", !messages.length);
  scannerCancelReturnAreaWarning.classList.toggle("warn", !!messages.length);
  scannerCancelReturnAreaWarning.innerHTML = messages.map(msg => `<div>${escapeHtml(msg)}</div>`).join("");
}
function buildMaterialOptions(selectedValue = "") {
  return `<option value="">Sin material</option>` + (catalog.materials || []).map(m => `<option value="${m.id}" ${String(selectedValue) === String(m.id) ? "selected" : ""}>${escapeHtml(m.code || "")} - ${escapeHtml(m.name || "")}</option>`).join("");
}
function buildRackSelectOptions(selectedValue = "") {
  return `<option value="">Sin rack</option>` + (catalog.racks || []).map(r => `<option value="${r.id}" ${String(selectedValue) === String(r.id) ? "selected" : ""}>${escapeHtml(r.code || "")}${r.name ? ` - ${escapeHtml(r.name)}` : ""}</option>`).join("");
}
function cellOptionLabel(loc) {
  if (!loc) return "";
  const code = String(loc.code || "").trim();
  const area = (catalog.areas || []).find(a => Number(a.id) === Number(loc.area_id));
  return `${code || `(${loc.x},${loc.y})`}${area ? ` - ${area.name || area.code}` : ""}`;
}
function isCoordinateOnlyText(value) {
  const text = String(value || "").trim();
  return /^\(?\s*\d+\s*,\s*\d+\s*\)?$/.test(text);
}
function hasOperationalLocationData(loc) {
  if (!loc) return false;
  const code = String(loc.code || "").trim();
  const note = String(loc.note || "").trim();
  const area = (catalog.areas || []).find(a => Number(a.id) === Number(loc.area_id));
  const areaCode = String(area?.code || loc.area_code || "").trim();
  const areaName = String(area?.name || loc.area_name || "").trim();
  return (
    (!!code && !isCoordinateOnlyText(code)) ||
    (!!note && !isCoordinateOnlyText(note)) ||
    (!!areaCode && !isCoordinateOnlyText(areaCode)) ||
    (!!areaName && !isCoordinateOnlyText(areaName))
  );
}
function isOperationalConfiguredLocation(loc) {
  return !!loc && Number(loc.enabled ?? 1) === 1 && Number(loc.is_visible ?? 1) === 1 && hasOperationalLocationData(loc);
}
function buildCellOptions(selectedValue = "") {
  const selectedText = String(selectedValue || "");
  const rows = (locations || []).filter(isOperationalConfiguredLocation);
  const hasSelected = selectedText && rows.some(loc => String(loc.id) === selectedText);
  const selectedLoc = selectedText ? (locations || []).find(loc => loc && String(loc.id) === selectedText) : null;
  const staleOption = selectedText && selectedLoc && !hasSelected
    ? `<option value="${selectedLoc.id}" selected>${escapeHtml(cellOptionLabel(selectedLoc))} - no configurada / no operativa</option>`
    : "";
  return `<option value="">Sin celda</option>${staleOption}` + rows.map(loc => `<option value="${loc.id}" ${selectedText === String(loc.id) ? "selected" : ""}>${escapeHtml(cellOptionLabel(loc))}</option>`).join("");
}
function renderQrCellSummary(selectEl, targetEl) {
  if (!targetEl) return;
  const locId = selectEl?.value ? Number(selectEl.value) : null;
  if (!locId) {
    targetEl.classList.remove("warn");
    targetEl.innerHTML = "No hay celda seleccionada.";
    return;
  }
  const loc = (locations || []).find(row => row && Number(row.id) === Number(locId));
  if (!loc) {
    targetEl.classList.add("warn");
    targetEl.innerHTML = "Esta celda no est&aacute; configurada u operativa. Selecciona otra celda.";
    return;
  }
  const area = (catalog.areas || []).find(a => Number(a.id) === Number(loc.area_id));
  const code = String(loc.code || "").trim() || `(${loc.x},${loc.y})`;
  const note = String(loc.note || "").trim();
  const areaText = area ? `${area.code || ""}${area.name ? ` - ${area.name}` : ""}`.trim() : (loc.area_name || "");
  const isOperational = isOperationalConfiguredLocation(loc);
  targetEl.classList.toggle("warn", !isOperational);
  targetEl.innerHTML = `
    ${!isOperational ? `<div><b>Esta celda no est&aacute; configurada u operativa. Selecciona otra celda.</b></div>` : ""}
    <div><b>Celda seleccionada:</b></div>
    <div>${escapeHtml(code)}${note ? ` - ${escapeHtml(note)}` : ""}</div>
    <div><b>&Aacute;rea:</b> ${escapeHtml(areaText || "Sin area")}</div>
    <div><b>Coordenadas:</b> (${escapeHtml(loc.x)},${escapeHtml(loc.y)})</div>
  `;
}
function renderQrCellSummaries() {
  renderQrCellSummary(scannerSourceCell, scannerSourceCellSummary);
  renderQrCellSummary(scannerDestinationCell, scannerDestinationCellSummary);
  renderQrCellSummary(scannerSecondSourceCell, scannerSecondSourceCellSummary);
  renderQrCellSummary(scannerSecondDestinationCell, scannerSecondDestinationCellSummary);
  renderQrCellSummary(scannerFifoChainStep3SourceCell, scannerFifoChainStep3SourceCellSummary);
  renderQrCellSummary(scannerFifoChainStep3DestinationCell, scannerFifoChainStep3DestinationCellSummary);
  renderQrCellSummary(scannerFifoChainStep4SourceCell, scannerFifoChainStep4SourceCellSummary);
  renderQrCellSummary(scannerFifoChainStep4DestinationCell, scannerFifoChainStep4DestinationCellSummary);
  renderQrCellSummary(qrSourceCell, qrSourceCellSummary);
  renderQrCellSummary(qrDestinationCell, qrDestinationCellSummary);
  renderQrCellSummary(qrSecondSourceCell, qrSecondSourceCellSummary);
  renderQrCellSummary(qrSecondDestinationCell, qrSecondDestinationCellSummary);
  renderQrCellSummary(qrFifoChainStep3SourceCell, qrFifoChainStep3SourceCellSummary);
  renderQrCellSummary(qrFifoChainStep3DestinationCell, qrFifoChainStep3DestinationCellSummary);
  renderQrCellSummary(qrFifoChainStep4SourceCell, qrFifoChainStep4SourceCellSummary);
  renderQrCellSummary(qrFifoChainStep4DestinationCell, qrFifoChainStep4DestinationCellSummary);
}
function normalizeRouteMode(value) {
  if (value === "fifo_chain" || value === "trmx_doble") return "fifo_chain";
  return value === "double_area" ? "double_area" : "simple_area";
}
function routeModeLabel(value) {
  const mode = normalizeRouteMode(value);
  if (mode === "fifo_chain") return "Flujo doble FIFO / tareas encadenadas";
  return mode === "double_area" ? "Doble area / multipunto" : "Simple por origen/destino";
}
function fifoChainFlowLabel(item) {
  if (normalizeRouteMode(item?.route_mode) !== "fifo_chain") return routeModeLabel(item?.route_mode);
  const total = normalizeFifoChainTotalSteps(item?.fifo_chain_total_steps);
  return total === 4 ? "Flujo FIFO de 4 tramos" : (total === 3 ? "Flujo triple FIFO" : "Flujo doble FIFO");
}
function normalizeFifoMaterialPolicy(value) {
  return value === "any_available_from_source" ? "any_available_from_source" : "specific_material";
}
function fifoMaterialPolicyLabel(value) {
  return normalizeFifoMaterialPolicy(value) === "any_available_from_source" ? "Cualquier rack disponible" : "Material especifico";
}
function normalizeFifoChainSourceMode(value) {
  return value === "any_area_by_material" ? "any_area_by_material" : "configured_area";
}
function normalizeFifoChainStep2SourceMode(value) {
  return normalizeFifoChainSourceMode(value);
}
function normalizeFifoChainTotalSteps(value) {
  const total = Number(value || 2);
  return [2, 3, 4].includes(total) ? total : 2;
}
function fifoChainSourceModeLabel(value) {
  return normalizeFifoChainSourceMode(value) === "any_area_by_material" ? "Cualquier area por material" : "Area origen configurada";
}
function fifoChainStep2SourceModeLabel(value) {
  return fifoChainSourceModeLabel(value);
}
function qrRuleMaterialDisplay(q) {
  return normalizeFifoMaterialPolicy(q?.fifo_material_policy) === "any_available_from_source" ? "Cualquier rack del origen" : qrRuleMaterialLabel(q);
}
function fifoChainStep1MaterialDisplay(item) {
  if (item?.fifo_chain_step1_material_group_name) return item.fifo_chain_step1_material_group_name;
  if (item?.fifo_chain_step1_material_group_id) return `Material ID ${item.fifo_chain_step1_material_group_id}`;
  return "-";
}
function fifoChainStep2MaterialDisplay(item) {
  if (item?.fifo_chain_step2_material_group_name) return item.fifo_chain_step2_material_group_name;
  if (item?.fifo_chain_step2_material_group_id) return `Material ID ${item.fifo_chain_step2_material_group_id}`;
  return "-";
}
function fifoChainStep3MaterialDisplay(item) {
  if (item?.fifo_chain_step3_material_group_name) return item.fifo_chain_step3_material_group_name;
  if (item?.fifo_chain_step3_material_group_id) return `Material ID ${item.fifo_chain_step3_material_group_id}`;
  return "-";
}
function fifoChainStep4MaterialDisplay(item) {
  if (item?.fifo_chain_step4_material_group_name) return item.fifo_chain_step4_material_group_name;
  if (item?.fifo_chain_step4_material_group_id) return `Material ID ${item.fifo_chain_step4_material_group_id}`;
  return "-";
}
function syncFifoMaterialPolicyHelp() {
  const anyQr = normalizeFifoMaterialPolicy(qrFifoMaterialPolicy?.value) === "any_available_from_source" && (qrActionType?.value || "") === "fifo_request";
  if (qrFifoMaterialPolicyHelp) qrFifoMaterialPolicyHelp.classList.toggle("hidden", !anyQr);
}
function setFifoChainEndpointDisabled(ids, disabled) {
  ids.forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !!disabled || !adminToken;
  });
}
function syncFifoChainUi(owner, routeMode, totalSteps, step1SourceMode, step2SourceMode, step3SourceMode, step4SourceMode) {
  const isFifoChain = normalizeRouteMode(routeMode) === "fifo_chain";
  const normalizedTotal = normalizeFifoChainTotalSteps(totalSteps);
  const hasStep3 = isFifoChain && normalizedTotal >= 3;
  const hasStep4 = isFifoChain && normalizedTotal >= 4;
  const isGlobalStep1 = isFifoChain && normalizeFifoChainSourceMode(step1SourceMode) === "any_area_by_material";
  const isGlobalStep2 = isFifoChain && normalizeFifoChainSourceMode(step2SourceMode) === "any_area_by_material";
  const isGlobalStep3 = hasStep3 && normalizeFifoChainSourceMode(step3SourceMode) === "any_area_by_material";
  const isGlobalStep4 = hasStep4 && normalizeFifoChainSourceMode(step4SourceMode) === "any_area_by_material";
  document.querySelectorAll(`[data-fifo-chain-source1="${owner}"]`).forEach(el => el.classList.toggle("hidden", isGlobalStep1));
  document.querySelectorAll(`[data-fifo-chain-source2="${owner}"]`).forEach(el => el.classList.toggle("hidden", isGlobalStep2));
  document.querySelectorAll(`[data-fifo-chain-source3="${owner}"]`).forEach(el => el.classList.toggle("hidden", isGlobalStep3));
  document.querySelectorAll(`[data-fifo-chain-source4="${owner}"]`).forEach(el => el.classList.toggle("hidden", isGlobalStep4));
  document.querySelectorAll(`[data-fifo-chain-step3="${owner}"]`).forEach(el => el.classList.toggle("hidden", !hasStep3));
  document.querySelectorAll(`[data-fifo-chain-step4="${owner}"]`).forEach(el => el.classList.toggle("hidden", !hasStep4));
  document.querySelectorAll(`[data-fifo-chain-global-note="${owner}_step1"]`).forEach(el => el.classList.toggle("hidden", !isGlobalStep1));
  document.querySelectorAll(`[data-fifo-chain-global-note="${owner}_step2"]`).forEach(el => el.classList.toggle("hidden", !isGlobalStep2));
  document.querySelectorAll(`[data-fifo-chain-global-note="${owner}_step3"]`).forEach(el => el.classList.toggle("hidden", !isGlobalStep3));
  document.querySelectorAll(`[data-fifo-chain-global-note="${owner}_step4"]`).forEach(el => el.classList.toggle("hidden", !isGlobalStep4));
  if (owner === "scanner") {
    setFifoChainEndpointDisabled(["scannerSourceArea", "scannerSourceCell"], isGlobalStep1);
    setFifoChainEndpointDisabled(["scannerSecondSourceArea", "scannerSecondSourceCell"], isGlobalStep2);
    setFifoChainEndpointDisabled(["scannerFifoChainStep3SourceArea", "scannerFifoChainStep3SourceCell"], isGlobalStep3 || !hasStep3);
    setFifoChainEndpointDisabled(["scannerFifoChainStep3DestinationArea", "scannerFifoChainStep3DestinationCell"], !hasStep3);
    setFifoChainEndpointDisabled(["scannerFifoChainStep4SourceArea", "scannerFifoChainStep4SourceCell"], isGlobalStep4 || !hasStep4);
    setFifoChainEndpointDisabled(["scannerFifoChainStep4DestinationArea", "scannerFifoChainStep4DestinationCell"], !hasStep4);
  } else if (owner === "qr") {
    setFifoChainEndpointDisabled(["qrSourceArea", "qrSourceCell"], isGlobalStep1);
    setFifoChainEndpointDisabled(["qrSecondSourceArea", "qrSecondSourceCell"], isGlobalStep2);
    setFifoChainEndpointDisabled(["qrFifoChainStep3SourceArea", "qrFifoChainStep3SourceCell"], isGlobalStep3 || !hasStep3);
    setFifoChainEndpointDisabled(["qrFifoChainStep3DestinationArea", "qrFifoChainStep3DestinationCell"], !hasStep3);
    setFifoChainEndpointDisabled(["qrFifoChainStep4SourceArea", "qrFifoChainStep4SourceCell"], isGlobalStep4 || !hasStep4);
    setFifoChainEndpointDisabled(["qrFifoChainStep4DestinationArea", "qrFifoChainStep4DestinationCell"], !hasStep4);
  }
}
function syncRouteModeSections() {
  const scannerMode = normalizeRouteMode(scannerRouteMode?.value);
  const qrMode = normalizeRouteMode(qrRouteMode?.value);
  document.querySelectorAll('[data-route-owner="scanner"]').forEach(el => el.classList.toggle("hidden", !["double_area", "fifo_chain"].includes(scannerMode)));
  document.querySelectorAll('[data-route-owner="qr"]').forEach(el => el.classList.toggle("hidden", !["double_area", "fifo_chain"].includes(qrMode)));
  document.querySelectorAll('[data-fifo-chain-owner="scanner"]').forEach(el => el.classList.toggle("hidden", scannerMode !== "fifo_chain"));
  document.querySelectorAll('[data-fifo-chain-owner="qr"]').forEach(el => el.classList.toggle("hidden", qrMode !== "fifo_chain"));
  document.querySelectorAll('[data-fifo-chain-general="scanner"]').forEach(el => el.classList.toggle("hidden", scannerMode === "fifo_chain"));
  document.querySelectorAll('[data-fifo-chain-general="qr"]').forEach(el => el.classList.toggle("hidden", qrMode === "fifo_chain"));
  if (scannerDefaultMaterial) scannerDefaultMaterial.disabled = scannerMode === "fifo_chain" || !adminToken;
  if (scannerFifoMaterialPolicy) scannerFifoMaterialPolicy.disabled = scannerMode === "fifo_chain" || !adminToken;
  if (qrMaterial) qrMaterial.disabled = qrMode === "fifo_chain" || !adminToken;
  if (qrRack) qrRack.disabled = qrMode === "fifo_chain" || !adminToken;
  if (qrFifoMaterialPolicy) qrFifoMaterialPolicy.disabled = qrMode === "fifo_chain" || !adminToken;
  syncFifoChainUi("scanner", scannerMode, scannerFifoChainTotalSteps?.value, scannerFifoChainStep1SourceMode?.value, scannerFifoChainStep2SourceMode?.value, scannerFifoChainStep3SourceMode?.value, scannerFifoChainStep4SourceMode?.value);
  syncFifoChainUi("qr", qrMode, qrFifoChainTotalSteps?.value, qrFifoChainStep1SourceMode?.value, qrFifoChainStep2SourceMode?.value, qrFifoChainStep3SourceMode?.value, qrFifoChainStep4SourceMode?.value);
}
function validateRouteConfig(prefix, payload) {
  const mode = normalizeRouteMode(payload.route_mode);
  payload.route_mode = mode;
  if (!["double_area", "fifo_chain"].includes(mode)) return;
  const hasSource1 = !!payload.source_area_id || !!payload.source_cell_id;
  const hasDestination1 = !!payload.destination_area_id || !!payload.destination_cell_id;
  const hasSource2 = !!payload.second_source_area_id || !!payload.second_source_cell_id;
  const hasDestination2 = !!payload.second_destination_area_id || !!payload.second_destination_cell_id;
  if (mode === "fifo_chain") {
    const totalSteps = normalizeFifoChainTotalSteps(payload.fifo_chain_total_steps);
    const step1SourceMode = normalizeFifoChainSourceMode(payload.fifo_chain_step1_source_mode);
    const step2SourceMode = normalizeFifoChainSourceMode(payload.fifo_chain_step2_source_mode);
    const step3SourceMode = normalizeFifoChainSourceMode(payload.fifo_chain_step3_source_mode);
    const step4SourceMode = normalizeFifoChainSourceMode(payload.fifo_chain_step4_source_mode);
    const hasSource3 = !!payload.fifo_chain_step3_source_area_id || !!payload.fifo_chain_step3_source_cell_id;
    const hasDestination3 = !!payload.fifo_chain_step3_destination_area_id || !!payload.fifo_chain_step3_destination_cell_id;
    const hasSource4 = !!payload.fifo_chain_step4_source_area_id || !!payload.fifo_chain_step4_source_cell_id;
    const hasDestination4 = !!payload.fifo_chain_step4_destination_area_id || !!payload.fifo_chain_step4_destination_cell_id;
    payload.fifo_chain_total_steps = totalSteps;
    payload.fifo_chain_step1_source_mode = step1SourceMode;
    payload.fifo_chain_step2_source_mode = step2SourceMode;
    payload.fifo_chain_step3_source_mode = step3SourceMode;
    payload.fifo_chain_step4_source_mode = step4SourceMode;
    payload.material_group_id = null;
    payload.rack_id = null;
    payload.default_material_group_id = null;
    payload.fifo_material_policy = "specific_material";
    if (!payload.fifo_chain_step1_material_group_id) {
      throw new Error("El tramo 1 requiere Material requerido tramo 1.");
    }
    if (!hasDestination1) {
      throw new Error(step1SourceMode === "any_area_by_material" ? "El tramo 1 por material requiere destino 1." : `${prefix}: Flujo doble FIFO requiere destino para tramo 1.`);
    }
    if (step1SourceMode === "any_area_by_material") {
      if (!payload.fifo_chain_step1_material_group_id) {
        throw new Error("El tramo 1 por material requiere Material requerido tramo 1.");
      }
      payload.source_area_id = null;
      payload.source_cell_id = null;
    } else if (!hasSource1) {
      throw new Error(`${prefix}: Flujo doble FIFO requiere origen para tramo 1.`);
    }
    if (!hasDestination2) {
      throw new Error(step2SourceMode === "any_area_by_material" ? "El tramo 2 por material requiere destino 2." : `${prefix}: Flujo doble FIFO requiere destino para tramo 2.`);
    }
    if (step2SourceMode === "any_area_by_material") {
      if (!payload.fifo_chain_step2_material_group_id) {
        throw new Error("El tramo 2 por material requiere Material requerido tramo 2.");
      }
      payload.second_source_area_id = null;
      payload.second_source_cell_id = null;
    } else {
      if (!hasSource2) {
        throw new Error(`${prefix}: Flujo doble FIFO requiere origen para tramo 2.`);
      }
    }
    if (totalSteps === 2) {
      payload.fifo_chain_step3_source_mode = "configured_area";
      payload.fifo_chain_step3_material_group_id = null;
      payload.fifo_chain_step3_source_area_id = null;
      payload.fifo_chain_step3_source_cell_id = null;
      payload.fifo_chain_step3_destination_area_id = null;
      payload.fifo_chain_step3_destination_cell_id = null;
      payload.fifo_chain_step4_source_mode = "configured_area";
      payload.fifo_chain_step4_material_group_id = null;
      payload.fifo_chain_step4_source_area_id = null;
      payload.fifo_chain_step4_source_cell_id = null;
      payload.fifo_chain_step4_destination_area_id = null;
      payload.fifo_chain_step4_destination_cell_id = null;
      return;
    }
    if (step3SourceMode === "any_area_by_material") {
      if (!payload.fifo_chain_step3_material_group_id) {
        throw new Error("El tramo 3 por material requiere Material requerido tramo 3.");
      }
      if (!hasDestination3) {
        throw new Error("El tramo 3 por material requiere destino 3.");
      }
      payload.fifo_chain_step3_source_area_id = null;
      payload.fifo_chain_step3_source_cell_id = null;
    } else {
      if (!hasSource3 || !hasDestination3) {
        throw new Error("El tramo 3 requiere origen y destino.");
      }
    }
    if (totalSteps < 4) {
      payload.fifo_chain_step4_source_mode = "configured_area";
      payload.fifo_chain_step4_material_group_id = null;
      payload.fifo_chain_step4_source_area_id = null;
      payload.fifo_chain_step4_source_cell_id = null;
      payload.fifo_chain_step4_destination_area_id = null;
      payload.fifo_chain_step4_destination_cell_id = null;
      return;
    }
    if (step4SourceMode === "any_area_by_material") {
      if (!payload.fifo_chain_step4_material_group_id) {
        throw new Error("El tramo 4 por material requiere Material requerido tramo 4.");
      }
      if (!hasDestination4) {
        throw new Error("El tramo 4 por material requiere destino 4.");
      }
      payload.fifo_chain_step4_source_area_id = null;
      payload.fifo_chain_step4_source_cell_id = null;
      return;
    }
    if (!hasSource4 || !hasDestination4) {
      throw new Error("Tramo 4 requiere origen y destino.");
    }
    return;
  }
  if (!hasSource1 || !hasDestination1) {
    throw new Error(`${prefix}: Ruta doble requiere origen y destino para tramo 1.`);
  }
  if (!hasSource2 || !hasDestination2) {
    throw new Error(`${prefix}: Ruta doble requiere origen y destino para tramo 1 y tramo 2.`);
  }
}
function renderQrCatalogOptions() {
  [scannerSourceArea, scannerDestinationArea, scannerSecondSourceArea, scannerSecondDestinationArea, scannerFifoChainStep3SourceArea, scannerFifoChainStep3DestinationArea, scannerFifoChainStep4SourceArea, scannerFifoChainStep4DestinationArea, scannerStorageArea, scannerEmptyRackArea, qrSourceArea, qrDestinationArea, qrSecondSourceArea, qrSecondDestinationArea, qrFifoChainStep3SourceArea, qrFifoChainStep3DestinationArea, qrFifoChainStep4SourceArea, qrFifoChainStep4DestinationArea, qrTransitionSourceArea, qrTransitionDestinationArea].forEach((el) => {
    if (!el) return;
    const cur = el.value;
    el.innerHTML = buildAreaOptions(cur);
  });
  if (scannerCancelReturnArea) {
    const cur = scannerCancelReturnArea.value;
    scannerCancelReturnArea.innerHTML = buildScannerCancelReturnAreaOptions(cur);
    renderScannerCancelReturnAreaWarning();
  }
  [scannerDefaultMaterial, scannerFifoChainStep1Material, scannerFifoChainStep2Material, scannerFifoChainStep3Material, scannerFifoChainStep4Material, qrMaterial, qrFifoChainStep1Material, qrFifoChainStep2Material, qrFifoChainStep3Material, qrFifoChainStep4Material, qrTransitionCurrentMaterial, qrTransitionNextMaterial].forEach((el) => {
    if (!el) return;
    const cur = el.value;
    el.innerHTML = buildMaterialOptions(cur);
  });
  if (qrRack) {
    const cur = qrRack.value;
    qrRack.innerHTML = buildRackSelectOptions(cur);
  }
  [scannerSourceCell, scannerDestinationCell, scannerSecondSourceCell, scannerSecondDestinationCell, scannerFifoChainStep3SourceCell, scannerFifoChainStep3DestinationCell, scannerFifoChainStep4SourceCell, scannerFifoChainStep4DestinationCell, qrSourceCell, qrDestinationCell, qrSecondSourceCell, qrSecondDestinationCell, qrFifoChainStep3SourceCell, qrFifoChainStep3DestinationCell, qrFifoChainStep4SourceCell, qrFifoChainStep4DestinationCell, qrTransitionSourceCell, qrTransitionDestinationCell].forEach((el) => {
    if (!el) return;
    const cur = el.value;
    el.innerHTML = buildCellOptions(cur);
  });
  renderQrCellSummaries();
  syncRouteModeSections();
}
function clearScannerStationForm() {
  if (!scannerStationId) return;
  scannerStationId.value = "";
  scannerCode.value = "";
  scannerName.value = "";
  scannerDescription.value = "";
  scannerStationType.value = "generic";
  scannerDefaultAction.value = "preview_only";
  if (scannerRouteMode) scannerRouteMode.value = "simple_area";
  scannerSourceArea.value = "";
  scannerDestinationArea.value = "";
  scannerSourceCell.value = "";
  scannerDestinationCell.value = "";
  if (scannerSecondSourceArea) scannerSecondSourceArea.value = "";
  if (scannerSecondDestinationArea) scannerSecondDestinationArea.value = "";
  if (scannerSecondSourceCell) scannerSecondSourceCell.value = "";
  if (scannerSecondDestinationCell) scannerSecondDestinationCell.value = "";
  scannerStorageArea.value = "";
  scannerEmptyRackArea.value = "";
  if (scannerCancelReturnArea) {
    scannerCancelReturnArea.innerHTML = buildScannerCancelReturnAreaOptions("");
    scannerCancelReturnArea.value = "";
  }
  renderScannerCancelReturnAreaWarning();
  scannerDefaultMaterial.value = "";
  if (scannerFifoMaterialPolicy) scannerFifoMaterialPolicy.value = "specific_material";
  if (scannerFifoChainTotalSteps) scannerFifoChainTotalSteps.value = "2";
  if (scannerFifoChainStep1SourceMode) scannerFifoChainStep1SourceMode.value = "configured_area";
  if (scannerFifoChainStep1Material) scannerFifoChainStep1Material.value = "";
  if (scannerFifoChainStep2SourceMode) scannerFifoChainStep2SourceMode.value = "configured_area";
  if (scannerFifoChainStep2Material) scannerFifoChainStep2Material.value = "";
  if (scannerFifoChainStep3SourceMode) scannerFifoChainStep3SourceMode.value = "configured_area";
  if (scannerFifoChainStep3Material) scannerFifoChainStep3Material.value = "";
  if (scannerFifoChainStep3SourceArea) scannerFifoChainStep3SourceArea.value = "";
  if (scannerFifoChainStep3DestinationArea) scannerFifoChainStep3DestinationArea.value = "";
  if (scannerFifoChainStep3SourceCell) scannerFifoChainStep3SourceCell.value = "";
  if (scannerFifoChainStep3DestinationCell) scannerFifoChainStep3DestinationCell.value = "";
  if (scannerFifoChainStep4SourceMode) scannerFifoChainStep4SourceMode.value = "configured_area";
  if (scannerFifoChainStep4Material) scannerFifoChainStep4Material.value = "";
  if (scannerFifoChainStep4SourceArea) scannerFifoChainStep4SourceArea.value = "";
  if (scannerFifoChainStep4DestinationArea) scannerFifoChainStep4DestinationArea.value = "";
  if (scannerFifoChainStep4SourceCell) scannerFifoChainStep4SourceCell.value = "";
  if (scannerFifoChainStep4DestinationCell) scannerFifoChainStep4DestinationCell.value = "";
  scannerAgvCode.value = "";
  scannerTaskTyp.value = "";
  scannerPriority.value = 0;
  scannerRequirePreview.value = "0";
  scannerAllowExecute.value = "1";
  scannerActive.value = "1";
  renderQrCellSummaries();
  syncRouteModeSections();
}
function loadScannerStationForm(id) {
  const item = scannerStations.find(x => Number(x.id) === Number(id));
  if (!item) return;
  scannerStationId.value = item.id;
  scannerCode.value = item.scanner_code || "";
  scannerName.value = item.name || "";
  scannerDescription.value = item.description || "";
  scannerStationType.value = item.station_type || "generic";
  scannerDefaultAction.value = item.default_action || "preview_only";
  if (scannerRouteMode) scannerRouteMode.value = normalizeRouteMode(item.route_mode);
  scannerSourceArea.value = item.source_area_id || "";
  scannerDestinationArea.value = item.destination_area_id || "";
  scannerSourceCell.innerHTML = buildCellOptions(item.source_cell_id || "");
  scannerDestinationCell.innerHTML = buildCellOptions(item.destination_cell_id || "");
  scannerSourceCell.value = item.source_cell_id || "";
  scannerDestinationCell.value = item.destination_cell_id || "";
  if (scannerSecondSourceArea) scannerSecondSourceArea.value = item.second_source_area_id || "";
  if (scannerSecondDestinationArea) scannerSecondDestinationArea.value = item.second_destination_area_id || "";
  if (scannerSecondSourceCell) {
    scannerSecondSourceCell.innerHTML = buildCellOptions(item.second_source_cell_id || "");
    scannerSecondSourceCell.value = item.second_source_cell_id || "";
  }
  if (scannerSecondDestinationCell) {
    scannerSecondDestinationCell.innerHTML = buildCellOptions(item.second_destination_cell_id || "");
    scannerSecondDestinationCell.value = item.second_destination_cell_id || "";
  }
  renderQrCellSummaries();
  syncRouteModeSections();
  scannerStorageArea.value = item.storage_area_id || "";
  scannerEmptyRackArea.value = item.empty_rack_area_id || "";
  if (scannerCancelReturnArea) {
    scannerCancelReturnArea.innerHTML = buildScannerCancelReturnAreaOptions(item.cancel_return_area_id || "", item);
    scannerCancelReturnArea.value = item.cancel_return_area_id || "";
  }
  renderScannerCancelReturnAreaWarning(item);
  scannerDefaultMaterial.value = item.default_material_group_id || "";
  if (scannerFifoMaterialPolicy) scannerFifoMaterialPolicy.value = normalizeFifoMaterialPolicy(item.fifo_material_policy);
  if (scannerFifoChainTotalSteps) scannerFifoChainTotalSteps.value = String(normalizeFifoChainTotalSteps(item.fifo_chain_total_steps));
  if (scannerFifoChainStep1SourceMode) scannerFifoChainStep1SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step1_source_mode);
  if (scannerFifoChainStep1Material) scannerFifoChainStep1Material.value = item.fifo_chain_step1_material_group_id || "";
  if (scannerFifoChainStep2SourceMode) scannerFifoChainStep2SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step2_source_mode);
  if (scannerFifoChainStep2Material) scannerFifoChainStep2Material.value = item.fifo_chain_step2_material_group_id || "";
  if (scannerFifoChainStep3SourceMode) scannerFifoChainStep3SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step3_source_mode);
  if (scannerFifoChainStep3Material) scannerFifoChainStep3Material.value = item.fifo_chain_step3_material_group_id || "";
  if (scannerFifoChainStep3SourceArea) scannerFifoChainStep3SourceArea.value = item.fifo_chain_step3_source_area_id || "";
  if (scannerFifoChainStep3DestinationArea) scannerFifoChainStep3DestinationArea.value = item.fifo_chain_step3_destination_area_id || "";
  if (scannerFifoChainStep3SourceCell) {
    scannerFifoChainStep3SourceCell.innerHTML = buildCellOptions(item.fifo_chain_step3_source_cell_id || "");
    scannerFifoChainStep3SourceCell.value = item.fifo_chain_step3_source_cell_id || "";
  }
  if (scannerFifoChainStep3DestinationCell) {
    scannerFifoChainStep3DestinationCell.innerHTML = buildCellOptions(item.fifo_chain_step3_destination_cell_id || "");
    scannerFifoChainStep3DestinationCell.value = item.fifo_chain_step3_destination_cell_id || "";
  }
  if (scannerFifoChainStep4SourceMode) scannerFifoChainStep4SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step4_source_mode);
  if (scannerFifoChainStep4Material) scannerFifoChainStep4Material.value = item.fifo_chain_step4_material_group_id || "";
  if (scannerFifoChainStep4SourceArea) scannerFifoChainStep4SourceArea.value = item.fifo_chain_step4_source_area_id || "";
  if (scannerFifoChainStep4DestinationArea) scannerFifoChainStep4DestinationArea.value = item.fifo_chain_step4_destination_area_id || "";
  if (scannerFifoChainStep4SourceCell) {
    scannerFifoChainStep4SourceCell.innerHTML = buildCellOptions(item.fifo_chain_step4_source_cell_id || "");
    scannerFifoChainStep4SourceCell.value = item.fifo_chain_step4_source_cell_id || "";
  }
  if (scannerFifoChainStep4DestinationCell) {
    scannerFifoChainStep4DestinationCell.innerHTML = buildCellOptions(item.fifo_chain_step4_destination_cell_id || "");
    scannerFifoChainStep4DestinationCell.value = item.fifo_chain_step4_destination_cell_id || "";
  }
  syncRouteModeSections();
  scannerAgvCode.value = item.agv_code || "";
  scannerTaskTyp.value = item.task_typ || "";
  scannerPriority.value = item.priority ?? 0;
  scannerRequirePreview.value = String(item.require_preview ?? 0);
  scannerAllowExecute.value = String(item.allow_execute ?? 1);
  scannerActive.value = String(item.is_active ?? 1);
}
function scannerStationPayload() {
  const payload = {
    scanner_code: scannerCode.value.trim(),
    name: scannerName.value.trim(),
    description: scannerDescription.value.trim() || null,
    station_type: scannerStationType.value || "generic",
    default_action: scannerDefaultAction.value || "preview_only",
    route_mode: normalizeRouteMode(scannerRouteMode?.value),
    source_area_id: scannerSourceArea.value ? Number(scannerSourceArea.value) : null,
    destination_area_id: scannerDestinationArea.value ? Number(scannerDestinationArea.value) : null,
    source_cell_id: scannerSourceCell.value ? Number(scannerSourceCell.value) : null,
    destination_cell_id: scannerDestinationCell.value ? Number(scannerDestinationCell.value) : null,
    fifo_chain_total_steps: normalizeFifoChainTotalSteps(scannerFifoChainTotalSteps?.value),
    fifo_chain_step1_source_mode: normalizeFifoChainSourceMode(scannerFifoChainStep1SourceMode?.value),
    fifo_chain_step1_material_group_id: scannerFifoChainStep1Material?.value ? Number(scannerFifoChainStep1Material.value) : null,
    fifo_chain_step2_source_mode: normalizeFifoChainSourceMode(scannerFifoChainStep2SourceMode?.value),
    fifo_chain_step2_material_group_id: scannerFifoChainStep2Material?.value ? Number(scannerFifoChainStep2Material.value) : null,
    fifo_chain_step3_source_mode: normalizeFifoChainSourceMode(scannerFifoChainStep3SourceMode?.value),
    fifo_chain_step3_material_group_id: scannerFifoChainStep3Material?.value ? Number(scannerFifoChainStep3Material.value) : null,
    fifo_chain_step3_source_area_id: scannerFifoChainStep3SourceArea?.value ? Number(scannerFifoChainStep3SourceArea.value) : null,
    fifo_chain_step3_source_cell_id: scannerFifoChainStep3SourceCell?.value ? Number(scannerFifoChainStep3SourceCell.value) : null,
    fifo_chain_step3_destination_area_id: scannerFifoChainStep3DestinationArea?.value ? Number(scannerFifoChainStep3DestinationArea.value) : null,
    fifo_chain_step3_destination_cell_id: scannerFifoChainStep3DestinationCell?.value ? Number(scannerFifoChainStep3DestinationCell.value) : null,
    fifo_chain_step4_source_mode: normalizeFifoChainSourceMode(scannerFifoChainStep4SourceMode?.value),
    fifo_chain_step4_material_group_id: scannerFifoChainStep4Material?.value ? Number(scannerFifoChainStep4Material.value) : null,
    fifo_chain_step4_source_area_id: scannerFifoChainStep4SourceArea?.value ? Number(scannerFifoChainStep4SourceArea.value) : null,
    fifo_chain_step4_source_cell_id: scannerFifoChainStep4SourceCell?.value ? Number(scannerFifoChainStep4SourceCell.value) : null,
    fifo_chain_step4_destination_area_id: scannerFifoChainStep4DestinationArea?.value ? Number(scannerFifoChainStep4DestinationArea.value) : null,
    fifo_chain_step4_destination_cell_id: scannerFifoChainStep4DestinationCell?.value ? Number(scannerFifoChainStep4DestinationCell.value) : null,
    second_source_area_id: scannerSecondSourceArea?.value ? Number(scannerSecondSourceArea.value) : null,
    second_destination_area_id: scannerSecondDestinationArea?.value ? Number(scannerSecondDestinationArea.value) : null,
    second_source_cell_id: scannerSecondSourceCell?.value ? Number(scannerSecondSourceCell.value) : null,
    second_destination_cell_id: scannerSecondDestinationCell?.value ? Number(scannerSecondDestinationCell.value) : null,
    storage_area_id: scannerStorageArea.value ? Number(scannerStorageArea.value) : null,
    empty_rack_area_id: scannerEmptyRackArea.value ? Number(scannerEmptyRackArea.value) : null,
    cancel_return_area_id: scannerCancelReturnArea?.value ? Number(scannerCancelReturnArea.value) : null,
    default_material_group_id: scannerDefaultMaterial.value ? Number(scannerDefaultMaterial.value) : null,
    fifo_material_policy: normalizeFifoMaterialPolicy(scannerFifoMaterialPolicy?.value),
    agv_code: scannerAgvCode.value.trim() || null,
    task_typ: scannerTaskTyp.value.trim() || null,
    priority: Number(scannerPriority.value || 0),
    require_preview: Number(scannerRequirePreview.value || 0),
    allow_execute: Number(scannerAllowExecute.value || 0),
    is_active: Number(scannerActive.value || 0),
  };
  validateRouteConfig("Scanner", payload);
  return payload;
}
function renderScannerStationsList() {
  if (!scannerStationsList) return;
  if (!scannerStations.length) {
    scannerStationsList.innerHTML = `<div class="small">Sin scanners configurados.</div>`;
    return;
  }
  const rows = scannerStations.map(s => `
    <tr class="clickable-row" data-scanner-id="${s.id}">
      <td><b>${escapeHtml(s.scanner_code || "")}</b><div class="small">${escapeHtml(s.name || "")}</div></td>
      <td>${escapeHtml(fifoChainFlowLabel(s))}</td>
      <td>${escapeHtml(routeEndpointLabel(s, "source"))}</td>
      <td>${escapeHtml(routeEndpointLabel(s, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep1MaterialDisplay(s))}</td>
      <td>${escapeHtml(secondaryRouteEndpointLabel(s, "second_source"))}</td>
      <td>${escapeHtml(secondaryRouteEndpointLabel(s, "second_destination"))}</td>
      <td>${escapeHtml(fifoChainStep2MaterialDisplay(s))}</td>
      <td>${escapeHtml(fifoChainStep3EndpointLabel(s, "source"))}</td>
      <td>${escapeHtml(fifoChainStep3EndpointLabel(s, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep3MaterialDisplayForList(s))}</td>
      <td>${escapeHtml(fifoChainStep4EndpointLabel(s, "source"))}</td>
      <td>${escapeHtml(fifoChainStep4EndpointLabel(s, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep4MaterialDisplayForList(s))}</td>
      <td>${escapeHtml(s.default_action || "-")}</td>
      <td>${escapeHtml(fifoMaterialPolicyLabel(s.fifo_material_policy))}</td>
      <td>${Number(s.is_active ?? 0) ? "Activo" : "Inactivo"}</td>
    </tr>`).join("");
  scannerStationsList.innerHTML = `<table class="diagnosis-table qr-route-table"><thead><tr><th>C&oacute;digo scanner</th><th>Modo de ruta</th><th>Origen 1</th><th>Destino 1</th><th>Material tramo 1</th><th>Origen 2</th><th>Destino 2</th><th>Material tramo 2</th><th>Origen 3</th><th>Destino 3</th><th>Material tramo 3</th><th>Origen 4</th><th>Destino 4</th><th>Material tramo 4</th><th>Default action</th><th>Modo selecci&oacute;n</th><th>Activo</th></tr></thead><tbody>${rows}</tbody></table>`;
  scannerStationsList.querySelectorAll("[data-scanner-id]").forEach(btn => btn.addEventListener("click", () => loadScannerStationForm(Number(btn.dataset.scannerId))));
}
function buildScannerStationOptions(selectedValue = "") {
  const selectedText = String(selectedValue || "");
  return `<option value="">Selecciona scanner</option>` + (scannerStations || []).map(s => {
    const inactive = Number(s.is_active ?? 0) === 1 ? "" : " - inactivo";
    const label = `${s.scanner_code || ""}${s.name ? ` - ${s.name}` : ""}${inactive}`;
    return `<option value="${s.id}" ${selectedText === String(s.id) ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}
function renderScanTerminalScannerOptions() {
  if (!scanTerminalScannerStation) return;
  const cur = scanTerminalScannerStation.value;
  scanTerminalScannerStation.innerHTML = buildScannerStationOptions(cur);
}
function renderQrTransitionScannerOptions() {
  if (!qrTransitionScanner) return;
  const cur = qrTransitionScanner.value;
  qrTransitionScanner.innerHTML = `<option value="">Cualquier scanner</option>` + (scannerStations || []).map(s => {
    const inactive = Number(s.is_active ?? 0) === 1 ? "" : " - inactivo";
    const label = `${s.scanner_code || ""}${s.name ? ` - ${s.name}` : ""}${inactive}`;
    return `<option value="${s.id}" ${String(cur) === String(s.id) ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}
async function loadScannerStations() {
  if (!adminToken) return;
  scannerStations = await fetchJson(API.adminScannerStations, { headers: fetchHeaders() });
  renderScannerStationsList();
  renderScanTerminalScannerOptions();
  renderQrTransitionScannerOptions();
  renderScanQrScannerOptions();
}
async function saveScannerStation() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = scannerStationId.value ? Number(scannerStationId.value) : null;
  const url = id ? API.adminScannerStation(id) : API.adminScannerStations;
  const method = id ? "PUT" : "POST";
  if (btnSaveScannerStation) btnSaveScannerStation.disabled = true;
  try {
    await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(scannerStationPayload()) });
    await loadScannerStations();
    if (qrAdminMsg) qrAdminMsg.textContent = "Scanner guardado.";
  } finally {
    if (btnSaveScannerStation) btnSaveScannerStation.disabled = !adminToken;
  }
}
async function disableScannerStation() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = scannerStationId.value ? Number(scannerStationId.value) : null;
  if (!id) throw new Error("Selecciona un scanner.");
  const row = await fetchJson(API.adminScannerStation(id), { method: "DELETE", headers: fetchHeaders() });
  await loadScannerStations();
  loadScannerStationForm(row.id);
  if (qrAdminMsg) qrAdminMsg.textContent = "Scanner desactivado.";
}
function clearQrRuleForm() {
  if (!qrRuleId) return;
  qrRuleId.value = "";
  qrValue.value = "";
  qrAlias.value = "";
  qrDescription.value = "";
  qrType.value = "material";
  qrMatchType.value = "exact";
  qrActionType.value = "fifo_request";
  qrMaterial.value = "";
  if (qrFifoMaterialPolicy) qrFifoMaterialPolicy.value = "specific_material";
  if (qrFifoChainTotalSteps) qrFifoChainTotalSteps.value = "2";
  if (qrFifoChainStep1SourceMode) qrFifoChainStep1SourceMode.value = "configured_area";
  if (qrFifoChainStep1Material) qrFifoChainStep1Material.value = "";
  if (qrFifoChainStep2SourceMode) qrFifoChainStep2SourceMode.value = "configured_area";
  if (qrFifoChainStep2Material) qrFifoChainStep2Material.value = "";
  if (qrFifoChainStep3SourceMode) qrFifoChainStep3SourceMode.value = "configured_area";
  if (qrFifoChainStep3Material) qrFifoChainStep3Material.value = "";
  if (qrFifoChainStep3SourceArea) qrFifoChainStep3SourceArea.value = "";
  if (qrFifoChainStep3DestinationArea) qrFifoChainStep3DestinationArea.value = "";
  if (qrFifoChainStep3SourceCell) qrFifoChainStep3SourceCell.value = "";
  if (qrFifoChainStep3DestinationCell) qrFifoChainStep3DestinationCell.value = "";
  if (qrFifoChainStep4SourceMode) qrFifoChainStep4SourceMode.value = "configured_area";
  if (qrFifoChainStep4Material) qrFifoChainStep4Material.value = "";
  if (qrFifoChainStep4SourceArea) qrFifoChainStep4SourceArea.value = "";
  if (qrFifoChainStep4DestinationArea) qrFifoChainStep4DestinationArea.value = "";
  if (qrFifoChainStep4SourceCell) qrFifoChainStep4SourceCell.value = "";
  if (qrFifoChainStep4DestinationCell) qrFifoChainStep4DestinationCell.value = "";
  qrRack.value = "";
  if (qrRouteMode) qrRouteMode.value = "simple_area";
  qrSourceArea.value = "";
  qrDestinationArea.value = "";
  qrSourceCell.value = "";
  qrDestinationCell.value = "";
  if (qrSecondSourceArea) qrSecondSourceArea.value = "";
  if (qrSecondDestinationArea) qrSecondDestinationArea.value = "";
  if (qrSecondSourceCell) qrSecondSourceCell.value = "";
  if (qrSecondDestinationCell) qrSecondDestinationCell.value = "";
  qrPriority.value = "";
  qrAgvCode.value = "";
  qrTaskTyp.value = "";
  qrRequiresScanner.value = "1";
  qrActive.value = "1";
  renderQrCellSummaries();
  syncRouteModeSections();
  syncFifoMaterialPolicyHelp();
}
function loadQrRuleForm(id) {
  const item = qrActionRules.find(x => Number(x.id) === Number(id));
  if (!item) return;
  qrRuleId.value = item.id;
  qrValue.value = item.qr_value || "";
  qrAlias.value = item.qr_alias || "";
  qrDescription.value = item.description || "";
  qrType.value = item.qr_type || "generic";
  qrMatchType.value = item.match_type || "exact";
  qrActionType.value = item.action_type || "use_scanner_default";
  qrMaterial.value = item.material_group_id || "";
  if (qrFifoMaterialPolicy) qrFifoMaterialPolicy.value = normalizeFifoMaterialPolicy(item.fifo_material_policy);
  if (qrFifoChainTotalSteps) qrFifoChainTotalSteps.value = String(normalizeFifoChainTotalSteps(item.fifo_chain_total_steps));
  if (qrFifoChainStep1SourceMode) qrFifoChainStep1SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step1_source_mode);
  if (qrFifoChainStep1Material) qrFifoChainStep1Material.value = item.fifo_chain_step1_material_group_id || "";
  if (qrFifoChainStep2SourceMode) qrFifoChainStep2SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step2_source_mode);
  if (qrFifoChainStep2Material) qrFifoChainStep2Material.value = item.fifo_chain_step2_material_group_id || "";
  if (qrFifoChainStep3SourceMode) qrFifoChainStep3SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step3_source_mode);
  if (qrFifoChainStep3Material) qrFifoChainStep3Material.value = item.fifo_chain_step3_material_group_id || "";
  qrRack.value = item.rack_id || "";
  if (qrRouteMode) qrRouteMode.value = normalizeRouteMode(item.route_mode);
  qrSourceArea.value = item.source_area_id || "";
  qrDestinationArea.value = item.destination_area_id || "";
  qrSourceCell.innerHTML = buildCellOptions(item.source_cell_id || "");
  qrDestinationCell.innerHTML = buildCellOptions(item.destination_cell_id || "");
  qrSourceCell.value = item.source_cell_id || "";
  qrDestinationCell.value = item.destination_cell_id || "";
  if (qrSecondSourceArea) qrSecondSourceArea.value = item.second_source_area_id || "";
  if (qrSecondDestinationArea) qrSecondDestinationArea.value = item.second_destination_area_id || "";
  if (qrSecondSourceCell) {
    qrSecondSourceCell.innerHTML = buildCellOptions(item.second_source_cell_id || "");
    qrSecondSourceCell.value = item.second_source_cell_id || "";
  }
  if (qrSecondDestinationCell) {
    qrSecondDestinationCell.innerHTML = buildCellOptions(item.second_destination_cell_id || "");
    qrSecondDestinationCell.value = item.second_destination_cell_id || "";
  }
  if (qrFifoChainStep3SourceArea) qrFifoChainStep3SourceArea.value = item.fifo_chain_step3_source_area_id || "";
  if (qrFifoChainStep3DestinationArea) qrFifoChainStep3DestinationArea.value = item.fifo_chain_step3_destination_area_id || "";
  if (qrFifoChainStep3SourceCell) {
    qrFifoChainStep3SourceCell.innerHTML = buildCellOptions(item.fifo_chain_step3_source_cell_id || "");
    qrFifoChainStep3SourceCell.value = item.fifo_chain_step3_source_cell_id || "";
  }
  if (qrFifoChainStep3DestinationCell) {
    qrFifoChainStep3DestinationCell.innerHTML = buildCellOptions(item.fifo_chain_step3_destination_cell_id || "");
    qrFifoChainStep3DestinationCell.value = item.fifo_chain_step3_destination_cell_id || "";
  }
  if (qrFifoChainStep4SourceMode) qrFifoChainStep4SourceMode.value = normalizeFifoChainSourceMode(item.fifo_chain_step4_source_mode);
  if (qrFifoChainStep4Material) qrFifoChainStep4Material.value = item.fifo_chain_step4_material_group_id || "";
  if (qrFifoChainStep4SourceArea) qrFifoChainStep4SourceArea.value = item.fifo_chain_step4_source_area_id || "";
  if (qrFifoChainStep4DestinationArea) qrFifoChainStep4DestinationArea.value = item.fifo_chain_step4_destination_area_id || "";
  if (qrFifoChainStep4SourceCell) {
    qrFifoChainStep4SourceCell.innerHTML = buildCellOptions(item.fifo_chain_step4_source_cell_id || "");
    qrFifoChainStep4SourceCell.value = item.fifo_chain_step4_source_cell_id || "";
  }
  if (qrFifoChainStep4DestinationCell) {
    qrFifoChainStep4DestinationCell.innerHTML = buildCellOptions(item.fifo_chain_step4_destination_cell_id || "");
    qrFifoChainStep4DestinationCell.value = item.fifo_chain_step4_destination_cell_id || "";
  }
  renderQrCellSummaries();
  syncRouteModeSections();
  qrPriority.value = item.priority ?? "";
  qrAgvCode.value = item.agv_code || "";
  qrTaskTyp.value = item.task_typ || "";
  qrRequiresScanner.value = String(item.requires_scanner_station ?? 1);
  qrActive.value = String(item.is_active ?? 1);
  syncFifoMaterialPolicyHelp();
}
function qrRulePayload() {
  const payload = {
    qr_value: qrValue.value.trim(),
    qr_alias: qrAlias.value.trim() || null,
    description: qrDescription.value.trim() || null,
    qr_type: qrType.value || "generic",
    match_type: qrMatchType.value || "exact",
    action_type: qrActionType.value || "use_scanner_default",
    material_group_id: qrMaterial.value ? Number(qrMaterial.value) : null,
    fifo_material_policy: normalizeFifoMaterialPolicy(qrFifoMaterialPolicy?.value),
    fifo_chain_total_steps: normalizeFifoChainTotalSteps(qrFifoChainTotalSteps?.value),
    fifo_chain_step1_source_mode: normalizeFifoChainSourceMode(qrFifoChainStep1SourceMode?.value),
    fifo_chain_step1_material_group_id: qrFifoChainStep1Material?.value ? Number(qrFifoChainStep1Material.value) : null,
    fifo_chain_step2_source_mode: normalizeFifoChainSourceMode(qrFifoChainStep2SourceMode?.value),
    fifo_chain_step2_material_group_id: qrFifoChainStep2Material?.value ? Number(qrFifoChainStep2Material.value) : null,
    fifo_chain_step3_source_mode: normalizeFifoChainSourceMode(qrFifoChainStep3SourceMode?.value),
    fifo_chain_step3_material_group_id: qrFifoChainStep3Material?.value ? Number(qrFifoChainStep3Material.value) : null,
    fifo_chain_step3_source_area_id: qrFifoChainStep3SourceArea?.value ? Number(qrFifoChainStep3SourceArea.value) : null,
    fifo_chain_step3_source_cell_id: qrFifoChainStep3SourceCell?.value ? Number(qrFifoChainStep3SourceCell.value) : null,
    fifo_chain_step3_destination_area_id: qrFifoChainStep3DestinationArea?.value ? Number(qrFifoChainStep3DestinationArea.value) : null,
    fifo_chain_step3_destination_cell_id: qrFifoChainStep3DestinationCell?.value ? Number(qrFifoChainStep3DestinationCell.value) : null,
    fifo_chain_step4_source_mode: normalizeFifoChainSourceMode(qrFifoChainStep4SourceMode?.value),
    fifo_chain_step4_material_group_id: qrFifoChainStep4Material?.value ? Number(qrFifoChainStep4Material.value) : null,
    fifo_chain_step4_source_area_id: qrFifoChainStep4SourceArea?.value ? Number(qrFifoChainStep4SourceArea.value) : null,
    fifo_chain_step4_source_cell_id: qrFifoChainStep4SourceCell?.value ? Number(qrFifoChainStep4SourceCell.value) : null,
    fifo_chain_step4_destination_area_id: qrFifoChainStep4DestinationArea?.value ? Number(qrFifoChainStep4DestinationArea.value) : null,
    fifo_chain_step4_destination_cell_id: qrFifoChainStep4DestinationCell?.value ? Number(qrFifoChainStep4DestinationCell.value) : null,
    rack_id: qrRack.value ? Number(qrRack.value) : null,
    route_mode: normalizeRouteMode(qrRouteMode?.value),
    source_area_id: qrSourceArea.value ? Number(qrSourceArea.value) : null,
    destination_area_id: qrDestinationArea.value ? Number(qrDestinationArea.value) : null,
    source_cell_id: qrSourceCell.value ? Number(qrSourceCell.value) : null,
    destination_cell_id: qrDestinationCell.value ? Number(qrDestinationCell.value) : null,
    second_source_area_id: qrSecondSourceArea?.value ? Number(qrSecondSourceArea.value) : null,
    second_destination_area_id: qrSecondDestinationArea?.value ? Number(qrSecondDestinationArea.value) : null,
    second_source_cell_id: qrSecondSourceCell?.value ? Number(qrSecondSourceCell.value) : null,
    second_destination_cell_id: qrSecondDestinationCell?.value ? Number(qrSecondDestinationCell.value) : null,
    priority: qrPriority.value === "" ? null : Number(qrPriority.value || 0),
    task_typ: qrTaskTyp.value.trim() || null,
    agv_code: qrAgvCode.value.trim() || null,
    requires_scanner_station: Number(qrRequiresScanner.value || 0),
    is_active: Number(qrActive.value || 0),
  };
  validateRouteConfig("QR", payload);
  return payload;
}
function revokeQrRuleThumbUrls() {
  qrRuleImageObjectUrls.forEach(url => URL.revokeObjectURL(url));
  qrRuleImageObjectUrls.clear();
}
function revokeQrRuleModalImageUrl() {
  if (qrRuleModalImageObjectUrl) {
    URL.revokeObjectURL(qrRuleModalImageObjectUrl);
    qrRuleModalImageObjectUrl = null;
  }
}
async function fetchQrRuleImageObjectUrl(ruleId, size) {
  const res = await fetchWithAdminSession(API.adminQrActionRuleImage(ruleId, size), {
    cache: "no-store",
    headers: fetchHeaders(),
  });
  if (!res.ok) {
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) {}
    throw new Error((data && data.detail) || text || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
function qrRuleAliasTitle(q) {
  return q?.qr_alias ? `${q.qr_alias}` : "QR configurado";
}
function qrRuleMaterialLabel(q) {
  if (q?.material_group_name) return q.material_group_name;
  if (q?.material_group_id) return `Material ID ${q.material_group_id}`;
  return "-";
}
function qrRuleCellLabel(q, kind) {
  const code = q?.[`${kind}_cell_code`];
  const id = q?.[`${kind}_cell_id`];
  return code || (id ? `Celda ID ${id}` : "-");
}
function qrRuleAreaLabel(q, kind) {
  const name = q?.[`${kind}_area_name`];
  const id = q?.[`${kind}_area_id`];
  return name || (id ? `Área ID ${id}` : "-");
}
function routeEndpointLabel(item, kind) {
  if (
    kind === "source"
    && normalizeRouteMode(item?.route_mode) === "fifo_chain"
    && normalizeFifoChainSourceMode(item?.fifo_chain_step1_source_mode) === "any_area_by_material"
  ) {
    return "Cualquier area por material";
  }
  const areaName = item?.[`${kind}_area_name`];
  const areaCode = item?.[`${kind}_area_code`];
  const areaId = item?.[`${kind}_area_id`];
  const cellCode = item?.[`${kind}_cell_code`];
  const cellId = item?.[`${kind}_cell_id`];
  const area = areaCode && areaName ? `${areaCode} - ${areaName}` : (areaName || areaCode || (areaId ? `Area ID ${areaId}` : ""));
  const cell = cellCode || (cellId ? `Celda ID ${cellId}` : "");
  if (area && cell) return `${area} / ${cell}`;
  return area || cell || "-";
}
function secondaryRouteEndpointLabel(item, kind) {
  const mode = normalizeRouteMode(item?.route_mode);
  if (!["double_area", "fifo_chain"].includes(mode)) return "-";
  if (mode === "fifo_chain" && kind === "second_source" && normalizeFifoChainSourceMode(item?.fifo_chain_step2_source_mode) === "any_area_by_material") {
    return "Cualquier area por material";
  }
  return routeEndpointLabel(item, kind);
}
function fifoChainStep3EndpointLabel(item, kind) {
  if (normalizeRouteMode(item?.route_mode) !== "fifo_chain" || normalizeFifoChainTotalSteps(item?.fifo_chain_total_steps) < 3) return "-";
  if (kind === "source" && normalizeFifoChainSourceMode(item?.fifo_chain_step3_source_mode) === "any_area_by_material") {
    return "Cualquier area por material";
  }
  const prefix = kind === "source" ? "fifo_chain_step3_source" : "fifo_chain_step3_destination";
  const areaName = item?.[`${prefix}_area_name`];
  const areaId = item?.[`${prefix}_area_id`];
  const cellCode = item?.[`${prefix}_cell_code`];
  const cellId = item?.[`${prefix}_cell_id`];
  const area = areaName || (areaId ? `Area ID ${areaId}` : "");
  const cell = cellCode || (cellId ? `Celda ID ${cellId}` : "");
  if (area && cell) return `${area} / ${cell}`;
  return area || cell || "-";
}
function fifoChainStep3MaterialDisplayForList(item) {
  if (normalizeRouteMode(item?.route_mode) !== "fifo_chain" || normalizeFifoChainTotalSteps(item?.fifo_chain_total_steps) < 3) return "-";
  return fifoChainStep3MaterialDisplay(item);
}
function fifoChainStep4EndpointLabel(item, kind) {
  if (normalizeRouteMode(item?.route_mode) !== "fifo_chain" || normalizeFifoChainTotalSteps(item?.fifo_chain_total_steps) < 4) return "-";
  if (kind === "source" && normalizeFifoChainSourceMode(item?.fifo_chain_step4_source_mode) === "any_area_by_material") {
    return `Cualquier area por material ${fifoChainStep4MaterialDisplay(item)}`;
  }
  const prefix = kind === "source" ? "fifo_chain_step4_source" : "fifo_chain_step4_destination";
  const areaName = item?.[`${prefix}_area_name`];
  const areaId = item?.[`${prefix}_area_id`];
  const cellCode = item?.[`${prefix}_cell_code`];
  const cellId = item?.[`${prefix}_cell_id`];
  const area = areaName || (areaId ? `Area ID ${areaId}` : "");
  const cell = cellCode || (cellId ? `Celda ID ${cellId}` : "");
  if (area && cell) return `${area} / ${cell}`;
  return area || cell || "-";
}
function fifoChainStep4MaterialDisplayForList(item) {
  if (normalizeRouteMode(item?.route_mode) !== "fifo_chain" || normalizeFifoChainTotalSteps(item?.fifo_chain_total_steps) < 4) return "-";
  return fifoChainStep4MaterialDisplay(item);
}
function renderQrRuleMetaHtml(q) {
  const rows = [
    ["Tipo QR", q?.qr_type || "-"],
    ["Coincidencia", q?.match_type || "-"],
    ["Acción", q?.action_type || "-"],
    ["Material", qrRuleMaterialDisplay(q)],
    ["Modo seleccion", fifoMaterialPolicyLabel(q?.fifo_material_policy)],
    ["Rack", q?.rack_code || (q?.rack_id ? `Rack ID ${q.rack_id}` : "-")],
    ["Modo de ruta", fifoChainFlowLabel(q)],
    ["Origen tramo 1", routeEndpointLabel(q, "source")],
    ["Material tramo 1", fifoChainStep1MaterialDisplay(q)],
    ["Destino 1", routeEndpointLabel(q, "destination")],
    ["Origen tramo 2", fifoChainStep2SourceModeLabel(q?.fifo_chain_step2_source_mode)],
    ["Material tramo 2", fifoChainStep2MaterialDisplay(q)],
    ["Origen 2", secondaryRouteEndpointLabel(q, "second_source")],
    ["Destino 2", secondaryRouteEndpointLabel(q, "second_destination")],
    ["Origen tramo 3", fifoChainStep3EndpointLabel(q, "source")],
    ["Material tramo 3", fifoChainStep3MaterialDisplayForList(q)],
    ["Destino tramo 3", fifoChainStep3EndpointLabel(q, "destination")],
    ["Origen tramo 4", fifoChainStep4EndpointLabel(q, "source")],
    ["Material tramo 4", fifoChainStep4MaterialDisplayForList(q)],
    ["Destino tramo 4", fifoChainStep4EndpointLabel(q, "destination")],
    ["Scanner requerido", Number(q?.requires_scanner_station ?? 0) ? "Sí" : "No"],
    ["Estado", Number(q?.is_active ?? 0) ? "Activo" : "Inactivo"],
  ];
  return rows.map(([label, value]) => `
    <div class="qr-modal-meta-row">
      <span>${escapeHtml(label)}</span>
      <b>${escapeHtml(value)}</b>
    </div>`).join("");
}
function renderQrRulePrintLabel(q, imageUrl = "") {
  if (!qrRulePrintLabel || !q) return;
  const origin = `${qrRuleAreaLabel(q, "source")} / ${qrRuleCellLabel(q, "source")}`;
  const destination = `${qrRuleAreaLabel(q, "destination")} / ${qrRuleCellLabel(q, "destination")}`;
  qrRulePrintLabel.innerHTML = `
    <div class="qr-print-title">${escapeHtml(qrRuleAliasTitle(q))}</div>
    ${imageUrl ? `<img class="qr-print-image" src="${imageUrl}" alt="QR ${escapeHtml(q.qr_value || "")}">` : `<div class="qr-print-missing">QR no disponible</div>`}
    <div class="qr-print-value">${escapeHtml(q.qr_value || "")}</div>
    <div class="qr-print-line">Acción: ${escapeHtml(q.action_type || "-")}</div>
    <div class="qr-print-line">Material: ${escapeHtml(qrRuleMaterialLabel(q))}</div>
    <div class="qr-print-line">Origen: ${escapeHtml(origin)}</div>
    <div class="qr-print-line">Destino: ${escapeHtml(destination)}</div>
  `;
}
async function hydrateQrRuleThumbnails() {
  if (!qrRulesList || !adminToken) return;
  const imgs = Array.from(qrRulesList.querySelectorAll("[data-qr-thumb-img]"));
  for (const img of imgs) {
    const ruleId = Number(img.dataset.qrThumbImg || 0);
    if (!ruleId) continue;
    const holder = img.closest(".qr-thumb-button");
    const loading = holder?.querySelector(".qr-thumb-loading");
    const error = holder?.querySelector(".qr-thumb-error");
    try {
      const objectUrl = await fetchQrRuleImageObjectUrl(ruleId, 120);
      if (!document.body.contains(img)) {
        URL.revokeObjectURL(objectUrl);
        continue;
      }
      qrRuleImageObjectUrls.set(ruleId, objectUrl);
      img.src = objectUrl;
      img.classList.remove("hidden");
      if (loading) loading.classList.add("hidden");
      if (error) error.classList.add("hidden");
    } catch (_) {
      img.classList.add("hidden");
      if (loading) loading.classList.add("hidden");
      if (error) error.classList.remove("hidden");
    }
  }
}
async function openQrRulePreviewModal(ruleId) {
  const q = qrActionRules.find(x => Number(x.id) === Number(ruleId));
  if (!q || !qrRulePreviewModal) return;
  revokeQrRuleModalImageUrl();
  if (qrRuleModalTitle) qrRuleModalTitle.textContent = qrRuleAliasTitle(q);
  if (qrRuleModalAlias) qrRuleModalAlias.textContent = q.qr_alias || "-";
  if (qrRuleModalValue) qrRuleModalValue.textContent = q.qr_value || "";
  if (qrRuleModalMeta) qrRuleModalMeta.innerHTML = renderQrRuleMetaHtml(q);
  if (qrRuleModalImage) {
    qrRuleModalImage.removeAttribute("src");
    qrRuleModalImage.alt = `QR ${q.qr_value || ""}`;
    qrRuleModalImage.classList.add("hidden");
  }
  if (qrRuleModalImageError) {
    qrRuleModalImageError.textContent = "Cargando QR...";
    qrRuleModalImageError.classList.remove("hidden");
  }
  if (btnPrintQrRuleLabel) btnPrintQrRuleLabel.disabled = true;
  renderQrRulePrintLabel(q, "");
  qrRulePreviewModal.classList.remove("hidden");
  qrRulePreviewModal.style.display = "flex";
  try {
    qrRuleModalImageObjectUrl = await fetchQrRuleImageObjectUrl(ruleId, 500);
    if (qrRuleModalImage) {
      qrRuleModalImage.src = qrRuleModalImageObjectUrl;
      qrRuleModalImage.classList.remove("hidden");
    }
    if (qrRuleModalImageError) qrRuleModalImageError.classList.add("hidden");
    renderQrRulePrintLabel(q, qrRuleModalImageObjectUrl);
    if (btnPrintQrRuleLabel) btnPrintQrRuleLabel.disabled = false;
  } catch (err) {
    if (qrRuleModalImageError) {
      qrRuleModalImageError.textContent = `QR no disponible: ${String(err.message || err)}`;
      qrRuleModalImageError.classList.remove("hidden");
    }
  }
}
function closeQrRulePreviewModal() {
  if (!qrRulePreviewModal) return;
  qrRulePreviewModal.classList.add("hidden");
  qrRulePreviewModal.style.display = "";
  revokeQrRuleModalImageUrl();
  if (qrRuleModalImage) {
    qrRuleModalImage.removeAttribute("src");
    qrRuleModalImage.classList.add("hidden");
  }
}
function renderQrRulesList() {
  if (!qrRulesList) return;
  revokeQrRuleThumbUrls();
  if (!qrActionRules.length) {
    qrRulesList.innerHTML = `<div class="small">Sin QR configurados.</div>`;
    return;
  }
  const rows = qrActionRules.map(q => `
    <tr class="clickable-row" data-qr-rule-id="${q.id}" tabindex="0">
      <td>
        <button type="button" class="qr-thumb-button" data-qr-preview-id="${q.id}" title="Ver QR ${escapeHtml(q.qr_value || "")}" aria-label="Ver QR ${escapeHtml(q.qr_value || "")}">
          <span class="qr-thumb-loading">QR</span>
          <img class="qr-thumb-img hidden" data-qr-thumb-img="${q.id}" alt="QR ${escapeHtml(q.qr_value || "")}">
          <span class="qr-thumb-error hidden">QR no disponible</span>
        </button>
      </td>
      <td><b>${escapeHtml(q.qr_value || "")}</b><div class="small">${escapeHtml(q.qr_alias || "")}</div></td>
      <td>${escapeHtml(q.action_type || "-")}</td>
      <td>${escapeHtml(qrRuleMaterialDisplay(q))}</td>
      <td>${escapeHtml(fifoMaterialPolicyLabel(q.fifo_material_policy))}</td>
      <td>${escapeHtml(fifoChainFlowLabel(q))}</td>
      <td>${escapeHtml(routeEndpointLabel(q, "source"))}</td>
      <td>${escapeHtml(routeEndpointLabel(q, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep1MaterialDisplay(q))}</td>
      <td>${escapeHtml(secondaryRouteEndpointLabel(q, "second_source"))}</td>
      <td>${escapeHtml(secondaryRouteEndpointLabel(q, "second_destination"))}</td>
      <td>${escapeHtml(fifoChainStep2MaterialDisplay(q))}</td>
      <td>${escapeHtml(fifoChainStep3EndpointLabel(q, "source"))}</td>
      <td>${escapeHtml(fifoChainStep3EndpointLabel(q, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep3MaterialDisplayForList(q))}</td>
      <td>${escapeHtml(fifoChainStep4EndpointLabel(q, "source"))}</td>
      <td>${escapeHtml(fifoChainStep4EndpointLabel(q, "destination"))}</td>
      <td>${escapeHtml(fifoChainStep4MaterialDisplayForList(q))}</td>
      <td>${escapeHtml(q.agv_code || "-")}</td>
      <td>${escapeHtml(q.task_typ || "-")}</td>
      <td>${Number(q.is_active ?? 0) ? "Activo" : "Inactivo"}</td>
    </tr>`).join("");
  qrRulesList.innerHTML = `<table class="diagnosis-table qr-route-table"><thead><tr><th>QR le&iacute;do</th><th>Valor</th><th>Acci&oacute;n</th><th>Material</th><th>Modo selecci&oacute;n</th><th>Modo de ruta</th><th>Origen tramo 1</th><th>Destino 1</th><th>Material tramo 1</th><th>Origen 2</th><th>Destino 2</th><th>Material tramo 2</th><th>Origen 3</th><th>Destino 3</th><th>Material tramo 3</th><th>Origen 4</th><th>Destino 4</th><th>Material tramo 4</th><th>AGV</th><th>Task type</th><th>Activo</th></tr></thead><tbody>${rows}</tbody></table>`;
  qrRulesList.querySelectorAll("[data-qr-rule-id]").forEach(row => {
    row.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-qr-preview-id]")) return;
      loadQrRuleForm(Number(row.dataset.qrRuleId));
    });
    row.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        loadQrRuleForm(Number(row.dataset.qrRuleId));
      }
    });
  });
  qrRulesList.querySelectorAll("[data-qr-preview-id]").forEach(btn => btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    openQrRulePreviewModal(Number(btn.dataset.qrPreviewId));
  }));
  hydrateQrRuleThumbnails();
}
async function loadQrActionRules() {
  if (!adminToken) return;
  qrActionRules = await fetchJson(API.adminQrActionRules, { headers: fetchHeaders() });
  renderQrRulesList();
  renderQrTransitionQrOptions();
}
async function saveQrRule() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrRuleId.value ? Number(qrRuleId.value) : null;
  const url = id ? API.adminQrActionRule(id) : API.adminQrActionRules;
  const method = id ? "PUT" : "POST";
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(qrRulePayload()) });
  await loadQrActionRules();
  if (qrAdminMsg) qrAdminMsg.textContent = "QR guardado.";
}
async function disableQrRule() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrRuleId.value ? Number(qrRuleId.value) : null;
  if (!id) throw new Error("Selecciona un QR.");
  const row = await fetchJson(API.adminQrActionRule(id), { method: "DELETE", headers: fetchHeaders() });
  await loadQrActionRules();
  loadQrRuleForm(row.id);
  if (qrAdminMsg) qrAdminMsg.textContent = "QR desactivado.";
}
function buildQrActionRuleOptions(selectedValue = "") {
  const selectedText = String(selectedValue || "");
  return `<option value="">Cualquier QR</option>` + (qrActionRules || []).map(q => {
    const alias = q.qr_alias ? ` - ${q.qr_alias}` : "";
    const inactive = Number(q.is_active ?? 0) === 1 ? "" : " - inactivo";
    return `<option value="${q.id}" ${selectedText === String(q.id) ? "selected" : ""}>${escapeHtml((q.qr_value || "") + alias + inactive)}</option>`;
  }).join("");
}
function renderQrTransitionQrOptions() {
  if (!qrTransitionQrRule) return;
  const cur = qrTransitionQrRule.value;
  qrTransitionQrRule.innerHTML = buildQrActionRuleOptions(cur);
}
function qrTransitionScopeLabel(value) {
  return value === "any_completed_order" ? "Cualquier orden completada" : "Solo ordenes QR/PDA";
}
function qrTransitionMatchModeLabel(value) {
  return value === "route_simple" ? "Simple por origen/destino" : "Avanzado";
}
function normalizeQrTransitionSourceMatchMode(value) {
  return value === "any_source" ? "any_source" : "configured_source";
}
function qrTransitionSourceMatchModeLabel(value) {
  return normalizeQrTransitionSourceMatchMode(value) === "any_source" ? "Cualquier area" : "Origen configurado";
}
function syncQrTransitionModeOptions({ applyDefaults = false } = {}) {
  const mode = qrTransitionMatchMode?.value || "advanced";
  const simple = mode === "route_simple";
  const anySource = normalizeQrTransitionSourceMatchMode(qrTransitionSourceMatchMode?.value) === "any_source";
  const ignoreCurrentMaterial = Number(qrTransitionIgnoreCurrentMaterial?.value || 0) === 1;
  if (qrTransitionSimpleHelp) qrTransitionSimpleHelp.classList.toggle("hidden", !simple);
  if (qrTransitionAnySourceHelp) {
    qrTransitionAnySourceHelp.textContent = ignoreCurrentMaterial
      ? "Aplicara desde cualquier area y con cualquier material actual."
      : "La regla aplicara desde cualquier origen, siempre que coincidan el material actual y el destino configurado.";
    qrTransitionAnySourceHelp.classList.toggle("hidden", !anySource);
  }
  if (qrTransitionCurrentMaterialLabel) {
    qrTransitionCurrentMaterialLabel.textContent = anySource && !ignoreCurrentMaterial ? "Material actual obligatorio" : "Material actual opcional";
  }
  document.querySelectorAll("[data-qr-transition-source-config]").forEach(el => el.classList.toggle("hidden", anySource));
  [qrTransitionSourceArea, qrTransitionSourceCell].forEach(el => {
    if (el) el.disabled = anySource || !adminToken;
  });
  document.querySelectorAll(".qr-transition-advanced-only").forEach(el => el.classList.toggle("hidden", simple));
  document.querySelectorAll("[data-qr-transition-current-condition]").forEach(el => el.classList.toggle("hidden", simple && (!anySource || ignoreCurrentMaterial)));
  if (simple && applyDefaults) {
    if (qrTransitionScope && (!qrTransitionRuleId?.value || qrTransitionScope.value === "qr_pda")) qrTransitionScope.value = "any_completed_order";
    if (qrTransitionIgnoreCurrentMaterial && (!qrTransitionRuleId?.value || qrTransitionIgnoreCurrentMaterial.value === "0")) qrTransitionIgnoreCurrentMaterial.value = "1";
    if (qrTransitionApplyOn) qrTransitionApplyOn.value = "movement_completed";
  }
}
function clearQrTransitionRuleForm() {
  if (!qrTransitionRuleId) return;
  qrTransitionRuleId.value = "";
  qrTransitionName.value = "";
  qrTransitionDescription.value = "";
  qrTransitionScope.value = "qr_pda";
  qrTransitionMatchMode.value = "advanced";
  qrTransitionSourceMatchMode.value = "configured_source";
  qrTransitionIgnoreCurrentMaterial.value = "0";
  qrTransitionQrRule.value = "";
  qrTransitionScanner.value = "";
  qrTransitionSourceArea.value = "";
  qrTransitionDestinationArea.value = "";
  qrTransitionSourceCell.value = "";
  qrTransitionDestinationCell.value = "";
  qrTransitionCurrentMaterial.value = "";
  qrTransitionCurrentRackStatus.value = "";
  qrTransitionNextMaterial.value = "";
  qrTransitionNextRackStatus.value = "";
  qrTransitionNextQuantity.value = "";
  qrTransitionClearQuantity.value = "0";
  qrTransitionNextComment.value = "";
  qrTransitionAppendComment.value = "1";
  qrTransitionApplyOn.value = "movement_completed";
  qrTransitionPriority.value = 0;
  qrTransitionActive.value = "1";
  syncQrTransitionModeOptions();
}
function loadQrTransitionRuleForm(id) {
  const item = qrTransitionRules.find(x => Number(x.id) === Number(id));
  if (!item) return;
  qrTransitionRuleId.value = item.id;
  qrTransitionName.value = item.name || "";
  qrTransitionDescription.value = item.description || "";
  qrTransitionScope.value = item.scope || "qr_pda";
  qrTransitionMatchMode.value = item.match_mode || "advanced";
  qrTransitionSourceMatchMode.value = normalizeQrTransitionSourceMatchMode(item.source_match_mode);
  qrTransitionIgnoreCurrentMaterial.value = String(item.ignore_current_material ?? 0);
  qrTransitionQrRule.innerHTML = buildQrActionRuleOptions(item.qr_action_rule_id || "");
  qrTransitionQrRule.value = item.qr_action_rule_id || "";
  renderQrTransitionScannerOptions();
  qrTransitionScanner.value = item.scanner_station_id || "";
  qrTransitionSourceArea.value = item.source_area_id || "";
  qrTransitionDestinationArea.value = item.destination_area_id || "";
  qrTransitionSourceCell.innerHTML = buildCellOptions(item.source_cell_id || "");
  qrTransitionDestinationCell.innerHTML = buildCellOptions(item.destination_cell_id || "");
  qrTransitionSourceCell.value = item.source_cell_id || "";
  qrTransitionDestinationCell.value = item.destination_cell_id || "";
  qrTransitionCurrentMaterial.value = item.current_material_group_id || "";
  qrTransitionCurrentRackStatus.value = item.current_rack_status || "";
  qrTransitionNextMaterial.value = item.next_material_group_id || "";
  qrTransitionNextRackStatus.value = item.next_rack_status || "";
  qrTransitionNextQuantity.value = item.next_quantity ?? "";
  qrTransitionClearQuantity.value = String(item.clear_quantity ?? 0);
  qrTransitionNextComment.value = item.next_comment || "";
  qrTransitionAppendComment.value = String(item.append_comment ?? 1);
  qrTransitionApplyOn.value = item.apply_on || "movement_completed";
  qrTransitionPriority.value = item.priority ?? 0;
  qrTransitionActive.value = String(item.is_active ?? 1);
  syncQrTransitionModeOptions();
}
function qrTransitionRulePayload() {
  const payload = {
    name: qrTransitionName.value.trim(),
    description: qrTransitionDescription.value.trim() || null,
    scope: qrTransitionScope.value || "qr_pda",
    match_mode: qrTransitionMatchMode.value || "advanced",
    source_match_mode: normalizeQrTransitionSourceMatchMode(qrTransitionSourceMatchMode?.value),
    ignore_current_material: Number(qrTransitionIgnoreCurrentMaterial.value || 0),
    qr_action_rule_id: qrTransitionQrRule.value ? Number(qrTransitionQrRule.value) : null,
    scanner_station_id: qrTransitionScanner.value ? Number(qrTransitionScanner.value) : null,
    source_area_id: qrTransitionSourceArea.value ? Number(qrTransitionSourceArea.value) : null,
    destination_area_id: qrTransitionDestinationArea.value ? Number(qrTransitionDestinationArea.value) : null,
    source_cell_id: qrTransitionSourceCell.value ? Number(qrTransitionSourceCell.value) : null,
    destination_cell_id: qrTransitionDestinationCell.value ? Number(qrTransitionDestinationCell.value) : null,
    current_material_group_id: qrTransitionCurrentMaterial.value ? Number(qrTransitionCurrentMaterial.value) : null,
    current_rack_status: qrTransitionCurrentRackStatus.value.trim() || null,
    next_material_group_id: qrTransitionNextMaterial.value ? Number(qrTransitionNextMaterial.value) : null,
    next_rack_status: qrTransitionNextRackStatus.value.trim() || null,
    next_quantity: qrTransitionNextQuantity.value === "" ? null : Number(qrTransitionNextQuantity.value),
    clear_quantity: Number(qrTransitionClearQuantity.value || 0),
    next_comment: qrTransitionNextComment.value.trim() || null,
    append_comment: Number(qrTransitionAppendComment.value || 0),
    apply_on: qrTransitionApplyOn.value || "movement_completed",
    priority: Number(qrTransitionPriority.value || 0),
    is_active: Number(qrTransitionActive.value || 0),
  };
  if (!["qr_pda", "any_completed_order"].includes(payload.scope)) throw new Error("Alcance invalido.");
  if (!["advanced", "route_simple"].includes(payload.match_mode)) throw new Error("Modo de coincidencia invalido.");
  if (!["configured_source", "any_source"].includes(payload.source_match_mode)) throw new Error("Origen de transicion invalido.");
  if (payload.source_match_mode === "any_source") {
    payload.source_area_id = null;
    payload.source_cell_id = null;
    if (!payload.destination_area_id && !payload.destination_cell_id) {
      throw new Error("La transicion desde cualquier area requiere destino y material siguiente.");
    }
    if (!payload.next_material_group_id && !payload.next_rack_status) {
      throw new Error("La transicion desde cualquier area requiere destino y material siguiente.");
    }
    if (!Number(payload.ignore_current_material || 0) && !payload.current_material_group_id) {
      throw new Error("Cualquier area requiere Material actual cuando no se ignora el material.");
    }
  }
  if (payload.match_mode === "route_simple") {
    if (!payload.next_material_group_id) throw new Error("En modo simple selecciona Material despues de completar.");
    if (!payload.source_area_id && !payload.destination_area_id && !payload.source_cell_id && !payload.destination_cell_id) {
      throw new Error("En modo simple selecciona al menos un origen/destino por area o celda.");
    }
    payload.apply_on = "movement_completed";
  }
  return payload;
}
function transitionLabel(...values) {
  return values.map(v => String(v || "").trim()).filter(Boolean).join(" - ") || "-";
}
function qrTransitionSourceLabel(rule) {
  return normalizeQrTransitionSourceMatchMode(rule?.source_match_mode) === "any_source"
    ? "Cualquier area"
    : transitionLabel(rule?.source_cell_code, rule?.source_area_code, rule?.source_area_name);
}
function renderQrTransitionRulesList() {
  if (!qrTransitionRulesList) return;
  if (!qrTransitionRules.length) {
    qrTransitionRulesList.innerHTML = `<div class="small">Sin transiciones configuradas.</div>`;
    return;
  }
  const rows = qrTransitionRules.map(rule => `
    <tr>
      <td>${escapeHtml(rule.name || "-")}</td>
      <td>${escapeHtml(qrTransitionScopeLabel(rule.scope))}</td>
      <td>${escapeHtml(qrTransitionMatchModeLabel(rule.match_mode))}</td>
      <td>${escapeHtml(qrTransitionSourceMatchModeLabel(rule.source_match_mode))}</td>
      <td>${escapeHtml(transitionLabel(rule.qr_action_rule_value, rule.qr_action_rule_alias))}</td>
      <td>${escapeHtml(transitionLabel(rule.scanner_station_code, rule.scanner_station_name))}</td>
      <td>${escapeHtml(qrTransitionSourceLabel(rule))}</td>
      <td>${escapeHtml(transitionLabel(rule.destination_cell_code, rule.destination_area_code, rule.destination_area_name))}</td>
      <td>${escapeHtml(Number(rule.ignore_current_material ?? 0) ? "Ignorado" : transitionLabel(rule.current_material_group_code, rule.current_material_group_name))}</td>
      <td>${escapeHtml(transitionLabel(rule.next_material_group_code, rule.next_material_group_name))}</td>
      <td>${Number(rule.ignore_current_material ?? 0) ? "Si" : "No"}</td>
      <td>${escapeHtml(rule.priority ?? 0)}</td>
      <td>${Number(rule.is_active ?? 0) ? "Si" : "No"}</td>
      <td>${escapeHtml(rule.applied_count ?? 0)}</td>
      <td>${escapeHtml(rule.last_applied_at ? (toLocalInputValue(rule.last_applied_at).replace("T", " ") || rule.last_applied_at) : "-")}</td>
      <td><button class="btn ghost small-btn" type="button" data-qr-transition-edit="${rule.id}">Editar</button></td>
    </tr>`).join("");
  qrTransitionRulesList.innerHTML = `<table class="diagnosis-table"><thead><tr><th>Nombre</th><th>Alcance</th><th>Modo</th><th>Origen de transici&oacute;n</th><th>QR asociado</th><th>Scanner</th><th>Origen</th><th>Destino</th><th>Material actual</th><th>Material siguiente</th><th>Ignorar material actual</th><th>Prioridad</th><th>Activo</th><th>Applied count</th><th>Last applied</th><th>Acciones</th></tr></thead><tbody>${rows}</tbody></table>`;
  qrTransitionRulesList.querySelectorAll("[data-qr-transition-edit]").forEach(btn => btn.addEventListener("click", () => loadQrTransitionRuleForm(Number(btn.dataset.qrTransitionEdit))));
}
async function loadQrTransitionRules() {
  if (!adminToken) return;
  qrTransitionRules = await fetchJson(API.adminQrTransitionRules, { headers: fetchHeaders() });
  renderQrTransitionRulesList();
}
async function saveQrTransitionRule() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrTransitionRuleId.value ? Number(qrTransitionRuleId.value) : null;
  const url = id ? API.adminQrTransitionRule(id) : API.adminQrTransitionRules;
  const method = id ? "PUT" : "POST";
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(qrTransitionRulePayload()) });
  await loadQrTransitionRules();
  if (id) loadQrTransitionRuleForm(id);
  if (qrAdminMsg) qrAdminMsg.textContent = "Transicion guardada.";
}
async function disableQrTransitionRule() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrTransitionRuleId.value ? Number(qrTransitionRuleId.value) : null;
  if (!id) throw new Error("Selecciona una transicion.");
  const row = await fetchJson(API.adminQrTransitionRule(id), { method: "DELETE", headers: fetchHeaders() });
  await loadQrTransitionRules();
  loadQrTransitionRuleForm(row.id);
  if (qrAdminMsg) qrAdminMsg.textContent = "Transicion desactivada.";
}
function renderQrTransitionPreview(data) {
  if (!qrTransitionPreviewResult) return;
  if (!data) {
    qrTransitionPreviewResult.textContent = "Sin preview.";
    return;
  }
  const rack = data.rack || {};
  const rule = data.matched_rule || null;
  const simpleRuleMessage = rule?.match_mode === "route_simple" ? "Regla simple por origen/destino encontrada." : "";
  const anySourceMessage = normalizeQrTransitionSourceMatchMode(rule?.source_match_mode) === "any_source"
    ? "Regla desde cualquier area encontrada."
    : "";
  qrTransitionPreviewResult.innerHTML = `
    <div><b>movement_order_id:</b> ${escapeHtml(data.movement_order_id || "-")}</div>
    <div><b>Rack:</b> ${escapeHtml(transitionLabel(rack.code, rack.name))}</div>
    <div><b>Material actual:</b> ${escapeHtml(transitionLabel(data.current_material?.code, data.current_material?.name))}</div>
    <div><b>Regla encontrada:</b> ${escapeHtml(rule ? transitionLabel(rule.id, rule.name) : "No hay transicion configurada para esta orden.")}</div>
    ${simpleRuleMessage ? `<div><b>${escapeHtml(simpleRuleMessage)}</b></div>` : ""}
    ${anySourceMessage ? `<div><b>${escapeHtml(anySourceMessage)}</b></div>` : ""}
    <div><b>Alcance:</b> ${escapeHtml(rule ? qrTransitionScopeLabel(rule.scope) : "-")}</div>
    <div><b>Modo:</b> ${escapeHtml(rule ? qrTransitionMatchModeLabel(rule.match_mode) : "-")}</div>
    <div><b>Origen:</b> ${escapeHtml(rule ? qrTransitionSourceLabel(rule) : "-")}</div>
    <div><b>Ignorar material actual:</b> ${escapeHtml(rule ? (Number(rule.ignore_current_material ?? 0) ? "Si" : "No") : "-")}</div>
    <div><b>Material siguiente:</b> ${escapeHtml(transitionLabel(data.next_material?.code, data.next_material?.name))}</div>
    <div><b>Candidatas:</b> ${escapeHtml(data.candidate_count ?? 0)}</div>
    <div><b>Mensaje:</b> ${escapeHtml(data.message || "-")}</div>
  `;
}
async function previewQrTransition() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrTransitionPreviewOrderId?.value ? Number(qrTransitionPreviewOrderId.value) : null;
  if (!id) throw new Error("Captura un MovementOrder ID.");
  const data = await fetchJson(API.adminQrTransitionPreview(id), { headers: fetchHeaders() });
  renderQrTransitionPreview(data);
  if (qrAdminMsg) qrAdminMsg.textContent = "Preview de transicion generado. No se modifico ningun dato.";
}
function renderQrTransitionLogs() {
  if (!qrTransitionLogsList) return;
  if (!qrTransitionLogs.length) {
    qrTransitionLogsList.innerHTML = `<div class="small">Sin historial de transiciones.</div>`;
    return;
  }
  const rows = qrTransitionLogs.map(log => `
    <tr>
      <td>${escapeHtml(log.created_at ? (toLocalInputValue(log.created_at).replace("T", " ") || log.created_at) : "-")}</td>
      <td>${escapeHtml(transitionLabel(log.transition_rule_id, log.transition_rule_name))}</td>
      <td>${escapeHtml(transitionLabel(log.movement_order_id, log.order_code))}</td>
      <td>${escapeHtml(transitionLabel(log.rack_code, log.rack_id))}</td>
      <td>${escapeHtml(transitionLabel(log.previous_material_group_name, log.previous_material_group_id))}</td>
      <td>${escapeHtml(transitionLabel(log.next_material_group_name, log.next_material_group_id))}</td>
      <td>${escapeHtml(log.status || "-")}</td>
      <td>${escapeHtml(log.message || "-")}</td>
    </tr>`).join("");
  qrTransitionLogsList.innerHTML = `<table class="diagnosis-table"><thead><tr><th>Fecha</th><th>Regla</th><th>Orden</th><th>Rack</th><th>Material anterior</th><th>Material nuevo</th><th>Status</th><th>Mensaje</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function loadQrTransitionLogs() {
  if (!adminToken) return;
  qrTransitionLogs = await fetchJson(API.adminQrTransitionLogs, { headers: fetchHeaders() });
  renderQrTransitionLogs();
}
async function applyQrTransitionManual() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = qrTransitionPreviewOrderId?.value ? Number(qrTransitionPreviewOrderId.value) : null;
  if (!id) throw new Error("Captura un MovementOrder ID.");
  const result = await fetchJson(API.adminQrTransitionApply(id), { method: "POST", headers: fetchHeaders() });
  renderQrTransitionPreview({
    movement_order_id: result.movement_order_id,
    rack: { id: result.rack_id },
    matched_rule: result.transition_rule_id ? { id: result.transition_rule_id, name: "" } : null,
    candidate_count: "-",
    message: result.message || "-",
  });
  await Promise.all([loadQrTransitionRules(), loadQrTransitionLogs()]);
  if (qrAdminMsg) qrAdminMsg.textContent = result.message || "Aplicacion de transicion finalizada.";
}
function clearScanTerminalForm() {
  if (!scanTerminalId) return;
  scanTerminalId.value = "";
  scanTerminalCode.value = "";
  scanTerminalName.value = "";
  scanTerminalDescription.value = "";
  scanTerminalScannerStation.value = "";
  scanTerminalApiKey.value = "";
  scanTerminalMode.value = "preview";
  scanTerminalAllowExecute.value = "0";
  scanTerminalRequirePreview.value = "1";
  scanTerminalActive.value = "1";
  scanTerminalLastSeen.value = "-";
  scanTerminalLastIp.value = "-";
}
function formatScanTerminalLastSeen(value) {
  return value ? (toLocalInputValue(value).replace("T", " ") || String(value)) : "-";
}
function loadScanTerminalForm(id) {
  const item = scanTerminals.find(x => Number(x.id) === Number(id));
  if (!item) return;
  scanTerminalId.value = item.id;
  scanTerminalCode.value = item.terminal_code || "";
  scanTerminalName.value = item.name || "";
  scanTerminalDescription.value = item.description || "";
  scanTerminalScannerStation.innerHTML = buildScannerStationOptions(item.scanner_station_id || "");
  scanTerminalScannerStation.value = item.scanner_station_id || "";
  scanTerminalApiKey.value = item.api_key || "";
  scanTerminalMode.value = item.mode || "preview";
  scanTerminalAllowExecute.value = String(item.allow_execute ?? 0);
  scanTerminalRequirePreview.value = String(item.require_preview ?? 1);
  scanTerminalActive.value = String(item.is_active ?? 1);
  scanTerminalLastSeen.value = formatScanTerminalLastSeen(item.last_seen_at);
  scanTerminalLastIp.value = item.last_ip || "-";
}
function scanTerminalPayload() {
  return {
    terminal_code: scanTerminalCode.value.trim(),
    name: scanTerminalName.value.trim(),
    description: scanTerminalDescription.value.trim() || null,
    scanner_station_id: scanTerminalScannerStation.value ? Number(scanTerminalScannerStation.value) : null,
    api_key: scanTerminalApiKey.value.trim() || null,
    mode: scanTerminalMode.value || "preview",
    allow_execute: Number(scanTerminalAllowExecute.value || 0),
    require_preview: Number(scanTerminalRequirePreview.value || 0),
    is_active: Number(scanTerminalActive.value || 0),
  };
}
function renderScanTerminalsList() {
  if (!scanTerminalsList) return;
  scanTerminalsList.innerHTML = scanTerminals.map(t => `
    <button type="button" class="list-item" data-scan-terminal-id="${t.id}">
      <b>${escapeHtml(t.terminal_code || "")}</b> ${escapeHtml(t.name || "")}
      <small>${escapeHtml(t.scanner_code || "sin scanner")} - ${escapeHtml(t.mode || "preview")} - ${Number(t.is_active ?? 0) ? "activo" : "inactivo"} - Last seen: ${escapeHtml(formatScanTerminalLastSeen(t.last_seen_at))} - Last IP: ${escapeHtml(t.last_ip || "-")}</small>
    </button>`).join("") || `<div class="small">Sin terminales PDA configurados.</div>`;
  scanTerminalsList.querySelectorAll("[data-scan-terminal-id]").forEach(btn => btn.addEventListener("click", () => loadScanTerminalForm(Number(btn.dataset.scanTerminalId))));
}
async function loadScanTerminals() {
  if (!adminToken) return;
  const selectedId = scanTerminalId?.value || "";
  scanTerminals = await fetchJson(API.adminScanTerminals, { headers: fetchHeaders() });
  renderScanTerminalsList();
  if (selectedId && scanTerminals.some(t => String(t.id) === String(selectedId))) {
    loadScanTerminalForm(Number(selectedId));
  }
}
async function saveScanTerminal() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = scanTerminalId.value ? Number(scanTerminalId.value) : null;
  const url = id ? API.adminScanTerminal(id) : API.adminScanTerminals;
  const method = id ? "PUT" : "POST";
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(scanTerminalPayload()) });
  await loadScanTerminals();
  if (qrAdminMsg) qrAdminMsg.textContent = "Terminal PDA guardado.";
}
async function disableScanTerminal() {
  if (!adminToken) throw new Error("Admin bloqueado.");
  const id = scanTerminalId.value ? Number(scanTerminalId.value) : null;
  if (!id) throw new Error("Selecciona un terminal.");
  const row = await fetchJson(API.adminScanTerminal(id), { method: "DELETE", headers: fetchHeaders() });
  await loadScanTerminals();
  loadScanTerminalForm(row.id);
  if (qrAdminMsg) qrAdminMsg.textContent = "Terminal PDA desactivado.";
}
function focusScanQrInput() {
  if (!scanQrValue) return;
  setTimeout(() => {
    try {
      scanQrValue.focus();
      scanQrValue.select();
    } catch (_) {}
  }, 0);
}
function scanQrStationLabel(row) {
  if (!row) return "";
  const code = String(row.scanner_code || "").trim();
  const name = String(row.name || "").trim();
  return name ? `${code} - ${name}` : code;
}
function renderScanQrScannerOptions() {
  if (!scanQrScannerSelect) return;
  const current = scanQrScannerSelect.value;
  const active = (scannerStations || []).filter(row => Number(row.is_active ?? 0) === 1 && String(row.scanner_code || "").trim());
  if (!active.length) {
    scanQrScannerSelect.innerHTML = `<option value="">Sin scanners activos cargados</option>`;
    if (scanQrScannerHelp) scanQrScannerHelp.textContent = adminToken ? "No hay scanners activos cargados. Puedes capturar el codigo manual." : "Para cargar el select inicia admin, o captura el codigo manual.";
    return;
  }
  scanQrScannerSelect.innerHTML = `<option value="">Selecciona scanner activo</option>` + active.map(row => `<option value="${escapeHtml(row.scanner_code || "")}">${escapeHtml(scanQrStationLabel(row))}</option>`).join("");
  if ([...scanQrScannerSelect.options].some(opt => opt.value === current)) scanQrScannerSelect.value = current;
  if (scanQrScannerHelp) scanQrScannerHelp.textContent = "Selecciona una estacion activa o captura un codigo manual como respaldo.";
}
async function loadScanQrScanners() {
  if (!scanQrScannerSelect) return;
  if (!adminToken) {
    renderScanQrScannerOptions();
    return;
  }
  try {
    scannerStations = await fetchJson(API.adminScannerStations, { headers: fetchHeaders() });
    renderScannerStationsList();
    renderScanQrScannerOptions();
  } catch (err) {
    scanQrScannerSelect.innerHTML = `<option value="">No se pudo cargar catalogo</option>`;
    if (scanQrScannerHelp) scanQrScannerHelp.textContent = `Usa scanner manual. Error: ${String(err)}`;
  }
}
function currentScanQrScannerCode() {
  const selectedCode = String(scanQrScannerSelect?.value || "").trim();
  if (selectedCode) return selectedCode;
  return String(scanQrScannerManual?.value || "").trim();
}
function entityLabel(entity, fallback = "-") {
  if (!entity || typeof entity !== "object") return fallback;
  const code = String(entity.code || entity.scanner_code || "").trim();
  const name = String(entity.name || entity.qr_alias || "").trim();
  if (code && name) return `${code} - ${name}`;
  return code || name || fallback;
}
function cellPayloadLabel(cell) {
  if (!cell || typeof cell !== "object" || !cell.id) return "-";
  const code = String(cell.code || "").trim();
  const coords = cell.x != null && cell.y != null ? `(${cell.x},${cell.y})` : "";
  return code && coords ? `${code} ${coords}` : (code || coords || "-");
}
function previewDetailRow(label, value) {
  return `<div><b>${escapeHtml(label)}:</b> ${escapeHtml(value == null || value === "" ? "-" : value)}</div>`;
}
function previewRoutePointsRows(result) {
  const points = Array.isArray(result?.route_points) ? result.route_points : [];
  if (!points.length) return "";
  return points.map((point, idx) => {
    const label = point?.role === "source_1" ? "Origen 1"
      : point?.role === "destination_1" ? "Destino 1"
      : point?.role === "source_2" ? "Origen 2"
      : point?.role === "destination_2" ? "Destino 2"
      : `Punto ${idx + 1}`;
    const area = entityLabel(point?.area, "");
    const cell = cellPayloadLabel(point?.cell);
    return previewDetailRow(label, `${area || "-"} / ${cell}`);
  }).join("");
}
function previewFifoChainStepsRows(result) {
  const steps = Array.isArray(result?.fifo_chain_steps) ? result.fifo_chain_steps.slice() : (Array.isArray(result?.trmx_steps) ? result.trmx_steps.slice() : []);
  const totalSteps = normalizeFifoChainTotalSteps(result?.fifo_chain_total_steps ?? result?.trmx_total_steps);
  if (totalSteps >= 3 && !steps.some(step => Number(step?.step || 0) === 3)) {
    steps.push({
      step: 3,
      source_mode: result?.fifo_chain_step3_source_mode,
      step3_material: result?.fifo_chain_step3_material,
      source: result?.fifo_chain_step3_source,
      destination: result?.fifo_chain_step3_destination,
    });
  }
  if (totalSteps >= 4 && !steps.some(step => Number(step?.step || 0) === 4)) {
    steps.push({
      step: 4,
      source_mode: result?.fifo_chain_step4_source_mode,
      step4_material: result?.fifo_chain_step4_material,
      source: result?.fifo_chain_step4_source,
      destination: result?.fifo_chain_step4_destination,
    });
  }
  if (!steps.length) return "";
  return steps.map((step) => {
    const sourceMode = normalizeFifoChainStep2SourceMode(step?.source_mode || step?.source?.source_mode);
    const isGlobalStep1 = Number(step?.step || 0) === 1 && sourceMode === "any_area_by_material";
    const isGlobalStep2 = Number(step?.step || 0) === 2 && sourceMode === "any_area_by_material";
    const isGlobalStep3 = Number(step?.step || 0) === 3 && sourceMode === "any_area_by_material";
    const isGlobalStep4 = Number(step?.step || 0) === 4 && sourceMode === "any_area_by_material";
    const materialLabel = entityLabel(step?.step1_material || step?.step2_material || step?.step3_material || step?.step4_material || result?.fifo_chain_step1_material || result?.fifo_chain_step2_material || result?.fifo_chain_step3_material || result?.fifo_chain_step4_material, "");
    const source = isGlobalStep1 || isGlobalStep2 || isGlobalStep3 || isGlobalStep4
      ? `Cualquier area con material ${materialLabel || "-"}`
      : `${entityLabel(step?.source?.area, "") || "-"} / ${cellPayloadLabel(step?.source?.cell)}`;
    const destination = `${entityLabel(step?.destination?.area, "") || "-"} / ${cellPayloadLabel(step?.destination?.cell)}`;
    const stepNumber = Number(step?.step || 0);
    const rowLabel = `Tramo ${stepNumber || "-"}`;
    const row = previewDetailRow(rowLabel, `${source} -> ${destination}`);
    if (isGlobalStep1) {
      const rack = entityLabel(step?.source?.rack, "");
      const cell = cellPayloadLabel(step?.source?.cell);
      const candidate = rack || cell ? previewDetailRow("Candidato tramo 1", `${rack || "-"} / ${cell}`) : "";
      return row + candidate + previewDetailRow("Nota tramo 1", "El rack del tramo 1 se seleccionara al ejecutar.");
    }
    if (isGlobalStep2) return row + previewDetailRow("Nota tramo 2", "El rack del tramo 2 se revalidara al finalizar el tramo 1.");
    if (isGlobalStep3) return row + previewDetailRow("Nota tramo 3", "El rack del tramo 3 se revalidara al finalizar el tramo 2.");
    if (isGlobalStep4) return row + previewDetailRow("Nota tramo 4", "El rack se revalidara al finalizar el tramo anterior.");
    return row;
  }).join("");
}
function renderScanQrPreviewResult(result) {
  if (!scanQrResultPanel) return;
  const ok = !!result?.ok;
  scanQrResultPanel.classList.toggle("ok", ok);
  scanQrResultPanel.classList.toggle("error", !ok);
  const scanner = result?.scanner || {};
  const qr = result?.qr || {};
  const source = result?.source || {};
  const destination = result?.destination || {};
  scanQrResultPanel.innerHTML = `
    <div class="scan-preview-title">${ok ? "Preview correcto" : "Preview con error"}</div>
    <div class="scan-preview-grid">
      ${previewDetailRow("Status", ok ? "preview_ok" : "error")}
      ${previewDetailRow("Mensaje", result?.message || "-")}
      ${previewDetailRow("Accion", result?.action || "-")}
      ${previewDetailRow("Scanner", entityLabel(scanner))}
      ${previewDetailRow("QR leido", qr.qr_value || "-")}
      ${previewDetailRow("Alias", qr.qr_alias || "-")}
      ${previewDetailRow("Tipo QR", qr.qr_type || result?.parsed?.parsed_type || "-")}
      ${previewDetailRow("Material", entityLabel(result?.material))}
      ${previewDetailRow("Rack", entityLabel(result?.rack_selected))}
      ${previewDetailRow("Modo de ruta", routeModeLabel(result?.route_mode))}
      ${previewDetailRow("Origen", `${entityLabel(source.area)} / ${cellPayloadLabel(source.cell)}`)}
      ${previewDetailRow("Destino", `${entityLabel(destination.area)} / ${cellPayloadLabel(destination.cell)}`)}
      ${previewRoutePointsRows(result)}
      ${previewFifoChainStepsRows(result)}
      ${previewDetailRow("Orden", result?.movement_order_id || result?.existing_movement_order_id || "-")}
      ${previewDetailRow("RCS", result?.dispatch_status || result?.rcs_status || result?.movement_order?.dispatch_status || "-")}
      ${previewDetailRow("Evento", result?.scan_event_id || "-")}
    </div>
  `;
}
function renderScanQrHistory(events = scanEvents) {
  if (!scanQrHistoryList) return;
  const rowsData = Array.isArray(events) ? events.slice(0, 30) : [];
  if (!rowsData.length) {
    scanQrHistoryList.innerHTML = `<div class="small">Sin escaneos registrados.</div>`;
    return;
  }
  const rows = rowsData.map(ev => `
    <tr>
      <td>${escapeHtml(toLocalInputValue(ev.created_at).replace("T", " ") || ev.created_at || "-")}</td>
      <td>${escapeHtml(ev.terminal_code || "-")}</td>
      <td>${escapeHtml(ev.scanner_code || "-")}</td>
      <td>${escapeHtml(ev.qr_value || "-")}</td>
      <td>${escapeHtml(ev.resolved_action || "-")}</td>
      <td>${escapeHtml(ev.status || "-")}</td>
      <td>${escapeHtml(ev.error_message || "-")}</td>
      <td>${escapeHtml(ev.movement_order_id || "-")}</td>
    </tr>`).join("");
  scanQrHistoryList.innerHTML = `<table class="diagnosis-table"><thead><tr><th>Hora</th><th>Terminal</th><th>Scanner</th><th>QR</th><th>Accion</th><th>Status</th><th>Error</th><th>Orden</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function loadScanQrHistory({ quiet = false } = {}) {
  scanEvents = await fetchJson(API.scanEvents);
  renderScanQrHistory(scanEvents);
  renderScanEvents();
  if (!quiet && scanQrMsg) scanQrMsg.textContent = "Historial actualizado.";
}
async function runScanQrPreview(mode = "preview") {
  if (scanQrPreviewInFlight) return;
  const scannerCode = currentScanQrScannerCode();
  const qrValueText = String(scanQrValue?.value || "").trim();
  if (!scannerCode) {
    if (scanQrMsg) scanQrMsg.textContent = "Selecciona o captura el codigo del scanner.";
    focusScanQrInput();
    return;
  }
  if (!qrValueText) {
    if (scanQrMsg) scanQrMsg.textContent = "Escanea o captura un QR.";
    focusScanQrInput();
    return;
  }
  scanQrPreviewInFlight = true;
  if (btnScanQrPreview) btnScanQrPreview.disabled = true;
  if (btnScanQrExecute) btnScanQrExecute.disabled = true;
  const isExecute = mode === "execute";
  if (scanQrMsg) scanQrMsg.textContent = isExecute ? "Ejecutando FIFO..." : "Generando preview...";
  try {
    const result = await fetchJson(isExecute ? API.scanExecute : API.scanPreview, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scanner_code: scannerCode, qr_value: qrValueText, created_by: "operator" }),
    });
    renderScanQrPreviewResult(result);
    if (scanQrMsg) scanQrMsg.textContent = result?.message || "Preview procesado.";
    if (scanQrValue) scanQrValue.value = "";
    await loadScanQrHistory({ quiet: true }).catch(err => {
      if (scanQrMsg) scanQrMsg.textContent = `${result?.message || "Preview procesado."} No se pudo refrescar historial: ${String(err)}`;
    });
  } catch (err) {
    renderScanQrPreviewResult({ ok: false, scanner: { scanner_code: scannerCode }, qr: { qr_value: qrValueText }, message: String(err) });
    if (scanQrMsg) scanQrMsg.textContent = `Error: ${String(err)}`;
  } finally {
    scanQrPreviewInFlight = false;
    if (btnScanQrPreview) btnScanQrPreview.disabled = false;
    if (btnScanQrExecute) btnScanQrExecute.disabled = false;
    focusScanQrInput();
  }
}
function renderScanEvents() {
  if (!scanEventsList) return;
  if (!Array.isArray(scanEvents) || !scanEvents.length) {
    scanEventsList.innerHTML = `<div class="small">Sin escaneos registrados.</div>`;
    return;
  }
  const rows = scanEvents.map(ev => `
    <tr>
      <td>${escapeHtml(toLocalInputValue(ev.created_at).replace("T", " ") || ev.created_at || "-")}</td>
      <td>${escapeHtml(ev.terminal_code || "-")}</td>
      <td>${escapeHtml(ev.scanner_code || "-")}</td>
      <td>${escapeHtml(ev.qr_value || "-")}</td>
      <td>${escapeHtml(ev.resolved_action || "-")}</td>
      <td>${escapeHtml(ev.rack_code || ev.rack_id || "-")}</td>
      <td>${escapeHtml(ev.material_group_name || ev.material_group_id || "-")}</td>
      <td>${escapeHtml(ev.movement_order_id || "-")}</td>
      <td>${escapeHtml(ev.status || "-")}</td>
      <td>${escapeHtml(ev.error_message || "-")}</td>
      <td><button class="btn ghost" type="button" data-scan-event-detail="${ev.id}">JSON</button></td>
    </tr>`).join("");
  scanEventsList.innerHTML = `<table class="diagnosis-table"><thead><tr><th>Fecha/hora</th><th>Terminal</th><th>Scanner</th><th>QR leido</th><th>Accion</th><th>Rack</th><th>Material</th><th>Orden</th><th>Status</th><th>Error</th><th>Detalle</th></tr></thead><tbody>${rows}</tbody></table>`;
  scanEventsList.querySelectorAll("[data-scan-event-detail]").forEach(btn => btn.addEventListener("click", () => showScanEventDetail(Number(btn.dataset.scanEventDetail))));
}
function showScanEventDetail(id) {
  const item = scanEvents.find(x => Number(x.id) === Number(id));
  if (!item || !scanEventDetailBox || !scanEventDetailJson) return;
  scanEventDetailBox.classList.remove("hidden");
  scanEventDetailJson.textContent = JSON.stringify({ request: item.request || {}, result: item.result || {}, event: item }, null, 2);
}
async function loadScanEvents() {
  scanEvents = await fetchJson(API.scanEvents);
  renderScanEvents();
  if (qrAdminMsg) qrAdminMsg.textContent = "Historial actualizado.";
}
async function loadQrAdminData() {
  renderQrCatalogOptions();
  if (!adminToken) return;
  await loadScannerStations();
  await Promise.all([loadQrActionRules(), loadScanTerminals(), loadScanEvents(), loadQrTransitionRules(), loadQrTransitionLogs()]);
}
function activateQrPanel(name) {
  document.querySelectorAll("#card-qr-scanners .qr-tab").forEach(btn => btn.classList.toggle("active", btn.dataset.qrTab === name));
  const panelMap = { stations: "qrPanelStations", rules: "qrPanelRules", terminals: "qrPanelTerminals", events: "qrPanelEvents", transitions: "qrPanelTransitions" };
  Object.entries(panelMap).forEach(([key, id]) => {
    const panel = document.getElementById(id);
    if (panel) panel.classList.toggle("active", key === name);
  });
  if (name === "terminals" && adminToken) {
    loadScanTerminals().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; });
  }
  if (name === "transitions" && adminToken) {
    renderQrCatalogOptions();
    renderQrTransitionScannerOptions();
    renderQrTransitionQrOptions();
    loadQrTransitionRules().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; });
    loadQrTransitionLogs().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; });
  }
}

function clearAreaForm() {
  finishEditingLock("areas", { applyPending: false });
  areaId.value = ""; areaCode.value = ""; areaName.value = ""; areaType.value = "almacen"; areaColor.value = "#4f46e5"; areaPriority.value = 0; areaActive.value = 1; if (areaMatterArea) areaMatterArea.value = ""; areaDescription.value = "";
}
function loadAreaForm(id) {
  finishEditingLock("areas", { applyPending: false });
  const item = catalog.areas.find(a => Number(a.id) === Number(id));
  if (!item) return;
  areaId.value = item.id; areaCode.value = item.code; areaName.value = item.name; areaType.value = item.area_type; areaColor.value = item.color || "#4f46e5"; areaPriority.value = item.priority ?? 0; areaActive.value = String(item.is_active ?? 1); if (areaMatterArea) areaMatterArea.value = item.matter_area || ""; areaDescription.value = item.description || "";
}
function clearMaterialForm() {
  materialId.value = ""; materialCode.value = ""; materialName.value = ""; materialColor.value = randomMaterialColor(); materialActive.value = 1; materialDescription.value = "";
}
function loadMaterialForm(id) {
  const item = catalog.materials.find(m => Number(m.id) === Number(id));
  if (!item) return;
  materialId.value = item.id; materialCode.value = item.code; materialName.value = item.name; materialColor.value = item.color || randomMaterialColor(); materialActive.value = String(item.is_active ?? 1); materialDescription.value = item.description || "";
}
function clearRackForm() {
  const templateRows = getRackCustomFieldEditorRows();
  rackId.value = ""; rackCode.value = ""; rackName.value = ""; rackStatus.value = "disponible"; rackMaterial.value = ""; if (rackReservationState) rackReservationState.value = "0"; rackReservationOriginal = "0"; if (rackReservationTask) rackReservationTask.value = ""; rackLot.value = ""; rackQty.value = 0; rackMfgCode.value = ""; rackFifo.value = ""; rackMoved.value = ""; rackComment.value = ""; renderRackCustomFieldEditor(templateRows);
}
function loadRackForm(id) {
  const item = catalog.racks.find(r => Number(r.id) === Number(id));
  if (!item) return;
  const reservation = getRackReservationSnapshot(item);
  rackId.value = item.id; rackCode.value = item.code; rackName.value = item.name || ""; rackStatus.value = item.status || "disponible"; rackMaterial.value = item.material_group_id || ""; if (rackReservationState) rackReservationState.value = isReservedState(reservation.reservation_status) ? "1" : "0"; rackReservationOriginal = rackReservationState ? String(rackReservationState.value || "0") : "0"; if (rackReservationTask) rackReservationTask.value = reservationTaskLabel(reservation); rackLot.value = item.lot || ""; rackQty.value = item.quantity || 0; rackMfgCode.value = item.manufacturer_code || ""; rackFifo.value = toLocalInputValue(item.fifo_entered_at); rackMoved.value = toLocalInputValue(item.last_moved_at); rackComment.value = item.comment || ""; renderRackCustomFieldEditor(item.custom_fields || []);
}
function refreshRackReservationFieldsForSelection() {
  if (!rackId?.value) return;
  const item = catalog.racks.find(r => Number(r.id) === Number(rackId.value));
  if (!item) return;
  const reservation = getRackReservationSnapshot(item);
  if (rackReservationState) rackReservationState.value = isReservedState(reservation.reservation_status) ? "1" : "0";
  if (rackReservationTask) rackReservationTask.value = reservationTaskLabel(reservation);
  rackReservationOriginal = rackReservationState ? String(rackReservationState.value || "0") : rackReservationOriginal;
}
async function refreshReservationUiState() {
  await loadLocations();
  await loadCatalog();
  if (isEditingLockEffective("cell")) {
    deferAdminRefresh();
  } else {
    renderRackOptions();
    const selectedLoc = getLocationAtGrid(selected.x, selected.y);
    if (isMultiSelectionActive()) {
      multiSelectedLocationIds = new Set(Array.from(multiSelectedLocationIds).filter(id => getLocationById(id)));
      fillMultiCellForm();
    } else if (selectedLoc) {
      fillCellForm(selectedLoc);
    }
    setCellBulkControlsState();
    refreshRackReservationFieldsForSelection();
    if (!isMultiSelectionActive()) syncCellReservationFromRackSelection();
  }
  draw();
}
async function refreshBackground() {
  const bg = await fetchJson(API.background);
  bgState.url = bg.url;
  bgState.scale_x = Number(bg.scale_x || 1);
  bgState.scale_y = Number(bg.scale_y || 1);
  bgState.offset_x = Number(bg.offset_x || 0);
  bgState.offset_y = Number(bg.offset_y || 0);
  bgScaleX.value = bgState.scale_x;
  bgScaleY.value = bgState.scale_y;
  bgOffX.value = bgState.offset_x;
  bgOffY.value = bgState.offset_y;
  bgStateLbl.textContent = bg.url ? "Imagen cargada" : "Sin imagen";
  if (!bg.url) {
    bgState.img = null; bgState.loaded = false; draw(); return;
  }
  await new Promise((resolve) => {
    const img = new Image();
    img.onload = () => { bgState.img = img; bgState.loaded = true; resolve(); };
    img.onerror = () => { bgState.img = null; bgState.loaded = false; resolve(); };
    img.src = `${bg.url}?t=${Date.now()}`;
  });
  draw();
}
async function saveBgTransform() {
  if (!adminToken) return;
  bgState.scale_x = Number(bgScaleX.value || 1);
  bgState.scale_y = Number(bgScaleY.value || 1);
  bgState.offset_x = Number(bgOffX.value || 0);
  bgState.offset_y = Number(bgOffY.value || 0);
  await fetchJson(API.adminBgTransform, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ scale_x: bgState.scale_x, scale_y: bgState.scale_y, offset_x: bgState.offset_x, offset_y: bgState.offset_y }) });
  draw();
}
function scheduleBgTransformSave(delay = 250) {
  clearTimeout(bgSaveTimer);
  bgSaveTimer = setTimeout(() => { saveBgTransform().catch(() => {}); }, delay);
}
async function adminLoadClientIp() {
  const data = await fetchJson(API.adminClientIpGet, { headers: fetchHeaders() });
  clientIp.value = data.client_ip || "";
}
async function adminSaveClientIp() {
  await fetchJson(API.adminClientIpSet, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ client_ip: clientIp.value || "" }) });
  adminMsg.textContent = "IP guardada.";
}

function setRobotMonitorEnabled(enabled, options = {}) {
  robotMonitorEnabled = !!enabled;
  if (robotMonitorPanel) {
    robotMonitorPanel.style.display = robotMonitorEnabled ? '' : 'none';
  }
  if (btnRobotMonitorRefresh) {
    btnRobotMonitorRefresh.disabled = !robotMonitorEnabled;
  }
  if (robotMonitorEnabled) {
    if (!options.skipImmediateRefresh) {
      refreshRobotMonitor({ force: true }).catch(() => {});
    }
  } else {
    syncRobotVisualTargets([]);
    draw();
    if (robotMonitorSubtitle) robotMonitorSubtitle.textContent = 'Monitoreo deshabilitado';
    renderRobotMonitorItems([], 'Monitoreo AMR deshabilitado desde la configuración RCS.');
  }
}

function renderRcsResolvedInfo(data) {
  if (!rcsResolvedInfo) return;
  if (!data) {
    rcsResolvedInfo.textContent = 'Sin configuración cargada.';
    return;
  }
  const lines = [
    `Base URL resuelta: ${data.resolved_base_url || 'Sin definir'}`,
    `Endpoint crear tarea: ${data.create_task_endpoint || '/rcs/task/create'}`,
    `Endpoint consulta tarea: ${data.query_task_status_endpoint || '/rcms/services/rest/hikRpcService/queryTaskStatus'}`,
    `Endpoint cancelar tarea: ${data.cancel_task_endpoint || '/rcms/services/rest/hikRpcService/cancelTask'}`,
    `Endpoint stop robot: ${data.stop_robot_endpoint || '/rcms/services/rest/hikRpcService/stopRobot'}`,
    `Endpoint resume robot: ${data.resume_robot_endpoint || '/rcms/services/rest/hikRpcService/resumeRobot'}`,
    `Endpoint estado AMR: ${data.agv_status_endpoint || '/rcms-dps/rest/queryAgvStatus'}`,
    `Endpoint posicion rack/pod: ${data.pod_position_endpoint || '/rcms/services/rest/hikRpcService/queryPodPosition'}`,
    `Endpoint consultar rack/posicion: ${data.rack_sync_query_endpoint || '/rcms/services/rest/hikRpcService/queryPodBerthAndMat'}`,
    `Endpoint vincular rack/posicion: ${data.rack_sync_bind_endpoint || '/rcms/services/rest/hikRpcService/bindPodAndBerth'}`,
    `Sync racks programada: ${Number(data.rack_sync_schedule_enabled ?? 0) === 1 ? `Habilitada ${data.rack_sync_schedule_time || '12:00'}` : 'Deshabilitada'}`,
    `Ultima sync programada: ${data.rack_sync_schedule_last_run_date || '-'}`,
    `Frecuencia monitoreo tarea: ${Number(data.task_monitor_interval_seconds ?? 3).toFixed(1)} s`,
    `Frecuencia monitoreo AGV: ${Number(data.agv_monitor_interval_seconds ?? 5).toFixed(1)} s`,
    `Habilitar mapShortName: ${Number(data.enable_map_short_name ?? 1) === 1 ? 'Sí' : 'No'}`,
    `Map Short Name estado AMR: ${data.map_short_name || 'AA'}`,
    `Habilitar mapCode: ${Number(data.enable_map_code ?? 0) === 1 ? 'Sí' : 'No'}`,
    `Map Code estado AMR: ${data.map_code || 'Vacío'}`,
    `Monitoreo AMR: ${Number(data.enable_amr_monitor ?? 1) === 1 ? 'Habilitado' : 'Deshabilitado'}`,
    `Payload estado AMR: reqCode y reqTime automáticos + mapShortName/mapCode configurables`,
    `Verify TLS: ${Number(data.verify_tls) === 1 ? 'Sí' : 'No'}`,
    `TokenCode: ${data.resolved_token_code || 'Vacío'}`,
    `Authorization: ${data.resolved_auth_header || 'Vacío'}`,
  ];
  rcsResolvedInfo.innerHTML = lines.map(x => `<div class="small">${x}</div>`).join('');
}

async function loadPublicRcsMonitorConfig() {
  try {
    const data = await fetchJson(API.rcsConfigPublic, { cache: 'no-store' });
    setRobotMonitorEnabled(Number(data.enable_amr_monitor ?? 1) === 1, { skipImmediateRefresh: true });
  } catch (err) {
    setRobotMonitorEnabled(true, { skipImmediateRefresh: true });
  }
}

async function adminLoadRcsConfig() {
  const data = await fetchJson(API.adminRcsConfigGet, { headers: fetchHeaders() });
  rcsBaseUrl.value = data.base_url || '';
  rcsEndpoint.value = data.create_task_endpoint || '/rcs/task/create';
  if (rcsQueryEndpoint) rcsQueryEndpoint.value = data.query_task_status_endpoint || '/rcms/services/rest/hikRpcService/queryTaskStatus';
  if (rcsCancelEndpoint) rcsCancelEndpoint.value = data.cancel_task_endpoint || '/rcms/services/rest/hikRpcService/cancelTask';
  if (rcsStopEndpoint) rcsStopEndpoint.value = data.stop_robot_endpoint || '/rcms/services/rest/hikRpcService/stopRobot';
  if (rcsResumeEndpoint) rcsResumeEndpoint.value = data.resume_robot_endpoint || '/rcms/services/rest/hikRpcService/resumeRobot';
  if (rcsAgvStatusEndpoint) rcsAgvStatusEndpoint.value = data.agv_status_endpoint || '/rcms-dps/rest/queryAgvStatus';
  if (rcsPodPositionEndpoint) rcsPodPositionEndpoint.value = data.pod_position_endpoint || '/rcms/services/rest/hikRpcService/queryPodPosition';
  if (rcsRackSyncQueryEndpoint) rcsRackSyncQueryEndpoint.value = data.rack_sync_query_endpoint || '/rcms/services/rest/hikRpcService/queryPodBerthAndMat';
  if (rcsRackSyncBindEndpoint) rcsRackSyncBindEndpoint.value = data.rack_sync_bind_endpoint || '/rcms/services/rest/hikRpcService/bindPodAndBerth';
  if (rcsRackSyncScheduleEnabled) rcsRackSyncScheduleEnabled.value = String(Number(data.rack_sync_schedule_enabled ?? 0));
  if (rcsRackSyncScheduleTime) rcsRackSyncScheduleTime.value = data.rack_sync_schedule_time || '12:00';
  if (rcsTaskMonitorInterval) rcsTaskMonitorInterval.value = String(Number(data.task_monitor_interval_seconds ?? 3));
  if (rcsAgvMonitorInterval) rcsAgvMonitorInterval.value = String(Number(data.agv_monitor_interval_seconds ?? 5));
  if (cleanupMinAgeMinutes) cleanupMinAgeMinutes.value = String(Number(data.cleanup_min_age_minutes ?? 30));
  if (forceReleaseMinAgeMinutes) forceReleaseMinAgeMinutes.value = String(Number(data.force_release_min_age_minutes ?? 20));
  if (cancelUndoAutoRecoveryEnabled) cancelUndoAutoRecoveryEnabled.value = String(Number(data.cancel_undo_auto_recovery_enabled ?? 1));
  if (cancelUndoAutoRecoveryMinAge) cancelUndoAutoRecoveryMinAge.value = String(Number(data.cancel_undo_auto_recovery_min_age_minutes ?? 5));
  if (rcsEnableMapShortName) rcsEnableMapShortName.value = String(Number(data.enable_map_short_name ?? 1));
  if (rcsMapShortName) rcsMapShortName.value = data.map_short_name || 'AA';
  if (rcsEnableMapCode) rcsEnableMapCode.value = String(Number(data.enable_map_code ?? 0));
  if (rcsMapCode) rcsMapCode.value = data.map_code || '';
  if (rcsEnableAmrMonitor) rcsEnableAmrMonitor.value = String(Number(data.enable_amr_monitor ?? 1));
  if (statusQueryBaseUrl) statusQueryBaseUrl.value = data.resolved_base_url || data.base_url || "";
  if (statusQueryEndpoint) statusQueryEndpoint.value = data.query_task_status_endpoint || '/rcms/services/rest/hikRpcService/queryTaskStatus';
  rcsTokenCode.value = '';
  rcsAuthHeader.value = '';
  rcsVerifyTls.value = String(Number(data.verify_tls || 0));
  setRobotMonitorEnabled(Number(data.enable_amr_monitor ?? 1) === 1, { skipImmediateRefresh: true });
  renderRcsResolvedInfo(data);
}

async function adminSaveRcsConfig() {
  const payload = {
    base_url: rcsBaseUrl.value.trim() || '',
    create_task_endpoint: rcsEndpoint.value.trim() || '/rcs/task/create',
    query_task_status_endpoint: (rcsQueryEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/queryTaskStatus',
    cancel_task_endpoint: (rcsCancelEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/cancelTask',
    stop_robot_endpoint: (rcsStopEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/stopRobot',
    resume_robot_endpoint: (rcsResumeEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/resumeRobot',
    agv_status_endpoint: (rcsAgvStatusEndpoint?.value || '').trim() || '/rcms-dps/rest/queryAgvStatus',
    pod_position_endpoint: (rcsPodPositionEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/queryPodPosition',
    rack_sync_query_endpoint: (rcsRackSyncQueryEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/queryPodBerthAndMat',
    rack_sync_bind_endpoint: (rcsRackSyncBindEndpoint?.value || '').trim() || '/rcms/services/rest/hikRpcService/bindPodAndBerth',
    rack_sync_schedule_enabled: safeFlagValue(rcsRackSyncScheduleEnabled, 0),
    rack_sync_schedule_time: (rcsRackSyncScheduleTime?.value || '12:00').trim() || '12:00',
    task_monitor_interval_seconds: safeNumberInput(rcsTaskMonitorInterval, 3),
    agv_monitor_interval_seconds: safeNumberInput(rcsAgvMonitorInterval, 5),
    cleanup_min_age_minutes: Math.max(1, Math.round(safeNumberInput(cleanupMinAgeMinutes, 30))),
    force_release_min_age_minutes: Math.max(1, Math.round(safeNumberInput(forceReleaseMinAgeMinutes, 20))),
    cancel_undo_auto_recovery_enabled: safeFlagValue(cancelUndoAutoRecoveryEnabled, 1),
    cancel_undo_auto_recovery_min_age_minutes: Math.max(1, Math.round(safeNumberInput(cancelUndoAutoRecoveryMinAge, 5))),
    enable_map_short_name: safeFlagValue(rcsEnableMapShortName, 1),
    map_short_name: (rcsMapShortName?.value || '').trim() || 'AA',
    enable_map_code: safeFlagValue(rcsEnableMapCode, 0),
    map_code: (rcsMapCode?.value || '').trim(),
    enable_amr_monitor: safeFlagValue(rcsEnableAmrMonitor, 1),
    verify_tls: safeFlagValue(rcsVerifyTls, 0),
    token_code: rcsTokenCode.value || '',
    auth_header: rcsAuthHeader.value || '',
  };
  const data = await fetchJson(API.adminRcsConfigSet, { method: 'POST', headers: { 'Content-Type': 'application/json', ...fetchHeaders() }, body: JSON.stringify(payload) });
  setRobotMonitorEnabled(Number(data.enable_amr_monitor ?? 1) === 1);
  renderRcsResolvedInfo(data);
  rcsTokenCode.value = '';
  rcsAuthHeader.value = '';
  rcsConfigMsg.textContent = 'Configuración RCS guardada.';
}

async function adminTestRcsConfig() {
  const data = await fetchJson(API.adminRcsConfigTest, { method: 'POST', headers: fetchHeaders() });
  rcsConfigMsg.textContent = data.message || (data.ok ? 'Configuración válida.' : 'Configuración incompleta.');
}
function renderPodPositionResult(data) {
  if (!podPositionResult) return;
  if (!data) {
    podPositionResult.textContent = 'Selecciona un rack y consulta su posicion en el RCS.';
    return;
  }
  const localCell = data.local_cell;
  const localRackCell = data.local_rack_cell;
  const lines = [
    `Rack / pod: ${data.rack_code || '-'}`,
    `Endpoint: ${data.endpoint || '-'}`,
    `Posicion RCS: ${data.rcs_position_code || 'No encontrada'}`,
    `Celda local por posicion RCS: ${localCell ? `${localCell.code || '-'} (${localCell.x}, ${localCell.y})` : 'Sin coincidencia'}`,
    `Celda local donde el sistema tiene asignado el rack: ${localRackCell ? `${localRackCell.code || '-'} (${localRackCell.x}, ${localRackCell.y})` : 'Sin asignacion local'}`,
    '',
    'Request:',
    JSON.stringify(data.request_payload || {}, null, 2),
    '',
    'Response:',
    JSON.stringify(data.response_payload || {}, null, 2),
  ];
  podPositionResult.textContent = lines.join('\n');
}

async function queryPodPosition() {
  const rackId = Number(podPositionRack?.value || 0);
  if (!rackId) throw new Error('Selecciona un rack.');
  if (podPositionMsg) podPositionMsg.textContent = 'Consultando posicion en RCS...';
  if (btnQueryPodPosition) btnQueryPodPosition.disabled = true;
  try {
    const data = await fetchJson(API.podPositionQuery, { method: 'POST', headers: { 'Content-Type': 'application/json', ...fetchHeaders() }, body: JSON.stringify({ rack_id: rackId }) });
    renderPodPositionResult(data);
    if (podPositionMsg) podPositionMsg.textContent = data.message || 'Consulta terminada.';
  } finally {
    if (btnQueryPodPosition) btnQueryPodPosition.disabled = !adminToken;
  }
}

function renderRackSyncPreview(data) {
  if (!rackSyncResult) return;
  lastRackSyncData = data || null;
  if (!data) {
    rackSyncResult.textContent = 'Etapa 3: consulta RCS y permite reasignar discrepancias manualmente con confirmacion.';
    updateRackSyncButtons();
    return;
  }
  const items = Array.isArray(data.items) ? data.items : [];
  const isQuery = data.mode === 'query_compare_only';
  const isBind = data.mode === 'bind_mismatches_manual';
  const lines = [
    `Modo: ${data.mode || 'preview_only'}`,
    isBind ? 'Bind RCS: ejecutado' : (isQuery ? 'Consulta RCS: ejecutada' : 'Consulta RCS: no ejecutada'),
    data.blocked == null ? '' : `Bloqueado por tareas activas: ${data.blocked ? 'Si' : 'No'}`,
    `Tareas activas: ${Number(data.active_tasks_count || 0)}`,
    `Endpoint consulta: ${data.query_endpoint || '-'}`,
    `Endpoint bind: ${data.bind_endpoint || '-'}`,
    data.map_short_name == null ? '' : `mapShortName: ${data.map_short_name || '(vacio)'}`,
    `Racks asignados: ${Number(data.total_assigned_racks || 0)}`,
    isBind
      ? `Discrepancias: ${Number(data.mismatch_count || 0)} | Intentados: ${Number(data.attempted_count || 0)} | Exitosos: ${Number(data.success_count || 0)} | Errores: ${Number(data.error_count || 0)} | Omitidos: ${Number(data.skipped_count || 0)}`
      : isQuery
      ? `Coinciden: ${Number(data.match_count || 0)} | Diferentes: ${Number(data.mismatch_count || 0)} | No encontrados: ${Number(data.missing_count || 0)} | Invalidos: ${Number(data.invalid_count || 0)} | Errores: ${Number(data.error_count || 0)}`
      : `Listos: ${Number(data.ready_count || 0)} | Con errores: ${Number(data.error_count || 0)}`,
    '',
    'Detalle:',
  ].filter(Boolean);
  for (const item of items.slice(0, 80)) {
    lines.push('');
    lines.push(`Rack ${item.rack_code || '(sin codigo)'} -> posicion local ${item.location_code || '(sin codigo)'} (${item.location_x}, ${item.location_y})`);
    if (isBind) {
      const rcsPosition = item.rcs_position_code || item.rcs_map_data_code || '(sin posicion previa)';
      lines.push(`RCS previo: ${rcsPosition}`);
      lines.push(`Estado comparacion: ${item.status} | accion: ${item.action || 'none'}`);
      lines.push(`Resultado bind: ${item.bind_status || 'not_sent'}${item.bind_error ? ` | error: ${item.bind_error}` : ''}`);
      if (item.bind_response_payload && Object.keys(item.bind_response_payload).length) {
        lines.push('Respuesta bindPodAndBerth:');
        lines.push(JSON.stringify(item.bind_response_payload || {}, null, 2));
      }
    } else if (isQuery) {
      const rcsPosition = item.rcs_position_code || item.rcs_map_data_code || '(sin posicion)';
      lines.push(`RCS reporta: ${rcsPosition}`);
      lines.push(`Estado: ${item.status} | accion: ${item.action || 'none'}${item.query_error ? ` | error: ${item.query_error}` : ''}`);
      if (item.status === 'mismatch') {
        lines.push('JSON futuro bindPodAndBerth para etapa posterior:');
        lines.push(JSON.stringify(item.bind_payload || {}, null, 2));
      }
    } else {
      lines.push(`Estado: ${item.status}${Array.isArray(item.errors) && item.errors.length ? ` | errores: ${item.errors.join(', ')}` : ''}`);
      lines.push('JSON consulta queryPodBerthAndMat:');
      lines.push(JSON.stringify(item.query_payload || {}, null, 2));
      lines.push('JSON futuro bindPodAndBerth si hay discrepancia:');
      lines.push(JSON.stringify(item.bind_payload || {}, null, 2));
    }
  }
  if (items.length > 80) lines.push(`\n... ${items.length - 80} racks adicionales no mostrados en pantalla.`);
  rackSyncResult.textContent = lines.join('\n');
  updateRackSyncButtons();
}

function updateRackSyncButtons() {
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = !adminToken;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = !adminToken;
  if (btnRackSyncHistory) btnRackSyncHistory.disabled = !adminToken;
  if (btnRackSyncBind) {
    const canBind = !!adminToken
      && lastRackSyncData?.mode === 'query_compare_only'
      && Number(lastRackSyncData?.mismatch_count || 0) > 0
      && Number(lastRackSyncData?.active_tasks_count || 0) === 0;
    btnRackSyncBind.disabled = !canBind;
  }
}

function renderRackSyncHistory(rows) {
  if (!rackSyncResult) return;
  const events = Array.isArray(rows) ? rows : [];
  lastRackSyncData = null;
  const lines = [
    'Historial reciente de sincronizacion rack-RCS',
    `Eventos: ${events.length}`,
    '',
  ];
  if (!events.length) {
    lines.push('Sin eventos registrados todavia.');
  }
  for (const event of events) {
    lines.push(`${event.created_at || ''} | ${event.action || '-'} | ${event.ok ? 'ok' : 'revision'} | ${Number(event.duration_ms || 0)} ms`);
    lines.push(`Racks: ${Number(event.total_assigned_racks || 0)} | match: ${Number(event.match_count || 0)} | mismatch: ${Number(event.mismatch_count || 0)} | missing: ${Number(event.missing_count || 0)} | invalid: ${Number(event.invalid_count || 0)} | intentados: ${Number(event.attempted_count || 0)} | exitosos: ${Number(event.success_count || 0)} | errores: ${Number(event.error_count || 0)} | activos: ${Number(event.active_tasks_count || 0)}`);
    if (event.message) lines.push(`Mensaje: ${event.message}`);
    lines.push('');
  }
  rackSyncResult.textContent = lines.join('\n');
  updateRackSyncButtons();
}

async function loadRackSyncPreview() {
  if (rackSyncMsg) rackSyncMsg.textContent = 'Generando vista previa local...';
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = true;
  try {
    const data = await fetchJson(API.adminRackSyncPreview, { headers: fetchHeaders() });
    renderRackSyncPreview(data);
    if (rackSyncMsg) rackSyncMsg.textContent = data.message || 'Vista previa generada. No se envio nada al RCS.';
  } finally {
    updateRackSyncButtons();
  }
}

async function loadRackSyncQuery() {
  if (rackSyncMsg) rackSyncMsg.textContent = 'Consultando RCS y comparando posiciones...';
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = true;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = true;
  try {
    const data = await fetchJson(API.adminRackSyncQuery, { method: 'POST', headers: fetchHeaders() });
    renderRackSyncPreview(data);
    if (rackSyncMsg) rackSyncMsg.textContent = data.message || 'Consulta RCS terminada. No se envio bind al RCS.';
  } finally {
    updateRackSyncButtons();
  }
}

async function loadRackSyncHistory() {
  if (rackSyncMsg) rackSyncMsg.textContent = 'Cargando historial de sincronizacion...';
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = true;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = true;
  if (btnRackSyncBind) btnRackSyncBind.disabled = true;
  if (btnRackSyncHistory) btnRackSyncHistory.disabled = true;
  try {
    const data = await fetchJson(`${API.adminRackSyncHistory}?limit=30`, { headers: fetchHeaders() });
    renderRackSyncHistory(data);
    if (rackSyncMsg) rackSyncMsg.textContent = 'Historial cargado.';
  } finally {
    updateRackSyncButtons();
  }
}

async function bindRackSyncMismatches() {
  const mismatchCount = Number(lastRackSyncData?.mismatch_count || 0);
  if (!mismatchCount) throw new Error('Primero consulta RCS y compara para detectar discrepancias.');
  if (Number(lastRackSyncData?.active_tasks_count || 0) > 0) throw new Error('Hay tareas activas. No se puede reasignar racks en RCS.');
  const confirmed = window.confirm(`Se enviara bindPodAndBerth para ${mismatchCount} rack(s) con discrepancia. Esta accion modifica la ubicacion del rack en RCS. ¿Continuar?`);
  if (!confirmed) return;
  if (rackSyncMsg) rackSyncMsg.textContent = 'Enviando bindPodAndBerth para discrepancias...';
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = true;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = true;
  if (btnRackSyncBind) btnRackSyncBind.disabled = true;
  try {
    const data = await fetchJson(API.adminRackSyncBind, { method: 'POST', headers: fetchHeaders() });
    renderRackSyncPreview(data);
    if (rackSyncMsg) rackSyncMsg.textContent = data.message || 'Reasignacion manual terminada.';
  } finally {
    updateRackSyncButtons();
  }
}

async function adminHideConfiguredRange() {
  await fetchJson(API.adminHideConfiguredRange, { method: "POST", headers: fetchHeaders() });
  await loadLocations();
  draw();
  rangeMsg.textContent = "Rango configurado ocultado.";
}
async function adminShowConfiguredRange() {
  await fetchJson(API.adminShowConfiguredRange, { method: "POST", headers: fetchHeaders() });
  await loadLocations();
  draw();
  rangeMsg.textContent = "Rango configurado mostrado.";
}
function getLocationById(locId) {
  if (locId == null || locId === "" || Number.isNaN(Number(locId))) return null;
  return locations.find(l => l && Number(l.id) === Number(locId)) || null;
}
function getLocationAtGrid(x, y) {
  const loc = locations[idx(x, y)];
  if (loc) return loc;
  return locations.find(l => l && Number(l.x) === Number(x) && Number(l.y) === Number(y)) || null;
}
function describeCell(loc) {
  if (!loc) return 'Sin seleccionar';
  const code = loc.code ? ` · ${loc.code}` : '';
  const rack = loc.rack_code ? ` · Rack ${loc.rack_code}` : '';
  const area = loc.area_name ? ` · Área ${loc.area_name}` : '';
  return `(${loc.x}, ${loc.y})${code}${rack}${area}`;
}
function renderDirectMoveSelection() {
  const sourceLoc = getLocationById(directMoveSourceCellId);
  const destLoc = getLocationById(directMoveDestinationCellId);
  if (directSourceCellLabel) directSourceCellLabel.textContent = describeCell(sourceLoc);
  if (directDestinationCellLabel) directDestinationCellLabel.textContent = describeCell(destLoc);
  if (btnDirectPickSource) btnDirectPickSource.textContent = directMovePickMode === 'source' ? 'Haz clic en una celda origen...' : 'Elegir origen en tabla';
  if (btnDirectPickDestination) btnDirectPickDestination.textContent = directMovePickMode === 'destination' ? 'Haz clic en una celda destino...' : 'Elegir destino en tabla';
}
function setDirectMoveCell(kind, loc) {
  if (!loc) return;
  const resolvedLoc = getLocationAtGrid(Number(loc.x), Number(loc.y)) || loc;
  const resolvedId = resolvedLoc?.id != null ? Number(resolvedLoc.id) : null;
  if (kind === 'source') {
    directMoveSourceCellId = resolvedId;
    directMsg.textContent = `Origen seleccionado: ${describeCell(resolvedLoc)}`;
  } else if (kind === 'destination') {
    directMoveDestinationCellId = resolvedId;
    directMsg.textContent = `Destino seleccionado: ${describeCell(resolvedLoc)}`;
  }
  directMovePickMode = null;
  renderDirectMoveSelection();
  draw();
}
function clearDirectMoveSelection() {
  directMoveSourceCellId = null;
  directMoveDestinationCellId = null;
  directMovePickMode = null;
  renderDirectMoveSelection();
  draw();
}
function directMovePayload() {
  return {
    source_cell_id: Number(directMoveSourceCellId ?? 0),
    destination_cell_id: Number(directMoveDestinationCellId ?? 0),
    priority: directPriority?.value || 'normal',
    agv_code: directAgvCode?.value?.trim() || null,
    task_typ: directTaskTyp?.value?.trim() || null,
    comment: directComment?.value?.trim() || null,
    created_by: 'operador',
  };
}
async function executeDirectMoveRequest() {
  if (!directMoveSourceCellId) throw new Error('Selecciona la celda origen en la tabla.');
  if (!directMoveDestinationCellId) throw new Error('Selecciona la celda destino en la tabla.');
  const payload = directMovePayload();
  let result;
  try {
    result = await fetchJson(API.directMoveExecute, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  } catch (err) {
    await refreshAfterDispatchError();
    throw err;
  }
  directMsg.textContent = `Orden ${result.order_code} creada y enviada al RCS para movimiento directo del rack ${result.rack_code}.`;
  await loadLocations();
  await loadCatalog();
  await loadMovementOrders(result.order_id);
  await selectCell(Number(result.source_cell.x), Number(result.source_cell.y));
  renderDirectMoveSelection();
  draw();
}
function fillCellForm(loc) {
  setBulkSelectMode(false);
  selectedCellTitle.textContent = `Celda (${selected.x}, ${selected.y})`;
  cellX.value = selected.x;
  cellY.value = selected.y;
  cellCode.value = loc?.code || "";
  cellStatus.value = String(loc?.status ?? 0);
  cellEnabled.value = String(loc?.enabled ?? 1);
  cellVisible.value = String(loc?.is_visible ?? 1);
  renderAreaOptions();
  renderRackOptions();
  cellArea.value = loc?.area_id ? String(loc.area_id) : "";
  cellRack.value = loc?.rack_id ? String(loc.rack_id) : "";
  const rack = getRackById(Number(loc?.rack_id || 0));
  const reservation = loc?.rack_id ? (getLiveReservationForRack(loc.rack_id) || getRackReservationSnapshot(rack || {})) : null;
  const reserved = isReservedState(reservation?.reservation_status || loc?.reservation_status);
  const rackLabel = (reservation?.reservation_rack_code || loc?.reservation_rack_code || loc?.rack_code || "").trim() || (reservation?.reservation_rack_id ? `ID ${reservation.reservation_rack_id}` : (loc?.reservation_rack_id ? `ID ${loc.reservation_rack_id}` : "Sin rack"));
  const taskLabel = reservationTaskLabel({
    reservation_task_identifier: reservation?.reservation_task_identifier || loc?.reservation_task_identifier || null,
    reservation_task_id: reservation?.reservation_task_id ?? loc?.reservation_task_id ?? null,
  });
  if (cellReservationState) cellReservationState.value = reserved ? "1" : "0";
  if (cellReservationRack) cellReservationRack.value = rackLabel;
  if (cellReservationTask) cellReservationTask.value = taskLabel;
  cellNote.value = loc?.note || "";
}
function selectedLocations() {
  return Array.from(multiSelectedLocationIds)
    .map(id => getLocationById(id))
    .filter(Boolean)
    .sort((a, b) => (Number(a.y) - Number(b.y)) || (Number(a.x) - Number(b.x)));
}
function setSelectKeepOption(selectEl, enabled, label = "Sin cambio") {
  if (!selectEl) return;
  const existing = Array.from(selectEl.options).find(o => o.value === KEEP_VALUE);
  if (enabled && !existing) {
    selectEl.insertBefore(new Option(label, KEEP_VALUE), selectEl.firstChild);
  } else if (!enabled && existing) {
    existing.remove();
  }
}
function setBulkSelectMode(enabled) {
  [cellStatus, cellEnabled, cellVisible, cellReservationState].forEach(el => setSelectKeepOption(el, enabled));
  setSelectKeepOption(cellArea, enabled);
  if (enabled) {
    [cellStatus, cellEnabled, cellVisible, cellArea, cellReservationState].forEach(el => { if (el) el.value = KEEP_VALUE; });
  }
}
function isMultiSelectionActive() {
  return multiSelectedLocationIds.size > 1;
}
function clearMultiSelection(redraw = true) {
  multiSelectedLocationIds.clear();
  setBulkSelectMode(false);
  if (redraw) {
    fillCellForm(getLocationAtGrid(selected.x, selected.y));
    setCellBulkControlsState();
    draw();
  }
}
function fillMultiCellForm() {
  const rows = selectedLocations();
  if (rows.length <= 1) {
    setBulkSelectMode(false);
    if (rows.length === 1) {
      selected = { x: Number(rows[0].x), y: Number(rows[0].y) };
      fillCellForm(rows[0]);
    }
    return;
  }
  setBulkSelectMode(true);
  selectedCellTitle.textContent = `${rows.length} celdas seleccionadas`;
  cellX.value = "";
  cellY.value = "";
  cellCode.value = "";
  cellRack.value = "";
  cellReservationRack.value = "Varios";
  cellReservationTask.value = "Varios";
  cellNote.value = "";
  renderRackOptions();
  cellRack.value = "";
}
function setCellBulkControlsState() {
  const multi = isMultiSelectionActive();
  [cellX, cellY, cellCode, cellRack, cellReservationRack, cellReservationTask, cellNote].forEach(el => {
    if (el) el.disabled = multi ? true : !adminToken;
  });
  [cellStatus, cellEnabled, cellVisible, cellArea, cellReservationState, btnSaveCell].forEach(el => {
    if (el) el.disabled = !adminToken;
  });
}
function setPrimarySelection(loc, { preserveMulti = false } = {}) {
  if (!loc) return;
  finishEditingLock("cell", { applyPending: false });
  selected = { x: Number(loc.x), y: Number(loc.y) };
  if (!preserveMulti) multiSelectedLocationIds.clear();
  if (isMultiSelectionActive()) fillMultiCellForm();
  else fillCellForm(loc);
  setCellBulkControlsState();
  renderDirectMoveSelection();
  draw();
}
async function selectCell(x, y, options = {}) {
  const loc = locations[idx(x, y)] || getLocationAtGrid(x, y);
  setPrimarySelection(loc || { x, y }, options);
}
function toggleMultiSelectedLocation(loc) {
  if (!loc?.id) return;
  const id = Number(loc.id);
  if (multiSelectedLocationIds.has(id)) multiSelectedLocationIds.delete(id);
  else multiSelectedLocationIds.add(id);
  if (!multiSelectedLocationIds.size) multiSelectedLocationIds.add(id);
  setPrimarySelection(loc, { preserveMulti: true });
}
async function saveMultiSelectedCells() {
  const rows = selectedLocations();
  if (rows.length <= 1) return saveSelectedCell();
  const patch = {};
  if (cellStatus?.value !== KEEP_VALUE) patch.status = Number(cellStatus.value || 0);
  if (cellEnabled?.value !== KEEP_VALUE) patch.enabled = Number(cellEnabled.value || 1);
  if (cellVisible?.value !== KEEP_VALUE) patch.is_visible = Number(cellVisible.value || 1);
  if (cellArea?.value !== KEEP_VALUE) patch.area_id = cellArea.value ? Number(cellArea.value) : null;
  const changeReservation = cellReservationState?.value !== KEEP_VALUE;
  if (!Object.keys(patch).length && !changeReservation) {
    cellMsg.textContent = "Selecciona al menos un campo para cambiar.";
    return;
  }
  for (const loc of rows) {
    if (Object.keys(patch).length) {
      const saved = await fetchJson(API.adminLocPatch(loc.x, loc.y), { method: "PATCH", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(patch) });
      locations[idx(saved.x, saved.y)] = saved;
    }
    if (changeReservation && loc.rack_id) {
      await fetchJson(API.adminRackReservation(loc.rack_id), { method: "PATCH", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ reserved: Number(cellReservationState.value || 0) }) });
    }
  }
  await loadLocations();
  finishEditingLock("cell", { applyPending: false });
  await refreshReservationUiState();
  const ids = new Set(rows.map(loc => Number(loc.id)));
  multiSelectedLocationIds = new Set(locations.filter(loc => loc && ids.has(Number(loc.id))).map(loc => Number(loc.id)));
  fillMultiCellForm();
  draw();
  cellMsg.textContent = `${rows.length} celdas actualizadas.`;
}
async function saveSelectedCell() {
  if (isMultiSelectionActive()) {
    await saveMultiSelectedCells();
    return;
  }
  const targetReserved = Number(cellReservationState?.value || 0) === 1 ? 1 : 0;
  const intendedRackId = Number(cellRack?.value || 0);
  if (targetReserved === 1 && !intendedRackId) {
    throw new Error("No hay rack en la celda para marcarlo como reservado.");
  }
  const payload = {
    code: cellCode.value || null,
    status: Number(cellStatus.value || 0),
    enabled: Number(cellEnabled.value || 1),
    is_visible: Number(cellVisible.value || 1),
    area_id: cellArea.value ? Number(cellArea.value) : null,
    rack_id: cellRack.value ? Number(cellRack.value) : null,
    note: cellNote.value || null,
  };
  let loc = await fetchJson(API.adminLocSave(selected.x, selected.y), { method: "PUT", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
  const rackIdForReservation = Number(loc?.rack_id || 0);
  if (rackIdForReservation) {
    await fetchJson(API.adminRackReservation(rackIdForReservation), { method: "PATCH", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ reserved: targetReserved }) });
    loc = await fetchJson(API.adminLocPatch(selected.x, selected.y), { headers: { ...fetchHeaders() } });
  }
  locations[idx(selected.x, selected.y)] = loc;
  finishEditingLock("cell", { applyPending: false });
  await refreshReservationUiState();
  cellMsg.textContent = `Celda (${selected.x}, ${selected.y}) guardada.`;
}
async function saveFreeLocationLayout(loc) {
  if (!loc?.id) return null;
  const payload = {
    free_enabled: 1,
    free_x: Number(loc.free_x || 0),
    free_y: Number(loc.free_y || 0),
    free_w: Math.max(4, Number(loc.free_w || BASE_CELL)),
    free_h: Math.max(4, Number(loc.free_h || BASE_CELL)),
  };
  const saved = await fetchJson(API.adminLocFreeLayout(loc.id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...fetchHeaders() },
    body: JSON.stringify(payload),
  });
  locations[idx(saved.x, saved.y)] = saved;
  return saved;
}
async function saveArea() {
  const payload = { code: areaCode.value.trim(), name: areaName.value.trim(), description: areaDescription.value.trim() || null, matter_area: areaMatterArea?.value?.trim() || null, color: areaColor.value || "#4f46e5", area_type: areaType.value.trim() || "almacen", is_active: Number(areaActive.value || 1), priority: Number(areaPriority.value || 0) };
  const url = areaId.value ? API.adminArea(Number(areaId.value)) : API.adminAreas;
  const method = areaId.value ? "PUT" : "POST";
  const wasUpdate = !!areaId.value;
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
  finishEditingLock("areas", { applyPending: false });
  await loadCatalog();
  areaMsg.textContent = wasUpdate ? "Área actualizada." : "Área creada.";
  if (!wasUpdate) clearAreaForm();
}
async function saveMaterial() {
  const payload = { code: materialCode.value.trim(), name: materialName.value.trim(), description: materialDescription.value.trim() || null, color: materialColor.value || randomMaterialColor(), is_active: Number(materialActive.value || 1) };
  const url = materialId.value ? API.adminMaterial(Number(materialId.value)) : API.adminMaterials;
  const method = materialId.value ? "PUT" : "POST";
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
  await loadCatalog();
  materialMsg.textContent = materialId.value ? "Material actualizado." : "Material creado.";
  if (!materialId.value) clearMaterialForm();
}

async function deleteArea() {
  if (!areaId.value) throw new Error('Selecciona un área.');
  const item = catalog.areas.find(a => Number(a.id) === Number(areaId.value));
  const name = item ? `${item.code} - ${item.name}` : `ID ${areaId.value}`;
  if (!window.confirm(`¿Deseas borrar el área ${name}?`)) return;
  await fetchJson(API.adminArea(Number(areaId.value)), { method: "DELETE", headers: { ...fetchHeaders() } });
  finishEditingLock("areas", { applyPending: false });
  await loadCatalog();
  clearAreaForm();
  areaMsg.textContent = 'Área eliminada.';
}

async function deleteMaterial() {
  if (!materialId.value) throw new Error('Selecciona un material.');
  const item = catalog.materials.find(m => Number(m.id) === Number(materialId.value));
  const name = item ? `${item.code} - ${item.name}` : `ID ${materialId.value}`;
  if (!window.confirm(`¿Deseas borrar el material ${name}?`)) return;
  await fetchJson(API.adminMaterial(Number(materialId.value)), { method: "DELETE", headers: { ...fetchHeaders() } });
  await loadCatalog();
  clearMaterialForm();
  materialMsg.textContent = 'Material eliminado.';
}

async function deleteRack() {
  if (!rackId.value) throw new Error('Selecciona un rack.');
  const item = catalog.racks.find(r => Number(r.id) === Number(rackId.value));
  const name = item ? `${item.code}${item.name ? ' - ' + item.name : ''}` : `ID ${rackId.value}`;
  if (!window.confirm(`¿Deseas borrar el rack ${name}?`)) return;
  await fetchJson(API.adminRack(Number(rackId.value)), { method: "DELETE", headers: { ...fetchHeaders() } });
  await loadCatalog();
  clearRackForm();
  rackMsg.textContent = 'Rack eliminado.';
}

function fifoPayload() {
  return {
    source_area_id: fifoSourceArea.value ? Number(fifoSourceArea.value) : null,
    destination_area_id: fifoDestinationArea.value ? Number(fifoDestinationArea.value) : null,
    material_group_id: fifoMaterial.value ? Number(fifoMaterial.value) : null,
    priority: fifoPriority.value || "normal",
    agv_code: fifoAgvCode?.value?.trim() || null,
    task_typ: fifoTaskTyp?.value?.trim() || "",
    comment: fifoComment.value.trim() || null,
    created_by: "operador",
  };
}
function renderFifoPreview(preview) {
  lastFifoPreview = preview;
  if (!preview) {
    fifoPreviewBox.innerHTML = `<div class="small">Sin validación todavía.</div>`;
    return;
  }
  fifoPreviewBox.innerHTML = `
    <div class="small"><b>Rack FIFO:</b> ${preview.rack.code}</div>
    <div class="small"><b>Material:</b> ${preview.material.code} - ${preview.material.name}</div>
    <div class="small"><b>Origen:</b> ${preview.source_area.code} · celda ${preview.source_cell.code || `(${preview.source_cell.x}, ${preview.source_cell.y})`}</div>
    <div class="small"><b>Destino:</b> ${preview.destination_area.code} · celda ${preview.destination_cell.code || `(${preview.destination_cell.x}, ${preview.destination_cell.y})`}</div>
    <div class="small"><b>FIFO ingreso:</b> ${preview.rack.fifo_entered_at ? new Date(preview.rack.fifo_entered_at).toLocaleString() : 'Sin fecha FIFO'}</div>
  `;
}
async function validateFifoRequest() {
  const payload = fifoPayload();
  const preview = await fetchJson(API.fifoValidate, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  renderFifoPreview(preview);
  fifoMsg.textContent = preview.message || "Selección validada.";
}
async function executeFifoRequest() {
  const payload = fifoPayload();
  let result;
  try {
    result = await fetchJson(API.fifoExecute, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  } catch (err) {
    await refreshAfterDispatchError();
    throw err;
  }
  fifoMsg.textContent = `Orden ${result.order_code} creada y enviada al RCS para ${result.rack_code}.`;
  await loadLocations();
  await loadCatalog();
  await loadMovementOrders(result.order_id);
  renderFifoPreview(null);
  await selectCell(Number(result.source_cell.x), Number(result.source_cell.y));
  draw();
}

async function refreshAfterDispatchError() {
  await Promise.all([loadMovementOrders(), loadCatalog(), loadLocations()]);
  draw();
}

function isFifoChainOrder(order) {
  return normalizeRouteMode(order?.route_mode) === "fifo_chain"
    || !!(order?.fifo_chain_group_id || order?.trmx_group_id);
}

function fifoChainOrderInfo(order) {
  if (!isFifoChainOrder(order)) return null;
  const step = Number(order?.fifo_chain_step ?? order?.trmx_step ?? 0) || null;
  const total = Number(order?.fifo_chain_total_steps ?? order?.trmx_total_steps ?? 2) || 2;
  const groupId = String(order?.fifo_chain_group_id || order?.trmx_group_id || "").trim();
  const groupShort = groupId ? groupId.slice(0, 8) : "-";
  const status = String(order?.fifo_chain_status || order?.trmx_status || "").trim();
  const parentOrderId = order?.fifo_chain_parent_order_id ?? order?.trmx_parent_order_id;
  return { step, total, groupShort, status, parentOrderId };
}

function fifoChainOrderSummaryHtml(order) {
  const info = fifoChainOrderInfo(order);
  if (!info) return "";
  const parts = [
    "Flujo doble FIFO",
    `Paso ${info.step || "-"}/${info.total || "-"}`,
    `Grupo ${info.groupShort}`,
  ];
  if (info.status) parts.push(`Estado ${info.status}`);
  if (info.step === 2 && info.parentOrderId) parts.push(`Generada por orden ${info.parentOrderId}`);
  return `<div class="fifo-chain-history-badge">${escapeHtml(parts.join(" · "))}</div>`;
}

function fifoChainDeveloperNoteHtml(order) {
  const info = fifoChainOrderInfo(order);
  if (!info) return "";
  if (info.step === 1) {
    return `<div class="fifo-chain-history-note">Al simular completed se creará/despachará el paso 2 automáticamente.</div>`;
  }
  if (info.step === 2) {
    return `<div class="fifo-chain-history-note">Último paso del Flujo doble FIFO. No se crearán más órdenes.</div>`;
  }
  return "";
}

function renderOrdersList() {
  if (!ordersList) return;
  if (!movementOrders.length) {
    ordersList.innerHTML = `<div class="small">Sin tareas registradas.</div>`;
    renderSelectedOrderDetail(null);
    return;
  }
  ordersList.innerHTML = movementOrders.map(order => {
    const active = order.order_id === selectedOrderId ? ' style="border-color:#60a5fa;background:rgba(96,165,250,.12);"' : '';
    const areaText = `${order.source_area_name || order.source_area_id} → ${order.destination_area_name || order.destination_area_id}`;
    const auditText = order.cancel_source ? `${order.status} · ${order.cancel_source}` : order.status;
    const canCancel = historyOrderCanCancel(order);
    const cancelTitle = canCancel ? 'Cancelar tarea' : historyOrderUnavailableReason(order);
    const cancelDisabled = canCancel ? '' : ' disabled';
    return `<div class="order-history-row">
      <button type="button" class="list-item order-list-main" data-order-id="${order.order_id}"${active}>
        <div><b>${order.order_code}</b></div>
        <div class="small">${auditText} · ${order.rack_code}</div>
        <div class="small">Orden: ${order.order_type || '-'} · AGV: ${order.agv_code || '-'} · Tipo tarea: ${order.task_typ || '-'}</div>
        <div class="small">${areaText}</div>
        ${fifoChainOrderSummaryHtml(order)}
        <div class="small">${new Date(order.created_at).toLocaleString()}</div>
      </button>
      <button type="button" class="btn danger order-cancel-btn" data-cancel-order-id="${order.order_id}" title="${escapeHtml(cancelTitle || '')}"${cancelDisabled}>Cancelar</button>
    </div>`;
  }).join('');
  ordersList.querySelectorAll('[data-order-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      selectedOrderId = Number(btn.dataset.orderId);
      renderOrdersList();
      renderSelectedOrderDetail(movementOrders.find(x => x.order_id === selectedOrderId) || null);
    });
  });
  ordersList.querySelectorAll('[data-cancel-order-id]').forEach(btn => {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      selectedOrderId = Number(btn.dataset.cancelOrderId);
      const order = movementOrders.find(x => x.order_id === selectedOrderId) || null;
      renderOrdersList();
      renderSelectedOrderDetail(order);
      try {
        openHistoryUndoModal(order);
      } catch (err) {
        orderMsg.textContent = `Error: ${String(err)}`;
      }
    });
  });
  renderSelectedOrderDetail(movementOrders.find(x => x.order_id === selectedOrderId) || null);
}


function currentStatusQueryMode() { return "manual"; }

function currentEditedStatusQueryText() {
  if (!orderStatusQueryRequestBox) return '';
  return ('value' in orderStatusQueryRequestBox ? orderStatusQueryRequestBox.value : orderStatusQueryRequestBox.textContent || '').trim();
}

function parseCurrentEditedStatusQueryJson() {
  const text = currentEditedStatusQueryText();
  if (!text) throw new Error('No hay JSON de consulta para enviar.');
  let payload = null;
  try { payload = JSON.parse(text); } catch (_) { throw new Error('El JSON de consulta no es válido.'); }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('El JSON de consulta debe ser un objeto.');
  return payload;
}

function formatStatusQueryEditor() {
  if (!orderStatusQueryRequestBox) return;
  const payload = parseCurrentEditedStatusQueryJson();
  orderStatusQueryRequestBox.value = JSON.stringify(payload, null, 2);
}

async function copyStatusQueryEditor() {
  const text = currentEditedStatusQueryText();
  if (!text) throw new Error('No hay JSON para copiar.');
  if (!navigator.clipboard?.writeText) throw new Error('El portapapeles no está disponible en este navegador.');
  await navigator.clipboard.writeText(text);
}

function clearStatusQueryEditor() {
  if (!orderStatusQueryRequestBox) return;
  orderStatusQueryRequestBox.value = '{\n  \n}';
  orderStatusQueryRequestBox.focus();
}

function formatNowForRcs() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

async function refreshStatusQueryEditor(force = false) {
  if (!orderStatusQueryRequestBox) return;
  orderStatusQueryRequestBox.readOnly = false;
  orderStatusQueryRequestBox.placeholder = 'Escribe aquí cualquier JSON para enviarlo desde la consola debug.';
  if (force && !currentEditedStatusQueryText()) {
    orderStatusQueryRequestBox.value = `{\n  \n}`;
  }
}


function renderOrderJson(payload, source = "generated", force = false) {
  selectedOrderJsonPayload = payload || null;
  selectedOrderJsonSource = source || "generated";
  if (orderJsonSource) orderJsonSource.textContent = payload ? `Fuente: ${selectedOrderJsonSource === 'edited' ? 'JSON editado' : 'JSON generado'}` : 'Sin JSON cargado';
  if (!orderJsonBox) return;
  if (!force && isEditingOrderJson) return;
  orderJsonBox.value = payload ? JSON.stringify(payload, null, 2) : '';
  if (!payload) orderJsonBox.placeholder = 'Selecciona una tarea del historial para ver y editar el JSON.';
}

function renderOrderResponse(payload) {
  selectedOrderResponsePayload = payload || null;
  if (!orderResponseBox) return;
  orderResponseBox.textContent = payload ? JSON.stringify(payload, null, 2) : 'Selecciona una tarea y envíala al RCS para ver la respuesta JSON.';
}

function formatStatusLogEntries(entries) {
  if (!Array.isArray(entries) || !entries.length) return '<div class="small">Aún no hay mensajes.</div>';
  const normalized = [...entries].sort((a, b) => {
    const aTime = new Date(a?.created_at || a?.arrived_at || 0).getTime() || 0;
    const bTime = new Date(b?.created_at || b?.arrived_at || 0).getTime() || 0;
    if (bTime !== aTime) return bTime - aTime;
    const aId = Number(a?.id || 0) || 0;
    const bId = Number(b?.id || 0) || 0;
    return bId - aId;
  });
  return normalized.map((entry) => {
    const when = entry?.created_at || entry?.arrived_at;
    const whenText = when ? new Date(when).toLocaleString() : 'Sin fecha';
    const direction = String(entry?.direction || '').toLowerCase() === 'received' ? 'RECIBIDO' : 'ENVIADO';
    const module = entry?.module || entry?.kind || 'rcs';
    const base = entry?.base_url || '';
    const endpoint = entry?.endpoint || '';
    const payload = entry?.payload ?? entry?.response ?? entry?.request ?? null;
    const payloadText = payload ? JSON.stringify(payload, null, 2) : 'Sin payload';
    const message = entry?.message || 'Sin mensaje';
    return `
      <div class="console-entry">
        <div class="small"><b>${direction}</b> · ${escapeHtml(module)}</div>
        <div class="small"><b>Fecha/hora:</b> ${escapeHtml(whenText)}</div>
        <div class="small"><b>URL base:</b> ${escapeHtml(base || '-')}</div>
        <div class="small"><b>Endpoint:</b> ${escapeHtml(endpoint || '-')}</div>
        <div class="small"><b>Mensaje:</b> ${escapeHtml(message)}</div>
        <pre class="json-pre">${escapeHtml(payloadText)}</pre>
      </div>
    `;
  }).join('');
}

function renderOrderStatusQuery(_requestPayload, _responsePayload, logEntries = [], _force = false) {
  if (!orderStatusQueryResponseBox) return;
  if (debugConsoleHoverPaused) {
    debugConsolePendingEntries = Array.isArray(logEntries) ? [...logEntries] : [];
    return;
  }
  debugConsolePendingEntries = null;
  orderStatusQueryResponseBox.innerHTML = formatStatusLogEntries(logEntries);
}


async function loadSelectedOrderJson() {
  if (!selectedOrderId) {
    renderOrderJson(null);
    if (btnCopyOrderJson) btnCopyOrderJson.disabled = true;
    return;
  }
  const result = await fetchJson(API.movementOrderJson(selectedOrderId));
  renderOrderJson(result.payload || null, result.source || 'generated');
  if (btnCopyOrderJson) btnCopyOrderJson.disabled = !(result && result.payload);
}


function currentEditedJsonText() {
  return orderJsonBox?.value?.trim() || '';
}

function parseCurrentEditedJson() {
  const text = currentEditedJsonText();
  if (!text) throw new Error('No hay JSON para guardar o enviar.');
  let payload = null;
  try { payload = JSON.parse(text); } catch (_) { throw new Error('El JSON editado no es válido.'); }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('El JSON debe ser un objeto.');
  return payload;
}

async function saveSelectedOrderJsonOverride() {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const payload = parseCurrentEditedJson();
  const result = await fetchJson(API.movementOrderJsonSave(selectedOrderId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload }) });
  renderOrderJson(result.payload || null, result.source || 'edited');
  orderMsg.textContent = 'JSON editado guardado. Ese será el JSON enviado al RCS.';
}

async function resetSelectedOrderJsonOverride() {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const result = await fetchJson(API.movementOrderJsonReset(selectedOrderId), { method: 'POST' });
  renderOrderJson(result.payload || null, result.source || 'generated');
  orderMsg.textContent = 'Se restauró el JSON generado automáticamente.';
}

async function loadSelectedOrderResponse() {
  if (!selectedOrderId) {
    renderOrderResponse(null);
    if (btnRefreshOrderResponse) btnRefreshOrderResponse.disabled = true;
    return;
  }
  const result = await fetchJson(API.movementOrderDispatchResponse(selectedOrderId));
  renderOrderResponse(result);
  if (btnRefreshOrderResponse) btnRefreshOrderResponse.disabled = false;
}

async function dispatchSelectedOrder() {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const payload = parseCurrentEditedJson();
  await fetchJson(API.movementOrderJsonSave(selectedOrderId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload }) });
  const result = await fetchJson(API.movementOrderDispatch(selectedOrderId), { method: 'POST' });
  renderOrderResponse(result);
  orderMsg.textContent = result.dispatch_status === 'success'
    ? `Orden ${result.order_code} enviada al RCS. Remote task: ${result.remote_task_code || 'sin dato'}.`
    : `Error al enviar ${result.order_code}: ${result.rcs_message || 'sin detalle'}.`;
  if (result.dispatch_status === 'success') {
    await loadMovementOrders(selectedOrderId);
  } else {
    await refreshAfterDispatchError();
  }
}

function historyOrderUnavailableReason(order) {
  if (!order) return '';
  const status = String(order.status || '').trim();
  if (status === 'dispatch_error') return 'El RCS no aceptó la tarea. El rack fue liberado localmente.';
  if (status === 'forced_local_closed') return 'Orden cerrada localmente por recuperación avanzada.';
  if (status === 'cancelled') return 'Orden ya cancelada.';
  if (status === 'completed') return 'Orden completada.';
  if (status === 'undone') return 'Orden revertida.';
  if (['in_progress', 'dispatched'].includes(status)) {
    const createdAt = new Date(order.created_at || 0).getTime();
    const ageMinutes = createdAt ? Math.floor((Date.now() - createdAt) / 60000) : 0;
    if (ageMinutes >= 20) return 'Usar Diagnóstico y limpieza → Recuperación avanzada.';
  }
  if (!order.can_undo && !order.can_simulate_complete) return 'Esta orden ya no permite cancelación ni simulación desde historial.';
  if (!order.can_undo) return 'Esta orden no permite cancelación desde historial.';
  if (!order.can_simulate_complete) return 'Esta orden no permite simulación desde historial.';
  return '';
}

function historyOrderCanCancel(order) {
  const status = String(order?.status || '').trim().toLowerCase();
  return status !== 'completed' && !!order?.can_undo;
}

function renderSelectedOrderDetail(order) {
  if (!orderDetailBox) return;
  if (!order) {
    orderDetailBox.innerHTML = `<div class="small">Selecciona una tarea del historial.</div>`;
    if (btnSimulateComplete) btnSimulateComplete.disabled = true;
    if (btnUndoOrder) btnUndoOrder.disabled = true;
    if (btnSimulateComplete) btnSimulateComplete.title = '';
    if (btnUndoOrder) btnUndoOrder.title = '';
    if (btnDeleteOrder) btnDeleteOrder.disabled = true;
    return;
  }
  const currentCell = order.current_cell ? (order.current_cell.code || `(${order.current_cell.x}, ${order.current_cell.y})`) : 'Sin ubicación';
  const unavailableReason = historyOrderUnavailableReason(order);
  const unavailableHtml = unavailableReason ? `<div class="small"><b>Acciones historial:</b> ${escapeHtml(unavailableReason)}</div>` : '';
  const fifoChainSummary = fifoChainOrderSummaryHtml(order);
  const fifoChainNote = fifoChainDeveloperNoteHtml(order);
  orderDetailBox.innerHTML = `
    <div class="small"><b>Orden:</b> ${order.order_code}</div>
    <div class="small"><b>Estado:</b> ${order.status}</div>
    ${fifoChainSummary}
    ${fifoChainNote}
    ${unavailableHtml}
    <div class="small"><b>Tipo orden:</b> ${order.order_type || '-'}</div>
    <div class="small"><b>Rack:</b> ${order.rack_code}</div>
    <div class="small"><b>Material:</b> ${order.material_group_name || order.material_group_id}</div>
    <div class="small"><b>Origen:</b> ${order.source_area_name || order.source_area_id} · ${order.source_cell.code || `(${order.source_cell.x}, ${order.source_cell.y})`}</div>
    <div class="small"><b>Destino:</b> ${order.destination_area_name || order.destination_area_id} · ${order.destination_cell.code || `(${order.destination_cell.x}, ${order.destination_cell.y})`}</div>
    <div class="small"><b>Ubicación actual rack:</b> ${currentCell}</div>
    <div class="small"><b>Prioridad:</b> ${order.priority}</div>
    <div class="small"><b>Dispatch RCS:</b> ${order.dispatch_status || 'not_sent'}</div>
    <div class="small"><b>Remote task:</b> ${order.remote_task_code || 'Sin enviar'}</div>
    <div class="small"><b>Req code:</b> ${order.req_code || 'Sin dato'}</div>
    <div class="small"><b>Estatus remoto:</b> ${order.rcs_status || 'Sin consultar'}</div>
    <div class="small"><b>Auditoría:</b> ${order.cancel_source || '-'}${order.cancel_reason ? ` · ${order.cancel_reason}` : ''}${order.closed_by ? ` · por ${order.closed_by}` : ''}${order.release_source ? ` · rack ${order.release_source}` : ''}</div>
    <div class="small"><b>Última consulta:</b> ${order.status_query_checked_at ? new Date(order.status_query_checked_at).toLocaleString() : 'Sin consultar'}</div>
    <div class="small"><b>Mensaje RCS:</b> ${order.rcs_message || 'Sin respuesta'}</div>
    <div class="small"><b>Comentario:</b> ${order.comment || 'Sin comentario'}</div>
    <div class="small"><b>Creada:</b> ${new Date(order.created_at).toLocaleString()}</div>
  `;
  const canCancel = historyOrderCanCancel(order);
  if (btnSimulateComplete) btnSimulateComplete.disabled = !order.can_simulate_complete;
  if (btnUndoOrder) btnUndoOrder.disabled = !canCancel;
  if (btnSimulateComplete) btnSimulateComplete.title = order.can_simulate_complete ? '' : unavailableReason;
  if (btnUndoOrder) btnUndoOrder.title = canCancel ? '' : unavailableReason;
  if (btnDeleteOrder) btnDeleteOrder.disabled = !adminToken;
}

async function loadDebugConsoleLog() {
  debugConsoleEvents = await fetchJson(`${API.rcsDebugLog}?limit=300`);
  renderOrderStatusQuery(null, null, debugConsoleEvents, true);
}

async function runMonitorNow() {
  const textPayload = currentEditedStatusQueryText();
  if (!textPayload) throw new Error('Escribe un JSON para enviarlo desde la consola.');
  const payload = parseCurrentEditedStatusQueryJson();
  const base_url = statusQueryBaseUrl?.value?.trim() || '';
  const endpoint = statusQueryEndpoint?.value?.trim() || '';
  const result = await fetchJson(API.rcsDebugSend, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload, base_url, endpoint }),
  });
  orderMsg.textContent = `JSON libre enviado a ${result.base_url || '-'}${result.endpoint || ''}.`;
  await loadDebugConsoleLog();
}


async function loadMovementOrders(preferredOrderId = null, skipMonitor = false) {
  const includeRobotMonitor = robotMonitorEnabled && !skipMonitor ? 1 : 0;
  const snapshot = await fetchJson(`${API.runtimeSnapshot}?debug_limit=300&include_robot_monitor=${includeRobotMonitor}`, { cache: 'no-store' });
  applyRuntimeSnapshotData(snapshot, preferredOrderId);
}

async function autoRefreshRuntimeSections(preferredOrderId = null) {
  if (runtimeSocketConnected) return;
  if (runtimeRefreshInFlight) return;
  runtimeRefreshInFlight = true;
  try {
    const includeRobotMonitor = robotMonitorEnabled ? 1 : 0;
    const snapshot = await fetchJson(`${API.runtimeSnapshot}?debug_limit=300&include_robot_monitor=${includeRobotMonitor}`, { cache: 'no-store' });
    applyRuntimeSnapshotData(snapshot, preferredOrderId);
  } finally {
    runtimeRefreshInFlight = false;
  }
}


async function simulateSelectedOrderComplete() {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const order = movementOrders.find(x => x.order_id === selectedOrderId) || null;
  if (order && !order.can_simulate_complete) throw new Error(historyOrderUnavailableReason(order) || 'Esta orden no permite simulación desde historial.');
  const result = await fetchJson(API.movementOrderSimulateComplete(selectedOrderId), { method: 'POST' });
  orderMsg.textContent = `Orden ${result.order_code} finalizada de forma simulada.`;
  await loadLocations();
  await loadCatalog();
  await loadMovementOrders(result.order_id);
  await selectCell(Number(result.destination_cell.x), Number(result.destination_cell.y));
  draw();
}

async function undoSelectedOrder(returnAreaId = null, matterArea = '') {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const order = movementOrders.find(x => x.order_id === selectedOrderId) || null;
  if (order && !historyOrderCanCancel(order)) throw new Error(historyOrderUnavailableReason(order) || 'Esta orden no permite cancelacion desde historial.');
  const body = returnAreaId ? { return_to_area: true, return_area_id: Number(returnAreaId), matter_area: matterArea || '' } : { return_to_area: true };
  const result = await fetchJson(API.movementOrderUndo(selectedOrderId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  orderMsg.textContent = `Orden ${result.order_code} cancelada desde historial con cancelTask forceCancel=1 y reversa local del almacén. Nuevo estado: ${result.status}.`;
  await loadLocations();
  await loadCatalog();
  await loadMovementOrders(result.order_id);
  const focusCell = result.current_cell || result.source_cell;
  await selectCell(Number(focusCell.x), Number(focusCell.y));
  draw();
}

async function deleteSelectedOrder() {
  if (!adminToken) throw new Error('Solo el admin puede borrar tareas del historial.');
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const order = movementOrders.find(x => x.order_id === selectedOrderId) || null;
  const orderLabel = order?.order_code || `ID ${selectedOrderId}`;
  const confirmed = window.confirm(`¿Seguro que deseas borrar por completo la tarea ${orderLabel}? Esta acción la eliminará del historial y del monitoreo de estatus.`);
  if (!confirmed) return;
  const result = await fetchJson(API.adminDeleteMovementOrder(selectedOrderId), { method: 'DELETE', headers: fetchHeaders() });
  orderMsg.textContent = result?.message || `Orden ${orderLabel} borrada.`;
  await loadMovementOrders();
}

async function saveRack() {
  const desiredReservation = Number(rackReservationState?.value || 0) === 1 ? "1" : "0";
  let rackStatusValue = rackStatus.value.trim() || "available";
  if (desiredReservation === "1") rackStatusValue = "reserved";
  if (desiredReservation === "0") rackStatusValue = "available";
  const payload = { code: rackCode.value.trim(), name: rackName.value.trim() || null, status: rackStatusValue, material_group_id: rackMaterial.value ? Number(rackMaterial.value) : null, lot: rackLot.value.trim() || null, quantity: Number(rackQty.value || 0), manufacturer_code: rackMfgCode.value.trim() || null, fifo_entered_at: fromLocalInputValue(rackFifo.value), last_moved_at: fromLocalInputValue(rackMoved.value), comment: rackComment.value.trim() || null, custom_fields: getRackCustomFieldRows() };
  const url = rackId.value ? API.adminRack(Number(rackId.value)) : API.adminRacks;
  const method = rackId.value ? "PUT" : "POST";
  const savedRack = await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
  const targetRackId = Number(savedRack?.id || rackId.value || 0);
  if (targetRackId && desiredReservation !== String(rackReservationOriginal || "0")) {
    await fetchJson(API.adminRackReservation(targetRackId), { method: "PATCH", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ reserved: Number(desiredReservation) }) });
  }
  rackReservationOriginal = desiredReservation;
  await refreshReservationUiState();
  rackMsg.textContent = rackId.value ? "Rack actualizado." : "Rack creado.";
  if (!rackId.value) clearRackForm();
}

canvas.addEventListener("mousedown", (e) => {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  if (e.button === 0 && multiSelectMode && !operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode) {
    e.preventDefault();
    multiSelectBox = { startX: mx, startY: my, endX: mx, endY: my };
    draw();
    return;
  }
  if (e.button === 1 || e.button === 2) {
    e.preventDefault(); dragging = true; dragStart = { x: e.clientX, y: e.clientY, offX: cam.offX, offY: cam.offY }; return;
  }
  if (e.button !== 0) return;
  const additiveSelect = e.ctrlKey || e.metaKey;
  if (isFreeLayoutMode()) {
    const loc = hitTestFreeLocation(mx, my);
    if (!loc) {
      if (isMultiSelectionActive()) clearMultiSelection(true);
      return;
    }
    const locAlreadySelected = multiSelectedLocationIds.has(Number(loc.id));
    if (operatorButtonPickMode) {
      setOperatorButtonCell(operatorButtonPickMode, loc);
    } else if (operatorActionPickMode) {
      setOperatorActionCell(operatorActionPickMode, loc);
    } else if (directMovePickMode) {
      setDirectMoveCell(directMovePickMode, loc);
    }
    if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && additiveSelect) {
      toggleMultiSelectedLocation(loc);
    } else if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && isMultiSelectionActive() && locAlreadySelected) {
      setPrimarySelection(loc, { preserveMulti: true });
    } else if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && isMultiSelectionActive() && !locAlreadySelected) {
      clearMultiSelection(false);
      selectCell(Number(loc.x), Number(loc.y));
    } else {
      selectCell(Number(loc.x), Number(loc.y));
    }
    if (isFreeLayoutEditing()) {
      const world = canvasToWorld(mx, my);
      const freeRect = freeRectForLocation(loc);
      const group = multiSelectedLocationIds.has(Number(loc.id)) && multiSelectedLocationIds.size > 1
        ? selectedLocations()
        : [loc];
      freeLayoutDrag = {
        locationId: Number(loc.id),
        startWorldX: world.x,
        startWorldY: world.y,
        startX: freeRect.x,
        startY: freeRect.y,
        group: group.map(item => ({ id: Number(item.id), rect: freeRectForLocation(item) })),
        moved: false,
      };
      suppressNextCanvasClick = true;
      e.preventDefault();
    }
    return;
  }
  const g = canvasToGrid(e.clientX - rect.left, e.clientY - rect.top);
  if (!g) {
    if (isMultiSelectionActive()) clearMultiSelection(true);
    return;
  }
  const loc = getLocationAtGrid(g.x, g.y);
  const locAlreadySelected = loc ? multiSelectedLocationIds.has(Number(loc.id)) : false;
  if (operatorButtonPickMode) {
    if (loc) {
      setOperatorButtonCell(operatorButtonPickMode, loc);
    } else if (operatorWindowAdminMsg) {
      operatorWindowAdminMsg.textContent = `La celda (${g.x}, ${g.y}) no está disponible en la base de datos.`;
    }
  } else if (operatorActionPickMode) {
    if (loc) {
      setOperatorActionCell(operatorActionPickMode, loc);
    } else if (operatorWindowMsg) {
      operatorWindowMsg.textContent = `La celda (${g.x}, ${g.y}) no está disponible en la base de datos.`;
    }
  } else if (directMovePickMode) {
    if (loc) {
      setDirectMoveCell(directMovePickMode, loc);
    } else if (directMsg) {
      directMsg.textContent = `La celda (${g.x}, ${g.y}) no está disponible en la base de datos.`;
    }
  }
  if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && additiveSelect && loc) {
    toggleMultiSelectedLocation(loc);
  } else if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && isMultiSelectionActive() && locAlreadySelected) {
    setPrimarySelection(loc, { preserveMulti: true });
  } else if (!operatorButtonPickMode && !operatorActionPickMode && !directMovePickMode && isMultiSelectionActive() && !locAlreadySelected) {
    clearMultiSelection(false);
    selectCell(g.x, g.y);
  } else {
    selectCell(g.x, g.y);
  }
});
canvas.addEventListener("click", (e) => {
  if (suppressNextCanvasClick) {
    suppressNextCanvasClick = false;
    return;
  }
  if (isFreeLayoutMode()) return;
  if (e.button !== 0 || !operatorButtonPickMode) return;
  const rect = canvas.getBoundingClientRect();
  const g = canvasToGrid(e.clientX - rect.left, e.clientY - rect.top);
  if (!g) return;
  const loc = getLocationAtGrid(g.x, g.y);
  if (loc) {
    setOperatorButtonCell(operatorButtonPickMode, loc);
    selectCell(g.x, g.y);
  } else if (operatorWindowAdminMsg) {
    operatorWindowAdminMsg.textContent = `La celda (${g.x}, ${g.y}) no está disponible en la base de datos.`;
  }
});
window.addEventListener("mousemove", (e) => {
  if (multiSelectBox) {
    const rect = canvas.getBoundingClientRect();
    multiSelectBox.endX = e.clientX - rect.left;
    multiSelectBox.endY = e.clientY - rect.top;
    draw();
    return;
  }
  if (freeLayoutDrag) {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const world = canvasToWorld(mx, my);
    const loc = getLocationById(freeLayoutDrag.locationId);
    if (loc) {
      const dx = world.x - freeLayoutDrag.startWorldX;
      const dy = world.y - freeLayoutDrag.startWorldY;
      const group = Array.isArray(freeLayoutDrag.group) && freeLayoutDrag.group.length ? freeLayoutDrag.group : [{ id: freeLayoutDrag.locationId, rect: { x: freeLayoutDrag.startX, y: freeLayoutDrag.startY, w: loc.free_w || BASE_CELL, h: loc.free_h || BASE_CELL } }];
      for (const item of group) {
        const groupLoc = getLocationById(item.id);
        if (!groupLoc) continue;
        groupLoc.free_enabled = 1;
        groupLoc.free_x = item.rect.x + dx;
        groupLoc.free_y = item.rect.y + dy;
        groupLoc.free_w = item.rect.w;
        groupLoc.free_h = item.rect.h;
      }
      freeLayoutDrag.moved = freeLayoutDrag.moved || Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5;
      hoverPointer = { x: mx, y: my };
      hoverCell = loc;
      draw();
      updateCellHoverTooltip();
    }
    return;
  }
  if (dragging) {
    cam.offX = dragStart.offX + (e.clientX - dragStart.x);
    cam.offY = dragStart.offY + (e.clientY - dragStart.y);
    draw();
    updateCellHoverTooltip();
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const inside = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
  if (!inside) {
    clearCellHoverTooltip();
    return;
  }
  hoverPointer = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  hoverCell = isFreeLayoutMode() ? hitTestFreeLocation(hoverPointer.x, hoverPointer.y) : canvasToGrid(hoverPointer.x, hoverPointer.y);
  updateCellHoverTooltip();
  draw();
});
window.addEventListener("mouseup", () => {
  if (multiSelectBox) {
    const rows = locationsInsideSelectionBox(multiSelectBox);
    multiSelectBox = null;
    if (rows.length) {
      multiSelectedLocationIds = new Set(rows.map(loc => Number(loc.id)));
      setPrimarySelection(rows[0], { preserveMulti: true });
      cellMsg.textContent = `${rows.length} celdas seleccionadas.`;
    } else {
      clearMultiSelection(true);
      cellMsg.textContent = "No se encontraron celdas en el area seleccionada.";
    }
    draw();
    return;
  }
  dragging = false;
  if (freeLayoutDrag) {
    const groupIds = Array.isArray(freeLayoutDrag.group) && freeLayoutDrag.group.length
      ? freeLayoutDrag.group.map(item => Number(item.id))
      : [Number(freeLayoutDrag.locationId)];
    const didMove = freeLayoutDrag.moved;
    freeLayoutDrag = null;
    if (didMove) {
      Promise.all(groupIds.map(id => {
        const item = getLocationById(id);
        return item ? saveFreeLocationLayout(item) : Promise.resolve(null);
      }))
        .then(() => { if (freeLayoutMsg) freeLayoutMsg.textContent = groupIds.length > 1 ? "Posiciones libres guardadas." : "Posicion libre guardada."; })
        .catch((err) => { if (freeLayoutMsg) freeLayoutMsg.textContent = `Error: ${String(err)}`; });
    }
  }
});
canvas.addEventListener("mouseleave", () => { clearCellHoverTooltip(); draw(); });
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left; const my = e.clientY - rect.top;
  const factor = Math.sign(e.deltaY) > 0 ? 0.9 : 1.1;
  zoomAtPoint(cam.scale * factor, mx, my);
  updateCellHoverTooltip();
  draw();
}, { passive: false });


function getTouchDistance(t1, t2) {
  return Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
}

function getTouchCenterRelative(t1, t2) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((t1.clientX + t2.clientX) / 2) - rect.left,
    y: ((t1.clientY + t2.clientY) / 2) - rect.top,
  };
}

canvas.addEventListener("touchstart", (e) => {
  if (!e.touches?.length) return;
  if (e.touches.length >= 2) {
    e.preventDefault();
    const [t1, t2] = e.touches;
    touchState.mode = 'pinch';
    touchState.pinchStartDistance = Math.max(1, getTouchDistance(t1, t2));
    touchState.pinchStartScale = cam.scale;
    touchState.pinchCenter = getTouchCenterRelative(t1, t2);
    dragging = false;
    return;
  }
  const t = e.touches[0];
  touchState.mode = 'pan';
  touchState.lastPan = { x: t.clientX, y: t.clientY };
}, { passive: false });

canvas.addEventListener("touchmove", (e) => {
  if (!e.touches?.length) return;
  if (e.touches.length >= 2) {
    e.preventDefault();
    const [t1, t2] = e.touches;
    const center = getTouchCenterRelative(t1, t2);
    const distance = Math.max(1, getTouchDistance(t1, t2));
    const ratio = distance / Math.max(1, touchState.pinchStartDistance || distance);
    zoomAtPoint((touchState.pinchStartScale || cam.scale) * ratio, center.x, center.y);
    touchState.pinchCenter = center;
    hoverPointer = { x: center.x, y: center.y };
    updateCellHoverTooltip();
    draw();
    return;
  }
  if (touchState.mode === 'pan') {
    e.preventDefault();
    const t = e.touches[0];
    if (touchState.lastPan) {
      cam.offX += t.clientX - touchState.lastPan.x;
      cam.offY += t.clientY - touchState.lastPan.y;
      draw();
      updateCellHoverTooltip();
    }
    touchState.lastPan = { x: t.clientX, y: t.clientY };
  }
}, { passive: false });

canvas.addEventListener("touchend", (e) => {
  if (e.touches.length >= 2) {
    const [t1, t2] = e.touches;
    touchState.mode = 'pinch';
    touchState.pinchStartDistance = Math.max(1, getTouchDistance(t1, t2));
    touchState.pinchStartScale = cam.scale;
    touchState.pinchCenter = getTouchCenterRelative(t1, t2);
    return;
  }
  if (e.touches.length === 1) {
    const t = e.touches[0];
    touchState.mode = 'pan';
    touchState.lastPan = { x: t.clientX, y: t.clientY };
    return;
  }
  touchState.mode = null;
  touchState.lastPan = null;
  touchState.pinchCenter = null;
});

canvas.addEventListener("touchcancel", () => {
  touchState.mode = null;
  touchState.lastPan = null;
  touchState.pinchCenter = null;
});

function renderOperatorCellOptions() {
  renderOperatorButtonCellLabels();
}

function renderOperatorButtonCellLabels() {
  const sourceLoc = operatorButtonSourceCell?.value ? locations.find(loc => loc && Number(loc.id) === Number(operatorButtonSourceCell.value)) : null;
  const destLoc = operatorButtonDestinationCell?.value ? locations.find(loc => loc && Number(loc.id) === Number(operatorButtonDestinationCell.value)) : null;
  if (operatorButtonSourceCellLabel) operatorButtonSourceCellLabel.textContent = sourceLoc ? (sourceLoc.code ? `${sourceLoc.code} (${sourceLoc.x}, ${sourceLoc.y})` : `(${sourceLoc.x}, ${sourceLoc.y})`) : 'Haz clic en "Elegir en matriz"';
  if (operatorButtonDestinationCellLabel) operatorButtonDestinationCellLabel.textContent = destLoc ? (destLoc.code ? `${destLoc.code} (${destLoc.x}, ${destLoc.y})` : `(${destLoc.x}, ${destLoc.y})`) : 'Haz clic en "Elegir en matriz"';
}


function actionModeLabel(mode) {
  return mode === 'fifo' ? 'Solicitud FIFO' : (mode === 'direct_move' ? 'Movimiento directo' : (mode === 'direct_move_config' ? 'Movimiento directo configurable' : (mode === 'point_to_area' ? 'Mover de punto a área' : (mode === 'cancel_return' ? 'Cancelar/devolver' : mode))));
}

function getLocationById(id) { return id ? locations.find(loc => loc && Number(loc.id) === Number(id)) : null; }
function getRackById(id) { return id ? (catalog.racks || []).find(r => r && Number(r.id) === Number(id)) : null; }
function getMaterialById(id) { return id ? (catalog.materials || []).find(m => m && Number(m.id) === Number(id)) : null; }
function getAreaById(id) { return id ? (catalog.areas || []).find(a => a && Number(a.id) === Number(id)) : null; }
function getRackFromLocation(loc) { return loc && loc.rack_id ? getRackById(loc.rack_id) : null; }
function syncCellReservationFromRackSelection() {
  if (!cellRack) return;
  const rack = getRackById(Number(cellRack.value || 0));
  if (!rack) {
    if (cellReservationState) cellReservationState.value = "0";
    if (cellReservationRack) cellReservationRack.value = "Sin rack";
    if (cellReservationTask) cellReservationTask.value = "Sin tarea";
    return;
  }
  const reservation = getRackReservationSnapshot(rack);
  if (cellReservationState) cellReservationState.value = isReservedState(reservation.reservation_status) ? "1" : "0";
  if (cellReservationRack) cellReservationRack.value = (reservation.reservation_rack_code || rack.code || "").trim() || `ID ${reservation.reservation_rack_id || rack.id}`;
  if (cellReservationTask) cellReservationTask.value = reservationTaskLabel(reservation);
}
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', '&quot;')
    .replaceAll("'", "&#39;");
}
function fmtDateTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}
function locationStatusLabel(loc) {
  if (!loc) return 'Sin datos';
  if (Number(loc.enabled ?? 1) !== 1) return 'Deshabilitada';
  return Number(loc.status) === 1 ? 'Ocupada' : 'Libre';
}
function buildCellHoverHtml(loc) {
  if (!loc) return '';
  const area = getAreaById(loc.area_id);
  const rack = getRackFromLocation(loc);
  const material = getMaterialById(rack?.material_group_id);
  const code = loc.code ? escapeHtml(loc.code) : '<span class="muted">Sin código</span>';
  const areaText = area ? escapeHtml(`${area.code || ''}${area.name ? ` - ${area.name}` : ''}`.trim()) : '<span class="muted">Sin área</span>';
  const rackText = rack ? escapeHtml(rack.code || 'Rack sin código') : '<span class="muted">Sin rack</span>';
  const lines = [
    `<div><b>Coordenada:</b> (${Number(loc.x)}, ${Number(loc.y)})</div>`,
    `<div><b>Código:</b> ${code}</div>`,
    `<div><b>Estado:</b> ${escapeHtml(locationStatusLabel(loc))}</div>`,
    `<div><b>Área:</b> ${areaText}</div>`,
    `<div><b>Rack:</b> ${rackText}</div>`,
  ];
  if (rack) {
    lines.push(`<div><b>Material:</b> ${material ? escapeHtml(`${material.code || ''}${material.name ? ` - ${material.name}` : ''}`.trim()) : '<span class="muted">Sin material</span>'}</div>`);
    const displayFields = getRackDisplayFields(rack);
    displayFields.forEach(field => {
      lines.push(`<div><b>${escapeHtml(field.label || field.key || 'Dato')}:</b> ${escapeHtml(field.value ?? '-')}</div>`);
    });
    lines.push(`<div><b>FIFO ingreso:</b> ${escapeHtml(fmtDateTime(rack.fifo_entered_at))}</div>`);
    lines.push(`<div><b>Último movimiento:</b> ${escapeHtml(fmtDateTime(rack.last_moved_at))}</div>`);
  }
  return lines.join('');
}
function updateCellHoverTooltip() {
  if (!cellHoverTooltip) return;
  const loc = isFreeLayoutMode()
    ? hoverCell
    : (hoverCell ? getLocationAtGrid(hoverCell.x, hoverCell.y) : null);
  const visible = isFreeLayoutMode()
    ? (Number(loc?.free_enabled || 0) === 1 || Number(loc?.is_visible ?? 1) === 1)
    : Number(loc?.is_visible ?? 1) === 1;
  if (!hoverCell || !loc || !visible) {
    cellHoverTooltip.classList.add('hidden');
    cellHoverTooltip.innerHTML = '';
    return;
  }
  cellHoverTooltip.innerHTML = buildCellHoverHtml(loc);
  cellHoverTooltip.classList.remove('hidden');
  const parentRect = cellHoverTooltip.parentElement?.getBoundingClientRect();
  if (!parentRect) return;
  const margin = 14;
  const tooltipWidth = Math.min(320, Math.max(220, cellHoverTooltip.offsetWidth || 280));
  const tooltipHeight = cellHoverTooltip.offsetHeight || 180;
  let left = hoverPointer.x + margin;
  let top = hoverPointer.y + margin;
  if (left + tooltipWidth > parentRect.width - 8) left = Math.max(8, hoverPointer.x - tooltipWidth - margin);
  if (top + tooltipHeight > parentRect.height - 8) top = Math.max(8, hoverPointer.y - tooltipHeight - margin);
  cellHoverTooltip.style.left = `${left}px`;
  cellHoverTooltip.style.top = `${top}px`;
}
function clearCellHoverTooltip() {
  hoverCell = null;
  if (cellHoverTooltip) {
    cellHoverTooltip.classList.add('hidden');
    cellHoverTooltip.innerHTML = '';
  }
}
function loadOperatorPointDefaults() { try { return JSON.parse(localStorage.getItem(OPERATOR_LAST_POINT_KEY) || '{}') || {}; } catch { return {}; } }
function loadOperatorPointAreaDefaults() { try { return JSON.parse(localStorage.getItem(OPERATOR_LAST_POINT_AREA_KEY) || '{}') || {}; } catch { return {}; } }
function saveOperatorPointAreaDefaults() {
  try {
    const data = { destination_area_id: operatorActionAreaSelect?.value ? Number(operatorActionAreaSelect.value) : null };
    localStorage.setItem(OPERATOR_LAST_POINT_AREA_KEY, JSON.stringify(data));
  } catch {}
}
function saveOperatorPointDefaults() {
  try {
    const data = {
      material_group_id: operatorActionMaterial?.value ? Number(operatorActionMaterial.value) : null,
      point_field_values: { ...(operatorActionState?.point_field_values || {}) },
    };
    localStorage.setItem(OPERATOR_LAST_POINT_KEY, JSON.stringify(data));
  } catch {}
}
function renderOperatorActionMaterialOptions() {
  if (!operatorActionMaterial) return;
  const visibleIds = getButtonVisibleMaterialIds();
  const sourceLoc = getLocationById(operatorActionState?.source_cell_id);
  const rack = getRackFromLocation(sourceLoc);
  const fallbackId = Number(operatorActionState?.material_group_id || rack?.material_group_id || 0) || null;
  const options = (catalog.materials || []).filter(m => !visibleIds.length || visibleIds.includes(Number(m.id)) || Number(m.id) === fallbackId);
  const cur = operatorActionMaterial.value || (fallbackId ? String(fallbackId) : '');
  operatorActionMaterial.innerHTML = `<option value="">Selecciona</option>` + options.map(m => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');
  if ([...operatorActionMaterial.options].some(o => o.value === String(cur))) operatorActionMaterial.value = String(cur);
}
function getOperatorActionCustomFieldRows() {
  const sourceLoc = getLocationById(operatorActionState?.source_cell_id);
  const rack = getRackFromLocation(sourceLoc);
  return getRackDisplayFields(rack).map(field => ({
    key: field.key,
    label: field.label || field.key || 'Dato',
    value: operatorActionState?.point_field_values?.[field.key] ?? field.value ?? '',
  }));
}
function renderOperatorActionDynamicFields() {
  if (!operatorActionDynamicFields) return;
  const rows = getOperatorActionCustomFieldRows();
  const editable = operatorActionIsPointToArea();
  operatorActionDynamicFields.innerHTML = rows.length ? `
    <div class="dynamic-fields-grid">${rows.map(field => `
      <div class="compact-field-card"><label>${escapeHtml(field.label)}</label><input type="text" data-point-field="${escapeHtml(field.key)}" value="${escapeHtml(field.value ?? '')}" ${editable ? '' : 'readonly'}/></div>`).join('')}
    </div>` : `<div class="small">El rack seleccionado no tiene características configuradas.</div>`;
}
function renderOperatorPointMaterialSelector(selectedIds = []) {
  if (!operatorButtonPointMaterialList) return;
  const ids = new Set((selectedIds || []).map(v => Number(v)).filter(Boolean));
  const materials = catalog.materials || [];
  operatorButtonPointMaterialList.classList.add('compact-check-grid');
  operatorButtonPointMaterialList.innerHTML = materials.length ? materials.map(m => `
    <label class="list-item compact-check-item">
      <span class="compact-check-head"><input type="checkbox" data-point-material-id="${m.id}" ${ids.has(Number(m.id)) ? 'checked' : ''}/><span class="swatch" style="background:${escapeHtml(m.color || '#7c3aed')}"></span><b>${escapeHtml(m.code || '')}</b></span>
      <small>${escapeHtml(m.name || '')}</small>
    </label>`).join('') : `<div class="small">Sin materiales capturados.</div>`;
}
function getSelectedPointMaterialIds() {
  return operatorButtonPointMaterialList ? [...operatorButtonPointMaterialList.querySelectorAll('[data-point-material-id]:checked')].map(el => Number(el.dataset.pointMaterialId)).filter(Boolean) : [];
}
function renderOperatorPointCustomFieldEditor(rows) {
  if (!operatorButtonPointCustomFields) return;
  const normalized = normalizePointCustomFields(rows);
  operatorButtonPointCustomFields.innerHTML = normalized.length ? normalized.map((row, idx) => {
    const options = Object.entries(POINT_FIELD_DEFS).map(([key, def]) => `<option value="${key}" ${row.key === key ? 'selected' : ''}>${escapeHtml(def.label)}</option>`).join('');
    return `<div class="row two-col" data-point-custom-row="${idx}"><div><label>Parámetro</label><select data-point-custom-key="${idx}">${options}</select></div><div><label>Texto visible</label><div style="display:flex;gap:8px;"><input data-point-custom-label="${idx}" type="text" value="${escapeHtml(row.label || '')}"/><button class="btn danger" type="button" data-remove-point-custom="${idx}">Quitar</button></div></div></div>`;
  }).join('') : `<div class="small">Sin características configuradas.</div>`;
  if (btnAddOperatorPointField) {
    const used = new Set(normalized.map(r => r.key));
    btnAddOperatorPointField.disabled = !adminToken || used.size >= Object.keys(POINT_FIELD_DEFS).length;
  }
}
function getAdminPointCustomFieldRows() {
  if (!operatorButtonPointCustomFields) return defaultPointCustomFields();
  return normalizePointCustomFields([...operatorButtonPointCustomFields.querySelectorAll('[data-point-custom-row]')].map((row, idx) => ({
    key: row.querySelector(`[data-point-custom-key="${idx}"]`)?.value || '',
    label: row.querySelector(`[data-point-custom-label="${idx}"]`)?.value || '',
  })));
}
function addAdminPointCustomFieldRow() {
  const rows = getAdminPointCustomFieldRows();
  const used = new Set(rows.map(r => r.key));
  const nextKey = Object.keys(POINT_FIELD_DEFS).find(key => !used.has(key));
  if (!nextKey) return;
  rows.push({ key: nextKey, label: POINT_FIELD_DEFS[nextKey].label });
  renderOperatorPointCustomFieldEditor(rows);
}
function operatorActionUsesInlineCells() {
  return ['direct_move', 'direct_move_config', 'point_to_area'].includes(operatorActionState?.button?.action_mode || '');
}
function operatorActionRequiresImmediatePreview() {
  return operatorActionState?.button?.action_mode === 'fifo';
}
function operatorActionIsPointToArea() {
  return operatorActionState?.button?.action_mode === 'point_to_area';
}
function operatorActionIsDirectMoveConfig() {
  return operatorActionState?.button?.action_mode === 'direct_move_config';
}
function operatorActionShowsAgvTask() {
  return !['direct_move_config', 'point_to_area'].includes(operatorActionState?.button?.action_mode || '');
}
function operatorActionNeedsDestinationCell() {
  return ['direct_move', 'direct_move_config'].includes(operatorActionState?.button?.action_mode || '');
}
function operatorActionNeedsDynamicCells() {
  return operatorActionUsesInlineCells();
}

function renderOperatorActionAreaOptions() {
  if (!operatorActionAreaSelect) return;
  const cur = operatorActionState?.destination_area_id ? String(operatorActionState.destination_area_id) : (operatorActionAreaSelect.value || '');
  operatorActionAreaSelect.innerHTML = `<option value="">Selecciona</option>` + (catalog.areas || []).map(a => `<option value="${a.id}">${a.name || a.code}${a.code ? ` (${a.code})` : ''}</option>`).join('');
  const target = cur || (operatorActionState?.button?.destination_area_id ? String(operatorActionState.button.destination_area_id) : '');
  if ([...operatorActionAreaSelect.options].some(o => o.value === String(target))) operatorActionAreaSelect.value = String(target);
}

function renderOperatorActionCellLabels() {
  const sourceLoc = getLocationById(operatorActionState?.source_cell_id);
  const destLoc = getLocationById(operatorActionState?.destination_cell_id);
  if (operatorActionSourceLabel) operatorActionSourceLabel.textContent = sourceLoc ? (sourceLoc.code ? `${sourceLoc.code} (${sourceLoc.x}, ${sourceLoc.y})` : `(${sourceLoc.x}, ${sourceLoc.y})`) : 'Sin seleccionar';
  if (operatorActionDestinationLabel) operatorActionDestinationLabel.textContent = destLoc ? (destLoc.code ? `${destLoc.code} (${destLoc.x}, ${destLoc.y})` : `(${destLoc.x}, ${destLoc.y})`) : 'Sin seleccionar';
  if (operatorActionInlineCells) operatorActionInlineCells.classList.toggle('hidden', !operatorActionUsesInlineCells());
  if (operatorActionInlineAreaRow) operatorActionInlineAreaRow.classList.toggle('hidden', true);
  if (operatorActionPointFields) operatorActionPointFields.classList.toggle('hidden', !operatorActionIsPointToArea());
  if (operatorActionRackInfoRow) operatorActionRackInfoRow.classList.toggle('hidden', !operatorActionIsPointToArea());
  if (operatorActionAgvTaskRow) operatorActionAgvTaskRow.classList.toggle('hidden', !operatorActionShowsAgvTask());
  if (operatorActionAreaWrap) operatorActionAreaWrap.classList.toggle('hidden', !operatorActionIsPointToArea());
  if (operatorActionDestinationCellWrap) operatorActionDestinationCellWrap.classList.toggle('hidden', !operatorActionNeedsDestinationCell());
  const areaId = operatorActionState?.destination_area_id || operatorActionState?.button?.destination_area_id;
  const area = getAreaById(areaId);
  if (operatorActionAreaDestinationLabel) operatorActionAreaDestinationLabel.textContent = area ? `Preselección: ${area.name || area.code}${area.code ? ` (${area.code})` : ''}` : 'Sin configurar';
  if (operatorActionAreaSelect) {
    renderOperatorActionAreaOptions();
    operatorActionAreaSelect.value = areaId && [...operatorActionAreaSelect.options].some(o => Number(o.value || 0) === Number(areaId)) ? String(areaId) : '';
  }
  renderOperatorActionMaterialOptions();
  renderOperatorActionDynamicFields();
  const rack = getRackFromLocation(sourceLoc);
  const mat = getMaterialById(rack?.material_group_id);
  if (operatorActionRackInfo) operatorActionRackInfo.textContent = rack ? `${rack.code || 'Rack'} · ${mat?.name || 'Sin material'} · lote ${rack.lot || '-'} · qty ${Number(rack.quantity || 0)}` : 'No hay rack en la celda seleccionada';
}

function applyOperatorPointDefaultsFromRack() {
  if (!operatorActionIsPointToArea()) return;
  const defaults = loadOperatorPointDefaults();
  const areaDefaults = loadOperatorPointAreaDefaults();
  const sourceLoc = getLocationById(operatorActionState?.source_cell_id);
  const rack = getRackFromLocation(sourceLoc);
  const displayFields = getRackDisplayFields(rack);
  const baseValues = {};
  displayFields.forEach(field => { baseValues[field.key] = field.value ?? ''; });
  operatorActionState.point_field_values = { ...baseValues };
  operatorActionState.destination_area_id = areaDefaults.destination_area_id || operatorActionState?.button?.destination_area_id || null;
  renderOperatorActionMaterialOptions();
  if (operatorActionMaterial) {
    const target = defaults.material_group_id || rack?.material_group_id || '';
    operatorActionMaterial.value = [...operatorActionMaterial.options].some(o => o.value === String(target)) ? String(target) : '';
  }
  operatorActionState.material_group_id = operatorActionMaterial?.value ? Number(operatorActionMaterial.value) : null;
  renderOperatorActionDynamicFields();
  renderOperatorActionCellLabels();
}

function setOperatorActionCell(mode, loc) {
  if (!loc) return;
  if (mode === 'source') {
    operatorActionState.source_cell_id = Number(loc.id);
    if (operatorActionIsPointToArea()) applyOperatorPointDefaultsFromRack();
  }
  if (mode === 'destination') operatorActionState.destination_cell_id = Number(loc.id);
  operatorActionPickMode = null;
  renderOperatorActionCellLabels();
  draw();
  if (operatorWindowMsg) operatorWindowMsg.textContent = `Celda ${mode === 'source' ? 'origen' : 'destino'} seleccionada: ${loc.code ? loc.code + ' ' : ''}(${loc.x}, ${loc.y}).`;
}

function syncOperatorActionStateFromInputs() {
  operatorActionState.material_group_id = operatorActionMaterial?.value ? Number(operatorActionMaterial.value) : null;
  operatorActionState.destination_area_id = operatorActionAreaSelect?.value ? Number(operatorActionAreaSelect.value) : (operatorActionState?.button?.destination_area_id || null);
  saveOperatorPointDefaults();
  saveOperatorPointAreaDefaults();
}

function operatorActionRequestBody() {
  syncOperatorActionStateFromInputs();
  const sourceLoc = getLocationById(operatorActionState?.source_cell_id);
  const rack = getRackFromLocation(sourceLoc);
  const customRows = getOperatorActionCustomFieldRows();
  const customMap = Object.fromEntries(customRows.map(row => [row.key, row.value]));
  const numberOrNull = (value) => {
    if (value === '' || value == null) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  return {
    password: operatorWindowPassword?.value || '',
    source_cell_id: operatorActionState?.source_cell_id || null,
    destination_cell_id: operatorActionState?.destination_cell_id || null,
    destination_area_id: operatorActionState?.destination_area_id || null,
    material_group_id: operatorActionState?.material_group_id || null,
    lot: String(customMap.lot ?? rack?.lot ?? ''),
    quantity: numberOrNull(customMap.quantity ?? rack?.quantity),
    manufacturer_code: String(customMap.manufacturer_code ?? rack?.manufacturer_code ?? ''),
    comment: String(customMap.comment ?? rack?.comment ?? ''),
    custom_field_values: customRows,
    agv_code: operatorActionShowsAgvTask() ? (operatorActionAgv?.value || '') : '',
    task_typ: operatorActionShowsAgvTask() ? (operatorActionTaskTyp?.value || '') : '',
  };
}

function setOperatorButtonCell(mode, loc) {
  if (!loc) return;
  if (mode === 'source' && operatorButtonSourceCell) {
    operatorButtonSourceCell.value = String(loc.id);
  }
  if (mode === 'destination' && operatorButtonDestinationCell) {
    operatorButtonDestinationCell.value = String(loc.id);
  }
  operatorButtonPickMode = null;
  if (canvas) canvas.style.cursor = '';
  renderOperatorButtonCellLabels();
  draw();
  if (operatorWindowAdminMsg) operatorWindowAdminMsg.textContent = `Celda ${mode === 'source' ? 'origen' : 'destino'} seleccionada: ${loc.code ? loc.code + ' ' : ''}(${loc.x}, ${loc.y}).`;
}

function startOperatorButtonCellPick(mode) {
  if ((operatorButtonMode?.value || '') !== 'direct_move') {
    if (operatorWindowAdminMsg) operatorWindowAdminMsg.textContent = 'La selección en matriz solo aplica al modo Movimiento directo.';
    return;
  }
  operatorButtonPickMode = mode;
  if (canvas) canvas.style.cursor = 'crosshair';
  renderOperatorButtonCellLabels();
  draw();
  try {
    canvas?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch {}
  try { canvas?.focus?.(); } catch {}
  if (operatorWindowAdminMsg) operatorWindowAdminMsg.textContent = `Haz clic en la celda ${mode === 'source' ? 'origen' : 'destino'} dentro de la matriz.`;
}

function clearAdminOperatorWindowForm() {
  if (operatorWindowId) operatorWindowId.value = '';
  if (operatorWindowName) operatorWindowName.value = '';
  if (operatorWindowActive) operatorWindowActive.value = '1';
  if (operatorWindowBgColor) operatorWindowBgColor.value = '#0f2747';
  if (operatorWindowButtonCount) operatorWindowButtonCount.value = '1';
  if (operatorWindowPasswordAdmin) operatorWindowPasswordAdmin.value = '';
  if (operatorButtonList) operatorButtonList.innerHTML = `<div class="small">Guarda o selecciona una ventana para configurar sus botones.</div>`;
  clearAdminOperatorButtonForm();
  selectedAdminWindowId = null;
}

function clearAdminOperatorButtonForm() {
  selectedAdminWindowButtonIndex = null;
  [operatorButtonIndex, operatorButtonLabel, operatorButtonAgv, operatorButtonTaskTyp, operatorButtonComment].forEach(el => { if (el) el.value = ''; });
  if (operatorButtonTaskTyp) operatorButtonTaskTyp.value = 'A01';
  if (operatorButtonActive) operatorButtonActive.value = '1';
  if (operatorButtonColor) operatorButtonColor.value = '#1f4b99';
  if (operatorButtonMode) operatorButtonMode.value = 'fifo';
  if (operatorButtonPriority) operatorButtonPriority.value = 'normal';
  if (operatorButtonSourceArea) operatorButtonSourceArea.value = '';
  if (operatorButtonDestinationArea) operatorButtonDestinationArea.value = '';
  if (operatorButtonPointDestinationArea) operatorButtonPointDestinationArea.value = '';
  if (operatorButtonMaterial) operatorButtonMaterial.value = '';
  renderOperatorPointMaterialSelector([]);
  renderOperatorPointCustomFieldEditor([]);
  if (operatorButtonSourceCell) operatorButtonSourceCell.value = '';
  if (operatorButtonDestinationCell) operatorButtonDestinationCell.value = '';
  operatorButtonPickMode = null;
  renderOperatorButtonCellLabels();
  updateOperatorButtonModeFields();
}

function updateOperatorButtonModeFields() {
  const mode = operatorButtonMode?.value || 'fifo';
  const usesDirectCells = mode === 'direct_move';
  if (!usesDirectCells) operatorButtonPickMode = null;
  if (canvas && !operatorButtonPickMode) canvas.style.cursor = '';
  if (operatorButtonFifoFields) operatorButtonFifoFields.style.display = mode === 'fifo' ? 'block' : 'none';
  if (operatorButtonPointAreaFields) operatorButtonPointAreaFields.style.display = mode === 'point_to_area' ? 'block' : 'none';
  if (document.getElementById('operatorButtonPointCustomSection')) document.getElementById('operatorButtonPointCustomSection').style.display = 'none';
  if (operatorButtonCancelFields) operatorButtonCancelFields.style.display = mode === 'cancel_return' ? 'block' : 'none';
  if (operatorButtonDirectFields) operatorButtonDirectFields.style.display = usesDirectCells ? 'block' : 'none';
  if (mode !== 'direct_move') {
    if (operatorButtonSourceCell) operatorButtonSourceCell.value = mode === 'direct_move_config' ? '' : (operatorButtonSourceCell.value || '');
    if (operatorButtonDestinationCell) operatorButtonDestinationCell.value = mode === 'direct_move_config' ? '' : (operatorButtonDestinationCell.value || '');
    renderOperatorButtonCellLabels();
  }
}

async function loadOperatorWindows() {
  operatorWindows = await fetchJson(API.operatorWindows);
  if (operatorWindowSelect) operatorWindowSelect.innerHTML = `<option value="">Selecciona una ventana</option>` + operatorWindows.map(w => `<option value="${w.id}">${w.name}</option>`).join('');
}

async function loadAdminOperatorWindows() {
  const rows = await fetchJson(API.adminOperatorWindows, { headers: fetchHeaders() });
  if (adminWindowSelect) adminWindowSelect.innerHTML = `<option value="">Selecciona una ventana</option>` + rows.map(w => `<option value="${w.id}">${w.name}</option>`).join('');
}

function renderAdminOperatorButtonList(buttons) {
  if (!operatorButtonList) return;
  operatorButtonList.innerHTML = buttons.length ? buttons.map(btn => { const modeLabel = btn.action_mode === 'fifo' ? 'FIFO' : (btn.action_mode === 'direct_move' ? 'Directo' : (btn.action_mode === 'direct_move_config' ? 'Directo configurable' : (btn.action_mode === 'point_to_area' ? 'Punto a área' : (btn.action_mode === 'cancel_return' ? 'Cancelar/devolver' : (btn.action_mode === 'cancel_total' ? 'Cancel total' : 'Cancel+deshacer'))))); return `<button type="button" class="list-item" data-window-button="${btn.button_index}"><b>${btn.button_index}. ${btn.label}</b><small>${modeLabel} · ${btn.is_active ? 'activo' : 'inactivo'}</small></button>`; }).join('') : `<div class="small">Sin botones.</div>`;
  operatorButtonList.querySelectorAll('[data-window-button]').forEach(el => el.addEventListener('click', () => loadAdminOperatorButton(Number(el.dataset.windowButton))));
}

async function loadAdminOperatorWindowDetail(windowId) {
  if (!windowId) { clearAdminOperatorWindowForm(); return; }
  const data = await fetchJson(API.adminOperatorWindow(windowId), { headers: fetchHeaders() });
  selectedAdminWindowId = data.id;
  operatorWindowId.value = data.id;
  operatorWindowName.value = data.name || '';
  operatorWindowActive.value = String(Number(data.is_active || 0));
  operatorWindowBgColor.value = data.bg_color || '#0f2747';
  operatorWindowButtonCount.value = String(Number(data.button_count || 1));
  operatorWindowPasswordAdmin.value = '';
  renderAdminOperatorButtonList(data.buttons || []);
  clearAdminOperatorButtonForm();
  renderOperatorPointMaterialSelector([]);
  renderOperatorPointCustomFieldEditor([]);
}

async function loadAdminOperatorButton(buttonIndex) {
  if (!selectedAdminWindowId) return;
  const data = await fetchJson(API.adminOperatorWindow(selectedAdminWindowId), { headers: fetchHeaders() });
  const btn = (data.buttons || []).find(x => Number(x.button_index) === Number(buttonIndex));
  if (!btn) throw new Error('Botón no encontrado.');
  selectedAdminWindowButtonIndex = btn.button_index;
  operatorButtonIndex.value = String(btn.button_index);
  operatorButtonActive.value = String(Number(btn.is_active || 0));
  operatorButtonLabel.value = btn.label || '';
  operatorButtonColor.value = btn.color || '#1f4b99';
  operatorButtonMode.value = btn.action_mode || 'fifo';
  operatorButtonPriority.value = btn.priority || 'normal';
  operatorButtonSourceArea.value = btn.source_area_id || '';
  operatorButtonDestinationArea.value = btn.destination_area_id || '';
  if (operatorButtonPointDestinationArea) operatorButtonPointDestinationArea.value = btn.destination_area_id || '';
  operatorButtonMaterial.value = btn.material_group_id || '';
  operatorButtonSourceCell.value = btn.action_mode === 'direct_move' ? (btn.source_cell_id || '') : '';
  operatorButtonDestinationCell.value = btn.action_mode === 'direct_move' ? (btn.destination_cell_id || '') : '';
  renderOperatorButtonCellLabels();
  operatorButtonAgv.value = btn.agv_code || '';
  operatorButtonTaskTyp.value = btn.task_typ || 'A01';
  operatorButtonComment.value = btn.comment || '';
  renderOperatorPointMaterialSelector(btn.point_visible_material_ids || []);
  renderOperatorPointCustomFieldEditor(btn.point_custom_fields || []);
  if (operatorButtonCancelMatterArea) operatorButtonCancelMatterArea.value = btn.cancel_matter_area || '';
  updateOperatorButtonModeFields();
}

async function saveAdminOperatorWindow() {
  const body = {
    id: operatorWindowId?.value ? Number(operatorWindowId.value) : null,
    name: operatorWindowName.value || '',
    bg_color: operatorWindowBgColor.value || '#0f2747',
    button_count: Number(operatorWindowButtonCount.value || 1),
    password: operatorWindowPasswordAdmin.value,
    is_active: Number(operatorWindowActive.value || 0),
  };
  const data = await fetchJson(API.adminOperatorWindows, { method: 'POST', headers: { 'Content-Type': 'application/json', ...fetchHeaders() }, body: JSON.stringify(body) });
  operatorWindowAdminMsg.textContent = 'Ventana guardada.';
  await loadAdminOperatorWindows();
  adminWindowSelect.value = String(data.id);
  await loadAdminOperatorWindowDetail(data.id);
  await loadOperatorWindows();
}

async function saveAdminOperatorButton() {
  if (!selectedAdminWindowId) throw new Error('Primero guarda o selecciona una ventana.');
  const mode = operatorButtonMode.value || 'fifo';
  const body = {
    label: operatorButtonLabel.value || '',
    color: operatorButtonColor.value || '#1f4b99',
    is_active: Number(operatorButtonActive.value || 0),
    action_mode: mode,
    source_area_id: mode === 'fifo' && operatorButtonSourceArea.value ? Number(operatorButtonSourceArea.value) : null,
    destination_area_id: (mode === 'fifo' && operatorButtonDestinationArea.value ? Number(operatorButtonDestinationArea.value) : null) || (mode === 'point_to_area' && operatorButtonPointDestinationArea.value ? Number(operatorButtonPointDestinationArea.value) : null),
    material_group_id: mode === 'fifo' && operatorButtonMaterial.value ? Number(operatorButtonMaterial.value) : null,
    source_cell_id: mode === 'direct_move' && operatorButtonSourceCell.value ? Number(operatorButtonSourceCell.value) : null,
    destination_cell_id: mode === 'direct_move' && operatorButtonDestinationCell.value ? Number(operatorButtonDestinationCell.value) : null,
    priority: operatorButtonPriority.value || 'normal',
    agv_code: operatorButtonAgv.value || '',
    task_typ: operatorButtonTaskTyp.value || '',
    comment: operatorButtonComment.value || '',
    cancel_matter_area: mode === 'cancel_return' ? (operatorButtonCancelMatterArea?.value || '') : '',
    point_visible_material_ids: mode === 'point_to_area' ? getSelectedPointMaterialIds() : [],
    point_custom_fields: [],
  };
  const buttonIndex = Number(operatorButtonIndex.value || selectedAdminWindowButtonIndex || 0);
  if (!buttonIndex) throw new Error('Selecciona un botón.');
  await fetchJson(API.adminOperatorWindowButton(selectedAdminWindowId, buttonIndex), { method: 'POST', headers: { 'Content-Type': 'application/json', ...fetchHeaders() }, body: JSON.stringify(body) });
  operatorWindowAdminMsg.textContent = 'Botón guardado.';
  await loadAdminOperatorWindowDetail(selectedAdminWindowId);
  await loadAdminOperatorButton(buttonIndex);
}

async function deleteAdminOperatorWindow() {
  const windowId = Number(operatorWindowId?.value || selectedAdminWindowId || adminWindowSelect?.value || 0);
  if (!windowId) throw new Error('Selecciona una ventana para borrar.');
  const windowName = (operatorWindowName?.value || '').trim();
  const confirmed = window.confirm(`¿Deseas borrar la ventana${windowName ? ` "${windowName}"` : ''}? Esta acción no se puede deshacer.`);
  if (!confirmed) return;
  await fetchJson(API.adminDeleteOperatorWindow(windowId), { method: 'DELETE', headers: fetchHeaders() });
  operatorWindowAdminMsg.textContent = 'Ventana borrada.';
  clearAdminOperatorWindowForm();
  if (adminWindowSelect) adminWindowSelect.value = '';
  await loadAdminOperatorWindows();
  await loadOperatorWindows();
}

function closeOperatorActionModal() {
  operatorActionPickMode = null;
  actionModalContext = { mode: null, orderId: null };
  operatorActionModal?.classList.add('hidden');
  hideHistoryCancelModalControls();
  if (operatorActionPreview) operatorActionPreview.textContent = 'Sin datos.';
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = '';
  if (operatorActionModalTitle) operatorActionModalTitle.textContent = 'Confirmar solicitud';
  if (btnOperatorActionConfirm) btnOperatorActionConfirm.textContent = 'Enviar';
}

function hideHistoryCancelModalControls() {
  if (operatorCancelModeRow) operatorCancelModeRow.classList.add('hidden');
  if (operatorCancelMode) operatorCancelMode.value = 'return_area';
  if (operatorCancelModeHint) operatorCancelModeHint.textContent = '';
  if (operatorCancelReturnAreaRow) operatorCancelReturnAreaRow.classList.add('hidden');
  if (operatorCancelReturnArea) operatorCancelReturnArea.value = '';
  if (operatorCancelReturnAreaHint) operatorCancelReturnAreaHint.textContent = '';
}

function clearOperatorActionPanel() {
  operatorActionState = { button: null, preview: null, source_cell_id: null, destination_cell_id: null, destination_area_id: null, material_group_id: null, point_field_values: {} };
  operatorActionPickMode = null;
  if (operatorActionPanel) operatorActionPanel.classList.add('hidden');
  if (operatorActionPanelTitle) operatorActionPanelTitle.textContent = 'Parámetros de la acción';
  if (operatorActionPanelModeLabel) operatorActionPanelModeLabel.textContent = '';
  if (operatorActionAgv) operatorActionAgv.value = '';
  if (operatorActionTaskTyp) operatorActionTaskTyp.value = 'A01';
  if (operatorActionMaterial) operatorActionMaterial.value = '';
  if (operatorActionAreaSelect) operatorActionAreaSelect.value = '';
  if (operatorActionDynamicFields) operatorActionDynamicFields.innerHTML = '';
  if (operatorWindowMsg) operatorWindowMsg.textContent = '';
  renderOperatorActionCellLabels();
  draw();
}

function renderOperatorPreviewText(preview) {
  const s = preview?.summary || {};
  return [
    `Acción: ${preview?.action_mode || ''}`,
    `Mensaje: ${preview?.message || ''}`,
    '',
    `Tipo: ${s.tipo || ''}`,
    `Rack: ${s.rack || ''}`,
    `Material: ${s.material || ''}`,
    `Origen: ${s.origen || ''}`,
    `Destino: ${s.destino || ''}`,
    `AGV: ${s.agv || ''}`,
    `Prioridad: ${s.prioridad || ''}`,
    `Tipo de tarea: ${s.taskTyp || ''}`,
    `Comentario: ${s.comentario || ''}`,
    '',
    'JSON a enviar:',
    JSON.stringify(preview?.payload || {}, null, 2),
  ].join('\n');
}

async function validateOperatorActionModal() {
  hideHistoryCancelModalControls();
  if (!activeOperatorWindow?.id || !operatorActionState.button) throw new Error('No hay acción seleccionada.');
  const preview = await fetchJson(API.operatorWindowPreview(activeOperatorWindow.id, operatorActionState.button.button_index), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operatorActionRequestBody()) });
  operatorActionState.preview = preview;
  if (operatorActionPreview) operatorActionPreview.textContent = renderOperatorPreviewText(preview);
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = preview.message || 'Detalle listo para confirmar.';
}

async function confirmOperatorActionModal() {
  if (actionModalContext.mode === 'history_undo') {
    const orderId = actionModalContext.orderId || selectedOrderId;
    if (!orderId) throw new Error('Selecciona una tarea.');
    const order = movementOrders.find(x => x.order_id === orderId) || null;
    if (order && !historyOrderCanCancel(order)) throw new Error(historyOrderUnavailableReason(order) || 'Esta orden no permite cancelacion desde historial.');
    const returnToArea = operatorCancelMode?.value === 'return_area';
    const returnAreaId = returnToArea && operatorCancelReturnArea?.value ? Number(operatorCancelReturnArea.value) : null;
    if (returnToArea && !returnAreaId) throw new Error('Selecciona el area de devolucion.');
    const matterArea = returnToArea ? selectedCancelReturnMatterArea() : '';
    const result = await fetchJson(API.movementOrderUndo(orderId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ return_to_area: returnToArea, return_area_id: returnAreaId, matter_area: matterArea }) });
    closeOperatorActionModal();
    orderMsg.textContent = returnToArea
      ? `Orden ${result.order_code} cancelada desde historial con cancelTask forceCancel=1 y reversa local del almacén. Nuevo estado: ${result.status}.`
      : `Orden ${result.order_code} cancelada desde historial con cancelTask forceCancel=0. Nuevo estado: ${result.status}.`;
    await loadLocations();
    await loadCatalog();
    await loadMovementOrders(result.order_id);
    const focusCell = result.current_cell || result.source_cell;
    await selectCell(Number(focusCell.x), Number(focusCell.y));
    draw();
    return;
  }

  if (!activeOperatorWindow?.id || !operatorActionState.button) throw new Error('No hay acción seleccionada.');
  try {
    await fetchJson(API.operatorWindowExecute(activeOperatorWindow.id, operatorActionState.button.button_index), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operatorActionRequestBody()) });
  } catch (err) {
    await refreshAfterDispatchError();
    throw err;
  }
  closeOperatorActionModal();
  clearOperatorActionPanel();
  operatorWindowMsg.textContent = 'Tarea generada y aceptada por el RCS.';
  await loadLocations();
  await loadCatalog();
  await loadMovementOrders();
  draw();
}

function getAreaByIdForCancel(id) {
  return id ? (catalog.areas || []).find(a => Number(a.id) === Number(id)) : null;
}

function isHistoryCancelReturnMode() {
  return operatorCancelMode?.value === 'return_area';
}

function selectedCancelReturnMatterArea() {
  const area = getAreaByIdForCancel(operatorCancelReturnArea?.value);
  return String(area?.matter_area || '').trim();
}

function fillCancelReturnAreaSelect(order) {
  if (!operatorCancelReturnArea) return;
  const activeAreas = (catalog.areas || []).filter(a => Number(a.is_active ?? 1) === 1);
  operatorCancelReturnArea.innerHTML = `<option value="">Selecciona area</option>` + activeAreas.map(a => `<option value="${a.id}">${escapeHtml(a.name || a.code)}${a.code ? ` (${escapeHtml(a.code)})` : ''}</option>`).join('');
  const preferredId = order?.source_cell?.area_id ? String(order.source_cell.area_id) : (order?.source_area_id ? String(order.source_area_id) : '');
  if (preferredId && [...operatorCancelReturnArea.options].some(o => o.value === preferredId)) {
    operatorCancelReturnArea.value = preferredId;
  }
}

function updateCancelReturnAreaHint(order) {
  const returnMode = isHistoryCancelReturnMode();
  if (operatorCancelReturnAreaRow) operatorCancelReturnAreaRow.classList.toggle('hidden', !returnMode);
  if (operatorCancelModeHint) operatorCancelModeHint.textContent = returnMode
    ? 'Se cancelara y se devolvera el contenedor al area seleccionada.'
    : 'Se enviara cancelTask normal sin matterArea y sin devolucion local.';
  const area = getAreaByIdForCancel(operatorCancelReturnArea?.value);
  const matterArea = returnMode ? selectedCancelReturnMatterArea() : '';
  if (operatorCancelReturnAreaHint) operatorCancelReturnAreaHint.textContent = area ? `Se enviara matterArea: ${matterArea || '(vacio)'}` : 'Selecciona el area a la que se devuelve el contenedor.';
  if (operatorActionPreview) operatorActionPreview.textContent = renderHistoryUndoPreviewText(order);
}

function renderHistoryUndoPreviewText(order) {
  if (!order) return 'Sin orden seleccionada.';
  const returnMode = isHistoryCancelReturnMode();
  const remoteActive = !!(order.remote_task_code && ['pending_dispatch', 'dispatched', 'in_progress'].includes(String(order.status || '')));
  const currentCell = order.current_cell ? (order.current_cell.code || `(${order.current_cell.x}, ${order.current_cell.y})`) : 'Sin ubicación';
  const sourceCell = order.source_cell ? (order.source_cell.code || `(${order.source_cell.x}, ${order.source_cell.y})`) : 'Sin origen';
  const destinationCell = order.destination_cell ? (order.destination_cell.code || `(${order.destination_cell.x}, ${order.destination_cell.y})`) : 'Sin destino';
  const returnArea = returnMode ? getAreaByIdForCancel(operatorCancelReturnArea?.value) : null;
  const matterArea = returnMode ? selectedCancelReturnMatterArea() : '';
  const forceCancel = returnMode ? '1' : '0';
  const payload = {
    accion: returnMode ? 'cancelar_y_devolver_a_area_desde_historial' : 'cancelar_desde_historial',
    order_id: order.order_id,
    order_code: order.order_code,
    forceCancel,
    return_to_area: returnMode,
    return_area_id: returnArea?.id || null,
    return_area: returnArea ? (returnArea.name || returnArea.code || '') : '',
    matterArea,
    remote_task_code: order.remote_task_code || '',
    remote_cancel: remoteActive,
    deshacer_movimiento_local: returnMode,
  };
  return [
    `Acción: ${returnMode ? 'Cancelar y devolver a área' : 'Cancelar'}`,
    `Orden: ${order.order_code || ''}`,
    `Estado actual: ${order.status || ''}`,
    `Rack: ${order.rack_code || ''}`,
    `Origen: ${sourceCell}`,
    `Destino: ${destinationCell}`,
    `Area de devolucion: ${returnMode ? (returnArea ? `${returnArea.name || returnArea.code}${returnArea.code ? ` (${returnArea.code})` : ''}` : 'Sin seleccionar') : 'No aplica'}`,
    `Matter Area / matterArea: ${returnMode ? (matterArea || '(vacio)') : '(vacio)'}`,
    `Ubicación actual rack: ${currentCell}`,
    `Cancelación remota en RCS: ${remoteActive ? 'Sí' : (returnMode ? 'No (solo deshacer local)' : 'No disponible para cancelación normal')}`,
    '',
    'JSON / parámetros de la acción:',
    JSON.stringify(payload, null, 2),
  ].join('\n');
}

function openHistoryUndoModal(orderArg = null) {
  const order = orderArg || movementOrders.find(x => x.order_id === selectedOrderId) || null;
  if (!order) throw new Error('Selecciona una tarea.');
  if (!historyOrderCanCancel(order)) throw new Error(historyOrderUnavailableReason(order) || 'La tarea seleccionada no permite cancelar o deshacer.');
  actionModalContext = { mode: 'history_undo', orderId: order.order_id };
  if (operatorActionModalTitle) operatorActionModalTitle.textContent = 'Confirmar cancelación / deshacer';
  if (btnOperatorActionConfirm) btnOperatorActionConfirm.textContent = 'Enviar';
  if (operatorCancelModeRow) operatorCancelModeRow.classList.remove('hidden');
  if (operatorCancelMode) operatorCancelMode.value = 'return_area';
  fillCancelReturnAreaSelect(order);
  updateCancelReturnAreaHint(order);
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = 'Revisa la previsualización y confirma para enviar la cancelación o el deshacer.';
  operatorActionModal?.classList.remove('hidden');
}

async function openOperatorActionModal(button) {
  hideHistoryCancelModalControls();
  operatorActionState = { button, preview: null, source_cell_id: button?.source_cell_id || null, destination_cell_id: button?.destination_cell_id || null, destination_area_id: button?.destination_area_id || null, material_group_id: null, lot: '', quantity: null, manufacturer_code: '', comment: '' };
  operatorActionPickMode = null;
  const showParameterPanel = operatorActionUsesInlineCells();
  if (operatorActionPanel) operatorActionPanel.classList.toggle('hidden', !showParameterPanel);
  if (operatorActionPanelTitle) operatorActionPanelTitle.textContent = button.label || 'Parámetros de la acción';
  if (operatorActionPanelModeLabel) operatorActionPanelModeLabel.textContent = button.action_mode === 'point_to_area' ? '' : `Modo: ${actionModeLabel(button.action_mode)}`;
  if (operatorActionAgv) operatorActionAgv.value = button.agv_code || '';
  if (operatorActionTaskTyp) operatorActionTaskTyp.value = button.task_typ || 'A01';
  renderOperatorActionMaterialOptions();
  renderOperatorActionCellLabels();
  if (button.action_mode === 'direct_move' && (!operatorActionState.source_cell_id || !operatorActionState.destination_cell_id)) {
    if (operatorWindowMsg) operatorWindowMsg.textContent = 'Este botón de movimiento directo no tiene configuradas la celda origen y la celda destino.';
    return;
  }
  if (showParameterPanel) {
    if (operatorWindowMsg) operatorWindowMsg.textContent = 'Selecciona los parámetros abajo y luego presiona Vista previa.';
    if (button.action_mode === 'point_to_area') applyOperatorPointDefaultsFromRack();
    draw();
    return;
  }

  if (operatorActionModalTitle) operatorActionModalTitle.textContent = button.label || 'Confirmar solicitud';
  if (operatorActionPreview) operatorActionPreview.textContent = 'Generando vista previa...';
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = 'Preparando JSON de solicitud...';
  operatorActionModal?.classList.remove('hidden');
  if (operatorWindowMsg) operatorWindowMsg.textContent = operatorActionRequiresImmediatePreview() ? 'Generando vista previa...' : '';

  try {
    await validateOperatorActionModal();
    if (operatorWindowMsg) operatorWindowMsg.textContent = 'Vista previa lista. Revisa y confirma o cancela.';
    if (operatorActionModalMsg && !operatorActionModalMsg.textContent) operatorActionModalMsg.textContent = 'Revisa el JSON generado antes de enviar.';
  } catch (err) {
    if (operatorActionPreview) operatorActionPreview.textContent = '';
    if (operatorActionModalMsg) operatorActionModalMsg.textContent = `Error: ${String(err)}`;
    if (operatorWindowMsg) operatorWindowMsg.textContent = `Error: ${String(err)}`;
  }
}

function renderActiveOperatorWindow() {
  if (!activeOperatorWindow) {
    if (operatorButtonsBox) operatorButtonsBox.innerHTML = `<div class="small">Selecciona una ventana y captura su contraseña para ver los botones.</div>`;
    clearOperatorActionPanel();
    return;
  }
  const buttons = (activeOperatorWindow.buttons || []).filter(btn => Number(btn.is_active || 0) === 1);
  if (operatorButtonsBox) {
    operatorButtonsBox.style.background = activeOperatorWindow.bg_color || '#0f2747';
    operatorButtonsBox.innerHTML = buttons.length ? buttons.map(btn => `<button type="button" class="operator-big-btn" data-run-window-button="${btn.button_index}" style="background:${btn.color || '#1f4b99'}">${btn.label || ('Botón ' + btn.button_index)}</button>`).join('') : `<div class="small">Esta ventana no tiene botones activos.</div>`;
    operatorButtonsBox.querySelectorAll('[data-run-window-button]').forEach(el => el.addEventListener('click', async () => {
      const btn = (activeOperatorWindow.buttons || []).find(x => Number(x.button_index) === Number(el.dataset.runWindowButton));
      if (!btn) return;
      try {
        await openOperatorActionModal(btn);
      } catch (err) {
        if (operatorWindowMsg) operatorWindowMsg.textContent = `Error: ${String(err)}`;
      }
    }));
  }
}

async function openOperatorWindow() {
  const windowId = operatorWindowSelect?.value ? Number(operatorWindowSelect.value) : null;
  if (!windowId) {
    activeOperatorWindow = null;
    renderActiveOperatorWindow();
    if (operatorWindowMsg) operatorWindowMsg.textContent = '';
    return;
  }
  activeOperatorWindow = await fetchJson(API.operatorWindowAccess(windowId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: operatorWindowPassword?.value || '' }) });
  renderActiveOperatorWindow();
  if (operatorWindowMsg) operatorWindowMsg.textContent = 'Ventana abierta.';
}

btnAdminLogin?.addEventListener("click", async () => {
  try {
    const data = await fetchJson(API.adminLogin, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: adminPwd.value || "" }) });
    adminToken = data.token;
    persistAdminSession(adminToken, data.expires_hours);
    setAdminUI(true);
    adminMsg.textContent = "Admin habilitado.";
    await loadAllAdminDataAfterLogin("login");
  } catch (err) { adminMsg.textContent = `Error: ${String(err)}`; }
});
btnAdminLock?.addEventListener("click", () => clearAdminSession("Admin bloqueado."));
btnOpenDbBackupsModal?.addEventListener("click", () => openDbBackupsModal());
btnCloseDbBackupsModal?.addEventListener("click", () => closeDbBackupsModal());
btnDownloadDb?.addEventListener("click", () => downloadDatabaseBackup());
btnDownloadFullBackup?.addEventListener("click", () => downloadFullBackup());
btnChooseBackupFile?.addEventListener("click", () => chooseBackupFile());
backupZipFile?.addEventListener("change", () => handleBackupFileSelected());
btnValidateBackup?.addEventListener("click", () => validateSelectedBackup());
btnRestoreBackup?.addEventListener("click", () => openBackupRestoreConfirmModal());
btnCancelBackupRestore?.addEventListener("click", () => closeBackupRestoreConfirmModal());
btnConfirmBackupRestore?.addEventListener("click", () => restoreSelectedBackup());
btnMarkBackupRestarted?.addEventListener("click", () => markBackupRestarted());
console.log("[cleanup] registrando botón diagnóstico");
if (!document.getElementById("cleanupDiagnosisModal")) {
  console.warn("[cleanup-diagnosis] No se encontró el modal #cleanupDiagnosisModal.");
}
btnRefreshCleanupDiagnosis?.addEventListener("click", () => loadCleanupDiagnosis().catch(err => {
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
  console.warn("[cleanup-diagnosis] No se pudo refrescar el diagnóstico.", err);
}));
btnSelectSafeCleanup?.addEventListener("click", () => {
  document.querySelectorAll('[data-cleanup-select="1"]:not(:disabled)').forEach((input) => { input.checked = true; });
});
btnCleanSelected?.addEventListener("click", () => openCleanupConfirmModal("all"));
btnCloseSelectedOrders?.addEventListener("click", () => openCleanupConfirmModal("orders"));
btnReleaseSelectedRacks?.addEventListener("click", () => openCleanupConfirmModal("racks"));
btnResolveSelectedInconsistentRacks?.addEventListener("click", () => resolveSelectedInconsistentRacks().catch(err => {
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
}));
btnForceReleaseOldActiveRacks?.addEventListener("click", () => forceReleaseSelectedOldActiveRacks().catch(err => {
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
}));
btnCancelCleanupClose?.addEventListener("click", () => closeCleanupConfirmModal());
btnConfirmCleanupClose?.addEventListener("click", () => executeSelectedCleanup().catch(err => {
  if (cleanupConfirmMsg) cleanupConfirmMsg.textContent = `Error: ${String(err)}`;
  if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
}));
cleanupDiagnosisModal?.addEventListener("click", (ev) => {
  if (ev.target === cleanupDiagnosisModal) closeCleanupDiagnosisModal();
});
dbBackupsModal?.addEventListener("click", (ev) => {
  if (ev.target === dbBackupsModal) closeDbBackupsModal();
});
backupRestoreConfirmModal?.addEventListener("click", (ev) => {
  if (ev.target === backupRestoreConfirmModal) closeBackupRestoreConfirmModal();
});
cleanupConfirmModal?.addEventListener("click", (ev) => {
  if (ev.target === cleanupConfirmModal) closeCleanupConfirmModal();
});
qrRulePreviewModal?.addEventListener("click", (ev) => {
  if (ev.target === qrRulePreviewModal) closeQrRulePreviewModal();
});
btnCloseQrRuleModal?.addEventListener("click", closeQrRulePreviewModal);
btnCloseQrRuleModalX?.addEventListener("click", closeQrRulePreviewModal);
btnPrintQrRuleLabel?.addEventListener("click", () => window.print());
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && qrRulePreviewModal && !qrRulePreviewModal.classList.contains("hidden")) {
    closeQrRulePreviewModal();
    return;
  }
  if (ev.key === "Escape" && backupRestoreConfirmModal && !backupRestoreConfirmModal.classList.contains("hidden")) {
    closeBackupRestoreConfirmModal();
    return;
  }
  if (ev.key === "Escape" && dbBackupsModal && !dbBackupsModal.classList.contains("hidden")) {
    closeDbBackupsModal();
    return;
  }
  if (ev.key === "Escape" && cleanupConfirmModal && !cleanupConfirmModal.classList.contains("hidden")) {
    closeCleanupConfirmModal();
    return;
  }
  if (ev.key === "Escape" && cleanupDiagnosisModal && !cleanupDiagnosisModal.classList.contains("hidden")) {
    closeCleanupDiagnosisModal();
  }
});
function buildGeneralConfigPayload() {
  syncAgvOverlayConfigFromInputs();
  return {
    display_rows: Number(dispRows.value || GRID_H || 1),
    display_cols: Number(dispCols.value || GRID_W || 1),
    map_layout_mode: mapLayoutModeSelect?.value === "free" ? "free" : "grid",
    agv_overlay_scale_x: Number(agvOverlayConfig.scale_x || 1),
    agv_overlay_scale_y: Number(agvOverlayConfig.scale_y || 1),
    agv_overlay_offset_x: Number(agvOverlayConfig.offset_x || 0),
    agv_overlay_offset_y: Number(agvOverlayConfig.offset_y || 0),
    agv_overlay_rotation_deg: Number(agvOverlayConfig.rotation_deg || 0),
    agv_orientation_offset_deg: Number(agvOverlayConfig.orientation_offset_deg || 0),
    agv_overlay_mirror_x: Number(agvOverlayConfig.mirror_x || 0),
    agv_overlay_mirror_y: Number(agvOverlayConfig.mirror_y || 0),
    agv_icon_angle_mirror: Number(agvOverlayConfig.icon_angle_mirror || 0),
    runtime_refresh_seconds: Math.max(2, Number(runtimeRefreshSeconds?.value || (RUNTIME_AUTO_REFRESH_MS / 1000) || 5)),
    runtime_reconnect_seconds: Math.max(1, Number(runtimeReconnectSeconds?.value || (RUNTIME_SOCKET_RECONNECT_MS / 1000) || 3)),
  };
}

async function saveAgvOverlaySettings() {
  await fetchJson(API.adminGrid, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...fetchHeaders() },
    body: JSON.stringify(buildGeneralConfigPayload())
  });
  await loadGridConfig();
  draw();
}

btnSaveGeneralConfig?.addEventListener("click", async () => {
  try {
    const payload = buildGeneralConfigPayload();
    const saved = await fetchJson(API.adminGrid, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...fetchHeaders() },
      body: JSON.stringify(payload)
    });
    await saveBgTransform();
    await loadGridConfig();
    await refreshBackground();
    fitMapToScreen();
    draw();
    if (selected.x >= GRID_W || selected.y >= GRID_H) selectCell(0, 0);
    adminMsg.textContent = `Configuración general guardada. Vista actualizada a ${GRID_W}×${GRID_H}.`;
  } catch (err) {
    adminMsg.textContent = `Error: ${String(err)}`;
  }
});
btnHideConfiguredRange?.addEventListener("click", () => adminHideConfiguredRange().catch(err => rangeMsg.textContent = `Error: ${String(err)}`));
btnShowConfiguredRange?.addEventListener("click", () => adminShowConfiguredRange().catch(err => rangeMsg.textContent = `Error: ${String(err)}`));
mapLayoutModeSelect?.addEventListener("change", () => {
  mapLayoutMode = mapLayoutModeSelect.value === "free" ? "free" : "grid";
  hoverCell = null;
  updateAddFreeCellAvailability();
  fitMapToScreen();
  draw();
});
freeLayoutEditEnabled?.addEventListener("change", () => {
  updateAddFreeCellAvailability();
  draw();
});
btnAddFreeCell?.addEventListener("click", async () => {
  try {
    if (!isFreeLayoutEditing()) {
      if (freeLayoutMsg) freeLayoutMsg.textContent = "Activa modo Libre y Editar modo libre = Si para agregar celdas.";
      updateAddFreeCellAvailability();
      return;
    }
    const loc = await fetchJson(API.adminLocFreeCreate, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...fetchHeaders() },
      body: JSON.stringify({ display_rows: GRID_H, display_cols: GRID_W }),
    });
    locations[idx(loc.x, loc.y)] = loc;
    if (mapLayoutModeSelect) {
      mapLayoutModeSelect.value = "free";
      mapLayoutMode = "free";
    }
    await selectCell(Number(loc.x), Number(loc.y));
    if (freeLayoutMsg) freeLayoutMsg.textContent = `Celda de cuadricula mostrada (${loc.x}, ${loc.y}).`;
  } catch (err) {
    if (freeLayoutMsg) freeLayoutMsg.textContent = `Error: ${String(err)}`;
  }
});
btnUploadBg?.addEventListener("click", async () => {
  try {
    if (!bgFile?.files?.[0]) return;
    const fd = new FormData(); fd.append("file", bgFile.files[0]);
    await fetchJson(API.adminBgUpload, { method: "POST", headers: fetchHeaders(), body: fd });
    await refreshBackground(); adminMsg.textContent = "Imagen cargada.";
  } catch (err) { adminMsg.textContent = `Error: ${String(err)}`; }
});
[agvOverlayScaleX, agvOverlayScaleY, agvOverlayOffsetX, agvOverlayOffsetY, agvOverlayRotationDeg, agvOrientationOffsetDeg, agvOverlayMirrorX, agvOverlayMirrorY, agvIconAngleMirror].forEach((el) => {
  el?.addEventListener('input', () => {
    syncAgvOverlayConfigFromInputs();
    draw();
  });
});
[bgScaleX, bgScaleY, bgOffX, bgOffY].forEach(el => el?.addEventListener("input", () => { bgState.scale_x = Number(bgScaleX.value || 1); bgState.scale_y = Number(bgScaleY.value || 1); bgState.offset_x = Number(bgOffX.value || 0); bgState.offset_y = Number(bgOffY.value || 0); draw(); }));
btnSaveClientIp?.addEventListener("click", () => adminSaveClientIp().catch(err => adminMsg.textContent = `Error: ${String(err)}`));
btnSaveRcsConfig?.addEventListener("click", () => adminSaveRcsConfig().catch(err => rcsConfigMsg.textContent = `Error: ${String(err)}`));
btnTestRcsConfig?.addEventListener("click", () => adminTestRcsConfig().catch(err => rcsConfigMsg.textContent = `Error: ${String(err)}`));
btnRackSyncPreview?.addEventListener("click", () => loadRackSyncPreview().catch(err => {
  if (rackSyncMsg) rackSyncMsg.textContent = `Error: ${String(err)}`;
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = !adminToken;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = !adminToken;
}));
btnRackSyncQuery?.addEventListener("click", () => loadRackSyncQuery().catch(err => {
  if (rackSyncMsg) rackSyncMsg.textContent = `Error: ${String(err)}`;
  if (btnRackSyncPreview) btnRackSyncPreview.disabled = !adminToken;
  if (btnRackSyncQuery) btnRackSyncQuery.disabled = !adminToken;
  updateRackSyncButtons();
}));
btnRackSyncBind?.addEventListener("click", () => bindRackSyncMismatches().catch(err => {
  if (rackSyncMsg) rackSyncMsg.textContent = `Error: ${String(err)}`;
  updateRackSyncButtons();
}));
btnRackSyncHistory?.addEventListener("click", () => loadRackSyncHistory().catch(err => {
  if (rackSyncMsg) rackSyncMsg.textContent = `Error: ${String(err)}`;
  updateRackSyncButtons();
}));
btnQueryPodPosition?.addEventListener("click", () => queryPodPosition().catch(err => {
  if (podPositionMsg) podPositionMsg.textContent = `Error: ${String(err)}`;
  if (btnQueryPodPosition) btnQueryPodPosition.disabled = !adminToken;
}));
btnChangePwd?.addEventListener("click", async () => {
  try {
    await fetchJson(API.adminChangePwd, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ old_password: oldPwd.value || "", new_password: newPwd.value || "" }) });
    oldPwd.value = ""; newPwd.value = ""; adminMsg.textContent = "Contraseña cambiada correctamente.";
  } catch (err) { adminMsg.textContent = `Error: ${String(err)}`; }
});
btnSaveCell?.addEventListener("click", () => saveSelectedCell().catch(err => cellMsg.textContent = `Error: ${String(err)}`));
btnSelectCellsByArea?.addEventListener("click", () => {
  multiSelectMode = !multiSelectMode;
  if (btnSelectCellsByArea) {
    btnSelectCellsByArea.textContent = multiSelectMode ? "Seleccion multiple activa" : "Seleccion multiple";
    btnSelectCellsByArea.classList.toggle("primary", multiSelectMode);
  }
  cellMsg.textContent = multiSelectMode
    ? "Seleccion multiple activa: clic izquierdo y arrastra para dibujar el area."
    : "Seleccion multiple desactivada.";
  draw();
});
btnNewArea?.addEventListener("click", clearAreaForm);
btnSaveArea?.addEventListener("click", () => saveArea().catch(err => areaMsg.textContent = `Error: ${String(err)}`));
btnDeleteArea?.addEventListener("click", () => deleteArea().catch(err => areaMsg.textContent = `Error: ${String(err)}`));
btnNewMaterial?.addEventListener("click", clearMaterialForm);
btnSaveMaterial?.addEventListener("click", () => saveMaterial().catch(err => materialMsg.textContent = `Error: ${String(err)}`));
btnDeleteMaterial?.addEventListener("click", () => deleteMaterial().catch(err => materialMsg.textContent = `Error: ${String(err)}`));
btnNewRack?.addEventListener("click", clearRackForm);
btnSaveRack?.addEventListener("click", () => saveRack().catch(err => rackMsg.textContent = `Error: ${String(err)}`));
btnDeleteRack?.addEventListener("click", () => deleteRack().catch(err => rackMsg.textContent = `Error: ${String(err)}`));
document.querySelectorAll("#card-qr-scanners .qr-tab").forEach(btn => btn.addEventListener("click", () => activateQrPanel(btn.dataset.qrTab || "stations")));
btnNewScannerStation?.addEventListener("click", () => { clearScannerStationForm(); if (qrAdminMsg) qrAdminMsg.textContent = ""; });
btnSaveScannerStation?.addEventListener("click", () => saveScannerStation().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnDisableScannerStation?.addEventListener("click", () => disableScannerStation().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
scannerCancelReturnArea?.addEventListener("change", () => renderScannerCancelReturnAreaWarning());
scannerRouteMode?.addEventListener("change", syncRouteModeSections);
scannerFifoChainTotalSteps?.addEventListener("change", syncRouteModeSections);
scannerFifoChainStep1SourceMode?.addEventListener("change", syncRouteModeSections);
scannerFifoChainStep2SourceMode?.addEventListener("change", syncRouteModeSections);
scannerFifoChainStep3SourceMode?.addEventListener("change", syncRouteModeSections);
scannerFifoChainStep4SourceMode?.addEventListener("change", syncRouteModeSections);
btnNewQrRule?.addEventListener("click", () => { clearQrRuleForm(); if (qrAdminMsg) qrAdminMsg.textContent = ""; });
btnSaveQrRule?.addEventListener("click", () => saveQrRule().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnDisableQrRule?.addEventListener("click", () => disableQrRule().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
qrRouteMode?.addEventListener("change", syncRouteModeSections);
qrFifoChainTotalSteps?.addEventListener("change", syncRouteModeSections);
qrFifoChainStep1SourceMode?.addEventListener("change", syncRouteModeSections);
qrFifoChainStep2SourceMode?.addEventListener("change", syncRouteModeSections);
qrFifoChainStep3SourceMode?.addEventListener("change", syncRouteModeSections);
qrFifoChainStep4SourceMode?.addEventListener("change", syncRouteModeSections);
qrActionType?.addEventListener("change", syncFifoMaterialPolicyHelp);
qrFifoMaterialPolicy?.addEventListener("change", syncFifoMaterialPolicyHelp);
btnNewQrTransitionRule?.addEventListener("click", () => { clearQrTransitionRuleForm(); renderQrTransitionQrOptions(); renderQrTransitionScannerOptions(); renderQrCatalogOptions(); if (qrAdminMsg) qrAdminMsg.textContent = ""; });
btnSaveQrTransitionRule?.addEventListener("click", () => saveQrTransitionRule().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnDisableQrTransitionRule?.addEventListener("click", () => disableQrTransitionRule().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnPreviewQrTransition?.addEventListener("click", () => previewQrTransition().catch(err => { if (qrTransitionPreviewResult) qrTransitionPreviewResult.textContent = `Error: ${String(err)}`; }));
qrTransitionMatchMode?.addEventListener("change", () => syncQrTransitionModeOptions({ applyDefaults: qrTransitionMatchMode.value === "route_simple" }));
qrTransitionSourceMatchMode?.addEventListener("change", syncQrTransitionModeOptions);
qrTransitionIgnoreCurrentMaterial?.addEventListener("change", syncQrTransitionModeOptions);
btnApplyQrTransition?.addEventListener("click", () => applyQrTransitionManual().catch(err => { if (qrTransitionPreviewResult) qrTransitionPreviewResult.textContent = `Error: ${String(err)}`; }));
btnRefreshQrTransitionLogs?.addEventListener("click", () => loadQrTransitionLogs().then(() => { if (qrAdminMsg) qrAdminMsg.textContent = "Historial de transiciones actualizado."; }).catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnNewScanTerminal?.addEventListener("click", () => { clearScanTerminalForm(); renderScanTerminalScannerOptions(); if (qrAdminMsg) qrAdminMsg.textContent = ""; });
btnSaveScanTerminal?.addEventListener("click", () => saveScanTerminal().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnRefreshScanTerminals?.addEventListener("click", () => loadScanTerminals().then(() => { if (qrAdminMsg) qrAdminMsg.textContent = "Terminales PDA actualizados."; }).catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnDisableScanTerminal?.addEventListener("click", () => disableScanTerminal().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
btnRefreshScanEvents?.addEventListener("click", () => loadScanEvents().catch(err => { if (qrAdminMsg) qrAdminMsg.textContent = `Error: ${String(err)}`; }));
[scannerSourceCell, scannerDestinationCell, scannerSecondSourceCell, scannerSecondDestinationCell, scannerFifoChainStep3SourceCell, scannerFifoChainStep3DestinationCell, scannerFifoChainStep4SourceCell, scannerFifoChainStep4DestinationCell, qrSourceCell, qrDestinationCell, qrSecondSourceCell, qrSecondDestinationCell, qrFifoChainStep3SourceCell, qrFifoChainStep3DestinationCell, qrFifoChainStep4SourceCell, qrFifoChainStep4DestinationCell].forEach(el => el?.addEventListener("change", renderQrCellSummaries));
btnScanQrPreview?.addEventListener("click", () => runScanQrPreview());
btnScanQrExecute?.addEventListener("click", () => runScanQrPreview("execute"));
scanQrValue?.addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter") return;
  ev.preventDefault();
  runScanQrPreview();
});
scanQrScannerSelect?.addEventListener("change", () => focusScanQrInput());
scanQrScannerManual?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    focusScanQrInput();
  }
});
btnRefreshScanQrHistory?.addEventListener("click", () => loadScanQrHistory().catch(err => { if (scanQrMsg) scanQrMsg.textContent = `Error: ${String(err)}`; }));
rackReservationState?.addEventListener("change", () => {
  if (!rackStatus) return;
  rackStatus.value = Number(rackReservationState?.value || 0) === 1 ? "reserved" : "available";
});
btnAddRackCustomField?.addEventListener("click", () => addRackCustomFieldRow());
btnValidateFifo?.addEventListener("click", () => validateFifoRequest().catch(err => { renderFifoPreview(null); fifoMsg.textContent = `Error: ${String(err)}`; }));
btnExecuteFifo?.addEventListener("click", () => executeFifoRequest().catch(err => fifoMsg.textContent = `Error: ${String(err)}`));
btnDirectPickSource?.addEventListener("click", () => {
  directMovePickMode = 'source';
  renderDirectMoveSelection();
  draw();
  directMsg.textContent = 'Haz clic en la celda origen dentro de la tabla.';
});
btnDirectPickDestination?.addEventListener("click", () => {
  directMovePickMode = 'destination';
  renderDirectMoveSelection();
  draw();
  directMsg.textContent = 'Haz clic en la celda destino dentro de la tabla.';
});
btnDirectClearSelection?.addEventListener("click", () => { clearDirectMoveSelection(); draw(); directMsg.textContent = 'Selección de movimiento directo limpiada.'; });
btnExecuteDirectMove?.addEventListener("click", () => executeDirectMoveRequest().catch(err => directMsg.textContent = `Error: ${String(err)}`));
orderJsonBox?.addEventListener("focus", () => { isEditingOrderJson = true; });
orderJsonBox?.addEventListener("blur", () => { isEditingOrderJson = false; });
orderStatusQueryRequestBox?.addEventListener("focus", () => { if (currentStatusQueryMode() === "manual") isEditingStatusQueryRequest = true; });
orderStatusQueryRequestBox?.addEventListener("blur", () => { isEditingStatusQueryRequest = false; });
orderStatusQueryRequestBox?.addEventListener("input", () => { if (currentStatusQueryMode() === "manual") isEditingStatusQueryRequest = true; });
orderStatusQueryResponseBox?.addEventListener("mouseenter", () => {
  debugConsoleHoverPaused = true;
  orderStatusQueryResponseBox.classList.add("paused");
});
orderStatusQueryResponseBox?.addEventListener("mouseleave", () => {
  debugConsoleHoverPaused = false;
  orderStatusQueryResponseBox.classList.remove("paused");
  if (debugConsolePendingEntries) {
    const pendingEntries = debugConsolePendingEntries;
    debugConsolePendingEntries = null;
    renderOrderStatusQuery(null, null, pendingEntries, true);
  }
});
orderStatusQueryMode?.addEventListener("change", () => {
  isEditingStatusQueryRequest = false;
  refreshStatusQueryEditor(true).catch(err => orderMsg.textContent = `Error: ${String(err)}`);
});
btnFormatStatusQuery?.addEventListener("click", () => {
  try {
    formatStatusQueryEditor();
    orderMsg.textContent = 'JSON formateado.';
  } catch (err) {
    orderMsg.textContent = `Error: ${String(err)}`;
  }
});
btnCopyStatusQuery?.addEventListener("click", async () => {
  try {
    await copyStatusQueryEditor();
    orderMsg.textContent = 'JSON copiado al portapapeles.';
  } catch (err) {
    orderMsg.textContent = `Error: ${String(err)}`;
  }
});
btnClearStatusQuery?.addEventListener("click", () => {
  clearStatusQueryEditor();
  orderMsg.textContent = 'Editor de consulta limpiado.';
});
btnRefreshOrders?.addEventListener("click", () => loadMovementOrders().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnSendStatusQuery?.addEventListener("click", () => runMonitorNow().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnRefreshOrderJson?.addEventListener("click", () => loadSelectedOrderJson().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnSaveOrderJson?.addEventListener("click", () => saveSelectedOrderJsonOverride().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnResetOrderJson?.addEventListener("click", () => resetSelectedOrderJsonOverride().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnRefreshOrderResponse?.addEventListener("click", () => loadSelectedOrderResponse().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnDispatchOrder?.addEventListener("click", () => dispatchSelectedOrder().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnCopyOrderJson?.addEventListener("click", async () => {
  try {
    const text = currentEditedJsonText() || (selectedOrderJsonPayload ? JSON.stringify(selectedOrderJsonPayload, null, 2) : '');
    if (!text) throw new Error('No hay JSON seleccionado.');
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      orderMsg.textContent = 'JSON copiado al portapapeles.';
      return;
    }
    throw new Error('El navegador no permite copiar automáticamente.');
  } catch (err) {
    orderMsg.textContent = `Error: ${String(err)}`;
  }
});
btnSimulateComplete?.addEventListener("click", () => simulateSelectedOrderComplete().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
btnUndoOrder?.addEventListener("click", () => {
  try {
    openHistoryUndoModal();
  } catch (err) {
    orderMsg.textContent = `Error: ${String(err)}`;
  }
});
operatorCancelReturnArea?.addEventListener("change", () => {
  const order = movementOrders.find(x => x.order_id === (actionModalContext.orderId || selectedOrderId)) || null;
  updateCancelReturnAreaHint(order);
});
operatorCancelMode?.addEventListener("change", () => {
  const order = movementOrders.find(x => x.order_id === (actionModalContext.orderId || selectedOrderId)) || null;
  updateCancelReturnAreaHint(order);
});
btnDeleteOrder?.addEventListener("click", () => deleteSelectedOrder().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
bindEditingLockEvents("cell", editingSectionElements("cell"));
bindEditingLockEvents("areas", editingSectionElements("areas"));
btnRefreshCellAfterEdit?.addEventListener("click", () => refreshAfterEditingLock("cell").catch(err => { if (cellMsg) cellMsg.textContent = `Error: ${String(err)}`; }));
btnRefreshAreasAfterEdit?.addEventListener("click", () => refreshAfterEditingLock("areas").catch(err => { if (areaMsg) areaMsg.textContent = `Error: ${String(err)}`; }));
operatorWindowSelect?.addEventListener("change", () => openOperatorWindow().catch(err => operatorWindowMsg.textContent = `Error: ${String(err)}`));
operatorWindowPassword?.addEventListener("change", () => { if (operatorWindowSelect?.value) openOperatorWindow().catch(err => operatorWindowMsg.textContent = `Error: ${String(err)}`); });
operatorWindowPassword?.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && operatorWindowSelect?.value) openOperatorWindow().catch(err => operatorWindowMsg.textContent = `Error: ${String(err)}`); });
adminWindowSelect?.addEventListener("change", () => { loadAdminOperatorWindowDetail(adminWindowSelect.value ? Number(adminWindowSelect.value) : null).catch(err => operatorWindowAdminMsg.textContent = `Error: ${String(err)}`); });
btnNewOperatorWindow?.addEventListener("click", () => { if (adminWindowSelect) adminWindowSelect.value = ""; clearAdminOperatorWindowForm(); operatorWindowAdminMsg.textContent = "Nueva ventana."; });
operatorButtonMode?.addEventListener("change", updateOperatorButtonModeFields);
btnSaveOperatorWindow?.addEventListener("click", () => saveAdminOperatorWindow().catch(err => operatorWindowAdminMsg.textContent = `Error: ${String(err)}`));
btnDeleteOperatorWindow?.addEventListener("click", () => deleteAdminOperatorWindow().catch(err => operatorWindowAdminMsg.textContent = `Error: ${String(err)}`));
btnSaveOperatorButton?.addEventListener("click", () => saveAdminOperatorButton().catch(err => operatorWindowAdminMsg.textContent = `Error: ${String(err)}`));
btnOperatorButtonPickSource?.addEventListener("click", () => { startOperatorButtonCellPick("source"); });
btnOperatorButtonPickDestination?.addEventListener("click", () => { startOperatorButtonCellPick("destination"); });
operatorButtonSourceCell?.addEventListener("change", renderOperatorButtonCellLabels);
operatorButtonDestinationCell?.addEventListener("change", renderOperatorButtonCellLabels);
btnOperatorActionCancel?.addEventListener("click", closeOperatorActionModal);
btnOperatorActionPickSource?.addEventListener("click", () => {
  operatorActionPickMode = 'source';
  draw();
  if (operatorWindowMsg) operatorWindowMsg.textContent = 'Haz clic en la matriz para elegir la celda origen.';
});
btnOperatorActionPickDestination?.addEventListener("click", () => {
  operatorActionPickMode = 'destination';
  draw();
  if (operatorWindowMsg) operatorWindowMsg.textContent = 'Haz clic en la matriz para elegir la celda destino.';
});
btnOperatorActionConfirm?.addEventListener("click", () => confirmOperatorActionModal().catch(err => operatorActionModalMsg.textContent = `Error: ${String(err)}`));
btnOperatorActionPreview?.addEventListener("click", async () => {
  try {
    await validateOperatorActionModal();
    if (operatorActionModalTitle) operatorActionModalTitle.textContent = operatorActionState?.button?.label || "Confirmar solicitud";
    operatorActionModal?.classList.remove("hidden");
  } catch (err) {
    if (operatorWindowMsg) operatorWindowMsg.textContent = `Error: ${String(err)}`;
  }
});
btnOperatorActionPanelClear?.addEventListener("click", clearOperatorActionPanel);
operatorActionMaterial?.addEventListener("change", syncOperatorActionStateFromInputs);
operatorActionAreaSelect?.addEventListener("change", syncOperatorActionStateFromInputs);
cellArea?.addEventListener("change", renderRackOptions);
cellRack?.addEventListener("change", syncCellReservationFromRackSelection);
btnCenterGrid?.addEventListener("click", () => { scheduleCanvasResize(); requestAnimationFrame(() => fitMapToScreen()); });
btnRobotMonitorRefresh?.addEventListener('click', () => { refreshRobotMonitor({ force: true }).catch(() => {}); });
window.addEventListener("resize", scheduleCanvasResize);
window.addEventListener("orientationchange", scheduleCanvasResize);
if ("ResizeObserver" in window) {
  const ro = new ResizeObserver(() => scheduleCanvasResize());
  const wrap = document.querySelector(".canvas-wrap");
  if (wrap) ro.observe(wrap);
  if (layoutEl) ro.observe(layoutEl);
  ro.observe(canvas);
}

if (btnAddOperatorPointField) btnAddOperatorPointField.addEventListener('click', () => {
  try {
    addAdminPointCustomFieldRow();
  } catch (err) {
    if (operatorWindowAdminMsg) operatorWindowAdminMsg.textContent = err.message || String(err);
  }
});
if (operatorButtonPointCustomFields) operatorButtonPointCustomFields.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-remove-point-custom]');
  if (!btn) return;
  const idx = Number(btn.dataset.removePointCustom);
  const rows = getAdminPointCustomFieldRows().filter((_, i) => i !== idx);
  renderOperatorPointCustomFieldEditor(rows);
});
if (operatorActionDynamicFields) operatorActionDynamicFields.addEventListener('input', (ev) => {
  const input = ev.target.closest('[data-point-field]');
  if (!input) return;
  setPointFieldValue(input.dataset.pointField, input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value || '');
});

(async function init() {
  try {
    reorderActionCards();
    initActionCardsLayout();
    setAdminUI(false);
    await restoreAdminSession();
    initSplitter();
    initVerticalSplitter();
    ensureSplitterMode();
    initRobotMonitorInteractions();
    resizeCanvasToContainer();
    await loadGridConfig();
    await loadLocations();
    await loadCatalog();
    await loadScanQrScanners().catch(err => { if (scanQrMsg) scanQrMsg.textContent = `No se pudo cargar scanners: ${String(err)}`; });
    await loadScanQrHistory({ quiet: true }).catch(err => { if (scanQrMsg) scanQrMsg.textContent = `No se pudo cargar historial QR: ${String(err)}`; });
    await loadOperatorWindows();
    await loadMovementOrders();
    if (runtimeAutoRefreshHandle) clearInterval(runtimeAutoRefreshHandle);
    runtimeAutoRefreshHandle = null;
    connectRuntimeSocket();
    fitMapToScreen();
    await refreshBackground();
    await loadPublicRcsMonitorConfig();
    if (robotMonitorEnabled) {
      await refreshRobotMonitor({ force: true });
    }
    clearAreaForm(); clearMaterialForm(); clearRackForm();
    await selectCell(0, 0);
    draw();
  } catch (err) {
    setConn("Error de conexión");
    alert(`No se pudo iniciar la app: ${String(err)}`);
  }
})();



window.addEventListener("beforeunload", () => { cleanupRuntimeSocket(); revokeQrRuleThumbUrls(); revokeQrRuleModalImageUrl(); });


renderRackCustomFieldEditor([]);

document.addEventListener("click", function (event) {
  const devBtn = event.target.closest("#createOldActiveOrderTestBtn");
  if (devBtn) {
    event.preventDefault();
    event.stopPropagation();
    createOldActiveOrderTestForSelectedRack().catch(err => {
      if (cleanupDiagnosisMsg) cleanupDiagnosisMsg.textContent = `Error: ${String(err)}`;
    });
    return;
  }

  const btn = event.target.closest("#cleanupDiagnosisBtn");

  if (!btn) return;

  event.preventDefault();
  event.stopPropagation();

  console.log("[cleanup] click detectado");

  openCleanupDiagnosisModal();
});

async function validateSoftwareUpdatePackage() {
  const fileInput = document.getElementById("softwareUpdateZip");
  const resultBox = document.getElementById("softwareUpdateResult");
  const applyBtn = document.getElementById("btnApplySoftwareUpdate");
  const restartBtn = document.getElementById("btnRestartSoftwareUpdate");

  console.log("[software-update] validateSoftwareUpdatePackage ejecutada");

  lastSoftwareUpdateValidation = null;
  if (applyBtn) {
    applyBtn.classList.add("hidden");
    applyBtn.style.display = "none";
  }
  if (restartBtn) {
    restartBtn.classList.add("hidden");
    restartBtn.style.display = "none";
  }

  if (!fileInput || !fileInput.files || !fileInput.files.length) {
    if (resultBox) resultBox.textContent = "Selecciona un archivo ZIP.";
    return;
  }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  if (resultBox) resultBox.textContent = "Validando paquete...";

  try {
    const response = await fetchWithAdminSession("/api/admin/software-update/validate", {
      method: "POST",
      headers: adminToken ? { "X-Admin-Token": adminToken } : {},
      body: formData
    });

    const data = await response.json();

    console.log("[software-update] respuesta", data);

    lastSoftwareUpdateValidation = data;
    if (applyBtn && data?.ok === true) {
      applyBtn.classList.remove("hidden");
      applyBtn.style.display = "";
    }

    if (resultBox) {
      resultBox.textContent = JSON.stringify(data, null, 2);
    }
  } catch (err) {
    console.error("[software-update] error", err);
    if (resultBox) resultBox.textContent = String(err);
  }
}

window.validateSoftwareUpdatePackage = validateSoftwareUpdatePackage;

async function applyValidatedSoftwareUpdate() {
  const resultBox = document.getElementById("softwareUpdateResult");
  const applyBtn = document.getElementById("btnApplySoftwareUpdate");
  const restartBtn = document.getElementById("btnRestartSoftwareUpdate");

  if (!lastSoftwareUpdateValidation || lastSoftwareUpdateValidation.ok !== true) {
    if (resultBox) resultBox.textContent = "Primero valida correctamente un paquete ZIP.";
    return;
  }

  const confirmed = window.confirm("Esto reemplazará archivos del software actual usando el paquete validado. Se creará backup y no se reiniciará la app. ¿Continuar?");
  if (!confirmed) return;

  if (resultBox) resultBox.textContent = "Aplicando actualización validada...";
  if (applyBtn) applyBtn.disabled = true;

  try {
    const response = await fetchWithAdminSession("/api/admin/software-update/apply", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(adminToken ? { "X-Admin-Token": adminToken } : {})
      },
      body: JSON.stringify({ staging_id: lastSoftwareUpdateValidation.staging_id || null })
    });
    const data = await response.json();
    console.log("[software-update] apply respuesta", data);
    if (resultBox) resultBox.textContent = JSON.stringify(data, null, 2);
    if (applyBtn && data?.ok === true) {
      applyBtn.classList.add("hidden");
      applyBtn.style.display = "none";
    }
    if (restartBtn && data?.ok === true) {
      restartBtn.classList.remove("hidden");
      restartBtn.style.display = "";
    }
  } catch (err) {
    console.error("[software-update] apply error", err);
    if (resultBox) resultBox.textContent = String(err);
  } finally {
    if (applyBtn) applyBtn.disabled = false;
  }
}

window.applyValidatedSoftwareUpdate = applyValidatedSoftwareUpdate;

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForSoftwareReconnect(resultBox = document.getElementById("softwareUpdateResult")) {
  let attempts = 0;
  await wait(2000);
  while (attempts < 120) {
    attempts += 1;
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (response.ok) {
        const data = await response.json().catch(() => ({}));
        if (resultBox) {
          resultBox.textContent += `\n\nAplicación reconectada.\n${JSON.stringify(data, null, 2)}`;
        }
        return true;
      }
    } catch (_err) {
      // La app puede estar reiniciando; se reintenta abajo.
    }
    if (resultBox) resultBox.textContent = "Esperando reconexión...";
    await wait(2000);
  }
  if (resultBox) resultBox.textContent += "\n\nNo se pudo confirmar reconexión por /api/health.";
  return false;
}

async function restartSoftwareUpdateApp() {
  const resultBox = document.getElementById("softwareUpdateResult");
  const restartBtn = document.getElementById("btnRestartSoftwareUpdate");

  if (!window.confirm("La aplicación intentará reiniciarse ahora. No se aplicarán cambios adicionales. ¿Continuar?")) return;

  if (resultBox) resultBox.textContent = "Solicitando reinicio...";
  if (restartBtn) restartBtn.disabled = true;

  try {
    const response = await fetchWithAdminSession("/api/admin/software-update/restart", {
      method: "POST",
      headers: adminToken ? { "X-Admin-Token": adminToken } : {}
    });
    const data = await response.json();
    console.log("[software-update] restart respuesta", data);
    if (resultBox) resultBox.textContent = JSON.stringify(data, null, 2);
    if (!data?.ok) return;
    if (data?.mode === "manual") {
      if (resultBox) resultBox.textContent = data.message || "Actualización aplicada. Reinicie manualmente la aplicación.";
      return;
    }
    if (resultBox) resultBox.textContent = "Esperando reconexión...";
    await waitForSoftwareReconnect();
  } catch (err) {
    console.error("[software-update] restart error", err);
    if (resultBox) resultBox.textContent = String(err);
  } finally {
    if (restartBtn) restartBtn.disabled = false;
  }
}

window.restartSoftwareUpdateApp = restartSoftwareUpdateApp;

document.addEventListener("click", async function (event) {
  const btn = event.target.closest("#btnValidateSoftwareUpdate");
  if (!btn) return;

  event.preventDefault();
  event.stopPropagation();

  console.log("[software-update] validar paquete click delegado");

  await window.validateSoftwareUpdatePackage();
});

document.addEventListener("click", async function (event) {
  const btn = event.target.closest("#btnApplySoftwareUpdate");
  if (!btn) return;

  event.preventDefault();
  event.stopPropagation();

  console.log("[software-update] aplicar update click delegado");

  await window.applyValidatedSoftwareUpdate();
});

document.addEventListener("click", async function (event) {
  const btn = event.target.closest("#btnRestartSoftwareUpdate");
  if (!btn) return;

  event.preventDefault();
  event.stopPropagation();

  console.log("[software-update] reiniciar app click delegado");

  await window.restartSoftwareUpdateApp();
});
