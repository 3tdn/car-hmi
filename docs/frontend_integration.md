# Frontend Integration Guide

Verified against the backend and bundled frontend on 2026-09-19. See the [complete API reference](api_reference.md) for all 54 HTTP operations, 3 WebSocket endpoints, exact error messages, formats, and examples. The [OpenAPI snapshot](api.openapi.json) describes HTTP schemas; the reference also explains runtime behavior that OpenAPI does not capture.

## Backend URL and authentication

For the bundled dashboard, set these globals **before loading `frontend/js/api.js`**:

```html
<script>
  window.API_BASE = "http://192.168.2.10:8000";
  window.WS_BASE = "ws://192.168.2.10:8000";
  window.API_KEY = "YOUR_CONFIGURED_API_KEY";
</script>
<script src="js/api.js"></script>
```

Use HTTPS/WSS when the backend is exposed through TLS. `WS_BASE` defaults to the page origin independently of `API_BASE`; changing only `API_BASE` does not redirect WebSockets. The backend serves `frontend/dist` when it exists, otherwise `frontend/`; deploy the matching frontend assets.

For a separately hosted frontend, configure `api.cors_origins` with its exact page origin, including scheme and port. That setting requires a backend restart. CORS controls browser HTTP access; WebSocket authentication still uses the configured key.

Send `X-API-Key` on protected HTTP routes, `X-Profile-Name` for explicit profile scope, and a stable `X-Client-Id` for each browser tab/session. The bundled frontend stores its client ID in session storage under `can_hmi_client_id`. Independent tabs should use different IDs. `X-Dev-Mode: true` bypasses supported profile checks, but does not bypass API key authentication.

System GET, adaptive restraint, camera, and restraints/video routes are public. Authentication on protected routers is disabled for an empty key or the placeholder values `change-me-in-production`, `changeme`, and `default`. System retry/reboot controls always require a real configured key and `X-Dev-Mode: true`.

## HTTP errors and partial success

The backend does not use one universal error envelope. Handle all of these:

| Response | Frontend handling |
|---|---|
| Non-2xx with string `detail` | Display that exact string. |
| Non-2xx with object `detail` | Preserve `code`, `message`, and contextual fields. |
| HTTP 422 with array `detail` | Display validation locations and messages. |
| HTTP 200/202 with `warnings` | Display warnings; permission filtering can return an empty result. |
| Batch HTTP 202 with `errors` | Report each failed signal even though the HTTP request succeeded. |
| Dev Mode response with per-seat `applied` results | Report each seat's result; all-seat failure can return HTTP 409. |
| Network failure | Treat separately from a backend JSON error. |

The bundled `_fetchJson()` exposes `status`, `payload`, `detail`, and `warnings` on its thrown error, but its `message` falls back to method/URL/status when `detail` is a string. Read `error.detail` to display the exact backend text. Render messages as text, for example with `textContent`.

This standalone reference helper handles all three `detail` formats. It is an integration example, not a replacement already installed in the bundled frontend:

```javascript
const apiBase = "http://localhost:8000";
const apiKey = "YOUR_CONFIGURED_API_KEY";
const clientId = crypto.randomUUID();
let profileName = "admin"; // This profile must already exist.

function errorText(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => `${item.loc?.join(".")}: ${item.msg}`).join("; ");
  }
  return detail?.message || payload?.message || fallback;
}

async function request(path, { method = "GET", body, devMode = false, keepalive = false } = {}) {
  const headers = {
    "X-API-Key": apiKey,
    "X-Client-Id": clientId,
    "X-Profile-Name": profileName,
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (devMode) headers["X-Dev-Mode"] = "true";
  const response = await fetch(`${apiBase}${path}`, {
    method, headers, keepalive,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(errorText(payload, `${method} ${path}: ${response.status}`));
    Object.assign(error, { status: response.status, payload, detail: payload?.detail });
    throw error;
  }
  // Callers must inspect payload.warnings/errors and per-seat applied results.
  return payload;
}
```

