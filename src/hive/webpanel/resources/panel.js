(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const routes = [
    ["overview", "Overview", "Runtime posture, metrics, and current work"],
    ["takeovers", "Takeovers", "Observed chats and active engagements"],
    ["intelligence", "Intelligence", "Indicators and sandbox findings"],
    ["evidence", "Evidence", "Sealed case bundles"],
    ["activity", "Activity", "Operator and control-plane events"],
    ["logs", "Logs", "Redacted live diagnostics"],
    ["models", "Models", "Endpoint and tier assignments"],
    ["telegram", "Telegram", "Messaging plane configuration"],
    ["security", "Security", "Readiness, keys, and panel access"],
  ];
  const personaLabels = {
    confused_elderly: "Confused elderly",
    naive_young_adult: "Naive young adult",
    overseas_worker: "Overseas worker",
    small_business_owner: "Small business owner",
  };
  const readinessLabels = {
    llm: "LLM",
    control_bot: "Control bot",
    telethon: "Telethon",
    signing_key: "Signing key",
    panel: "Panel",
  };
  const injectedToken = window.__HIVE_PANEL_TOKEN__ === "__SESSION_TOKEN__" ? "" : window.__HIVE_PANEL_TOKEN__;
  const hashParams = new URLSearchParams(location.hash.slice(1));
  let token = hashParams.get("token") || injectedToken || sessionStorage.getItem("hive-panel-token") || "";
  if (token) sessionStorage.setItem("hive-panel-token", token);

  const state = {
    route: localStorage.getItem("hive-route") || "overview",
    dashboard: null,
    sessions: [],
    chats: [],
    selectedPeer: null,
    selectedSession: null,
    activity: [],
    logs: [],
    activityHiddenBefore: 0,
    telegramAttempt: "",
    telegramStage: "start",
    setupOnly: false,
    loading: new Set(),
  };

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const titleCase = (value) => String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const formatTime = (timestamp) => timestamp ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp * 1000)) : "-";
  const formatDate = (timestamp) => timestamp ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(timestamp * 1000)) : "-";
  const formatDuration = (seconds) => {
    if (seconds == null) return "-";
    const minutes = Math.floor(seconds / 60);
    return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
  };
  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes)) return "-";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  };
  const chipClass = (value) => {
    const normalized = String(value || "").toLowerCase().replaceAll(" ", "_");
    if (["running", "ready", "success", "healthy", "configured", "signed"].includes(normalized)) return "success";
    if (["error", "failed", "stopped", "likely_scam", "missing", "unsigned"].includes(normalized)) return "error";
    if (["warning", "degraded", "restart_required"].includes(normalized)) return "warning";
    return "info";
  };
  const emptyState = (title, detail) => `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div>`;
  const tableEmpty = (columns, title, detail) => `<tr><td colspan="${columns}">${emptyState(title, detail)}</td></tr>`;

  function setMessage(selector, message = "", kind = "") {
    const node = $(selector);
    if (!node) return;
    node.textContent = message;
    node.className = `inline-message${kind ? ` ${kind}` : ""}`;
  }

  function toast(message, kind = "") {
    const node = document.createElement("div");
    node.className = `toast${kind ? ` ${kind}` : ""}`;
    node.textContent = message;
    $("#toastRegion").append(node);
    setTimeout(() => node.remove(), 4200);
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-HIVE-Token", token);
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch { payload = { detail: response.statusText }; }
      const detail = payload.detail;
      const message = typeof detail === "string" ? detail : detail?.code === "agent_not_running" ? "The agent is not running." : JSON.stringify(detail || payload);
      const error = new Error(message || `Request failed (${response.status})`);
      error.status = response.status;
      error.detail = detail;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }

  async function withLoading(key, button, work) {
    if (state.loading.has(key)) return;
    state.loading.add(key);
    const old = button?.textContent;
    if (button) { button.disabled = true; button.textContent = "Working..."; }
    try { return await work(); }
    finally {
      state.loading.delete(key);
      if (button) { button.disabled = false; button.textContent = old; }
    }
  }

  function confirmAction(title, text, acceptLabel = "Confirm") {
    const dialog = $("#confirmDialog");
    $("#confirmTitle").textContent = title;
    $("#confirmText").textContent = text;
    $("#confirmAccept").textContent = acceptLabel;
    dialog.showModal();
    return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true }));
  }

  function navigate(route) {
    if (!routes.some(([key]) => key === route)) route = "overview";
    state.route = route;
    localStorage.setItem("hive-route", route);
    $$(".page").forEach((page) => page.classList.toggle("active", page.dataset.page === route));
    $$(".nav-item").forEach((item) => {
      const active = item.dataset.route === route;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    });
    $("#currentPageName").textContent = routes.find(([key]) => key === route)?.[1] || "Overview";
    $("#sidebar").classList.remove("open");
    $("#commandDialog").close?.();
    loadRoute(route);
    $("#workspace").focus({ preventScroll: true });
  }

  async function loadRoute(route) {
    try {
      if (route === "overview") await loadDashboard();
      if (["takeovers", "intelligence"].includes(route)) await loadOperations();
      if (route === "evidence") await loadEvidence();
      if (route === "activity") await loadActivity();
      if (route === "logs") await loadLogs();
      if (route === "models") await loadModels();
      if (route === "telegram") await Promise.all([loadTelegram(), loadSetup()]);
      if (route === "security") await loadSetup();
    } catch (error) {
      if (error.status === 401) toast("Panel authentication failed. Set a valid token in Security.", "error");
      else toast(error.message, "error");
    }
  }

  function renderRuntime(runtime = {}) {
    const running = Boolean(runtime.running);
    $("#sidebarRuntime").textContent = running ? "Agent running" : titleCase(runtime.state || "Agent stopped");
    $("#sidebarRuntimeMeta").textContent = runtime.restart_required ? "Restart required" : running ? `${runtime.active_sessions || 0} active takeover(s)` : runtime.ready === false ? "Setup incomplete" : "Panel connected";
    $("#sidebarRuntimeDot").className = `runtime-dot ${running ? "running" : runtime.error ? "error" : ""}`;
    $("#runtimeState").textContent = titleCase(runtime.state || "Unknown");
    $("#runtimeState").className = `status-chip ${chipClass(runtime.state)}`;
    $("#runtimeSummary").textContent = runtime.restart_required ? "Configuration changed; restart to apply it." : running ? "All process states shown below are live." : "The control panel remains available while the agent is stopped.";
    $("#startAgent").disabled = running;
    $("#restartAgent").disabled = !running;
    $("#stopAgent").disabled = !running;
    const components = runtime.components || {};
    const entries = Object.entries(components);
    $("#componentList").innerHTML = entries.length ? entries.map(([name, item]) => {
      const status = item.state || (item.ready ? "ready" : item.running ? "running" : "stopped");
      return `<div class="component-row"><div><strong>${escapeHtml(titleCase(name))}</strong><span>${escapeHtml(item.detail || item.error || "No diagnostic detail")}</span></div><span class="status-chip ${chipClass(status)}">${escapeHtml(titleCase(status))}</span></div>`;
    }).join("") : emptyState("No component telemetry", "Start the runtime to populate component health.");
  }

  function renderCompact(items, kind) {
    if (!items.length) return emptyState(kind === "chat" ? "Queue is clear" : "No active engagements", kind === "chat" ? "New observed private chats will appear here." : "Begin a takeover from the incoming queue.");
    return items.map((item) => {
      if (kind === "chat") return `<div class="compact-item"><div><strong>${escapeHtml(item.display_name || item.username || `Peer ${item.peer_id}`)}</strong><span>${escapeHtml(item.latest_text || item.text || "No message preview")}</span></div><time>${formatTime(item.latest_ts || item.ts)}</time></div>`;
      return `<button class="compact-item text-button" type="button" data-open-peer="${Number(item.peer_id)}"><div><strong>Peer ${Number(item.peer_id)}</strong><span>${escapeHtml(titleCase(item.verdict))} · ${item.turns} turns</span></div><span>${Math.round(Number(item.score || 0) * 100)}%</span></button>`;
    }).join("");
  }

  function renderActivity(items, compact = false) {
    if (!items.length) return emptyState("No activity yet", "Operator and runtime actions will appear here.");
    return items.map((item) => `<div class="timeline-item"><time>${formatTime(item.ts)}</time><span class="category">${escapeHtml(titleCase(item.category))}</span><div><strong>${escapeHtml(item.title)}</strong>${item.detail ? `<p>${escapeHtml(item.detail)}${item.peer_id ? ` · Peer ${Number(item.peer_id)}` : ""}</p>` : ""}</div></div>`).join("");
  }

  async function loadDashboard() {
    try {
      const data = await api("/api/dashboard");
      state.dashboard = data;
      state.setupOnly = false;
      renderRuntime(data.runtime);
      const metrics = data.metrics || {};
      $("#metricActive").textContent = metrics.active_sessions ?? 0;
      $("#metricScams").textContent = metrics.likely_scams ?? 0;
      $("#metricHvis").textContent = metrics.hvis ?? 0;
      $("#metricSandbox").textContent = metrics.sandbox_runs ?? 0;
      $("#metricTurns").textContent = metrics.turns ?? 0;
      $("#takeoverCount").textContent = metrics.active_sessions ?? 0;
      $("#overviewQueue").innerHTML = renderCompact(data.chats || [], "chat");
      $("#overviewSessions").innerHTML = renderCompact(data.sessions || [], "session");
      $("#overviewActivity").innerHTML = renderActivity(data.activity || [], true);
    } catch (error) {
      if (error.status !== 404) throw error;
      state.setupOnly = true;
      renderRuntime({ state: "setup", running: false, ready: false, components: {} });
      ["#metricActive", "#metricScams", "#metricHvis", "#metricSandbox", "#metricTurns"].forEach((selector) => { $(selector).textContent = "0"; });
      $("#overviewQueue").innerHTML = emptyState("Data plane not started", "Configure Telegram, then start the agent to observe private chats.");
      $("#overviewSessions").innerHTML = emptyState("No active engagements", "Takeovers become available after the runtime starts.");
      $("#overviewActivity").innerHTML = emptyState("Runtime not started", "Operational events will appear here after setup.");
      setMessage("#runtimeMessage", "Setup mode: configure Models, Telegram, and Security before starting HIVE.");
    }
  }

  async function runtimeAction(action, force = false) {
    const button = $(`#${action}Agent`);
    return withLoading(`runtime-${action}`, button, async () => {
      try {
        const result = await api(`/api/runtime/${action}`, { method: "POST", body: JSON.stringify({ force }) });
        renderRuntime(result);
        setMessage("#runtimeMessage", action === "stop" ? "Agent stopped. The panel remains available." : `Agent ${action} completed.`, "success");
        await loadDashboard();
      } catch (error) {
        if (error.detail?.code === "active_sessions" && !force) {
          const confirmed = await confirmAction("Active sessions will be discarded", `${error.detail.count} takeover(s) are still active. Force ${action}?`, `Force ${action}`);
          if (confirmed) return runtimeAction(action, true);
        }
        setMessage("#runtimeMessage", error.message, "error");
      }
    });
  }

  async function loadOperations() {
    try {
      const [chats, sessions] = await Promise.all([api("/api/chats"), api("/api/sessions")]);
      state.chats = chats;
      state.sessions = sessions;
      $("#takeoverCount").textContent = sessions.length;
      renderOperations();
      renderIntelligenceSessionOptions();
    } catch (error) {
      if (error.status !== 409) throw error;
      state.chats = [];
      state.sessions = [];
      renderOperations();
      renderIntelligenceSessionOptions();
      setMessage("#takeoverMessage", "Start the agent to load observed chats and active takeovers.");
    }
  }

  function renderOperations() {
    const query = $("#takeoverSearch").value.trim().toLowerCase();
    const chats = state.chats.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    const sessions = state.sessions.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    $("#chatCountLabel").textContent = `${chats.length} observed chat${chats.length === 1 ? "" : "s"}`;
    $("#sessionCountLabel").textContent = `${sessions.length} active takeover${sessions.length === 1 ? "" : "s"}`;
    $("#chatRows").innerHTML = chats.length ? chats.map((item) => `<tr><td><strong>${escapeHtml(item.display_name || item.username || `Peer ${item.peer_id}`)}</strong><br><span class="mono">${Number(item.peer_id)}</span></td><td class="ellipsis">${escapeHtml(item.latest_text || item.text || "No preview")}</td><td>${formatDate(item.latest_ts || item.ts)}</td><td class="actions"><button class="table-button" type="button" data-takeover-peer="${Number(item.peer_id)}">Take over</button></td></tr>`).join("") : tableEmpty(4, "No observed chats", "Start the data plane or adjust the search filter.");
    $("#sessionRows").innerHTML = sessions.length ? sessions.map((item) => `<tr><td class="mono">${Number(item.peer_id)}</td><td>${escapeHtml(personaLabels[item.persona] || titleCase(item.persona))}</td><td>${escapeHtml(titleCase(item.phase))}</td><td><span class="status-chip ${chipClass(item.verdict)}">${escapeHtml(titleCase(item.verdict))}</span></td><td>${Math.round(Number(item.score || 0) * 100)}%</td><td class="actions"><button class="table-button" type="button" data-open-peer="${Number(item.peer_id)}">Inspect</button></td></tr>`).join("") : tableEmpty(6, "No active takeovers", "Begin a controlled engagement from an observed chat.");
  }

  async function beginTakeover(peerId) {
    try {
      await api("/api/takeover", { method: "POST", body: JSON.stringify({ peer_id: Number(peerId), persona: $("#takeoverPersona").value }) });
      toast(`Takeover started for peer ${peerId}.`, "success");
      await loadOperations();
      await openSession(Number(peerId));
    } catch (error) { setMessage("#takeoverMessage", error.message, "error"); }
  }

  function showManualTakeover() {
    const button = $("#manualTakeover");
    if ($("#manualPeerInput")) return $("#manualPeerInput").focus();
    button.hidden = true;
    const input = document.createElement("input");
    input.id = "manualPeerInput";
    input.inputMode = "numeric";
    input.placeholder = "Telegram peer ID";
    input.setAttribute("aria-label", "Telegram peer ID");
    input.style.maxWidth = "190px";
    const start = document.createElement("button");
    start.className = "button primary compact";
    start.type = "button";
    start.textContent = "Begin";
    const cancel = document.createElement("button");
    cancel.className = "button secondary compact";
    cancel.type = "button";
    cancel.textContent = "Cancel";
    const remove = () => { input.remove(); start.remove(); cancel.remove(); button.hidden = false; };
    start.addEventListener("click", () => { const peer = Number(input.value); if (!Number.isSafeInteger(peer)) return setMessage("#takeoverMessage", "Enter a valid integer peer ID.", "error"); remove(); beginTakeover(peer); });
    cancel.addEventListener("click", remove);
    button.before(input, start, cancel);
    input.focus();
  }

  async function openSession(peerId) {
    try {
      const session = await api(`/api/sessions/${peerId}`);
      state.selectedPeer = peerId;
      state.selectedSession = session;
      renderInspector(session);
      $("#sessionInspector").classList.add("open");
      if (state.route === "intelligence") renderIntelligence(session);
    } catch (error) { toast(error.message, "error"); }
  }

  function renderInspector(session) {
    $("#inspectorEmpty").hidden = true;
    $("#inspectorContent").hidden = false;
    $("#inspectorPeer").textContent = `PEER ${session.peer_id}`;
    $("#inspectorTitle").textContent = personaLabels[session.persona] || titleCase(session.persona);
    $("#inspectorVerdict").textContent = titleCase(session.verdict);
    $("#inspectorVerdict").className = `status-chip ${chipClass(session.verdict)}`;
    $("#inspectorScore").textContent = `${Math.round(Number(session.score || 0) * 100)}%`;
    $("#inspectorTurns").textContent = `${session.turns} turns · ${formatDuration(session.duration_s)}`;
    $("#inspectorPersona").innerHTML = Object.entries(personaLabels).map(([key, label]) => `<option value="${key}" ${key === session.persona ? "selected" : ""}>${label}</option>`).join("");
    $("#inspectorTranscript").innerHTML = session.messages?.length ? session.messages.map((message) => `<div class="message ${message.role === "agent" || message.role === "assistant" ? "agent" : "peer"}"><p>${escapeHtml(message.text)}</p><span>${escapeHtml(titleCase(message.role))} · ${formatTime(message.ts)}${message.media_kind ? ` · ${escapeHtml(message.media_kind)}` : ""}</span></div>`).join("") : emptyState("No transcript yet", "Messages will appear after the engagement begins.");
    $("#inspectorSignals").innerHTML = session.signal_trail?.length ? session.signal_trail.map((signal) => `<div class="signal-item"><strong>${escapeHtml(signal.name || signal.kind || titleCase(signal.signal || "Signal"))}</strong><p>${escapeHtml(signal.detail || signal.reason || JSON.stringify(signal))}</p></div>`).join("") : emptyState("No signals yet", "Classifier evidence will appear as messages are assessed.");
    $("#inspectorIndicators").innerHTML = session.hvi_items?.length ? session.hvi_items.map((item) => `<div class="indicator-item"><strong>${escapeHtml(titleCase(item.kind))}</strong><p class="mono">${escapeHtml(item.value)}</p><span>${Math.round(Number(item.confidence || 0) * 100)}% confidence</span></div>`).join("") : emptyState("No indicators extracted", "URLs, wallet addresses, accounts, and phone numbers will appear here.");
  }

  function renderIntelligenceSessionOptions() {
    const select = $("#intelligenceSession");
    if (!state.sessions.length) {
      select.innerHTML = "<option>No active sessions</option>";
      select.disabled = true;
      $("#intelligenceSummary").innerHTML = emptyState("No session selected", "Start a takeover to inspect extracted intelligence.");
      $("#hviRows").innerHTML = tableEmpty(4, "No indicators", "No active session is selected.");
      $("#sandboxList").innerHTML = emptyState("No sandbox results", "No active session is selected.");
      return;
    }
    select.disabled = false;
    select.innerHTML = state.sessions.map((item) => `<option value="${Number(item.peer_id)}" ${item.peer_id === state.selectedPeer ? "selected" : ""}>Peer ${Number(item.peer_id)} · ${escapeHtml(titleCase(item.verdict))}</option>`).join("");
    const peer = state.selectedPeer && state.sessions.some((item) => item.peer_id === state.selectedPeer) ? state.selectedPeer : Number(select.value);
    if (!state.selectedSession || state.selectedSession.peer_id !== peer) openSession(peer);
  }

  function renderIntelligence(session) {
    $("#intelligenceSession").value = String(session.peer_id);
    $("#intelligenceSummary").innerHTML = [
      ["Peer", session.peer_id], ["Verdict", titleCase(session.verdict)], ["Confidence", `${Math.round(Number(session.score || 0) * 100)}%`], ["Duration", formatDuration(session.duration_s)],
    ].map(([label, value]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    $("#hviRows").innerHTML = session.hvi_items?.length ? session.hvi_items.map((item) => `<tr><td>${escapeHtml(titleCase(item.kind))}</td><td class="mono">${escapeHtml(item.value)}</td><td>${Math.round(Number(item.confidence || 0) * 100)}%</td><td>Active transcript</td></tr>`).join("") : tableEmpty(4, "No high-value indicators", "The extraction pipeline has not found a supported value.");
    $("#sandboxList").innerHTML = session.sandbox_results?.length ? session.sandbox_results.map((item) => `<div class="sandbox-item"><strong>${escapeHtml(item.url || item.target || "Sandbox run")}</strong><p>${escapeHtml(item.summary || item.verdict || JSON.stringify(item))}</p></div>`).join("") : emptyState("No sandbox analysis", "Links are analyzed only when the active policy permits it.");
  }

  async function sealSession() {
    if (!state.selectedPeer) return;
    const confirmed = await confirmAction("Stop and seal this session?", "The engagement will end and HIVE will generate an immutable evidence bundle.", "Stop and seal");
    if (!confirmed) return;
    await withLoading("seal", $("#sealSession"), async () => {
      try {
        await api(`/api/sessions/${state.selectedPeer}/stop`, { method: "POST", body: "{}" });
        toast(`Evidence sealed for peer ${state.selectedPeer}.`, "success");
        state.selectedPeer = null;
        state.selectedSession = null;
        $("#sessionInspector").classList.remove("open");
        await loadOperations();
        navigate("evidence");
      } catch (error) { toast(error.message, "error"); }
    });
  }

  async function loadEvidence() {
    const rows = await api("/api/evidence");
    $("#evidenceCount").textContent = `${rows.length} sealed bundle${rows.length === 1 ? "" : "s"}`;
    $("#evidenceRows").innerHTML = rows.length ? rows.map((item) => `<tr><td class="mono">${Number(item.peer_id)}</td><td>${formatDate(item.created_ts)}</td><td>${formatBytes(item.size)}</td><td><div class="hash" title="${escapeHtml(item.sha256)}">${escapeHtml(item.sha256)}</div></td><td><span class="status-chip ${item.signature_present ? "success" : "error"}">${item.signature_present ? "Signed" : "Unsigned"}</span></td><td class="actions"><button class="table-button" type="button" data-download-evidence="${Number(item.peer_id)}">Download PDF</button></td></tr>`).join("") : tableEmpty(6, "No evidence bundles", "Stop and seal a takeover to create the first case file.");
  }

  async function downloadEvidence(peerId) {
    try {
      const response = await fetch(`/api/sessions/${peerId}/evidence`, { headers: { "X-HIVE-Token": token } });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `evidence_${peerId}.pdf`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(error.message, "error"); }
  }

  async function loadActivity() {
    const data = await api("/api/activity?limit=300");
    state.activity = data.items || [];
    renderActivityPage();
  }

  function renderActivityPage() {
    const query = $("#activitySearch").value.trim().toLowerCase();
    const severity = $("#activitySeverity").value;
    const items = state.activity.filter((item) => item.id > state.activityHiddenBefore && (severity === "all" || item.severity === severity) && JSON.stringify(item).toLowerCase().includes(query));
    $("#activityTimeline").innerHTML = renderActivity(items);
  }

  async function loadLogs() {
    const data = await api("/api/logs?limit=400");
    state.logs = data.items || [];
    renderLogs();
  }

  function filteredLogs() {
    const query = $("#logSearch").value.trim().toLowerCase();
    const level = $("#logLevel").value;
    return state.logs.filter((item) => (level === "all" || item.level === level) && JSON.stringify(item).toLowerCase().includes(query));
  }

  function renderLogs() {
    const viewer = $("#logViewer");
    const items = filteredLogs();
    viewer.innerHTML = items.length ? items.map((item) => `<div class="log-line ${escapeHtml(item.level)}"><time>${formatTime(item.ts)}</time><span class="level">${escapeHtml(String(item.level).toUpperCase())}</span><span class="logger">${escapeHtml(item.logger)}</span><span>${escapeHtml(item.message)}</span></div>`).join("") : emptyState("No matching logs", "Adjust the level or text filter.");
    if ($("#logFollow").checked) viewer.scrollTop = viewer.scrollHeight;
  }

  function exportLogs() {
    const lines = filteredLogs().map((item) => `${new Date(item.ts * 1000).toISOString()} ${String(item.level).toUpperCase()} ${item.logger}: ${item.message}`).join("\n");
    const url = URL.createObjectURL(new Blob([lines], { type: "text/plain" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `hive-logs-${new Date().toISOString().slice(0, 10)}.txt`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function loadModels() {
    try {
      const data = await api("/api/models/status");
      $("#modelEndpoint").textContent = data.endpoint || "Not configured";
      $("#modelHealth").textContent = titleCase(data.component?.state || (data.configured ? "configured" : "missing"));
      $("#modelHealth").className = `status-chip ${chipClass(data.component?.state || (data.configured ? "configured" : "missing"))}`;
      $("#modelDefinition").innerHTML = Object.entries(data.models || {}).map(([tier, model]) => `<div class="definition-row"><span>${escapeHtml(titleCase(tier))}</span><strong>${escapeHtml(model)}</strong></div>`).join("");
      $("#llmUrl").value = data.endpoint || $("#llmUrl").value;
      $("#modelCheap").value = data.models?.cheap || $("#modelCheap").value;
      $("#modelStrong").value = data.models?.strong || $("#modelStrong").value;
      $("#modelLight").value = data.models?.light || $("#modelLight").value;
      $("#visionModel").value = data.models?.vision || $("#visionModel").value;
      if (data.restart_required) setMessage("#modelProbeMessage", "Saved configuration is waiting for a runtime restart.");
    } catch (error) {
      if (error.status !== 404) throw error;
      $("#modelEndpoint").textContent = "Setup mode";
    }
  }

  async function saveModels(event) {
    event.preventDefault();
    const payload = {
      HIVE_LLM_BASE_URL: $("#llmUrl").value.trim(), HIVE_LLM_MODEL_CHEAP: $("#modelCheap").value.trim(),
      HIVE_LLM_MODEL_STRONG: $("#modelStrong").value.trim(), HIVE_LLM_MODEL_LIGHT: $("#modelLight").value.trim(), HIVE_VISION_MODEL: $("#visionModel").value.trim(),
    };
    if ($("#llmKey").value) payload.HIVE_LLM_API_KEY = $("#llmKey").value;
    if ($("#hfToken").value) payload.HF_TOKEN = $("#hfToken").value;
    const button = event.submitter;
    await withLoading("model-save", button, async () => {
      try {
        await api("/api/setup/config", { method: "PUT", body: JSON.stringify(payload) });
        $("#llmKey").value = ""; $("#hfToken").value = "";
        setMessage("#modelSaveMessage", "Assignments saved. Restart the runtime to apply them.", "success");
        await loadModels();
      } catch (error) { setMessage("#modelSaveMessage", error.message, "error"); }
    });
  }

  async function probeModels() {
    await withLoading("model-probe", $("#probeModels"), async () => {
      setMessage("#modelProbeMessage", "Checking the configured endpoint...");
      try { const result = await api("/api/models/probe", { method: "POST", body: "{}" }); setMessage("#modelProbeMessage", result.detail || "Endpoint responded successfully.", "success"); }
      catch (error) { setMessage("#modelProbeMessage", error.message, "error"); }
    });
  }

  async function loadTelegram() {
    try {
      const data = await api("/api/telegram/status");
      const cards = [
        ["Data plane", data.account?.component?.state || (data.account?.configured ? "configured" : "missing"), data.account?.phone || "No account configured"],
        ["Control plane", data.control_bot?.component?.state || (data.control_bot?.configured ? "configured" : "missing"), data.control_bot?.operator_id ? `Operator ${data.control_bot.operator_id}` : "No operator configured"],
        ["Observed chats", String(data.observed_chats || 0), data.running ? "Live from Telegram" : "Agent is stopped"],
      ];
      $("#telegramStatus").innerHTML = cards.map(([label, status, detail]) => `<div class="health-card"><div><span>${escapeHtml(label)}</span><strong title="${escapeHtml(detail)}">${escapeHtml(detail)}</strong></div><span class="status-chip ${chipClass(status)}">${escapeHtml(titleCase(status))}</span></div>`).join("");
    } catch (error) {
      if (error.status !== 404) throw error;
      $("#telegramStatus").innerHTML = emptyState("Setup mode", "Save and authorize both Telegram planes below.");
    }
  }

  async function verifyBot(event) {
    event.preventDefault();
    await withLoading("bot", event.submitter, async () => {
      try {
        const result = await api("/api/setup/bot/verify", { method: "POST", body: JSON.stringify({ token: $("#botToken").value, operator_id: Number($("#operatorId").value) }) });
        $("#botToken").value = "";
        setMessage("#botMessage", `Verified ${result.bot?.username ? `@${result.bot.username}` : "control bot"}.`, "success");
        await Promise.all([loadTelegram(), loadSetup()]);
      } catch (error) { setMessage("#botMessage", error.message, "error"); }
    });
  }

  function setTelegramStage(stage, message = "") {
    state.telegramStage = stage;
    $("#codeField").hidden = stage !== "code";
    $("#passwordField").hidden = stage !== "password";
    $("#cancelTelegramLogin").hidden = stage === "start";
    $("#telegramPrimary").textContent = stage === "start" ? "Send code" : stage === "code" ? "Verify code" : "Verify password";
    ["#apiId", "#apiHash", "#phone", "#passphrase", "#sessionPath"].forEach((selector) => { $(selector).disabled = stage !== "start"; });
    setMessage("#telegramMessage", message);
  }

  async function telegramSubmit(event) {
    event.preventDefault();
    await withLoading("telegram-auth", event.submitter, async () => {
      try {
        let result;
        if (state.telegramStage === "start") result = await api("/api/setup/telethon/start", { method: "POST", body: JSON.stringify({ api_id: Number($("#apiId").value), api_hash: $("#apiHash").value, phone: $("#phone").value, passphrase: $("#passphrase").value, session_path: $("#sessionPath").value }) });
        else if (state.telegramStage === "code") result = await api("/api/setup/telethon/code", { method: "POST", body: JSON.stringify({ attempt_id: state.telegramAttempt, code: $("#loginCode").value }) });
        else result = await api("/api/setup/telethon/password", { method: "POST", body: JSON.stringify({ attempt_id: state.telegramAttempt, password: $("#tgPassword").value }) });
        state.telegramAttempt = result.attempt_id || state.telegramAttempt;
        if (result.state === "password_required") setTelegramStage("password", "Telegram requires the account's 2FA password.");
        else if (result.state === "ready") { setTelegramStage("start", "Telegram account authorized and encrypted."); $("#telegramForm").reset(); $("#sessionPath").value = "./secrets/user.session"; await Promise.all([loadTelegram(), loadSetup()]); }
        else setTelegramStage("code", "A login code was sent to the Telegram account.");
      } catch (error) { setMessage("#telegramMessage", error.message, "error"); }
    });
  }

  async function cancelTelegram() {
    if (state.telegramAttempt) await api("/api/setup/telethon/cancel", { method: "POST", body: JSON.stringify({ attempt_id: state.telegramAttempt }) }).catch(() => {});
    state.telegramAttempt = "";
    setTelegramStage("start", "Login attempt cancelled.");
  }

  async function loadSetup() {
    const data = await api("/api/setup/status");
    $("#readinessList").innerHTML = Object.entries(data.checks || {}).map(([name, ready]) => `<div class="readiness-item"><div class="readiness-copy"><strong>${escapeHtml(readinessLabels[name] || titleCase(name))}</strong><span>${ready ? "Configuration present" : "Action required"}</span></div><span class="status-chip ${ready ? "success" : "error"}">${ready ? "Ready" : "Missing"}</span></div>`).join("");
    setMessage("#readinessMessage", data.ready ? "Required services are ready to start." : "Complete the missing required checks before starting the runtime.", data.ready ? "success" : "");
    if (data.paths?.signing_key) $("#signingPath").value = data.paths.signing_key;
    if (data.bot_username) setMessage("#botMessage", `Saved control bot: @${data.bot_username}`);
  }

  async function createSigningKey(event) {
    event.preventDefault();
    await withLoading("signing", event.submitter, async () => {
      try {
        const result = await api("/api/setup/signing-key", { method: "POST", body: JSON.stringify({ path: $("#signingPath").value }) });
        setMessage("#signingMessage", result.created ? `Keypair created at ${result.path}.` : `A signing key already exists at ${result.path}.`, "success");
        await loadSetup();
      } catch (error) { setMessage("#signingMessage", error.message, "error"); }
    });
  }

  async function savePanelToken(event) {
    event.preventDefault();
    const value = $("#panelToken").value.trim();
    if (!value) return setMessage("#panelTokenMessage", "Enter a non-empty token.", "error");
    try {
      await api("/api/setup/config", { method: "PUT", body: JSON.stringify({ HIVE_PANEL_TOKEN: value }) });
      token = value; sessionStorage.setItem("hive-panel-token", value); $("#panelToken").value = "";
      setMessage("#panelTokenMessage", "Panel token saved and activated for this tab.", "success");
      await loadSetup();
    } catch (error) { setMessage("#panelTokenMessage", error.message, "error"); }
  }

  function renderCommandResults(query = "") {
    const words = query.toLowerCase();
    const matches = routes.filter(([, name, detail]) => `${name} ${detail}`.toLowerCase().includes(words));
    $("#commandResults").innerHTML = matches.length ? matches.map(([route, name, detail]) => `<button class="command-option" type="button" data-command-route="${route}"><strong>${name}</strong><span>${escapeHtml(detail)}</span></button>`).join("") : emptyState("No matching page", "Try a broader term.");
  }

  function bindEvents() {
    $$("[data-route]").forEach((node) => node.addEventListener("click", () => navigate(node.dataset.route)));
    $$("[data-route-link]").forEach((node) => node.addEventListener("click", () => navigate(node.dataset.routeLink)));
    $("#openNav").addEventListener("click", () => $("#sidebar").classList.add("open"));
    $("#closeNav").addEventListener("click", () => $("#sidebar").classList.remove("open"));
    $("#refreshPage").addEventListener("click", () => loadRoute(state.route));
    $("#startAgent").addEventListener("click", () => runtimeAction("start"));
    $("#restartAgent").addEventListener("click", () => runtimeAction("restart"));
    $("#stopAgent").addEventListener("click", async () => { if (await confirmAction("Stop the agent?", "The control panel will remain available. Active sessions must be sealed or explicitly discarded.", "Stop agent")) runtimeAction("stop"); });
    $("#takeoverSearch").addEventListener("input", renderOperations);
    $("#manualTakeover").addEventListener("click", showManualTakeover);
    $("#closeInspector").addEventListener("click", () => $("#sessionInspector").classList.remove("open"));
    $("#sealSession").addEventListener("click", sealSession);
    $("#inspectorPersona").addEventListener("change", async (event) => {
      try { await api(`/api/sessions/${state.selectedPeer}/persona`, { method: "POST", body: JSON.stringify({ persona: event.target.value }) }); toast("Persona updated.", "success"); await openSession(state.selectedPeer); }
      catch (error) { toast(error.message, "error"); }
    });
    $$("[data-inspector-tab]").forEach((tab) => tab.addEventListener("click", () => {
      $$("[data-inspector-tab]").forEach((item) => item.classList.toggle("active", item === tab));
      $$("[data-inspector-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.inspectorPanel === tab.dataset.inspectorTab));
    }));
    $("#intelligenceSession").addEventListener("change", (event) => openSession(Number(event.target.value)));
    $("#activitySearch").addEventListener("input", renderActivityPage);
    $("#activitySeverity").addEventListener("change", renderActivityPage);
    $("#clearActivityView").addEventListener("click", () => { state.activityHiddenBefore = Math.max(0, ...state.activity.map((item) => item.id || 0)); renderActivityPage(); });
    $("#logSearch").addEventListener("input", renderLogs);
    $("#logLevel").addEventListener("change", renderLogs);
    $("#exportLogs").addEventListener("click", exportLogs);
    $("#modelForm").addEventListener("submit", saveModels);
    $("#probeModels").addEventListener("click", probeModels);
    $("#botForm").addEventListener("submit", verifyBot);
    $("#telegramForm").addEventListener("submit", telegramSubmit);
    $("#cancelTelegramLogin").addEventListener("click", cancelTelegram);
    $("#signingForm").addEventListener("submit", createSigningKey);
    $("#panelTokenForm").addEventListener("submit", savePanelToken);
    $("#openCommand").addEventListener("click", () => { renderCommandResults(); $("#commandDialog").showModal(); $("#commandSearch").focus(); });
    $("#commandSearch").addEventListener("input", (event) => renderCommandResults(event.target.value));
    document.addEventListener("click", (event) => {
      const route = event.target.closest("[data-command-route]")?.dataset.commandRoute;
      const takeover = event.target.closest("[data-takeover-peer]")?.dataset.takeoverPeer;
      const peer = event.target.closest("[data-open-peer]")?.dataset.openPeer;
      const download = event.target.closest("[data-download-evidence]")?.dataset.downloadEvidence;
      if (route) navigate(route);
      if (takeover) beginTakeover(Number(takeover));
      if (peer) { if (state.route === "overview") navigate("takeovers"); openSession(Number(peer)); }
      if (download) downloadEvidence(Number(download));
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#openCommand").click(); }
    });
  }

  async function init() {
    $("#overviewDate").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(new Date());
    bindEvents();
    renderCommandResults();
    navigate(state.route);
    loadSetup().catch(() => {});
    setInterval(() => {
      if (document.hidden) return;
      if (state.route === "overview" && !state.setupOnly) loadDashboard().catch(() => {});
      if (["takeovers", "intelligence"].includes(state.route)) loadOperations().catch(() => {});
      if (state.route === "activity") loadActivity().catch(() => {});
      if (state.route === "logs") loadLogs().catch(() => {});
    }, 5000);
  }

  init();
})();
