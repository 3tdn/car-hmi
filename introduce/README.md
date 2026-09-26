# CAN-HMI — CAN Bus Signal Monitoring and Control System for CarPC

> Internal introduction document — for developer / lead review  
> Documentation verified: 2026-09-17

---

## 1. Overview

**CAN-HMI** is a backend + frontend software system that runs on **CarPC** (the embedded computer in the vehicle) and is responsible for:

- **Reading** real-time signals from vehicle ECUs over **CAN Bus** (CAN 2.0B protocol)
- **Decoding** CAN frames into physical signal values according to the configured DBC file (`can[].can_db_file`; for example `VehicleSpeed`, `EngineRPM`, `BrakePressure`)
- **Processing**: limiting update rate, calculating derived signals, coalescing ingress updates, and controlling queue backpressure
- **Keeping** the latest decoded signal values in the in-memory `SignalStore`
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
| Storage | In-memory realtime values; SQLite only for persistent `signal_config` metadata |
| Deployment | systemd service (`can-hmi.service`) or Docker |

---

## 3. Overall architecture

```text
CAN channels / Simulator
          |
    Per-channel DBC loaders, readers, and writers
          |
    Shared bounded ingress queue
          |
    RateLimiter -> ComputedSignals
          |
    SignalStore (in-memory realtime values)
          |
    FastAPI REST + WebSocket -> Web dashboard
```

The current pipeline does not install smoothing or alarm stages and does not persist signal
samples. New databases contain only `signal_config`; there is no signal-history or REST export
route. See the [current API reference](../docs/api_reference.md).


## 4. Main modules

| Module | Directory | Description |
|---|---|---|
| **CAN I/O** | `src/can_io/` | Read/write CAN frames, decode/encode from configured DBC files |
| **Signal Processor** | `src/processor/` | RateLimiter and ComputedSignals pipeline |
| **Signal Store** | `src/core/signal_store.py` | In-memory cache, Observer pattern |
| **Storage** | `src/storage/` | Small SQLite repository for persistent signal display configuration |
| **FastAPI Backend** | `src/api/` | REST routes, WebSocket, auth |
| **CAN Simulator** | `src/can_simulator/` | DBC-driven random signal simulator |
| **Config Manager** | `src/core/config_manager.py` | JSON configuration and field-policy management |
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

For full HTTP schemas and exact error messages, use the [English API reference](../docs/api_reference.md) and [OpenAPI snapshot](../docs/api.openapi.json). For frontend setup and integration, use the [frontend integration guide](../docs/frontend_integration.md).

## 6. Quick Start (dev mode)

```bash
# 1. Install
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate      # Windows
pip install -e ".[dev]"

# 2. Run the application (enable simulator in config for hardware-free use)
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
| 2 | CAN Reader (python-can, async producer, DBC parser) | ✅ DONE |
| 3 | Signal Processor (pipeline, rate limiter, computed) | ✅ DONE |
| 4 | FastAPI full implementation (REST + WebSocket) | ✅ DONE |
| 5 | CLI / Runner (orchestrate full stack) | ✅ DONE |
| 6 | Frontend (Dev mode + User mode, whitelist) | ✅ DONE |
| 7 | Tests (unit + integration) | 🔄 In progress |
| 8 | Per-signal/channel WS subscribe, metrics push | ✅ DONE |

---

## 8. Contact & Notes

- All PlantUML diagrams are in `diagram/` (C4 Level 1–2, Component, Class, ER, Sequence, Activity, Deployment)
- Full requirements document: `docs/requirement.md`
- Runtime configuration: `config/system.json` and `config/system.fields.json`
