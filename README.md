# CAN-HMI

Real-time CAN bus signal reader, processor, and web dashboard for CarPC / automotive HMI applications.

## Features

- **CAN I/O** — Read and write CAN frames via `python-can`; decode/encode signals using DBC/JSON CAN databases
- **Signal Processing** — Smoothing, rate limiting, computed signals, alarm thresholds
- **REST + WebSocket API** — FastAPI-based API for live signal streaming, alarm history, and CAN write commands
- **Storage** — Async SQLite persistence with configurable batch inserts and retention
- **Simulator** — Built-in CAN simulator with YAML/JSON scenario definitions for development without hardware

## Requirements

- Python ≥ 3.10
- (Optional) SocketCAN interface or compatible CAN adapter for real hardware

## Quick Start

```bash
# Clone
git clone git@github.com:thithuongdk/car-hmi.git
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
│   ├── system.json         # CAN bus, API, storage, simulator settings
│   ├── alarms.json         # Alarm thresholds per signal (dict format)
│   └── signals.json        # Signal display configuration
├── db/
│   ├── can_db/             # DBC files (CAN database definitions)
│   └── ecu_db/             # A2L files (ECU descriptions)
├── src/
│   ├── api/                # FastAPI app, routes, WebSocket, auth
│   │   └── routes/         # signals, alarms, config, system routes
│   ├── can_io/             # bus_factory, parser, reader, writer
│   ├── can_simulator/      # Scenario + random CAN simulators
│   ├── core/               # config, config_manager, runner, signal_store
│   ├── processor/          # Pipeline stages: filters, alarms, computed
│   └── storage/            # SQLite repository, database init, exporter
├── tests/                  # pytest test suite
├── frontend/               # Static HTML/CSS/JS dashboard
├── scripts/                # Helper scripts (run, test, config tools)
├── diagram/                # PlantUML architecture diagrams
├── scenarios/              # YAML scenario files for simulator
├── deploy/                 # systemd service file
├── pyproject.toml
├── ruff.toml
└── README.md
```

## Configuration

All runtime behaviour is controlled via `config/system.json`. Key sections:

| Section       | Description                                          |
|---------------|------------------------------------------------------|
| `can`         | Interface, channel, bitrate, CAN DB paths            |
| `simulator`   | Enable/disable, scenario file, default cycle time    |
| `processor`   | Smoothing window, rate limiter, queue policy          |
| `api`         | Host, port, API key, CORS origins, WS heartbeat      |
| `storage`     | SQLite path, batch size, retention days               |
| `alarms`      | (separate file) per-signal threshold definitions      |

## API Endpoints

| Method | Path                          | Description                                |
|--------|-------------------------------|--------------------------------------------|
| GET    | `/signals`                    | List all current signal values             |
| GET    | `/signals/{name}`             | Get a specific signal                      |
| GET    | `/signals/{name}/history`     | Signal history (time-series)               |
| PUT    | `/signals/{name}`             | Write a signal value to CAN bus (202)      |
| GET    | `/alarms`                     | List recent alarms                         |
| GET    | `/alarms/{id}`                | Get a specific alarm                       |
| POST   | `/alarms/{id}/acknowledge`    | Acknowledge an alarm                       |
| POST   | `/alarms/{id}/resolve`        | Resolve an alarm                           |
| GET    | `/config`                     | List signal display configs                |
| GET    | `/config/signal/{name}`       | Read a signal config                       |
| PATCH  | `/config/signal/{name}`       | Update a signal config                     |
| GET    | `/config/processor`           | Read processor runtime config              |
| POST   | `/config/processor`           | Update processor config (live apply)       |
| GET    | `/config/general`             | Read full application config (JSON)        |
| PATCH  | `/config/general`             | Patch application config (partial)         |
| POST   | `/config/general/reset`       | Reset application config to defaults       |
| GET    | `/config/alarms`              | Read alarms config file (JSON)             |
| POST   | `/config/alarms`              | Update alarms config (overwrite)           |
| POST   | `/config/alarms/reset`        | Reset alarms config to default (empty)     |
| GET    | `/system/health`              | Health check (liveness)                    |
| GET    | `/system/ready`               | Readiness check                            |
| WS     | `/ws/signals`                 | Live signal stream via WebSocket           |
| WS     | `/ws/alarms`                  | Live alarm events via WebSocket            |
| WS     | `/ws/all`                     | All events (signals + alarms) via WS       |

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

The web dashboard now includes `Settings` and `Alarms` buttons in the header. Use them to:

 - View and edit the full `config/system.json` (JSON editor in modal).
 - Reset the application config to defaults (Reset button in modal).
 - View and edit `config/alarms.json` and reset alarms to an empty default.

Notes:
- The modal editors send JSON to the backend endpoints under `/config/*`. The backend will persist changes to disk and attempt a live apply where supported.
 - Always backup `config/system.json` if you have customized critical paths (DBC dirs, DB locations) before resetting.

## Frontend Modes

- The web dashboard supports two client-side modes selectable from the header: `Dev` and `User`.
	- **Dev**: default behavior — the UI subscribes to and displays all available signals (useful for development and debugging).
	- **User**: restricted mode — the UI only fetches, subscribes to and displays a curated whitelist of signals intended for end-users.

- The selected mode is stored in `localStorage` under the key `frontend_mode`. Changing the mode reloads the page to re-bootstrap subscriptions.
- The whitelist used in `User` mode is defined client-side in `frontend/js/app.js` as `USER_SIGNAL_WHITELIST` and can be adjusted there or moved to a backend-config endpoint in a future change.

Usage: select mode from the header `Mode` dropdown. In `User` mode the signal table and live updates are limited to the approved signals to reduce information overload and improve safety/privacy.

## Docker

```bash
# Build and run
docker compose up --build

# Or build image only
docker build -t can-hmi .

# Run with custom config
docker run -p 8000:8000 -v ./config:/app/config:ro can-hmi
```

The container exposes port 8000 and includes a health check against `/system/health`.

## Deployment (Linux)

A systemd service file is provided at `deploy/can-hmi.service`:

```bash
# Install
sudo cp deploy/can-hmi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now can-hmi

# Logs
sudo journalctl -u can-hmi -f
```

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
