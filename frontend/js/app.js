/**
 * app.js — main entry-point: boots the WebSocket connection, polls REST
 * as fallback, and wires up the dashboard.
 */

const badge      = document.getElementById("connection-badge");
const statusText = document.getElementById("status-text");
let reconnectTimer = null;

// Frontend mode: 'dev' => show all signals; 'user' => restrict to whitelist
const FRONTEND_MODE = (window.FRONTEND_MODE || 'dev').toLowerCase();

// Whitelist of signals to show in `user` mode (provided by user request).
const USER_SIGNAL_WHITELIST = [
  "HMI_CrashSeverity",
  "HMI_CrashImpactTrigger",
  "HMI_SILG_ActivationRequest",
  "HB_ActivationSync",
  "Actuator_Manual_Seat_Function_enable",
  "HB_DynamicTargetTemp",
  "HB_ManualTargetTemp",
  "ABL_FL_RetractRequest",
  "ABL_FR_RetractRequest",
  "ABL_RL1_RetractRequest",
  "ABL_RL2_RetractRequest",
  "ACR_FR_RetractRequest",
  "ACR_RL1_RetractRequest",
  "ACR_RL2_RetractRequest",
  "ELK_FR_LockingRequest",
  "ELK_RL1_LockingRequest",
  "ELK_RL2_LockingRequest",
  "ISB_FL_ColorBlue",
  "ISB_FL_ColorRed",
  "ISB_FL_Intensity",
  "ISB_FL_Normalization",
  "ISB_FL_Transitionspeed",
  "ISB_FL_GroupOrModule",
  "ISB_FL_AdressByte0",
  "ISB_FL_AdressByte1",
  "ISB_FR_ColorGreen",
  "ISB_FR_ColorBlue",
  "ISB_FR_ColorRed",
  "ISB_FR_Intensity",
  "ISB_FR_Normalization",
  "ISB_FR_Transitionspeed",
  "ISB_FR_GroupOrModule",
  "ISB_FR_AdressByte0",
  "ISB_FR_AdressByte1",
  "ISB_RL1_ColorGreen",
  "ISB_RL1_ColorBlue",
  "ISB_RL1_ColorRed",
  "ISB_RL1_Intensity",
  "ISB_RL1_Normalization",
  "ISB_RL1_Transitionspeed",
  "ISB_RL1_GroupOrModule",
  "ISB_RL1_AdressByte0",
  "ISB_RL1_AdressByte1",
  "ISB_RL2_ColorGreen",
  "ISB_RL2_ColorBlue",
  "ISB_RL2_ColorRed",
  "ISB_RL2_Intensity",
  "ISB_RL2_Normalization",
  "ISB_RL2_Transitionspeed",
  "ISB_RL2_GroupOrModule",
  "ISB_RL2_AdressByte0",
  "ISB_RL2_AdressByte1",
  "OMS_FL_HandsOnWheel",
  "OMS_FL_OccupantClassification",
  "OMS_FL_OutOfPosition",
  "OMS_FL_OccupantWeight",
  "OMS_FL_OccupantLength",
  "OMS_FL_OccupantGender",
  "OMS_FR_OccupantClassification",
  "OMS_FR_OutOfPosition",
  "OMS_FR_OccupantWeight",
  "OMS_FR_OccupantLength",
  "OMS_FR_OccupantGender",
  "SPS_FL_SeatDirectionX",
  "SPS_FL_SeatDirectionZ",
  "SPS_FL_SeatBackRestPosition",
  "SPS_FL_FootRestPosition",
  "SPS_FL_HeadRestPosition",
  "SPS_FR_SeatDirectionX",
  "SPS_FR_SeatDirectionZ",
  "SPS_FR_SeatBackRestPosition",
  "SPS_FR_FootRestPosition",
  "SPS_FR_HeadRestPosition",
  "ABL_FL_S0SensorStatus",
  "ABL_FL_ActivationLevelStatus",
  "ABL_FL_ActivationPhase",
  "ABL_FR_S0SensorStatus",
  "ABL_FR_ActivationLevelStatus",
  "ABL_FR_ActivationPhase",
  "ABL_RL1_S0SensorStatus",
  "ABL_RL1_ActivationLevelStatus",
  "ABL_RL1_ActivationPhase",
  "ABL_RL2_S0SensorStatus",
  "ABL_RL2_ActivationLevelStatus",
  "ABL_RL2_ActivationPhase",
  "ACR_FL_ActivationPhase",
  "ACR_FL_SpoolFasterClutch",
  "ACR_FR_ActivationLevelStatus",
  "ACR_FR_ActivationPhase",
  "ACR_FR_SpoolFasterClutch",
  "ACR_RL1_ActivationLevelStatus",
  "ACR_RL1_ActivationPhase",
  "ACR_RL1_SpoolFasterClutch",
  "ACR_RL2_ActivationLevelStatus",
  "ACR_RL2_ActivationPhase",
  "ACR_RL2_SpoolFasterClutch",
  "WMS_FL_SpoolAngle",
  "WMS_FL_SensorStatus",
  "WMS_FR_WebbingMovement",
  "WMS_FR_SpoolAngle",
  "WMS_FR_SensorStatus",
  "WMS_RL1_WebbingMovement",
  "WMS_RL1_SpoolAngle",
  "WMS_RL1_SensorStatus",
  "WMS_RL2_WebbingMovement",
  "WMS_RL2_SpoolAngle",
  "WMS_RL2_SensorStatus",
  "ELK_FR_LockingStatus",
  "ELK_RL1_LockingStatus",
  "ELK_RL2_LockingStatus",
  "BSW_FR_BuckleStatus",
  "BSW_RL1_BuckleStatus",
  "BSW_RL2_BuckleStatus",
  "HB_FR_ActivationLevel",
  "HB_RL1_ActivationLevel",
  "HB_RL2_ActivationLevel"
];

