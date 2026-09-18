# CAN-HMI — API Reference

Verified against the registered routes, generated OpenAPI, and implementation on 2026-09-17. There are **53 HTTP operations (47 operations + 6 system aliases) and 3 WebSocket endpoints**. Examples were validated without sending CAN writes, changing configuration/profiles, or rebooting the service.

## Common Usage

Example base URL: `http://localhost:8000`. Use HTTPS/WSS when TLS is provided by a reverse proxy. JSON request bodies use `Content-Type: application/json`. REST signal timestamps are Unix seconds; WebSocket signal frames use ISO8601 UTC. Responses below are **illustrative examples**, not verified production responses.

```bash
BASE="http://localhost:8000"
API_KEY="YOUR_CONFIGURED_API_KEY"
PROFILE="admin"
CLIENT_ID="web-demo-01"
```

| Header | Meaning |
|---|---|
| `X-API-Key` | The configured API key; signals/config/devmode/profile routers require it when authentication is enabled. |
| `X-Profile-Name` | Select the profile scope. If omitted, the server resolves the client/global profile. The example admin profile must already exist. |
| `X-Client-Id` | A consistent client identifier; required for heartbeat/offline and Dev Mode locks. |
| `X-Dev-Mode: true` | Bypasses profile checks on routes that support Dev Mode; required for system controls. Does not bypass API key authentication. |

The app treats the placeholder keys `change-me-in-production`, `changeme`, and `default` as authentication disabled. Retry/reboot controls still require a real configured key. Public routes include system GET, adaptive restraint, camera, and restraints/video. There is no business route at root `/health` or `/ready`: use `/system/health`, `/system/ready`, or their `/api/health`, `/api/ready` aliases.

Mutation examples demonstrate the format. Choose values using DBC writable/states metadata and profile permissions. Profiles restrict TX writes only; signal reads and RX subscriptions do not require profile entries or X-Dev-Mode. Ordinary TX writes do not require X-Dev-Mode when the profile grants write or full permission for the signal.

## Complete HTTP Endpoint List

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

## Formats and Examples for Each HTTP API

### `GET /signals`

List latest signal values

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/signals" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `SignalListResponse`.

```json
{
  "items": [
    {
      "signal_name": "COM_Status_ElkCan",
      "std_name": "COM_Status_ElkCan",
      "value": 1.0,
      "unit": null,
      "timestamp": 1789600000.0
    }
  ],
  "total": 1,
  "warnings": []
}
```

### `GET /signals/available`

List all available signals with metadata

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/signals/available" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `SignalMetadataListResponse`.

```json
{
  "signals_info": [
    {
      "signal_name": "COM_Status_ElkCan",
      "std_name": "COM_Status_ElkCan",
      "tag": null,
      "unit": null,
      "min_value": 0.0,
      "max_value": 0.0,
      "writable": false,
      "states": null,
      "group_name": "example",
      "widget_type": "example",
      "value": 1.0,
      "timestamp": 1789600000.0
    }
  ],
  "total": 1,
  "warnings": []
}
```

Note: Read writable, min_value, max_value, and states here before choosing a write value. RX-only signals do not need profile entries. Values and timestamps are null only when no sample is available in the store.

### `GET /signals/{signal_name}`

Get latest value for one signal

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `signal_name` | path | string | Yes |  |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/signals/COM_Status_ElkCan" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `SignalValueResponse`.

```json
{
  "signal_name": "COM_Status_ElkCan",
  "std_name": "COM_Status_ElkCan",
  "value": 1.0,
  "unit": null,
  "timestamp": 1789600000.0
}
```

### `PUT /signals/{signal_name}`

Write value to signal (CAN write)

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `signal_name` | path | string | Yes |  |

Body schema: `WriteSignalRequest` (complete field tables are provided at the end of this document).

```json
{
  "value": 0
}
```

Example:

```bash
curl -sS \
  -X PUT \
  "$BASE/signals/ABL_FL_RetractRequest" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"value":0}'
```

Successful response: HTTP 202.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "signal_name": "ABL_FL_RetractRequest",
  "value": 0,
  "queued_at": 1789600000.0
}
```

### `GET /signals/{signal_name}/history`

Query signal history from DB

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `signal_name` | path | string | Yes |  |
| `start` | query | number / null | No |  |
| `end` | query | number / null | No |  |
| `limit` | query | integer | No | default=100; minimum=1; maximum=10000 |
| `offset` | query | integer | No | default=0; minimum=0 |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/signals/COM_Status_ElkCan/history?start=1789600000&end=1789603600&limit=100&offset=0" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `SignalListResponse`.

```json
{
  "items": [
    {
      "signal_name": "COM_Status_ElkCan",
      "std_name": "COM_Status_ElkCan",
      "value": 1.0,
      "unit": null,
      "timestamp": 1789600000.0
    }
  ],
  "total": 1,
  "warnings": []
}
```

### `POST /signals/batch_update`

Write multiple writable signals simultaneously (batch)

Auth: API key when authentication is enabled. Signal reads and RX subscriptions are independent of profiles. TX writes require write or full permission for the signal in the selected profile; X-Dev-Mode can bypass profile checks.

Outside Dev Mode, an unresolved/missing profile sends nothing and returns `queued: []`, `count: 0`, and profile warnings. Only requested signals with `write` or `full` permission in the resolved profile are forwarded to the CAN writer; other requested signals are skipped with `profile_signal_filtered` warnings.

Query/path: no parameters.

Body schema: `BatchSignalWrite` (complete field tables are provided at the end of this document).

```json
{
  "signals": [
    {
      "signal_name": "ABL_FL_RetractRequest",
      "value": 0
    },
    {
      "signal_name": "ABL_FR_RetractRequest",
      "value": 0
    }
  ]
}
```

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/signals/batch_update" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"signals":[{"signal_name":"ABL_FL_RetractRequest","value":0},{"signal_name":"ABL_FR_RetractRequest","value":0}]}'
```

Successful response: HTTP 202.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "queued": [
    {
      "signal_name": "ABL_FL_RetractRequest",
      "value": 0
    },
    {
      "signal_name": "ABL_FR_RetractRequest",
      "value": 0
    }
  ],
  "count": 2,
  "queued_at": 1789600000.0,
  "errors": [],
  "warnings": []
}
```

Note: HTTP 202 may represent a partially successful write; check both errors and warnings. When nothing is queued: transport errors produce 503, not_tx errors produce 403, and other write errors produce 404. Permission filtering or seat-lock warnings can also produce 202 with count=0 and no transport attempt.

### `GET /config`

List all signal configurations

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `array<SignalConfigResponse>`.

```json
[
  {
    "signal_name": "COM_Status_ElkCan",
    "unit": null,
    "min_value": 0.0,
    "max_value": 0.0,
    "group_name": "example",
    "widget_type": "example",
    "writable": false
  }
]
```

Note: The implementation returns a SignalStore snapshot, not a list of saved SQLite configuration records.

### `GET /config/signal/{signal_name}`

Get config for one signal

Auth: API key when authentication is enabled. This route has no additional profile permission check.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `signal_name` | path | string | Yes |  |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config/signal/COM_Status_ElkCan" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `SignalConfigResponse`.

```json
{
  "signal_name": "COM_Status_ElkCan",
  "unit": null,
  "min_value": 0.0,
  "max_value": 0.0,
  "group_name": "example",
  "widget_type": "example",
  "writable": false
}
```

Note: PATCH saves display metadata to the repository. GET currently reads only name/unit from SignalStore and returns defaults for the other fields; it does not read the complete saved PATCH record. writable here is display metadata; the DBC still determines actual TX permission.

### `PATCH /config/signal/{signal_name}`

Update signal config

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `signal_name` | path | string | Yes |  |

Body schema: `UpdateSignalConfigRequest` (complete field tables are provided at the end of this document).

```json
{
  "unit": "",
  "min_value": 0,
  "max_value": 12,
  "widget_type": "slider",
  "writable": true
}
```

Example:

```bash
curl -sS \
  -X PATCH \
  "$BASE/config/signal/ABL_FL_RetractRequest" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"unit":"","min_value":0,"max_value":12,"widget_type":"slider","writable":true}'
