/**
 * api.js — REST + WebSocket wrapper cho CAN-HMI backend.
 *
 * Tương thích với demo API: https://car-hmi-api-demo.onrender.com
 *
 * Cấu hình trước khi load (thêm <script> trước file này):
 *   window.API_BASE = "http://192.168.1.100:8000";   // mặc định: cùng origin
 *   window.WS_BASE  = "ws://192.168.1.100:8000";      // mặc định: cùng origin
 *   window.API_KEY  = "your-key";                      // mặc định: không cần key
 *
 * REST endpoints:
 *   GET  /api/info                   → fetchSystemInfo()
 *   GET  /api/profiles               → listProfiles()
 *   GET  /api/profile[?name=x]       → fetchProfile(name?)
 *   POST /api/profile                → createProfile(body)
 *   PUT  /api/profile                → updateProfile(body)   [optimistic lock: section_id]
 *   DELETE /api/profile/{name}       → deleteProfile(name)
 *   GET  /signals                    → fetchSignals()
 *   GET  /signals/available          → fetchAvailableSignals()
 *   PUT  /signals/{name}             → writeSignal(name, value)
 *   POST /signals/batch_update       → batchWriteSignals(writes)
 *   GET  /alarms                     → fetchActiveAlarms()
 *   POST /alarms/{id}/acknowledge    → acknowledgeAlarm(id)
 *   GET  /system/metrics             → fetchSystemMetrics()
 *
 * WebSocket (demo-compatible):
 *   Endpoint:  ws[s]://host/ws/signals
 *   Client → Server:
 *     {"type": "subscribe",   "signals": ["SignalName", "*", "alarms", "metrics"]}
 *     {"type": "unsubscribe", "signals": ["SignalName"]}
 *     {"type": "ping"}
 *   Server → Client (signal frame):
 *     {"timestamp": "2026-05-20T10:00:00.123Z", "signals": [{"name":"...", "value":0, "std_name":"..."}]}
 *   Server → Client (ack):    {"type": "subscribe_ack", "action": "subscribe", "channels": [...], "count": N}
 *   Server → Client (pong):   {"type": "pong"}
 *
 * std_name Support:
 *   - Backend maps signal_name ↔ std_name from config/signal_std_name.json
 *   - Frontend can read/write using either name; backend resolves transparently
 *   - API responses include std_name field when available
 */

// ── Signal Name Registry (std_name support) ─────────────────────────────────
const _signalRegistry = {
  byCanonical: new Map(),  // signal_name → {signal_name, std_name, unit, ...}
  byStdName:   new Map(),  // std_name → signal_name (for fast reverse lookup)
};

/**
 * Resolve a name to canonical signal_name (handles both signal_name and std_name).
 * @param {string} nameOrStdName
 * @returns {string} canonical signal_name
 */
function resolveSignalName(nameOrStdName) {
  // Try lookup by std_name first
  if (_signalRegistry.byStdName.has(nameOrStdName)) {
    return _signalRegistry.byStdName.get(nameOrStdName);
  }
  // Otherwise assume it's already canonical or will be resolved by backend
  return nameOrStdName;
}

/**
 * Get full signal metadata including std_name.
 * @param {string} nameOrStdName
 * @returns {object|null}
 */
function getSignalMetadata(nameOrStdName) {
  const canonical = resolveSignalName(nameOrStdName);
  return _signalRegistry.byCanonical.get(canonical) || null;
}

/**
 * Populate signal registry from available signals (call after fetchAvailableSignals).
 * @param {Array} availableSignalsList — list from fetchAvailableSignals().signals_info
 */
function populateSignalRegistry(availableSignalsList) {
  _signalRegistry.byCanonical.clear();
  _signalRegistry.byStdName.clear();
  
  if (!availableSignalsList) return;
  
  availableSignalsList.forEach(sig => {
    _signalRegistry.byCanonical.set(sig.signal_name, sig);
    if (sig.std_name && sig.std_name !== sig.signal_name) {
      _signalRegistry.byStdName.set(sig.std_name, sig.signal_name);
    }
  });
}

const normalizeHost = (host) => {
  // Browsers may not be able to connect to 0.0.0.0; default to localhost in that case.
  if (!host || host === "0.0.0.0" || host === "[::]") return "localhost";
  return host;
};

const originProtocol = window.location.protocol;
const originHost = normalizeHost(window.location.hostname);
const originPort = window.location.port ? `:${window.location.port}` : "";