function isSignalAllowed(name, std_name) {
  if (FRONTEND_MODE === 'dev') return true;
  return USER_SIGNAL_WHITELIST.includes(name) || USER_SIGNAL_WHITELIST.includes(std_name);
}

// ── Signal tracking for the table + fast gauges ────────────────────────────

const signalUnits = new Map();
const signalHistory = new Map();
const lastUpdateTs = new Map();
const FAST_SIGNAL_THRESHOLD_S = 0.25;
const FAST_SIGNAL_MAX = 4;
const SIGNAL_HISTORY_LEN = 60;

// fast-changing signals feature removed; no container present
const fastSignalsContainer = null;
const signalTableBody = document.getElementById("signal-table-body");

function sanitizeId(name) {
  return name.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function createSignalRow(signalName, unit, writable = false, states = null) {
  const row = document.createElement("tr");
  row.id = `signal-row-${sanitizeId(signalName)}`;

  let writeCell;
  if (!writable) {
    writeCell = `<td class="signal-write signal-write--ro">—</td>`;
  } else if (states && states.length > 0) {
    // Enum signal: render a <select> with named states
    const options = states
      .map((s) => `<option value="${s.value}">${s.value} — ${s.description}</option>`)
      .join("");
    writeCell = `<td class="signal-write">
        <select class="write-select" aria-label="Write value for ${signalName}">
          ${options}
        </select>
        <button class="write-btn btn" data-signal="${signalName}">Set</button>
       </td>`;
  } else {
    // Continuous numeric signal: render a number input
    writeCell = `<td class="signal-write">
        <input class="write-input" type="number" step="any"
               aria-label="Write value for ${signalName}" />
        <button class="write-btn btn" data-signal="${signalName}">Set</button>
       </td>`;
  }

  // Get std_name to display alongside canonical name
  const stdName = getSignalMetadata(signalName)?.std_name;
  const nameDisplay = stdName && stdName !== signalName 
    ? `${signalName}<br/><small class="signal-std-name">${stdName}</small>`
    : signalName;

  row.innerHTML = `
    <td class="signal-name">${nameDisplay}</td>
    <td class="signal-value">— ${unit || ""}</td>
    <td class="signal-history"><canvas class="sparkline" width="140" height="28"></canvas></td>
    ${writeCell}
  `;
  if (writable) {
    const btn = row.querySelector(".write-btn");
    btn.addEventListener("click", () => handleWriteSignal(signalName, row));
    const inp = row.querySelector(".write-input, .write-select");
    if (inp && inp.tagName === "INPUT") {
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleWriteSignal(signalName, row);
      });
    }
  }
  signalTableBody.appendChild(row);
  return row;
}