```

Successful response: HTTP 200.

Response schema: `SignalConfigResponse`.

```json
{
  "signal_name": "COM_Status_ElkCan",
  "unit": null,
  "min_value": 0.0,
  "max_value": 0.0,
  "group_name": "example",
  "widget_type": "example",
  "writable": false
}
```

Note: PATCH saves display metadata to the repository. GET currently reads only name/unit from SignalStore and returns defaults for the other fields; it does not read the complete saved PATCH record. writable here is display metadata; the DBC still determines actual TX permission.

### `GET /config/processor`

Get processor config

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config/processor" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProcessorConfigResponse`.

```json
{
  "max_queue_size": 10000,
  "queue_policy": "drop_oldest"
}
```

### `POST /config/processor`

Update processor config

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body schema: `UpdateProcessorConfigRequest` (complete field tables are provided at the end of this document).

```json
{
  "max_queue_size": 10000,
  "queue_policy": "drop_oldest"
}
```

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/processor" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"max_queue_size":10000,"queue_policy":"drop_oldest"}'
```

Successful response: HTTP 200.

Response schema: `ProcessorConfigResponse`.

```json
{
  "max_queue_size": 10000,
  "queue_policy": "drop_oldest"
}
```

### `GET /config/system`

Get system config and field update policy

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config/system" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "config": {
    "api": {
      "api_key": "********"
    },
    "reader": {
      "stale_threshold_sec": 30
    }
  },
  "fields_schema_version": 1,
  "reload_levels": {
    "live": "Applied immediately to runtime references.",
    "reboot": "Saved to disk; reboot Car-HMI to apply fully.",
    "immutable": "Cannot be changed through the API."
  },
  "fields": [],
  "paths": {
    "config": "config/system.json",
    "reset_template": "config/system_bk.json",
    "field_definitions": "config/system.fields.json",
    "backup_directory": "config/backups"
  },
  "pending_reboot_paths": [],
  "reboot_required": false
}
```

Note: The response contains the full configuration and policy; the example abbreviates config/fields. PATCH recursively merges objects and replaces arrays as a whole. To change can[0], send the complete can list to retain. can_db_file, channel_tracking_signals, camera, and supervisor changes require reboot; use the fields returned by GET to determine policy.

### `PATCH /config/system`

Patch system config without dropping unrelated fields

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body schema: `object` (complete field tables are provided at the end of this document).

```json
{
  "reader": {
    "stale_threshold_sec": 30
  }
}
```

Example:

```bash
curl -sS \
  -X PATCH \
  "$BASE/config/system" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"reader":{"stale_threshold_sec":30}}'
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "config": {
    "reader": {
      "stale_threshold_sec": 30
    }
  },
  "changed_paths": [
    "reader.stale_threshold_sec"
  ],
  "reload": {
    "live": [
      "reader.stale_threshold_sec"
    ],
    "reboot": [],
    "immutable": []
  },
  "runtime": {
    "applied": [
      "reader.stale_threshold_sec"
    ],
    "unavailable": []
  },
  "pending_reboot_paths": [],
  "reboot_required": false,
  "backup": {
    "id": "BACKUP_ID",
    "created_at": "2026-09-17T00:00:00+00:00",
    "size_bytes": 5000
  }
}
```

Note: The response contains the full configuration and policy; the example abbreviates config/fields. PATCH recursively merges objects and replaces arrays as a whole. To change can[0], send the complete can list to retain. can_db_file, channel_tracking_signals, camera, and supervisor changes require reboot; use the fields returned by GET to determine policy.

### `GET /config/system/backups`

List fixed-path system config backups

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config/system/backups" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "backups": [
    {
      "id": "BACKUP_ID",
      "created_at": "2026-09-17T00:00:00+00:00",
      "size_bytes": 5000
    }
  ]
}
```

### `POST /config/system/backups`

Back up system config

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/system/backups" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 201.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "backup": {
    "id": "BACKUP_ID",
    "created_at": "2026-09-17T00:00:00+00:00",
    "size_bytes": 5000
  }
}
```

### `POST /config/system/backups/{backup_id}/restore`

Restore a system config backup

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `backup_id` | path | string | Yes |  |

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/system/backups/BACKUP_ID/restore" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "config": {
    "reader": {
      "stale_threshold_sec": 30
    }
  },
  "changed_paths": [
    "reader.stale_threshold_sec"
  ],
  "reload": {
    "live": [
      "reader.stale_threshold_sec"
    ],
    "reboot": [],
    "immutable": []
  },
  "runtime": {
    "applied": [
      "reader.stale_threshold_sec"
    ],
    "unavailable": []
  },
  "pending_reboot_paths": [],
  "reboot_required": false,
  "backup": {
    "id": "BACKUP_ID",
    "created_at": "2026-09-17T00:00:00+00:00",
    "size_bytes": 5000
  }
}
```

Note: Replace BACKUP_ID with an id from GET /config/system/backups. The example uses the common response wrapper; the actual diff and backup depend on the restored record.

### `POST /config/system/reset`

Reset system config from the fixed project template

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/system/reset" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "config": {
    "reader": {
      "stale_threshold_sec": 30
    }
  },
  "changed_paths": [
    "reader.stale_threshold_sec"
  ],
  "reload": {
    "live": [
      "reader.stale_threshold_sec"
    ],
    "reboot": [],
    "immutable": []
  },
  "runtime": {
    "applied": [
      "reader.stale_threshold_sec"
    ],
    "unavailable": []
  },
  "pending_reboot_paths": [],
  "reboot_required": false,
  "backup": {
    "id": "BACKUP_ID",
    "created_at": "2026-09-17T00:00:00+00:00",
    "size_bytes": 5000
  }
}
```

Note: Reset uses config/system_bk.json. The example uses the common response wrapper; actual changed_paths and reload values depend on the diff.

### `POST /config/system/reload`