const DEFAULT_ORIGIN = `${originProtocol}//${originHost}${originPort}`;
const API_BASE = window.API_BASE || DEFAULT_ORIGIN;
const WS_BASE =
  window.WS_BASE || `${originProtocol === "https:" ? "wss" : "ws"}://${originHost}${originPort}`;
const API_KEY = window.API_KEY || "";

const _headers = () => {
  const h = { "Content-Type": "application/json" };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
};

// ── System ──────────────────────────────────────────────────────────────────────

/**
 * Thông tin tổng quan dự án: tên, phiên bản, uptime, số signal, trạng thái kết nối.
 * @returns {Promise<{name:string, version:string, uptime_seconds:number, signal_count:number, bus_connected:boolean, db_connected:boolean}>}
 */
async function fetchSystemInfo() {
  const resp = await fetch(`${API_BASE}/api/info`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /api/info → ${resp.status}`);
  return resp.json();
}

/**
 * Thông tin tài nguyên CarPC: CPU, RAM, disk, queue, heap.
 * @returns {Promise<Object>}
 */
async function fetchSystemMetrics() {
  const resp = await fetch(`${API_BASE}/system/metrics`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /system/metrics → ${resp.status}`);
  return resp.json();
}

// ── Profiles ───────────────────────────────────────────────────────────────

/**
 * Danh sách tất cả profiles và profile đang active.
 * @returns {Promise<{profiles:Array, total:number, active:string|null}>}
 */
async function listProfiles() {
  const resp = await fetch(`${API_BASE}/api/profiles`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /api/profiles → ${resp.status}`);
  return resp.json();
}

/**
 * Lấy một profile theo tên, hoặc active profile nếu không truyền name.
 * @param {string} [name]
 * @returns {Promise<{name:string, signals:string[], description:string|null, section_id:string}>}
 */
async function fetchProfile(name) {
  const url = name
    ? `${API_BASE}/api/profile?name=${encodeURIComponent(name)}`
    : `${API_BASE}/api/profile`;
  const resp = await fetch(url, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /api/profile → ${resp.status}`);
  return resp.json();
}

/**
 * Tạo profile mới.
 * @param {{name:string, signals:string[], description?:string}} body
 * @returns {Promise<Object>}
 */
