# Frontend Error and Warning Catalogue

Verified from the current source on 2026-09-17. This catalogue complements the
[API reference](api_reference.md) and [frontend integration guide](frontend_integration.md).

Literal text below preserves the backend wording. `{expression}` denotes a value inserted
by the source at runtime, not text sent literally to the frontend. Rows containing `str(exc)`,
`message`, `errors`, or a structured dictionary carry dynamic content; the following sections
identify its providers. There is no application error code on plain-string `detail` responses.
HTTP validation messages and underlying CAN/filesystem/database errors can vary by exception
and installed library version, so no fixed string is invented for those cases.

## Route errors

Protected HTTP routers additionally return HTTP 401 with `detail: "Invalid or missing API key"`
when authentication is enabled. Schema validation returns HTTP 422 with an array of
`loc`, `msg`, and `type` entries. Routes can also fail with an unhandled server exception;
those generic HTTP 500 responses are not a stable application JSON error contract.

| API / scope | HTTP / delivery | Code / error | Exact message or source template |
|---|---|---|---|
| GET /signals/{signal_name} | 404 | — | `Signal '{signal_name}' not found` |
| PUT /signals/{signal_name} | 503 | — | `CAN writer not available` |
| PUT /signals/{signal_name} | 423 | devmode_seat_locked | See lock message below |
| PUT /signals/{signal_name} | 403 | — | `str(exc)` |
| PUT /signals/{signal_name} | 503 | — | `message` |
| PUT /signals/{signal_name} | 503 | — | `str(exc)` |
| PUT /signals/{signal_name} | 404 | — | `message` |
| POST /signals/batch_update | 503 | — | `CAN writer not available` |
| POST /signals/batch_update | 503 | — | `errors` |
| POST /signals/batch_update | 403 | — | `errors` |
| POST /signals/batch_update | 404 | — | `errors` |
| GET /config/signal/{signal_name} | 404 | signal_config_not_found | `Signal '{signal_name}' not found` |
| PATCH /config/signal/{signal_name} | 404 | signal_config_not_found | `Signal '{signal_name}' not found` |
| Config mutation routes | 422 / 404 / 500 | See configuration providers below | Manager error message |
| Protected profile-scoped routes (profile context) | 403 | profile_not_selected | `No profile selected for this operation` |
| Protected profile-scoped routes (profile context) | 404 | profile_not_found | `Profile '{target}' not found` |
| Protected profile-scoped routes (permission check) | 403 | profile_permission_denied | `Profile '{resolved_name}' lacks '{required}' permission` |
| Protected profile-scoped routes (permission check) | 403 | profile_signal_denied | `Signal '{signal_name}' is outside profile '{resolved_name}' scope` |
| POST /api/profile/heartbeat | 400 | client_id_required | `Header 'X-Client-Id' is required for heartbeat` |
| POST /api/profile/offline | 400 | client_id_required | `Header 'X-Client-Id' is required for offline update` |
| GET /api/profile | 404 | profile_not_selected | `No active profile` |
| GET /api/profile | 404 | profile_not_found | `Profile '{target}' not found` |
| PUT /api/profile/active | 404 | profile_not_found | `Profile '{target}' not found` |
| POST /api/profile | 409 | profile_already_exists | `Profile '{body.name}' already exists` |
| PUT /api/profile | 404 | profile_not_found | `Profile '{body.name}' not found` |
| PUT /api/profile | 409 | profile_section_mismatch | `section_id mismatch — please GET the profile again and retry` |
| DELETE /api/profile/{name} | 404 | profile_not_found | `Profile '{name}' not found` |
| POST /system/can/retry and /system/reboot (also /api aliases) | 503 | — | `System controls require a configured non-placeholder API key` |
| POST /system/can/retry and /system/reboot (also /api aliases) | 401 | — | `Invalid or missing API key` |
| POST /system/can/retry and /system/reboot (also /api aliases) | 403 | — | `Dev Mode is required for this operation` |
| POST /system/can/retry; POST /api/can/retry | 503 | — | `CAN runner unavailable` |
| POST /system/reboot; POST /api/reboot | 503 | — | `CAN runner unavailable` |
| POST /system/reboot; POST /api/reboot | 409 | — | `Reboot already in progress` |
| GET /api/devmode/status; POST seats/select, signals, exit under /api/devmode | 400 | — | `Header 'X-Client-Id' is required for Dev Mode lock operations` |
| POST /api/devmode/seats/select and /api/devmode/signals | 422 | — | `Unknown seat '{raw_seat}'. Valid seats: ['fl', 'fr', 'rl1', 'rl2', 'rr1']` |
| POST /api/devmode/signals | 422 | — | `ISB_Color must be an integer RGB value between 0 and 16777215` |
| POST /api/devmode/seats/select | 409 | — | `{'applied': applied, 'expires_at': None}` |
| POST /api/devmode/signals | 422 | — | `Unsupported signal '{body.signal_name}'. Supported: ['ABL_RetractRequest', 'ACR_RetractRequest', 'HB_Request', 'ISB_Color']` |
| POST /api/devmode/signals | 422 | — | `No seat selected` |
| POST /api/devmode/signals | 503 | — | `CAN writer not available` |
| POST /api/devmode/signals | 409 | — | `{'applied': applied, 'expires_at': None}` |
| POST /api/devmode/signals | 503 | — | `str(exc)` |
| GET /api/camera/stream and /api/camera/status | 503 | — | `Camera stream not configured/enabled` |
| GET /api/restraints/match | 500 | — | `Media directory not found` |
| GET /api/restraints/match | 422 | — | `Velocity {target_velocity} km/h is not supported. Use one of: [35, 40, 50, 56] km/h.` |
| GET /api/restraints/match | 422 | — | `seatbelt_system '{seatbelt_system}' invalid. Use SLL, CLL, or MSLL.` |
| GET /api/restraints/match | 422 | — | `seat '{seat}' invalid. Use 'fl' (front-left) or 'fr' (front-right).` |
| GET /api/restraints/video/{filename} | 400 | — | `Invalid filename` |
| GET /api/restraints/video/{filename} | 404 | — | `Video not found` |
| GET /adaptive_restraint/chart_info | 503 | — | `Adaptive restraint database is not available. Check db_path / csv_path in config/system.json under 'adaptive_restraint'.` |