Re-apply live fields from the system config file

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/system/reload" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "config": {
    "reader": {
      "stale_threshold_sec": 30
    }
  },
  "changed_paths": [
    "reader.stale_threshold_sec"
  ],
  "reload": {
    "live": [
      "reader.stale_threshold_sec"
    ],
    "reboot": [],
    "immutable": []
  },
  "runtime": {
    "applied": [
      "reader.stale_threshold_sec"
    ],
    "unavailable": []
  },
  "pending_reboot_paths": [],
  "reboot_required": false,
  "backup": {
    "id": "BACKUP_ID",
    "created_at": "2026-09-17T00:00:00+00:00",
    "size_bytes": 5000
  }
}
```

Note: Reapplies live configuration; it does not replace reboot for fields classified as reboot. backup is optional; reload normally has no backup field.

### `GET /config/general`

Get full application config

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/config/general" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "api": {
    "api_key": "********"
  },
  "reader": {
    "stale_threshold_sec": 30
  }
}
```

Note: Legacy alias: GET/PATCH return configuration directly, without an ok/reload/runtime wrapper. The example configuration is abbreviated.

### `PATCH /config/general`

Patch application config (partial)

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body schema: `object` (complete field tables are provided at the end of this document).

```json
{
  "reader": {
    "stale_threshold_sec": 30
  }
}
```

Example:

```bash
curl -sS \
  -X PATCH \
  "$BASE/config/general" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"reader":{"stale_threshold_sec":30}}'
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "api": {
    "api_key": "********"
  },
  "reader": {
    "stale_threshold_sec": 30
  }
}
```

Note: Legacy alias: GET/PATCH return configuration directly, without an ok/reload/runtime wrapper. The example configuration is abbreviated.

### `POST /config/general/reset`

Reset application config to defaults

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/config/general/reset" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "ok": true,
  "default": {
    "reader": {
      "stale_threshold_sec": 30
    }
  }
}
```

Note: Legacy alias: returns ok and default, without a reload/runtime wrapper. The example default object is abbreviated.

### `GET /adaptive_restraint/available`

Get all available options for adaptive restraint filters

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/adaptive_restraint/available"
```

Successful response: HTTP 200.

Response schema: `map<string, array<any>>`.

```json
{
  "System": [
    "fusion",
    "camera",
    "non_adapt"
  ],
  "Age": [
    "35y",
    "65y"
  ],
  "Seatbelt": [],
  "Velocity": [],
  "Weight": [],
  "Height": [],
  "Distance": []
}
```

Note: System/Age are fixed as shown; other lists come from the dataset. Empty lists in the example are illustrative, not verified live results.

### `GET /adaptive_restraint/chart_info`

Get statistic and chart information for adaptive restraint systems

Auth: This route has no API key dependency.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `System` | query | array<string> | No |  |
| `Age` | query | array<string> | No |  |
| `Seatbelt` | query | array<string> | No |  |
| `Velocity` | query | array<number> | No |  |
| `Weight` | query | array<number> | No |  |
| `Height` | query | array<number> | No |  |
| `Distance` | query | array<number> | No |  |
| `RawData` | query | boolean | No | default=true |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/adaptive_restraint/chart_info?System=fusion&System=camera&Age=35y&RawData=false"
```

Successful response: HTTP 200.

Response schema: `object`.

```json
{
  "controls": {
    "System": [
      "fusion",
      "camera"
    ],
    "Age": [
      "35y"
    ],
    "Seatbelt": [],
    "Velocity": [],
    "Weight": [],
    "Height": [],
    "Distance": [],
    "RawData": false
  },
  "datas": [
    {
      "injury_risk_fusion_35y": {
        "values": [],
        "max": 0,
        "min": 0,
        "upper fence": 0,
        "q3": 0,
        "median": 0,
        "q1": 0,
        "lower fence": 0
      }
    }
  ],
  "available_options": {
    "Velocity": [],
    "Weight": [],
    "Height": [],
    "Distance": [],
    "Seatbelt": []
  }
}
```

Note: Query names are case-sensitive. Pass lists using repeated parameters: System=fusion&System=camera. An omitted/empty filter selects all available values for that filter. RawData defaults to true: true includes up to 100 raw_rows; false omits raw_rows. The route does not read a GET JSON body despite an older docstring. available_options contains available filter choices; the example is abbreviated.

### `GET /system/info`

Get project & system information

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/system/info"
```

Successful response: HTTP 200.

Response schema: `SystemInfoResponse`.

```json
{
  "name": "CAN-HMI Signal API",
  "version": "1.0.0",
  "description": "Real-time CAN bus signal monitoring and control API",
  "uptime_seconds": 0.0,
  "bus_connected": true,
  "db_connected": true,
  "signal_count": 0
}
```

### `GET /system/health`

Health check

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/system/health"
```

Successful response: HTTP 200.

Response schema: `HealthResponse`.

```json
{
  "status": "degraded",
  "uptime_seconds": 0.0,
  "bus_connected": false,
  "db_connected": true
}
```

### `GET /system/ready`

Readiness probe (for container/systemd)

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/system/ready"
```

Successful response: HTTP 200.

Response schema: `ReadinessResponse`.

```json
{
  "ready": false,
  "details": {
    "bus": false,
    "db": true,
    "readers_thread_alive": false,
    "readers_recent_frames": false,
    "readers_no_fatal_error": true
  }
}
```

### `GET /system/metrics`

CarPC resource information (CPU, RAM, disk, queue, heap…)

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/system/metrics"
```

Successful response: HTTP 200.

Response schema: `SystemMetricsResponse`.

```json
{
  "timestamp": 1789600000.0,
  "cpu_percent": 0.0,
  "cpu_percent_per_core": [
    0.0
  ],
  "cpu_count_logical": 0,
  "cpu_count_physical": 0,
  "cpu_freq_current_mhz": 0.0,
  "cpu_freq_max_mhz": 0.0,
  "process_cpu_percent": 0.0,
  "process_memory_rss_mb": 0.0,
  "process_memory_vms_mb": 0.0,
  "process_memory_percent": 0.0,
  "process_threads": 0,
  "process_open_files": 0,
  "process_pid": 0,
  "ram_total_mb": 0.0,
  "ram_available_mb": 0.0,
  "ram_used_mb": 0.0,
  "ram_percent": 0.0,
  "swap_total_mb": 0.0,
  "swap_used_mb": 0.0,
  "swap_percent": 0.0,
  "disk_total_gb": 0.0,
  "disk_used_gb": 0.0,
  "disk_free_gb": 0.0,
  "disk_percent": 0.0,
  "net_bytes_sent": 0,
  "net_bytes_recv": 0,
  "net_packets_sent": 0,
  "net_packets_recv": 0,
  "queue_size": 0,
  "queue_maxsize": 0,
  "queue_usage_percent": 0.0,
  "heap_allocated_mb": 0.0,
  "gc_objects": 0,
  "asyncio_tasks": 0,
  "uptime_seconds": 0.0,
  "python_version": "3.x",
  "platform": "Linux"
}
```

### `POST /system/can/retry`

Retry CAN connections

Auth: A real configured API key + `X-Dev-Mode: true`.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/system/can/retry" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Dev-Mode: true"
```

Successful response: HTTP 200.

Response schema: `object`.

```json
{
  "scheduled": [
    true
  ],
  "count": 1
}
```

### `POST /system/reboot`

Reboot Car-HMI service

