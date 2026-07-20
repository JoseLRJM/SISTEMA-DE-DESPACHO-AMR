(function () {
  const STORAGE_TERMINAL = "agv_pda_terminal_code";
  const STORAGE_KEY = "agv_pda_terminal_key";
  const LEGACY_STORAGE_TERMINAL = "pdaTerminalCode";
  const LEGACY_STORAGE_KEY = "pdaTerminalKey";
  const HISTORY_LIMIT = 15;
  const DUPLICATE_WINDOW_MS = 1600;
  const SCAN_IDLE_SUBMIT_MS = 500;

  const $ = (id) => document.getElementById(id);
  const els = {
    eyebrow: document.querySelector(".eyebrow"),
    statusBadge: $("statusBadge"),
    pdaModeBadge: $("pdaModeBadge"),
    pdaModeTerminal: $("pdaModeTerminal"),
    pdaModeScanner: $("pdaModeScanner"),
    btnPdaAdminAccess: $("btnPdaAdminAccess"),
    btnPdaExitDeveloper: $("btnPdaExitDeveloper"),
    pdaAdminModal: $("pdaAdminModal"),
    pdaAdminPassword: $("pdaAdminPassword"),
    pdaAdminError: $("pdaAdminError"),
    btnPdaAdminLogin: $("btnPdaAdminLogin"),
    btnPdaCloseAdminModal: $("btnPdaCloseAdminModal"),
    btnToggleConfig: $("btnToggleConfig"),
    configBody: $("configBody"),
    terminalCode: $("terminalCode"),
    terminalKey: $("terminalKey"),
    btnSaveConfig: $("btnSaveConfig"),
    btnCancelConfig: $("btnCancelConfig"),
    btnClearConfig: $("btnClearConfig"),
    configMsg: $("configMsg"),
    qrInput: $("qrInput"),
    btnRefocus: $("btnRefocus"),
    scanMode: $("scanMode"),
    executeWarning: $("executeWarning"),
    qrCatalogSelect: $("qrCatalogSelect"),
    qrCatalogMsg: $("qrCatalogMsg"),
    qrCatalogImage: $("qrCatalogImage"),
    qrCatalogDetails: $("qrCatalogDetails"),
    qrCatalogAlias: $("qrCatalogAlias"),
    qrCatalogValue: $("qrCatalogValue"),
    qrCatalogType: $("qrCatalogType"),
    qrCatalogMatch: $("qrCatalogMatch"),
    qrCatalogAction: $("qrCatalogAction"),
    qrCatalogMaterial: $("qrCatalogMaterial"),
    qrCatalogSource: $("qrCatalogSource"),
    qrCatalogDestination: $("qrCatalogDestination"),
    qrCatalogRequiresScanner: $("qrCatalogRequiresScanner"),
    btnHideQrCatalog: $("btnHideQrCatalog"),
    resultPanel: $("resultPanel"),
    resultTitle: $("resultTitle"),
    resultSummary: $("resultSummary"),
    resultSections: $("resultSections"),
    resultDetails: $("resultDetails"),
    resultJson: $("resultJson"),
    resQr: $("resQr"),
    resTerminal: $("resTerminal"),
    resScanner: $("resScanner"),
    resAction: $("resAction"),
    resAlias: $("resAlias"),
    resType: $("resType"),
    resMaterial: $("resMaterial"),
    resRack: $("resRack"),
    resSource: $("resSource"),
    resDestination: $("resDestination"),
    resOrder: $("resOrder"),
    resEvent: $("resEvent"),
    resError: $("resError"),
    pdaHistoryToggle: $("pdaHistoryToggle"),
    pdaHistoryCount: $("pdaHistoryCount"),
    pdaHistoryToggleIcon: $("pdaHistoryToggleIcon"),
    pdaHistoryContent: $("pdaHistoryContent"),
    localHistory: $("localHistory"),
    btnClearHistory: $("btnClearHistory"),
    pdaActionModal: $("pdaActionModal"),
    pdaActionSummary: $("pdaActionSummary"),
    pdaActionConfirm: $("pdaActionConfirm"),
    pdaActionError: $("pdaActionError"),
    pdaActionButtons: $("pdaActionButtons"),
    pdaActionConfirmButtons: $("pdaActionConfirmButtons"),
    btnPdaCancelOrder: $("btnPdaCancelOrder"),
    btnPdaCancelReturnOrder: $("btnPdaCancelReturnOrder"),
    btnPdaCloseActionModal: $("btnPdaCloseActionModal"),
    btnPdaConfirmCancel: $("btnPdaConfirmCancel"),
    btnPdaConfirmCancelReturn: $("btnPdaConfirmCancelReturn"),
    btnPdaBackAction: $("btnPdaBackAction"),
    pdaExecuteConfirmModal: $("pdaExecuteConfirmModal"),
    pdaExecuteConfirmSummary: $("pdaExecuteConfirmSummary"),
    pdaExecuteConfirmError: $("pdaExecuteConfirmError"),
    btnPdaExecuteConfirm: $("btnPdaExecuteConfirm"),
    btnPdaExecuteCancel: $("btnPdaExecuteCancel"),
  };

  let isProcessing = false;
  let lastSubmittedQr = "";
  let lastSubmittedAt = 0;
  let scanIdleTimer = null;
  let lastSubmittedInputValue = "";
  let activeSubmission = null;
  let submissionSequence = 0;
  let lastSubmission = { value: "", timestamp: 0 };
  let localHistory = [];
  let terminalConfig = null;
  let pdaAdminToken = null;
  let pdaInterfaceMode = "operator";
  let terminalValidationState = "unconfigured";
  let terminalValidationMessage = "";
  let configDraft = { terminalCode: "", terminalKey: "" };
  let configSnapshot = { terminalCode: "", terminalKey: "", validationState: "unconfigured", terminalConfig: null };
  let isConfigPanelOpen = false;
  let isConfigSaving = false;
  let pathTerminalHandled = false;
  let scanModeSelectedByUser = false;
  let availableQrRules = [];
  let selectedQrRule = null;
  let qrCatalogImageUrl = null;
  let qrCatalogRequestSequence = 0;
  let qrCatalogLoadedForTerminal = "";
  let pdaHistory = [];
  let isPdaHistoryExpanded = false;
  let historyLoading = false;
  let historyRefreshTimer = null;
  let historyAuthPaused = false;
  let selectedHistoryItem = null;
  let selectedCancelAction = "";
  const pendingCancelOrders = new Set();
  let pendingPreviewForExecution = null;
  let pendingQrValue = "";
  let isConfirmModalOpen = false;
  let isExecutingConfirmed = false;

  function text(value, fallback = "-") {
    const raw = value == null ? "" : String(value).trim();
    return raw || fallback;
  }

  function entityLabel(entity) {
    if (!entity || typeof entity !== "object") return "-";
    const code = text(entity.code || entity.scanner_code, "");
    const name = text(entity.name || entity.qr_alias, "");
    if (code && name) return `${code} - ${name}`;
    return code || name || "-";
  }

  function cellLabel(cell) {
    if (!cell || typeof cell !== "object" || !cell.id) return "-";
    const code = text(cell.code, "");
    const coords = cell.x != null && cell.y != null ? `(${cell.x},${cell.y})` : "";
    return code && coords ? `${code} ${coords}` : (code || coords || "-");
  }

  function routeTerminalCode() {
    return text(window.PDA_TERMINAL_CODE_FROM_PATH, "");
  }

  function clearRouteTerminalCode() {
    pathTerminalHandled = true;
    try { window.PDA_TERMINAL_CODE_FROM_PATH = ""; } catch (_) {}
  }

  function normalizePdaUrl() {
    try {
      if (window.location?.pathname !== "/pda") {
        window.history.replaceState({}, "", "/pda");
      }
    } catch (_) {}
  }

  function isBasicTerminalCodeCandidate(value) {
    const code = text(value, "");
    return !!code && code.length <= 128 && !/[\/\\?#\s]/.test(code);
  }

  function focusScanner() {
    if (els.qrInput?.disabled) return;
    window.setTimeout(() => {
      try {
        els.qrInput.focus();
        els.qrInput.select();
      } catch (_) {}
    }, 0);
  }

  function focusConfig() {
    window.setTimeout(() => {
      try {
        els.terminalCode.focus();
        els.terminalCode.select();
      } catch (_) {}
    }, 0);
  }

  function setStatus(kind, label) {
    els.statusBadge.className = `status-badge ${kind}`;
    els.statusBadge.textContent = label;
  }

  function setResultClass(kind) {
    els.resultPanel.classList.remove("idle", "ok", "success", "preview", "warning", "duplicate", "error", "processing");
    els.resultPanel.classList.add(kind);
  }

  function setField(el, value) {
    if (el) el.textContent = text(value);
  }

  function firstValue(...values) {
    for (const value of values) {
      if (value == null) continue;
      if (typeof value === "string" && !value.trim()) continue;
      return value;
    }
    return "";
  }

  function readableValue(value) {
    if (value == null) return "";
    if (typeof value === "boolean") return value ? "Sí" : "No";
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return value.trim();
    if (Array.isArray(value)) return value.map(readableValue).filter(Boolean).join(", ");
    if (typeof value === "object") {
      const code = firstValue(value.code, value.scanner_code, value.qr_value, value.rack_code, value.cell_code, value.terminal_code, value.id);
      const name = firstValue(value.name, value.qr_alias, value.description, value.area_name);
      if (code && name && String(code) !== String(name)) return `${readableValue(code)} - ${readableValue(name)}`;
      return readableValue(code || name);
    }
    return String(value);
  }

  function parseBackendUtcDate(value) {
    const raw = readableValue(value);
    if (!raw) return null;
    let normalized = raw;
    const hasTimezone = /Z$/i.test(normalized) || /[+-]\d{2}:\d{2}$/.test(normalized);
    if (!hasTimezone) normalized += "Z";
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatPdaDate(value) {
    const parsed = parseBackendUtcDate(value);
    if (!parsed) return "Fecha no disponible";
    return parsed.toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "medium",
    });
  }

  function composeLabel(...parts) {
    return parts.map(readableValue).filter((part) => part && part !== "-").join(" / ");
  }

  function setConfigMessage(message) {
    if (els.configMsg) els.configMsg.textContent = message || "";
  }

  function storedTerminalCode() {
    return text(localStorage.getItem(STORAGE_TERMINAL), "");
  }

  function storedTerminalKey() {
    return String(localStorage.getItem(STORAGE_KEY) || "");
  }

  function activeTerminalCode() {
    return text(terminalConfig?.terminal_code || storedTerminalCode(), "");
  }

  function setConfigControlsDisabled(disabled) {
    isConfigSaving = !!disabled;
    [els.btnSaveConfig, els.btnCancelConfig, els.btnClearConfig].forEach((btn) => {
      if (btn) btn.disabled = !!disabled;
    });
  }

  function clearScanIdleTimer() {
    if (scanIdleTimer) {
      window.clearTimeout(scanIdleTimer);
      scanIdleTimer = null;
    }
  }

  function canScheduleScanIdleSubmit() {
    return !!(
      terminalValidationState === "valid" &&
      terminalConfig &&
      !isConfigPanelOpen &&
      !isPdaModalOpen() &&
      !isProcessing &&
      els.qrInput &&
      !els.qrInput.disabled
    );
  }

  function scheduleScanIdleSubmit() {
    clearScanIdleTimer();
    if (!canScheduleScanIdleSubmit()) return;
    const value = text(els.qrInput.value, "");
    if (!value) return;
    scanIdleTimer = window.setTimeout(submitCurrentQrFromIdle, SCAN_IDLE_SUBMIT_MS);
  }

  function submitCurrentQrFromIdle() {
    scanIdleTimer = null;
    if (!canScheduleScanIdleSubmit()) return;
    const value = text(els.qrInput.value, "");
    if (!value) return;
    if (value === lastSubmittedInputValue) return;
    lastSubmittedInputValue = value;
    submitQr("idle");
  }

  function showLocalDuplicateIgnored(qrValue) {
    renderScanResult({ ok: false, status: "duplicate", duplicate: true, qr_value: qrValue, message: "Lectura duplicada ignorada." }, { qrValue });
    setStatus("ready", "LECTURA DUPLICADA");
  }

  function resetLocalDuplicateGuard() {
    lastSubmittedQr = "";
    lastSubmittedAt = 0;
    lastSubmittedInputValue = "";
    lastSubmission = { value: "", timestamp: 0 };
  }

  function setScannerEnabled(enabled) {
    if (!enabled) {
      clearScanIdleTimer();
      lastSubmittedInputValue = "";
    }
    if (els.qrInput) {
      els.qrInput.disabled = !enabled;
      if (!enabled) els.qrInput.value = "";
    }
    if (enabled) focusScanner();
  }

  function setTerminalValidationState(state, message = "") {
    terminalValidationState = state;
    terminalValidationMessage = message || "";
  }

  function scannerLabelFromConfig(config) {
    if (!config?.scanner) return "-";
    const code = text(config.scanner.scanner_code, "");
    const name = text(config.scanner.scanner_name, "");
    return code && name ? `${code} - ${name}` : (code || name || "-");
  }

  function updatePdaModeSummary() {
    if (els.pdaModeTerminal) els.pdaModeTerminal.textContent = currentTerminalCode() || "-";
    if (els.pdaModeScanner) els.pdaModeScanner.textContent = scannerLabelFromConfig(terminalConfig);
  }

  function closePdaAdminModal() {
    els.pdaAdminModal?.classList.add("hidden");
    if (els.pdaAdminPassword) els.pdaAdminPassword.value = "";
    setPdaAdminError("");
    setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
    if (pdaInterfaceMode === "developer") {
      window.setTimeout(() => {
        try { els.btnPdaExitDeveloper?.focus(); } catch (_) {}
      }, 0);
    } else if (terminalValidationState === "valid") {
      focusScanner();
    }
  }

  function setPdaAdminError(message) {
    if (!els.pdaAdminError) return;
    els.pdaAdminError.textContent = message || "";
    els.pdaAdminError.classList.toggle("hidden", !message);
  }

  function openPdaAdminModal() {
    clearScanIdleTimer();
    setScannerEnabled(false);
    setPdaAdminError("");
    els.pdaAdminModal?.classList.remove("hidden");
    window.setTimeout(() => {
      try {
        els.pdaAdminPassword?.focus();
        els.pdaAdminPassword?.select();
      } catch (_) {}
    }, 0);
  }

  function expirePdaAdminSession(message = "La sesion administrativa expiro.") {
    pdaAdminToken = null;
    setPdaInterfaceMode("operator");
    setStatus("ready", "MODO OPERADOR");
    if (els.resultSummary) els.resultSummary.textContent = message;
  }

  async function verifyPdaAdminSession() {
    if (!pdaAdminToken) {
      expirePdaAdminSession("La sesion administrativa expiro.");
      return false;
    }
    try {
      await pdaFetchJson("/api/admin/session", {
        method: "GET",
        headers: { Accept: "application/json", "X-Admin-Token": pdaAdminToken },
        cache: "no-store",
      });
      return true;
    } catch (err) {
      if (err?.status === 401 || err?.status === 403) {
        expirePdaAdminSession("La sesion administrativa expiro.");
        return false;
      }
      throw err;
    }
  }

  async function loginPdaAdministrator() {
    const password = String(els.pdaAdminPassword?.value || "");
    if (!password) {
      setPdaAdminError("Escribe la contrasena administrativa.");
      return;
    }
    if (els.btnPdaAdminLogin) els.btnPdaAdminLogin.disabled = true;
    setPdaAdminError("");
    try {
      const data = await pdaFetchJson("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ password }),
      });
      pdaAdminToken = data?.token || null;
      if (!pdaAdminToken) throw new Error("Respuesta administrativa invalida.");
      setPdaInterfaceMode("developer");
      closePdaAdminModal();
      setStatus("ok", "MODO DESARROLLADOR");
      if (els.resultSummary) els.resultSummary.textContent = "Modo desarrollador activado.";
    } catch (err) {
      pdaAdminToken = null;
      const message = err?.status === 401 ? "Contrasena incorrecta." : `No fue posible activar modo desarrollador: ${err?.message || "error de red"}.`;
      setPdaAdminError(message);
    } finally {
      if (els.btnPdaAdminLogin) els.btnPdaAdminLogin.disabled = false;
      if (els.pdaAdminPassword) els.pdaAdminPassword.value = "";
    }
  }

  function hasPdaDeveloperAccess() {
    return pdaInterfaceMode === "developer" && !!pdaAdminToken;
  }

  function exitPdaDeveloperMode(message = "Modo operador activado.") {
    pdaAdminToken = null;
    if (els.resultDetails) {
      els.resultDetails.open = false;
      els.resultDetails.classList.add("hidden");
    }
    setPdaInterfaceMode("operator");
    setStatus("ready", "MODO OPERADOR");
    if (els.resultSummary) els.resultSummary.textContent = message;
    setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
    if (terminalValidationState === "valid") focusScanner();
  }

  function setPdaInterfaceMode(mode) {
    pdaInterfaceMode = mode === "developer" && pdaAdminToken ? "developer" : "operator";
    document.body.classList.toggle("pda-mode-developer", pdaInterfaceMode === "developer");
    document.body.classList.toggle("pda-mode-operator", pdaInterfaceMode === "operator");
    if (els.pdaModeBadge) els.pdaModeBadge.textContent = pdaInterfaceMode === "developer" ? "Modo desarrollador" : "Modo operador";
    if (pdaInterfaceMode === "operator") {
      if (els.scanMode) els.scanMode.value = "execute";
      if (terminalValidationState === "valid" && terminalConfig) {
        isConfigPanelOpen = false;
        els.configBody?.classList.remove("open");
      }
      closePdaAdminModal();
    } else {
      syncModeWithTerminalConfig();
    }
    updatePdaModeSummary();
    syncModeWarning();
    renderHistory();
    if (pdaInterfaceMode === "developer") {
      window.setTimeout(() => {
        try { els.btnPdaExitDeveloper?.focus(); } catch (_) {}
      }, 0);
    } else if (terminalValidationState === "valid" && !isPdaModalOpen()) {
      focusScanner();
    }
  }

  function updateEyebrow() {
    if (!els.eyebrow) return;
    if (pdaInterfaceMode === "operator") {
      els.eyebrow.textContent = "PDA - OPERADOR";
      return;
    }
    const execute = terminalValidationState === "valid" && terminalConfig && currentMode() === "execute";
    els.eyebrow.textContent = execute ? "PDA · EJECUCIÓN QR" : "PDA · PREVIEW QR";
  }

  function showTerminalConfigSummary(config) {
    const mode = text(config?.mode, "preview").toUpperCase();
    setResultClass("ok");
    els.resultSummary.textContent = `Terminal validada. Terminal: ${text(config?.terminal_code)} · Scanner: ${text(config?.scanner?.scanner_code)} · Modo: ${mode}`;
    setField(els.resTerminal, config?.terminal_code);
    setField(els.resScanner, scannerLabelFromConfig(config));
    setField(els.resAction, mode);
    setField(els.resError, "-");
  }

  function showTerminalError(message, statusText = "TERMINAL NO VÁLIDA") {
    setResultClass("error");
    els.resultSummary.textContent = message || statusText;
    setField(els.resTerminal, currentTerminalCode() || "-");
    setField(els.resScanner, "-");
    setField(els.resAction, "-");
    setField(els.resError, message || statusText);
  }

  function renderValidationState() {
    updatePdaModeSummary();
    if (terminalValidationState === "valid" && terminalConfig) {
      setScannerEnabled(true);
      syncModeWarning();
      showTerminalConfigSummary(terminalConfig);
      setStatus("ready", "LISTO PARA ESCANEAR");
      updateEyebrow();
      return;
    }
    if (terminalValidationState === "validating") {
      clearQrCatalog("Validando terminal antes de cargar catálogo...");
      setScannerEnabled(false);
      setResultClass("idle");
      setStatus("processing", "VALIDANDO TERMINAL");
      els.resultSummary.textContent = "Validando terminal guardada...";
      setField(els.resTerminal, currentTerminalCode() || "-");
      setField(els.resScanner, "-");
      setField(els.resAction, "-");
      setField(els.resError, "-");
      updateEyebrow();
      return;
    }
    if (terminalValidationState === "unconfigured") {
      terminalConfig = null;
      isConfigPanelOpen = true;
      clearQrCatalog("Configura y valida la terminal para cargar el catálogo.");
      syncModeWithTerminalConfig();
      setScannerEnabled(false);
      setResultClass("idle");
      setStatus("ready", "CONFIGURAR TERMINAL");
      els.resultSummary.textContent = "Configura y valida la terminal antes de escanear.";
      setField(els.resTerminal, "-");
      setField(els.resScanner, "-");
      setField(els.resAction, "-");
      setField(els.resError, "-");
      els.configBody.classList.add("open");
      setConfigMessage("Escribe el código de terminal.");
      updateEyebrow();
      return;
    }
    terminalConfig = null;
    isConfigPanelOpen = true;
    clearQrCatalog("Terminal no válida. Catálogo no disponible.");
    syncModeWithTerminalConfig();
    setScannerEnabled(false);
    setStatus("error", "TERMINAL NO VÁLIDA");
    showTerminalError(terminalValidationMessage || "Terminal no válida.");
    els.configBody.classList.add("open");
    setConfigMessage(terminalValidationMessage || "Terminal no válida.");
    updateEyebrow();
  }

  function migrateLegacyStorage() {
    const currentTerminal = text(localStorage.getItem(STORAGE_TERMINAL), "");
    const legacyTerminal = text(localStorage.getItem(LEGACY_STORAGE_TERMINAL), "");
    if (!currentTerminal && legacyTerminal) {
      localStorage.setItem(STORAGE_TERMINAL, legacyTerminal);
    }
    const currentKey = String(localStorage.getItem(STORAGE_KEY) || "");
    const legacyKey = String(localStorage.getItem(LEGACY_STORAGE_KEY) || "");
    if (!currentKey && legacyKey) {
      localStorage.setItem(STORAGE_KEY, legacyKey);
    }
    localStorage.removeItem(LEGACY_STORAGE_TERMINAL);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  }

  function saveValidatedConfig(config, apiKey) {
    if (config?.terminal_code) localStorage.setItem(STORAGE_TERMINAL, config.terminal_code);
    if (apiKey) localStorage.setItem(STORAGE_KEY, apiKey);
    else localStorage.removeItem(STORAGE_KEY);
  }

  function openConfigPanel(message = "Escribe el código de terminal.") {
    clearScanIdleTimer();
    const activeCode = activeTerminalCode() || routeTerminalCode();
    configDraft = { terminalCode: activeCode, terminalKey: "" };
    configSnapshot = {
      terminalCode: activeTerminalCode(),
      terminalKey: storedTerminalKey(),
      validationState: terminalValidationState,
      terminalConfig,
    };
    isConfigPanelOpen = true;
    els.terminalCode.value = configDraft.terminalCode;
    els.terminalKey.value = "";
    setConfigMessage(message);
    els.configBody.classList.add("open");
    setScannerEnabled(false);
    setStatus("processing", "CONFIGURANDO TERMINAL");
    focusConfig();
  }

  function closeConfigPanel() {
    isConfigPanelOpen = false;
    configDraft = { terminalCode: activeTerminalCode(), terminalKey: "" };
    els.terminalCode.value = configDraft.terminalCode;
    els.terminalKey.value = "";
    setConfigMessage("Escribe el código de terminal.");
    els.configBody.classList.remove("open");
  }

  async function fetchTerminalConfig(terminalCode, apiKey) {
    const code = text(terminalCode, "");
    if (!code) throw new Error("terminal_code requerido");
    const headers = { Accept: "application/json" };
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    const response = await fetch(`/api/scan/terminal-config/${encodeURIComponent(code)}`, {
      method: "GET",
      headers,
      cache: "no-store",
    });
    const textBody = await response.text();
    let data = {};
    try { data = textBody ? JSON.parse(textBody) : {}; } catch (_) { data = {}; }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "";
      const error = new Error(detail || `HTTP ${response.status}`);
      error.status = response.status;
      error.detail = detail;
      throw error;
    }
    return data;
  }

  function revokeQrCatalogImageUrl() {
    if (qrCatalogImageUrl && window.URL?.revokeObjectURL) {
      try { window.URL.revokeObjectURL(qrCatalogImageUrl); } catch (_) {}
    }
    qrCatalogImageUrl = null;
  }

  function setQrCatalogMessage(message) {
    if (els.qrCatalogMsg) els.qrCatalogMsg.textContent = message || "";
  }

  function resetQrCatalogSelection({ clearSelect = true, clearRules = false, message = "" } = {}) {
    qrCatalogRequestSequence += 1;
    selectedQrRule = null;
    revokeQrCatalogImageUrl();
    if (clearRules) {
      availableQrRules = [];
      qrCatalogLoadedForTerminal = "";
    }
    if (clearSelect && els.qrCatalogSelect) els.qrCatalogSelect.value = "";
    if (els.qrCatalogImage) {
      els.qrCatalogImage.removeAttribute("src");
      els.qrCatalogImage.classList.add("hidden");
    }
    if (els.qrCatalogDetails) els.qrCatalogDetails.classList.add("hidden");
    [
      els.qrCatalogAlias,
      els.qrCatalogValue,
      els.qrCatalogType,
      els.qrCatalogMatch,
      els.qrCatalogAction,
      els.qrCatalogMaterial,
      els.qrCatalogSource,
      els.qrCatalogDestination,
      els.qrCatalogRequiresScanner,
    ].forEach((field) => setQrCatalogField(field, ""));
    if (message) setQrCatalogMessage(message);
  }

  function setQrCatalogField(el, value) {
    if (!el) return;
    const display = readableValue(value);
    el.textContent = display;
    const row = el.closest?.(".qr-detail-row") || el.parentElement;
    if (row) row.classList.toggle("hidden", !display);
  }

  function clearQrCatalog(message = "Configura y valida la terminal para cargar el catálogo.") {
    resetQrCatalogSelection({ clearRules: true, message });
    if (els.qrCatalogSelect) {
      els.qrCatalogSelect.innerHTML = "";
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Selecciona un QR";
      els.qrCatalogSelect.appendChild(option);
      els.qrCatalogSelect.disabled = true;
    }
  }

  function ruleCatalogLabel(rule) {
    const alias = text(rule?.qr_alias, "");
    const value = text(rule?.qr_value, "");
    return alias && value ? `${alias} — ${value}` : (value || alias || `QR ${rule?.id || ""}`.trim());
  }

  function sortAvailableQrRules(rules) {
    return [...(Array.isArray(rules) ? rules : [])].sort((a, b) => {
      const aAlias = text(a?.qr_alias, "").toLocaleLowerCase();
      const bAlias = text(b?.qr_alias, "").toLocaleLowerCase();
      if (aAlias !== bAlias) return aAlias.localeCompare(bAlias);
      return text(a?.qr_value, "").toLocaleLowerCase().localeCompare(text(b?.qr_value, "").toLocaleLowerCase());
    });
  }

  function renderQrCatalogOptions() {
    if (!els.qrCatalogSelect) return;
    els.qrCatalogSelect.innerHTML = "";
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "Selecciona un QR";
    els.qrCatalogSelect.appendChild(emptyOption);

    availableQrRules.forEach((rule) => {
      const option = document.createElement("option");
      option.value = String(rule.id);
      option.textContent = ruleCatalogLabel(rule);
      els.qrCatalogSelect.appendChild(option);
    });
    els.qrCatalogSelect.disabled = availableQrRules.length === 0;
  }

  function showQrCatalogDetails(rule) {
    if (!rule) {
      if (els.qrCatalogDetails) els.qrCatalogDetails.classList.add("hidden");
      return;
    }
    setQrCatalogField(els.qrCatalogAlias, rule.qr_alias);
    setQrCatalogField(els.qrCatalogValue, rule.qr_value);
    setQrCatalogField(els.qrCatalogType, rule.qr_type);
    setQrCatalogField(els.qrCatalogMatch, rule.match_type);
    setQrCatalogField(els.qrCatalogAction, rule.action_type);
    setQrCatalogField(els.qrCatalogMaterial, rule.material_group_name);
    setQrCatalogField(els.qrCatalogSource, rule.source_area_name);
    setQrCatalogField(els.qrCatalogDestination, rule.destination_area_name);
    setQrCatalogField(els.qrCatalogRequiresScanner, rule.requires_scanner_station === true ? "Sí" : "No");
    if (els.qrCatalogDetails) els.qrCatalogDetails.classList.remove("hidden");
  }

  async function fetchAvailableQrRules({ force = false } = {}) {
    if (terminalValidationState !== "valid" || !terminalConfig) {
      clearQrCatalog("Configura y valida la terminal para cargar el catálogo.");
      return [];
    }
    const terminalCode = currentTerminalCode();
    if (!terminalCode) {
      clearQrCatalog("Terminal no disponible para cargar catálogo.");
      return [];
    }
    if (!force && qrCatalogLoadedForTerminal === terminalCode && availableQrRules.length) {
      return availableQrRules;
    }
    resetQrCatalogSelection({ clearRules: true, message: "Cargando QR configurados..." });
    if (els.qrCatalogSelect) els.qrCatalogSelect.disabled = true;
    const headers = { Accept: "application/json" };
    const apiKey = currentTerminalKey();
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    try {
      const response = await fetch(`/api/scan/available-qr-rules/${encodeURIComponent(terminalCode)}`, {
        method: "GET",
        headers,
        cache: "no-store",
      });
      const textBody = await response.text();
      let data = [];
      try { data = textBody ? JSON.parse(textBody) : []; } catch (_) { data = []; }
      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "";
        throw new Error(detail || `HTTP ${response.status}`);
      }
      availableQrRules = sortAvailableQrRules(data);
      qrCatalogLoadedForTerminal = terminalCode;
      renderQrCatalogOptions();
      if (availableQrRules.length) {
        setQrCatalogMessage(`${availableQrRules.length} QR activo${availableQrRules.length === 1 ? "" : "s"} disponible${availableQrRules.length === 1 ? "" : "s"}.`);
      } else {
        setQrCatalogMessage("No hay QR activos disponibles.");
      }
      return availableQrRules;
    } catch (err) {
      clearQrCatalog(`No fue posible cargar el catálogo: ${err?.message || "error"}.`);
      return [];
    }
  }

  async function loadSelectedQrImage(rule) {
    if (!rule || terminalValidationState !== "valid" || !terminalConfig) return;
    const terminalCode = currentTerminalCode();
    if (!terminalCode) return;
    const requestId = ++qrCatalogRequestSequence;
    revokeQrCatalogImageUrl();
    if (els.qrCatalogImage) {
      els.qrCatalogImage.removeAttribute("src");
      els.qrCatalogImage.classList.add("hidden");
    }
    setQrCatalogMessage("Cargando QR...");
    const headers = {};
    const apiKey = currentTerminalKey();
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    try {
      const response = await fetch(`/api/scan/available-qr-rules/${encodeURIComponent(terminalCode)}/${encodeURIComponent(rule.id)}/image?size=360`, {
        method: "GET",
        headers,
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const contentType = response.headers?.get?.("Content-Type") || "";
      if (!contentType.toLowerCase().includes("image/png")) throw new Error("La imagen recibida no es PNG.");
      const blob = await response.blob();
      if (!blob || blob.size <= 0) throw new Error("La imagen QR está vacía.");
      if (requestId !== qrCatalogRequestSequence) return;
      const objectUrl = window.URL.createObjectURL(blob);
      revokeQrCatalogImageUrl();
      qrCatalogImageUrl = objectUrl;
      if (els.qrCatalogImage) {
        els.qrCatalogImage.src = objectUrl;
        els.qrCatalogImage.classList.remove("hidden");
      }
      setQrCatalogMessage("QR listo para visualizar.");
    } catch (err) {
      if (requestId !== qrCatalogRequestSequence) return;
      revokeQrCatalogImageUrl();
      if (els.qrCatalogImage) {
        els.qrCatalogImage.removeAttribute("src");
        els.qrCatalogImage.classList.add("hidden");
      }
      setQrCatalogMessage(`No fue posible cargar la imagen QR: ${err?.message || "error"}.`);
    }
  }

  function hideSelectedQrCatalog({ refocus = false } = {}) {
    resetQrCatalogSelection({ clearSelect: true });
    setQrCatalogMessage(availableQrRules.length ? `${availableQrRules.length} QR activo${availableQrRules.length === 1 ? "" : "s"} disponible${availableQrRules.length === 1 ? "" : "s"}.` : "No hay QR activos disponibles.");
    if (refocus && terminalValidationState === "valid") focusScanner();
  }

  function handleQrCatalogSelectionChange() {
    const id = Number(els.qrCatalogSelect?.value || 0);
    const rule = availableQrRules.find((item) => Number(item.id) === id) || null;
    if (!rule) {
      hideSelectedQrCatalog();
      return;
    }
    selectedQrRule = rule;
    showQrCatalogDetails(rule);
    loadSelectedQrImage(rule);
  }

  function terminalConfigErrorMessage(err) {
    const status = Number(err?.status || 0);
    if (status === 401) return "Ingresa la clave para validar esta terminal.";
    if (status === 403) return "Terminal inactiva.";
    if (status === 404) return "Terminal no encontrada.";
    if (status === 409) return "Scanner asociado no disponible.";
    return err?.message || String(err || "Terminal no válida.");
  }

  function executeAllowedByTerminalConfig() {
    return !!(
      terminalConfig &&
      terminalConfig.mode === "execute" &&
      terminalConfig.allow_execute === true &&
      terminalConfig.scanner?.allow_execute === true
    );
  }

  function syncModeWithTerminalConfig() {
    const previewOption = els.scanMode?.querySelector('option[value="preview"]');
    const executeOption = els.scanMode?.querySelector('option[value="execute"]');
    const executeAllowed = executeAllowedByTerminalConfig();
    if (previewOption) previewOption.disabled = false;
    if (executeOption) executeOption.disabled = !executeAllowed;
    if (!els.scanMode) return;
    if (terminalValidationState !== "valid" || !terminalConfig || terminalConfig.mode !== "execute" || !executeAllowed) {
      els.scanMode.value = "preview";
      return;
    }
    if (terminalConfig.require_preview === true) {
      els.scanMode.value = "preview";
      return;
    }
    if (scanModeSelectedByUser && els.scanMode.value === "preview") {
      return;
    }
    if (scanModeSelectedByUser && els.scanMode.value === "execute" && executeAllowed) {
      return;
    }
    if (!scanModeSelectedByUser && terminalConfig.mode === "execute" && executeAllowed) {
      els.scanMode.value = "execute";
      return;
    }
    if (els.scanMode.value === "execute" && !executeAllowed) {
      els.scanMode.value = "preview";
    }
  }

  async function loadPreviousStoredConfig(savedTerminal, savedKey) {
    const code = text(savedTerminal, "");
    if (!code) return { terminalCode: "", terminalKey: "", validationState: "unconfigured", terminalConfig: null };
    try {
      const config = await fetchTerminalConfig(code, savedKey);
      return { terminalCode: config.terminal_code || code, terminalKey: savedKey || "", validationState: "valid", terminalConfig: config };
    } catch (_) {
      return { terminalCode: code, terminalKey: savedKey || "", validationState: "invalid", terminalConfig: null };
    }
  }

  function openConfigPanelForPathCandidate(candidateCode, message, previousSnapshot) {
    clearScanIdleTimer();
    const previousConfig = previousSnapshot?.terminalConfig || null;
    terminalConfig = previousConfig;
    setTerminalValidationState(previousConfig ? "valid" : "invalid", previousConfig ? "" : (message || "Terminal no válida."));
    configSnapshot = previousSnapshot || { terminalCode: "", terminalKey: "", validationState: "unconfigured", terminalConfig: null };
    configDraft = { terminalCode: candidateCode, terminalKey: "" };
    isConfigPanelOpen = true;
    els.terminalCode.value = candidateCode;
    els.terminalKey.value = "";
    els.configBody.classList.add("open");
    setConfigMessage(message || "Ingresa la clave para validar esta terminal.");
    setScannerEnabled(false);
    setResultClass("error");
    els.resultSummary.textContent = message || "Terminal no válida.";
    setField(els.resTerminal, candidateCode || "-");
    setField(els.resScanner, "-");
    setField(els.resAction, "-");
    setField(els.resError, message || "Terminal no válida.");
    setStatus(previousConfig ? "error" : "ready", previousConfig ? "TERMINAL NO VÁLIDA" : "CONFIGURAR TERMINAL");
    syncModeWithTerminalConfig();
    updateEyebrow();
    focusConfig();
  }

  async function handlePathTerminalCandidate(candidateCode, savedTerminal, savedKey) {
    normalizePdaUrl();
    clearRouteTerminalCode();
    const code = text(candidateCode, "");
    const previousSnapshot = code !== savedTerminal
      ? await loadPreviousStoredConfig(savedTerminal, savedKey)
      : { terminalCode: savedTerminal, terminalKey: savedKey || "", validationState: "unconfigured", terminalConfig: null };

    if (!isBasicTerminalCodeCandidate(code)) {
      openConfigPanelForPathCandidate(code, "Terminal no válida.", previousSnapshot);
      return;
    }

    const sameAsStored = !!savedTerminal && code === savedTerminal;
    const keyToValidate = sameAsStored ? savedKey : "";
    setTerminalValidationState("validating");
    terminalConfig = previousSnapshot.terminalConfig || null;
    els.terminalCode.value = code;
    els.terminalKey.value = "";
    setScannerEnabled(false);
    setResultClass("idle");
    setStatus("processing", "VALIDANDO TERMINAL");
    els.resultSummary.textContent = "Validando terminal del enlace...";
    setField(els.resTerminal, code);
    setField(els.resScanner, "-");
    setField(els.resAction, "-");
    setField(els.resError, "-");

    try {
      const config = await fetchTerminalConfig(code, keyToValidate);
      terminalConfig = config;
      setTerminalValidationState("valid");
      saveValidatedConfig(config, keyToValidate);
      if (els.terminalCode) els.terminalCode.value = config.terminal_code || code;
      if (els.terminalKey) els.terminalKey.value = "";
      scanModeSelectedByUser = false;
      syncModeWithTerminalConfig();
      closeConfigPanel();
      renderValidationState();
      fetchAvailableQrRules({ force: true });
      historyAuthPaused = false;
      refreshPdaHistoryNow();
      focusScanner();
    } catch (err) {
      const message = terminalConfigErrorMessage(err);
      openConfigPanelForPathCandidate(code, message, previousSnapshot);
    }
  }

  async function validateConfiguredTerminal(terminalCode, apiKey, options = {}) {
    const code = text(terminalCode, "");
    if (!code) {
      setTerminalValidationState("unconfigured");
      renderValidationState();
      focusConfig();
      return null;
    }
    terminalConfig = null;
    setTerminalValidationState("validating");
    renderValidationState();
    try {
      const config = await fetchTerminalConfig(code, apiKey);
      terminalConfig = config;
      setTerminalValidationState("valid");
      saveValidatedConfig(config, apiKey);
      if (els.terminalCode) els.terminalCode.value = config.terminal_code || code;
      if (els.terminalKey) els.terminalKey.value = "";
      scanModeSelectedByUser = false;
      syncModeWithTerminalConfig();
      if (options.closeConfig !== false) els.configBody.classList.remove("open");
      renderValidationState();
      fetchAvailableQrRules({ force: true });
      historyAuthPaused = false;
      refreshPdaHistoryNow();
      return config;
    } catch (err) {
      terminalConfig = null;
      setTerminalValidationState("invalid", err?.message || String(err));
      renderValidationState();
      focusConfig();
      return null;
    }
  }

  async function saveConfig() {
    if (isConfigSaving) return;
    const terminalCode = text(els.terminalCode.value, "");
    const typedKey = String(els.terminalKey.value || "").trim();
    if (!terminalCode) {
      setConfigMessage("Escribe el código de terminal.");
      focusConfig();
      return;
    }
    const activeCode = activeTerminalCode();
    const sameTerminal = !!activeCode && terminalCode === activeCode;
    const previousKey = storedTerminalKey();
    const keyToValidate = typedKey || (sameTerminal ? previousKey : "");
    configDraft = { terminalCode, terminalKey: typedKey };
    setConfigControlsDisabled(true);
    setConfigMessage("Validando terminal...");
    setStatus("processing", "VALIDANDO TERMINAL");
    setScannerEnabled(false);
    try {
      const config = await fetchTerminalConfig(terminalCode, keyToValidate);
      localStorage.setItem(STORAGE_TERMINAL, config.terminal_code);
      if (typedKey) {
        localStorage.setItem(STORAGE_KEY, typedKey);
      } else if (sameTerminal && previousKey) {
        localStorage.setItem(STORAGE_KEY, previousKey);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
      terminalConfig = config;
      setTerminalValidationState("valid");
      scanModeSelectedByUser = false;
      syncModeWithTerminalConfig();
      closeConfigPanel();
      renderValidationState();
      fetchAvailableQrRules({ force: true });
      historyAuthPaused = false;
      refreshPdaHistoryNow();
      setStatus("ok", "TERMINAL CONFIGURADA");
      setConfigMessage("Terminal validada correctamente.");
      window.setTimeout(() => {
        if (!isConfigPanelOpen && terminalValidationState === "valid") setStatus("ready", "LISTO PARA ESCANEAR");
      }, 1200);
      focusScanner();
    } catch (err) {
      const message = err?.message || String(err);
      setConfigMessage(`${message} La configuración anterior se conservará.`);
      showTerminalError(message);
      setStatus("error", "TERMINAL NO VÁLIDA");
      setScannerEnabled(false);
      focusConfig();
    } finally {
      setConfigControlsDisabled(false);
    }
  }

  function clearConfig() {
    const confirmed = window.confirm ? window.confirm("¿Quitar la configuración de este PDA? El lector quedará bloqueado hasta configurar una terminal válida.") : true;
    if (!confirmed) return;
    clearScanIdleTimer();
    lastSubmittedInputValue = "";
    localStorage.removeItem(STORAGE_TERMINAL);
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(LEGACY_STORAGE_TERMINAL);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    clearQrCatalog("Configura y valida la terminal para cargar el catálogo.");
    terminalConfig = null;
    configDraft = { terminalCode: "", terminalKey: "" };
    configSnapshot = { terminalCode: "", terminalKey: "", validationState: "unconfigured", terminalConfig: null };
    isConfigPanelOpen = true;
    els.terminalCode.value = "";
    els.terminalKey.value = "";
    setTerminalValidationState("unconfigured");
    renderValidationState();
    setConfigMessage("Configuración eliminada.");
    focusConfig();
  }

  function cancelConfig() {
    configDraft = { terminalCode: "", terminalKey: "" };
    els.terminalKey.value = "";
    if (terminalConfig && terminalValidationState === "valid") {
      els.terminalCode.value = terminalConfig.terminal_code || storedTerminalCode();
      closeConfigPanel();
      renderValidationState();
      setStatus("ready", "LISTO PARA ESCANEAR");
      focusScanner();
      return;
    }
    terminalConfig = configSnapshot.terminalConfig || null;
    setTerminalValidationState(configSnapshot.validationState || "unconfigured");
    els.terminalCode.value = "";
    setConfigMessage("Escribe el código de terminal.");
    if (terminalValidationState === "valid" && terminalConfig) {
      closeConfigPanel();
      renderValidationState();
      focusScanner();
    } else {
      isConfigPanelOpen = true;
      setTerminalValidationState("unconfigured");
      renderValidationState();
      focusConfig();
    }
  }

  function currentTerminalCode() {
    return text(terminalConfig?.terminal_code || localStorage.getItem(STORAGE_TERMINAL) || els.terminalCode.value, "");
  }

  function currentTerminalKey() {
    return storedTerminalKey();
  }

  async function initConfig() {
    migrateLegacyStorage();
    const fromPath = routeTerminalCode();
    const savedTerminal = text(localStorage.getItem(STORAGE_TERMINAL), "");
    const savedKey = storedTerminalKey();
    if (fromPath && !pathTerminalHandled) {
      await handlePathTerminalCandidate(fromPath, savedTerminal, savedKey);
      return;
    }
    clearRouteTerminalCode();
    normalizePdaUrl();
    const initialTerminal = savedTerminal;
    els.terminalCode.value = initialTerminal;
    els.terminalKey.value = "";
    if (!initialTerminal) {
      setTerminalValidationState("unconfigured");
      renderValidationState();
      focusConfig();
      return;
    }
    await validateConfiguredTerminal(initialTerminal, savedKey, { closeConfig: true });
  }

  function classifyScanResult(data) {
    const status = readableValue(data?.status).toLowerCase();
    const dispatchInfo = resolveDispatchInfo(data || {});
    const dispatchStatus = readableValue(dispatchInfo.dispatchStatus).toLowerCase();
    const rcsStatus = readableValue(dispatchInfo.rcsStatus).toLowerCase();
    const mode = readableValue(data?.mode).toLowerCase();
    const message = readableValue(data?.message || data?.error_message || data?.rcs_message).toLowerCase();
    if (status === "duplicate" || data?.duplicate === true || message.includes("duplicad")) return "duplicate";
    if (mode === "preview" || status === "preview" || (data?.ok === true && !firstValue(data?.movement_order_id, data?.existing_movement_order_id))) return "preview";
    if (data?.ok === false || status === "error" || status === "dispatch_error" || status === "communication_error" || dispatchStatus === "error" || rcsStatus === "error" || (data?.error_message && data?.ok !== true)) return "error";
    if (isUnknownRcsStatus(rcsStatus) || dispatchStatus === "unknown") return "warning";
    if (data?.ok === true && ["success", "dispatched", "sent", "accepted", "in_progress", "completed"].includes(dispatchStatus || rcsStatus || status)) return "success";
    if (mode === "preview" || status === "preview" || (data?.ok === true && !firstValue(data?.movement_order_id, data?.existing_movement_order_id))) return "preview";
    if (data?.ok === true && !firstValue(dispatchInfo.dispatchStatus, dispatchInfo.rcsStatus) && !firstValue(data?.movement_order_id, data?.existing_movement_order_id)) return "preview";
    return "warning";
  }

  function resultTitleForCategory(category) {
    if (category === "communication_error") return "ERROR DE COMUNICACIÓN";
    if (category === "success") return "OPERACIÓN PROCESADA";
    if (category === "preview") return "PREVIEW CORRECTO";
    if (category === "duplicate") return "LECTURA DUPLICADA";
    if (category === "error") return "ERROR DE ESCANEO";
    return "REVISAR RESULTADO";
  }

  function operationalHeadline(data, view) {
    const dispatch = view.rcs || {};
    const rollback = view.rollback || {};
    const message = readableValue(firstValue(data?.message, data?.rcs_message, data?.error_message));
    const statusText = `${readableValue(data?.status)} ${readableValue(dispatch.dispatchStatus)} ${readableValue(dispatch.rcsStatus)} ${message}`.toLowerCase();
    if (view.category === "success" && firstValue(dispatch.dispatchStatus, dispatch.rcsStatus)) {
      return {
        title: "ORDEN ENVIADA AL RCS",
        message: message || "La operación fue aceptada correctamente.",
      };
    }
    if (statusText.includes("timeout") && readableValue(data?.status).toLowerCase() !== "dispatch_error") {
      return {
        title: "TIEMPO DE ESPERA AGOTADO",
        message: message || "No se recibió confirmación del RCS. Revisar estado de la orden antes de repetir el escaneo.",
      };
    }
    if (view.category === "error" && rollback.exists && rollbackSuccessfulConfirmed(rollback)) {
      return {
        title: "ERROR DE DESPACHO",
        message: message || "El RCS no aceptó la tarea. La recuperación automática se ejecutó correctamente.",
      };
    }
    if (view.category === "error" && rollback.exists) {
      return {
        title: "ERROR DE DESPACHO",
        message: message || "La recuperación automática no pudo confirmarse completamente. Revisar rack, origen y destino.",
      };
    }
    if (view.category === "error" && firstValue(data?.movement_order_id, data?.existing_movement_order_id, dispatch.dispatchStatus, dispatch.rcsStatus)) {
      return {
        title: "ERROR DE DESPACHO",
        message: message || "El RCS no aceptó la tarea.",
      };
    }
    return null;
  }

  function resultMessage(data, category) {
    return firstValue(
      data?.message,
      data?.rcs_message,
      data?.error_message,
      category === "success" ? "Operación procesada." : "",
      category === "preview" ? "Preview correcto." : "",
      category === "duplicate" ? "Lectura duplicada ignorada." : "",
      category === "error" ? "No fue posible procesar la lectura." : "",
      "Revisa el resultado del escaneo."
    );
  }

  function areaCellLabel(group) {
    if (!group || typeof group !== "object") return "";
    return composeLabel(entityLabel(group.area), cellLabel(group.cell));
  }

  function appendConfirmRow(container, label, value) {
    if (!container) return false;
    const readable = readableValue(value);
    if (!readable) return false;
    const row = document.createElement("div");
    row.className = "history-field pda-confirm-field";
    const span = document.createElement("span");
    span.textContent = label;
    const bold = document.createElement("b");
    bold.textContent = readable;
    row.append(span, bold);
    container.appendChild(row);
    return true;
  }

  function setExecuteConfirmError(message) {
    if (!els.pdaExecuteConfirmError) return;
    els.pdaExecuteConfirmError.textContent = message || "";
    els.pdaExecuteConfirmError.classList.toggle("hidden", !message);
  }

  function setExecuteConfirmButtonsDisabled(disabled) {
    [els.btnPdaExecuteConfirm, els.btnPdaExecuteCancel].forEach((btn) => {
      if (btn) btn.disabled = !!disabled;
    });
  }

  function fillExecuteConfirmSummary(preview, qrValue) {
    if (!els.pdaExecuteConfirmSummary) return;
    els.pdaExecuteConfirmSummary.textContent = "";
    const qr = preview?.qr || {};
    const material = preview?.material || {};
    const rack = preview?.rack_selected || preview?.selected_rack || preview?.rack || {};
    const source = preview?.source || {};
    const destination = preview?.destination || {};
    const essentialValue = (value, fallback) => readableValue(value) || fallback;
    [
      ["QR leído", essentialValue(firstValue(preview?.qr_value, qr.qr_value, qrValue), "No disponible")],
      ["Material", essentialValue(firstValue(entityLabel(material), preview?.material_name, preview?.material_code), "No disponible")],
      ["Rack seleccionado", essentialValue(entityLabel(rack), "No disponible")],
      ["Origen", essentialValue(areaCellLabel(source), "No disponible")],
      ["Destino", essentialValue(areaCellLabel(destination), "No disponible")],
      ["Mensaje preview", essentialValue(preview?.message, "Sin mensaje")],
    ].forEach(([label, value]) => appendConfirmRow(els.pdaExecuteConfirmSummary, label, value));
  }

  function formatOperationalStatus(value) {
    const raw = readableValue(value);
    const key = raw.toLowerCase();
    const labels = {
      pending: "Pendiente",
      created: "Creada",
      dispatched: "Enviada",
      success: "Correcto",
      accepted: "Aceptada",
      sent: "Enviada",
      in_progress: "En progreso",
      completed: "Completada",
      error: "Error",
      dispatch_error: "Error de despacho",
      timeout: "Tiempo de espera agotado",
      cancelled: "Cancelada",
      canceled: "Cancelada",
      duplicate: "Duplicada",
      unknown: "Desconocido",
      unknown_or_not_found: "Desconocido / no encontrado",
      not_found: "No encontrado",
      not_sent: "No enviada",
      forced_closed: "Cierre forzado",
    };
    return labels[key] || raw;
  }

  function resolveDispatchInfo(data) {
    return {
      dispatchStatus: firstValue(data?.dispatch_status, data?.dispatch?.status, data?.order?.dispatch_status),
      rcsStatus: firstValue(data?.rcs_status, data?.rcs?.status, data?.order?.rcs_status),
      rcsMessage: firstValue(data?.rcs_message, data?.rcs?.message, data?.dispatch?.message, data?.movement_order?.rcs_message, data?.order?.rcs_message),
      errorMessage: firstValue(data?.error_message, data?.error?.message),
    };
  }

  function resolveRollbackInfo(data) {
    const raw = firstValue(data?.rollback, data?.result?.rollback, data?.order?.rollback);
    if (!raw || typeof raw !== "object") return { exists: false };
    const rawResult = firstValue(raw.result, raw.raw_result, raw.status);
    const mainRackId = firstValue(raw.rack_id, data?.rack_id, data?.movement_order?.rack_id, data?.order?.rack_id);
    const rackStatusAfter = readableValue(raw.rack_status_after).toLowerCase();
    const releasedRacks = Array.isArray(raw.released_racks) ? raw.released_racks : [];
    const rackReleasedFromStatus = rackStatusAfter === "available" || rackStatusAfter === "disponible";
    const rackReleasedFromList = releasedRacks.some((item) => {
      const sameRack = !mainRackId || String(item?.rack_id ?? "") === String(mainRackId);
      const newStatus = readableValue(item?.new_status).toLowerCase();
      return sameRack && (newStatus === "available" || newStatus === "disponible");
    });
    const inferredRackReleased = rackReleasedFromStatus || rackReleasedFromList;
    const inferredSourceRestored = !!mainRackId && String(raw.source_cell_rack_id ?? "") === String(mainRackId);
    const destinationRackId = raw.destination_cell_rack_id;
    const inferredDestinationReleased = raw.destination_cell_id != null && (destinationRackId == null || destinationRackId === "");
    const explicitSuccessful = typeof raw.success === "boolean" ? raw.success : (typeof raw.successful === "boolean" ? raw.successful : undefined);
    const rackReleased = firstValue(raw.rack_released, raw.rackReleased, inferredRackReleased ? true : "");
    const sourceRestored = firstValue(raw.source_restored, raw.sourceRestored, inferredSourceRestored ? true : "");
    const destinationReleased = firstValue(raw.destination_released, raw.destinationReleased, inferredDestinationReleased ? true : "");
    const confirmedRecovery = raw.executed === true && rackReleased === true && sourceRestored === true && destinationReleased === true && explicitSuccessful !== false;
    const successful = typeof explicitSuccessful === "boolean" ? explicitSuccessful : (confirmedRecovery ? true : undefined);
    return {
      exists: true,
      executed: raw.executed,
      successful,
      confirmedRecovery,
      message: firstValue(raw.message, raw.error_message),
      rackReleased,
      sourceRestored,
      destinationReleased,
      reservationReleased: firstValue(raw.reservation_released, raw.reservationReleased),
      rawResult,
    };
  }

  function isDispatchErrorStatus(value) {
    const status = readableValue(value).toLowerCase();
    return ["error", "dispatch_error", "timeout", "failed", "cancel_error"].includes(status);
  }

  function isUnknownRcsStatus(value) {
    const status = readableValue(value).toLowerCase();
    return ["unknown", "unknown_or_not_found", "not_found"].includes(status);
  }

  function rollbackSuccessfulConfirmed(rollback) {
    if (!rollback?.exists) return false;
    if (rollback.executed !== true) return false;
    if (rollback.confirmedRecovery === true) return true;
    if (rollback.successful === true) return true;
    return false;
  }

  function requiresManualReviewFor(data, dispatchInfo, rollbackInfo) {
    const hasDispatchError = isDispatchErrorStatus(firstValue(data?.status, dispatchInfo.dispatchStatus, dispatchInfo.rcsStatus)) || !!dispatchInfo.errorMessage;
    if (!hasDispatchError) return false;
    if (!rollbackInfo.exists) return true;
    if (rollbackInfo.executed === false) return true;
    if (rollbackInfo.successful === false) return true;
    if (rollbackInfo.confirmedRecovery === true) return false;
    if (rollbackInfo.exists && rollbackInfo.executed === true && rollbackInfo.successful !== true) return true;
    if (isUnknownRcsStatus(dispatchInfo.rcsStatus)) return true;
    return false;
  }

  function buildScanResultView(data, context = {}) {
    const qr = data?.qr || data?.scan?.qr || {};
    const rule = data?.rule || data?.action || {};
    const material = data?.material || {};
    const rack = data?.rack_selected || data?.selected_rack || data?.rack || {};
    const scanner = data?.scanner || {};
    const terminal = data?.terminal || {};
    const source = data?.source || {};
    const destination = data?.destination || {};
    const category = classifyScanResult(data || {});
    const rcs = resolveDispatchInfo(data || {});
    const rollback = resolveRollbackInfo(data || {});
    const requiresManualReview = requiresManualReviewFor(data || {}, rcs, rollback);
    const title = data?.status === "communication_error" ? "ERROR DE COMUNICACIÓN" : resultTitleForCategory(category);
    const view = {
      category,
      title,
      message: resultMessage(data || {}, category),
      rcs,
      rollback,
      requiresManualReview,
      notices: [],
      sections: [
        {
          title: "Lectura",
          rows: [
            ["QR", firstValue(data?.qr_value, data?.scan?.qr_value, qr.qr_value, context.qrValue)],
            ["Alias", firstValue(data?.qr_alias, qr.qr_alias, rule.qr_alias)],
            ["Tipo QR", firstValue(data?.qr_type, qr.qr_type, data?.parsed?.parsed_type)],
            ["Acción", firstValue(data?.action_type, data?.action, data?.resolved_action?.action_type)],
            ["Terminal", firstValue(data?.terminal_code, terminal.terminal_code, context.terminalCode)],
            ["Scanner", firstValue(data?.scanner_code, scanner.scanner_code, scanner.code)],
            ["Modo", firstValue(data?.mode, context.mode)],
          ],
        },
        {
          title: "Operación",
          rows: [
            ["Material", firstValue(data?.material_name, data?.material_code, material.name, material.code)],
            ["Grupo de material", firstValue(data?.material_group_name, data?.material_group_code, data?.material_group?.name, data?.material_group?.code)],
            ["Rack", firstValue(data?.rack_code, data?.rack_id, rack.rack_code, rack.code, rack.id)],
            ["Origen", firstValue(data?.source_name, data?.source_location, data?.source_area_name, areaCellLabel(source), data?.origin)],
            ["Destino", firstValue(data?.destination_name, data?.destination_location, data?.destination_area_name, areaCellLabel(destination), data?.destination)],
            ["Prioridad", data?.priority],
          ],
        },
        {
          title: "Orden",
          rows: [
            ["ID", firstValue(data?.movement_order_id, data?.existing_movement_order_id)],
            ["Estado", formatOperationalStatus(data?.status)],
          ],
        },
        {
          title: "Estado de despacho",
          tone: category === "success" ? "rcs-success" : (category === "error" ? "rcs-error" : "rcs-warning"),
          rows: [
            ["Orden", firstValue(data?.movement_order_id, data?.existing_movement_order_id)],
            ["Estado de orden", formatOperationalStatus(data?.status)],
            ["Dispatch", formatOperationalStatus(rcs.dispatchStatus)],
            ["Estado RCS", formatOperationalStatus(rcs.rcsStatus)],
            ["Mensaje RCS", rcs.rcsMessage],
            ["Error", firstValue(rcs.errorMessage, category === "error" ? data?.message : "")],
          ],
        },
      ],
    };
    const headline = operationalHeadline(data || {}, view);
    if (headline) {
      if (headline.title === "ORDEN ENVIADA AL RCS") {
        view.notices.push({ tone: "rcs-success", title: headline.title, message: "La operación fue aceptada correctamente." });
      } else {
        view.title = headline.title;
        view.message = headline.message;
      }
    }
    if (rollback.exists) {
      const rollbackTone = rollbackSuccessfulConfirmed(rollback) ? "rollback-success" : "rollback-warning";
      view.sections.push({
        title: rollback.confirmedRecovery ? "Recuperación automática completada" : "Recuperación automática",
        tone: rollbackTone,
        rows: [
          ["Rollback ejecutado", rollback.executed],
          ["Resultado confirmado", rollback.successful],
          ["Rack liberado", rollback.rackReleased],
          ["Origen restaurado", rollback.sourceRestored],
          ["Destino liberado", rollback.destinationReleased],
          ["Reserva liberada", rollback.reservationReleased],
          ["Mensaje", rollback.message],
          ["Resultado", rollback.rawResult],
        ],
      });
      if (rollback.confirmedRecovery) {
        view.notices.push({
          tone: "rollback-success",
          title: "RECUPERACIÓN AUTOMÁTICA COMPLETADA",
          message: "El rack y las ubicaciones fueron restaurados correctamente.",
        });
      } else if (rollback.executed === false || rollback.successful === false || rollback.successful == null) {
        view.notices.push({
          tone: "rollback-warning",
          title: "Revisar recuperación",
          message: "Revisar manualmente el estado del rack y las ubicaciones.",
        });
      }
    }
    if (requiresManualReview) {
      view.notices.push({
        tone: "manual-review",
        title: "REVISIÓN MANUAL REQUERIDA",
        message: "Verifique rack, celda origen, celda destino, estado de la orden y estado en RCS.",
      });
    }
    return view;
  }

  function appendResultRow(container, label, value) {
    const rendered = readableValue(value);
    if (!rendered) return false;
    const row = document.createElement("div");
    row.className = "result-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("b");
    valueEl.textContent = rendered;
    row.append(labelEl, valueEl);
    container.appendChild(row);
    return true;
  }

  function sanitizeTechnicalDetails(value, seen = new WeakSet()) {
    if (value == null || typeof value !== "object") return value;
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    if (Array.isArray(value)) return value.map((item) => sanitizeTechnicalDetails(item, seen));
    const blocked = new Set(["api_key", "terminal_key", "authorization", "admin_token", "headers", "x-terminal-key"]);
    const output = {};
    Object.entries(value).forEach(([key, item]) => {
      if (blocked.has(String(key).toLowerCase())) {
        output[key] = "[REDACTED]";
        return;
      }
      output[key] = sanitizeTechnicalDetails(item, seen);
    });
    return output;
  }

  function renderScanResult(data, context = {}) {
    const safeData = data || {};
    const view = buildScanResultView(safeData, context);
    setResultClass(view.category);
    if (els.resultTitle) els.resultTitle.textContent = view.title;
    els.resultSummary.textContent = readableValue(view.message) || view.title;
    if (els.resultSections) {
      els.resultSections.textContent = "";
      view.sections.forEach((section) => {
        const sectionEl = document.createElement("div");
        sectionEl.className = `result-section ${section.tone || ""}`.trim();
        const titleEl = document.createElement("h3");
        titleEl.textContent = section.title;
        const rowsEl = document.createElement("div");
        rowsEl.className = "result-rows";
        let hasRows = false;
        section.rows.forEach(([label, value]) => {
          hasRows = appendResultRow(rowsEl, label, value) || hasRows;
        });
        if (!hasRows) return;
        sectionEl.append(titleEl, rowsEl);
        els.resultSections.appendChild(sectionEl);
      });
      view.notices.forEach((notice) => {
        const noticeEl = document.createElement("div");
        noticeEl.className = `result-notice ${notice.tone || "result-warning"}`.trim();
        const titleEl = document.createElement("h3");
        titleEl.textContent = notice.title;
        const messageEl = document.createElement("p");
        messageEl.textContent = notice.message;
        noticeEl.append(titleEl, messageEl);
        els.resultSections.appendChild(noticeEl);
      });
    }
    if (els.resultDetails && els.resultJson) {
      els.resultDetails.classList.remove("hidden");
      els.resultDetails.open = false;
      els.resultJson.textContent = JSON.stringify(sanitizeTechnicalDetails(safeData), null, 2);
    }

    const scanner = safeData?.scanner || {};
    const qr = safeData?.qr || {};
    const source = safeData?.source || {};
    const destination = safeData?.destination || {};
    setField(els.resQr, firstValue(qr.qr_value, safeData?.qr_value, context.qrValue));
    setField(els.resTerminal, firstValue(safeData?.terminal_code, safeData?.terminal?.terminal_code, context.terminalCode));
    setField(els.resScanner, entityLabel(scanner) !== "-" ? entityLabel(scanner) : safeData?.scanner_code);
    setField(els.resAction, firstValue(safeData?.action_type, safeData?.action));
    setField(els.resAlias, firstValue(qr.qr_alias, safeData?.qr_alias));
    setField(els.resType, firstValue(qr.qr_type, safeData?.qr_type, safeData?.parsed?.parsed_type));
    setField(els.resMaterial, firstValue(entityLabel(safeData?.material), safeData?.material_name, safeData?.material_code));
    setField(els.resRack, entityLabel(safeData?.rack_selected || safeData?.selected_rack || safeData?.rack));
    setField(els.resSource, areaCellLabel(source));
    setField(els.resDestination, areaCellLabel(destination));
    setField(els.resOrder, firstValue(safeData?.movement_order_id, safeData?.existing_movement_order_id));
    setField(els.resEvent, safeData?.scan_event_id);
    setField(els.resError, view.category === "error" ? view.message : "");
  }

  function addHistory(item) {
    localHistory.unshift(item);
    localHistory = localHistory.slice(0, HISTORY_LIMIT);
    renderHistory();
  }

  function pdaHistoryContentElement() {
    return els.pdaHistoryContent || els.localHistory;
  }

  function updatePdaHistoryCount(count = pdaHistory.length) {
    if (!els.pdaHistoryCount) return;
    const safeCount = Number.isFinite(Number(count)) ? Number(count) : 0;
    els.pdaHistoryCount.textContent = `(${safeCount})`;
  }

  function setPdaHistoryExpanded(expanded) {
    isPdaHistoryExpanded = expanded === true;
    const content = pdaHistoryContentElement();
    if (content) {
      content.hidden = !isPdaHistoryExpanded;
      content.classList.toggle("is-collapsed", !isPdaHistoryExpanded);
      content.classList.toggle("is-expanded", isPdaHistoryExpanded);
    }
    if (els.pdaHistoryToggle) {
      els.pdaHistoryToggle.setAttribute("aria-expanded", String(isPdaHistoryExpanded));
      els.pdaHistoryToggle.setAttribute("aria-label", isPdaHistoryExpanded ? "Contraer historial" : "Desplegar historial");
    }
    if (els.pdaHistoryToggleIcon) {
      els.pdaHistoryToggleIcon.textContent = isPdaHistoryExpanded ? "▲" : "▼";
    }
  }

  function renderHistory() {
    if (!localHistory.length) {
      els.localHistory.textContent = "Sin lecturas en esta sesión.";
      return;
    }
    els.localHistory.innerHTML = localHistory.map((item) => `
      <div class="history-item ${item.ok ? "ok" : "error"}">
        <b>${escapeHtml(item.qr_value)}</b>
        <small>${escapeHtml(item.time)} · ${escapeHtml(item.ok ? "Correcto" : "Error")} · ${escapeHtml(item.action || "-")}</small>
        <small>${escapeHtml(item.message || "-")}</small>
      </div>
    `).join("");
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderLocalHistoryFallback() {
    updatePdaHistoryCount(localHistory.length);
    const target = els.localHistory;
    if (!target) return;
    if (!localHistory.length) {
      target.textContent = terminalValidationState === "valid" ? "Cargando historial de la terminal..." : "Configura y valida la terminal para cargar el historial.";
      return;
    }
    target.textContent = "";
    localHistory.forEach((item) => {
      const row = document.createElement("div");
      row.className = `history-item ${item.ok ? "ok" : "error"}`;
      const title = document.createElement("b");
      title.textContent = readableValue(item.qr_value) || "-";
      const meta = document.createElement("small");
      meta.textContent = `${readableValue(item.time)} - ${item.ok ? "Correcto" : "Error"} - ${readableValue(item.action || "-")}`;
      const message = document.createElement("small");
      message.textContent = readableValue(item.message || "-");
      row.append(title, meta, message);
      target.appendChild(row);
    });
  }

  function historyItemTone(item) {
    const status = readableValue(item?.status || item?.scan_status).toLowerCase();
    const dispatch = readableValue(item?.dispatch_status).toLowerCase();
    const rcs = readableValue(item?.rcs_status).toLowerCase();
    const error = readableValue(item?.error_message || item?.rcs_message).toLowerCase();
    if (status.includes("cancel_requested")) return "pending";
    if (["completed", "done", "success"].includes(status) || ["accepted", "ok", "success"].includes(dispatch) || ["accepted", "completed"].includes(rcs)) return "ok";
    if (["cancelled", "canceled", "undone", "forced_local_closed"].includes(status)) return "cancelled";
    if (status.includes("error") || dispatch.includes("error") || rcs.includes("error") || error) return "error";
    if (status.includes("pending") || status.includes("dispatch") || status.includes("progress")) return "pending";
    return "preview";
  }

  function canShowPdaActions(item) {
    return !!item?.movement_order_id;
  }

  function pdaActionAvailable(item) {
    return !!(item?.movement_order_id && item?.action_available === true);
  }

  function pdaActionUnavailableReason(item) {
    const status = readableValue(item?.status).toLowerCase();
    if (item?.action_unavailable_reason) return item.action_unavailable_reason;
    if (!item?.movement_order_id) return "Sin orden de movimiento asociada.";
    if (status === "dispatch_error" || readableValue(item?.dispatch_status).toLowerCase() === "dispatch_error") return "La tarea no fue aceptada por el RCS y el rollback local ya fue ejecutado.";
    if (status === "completed") return "La tarea ya fue completada.";
    if (status === "cancelled" || status === "canceled") return "La tarea ya fue cancelada.";
    if (status === "undone") return "La tarea ya fue devuelta.";
    if (status === "forced_local_closed") return "La tarea fue cerrada localmente.";
    if (status === "cancel_requested_total") return "La cancelacion de esta tarea ya esta en proceso.";
    if (status === "cancel_requested_undo") return "La cancelacion y devolucion de esta tarea ya esta en proceso.";
    return "Esta tarea no admite cancelacion en su estado actual.";
  }

  function areaDisplay(code, name) {
    const areaCode = readableValue(code);
    const areaName = readableValue(name);
    if (areaCode && areaName) return `${areaCode} - ${areaName}`;
    return areaCode || areaName || "No disponible";
  }

  function appendAreaBlock(container, title, cellCode, areaCode, areaName) {
    const block = document.createElement("div");
    block.className = "history-area-block";
    const heading = document.createElement("span");
    heading.textContent = title;
    const cell = document.createElement("b");
    cell.textContent = readableValue(cellCode) || "Celda no disponible";
    const area = document.createElement("small");
    area.textContent = `Area: ${areaDisplay(areaCode, areaName)}`;
    block.append(heading, cell, area);
    container.appendChild(block);
  }

  function appendCancelReturnAreaBlock(container, item) {
    const block = document.createElement("div");
    block.className = "history-area-block return-area";
    const heading = document.createElement("span");
    heading.textContent = "AREA DE DEVOLUCION";
    block.appendChild(heading);
    if (!item?.cancel_return_area_id) {
      const empty = document.createElement("b");
      empty.textContent = "Sin configurar";
      block.appendChild(empty);
      container.appendChild(block);
      return;
    }
    const name = document.createElement("b");
    name.textContent = areaDisplay(item.cancel_return_area_code, item.cancel_return_area_name);
    block.appendChild(name);
    if (Number(item.cancel_return_area_is_active ?? 1) !== 1) {
      const inactive = document.createElement("small");
      inactive.textContent = "Inactiva";
      block.appendChild(inactive);
    }
    const matter = document.createElement("small");
    const matterArea = readableValue(item.cancel_return_area_matter_area);
    matter.textContent = matterArea ? `Matter Area: ${matterArea}` : "Sin Matter Area";
    block.appendChild(matter);
    container.appendChild(block);
  }

  function appendHistoryField(container, label, value) {
    const display = readableValue(value);
    if (!display) return;
    const row = document.createElement("div");
    row.className = "history-field";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("b");
    valueEl.textContent = display;
    row.append(labelEl, valueEl);
    container.appendChild(row);
  }

  function normalizeHistoryRouteMode(value) {
    const mode = readableValue(value).toLowerCase();
    if (mode === "fifo_chain" || mode === "trmx_doble") return "fifo_chain";
    return mode;
  }

  function pdaFifoChainInfo(item) {
    if (normalizeHistoryRouteMode(item?.route_mode) !== "fifo_chain" && !item?.fifo_chain_group_id) return null;
    const step = Number(item?.fifo_chain_step || 0) || null;
    const total = Number(item?.fifo_chain_total_steps || 2) || 2;
    const groupId = readableValue(item?.fifo_chain_group_id);
    const groupShort = groupId ? groupId.slice(0, 8) : "-";
    const status = readableValue(item?.fifo_chain_status);
    const parentOrderId = item?.fifo_chain_parent_order_id;
    return { step, total, groupShort, status, parentOrderId };
  }

  function appendPdaFifoChainSummary(container, item) {
    if (pdaInterfaceMode !== "developer") return;
    const info = pdaFifoChainInfo(item);
    if (!info) return;
    const parts = [
      "Flujo doble FIFO",
      `Paso ${info.step || "-"}/${info.total || "-"}`,
      `Grupo ${info.groupShort}`,
    ];
    if (info.status) parts.push(`Estado ${info.status}`);
    if (info.step === 2 && info.parentOrderId) parts.push(`Generada por orden ${info.parentOrderId}`);

    const badge = document.createElement("div");
    badge.className = "fifo-chain-history-badge";
    badge.textContent = parts.join(" · ");
    container.appendChild(badge);

    if (info.step === 1 || info.step === 2) {
      const note = document.createElement("div");
      note.className = "fifo-chain-history-note";
      note.textContent = info.step === 1
        ? "Al simular completed se creará/despachará el paso 2 automáticamente."
        : "Último paso del Flujo doble FIFO. No se crearán más órdenes.";
      container.appendChild(note);
    }
  }

  function renderPdaHistory(items) {
    const target = els.localHistory;
    if (!target) return;
    pdaHistory = Array.isArray(items) ? items : [];
    updatePdaHistoryCount(pdaHistory.length);
    if (selectedHistoryItem?.movement_order_id) {
      const refreshedSelected = pdaHistory.find((item) => Number(item.movement_order_id) === Number(selectedHistoryItem.movement_order_id));
      if (refreshedSelected && !pdaActionAvailable(refreshedSelected) && isPdaActionModalOpen()) {
        closePdaActionModal({ refocus: false });
        setStatus("processing", "CANCELACION EN PROCESO");
      }
    }
    target.textContent = "";
    if (!pdaHistory.length) {
      target.textContent = "No hay lecturas registradas para esta terminal.";
      return;
    }
    pdaHistory.forEach((item) => {
      const card = document.createElement("div");
      card.className = `history-item history-card ${historyItemTone(item)}`;
      const title = document.createElement("b");
      title.textContent = `QR: ${readableValue(item.qr_value) || "-"}`;
      const meta = document.createElement("small");
      meta.textContent = `${formatPdaDate(item.created_at)} - Scanner: ${readableValue(item.scanner_code) || "-"}`;
      const fields = document.createElement("div");
      fields.className = "history-fields";
      appendHistoryField(fields, "Orden", item.order_code || (item.movement_order_id ? `ID ${item.movement_order_id}` : "Sin orden de movimiento asociada"));
      appendHistoryField(fields, "Rack", item.rack_code || item.rack_id);
      appendHistoryField(fields, "Lectura", item.scan_status);
      appendHistoryField(fields, "Estado orden", item.status);
      appendHistoryField(fields, "Dispatch", item.dispatch_status);
      appendHistoryField(fields, "RCS", item.rcs_status);
      appendHistoryField(fields, "Mensaje RCS", item.rcs_message);
      appendHistoryField(fields, "Error", item.error_message);
      card.append(title, meta, fields);
      appendPdaFifoChainSummary(card, item);

      const areaGrid = document.createElement("div");
      areaGrid.className = "history-area-grid";
      appendAreaBlock(areaGrid, "ORIGEN", item.source_cell_code || item.source_cell_id, item.source_area_code, item.source_area_name);
      appendAreaBlock(areaGrid, "DESTINO", item.destination_cell_code || item.destination_cell_id, item.destination_area_code, item.destination_area_name);
      appendCancelReturnAreaBlock(areaGrid, item);
      card.appendChild(areaGrid);

      if (item?.movement_order_id) {
        const actionButton = document.createElement("button");
        actionButton.type = "button";
        actionButton.className = `secondary small-btn history-action-btn ${pdaActionAvailable(item) ? "" : "blocked"}`.trim();
        const pending = pendingCancelOrders.has(Number(item.movement_order_id));
        actionButton.textContent = pending ? "Procesando..." : (pdaActionAvailable(item) ? "Acciones" : "Cancelar no disponible");
        actionButton.disabled = pending || !pdaActionAvailable(item);
        if (!pdaActionAvailable(item)) {
          actionButton.title = pdaActionUnavailableReason(item);
        } else {
          actionButton.addEventListener("click", () => openPdaActionModal(item));
        }
        card.appendChild(actionButton);
        if (!pdaActionAvailable(item)) {
          const reason = document.createElement("small");
          reason.className = "history-action-reason";
          reason.textContent = pdaActionUnavailableReason(item);
          card.appendChild(reason);
        }
      }
      target.appendChild(card);
    });
  }

  function renderHistory() {
    if (pdaHistory.length) renderPdaHistory(pdaHistory);
    else renderLocalHistoryFallback();
  }

  async function pdaFetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const textBody = await response.text();
    let data = {};
    try { data = textBody ? JSON.parse(textBody) : {}; } catch (_) { data = { raw: textBody }; }
    if (!response.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : "";
      const message = detail || data?.message || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.detail = detail;
      throw error;
    }
    return data;
  }

  async function loadPdaHistory({ force = false } = {}) {
    if (historyLoading) return;
    if (historyAuthPaused && !force) return;
    if (document.hidden && !force) return;
    const terminalCode = currentTerminalCode();
    if (!terminalCode) {
      pdaHistory = [];
      renderHistory();
      return;
    }
    historyLoading = true;
    const headers = { Accept: "application/json" };
    const apiKey = currentTerminalKey();
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    try {
      const data = await pdaFetchJson(`/api/scan/terminal/${encodeURIComponent(terminalCode)}/history?limit=20`, {
        method: "GET",
        headers,
        cache: "no-store",
      });
      historyAuthPaused = false;
      renderPdaHistory(Array.isArray(data) ? data : []);
    } catch (err) {
      updatePdaHistoryCount(pdaHistory.length);
      const target = els.localHistory;
      if (err?.status === 401 || err?.status === 403) {
        historyAuthPaused = true;
        stopPdaHistoryRefresh();
        if (target) target.textContent = err.message || "La terminal o clave no son validas. Configura nuevamente la terminal.";
      } else if (err?.status === 404) {
        historyAuthPaused = true;
        stopPdaHistoryRefresh();
        if (target) target.textContent = err.message || "Terminal no encontrada. Configura nuevamente la terminal.";
      } else {
        if (target) target.textContent = `No fue posible cargar el historial: ${err?.message || "error de red"}.`;
      }
    } finally {
      historyLoading = false;
    }
  }

  function startPdaHistoryRefresh() {
    if (historyRefreshTimer) return;
    historyRefreshTimer = window.setInterval(() => {
      if (terminalValidationState !== "valid" || historyAuthPaused || document.hidden) return;
      loadPdaHistory();
    }, 5000);
  }

  function stopPdaHistoryRefresh() {
    if (!historyRefreshTimer) return;
    window.clearInterval(historyRefreshTimer);
    historyRefreshTimer = null;
  }

  function refreshPdaHistoryNow() {
    if (terminalValidationState !== "valid") {
      renderHistory();
      return;
    }
    loadPdaHistory({ force: true });
    startPdaHistoryRefresh();
  }

  function isPdaActionModalOpen() {
    return !!(els.pdaActionModal && !els.pdaActionModal.classList.contains("hidden"));
  }

  function isPdaExecuteConfirmModalOpen() {
    return !!(els.pdaExecuteConfirmModal && !els.pdaExecuteConfirmModal.classList.contains("hidden"));
  }

  function isPdaModalOpen() {
    return isPdaActionModalOpen() || isPdaExecuteConfirmModalOpen() || !!(els.pdaAdminModal && !els.pdaAdminModal.classList.contains("hidden"));
  }

  function clearPendingExecutePreview() {
    pendingPreviewForExecution = null;
    pendingQrValue = "";
    isConfirmModalOpen = false;
    isExecutingConfirmed = false;
    setExecuteConfirmError("");
    setExecuteConfirmButtonsDisabled(false);
  }

  function closeExecuteConfirmModal({ refocus = true, clearPending = true, readyMessage = "" } = {}) {
    els.pdaExecuteConfirmModal?.classList.add("hidden");
    if (clearPending) clearPendingExecutePreview();
    if (els.qrInput) els.qrInput.value = "";
    resetLocalDuplicateGuard();
    setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
    if (readyMessage) setStatus("ready", readyMessage);
    if (refocus && terminalValidationState === "valid") focusScanner();
  }

  function cancelExecuteConfirmModal() {
    closeExecuteConfirmModal({ refocus: true, clearPending: true, readyMessage: "LISTO PARA ESCANEAR" });
    if (els.resultSummary) els.resultSummary.textContent = "Listo para escanear.";
  }

  function openExecuteConfirmModal(preview, qrValue) {
    pendingPreviewForExecution = preview;
    pendingQrValue = qrValue;
    isConfirmModalOpen = true;
    isExecutingConfirmed = false;
    clearScanIdleTimer();
    setScannerEnabled(false);
    fillExecuteConfirmSummary(preview, qrValue);
    setExecuteConfirmError("");
    setExecuteConfirmButtonsDisabled(false);
    els.pdaExecuteConfirmModal?.classList.remove("hidden");
    setStatus("processing", "CONFIRMAR ENVIO");
    window.setTimeout(() => {
      try { els.pdaExecuteConfirmModal?.focus(); } catch (_) {}
    }, 0);
  }

  function setPdaActionButtonsDisabled(disabled) {
    [
      els.btnPdaCancelOrder,
      els.btnPdaCancelReturnOrder,
      els.btnPdaCloseActionModal,
      els.btnPdaConfirmCancel,
      els.btnPdaConfirmCancelReturn,
      els.btnPdaBackAction,
    ].forEach((btn) => {
      if (btn) btn.disabled = !!disabled;
    });
  }

  function clearPdaActionError() {
    if (!els.pdaActionError) return;
    els.pdaActionError.textContent = "";
    els.pdaActionError.classList.add("hidden");
  }

  function setPdaActionError(message) {
    if (!els.pdaActionError) return;
    els.pdaActionError.textContent = message || "";
    els.pdaActionError.classList.toggle("hidden", !message);
  }

  function fillPdaActionSummary(item) {
    if (!els.pdaActionSummary) return;
    els.pdaActionSummary.textContent = "";
    const rows = [
      ["Orden", item?.order_code || item?.movement_order_id],
      ["QR", item?.qr_value],
      ["Rack", item?.rack_code || item?.rack_id],
      ["Origen", item?.source_cell_code || item?.source_cell_id],
      ["Destino", item?.destination_cell_code || item?.destination_cell_id],
    ];
    rows.forEach(([label, value]) => appendHistoryField(els.pdaActionSummary, label, value));
  }

  function openPdaActionModal(item) {
    selectedHistoryItem = item;
    selectedCancelAction = "";
    clearScanIdleTimer();
    setScannerEnabled(false);
    clearPdaActionError();
    fillPdaActionSummary(item);
    if (els.pdaActionConfirm) {
      els.pdaActionConfirm.textContent = "";
      els.pdaActionConfirm.classList.add("hidden");
    }
    els.pdaActionButtons?.classList.remove("hidden");
    els.pdaActionConfirmButtons?.classList.add("hidden");
    if (els.btnPdaConfirmCancel) els.btnPdaConfirmCancel.classList.add("hidden");
    if (els.btnPdaConfirmCancelReturn) els.btnPdaConfirmCancelReturn.classList.add("hidden");
    setPdaActionButtonsDisabled(false);
    els.pdaActionModal?.classList.remove("hidden");
  }

  function closePdaActionModal({ refocus = true } = {}) {
    selectedHistoryItem = null;
    selectedCancelAction = "";
    clearPdaActionError();
    els.pdaActionModal?.classList.add("hidden");
    els.pdaActionConfirm?.classList.add("hidden");
    els.pdaActionButtons?.classList.remove("hidden");
    els.pdaActionConfirmButtons?.classList.add("hidden");
    setPdaActionButtonsDisabled(false);
    setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
    if (refocus && terminalValidationState === "valid") focusScanner();
  }

  function showPdaCancelConfirm(action) {
    selectedCancelAction = action;
    clearPdaActionError();
    const isReturn = action === "cancel_return";
    if (els.pdaActionConfirm) {
      els.pdaActionConfirm.textContent = isReturn
        ? "Deseas cancelar esta tarea y devolver el material al area configurada? El area de devolucion se obtiene de la configuracion del scanner."
        : "Deseas cancelar esta tarea? La tarea se cancelara sin solicitar devolucion de material.";
      els.pdaActionConfirm.classList.remove("hidden");
    }
    els.pdaActionButtons?.classList.add("hidden");
    els.pdaActionConfirmButtons?.classList.remove("hidden");
    els.btnPdaConfirmCancel?.classList.toggle("hidden", isReturn);
    els.btnPdaConfirmCancelReturn?.classList.toggle("hidden", !isReturn);
  }

  async function cancelPdaMovementOrder(orderId, action) {
    const terminalCode = currentTerminalCode();
    if (!terminalCode) throw new Error("Configura terminal_code antes de cancelar.");
    if (!orderId) throw new Error("Orden no disponible.");
    const numericOrderId = Number(orderId);
    if (pendingCancelOrders.has(numericOrderId)) return;
    pendingCancelOrders.add(numericOrderId);
    setPdaActionButtonsDisabled(true);
    setPdaActionError("Procesando...");
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    const apiKey = currentTerminalKey();
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    try {
      const response = await pdaFetchJson(`/api/scan/terminal/${encodeURIComponent(terminalCode)}/movement-orders/${encodeURIComponent(numericOrderId)}/cancel`, {
        method: "POST",
        headers,
        body: JSON.stringify({ action }),
      });
      closePdaActionModal({ refocus: false });
      setStatus("ok", "CANCELACION ENVIADA");
      els.resultSummary.textContent = readableValue(response?.message || response?.status || "Cancelacion procesada.");
      await loadPdaHistory({ force: true });
      focusScanner();
    } catch (err) {
      setPdaActionError(err?.message || String(err));
      await loadPdaHistory({ force: true });
    } finally {
      pendingCancelOrders.delete(numericOrderId);
      setPdaActionButtonsDisabled(false);
      renderHistory();
    }
  }

  function currentMode() {
    if (pdaInterfaceMode === "operator") return "execute";
    if (terminalValidationState !== "valid" || !terminalConfig) return "preview";
    if (els.scanMode?.value === "execute" && executeAllowedByTerminalConfig()) return "execute";
    return "preview";
  }

  function syncModeWarning() {
    const execute = currentMode() === "execute";
    els.executeWarning?.classList.toggle("hidden", !execute);
    updateEyebrow();
    if (!isProcessing && !isConfigPanelOpen && terminalValidationState === "valid") setStatus("ready", "LISTO PARA ESCANEAR");
  }

  async function postTerminalScan(qrValue, mode = currentMode()) {
    const terminalCode = terminalConfig?.terminal_code || currentTerminalCode();
    if (!terminalCode) {
      els.configBody.classList.add("open");
      throw new Error("Configura terminal_code antes de escanear.");
    }
    const headers = { "Content-Type": "application/json" };
    const apiKey = currentTerminalKey();
    if (apiKey) headers["X-Terminal-Key"] = apiKey;
    const response = await fetch("/api/scan/terminal", {
      method: "POST",
      headers,
      body: JSON.stringify({ terminal_code: terminalCode, qr_value: qrValue, mode }),
    });
    const textBody = await response.text();
    let data = {};
    try { data = textBody ? JSON.parse(textBody) : {}; } catch (_) { data = { raw: textBody }; }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return data;
  }

  async function confirmPendingExecute() {
    if (!pendingPreviewForExecution || !pendingQrValue || isExecutingConfirmed) return;
    isExecutingConfirmed = true;
    setExecuteConfirmButtonsDisabled(true);
    setExecuteConfirmError("");
    setStatus("processing", "EJECUTANDO");
    const qrValue = pendingQrValue;
    const terminalCode = currentTerminalCode();
    try {
      const result = await postTerminalScan(qrValue, "execute");
      els.pdaExecuteConfirmModal?.classList.add("hidden");
      renderScanResult(result, { qrValue, terminalCode, mode: "execute" });
      addHistory({ time: new Date().toLocaleTimeString(), qr_value: qrValue, ok: !!result.ok, action: result.action || "-", message: result.message || "-" });
      await loadPdaHistory({ force: true });
      clearPendingExecutePreview();
      if (els.qrInput) els.qrInput.value = "";
      resetLocalDuplicateGuard();
      window.setTimeout(() => {
        syncModeWarning();
        setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
      }, 650);
    } catch (err) {
      const message = err?.message || String(err);
      els.pdaExecuteConfirmModal?.classList.add("hidden");
      renderScanResult({
        ok: false,
        status: "communication_error",
        qr_value: qrValue,
        terminal_code: terminalCode,
        mode: "execute",
        message: "No fue posible ejecutar la tarea.",
        error_message: message,
      }, { qrValue, terminalCode, mode: "execute" });
      addHistory({ time: new Date().toLocaleTimeString(), qr_value: qrValue, ok: false, action: "-", message });
      await loadPdaHistory({ force: true });
      clearPendingExecutePreview();
      if (els.qrInput) els.qrInput.value = "";
      resetLocalDuplicateGuard();
      syncModeWarning();
      setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
    }
  }

  async function submitQr(source = "manual") {
    clearScanIdleTimer();
    if (isConfirmModalOpen || isPdaExecuteConfirmModalOpen()) return;
    if (isProcessing) return;
    if (isConfigPanelOpen) {
      setConfigMessage("Termina de guardar o cancela la configuración antes de escanear.");
      setScannerEnabled(false);
      focusConfig();
      return;
    }
    if (terminalValidationState !== "valid" || !terminalConfig) {
      setTerminalValidationState(terminalValidationState === "unconfigured" ? "unconfigured" : "invalid", "Configura y valida la terminal antes de escanear.");
      renderValidationState();
      focusConfig();
      return;
    }
    const qrValue = text(els.qrInput.value, "");
    if (!qrValue) {
      lastSubmittedInputValue = "";
      focusScanner();
      return;
    }
    const now = Date.now();
    const isLocalDuplicate = qrValue === lastSubmission.value && now - lastSubmission.timestamp < DUPLICATE_WINDOW_MS;
    if (isLocalDuplicate || (qrValue === lastSubmittedQr && now - lastSubmittedAt < DUPLICATE_WINDOW_MS)) {
      showLocalDuplicateIgnored(qrValue);
      els.qrInput.value = "";
      lastSubmittedInputValue = "";
      focusScanner();
      return;
    }
    lastSubmittedInputValue = qrValue;
    lastSubmission = { value: qrValue, timestamp: now };
    lastSubmittedQr = qrValue;
    lastSubmittedAt = now;
    clearScanIdleTimer();
    isProcessing = true;
    const requestedMode = currentMode();
    const submissionId = ++submissionSequence;
    activeSubmission = {
      id: submissionId,
      qrValue,
      source,
      startedAt: now,
    };
    els.qrInput.disabled = true;
    setStatus("processing", requestedMode === "execute" ? "PREVIEW PARA EJECUTAR" : "PROCESANDO");
    setResultClass("processing");
    if (els.resultTitle) els.resultTitle.textContent = "PROCESANDO...";
    els.resultSummary.textContent = "Enviando lectura al servidor...";
    if (els.resultSections) els.resultSections.textContent = "";
    if (els.resultDetails) els.resultDetails.classList.add("hidden");
    setField(els.resQr, qrValue);
    setField(els.resTerminal, currentTerminalCode());
    setField(els.resScanner, terminalConfig?.scanner?.scanner_code);
    setField(els.resAction, requestedMode);
    setField(els.resAlias, "");
    setField(els.resType, "");
    setField(els.resMaterial, "");
    setField(els.resRack, "");
    setField(els.resSource, "");
    setField(els.resDestination, "");
    setField(els.resOrder, "");
    setField(els.resEvent, "");
    setField(els.resError, "");
    try {
      if (requestedMode === "execute") {
        const preview = await postTerminalScan(qrValue, "preview");
        renderScanResult(preview, { qrValue, terminalCode: currentTerminalCode(), mode: "preview" });
        if (!preview?.ok) {
          addHistory({ time: new Date().toLocaleTimeString(), qr_value: qrValue, ok: false, action: preview?.action || "-", message: preview?.message || "Preview rechazado." });
          await loadPdaHistory({ force: true });
          return;
        }
        openExecuteConfirmModal(preview, qrValue);
        return;
      }
      const result = await postTerminalScan(qrValue, "preview");
      renderScanResult(result, { qrValue, terminalCode: currentTerminalCode(), mode: "preview" });
      addHistory({ time: new Date().toLocaleTimeString(), qr_value: qrValue, ok: !!result.ok, action: result.action || "-", message: result.message || "-" });
      await loadPdaHistory({ force: true });
    } catch (err) {
      const message = err?.message || String(err);
      renderScanResult({
        ok: false,
        status: "communication_error",
        qr_value: qrValue,
        terminal_code: currentTerminalCode(),
        mode: requestedMode === "execute" ? "preview" : requestedMode,
        message: "No fue posible comunicarse con el sistema de despacho.",
        error_message: message,
      }, { qrValue, terminalCode: currentTerminalCode(), mode: requestedMode === "execute" ? "preview" : requestedMode });
      addHistory({ time: new Date().toLocaleTimeString(), qr_value: qrValue, ok: false, action: "-", message });
      await loadPdaHistory({ force: true });
    } finally {
      clearScanIdleTimer();
      if (activeSubmission?.id === submissionId) {
        activeSubmission = null;
        els.qrInput.value = "";
        lastSubmittedInputValue = "";
        isProcessing = false;
        if (isConfirmModalOpen || isPdaExecuteConfirmModalOpen()) {
          setScannerEnabled(false);
          return;
        }
        window.setTimeout(() => {
          if (!isProcessing) syncModeWarning();
          setScannerEnabled(!isConfigPanelOpen && terminalValidationState === "valid" && !!terminalConfig);
        }, 650);
      }
    }
  }

  els.btnToggleConfig.addEventListener("click", () => {
    if (!hasPdaDeveloperAccess()) return;
    if (isConfigPanelOpen || els.configBody.classList.contains("open")) {
      cancelConfig();
      return;
    }
    openConfigPanel();
  });
  els.btnSaveConfig.addEventListener("click", () => { if (hasPdaDeveloperAccess()) saveConfig(); });
  els.btnCancelConfig?.addEventListener("click", () => { if (hasPdaDeveloperAccess()) cancelConfig(); });
  els.btnClearConfig.addEventListener("click", () => { if (hasPdaDeveloperAccess()) clearConfig(); });
  els.btnRefocus.addEventListener("click", () => {
    if (!hasPdaDeveloperAccess()) return;
    if (terminalValidationState === "valid") focusScanner();
    else focusConfig();
  });
  els.scanMode?.addEventListener("change", () => {
    if (!hasPdaDeveloperAccess()) {
      if (els.scanMode) els.scanMode.value = "execute";
      return;
    }
    if (terminalValidationState !== "valid") {
      syncModeWithTerminalConfig();
      syncModeWarning();
      focusConfig();
      return;
    }
    scanModeSelectedByUser = true;
    if (els.scanMode.value === "execute" && !executeAllowedByTerminalConfig()) {
      els.scanMode.value = "preview";
    }
    syncModeWarning();
    focusScanner();
  });
  els.qrCatalogSelect?.addEventListener("change", () => { if (hasPdaDeveloperAccess()) handleQrCatalogSelectionChange(); });
  els.btnHideQrCatalog?.addEventListener("click", () => { if (hasPdaDeveloperAccess()) hideSelectedQrCatalog({ refocus: true }); });
  els.pdaHistoryToggle?.addEventListener("click", () => {
    setPdaHistoryExpanded(!isPdaHistoryExpanded);
  });
  els.btnClearHistory.addEventListener("click", () => {
    refreshPdaHistoryNow();
    if (terminalValidationState === "valid") focusScanner();
    else focusConfig();
  });
  els.btnPdaCloseActionModal?.addEventListener("click", () => closePdaActionModal());
  els.btnPdaAdminAccess?.addEventListener("click", openPdaAdminModal);
  els.btnPdaCloseAdminModal?.addEventListener("click", closePdaAdminModal);
  els.btnPdaAdminLogin?.addEventListener("click", loginPdaAdministrator);
  els.pdaAdminPassword?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loginPdaAdministrator();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closePdaAdminModal();
    }
  });
  els.btnPdaExitDeveloper?.addEventListener("click", () => exitPdaDeveloperMode());
  els.btnPdaBackAction?.addEventListener("click", () => {
    selectedCancelAction = "";
    clearPdaActionError();
    els.pdaActionConfirm?.classList.add("hidden");
    els.pdaActionButtons?.classList.remove("hidden");
    els.pdaActionConfirmButtons?.classList.add("hidden");
    els.btnPdaConfirmCancel?.classList.add("hidden");
    els.btnPdaConfirmCancelReturn?.classList.add("hidden");
  });
  els.btnPdaCancelOrder?.addEventListener("click", () => showPdaCancelConfirm("cancel"));
  els.btnPdaCancelReturnOrder?.addEventListener("click", () => showPdaCancelConfirm("cancel_return"));
  els.btnPdaConfirmCancel?.addEventListener("click", () => cancelPdaMovementOrder(selectedHistoryItem?.movement_order_id, "cancel"));
  els.btnPdaConfirmCancelReturn?.addEventListener("click", () => cancelPdaMovementOrder(selectedHistoryItem?.movement_order_id, "cancel_return"));
  els.btnPdaExecuteConfirm?.addEventListener("click", confirmPendingExecute);
  els.btnPdaExecuteCancel?.addEventListener("click", cancelExecuteConfirmModal);
  els.qrInput.addEventListener("keydown", (event) => {
    if (isPdaModalOpen()) return;
    if (event.key !== "Enter") return;
    event.preventDefault();
    clearScanIdleTimer();
    lastSubmittedInputValue = text(els.qrInput.value, "");
    submitQr("enter");
  });
  els.qrInput.addEventListener("input", scheduleScanIdleSubmit);
  document.addEventListener("keydown", (event) => {
    if (isPdaExecuteConfirmModalOpen()) {
      if (event.key === "Enter") {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        cancelExecuteConfirmModal();
      }
      return;
    }
    if (event.key !== "Escape") return;
    if (els.pdaAdminModal && !els.pdaAdminModal.classList.contains("hidden")) {
      event.preventDefault();
      closePdaAdminModal();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearScanIdleTimer();
      return;
    }
    if (!document.hidden && terminalValidationState === "valid" && !isPdaModalOpen()) {
      refreshPdaHistoryNow();
      focusScanner();
    }
  });
  window.addEventListener("pageshow", () => {
    if (terminalValidationState === "valid" && !isPdaModalOpen()) {
      refreshPdaHistoryNow();
      focusScanner();
    }
  });
  window.addEventListener("pagehide", revokeQrCatalogImageUrl);
  window.addEventListener("beforeunload", revokeQrCatalogImageUrl);

  updatePdaHistoryCount(0);
  setPdaHistoryExpanded(false);
  setPdaInterfaceMode("operator");

  initConfig().finally(() => {
    syncModeWarning();
    setPdaInterfaceMode(pdaInterfaceMode);
    renderHistory();
    if (terminalValidationState === "valid") {
      refreshPdaHistoryNow();
      focusScanner();
    }
  });
})();
