# CAN-HMI

Real-time CAN bus signal reader, processor, and web dashboard for CarPC / automotive HMI applications.

## Features

- **Multi-channel CAN I/O** — Read and write CAN frames via `python-can` across multiple independent bus channels; decode/encode signals using per-channel `can_json_path` databases
- **Signal Processing** — Smoothing (moving average), rate limiting, computed signals, alarm thresholds with `info / warning / critical` levels
- **REST + WebSocket API** — FastAPI-based API for live signal streaming, full signal metadata, alarm history, CAN write commands, and system metrics
- **Per-signal WebSocket subscription** — Clients subscribe to specific signal names, `alarms`, or `metrics` channels via a structured JSON protocol on `/ws/subscribe`
- **Storage** — Async SQLite persistence with configurable batch inserts, retention policy, and data export to CSV / JSON
- **System Metrics** — Real-time CarPC resource monitoring (CPU, RAM, disk, queue, process) via `/system/metrics`
- **Simulator** — Built-in CAN simulator for development without hardware; driven by the `can.json` signal definitions
- **API Key Auth** — Optional `X-API-Key` header authentication; disabled automatically when key is set to placeholder values

## Requirements

- Python ≥ 3.10
- (Optional) SocketCAN interface or compatible CAN adapter for real hardware
- Key dependencies: `python-can`, `fastapi`, `uvicorn[standard]`, `pydantic`, `aiosqlite`, `numpy`, `psutil`, `pyyaml`

## Quick Start

```bash
# Clone
git clone git@github.com:3tdn/car-hmi.git
cd car-hmi

# Create virtual environment
python -m venv .venv
.venv/Scripts/activate   # Windows
# source .venv/bin/activate  # Linux/macOS

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Start the application (uses config/system.json by default)
can-hmi
# or with a custom config:
can-hmi --config config/system.json --log-level DEBUG
```

## Helper scripts (run & test)

The project includes convenience scripts under the `scripts/` directory to prepare the virtual environment, install dependencies, run the app, and run tests.

- Windows (PowerShell):
	- `scripts/run_windows.ps1` — prepare `.venv`, install deps and run the application.
	- `scripts/test_windows.ps1` — prepare `.venv` (optionally install) and run tests with coverage.

- Linux / macOS (Bash):
	- `scripts/run_linux.sh` — prepare `.venv`, install deps and run the application.
	- `scripts/test_linux.sh` — prepare `.venv`, install deps and run tests with coverage.

Usage examples:

PowerShell (run app):
```powershell
.\scripts\run_windows.ps1 -Config config/system.json -LogLevel INFO
```

PowerShell (run tests, install before running):
```powershell
.\scripts\test_windows.ps1 -InstallBefore
```

Bash (make scripts executable once and run):
```bash
chmod +x scripts/*.sh
./scripts/run_linux.sh config/system.json INFO
./scripts/test_linux.sh
```

Notes:
- On Windows you may need to allow script execution for the current session:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
- The scripts create and use a local `.venv` in the project root and install the project in editable mode with dev dependencies.


## Project Structure

```
car-hmi/
├── config/                 # JSON configuration files
│   ├── system.json         # CAN channels, API, storage, simulator, processor settings
│   ├── can.json            # Combined CAN signal database (all channels)
│   ├── can0.json           # Per-channel CAN signal database for channel 0
│   ├── can1.json           # Per-channel CAN signal database for channel 1
│   └── alarms.json         # Alarm thresholds per signal (info/warning/critical)
├── db/
│   ├── can_db/             # DBC files (p_v2.dbc, m_dummy.dbc, p_dummy.dbc)
│   └── ecu_db/             # A2L files (m_dummy.a2l)
├── src/
│   ├── api/                # FastAPI app, routes, WebSocket, auth
│   │   └── routes/         # signals, alarms, config, system routes
│   ├── can_io/             # bus_factory, parser, reader, writer
│   ├── can_simulator/      # Built-in CAN simulator (scenario + random)
│   ├── core/               # config, config_manager, runner, signal_store, system_metrics
│   ├── processor/          # Pipeline stages: filters, alarms, computed, pipeline
│   └── storage/            # SQLite repository, database init, exporter (CSV/JSON)
├── tests/                  # pytest test suite
├── frontend/               # Static HTML/CSS/JS dashboard
├── scripts/                # Helper scripts (run, test, config tools, DBC utilities)
├── diagram/                # PlantUML architecture diagrams
├── docs/                   # Requirements documentation
├── introduce/              # Architecture and API reference guides
├── deploy/                 # systemd service file
├── pyproject.toml
├── ruff.toml
└── README.md
```