Auth: A real configured API key + `X-Dev-Mode: true`.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/system/reboot" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Dev-Mode: true"
```

Successful response: HTTP 202.

Response schema: `object`.

```json
{
  "status": "reboot_scheduled"
}
```

### `GET /api/restraints/match`

Find best-matching restraint video for crash conditions

Auth: This route has no API key dependency.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `weight` | query | number | Yes |  |
| `height` | query | number | Yes |  |
| `crash_severity` | query | integer | Yes |  |
| `seatbelt_system` | query | string | Yes |  |
| `seat` | query | string | No | default="fl" |
| `seat_x_mm` | query | number / null | No |  |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/restraints/match?weight=75&height=175&crash_severity=40&seatbelt_system=SLL&seat=fl&seat_x_mm=100"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "matched": false,
  "video": null,
  "score": 0,
  "context": {
    "weight_kg": 75,
    "height_cm": 175,
    "derived_percentile": 50,
    "effective_percentile": 50,
    "can_percentile": null,
    "target_velocity_kmh": 40,
    "seatbelt_system": "SLL",
    "seat": "fl",
    "seat_x_mm": 100,
    "seat_x_source": "hmi_param",
    "seat_position_zone": "mid",
    "out_of_position": false,
    "candidates_found": 0
  }
}
```

Note: crash_severity must be 35/40/50/56; seatbelt_system is SLL/CLL/MSLL; seat is fl/fr. Optional seat_x_mm has priority query > live CAN > fallback. Live CAN occupant classification has priority over the weight-derived percentile. When matched=true, video contains filename, percentile, seat_position, velocity_kmh, seatbelt, and url.

### `GET /api/restraints/video/{filename}`

Stream a video file from the media directory

Auth: This route has no API key dependency.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `filename` | path | string | Yes |  |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/restraints/video/VIDEO.mp4" \
  --output video.mp4
```

Successful response: HTTP 200.

Format: binary video, media_type video/mp4; not JSON.

Note: Returns FileResponse with media_type video/mp4, not JSON despite the current OpenAPI application/json declaration. Replace VIDEO.mp4 with a filename/url from the match API.

### `GET /api/camera/stream`

Proxy live MJPEG stream from the vehicle camera

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/camera/stream" \
  --output camera.mjpeg
```

Successful response: HTTP 200.

Format: binary MJPEG multipart/x-mixed-replace, not JSON.

Note: Returns MJPEG multipart/x-mixed-replace using the upstream boundary, not JSON despite the current OpenAPI application/json declaration. Connects upstream only while viewers exist; it does not poll with a request for each frame.

### `GET /api/camera/status`

Camera stream proxy status

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/camera/status"
```

Successful response: HTTP 200.

Response schema: `CameraStatusResponse`.

```json
{
  "enabled": true,
  "stream_url": "http://192.168.2.119:8080/stream",
  "connected": true,
  "viewer_count": 0,
  "last_error": "example"
}
```

### `GET /api/devmode/catalog`

Dev Mode signal families and selectable states

Auth: API key when authentication is enabled. The route does not require X-Dev-Mode or check profile permissions; lock/status operations require X-Client-Id.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/devmode/catalog" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "seats": [
    "fl",
    "fr",
    "rl1",
    "rl2",
    "rr1"
  ],
  "families": [
    {
      "signal_name": "ABL_RetractRequest",
      "kind": "state",
      "states": [
        {
          "value": 0,
          "description": "Off"
        }
      ],
      "signals": [
        "ABL_FL_RetractRequest"
      ]
    }
  ],
  "block_timeout_sec": 60,
  "status_stale_timeout_sec": 30
}
```

Note: The example abbreviates families/signals/states. Current families are ACR_RetractRequest, ABL_RetractRequest, ISB_Color, and HB_Request; use the catalog to display available states.

### `GET /api/devmode/status`

Current Dev Mode seat locks

Auth: API key when authentication is enabled. The route does not require X-Dev-Mode or check profile permissions; lock/status operations require X-Client-Id.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/devmode/status" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "seats": {
    "fl": {
      "selected": false,
      "owned": false,
      "connected": false,
      "expires_at": null,
      "remaining_sec": 0.0
    }
  },
  "expires_at": null
}
```

Note: The actual seats object contains fl, fr, rl1, rl2, and rr1; the example shows only fl.

### `POST /api/devmode/seats/select`

Select seats for Dev Mode (locks other sections out)

Auth: API key when authentication is enabled. The route does not require X-Dev-Mode or check profile permissions; lock/status operations require X-Client-Id.

Query/path: no parameters.

Body schema: `DevModeSeatSelectRequest` (complete field tables are provided at the end of this document).

```json
{
  "seats": {
    "fl": true,
    "fr": false
  },
  "block_timeout_sec": 60
}
```

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/devmode/seats/select" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"seats":{"fl":true,"fr":false},"block_timeout_sec":60}'
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "applied": {
    "fl": {
      "selected": true,
      "applied_at": "2026-09-17T00:00:00Z"
    },
    "fr": {
      "selected": false,
      "applied_at": "2026-09-17T00:00:00Z"
    }
  },
  "expires_at": "2026-09-17T00:01:00Z"
}
```

Note: seats is a seat-to-boolean map. Optional block_timeout_sec is 1–3600 seconds and defaults to devmode.block_timeout_sec. Locks belong to X-Client-Id; an offline ECU or another client's lock can reject the operation.

### `POST /api/devmode/exit`

Leave Dev Mode and release all seat locks of this section

Auth: API key when authentication is enabled. The route does not require X-Dev-Mode or check profile permissions; lock/status operations require X-Client-Id.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/devmode/exit" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "released": [
    "fl"
  ],
  "released_at": "2026-09-17T00:00:00Z"
}
```

### `POST /api/devmode/signals`

Apply one signal family to several seats at once

Auth: API key when authentication is enabled. The route does not require X-Dev-Mode or check profile permissions; lock/status operations require X-Client-Id.

Query/path: no parameters.

Body schema: `DevModeSignalRequest` (complete field tables are provided at the end of this document).

```json
{
  "signal_name": "ABL_RetractRequest",
  "value": 0,
  "seats": {
    "fl": true
  },
  "block_timeout_sec": 60
}
```

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/devmode/signals" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"signal_name":"ABL_RetractRequest","value":0,"seats":{"fl":true},"block_timeout_sec":60}'
```

Successful response: HTTP 200.

Response format taken from the implementation (OpenAPI does not declare a detailed schema).

```json
{
  "applied": {
    "fl": {
      "signal_name": "ABL_RetractRequest",
      "value": 0,
      "signals": {
        "ABL_FL_RetractRequest": 0
      },
      "applied_at": "2026-09-17T00:00:00Z"
    }
  },
  "expires_at": "2026-09-17T00:01:00Z"
}
```

Note: Seats: fl/fr/rl1/rl2/rr1. Optional block_timeout_sec is 1–3600 seconds. ISB_Color uses an RGB number from 0x000000 to 0xFFFFFF (send a decimal number in JSON). The route explicitly validates the color range; the catalog's state lists are not all enforced as value ranges by the route.

### `GET /api/info`

