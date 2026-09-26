# 04 — Data Flow & Signal Pipeline

> Detailed description of each step as data moves from CAN Bus → WebSocket client  
> Version: 0.8.0

---

## 1. End-to-end data flow overview

The diagram below describes the current realtime path. Signal samples are not persisted.


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
│  Stage 1: RateLimiter                      │
│  Stage 2: ComputedSignals                  │
└────────┬────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  SignalStore     │
│  (in-memory)     │
│  dict[str,       │
│    SignalValue]  │
│  Observer/PubSub │
└────────┬─────────┘
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

**Legacy CAN JSON example** (supported for parser compatibility only; runtime uses DBC):
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

### DatabaseLoader — Load DBC (preferred) or can.json (legacy)

```
load_dbc("db/can_db/p_v2.dbc")   # or: load("config/can.json")
       │
       ▼
Parse DBC → build message/signal dicts:
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

## 3. Current Signal Processing Pipeline

The runner installs `RateLimiter` followed by `ComputedSignals`. Both implement asynchronous
`process()` methods. The pipeline drains at most `processor.batch_drain_size` frames per
cycle, keeps the latest value per signal in the batch, and publishes to SignalStore.
`processor.max_update_rate_hz` controls the rate-limiter stage.

No smoothing stage is installed. AlarmChecker, alarm storage, and alarm REST/WebSocket
routes are removed.


## 4. Backpressure — Queue Policy

When the pipeline processes more slowly than the CAN reader produces (for example, CPU busy):

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

## 5. Realtime storage

The latest value of each signal is kept in `SignalStore` and broadcast to subscribers. Signal
samples are not buffered or written to disk. SQLite is used independently for the small
`signal_config` metadata table.

---

## 6. WebSocket — Signal Broadcast Flow

The three registered WebSocket endpoints are `/ws/signals`, `/ws/subscribe` (alias),
and `/ws/all` (legacy automatic broadcast). Use the first two for subscription commands:

```json
{"type":"subscribe","signals":["COM_Status_ElkCan","metrics"],"rate_ms":200,"mode":"continuous"}
```

```json
{"type":"subscribe_ack","action":"subscribe","channels":["COM_Status_ElkCan","metrics"],"count":2,"warnings":[]}
```

```json
{"timestamp":"2026-09-17T00:00:00Z","signals":[{"name":"COM_Status_ElkCan","std_name":"COM_Status_ElkCan","value":1}]}
```

Signal frames have no `type` field. Metrics use `type: "metrics"`. Send
`{"type":"ping"}` for a `pong`, and `{"type":"unsubscribe","signals":["COM_Status_ElkCan"]}`
to unsubscribe. `mode: "once"` waits for the next eligible broadcast; fetch initial values
with REST. `/ws/all` does not handle subscription/ping commands. No alarm channel exists.

See the [API reference](../docs/api_reference.md) and [frontend integration guide](../docs/frontend_integration.md) for authentication, profile scope, reconnect, and cleanup.

## 7. Metrics Push

`AppRunner._metrics_broadcaster()` runs at `api.ws_metrics_interval_sec` (3 seconds in the checked configuration), collects system metrics via `psutil`, and pushes them to WebSocket clients that subscribed to the `"metrics"` channel:

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
    ├── DatabaseLoader.load_dbc()        ← load can[].can_db_file
    ├── SignalStore.bulk_update()      ← seed all signal names + units
    ├── init_db() / SQLiteRepository  ← create tables if missing
    ├── create_bus()                  ← open CAN interface
    ├── SignalPipeline + stages        ← RateLimiter + ComputedSignals
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
    ├── SignalPipeline.stop()        ← stop realtime processing
    ├── SQLite connection close      ← close signal-config DB
    └── FastAPI shutdown             ← close WebSocket connections
```

Default timeout: 10 seconds before forcing exit.