## Configuration

All runtime behaviour is controlled via `config/system.json`. Key sections:

| Section       | Description                                                                      |
|---------------|----------------------------------------------------------------------------------|
| `can`         | **Array** of bus channels — each with `interface`, `channel`, `bitrate`, `can_json_path`, `can_db_files` |
| `simulator`   | Enable/disable, `default_cycle_ms`, `can_json_path` for the built-in simulator   |
| `processor`   | `smoothing_window`, `max_update_rate_hz`, `max_queue_size`, `queue_policy` (`drop_oldest` / `reject`), `batch_drain_size` |
| `api`         | `host`, `port`, `api_key`, `cors_origins`, `ws_heartbeat_interval_sec`, `ws_metrics_interval_sec` |
| `storage`     | `engine` (`sqlite`), `sqlite_path`, `batch_size`, `batch_interval_sec`, `retention_days`, `max_disk_mb` |
| `writer`      | `rate_limit_per_sec`, `burst` for CAN write commands                             |
| `shutdown`    | `timeout_sec` for graceful shutdown                                              |
| `supervisor`  | `watchdog_interval_sec` for component health monitoring                          |
| `logging`     | `level`, `file_path`, `max_size_mb`, `backup_count` for rotating file log        |

Alarm thresholds are defined separately in `config/alarms.json` (per-signal `warning_high`, `warning_low`, `critical_high`, `critical_low`).

## API Endpoints

> **Base URL**: `http://localhost:8000` (default). Interactive docs at `/docs` (Swagger UI) and `/redoc`.
>
> **Authentication**: All REST and WebSocket endpoints accept an optional `X-API-Key` header (or `?api_key=` query param for WebSocket). Auth is disabled when `api_key` is set to a placeholder value (`change-me-in-production`, `changeme`, `default`).

### Quick reference

| Method  | Path                              | Description                                                     |
|---------|-----------------------------------|-----------------------------------------------------------------|
| GET     | `/signals`                        | List all current signal values (snapshot)                       |
| GET     | `/signals/available`              | List all signals with full metadata and alarm thresholds        |
| GET     | `/signals/{name}`                 | Get latest value for a specific signal                          |
| GET     | `/signals/{name}/history`         | Query signal history from DB (time-range, paginated)            |
| PUT     | `/signals/{name}`                 | Write a single signal value to CAN bus                          |
| POST    | `/signals/batch_update`           | Write multiple signals simultaneously                           |
| GET     | `/alarms`                         | List alarm history (filterable)                                 |
| GET     | `/alarms/{id}`                    | Get a specific alarm by ID                                      |
| POST    | `/alarms/{id}/acknowledge`        | Acknowledge an alarm                                            |
| POST    | `/alarms/{id}/resolve`            | Resolve an alarm                                                |
| GET     | `/config`                         | List signal display configs                                     |
| GET     | `/config/signal/{name}`           | Read a signal config                                            |
| PATCH   | `/config/signal/{name}`           | Update a signal config                                          |
| GET     | `/config/processor`               | Read processor runtime config                                   |
| POST    | `/config/processor`               | Update processor config (live apply)                            |
| GET     | `/config/general`                 | Read full application config (JSON)                             |
| PATCH   | `/config/general`                 | Patch application config (partial update)                       |
| POST    | `/config/general/reset`           | Reset application config to defaults                            |
| GET     | `/config/alarms`                  | Read alarms config file                                         |
| POST    | `/config/alarms`                  | Overwrite alarms config                                         |
| POST    | `/config/alarms/reset`            | Reset alarms config to empty default                            |
| GET     | `/api/profiles`                   | List all signal display profiles                                |
| GET     | `/api/profile`                    | Get a profile by name (or active profile)                       |
| POST    | `/api/profile`                    | Create a new profile                                            |
| PUT     | `/api/profile`                    | Update a profile (optimistic locking via `section_id`)          |
| DELETE  | `/api/profile/{name}`             | Delete a profile                                                |
| GET     | `/system/info` · `/api/info`      | Project & system overview (uptime, bus/db status, signal count) |
| GET     | `/system/health`                  | Liveness probe (bus + DB status, uptime)                        |
| GET     | `/system/ready`                   | Readiness probe (for container / systemd)                       |
| GET     | `/system/metrics`                 | System resource metrics (CPU, RAM, disk, queue, process)        |
| GET     | `/adaptive_restraint/available`   | Available filter options for adaptive restraint UI              |
| GET     | `/adaptive_restraint/chart_info`  | Box-plot statistics filtered by occupant parameters             |
| WS      | `/ws/signals`                     | Live signal stream — subscribe per signal name                  |
| WS      | `/ws/subscribe`                   | Alias of `/ws/signals` (backward compatible)                    |
| WS      | `/ws/alarms`                      | Live alarm events only                                          |
| WS      | `/ws/all`                         | All events (signals + alarms)                                   |