## Signals: metadata, initial values, and writes

Fetch `GET /signals/available` to discover signal names, states, units, profile scope, and DBC-derived write capability. Fetch `GET /signals` for initial values, then subscribe through WebSocket. Unknown or unavailable values should remain distinguishable from numeric zero.

Use the signal name as the identifier; `std_name` currently equals the signal name. REST timestamps are Unix seconds. WebSocket signal frames use ISO8601 UTC and do not include a `type` field. Display configuration is not authority for CAN transmission: the DBC sender/write rules still apply.

```javascript
const metadata = await request("/signals/available");
const snapshot = await request("/signals");

// Verify this signal is writable in your configured DBC and profile first.
const write = await request("/signals/ABL_FL_RetractRequest", {
  method: "PUT", body: { value: 0 },
});
const batch = await request("/signals/batch_update", {
  method: "POST",
  body: { signals: [{ signal_name: "ABL_FL_RetractRequest", value: 0 }] },
});
console.log(metadata, snapshot, write, batch.errors, batch.warnings);
```

Single and batch writes return HTTP **202** when accepted. Batch payloads use an array of `{signal_name, value}` objects, not a name/value map. Writing one signal also encodes its CAN message siblings according to `writer.use_prevalue_for_unwritten_signal`. A successful API response is not an ECU acknowledgement.

## WebSocket subscription lifecycle

Use `/ws/signals` or its `/ws/subscribe` alias. Browser WebSockets cannot attach the HTTP headers above; pass URL-encoded `api_key` and an explicit `profile_name`. The bundled helper also sends `client_id`, but the current backend WebSocket route does not use that query parameter to resolve the client profile.

```javascript
const wsUrl = new URL("/ws/signals", apiBase);
wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
wsUrl.searchParams.set("api_key", apiKey);
wsUrl.searchParams.set("profile_name", profileName);
const socket = new WebSocket(wsUrl);
socket.addEventListener("open", () => {
  socket.send(JSON.stringify({
    type: "subscribe", signals: ["COM_Status_ElkCan", "metrics"],
    rate_ms: 200, mode: "continuous",
  }));
});
socket.addEventListener("message", event => {
  const frame = JSON.parse(event.data);
  if (Array.isArray(frame.signals)) console.log("Signal values", frame);
  else if (frame.type === "subscribe_ack") console.log("Accepted scope", frame.channels, frame.warnings);
  else if (frame.type === "metrics") console.log("Metrics", frame);
  else if (frame.type === "error") console.error(frame);
});
```

An illustrative ACK is:

```json
{"type":"subscribe_ack","action":"subscribe","channels":["COM_Status_ElkCan","metrics"],"count":2,"warnings":[]}
```

Signal updates have this format:

```json
{"timestamp":"2026-09-17T00:00:00Z","signals":[{"name":"COM_Status_ElkCan","std_name":"COM_Status_ElkCan","value":1}]}
```

Send `{"type":"unsubscribe","signals":["COM_Status_ElkCan"]}` to remove a subscription, or `{"type":"ping"}` to receive a `pong`. `mode: "once"` waits for the next eligible broadcast; it does not provide an immediate snapshot. `"*"` expands to the allowed signals when subscribed, and profile filtering can produce warnings.

On disconnect, mark telemetry stale and reconnect with a delay, then resubscribe. Keep only one active socket, heartbeat timer, and reconnect timer per view/session; cancel all of them on cleanup. Refresh metadata and the REST snapshot after changing profile and reconnect using the selected profile explicitly.

`/ws/all` is a legacy automatic broadcast endpoint. It does not implement subscription or ping commands. The bundled `openWebSocket()` legacy helper omits the API key; it cannot serve as an authenticated fallback without adjustment. Prefer the bundled `openSubscriptionWS()` for new integration.

## Profiles and online sessions

