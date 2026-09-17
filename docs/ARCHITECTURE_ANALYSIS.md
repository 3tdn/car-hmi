# Car HMI Source Code Architecture Analysis

> Implementation update (2026-09-17): the API contract below reflects the current code.
> Other design examples and diagrams in this document are historical requirements/analysis,
> not a claim that every component is currently implemented. Alarm processing/storage/routes,
> smoothing, automatic WebSocket snapshots, `signal_config`/`signal_update` frame types, and
> writer token-bucket limiting are not active contracts. Use the [current API reference](api_reference.md)
> and [frontend integration guide](frontend_integration.md) for integration.

**Project:** CAN-HMI Signal API (CarPC)  
**Language:** Python 3.10+  
**Framework:** FastAPI, asyncio, python-can  
**Scope:** Real-time CAN bus signal monitoring, processing, storage, and REST/WebSocket API

---

## Table of Contents

1. [Overall System Architecture](#overall-system-architecture)
2. [Module Breakdown](#module-breakdown)
3. [Component Relationships & Data Flow](#component-relationships--data-flow)
4. [Key Workflows](#key-workflows)
5. [External Interfaces](#external-interfaces)
6. [Data Structures & Class Hierarchies](#data-structures--class-hierarchies)

---

## Overall System Architecture

### High-Level Overview

The CAN-HMI system follows a **layered architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI + WebSocket Layer                     │
│          (REST API routes, WebSocket connections, auth)         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│          Signal Processing Pipeline Layer                        │
│  (RateLimiter, ComputedSignals, AlarmChecker, SignalStore)      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│        CAN Bus Communication Layer                              │
│    (CANReader, CANWriter, parser, database loader)              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│  Storage & Persistence Layer                                    │
│    (SQLite database, repositories, data export)                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│        Physical & Virtual CAN Buses                             │
│    (python-can: socketcan, virtual, kvaser, etc.)               │
└─────────────────────────────────────────────────────────────────┘
```

### Concurrency Model

- **Single event loop**: asyncio-based async/await throughout
- **Dedicated recv thread**: Blocking `bus.recv()` runs in OS thread to minimize overhead (~0.5 µs/frame)
- **Thread-safe queue**: `asyncio.Queue` bridges recv thread → event loop
- **Lock-based synchronization**: `asyncio.Lock` protects shared state (SignalStore, database)

---

## Module Breakdown

### 1. **src/core/** — System Bootstrap & Configuration

#### Core Components

**`config.py`**
- **Purpose**: Pydantic-based configuration schema validation
- **Key Classes**:
  - `CANConfig`: Single CAN bus channel definition (interface, channel, bitrate, database paths)
  - `APIConfig`: REST/WebSocket server settings (host, port, API key, CORS origins)
  - `StorageConfig`: Persistence backend selection and tuning (SQLite, TimescaleDB, InfluxDB)
  - `ProcessorConfig`: Signal pipeline tuning (smoothing window, max rate, queue size)
  - `WriterConfig`: CAN write rate limiting
  - `ShutdownConfig`: Graceful shutdown timeout
  - `LoggingConfig`: Log level, file rotation settings
  - `AppConfig`: Top-level aggregation of all sub-configs

- **Key Methods**:
  - `AppConfig.model_validate()`: Pydantic validation with constraint checking
  - `load_config(path)`: Load and parse JSON config file

- **Validation Rules**:
  - At least one CAN channel required
  - No duplicate channel names
  - API key must be changed from default in production

**`config_manager.py`**
- **Purpose**: Runtime config read/write helpers (non-validation, atomic file updates)
- **Key Functions**:
  - `read_config()`: Load raw JSON from `config/system.json`
  - `write_config(data, path)`: Write config atomically via temp file → `os.replace()`
  - `update_processor_config()`: Patch processor settings (queue size, policy)
  - `read_alarms()` / `write_alarms()`: Manage alarm threshold configurations
  - `merge_dict()`: Recursive config merging for partial updates

- **Design Pattern**: Atomic file writes prevent corruption on unexpected crashes

**`signal_store.py`** — **SignalStore** (In-Memory Signal Repository)
- **Purpose**: Thread-safe, asyncio-compatible in-memory storage for latest signal values
- **Key Data**:
  - `_signals: dict[str, SignalValue]`: Name → (value, status, timestamp, unit)
  - `_subscribers: list[Callable]`: Observer pattern callbacks

- **SignalValue Data Class**:
  ```python
  @dataclass
  class SignalValue:
      value: float
      status: str = "ok"  # "ok" | "warning" | "critical"
      timestamp: float
      unit: str | None
  ```

- **Key Methods**:
  - `async update()`: Update single signal, notify subscribers (O(1) lock)
  - `async bulk_update()`: Update multiple signals in one atomic operation
  - `async get()`: Retrieve signal value asynchronously
  - `get_unit()`: Synchronous unit lookup (safe under CPython GIL)
  - `async get_snapshot()`: Full dict copy for API responses
  - `subscribe()`: Register async/sync callback on signal change
  - `async _notify()`: Invoke all subscribers (handles both async and sync callbacks)

- **Concurrency**: `asyncio.Lock` protects `_signals` dict; unit is immutable after init so can be read sync

**`system_metrics.py`** — **SystemMetrics** (Resource Monitoring)
- **Purpose**: Non-blocking system resource snapshot collection
- **SystemMetrics Data Class**:
  ```python
  @dataclass
  class SystemMetrics:
      # CPU metrics (per-core breakdown, frequency)
      cpu_percent, cpu_percent_per_core, cpu_count_logical, cpu_count_physical
      cpu_freq_current_mhz, cpu_freq_max_mhz
      
      # Process metrics (CarPC app resource usage)
      process_cpu_percent, process_memory_rss_mb, process_memory_vms_mb
      process_memory_percent, process_threads, process_open_files, process_pid
      
      # RAM, Swap, Disk
      ram_total_mb, ram_available_mb, ram_used_mb, ram_percent
      swap_total_mb, swap_used_mb, swap_percent
      disk_total_gb, disk_used_gb, disk_free_gb, disk_percent
      
      # Network I/O counters
      net_bytes_sent, net_bytes_recv, net_packets_sent, net_packets_recv
      
      # Application-specific (queue, heap, GC, asyncio tasks, uptime)
      queue_size, queue_maxsize, queue_usage_percent
      heap_allocated_mb, gc_objects, asyncio_tasks, uptime_seconds
  ```

- **Key Functions**:
  - `collect_system_metrics()`: Snapshot collection using `psutil` (non-blocking, no async overhead)

**`runner.py`** — **AppRunner** (Application Orchestrator)
- **Purpose**: Bootstrap entire system, coordinate component startup in correct order
- **Key Lifecycle**:
  1. Setup logging (rotating file handler + console)
  2. Load CAN databases (DBC/A2L → can.json → `DatabaseLoader`)
  3. Initialize SQLite database and schema
  4. Create CAN bus instances for each channel
  5. Start CAN readers (decode frames → queue)
  6. Start CAN writers (encode signals → bus)
  7. Build signal processing pipeline (stages: rate limit → computed → alarms)
  8. Optionally start CAN simulator (dev/test mode)
  9. Launch FastAPI/Uvicorn server (REST + WebSocket)
  10. Install watchdog (health monitoring)
  11. Register signal handlers (SIGINT, SIGTERM) for graceful shutdown

- **Key Methods**:
  - `async start()`: Main orchestration coroutine
  - `async _init_components()`: Detailed component initialization
  - `async shutdown()`: Graceful teardown (flush buffers, close files, wait for tasks)
  - `async _load_alarm_configs()`: Parse alarm thresholds from config
  - `async _on_alarm()`: Alarm event handler (record to DB, broadcast via WS)

- **Coordination Pattern**: Uses `asyncio.gather()` to run all components concurrently; detects task failures and triggers shutdown

---

### 2. **src/can_io/** — CAN Bus Communication Layer

#### `parser.py` — **DatabaseLoader** (CAN Signal Definition Loader)

- **Purpose**: Load signal/message definitions from `can.json`, encode/decode CAN frames
- **Key Data Classes**:
  ```python
  @dataclass
  class ParsedSignal:
      name: str
      start_bit: int
      length: int
      is_signed: bool
      byte_order: str  # "little_endian" | "big_endian"
      factor: float
      offset: float
      unit: str
      minimum: float | None
      maximum: float | None
      description: str
      db_source: str
      receivers: list[str]
  
  @dataclass
  class ParsedMessage:
      msg_id: int
      name: str
      dlc: int
      senders: list[str]
      signals: dict[str, ParsedSignal]
      db_source: str
      cycle_ms: int | None
  ```

- **Key Functions** (Bit-level encode/decode):
  - `_extract_bits(data, start_bit, length, is_signed, big_endian)`: Extract raw value from byte array
  - `_insert_bits(data, raw_int, start_bit, length, is_signed, big_endian)`: Insert raw value into byte array
  - `decode_frame_from_msg(msg, data)`: CAN bytes → dict[signal_name: physical_value]
  - `encode_frame_from_msg(msg, signals)`: dict[signal_name: physical_value] → CAN bytes
  - Formula applied: `physical_value = raw_value * factor + offset`

- **DatabaseLoader Class**:
  - `_messages: dict[int, ParsedMessage]`: Message ID → parsed definition
  - `_signals: dict[str, ParsedSignal]`: Signal name → parsed definition
  - `_signal_to_msg: dict[str, int]`: Signal name → message ID (fast lookup)
  - `_loaded_files: list[str]`: Track which files have been loaded
  - `load_dbc(path)`: Parse a runtime DBC file through `cantools`; `load(path)` remains for legacy JSON compatibility
  - `encode_signal(name, value)`: Find signal's message, encode to `can.Message`
  - `encode_message(msg_id, signals)`: Encode multiple signals into message
  - `decode_message(msg_id, data)`: Decode byte frame to signal dict
  - `summary()`: Human-readable info string (count of messages/signals)

- **Auto-Allocation Logic**:
  - Missing `start_bit`: Finds first unused bit range in message
  - Missing `minimum`/`maximum`: Derives from bit length + signedness + factor

#### `reader.py` — **CANReader** (Async CAN Frame Receiver)

- **Purpose**: Continuously read CAN frames from bus, decode, enqueue for pipeline
- **Key Data Classes**:
  ```python
  @dataclass
  class RawCANFrame:
      timestamp: float
      bus: str
      msg_id: int
      is_extended: bool
      is_fd: bool
      data: bytes
  
  @dataclass
  class DecodedFrame:
      raw: RawCANFrame
      signals: dict[str, float]
      msg_name: str
  ```

- **CANReader Attributes**:
  - `_bus: can.BusABC`: python-can bus instance
  - `_db: DatabaseLoader`: Signal definitions
  - `_queue: asyncio.Queue[DecodedFrame]`: Output queue
  - `_recv_thread: threading.Thread`: Dedicated receiver OS thread
  - `_filter_ids: set[int] | None`: ID whitelist (optional)
  - `_policy: str`: Queue overflow policy ("drop_oldest" | "reject")
  - `_min_interval: float`: Rate gating per message ID (seconds)
  - `_last_enqueue: dict[int, float]`: Per-ID last enqueue timestamp (for rate gating)
  - `_stale_threshold_sec: float`: Maximum allowed age of the latest received frame

- **Key Methods**:
  - `async start()`: Accept frames until stop(), spawn recv thread
  - `_spawn_recv_thread()`: Create dedicated OS thread for `bus.recv()`
  - `_recv_loop()`: Thread entry point—filters, deduplicates, rate-gates, and decodes frames
  - `_submit_frame()`: Coalesces the latest changed signals per CAN ID and permits at most one pending event-loop ingress callback
  - `_enqueue_frame_sync()`: Called in event loop thread—places an already prepared frame onto the bounded queue
  - `_decode()`: Wrapper calling `db.decode_message()`, return `DecodedFrame`
  - `request_reconnect()`: Closes the current receiver safely and starts one reconnect task
  - `async _reconnect()`: Persistent staged reconnect schedule after bus error or stale traffic; awaits a channel callback so the paired writer uses the replacement bus
  - `async stop()`: Signal thread to exit, clean up

- **Performance**:
  - Dedicated recv thread avoids `run_in_executor()` overhead (~10 µs → ~0.5 µs per frame)
  - Deduplicating and coalescing before event-loop scheduling prevents an unbounded callback backlog even when payloads change continuously
  - Rate gating per message ID prevents queue fill-up with high-frequency simulator
  - The pipeline drains a bounded `batch_drain_size` and merges values within each batch, keeping the event loop responsive during a backlog

- **CAN outage recovery**:
  - If traffic is silent from startup or stops later, `reader.stale_threshold_sec` triggers a bus close and reconnect.
  - Reconnect backoff resets only after the replacement bus receives a frame, not merely when its socket opens.
  - Reconnects use 5 fast exponential attempts (`1s` to `16s`), then 10 attempts each at `30s`, `1m`, `2m`, and longer intervals, capped at one retry per hour.
  - While a CAN reader is stale, the `COM_Status_*Can` signals owned by its channel DBC are set to `0`; healthy channels remain unchanged.

#### `writer.py` — **CANWriter** & **CANWriterRouter** (CAN Frame Transmission)

- **Purpose**: Encode signal dict to CAN frames and transmit on bus
- **CANWriter Class**:
  - `_bus: can.BusABC`: Bus instance
  - `_db: DatabaseLoader`: Signal definitions (for encoding)
  - `_lock: asyncio.Lock`: Serialize concurrent writes
  - `_sent_count: int`: Statistics

  - **Key Methods**:
    - `async send_signal(name, value)`: Find signal's message, encode, transmit
    - `async send_message(msg_id, signals)`: Encode multiple signals in message, transmit

- **CANWriterRouter Class** (Router Pattern):
  - `_signal_to_writer: dict[str, CANWriter]`: Signal name → writer (O(1) lookup)
  - `_msgid_to_writer: dict[int, CANWriter]`: Message ID → writer (O(1) lookup)
  - `_writers: list[CANWriter]`: All registered writers (multi-channel support)

  - **Key Methods**:
    - `register(db, writer)`: Map all signals/messages from `db` to this `writer`
    - `async send_signal(name, value)`: Route to correct writer by signal name
    - `async send_message(msg_id, signals)`: Route to correct writer by message ID

- **Design Pattern**: Router abstracts multiple physical CAN buses; REST API calls router without knowing which bus

#### `bus_factory.py` — **Bus Creation Factory**

- **Purpose**: Instantiate `can.Bus` from `CANConfig` with appropriate interface
- **Key Functions**:
  - `create_bus(cfg, **kwargs)`: Return opened `can.Bus` instance
    - Supports: virtual, socketcan, pcan, vector, kvaser, serial, ixxat, …
    - Handles special cases (virtual bus doesn't use bitrate)
    - Logs opening and channel info
  - `create_virtual_bus(channel)`: Convenience helper for virtual bus testing

---

### 3. **src/processor/** — Signal Processing Pipeline

#### `pipeline.py` — **SignalPipeline** (Main Processing Orchestrator)

- **Purpose**: Consume decoded frames from queue, apply processing stages, store results
- **Key Concepts**:
  - **Batch merging**: When queue has multiple frames with same signal ID, merge into one dict (keep newest value only)
  - **Pipeline stages**: Chain of filters applied to each signal batch
  - **Buffered writes**: Accumulate records, flush in batches for efficiency
  - **Dual output**: (1) immediate push to SignalStore, (2) buffered batch to database

- **SignalPipeline Attributes**:
  - `_queue: asyncio.Queue`: Input (decoded frames)
  - `_store: SignalStore`: In-memory signal repository
  - `_repo: ISignalRepository`: Database persistence
  - `_stages: list[ProcessingStage]`: Ordered pipeline stages
  - `_batch_size, _batch_interval_sec`: Flush triggers
  - `_batch_drain_size`: Max frames to drain in one iteration
  - `_buffer: list[tuple[name, value, unit]]`: Accumulator for DB writes
  - `_policy: str`: Queue overflow handling

- **Key Methods**:
  - `add_stage(stage)`: Register processing stage
  - `async start()`: Main event loop—wait for frames, batch-drain, process, flush
  - `async _process_signals()`: Run signals through all stages, update store, buffer for DB
  - `async _flush_buffer()`: Write buffered records to database
  - `async flush()`: Final flush on shutdown
  - `set_input_queue()`: Swap queue (for testing, reconfiguration)

- **Batch-Drain Algorithm**:
  ```
  1. Get first frame from queue (with timeout)
  2. While queue not empty and drained < batch_drain_size:
       - Get frame without blocking
       - Merge signals (newer values overwrite older)
  3. Process merged dict through pipeline
  4. Store result immediately
  5. Buffer for batch flush
  ```
  - **Effect**: High-frequency frames (6000+ fps) reduced from N iterations to 1

#### `filters.py` — **RateLimiter** (Per-Signal Rate Gating)

- **Purpose**: Limit signal update frequency to avoid redundant processing/storage
- **Key Class**:
  ```python
  class RateLimiter(ProcessingStage):
      _min_interval: float  # seconds (1.0 / max_hz)
      _last_update: dict[str, float]  # signal_name → last update timestamp
  ```

- **Logic**:
  - For each signal, check if `(now - last_update) >= min_interval`
  - If yes: pass through, update timestamp; if no: drop

#### `computed.py` — **ComputedSignals** (Virtual Signal Derivation)

- **Purpose**: Calculate derived signals from formulas
- **Example Formula**:
  ```python
  EnginePower_kW = lambda signals: signals['EngineRPM'] * signals['Torque'] / 9549
  ```

- **ComputedSignals Class**:
  - `_formulas: dict[str, Callable[[dict], float]]`: Virtual signal name → computation function
  - `add_formula(name, fn)`: Register formula
  - `async process()`: Apply all formulas, add results to signal dict

#### Historical, removed: `alarms.py` — **AlarmChecker** (Threshold-Based Alarm Detection)

- **Purpose**: Monitor signals for threshold violations, generate alarm events
- **Key Data Classes**:
  ```python
  @dataclass
  class AlarmConfig:
      signal: str
      critical_high: float | None
      warning_high: float | None
      warning_low: float | None
      critical_low: float | None
  
  @dataclass
  class Alarm:
      signal: str
      level: str  # "info" | "warning" | "critical"
      value: float
      threshold: float
      timestamp: float
      description: str
  ```

- **AlarmChecker Attributes**:
  - `_configs: dict[str, AlarmConfig]`: Signal → threshold config
  - `_last_state: dict[str, (level, threshold) | None]`: Track state changes to emit only transitions
  - `_alarm_handlers: list[Callable]`: Async handlers called when alarm state changes

- **Key Methods**:
  - `add_alarm_handler()`: Register callback (e.g., store to DB, broadcast via WS)
  - `async process()`: Check each signal against thresholds, emit Alarm on state transition
  - `_eval_state()`: Determine current alarm state (critical > warning > normal)

- **Design Pattern**: Observer pattern—handlers are called when alarms fire/clear

---

### 4. **src/storage/** — Persistence & Data Export

#### `database.py` — **Database Initialization**

- **Purpose**: Create SQLite schema on first run
- **Schema**:
  ```sql
  signal_log (id, signal_name, value, unit, timestamp)
  Index: (signal_name, timestamp)
  
  alarm_log (id, signal_name, level, value, threshold, description, 
             triggered_at, acknowledged, resolved_at)
  Index: (signal_name, triggered_at)
  
  signal_config (signal_name PK, unit, min_value, max_value, 
                 group_name, widget_type, writable, updated_at)
  ```

- **Key Function**:
  - `async init_db(path)`: Open connection, apply `SCHEMA_SQL`, enable WAL + foreign keys

- **Configuration**:
  - `PRAGMA journal_mode=WAL`: Write-Ahead Logging for better concurrency
  - `PRAGMA foreign_keys=ON`: Enforce referential integrity

#### `repository.py` — **ISignalRepository & SQLiteRepository** (Database Access Layer)

- **Purpose**: Abstract DB operations, implement async SQLite operations
- **Key Data Classes**:
  ```python
  @dataclass
  class SignalRecord:
      signal_name: str
      value: float
      unit: str | None
      timestamp: float
  
  @dataclass
  class AlarmRecord:
      id: int | None
      signal_name: str
      level: str
      value: float
      threshold: float
      description: str
      triggered_at: float
      acknowledged: bool
      resolved_at: float | None
  
  @dataclass
  class SignalConfigRecord:
      signal_name: str
      unit: str | None
      min_value, max_value: float | None
      group_name, widget_type: str | None
      writable: bool
  ```

- **ISignalRepository Interface** (Abstract):
  - Signal CRUD: `insert_signal()`, `insert_signals_bulk()`, `query_signals()`, `delete_old_signals()`, `trim_to_size()`, `vacuum()`
  - Alarm CRUD: `insert_alarm()`, `query_alarms()`, `get_alarm_by_id()`, `acknowledge_alarm()`, `resolve_alarm()`
  - Config: `get_signal_config()`, `upsert_signal_config()`

- **SQLiteRepository Implementation**:
  - `_conn: aiosqlite.Connection`: Async SQLite connection
  - `async insert_signal()`: INSERT single record, commit
  - `async insert_signals_bulk()`: executemany + commit (better than N individual inserts)
  - `async query_signals()`: SELECT with WHERE clauses (signal_name, time range, limit/offset)
  - `async delete_old_signals()`: Remove records older than timestamp (retention)
  - `async trim_to_size()`: Estimate rows to delete based on size, delete in batches to reach target (proactive cleanup)
  - `async vacuum()`: Checkpoint WAL, then VACUUM to reclaim space
  - Retry logic on VACUUM (DB may be busy)

- **Performance Optimization**:
  - Bulk insert: `executemany()` → fewer roundtrips to DB
  - Batch delete: Delete in chunks (5K rows) to avoid long locks
  - WAL mode: Readers don't block writers during checkpoint

#### `exporter.py` — **DataExporter** (Bulk Export to CSV/JSON)

- **Purpose**: Export historical signal/alarm data for offline analysis
- **Key Methods**:
  - `async export_signals_csv()`: Query signals, write CSV (non-blocking via executor)
  - `async export_alarms_json()`: Query alarms, serialize to JSON

- **Design Pattern**: Offload I/O to thread pool to avoid blocking event loop

---

### 5. **src/api/** — REST API & WebSocket Layer

#### `app.py` — **FastAPI Application Factory**

- **Purpose**: Build and configure FastAPI app
- **Key Function**: `create_app(signal_store, repository, can_readers, api_key, cors_origins)`
  - Setup CORS middleware (allow configured origins)
  - Store shared state: `app.state.store`, `app.state.repo`, `app.state.readers`, `app.state.ws_manager`, `app.state.auth`
  - Include routers for all route groups
  - Mount frontend static files (dist/ or frontend/)
  - Setup API key authentication as dependency

#### `auth.py` — **APIKeyAuth** (API Key Authentication)

- **Purpose**: Validate X-API-Key header
- **Key Class**:
  ```python
  class APIKeyAuth:
      _key: str  # expected API key
      
      def verify(key: str | None) -> bool:
          # Return True if auth disabled (empty key) or key matches
          
      async __call__(key: str | None = Security(...)) -> None:
          # FastAPI dependency—raise HTTPException if invalid
  ```

- **Security**: Uses `secrets.compare_digest()` for timing-safe comparison
- **Flexibility**: Auth can be disabled by setting empty key (for local/demo)

#### `models.py` — **Pydantic Data Models** (Request/Response Schemas)

- **Purpose**: Define and validate API request/response payloads
- **Signal Models**:
  - `SignalValueResponse`: Latest value for 1 signal (name, value, unit, timestamp)
  - `SignalListResponse`: Collection of signal values (items, total count)
  - `SignalMetadata`: Full metadata (name, unit, min/max, writable, alarm thresholds, current value/status)
  - `SignalMetadataListResponse`: All available signals with metadata
  - `WriteSignalRequest`: PUT body (value only)
  - `BatchSignalWrite`: POST body (list of signal name/value pairs)

- **Alarm Models**:
  - `AlarmResponse`: Single alarm event (id, signal, level, value, threshold, timestamp, acknowledged, resolved_at)
  - `AlarmListResponse`: Collection of alarms

- **Config Models**:
  - `SignalConfigResponse`: Signal display config (name, unit, min/max, widget type, writable)
  - `UpdateSignalConfigRequest`: Patch request (partial update)
  - `ProcessorConfigResponse`: Processor settings (queue size, policy)
  - `UpdateProcessorConfigRequest`: Patch request

- **System Models**:
  - `HealthResponse`: System health (status, uptime, bus_connected, db_connected)
  - `ReadinessResponse`: Service readiness (ready flag, component details)
  - `SystemMetricsResponse`: Full resource snapshot (CPU, RAM, disk, network, queue, process, uptime)
  - `SystemInfoResponse`: Project info (name, version, uptime, signal count)

#### `websocket.py` — **ConnectionManager & WebSocket Handling**

- **Purpose**: Manage multiple concurrent WebSocket connections, route signal/alarm updates
- **Key Classes**:
  ```python
  enum SubscriptionTopic:
      SIGNALS = "signals"
      ALARMS = "alarms"
      ALL = "all"
  
  @dataclass
  class _ClientSubscription:
      signal_names: set[str]  # "*" = all, or specific signal names
      subscribe_alarms: bool
      subscribe_metrics: bool
      once_channels: set[str]  # signals delivered once then unsubscribed
      min_interval_s: float  # per-connection rate limiting
  
  class ConnectionManager:
      _connections: dict[WebSocket, set[SubscriptionTopic]]  # Legacy topic-based
      _subscriptions: dict[WebSocket, _ClientSubscription]  # New per-signal
      _last_sent: dict[WebSocket, float]  # Rate-limit tracking
      _lock: asyncio.Lock  # Protect concurrent access
  ```

- **Protocol Support**:
  - **Legacy**: Topic-based (`/ws/signals`, `/ws/alarms`, `/ws/all`)
  - **New**: Command-based (`/ws/subscribe` with JSON commands)
    - `{"type": "subscribe", "signals": ["SignalA", "*"], "mode": "continuous"}`
    - `{"type": "unsubscribe", "signals": ["SignalA"]}`
    - `{"type": "subscribe", "signals": ["alarms"], "mode": "continuous"}`
    - `{"type": "subscribe", "signals": ["metrics"]}`

- **Key Methods**:
  - `async connect()`: Accept legacy WebSocket
  - `async connect_subscribe()`: Accept new-protocol WebSocket
  - `async process_subscribe_command()`: Parse and apply subscription changes
  - `async disconnect()`: Clean up connection state
  - `async broadcast_signal()`: Fan-out signal updates to all subscribed connections
  - `async broadcast_alarm()`: Fan-out alarm event
  - `async broadcast_metrics()`: Fan-out system metrics
  - `async _broadcast()`: Internal helper (legacy topics)
  - `async _broadcast_to_subscribers()`: Internal helper (new protocol)
  - Rate limiting: Track last send time per connection, respect `min_interval_s`

- **Broadcasting Format**:
  - Signals: `{"timestamp": "ISO8601", "signals": [{"name": "...", "value": ...}]}`
  - Alarms: `{"type": "alarm", "id": ..., "signal_name": ..., "level": ..., ...}`
  - Metrics: `{"type": "metrics", "cpu_percent": ..., "ram_percent": ...}`

#### `routes/signals.py` — **Signal REST Endpoints & WebSocket**

- **Purpose**: REST CRUD for signals + WebSocket streaming
- **REST Endpoints**:
  - `GET /signals` — List all latest signal values
  - `GET /signals/available` — Full metadata for all signals (loaded from configured DBC files + alarm config)
  - `GET /signals/{signal_name}` — Get latest value for 1 signal
  - `GET /signals/{signal_name}/history` — Query historical values (time range, limit, offset)
  - `PUT /signals/{signal_name}` — Write value to CAN bus (triggers CANWriter)
  - `POST /signals/batch_update` — Write multiple signals atomically

- **WebSocket Endpoints**:
  - `GET /ws/signals` — Legacy topic-based (auto-sub to SIGNALS topic)
  - `GET /ws/alarms` — Legacy topic-based (auto-sub to ALARMS topic)
  - `GET /ws/subscribe` — New command-based protocol
    - Client sends JSON commands to subscribe/unsubscribe
    - Server sends stream of signal/alarm/metrics updates

- **Key Implementations**:
  - `list_signals()`: Snapshot from SignalStore
  - `list_available_signals()`: Merge metadata from:
    - `can.json` (min/max, unit, writable)
    - `system.json` (processor config)
    - `config/alarms.json` (alarm thresholds)
    - SignalStore (current value, status)
  - `get_signal_history()`: Query DB with time range filtering
  - `write_signal()`: Call writer router, return 202 Accepted
  - `batch_update_signals()`: Loop through signals, collect errors, return mixed success/errors

#### Historical, removed: `routes/alarms.py` — **Alarm Management Endpoints**

- **REST Endpoints**:
  - `GET /alarms` — List alarms (filter by signal, level, acknowledged status)
  - `GET /alarms/{alarm_id}` — Get single alarm
  - `POST /alarms/{alarm_id}/acknowledge` — Mark acknowledged
  - `POST /alarms/{alarm_id}/resolve` — Mark resolved

- **Database Operations**: Delegate to repository

#### `routes/config.py` — **Configuration Management Endpoints**

- **REST Endpoints**:
  - `GET /config` — List all signal configurations
  - `GET /config/signal/{signal_name}` — Config for 1 signal
  - `PATCH /config/signal/{signal_name}` — Update widget type, min/max, etc.
  - `GET /config/processor` — Current processor config
  - `GET /config/general` — Full app config (dumped from `AppConfig`)
  - `PATCH /config/general` — Partial update to system.json
  - `POST /config/general/reset` — Reset to defaults
  - `GET /config/alarms` — Get alarm thresholds
  - `POST /config/alarms` — Update alarm thresholds
  - `POST /config/alarms/reset` — Reset to empty defaults

- **Implementation**:
  - Load from disk on GET (no caching to always show current state)
  - Write atomically on PATCH/POST
  - Validate after write using `load_config()`

#### `routes/system.py` — **System Health & Metrics Endpoints**

- **REST Endpoints**:
  - `GET /api/info` — Project info (name, version, uptime, signal count)
  - `GET /api/health` — Health check (status, uptime, bus/db connectivity)
  - `GET /api/ready` — Readiness probe (for Kubernetes/systemd)
  - `GET /api/metrics` — System resource metrics (CPU, RAM, disk, network, process, queue, GC)

- **Implementation**:
  - Poll component state at request time (no caching)
  - Derive status from readers and repository availability
  - Call `collect_system_metrics()` for non-blocking snapshot

#### `routes/adaptive_restraint.py`, `routes/restraints.py`, `routes/profiles.py`
- **Purpose**: Domain-specific endpoints (beyond core signal API)
- **Out of scope for this analysis** (not core to architecture)

---

### 6. **src/can_simulator/** — CAN Message Generator

#### `simulator.py` — **CANSimulator** (Message Generator for Testing)

- **Purpose**: Read `can.json`, generate random signal values, encode/transmit CAN frames for testing
- **Key Data Classes**:
  ```python
  @dataclass
  class _SigDef:
      name, start_bit, length, is_signed, big_endian, factor, offset
      minimum, maximum
  
  @dataclass
  class _MsgDef:
      msg_id, name, dlc
      signals: list[_SigDef]
  ```

- **CANSimulator Attributes**:
  - `_bus: can.BusABC`: Virtual bus to transmit on
  - `_cycle_ms: int`: Period between message sends
  - `_messages: list[_MsgDef]`: All messages to simulate
  - `_running: bool`: Loop control

- **Key Methods**:
  - `_load_can_json()`: Parse can.json, auto-allocate missing start_bit, compute missing min/max
  - `async start()`: Loop—for each message, generate random values, encode, send with cycle delay
  - `stop()`: Set running flag to False

- **Value Generation**:
  - For each signal: `value = random.uniform(minimum, maximum)`
  - Encode using same `_insert_bits()` as CANWriter
  - Transmit via `bus.send()`

- **Auto-Allocation** (same as DatabaseLoader):
  - Missing `start_bit`: Find first unused bit range in message
  - Missing `minimum`/`maximum`: Compute from bit length, signedness, factor, offset

#### `cli.py` — **Simulator CLI Entrypoint**

- **Purpose**: Command-line interface for standalone simulator
- **Usage**: `python -m src.can_simulator.cli --config config/system.json`
- **Implementation**:
  - Load config
  - Create virtual bus
  - Instantiate CANSimulator
  - Call `asyncio.run(sim.start())`

---

## Component Relationships & Data Flow

### Data Flow Diagram (Signal Read Path)

```
┌─────────────────┐
│  CAN Bus        │  Physical or virtual bus with messages
└────────┬────────┘
         │
         │ bus.recv() in dedicated OS thread
         │ (low-latency, no asyncio overhead)
         ▼
┌─────────────────────────────────────────┐
│  CANReader._recv_loop()                │
│  - Receive raw CAN message             │
│  - Copy data (some backends reuse buf) │
│  - Post via call_soon_threadsafe()     │
└────────┬────────────────────────────────┘
         │
         │ event loop thread
         │
         ▼
┌──────────────────────────────────────────┐
│  CANReader._enqueue_sync()              │
│  - Filter by ID (optional)              │
│  - Rate-gate per message ID             │
│  - Decode msg bytes → signals dict      │
│  - Put into asyncio.Queue               │
└────────┬─────────────────────────────────┘
         │
         │ queue (bounded, configurable)
         │
         ▼
┌────────────────────────────────────────────┐
│  SignalPipeline.start()                  │
│  - Wait for frame(s) from queue          │
│  - Batch-drain: merge multiple frames    │
│    (keep newest value per signal)        │
│  - Apply stages:                         │
│    1. RateLimiter                        │
│    2. ComputedSignals                    │
│    3. AlarmChecker (trigger alarms)      │
│  - Update SignalStore (in-memory)        │
│  - Buffer for batch DB write             │
└────────┬─────────────────────────────────┘
         │
         ├────────────────────────────┬──────────────────────┐
         │                            │                      │
         ▼                            ▼                      ▼
┌─────────────────────┐    ┌──────────────────────┐  ┌──────────────────┐
│  SignalStore        │    │  SQLite DB           │  │  WebSocket       │
│  (dict[name →       │    │  (signal_log table)  │  │  ConnectionMgr   │
│   SignalValue])     │    │  - Batch insert      │  │  - Fan-out       │
│  - Immediate update │    │  - Retention task    │  │  - Rate limit    │
│  - Subscribe notify │    │  - Auto trim to size │  │  - Per-client    │
│  - API GET returns  │    │                      │  │    subscription  │
│  - Snapshot copy    │    │                      │  │                  │
└─────────────────────┘    └──────────────────────┘  └──────────────────┘
```

### Data Flow Diagram (Signal Write Path)

```
┌──────────────────────┐
│  REST API Client     │
│  PUT /signals/{name} │
│  value={x}           │
└─────────┬────────────┘
          │
          ▼
┌──────────────────────────────────────┐
│  APIKeyAuth dependency               │
│  (validate X-API-Key header)         │
└─────────┬────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────┐
│  routes/signals.write_signal()          │
│  - Get writer from app state            │
│  - Call writer.send_signal(name, value) │
│  - Return 202 Accepted                  │
└─────────┬────────────────────────────────┘
          │
          ▼
┌────────────────────────────────────────┐
│  CANWriterRouter.send_signal()         │
│  - Lookup signal_name → CANWriter (O1) │
│  - Delegate to correct writer          │
└─────────┬──────────────────────────────┘
          │
          ▼
┌────────────────────────────────────────┐
│  CANWriter.send_signal()               │
│  - Encode signal to message ID         │
│  - Acquire lock (serialize writes)     │
│  - Run bus.send() in executor          │
│  - Log transmission                    │
└─────────┬──────────────────────────────┘
          │
          ▼
┌──────────────────────────┐
│  CAN Bus                 │
│  (message transmitted)   │
└──────────────────────────┘
         │
         │ (frame propagates on bus)
         │ (other ECUs receive and act)
         │
         ▼
     (next cycle)
  Frame echoed back to CarPC reader
  (depends on physical/virtual bus behavior)
```

### Component Communication Matrix

| From → To | Mechanism | Data Type | Async | Notes |
|-----------|-----------|-----------|-------|-------|
| Bus → CANReader | python-can API | `can.Message` | Thread | Dedicated recv thread |
| CANReader → Queue | `queue.put_nowait()` | `DecodedFrame` | Async | Event loop thread |
| Queue → Pipeline | `queue.get()` | `DecodedFrame` | Async | With timeout |
| Pipeline → SignalStore | `store.bulk_update()` | `dict[str, float]` | Async | Lock protected |
| Pipeline → Repository | `repo.insert_signals_bulk()` | `list[SignalRecord]` | Async | Batch write |
| Pipeline → AlarmChecker | `stage.process()` | `dict[str, float]` | Async | Pass-through stage |
| AlarmChecker → Handler | `await handler(alarm)` | `Alarm` | Async | Multiple subscribers |
| Handler → Repository | `repo.insert_alarm()` | `AlarmRecord` | Async | Event recording |
| Handler → WebSocket | `broadcast_alarm()` | JSON | Async | Fan-out |
| REST API → Writer | `writer.send_signal()` | Signal name, value | Async | Async def endpoint |
| Writer → Bus | `bus.send()` in executor | `can.Message` | Async | Non-blocking |
| API Snapshot → Client | HTTP response | JSON | Sync | Snapshot copy |
| WebSocket → Clients | Fan-out broadcast | JSON | Async | Per-connection rate limit |
| SignalStore → Subscribers | Callback invocation | Signal name, value | Both | Supports sync & async |

---

## Key Workflows

### 1. **Application Startup Workflow**

```python
# main.py or entry point
AppRunner(config)
  .start()
    ├─ 1. _setup_logging(cfg)
    │   └─ Create console + file handlers
    │
    ├─ 2. Signal handlers (SIGINT, SIGTERM)
    │   └─ Register graceful shutdown on Ctrl+C
    │
    ├─ 3. _init_components()
    │   ├─ Load CAN databases (can.json)
    │   │   └─ DatabaseLoader for each channel
    │   │
    │   ├─ Seed SignalStore with initial values
    │   │   └─ Set units from signal definitions
    │   │
    │   ├─ Init SQLite DB
    │   │   └─ Create schema (signal_log, alarm_log, signal_config)
    │   │
    │   ├─ Create CAN buses (one per channel)
    │   │
    │   ├─ Start CANReaders (one per bus)
    │   │   └─ Spawn dedicated recv threads
    │   │
    │   ├─ Start CANWriters (one per bus)
    │   │   └─ Setup writer router (for API writes)
    │   │
    │   ├─ Build Signal Pipeline
    │   │   ├─ Add RateLimiter
    │   │   ├─ Add ComputedSignals
    │   │   ├─ Add AlarmChecker (load alarm configs)
    │   │   └─ Start pipeline coroutine
    │   │
    │   ├─ Start CANSimulator (if enabled & no real hardware)
    │   │
    │   ├─ Create FastAPI app
    │   │   ├─ Inject store, repo, readers, ws_manager
    │   │   ├─ Register routers (signals, alarms, config, system, etc.)
    │   │   └─ Include static frontend
    │   │
    │   └─ Start Uvicorn server (REST + WebSocket)
    │
    └─ 4. await asyncio.gather(*tasks)
        └─ Wait for all components (blocking until shutdown)
```

### 2. **Signal Reception & Processing Workflow (Per Frame)**

```
Thread: recv_thread                    Thread: event_loop
─────────────────────────────────────────────────────────────
bus.recv()
  │
  ├─ Get raw CAN message
  │
  ├─ Copy data to avoid buffer reuse issues
  │
  └─ call_soon_threadsafe(_enqueue_sync, msg, arrival_time)
                                   │
                                   ├─> Event loop
                                   │
                                   ▼
                           _enqueue_sync():
                             ├─ Check message ID filter
                             ├─ Rate-gate per ID
                             ├─ Decode frame → signals dict
                             ├─ Create DecodedFrame
                             └─ queue.put_nowait(frame)
                                    │
                                    ├─> Pipeline task
                                    │
                                    ▼
                              Pipeline._queue.get()
                                 (wait with timeout)
                                    │
                                    ├─ Batch-drain remaining frames
                                    │  (merge signals, keep newest)
                                    │
                                    ├─ RateLimiter stage
                                    │  (drop if too frequent)
                                    │
                                    ├─ ComputedSignals stage
                                    │  (calculate derived signals)
                                    │
                                    ├─ AlarmChecker stage
                                    │  (check thresholds, emit Alarm objects)
                                    │  └─ Call alarm handlers
                                    │     ├─ Insert to alarm_log
                                    │     └─ broadcast_alarm(ws_mgr)
                                    │
                                    ├─ await store.bulk_update()
                                    │  (update all signal values atomically)
                                    │
                                    ├─ Signal subscribers called
                                    │  (e.g., auto-broadcast to WS clients)
                                    │
                                    ├─ Buffer signals for batch write
                                    │
                                    └─ Check flush triggers
                                       ├─ If buffer full (>= batch_size)
                                       │  ├─ repo.insert_signals_bulk()
                                       │  └─ Clear buffer
                                       │
                                       └─ Or if interval elapsed
                                          └─ (same)
```

### 3. **WebSocket Signal Push Workflow**

```
SignalStore subscriber triggered
    │
    ├─ Async callback (registered in app startup)
    │
    ▼
ConnectionManager.broadcast_signal(name, value, timestamp)
    │
    ├─ Create JSON payload
    │  {"timestamp": "ISO8601", "signals": [{"name": "...", "value": ...}]}
    │
    ├─ For each connected client
    │  ├─ Check subscription state (_subscriptions dict)
    │  ├─ If subscribed to this signal or "*"
    │  ├─ Check min_interval_s rate limit
    │  └─ ws.send_text(payload)
    │
    └─ Broadcast complete
```

### 4. **Historical, removed: Alarm Trigger Workflow**

```
AlarmChecker.process(signals)
    │
    ├─ For each signal with config
    │
    ├─ Evaluate current state
    │  ├─ critical_high? → ("critical", threshold)
    │  ├─ critical_low?  → ("critical", threshold)
    │  ├─ warning_high?  → ("warning", threshold)
    │  ├─ warning_low?   → ("warning", threshold)
    │  └─ none? → None
    │
    ├─ Compare with last state
    │  └─ If changed (transition)
    │
    ├─ Create Alarm object
    │  {"signal": "...", "level": "...", "value": ..., "threshold": ...}
    │
    ├─ Call all alarm handlers
    │  ├─ Handler 1: Insert to alarm_log
    │  │  └─ repo.insert_alarm(AlarmRecord(...))
    │  │
    │  └─ Handler 2: Broadcast via WebSocket
    │     └─ ConnectionManager.broadcast_alarm(alarm)
    │        ├─ Format JSON
    │        └─ Send to all subscribers (subscribe_alarms=True)
    │
    └─ Update _last_state (prevent re-emit on next frame)
```

### 5. **Signal Write (from REST API) Workflow**

```
PUT /signals/{signal_name}
  {"value": 100.0}
    │
    ├─ Authenticate (APIKeyAuth dep)
    │
    ├─ routes/signals.write_signal()
    │
    ├─ Get writer_router from app state
    │
    ├─ writer_router.send_signal(name, value)
    │  │
    │  └─ O(1) dict lookup: signal_name → CANWriter
    │
    ├─ CANWriter.send_signal(name, value)
    │  │
    │  ├─ db.encode_signal(name, value)
    │  │  └─ Find signal, find message
    │  │  └─ Convert physical_value → raw_value (inverse of factor/offset)
    │  │  └─ Insert bits into bytearray (message DLC)
    │  │  └─ Create can.Message
    │  │
    │  ├─ Acquire asyncio.Lock (serialize writes)
    │  │
    │  ├─ await loop.run_in_executor(None, bus.send, msg)
    │  │  (non-blocking: sends to thread pool)
    │  │
    │  ├─ Release lock
    │  │
    │  └─ Log transmission
    │
    └─ Return 202 Accepted
       {"signal_name": ..., "value": ..., "queued_at": ...}
```

### 6. **Graceful Shutdown Workflow**

```
SIGINT (Ctrl+C) received
    │
    ├─ Event loop detects signal
    │
    ├─ Calls AppRunner.shutdown()
    │
    ├─ Stop all components
    │  ├─ pipeline.stop() (exit event loop)
    │  ├─ reader.stop() for each reader (set running=False, join thread)
    │  ├─ simulator.stop() if active
    │  ├─ Close buses
    │
    ├─ Final cleanup
    │  ├─ pipeline.flush() (write remaining buffered signals)
    │  ├─ repo.vacuum() (reclaim DB space)
    │  ├─ db_conn.close()
    │  └─ Uvicorn shutdown
    │
    └─ All tasks completed → event loop exits
       Process terminates with exit code 0
```

---

## External Interfaces

### 1. **REST API Endpoints**

Use `X-API-Key` on protected HTTP routers, `X-Profile-Name` for explicit profile scope,
and `X-Client-Id` for client sessions and Dev Mode locks. Browser WebSockets use
`?api_key=...&profile_name=...`. `X-Dev-Mode: true` does not bypass API key authentication.
System controls require both a real configured key and Dev Mode. Public routes include
system GET, adaptive restraint, camera, and restraints/video.

HTTP errors can contain string, object, or validation-array `detail`. Successful responses
can contain `warnings`; batch writes return HTTP 202 even when individual signals fail.
Inspect `errors` and per-seat `applied` results instead of checking HTTP status alone.

The current backend has no alarm REST/config/WebSocket routes and no root `/health` or
`/ready` business routes. Use `/system/health` and `/system/ready` (or their `/api` aliases).
These probes return HTTP 200 even when their JSON body reports degraded health or not-ready.

| Method | API | Purpose (from implementation) |
|---|---|---|
| GET | `/signals` | List latest signal values |
| GET | `/signals/available` | List all available signals with metadata |
| GET | `/signals/{signal_name}` | Get latest value for one signal |
| PUT | `/signals/{signal_name}` | Write value to signal (CAN write) |
| GET | `/signals/{signal_name}/history` | Query signal history from DB |
| POST | `/signals/batch_update` | Write multiple writable signals simultaneously (batch) |
| GET | `/config` | List all signal configurations |
| GET | `/config/signal/{signal_name}` | Get config for one signal |
| PATCH | `/config/signal/{signal_name}` | Update signal config |
| GET | `/config/processor` | Get processor config |
| POST | `/config/processor` | Update processor config |
| GET | `/config/system` | Get system config and field update policy |
| PATCH | `/config/system` | Patch system config without dropping unrelated fields |
| GET | `/config/system/backups` | List fixed-path system config backups |
| POST | `/config/system/backups` | Back up system config |
| POST | `/config/system/backups/{backup_id}/restore` | Restore a system config backup |
| POST | `/config/system/reset` | Reset system config from the fixed project template |
| POST | `/config/system/reload` | Re-apply live fields from the system config file |
| GET | `/config/general` | Get full application config |
| PATCH | `/config/general` | Patch application config (partial) |
| POST | `/config/general/reset` | Reset application config to defaults |
| GET | `/adaptive_restraint/available` | Get all available options for adaptive restraint filters |
| GET | `/adaptive_restraint/chart_info` | Get statistic and chart information for adaptive restraint systems |
| GET | `/system/info` | Get project & system information |
| GET | `/system/health` | Health check |
| GET | `/system/ready` | Readiness probe (for container/systemd) |
| GET | `/system/metrics` | CarPC resource information (CPU, RAM, disk, queue, heap…) |
| POST | `/system/can/retry` | Retry CAN connections |
| POST | `/system/reboot` | Reboot Car-HMI service |
| GET | `/api/restraints/match` | Find best-matching restraint video for crash conditions |
| GET | `/api/restraints/video/{filename}` | Stream a video file from the media directory |
| GET | `/api/camera/stream` | Proxy live MJPEG stream from the vehicle camera |
| GET | `/api/camera/status` | Camera stream proxy status |
| GET | `/api/devmode/catalog` | Dev Mode signal families and selectable states |
| GET | `/api/devmode/status` | Current Dev Mode seat locks |
| POST | `/api/devmode/seats/select` | Select seats for Dev Mode (locks other sections out) |
| POST | `/api/devmode/exit` | Leave Dev Mode and release all seat locks of this section |
| POST | `/api/devmode/signals` | Apply one signal family to several seats at once |
| GET | `/api/info` | Get project & system information |
| GET | `/api/health` | Health check |
| GET | `/api/ready` | Readiness probe (for container/systemd) |
| GET | `/api/metrics` | CarPC resource information (CPU, RAM, disk, queue, heap…) |
| POST | `/api/can/retry` | Retry CAN connections |
| POST | `/api/reboot` | Reboot Car-HMI service |
| GET | `/api/profiles` | List all profiles |
| GET | `/api/profile/sessions` | List client active-profile sessions |
| POST | `/api/profile/heartbeat` | Heartbeat for client profile session |
| POST | `/api/profile/offline` | Mark client profile session offline |
| GET | `/api/profile` | Get profile by name (or active profile) |
| POST | `/api/profile` | Create new profile |
| PUT | `/api/profile` | Update profile (optimistic lock) |
| PUT | `/api/profile/active` | Set active profile |
| DELETE | `/api/profile/{name}` | Delete profile |

### 2. **WebSocket Endpoints**

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

See the [complete reference](api_reference.md) and [frontend integration guide](frontend_integration.md) for formats, errors, and lifecycle examples.

### 3. **Configuration Files**

**`config/system.json`** (AppConfig)
- CAN bus definitions (interface, channel, bitrate, DB paths)
- API server settings (host, port, API key, CORS)
- Storage config (backend, SQLite path, batch size, retention)
- Processor settings (queue size, max rate, smoothing)
- Writer config (rate limit per second, burst)
- Logging config (level, file path, rotation)

**`db/can_db/*.dbc`** (CAN Database)
- The DBC file referenced by each `can[].can_db_file` defines messages (ID, name, DLC, cycle time)
- It also defines signals (name, start bit, length, sign, byte order, factor, offset, unit, min/max)

**`config/alarms.json`** (Alarm Thresholds)
- Per-signal alarm thresholds (warning_high/low, critical_high/low)

### 4. **Database Schema**

**SQLite Tables**:

- `signal_log`: Historical signal values
  - `id`, `signal_name`, `value`, `unit`, `timestamp`
  - Index: `(signal_name, timestamp)`

- `alarm_log`: Alarm events
  - `id`, `signal_name`, `level`, `value`, `threshold`, `description`, `triggered_at`, `acknowledged`, `resolved_at`
  - Index: `(signal_name, triggered_at)`

- `signal_config`: Display metadata
  - `signal_name` (PK), `unit`, `min_value`, `max_value`, `group_name`, `widget_type`, `writable`, `updated_at`

### 5. **CAN Bus Interfaces (python-can)**

- Virtual: `interface=virtual, channel=vcan0` (for testing, simulation)
- SocketCAN: `interface=socketcan, channel=can0` (Linux kernel, real hardware)
- KVASER: `interface=kvaser, channel=0` (hardware)
- PCAN: `interface=pcan` (Windows hardware)
- Vector: `interface=vector` (professional)
- Serial: `interface=serial, port=/dev/ttyUSB0` (CAN-to-USB adapters)

---

## Data Structures & Class Hierarchies

### Core Data Classes

```
SignalValue (dataclass)
├─ value: float
├─ status: str  ("ok", "warning", "critical")
├─ timestamp: float
└─ unit: str | None

ParsedSignal (dataclass)
├─ name: str
├─ start_bit: int
├─ length: int
├─ is_signed: bool
├─ byte_order: str
├─ factor: float
├─ offset: float
├─ unit: str
├─ minimum: float | None
├─ maximum: float | None
├─ description: str
├─ db_source: str
└─ receivers: list[str]

ParsedMessage (dataclass)
├─ msg_id: int
├─ name: str
├─ dlc: int
├─ senders: list[str]
├─ signals: dict[str, ParsedSignal]
├─ db_source: str
└─ cycle_ms: int | None

RawCANFrame (dataclass)
├─ timestamp: float
├─ bus: str
├─ msg_id: int
├─ is_extended: bool
├─ is_fd: bool
└─ data: bytes

DecodedFrame (dataclass)
├─ raw: RawCANFrame
├─ signals: dict[str, float]
└─ msg_name: str

SignalRecord (dataclass)
├─ signal_name: str
├─ value: float
├─ unit: str | None
└─ timestamp: float

AlarmRecord (dataclass)
├─ id: int | None
├─ signal_name: str
├─ level: str  ("info", "warning", "critical")
├─ value: float
├─ threshold: float
├─ description: str
├─ triggered_at: float
├─ acknowledged: bool
└─ resolved_at: float | None

Alarm (dataclass)
├─ signal: str
├─ level: str
├─ value: float
├─ threshold: float
├─ timestamp: float
└─ description: str

SystemMetrics (dataclass)
├─ timestamp: float
├─ cpu_percent: float
├─ cpu_percent_per_core: list[float]
├─ ... (30+ fields for CPU, process, RAM, disk, network, queue, heap, GC)
└─ uptime_seconds: float

SignalConfigRecord (dataclass)
├─ signal_name: str
├─ unit: str | None
├─ min_value: float | None
├─ max_value: float | None
├─ group_name: str | None
├─ widget_type: str | None
└─ writable: bool

AlarmConfig (dataclass)
├─ signal: str
├─ critical_high: float | None
├─ warning_high: float | None
├─ warning_low: float | None
└─ critical_low: float | None
```

### Class Hierarchies

```
ProcessingStage (ABC)
├─ async process(signals: dict[str, float]) → dict[str, float]
│
├─ RateLimiter
│  └─ Drops signals if last update < min_interval ago
│
├─ ComputedSignals
│  └─ Applies formulas to derive virtual signals
│
└─ AlarmChecker
   └─ Detects threshold violations, emits Alarm objects

ISignalRepository (ABC)
├─ async insert_signal(record: SignalRecord) → None
├─ async insert_signals_bulk(records: list[SignalRecord]) → None
├─ async query_signals(...) → list[SignalRecord]
├─ async insert_alarm(...) → int
├─ async query_alarms(...) → list[AlarmRecord]
├─ async delete_old_signals(older_than: float) → int
├─ async trim_to_size(current_size, max_bytes) → int
├─ async vacuum() → None
├─ async get_signal_config(...) → SignalConfigRecord | None
└─ async upsert_signal_config(...) → None
    │
    └─ SQLiteRepository
       └─ Uses aiosqlite.Connection for async DB ops
```

### Configuration Hierarchy

```
AppConfig (BaseModel)
├─ can: list[CANConfig]
│  ├─ interface: str (virtual, socketcan, kvaser, …)
│  ├─ channel: str (vcan0, can0, /dev/…)
│  ├─ bitrate: int
│  └─ can_db_file: str (DBC path, read directly via cantools)
│
├─ simulator: SimulatorConfig
│  ├─ enabled: bool
│  ├─ default_cycle_ms: int
│  └─ can_db_file: str (DBC path)
│
├─ api: APIConfig
│  ├─ host: str
│  ├─ port: int
│  ├─ api_key: str
│  ├─ ws_heartbeat_interval_sec: float
│  ├─ ws_metrics_interval_sec: float
│  └─ cors_origins: list[str]
│
├─ storage: StorageConfig
│  ├─ engine: str (sqlite, timescaledb, influxdb)
│  ├─ sqlite_path: str
│  ├─ batch_size: int
│  ├─ batch_interval_sec: float
│  ├─ retention_days: int
│  └─ max_disk_mb: int
│
├─ processor: ProcessorConfig
│  ├─ smoothing_window: int
│  ├─ max_update_rate_hz: float
│  ├─ max_queue_size: int
│  ├─ queue_policy: str (drop_oldest, reject)
│  └─ batch_drain_size: int
│
├─ writer: WriterConfig
│  ├─ rate_limit_per_sec: int
│  └─ burst: int
│
├─ shutdown: ShutdownConfig
│  └─ timeout_sec: int
│
├─ supervisor: SupervisorConfig
│  └─ watchdog_interval_sec: int
│
└─ logging: LoggingConfig
   ├─ level: str (DEBUG, INFO, WARNING, ERROR)
   ├─ file_path: str
   ├─ max_size_mb: int
   └─ backup_count: int
```

---

## Key Architectural Patterns

### 1. **Observer/Pub-Sub** (SignalStore Subscribers)
- Components subscribe to signal value changes
- When update occurs, all subscribers notified (can be sync or async)
- Used for WebSocket broadcasting, external handlers

### 2. **Pipeline/Chain of Responsibility** (Signal Processing)
- Stages execute in order, each transforms signal dict
- Each stage independent, can be added/removed
- Enables composability (filters, computed, alarms, etc.)

### 3. **Router Pattern** (CANWriterRouter)
- Abstracts multiple buses behind single interface
- Signal name or message ID → correct bus lookup (O(1))
- Hides complexity of multi-channel systems

### 4. **Factory Pattern** (Database Loader, Bus Factory, App Factory)
- Decouple object creation from usage
- `DatabaseLoader` loads and parses CAN definitions
- `create_bus()` creates platform-specific bus instance
- `create_app()` builds FastAPI app with dependencies

### 5. **Repository Pattern** (ISignalRepository)
- Abstract data access layer (SQL details hidden)
- Swap implementations (SQLite ↔ TimescaleDB ↔ InfluxDB)
- Enable testing with mock repositories

### 6. **Command Pattern** (WebSocket Commands)
- Client sends JSON commands (`{"type": "subscribe", …}`)
- Server parses and executes (process_subscribe_command)
- Enables extensibility (add new command types)

### 7. **Batch Processing** (Pipeline Buffer + Drain)
- Accumulate work before expensive operation
- Reduce lock contention, I/O overhead
- Configurable batch size and interval

### 8. **Rate Limiting** (RateLimiter Stage, Writer Burst Bucket)
- Per-signal min interval enforcement
- Per-writer token bucket for outgoing traffic
- Prevents bus saturation and resource exhaustion

### 9. **Graceful Degradation**
- Simulator auto-disables if real hardware detected
- Missing DB fields auto-filled (start_bit, min/max)
- Auth can be disabled (empty key)
- Partial startup if component fails (logged, not fatal)

### 10. **Async/Await + Thread Safety**
- Single event loop (no thread pool needed except I/O)
- Dedicated recv thread for blocking `bus.recv()`
- `asyncio.Lock` protects shared state
- `call_soon_threadsafe()` bridges thread ↔ event loop

---

## Performance Considerations

### Optimizations

1. **Dedicated Recv Thread**: Eliminates asyncio overhead (~10 µs → ~0.5 µs per frame)
2. **Batch Processing**: Merge N frames → 1 iteration (reduces pipeline overhead)
3. **Bulk DB Inserts**: `executemany()` vs. N individual inserts
4. **In-Memory SignalStore**: O(1) signal lookups, zero disk I/O for current values
5. **Per-ID Rate Gating**: Prevents queue fill-up with high-frequency simulator
6. **Synchronous Unit Lookup**: No asyncio.Lock for immutable field (GIL-safe in CPython)
7. **WAL Mode DB**: Readers don't block writers; concurrent access
8. **Connection Pooling**: aiosqlite reuses connection (no per-query overhead)

### Scalability

- **Queue Size**: Configurable (default 10,000 frames); auto-drop or auto-trim when full
- **Batch Size**: Configurable (default 100 records); larger = fewer DB roundtrips
- **Rate Limiting**: Per-signal max frequency (default 10 Hz); prevents redundant processing
- **Multi-Channel Support**: Multiple buses, each with dedicated reader thread
- **Database Retention**: Auto-trim oldest records when size exceeds max (configurable, default 2 GB)

---

## Testing & Simulation

### CAN Simulator

- **Inputs**: can.json signal definitions
- **Output**: Random values in [minimum, maximum] range
- **Usage**: Dev/test environment without real hardware
- **Features**:
  - Auto-allocates missing start_bit
  - Auto-computes missing min/max
  - Respects cycle time (configurable, default 50 ms)
  - Encodes using same encoder as CANWriter

### Test Hooks

- **Virtual Bus**: Use `interface=virtual` for in-process testing
- **Mock Repository**: Replace SQLiteRepository with test double
- **Mock SignalStore**: Inject test subscriber to verify callbacks
- **Rate Limiting**: Override intervals for fast testing
- **Batch Size**: Set to 1 for immediate DB writes (no buffering)

---

## Security & Reliability

### Security

1. **API Key Authentication**: X-API-Key header, timing-safe comparison
2. **CORS Configuration**: Whitelist allowed origins
3. **Input Validation**: Pydantic models enforce schema
4. **SQL Injection Prevention**: aiosqlite parameterized queries
5. **Rate Limiting**: Per-signal, per-client throttling

### Reliability

1. **Graceful Shutdown**: Signal handlers, await pending tasks, flush buffers
2. **Watchdog Monitoring**: Detect dead tasks, attempt recovery
3. **Exponential Backoff**: Reconnect to bus with increasing delays (max 30 s)
4. **Error Logging**: All exceptions caught, logged, don't crash system
5. **Atomic Config Writes**: Temp file + `os.replace()` prevents corruption
6. **Database Retention**: Auto-cleanup of old records, VACUUM to reclaim space
7. **Health Endpoints**: `/api/health`, `/api/ready` for orchestration probes

---

## Summary Table

| Component | Language | Purpose | Concurrency | Key Classes |
|-----------|----------|---------|-------------|------------|
| **core** | Python | Bootstrap, config, orchestration | Async/threading | AppRunner, SignalStore, AppConfig |
| **can_io** | Python | CAN frame I/O, encode/decode | Async + OS thread | CANReader, CANWriter, DatabaseLoader |
| **processor** | Python | Signal processing pipeline | Async | SignalPipeline, RateLimiter, AlarmChecker |
| **storage** | Python | Persistence, data export | Async | SQLiteRepository, DatabaseLoader |
| **api** | Python | REST/WebSocket endpoints | Async | FastAPI app, ConnectionManager, routes |
| **can_simulator** | Python | Message generation for testing | Async | CANSimulator |

---

**End of Architecture Analysis**