---

### Signals

#### `GET /signals`
Returns a snapshot of all current signal values.

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/signals
```

Response:
```json
{
  "items": [
    {"signal_name": "EngineSpeed", "value": 3200.0, "unit": "rpm", "timestamp": 1716451200.123}
  ],
  "total": 1
}
```

#### `GET /signals/available`
Returns full metadata for every signal (call once on client startup). Includes min/max, writable flag, enum states, alarm thresholds, and current snapshot value.

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/signals/available
```

Response item schema:
```json
{
  "signal_name": "EngineSpeed",
  "unit": "rpm",
  "min_value": 0.0,
  "max_value": 8000.0,
  "writable": true,
  "states": null,
  "group_name": null,
  "widget_type": null,
  "alarm_warning_high": 6000.0,
  "alarm_warning_low": null,
  "alarm_critical_high": 7500.0,
  "alarm_critical_low": null,
  "value": 3200.0,
  "status": "ok",
  "timestamp": 1716451200.123
}
```

#### `GET /signals/{name}`
Get latest value for a single signal. Returns `404` if unknown.

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/signals/EngineSpeed
```

#### `GET /signals/{name}/history`
Query time-series history from the database.

| Query param | Type  | Default | Description                      |
|-------------|-------|---------|----------------------------------|
| `start`     | float | —       | Unix timestamp lower bound       |
| `end`       | float | —       | Unix timestamp upper bound       |
| `limit`     | int   | 100     | Max rows returned (1–10 000)     |
| `offset`    | int   | 0       | Pagination offset                |

```bash
curl "http://localhost:8000/signals/EngineSpeed/history?start=1716400000&limit=50" \
  -H "X-API-Key: your_api_key"
```

#### `PUT /signals/{name}`
Write a value to CAN bus. Returns `202 Accepted`. Returns `503` if CAN writer is unavailable.

```bash
curl -X PUT http://localhost:8000/signals/EngineSpeed \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"value": 3500}'
```

Response:
```json
{"signal_name": "EngineSpeed", "value": 3500.0, "queued_at": 1716451200.456}
```

#### `POST /signals/batch_update`
Write multiple writable signals simultaneously. Successful items are returned even if some fail.

```bash
curl -X POST http://localhost:8000/signals/batch_update \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"signals": [{"signal_name": "EngineSpeed", "value": 3500}, {"signal_name": "CoolantTemp", "value": 90}]}'
```

Response:
```json
{
  "queued": [
    {"signal_name": "EngineSpeed", "value": 3500.0},
    {"signal_name": "CoolantTemp", "value": 90.0}
  ],
  "count": 2,
  "queued_at": 1716451200.789,
  "errors": []
}
```

---

### Alarms

#### `GET /alarms`
List alarm history. All query params are optional.

| Query param    | Type    | Description                               |
|----------------|---------|-------------------------------------------|
| `signal_name`  | string  | Filter by signal name                     |
| `level`        | string  | `info` \| `warning` \| `critical`         |
| `acknowledged` | bool    | Filter by acknowledged state              |
| `start`        | float   | Unix timestamp lower bound (triggered_at) |
| `end`          | float   | Unix timestamp upper bound                |
| `limit`        | int     | Max rows (1–1000, default 50)             |
| `offset`       | int     | Pagination offset                         |

```bash
curl "http://localhost:8000/alarms?level=critical&acknowledged=false&limit=20" \
  -H "X-API-Key: your_api_key"
