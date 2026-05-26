console.log("[startup] app.js cargado");
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
};

const DB_W = 100;
const DB_H = 100;
let GRID_W = 100;
let GRID_H = 100;
let locations = new Array(DB_W * DB_H).fill(null);
let selected = { x: 0, y: 0 };
let adminToken = null;
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
let RUNTIME_AUTO_REFRESH_MS = 5000;
let RUNTIME_SOCKET_RECONNECT_MS = 3000;
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
const rcsTaskMonitorInterval = $("rcsTaskMonitorInterval");
const rcsAgvMonitorInterval = $("rcsAgvMonitorInterval");
const cleanupMinAgeMinutes = $("cleanupMinAgeMinutes");
const forceReleaseMinAgeMinutes = $("forceReleaseMinAgeMinutes");
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
const btnSaveCell = $("btnSaveCell");
const cellMsg = $("cellMsg");

const areaId = $("areaId");
const areaCode = $("areaCode");
const areaName = $("areaName");
const areaType = $("areaType");
const areaColor = $("areaColor");
const areaPriority = $("areaPriority");
const areaActive = $("areaActive");
const areaDescription = $("areaDescription");
const btnSaveArea = $("btnSaveArea");
const btnNewArea = $("btnNewArea");
const btnDeleteArea = $("btnDeleteArea");
const areasList = $("areasList");
const areaMsg = $("areaMsg");

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
  "card-history",
  "card-cell",
  "card-areas",
  "card-materials",
  "card-racks",
  "card-operator-windows",
  "card-general",
  "card-direct-move",
  "card-fifo",
  "card-client-bg",
  "card-debug-rcs",
  "card-config-rcs",
  "card-admin-password",
  "card-admin-login",
];
const NON_ADMIN_VISIBLE_CARD_IDS = new Set(["card-workstation", "card-history", "card-admin-login"]);
const DEFAULT_EXPANDED_CARD_IDS = new Set(["card-workstation", "card-history", "card-admin-login"]);

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
  "card-history",
  "card-cell",
  "card-areas",
  "card-materials",
  "card-racks",
  "card-operator-windows",
  "card-general",
  "card-direct-move",
  "card-fifo",
  "card-client-bg",
  "card-debug-rcs",
  "card-config-rcs",
  "card-admin-password",
  "card-admin-login",
];
const PUBLIC_CARD_IDS = new Set(["card-workstation", "card-history", "card-admin-login"]);
const ACTION_TAB_STORAGE_KEY = "agv_side_panel_active_tab_v1";
const ACTION_CARD_TABS = [
  {
    key: "operation",
    label: "Operaci\u00f3n",
    cards: ["card-workstation", "card-history"],
  },
  {
    key: "configuration",
    label: "Configuraci\u00f3n",
    cards: ["card-cell", "card-areas", "card-materials", "card-racks", "card-operator-windows"],
  },
  {
    key: "advanced",
    label: "Configuraci\u00f3n avanzada",
    cards: ["card-general", "card-direct-move", "card-fifo", "card-client-bg", "card-debug-rcs", "card-config-rcs", "card-admin-password", "card-admin-login"],
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
function draw() {
  ctx.clearRect(0, 0, canvasCssW, canvasCssH);
  ctx.fillStyle = "#0a0f18";
  ctx.fillRect(0, 0, canvasCssW, canvasCssH);
  drawBackgroundImage();
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

  ctx.fillStyle = "rgba(147,164,199,0.9)";
  ctx.font = `${Math.max(12, 12 * scale)}px ui-sans-serif`;
  ctx.fillText(`Vista: ${GRID_W}×${GRID_H} | Zoom: ${scale.toFixed(2)}x`, 10, 18);
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
function zoomAtPoint(nextScale, mx, my) {
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
  const res = await fetch(url, finalOptions);
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
    { key: "safe_reason", label: "Motivo" },
  ], { type: "rack", section: "old-active-rack", idKey: "rack_id", emptyText: "Sin racks bloqueados por órdenes activas viejas." });
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
    const response = await fetch("/api/admin/cleanup-diagnosis", {
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
    const response = await fetch(downloadUrl, {
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
    "operatorActionAreaSelect", "operatorActionMaterial", "operatorActionAgv", "operatorActionTaskTyp"
  ]);

  const adminOnlyIds = new Set([
    "operatorWindowId", "operatorWindowName", "operatorWindowActive", "operatorWindowBgColor", "operatorWindowButtonCount", "operatorWindowPasswordAdmin", "btnSaveOperatorWindow", "btnNewOperatorWindow", "adminWindowSelect",
    "operatorButtonIndex", "operatorButtonActive", "operatorButtonLabel", "operatorButtonColor", "operatorButtonMode", "operatorButtonPriority", "operatorButtonSourceArea", "operatorButtonDestinationArea", "operatorButtonPointDestinationArea", "operatorButtonMaterial", "operatorButtonSourceCell", "operatorButtonDestinationCell", "btnOperatorButtonPickSource", "btnOperatorButtonPickDestination", "operatorButtonAgv", "operatorButtonTaskTyp", "operatorButtonComment", "btnSaveOperatorButton", "btnAddOperatorPointField"
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
  dispRows.value = GRID_H;
  dispCols.value = GRID_W;
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
}
async function loadCatalog() {
  catalog = await fetchJson(API.catalog);
  renderAreaOptions();
  renderMaterialOptions();
  renderRackOptions();
  renderAreaList();
  renderMaterialList();
  renderRackList();
}

function applyRuntimeSnapshotData(snapshot, preferredOrderId = null) {
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
  const selectedLoc = getLocationAtGrid(selected.x, selected.y);
  if (selectedLoc) fillCellForm(selectedLoc);
  refreshRackReservationFieldsForSelection();

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

function renderAreaOptions() {
  const current = cellArea.value;
  cellArea.innerHTML = `<option value="">Sin área</option>` + catalog.areas.map(a => `<option value="${a.id}">${a.code} - ${a.name}</option>`).join("");
  if ([...cellArea.options].some(o => o.value === current)) cellArea.value = current;

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
function renderRackOptions() {
  const current = cellRack.value;
  const racks = availableRacksForCell();
  cellRack.innerHTML = `<option value="">Sin rack</option>` + racks.map(r => `<option value="${r.id}">${r.code}${r.material_group_name ? ` - ${r.material_group_name}` : ""}</option>`).join("");
  if ([...cellRack.options].some(o => o.value === current)) cellRack.value = current;
}
function renderAreaList() {
  areasList.innerHTML = catalog.areas.map(a => `<button type="button" class="list-item" data-kind="area" data-id="${a.id}"><span class="swatch" style="background:${a.color}"></span><b>${a.code}</b> ${a.name}<small>${a.area_type} · prioridad ${a.priority}</small></button>`).join("") || `<div class="small">Sin áreas capturadas.</div>`;
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
function clearAreaForm() {
  areaId.value = ""; areaCode.value = ""; areaName.value = ""; areaType.value = "almacen"; areaColor.value = "#4f46e5"; areaPriority.value = 0; areaActive.value = 1; areaDescription.value = "";
}
function loadAreaForm(id) {
  const item = catalog.areas.find(a => Number(a.id) === Number(id));
  if (!item) return;
  areaId.value = item.id; areaCode.value = item.code; areaName.value = item.name; areaType.value = item.area_type; areaColor.value = item.color || "#4f46e5"; areaPriority.value = item.priority ?? 0; areaActive.value = String(item.is_active ?? 1); areaDescription.value = item.description || "";
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
  renderRackOptions();
  const selectedLoc = getLocationAtGrid(selected.x, selected.y);
  if (selectedLoc) fillCellForm(selectedLoc);
  refreshRackReservationFieldsForSelection();
  syncCellReservationFromRackSelection();
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
  if (rcsTaskMonitorInterval) rcsTaskMonitorInterval.value = String(Number(data.task_monitor_interval_seconds ?? 3));
  if (rcsAgvMonitorInterval) rcsAgvMonitorInterval.value = String(Number(data.agv_monitor_interval_seconds ?? 5));
  if (cleanupMinAgeMinutes) cleanupMinAgeMinutes.value = String(Number(data.cleanup_min_age_minutes ?? 30));
  if (forceReleaseMinAgeMinutes) forceReleaseMinAgeMinutes.value = String(Number(data.force_release_min_age_minutes ?? 20));
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
    task_monitor_interval_seconds: safeNumberInput(rcsTaskMonitorInterval, 3),
    agv_monitor_interval_seconds: safeNumberInput(rcsAgvMonitorInterval, 5),
    cleanup_min_age_minutes: Math.max(1, Math.round(safeNumberInput(cleanupMinAgeMinutes, 30))),
    force_release_min_age_minutes: Math.max(1, Math.round(safeNumberInput(forceReleaseMinAgeMinutes, 20))),
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
async function selectCell(x, y) {
  selected = { x, y };
  fillCellForm(locations[idx(x, y)]);
  renderDirectMoveSelection();
  draw();
}
async function saveSelectedCell() {
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
  await refreshReservationUiState();
  cellMsg.textContent = `Celda (${selected.x}, ${selected.y}) guardada.`;
}
async function saveArea() {
  const payload = { code: areaCode.value.trim(), name: areaName.value.trim(), description: areaDescription.value.trim() || null, color: areaColor.value || "#4f46e5", area_type: areaType.value.trim() || "almacen", is_active: Number(areaActive.value || 1), priority: Number(areaPriority.value || 0) };
  const url = areaId.value ? API.adminArea(Number(areaId.value)) : API.adminAreas;
  const method = areaId.value ? "PUT" : "POST";
  await fetchJson(url, { method, headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify(payload) });
  await loadCatalog();
  areaMsg.textContent = areaId.value ? "Área actualizada." : "Área creada.";
  if (!areaId.value) clearAreaForm();
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
    return `<button type="button" class="list-item" data-order-id="${order.order_id}"${active}>
      <div><b>${order.order_code}</b></div>
      <div class="small">${auditText} · ${order.rack_code}</div>
      <div class="small">Orden: ${order.order_type || '-'} · AGV: ${order.agv_code || '-'} · Tipo tarea: ${order.task_typ || '-'}</div>
      <div class="small">${areaText}</div>
      <div class="small">${new Date(order.created_at).toLocaleString()}</div>
    </button>`;
  }).join('');
  ordersList.querySelectorAll('[data-order-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      selectedOrderId = Number(btn.dataset.orderId);
      renderOrdersList();
      renderSelectedOrderDetail(movementOrders.find(x => x.order_id === selectedOrderId) || null);
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
  if (orderStatusQueryResponseBox) orderStatusQueryResponseBox.innerHTML = formatStatusLogEntries(logEntries);
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
  orderDetailBox.innerHTML = `
    <div class="small"><b>Orden:</b> ${order.order_code}</div>
    <div class="small"><b>Estado:</b> ${order.status}</div>
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
  if (btnSimulateComplete) btnSimulateComplete.disabled = !order.can_simulate_complete;
  if (btnUndoOrder) btnUndoOrder.disabled = !order.can_undo;
  if (btnSimulateComplete) btnSimulateComplete.title = order.can_simulate_complete ? '' : unavailableReason;
  if (btnUndoOrder) btnUndoOrder.title = order.can_undo ? '' : unavailableReason;
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

async function undoSelectedOrder() {
  if (!selectedOrderId) throw new Error('Selecciona una tarea.');
  const result = await fetchJson(API.movementOrderUndo(selectedOrderId), { method: 'POST' });
  orderMsg.textContent = `Orden ${result.order_code} cancelada desde historial con cancelTask forceCancel=0 y reversa local del almacén. Nuevo estado: ${result.status}.`;
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
  if (e.button === 1 || e.button === 2) {
    e.preventDefault(); dragging = true; dragStart = { x: e.clientX, y: e.clientY, offX: cam.offX, offY: cam.offY }; return;
  }
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const g = canvasToGrid(e.clientX - rect.left, e.clientY - rect.top);
  if (!g) return;
  const loc = getLocationAtGrid(g.x, g.y);
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
  selectCell(g.x, g.y);
});
canvas.addEventListener("click", (e) => {
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
  const g = canvasToGrid(hoverPointer.x, hoverPointer.y);
  hoverCell = g;
  updateCellHoverTooltip();
  draw();
});
window.addEventListener("mouseup", () => { dragging = false; });
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
  const loc = hoverCell ? getLocationAtGrid(hoverCell.x, hoverCell.y) : null;
  if (!hoverCell || !loc || Number(loc.is_visible ?? 1) !== 1) {
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
  return ['direct_move_config', 'point_to_area'].includes(operatorActionState?.button?.action_mode || '');
}
function operatorActionRequiresImmediatePreview() {
  return ['fifo', 'direct_move'].includes(operatorActionState?.button?.action_mode || '');
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
  return operatorActionIsDirectMoveConfig();
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
  if (operatorActionPreview) operatorActionPreview.textContent = 'Sin datos.';
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = '';
  if (operatorActionModalTitle) operatorActionModalTitle.textContent = 'Confirmar solicitud';
  if (btnOperatorActionConfirm) btnOperatorActionConfirm.textContent = 'Enviar';
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
    const result = await fetchJson(API.movementOrderUndo(orderId), { method: 'POST' });
    closeOperatorActionModal();
    orderMsg.textContent = `Orden ${result.order_code} cancelada desde historial con cancelTask forceCancel=0 y reversa local del almacén. Nuevo estado: ${result.status}.`;
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

function renderHistoryUndoPreviewText(order) {
  if (!order) return 'Sin orden seleccionada.';
  const remoteActive = !!(order.remote_task_code && ['pending_dispatch', 'dispatched', 'in_progress'].includes(String(order.status || '')));
  const currentCell = order.current_cell ? (order.current_cell.code || `(${order.current_cell.x}, ${order.current_cell.y})`) : 'Sin ubicación';
  const sourceCell = order.source_cell ? (order.source_cell.code || `(${order.source_cell.x}, ${order.source_cell.y})`) : 'Sin origen';
  const destinationCell = order.destination_cell ? (order.destination_cell.code || `(${order.destination_cell.x}, ${order.destination_cell.y})`) : 'Sin destino';
  const payload = {
    accion: 'cancelar_deshacer_desde_historial',
    order_id: order.order_id,
    order_code: order.order_code,
    forceCancel: '0',
    matterArea: order.source_cell?.code || '',
    remote_task_code: order.remote_task_code || '',
    remote_cancel: remoteActive,
    deshacer_movimiento_local: true,
  };
  return [
    'Acción: Cancelar / deshacer desde historial',
    `Orden: ${order.order_code || ''}`,
    `Estado actual: ${order.status || ''}`,
    `Rack: ${order.rack_code || ''}`,
    `Origen: ${sourceCell}`,
    `Destino: ${destinationCell}`,
    `Ubicación actual rack: ${currentCell}`,
    `Cancelación remota en RCS: ${remoteActive ? 'Sí' : 'No (solo deshacer local)'}`,
    '',
    'JSON / parámetros de la acción:',
    JSON.stringify(payload, null, 2),
  ].join('\n');
}

function openHistoryUndoModal() {
  const order = movementOrders.find(x => x.order_id === selectedOrderId) || null;
  if (!order) throw new Error('Selecciona una tarea.');
  if (btnUndoOrder?.disabled) throw new Error(historyOrderUnavailableReason(order) || 'La tarea seleccionada no permite cancelar o deshacer.');
  actionModalContext = { mode: 'history_undo', orderId: order.order_id };
  if (operatorActionModalTitle) operatorActionModalTitle.textContent = 'Confirmar cancelación / deshacer';
  if (btnOperatorActionConfirm) btnOperatorActionConfirm.textContent = 'Enviar';
  if (operatorActionPreview) operatorActionPreview.textContent = renderHistoryUndoPreviewText(order);
  if (operatorActionModalMsg) operatorActionModalMsg.textContent = 'Revisa la previsualización y confirma para enviar la cancelación o el deshacer.';
  operatorActionModal?.classList.remove('hidden');
}

async function openOperatorActionModal(button) {
  operatorActionState = { button, preview: null, source_cell_id: button?.source_cell_id || null, destination_cell_id: button?.destination_cell_id || null, destination_area_id: button?.destination_area_id || null, material_group_id: null, lot: '', quantity: null, manufacturer_code: '', comment: '' };
  operatorActionPickMode = null;
  if (operatorActionPanel) operatorActionPanel.classList.remove('hidden');
  if (operatorActionPanelTitle) operatorActionPanelTitle.textContent = button.label || 'Parámetros de la acción';
  if (operatorActionPanelModeLabel) operatorActionPanelModeLabel.textContent = button.action_mode === 'point_to_area' ? '' : `Modo: ${actionModeLabel(button.action_mode)}`;
  if (operatorActionAgv) operatorActionAgv.value = button.agv_code || '';
  if (operatorActionTaskTyp) operatorActionTaskTyp.value = button.task_typ || 'A01';
  renderOperatorActionMaterialOptions();
  renderOperatorActionCellLabels();
  if (operatorActionPanel) operatorActionPanel.classList.toggle('hidden', !operatorActionUsesInlineCells());
  if (button.action_mode === 'direct_move' && (!operatorActionState.source_cell_id || !operatorActionState.destination_cell_id)) {
    if (operatorWindowMsg) operatorWindowMsg.textContent = 'Este botón de movimiento directo no tiene configuradas la celda origen y la celda destino.';
    return;
  }
  if (operatorActionUsesInlineCells()) {
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
    setAdminUI(true);
    adminMsg.textContent = "Admin habilitado.";
    await adminLoadClientIp();
    await adminLoadRcsConfig();
    await loadCatalog();
    await loadCleanupHealth();
    await loadAdminOperatorWindows();
    await loadBackupStatus();
    fillCellForm(locations[idx(selected.x, selected.y)]);
    repairActionTabsLayout();
  } catch (err) { adminMsg.textContent = `Error: ${String(err)}`; }
});
btnAdminLock?.addEventListener("click", () => { adminToken = null; restorePendingRestart = false; setAdminUI(false); clearAdminOperatorWindowForm(); if (cleanupHealthBadge) cleanupHealthBadge.classList.add("hidden"); adminMsg.textContent = "Admin bloqueado."; });
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
document.addEventListener("keydown", (ev) => {
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
    fitGridToScreen();
    draw();
    if (selected.x >= GRID_W || selected.y >= GRID_H) selectCell(0, 0);
    adminMsg.textContent = `Configuración general guardada. Vista actualizada a ${GRID_W}×${GRID_H}.`;
  } catch (err) {
    adminMsg.textContent = `Error: ${String(err)}`;
  }
});
btnHideConfiguredRange?.addEventListener("click", () => adminHideConfiguredRange().catch(err => rangeMsg.textContent = `Error: ${String(err)}`));
btnShowConfiguredRange?.addEventListener("click", () => adminShowConfiguredRange().catch(err => rangeMsg.textContent = `Error: ${String(err)}`));
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
btnChangePwd?.addEventListener("click", async () => {
  try {
    await fetchJson(API.adminChangePwd, { method: "POST", headers: { "Content-Type": "application/json", ...fetchHeaders() }, body: JSON.stringify({ old_password: oldPwd.value || "", new_password: newPwd.value || "" }) });
    oldPwd.value = ""; newPwd.value = ""; adminMsg.textContent = "Contraseña cambiada correctamente.";
  } catch (err) { adminMsg.textContent = `Error: ${String(err)}`; }
});
btnSaveCell?.addEventListener("click", () => saveSelectedCell().catch(err => cellMsg.textContent = `Error: ${String(err)}`));
btnNewArea?.addEventListener("click", clearAreaForm);
btnSaveArea?.addEventListener("click", () => saveArea().catch(err => areaMsg.textContent = `Error: ${String(err)}`));
btnDeleteArea?.addEventListener("click", () => deleteArea().catch(err => areaMsg.textContent = `Error: ${String(err)}`));
btnNewMaterial?.addEventListener("click", clearMaterialForm);
btnSaveMaterial?.addEventListener("click", () => saveMaterial().catch(err => materialMsg.textContent = `Error: ${String(err)}`));
btnDeleteMaterial?.addEventListener("click", () => deleteMaterial().catch(err => materialMsg.textContent = `Error: ${String(err)}`));
btnNewRack?.addEventListener("click", clearRackForm);
btnSaveRack?.addEventListener("click", () => saveRack().catch(err => rackMsg.textContent = `Error: ${String(err)}`));
btnDeleteRack?.addEventListener("click", () => deleteRack().catch(err => rackMsg.textContent = `Error: ${String(err)}`));
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
btnDeleteOrder?.addEventListener("click", () => deleteSelectedOrder().catch(err => orderMsg.textContent = `Error: ${String(err)}`));
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
btnCenterGrid?.addEventListener("click", () => { scheduleCanvasResize(); requestAnimationFrame(() => fitGridToScreen()); });
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
    initSplitter();
    initVerticalSplitter();
    ensureSplitterMode();
    initRobotMonitorInteractions();
    resizeCanvasToContainer();
    await loadGridConfig();
    await loadLocations();
    await loadCatalog();
    await loadOperatorWindows();
    await loadMovementOrders();
    if (runtimeAutoRefreshHandle) clearInterval(runtimeAutoRefreshHandle);
    runtimeAutoRefreshHandle = null;
    connectRuntimeSocket();
    fitGridToScreen();
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



window.addEventListener("beforeunload", () => { cleanupRuntimeSocket(); });


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
    const response = await fetch("/api/admin/software-update/validate", {
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
    const response = await fetch("/api/admin/software-update/apply", {
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
    const response = await fetch("/api/admin/software-update/restart", {
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