## Configuration error providers

| API / scope | HTTP / delivery | Code / error | Exact message or source template |
|---|---|---|---|
| /config system/processor/general mutation routes | 422 | system_config_field_validation_failed | `'; '.join(errors)` |
| /config system/processor/general mutation routes | 422 | system_config_validation_failed | `str(exc)` |
| /config system/processor/general mutation routes | 422 | system_config_backup_invalid | `Invalid backup id` |
| /config system/processor/general mutation routes | 404 | system_config_backup_not_found | `Backup '{backup_id}' not found` |
| /config system/processor/general mutation routes | 422 | system_config_backup_invalid | `Invalid backup path` |
| /config system/processor/general mutation routes | 422 | system_config_patch_empty | `Patch body must be a non-empty object` |
| /config system/processor/general mutation routes | 422 | system_config_runtime_unavailable | `Application runner is unavailable` |
| /config system/processor/general mutation routes | 422 | system_config_field_unsupported | `Unsupported config field(s): {', '.join(unsupported)}` |
| /config system/processor/general mutation routes | 422 | system_config_field_immutable | `Immutable config field(s): {', '.join(immutable)}` |
| /config system/processor/general mutation routes | 500 | system_config_runtime_apply_failed | `detail` |

For `system_config_runtime_apply_failed`, `detail` is
`Runtime config apply failed: {apply_exc}`. Failed rollback appends
`; disk rollback failed: {exc}` and/or `; runtime rollback failed: {exc}`.
`system_config_validation_failed` uses the Pydantic validation exception text.
`system_config_field_validation_failed` joins the applicable messages below with `; `:

- `{path} must be {policy.value_type}`
- `{path} must be one of {allowed}`
- `{path} does not match the required pattern`
- `{path} must contain at least {minimum} item(s)`
- `{path} must contain at most {maximum} item(s)`
- `Every item in {path} must be {item_type}`
- `{path} file does not exist: {value}`
- `{path} must be {operator} {limit}`
- `{path} must use one of these extensions: {extensions}`
- `{path} is not a valid DBC file: {exc}`

## Access warnings and lock messages

| API / scope | HTTP / delivery | Code / error | Exact message or source template |
|---|---|---|---|
| Profile/access routes | warnings or detail; see route behavior | profile_permission_denied | `Profile '{resolved_name}' lacks '{required}' permission` |
| Profile/access routes | warnings or detail; see route behavior | profile_signal_denied | `Signal '{signal_name}' is outside profile '{resolved_name}' scope` |
| Profile/access routes | warnings or detail; see route behavior | profile_already_active | `Profile '{target}' is already the active profile` |
| Signals snapshots, metadata, batch writes | warnings or detail; see route behavior | devmode_seat_locked | `Seat '{lock.seat}' is reserved by another Dev Mode section for {lock.remaining_sec():.0f}s` |
| Signals snapshots, metadata, batch writes | warnings or detail; see route behavior | profile_signal_filtered | `Skipped {len(skipped)} signal(s) outside profile '{profile_name}' scope` |
| Signals snapshots, metadata, batch writes | warnings or detail; see route behavior | profile_access_error | `str(exc.detail)` |
| Signals snapshots, metadata, batch writes | warnings or detail; see route behavior | profile_permission_denied | `Profile '{profile_name}' lacks '{required}' permission` |
| WebSocket subscription ACK | warnings or detail; see route behavior | profile_permission_denied | `Profile '{profile_name}' lacks 'read' permission` |
| WebSocket subscription ACK | warnings or detail; see route behavior | profile_access_error | `str(exc)` |
| WebSocket subscription ACK | warnings or detail; see route behavior | profile_signal_denied | `Signal '{signal_name}' is outside profile '{profile_name}' scope` |
| WebSocket subscription ACK | warnings or detail; see route behavior | profile_signal_filtered | `Wildcard subscription limited to profile '{profile_name}' signals` |

