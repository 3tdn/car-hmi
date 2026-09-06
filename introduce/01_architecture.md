# 01 — System Architecture

> Internal CAN-HMI architecture document — for developer review  
> Version: 0.8.0

---

## 1. Architecture overview (C4 Level 2 — Containers)

```
┌─────────────────────────── CarPC (Embedded Linux) ────────────────────────────┐
│                                                                                │
│  ┌─────────────────┐     CAN frames     ┌───────────────────────────────────┐ │
│  │  CAN Simulator  │◄──────────────────►│          CAN I/O                  │ │
│  │  (dev mode)     │   virtual bus      │  bus_factory + reader + writer     │ │
│  │  python-can     │                    │  python-can + can.json parser      │ │
│  └─────────────────┘                    └──────────────┬────────────────────┘ │
│                                                        │ asyncio.Queue        │
│  ┌─────────────────┐                    ┌──────────────▼────────────────────┐ │
│  │  Vehicle ECU    │────────────────────│        Signal Processor           │ │
│  │  (hardware)     │   CAN Bus          │  Smooth → Rate → Computed → Alarm  │ │
│  └─────────────────┘   500 kbps         └──────┬───────────────┬────────────┘ │
│                                                │               │              │
│                                  ┌─────────────▼──┐   ┌────────▼────────────┐ │
│                                  │  Signal Store  │   │   SQLite Storage    │ │
│                                  │  (in-memory)   │   │  signal_log         │ │
│                                  │  Observer/PubSub│   │  alarm_log          │ │
│                                  └────────┬───────┘   │  signal_config      │ │
│                                           │           └────────┬────────────┘ │
│                                  ┌────────▼──────────────────▼──────────────┐│
│                                  │            FastAPI Server :8000           ││
│                                  │  REST + WebSocket + Static frontend serve ││
│                                  └──────────────────┬────────────────────────┘│
└─────────────────────────────────────────────────────┼───────────────────────-─┘
                                                       │ HTTP / WebSocket
                                             ┌─────────▼─────────┐
                                             │   Web Dashboard    │
                                             │  HTML + CSS + JS   │
                                             │  Dev/User mode     │
                                             └───────────────────┘
```

---

## 2. Module breakdown

### 2.1 `src/can_io/` — CAN Input/Output

| File | Responsibility |
|---|---|
| `bus_factory.py` | Create `can.BusABC` instances from config (socketcan, virtual, pcan, vector…) |
| `parser.py` | Load and parse `config/can.json` into `ParsedMessage` / `ParsedSignal`. Decode/encode CAN frames using custom bit manipulation |
| `reader.py` | `CANReader`: async producer, reads frames from the bus → decodes → pushes into `asyncio.Queue`. Supports automatic reconnect and backpressure |
| `writer.py` | `CANWriter`: encode signal value → CAN frame → send to bus. `CANWriterRouter`: route signal writes to the correct CAN channel |

**Multi-channel CAN support**: Each channel (vcan0, vcan1…) has its own `DatabaseLoader`, `CANReader`, and `CANWriter`.
All readers feed into a shared `asyncio.Queue` → centralized pipeline processing.
`CANWriterRouter` routes writable signals to the correct writer/channel in O(1).

**DatabaseLoader** is the core class that loads can.json:
```python
db_loader = DatabaseLoader()
db_loader.load("config/can.json")   # load all messages/signals
frame = db_loader.decode_frame(msg_id, raw_bytes)  # → dict[str, float]
msg   = db_loader.encode_signal("VehicleSpeed", 60.0)  # → can.Message
```

---

### 2.2 `src/processor/` — Signal Processing Pipeline

The pipeline consists of 4 sequential stages and applies the **Pipeline Pattern**:

```
asyncio.Queue
      │
      ▼
┌─────────────────┐
│  Stage 1        │  SmoothingFilter   — Moving Average / EMA (window size configurable)
│  Smoothing      │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Stage 2        │  RateLimiter       — Limits update frequency (max_hz)
│  Rate Limiter   │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Stage 3        │  ComputedSignals   — Calculate derived signals (e.g. Power = RPM × Torque)
│  Computed       │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Stage 4        │  AlarmChecker      — Compare against thresholds from config/alarms.json
│  Alarm Check    │                      Emit Alarm event → save to DB + push WS
└────────┬────────┘
         ▼
    SignalStore (update)  +  SQLite batch insert
```

Each stage implements the abstract class `ProcessingStage`:
```python
class ProcessingStage(ABC):
    @abstractmethod
    def process(self, signals: dict[str, float]) -> dict[str, float]: ...
```

---

### 2.3 `src/core/signal_store.py` — Signal Store

**Observer Pattern**: SignalStore is the subject, and subscribers (WebSocket manager, metrics) are observers.

