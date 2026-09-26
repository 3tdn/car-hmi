# 03 — Technology Stack

> Summary of CAN-HMI technologies, libraries, and environment  
> Version: 0.8.0

---

## 1. Language & Runtime

| Component | Version | Notes |
|---|---|---|
| **Python** | ≥ 3.10 (target 3.12) | `asyncio` native, type hints, `match` statement |
| **asyncio** | stdlib | CAN orchestration, HTTP, and WebSocket I/O use async/await |

---

## 2. Backend Dependencies

### CAN Bus

| Library | Version | Purpose |
|---|---|---|
| `python-can` | ≥ 4.4 | Abstraction layer for CAN interfaces (socketcan, virtual, PCAN, Vector) |

### Web Framework

| Library | Version | Purpose |
|---|---|---|
| `fastapi` | ≥ 0.115 | REST API framework, automatically generates OpenAPI docs |
| `uvicorn[standard]` | ≥ 0.30 | ASGI server (HTTP/1.1 + WebSocket) |
| `websockets` | ≥ 12.0 | WebSocket protocol support |

### Data Validation

| Library | Version | Purpose |
|---|---|---|
| `pydantic` | ≥ 2.9 | Request/response models, config validation |
| `pydantic-settings` | ≥ 2.0 | Env var → config object binding |

### Processing & Config

| Library | Version | Purpose |
|---|---|---|
| `numpy` | ≥ 1.26 | Numerical/computed-signal utilities |
| `pyyaml` | ≥ 6.0 | Legacy dependency; current runtime configuration is JSON |
| `psutil` | ≥ 5.9 | Collect system metrics (CPU, RAM, disk, process) |

---

## 3. Dev Dependencies

| Library | Purpose |
|---|---|
| `pytest` + `pytest-asyncio` | Test framework, async test support |
| `pytest-cov` | Coverage report |
| `httpx` | HTTP client for tests and the runtime camera proxy |
| `ruff` | Linter + formatter (replaces flake8, isort, black) |
| `locust` | Load testing |

---

## 4. Code Quality

| Tool | Config | Purpose |
|---|---|---|
| `ruff` | `ruff.toml` | Lint (pycodestyle, pyflakes, bugbear, security…) + format |
| `pytest` | `pyproject.toml` | `asyncio_mode = auto`, coverage ≥ 60% |

Enabled Ruff rule sets: `E, W, F, I, B, C4, UP, SIM, ANN, S (bandit), RUF`  
Security rules (bandit): SQL injection, hardcoded secrets, subprocess, bind 0.0.0.0…

---

## 5. Frontend

| Component | Description |
|---|---|
| **HTML5** | `frontend/index.html` — single-page app |
| **CSS** | `frontend/css/style.css` — dark theme, responsive |
| **JavaScript (Vanilla)** | `frontend/js/app.js` — state management, mode selection |
| | `frontend/js/api.js` — REST/WebSocket client |
| | `frontend/js/widgets.js` — gauge, chart, and table widgets |

No heavy JS framework is used (React/Vue) — suitable for embedded CarPC, with lower resource usage.

---

## 6. Signal metadata

Signal metadata is a Python in-memory catalog built from the configured DBC loaders. No external
driver or persistent store is required. The adaptive-restraint API separately uses the standard
library `sqlite3` module and a NumPy cache for its crash dataset; that dataset is outside the CAN
signal metadata path.

---

## 7. CAN Interface Support

| Interface | Operating system | Notes |
|---|---|---|
| `socketcan` | Linux | Kernel module, `vcan0` for dev/test |
| `virtual` | Windows / Linux | python-can virtual bus, dev mode (no hardware required) |
| `pcan` | Windows / Linux | PEAK PCAN adapter |
| `vector` | Windows | Vector hardware (CANalyzer, CANoe) |
| `serial` (SLCAN) | Cross-platform | USB-to-CAN serial adapters |

---

## 8. CAN Database Format Support

| Format | File | Parser | Notes |
|---|---|---|---|
| **DBC** | `db/can_db/*.dbc` | `DatabaseLoader.load_dbc()` (via `cantools`) | Preferred — read directly by CANReader/CANWriter, no JSON export step |
| **CAN JSON** (legacy) | External/legacy JSON export | `DatabaseLoader.load()` (built-in bit manipulation) | Supported for compatibility only; runtime, `/signals/available`, and Dev Mode use DBC |

> **Note:** `cantools` is a runtime dependency again (used by `load_dbc()`). KCD/SYM/A2L formats are still not supported.

---

## 9. Deployment

| Option | File | Description |
|---|---|---|
| **systemd service** | `deploy/can-hmi.service` | Managed by `systemd`, `WatchdogSec=30`, restart on crash |
| **Docker** | `Dockerfile`, `docker-compose.yml` | Container with `HEALTHCHECK /system/health` |
| **Env vars** | — | `CANHMI_CONFIG`, `CANHMI_API_KEY`, `CANHMI_LOG_LEVEL` |

**Target hardware**: ARM/x86 SoC, ≥ 512 MB RAM, CAN interface (SPI/USB), Ethernet.

```
# Systemd
sudo systemctl enable can-hmi
sudo systemctl start can-hmi
journalctl -u can-hmi -f

# Docker
docker compose up -d
docker compose logs -f
```

---

## 10. Project Setup

```toml
# pyproject.toml
[project]
name = "car-hmi"
version = "0.1.0"
requires-python = ">=3.10"

[project.scripts]
can-hmi = "src.core.runner:main"  # entry point
```

```bash
# Development setup
python -m venv .venv
.venv\Scriptsctivate      # Windows
pip install -e ".[dev]"

# Run
can-hmi --config config/system.json --log-level DEBUG

# Test
pytest --cov=src

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

---

## 11. Logging

| Component | Details |
|---|---|
| **Format** | `%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s` |
| **Handlers** | Console + RotatingFileHandler |
| **File** | `logs/can-hmi.log`, max 10 MB × 5 backup |
| **Level** | Configurable via config or `--log-level` CLI arg |

## Current API and frontend integration

The current runtime has no persistent signal metadata/history and no alarm or smoothing pipeline
stages. Older diagrams and examples describe historical designs.
The camera proxy uses `httpx` at runtime. Dependency constraints are maintained in
`pyproject.toml`; version labels above describe the original stack overview.

Use the [English API reference](../docs/api_reference.md), [OpenAPI snapshot](../docs/api.openapi.json),
and [frontend integration guide](../docs/frontend_integration.md) for current HTTP/WS formats,
authentication, profile scope, error handling, and resource cleanup.