```

#### `GET /alarms/{id}` · `POST /alarms/{id}/acknowledge` · `POST /alarms/{id}/resolve`

```bash
# Get alarm
curl http://localhost:8000/alarms/42 -H "X-API-Key: your_api_key"

# Acknowledge
curl -X POST http://localhost:8000/alarms/42/acknowledge -H "X-API-Key: your_api_key"

# Resolve
curl -X POST http://localhost:8000/alarms/42/resolve -H "X-API-Key: your_api_key"
```

Alarm response schema:
```json
{
  "id": 42,
  "signal_name": "EngineSpeed",
  "level": "critical",
  "value": 7800.0,
  "threshold": 7500.0,
  "description": "EngineSpeed exceeded critical_high threshold 7500.0",
  "triggered_at": 1716451100.0,
  "acknowledged": false,
  "resolved_at": null
}
```

---

### Config

#### Signal display config

```bash
# List all
curl http://localhost:8000/config -H "X-API-Key: your_api_key"

# Get one
curl http://localhost:8000/config/signal/EngineSpeed -H "X-API-Key: your_api_key"

# Update (PATCH — all fields optional)
curl -X PATCH http://localhost:8000/config/signal/EngineSpeed \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"unit": "RPM", "min_value": 0, "max_value": 8000, "writable": true}'
```

#### Processor config

```bash
# Read
curl http://localhost:8000/config/processor -H "X-API-Key: your_api_key"

# Update (live apply)
curl -X POST http://localhost:8000/config/processor \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"max_queue_size": 1000000, "queue_policy": "drop_oldest"}'
```

#### Application config (`system.json`)

```bash
# Read full config
curl http://localhost:8000/config/general -H "X-API-Key: your_api_key"

# Partial update
curl -X PATCH http://localhost:8000/config/general \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"api": {"port": 8080}}'

# Reset to defaults
curl -X POST http://localhost:8000/config/general/reset -H "X-API-Key: your_api_key"
```

#### Alarms config (`alarms.json`)

```bash
# Read
curl http://localhost:8000/config/alarms -H "X-API-Key: your_api_key"

# Overwrite
curl -X POST http://localhost:8000/config/alarms \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"alarms": {"EngineSpeed": {"warning_high": 6000, "critical_high": 7500}}}'

# Reset to empty
curl -X POST http://localhost:8000/config/alarms/reset -H "X-API-Key: your_api_key"
```

---

### Profiles

Profiles store per-user signal display whitelists in `config/profiles.json`. The first profile created becomes the active profile automatically.

#### `GET /api/profiles`
List all profiles and the current active profile name.

```bash
curl http://localhost:8000/api/profiles -H "X-API-Key: your_api_key"
```

Response:
```json
{
  "profiles": [
    {"name": "default", "signals": ["EngineSpeed", "CoolantTemp"], "description": "Default view", "section_id": "a1b2c3d4e5f6"}
  ],
  "total": 1,
  "active": "default"
}
```

#### `GET /api/profile?name={name}`
Get a profile by name. Omit `name` to get the active profile.

#### `POST /api/profile` — Create profile (201)

```bash
curl -X POST http://localhost:8000/api/profile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"name": "driver", "signals": ["EngineSpeed", "FuelLevel"], "description": "Driver view"}'
```

#### `PUT /api/profile` — Update profile (optimistic lock)

Requires the `section_id` returned by the last GET. Returns `409 Conflict` if the profile has changed since your last fetch.

```bash
curl -X PUT http://localhost:8000/api/profile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"name": "driver", "signals": ["EngineSpeed", "BatteryVoltage"], "description": "Updated view", "section_id": "a1b2c3d4e5f6"}'
```

#### `DELETE /api/profile/{name}` — Delete profile (204)

```bash
curl -X DELETE http://localhost:8000/api/profile/driver -H "X-API-Key: your_api_key"
```

---

### System

```bash
# Project & system overview (uptime, bus/db, signal count)
curl http://localhost:8000/system/info
# Alias:
curl http://localhost:8000/api/info

# Liveness probe
curl http://localhost:8000/system/health

# Readiness probe
curl http://localhost:8000/system/ready

