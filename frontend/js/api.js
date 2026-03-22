/**
 * api.js — thin wrapper around the REST API and WebSocket connection.
 * Configure BASE_URL and API_KEY before use.
 */

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

/**
 * Fetch the latest snapshot of all signals.
 * @returns {Promise<{items: Array, total: number}>}
 */
async function fetchSignals() {
  const resp = await fetch(`${API_BASE}/signals`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /signals → ${resp.status}`);
  return resp.json();
}

/**
 * Fetch active (unacknowledged) alarms.
 * @returns {Promise<{items: Array, total: number}>}
 */
async function fetchActiveAlarms() {
  const resp = await fetch(`${API_BASE}/alarms?acknowledged=false&limit=50`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /alarms → ${resp.status}`);
  return resp.json();
}

/**
 * Acknowledge a specific alarm by ID.
 * @param {number} alarmId
 */
async function acknowledgeAlarm(alarmId) {
  const resp = await fetch(`${API_BASE}/alarms/${alarmId}/acknowledge`, {
    method: "POST",
    headers: _headers(),
  });
  if (!resp.ok) throw new Error(`POST /alarms/${alarmId}/acknowledge → ${resp.status}`);
  return resp.json();
}

/**
 * Open a WebSocket connection to the given topic endpoint.
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

/**
 * Fetch CarPC system metrics (CPU, RAM, disk, queue, heap…).
 * @returns {Promise<Object>}
 */
async function fetchSystemMetrics() {
  const resp = await fetch(`${API_BASE}/system/metrics`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /system/metrics → ${resp.status}`);
  return resp.json();
}

/**
 * Fetch full metadata for all available signals (one-time, heavy).
 * Includes unit, min/max, alarm thresholds, widget type, current value.
 * @returns {Promise<{items: Array, total: number}>}
 */
async function fetchAvailableSignals() {
  const resp = await fetch(`${API_BASE}/signals/available`, { headers: _headers() });
  if (!resp.ok) throw new Error(`GET /signals/available → ${resp.status}`);
  return resp.json();
}

/**
 * Open subscription-based WebSocket. Client can send subscribe/unsubscribe
 * commands to select which channels (signal names, 'alarms', 'metrics') to receive.
 *
 * @param {function(object): void} onMessage — called on each incoming message
 * @param {function(): void} [onOpen] — called when connection is established
 * @returns {{ ws: WebSocket, subscribe: function, unsubscribe: function }}
 */
function openSubscriptionWS(onMessage, onOpen) {
  const url  = `${WS_BASE}/ws/subscribe`;
  const sock = new WebSocket(url);

  sock.addEventListener("message", (evt) => {
    try { onMessage(JSON.parse(evt.data)); }
    catch (e) { console.warn("WS parse error:", e); }
  });

  if (onOpen) {
    sock.addEventListener("open", onOpen);
  }

  /**
   * Send subscribe command.
   * @param {string[]} channels — e.g. ["EngineSpeed", "alarms", "metrics", "*"]
   * @param {"continuous"|"once"} [mode="continuous"]
   */
  function subscribe(channels, mode = "continuous") {
    // Optional third param `opts` can include rate_ms etc.
    const opts = arguments.length > 2 ? arguments[2] : undefined;
    if (sock.readyState === WebSocket.OPEN) {
      const payload = { action: "subscribe", channels, mode };
      if (opts && typeof opts === 'object') Object.assign(payload, opts);
      sock.send(JSON.stringify(payload));
    }
  }

  /**
   * Send unsubscribe command.
   * @param {string[]} channels
   */
  function unsubscribe(channels) {
    if (sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "unsubscribe", channels }));
    }
  }

  return { ws: sock, subscribe, unsubscribe };
}
