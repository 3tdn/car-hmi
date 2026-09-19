# CAN-HMI

Real-time CAN bus signal reader, processor, and web dashboard for CarPC / automotive HMI applications.

## Features

- **Multi-channel CAN I/O** — Read and write CAN frames via `python-can` across multiple independent bus channels; decode/encode signals using per-channel `can_db_file` DBC databases (read directly via `cantools`, no JSON export step)
- **Signal Processing** — Rate limiting, computed signals, bounded queues, and batch persistence
- **REST + WebSocket API** — FastAPI-based API for live signal streaming, full signal metadata, profile permissions, CAN write commands, and system metrics
- **Per-signal WebSocket subscription** — Clients subscribe to specific signal names or `metrics` channels via a structured JSON protocol on `/ws/subscribe`
- **Storage** — Async SQLite persistence with configurable batch inserts and retention; internal CSV/JSON export utility
- **System Metrics** — Real-time CarPC resource monitoring (CPU, RAM, disk, queue, process) via `/system/metrics`
- **Simulator** — Built-in CAN simulator for development without hardware; driven directly by the `can_db_file` DBC signal definitions
- **Standardized signal names (`std_name`)** — API responses include `std_name` for compatibility; it is identical to `signal_name`.
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
  - `scripts/perf_windows.ps1` — run k6 performance script and save JSON report.

- Linux / macOS (Bash):
	- `scripts/run_linux.sh` — prepare `.venv`, install deps and run the application.
	- `scripts/test_linux.sh` — prepare `.venv`, install deps and run tests with coverage.
  - `scripts/perf_linux.sh` — run k6 performance script and save JSON report.
  - `scripts/runtime_smoke_linux.sh` — start app runtime smoke suite (API + WebSocket + Dev Mode lock flow).

Usage examples:

PowerShell (run app):
```powershell
.\scripts\run_windows.ps1 -Config config/system.json -LogLevel INFO
```

PowerShell (run tests, install before running):
```powershell
.\scripts\test_windows.ps1 -InstallBefore
.\scripts\test_windows.ps1 -Suite unit
```

Bash (make scripts executable once and run):
```bash
chmod +x scripts/*.sh
./scripts/run_linux.sh config/system.json INFO
./scripts/test_linux.sh all
./scripts/test_linux.sh security
./scripts/test_linux.sh runtime
./scripts/perf_linux.sh http://localhost:8000
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
│   ├── system.fields.json  # Validation, editability, reload policy, and GUI metadata
│   └── system_bk.json      # Fixed reset template
├── db/
│   ├── can_db/             # DBC files (p_v2.dbc, m_dummy.dbc, p_dummy.dbc)
│   └── ecu_db/             # A2L files (m_dummy.a2l)
├── src/
│   ├── api/                # FastAPI app, routes, WebSocket, auth
│   │   └── routes/         # signals, config, profiles, devmode, system, camera, restraints
│   ├── can_io/             # bus_factory, parser, reader, writer
│   ├── can_simulator/      # DBC-driven CAN simulator
│   ├── core/               # config, config_manager, runner, signal_store, system_metrics
│   ├── processor/          # Rate limiter, computed signals, and pipeline
│   └── storage/            # SQLite repository, database init, exporter (CSV/JSON)
├── tests/
│   ├── 1_unit_functions/   # Unit tests by module/function
│   ├── 2_functional_tests/ # API, WebSocket, and integration tests
│   ├── 3_performance/      # Performance scripts and reports
│   └── 4_security/         # Security hardening and bypass tests
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

Field update permissions and reload levels are documented in
[`docs/system_config_management.md`](docs/system_config_management.md). The Settings GUI reads
the same policy from the backend and supports multiple CAN channel cards.

| Section       | Description                                                                      |
|---------------|----------------------------------------------------------------------------------|
| `can` | **Array** of channels: `interface`, `channel`, `bitrate`, `can_db_file`, optional `channel_tracking_signals`. Auto discovery tracks the CAN messages containing the configured signals. |
| `simulator`   | Enable/disable, `default_cycle_ms`, `can_db_file` (DBC path) for the built-in simulator   |
| `processor`   | `smoothing_window`, `max_update_rate_hz`, `max_queue_size`, `queue_policy` (`drop_oldest` / `reject`), `batch_drain_size` |
| `api`         | `host`, `port`, `api_key`, `cors_origins`, `ws_heartbeat_interval_sec`, `ws_metrics_interval_sec` |
| `storage`     | `engine` (`sqlite`), `sqlite_path`, `batch_size`, `batch_interval_sec`, `retention_days`, `max_disk_mb` |
| `writer`      | CAN write settings. `use_prevalue_for_unwritten_signal`: `true` (default, reuse the latest value for other signals in the same message) or `false` (encode those signals as physical value `0`) |
| `shutdown`    | `timeout_sec` for graceful shutdown                                              |
| `supervisor`  | `watchdog_interval_sec` for component health monitoring                          |
| `logging`     | `level`, `file_path`, `max_size_mb`, `backup_count` for rotating file log        |

Some configuration fields are retained for compatibility but have no active runtime implementation, including `processor.smoothing_window` and `api.ws_heartbeat_interval_sec`; the field policy marks them immutable. See the policy guide for details.

## API Endpoints

The default base URL is `http://localhost:8000`; interactive HTTP documentation is at
`/docs` and `/redoc`.

- [Complete English API reference](docs/api_reference.md): every endpoint, request/response format, exact error messages, and usage examples.
- [Frontend error and warning catalogue](docs/api_errors.md): API/status/code tables and exact backend message templates.
- [HTTP OpenAPI snapshot](docs/api.openapi.json): importable schemas for API tooling.
- [Frontend integration guide](docs/frontend_integration.md): REST/WS, profiles, Dev Mode, settings, charts, video, and camera lifecycle.

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

### Complete HTTP inventory

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

### WebSocket protocol

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


## Runtime configuration & CLI

Edit `config/system.json` directly, or use the included helper script to update processor settings:

```bash
python scripts/set_processor_config.py --max-queue-size 1000000 --queue-policy drop_oldest
```

To apply changes to a running server use `POST /config/processor` (see the [API reference](docs/api_reference.md)).

> When increasing `max_queue_size` the server performs a best-effort migration: new frames go to the new queue and existing items are drained into it within a short timeout. This is not strictly atomic but preserves most in-flight frames. Prefer `drop_oldest` policy for large queues to avoid OOM under heavy load.

## Frontend: System Settings UI

The web dashboard includes a `Settings` button in the header. Use it to:

- Edit system fields through a policy-driven form; each field is labeled `LIVE`, `REBOOT`, or `LOCKED`.
- Add/remove and configure multiple CAN channels.
- Create/list/restore backups, live-reload supported fields, reset from the project template, or reboot Car-HMI.

Notes:
- System config saves merge objects recursively; arrays are replaced completely. Submit the complete `can` array when editing channel cards.
- Reset and restore create a safety backup automatically. `api.api_key`, active resource paths, and unimplemented fields remain locked in the normal editor.
- See [`docs/system_config_management.md`](docs/system_config_management.md) for the complete field policy.

For frontend setup, profile changes, exact error handling, and cleanup of WebSocket/camera connections, see the [frontend integration guide](docs/frontend_integration.md).

## Frontend Modes

- The web dashboard supports two client-side modes selectable from the header: `Dev` and `User`.
	- **Dev**: default behavior — the UI subscribes to and displays all received signals (useful for development and debugging). Profiles restrict TX writes only; RX-only signals do not need profile entries.
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