The bundled profile selector and Profile Manager send `X-Dev-Mode: true` when selecting, creating, updating, or deleting profiles and when loading the session list. These frontend operations do not require the current profile to have `full` permission. Direct integrations may use the same Dev Mode header; without it, profile mutations still require `full` permission. API key authentication still applies.

1. Load `/api/profiles` and `/api/profile?name=...`.
2. Select a profile with `PUT /api/profile/active`, body `{"name":"admin"}`, and `X-Client-Id`. Omitting the client ID changes the global active profile.
3. Update local `X-Profile-Name` and WebSocket query scope. In the bundled API helper, use `setProfileName(name)`.
4. Reload metadata/snapshot and reconnect the subscription.
5. Send `POST /api/profile/heartbeat` while online. It has no body and requires `X-Client-Id`; choose an interval below the configured session TTL.
6. On explicit disconnect or page teardown, send `POST /api/profile/offline` with the same client ID. It releases owned Dev Mode locks. A `fetch` with `keepalive: true` preserves custom headers; unload delivery is best effort and TTL remains the fallback. Stop heartbeat timers after disconnect so they do not mark the session online again.

Profile updates require the latest `section_id` from GET to prevent stale edits. PUT replaces the `signals` array. Omitted `exinfo` is retained; omitted/null `description` becomes null in the current implementation. Profiles restrict TX only: a signal needs `write` or `full` permission to be transmitted. All signal values, metadata, history, and RX subscriptions are readable regardless of profile entries, including for an empty profile. RX-only signals do not need to be added. The legacy `read` permission remains accepted but is not required for signal reads. Do not treat a client-side whitelist as backend authorization.

## Dev Mode and seat locks

Use the [Dev Mode guide](devmode_api.md) and `/api/devmode/catalog` for supported signal families and states. Seat keys are `fl`, `fr`, `rl1`, `rl2`, and `rr1`. Catalog signal names are generated from templates and are not filtered to DBC availability. Check live signal metadata rather than assuming every family exists on every seat.

Keep `X-Client-Id` consistent for select, status, write, renew, and release requests. Selection creates time-limited seat locks; renew before expiry, typically halfway through the timeout, and release on leaving Dev Mode. Profile heartbeat tracks online state separately. Ordinary writes to a locked seat can fail with HTTP 423, while batch operations may report lock failures in the response body. Inspect each seat's `applied` result for Dev Mode writes.

## Settings and restart requirements

The bundled Settings frontend sends `X-Dev-Mode: true` for config load/save, backup/list/restore/delete, reset, and live reload. Settings do not require a selected profile or `full` permission. Integrations should send the same Dev Mode header together with `X-API-Key` when authentication is enabled; `X-Client-Id` remains attached by the bundled helper.

Render controls from `GET /config/system` field definitions and reload levels. The policy is in `config/system.fields.json`; runtime values are in `config/system.json`. Do not hard-code editability, display mode, or enum choices in a separate frontend schema.

- Every rendered field shows `EDITABLE: TRUE/FALSE` and `MODE: BASE/EXPAND` badges. `editable: false` also disables the control. The backend rejects a forged PATCH, so disabling the control is only the first layer.
- The `Base` / `Expand` switch controls the settings view. Base mode shows only fields marked `setting_mode: "base"`; Expand mode shows both `base` and `expand` fields and preserves unsaved values while switching views.
- A non-empty `validation.enum` becomes a select control. When the enum belongs to an array item wildcard, the bundled UI uses a multi-select and preserves the JSON value type rather than converting values to strings.
- The CAN interface select is restricted to `socketcan`, `virtual`, `pcan`, `vector`, and `kvaser`; it does not offer a custom `Other` value.
- The CAN channel combobox reads `can_channels.options` from `GET /config/system`. It suggests `auto`, detected SocketCAN interfaces (including DOWN devices), the default UP `virtual/vcan0`, and configured channels while still accepting a manually entered driver-specific channel. Option labels use `can_channels.devices` to show interface, administrative state, and kernel operstate.
- Continue applying the other `validation` constraints to controls where HTML supports them; all constraints are enforced again by the backend.