async function handleWriteSignal(signalName, row) {
  const inp = row.querySelector(".write-input");
  const sel = row.querySelector(".write-select");
  const btn = row.querySelector(".write-btn");
  const raw = sel ? sel.value : (inp ? inp.value.trim() : "");
  if (raw === "") return;
  const value = parseFloat(raw);
  if (isNaN(value)) {
    if (inp) inp.classList.add("write-input--error");
    return;
  }
  if (inp) inp.classList.remove("write-input--error");
  btn.disabled = true;
  btn.textContent = "…";
  try {
    await writeSignal(signalName, value);
    btn.textContent = "✓";
    btn.classList.add("write-btn--ok");
  } catch (e) {
    console.error("writeSignal failed:", e);
    btn.textContent = "✗";
    btn.classList.add("write-btn--err");
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Set";
      btn.classList.remove("write-btn--ok", "write-btn--err");
    }, 1500);
  }
}

function markFastSignal(/* signalName, unit */) {
  // no-op: fast-changing signals layout removed
}

function unmarkFastSignal(/* signalName */) {
  // no-op: fast-changing signals layout removed
}

function updateSignalRow(signalName, value, timestamp = Date.now() / 1000, unit = "", writable = false, states = null) {
  if (unit) signalUnits.set(signalName, unit);

  const nowTs = timestamp;
  const prevTs = lastUpdateTs.get(signalName);
  const delta = prevTs ? nowTs - prevTs : Infinity;
  lastUpdateTs.set(signalName, nowTs);

  const history = signalHistory.get(signalName) || [];
  history.push(value);
  if (history.length > SIGNAL_HISTORY_LEN) history.shift();
  signalHistory.set(signalName, history);

  if (delta < FAST_SIGNAL_THRESHOLD_S) {
    markFastSignal(signalName, unit);
  } else {
    unmarkFastSignal(signalName);
  }

  let row = document.getElementById(`signal-row-${sanitizeId(signalName)}`);
  if (!row) {
    row = createSignalRow(signalName, unit, writable, states);
  }
  const valueEl = row.querySelector(".signal-value");
  if (valueEl) {
    valueEl.textContent = `${value.toFixed(2)} ${unit || ""}`;
  }
  const canvas = row.querySelector("canvas.sparkline");
  if (canvas) {
    drawSparkline(canvas, history);
  }
}

// ── Signal metadata cache (populated once from /signals/available) ──────────

/** @type {Map<string, object>} signal_name → full metadata object */
const signalMetadataCache = new Map();

// ── Initial REST snapshot ──────────────────────────────────────────────────

async function loadSnapshot() {
  // 1. Fetch full metadata (heavy, once)
  try {
    const { items } = await fetchAvailableSignals();
    // Populate std_name → signal_name registry for resolving names
    populateSignalRegistry(items);
    items.forEach((meta) => {
      // Use canonical signal_name as the key for metadata cache
      signalMetadataCache.set(meta.signal_name, meta);
      const unit = meta.unit || "";
      if (unit) signalUnits.set(meta.signal_name, unit);
      if (meta.value != null && isSignalAllowed(meta.signal_name, meta.std_name)) {
        updateWidget(meta.signal_name, meta.value);
        updateSignalRow(meta.signal_name, meta.value, meta.timestamp || 0, unit, !!meta.writable, meta.states || null);
      }
    });
    console.info(`Loaded metadata for ${items.length} signals; std_name registry populated`);
  } catch (e) {
    console.warn("Available signals fetch failed, falling back to /signals:", e);
    // Fallback to legacy snapshot
    try {
      const { items } = await fetchSignals();
      items.forEach(({ signal_name, std_name, value, unit, timestamp }) => {
        if (!isSignalAllowed(signal_name, std_name)) return;
        updateWidget(signal_name, value);
        updateSignalRow(signal_name, value, timestamp, unit || "");
      });
    } catch (e2) {
      console.warn("Snapshot fetch also failed:", e2);
    }
  }
  try {
    const { items } = await fetchActiveAlarms();
    // Only render up to 3 alarms in the UI
    (items || []).slice(0, 3).forEach(renderAlarm);
  } catch (e) {
    console.warn("Alarm fetch failed:", e);
  }
}

// ── WebSocket handler (new subscribe protocol + legacy fallback) ───────────

/** @type {{ ws: WebSocket, subscribe: function, unsubscribe: function } | null} */
let subConn = null;