Get project & system information

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/info"
```

Successful response: HTTP 200.

Response schema: `SystemInfoResponse`.

```json
{
  "name": "CAN-HMI Signal API",
  "version": "1.0.0",
  "description": "Real-time CAN bus signal monitoring and control API",
  "uptime_seconds": 0.0,
  "bus_connected": true,
  "db_connected": true,
  "signal_count": 0
}
```

### `GET /api/health`

Health check

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/health"
```

Successful response: HTTP 200.

Response schema: `HealthResponse`.

```json
{
  "status": "degraded",
  "uptime_seconds": 0.0,
  "bus_connected": false,
  "db_connected": true
}
```

### `GET /api/ready`

Readiness probe (for container/systemd)

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/ready"
```

Successful response: HTTP 200.

Response schema: `ReadinessResponse`.

```json
{
  "ready": false,
  "details": {
    "bus": false,
    "db": true,
    "readers_thread_alive": false,
    "readers_recent_frames": false,
    "readers_no_fatal_error": true
  }
}
```

### `GET /api/metrics`

CarPC resource information (CPU, RAM, disk, queue, heap…)

Auth: This route has no API key dependency.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/metrics"
```

Successful response: HTTP 200.

Response schema: `SystemMetricsResponse`.

```json
{
  "timestamp": 1789600000.0,
  "cpu_percent": 0.0,
  "cpu_percent_per_core": [
    0.0
  ],
  "cpu_count_logical": 0,
  "cpu_count_physical": 0,
  "cpu_freq_current_mhz": 0.0,
  "cpu_freq_max_mhz": 0.0,
  "process_cpu_percent": 0.0,
  "process_memory_rss_mb": 0.0,
  "process_memory_vms_mb": 0.0,
  "process_memory_percent": 0.0,
  "process_threads": 0,
  "process_open_files": 0,
  "process_pid": 0,
  "ram_total_mb": 0.0,
  "ram_available_mb": 0.0,
  "ram_used_mb": 0.0,
  "ram_percent": 0.0,
  "swap_total_mb": 0.0,
  "swap_used_mb": 0.0,
  "swap_percent": 0.0,
  "disk_total_gb": 0.0,
  "disk_used_gb": 0.0,
  "disk_free_gb": 0.0,
  "disk_percent": 0.0,
  "net_bytes_sent": 0,
  "net_bytes_recv": 0,
  "net_packets_sent": 0,
  "net_packets_recv": 0,
  "queue_size": 0,
  "queue_maxsize": 0,
  "queue_usage_percent": 0.0,
  "heap_allocated_mb": 0.0,
  "gc_objects": 0,
  "asyncio_tasks": 0,
  "uptime_seconds": 0.0,
  "python_version": "3.x",
  "platform": "Linux"
}
```

### `POST /api/can/retry`

Retry CAN connections

Auth: A real configured API key + `X-Dev-Mode: true`.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/can/retry" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Dev-Mode: true"
```

Successful response: HTTP 200.

Response schema: `object`.

```json
{
  "scheduled": [
    true
  ],
  "count": 1
}
```

### `POST /api/reboot`

Reboot Car-HMI service

Auth: A real configured API key + `X-Dev-Mode: true`.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/reboot" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Dev-Mode: true"
```

Successful response: HTTP 202.

Response schema: `object`.

```json
{
  "status": "reboot_scheduled"
}
```

### `GET /api/profiles`

List all profiles

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/profiles" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProfilesResponse`.

```json
{
  "profiles": [
    {
      "name": "demo",
      "signals": [
        {
          "name": "demo",
          "permission": [
            "read"
          ]
        }
      ],
      "exinfo": {},
      "description": "example",
      "section_id": "012345abcdef"
    }
  ],
  "total": 1,
  "active": "demo",
  "global_active": "demo",
  "client_id": "web-demo-01"
}
```

### `GET /api/profile/sessions`

List client active-profile sessions

Auth: API key when authentication is enabled. A profile needs read permission, or X-Dev-Mode.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/profile/sessions" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProfileSessionsResponse`.

```json
{
  "sessions": [
    {
      "client_id": "web-demo-01",
      "active": "demo",
      "updated_at": 0.0,
      "last_seen": 0.0,
      "status": "ok"
    }
  ],
  "total": 1,
  "online_total": 0,
  "offline_total": 0,
  "by_profile": [
    {
      "profile_name": "example",
      "total": 1,
      "online": 0,
      "offline": 0
    }
  ],
  "global_active": "demo",
  "ttl_seconds": 600,
  "server_time": 0.0
}
```

### `POST /api/profile/heartbeat`

Heartbeat for client profile session

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/profile/heartbeat" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProfileHeartbeatResponse`.

```json
{
  "client_id": "web-demo-01",
  "active": "demo",
  "last_seen": 0.0,
  "ttl_seconds": 600
}
```

### `POST /api/profile/offline`

Mark client profile session offline

Auth: API key when authentication is enabled. This route has no additional profile permission check.

Query/path: no parameters.

Body: none.

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/profile/offline" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProfileHeartbeatResponse`.

```json
{
  "client_id": "web-demo-01",
  "active": "demo",
  "last_seen": 0.0,
  "ttl_seconds": 600
}
```

Note: Marks the client offline and releases its Dev Mode locks identified by X-Client-Id.

### `GET /api/profile`

Get profile by name (or active profile)

Auth: API key when authentication is enabled. This route has no additional profile permission check.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `name` | query | string / null | No |  |

Body: none.

Example:

```bash
curl -sS \
  "$BASE/api/profile?name=demo" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 200.

Response schema: `ProfileResponse`.

```json
{
  "name": "demo",
  "signals": [
    {
      "name": "demo",
      "permission": [
        "read"
      ]
    }
  ],
  "exinfo": {},
  "description": "example",
  "section_id": "012345abcdef"
}
```

Note: POST: signals/exinfo are optional. PUT requires name, signals, and the latest 12-character section_id from GET. 012345abcdef is illustrative, not a usable token. signals is replaced as a whole. Omit exinfo to retain it; omitted/null description currently assigns null.

### `POST /api/profile`

Create new profile

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode. Creating the first profile permits bootstrap when no profiles exist.

Query/path: no parameters.

Body schema: `ProfileCreate` (complete field tables are provided at the end of this document).

```json
{
  "name": "demo",
  "signals": [
    {
      "name": "COM_Status_ElkCan",
      "permission": [
        "read"
      ]
    },
    {
      "name": "ABL_FL_RetractRequest",
      "permission": [
        "read",
        "write"
      ]
    }
  ],
  "exinfo": {
    "label": "Demo"
  },
  "description": "Demo profile"
}
```

Example:

```bash
curl -sS \
  -X POST \
  "$BASE/api/profile?name=demo" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"name":"demo","signals":[{"name":"COM_Status_ElkCan","permission":["read"]},{"name":"ABL_FL_RetractRequest","permission":["read","write"]}],"exinfo":{"label":"Demo"},"description":"Demo profile"}'