PATCH merges objects recursively but **replaces arrays completely**. When editing one CAN card, submit the complete intended `can` array. Leave the redacted `api_key` value out of updates. After patch/reset/restore/reload, show `warnings` if present, `reload.live`, `reload.reboot`, `runtime`, and `pending_reboot_paths`/`reboot_required`. Saving a reboot field does not restart the service automatically.

Backup deletion uses `DELETE /config/system/backups/{backup_id}`. The bundled UI requires
a second click within five seconds before it deletes, refreshes only the backup list after
success, and leaves the active/edited configuration unchanged.

Retry and reboot use `/system/can/retry` and `/system/reboot` (also `/api/can/retry`, `/api/reboot`). Both require a real API key and `X-Dev-Mode: true`. Reboot returns HTTP 202 before shutting down; the launcher/supervisor must restart the process. See [system configuration management](system_config_management.md).

## Camera and resource lifecycle

Set an `<img>` source to `${API_BASE}/api/camera/stream` only while the camera view is active. The endpoint returns MJPEG bytes, not JSON. Detach/remove the image source and cancel frontend retry/poll timers on leaving that view so the HTTP subscription closes.

The first viewer starts one upstream connection; all viewers share it. The last viewer leaving stops the upstream connection and its retry loop. Polling `/api/camera/status` alone does not start upstream capture. Status returns `enabled`, `stream_url`, `connected`, `viewer_count`, and `last_error`; no FPS field is returned. A disabled/missing proxy returns HTTP 503.

The current bundled frontend polls camera status every 5 seconds and retries a failed image after 3 seconds. Backend upstream retry uses `camera.reconnect_interval_sec` (currently 3 seconds in the checked configuration); these are separate timers. See [camera documentation](camera_stream.md).

## Adaptive charts and restraints video

Adaptive chart parameters are case-sensitive: `System`, `Age`, `Seatbelt`, `Velocity`, `Weight`, `Height`, `Distance`, and `RawData`. Get valid choices from `/adaptive_restraint/available`; encode list values as repeated query parameters. Set `RawData=false` when raw rows are unnecessary. GET uses query parameters, not a JSON body.

```javascript
const filters = new URLSearchParams({ System: "fusion", Age: "35y", RawData: "false" });
filters.append("Velocity", "40");
filters.append("Velocity", "50");
const chart = await request(`/adaptive_restraint/chart_info?${filters}`);
console.log(chart);
```

For `/api/restraints/match`, send required `weight`, `height`, integer `crash_severity` (35/40/50/56), and `seatbelt_system` (`SLL`, `CLL`, or `MSLL`). The HTTP route does not accept OLC codes. Optional `seat` is `fl`/`fr`, and `seat_x_mm` overrides live seat-position CAN data. Live occupant classification takes priority over weight-derived classification when available.

When `matched` is true, resolve the relative video URL against the **backend origin**, especially with a separately hosted frontend:

```javascript
const match = await request("/api/restraints/match?weight=75&height=175&crash_severity=40&seatbelt_system=SLL");
if (match.matched && match.video?.url) {
  document.querySelector("video").src = new URL(match.video.url, apiBase).href;
}
```

## Health and degraded operation

Poll `/system/health` and `/system/ready` (or `/api/health`, `/api/ready`). These routes return HTTP 200 even when JSON reports degraded/error health or `ready: false`; inspect the body. No root `/health` or `/ready` business route exists.

`No UP SocketCAN interface is available` is caught by CAN recovery; the API remains available in degraded mode. That exact diagnostic is recorded in backend logs/watchdog state, not exposed verbatim as a dedicated frontend CAN diagnostic field. Disable unavailable CAN actions, show stale telemetry clearly, and use readiness details rather than interpreting a reachable HTTP server as a healthy bus. See the [CAN recovery runbook](can_recovery.md).

See the [frontend error and warning catalogue](api_errors.md) for API, HTTP status, application code, and exact backend message templates.