# Resource metrics (CPU, RAM, disk, queue)
curl http://localhost:8000/system/metrics
```

Health response:
```json
{"status": "ok", "uptime_seconds": 123.4, "bus_connected": true, "db_connected": true}
```

Readiness response:
```json
{"ready": true, "details": {"bus": true, "db": true}}
```

---

### Adaptive Restraint

Provides injury-risk box-plot data for occupant safety analytics. The database is built automatically from `db/adaptive_restraint_db/synthetic_data_out_gui.csv` on first startup.

#### `GET /adaptive_restraint/available`
Returns all valid filter values to populate UI dropdowns.

```bash
curl http://localhost:8000/adaptive_restraint/available
```

Response:
```json
{
  "System": ["fusion", "camera", "non_adapt"],
  "Age": ["35y", "65y"],
  "Seatbelt": ["Airbag", "Airbag+Belt", "Belt"],
  "Velocity": [30, 50, 80],
  "Weight": [60.0, 80.0],
  "Height": [160.0, 175.0],
  "Distance": [1, 2, 3]
}
```

#### `GET /adaptive_restraint/chart_info`
Returns filtered box-plot statistics and (optionally) raw rows. All query params are multi-value lists.

| Query param | Type            | Description                                          |
|-------------|-----------------|------------------------------------------------------|
| `System`    | `list[str]`     | System type filter (default: all)                    |
| `Age`       | `list[str]`     | Age group filter (default: all)                      |
| `Seatbelt`  | `list[str]`     | Seatbelt component filter (default: all)             |
| `Velocity`  | `list[float]`   | Velocity values in km/h (default: all)               |
| `Weight`    | `list[float]`   | Occupant weight in kg (default: all)                 |
| `Height`    | `list[float]`   | Occupant height in cm (default: all)                 |
| `Distance`  | `list[float]`   | Seat position / distance (default: all)              |
| `RawData`   | bool            | Include raw rows in response (default: `true`)       |

```bash
curl "http://localhost:8000/adaptive_restraint/chart_info?System=fusion&Age=35y&Velocity=50&RawData=false"
```

---

### WebSocket

> **Auth**: Pass `?api_key=your_api_key` as a query parameter when connecting.

All four WS endpoints are mounted under `/ws`:

| Endpoint         | Description                                             |
|------------------|---------------------------------------------------------|
| `/ws/signals`    | Per-signal subscription — full JSON command protocol    |
| `/ws/subscribe`  | Alias of `/ws/signals` (backward compatible)            |
| `/ws/alarms`     | Alarm events only (passive stream, no command needed)   |
| `/ws/all`        | All events — signals + alarms (passive stream)          |

#### `/ws/signals` and `/ws/subscribe` — Command Protocol

Connect and send JSON commands to subscribe / unsubscribe. The server streams updates until disconnected.

**Client → Server messages:**

```jsonc
// Subscribe to specific signals (preferred format)
{"type": "subscribe", "signals": ["EngineSpeed", "CoolantTemp"]}

// Subscribe to all signals
{"type": "subscribe", "signals": ["*"]}

// Subscribe to alarms and system metrics channels
{"type": "subscribe", "signals": ["alarms", "metrics"]}

// Subscribe to everything at once
{"type": "subscribe", "signals": ["*", "alarms", "metrics"]}

// Unsubscribe from specific signals
{"type": "unsubscribe", "signals": ["CoolantTemp"]}

// Keepalive ping
{"type": "ping"}

// Legacy format (backward compatible)
{"action": "subscribe", "channels": ["EngineSpeed", "alarms"], "mode": "continuous"}
{"action": "unsubscribe", "channels": ["EngineSpeed"]}
```

Special channel names: `*` (all signals), `alarms` (alarm events), `metrics` (system resource snapshots).

**Server → Client messages:**

```jsonc
// Subscription acknowledged
{"type": "subscribed", "signals": ["EngineSpeed", "CoolantTemp"], "count": 2}

// Pong response
{"type": "pong"}

// Signal update frame (streamed continuously)
{
  "timestamp": "2026-05-23T10:00:00.123Z",
  "signals": [
    {"name": "EngineSpeed", "value": 3200.0},
    {"name": "CoolantTemp", "value": 87.5}
  ]
}

