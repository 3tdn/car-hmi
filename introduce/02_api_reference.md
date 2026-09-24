# 02 — API Reference and Frontend Integration

Verified against the registered backend routes on 2026-09-19: **54 HTTP operations
(48 operations plus 6 system aliases) and 3 WebSocket endpoints**.

Use the [complete English API reference](../docs/api_reference.md) for all request
parameters, bodies, response formats, exact error messages, and curl examples.
The [OpenAPI snapshot](../docs/api.openapi.json) contains HTTP schemas.
The [frontend integration guide](../docs/frontend_integration.md) covers both the bundled
dashboard and a separately hosted frontend.

## Common contract

Use `X-API-Key` on protected HTTP routers, `X-Profile-Name` for explicit profile scope,
and `X-Client-Id` for client sessions and Dev Mode locks. Browser WebSockets use
`?api_key=...&profile_name=...`. `X-Dev-Mode: true` does not bypass API key authentication.
System controls require both a real configured key and Dev Mode. Public routes include
system GET, adaptive restraint, camera, and restraints/video.

HTTP errors can contain string, object, or validation-array `detail`. Successful responses
can contain `warnings`; batch writes return HTTP 202 even when individual signals fail.
Inspect `errors` and per-seat `applied` results instead of checking HTTP status alone.

The current backend has no alarm REST/config/WebSocket routes and no root `/health` or
`/ready` business routes. Use `/system/health` and `/system/ready` (or their `/api` aliases).
These probes return HTTP 200 even when their JSON body reports degraded health or not-ready.

## Complete HTTP inventory

| Method | API | Purpose (from implementation) |
|---|---|---|
| GET | `/signals` | List latest signal values |
| GET | `/signals/available` | List all available signals with metadata |
| GET | `/signals/{signal_name}` | Get latest value for one signal |
| PUT | `/signals/{signal_name}` | Write value to signal (CAN write) |
| GET | `/signals/{signal_name}/history` | Query signal history from DB |
| POST | `/signals/batch_update` | Write multiple writable signals simultaneously (batch) |
| GET | `/config` | List all signal configurations |
| GET | `/config/signal/{signal_name}` | Get config for one signal |
| PATCH | `/config/signal/{signal_name}` | Update signal config |
| GET | `/config/processor` | Get processor config |
| POST | `/config/processor` | Update processor config |
| GET | `/config/system` | Get system config and field update policy |
| PATCH | `/config/system` | Patch system config without dropping unrelated fields |
| GET | `/config/system/backups` | List fixed-path system config backups |
| POST | `/config/system/backups` | Back up system config |
| DELETE | `/config/system/backups/{backup_id}` | Delete a system config backup |
| POST | `/config/system/backups/{backup_id}/restore` | Restore a system config backup |
| POST | `/config/system/reset` | Reset system config from the fixed project template |
| POST | `/config/system/reload` | Re-apply live fields from the system config file |
| GET | `/config/general` | Get full application config |
| PATCH | `/config/general` | Patch application config (partial) |
| POST | `/config/general/reset` | Reset application config to defaults |
| GET | `/adaptive_restraint/available` | Get all available options for adaptive restraint filters |
| GET | `/adaptive_restraint/chart_info` | Get statistic and chart information for adaptive restraint systems |
| GET | `/system/info` | Get project & system information |
| GET | `/system/health` | Health check |
| GET | `/system/ready` | Readiness probe (for container/systemd) |
| GET | `/system/metrics` | CarPC resource information (CPU, RAM, disk, queue, heap…) |
| POST | `/system/can/retry` | Retry CAN connections |
| POST | `/system/reboot` | Reboot Car-HMI service |
| GET | `/api/restraints/match` | Find best-matching restraint video for crash conditions |
| GET | `/api/restraints/video/{filename}` | Stream a video file from the media directory |
| GET | `/api/camera/stream` | Proxy live MJPEG stream from the vehicle camera |
| GET | `/api/camera/status` | Camera stream proxy status |
| GET | `/api/devmode/catalog` | Dev Mode signal families and selectable states |
| GET | `/api/devmode/status` | Current Dev Mode seat locks |
| POST | `/api/devmode/seats/select` | Select seats for Dev Mode (locks other sections out) |
| POST | `/api/devmode/exit` | Leave Dev Mode and release all seat locks of this section |
| POST | `/api/devmode/signals` | Apply one signal family to several seats at once |
| GET | `/api/info` | Get project & system information |
| GET | `/api/health` | Health check |
| GET | `/api/ready` | Readiness probe (for container/systemd) |
| GET | `/api/metrics` | CarPC resource information (CPU, RAM, disk, queue, heap…) |
| POST | `/api/can/retry` | Retry CAN connections |
| POST | `/api/reboot` | Reboot Car-HMI service |
| GET | `/api/profiles` | List all profiles |
| GET | `/api/profile/sessions` | List client active-profile sessions |
| POST | `/api/profile/heartbeat` | Heartbeat for client profile session |
| POST | `/api/profile/offline` | Mark client profile session offline |
| GET | `/api/profile` | Get profile by name (or active profile) |
| POST | `/api/profile` | Create new profile |
| PUT | `/api/profile` | Update profile (optimistic lock) |
| PUT | `/api/profile/active` | Set active profile |
| DELETE | `/api/profile/{name}` | Delete profile |

## WebSocket formats

The three registered WebSocket endpoints are `/ws/signals`, `/ws/subscribe` (alias),
and `/ws/all` (legacy automatic broadcast). Use the first two for subscription commands:

```json
{"type":"subscribe","signals":["COM_Status_ElkCan","metrics"],"rate_ms":200,"mode":"continuous"}
```

```json
{"type":"subscribe_ack","action":"subscribe","channels":["COM_Status_ElkCan","metrics"],"count":2,"warnings":[]}
```

```json
{"timestamp":"2026-09-17T00:00:00Z","signals":[{"name":"COM_Status_ElkCan","std_name":"COM_Status_ElkCan","value":1}]}
```

Signal frames have no `type` field. Metrics use `type: "metrics"`. Send
`{"type":"ping"}` for a `pong`, and `{"type":"unsubscribe","signals":["COM_Status_ElkCan"]}`
to unsubscribe. `mode: "once"` waits for the next eligible broadcast; fetch initial values
with REST. `/ws/all` does not handle subscription/ping commands. No alarm channel exists.

## Frontend workflow

Load metadata and a REST snapshot before subscribing. Keep one client ID per tab and
pass the selected profile explicitly on HTTP requests and the WebSocket URL. After a
profile change, refresh metadata/snapshot and reconnect the subscription. Inspect warnings
and partial failures. Release Dev Mode locks and mark the session offline on disconnect.

Set both `window.API_BASE` and `window.WS_BASE` before loading the bundled API helper.
Use repeated query parameters for adaptive chart lists. Resolve video URLs against the
backend origin. Open camera streams only while visible and close the HTTP subscription
on leaving the view; the last viewer leaving stops the shared upstream connection.

System settings come from backend field policy. Object patches merge recursively, but
arrays replace completely. Show pending reboot paths and explicitly invoke the authorized
reboot control when a restart is required. See [configuration management](../docs/system_config_management.md).

See the [frontend error and warning catalogue](../docs/api_errors.md) for API, HTTP status, application code, and exact backend message templates.
