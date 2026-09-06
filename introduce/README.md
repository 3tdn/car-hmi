# CAN-HMI — CAN Bus Signal Monitoring and Control System for CarPC

> Internal introduction document — for developer / lead review  
> Updated: 2026-03-22 | Version: 0.8.0

---

## 1. Overview

**CAN-HMI** is a backend + frontend software system that runs on **CarPC** (the embedded computer in the vehicle) and is responsible for:

- **Reading** real-time signals from vehicle ECUs over **CAN Bus** (CAN 2.0B protocol)
- **Decoding** CAN frames into physical signal values according to `config/can.json` (for example `VehicleSpeed`, `EngineRPM`, `BrakePressure`)
- **Processing**: smoothing signals, limiting update rate, calculating derived signals, and raising alarms when thresholds are exceeded
- **Storing** time series in SQLite, with support for historical queries
- **Serving** REST API + WebSocket (FastAPI) for the frontend web dashboard to display real-time data
- **Writing back** signals to the CAN Bus when the user changes parameters from the UI

The system is designed to run **without real hardware** thanks to the built-in **CAN Simulator**.

---

## 2. Technical goals

| Goal | Value |
|---|---|
| Language | Python ≥ 3.10 |
| Read → WebSocket latency | ≤ 50 ms |
| Processing rate | ≥ 1 000 signal updates/second |
| Maximum queue size | 10 000 frame |
| Storage | SQLite, default retention 30 days |
| Deployment | systemd service (`can-hmi.service`) or Docker |

---

## 3. Overall architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       CarPC (Embedded)                      │
│                                                             │
│  [CAN Bus ch0] ──► [CANReader #0] ─┐                       │
│  [CAN Bus ch1] ──► [CANReader #1] ─┤  shared asyncio.Queue │
│  [Simulator]   ──► [CANReader #N] ─┘                       │
│                                  ▼                          │
│                          [Signal Pipeline]                   │
│                    Smooth → RateLimit → Computed → Alarm    │
│                                  │                          │
│               ┌──────────────────┼──────────────────┐       │
│               ▼                  ▼                   ▼       │
│       [Signal Store]        [SQLite DB]       [Alarm Log]   │
│       (in-memory)           (time-series)     (alarm_log)   │
│               │                  │                          │
│               └────────┬─────────┘                          │
│                        ▼                                    │
│               [FastAPI Server :8000]                        │
│           REST /signals  /alarms  /config                   │
│           WebSocket /ws/subscribe                           │
│                        │                                    │
└────────────────────────┼────────────────────────────────────┘
                         │ HTTP / WebSocket
                    [Web Dashboard]
                  (HTML + CSS + JS)
```

---

## 4. Main modules

| Module | Directory | Description |
|---|---|---|
| **CAN I/O** | `src/can_io/` | Read/write CAN frames, decode from can.json |
| **Signal Processor** | `src/processor/` | Signal processing pipeline (4 stages) |
| **Signal Store** | `src/core/signal_store.py` | In-memory cache, Observer pattern |
| **Storage** | `src/storage/` | SQLite repository, CSV/JSON export |
| **FastAPI Backend** | `src/api/` | REST routes, WebSocket, auth |
| **CAN Simulator** | `src/can_simulator/` | ECU simulator, supports scenario YAML |
| **Config Manager** | `src/core/config_manager.py` | Runtime YAML configuration CRUD |
| **Runner** | `src/core/runner.py` | Orchestrator that starts the whole system |

---

## 5. Detailed documentation

| File | Content |
|---|---|
| [01_architecture.md](01_architecture.md) | Module architecture, design patterns, diagrams |
| [02_api_reference.md](02_api_reference.md) | All REST endpoints + WebSocket protocol |
| [03_tech_stack.md](03_tech_stack.md) | Technologies, libraries, environment |
| [04_signal_pipeline.md](04_signal_pipeline.md) | Data flow from CAN Bus → Dashboard |

---

## 6. Quick Start (dev mode)

```bash
# 1. Install
python -m venv .venv
.venv\Scriptsctivate          # Windows
pip install -e ".[dev]"

# 2. Run the application (simulator enabled by default)
can-hmi --config config/system.json

# 3. Open the dashboard
# http://localhost:8000

# 4. Run tests
pytest
```

---

## 7. Development status  

| Phase | Content | Status |
|---|---|---|
| 1 | Foundation (config, project structure, all files) | ✅ DONE |
| 2 | CAN Reader (python-can, async producer, can.json parser) | ✅ DONE |
| 3 | Signal Processor (pipeline, filters, alarms, computed) | ✅ DONE |
| 4 | FastAPI full implementation (REST + WebSocket) | ✅ DONE |
| 5 | CLI / Runner (orchestrate full stack) | ✅ DONE |
| 6 | Frontend (Dev mode + User mode, whitelist) | ✅ DONE |
| 7 | Tests (unit + integration) | 🔄 In progress |
| 8 | Per-signal/channel WS subscribe, metrics push | ✅ DONE |

---

## 8. Contact & Notes

- All PlantUML diagrams are in `diagram/` (C4 Level 1–2, Component, Class, ER, Sequence, Activity, Deployment)
- Full requirements document: `docs/requirement.md`
- Runtime configuration: `config/system.json`, `config/alarms.json`, `config/signals.json`