async function createProfile(body) {
  const resp = await fetch(`${API_BASE}/api/profile`, {
    method:  "POST",
    headers: _headers(),
    body:    JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST /api/profile → ${resp.status}`);
  return resp.json();
}

/**
 * Cập nhật profile (yêu cầu section_id để tránh xung đột đồng thời).
 * Nếu section_id mismatch → lỗi 409 → gọi fetchProfile() lại rồi thử lại.
 * @param {{name:string, signals:string[], description?:string, section_id:string}} body
 * @returns {Promise<Object>}
 */
async function updateProfile(body) {
  const resp = await fetch(`${API_BASE}/api/profile`, {
    method:  "PUT",
    headers: _headers(),
    body:    JSON.stringify(body),
  });
  if (resp.status === 409) throw new Error("Conflict: reload profile và thử lại (section_id mismatch)");
  if (!resp.ok) throw new Error(`PUT /api/profile → ${resp.status}`);
  return resp.json();
}

/**
 * Xóa profile theo tên.
 * @param {string} name
 */
async function deleteProfile(name) {
  const resp = await fetch(`${API_BASE}/api/profile/${encodeURIComponent(name)}`, {
    method:  "DELETE",
    headers: _headers(),
  });
  if (!resp.ok) throw new Error(`DELETE /api/profile/${name} → ${resp.status}`);
}

// ── Signals ──────────────────────────────────────────────────────────────────

/**
 * Snapshot hiện tại của tất cả giá trị signal.
 * @returns {Promise<{items:Array, total:number}>}
 */
async function fetchSignals() {
  const resp = await fetch(`${API_BASE}/signals`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /signals → ${resp.status}`);
  return resp.json();
}

/**
 * Full metadata của tất cả signals (gọi 1 lần khi khởi động).
 * Gồm: unit, min/max, alarm thresholds, writable, giá trị hiện tại.
 * @returns {Promise<{signals_info:Array, total:number}>}
 */
async function fetchAvailableSignals() {
  const resp = await fetch(`${API_BASE}/signals/available`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /signals/available → ${resp.status}`);
  const data = await resp.json();
  // Backward-compatible normalization during contract transition.
  return {
    ...data,
    signals_info: data.signals_info || data.items || [],
  };
}

/**
 * Ghi giá trị lên một writable signal (queue CAN output). HTTP 202 Accepted.
 * Hỗ trợ cả signal_name và std_name — backend resolve tự động.
 * @param {string} nameOrStdName  — signal_name hoặc std_name
 * @param {number} value
 * @returns {Promise<{signal_name:string, value:number, queued_at:number}>}
 */
async function writeSignal(nameOrStdName, value) {
  const canonical = resolveSignalName(nameOrStdName);
  const resp = await fetch(`${API_BASE}/signals/${encodeURIComponent(canonical)}`, {
    method:  "PUT",
    headers: _headers(),
    body:    JSON.stringify({ value }),
  });
  if (!resp.ok) throw new Error(`PUT /signals/${canonical} → ${resp.status}`);
  return resp.json();
}

/**
 * Ghi nhiều writable signals cùng lúc. HTTP 202 Accepted.
 * Hỗ trợ cả signal_name và std_name trong mỗi item — backend resolve tự động.
 * @param {Array<{signal_name:string, value:number}>} writes  — signal_name/std_name
 * @returns {Promise<{queued:Array, count:number, queued_at:number}>}
 */
async function batchWriteSignals(writes) {
  // Resolve std_name → signal_name cho mỗi item
  const resolved = writes.map(item => ({
    signal_name: resolveSignalName(item.signal_name),
    value: item.value,
  }));
  const resp = await fetch(`${API_BASE}/signals/batch_update`, {
    method:  "POST",
    headers: _headers(),
    body:    JSON.stringify({ signals: resolved }),
  });
  if (!resp.ok) throw new Error(`POST /signals/batch_update → ${resp.status}`);
  return resp.json();
}

// ── Alarms ────────────────────────────────────────────────────────────────────

/**
 * Danh sách alarm chưa được acknowledge.
 * @returns {Promise<{items:Array, total:number}>}
 */
async function fetchActiveAlarms() {
  const resp = await fetch(`${API_BASE}/alarms?acknowledged=false&limit=50`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /alarms → ${resp.status}`);
  return resp.json();
}

/**
 * Acknowledge một alarm theo ID.
 * @param {number} alarmId
 */
async function acknowledgeAlarm(alarmId) {
  const resp = await fetch(`${API_BASE}/alarms/${alarmId}/acknowledge`, {
    method:  "POST",
    headers: _headers(),
  });
  if (!resp.ok) throw new Error(`POST /alarms/${alarmId}/acknowledge → ${resp.status}`);
  return resp.json();
}

// ── WebSocket (legacy topic-based) ───────────────────────────────────────────

/**
 * Mở WebSocket tĩnh đến một topic (không có subscribe control, legacy).
 * @param {"signals"|"alarms"|"all"} topic
 * @param {function(object): void} onMessage
 * @returns {WebSocket}
 */
function openWebSocket(topic, onMessage) {
  const url  = `${WS_BASE}/ws/${topic}`;
  const sock = new WebSocket(url);
  sock.addEventListener("message", (evt) => {
    try { onMessage(JSON.parse(evt.data)); }
    catch (e) { console.warn("WS parse error:", e); }
  });
  return sock;
}

// ── WebSocket (demo-compatible subscribe protocol) ────────────────────────────

/**
 * Mở WebSocket chính đến /ws/signals với subscribe protocol.
 *
 * Demo-compatible:
 *   subscribe(["*"])             → nhận tất cả signals
 *   subscribe(["A", "B"])        → nhận signal A và B
 *   subscribe(["*", "alarms"])   → signals + alarm events
 *   subscribe(["metrics"])       → chỉ metrics
 *   ping()                       → server trả {"type": "pong"}
 *
 * @param {function(object): void} onMessage  — gọi mỗi khi nhận message
 * @param {function(): void}       [onOpen]   — gọi khi kết nối thành công
 * @returns {{ ws:WebSocket, subscribe:function, unsubscribe:function, ping:function }}
 */
function openSubscriptionWS(onMessage, onOpen) {
  const url  = `${WS_BASE}/ws/signals`;
  const sock = new WebSocket(url);

  sock.addEventListener("message", (evt) => {
    try { onMessage(JSON.parse(evt.data)); }
    catch (e) { console.warn("WS parse error:", e); }
  });

  if (onOpen) {
    sock.addEventListener("open", onOpen);
  }

  /**
   * Đăng ký nhận signals/channels.
   * @param {string[]|string} signals  — e.g. ["EngineSpeed", "*", "alarms", "metrics"]
   * @param {"continuous"|"once"} [mode="continuous"]
   * @param {{rate_ms?:number}} [opts]
   */
  function subscribe(signals, mode = "continuous", opts) {
    if (sock.readyState === WebSocket.OPEN) {
      const payload = { type: "subscribe", signals, mode };
      if (opts && typeof opts === "object") Object.assign(payload, opts);
      sock.send(JSON.stringify(payload));
    }
  }

  /**
   * Hủy đăng ký signals.
   * @param {string[]} signals
   */
  function unsubscribe(signals) {
    if (sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type: "unsubscribe", signals }));
    }
  }

  /** Gửi keepalive ping — server trả về {"type": "pong"}. */
  function ping() {
    if (sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type: "ping" }));
    }
  }

  return { ws: sock, subscribe, unsubscribe, ping };
}