function connect() {
  clearTimeout(reconnectTimer);

  try {
    subConn = openSubscriptionWS(handleMessage, () => {
      badge.textContent   = "Connected";
      badge.className     = "badge badge--connected";
      statusText.textContent = "Live — subscribe protocol active";

      // Subscribe channels depending on frontend mode.
      if (FRONTEND_MODE === 'dev') {
        subConn.subscribe(["*", "alarms", "metrics"], "continuous");
      } else {
        // subscribe only to whitelist signals plus alarms and metrics
        const channels = USER_SIGNAL_WHITELIST.slice();
        // channels.push('alarms', 'metrics');
        subConn.subscribe(channels, 'continuous');
      }
        // If metrics polling was started earlier, stop it since subscribe will push metrics.
        try {
          if (typeof _metricsPollTimer !== 'undefined' && _metricsPollTimer) {
            clearInterval(_metricsPollTimer);
            _metricsPollTimer = null;
          }
        } catch (e) {}
    });

    subConn.ws.addEventListener("close", () => {
      badge.textContent   = "Disconnected";
      badge.className     = "badge badge--disconnected";
      statusText.textContent = "Reconnecting in 5 s…";
      subConn = null;
      reconnectTimer = setTimeout(connect, 5000);
    });

    subConn.ws.addEventListener("error", (evt) => {
      console.warn("WebSocket error", evt);
      subConn.ws.close();
    });
  } catch (e) {
    console.warn("Subscribe WS failed, falling back to legacy:", e);
    connectLegacy();
  }
}

function connectLegacy() {
  clearTimeout(reconnectTimer);
  const sock = openWebSocket("all", handleMessage);

  sock.addEventListener("open", () => {
    badge.textContent   = "Connected";
    badge.className     = "badge badge--connected";
    statusText.textContent = "Live — receiving data via WebSocket (legacy)";
  });

  sock.addEventListener("close", () => {
    badge.textContent   = "Disconnected";
    badge.className     = "badge badge--disconnected";
    statusText.textContent = "Reconnecting in 5 s…";
    reconnectTimer = setTimeout(connect, 5000);
  });

  sock.addEventListener("error", (evt) => {
    console.warn("WebSocket error", evt);
    sock.close();
  });
}

