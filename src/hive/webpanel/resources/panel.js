(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const routes = [
    ["overview", "Overview", "Runtime posture, metrics, and current work"],
    ["takeovers", "Takeovers", "Pending requests and active engagements"],
    ["intelligence", "Case Intelligence", "Pattern profiles, relationships, and grounded findings"],
    ["evidence", "Evidence", "Sealed case bundles"],
    ["evaluation", "Evaluation", "Recorded synthetic conversations and metrics"],
    ["demo", "Demo lab", "Run a synthetic conversation through the live HIVE pipeline"],
    ["activity", "Audit ledger", "Permanent actions, messages, and decisions"],
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
  const LIVE_SESSION_REFRESH_MS = 2000;
  const injectedToken = window.__HIVE_PANEL_TOKEN__ === "__SESSION_TOKEN__" ? "" : window.__HIVE_PANEL_TOKEN__;
  const hashParams = new URLSearchParams(location.hash.slice(1));
  let token = hashParams.get("token") || injectedToken || sessionStorage.getItem("hive-panel-token") || "";
  let tokenRefresh = null;
  if (token) sessionStorage.setItem("hive-panel-token", token);

  async function ensurePanelToken({ force = false } = {}) {
    if (token && !force) return token;
    if (!tokenRefresh) {
      tokenRefresh = (async () => {
        const response = await fetch("/api/panel/session", { cache: "no-store" });
        if (!response.ok) throw new Error("Unable to initialize the panel session.");
        const payload = await response.json();
        const refreshedToken = payload.token || "";
        if (!refreshedToken) throw new Error("The panel session did not provide an authentication token.");
        token = refreshedToken;
        sessionStorage.setItem("hive-panel-token", token);
        return token;
      })();
    }
    try {
      return await tokenRefresh;
    } finally {
      tokenRefresh = null;
    }
  }

  const state = {
    route: localStorage.getItem("hive-route") || "overview",
    dashboard: null,
    sessions: [],
    chats: [],
    history: [],
    selectedPeer: null,
    selectedSession: null,
    selectedHistoryId: null,
    selectedAnalysisId: null,
    analysisRuns: [],
    evaluations: [],
    demoCatalog: null,
    demoRuns: [],
    demoRunId: null,
    demoRefreshing: false,
    activity: [],
    logs: [],
    activityHiddenBefore: 0,
    telegramAttempt: "",
    telegramStage: "start",
    setupOnly: false,
    seenTakeoverRequests: new Set(),
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
  const percentage = (value) => `${Math.round(Number(value || 0) * 100)}%`;
  const chipClass = (value) => {
    const normalized = String(value || "").toLowerCase().replaceAll(" ", "_");
    if (["running", "ready", "success", "healthy", "configured", "signed", "clean"].includes(normalized)) return "success";
    if (["error", "failed", "stopped", "likely_scam", "malicious", "missing", "unsigned"].includes(normalized)) return "error";
    if (["warning", "degraded", "restart_required", "suspicious"].includes(normalized)) return "warning";
    return "info";
  };
  const emptyState = (title, detail) => `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div>`;
  const tableEmpty = (columns, title, detail) => `<tr><td colspan="${columns}">${emptyState(title, detail)}</td></tr>`;

  function openInspectorDialog() {
    const dialog = $("#sessionInspector");
    if (!dialog.open) dialog.showModal();
  }

  function closeInspectorDialog() {
    const dialog = $("#sessionInspector");
    if (dialog.open) dialog.close();
  }

  function authenticatedMediaUrl(message) {
    const path = String(message.media_url || "");
    if (!path.startsWith("/api/media/")) return "";
    return `${path}?token=${encodeURIComponent(token)}`;
  }

  function renderTranscriptMessage(message) {
    const role = message.role === "agent" || message.role === "assistant" ? "agent" : "peer";
    const mediaUrl = authenticatedMediaUrl(message);
    const kind = titleCase(message.media_kind || "file");
    const name = message.media_name || `Telegram ${kind}`;
    const metadata = [kind, Number.isFinite(message.media_size) ? formatBytes(message.media_size) : "", message.media_mime || ""].filter(Boolean).join(" · ");
    let media = "";
    if (mediaUrl && (message.media_kind === "image" || String(message.media_mime || "").startsWith("image/"))) {
      media = `<a class="transcript-media" href="${escapeHtml(mediaUrl)}" target="_blank" rel="noopener" title="Open ${escapeHtml(name)}"><img class="transcript-image" src="${escapeHtml(mediaUrl)}" alt="${escapeHtml(name)}" loading="lazy"></a>`;
    } else if (message.media_kind) {
      const tag = mediaUrl ? "a" : "div";
      const link = mediaUrl ? ` href="${escapeHtml(mediaUrl)}" target="_blank" rel="noopener"` : "";
      const availability = mediaUrl ? metadata : `${metadata}${metadata ? " · " : ""}Preview unavailable`;
      media = `<${tag} class="message-attachment"${link}><span class="attachment-icon">${escapeHtml(String(message.media_kind).slice(0, 3))}</span><span class="attachment-copy"><strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong><small>${escapeHtml(availability)}</small></span></${tag}>`;
    }
    const text = String(message.text || "").trim();
    return `<div class="message ${role}">${media}${text ? `<p>${escapeHtml(text)}</p>` : ""}<span>${escapeHtml(titleCase(message.role))} · ${formatTime(message.ts)}</span></div>`;
  }

  function signalReasonLabel(reason) {
    const [kind, ...parts] = String(reason || "signal").split(":");
    const label = titleCase(parts.join(" ") || kind);
    if (kind === "hvi") return `Indicator · ${label}`;
    if (kind === "soft") return `Behavior · ${label}`;
    if (kind === "sandbox") return `Sandbox · ${label}`;
    return label;
  }

  function resolveSignalMessages(signal, messages) {
    const strangerMessages = (Array.isArray(messages) ? messages : []).filter((message) => message.role === "stranger");
    const reasonsById = new Map();
    const addSource = (rawId, reason = "") => {
      const key = String(rawId);
      if (!reasonsById.has(key)) reasonsById.set(key, new Set());
      if (reason) reasonsById.get(key).add(reason);
    };
    (Array.isArray(signal.source_message_ids) ? signal.source_message_ids : []).forEach((id) => addSource(id));
    (Array.isArray(signal.contributions) ? signal.contributions : []).forEach((item) => {
      if (Number(item.weight || 0) <= 0) return;
      (Array.isArray(item.source_message_ids) ? item.source_message_ids : []).forEach((id) => addSource(id, signalReasonLabel(item.reason)));
    });
    let matches = strangerMessages.filter((message) => reasonsById.has(String(message.msg_id)));
    return matches.map((message) => ({ message, reasons: [...(reasonsById.get(String(message.msg_id)) || [])] }));
  }

  function renderSignalMessage({ message, reasons }) {
    const attachment = message.media_kind ? `[${titleCase(message.media_kind)}${message.media_name ? `: ${message.media_name}` : ""}]` : "";
    const excerpt = String(message.text || "").trim() || attachment || "[Empty message]";
    const messageId = message.msg_id != null ? `Message ${message.msg_id} · ` : "";
    const tags = reasons.length ? `<div class="signal-message-tags">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : "";
    return `<div class="signal-message"><div><strong>Stranger</strong><span>${escapeHtml(messageId)}${formatTime(message.ts)}</span></div><p>${escapeHtml(excerpt)}</p>${tags}</div>`;
  }

  function renderSignal(signal, messages = []) {
    const verdict = titleCase(signal.verdict || signal.name || signal.kind || "Assessment");
    const contributions = (Array.isArray(signal.contributions) ? signal.contributions : []).filter((item) => Number(item.weight || 0) > 0);
    const relatedMessages = resolveSignalMessages(signal, messages);
    const excluded = new Set(["ts", "turn", "score", "instantaneous_score", "verdict", "contributions", "source_message_ids"]);
    const extras = Object.entries(signal).filter(([key, value]) => !excluded.has(key) && value != null).map(([key, value]) => {
      let readable;
      if (Array.isArray(value)) readable = value.map((item) => typeof item === "object" ? Object.values(item).join(" · ") : item).join(", ");
      else if (typeof value === "object") readable = Object.entries(value).map(([label, item]) => `${titleCase(label)}: ${item}`).join(" · ");
      else if (typeof value === "boolean") readable = value ? "Yes" : "No";
      else readable = value;
      return `<div class="signal-detail-row"><span>${escapeHtml(titleCase(key))}</span><strong>${escapeHtml(readable)}</strong></div>`;
    }).join("");
    const contributionPill = (item) => {
      const value = item.value ? ` · ${item.value}` : "";
      return `<span class="signal-pill" title="${escapeHtml(`${item.extractor || "unknown source"}${item.confidence != null ? ` · ${percentage(item.confidence)} confidence` : ""}`)}">${escapeHtml(signalReasonLabel(item.reason))}${escapeHtml(value)} · ${percentage(item.weight)}</span>`;
    };
    const currentContributions = contributions.filter((item) => item.scope !== "carried");
    const carriedContributions = contributions.filter((item) => item.scope === "carried");
    const currentEvidence = currentContributions.length ? `<div class="signal-detail-row"><span>Current assessment</span><div class="signal-contributions">${currentContributions.map(contributionPill).join("")}</div></div>` : `<div class="signal-detail-row"><span>Current assessment</span><strong>No new contributing signals</strong></div>`;
    const carriedEvidence = carriedContributions.length ? `<div class="signal-detail-row"><span>Carried session evidence</span><div class="signal-contributions">${carriedContributions.map(contributionPill).join("")}</div></div>` : "";
    const messageEvidence = relatedMessages.length ? `<div class="signal-detail-row signal-message-row"><span>Related messages</span><div class="signal-messages">${relatedMessages.map(renderSignalMessage).join("")}</div></div>` : `<div class="signal-detail-row"><span>Related messages</span><strong>No message reference was recorded for this assessment</strong></div>`;
    return `<article class="signal-card"><div class="signal-card-header"><strong>${escapeHtml(verdict)}</strong><time>${signal.turn != null ? `Turn ${Number(signal.turn)} · ` : ""}${formatTime(signal.ts)}</time></div><div class="signal-score"><span class="status-chip ${chipClass(signal.verdict)}">Session risk ${percentage(signal.score)}</span><span class="status-chip info">Assessment ${percentage(signal.instantaneous_score)}</span></div><div class="signal-detail-list">${messageEvidence}${currentEvidence}${carriedEvidence}${extras}</div></article>`;
  }

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

  async function api(path, options = {}, retryAuthentication = true) {
    const requestToken = token;
    const headers = new Headers(options.headers || {});
    headers.set("X-HIVE-Token", requestToken);
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401 && retryAuthentication) {
      if (token === requestToken) await ensurePanelToken({ force: true });
      return api(path, options, false);
    }
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
    if (route !== "takeovers") closeInspectorDialog();
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
      if (route === "evaluation") await loadEvaluations();
      if (route === "demo") await loadDemo();
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
    if (!items.length) return emptyState(kind === "chat" ? "Queue is clear" : "No active engagements", kind === "chat" ? "New takeover requests will appear here and in the Telegram bot." : "Begin a takeover from the incoming queue.");
    return items.map((item) => {
      if (kind === "chat") return `<div class="compact-item"><div><strong>${escapeHtml(item.display_name || item.name || item.username || `Peer ${item.peer_id}`)}</strong><span>${escapeHtml(item.latest_text || item.last_message || item.text || "No message preview")}</span></div><time>${formatTime(item.latest_ts || item.last_message_at || item.ts)}</time></div>`;
      return `<button class="compact-item text-button" type="button" data-open-peer="${Number(item.peer_id)}"><div><strong>Peer ${Number(item.peer_id)}</strong><span>${escapeHtml(titleCase(item.verdict))} · ${item.turns} turns</span></div><span>${Math.round(Number(item.score || 0) * 100)}%</span></button>`;
    }).join("");
  }

  function notifyTakeoverRequests(chats) {
    const fresh = chats.filter((chat) => {
      if (chat.request_pending === false) return false;
      const key = `${chat.peer_id}:${chat.request_created_at || chat.latest_ts || chat.last_message_at || 0}`;
      if (state.seenTakeoverRequests.has(key)) return false;
      state.seenTakeoverRequests.add(key);
      return true;
    });
    if (!fresh.length) return;
    const first = fresh[0];
    const account = first.display_name || first.name || first.username || `Peer ${first.peer_id}`;
    toast(fresh.length === 1 ? `New takeover request from ${account}.` : `${fresh.length} new takeover requests.`, "warning");
  }

  function renderActivity(items, compact = false) {
    if (!items.length) return emptyState("No activity yet", "Operator and runtime actions will appear here.");
    return items.map((item) => `<div class="timeline-item"><time>${formatTime(item.ts)}</time><span class="category">${escapeHtml(titleCase(item.category))}</span><div><strong>${escapeHtml(item.title)}</strong>${item.detail ? `<p>${escapeHtml(item.detail)}${item.peer_id ? ` · Peer ${Number(item.peer_id)}` : ""}</p>` : ""}</div></div>`).join("");
  }

  async function loadDashboard() {
    try {
      const data = await api("/api/dashboard");
      notifyTakeoverRequests(data.chats || []);
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
          const confirmed = await confirmAction("Active sessions will be checkpointed", `${error.detail.count} takeover(s) are still active. Force ${action}? They will return paused and require an explicit resume after startup.`, `Force ${action}`);
          if (confirmed) return runtimeAction(action, true);
        }
        setMessage("#runtimeMessage", error.message, "error");
      }
    });
  }

  async function loadOperations() {
    state.history = await api("/api/history");
    try {
      const [chats, sessions] = await Promise.all([api("/api/chats"), api("/api/sessions")]);
      notifyTakeoverRequests(chats);
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
      setMessage("#takeoverMessage", "Start the agent to load takeover requests and active takeovers.");
    }
  }

  async function pollTakeoverRequests() {
    try {
      notifyTakeoverRequests(await api("/api/chats"));
    } catch (error) {
      if (error.status !== 409) throw error;
    }
  }

  function renderOperations() {
    const query = $("#takeoverSearch").value.trim().toLowerCase();
    const chats = state.chats.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    const sessions = state.sessions.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    const history = state.history.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    $("#chatCountLabel").textContent = `${chats.length} pending request${chats.length === 1 ? "" : "s"}`;
    $("#sessionCountLabel").textContent = `${sessions.length} active takeover${sessions.length === 1 ? "" : "s"}`;
    $("#historyCountLabel").textContent = `${history.length} archived takeover${history.length === 1 ? "" : "s"}`;
    $("#chatRows").innerHTML = chats.length ? chats.map((item) => `<tr><td><strong>${escapeHtml(item.display_name || item.name || item.username || `Peer ${item.peer_id}`)}</strong><br><span class="mono">${Number(item.peer_id)}</span></td><td class="ellipsis">${escapeHtml(item.latest_text || item.last_message || item.text || "No preview")}</td><td>${formatDate(item.latest_ts || item.last_message_at || item.ts)}</td><td class="actions"><button class="table-button" type="button" data-takeover-peer="${Number(item.peer_id)}">Take over</button></td></tr>`).join("") : tableEmpty(4, "No takeover requests", "New private messages will appear here and in the Telegram control bot.");
    $("#sessionRows").innerHTML = sessions.length ? sessions.map((item) => { const paused = item.recovery_status === "paused_after_restart"; return `<tr><td class="mono">${Number(item.peer_id)}</td><td>${escapeHtml(personaLabels[item.persona] || titleCase(item.persona))}</td><td><span class="status-chip ${paused ? "warning" : "running"}">${paused ? "Recovery paused" : escapeHtml(titleCase(item.phase))}</span></td><td><span class="status-chip ${chipClass(item.verdict)}">${escapeHtml(titleCase(item.verdict))}</span></td><td>${Math.round(Number(item.score || 0) * 100)}%</td><td class="actions"><button class="table-button" type="button" data-open-peer="${Number(item.peer_id)}">Inspect</button></td></tr>`; }).join("") : tableEmpty(6, "No active takeovers", "Begin a controlled engagement from a takeover request.");
    $("#historyRows").innerHTML = history.length ? history.map((item) => `<tr><td class="mono">${Number(item.peer_id)}</td><td>${formatDate(item.ended_ts)}</td><td><span class="status-chip ${chipClass(item.verdict)}">${escapeHtml(titleCase(item.verdict))}</span></td><td>${Number(item.message_count || 0)}</td><td class="actions"><button class="table-button" type="button" data-history-id="${escapeHtml(item.id)}">View chat</button></td></tr>`).join("") : tableEmpty(5, "No takeover history", "Completed takeovers and their transcripts will appear here.");
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
      state.selectedHistoryId = null;
      state.selectedAnalysisId = null;
      state.analysisRuns = [];
      state.selectedSession = session;
      renderInspector(session, true, false);
      if (state.route === "takeovers") openInspectorDialog();
      if (state.route === "intelligence") renderIntelligence(session);
    } catch (error) { toast(error.message, "error"); }
  }

  async function openHistory(historyId) {
    try {
      const analyses = await api(`/api/history/${encodeURIComponent(historyId)}/analyses`);
      const selected = analyses.find((item) => item.kind === "reanalysis") || analyses[0];
      const session = selected
        ? await api(`/api/history/${encodeURIComponent(historyId)}/analyses/${encodeURIComponent(selected.id)}`)
        : await api(`/api/history/${encodeURIComponent(historyId)}`);
      state.selectedPeer = null;
      state.selectedHistoryId = historyId;
      state.selectedAnalysisId = selected?.id || null;
      state.analysisRuns = analyses;
      state.selectedSession = session;
      renderInspector(session, true, true);
      openInspectorDialog();
    } catch (error) { toast(error.message, "error"); }
  }

  async function openIntelligenceHistory(historyId) {
    try {
      const [analyses, relatedCases] = await Promise.all([
        api(`/api/history/${encodeURIComponent(historyId)}/analyses`),
        api(`/api/history/${encodeURIComponent(historyId)}/related`),
      ]);
      const selected = analyses.find((item) => item.kind === "reanalysis") || analyses[0];
      const session = selected
        ? await api(`/api/history/${encodeURIComponent(historyId)}/analyses/${encodeURIComponent(selected.id)}`)
        : await api(`/api/history/${encodeURIComponent(historyId)}`);
      state.selectedPeer = null;
      state.selectedHistoryId = historyId;
      state.selectedAnalysisId = selected?.id || null;
      state.analysisRuns = analyses;
      state.selectedSession = session;
      state.selectedSession.related_cases = relatedCases;
      renderIntelligence(session, true);
    } catch (error) { toast(error.message, "error"); }
  }

  async function selectIntelligenceAnalysis(runId) {
    if (!state.selectedHistoryId) return;
    try {
      const session = await api(`/api/history/${encodeURIComponent(state.selectedHistoryId)}/analyses/${encodeURIComponent(runId)}`);
      session.related_cases = state.selectedSession?.related_cases || [];
      state.selectedAnalysisId = runId;
      state.selectedSession = session;
      renderIntelligence(session, true);
    } catch (error) { toast(error.message, "error"); }
  }

  async function reanalyzeHistory() {
    if (!state.selectedHistoryId) return;
    const confirmed = await confirmAction("Reanalyse this archived case?", "HIVE will preserve the transcript and original analysis, then add a new versioned intelligence run.", "Start reanalysis");
    if (!confirmed) return;
    const button = $("#reanalyzeHistory");
    const status = $("#reanalysisStatus");
    button.disabled = true;
    status.className = "inline-message";
    status.textContent = "Reanalysis queued…";
    try {
      const job = await api(`/api/history/${encodeURIComponent(state.selectedHistoryId)}/reanalyze`, { method: "POST", body: "{}" });
      for (let attempt = 0; attempt < 600; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const current = await api(`/api/reanalysis/${encodeURIComponent(job.id)}`);
        status.textContent = current.status === "running" ? "Reanalysing transcript…" : "Reanalysis queued…";
        if (current.status === "failed") throw new Error(current.error || "Reanalysis failed.");
        if (current.status === "completed") {
          status.className = "inline-message success";
          status.textContent = "New analysis ready.";
          await openIntelligenceHistory(state.selectedHistoryId);
          if (current.analysis_run_id) await selectIntelligenceAnalysis(current.analysis_run_id);
          return;
        }
      }
      throw new Error("Reanalysis is still running. Check the case again shortly.");
    } catch (error) {
      status.className = "inline-message error";
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function refreshSelectedSession() {
    if (!state.selectedPeer || state.loading.has("session-refresh")) return;
    if (state.route === "takeovers" && !$("#sessionInspector").open) return;
    const peerId = state.selectedPeer;
    state.loading.add("session-refresh");
    try {
      const session = await api(`/api/sessions/${peerId}`);
      if (state.selectedPeer !== peerId) return;
      if (JSON.stringify(session) === JSON.stringify(state.selectedSession)) return;
      state.selectedSession = session;
      renderInspector(session);
      if (state.route === "intelligence") renderIntelligence(session);
    } catch (error) {
      if (![404, 409].includes(error.status)) throw error;
      if (state.selectedPeer !== peerId) return;
      state.selectedPeer = null;
      state.selectedSession = null;
      closeInspectorDialog();
      await loadOperations();
    } finally {
      state.loading.delete("session-refresh");
    }
  }

  function renderInspector(session, initial = false, archived = false) {
    const transcript = $("#inspectorTranscript");
    const followLatest = initial || transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48;
    $("#inspectorContent").hidden = false;
    $("#inspectorPeer").textContent = `PEER ${session.peer_id}`;
    $("#inspectorTitle").textContent = personaLabels[session.persona] || titleCase(session.persona);
    $("#inspectorVerdict").textContent = titleCase(session.verdict);
    $("#inspectorVerdict").className = `status-chip ${chipClass(session.verdict)}`;
    $("#inspectorScore").textContent = `${Math.round(Number(session.score || 0) * 100)}%`;
    $("#inspectorTurns").textContent = `${session.turns} turns · ${formatDuration(session.duration_s)}`;
    $("#inspectorPersona").innerHTML = Object.entries(personaLabels).map(([key, label]) => `<option value="${key}" ${key === session.persona ? "selected" : ""}>${label}</option>`).join("");
    const recoveryPaused = !archived && session.recovery_status === "paused_after_restart";
    $("#inspectorPersona").disabled = archived || recoveryPaused;
    $("#inspectorActions").hidden = archived;
    $(".live-update").classList.toggle("archived", archived);
    $("#inspectorLiveLabel").textContent = archived ? "Archived" : recoveryPaused ? "Paused after restart" : "Live";
    $("#resumeSession").hidden = !recoveryPaused;
    $("#abandonSession").hidden = !recoveryPaused;
    transcript.innerHTML = session.messages?.length ? session.messages.map(renderTranscriptMessage).join("") : emptyState("No transcript yet", "Messages will appear after the engagement begins.");
    if (followLatest) requestAnimationFrame(() => { transcript.scrollTop = transcript.scrollHeight; });
    $("#inspectorSignals").innerHTML = session.signal_trail?.length ? session.signal_trail.map((signal) => renderSignal(signal, session.messages)).join("") : emptyState("No signals yet", "Classifier evidence will appear as messages are assessed.");
    $("#inspectorIndicators").innerHTML = session.hvi_items?.length ? session.hvi_items.map((item) => {
      const source = item.source_msg_id != null ? ` · Message ${item.source_msg_id}` : "";
      const extractor = item.extractor && item.extractor !== "unknown" ? ` · ${titleCase(item.extractor)}` : "";
      return `<div class="indicator-item"><strong>${escapeHtml(titleCase(item.kind))}</strong><p class="mono">${escapeHtml(item.value)}</p><span>${Math.round(Number(item.confidence || 0) * 100)}% confidence${escapeHtml(extractor)}${escapeHtml(source)}</span></div>`;
    }).join("") : emptyState("No indicators extracted", "URLs, wallet addresses, accounts, and phone numbers will appear here.");
    const guidance = session.reporting_guidance;
    $("#inspectorReporting").innerHTML = guidance ? `<div class="signal-guide"><strong>${escapeHtml(guidance.title)}</strong><p>${escapeHtml(guidance.disclaimer)}</p></div>${guidance.steps.map((step, index) => `<article class="indicator-item"><strong>${index + 1}. ${escapeHtml(step.title)}</strong><p>${escapeHtml(step.action)}</p>${step.source_url ? `<a href="${escapeHtml(step.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(step.source_label)}</a>` : ""}</article>`).join("")}<p class="signal-guide">Reviewed ${escapeHtml(guidance.reviewed_date)}. Verify current official instructions before acting.</p>` : emptyState("No reporting guide", "Reporting guidance is unavailable.");
  }

  function renderIntelligenceSessionOptions() {
    const select = $("#intelligenceSession");
    if (!state.sessions.length && !state.history.length) {
      select.innerHTML = "<option>No takeover runs</option>";
      select.disabled = true;
      $("#intelligenceSummary").innerHTML = emptyState("No run selected", "Start and seal a takeover to retain its intelligence findings.");
      $("#hviRows").innerHTML = tableEmpty(4, "No indicators", "No takeover run is available.");
      $("#sandboxList").innerHTML = emptyState("No sandbox results", "No takeover run is available.");
      $("#mediaAnalysisList").innerHTML = emptyState("No media findings", "No takeover run is available.");
      $("#relatedCasesList").innerHTML = emptyState("No related cases", "No takeover run is available.");
      $("#relationshipGraph").innerHTML = "";
      $("#patternProfile").innerHTML = emptyState("No pattern profile", "Start and seal a takeover to build a privacy-reduced scam pattern.");
      $("#patternProfileStatus").className = "status-chip neutral";
      $("#patternProfileStatus").textContent = "Awaiting case";
      $("#intelligenceAnalysis").innerHTML = "<option>No analysis runs</option>";
      $("#intelligenceAnalysis").disabled = true;
      $("#reanalyzeHistory").hidden = true;
      return;
    }
    select.disabled = false;
    const activeOptions = state.sessions.map((item) => `<option value="active:${Number(item.peer_id)}">Peer ${Number(item.peer_id)} · ${escapeHtml(titleCase(item.verdict))}</option>`).join("");
    const historyOptions = state.history.map((item) => `<option value="history:${escapeHtml(item.id)}">${escapeHtml(formatDate(item.ended_ts))} · Peer ${Number(item.peer_id)} · ${escapeHtml(titleCase(item.verdict))}</option>`).join("");
    select.innerHTML = [
      activeOptions ? `<optgroup label="Active sessions">${activeOptions}</optgroup>` : "",
      historyOptions ? `<optgroup label="Previous runs">${historyOptions}</optgroup>` : "",
    ].join("");

    const preferred = state.selectedPeer != null
      ? `active:${state.selectedPeer}`
      : state.selectedHistoryId
        ? `history:${state.selectedHistoryId}`
        : "";
    if (preferred && [...select.options].some((option) => option.value === preferred)) select.value = preferred;

    const [kind, identifier] = select.value.split(":", 2);
    if (kind === "active") {
      const peer = Number(identifier);
      if (state.selectedPeer !== peer || !state.selectedSession) openSession(peer);
    } else if (kind === "history" && (state.selectedHistoryId !== identifier || !state.selectedSession)) {
      openIntelligenceHistory(identifier);
    }
  }

  function renderIntelligence(session, archived = false) {
    $("#intelligenceSession").value = archived ? `history:${state.selectedHistoryId}` : `active:${session.peer_id}`;
    const analysisSelect = $("#intelligenceAnalysis");
    const reanalyzeButton = $("#reanalyzeHistory");
    if (archived) {
      const latestReanalysis = state.analysisRuns.find((item) => item.kind === "reanalysis")?.id;
      analysisSelect.innerHTML = state.analysisRuns.map((item) => {
        const prefix = item.kind === "original" ? "Original" : item.id === latestReanalysis ? "Latest reanalysis" : "Reanalysis";
        return `<option value="${escapeHtml(item.id)}">${escapeHtml(prefix)} · ${escapeHtml(formatDate(item.created_ts))}</option>`;
      }).join("") || "<option>Legacy analysis</option>";
      analysisSelect.disabled = !state.analysisRuns.length;
      if (state.selectedAnalysisId) analysisSelect.value = state.selectedAnalysisId;
      reanalyzeButton.hidden = false;
    } else {
      analysisSelect.innerHTML = "<option>Live analysis</option>";
      analysisSelect.disabled = true;
      reanalyzeButton.hidden = true;
      $("#reanalysisStatus").textContent = "";
    }
    const selectedAnalysis = session.selected_analysis;
    const runLabel = archived
      ? selectedAnalysis
        ? `${selectedAnalysis.kind === "original" ? "Original" : "Reanalysis"} · Schema v${selectedAnalysis.schema_version}`
        : `Archived · ${formatDate(session.ended_ts)}`
      : "Active · Live updating";
    $("#intelligenceSummary").innerHTML = [
      ["Peer", session.peer_id],
      ["Verdict", titleCase(session.verdict)],
      ["Confidence", `${Math.round(Number(session.score || 0) * 100)}%`],
      ["Duration", formatDuration(session.duration_s)],
      ["Run", runLabel],
    ].map(([label, value]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    $("#hviRows").innerHTML = session.hvi_items?.length ? session.hvi_items.map((item) => `<tr><td>${escapeHtml(titleCase(item.kind))}</td><td class="mono">${escapeHtml(item.value)}</td><td>${Math.round(Number(item.confidence || 0) * 100)}%</td><td>${item.source_msg_id != null ? `Message ${Number(item.source_msg_id)}` : archived ? "Archived transcript" : "Active transcript"}</td></tr>`).join("") : tableEmpty(4, "No high-value indicators", archived ? "No indicators were retained for this run." : "The extraction pipeline has not found a supported value.");
    $("#sandboxList").innerHTML = session.sandbox_results?.length ? session.sandbox_results.map(renderSandboxResult).join("") : emptyState("No sandbox analysis", "A sandbox run starts when a URL or bare domain is found in an incoming message.");
    $("#mediaAnalysisList").innerHTML = session.media_analysis?.length ? session.media_analysis.map(renderMediaAnalysis).join("") : emptyState("No media intelligence", "Captured images are checked locally for QR codes and text before optional vision analysis.");
    renderPatternProfile(session);
    $("#relatedCasesList").innerHTML = session.related_cases?.length ? session.related_cases.map((item) => renderRelatedCase(item, session.scam_vector)).join("") : emptyState("No related cases", "No verified identifier overlap or sufficiently similar historical pattern was found.");
    $("#relationshipGraph").innerHTML = renderRelationshipGraph(session.related_cases || [], session.peer_id);
  }

  function renderPatternTags(values, fallback) {
    return values?.length
      ? `<div class="pattern-tags">${values.map((value) => `<span>${escapeHtml(titleCase(value))}</span>`).join("")}</div>`
      : `<span class="pattern-empty">${escapeHtml(fallback)}</span>`;
  }

  function renderPatternProfile(session) {
    const vector = session.scam_vector || {};
    const metadata = session.case_intelligence || {};
    const hasProfile = ["method_labels", "indicator_kinds", "payment_channels", "sandbox_traits"].some((key) => vector[key]?.length) || Boolean(vector.redacted_script);
    const status = $("#patternProfileStatus");
    if (!hasProfile) {
      status.className = "status-chip neutral";
      status.textContent = "Insufficient signals";
      $("#patternProfile").innerHTML = emptyState("No scam pattern yet", "The profile will develop as grounded tactics and technical findings are observed.");
      return;
    }
    if (!metadata.semantic_matching) {
      status.className = "status-chip neutral";
      status.textContent = "Exact links only";
    } else if (metadata.similarity_eligible) {
      status.className = "status-chip ready";
      status.textContent = "Similarity enabled";
    } else {
      status.className = "status-chip neutral";
      status.textContent = "Not similarity eligible";
    }
    const modelName = String(metadata.embedding_model || "").split("/").pop();
    const meta = [
      `Schema v${Number(vector.schema_version || 1)}`,
      metadata.semantic_matching ? `Threshold ${Math.round(Number(metadata.similarity_threshold || 0) * 100)}%` : "Semantic matching disabled",
      modelName || "No embedding model",
      "Identifiers redacted",
    ];
    $("#patternProfile").innerHTML = `<div class="pattern-field pattern-field-wide"><span>Observed tactics</span>${renderPatternTags(vector.method_labels, "No supported tactic classified")}</div><div class="pattern-facts"><div class="pattern-field"><span>Identifier types</span>${renderPatternTags(vector.indicator_kinds, "None retained")}</div><div class="pattern-field"><span>Payment channels</span>${renderPatternTags(vector.payment_channels, "None observed")}</div><div class="pattern-field pattern-field-wide"><span>Sandbox behaviour</span>${renderPatternTags(vector.sandbox_traits, "No sandbox traits observed")}</div></div><div class="pattern-field pattern-field-wide"><span>Redacted conversation pattern</span>${vector.redacted_script ? `<pre class="pattern-script">${escapeHtml(vector.redacted_script)}</pre>` : `<span class="pattern-empty">No stranger-authored pattern retained</span>`}</div><div class="pattern-meta">${meta.map((item) => `<span title="${escapeHtml(metadata.embedding_model || item)}">${escapeHtml(item)}</span>`).join("")}</div><p class="pattern-disclaimer">This profile supports retrieval and comparison. It does not prove that two cases involve the same actor.</p>`;
  }

  function renderRelationshipGraph(items, peerId) {
    if (!items.length) return "";
    const rows = items.slice(0, 8);
    const width = 720;
    const height = 300;
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.34;
    const positioned = rows.map((item, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / rows.length;
      return { item, x: cx + Math.cos(angle) * radius * 1.75, y: cy + Math.sin(angle) * radius };
    });
    const edges = positioned.map(({ item, x, y }) => `<line class="relationship-edge ${item.relationship === "shared_identifier" ? "exact" : "candidate"}" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}"><title>${escapeHtml(item.relationship === "shared_identifier" ? "Verified shared identifier" : "Candidate script similarity")} · ${Math.round(Number(item.score || 0) * 100)}%</title></line>`).join("");
    const nodes = positioned.map(({ item, x, y }) => {
      const historyId = item.related_history_id || item.related_case_id;
      return `<g class="relationship-node related" transform="translate(${x} ${y})" data-intelligence-history-id="${escapeHtml(historyId)}"><circle r="31"></circle><text y="-2">Peer</text><text y="13">${escapeHtml(item.peer_id ?? "?")}</text><title>Open related case</title></g>`;
    }).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Case relationship graph"><g>${edges}</g><g class="relationship-node current" transform="translate(${cx} ${cy})"><circle r="38"></circle><text y="-2">Current</text><text y="14">Peer ${escapeHtml(peerId)}</text></g>${nodes}</svg>`;
  }

  function comparePatternProfiles(current = {}, related = {}) {
    const fields = [
      ["method_keys", "Shared tactics"],
      ["indicator_kinds", "Shared indicator types"],
      ["payment_channels", "Shared payment channels"],
      ["sandbox_traits", "Shared sandbox behaviour"],
    ];
    return fields.flatMap(([key, label]) => {
      const relatedValues = new Set(related[key] || []);
      const values = [...new Set(current[key] || [])].filter((value) => relatedValues.has(value));
      return values.length ? [{ label, values }] : [];
    });
  }

  function renderRelatedCase(item, currentVector = {}) {
    const exact = item.relationship === "shared_identifier";
    const label = exact ? "Verified identifier link" : "Candidate similarity";
    const evidenceReasons = (item.reasons || []).filter((reason) => reason.kind !== "semantic_similarity").map((reason) => {
      const value = exact && reason.value ? ` · ${reason.value}` : "";
      return `<span>${escapeHtml(titleCase(reason.kind))}${escapeHtml(value)}</span>`;
    });
    const relatedVector = item.pattern_profile || {};
    const overlaps = comparePatternProfiles(currentVector, relatedVector);
    const overlapReasons = overlaps.map((overlap) => `<span title="${escapeHtml(overlap.label)}">${escapeHtml(overlap.label)} · ${escapeHtml(overlap.values.map(titleCase).join(", "))}</span>`);
    const reasons = [...evidenceReasons, ...overlapReasons].join("") || `<span>Similar redacted conversation pattern</span>`;
    const methods = (item.methods || []).map((method) => method.label || titleCase(method.key)).join(", ");
    const historyId = item.related_history_id || item.related_case_id;
    const comparison = `<details class="pattern-comparison"><summary>Compare pattern profiles</summary><div class="comparison-grid"><div><span>Current case</span>${renderPatternTags(currentVector.method_labels, "No classified tactics")}</div><div><span>Related case</span>${renderPatternTags(relatedVector.method_labels, "No classified tactics")}</div></div><div class="comparison-facts"><span>Related indicator types</span>${renderPatternTags(relatedVector.indicator_kinds, "None retained")}</div><p>${exact ? "This relationship is supported by exact retained evidence." : "This is a retrieval candidate based on the redacted pattern; it is not an attribution."}</p></details>`;
    return `<div class="related-case"><div class="surface-heading"><div><strong>Peer ${escapeHtml(item.peer_id ?? "unknown")}</strong><p>${escapeHtml(methods || "No method label retained")}</p></div><span class="status-chip ${exact ? "likely_scam" : "info"}">${escapeHtml(label)}</span></div><div class="related-case-reasons">${reasons}</div><div class="signal-detail-row"><span>${exact ? "Relationship confidence" : "Pattern similarity"}</span><strong>${Math.round(Number(item.score || 0) * 100)}%</strong></div>${item.semantic_score != null ? `<div class="signal-detail-row"><span>Pattern similarity</span><strong>${Math.round(Number(item.semantic_score) * 100)}%</strong></div>` : ""}${comparison}<button class="table-button" type="button" data-intelligence-history-id="${escapeHtml(historyId)}">Open related case</button></div>`;
  }

  function renderMediaAnalysis(item) {
    const source = titleCase(item.source || "unknown");
    const message = item.source_msg_id != null ? `Message ${Number(item.source_msg_id)}` : "Unknown message";
    const indicators = item.indicator_count == null ? "" : `${Number(item.indicator_count)} indicator${Number(item.indicator_count) === 1 ? "" : "s"}`;
    return `<div class="sandbox-item"><div class="surface-heading"><div><strong>${escapeHtml(message)}</strong><p>${escapeHtml(item.description || "No readable content was found.")}</p></div><span class="status-chip neutral">${escapeHtml(source)}</span></div><div class="signal-details">${indicators ? `<div class="signal-detail-row"><span>Extracted</span><strong>${escapeHtml(indicators)}</strong></div>` : ""}${item.media_sha256 ? `<div class="signal-detail-row"><span>Media SHA-256</span><strong class="hash">${escapeHtml(item.media_sha256)}</strong></div>` : ""}</div></div>`;
  }

  function renderSandboxResult(item) {
    const signal = item.verdict_signal || (item.error ? "error" : "unknown");
    const facts = [
      ["Access", item.access_state ? titleCase(item.access_state) : ""],
      ["Fetcher", item.fetcher],
      ["HTTP status", item.http_status || ""],
      ["Challenge", item.challenge_detected ? titleCase(item.challenge_provider || "Detected") : ""],
      ["Final URL", item.final_url],
      ["Destination IP", item.dest_ip],
      ["Page title", item.title],
      ["Redirects", item.redirect_chain?.length ? String(item.redirect_chain.length) : "0"],
      ["Password field", item.has_password_field == null ? "" : (item.has_password_field ? "Detected" : "Not detected")],
      ["Cloaking", item.cloaking_suspected ? "Suspected" : "Not detected"],
      ["Blocked private requests", item.blocked_requests?.length ? String(item.blocked_requests.length) : "0"],
      ["Screenshot warning", item.screenshot_error],
      ["Error", item.error],
    ].filter(([, value]) => value !== "" && value != null);
    return `<div class="sandbox-item"><div class="surface-heading"><div><strong>${escapeHtml(item.url || item.target || "Sandbox run")}</strong><p>${escapeHtml(item.final_url || item.error || "Analysis completed")}</p></div><span class="status-chip ${chipClass(signal)}">${escapeHtml(titleCase(signal))}</span></div><div class="signal-details">${facts.map(([label, value]) => `<div class="signal-detail-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div></div>`;
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
        closeInspectorDialog();
        await loadOperations();
        navigate("evidence");
      } catch (error) { toast(error.message, "error"); }
    });
  }

  async function resumeSession() {
    if (!state.selectedPeer) return;
    const confirmed = await confirmAction("Resume this interrupted takeover?", "Any messages queued while HIVE was offline or paused will be analysed, and HIVE may send a response as the account user.", "Resume takeover");
    if (!confirmed) return;
    await withLoading("resume-recovery", $("#resumeSession"), async () => {
      try {
        const result = await api(`/api/sessions/${state.selectedPeer}/resume`, { method: "POST", body: "{}" });
        toast(`Takeover resumed. ${Number(result.processed_messages || 0)} queued message(s) processed.`, "success");
        await loadOperations();
        await openSession(state.selectedPeer);
      } catch (error) { toast(error.message, "error"); }
    });
  }

  async function abandonSession() {
    if (!state.selectedPeer) return;
    const peerId = state.selectedPeer;
    const confirmed = await confirmAction("Abandon this checkpoint?", "The unfinished session will be removed without an evidence bundle. The operator action remains in the audit ledger.", "Abandon checkpoint");
    if (!confirmed) return;
    await withLoading("abandon-recovery", $("#abandonSession"), async () => {
      try {
        await api(`/api/sessions/${peerId}/abandon`, { method: "POST", body: "{}" });
        toast(`Interrupted takeover ${peerId} abandoned.`, "warning");
        state.selectedPeer = null;
        state.selectedSession = null;
        closeInspectorDialog();
        await loadOperations();
      } catch (error) { toast(error.message, "error"); }
    });
  }

  async function loadEvidence() {
    const rows = await api("/api/evidence");
    $("#evidenceCount").textContent = `${rows.length} sealed bundle${rows.length === 1 ? "" : "s"}`;
    $("#evidenceRows").innerHTML = rows.length ? rows.map((item) => `<tr><td class="mono">${Number(item.peer_id)}</td><td>${formatDate(item.created_ts)}</td><td>${formatBytes(item.size)}</td><td><div class="hash" title="${escapeHtml(item.sha256)}">${escapeHtml(item.sha256)}</div></td><td><span class="status-chip ${item.signature_present && item.package_present ? "success" : "error"}">${item.signature_present && item.package_present ? "Packaged" : item.signature_present ? "PDF only" : "Unsigned"}</span></td><td class="actions"><button class="table-button" type="button" data-download-evidence="${escapeHtml(item.download_url)}" data-evidence-filename="${escapeHtml(item.filename)}">PDF</button>${item.package_present ? `<button class="table-button" type="button" data-download-evidence="${escapeHtml(item.package_download_url)}" data-evidence-filename="${escapeHtml(item.package_filename)}">Evidence ZIP</button>` : ""}</td></tr>`).join("") : tableEmpty(6, "No evidence bundles", "Stop and seal a takeover to create the first case file.");
  }

  async function loadEvaluations() {
    const rows = await api("/api/evaluations");
    state.evaluations = rows;
    const verified = rows.filter((item) => item.evidence_verified && item.chain_valid).length;
    $("#evaluationCount").textContent = `${rows.length} recorded run${rows.length === 1 ? "" : "s"} · ${verified} chain and evidence verified`;
    $("#evaluationRows").innerHTML = rows.length ? rows.map((item) => `<tr><td><strong>${escapeHtml(titleCase(item.scenario || item.archetype))}</strong><br><span class="mono">${escapeHtml(item.run_group)}</span></td><td>${escapeHtml(personaLabels[item.persona] || titleCase(item.persona))}<br><span>${escapeHtml(item.language || "Unspecified")}</span></td><td><span class="status-chip ${chipClass(item.verdict)}">${escapeHtml(titleCase(item.verdict))}</span></td><td>${percentage(item.verdict_score)}</td><td>${Number(item.exchanges || item.turns || 0)}</td><td>${item.f1 == null ? "Needs annotation" : percentage(item.f1)}</td><td><span class="status-chip ${item.evidence_verified && item.chain_valid ? "success" : "error"}">${item.evidence_verified && item.chain_valid ? "Verified" : "Check failed"}</span></td><td class="actions"><button class="table-button" type="button" data-open-evaluation="${escapeHtml(item.id)}">Inspect</button></td></tr>`).join("") : tableEmpty(8, "No evaluation runs", "Run task evaluate:redteam to record synthetic conversations.");
  }

  async function openEvaluation(runId) {
    try {
      const run = await api(`/api/evaluations/${encodeURIComponent(runId)}`);
      $("#evaluationInspectorTitle").textContent = titleCase(run.scenario || run.archetype);
      $("#evaluationInspectorMeta").textContent = `${personaLabels[run.persona] || titleCase(run.persona)} · ${run.language || "Unspecified"} · ${run.run_group}`;
      $("#evaluationSummary").innerHTML = [
        ["Verdict", `${titleCase(run.verdict)} · ${percentage(run.verdict_score)}`],
        ["Exchanges", run.exchanges || run.turns || 0],
        ["Duration", formatDuration(Math.round(Number(run.duration_s || 0)))],
        ["Extraction F1", run.extraction?.f1 == null ? "Needs annotation" : percentage(run.extraction.f1)],
        ["Model tiers", (run.agent_tiers || []).map(titleCase).join(", ") || "None"],
      ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      const transcript = (run.transcript || []).map(([speaker, text]) => ({ role: speaker === "victim" ? "agent" : "stranger", text, ts: null }));
      $("#evaluationTranscript").innerHTML = transcript.length ? transcript.map(renderTranscriptMessage).join("") : emptyState("No transcript", "This run did not retain conversation messages.");
      $("#evaluationIndicators").innerHTML = (run.hvi_items || []).length ? run.hvi_items.map((item) => `<div class="indicator-item"><strong>${escapeHtml(titleCase(item.kind))}</strong><p class="mono">${escapeHtml(item.value)}</p><span>${percentage(item.confidence)} · Message ${Number(item.source_msg_id)}</span></div>`).join("") : emptyState("No indicators", "No indicators were retained in this run.");
      const checks = [
        ["Expected verdict", run.verdict_correct],
        ["Hash chain", run.chain_valid],
        ["Evidence package", run.evidence_verified],
        ["Guardrail flags", Number(run.guardrail_flags || 0)],
        ["Bot probes", Number(run.bot_probes || 0)],
        ["Explicit detection", run.bot_detected],
        ["Sandbox runs", Number(run.sandbox_runs || 0)],
      ];
      $("#evaluationChecks").innerHTML = checks.map(([label, value]) => `<div class="definition-row"><span>${escapeHtml(label)}</span><strong>${typeof value === "boolean" ? value ? "Pass / yes" : "No" : escapeHtml(value)}</strong></div>`).join("");
      $("#evaluationActions").innerHTML = run.package_available ? `<button class="button secondary" type="button" data-download-evidence="${escapeHtml(run.package_download_url)}" data-evidence-filename="${escapeHtml(run.package_filename)}">Download verified evidence ZIP</button>` : "";
      const dialog = $("#evaluationInspector");
      if (!dialog.open) dialog.showModal();
    } catch (error) { toast(error.message, "error"); }
  }

  const activeDemoStatuses = new Set(["running", "paused", "processing", "awaiting_input", "sealing", "stopping"]);

  function renderDemoScenarioCopy() {
    const key = $("#demoScenario").value;
    const scenario = state.demoCatalog?.scenarios?.find((item) => item.key === key);
    const modeKey = $("#demoMode").value || "scripted";
    const mode = state.demoCatalog?.modes?.find((item) => item.key === modeKey);
    $("#demoScenarioCopy").innerHTML = scenario
      ? `<strong>${escapeHtml(mode?.label || titleCase(modeKey))}${mode?.recommended ? " · Recommended" : ""}</strong>${escapeHtml(mode?.description || "Controlled synthetic conversation.")}<br><br><strong>${escapeHtml(scenario.title)}</strong>${escapeHtml(scenario.description)}<br>${escapeHtml(scenario.language)} · ${modeKey === "interactive" ? "presenter-controlled exchanges" : `${Number(scenario.exchanges)} planned exchanges`}`
      : "Select a controlled scenario.";
    if (modeKey === "interactive") {
      $("#demoSpeed").value = "normal";
      $("#demoSpeed").disabled = true;
    } else if (!state.demoRuns.some((item) => activeDemoStatuses.has(item.status))) {
      $("#demoSpeed").disabled = false;
    }
  }

  function renderDemo(run = null) {
    const status = run?.status || "idle";
    const active = activeDemoStatuses.has(status);
    const paused = status === "paused";
    const mode = run?.mode || "scripted";
    $("#demoRunId").textContent = run?.id || "No demo selected";
    $("#demoStatus").textContent = titleCase(status);
    $("#demoStatus").className = `status-chip ${status === "cancelled" || status === "interrupted" ? "warning" : chipClass(status)}`;
    $("#demoStage").textContent = run?.stage || "Choose a scenario to begin.";
    $("#demoProgress").max = Number(run?.total_exchanges || Math.max(3, Number(run?.current_exchange || 0) + 1));
    $("#demoProgress").value = Number(run?.current_exchange || 0);
    $("#demoLiveLabel").textContent = active ? paused ? "Paused" : "Live" : run ? "Recorded" : "Waiting";

    const metrics = run ? [
      ["Mode", state.demoCatalog?.modes?.find((item) => item.key === mode)?.label || titleCase(mode)],
      ["Verdict", `${titleCase(run.verdict)} · ${percentage(run.verdict_score)}`],
      ["Progress", mode === "interactive" ? `${Number(run.current_exchange || 0)} presenter exchange(s)` : `${Number(run.current_exchange || 0)} / ${Number(run.total_exchanges || 0)} exchanges`],
      ["Messages", `${(run.messages || []).length} separate bubbles`],
      ["Indicators", (run.hvi_items || []).length],
      ["Sandbox", `${(run.sandbox_results || []).length} deterministic run(s)`],
      ["Model tiers", (run.tiers || []).map(titleCase).join(", ") || "Waiting"],
      ["Audit chain", run.audit?.valid === false ? "Check failed" : run.audit?.events ? `${run.audit.events} events · verified` : "Recording"],
      ["Telegram", "Disconnected"],
    ] : [["Mode", "Synthetic / isolated"], ["Telegram", "Disconnected"]];
    $("#demoMetrics").innerHTML = metrics.map(([label, value]) => `<div class="definition-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");

    const transcript = $("#demoTranscript");
    const follow = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
    transcript.innerHTML = (run?.messages || []).length
      ? run.messages.map(renderTranscriptMessage).join("")
      : emptyState("No demo messages yet", mode === "interactive" ? "Send the presenter’s first scammer message below." : "Start a scenario to watch each scammer and HIVE message appear separately.");
    if (follow) transcript.scrollTop = transcript.scrollHeight;
    $("#demoIndicators").innerHTML = (run?.hvi_items || []).length
      ? run.hvi_items.map((item) => `<div class="indicator-item"><strong>${escapeHtml(titleCase(item.kind))}</strong><p class="mono">${escapeHtml(item.value)}</p><span>${percentage(item.confidence)} · Message ${Number(item.source_msg_id)}</span></div>`).join("")
      : emptyState("No indicators yet", "Findings will appear after each analysed exchange.");
    $("#demoTimeline").innerHTML = renderActivity(run?.timeline || []);

    $("#pauseDemo").disabled = mode === "interactive" || !active || paused || ["sealing", "stopping"].includes(status);
    $("#resumeDemo").disabled = mode === "interactive" || !paused;
    $("#advanceDemo").disabled = mode === "interactive" || !active || run?.speed !== "step" || ["sealing", "stopping"].includes(status);
    $("#stopDemo").disabled = !active || status === "stopping";
    $("#stopDemo").textContent = mode === "interactive" ? "End and seal" : "Stop";
    const anyActive = state.demoRuns.some((item) => activeDemoStatuses.has(item.status));
    $("#startDemo").disabled = anyActive;
    ["#demoMode", "#demoScenario", "#demoPersona", "#demoSpeed"].forEach((selector) => { $(selector).disabled = anyActive || (selector === "#demoSpeed" && $("#demoMode").value === "interactive"); });
    const composer = $("#interactiveDemoForm");
    composer.hidden = mode !== "interactive" || !active;
    $("#interactiveDemoMessage").disabled = status !== "awaiting_input";
    $("#sendInteractiveDemo").disabled = status !== "awaiting_input";
    $("#demoConversationCopy").textContent = mode === "interactive" ? "The presenter sends one scammer bubble at a time; HIVE responds through the live pipeline." : mode === "model_driven" ? "The fixed opener is followed by dynamically generated scammer bubbles." : "Fixed scammer bursts and HIVE replies are shown as separate mobile-chat messages.";
    const download = $("#downloadDemoEvidence");
    download.hidden = !run?.evidence_available;
    download.dataset.downloadEvidence = run?.evidence_download_url || "";
    download.dataset.evidenceFilename = run?.evidence_filename || "";
    download.textContent = run?.evidence_verified ? "Download verified demo evidence" : "Download demo evidence";
    setMessage("#demoMessage", run?.error || "", run?.error ? "error" : "");
  }

  function renderDemoHistory() {
    const rows = state.demoRuns;
    $("#demoHistoryCount").textContent = `${rows.length} isolated demo run${rows.length === 1 ? "" : "s"}`;
    $("#demoHistoryRows").innerHTML = rows.length ? rows.map((run) => `<tr><td><strong class="mono">${escapeHtml(run.id)}</strong><br><span>${formatDate(run.created_ts)}</span></td><td>${escapeHtml(state.demoCatalog?.modes?.find((item) => item.key === (run.mode || "scripted"))?.label || titleCase(run.mode || "scripted"))}</td><td>${escapeHtml(run.scenario?.title || titleCase(run.scenario?.key))}<br><span>${escapeHtml(personaLabels[run.persona] || titleCase(run.persona))}</span></td><td><span class="status-chip ${run.status === "cancelled" || run.status === "interrupted" ? "warning" : chipClass(run.status)}">${escapeHtml(titleCase(run.status))}</span></td><td>${escapeHtml(titleCase(run.verdict))}<br><span>${percentage(run.verdict_score)}</span></td><td>${(run.messages || []).length}</td><td><span class="status-chip ${run.evidence_verified ? "success" : run.evidence_available ? "warning" : "info"}">${run.evidence_verified ? "Verified" : run.evidence_available ? "Created" : "None"}</span></td><td class="actions"><button class="table-button" type="button" data-open-demo="${escapeHtml(run.id)}">Open</button></td></tr>`).join("") : tableEmpty(8, "No demo runs", "Start a scenario to create the first isolated run.");
  }

  async function loadDemo() {
    if (state.demoRefreshing) return;
    state.demoRefreshing = true;
    try {
      const [catalog, runs] = await Promise.all([api("/api/demo/scenarios"), api("/api/demo/runs")]);
      state.demoCatalog = catalog;
      state.demoRuns = runs;
      if (!$("#demoScenario").options.length) {
        $("#demoMode").innerHTML = catalog.modes.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}${item.recommended ? " (recommended)" : ""}</option>`).join("");
        $("#demoScenario").innerHTML = catalog.scenarios.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.title)}</option>`).join("");
        $("#demoSpeed").innerHTML = catalog.speeds.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`).join("");
      }
      renderDemoScenarioCopy();
      const selected = runs.find((item) => item.id === state.demoRunId);
      const active = runs.find((item) => activeDemoStatuses.has(item.status));
      const run = selected || active || runs[0] || null;
      if (run) state.demoRunId = run.id;
      renderDemo(run);
      renderDemoHistory();
    } finally { state.demoRefreshing = false; }
  }

  async function startDemo(event) {
    event.preventDefault();
    await withLoading("demo-start", $("#startDemo"), async () => {
      try {
        const run = await api("/api/demo/runs", { method: "POST", body: JSON.stringify({ mode: $("#demoMode").value, scenario: $("#demoScenario").value, persona: $("#demoPersona").value, speed: $("#demoSpeed").value }) });
        state.demoRunId = run.id;
        toast("Synthetic live demo started.", "success");
        await loadDemo();
      } catch (error) { setMessage("#demoMessage", error.message, "error"); }
    });
  }

  async function controlDemo(action) {
    if (!state.demoRunId) return;
    try {
      const run = await api(`/api/demo/runs/${encodeURIComponent(state.demoRunId)}/${action}`, { method: "POST", body: "{}" });
      const index = state.demoRuns.findIndex((item) => item.id === run.id);
      if (index >= 0) state.demoRuns[index] = run;
      renderDemo(run);
      await loadDemo();
    } catch (error) { toast(error.message, "error"); }
  }

  async function sendInteractiveDemo(event) {
    event.preventDefault();
    if (!state.demoRunId) return;
    const input = $("#interactiveDemoMessage");
    const text = input.value.trim();
    if (!text) return;
    await withLoading("demo-message", $("#sendInteractiveDemo"), async () => {
      try {
        await api(`/api/demo/runs/${encodeURIComponent(state.demoRunId)}/messages`, { method: "POST", body: JSON.stringify({ text }) });
        input.value = "";
        await loadDemo();
      } catch (error) { toast(error.message, "error"); }
    });
  }

  async function downloadEvidence(downloadUrl, filename) {
    try {
      const response = await fetch(downloadUrl, { headers: { "X-HIVE-Token": token } });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename || "hive-evidence.pdf";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(error.message, "error"); }
  }

  async function loadActivity() {
    const scope = $("#activityScope").value;
    const data = await api(`/api/activity?limit=300&scope=${encodeURIComponent(scope)}`);
    state.activity = data.items || [];
    renderActivityPage();
  }

  function renderActivityPage() {
    const query = $("#activitySearch").value.trim().toLowerCase();
    const severity = $("#activitySeverity").value;
    const items = state.activity.filter((item) => item.id > state.activityHiddenBefore && (severity === "all" || item.severity === severity) && JSON.stringify(item).toLowerCase().includes(query));
    $("#activityTimeline").innerHTML = items.length
      ? renderActivity(items)
      : emptyState($("#activityScope").value === "important" ? "No important activity" : "No audit events", "Adjust the filters or wait for new runtime activity.");
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
        ["Control plane", data.control_bot?.component?.state || (data.control_bot?.configured ? "configured" : "missing"), data.control_bot?.operator_name || (data.control_bot?.operator_id ? `Operator ${data.control_bot.operator_id}` : "No operator configured")],
        ["Takeover requests", String(data.observed_chats || 0), data.running ? "Live from Telegram" : "Agent is stopped"],
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
        const result = await api("/api/setup/bot/verify", { method: "POST", body: JSON.stringify({ token: $("#botToken").value, operator_id: Number($("#operatorId").value), operator_name: $("#operatorName").value.trim() }) });
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
    $("#stopAgent").addEventListener("click", async () => { if (await confirmAction("Stop the agent?", "The control panel will remain available. Active sessions must be sealed first, or force-stopped into restart-safe paused recovery.", "Stop agent")) runtimeAction("stop"); });
    $("#takeoverSearch").addEventListener("input", renderOperations);
    $("#manualTakeover").addEventListener("click", showManualTakeover);
    $("#closeInspector").addEventListener("click", closeInspectorDialog);
    $("#sessionInspector").addEventListener("close", () => {
      if (state.route === "takeovers") {
        state.selectedPeer = null;
        state.selectedHistoryId = null;
        state.selectedSession = null;
      }
    });
    $("#sessionInspector").addEventListener("click", (event) => {
      if (event.target === $("#sessionInspector")) closeInspectorDialog();
    });
    $("#sealSession").addEventListener("click", sealSession);
    $("#resumeSession").addEventListener("click", resumeSession);
    $("#abandonSession").addEventListener("click", abandonSession);
    $("#inspectorPersona").addEventListener("change", async (event) => {
      try { await api(`/api/sessions/${state.selectedPeer}/persona`, { method: "POST", body: JSON.stringify({ persona: event.target.value }) }); toast("Persona updated.", "success"); await openSession(state.selectedPeer); }
      catch (error) { toast(error.message, "error"); }
    });
    $$("[data-inspector-tab]").forEach((tab) => tab.addEventListener("click", () => {
      $$("[data-inspector-tab]").forEach((item) => item.classList.toggle("active", item === tab));
      $$("[data-inspector-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.inspectorPanel === tab.dataset.inspectorTab));
    }));
    $("#intelligenceSession").addEventListener("change", (event) => {
      const [kind, identifier] = event.target.value.split(":", 2);
      if (kind === "active") openSession(Number(identifier));
      if (kind === "history") openIntelligenceHistory(identifier);
    });
    $("#intelligenceAnalysis").addEventListener("change", (event) => selectIntelligenceAnalysis(event.target.value));
    $("#reanalyzeHistory").addEventListener("click", reanalyzeHistory);
    $("#closeEvaluationInspector").addEventListener("click", () => $("#evaluationInspector").close());
    $("#demoForm").addEventListener("submit", startDemo);
    $("#demoMode").addEventListener("change", renderDemoScenarioCopy);
    $("#demoScenario").addEventListener("change", renderDemoScenarioCopy);
    $("#interactiveDemoForm").addEventListener("submit", sendInteractiveDemo);
    $("#pauseDemo").addEventListener("click", () => controlDemo("pause"));
    $("#resumeDemo").addEventListener("click", () => controlDemo("resume"));
    $("#advanceDemo").addEventListener("click", () => controlDemo("advance"));
    $("#stopDemo").addEventListener("click", async () => {
      const run = state.demoRuns.find((item) => item.id === state.demoRunId);
      const interactive = (run?.mode || "scripted") === "interactive";
      const confirmed = interactive
        ? await confirmAction("End this interactive demo?", "HIVE will seal the conversation and create its evidence package.", "End and seal")
        : await confirmAction("Stop this synthetic demo?", "The current run will stop after any in-progress model call and seal its partial evidence when possible.", "Stop demo");
      if (confirmed) controlDemo(interactive ? "finish" : "stop");
    });
    $("#activitySearch").addEventListener("input", renderActivityPage);
    $("#activityScope").addEventListener("change", () => loadActivity().catch((error) => toast(error.message, "error")));
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
      const historyId = event.target.closest("[data-history-id]")?.dataset.historyId;
      const intelligenceHistoryId = event.target.closest("[data-intelligence-history-id]")?.dataset.intelligenceHistoryId;
      const downloadButton = event.target.closest("[data-download-evidence]");
      const evaluationId = event.target.closest("[data-open-evaluation]")?.dataset.openEvaluation;
      const demoId = event.target.closest("[data-open-demo]")?.dataset.openDemo;
      if (route) navigate(route);
      if (takeover) beginTakeover(Number(takeover));
      if (peer) { if (state.route === "overview") navigate("takeovers"); openSession(Number(peer)); }
      if (historyId) openHistory(historyId);
      if (intelligenceHistoryId) { navigate("intelligence"); openIntelligenceHistory(intelligenceHistoryId); }
      if (downloadButton) downloadEvidence(downloadButton.dataset.downloadEvidence, downloadButton.dataset.evidenceFilename);
      if (evaluationId) openEvaluation(evaluationId);
      if (demoId) { state.demoRunId = demoId; renderDemo(state.demoRuns.find((item) => item.id === demoId)); }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#openCommand").click(); }
    });
  }

  async function init() {
    await ensurePanelToken();
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
      if (!["overview", "takeovers", "intelligence"].includes(state.route)) pollTakeoverRequests().catch(() => {});
    }, 5000);
    setInterval(() => {
      if (document.hidden || !["takeovers", "intelligence"].includes(state.route)) return;
      refreshSelectedSession().catch(() => {});
    }, LIVE_SESSION_REFRESH_MS);
    setInterval(() => {
      if (document.hidden || state.route !== "demo") return;
      loadDemo().catch((error) => toast(error.message, "error"));
    }, 900);
  }

  init();
})();
