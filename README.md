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

| Method | Path                          | Description                                            |
|--------|-------------------------------|--------------------------------------------------------|
| GET    | `/signals`                    | List all current signal values                         |
| GET    | `/signals/available`          | List all signals with full metadata and alarm thresholds |
| GET    | `/signals/{name}`             | Get latest value for a specific signal                 |
| GET    | `/signals/{name}/history`     | Signal history (time-series) from DB                   |
| PUT    | `/signals/{name}`             | Write a signal value to CAN bus (202)                  |
| GET    | `/alarms`                     | List alarm history (filterable by signal, level, acknowledged, time range) |
| GET    | `/alarms/{id}`                | Get a specific alarm                                   |
| POST   | `/alarms/{id}/acknowledge`    | Acknowledge an alarm                                   |
| POST   | `/alarms/{id}/resolve`        | Resolve an alarm                                       |
| GET    | `/config`                     | List signal display configs                            |
| GET    | `/config/signal/{name}`       | Read a signal config                                   |
| PATCH  | `/config/signal/{name}`       | Update a signal config                                 |
| GET    | `/config/processor`           | Read processor runtime config                          |
| POST   | `/config/processor`           | Update processor config (live apply)                   |
| GET    | `/config/general`             | Read full application config (JSON)                    |
| PATCH  | `/config/general`             | Patch application config (partial)                     |
| POST   | `/config/general/reset`       | Reset application config to defaults                   |
| GET    | `/config/alarms`              | Read alarms config file (JSON)                         |
| POST   | `/config/alarms`              | Update alarms config (overwrite)                       |
| POST   | `/config/alarms/reset`        | Reset alarms config to default (empty)                 |
| GET    | `/system/health`              | Liveness probe (bus + DB status, uptime)               |
| GET    | `/system/ready`               | Readiness probe (for container/systemd)                |
| GET    | `/system/metrics`             | System resource metrics (CPU, RAM, disk, queue, process) |
| WS     | `/ws/signals`                 | Live signal stream (all signals)                       |
| WS     | `/ws/alarms`                  | Live alarm events                                      |
| WS     | `/ws/all`                     | All events (signals + alarms)                          |
| WS     | `/ws/subscribe`               | Per-signal subscription via JSON commands              |

> **Authentication**: All REST and WebSocket endpoints accept an optional `X-API-Key` header (or `?api_key=` query param for WebSocket). Auth is disabled when `api_key` is set to a placeholder value (`change-me-in-production`, etc.).

### WebSocket subscribe protocol (`/ws/subscribe`)

The `/ws/subscribe` endpoint uses a structured JSON command to select channels:

```json
// Subscribe to specific signals, alarms, and metrics
{"action": "subscribe", "channels": ["EngineSpeed", "CoolantTemp", "alarms", "metrics"], "mode": "continuous"}

// Subscribe to all signals
{"action": "subscribe", "channels": ["*"], "mode": "continuous"}

// Unsubscribe from a channel
{"action": "unsubscribe", "channels": ["EngineSpeed"]}
```

Special channel names: `alarms` (alarm events), `metrics` (system resource snapshots), `*` (all signals).

## Runtime configuration & CLI

You can update runtime configuration on-disk and attempt a live apply.

 - Edit `config/system.json` manually, or use the included CLI:

```bash
python scripts/set_processor_config.py --max-queue-size 1000000 --queue-policy drop_oldest
```

- To attempt applying the change to a running server, POST to the API:

```bash
curl -X POST http://localhost:8000/config/processor -H 'Content-Type: application/json' -d '{"max_queue_size":1000000,"queue_policy":"drop_oldest"}'
```

Reset endpoints (examples):

```bash
# Reset application config to defaults
curl -X POST http://localhost:8000/config/general/reset

# Reset alarms to empty default
curl -X POST http://localhost:8000/config/alarms/reset
```

When increasing `max_queue_size` the server will perform a best-effort migration: new incoming frames are routed to the new queue and existing queued items are drained from the old queue into the new one for a short timeout. This preserves most in-flight frames but is not strictly atomic; the CLI/API will always persist the change to disk.

Be cautious increasing the queue to very large values — large queues consume RAM. Prefer `drop_oldest` policy to avoid OOM when under heavy load.

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