```

Successful response: HTTP 201.

Response schema: `ProfileResponse`.

```json
{
  "name": "demo",
  "signals": [
    {
      "name": "demo",
      "permission": [
        "read"
      ]
    }
  ],
  "exinfo": {},
  "description": "example",
  "section_id": "012345abcdef"
}
```

Note: POST: signals/exinfo are optional. PUT requires name, signals, and the latest 12-character section_id from GET. 012345abcdef is illustrative, not a usable token. signals is replaced as a whole. Omit exinfo to retain it; omitted/null description currently assigns null.

### `PUT /api/profile`

Update profile (optimistic lock)

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode. Creating the first profile permits bootstrap when no profiles exist.

Query/path: no parameters.

Body schema: `ProfileUpdate` (complete field tables are provided at the end of this document).

```json
{
  "name": "demo",
  "signals": [
    {
      "name": "COM_Status_ElkCan",
      "permission": [
        "read"
      ]
    }
  ],
  "section_id": "012345abcdef",
  "exinfo": {},
  "description": "Demo profile"
}
```

Example:

```bash
curl -sS \
  -X PUT \
  "$BASE/api/profile?name=demo" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"name":"demo","signals":[{"name":"COM_Status_ElkCan","permission":["read"]}],"section_id":"012345abcdef","exinfo":{},"description":"Demo profile"}'
```

Successful response: HTTP 200.

Response schema: `ProfileResponse`.

```json
{
  "name": "demo",
  "signals": [
    {
      "name": "demo",
      "permission": [
        "read"
      ]
    }
  ],
  "exinfo": {},
  "description": "example",
  "section_id": "012345abcdef"
}
```

Note: POST: signals/exinfo are optional. PUT requires name, signals, and the latest 12-character section_id from GET. 012345abcdef is illustrative, not a usable token. signals is replaced as a whole. Omit exinfo to retain it; omitted/null description currently assigns null.

### `PUT /api/profile/active`

Set active profile

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode. Creating the first profile permits bootstrap when no profiles exist.

Query/path: no parameters.

Body schema: `ProfileSetActiveRequest` (complete field tables are provided at the end of this document).

```json
{
  "name": "demo"
}
```

Example:

```bash
curl -sS \
  -X PUT \
  "$BASE/api/profile/active" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID" \
  -H 'Content-Type: application/json' \
  --data '{"name":"demo"}'
```

Successful response: HTTP 200.

Response schema: `ActiveProfileResponse`.

```json
{
  "active": "demo",
  "global_active": "demo",
  "client_id": "web-demo-01",
  "warnings": []
}
```

Note: With X-Client-Id, changes that client's profile; without it, changes the global active profile. The demo profile must exist before this call.

### `DELETE /api/profile/{name}`

Delete profile

Auth: API key when authentication is enabled. A profile needs full permission, or X-Dev-Mode. Creating the first profile permits bootstrap when no profiles exist.

| Parameter | Location | Type | Required | Default/Constraints |
|---|---|---|---|---|
| `name` | path | string | Yes |  |

Body: none.

Example:

```bash
curl -sS \
  -X DELETE \
  "$BASE/api/profile/demo" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Profile-Name: $PROFILE" \
  -H "X-Client-Id: $CLIENT_ID"
```

Successful response: HTTP 204.

No response body (204).

## WebSocket — 3 endpoint

| URL | Format |
|---|---|
| `/ws/signals?api_key=...&profile_name=admin` | Subscribe/unsubscribe/ping. Primary endpoint. |
| `/ws/subscribe?api_key=...&profile_name=admin` | Alias of /ws/signals. |
| `/ws/all?api_key=...` | Legacy automatic signal frames; does not handle subscribe/ping commands as the other two endpoints do. |

Pass the API key in the query; it is checked before accept. Invalid keys trigger close code 4401 (a client may only see a handshake failure). profile_name is optional; omission resolves the profile/global selection. The WebSocket contract has no client_id query; a browser WebSocket cannot supply X-Client-Id as a custom REST-style header.

```javascript
const apiKey = "YOUR_CONFIGURED_API_KEY";
const profile = "admin";
const socket = new WebSocket(
  `ws://localhost:8000/ws/signals?api_key=${encodeURIComponent(apiKey)}&profile_name=${encodeURIComponent(profile)}`
);
socket.onopen = () => socket.send(JSON.stringify({
  type: "subscribe",
  signals: ["COM_Status_ElkCan", "metrics"],
  rate_ms: 200,
  mode: "continuous"
}));
socket.onmessage = event => console.log(JSON.parse(event.data));
// Once connected: socket.send(JSON.stringify({type:"ping"}));
// socket.send(JSON.stringify({type:"unsubscribe",signals:["COM_Status_ElkCan"]}));
```

| Client-to-server field | Type / format |
|---|---|
| `type` | subscribe / unsubscribe / ping |
| `signals` | An array of signal names, `"*"`, or `"metrics"`; the implementation also accepts the string `"*"`. |
| `mode` | continuous (default) / once; once waits for the next eligible broadcast and then stops that signal/channel. It does not send an immediate snapshot. |
| `rate_ms` | Milliseconds >= 0; the minimum send interval for the connection. Handled by the implementation although absent from the SubscribeRequest model. |
| Legacy | `{ "action": "subscribe", "channels": ["COM_Status_ElkCan"], "mode": "continuous" }` |

Actual ACK (not type=subscribed as stated in an older docstring):

```json
{
  "type": "subscribe_ack",
  "action": "subscribe",
  "channels": [
    "COM_Status_ElkCan",
    "metrics"
  ],
  "count": 2,
  "warnings": []
}
```

Unsubscribe ACK:

```json
{
  "type": "unsubscribe_ack",
  "action": "unsubscribe",
  "channels": [
    "COM_Status_ElkCan"
  ],
  "count": 1,
  "warnings": []
}
```

Signal frame:

```json
{
  "timestamp": "2026-09-17T00:00:00.123Z",
  "signals": [
    {
      "name": "COM_Status_ElkCan",
      "std_name": "COM_Status_ElkCan",
      "value": 1.0
    }
  ]
}
```

Metrics use `{ "type": "metrics", ... }` with SystemMetricsResponse fields. Ping returns `{ "type": "pong" }`. Invalid JSON returns `{ "type": "error", "message": "Invalid JSON" }`. ACKs can contain profile permission/scope warnings. Signal frames contain only signals accepted by the subscription.

## Common Errors

See the [frontend error and warning catalogue](api_errors.md) for API/status/code tables and exact backend message templates, and the [frontend integration guide](frontend_integration.md) for handling each response shape.

HTTP errors commonly use `{ "detail": "message" }` or `{ "detail": { "code": "...", "message": "...", "signals": [] } }`. Request validation uses HTTP 422 with detail entries containing loc/msg/type. HTTP 200/202 with warnings/errors does not imply complete success: inspect count/queued/applied. Registered health/readiness routes still return HTTP 200 for degraded/ready=false; inspect the JSON body.

## Complete Request/Response Model Schemas

These tables come from the app-generated OpenAPI. Nullable fields include null; Required reflects JSON schema required fields. Configuration objects and custom responses without typed schemas are described in their endpoint sections above.

### AccessWarning

Access/profile warning information for a profile-scoped operation.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `code` | string | Yes |  | Warning or access error code |
| `message` | string | Yes |  | Short description for the frontend |
| `profile_name` | string / null | No |  | Profile name currently in effect |
| `required_permission` | string / null | No |  | Permission required for the operation |
| `signal_name` | string / null | No |  | Affected signal name if this is a single-signal warning |
| `signals` | array<string> | No |  | List of signals skipped in the batch |

### ActiveProfileResponse

Active profile change result.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `active` | string | Yes |  | Active profile name after the update |
| `global_active` | string / null | No |  | Globally active profile name |
| `client_id` | string / null | No |  | Client ID if updated for a client session |
| `warnings` | array<AccessWarning> | No |  | Warnings if the state did not change |

### BatchSignalWrite

Request to write multiple CAN signals at once — POST /signals/batch_update.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signals` | array<BatchSignalWriteItem> | Yes |  | List of signals to be written |