```python
store = SignalStore()
store.subscribe(callback)          # register to receive updates
await store.update("EngineRPM", 2500.0)  # → automatically notify all subscribers
sv = store.get("EngineRPM")        # → SignalValue(value, status, timestamp, unit)
snap = store.get_snapshot()        # → full current cache
```

---

### 2.4 `src/storage/` — Storage Layer

**Repository Pattern** with interface `ISignalRepository` allows swapping backends:

```
ISignalRepository (ABC)
        │
        └── SQLiteRepository  (aiosqlite, async)
```

The database schema contains 3 tables:

| Table | Purpose |
|---|---|
| `signal_log` | Time series (timestamp, signal_name, value, unit) — indexed |
| `alarm_log` | Alarm history (level, value, threshold, acknowledged, resolved_at) |
| `signal_config` | Per-signal display config (unit, min, max, widget_type, writable) |

`DataExporter` allows exporting data as CSV / JSON through the API.

---

### 2.5 `src/api/` — FastAPI Backend

**Factory Pattern**: `create_app()` creates the FastAPI application and injects dependencies through `app.state`.

```
FastAPI app
├── /signals        (REST)   — read snapshot, history, write back
├── /alarms         (REST)   — alarm history, acknowledge, resolve
├── /config         (REST)   — signal config, processor config, app config
├── /system         (REST)   — /health, /ready, /metrics
└── /ws/            (WS)     — /signals, /alarms, /all, /subscribe
```

Authentication: `X-API-Key` header (REST), `?token=` query param (WebSocket). If `api_key` is empty or a placeholder → auth is disabled (dev mode).

---

### 2.6 `src/can_simulator/` — CAN Simulator

Uses `CANSimulator` to read `can.json` directly and generate random values in `[minimum, maximum]`:

| Mode | Class | Description |
|---|---|---|
| `can_json` | `CANSimulator` | Generate random signals in [min, max] from can.json on a fixed cycle |

The simulator uses a dedicated **virtual bus**, isolated from the reader bus (python-can virtual allows multiple instances on the same channel).

---

### 2.7 `src/core/runner.py` — Application Orchestrator

`AppRunner` is the central coordinator that starts the whole system in this order:

1. Setup logging (rotating file + console)
2. Load CAN databases for each channel (can.json per channel)
3. Seed SignalStore with initial values from every channel DB
4. Initialize SQLite storage
5. Create a CAN Bus instance for each channel
6. Initialize the Signal Pipeline + stages (shared queue)
7. Create CANReader + CANWriter
8. Start CAN Simulator (if enabled)
9. Create FastAPI server (uvicorn)
10. Create Watchdog task + Metrics broadcaster task
11. `asyncio.gather()` all tasks

---

## 3. Applied design patterns

| Pattern | Where applied |
|---|---|
| **Pipeline** | `SignalPipeline` — chain processing stages |
| **Observer / Pub-Sub** | `SignalStore.subscribe()` — push to WS clients |
| **Repository** | `ISignalRepository` / `SQLiteRepository` — separate storage logic |
| **Factory** | `create_app()` — FastAPI application factory; `create_bus()` — CAN bus factory |
| **Strategy** | `DatabaseLoader` — loads can.json, built-in bit-level decode/encode |

---

## 4. PlantUML diagrams

All architecture diagrams are in `diagram/`:

| File | Type |
|---|---|
| `01_system_context.puml` | C4 Level 1 — System Context |
| `02_container.puml` | C4 Level 2 — Containers |
| `03_component.puml` | Component — CarPC internals |
| `04_class_diagram.puml` | Class — Key classes + design patterns |
| `08_activity_pipeline.puml` | Activity — Pipeline processing |
| `11_database_er.puml` | ER — Database schema |
| `10_deployment.puml` | Deployment — Physical nodes |
| `12_data_flow.puml` | Data Flow — End-to-end |

Render:
```bash
java -jar plantuml.jar diagram/*.puml
# or the VS Code extension: PlantUML by jebbs
```

---

## 5. System configuration

All runtime config is in `config/system.json`:

```yaml
can:
  interface: virtual          # socketcan / pcan / vector / virtual
  channel: vcan0
  bitrate: 500000
  can_json_path: "config/can.json"

simulator:
  enabled: true
  default_cycle_ms: 100

processor:
  smoothing_window: 5
  max_update_rate_hz: 20
  max_queue_size: 10000
  queue_policy: drop_oldest   # drop_oldest / block / reject

api:
  host: 0.0.0.0
  port: 8000
  api_key: ""                 # empty = auth disabled

storage:
  sqlite_path: data/signals.db
  batch_size: 100
  retention_days: 30
```

Alarm thresholds are in `config/alarms.json`:
```yaml
alarms:
  EngineRPM:
    critical_high: 7500.0
  BrakePressure:
    critical_high: 180.0
```