// Alarm event frame (when subscribed to "alarms" or via /ws/alarms)
{
  "type": "alarm",
  "id": 42,
  "signal_name": "EngineSpeed",
  "level": "critical",
  "value": 7800.0,
  "threshold": 7500.0,
  "description": "EngineSpeed exceeded critical_high threshold 7500.0",
  "triggered_at": 1716451100.0
}

// Metrics frame (when subscribed to "metrics")
{
  "type": "metrics",
  "cpu_percent": 12.5,
  "ram_used_mb": 256.0,
  "disk_used_mb": 1024.0,
  "queue_size": 0,
  "uptime_seconds": 3600.0
}
```

**JavaScript example:**

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/signals?api_key=your_api_key");

ws.onopen = () => {
  ws.send(JSON.stringify({ type: "subscribe", signals: ["EngineSpeed", "CoolantTemp", "alarms"] }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "subscribed") {
    console.log("Subscribed to:", msg.signals);
  } else if (msg.signals) {
    // Signal update frame
    msg.signals.forEach(s => console.log(s.name, "=", s.value));
  } else if (msg.type === "alarm") {
    console.warn("ALARM:", msg.signal_name, msg.level, msg.value);
  }
};

// Keepalive
setInterval(() => ws.send(JSON.stringify({ type: "ping" })), 30000);
```

## Runtime configuration & CLI

Edit `config/system.json` directly, or use the included helper script to update processor settings:

```bash
python scripts/set_processor_config.py --max-queue-size 1000000 --queue-policy drop_oldest
```

To apply changes to a running server use `POST /config/processor` (see [Config](#config) section above).

> When increasing `max_queue_size` the server performs a best-effort migration: new frames go to the new queue and existing items are drained into it within a short timeout. This is not strictly atomic but preserves most in-flight frames. Prefer `drop_oldest` policy for large queues to avoid OOM under heavy load.

## Frontend: Settings & Alarms UI

The web dashboard includes `Settings` and `Alarms` buttons in the header. Use them to:

 - View and edit the full `config/system.json` (JSON editor in modal).
 - Reset the application config to defaults (Reset button in modal).
 - View and edit `config/alarms.json` and reset alarms to an empty default.

Notes:
- The modal editors send JSON to the backend endpoints under `/config/*`. The backend persists changes to disk and attempts a live apply where supported.
- Always backup `config/system.json` if you have customized critical paths (`can_json_path`, `sqlite_path`) before resetting.

## Frontend Modes

- The web dashboard supports two client-side modes selectable from the header: `Dev` and `User`.
	- **Dev**: default behavior — the UI subscribes to and displays all available signals (useful for development and debugging).
	- **User**: restricted mode — the UI only fetches, subscribes to and displays a curated whitelist of signals intended for end-users.

- The selected mode is stored in `localStorage` under the key `frontend_mode`. Changing the mode reloads the page to re-bootstrap subscriptions.
- The whitelist used in `User` mode is defined client-side in `frontend/js/app.js` as `USER_SIGNAL_WHITELIST` and can be adjusted there or moved to a backend-config endpoint in a future change.

Usage: select mode from the header `Mode` dropdown. In `User` mode the signal table and live updates are limited to the approved signals to reduce information overload and improve safety/privacy.

## Deployment (Linux)

A systemd service template is provided at `deploy/can-hmi.service`. The template uses `@@PROJECT_DIR@@` and `@@SERVICE_USER@@` placeholders — filled in automatically by the deploy script.

```bash
# Install, enable, and start the service (run from any directory)
bash scripts/deploy_linux.sh

# Check service status
bash scripts/deploy_linux.sh --status

# View live logs
sudo journalctl -u can-hmi -f

# Stop the service
sudo systemctl stop can-hmi

# Remove the service
bash scripts/deploy_linux.sh --uninstall
```

The script:
1. Resolves `PROJECT_DIR` from its own location (no hardcoded paths)
2. Validates that `.venv/bin/can-hmi` and `config/system.json` exist
3. Renders the service template and installs it to `/etc/systemd/system/`
4. Enables and starts (or restarts) the service
5. Prints status and useful commands on success

## Testing

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=src --cov-report=term-missing

# Lint
ruff check src/ tests/
ruff format --check src/ tests/
```

## License

MIT