### BatchSignalWriteItem

One signal in a batch write request.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signal_name` | string | Yes |  | Signal name |
| `value` | number | Yes |  | Value to write |

### CameraStatusResponse

Status of the MJPEG camera stream proxy.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `enabled` | boolean | Yes |  | Whether the camera stream is enabled in config |
| `stream_url` | string | Yes |  | MJPEG source URL proxied by CarPC |
| `connected` | boolean | Yes |  | Whether CarPC is currently able to connect to the camera |
| `viewer_count` | integer | Yes |  | Number of clients currently viewing the stream through CarPC |
| `last_error` | string / null | No |  | Most recent upstream error, if any |

### ClientProfileSession

Current profile session for a client.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `client_id` | string | Yes |  | Client ID from the X-Client-Id header |
| `active` | string | Yes |  | Active profile name for this client |
| `updated_at` | number | Yes |  | Unix timestamp of the latest update |
| `last_seen` | number | Yes |  | Unix timestamp of the latest heartbeat |
| `status` | enum ["online", "offline"] | Yes | enum=["online", "offline"] | Online/offline status based on TTL |

### DevModeSeatSelectRequest

Select/deselect seats in Dev Mode — POST /api/devmode/seats/select.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `seats` | map<string, boolean> | Yes |  | Map seat_id → selected (fl, fr, rl1, rl2, rr1) |
| `block_timeout_sec` | number / null | No | minimum=1.0; maximum=3600.0 | How long other sections stay blocked from writing the seat (seconds); defaults to 60 |

### DevModeSignalRequest

Apply one signal family to several seats at once — POST /api/devmode/signals.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signal_name` | string | Yes |  | Signal family: ACR_RetractRequest \| ABL_RetractRequest \| ISB_Color \| HB_Request |
| `value` | number | Yes |  | Value applied to every selected seat |
| `seats` | map<string, boolean> | Yes |  | Map seat_id → whether the value is applied |
| `block_timeout_sec` | number / null | No | minimum=1.0; maximum=3600.0 | Seat lock renewal duration (seconds); defaults to 60 |

### HTTPValidationError



| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `detail` | array<ValidationError> | No |  |  |

### HealthResponse

Overall system health status.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `status` | string | Yes |  | Overall status: 'ok', 'degraded', or 'error' |
| `uptime_seconds` | number | Yes |  | Number of seconds the system has been running continuously |
| `bus_connected` | boolean | Yes |  | True if the CAN bus connection is active |
| `db_connected` | boolean | Yes |  | True if the database connection is active |

### ProcessorConfigResponse

Current processor pipeline configuration.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `max_queue_size` | integer | Yes |  | Maximum signal queue size |
| `queue_policy` | string | Yes |  | Policy when the queue is full: 'drop_oldest' or 'reject' |

### ProfileCreate

Request to create a new profile — POST /api/profile.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Profile name (unique) |
| `signals` | array<ProfileSignal> | No |  | List of signals and per-signal permissions |
| `exinfo` | object | No |  | Arbitrary data for the frontend |
| `description` | string / null | No |  | Short profile description |

### ProfileHeartbeatResponse

Client session heartbeat update result.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `client_id` | string | Yes |  | Client ID whose heartbeat was updated |
| `active` | string / null | No |  | Active profile for the client, or the global fallback |
| `last_seen` | number | Yes |  | Unix timestamp of the latest heartbeat |
| `ttl_seconds` | integer | Yes |  | Current session TTL |

### ProfileResponse

Profile information.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Profile name |
| `signals` | array<ProfileSignal> | Yes |  | List of signals and per-signal permissions |
| `exinfo` | object | No |  | Arbitrary data for the frontend |
| `description` | string / null | No |  | Description |
| `section_id` | string | Yes |  | Hash used for optimistic locking |

### ProfileSessionProfileStat

Session counts by active profile.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `profile_name` | string | Yes |  | Profile name currently active for the session |
| `total` | integer | Yes |  | Total sessions currently using this active profile |
| `online` | integer | Yes |  | Number of online sessions for this profile |
| `offline` | integer | Yes |  | Number of offline sessions for this profile |

### ProfileSessionsResponse

List of active-profile sessions by client.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `sessions` | array<ClientProfileSession> | No |  | List mapping client -> active profile |
| `total` | integer | Yes |  | Total client sessions |
| `online_total` | integer | No | default=0 | Total online sessions |
| `offline_total` | integer | No | default=0 | Total offline sessions |
| `by_profile` | array<ProfileSessionProfileStat> | No |  | Statistics for active sessions by profile |
| `global_active` | string / null | No |  | Default active profile at the global level |
| `ttl_seconds` | integer | Yes |  | TTL used to determine online/offline status |
| `server_time` | number | Yes |  | Current Unix timestamp on the server |

### ProfileSetActiveRequest

Request to change the active profile on the server.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Profile name to become active |

### ProfileSignal

Signal scope in a profile with signal-specific permissions.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Signal name |
| `permission` | array<enum ["read", "write", "full"]> | No |  | Permissions for the signal: read, write, full |

### ProfileUpdate

Request to update a profile (optimistic lock) — PUT /api/profile.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Profile name to update |
| `signals` | array<ProfileSignal> | Yes |  | List of signals and per-signal permissions |
| `exinfo` | object / null | No |  | Arbitrary data for the frontend (leave empty to keep unchanged) |
| `description` | string / null | No |  | Short description |
| `section_id` | string | Yes |  | Current section_id (from GET /api/profile). Used to prevent concurrent overwrites — 409 on mismatch. |

### ProfilesResponse

List of all profiles.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `profiles` | array<ProfileResponse> | Yes |  |  |
| `total` | integer | Yes |  |  |
| `active` | string / null | No |  | Currently active profile name |
| `global_active` | string / null | No |  | Globally active profile name |
| `client_id` | string / null | No |  | Client ID if the request includes X-Client-Id |

### ReadinessResponse

Readiness status for processing incoming requests.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `ready` | boolean | Yes |  | True if the application is ready to accept requests |
| `details` | map<string, boolean> | Yes |  | Status of each component (key: component name, value: ready or not) |

### SignalConfigResponse

