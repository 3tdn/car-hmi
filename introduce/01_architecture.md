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
| `parser.py` | Load and parse a `.dbc` file (via `cantools`) or the legacy `config/can.json` into `ParsedMessage` / `ParsedSignal`. Decode/encode CAN frames using custom bit manipulation |
| `reader.py` | `CANReader`: async producer, reads frames from the bus → decodes → pushes into `asyncio.Queue`. Supports automatic reconnect and backpressure |
| `writer.py` | `CANWriter`: encode signal value → CAN frame → send to bus. `CANWriterRouter`: route signal writes to the correct CAN channel |

**Multi-channel CAN support**: Each channel (vcan0, vcan1…) has its own `DatabaseLoader`, `CANReader`, and `CANWriter`.
All readers feed into a shared `asyncio.Queue` → centralized pipeline processing.
`CANWriterRouter` routes writable signals to the correct writer/channel in O(1).

**DatabaseLoader** is the core class that loads the CAN database, directly from DBC or from can.json:
```python
db_loader = DatabaseLoader()
db_loader.load_dbc("db/can_db/p_v2.dbc")  # preferred: read directly from DBC
db_loader.load("config/can.json")          # legacy: read from can.json export
frame = db_loader.decode_frame(msg_id, raw_bytes)  # → dict[str, float]
msg   = db_loader.encode_signal("VehicleSpeed", 60.0)  # → can.Message
```

---

### 2.2 `src/processor/` — Signal Processing Pipeline

The runner installs two asynchronous processing stages, `RateLimiter` and `ComputedSignals`.
Frames from the shared queue are drained in bounded batches, coalesced to the latest values,
then published to SignalStore and buffered for SQLite inserts. Smoothing and alarm stages
are not installed.

```text
CAN readers -> bounded queue -> RateLimiter -> ComputedSignals -> SignalStore + SQLite
```

Stages implement `async def process(self, signals: dict[str, float]) -> dict[str, float]`.


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

New databases contain two active tables:

| Table | Purpose |
|---|---|
| `signal_log` | Time series (timestamp, signal_name, value, unit) — indexed |
| `signal_config` | Per-signal display config (unit, min, max, widget_type, writable) |

`DataExporter` is an internal CSV/JSON utility; no REST export route is registered.

---

### 2.5 `src/api/` — FastAPI Backend

`create_app()` injects dependencies through `app.state`. The current API has 54 HTTP
operations (including 6 system aliases) and 3 WebSocket endpoints. Signal, config, profiles,
and Dev Mode routers use configured API key authentication; system GET, camera, adaptive
restraint, and restraints/video routes are public. System retry/reboot require a real key
and `X-Dev-Mode: true`.

WebSocket `/ws/signals` and `/ws/subscribe` share the subscription protocol; `/ws/all` is
legacy automatic broadcasting. Query authentication uses `api_key`, not `token`.
Alarm routes are removed. See the [API index](02_api_reference.md),
[complete reference](../docs/api_reference.md), and [frontend integration guide](../docs/frontend_integration.md).


### 2.6 `src/can_simulator/` — CAN Simulator

Uses `CANSimulator` to read a `.dbc` file directly (via `DatabaseLoader.load_dbc()`) and generate random values in `[minimum, maximum]`:

| Mode | Class | Description |
|---|---|---|
| `can_dbc` | `CANSimulator` | Generate random signals in [min, max] from a DBC file on a fixed cycle |

The simulator uses a dedicated **virtual bus**, isolated from the reader bus (python-can virtual allows multiple instances on the same channel).

---

### 2.7 `src/core/runner.py` — Application Orchestrator

`AppRunner` is the central coordinator that starts the whole system in this order:

1. Setup logging (rotating file + console)
2. Load configured DBC databases once per channel and share each loader with its channel components
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

The diagrams are historical design snapshots and may include removed alarm/smoothing components. The current pipeline and API contract are described above.


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
  can_db_file: "db/can_db/p_v2.dbc"

simulator:
  enabled: true
  default_cycle_ms: 100
  can_db_file: "db/can_db/p_v2.dbc"

processor:
  max_update_rate_hz: 20
  max_queue_size: 10000
  queue_policy: drop_oldest   # drop_oldest / reject

api:
  host: 0.0.0.0
  port: 8000
  api_key: ""                 # empty = auth disabled

storage:
  sqlite_path: data/signals.db
  batch_size: 100
  retention_days: 30
```

The example above is YAML notation for readability; the runtime file is JSON.
See [system configuration management](../docs/system_config_management.md) for actual field
policy and [CAN recovery](../docs/can_recovery.md) for message-based auto channel tracking.
