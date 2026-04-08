# 03 — Technology Stack

> Tổng hợp công nghệ, thư viện và môi trường CAN-HMI  
> Phiên bản: 0.8.0

---

## 1. Ngôn ngữ & Runtime

| Thành phần | Phiên bản | Ghi chú |
|---|---|---|
| **Python** | ≥ 3.10 (target 3.12) | `asyncio` native, type hints, `match` statement |
| **asyncio** | stdlib | Toàn bộ I/O (CAN, DB, HTTP, WS) là async/await |

---

## 2. Backend Dependencies

### CAN Bus

| Thư viện | Phiên bản | Mục đích |
|---|---|---|
| `python-can` | ≥ 4.4 | Abstraction layer cho CAN interface (socketcan, virtual, PCAN, Vector) |

### Web Framework

| Thư viện | Phiên bản | Mục đích |
|---|---|---|
| `fastapi` | ≥ 0.115 | REST API framework, tự động sinh OpenAPI docs |
| `uvicorn[standard]` | ≥ 0.30 | ASGI server (HTTP/1.1 + HTTP/2 + WebSocket) |
| `websockets` | ≥ 12.0 | WebSocket protocol support |

### Data Validation

| Thư viện | Phiên bản | Mục đích |
|---|---|---|
| `pydantic` | ≥ 2.9 | Request/response models, config validation |
| `pydantic-settings` | ≥ 2.0 | Env var → config object binding |

### Storage

| Thư viện | Phiên bản | Mục đích |
|---|---|---|
| `aiosqlite` | ≥ 0.20 | Async wrapper cho SQLite — không block event loop |

### Processing & Config

| Thư viện | Phiên bản | Mục đích |
|---|---|---|
| `numpy` | ≥ 1.26 | Smoothing, computed signals |
| `pyyaml` | ≥ 6.0 | Load/save YAML config files |
| `psutil` | ≥ 5.9 | Thu thập system metrics (CPU, RAM, disk, process) |

---

## 3. Dev Dependencies

| Thư viện | Mục đích |
|---|---|
| `pytest` + `pytest-asyncio` | Test framework, async test support |
| `pytest-cov` | Coverage report |
| `httpx` | HTTP client cho test FastAPI |
| `ruff` | Linter + formatter (thay thế flake8, isort, black) |
| `locust` | Load testing |

---

## 4. Code Quality

| Tool | Config | Mục đích |
|---|---|---|
| `ruff` | `ruff.toml` | Lint (pycodestyle, pyflakes, bugbear, security…) + format |
| `pytest` | `pyproject.toml` | `asyncio_mode = auto`, coverage ≥ 60% |

Ruff rule sets bật: `E, W, F, I, B, C4, UP, SIM, ANN, S (bandit), RUF`  
Security rules (bandit): SQL injection, hardcoded secrets, subprocess, bind 0.0.0.0…

---

## 5. Frontend

| Thành phần | Mô tả |
|---|---|
| **HTML5** | `frontend/index.html` — single-page app |
| **CSS** | `frontend/css/style.css` — dark theme, responsive |
| **JavaScript (Vanilla)** | `frontend/js/app.js` — state management, mode selection |
| | `frontend/js/api.js` — REST/WebSocket client |
| | `frontend/js/widgets.js` — gauge, chart, table, alarm widgets |

Không dùng framework JS nặng (React/Vue) — phù hợp với CarPC embedded, giảm resource tiêu thụ.

---

## 6. Database

| Thành phần | Chi tiết |
|---|---|
| **Engine** | SQLite 3 (file-based, zero-config) |
| **Async driver** | `aiosqlite` — wrapper async, không block event loop |
| **Schema** | 3 bảng: `signal_log`, `alarm_log`, `signal_config` |
| **Retention** | Auto-purge bản ghi cũ hơn `retention_days` (mặc định 30) |
| **Batch insert** | Buffer signal records, flush theo `batch_size` hoặc `batch_interval_sec` |

Có thể swap sang **TimescaleDB** / **InfluxDB** bằng cách implement `ISignalRepository` interface.

---

## 7. CAN Interface Support

| Interface | Hệ điều hành | Ghi chú |
|---|---|---|
| `socketcan` | Linux | Kernel module, `vcan0` cho dev/test |
| `virtual` | Windows / Linux | python-can virtual bus, dev mode (không cần phần cứng) |
| `pcan` | Windows / Linux | PEAK PCAN adapter |
| `vector` | Windows | Vector hardware (CANalyzer, CANoe) |
| `serial` (SLCAN) | Cross-platform | USB-to-CAN serial adapters |

---

## 8. CAN Database Format Support

| Format | File | Parser | Ghi chú |
|---|---|---|---|
| **CAN JSON** | `config/can.json` | `DatabaseLoader` (built-in bit manipulation) | Định dạng duy nhất được hỗ trợ |

> **Lưu ý:** Các format DBC, KCD, SYM, A2L đã được loại bỏ. `cantools` không còn là dependency.

---

## 9. Deployment

| Phương án | File | Mô tả |
|---|---|---|
| **systemd service** | `deploy/can-hmi.service` | `systemd` managed, `WatchdogSec=30`, restart on crash |
| **Docker** | `Dockerfile`, `docker-compose.yml` | Container với `HEALTHCHECK /system/health` |
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
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"

# Chạy
can-hmi --config config/system.json --log-level DEBUG

# Test
pytest --cov=src

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

---

## 11. Logging

| Thành phần | Chi tiết |
|---|---|
| **Format** | `%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s` |
| **Handlers** | Console + RotatingFileHandler |
| **File** | `logs/can-hmi.log`, max 10 MB × 5 backup |
| **Level** | Configurable qua config hoặc `--log-level` CLI arg |