Display configuration and metadata for a signal.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signal_name` | string | Yes |  | Signal identifier |
| `unit` | string / null | No |  | Measurement unit |
| `min_value` | number / null | No |  | Minimum valid value |
| `max_value` | number / null | No |  | Maximum valid value |
| `group_name` | string / null | No |  | Functional grouping |
| `widget_type` | string / null | No |  | Frontend widget type |
| `writable` | boolean | No | default=false | Whether the signal is writable |

### SignalListResponse

List of signal values returned by the API.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `items` | array<SignalValueResponse> | Yes |  | List of signals |
| `total` | integer | Yes |  | Total number of signals in the list |
| `warnings` | array<AccessWarning> | No |  | Access warnings, if any |

### SignalMetadata

Full metadata for one signal — returned by GET /signals/available.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signal_name` | string | Yes |  | Unique signal identifier |
| `std_name` | string / null | No |  | Standard signal name; currently identical to signal_name |
| `tag` | array<string> / null | No |  | Tags inferred from the signal name or DBC configuration |
| `unit` | string / null | No |  | Measurement unit |
| `min_value` | number / null | No |  | Minimum valid value |
| `max_value` | number / null | No |  | Maximum valid value |
| `writable` | boolean | No | default=false | Whether the signal can be written via the API |
| `states` | array<object> / null | No |  | List of enum states [{value, description}], or None for continuous numeric signals |
| `group_name` | string / null | No |  | Functional group (for example: engine, body) |
| `widget_type` | string / null | No |  | Frontend widget type |
| `value` | number / null | No |  | Current value (snapshot, optional) |
| `timestamp` | number / null | No |  | Unix timestamp of the latest read |

### SignalMetadataListResponse

List of signal metadata.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signals_info` | array<SignalMetadata> | Yes |  | List of signal metadata |
| `total` | integer | Yes |  | Total number of signals |
| `warnings` | array<AccessWarning> | No |  | Access warnings, if any |

### SignalValueResponse

Current value of a CAN signal.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `signal_name` | string | Yes |  | Unique signal identifier |
| `std_name` | string / null | No |  | Standard signal name; currently identical to signal_name |
| `value` | number | Yes |  | Decoded real value |
| `unit` | string / null | No |  | Measurement unit (for example: km/h, °C) |
| `timestamp` | number | Yes |  | Unix timestamp (seconds) when the value was read |

### SystemInfoResponse

Project overview and system status — GET /api/info.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes |  | Application name |
| `version` | string | Yes |  | API version |
| `description` | string | Yes |  | Description |
| `uptime_seconds` | number | Yes |  | Uptime (seconds) |
| `bus_connected` | boolean | Yes |  | Whether the CAN bus is connected |
| `db_connected` | boolean | Yes |  | Whether the database is connected |
| `signal_count` | integer | Yes |  | Number of signals currently in the store |

### SystemMetricsResponse

System resource and application process information (CarPC metrics).

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `timestamp` | number | Yes |  | Unix timestamp when metrics were collected |
| `cpu_percent` | number | Yes |  | Overall CPU usage (%) |
| `cpu_percent_per_core` | array<number> | Yes |  | Per-core CPU usage (%) |
| `cpu_count_logical` | integer | Yes |  | Logical CPU core count |
| `cpu_count_physical` | integer | Yes |  | Physical CPU core count |
| `cpu_freq_current_mhz` | number | Yes |  | Current CPU frequency (MHz) |
| `cpu_freq_max_mhz` | number | Yes |  | Maximum CPU frequency (MHz) |
| `process_cpu_percent` | number | Yes |  | Application process CPU usage (%) |
| `process_memory_rss_mb` | number | Yes |  | Process RSS memory (MB) |
| `process_memory_vms_mb` | number | Yes |  | Process virtual memory (VMS) (MB) |
| `process_memory_percent` | number | Yes |  | Percentage of system RAM used by the process |
| `process_threads` | integer | Yes |  | Process thread count |
| `process_open_files` | integer | Yes |  | Number of open file descriptors |
| `process_pid` | integer | Yes |  | Application process PID |
| `ram_total_mb` | number | Yes |  | Total physical RAM (MB) |
| `ram_available_mb` | number | Yes |  | Available RAM (MB) |
| `ram_used_mb` | number | Yes |  | RAM currently in use (MB) |
| `ram_percent` | number | Yes |  | RAM usage (%) |
| `swap_total_mb` | number | Yes |  | Total swap capacity (MB) |
| `swap_used_mb` | number | Yes |  | Swap currently in use (MB) |
| `swap_percent` | number | Yes |  | Swap usage (%) |
| `disk_total_gb` | number | Yes |  | Total disk capacity (GB) |
| `disk_used_gb` | number | Yes |  | Used disk space (GB) |
| `disk_free_gb` | number | Yes |  | Free disk space (GB) |
| `disk_percent` | number | Yes |  | Disk usage (%) |
| `net_bytes_sent` | integer | Yes |  | Total bytes sent over the network |
| `net_bytes_recv` | integer | Yes |  | Total bytes received over the network |
| `net_packets_sent` | integer | Yes |  | Total packets sent |
| `net_packets_recv` | integer | Yes |  | Total packets received |
| `queue_size` | integer | Yes |  | Current number of items in the signal processing queue |
| `queue_maxsize` | integer | Yes |  | Maximum queue size |
| `queue_usage_percent` | number | Yes |  | Queue usage (%) |
| `heap_allocated_mb` | number | Yes |  | Allocated Python heap memory (MB) |
| `gc_objects` | integer | Yes |  | Number of Python objects tracked by the Garbage Collector |
| `asyncio_tasks` | integer | Yes |  | Number of running asyncio tasks |
| `uptime_seconds` | number | Yes |  | Application uptime (seconds) |
| `python_version` | string | Yes |  | Python version in use |
| `platform` | string | Yes |  | Operating system / platform information |

### UpdateProcessorConfigRequest

Request to update the processor configuration (PATCH).

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `max_queue_size` | integer / null | No |  | New queue size |
| `queue_policy` | enum ["drop_oldest", "reject"] / null | No | enum=["drop_oldest", "reject"] | Handling policy when the queue is full |

### UpdateSignalConfigRequest

Request to update a partial signal configuration (PATCH).

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `unit` | string / null | No |  | New measurement unit |
| `min_value` | number / null | No |  | New minimum value |
| `max_value` | number / null | No |  | New maximum value |
| `widget_type` | string / null | No |  | New widget type |
| `writable` | boolean / null | No |  | Allow writes or not |

### ValidationError



| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `loc` | array<string / integer> | Yes |  |  |
| `msg` | string | Yes |  |  |
| `type` | string | Yes |  |  |
| `input` | any | No |  |  |
| `ctx` | object | No |  |  |

### WriteSignalRequest

Request to write a value onto the CAN bus.

| Field | Type | Required | Default/Constraints | Description |
|---|---|---|---|---|
| `value` | number | Yes |  | Value to write to the CAN bus |

## Documentation and Frontend Routes

| Method | URL | Result |
|---|---|---|
| GET | /docs | Swagger UI; use Authorize to provide a real API key. |
| GET | /redoc | ReDoc. |
| GET | /openapi.json | OpenAPI JSON runtime. |
| GET | /docs/oauth2-redirect | Swagger UI redirect helper, not a business API. |
| GET | / and static assets | Frontend StaticFiles mount when the frontend directory exists. |

Repository OpenAPI snapshot: [api.openapi.json](api.openapi.json). OpenAPI does not include WebSocket and currently declares incorrect media types for camera/video binary responses; the manual sections above describe actual behavior.