// ── Adaptive Restraint ─────────────────────────────────────────────────────────

/**
 * Lấy danh sách các options lọc hệ thống hỗ trợ thích ứng
 */
async function fetchAdaptiveAvailable() {
  const resp = await fetch(`${API_BASE}/adaptive_restraint/available`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /adaptive_restraint/available → ${resp.status}`);
  return resp.json();
}

/**
 * Lấy thống kê và thông tin vẽ biểu đồ Box-plot cho hệ thống thích ứng
 */
async function fetchAdaptiveChartInfo(params) {
  const queryParts = [];
  for (const [key, values] of Object.entries(params)) {
    if (Array.isArray(values)) {
      values.forEach(val => {
        queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`);
      });
    } else if (values !== undefined && values !== null) {
      queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(values)}`);
    }
  }
  const queryString = queryParts.length ? `?${queryParts.join("&")}` : "";
  const resp = await fetch(`${API_BASE}/adaptive_restraint/chart_info${queryString}`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /adaptive_restraint/chart_info → ${resp.status}`);
  return resp.json();
}

// ── Restraint Video Match ──────────────────────────────────────────────────────

/**
 * Find the best-matching restraint video for given crash parameters.
 *
 * @param {object} params
 * @param {number} params.weight          - Occupant weight in kg
 * @param {number} params.height          - Occupant height in cm
 * @param {string} params.crash_severity  - OLC code (OLC16/OLC18/OLC26/OLC33) or velocity (35/40/50/56)
 * @param {string} params.seatbelt_system - Seatbelt system: SLL | CLL | MSLL
 * @param {string} [params.seat]          - Seat: fl (front-left) | fr (front-right)
 * @param {number|null} [params.seat_x_mm] - Seat travel distance in mm (0=front, 113.5=mid, 227=rear).
 *                                           If null, backend reads from live CAN signal SPS_SeatDirectionX.
 * @returns {Promise<object>} API response with matched video info and context
 */
async function fetchRestraintMatch({ weight, height, crash_severity, seatbelt_system, seat = "driver", seat_x_mm = null }) {
  const p = { weight, height, crash_severity, seatbelt_system, seat };
  if (seat_x_mm !== null && seat_x_mm !== undefined) p.seat_x_mm = seat_x_mm;
  const qs = new URLSearchParams(p).toString();
  const resp = await fetch(`/api/restraints/match?${qs}`, { headers: _headers() });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(detail.detail || `GET /api/restraints/match → ${resp.status}`);
  }
  return resp.json();
}

// ── Camera Stream ──────────────────────────────────────────────────────────────

/**
 * URL of the MJPEG camera stream proxied by CarPC. Suitable as an <img> src —
 * many devices can point at this URL simultaneously; CarPC keeps a single
 * upstream connection to the camera and fans it out to all viewers.
 * @returns {string}
 */
function cameraStreamUrl() {
  return `${API_BASE}/api/camera/stream`;
}

/**
 * Current status of the camera stream proxy: upstream connectivity and
 * number of active viewers.
 * @returns {Promise<{enabled:boolean, stream_url:string, connected:boolean, viewer_count:number, last_error:string|null}>}
 */
async function fetchCameraStatus() {
  const resp = await fetch(`${API_BASE}/api/camera/status`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /api/camera/status → ${resp.status}`);
  return resp.json();
}

