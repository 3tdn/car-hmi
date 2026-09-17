# System Configuration Management

`config/system.json` contains runtime values. `config/system.fields.json` is the backend's
source of truth for validation, descriptions, GUI metadata, editability, and reload policy.
The Settings UI reads that policy through `GET /config/system` instead of maintaining a
separate list of locked fields.

Definitions use dotted paths such as `reader.stale_threshold_sec`. A `*` matches one path
segment; `can.*.can_db_file` applies to every channel. The backend derives `editable` from
`reload_level`, validates policy metadata at startup, and validates changed values before
writing. DBC constraints check the `.dbc` extension, file existence, and parseability.

## Reload levels

| Level | Meaning |
|---|---|
| `live` | Save and immediately update the runtime objects using the value. |
| `reboot` | Save; restart the service to apply fully. Saving does not automatically reboot. |
| `immutable` | Reject changes through the API: secrets, open resource paths, or fields without a runtime implementation. |

## Live fields

| Group | Fields |
|---|---|
| API | `api.ws_metrics_interval_sec` |
| Storage | `storage.batch_size`, `storage.batch_interval_sec`, `storage.retention_days`, `storage.max_disk_mb` |
| Processor | `processor.max_update_rate_hz`, `processor.max_queue_size`, `processor.queue_policy`, `processor.batch_drain_size` |
| Reader | `reader.frequency_piority`, `reader.only_send_signal_update`, `reader.stale_threshold_sec` |
| Writer | `writer.periodic_mode`, `writer.periodic_time_step`, `writer.periodic_duration`, `writer.use_prevalue_for_unwritten_signal` |
| Runtime | `shutdown.timeout_sec`, `logging.level` |
| Dev Mode | `devmode.block_timeout_sec`, `devmode.require_seat_connected`, `devmode.bypass_check_CAN_status` |
| Config manager | `config_management.backup_retention_count` |

Changing `processor.max_queue_size` switches the reader and pipeline to a new queue and
drains pending data from the old queue. Reader, writer, pipeline, WebSocket, and app-state
references are updated during the same configuration operation.

## Fields requiring reboot

| Group | Fields |
|---|---|
| Multi-CAN | `can` additions/removals; `can.*.interface`, `can.*.channel`, `can.*.bitrate`, `can.*.can_db_file`, `can.*.channel_tracking_signals` |
| Simulator | `simulator.enabled`, `simulator.random_mode`, `simulator.default_cycle_ms`, `simulator.can_db_file` |
| API server | `api.host`, `api.port`, `api.cors_origins` |
| Profiles | `profiles.default_profile_permission`, `profiles.session_online_ttl_seconds`, `profiles.session_history_limit`, `profiles.session_cleanup_interval_sec` |
| Camera | All `camera.*` fields, including upstream retry and FPS logging intervals |
| Status monitor | `status_monitor.enabled`, `status_monitor.interval_sec`, `status_monitor.ping_timeout_sec`, `status_monitor.targets.*` |
| Supervisor/log | `supervisor.watchdog_interval_sec`, `logging.max_size_mb`, `logging.backup_count` |

With `channel: "auto"`, `channel_tracking_signals: ["COM_Status_ElkCan"]` resolves to the
CAN message containing that signal. Discovery checks that message ID rather than decoding
or checking every signal. An empty list permits all DBC messages with signals. See the
[CAN recovery runbook](can_recovery.md) for configuration constraints and recovery behavior.

## Immutable fields

| Field | Reason |
|---|---|
| `adaptive_restraint.db_path`, `adaptive_restraint.csv_path` | Data resources are already opened/cached. |
| `api.api_key` | Authentication secret; GET redacts it as `********`. |
| `api.ws_heartbeat_interval_sec` | Not used by the current WebSocket runtime. |
| `profiles.profiles_path`, `profiles.sessions_path` | Data/access paths remain fixed for the process lifetime. |
| `profiles.allow_legacy_profile_mutations` | Not implemented by the runtime. |
| `storage.engine`, `storage.sqlite_path` | Cannot replace an open database/backend in place. |
| `processor.smoothing_window` | No smoothing stage is installed in the current pipeline. |
| `writer.rate_limit_per_sec`, `writer.burst` | Writer token-bucket limiting is not implemented. |
| `logging.file_path` | The file handler is already open. |

Changed fields not defined in policy are rejected. Unknown fields already present on disk
are retained when patching an unrelated field; this supports forward compatibility without
allowing clients to introduce unsupported configuration.

## APIs and permissions

Write endpoints use the same `full` profile-permission requirement as profile updates.
A supported `X-Dev-Mode: true` request can bypass profile checks, but not authentication.

| Method | Endpoint | Function |
|---|---|---|
| GET | `/config/system` | Redacted config, field policy, logical paths, and pending reboot state. |
| PATCH | `/config/system` | Validate and merge a partial object patch. |
| POST | `/config/system/reload` | Read disk configuration and apply live fields. |
| POST | `/config/system/reset` | Reset from the fixed template. |
| GET | `/config/system/backups` | List backups. |
| POST | `/config/system/backups` | Create a manual backup. |
| POST | `/config/system/backups/{backup_id}/restore` | Restore a backup after backing up the current state. |
| POST | `/system/reboot` | Graceful service restart; requires a real configured key and Dev Mode. |

Legacy `/config/general` and `/config/general/reset` aliases remain available, but their
response envelopes differ. Prefer the policy-aware `/config/system` endpoints for new UIs.

```bash
curl -X PATCH http://localhost:8000/config/system \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_CONFIGURED_API_KEY' \
  -H 'X-Profile-Name: admin' \
  -d '{"reader":{"stale_threshold_sec":30}}'
```

The example profile must exist and grant the required permission. Objects merge recursively;
**arrays replace completely**. Send the complete intended `can` array when editing one
channel. Do not send the redacted API key back as a new value.

## Response and frontend behavior

GET includes `config`, `fields_schema_version`, `reload_levels`, `fields`, `paths`,
`pending_reboot_paths`, and `reboot_required`. Patch/reset/restore/reload responses include
`ok`, redacted `config`, `changed_paths`, `reload` (`live`, `reboot`, `immutable`), `runtime`
(`applied`, `unavailable`), `pending_reboot_paths`, and `reboot_required`; operations that
create a backup also return backup information. Backup entries contain `id`, `created_at`,
and `size_bytes`.

Render field widgets and locked/restart indicators from GET policy. Show pending reboot
paths after a mutation and do not report reboot fields as applied merely because they were
saved. Reset/restore create safety backups. Invoke the reboot endpoint explicitly when
restart is intended. See the [frontend integration guide](frontend_integration.md).

For immediate ordinary exceptions during live apply, the backend attempts to restore disk
and runtime state and returns `system_config_runtime_apply_failed` (HTTP 500). This rollback
is best effort and does not guarantee recovery from later asynchronous failures or cancellation.
See the [API reference](api_reference.md) for exact validation, policy, backup, and runtime
error messages.

## Fixed paths and test isolation

- Runtime configuration: `config/system.json`
- Reset template: `config/system_bk.json`
- Field definitions: `config/system.fields.json`
- Backups: `config/backups/*.json`
- Retention: `config_management.backup_retention_count` (1–200)

Clients cannot choose arbitrary filesystem paths. Backup IDs are validated and resolved
only within the backup directory. Configuration mutation tests use temporary paths and
injected managers instead of writing the real runtime configuration.