function handleMessage(msg) {
  // Demo-compatible batch frame: {timestamp: "ISO8601", signals: [{name, value}]}
  if (Array.isArray(msg.signals) && !msg.type) {
    const ts = msg.timestamp ? new Date(msg.timestamp).getTime() / 1000 : Date.now() / 1000;
    msg.signals.forEach(({ name, value, std_name }) => {
      if (!isSignalAllowed(name, std_name)) return;
      const meta = signalMetadataCache.get(name);
      updateWidget(name, value);
      updateSignalRow(name, value, ts, signalUnits.get(name) || "", !!(meta && meta.writable), (meta && meta.states) || null);
    });
    return;
  }
  // Legacy single-signal frame
  if (msg.type === "signal") {
    if (!isSignalAllowed(msg.signal, msg.std_name)) return;
    const meta = signalMetadataCache.get(msg.signal);
    updateWidget(msg.signal, msg.value);
    updateSignalRow(msg.signal, msg.value, msg.timestamp, signalUnits.get(msg.signal) || "", !!(meta && meta.writable), (meta && meta.states) || null);
  } else if (msg.type === "alarm") {
    renderAlarm(msg);
  } else if (msg.type === "metrics") {
    renderMetrics(msg);
  } else if (msg.type === "subscribed") {
    // Demo-compatible ack: {type: "subscribed", signals: [...], count: N}
    console.debug("Subscribed:", msg.signals, "count:", msg.count);
  } else if (msg.type === "subscribe_ack") {
    // Legacy ack format — kept for backward compat
    console.debug("Subscribe ack:", msg);
  } else if (msg.type === "pong") {
    // keepalive response — no action needed
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────

(async () => {
  await loadSnapshot();
  // Update signal panel title based on mode
  try {
    const sigTitle = document.querySelector('#signal-panel h2');
    if (sigTitle) sigTitle.textContent = FRONTEND_MODE === 'dev' ? 'All Signals' : 'User Signals';
  } catch(e) {}
  connect();
  // Metrics polling as fallback — WS subscribe also pushes metrics.
  // Keep REST fallback in case WS doesn't cover metrics yet.
  startMetricsPolling();
})();

// ── CarPC System Metrics Polling ───────────────────────────────────────────

const METRICS_POLL_INTERVAL_MS = 3000;
let _prevNetSent = 0;
let _prevNetRecv = 0;
let _prevNetTs = 0;
let _metricsPollTimer = null;
// Metrics history for sparklines
const METRICS_HISTORY_LEN = 60;
const metricsHistory = new Map();

function pushMetricHistory(key, value) {
  let arr = metricsHistory.get(key) || [];
  arr.push(typeof value === 'number' ? value : 0);
  if (arr.length > METRICS_HISTORY_LEN) arr.shift();
  metricsHistory.set(key, arr);
  return arr;
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes.toFixed(0) + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MB";
  return (bytes / 1073741824).toFixed(2) + " GB";
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function setBarFill(barId, percent) {
  const el = document.getElementById(barId);
  if (!el) return;
  el.style.width = Math.min(percent, 100) + "%";
  el.classList.remove("carpc-bar__fill--warn", "carpc-bar__fill--crit");
  if (percent > 90) el.classList.add("carpc-bar__fill--crit");
  else if (percent > 70) el.classList.add("carpc-bar__fill--warn");
}

function renderMetrics(d) {
  // CPU (system)
  const cpuEl = document.getElementById("carpc-cpu-percent");
  if (cpuEl) cpuEl.textContent = d.cpu_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-cpu-spark'), pushMetricHistory('cpu_percent', d.cpu_percent)); } catch(e){}
  setBarFill("carpc-cpu-bar", d.cpu_percent);
  const cpuDetail = document.getElementById("carpc-cpu-detail");
  if (cpuDetail) cpuDetail.textContent =
    `${d.cpu_count_physical}C/${d.cpu_count_logical}T  ${d.cpu_freq_current_mhz} MHz`;

  // Process CPU
  const procCpu = document.getElementById("carpc-proc-cpu");
  if (procCpu) procCpu.textContent = d.process_cpu_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-proc-cpu-spark'), pushMetricHistory('process_cpu_percent', d.process_cpu_percent)); } catch(e){}
  setBarFill("carpc-proc-cpu-bar", Math.min(d.process_cpu_percent, 100));
  const procDetail = document.getElementById("carpc-proc-detail");
  if (procDetail) procDetail.textContent =
    `PID ${d.process_pid}  Threads: ${d.process_threads}  Files: ${d.process_open_files >= 0 ? d.process_open_files : "N/A"}`;

  // RAM
  const ramEl = document.getElementById("carpc-ram-percent");
  if (ramEl) ramEl.textContent = d.ram_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-ram-spark'), pushMetricHistory('ram_percent', d.ram_percent)); } catch(e){}
  setBarFill("carpc-ram-bar", d.ram_percent);
  const ramDetail = document.getElementById("carpc-ram-detail");
  if (ramDetail) ramDetail.textContent =
    `${d.ram_used_mb.toFixed(0)} / ${d.ram_total_mb.toFixed(0)} MB (avail ${d.ram_available_mb.toFixed(0)} MB)`;

  // Process Memory
  const procMem = document.getElementById("carpc-proc-mem");
  if (procMem) procMem.textContent = d.process_memory_rss_mb.toFixed(1) + " MB";
  try { drawSparkline(document.getElementById('carpc-proc-mem-spark'), pushMetricHistory('process_memory_rss_mb', d.process_memory_rss_mb)); } catch(e){}
  setBarFill("carpc-proc-mem-bar", d.process_memory_percent);
  const procMemDetail = document.getElementById("carpc-proc-mem-detail");
  if (procMemDetail) procMemDetail.textContent =
    `RSS ${d.process_memory_rss_mb.toFixed(1)} MB  VMS ${d.process_memory_vms_mb.toFixed(1)} MB  (${d.process_memory_percent.toFixed(1)}%)`;

  // Disk
  const diskEl = document.getElementById("carpc-disk-percent");
  if (diskEl) diskEl.textContent = d.disk_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-disk-spark'), pushMetricHistory('disk_percent', d.disk_percent)); } catch(e){}
  setBarFill("carpc-disk-bar", d.disk_percent);
  const diskDetail = document.getElementById("carpc-disk-detail");
  if (diskDetail) diskDetail.textContent =
    `${d.disk_used_gb.toFixed(1)} / ${d.disk_total_gb.toFixed(1)} GB (free ${d.disk_free_gb.toFixed(1)} GB)`;

  // Swap
  const swapEl = document.getElementById("carpc-swap-percent");
  if (swapEl) swapEl.textContent = d.swap_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-swap-spark'), pushMetricHistory('swap_percent', d.swap_percent)); } catch(e){}
  setBarFill("carpc-swap-bar", d.swap_percent);
  const swapDetail = document.getElementById("carpc-swap-detail");
  if (swapDetail) swapDetail.textContent =
    `${d.swap_used_mb.toFixed(0)} / ${d.swap_total_mb.toFixed(0)} MB`;

  // Queue
  const queueEl = document.getElementById("carpc-queue-percent");
  if (queueEl) queueEl.textContent = d.queue_usage_percent.toFixed(1) + "%";
  try { drawSparkline(document.getElementById('carpc-queue-spark'), pushMetricHistory('queue_usage_percent', d.queue_usage_percent)); } catch(e){}
  setBarFill("carpc-queue-bar", d.queue_usage_percent);
  const queueDetail = document.getElementById("carpc-queue-detail");
  if (queueDetail) queueDetail.textContent =
    `${d.queue_size} / ${d.queue_maxsize} items`;

  // Heap / GC
  const heapEl = document.getElementById("carpc-heap");
  if (heapEl) heapEl.textContent = d.heap_allocated_mb.toFixed(1) + " MB";
  try { drawSparkline(document.getElementById('carpc-heap-spark'), pushMetricHistory('heap_allocated_mb', d.heap_allocated_mb)); } catch(e){}
  // Use process memory percent for bar approximation
  setBarFill("carpc-heap-bar", d.process_memory_percent);
  const gcDetail = document.getElementById("carpc-gc-detail");
  if (gcDetail) gcDetail.textContent = `GC objects: ${d.gc_objects.toLocaleString()}`;

  // Network I/O (calculate rate)
  const netEl = document.getElementById("carpc-net");
  const netDetail = document.getElementById("carpc-net-detail");
  const now = d.timestamp;
  if (_prevNetTs > 0) {
    const dt = now - _prevNetTs;
    if (dt > 0) {
      const txRate = (d.net_bytes_sent - _prevNetSent) / dt;
      const rxRate = (d.net_bytes_recv - _prevNetRecv) / dt;
      if (netEl) netEl.textContent = `TX ${formatBytes(Math.max(0, txRate))}/s`;
      if (netDetail) netDetail.textContent =
        `RX ${formatBytes(Math.max(0, rxRate))}/s  Total: TX ${formatBytes(d.net_bytes_sent)} / RX ${formatBytes(d.net_bytes_recv)}`;
    }
  } else {
    if (netEl) netEl.textContent = `TX ${formatBytes(d.net_bytes_sent)}`;
    if (netDetail) netDetail.textContent =
      `RX ${formatBytes(d.net_bytes_recv)}  (cumulative since boot)`;
  }
  try { drawSparkline(document.getElementById('carpc-net-spark'), pushMetricHistory('net_tx_rate', _prevNetSent>0 ? (d.net_bytes_sent - _prevNetSent) : 0)); } catch(e){}
  _prevNetSent = d.net_bytes_sent;
  _prevNetRecv = d.net_bytes_recv;
  _prevNetTs = now;

  // Async Tasks
  const taskEl = document.getElementById("carpc-tasks");
  if (taskEl) taskEl.textContent = d.asyncio_tasks;
  try { drawSparkline(document.getElementById('carpc-tasks-spark'), pushMetricHistory('asyncio_tasks', d.asyncio_tasks)); } catch(e){}
  const taskDetail = document.getElementById("carpc-tasks-detail");
  if (taskDetail) taskDetail.textContent = "running asyncio tasks";

  // Uptime
  const uptimeEl = document.getElementById("carpc-uptime");
  if (uptimeEl) uptimeEl.textContent = formatUptime(d.uptime_seconds);
  const uptimeDetail = document.getElementById("carpc-uptime-detail");
  if (uptimeDetail) uptimeDetail.textContent = `${d.uptime_seconds.toFixed(0)}s total`;

  // Platform
  const platEl = document.getElementById("carpc-platform");
  if (platEl) platEl.textContent = d.platform || "—";
  const platDetail = document.getElementById("carpc-platform-detail");
  if (platDetail) platDetail.textContent = `Python ${d.python_version}`;
}

function startMetricsPolling() {
  async function poll() {
    try {
      const data = await fetchSystemMetrics();
      renderMetrics(data);
    } catch (e) {
      console.warn("Metrics poll failed:", e);
    }
  }
  // If subscribe WS is active (subConn), rely on server push and skip polling.
  if (subConn) {
    // already using subscription; no polling needed
    return;
  }
  poll(); // immediate first fetch
  _metricsPollTimer = setInterval(poll, METRICS_POLL_INTERVAL_MS);
}