`profile_already_active` is an HTTP 200 warning. Signals list/metadata and batch operations
can return permission/scope warnings with HTTP 200/202 rather than raising an HTTP error.
WebSocket access failures appear in ACK `warnings`. Single-signal permission failures raise
HTTP 403. Single writes blocked by seat locks raise HTTP 423; batch writes carry lock errors
in the result or warnings. Preserve the accompanying `signals`, `profile_name`,
`required_permission`, and `signal_name` fields when present.

## CAN writer error providers

Single writes map non-TX rejection to HTTP 403, missing signals to HTTP 404, and other
encode/transport failures to HTTP 503. Batch failure entries include `signal_name`, `error`,
and `kind` (`missing`, `not_tx`, `value`, `transport`, or `unknown`). If any signal is queued,
the batch returns HTTP 202 with `errors`; when none can be queued, transport errors take
HTTP 503 precedence, then non-TX HTTP 403, then other errors HTTP 404. A profile-filtered
empty batch can return HTTP 202 with warnings without reaching the writer.

- `CAN write rejected: {subject}message '{msg_def.name}' (msg_id={msg_def.msg_id:#x}) is not TX for local node '{LOCAL_CAN_TX_NODE}'; DBC sender(s): [{senders}]`
- `Signal '{signal_name}' not found in CAN database — cannot encode`
- `Failed to encode message {msg_id:#x}`
- `Message ID {msg_id:#x} not found in CAN database — cannot encode`
- `Signal '{name}' not found in any CAN channel — cannot encode`
- `Message ID {msg_id:#x} not found in any CAN channel — cannot encode`
- `CAN bus unavailable while reconnecting: message='{msg_def.name}', msg_id={msg_id:#x}, signals={list(sig_values)}, reason='{self._bus_unavailable_reason}'`
- `CAN bus unavailable while reconnecting: message='{msg_def.name}', msg_id={msg_id:#x}, signals={list(signals)}, reason='{self._bus_unavailable_reason}'`
- `Failed to send CAN frame: message='{msg_def.name}', msg_id={msg_id:#x}, signals={list(sig_values)}, cause={type(exc).__name__}: {exc}`
- `Failed to send CAN message: message='{msg_def.name}', msg_id={msg_id:#x}, signals={list(signals)}, cause={type(exc).__name__}: {exc}`

In the non-TX template, `subject` is `signal '{signal_name}' in ` for signal writes;
`LOCAL_CAN_TX_NODE` is `CAR_PC`, and `senders` lists DBC senders or `unspecified`.
Missing batch signals use `Signal '{sig_name}' not found in any CAN channel`.

## Dev Mode per-seat errors

| `applied[seat].error` | Exact `reason` |
|---|---|
| `seat_not_connected` | `ECU is not connected or not responding` |
| `seat_locked` | `Seat is locked by another Dev Mode section` |
| `signal_not_available` | The corresponding writer error string; fallback `write failed`. |

Selection errors include `selected: false`; signal-write errors instead identify `signal_name`. Some seats succeeding returns HTTP 200;
all selected seats failing returns HTTP 409 with `detail: {applied, expires_at: null}`.

## WebSocket and runtime diagnostics

Invalid JSON on subscription endpoints returns
`{"type":"error","message":"Invalid JSON"}`. Invalid/missing API key is rejected before
acceptance with code 4401; browser clients may observe handshake failure rather than a
readable close frame. `/ws/all` does not handle subscription or ping commands.

Camera status `last_error` carries the upstream exception text when capture fails; it has
no fixed application error code. The exact internal CAN diagnostic
`No UP SocketCAN interface is available` is caught by CAN recovery and appears in logs/
watchdog state. It is not exposed verbatim through the health/readiness API. Those probes
return HTTP 200 with degraded/error status or `ready: false`; inspect their JSON body.
