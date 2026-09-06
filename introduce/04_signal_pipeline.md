# 04 — Data Flow & Signal Pipeline

> Detailed description of each step as data moves from CAN Bus → WebSocket client  
> Version: 0.8.0

---

## 1. End-to-end data flow overview

```
[Vehicle ECU / Simulator]          [Vehicle ECU / Simulator]
         │  Channel 0 (vcan0)                │  Channel 1 (vcan1)
         │  CAN frame                        │  CAN frame
         ▼                                   ▼
┌──────────────────────────┐  ┌──────────────────────────┐
│  CANReader #0            │  │  CANReader #1            │
│  (can_io/reader.py)      │  │  (can_io/reader.py)      │
│  decode via DB Loader #0 │  │  decode via DB Loader #1 │
└────────────┬─────────────┘  └────────────┬─────────────┘
             │                              │
             └──────────┬───────────────────┘
                        │  shared asyncio.Queue[DecodedFrame]
                        │  (maxsize=10 000, backpressure: drop_oldest)
                        ▼
┌─────────────────────────────────────────────┐
│  SignalPipeline (processor/pipeline.py)     │
│  ─────────────────────────────────────────  │
│  Stage 1: SmoothingFilter                  │
│  Stage 2: RateLimiter                      │
│  Stage 3: ComputedSignals                  │
│  Stage 4: AlarmChecker                     │
└────────┬─────────────────────┬─────────────┘
         │                     │
         ▼                     ▼
┌──────────────────┐  ┌────────────────────────┐
│  SignalStore     │  │  SQLiteRepository       │
│  (in-memory)     │  │  (storage/repository.py)│
│  dict[str,       │  │                        │
│    SignalValue]  │  │  signal_log table       │
│                  │  │  (batch insert)         │
│  Observer/PubSub │  │                        │
└────────┬─────────┘  └────────────────────────┘
         │ notify subscribers
         ▼
┌─────────────────────────────────────────────┐
│  ConnectionManager (api/websocket.py)       │
│  broadcast_signal() / broadcast_alarm()     │
└────────┬────────────────────────────────────┘
         │  JSON over WebSocket
         ▼
[Web Dashboard / Client]
```

> **Multi-channel**: The system supports N CAN channels in parallel. Each channel has
> its own DatabaseLoader & CANReader, and all of them feed into one shared Queue.
> `CANWriterRouter` ensures write commands go to the correct channel (O(1) lookup).

---

## 2. CAN Frame Decode

### Real example

**can.json** (`config/can.json`):
```json
{
  "messages": {
    "VCU_Status": {
      "id": 256,
      "dlc": 8,
      "signals": {
        "VehicleSpeed": {"start_bit": 0, "size": 16, "factor": 0.01, "offset": 0, "unit": "km/h"},
        "EngineRPM": {"start_bit": 16, "size": 16, "factor": 0.125, "offset": 0, "unit": "rpm"}
      }
    }
  }
}
```

**Received CAN frame**:
```
Message ID: 0x100 (256)
Data:       [0x1A, 0x15, 0xC8, 0x13, ...]
```

**After decode**:
```python
{
    "VehicleSpeed": 84.1,    # 0x151A * 0.01 = 54.02... (illustrative example)
    "EngineRPM": 2500.0
}
```

### DatabaseLoader — Load can.json

```
load("config/can.json")
       │
       ▼
Parse JSON → build message/signal dicts:
  Built-in bit extraction / insertion
  Auto start_bit allocation (if null)
  Auto min/max calculation
       │
       ▼
_messages: dict[int, ParsedMessage]   ← lookup by msg_id
_signals:  dict[str, ParsedSignal]    ← lookup by name
_signal_to_msg: dict[str, int]        ← reverse index
```

---

## 3. Signal Processing Pipeline — Details of the 4 stages

### Stage 1: SmoothingFilter

**Purpose**: Smooth analog signal noise (sensors, ADC).

**Algorithm**: Sliding window (Moving Average) or EMA (Exponential Moving Average).
EMA uses a fixed alpha = 2/(window+1), ensuring consistency regardless of position in the series.

```
Input:  [84.1, 85.3, 83.8, 86.0, 84.5]   (5 most recent values)
Output: 84.74                              (average)
```

**Config** (`system.json`):
```json
{
  "processor": {
    "smoothing_window": 5
  }
}
```

---

### Stage 2: RateLimiter

**Purpose**: Prevent rapidly changing signals from spamming WebSocket and DB. The ECU may send the same message ID every 10 ms/frame, but the frontend only needs 50 ms/frame.

```
last_update["VehicleSpeed"] = T
frame arrives at T + 5ms  → Δt = 5ms < 50ms → DROP
frame arrives at T + 60ms → Δt = 60ms > 50ms → PASS
```

**Config**:
```json
{
  "processor": {
    "max_update_rate_hz": 20
  }
}
```

---

### Stage 3: ComputedSignals

**Purpose**: Calculate derived (virtual) signals that are not available directly on the bus.

**Example formulas**:
```python
# Engine Power (kW)
power_kw = engine_rpm * torque_nm / 9549.0

# Battery Power  
battery_power = battery_voltage * battery_current / 1000.0
```

