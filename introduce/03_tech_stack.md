# 03 — Technology Stack

> Summary of CAN-HMI technologies, libraries, and environment  
> Version: 0.8.0

---

## 1. Language & Runtime

| Component | Version | Notes |
|---|---|---|
| **Python** | ≥ 3.10 (target 3.12) | `asyncio` native, type hints, `match` statement |
| **asyncio** | stdlib | All I/O (CAN, DB, HTTP, WS) uses async/await |

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
| `uvicorn[standard]` | ≥ 0.30 | ASGI server (HTTP/1.1 + HTTP/2 + WebSocket) |
| `websockets` | ≥ 12.0 | WebSocket protocol support |

### Data Validation

| Library | Version | Purpose |
|---|---|---|
| `pydantic` | ≥ 2.9 | Request/response models, config validation |
| `pydantic-settings` | ≥ 2.0 | Env var → config object binding |

### Storage

| Library | Version | Purpose |
|---|---|---|
| `aiosqlite` | ≥ 0.20 | Async wrapper for SQLite — does not block the event loop |

### Processing & Config

| Library | Version | Purpose |
|---|---|---|
| `numpy` | ≥ 1.26 | Smoothing, computed signals |
| `pyyaml` | ≥ 6.0 | Load/save YAML config files |
| `psutil` | ≥ 5.9 | Collect system metrics (CPU, RAM, disk, process) |

---

## 3. Dev Dependencies

| Library | Purpose |
|---|---|
| `pytest` + `pytest-asyncio` | Test framework, async test support |
| `pytest-cov` | Coverage report |
| `httpx` | HTTP client for FastAPI tests |
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
| | `frontend/js/widgets.js` — gauge, chart, table, alarm widgets |

No heavy JS framework is used (React/Vue) — suitable for embedded CarPC, with lower resource usage.

---

## 6. Database

| Component | Details |
|---|---|
| **Engine** | SQLite 3 (file-based, zero-config) |
| **Async driver** | `aiosqlite` — async wrapper, does not block the event loop |
| **Schema** | 3 tables: `signal_log`, `alarm_log`, `signal_config` |
| **Retention** | Auto-purge records older than `retention_days` (default 30) |
| **Batch insert** | Buffer signal records, flush by `batch_size` or `batch_interval_sec` |

Can be swapped to **TimescaleDB** / **InfluxDB** by implementing the `ISignalRepository` interface.

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
| **CAN JSON** | `config/can.json` | `DatabaseLoader` (built-in bit manipulation) | The only supported format |

> **Note:** DBC, KCD, SYM, and A2L formats have been removed. `cantools` is no longer a dependency.

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