The formulas are registered through:
```python
computed.add_formula("EnginePower", lambda s: s.get("EngineRPM", 0) * s.get("ActualTorque", 0) / 9549.0)
```

---

### Stage 4: AlarmChecker

**Purpose**: Detect signals that exceed thresholds and trigger alarms.

**Thresholds** (`config/alarms.json`):
```json
{
  "alarms": {
    "EngineRPM": { "critical_high": 7500.0 },
    "BrakePressure": { "critical_high": 180.0 }
  }
}

value >= critical_high  →  status = "critical",  level = "critical"
value >= warning_high   →  status = "warning",   level = "warning"
value <= critical_low   →  status = "critical",  level = "critical"
value <= warning_low    →  status = "warning",   level = "warning"
otherwise               →  status = "ok"
```

**When an alarm is triggered**:
1. `AlarmChecker` emits an `Alarm` event through a handler callback
2. The handler (`AppRunner._on_alarm`) runs:
   - INSERT into the `alarm_log` table in SQLite
   - `ConnectionManager.broadcast_alarm()` → push JSON over WebSocket to all subscribers of the `alarms` channel

---

## 4. Backpressure — Queue Policy

When the pipeline processes more slowly than the CAN reader produces (e.g. CPU busy, DB slow):

| Policy | Behavior |
|---|---|
| `drop_oldest` | Remove the oldest frame in the queue, add the new frame (default) |
| `block` | CANReader blocks until the queue has space |
| `reject` | Drop the new frame, log a warning |

Change live (no app restart):
```
POST /config/processor
{"queue_policy": "drop_oldest", "max_queue_size": 5000}
```

---

## 5. Storage — Batch Insert

To avoid writing to the DB too often (once per signal update):

```
Signal updates → Buffer list[SignalRecord]
                      │
              Buffer full (batch_size=100)
                   or
              Timer tick (batch_interval_sec=2.0)
                      │
                      ▼
              SQLite batch INSERT
              (1 transaction: BEGIN/COMMIT/ROLLBACK)
```

**Config**:
```yaml
storage:
  batch_size: 100
  batch_interval_sec: 2.0
```

---

## 6. WebSocket — Signal Broadcast Flow

```
SignalStore.update("VehicleSpeed", 84.1)
        │
        ▼ (Observer notify)
ConnectionManager._broadcast_signal("VehicleSpeed", 84.1, timestamp)
        │
        ├── Legacy /ws/signals clients → send JSON
        │
        └── Subscribe /ws/subscribe clients:
               for each client:
                 if client.wants_signal("VehicleSpeed"):  # or "*"
                   if rate_ok (min_interval_s):
                     await ws.send_text(payload)
```

**Subscriber protocol** (`/ws/subscribe`):

```
Client → Subscribe: {"action":"subscribe","channels":["VehicleSpeed","alarms"],"rate_ms":100}
Server → Ack:       {"type":"subscribe_ack",...}
Server → Stream:    {"type":"signal","signal":"VehicleSpeed","value":84.1,"timestamp":...}
Server → Stream:    {"type":"alarm","signal_name":"EngineRPM","level":"critical",...}
```

---

## 7. Metrics Push

`AppRunner._metrics_broadcaster()` runs every 3 seconds, collects system metrics via `psutil`, and pushes them to WebSocket clients that subscribed to the `"metrics"` channel:

```json
{
  "type": "metrics",
  "cpu_percent": 12.4,
  "ram_percent": 34.1,
  "queue_size": 42,
  "queue_usage_percent": 0.42,
  "uptime_seconds": 3600.5,
  "asyncio_tasks": 8
}
```

Frontend dev mode shows a real-time metrics panel from this stream.

---

## 8. Startup Sequence

```
can-hmi                    (CLI entry point: src/core/runner.py:main())
    │
    ▼
AppRunner.start()
    ├── _setup_logging()
    ├── DatabaseLoader.load()            ← load config/can.json
    ├── SignalStore.bulk_update()      ← seed all signal names + units
    ├── init_db() / SQLiteRepository  ← create tables if missing
    ├── create_bus()                  ← open CAN interface
    ├── SignalPipeline + stages        ← add 4 stages
    ├── CANReader                     ← async producer
    ├── CANWriter                     ← encode + send
    ├── CANSimulator (if enabled)     ← virtual bus producer
    ├── FastAPI + uvicorn             ← serve REST + WS + static frontend
    ├── Watchdog task                 ← health monitoring
    └── Metrics broadcaster task      ← push metrics over WS
         │
         └── asyncio.gather(*tasks)   ← all run in parallel
```

---

## 9. Graceful Shutdown

When receiving `SIGINT` or `SIGTERM`:

```
AppRunner.shutdown()
    ├── _shutting_down = True         ← stop watchdog + metrics loops
    ├── CANReader.stop()             ← drain queue, close bus
    ├── CANSimulator.stop()          ← if running
    ├── SignalPipeline.flush()       ← flush remaining buffer to DB
    ├── SQLiteRepository.close()     ← close DB connection
    └── FastAPI shutdown             ← close WebSocket connections
```

Default timeout: 10 seconds before forcing exit.
