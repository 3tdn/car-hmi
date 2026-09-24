# CAN-HMI System — Requirement Specification

> Implementation update (2026-09-17): the API contract below reflects the current code.
> Other design examples and diagrams in this document are historical requirements/analysis,
> not a claim that every component is currently implemented. Alarm processing/storage/routes,
> smoothing, automatic WebSocket snapshots, `signal_config`/`signal_update` frame types, and
> writer token-bucket limiting are not active contracts. Use the [current API reference](api_reference.md)
> and [frontend integration guide](frontend_integration.md) for integration.

| Field | Value |
|---|---|
| Document ID | REQ-CANHMI-001 |
| Version | 0.8.0 |
| Date | 2026-03-21 |
| Author | HMI Team |
| Status | Draft |
| Reviewers | — |

### Revision History

| Version | Date | Author | Description |
|---|---|---|---|
| 0.1.0 | 2026-03-01 | HMI Team | Initial draft |
| 0.2.0 | 2026-03-15 | HMI Team | Add concurrency & reliability section (2.8) |
| 0.3.0 | 2026-03-19 | HMI Team | Add alarm_log schema, error taxonomy, deployment guidance, NFR disk/reconnect |
| 0.4.0 | 2026-03-19 | HMI Team | Fix alarm level inconsistency, add /ready + alarm history endpoints, security section, expand testing & frontend |
| 0.5.0 | 2026-03-19 | HMI Team | Add glossary, fix deps (ruff/pytest-cov/locust), complete system.json config, add deployment files to dir tree, config validation approach |
| 0.6.0 | 2026-03-19 | HMI Team | Final round: expand AC (11–16), clarify /health vs /ready vs /status, update running section (dev + prod), align roadmap with new sections |
| 0.7.0 | 2026-03-21 | HMI Team | Sync docs with implemented code: fix API routes (no /api/v1/ prefix), update DB schema, remove unimplemented VehicleStateMachine, add DatabaseLoader/bus_factory/config_manager/RandomCANSimulator, fix alarm config format, update directory structure |
| 0.8.0 | 2026-03-22 | HMI Team | Add subscribe-based WS protocol (/ws/subscribe), GET /signals/available metadata endpoint, per-signal/channel filtering, metrics push, one-shot mode |
| Documentation update | 2026-09-17 | HMI Team | English API inventory and current WS contract; add frontend integration and mark historical designs |
| 0.8.1 | 2026-03-21 | HMI Team | Add frontend modes (dev/user) with client-side whitelist and subscription filtering |

---

## 1. System Overview

The system supports **reading/writing vehicle CAN bus signals**, processing data, storing it, and serving it through **FastAPI + WebSocket** so users can **view data in real time** and **edit parameters online** from the web UI.

**Primary design patterns:**
- **Pipeline** — Signal processing chain (smoothing → alarm check → computed signals)
- **Observer / Pub-Sub** — WebSocket broadcast to multiple clients, signal store notifications
- **Repository** — Storage abstraction (SQLite adapter, can be swapped to TimescaleDB/InfluxDB)
- **Factory** — FastAPI application factory (`create_app()`)
- **Strategy** — CAN database parser (can.json) that loads signal definitions at startup

### Frontend Modes (Dev / User)

- The frontend now supports two client-side modes: `dev` and `user`.
  - `dev` mode subscribes to and displays all available signals via the `/ws/subscribe` protocol or legacy `/ws/all` stream.
  - `user` mode restricts the UI to a curated whitelist of signals. The client subscribes only to the whitelisted channels plus `alarms` and `metrics` to reduce surface area and prevent exposing internal/developer-only signals.

- Mode selection is exposed in the header dropdown and persisted in the browser `localStorage` under `frontend_mode`.

- The whitelist is currently defined client-side (`frontend/js/app.js`) as `USER_SIGNAL_WHITELIST`. Recommended future enhancement: serve the whitelist from the backend (config endpoint) to allow central management.

```
┌──────────────────────┐          ┌──────────────────────────────────────────────────────────┐
│      VEHICLE         │          │                CAR PC (Onboard Compute)                  │
│    (Simulators)      │          │                                                          │
│  ┌──────────────┐    │          │  ┌──────────────┐      ┌──────────────┐                  │
│  │     CAN      │    │  CAN Bus │  │  CAN Driver  │      │    Signal    │                  │
│  │   Simulator  │◄───┼──────────┼─►│   (Socket)   │◄────►│   Processor  │──────┐           │
│  └──────────────┘    │          │  └──────────────┘      └──────────────┘      ▼           │
│                      │          │                               ▲       ┌──────────────┐   │
│                      │          │                               │       │  Data Store  │   │
│  ┌──────────────┐    │  Video   │                               │       │ (SQLite/TS)  │   │
│  │   DMS/OMS    │────┼──────────┼─┐                             ▼       └──────┬───────┘   │
│  │  Simulator   │    │  (RTSP)  │ │  ┌──────────────────────────────┐          │           │
│  └──────────────┘    │          │ └─►│      BACKEND SERVICE         │◄─────────┘           │
└──────────────────────┘          │    │  (FastAPI / GStreamer / WS)  │                      │
                                   │    └──────────────────────────────┘                      │
                                   │                   ▲                                      │
┌──────────────────────┐          │                   │ REST / WebSockets                    │
│      FRONTEND        │          │                   │ (Data & Video Stream)                │
│  ┌──────────────┐    │          │                   │                                      │
│  │   Web Demo   │◄───┼──────────┼───────────────────┘                                      │
│  │  Dashboard   │    │          │                                                          │
│  └──────────────┘    │          │                                                          │
└──────────────────────┘          └──────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Modules and Requirements

### 2.1 CAN Simulator (Vehicle Simulator)

> Simulates vehicle ECUs sending/receiving CAN frames on a virtual CAN bus.

| Item | Requirement |
|---|---|
| Purpose | Emit CAN frames according to CANdb / candb so development/testing can proceed without hardware |
| Protocol | CAN 2.0B, with optional CAN FD extension support |
| CANdb | Load the channel's `can_db_file` DBC definition to encode signal → CAN frame |
| Scenarios | Support scenario files (JSON/YAML) defining time-based signal sequences |
| Speed | Configurable cycle time per message (default 10–100 ms) |
| Interface | `python-can` virtual interface (`virtual`, `socketcan`, `pcan`, `vector`) |
| Noise | Optional added noise/jitter for realistic simulation |
| CLI | `python -m src.can_simulator.cli --config config/system.json --scenario scenarios/city_drive.yaml` |

> The processor, simulator, `GET /signals/available`, and Dev Mode load signal definitions directly from each
> configured `.dbc` file (`can[].can_db_file` or `simulator.can_db_file`) through `DatabaseLoader.load_dbc()`.
> `DatabaseLoader.load()` retains support for legacy CAN JSON files, but they are not part of the runtime configuration.

#### CANdb (candb) format

The system must support reading/writing message/signal definition data in the **CANdb (candb)** format to simplify integration with description data from other tools.

Legacy JSON compatibility example (the runtime source is DBC):

```json
{
  "meta": {
    "name": "vehicle",
    "version": "1.0"
  },
  "messages": {
    "VCU_Status": {
      "id": 256,
      "dlc": 8,
      "signals": {
        "VehicleSpeed": {"start_bit": 0, "length": 16, "factor": 0.01, "offset": 0, "unit": "km/h"},
        "EngineRPM": {"start_bit": 16, "length": 16, "factor": 0.125, "offset": 0, "unit": "rpm"}
      }
    }
  }
}
```

> **Runtime source:** `DatabaseLoader.load_dbc()` reads `.dbc` files directly via `cantools` for CANReader,
> CANWriter, the simulator, `/signals/available`, and Dev Mode. See `src/can_io/parser.py` and
> `src/can_simulator/simulator.py`.

Support requirements:
- Load one DBC file per CAN channel through `can[].can_db_file`.
- The parser returns a common message/signal structure shared by the decoder/encoder.
- Support `start_bit: null` (auto-allocation) and automatic min/max calculation.

**Sample signals that need to be simulated (from the configured DBC):**

| Sample signal | Message (ID) | Unit | Range | Cycle |
|---|---|---|---|---|
| HMI_CrashSeverity | INC_HMI_CrashInfo (128) |  | 0–7 | TBD |
| HMI_CrashImpactTrigger | INC_HMI_CrashInfo (128) |  | 0–1 | TBD |

> **Note:** The full signal list is defined by the configured DBC files. The system loads them at startup.

---

### 2.2 CAN Reader / Writer

> Read CAN frames from the bus, decode them into signals; encode signals and write them back to the bus.

| Item | Requirement |
|---|---|
| Library | `python-can` (CAN I/O) and `cantools` (DBC loading) |
| Bus factory | `bus_factory.py` — Factory that creates CAN bus instances (supports virtual, socketcan, pcan, vector, kvaser, serial) |
| Parser | `parser.py` — `DatabaseLoader` loads DBC files directly, then decodes/encodes CAN frames using the parsed bit layout |
| Decode | Automatically decode frames → signals based on DBC bit layout and factor/offset |
| Encode + Send | Receive signal-change commands → encode → send CAN frame |
| Async | Run non-blocking (`asyncio`) so the main loop is not blocked |
| Filter | Support CAN ID filters (receive only relevant messages) |
| Bus config | Read from the config file (JSON): interface, channel, bitrate. Support multiple CAN channels concurrently |
| Output | Push decoded signal dicts into the internal queue (`asyncio.Queue`) |
| Queue policy | Support 3 policies when the queue is full: `block`, `reject`, `drop_oldest` |
| Reconnect | Automatically reconnect when the bus fails with exponential backoff (1s → 30s), max retries configurable |
| Error handling | Automatically reconnect on bus errors and log warnings |

**Decoded signal structure (after frame decode):**

Standard format with 4 main parts:
1) **Message config** — metadata from the configured DBC
2) **Raw message** — frame received from the CAN bus
3) **Signal configs** — bit layout and factor/offset definitions from the DBC
4) **Decoded signal values** — real values after decoding (`raw × factor + offset`)

```json
{
    // --- 1) Message config (from the configured DBC) ------------------------------
    "message_config": {
        "msg_name": "SBS_WMS_FR_Response",
        "msg_id": 401,
        "dlc": 4,
        "cycle_ms": 20,                                 // if configured in config
        "description": "Engine status message",
        "db": "Interface_Panther_To_CarPC_generated.dbc",                    // or "candb" / "config"
        "src": "PANTHER"                                // source node
    },

    // --- 2) Raw CAN frame (from python-can) ---------------------------------------
    "raw_message": {
        "timestamp": 1742000000.123,                    // epoch seconds (from python-can frame.timestamp)
        "bus": "vcan0",                                 // optional if multi-bus
        "msg_id": 401,
        "is_extended": false,
        "is_fd": false,
        "data": ["0x0C", "0x1E", "..."],          // raw bytes
        "raw_hex": "0C1E..."              // hex string (optional)
    },

    // --- 3) Signal config + decoded values -------------------------------------
    "signal_configs": [
        {
            "msg_id": 401,
            "signal": "WMS_FR_WebbingMovement",
            "start_bit": 0,
            "length": 13,
            "is_signed": false,
            "endianness": "little",
            "factor": 1.0,
            "offset": 0.0,
            "unit": "mm",
            "min": 0,
            "max": 8191,
            "description": "Webbing Payout measured by ACR spool rotation",
            "db": "Interface_Panther_To_CarPC_generated.dbc",
            "dst": ["PANTHER", "CAR_PC"]                    // destination nodes
        },
        {
            "msg_id": 401,
            "signal": "WMS_FR_SpoolAngle",
            "start_bit": 13,
            "length": 14,
            "is_signed": false,
            "endianness": "little",
            "factor": 1,
            "offset": 0,
            "unit": "",
            "min": 0,
            "max": 16383,
            "description": "",
            "db": "Interface_Panther_To_CarPC_generated.dbc",
            "dst": ["PANTHER", "CAR_PC"]                    // destination nodes
        },
        {
            "msg_id": 964,
            "signal": "ElecMotorTemp",
            "start_bit": 28,
            "length": 8,
            "is_signed": true,
            "endianness": "little",
            "factor": 1.0,
            "offset": -40.0,
            "unit": "°C",           // degC
            "min": -40,
            "max": 215,
            "description": "Engine coolant temperature",
            "db": "mercedes_common.dbc",
            "dst": ["IC"]                    // destination node
        }
    ],
    // --- 4) Decoded signal values -----------------------------------------------
    "signals": {
        // value = raw * factor + offset    (min <= value <= max)
        "engine_rpm": 3200.0, 
        "engine_temp": 98.5
    }
}
```

> **Notes:**
> - `message_config` is metadata used for mapping between config and can.json (for example: cycle time, description, data source).
> - `signal_configs` contains information from can.json (factor/offset/unit/bit layout/description), used for encode/decode.
> - `signals` contains the values decoded from `raw_message` based on `signal_configs`.
> - The decoder/encoder must also support CANdb (candb) to keep names, numeric IDs, and calculation configuration consistent.


---

### 2.3 Signal Processor

> Process, filter, and transform raw signals before storing and publishing them.
>
> **Note:** The processor should support multiple CAN channels simultaneously, with each channel having its own can.json file. Signals from all channels are merged into a single processing pipeline.
>
| Item | Requirement |
|---|---|
| Smoothing | Moving average, exponential moving average (configurable window, `method: "moving_avg"` or `"ema"`) |
| Rate limiting | Limit update frequency per signal (for example: max 10 Hz for UI) |
| Alarm / Threshold | Define per-signal alarm thresholds (YAML config), detect state changes (none→warning→critical) |
| Unit conversion | Support unit conversion (raw → engineering value already provided by can.json, plus custom conversions if needed) |
| Computed signals | Support virtual signals computed from multiple other signals (for example: `Power = RPM × Torque / 9549`) — `ComputedSignals` stage with pluggable formulas |
| Output | Processed signal dict → push into the broadcast queue + send to storage |
| Backpressure | When the queue is full: apply `queue_policy` (drop_oldest / block / reject). Queue size is configurable (`max_queue_size`, default 10 000) |
| Pipeline | 4 stages in order: SmoothingFilter → RateLimiter → ComputedSignals → AlarmChecker |
| Batch storage | Buffer signal records, flush when `batch_size` is reached or `batch_interval_sec` elapses |

**Sample alarm threshold config (`config/alarms.json`):**

```yaml
alarms:
  CoolantTemp:
    critical_high: 95
    warning_high: 85
    warning_low: 10
    critical_low: -10
    description: "Coolant temperature thresholds"
  BatteryVoltage:
    critical_high: 15
    warning_high: 13
    warning_low: 10.5
    critical_low: 9.0
    description: "Battery voltage thresholds"
  EngineRPM:
    critical_high: 7500
    warning_high: 7000
    warning_low: null
    critical_low: null
    description: "Engine RPM thresholds"
```

> **Note:** Alarm config format is a dict (key = signal name), not a list. Auto-generated by script `scripts/gen_alarms_from_dbc.py`.

---

### 2.4 Storage

> Store time-series signal data and configuration.

Goals:
- Store signal values over time to support historical queries, charts, and analysis.
- Store signal configuration (min/max, unit, writable) so frontend and backend can share it.
- Support batch insert and retention to avoid I/O overload.

**Configuration (already in `config/system.json`):**

```yaml
storage:
  engine: sqlite              # sqlite | timescaledb | influxdb
  sqlite_path: data/signals.db
  batch_size: 100
  batch_interval_sec: 2
  retention_days: 30
```

| Item | Requirement |
|---|---|
| Default engine | **SQLite** (zero-config, file-based) |
| Extended engine | Can later add adapters for **TimescaleDB** / **InfluxDB** when needed |
| Schema | `signal_log` stores time, msg_name, signal_name, value, unit |
| Config store | `signal_config` stores metadata: min/max/writable/unit/description |
| Batch insert | Buffer + batch insert every N records or every T seconds |
| Retention | Auto-purge data older than N days (configurable) |
| Export | Support CSV/JSON export through the API |

**Schema (SQLite — implemented in `storage/database.py`):**

```sql
CREATE TABLE IF NOT EXISTS signal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_name TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT,
    timestamp   REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_ts ON signal_log(signal_name, timestamp);

CREATE TABLE IF NOT EXISTS alarm_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_name   TEXT NOT NULL,
    level         TEXT NOT NULL CHECK(level IN ('info','warning','critical')),
    value         REAL NOT NULL,
    threshold     REAL NOT NULL,
    description   TEXT,
    triggered_at  REAL NOT NULL,
    acknowledged  INTEGER DEFAULT 0,
    resolved_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_alarm_ts ON alarm_log(signal_name, triggered_at);

CREATE TABLE IF NOT EXISTS signal_config (
    signal_name TEXT PRIMARY KEY,
    unit        TEXT,
    min_value   REAL,
    max_value   REAL,
    group_name  TEXT,
    widget_type TEXT,
    writable    INTEGER DEFAULT 0,
    updated_at  REAL
);
```

> **Implementation suggestions:**
> - The write API pushes data into a queue → a worker uses batch insert to write to SQLite.
> - Every N seconds (or every N records), flush the batch to the DB.
> - Retention: run a periodic job to delete records older than `retention_days`.

---

### 2.5 FastAPI / WebSocket Server

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

#### Registered HTTP operations

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
| DELETE | `/config/system/backups/{backup_id}` | Delete a system config backup |
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

#### Current WebSocket protocol

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

See the [complete reference](api_reference.md) for parameter/schema/error tables and examples.

### 2.6 Web Demo Dashboard (Frontend)

For current REST/WS formats, profile lifecycle, settings policy, error handling, and camera cleanup, see the [frontend integration guide](frontend_integration.md). Client-side modes do not replace backend profile permission checks.


> Web UI that displays real-time signals and allows parameter editing.

| Item | Requirement |
|---|---|
| Tech | HTML + CSS + Vanilla JS (or Vue.js/React as an optional extension) |
| Serve | Static files served by FastAPI (`/static`) |
| Real-time | WebSocket connection receives the signal stream |
| Display | Dashboard divided into widget cards for each signal group |
| Browser support | Chrome/Edge ≥ 120, Firefox ≥ 120. Responsive: desktop (≥ 1024px) + tablet (≥ 768px) |
| Offline / fallback | When the WS connection is lost: show a `Disconnected` banner, keep last-known values (grey out stale values), auto-reconnect |
| Accessibility | Semantic HTML, aria-labels for widgets, keyboard navigation for edit controls, color contrast ≥ 4.5:1 |

**UI components for:**

| Widget | Description |
|---|---|
| **Gauge** | Speedometer, RPM (arc or large number) |
| **Line Chart** | Real-time chart for CoolantTemp, BatteryVoltage (rolling 60s) |
| **Status Panel** | GearPosition, TurnSignal, DoorStatus as icons/badges |
| **Alarm Bar** | Top warning bar, color changes by level |
| **Signal Table** | Table of all signals: name, value, unit, min, max, writable |
| **Edit Control** | Input field + Send button for writable signals (send PUT request → CAN) |
| **History Modal** | Click a signal → popup history chart (fetched from REST API) |
| **System Info** | Bus status, message count, uptime |

**Sample layout:**

```
┌─────────┬──────────┬──────────────────────────────────────┐
│User mode| Dev Mode |                                      │
├─────────┴──────────┴──────────────────────────────────────┤
│  🔴 ALARM BAR: CoolantTemp = 97°C (warning > 95°C)       │
├──────────────┬───────────────┬────────────────────────────┤
│   SPEED      │   RPM         │   BATTERY                  │
│   ◔ 85 km/h  │   ◔ 3200 rpm │   SoC: 78%  V: 48.2V       │
├──────────────┴───────────────┼────────────────────────────┤
│  📈 Real-time Charts        │   STATUS                    │
│  ┌─────────────────────┐    │   Gear: D                   │
│  │ CoolantTemp  ───/   │    │   Turn: OFF                 │
│  │ BattVoltage  ───\   │    │   Doors: All Closed         │
│  └─────────────────────┘    │   State: DRIVING            │
├─────────────────────────────┴─────────────────────────────┤
│  SIGNAL TABLE                              [Export CSV]    │
│  Name           │ Value │ Unit │ Range     │ Action        │
│  VehicleSpeed   │ 85.3  │ km/h │ 0-260    │ [readonly]    │
│  EngineRPM      │ 3200  │ rpm  │ 0-8000   │ [readonly]    │
│  CoolantTemp    │ 97.2  │ °C   │ -40~150  │ [⚠ warning]   │
│  BrakePressure  │ 12.0  │ bar  │ 0-200    │ [✏️ Edit]     │
├───────────────────────────────────────────────────────────┤
│  SYSTEM: Bus=OK │ Msgs: 12,345 │ Uptime: 00:23:45         │
└───────────────────────────────────────────────────────────┘

In Dev Mode, all signals are listed (including those not shown on the dashboard) to make debugging easier. In User Mode, only important signals are shown and supporting/internal signals are hidden. The user can toggle between the two modes using the tab controls in the UI.
```

---

## 2.7 Monitoring (Watchdog / Supervisor / mDNS)

> Provide a monitoring and self-recovery layer for the entire CarPC system.

| Function | Requirement |
|---|---|
| Watchdog | Monitor the main processes (CAN RW, signal processor, FastAPI) and automatically restart them on crash |
| Supervisor | Manage flows and start/stop components: CAN Reader/Writer, Processor, Storage, FastAPI, Video Stream |
| mDNS | Advertise services (FastAPI + WebSocket + video) on the LAN so the frontend can find them easily |
| Health check | Endpoint `/health` returns system status (uptime, process, memory, disk, error) |
| Logging | Structured JSON logs (stdout + file rotation). Levels: DEBUG / INFO / WARNING / ERROR. Config: `logging.level`, `logging.file_path`, `logging.max_size_mb`, `logging.backup_count` |

### 2.7.1 CarPC System Metrics (`/system/metrics`)

> Monitor CarPC system resource information in real time via the API endpoint and frontend dashboard.

**Library:** `psutil>=5.9` (cross-platform system info).

**Endpoint:** `GET /system/metrics` — returns a JSON snapshot of system resources, polled every 3 seconds from the frontend.

| Group | Metric | Description |
|---|---|---|
| **CPU (system)** | `cpu_percent` | Total system CPU % |
| | `cpu_percent_per_core` | CPU % per core |
| | `cpu_count_logical` / `cpu_count_physical` | Number of logical / physical cores |
| | `cpu_freq_current_mhz` / `cpu_freq_max_mhz` | Current / maximum CPU frequency |
| **CPU (process)** | `process_cpu_percent` | CPU % used by the CAN-HMI process |
| **RAM (system)** | `ram_total_mb`, `ram_used_mb`, `ram_available_mb`, `ram_percent` | System RAM |
| **Memory (process)** | `process_memory_rss_mb` | Resident Set Size (actual memory in use) |
| | `process_memory_vms_mb` | Virtual Memory Size |
| | `process_memory_percent` | % of RAM used by the process |
| | `process_threads` | Number of process threads |
| | `process_open_files` | Number of open file descriptors |
| **Swap** | `swap_total_mb`, `swap_used_mb`, `swap_percent` | Swap space |
| **Disk** | `disk_total_gb`, `disk_used_gb`, `disk_free_gb`, `disk_percent` | Working-directory disk usage |
| **Network I/O** | `net_bytes_sent`, `net_bytes_recv` | Total bytes sent/received (cumulative) |
| | `net_packets_sent`, `net_packets_recv` | Total packets sent/received |
| **Signal Queue** | `queue_size`, `queue_maxsize`, `queue_usage_percent` | `asyncio.Queue` status (RX pipeline) |
| **Heap / GC** | `heap_allocated_mb` | Process RSS (~ heap) |
| | `gc_objects` | Number of objects tracked by the garbage collector |
| **Async Tasks** | `asyncio_tasks` | Number of running asyncio tasks |
| **Runtime** | `uptime_seconds` | Running time |
| | `python_version`, `platform` | Python version, operating system |

**Sample response:**

```json
{
  "timestamp": 1742000123.456,
  "cpu_percent": 12.5,
  "cpu_percent_per_core": [8.2, 15.1, 10.3, 16.4],
  "cpu_count_logical": 4,
  "cpu_count_physical": 2,
  "cpu_freq_current_mhz": 2400.0,
  "cpu_freq_max_mhz": 3200.0,
  "process_cpu_percent": 5.3,
  "process_memory_rss_mb": 48.21,
  "process_memory_vms_mb": 128.50,
  "process_memory_percent": 1.48,
  "process_threads": 7,
  "process_open_files": 12,
  "process_pid": 12345,
  "ram_total_mb": 8192.0,
  "ram_available_mb": 4096.0,
  "ram_used_mb": 3800.0,
  "ram_percent": 50.0,
  "swap_total_mb": 4096.0,
  "swap_used_mb": 128.0,
  "swap_percent": 3.1,
  "disk_total_gb": 256.00,
  "disk_used_gb": 120.50,
  "disk_free_gb": 135.50,
  "disk_percent": 47.1,
  "net_bytes_sent": 1234567,
  "net_bytes_recv": 9876543,
  "net_packets_sent": 8901,
  "net_packets_recv": 12345,
  "queue_size": 42,
  "queue_maxsize": 10000,
  "queue_usage_percent": 0.4,
  "heap_allocated_mb": 48.21,
  "gc_objects": 35000,
  "asyncio_tasks": 8,
  "uptime_seconds": 3600.0,
  "python_version": "3.12.3",
  "platform": "Linux-5.15.0-x86_64"
}
```

**Frontend widget:** A `CarPC System Monitor` panel with 12 cards (CPU, Process CPU, RAM, Process Memory, Disk, Swap, Queue, Heap/GC, Network, Async Tasks, Uptime, Platform) using threshold-based colored progress bars (green < 70%, yellow 70–90%, red > 90%).

---

## 2.8 Concurrency & Reliability (Concurrency, Backpressure & Graceful shutdown)

> Operational guidance to keep the system stable under load and during failures.

- Runtime model:
  - Use `asyncio` as the main runtime for I/O-bound tasks (CAN reader/writer, FastAPI, WebSocket). CPU-bound tasks (for example: heavy signal processing with numpy) should be offloaded to `ProcessPoolExecutor` or split into a separate worker process/service to avoid blocking the event loop.
  - FastAPI runs on `uvicorn` (ASGI). If scaled with multiple workers, note that `SignalStore` is in-memory: when using multiple workers, `SignalStore` must be moved to a shared store (Redis) or a message broker must be used.

- Queues & Backpressure:
  - Connect components using an `asyncio.Queue` with `maxsize` (config: `processor.max_queue_size`). When the queue is full, apply policy `queue_policy`: `block` | `drop_oldest` | `reject_writes` (default: `reject_writes`).
  - Propagate backpressure: if storage/writer is slow, the system should reduce broadcast rate (WS) or return `503` for new write requests.
  - Use batch insert (`storage.batch_size`, `batch_interval_sec`) to reduce I/O overhead and stabilize throughput.

- Safe CAN writing:
  - Serialize write operations on each bus with `asyncio.Lock`, or keep a single writer task to guarantee ordering and avoid contention on the interface.
  - Apply write rate limiting (per-signal and global) and validate values before encoding and sending to the bus.

- Graceful shutdown & Flush:
  - On `SIGINT`/`SIGTERM`: mark `shutting_down`, reject new writes (return 503), and send a notification (`system_status`/`shutdown`) to clients over WS.
  - Call `storage.flush()` and wait up to `shutdown_timeout_sec` (config, default 10s) to persist the buffer; if it times out, log a warning and close connections safely.
  - Close the CAN interface after the writer queue has been processed, then close WebSocket connections.

- Monitoring & recovery:
  - Keep both `/health` (liveness) and `/ready` (readiness). Supervisor/watchdog (systemd, docker restart policy, or supervisor) uses these endpoints to decide when to restart.
  - Export important metrics: queue length, queue drops, storage backlog, write latency, error counts.

- Logging & Observability:
  - Structured JSON logs with fields: `timestamp`, `component`, `correlation_id`, `task`, `level`, `message`.
  - Add metrics for Prometheus (for example: `can_msgs_total`, `signal_updates_total`, `write_errors_total`, `queue_drops_total`).

- Scale & distributed architecture:
  - Single-process `asyncio` is simple and appropriate when using an in-memory `SignalStore`. To scale horizontally, move state out to Redis or use a message bus (NATS/RabbitMQ).
  - Note: multiple uvicorn workers require shared state; without shared state, avoid using multiple workers.

- Suggested additional configuration:
  - `processor.queue_policy: drop_oldest|block|reject`
  - `writer.rate_limit_per_sec`, `writer.burst`
  - `shutdown_timeout_sec: 10`
  - `supervisor.watchdog_interval_sec: 5`

- Testing:
  - Test backpressure: slow down storage to verify behavior (drops, 503, sampling).
  - Test graceful shutdown: ensure `storage.flush()` is called and the writer queue is drained.

---
## 2.9 Error Taxonomy & Recovery

> Classification of system errors and recovery strategy for each type.

| Error code | Source | Description | Severity | Recovery Strategy |
|---|---|---|---|---|
| `ERR_CAN_BUS_OFF` | CAN I/O | Bus-off state due to hardware failure or overload | CRITICAL | Auto-reconnect with exponential backoff (1s → max 30s). Log the error, temporarily pause writes. After 5 consecutive failures → alert Supervisor |
| `ERR_CAN_TIMEOUT` | CAN Reader | No frame received within the expected time (> 3× cycle time) | WARNING | Log a warning, send stale status for the signal. If > 30s → mark signal offline |
| `ERR_PARSE_DB` | Parser | Invalid or corrupted can.json file | CRITICAL | Reject the file, log detailed error information (file, line, reason). The system continues running with the remaining valid files |
| `ERR_DECODE_FRAME` | CAN Reader | Received frame does not match the can.json definition (DLC mismatch, unknown ID) | WARNING | Log + skip frame, increment error counter. If error rate > threshold → alert |
| `ERR_STORAGE_WRITE` | Storage | SQLite write failure (disk full, lock, corruption) | CRITICAL | Retry 3 times with backoff. If it still fails → buffer in memory (max 10 000 records), alert Supervisor. Flush the buffer when DB recovers |
| `ERR_STORAGE_FULL` | Storage | Disk usage exceeds threshold `storage.max_disk_mb` | WARNING | Run retention purge immediately. If still insufficient → reduce batch size, log warning |
| `ERR_WS_SLOW_CLIENT` | WebSocket | Client cannot consume messages fast enough (send buffer > threshold) | WARNING | Drop oldest messages for that client (per-client buffer). If the client remains too slow for > 60s → disconnect |
| `ERR_API_RATE_LIMIT` | FastAPI | Write request exceeds rate limit | INFO | Return HTTP 429 + `Retry-After` header |
| `ERR_CONFIG_INVALID` | Config | Invalid YAML config (schema mismatch) | CRITICAL | Reject startup, log detailed validation error (field, expected, got) |
| `ERR_ENCODER_FAIL` | CAN Writer | Signal encode failure (out of range, unknown signal) | WARNING | Reject the write request (HTTP 400), log the reason. Do not send the frame to the bus |

> **Error counters:** Each error type has its own counter, exported via a Prometheus metric and shown in `/api/v1/system/status`.

---

## 2.10 Deployment & Service

> Guidance for deploying the system as a service on embedded Linux (CarPC).

### systemd Service

```ini
# /etc/systemd/system/can-hmi.service
[Unit]
Description=CAN-HMI Signal Server
After=network.target can-setup.service
Wants=can-setup.service

[Service]
Type=notify
User=canhmi
Group=canhmi
WorkingDirectory=/opt/can-hmi
ExecStart=/opt/can-hmi/.venv/bin/python -m src.core.runner --config /opt/can-hmi/config/system.json
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=3
WatchdogSec=30
TimeoutStopSec=15
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### Docker (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://localhost:8000/health || exit 1
CMD ["python", "-m", "src.core.runner", "--config", "config/system.json"]
```

### CAN Bus Setup (Linux)

```bash
# Virtual CAN (dev/test)
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0

# Real SocketCAN
sudo ip link set can0 type can bitrate 500000
sudo ip link set up can0
```

### Environment Variables (override config)

| Variable | Default | Description |
|---|---|---|
| `CANHMI_CONFIG` | `config/system.json` | Path to main config file |
| `CANHMI_LOG_LEVEL` | `INFO` | Override logging level |
| `CANHMI_API_KEY` | (from config) | Override API key |
| `CANHMI_DB_PATH` | (from config) | Override SQLite path |

---

## 2.11 Security

> Security for the API, WebSocket, and CAN write path.

| Item | Requirement |
|---|---|
| Authentication | API key (header `X-API-Key`) for REST. Optional JWT for multi-user. WS: token via query param `?token=` |
| Authorization | Signal write access checks the `writable` flag in `signal_config`. Only signals with `writable=true` may be written via PUT |
| Input validation | Pydantic models validate all request bodies. Signal values must be within the `[min, max]` range from the configured DBC |
| Rate limiting | Global: 100 req/s (configurable). Write: 10 req/s per signal. Return HTTP 429 + `Retry-After` |
| TLS | Production: HTTPS (reverse proxy nginx/caddy). Dev: plain HTTP OK |
| Secret management | API key is not hard-coded; read from env var `CANHMI_API_KEY` or a secret file. Do not commit secrets in the config file |
| CAN bus safety | Validate signal value ranges before encoding. Reject out-of-range writes (HTTP 400). Log all write operations |
| CORS | Whitelist origins in config (`api.cors_origins`). Do not use `*` in production |
| Headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` for the frontend |

---

## 3. Project Directory Structure

```
car-hmi/
├── docs/
│   └── requirement.md          # Requirements document (this file)
├── README.md                   # Installation & run guide
├── pyproject.toml              # Project metadata & dependencies
├── ruff.toml                   # Ruff linter configuration
├── Dockerfile                  # Container image
├── docker-compose.yml          # Docker Compose setup
├── bench.py                    # Performance benchmarks
├── bench_signal_store.py       # SignalStore benchmarks
├── deploy/
│   └── can-hmi.service         # systemd unit file
├── config/
│   ├── system.json             # CAN bus configuration (main config)
│   ├── alarms.json             # Alarm thresholds (auto-generated from DBC)
│   └── signals.json            # Signal display config (auto-generated from DBC)
├── db/
│   ├── can_db/                 # DBC files (CAN database)
│   │   ├── m_dummy.dbc
│   │   └── p_dummy.dbc
│   └── ecu_db/                 # A2L files (measurement metadata)
│       └── m_dummy.a2l
├── diagram/                    # Architecture & sequence diagrams (PlantUML)
│   ├── README.md
│   └── *.puml                  # 15 PlantUML diagram files
├── scripts/
│   ├── gen_signals_from_dbc.py # Generate signals.json from DBC files
│   ├── gen_alarms_from_dbc.py  # Generate alarms.json from DBC files
│   ├── gen_configs_from_dbc.py # Generate all configs from DBC
│   ├── dbc_utils.py            # DBC utility functions
│   ├── set_processor_config.py # CLI to update processor config
│   ├── run_windows.ps1         # Windows run script
│   ├── run_linux.sh            # Linux run script
│   ├── setup_windows.ps1       # Windows setup script
│   ├── test_windows.ps1        # Windows test script
│   └── test_linux.sh           # Linux test script
├── src/
│   ├── __init__.py
│   ├── can_simulator/
│   │   ├── __init__.py
│   │   ├── simulator.py        # CANSimulator — reads .dbc directly, generates random signals
│   │   ├── cli.py              # CLI entry-point
│   │   └── config.json         # Simulator runtime config (mode, update_hz)
│   ├── can_io/
│   │   ├── __init__.py
│   │   ├── bus_factory.py      # Factory: create CAN bus instance (virtual/socketcan/pcan/...)
│   │   ├── parser.py           # DatabaseLoader: DBC/A2L/CANdb JSON multi-parser
│   │   ├── reader.py           # Async CAN frame reader + decode + reconnect
│   │   └── writer.py           # Encode signal → CAN frame + async send
│   ├── processor/
│   │   ├── __init__.py
│   │   ├── pipeline.py         # SignalPipeline: pluggable processing stages
│   │   ├── filters.py          # SmoothingFilter, RateLimiter
│   │   ├── alarms.py           # AlarmChecker: threshold detection + state change
│   │   └── computed.py         # ComputedSignals: virtual/derived signals
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py         # DB connection & schema init (aiosqlite)
│   │   ├── repository.py       # SQLiteRepository: CRUD operations
│   │   └── exporter.py         # DataExporter: CSV / JSON export
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py              # FastAPI application factory (create_app)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── signals.py      # /signals endpoints + /ws/* WebSocket
│   │   │   ├── alarms.py       # /alarms endpoints
│   │   │   ├── config.py       # /config endpoints (signal, processor, general, alarms)
│   │   │   └── system.py       # /system/health, /system/ready endpoints
│   │   ├── websocket.py        # ConnectionManager: topic-based WS broadcast
│   │   ├── models.py           # Pydantic request/response models
│   │   └── auth.py             # APIKeyAuth: X-API-Key validation
│   └── core/
│       ├── __init__.py
│       ├── config.py           # Pydantic AppConfig + all sub-config models
│       ├── config_manager.py   # Runtime config read/write/update (YAML persistence)
│       ├── signal_store.py     # In-memory SignalStore with Observer/Pub-Sub
│       ├── system_metrics.py   # CarPC system metrics collector (CPU, RAM, disk, queue, heap)
│       └── runner.py           # AppRunner orchestrator: start all components
├── frontend/
│   ├── index.html              # Main dashboard page
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js              # Main entry, WebSocket client
│       ├── widgets.js          # Gauge, chart, table components
│       └── api.js              # REST API helper functions
├── data/                       # Runtime data (SQLite DB, created at runtime)
├── logs/                       # Log files (rotating)
└── tests/
    ├── __init__.py
    ├── test_api.py
    ├── test_bus_io.py
    ├── test_can_io.py
    ├── test_can_simulator.py
    ├── test_core.py
    ├── test_exporter.py
    ├── test_integration.py
    ├── test_parser.py
    ├── test_processor.py
    ├── test_storage.py
    └── test_websocket.py
```

---

## 4. Data Flow

```
[1] CAN Simulator (CANSimulator)
     │
     │  CAN frames (virtual bus)
     ▼
[2] CAN Reader (reader.py) ──decode (DatabaseLoader: DBC/A2L/JSON)──► DecodedFrame
     │
     │  asyncio.Queue (queue_policy: reject/block/drop_oldest)
     ▼
[3] Signal Pipeline (pipeline.py)
     │  ├── Stage 1: SmoothingFilter
     │  ├── Stage 2: RateLimiter
     │  ├── Stage 3: ComputedSignals
     │  └── Stage 4: AlarmChecker
     │
     ├──► [4] Storage (batch insert → SQLiteRepository → signal_log)
     │
     └──► [5] Signal Store (in-memory latest values, Pub/Sub via callbacks)
              │
              ├──► REST API (GET /signals snapshot, GET /signals/{name}/history)
              │
              └──► WebSocket broadcast (ConnectionManager → topic-based push)
                        │
                        ▼
                   [6] Web Dashboard (display + edit)
                        │
                        │  PUT /signals/{signal_name}
                        ▼
                   [5] FastAPI ──► [2] CAN Writer (writer.py encode+send) ──► CAN Bus ──► [1] Simulator
```

**Write-back flow:**

1. The user enters a new value in the Web Dashboard
2. The frontend calls `PUT /signals/{signal_name}` with the new value (`WriteSignalRequest`)
3. FastAPI validates it (Pydantic) → passes it to the CAN Writer
4. CAN Writer encodes the signal into a CAN frame (via DatabaseLoader) → sends it to the bus (async, with Lock)
5. CAN Simulator (or the real ECU) receives it and responds
6. Response: 202 ACCEPTED with `{"signal_name": ..., "value": ..., "queued_at": ...}`

---

## 5. Dependencies

```
# Core
python-can>=4.4          # CAN bus interface
fastapi>=0.115           # Web framework
uvicorn[standard]>=0.30  # ASGI server
websockets>=12.0         # WebSocket support
pydantic>=2.9            # Data validation
pydantic-settings>=2.0   # Settings management

# Storage
aiosqlite>=0.20          # Async SQLite

# Processing
numpy>=1.26              # Signal processing (smoothing, etc.)

# Config
pyyaml>=6.0              # YAML config parser

# Optional
pya2l                    # A2L file parsing (optional dependency)

# Dev & Test
pytest>=8.0
pytest-asyncio>=0.24
pytest-cov>=5.0           # Coverage reporting
httpx>=0.27              # Async HTTP client (test FastAPI)
ruff>=0.5                # Linter + formatter
locust>=2.29             # Performance / load testing (optional)
```
### Testing Strategy

| Test type | Scope | Tooling |
|---|---|---|
| Unit test | Each individual module (simulator, reader, processor, storage) | `pytest` + `pytest-asyncio` |
| Integration test | API endpoints + WebSocket + combined CAN I/O | `httpx` + `pytest` |
| E2E test | Full stack: Simulator → Reader → Processor → API → Dashboard | Manual / Playwright (optional) |
| Performance test | Throughput, latency per NFR | `locust` or custom benchmark |

**Mocks & Fixtures:**
- CAN bus: use the `python-can` virtual interface (`VirtualBus`) for unit/integration tests — no hardware required.
- Storage: use in-memory SQLite (`:memory:`) for fast tests; file-based SQLite for integration tests.
- API: `httpx.AsyncClient` + `TestClient` from FastAPI.
- Clock: mock `time.time()` / `asyncio.get_event_loop().time()` for deterministic timing tests.

**Coverage targets:**

| Module | Target |
|---|---|
| `can_simulator` | ≥ 80% |
| `can_io` | ≥ 85% |
| `processor` | ≥ 90% |
| `storage` | ≥ 85% |
| `api` | ≥ 80% |
| `core` | ≥ 75% |
| **Overall** | **≥ 80%** |

**CI Pipeline (GitHub Actions / GitLab CI):**

```yaml
# .github/workflows/ci.yml (excerpt)
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: pytest --cov=src --cov-report=xml -q
      - run: ruff check src/ tests/
```

---

## 6. Configuration

### system.json

```json
{
  "can": [
    {
      "interface": "virtual",
      "channel": "vcan0",
      "bitrate": 500000,
      "can_db_file": "db/can_db/p_v2.dbc"
    },
    {
      "interface": "virtual",
      "channel": "vcan1",
      "bitrate": 500000,
      "can_db_file": "db/can_db/p_v2.dbc"
    }
  ],
  "simulator": {
    "enabled": true,
    "default_cycle_ms": 50,
    "can_db_file": "db/can_db/p_v2.dbc"
  },
  "api": {
    "host": "0.0.0.0",
    "port": 8000,
    "api_key": "change-me-in-production",
    "cors_origins": ["http://localhost:8000", "http://192.168.*.*:5173"]
  },
  "storage": {
    "sqlite_path": "data/signals.db",
    "batch_size": 100,
    "batch_interval_sec": 2.0,
    "retention_days": 30,
    "max_disk_mb": 2048
  },
  "processor": {
    "max_update_rate_hz": 10.0,
    "max_queue_size": 10000,
    "queue_policy": "reject"
  },
  "oms_config": {
    "bypass_simi_input": false,
    "class_config": [65, 90],
    "target_signal": {
      "OMS_FR_OccupantClassification": "OMS_FR_OccupantWeightMean",
      "OMS_FL_OccupantClassification": "OMS_FL_OccupantWeightMean",
      "OMS_RL1_OccupantClassification": "OMS_RL1_OccupantWeightMean",
      "OMS_RL2_OccupantClassification": "OMS_RL2_OccupantWeightMean",
      "OMS_RR1_OccupantClassification": "OMS_RR1_OccupantWeightMean"
    }
  },
  "writer": {
    "rate_limit_per_sec": 10,
    "burst": 5,
    "use_prevalue_for_unwritten_signal": true
  },
  "shutdown": { "timeout_sec": 10 },
  "supervisor": { "watchdog_interval_sec": 5 },
  "logging": {
    "level": "INFO",
    "file_path": "logs/can-hmi.log",
    "max_size_mb": 50,
    "backup_count": 5
  }
}
```

### signals.json

```json
{
  "signals": {
    "VehicleSpeed": {
      "display_name": "Vehicle Speed",
      "group": "driving",
      "widget": "gauge",
      "visible_user_mode": true
    },
    "EngineRPM": {
      "display_name": "Engine RPM",
      "group": "driving",
      "widget": "gauge",
      "visible_user_mode": true
    },
    "CoolantTemp": {
      "display_name": "Coolant Temperature",
      "group": "engine",
      "widget": "chart",
      "visible_user_mode": false
    },
    "BrakePressure": {
      "display_name": "Brake Pressure",
      "group": "safety",
      "widget": "table",
      "writable": true,
      "visible_user_mode": true
    }
  }
}
```

---

## 7. How to Run the System

### Development (local)

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Run the full stack (simulator + reader + processor + API + frontend)
python -m src.core.runner --config config/system.json

# 3. Or run individual parts
python -m src.can_simulator --dbc-dir db/can_db/ --a2l-dir db/ecu_db/ # Simulator only
python -m src.api.app --config config/system.json                     # API server only

# 4. Open the dashboard
# Access http://localhost:8000 in the browser

# 5. Run tests
pytest --cov=src --cov-report=term-missing -q
ruff check src/ tests/
```

### Production (systemd / Docker)

See details in **Section 2.10 — Deployment & Service**:
- **systemd:** copy `systemd/can-hmi.service` → `/etc/systemd/system/`, `systemctl enable --now can-hmi`
- **Docker:** `docker build -t can-hmi . && docker run -p 8000:8000 can-hmi`
- **CAN bus setup:** see the `vcan` / `socketcan` guidance in section 2.10

---

## 8. Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| AC-1 | CAN Simulator emits frames correctly according to CANdb (candb), with the correct cycle time | ⬜ |
| AC-2 | CAN Reader accurately decodes all signals in CANdb/candb | ⬜ |
| AC-3 | Signal Processor applies smoothing and raises alarms when thresholds are exceeded | ⬜ |
| AC-4 | Storage can persist ≥ 1000 samples/s without data loss | ⬜ |
| AC-5 | REST API returns signal snapshots with latency < 50 ms | ⬜ |
| AC-6 | WebSocket pushes signal updates with latency < 100 ms (end-to-end) | ⬜ |
| AC-7 | Web Dashboard shows smoothly updating real-time gauges/charts | ⬜ |
| AC-8 | Users can write signal values from the Dashboard → CAN bus successfully | ⬜ |
| AC-9 | All unit tests pass, coverage ≥ 80% | ⬜ |
| AC-10 | The system runs stably for ≥ 1 continuous hour without crashing | ⬜ |
| AC-11 | Graceful shutdown: flush storage, drain writer queue, close WS within ≤ `shutdown_timeout_sec` | ⬜ |
| AC-12 | Config validation reports clear errors when YAML is invalid (field name, expected vs got) | ⬜ |
| AC-13 | Alarm lifecycle: trigger → persist in alarm_log → WS push → ACK → resolve works completely | ⬜ |
| AC-14 | Security: only signals with `writable=true` allow PUT; API key required for REST | ⬜ |
| AC-15 | CAN bus reconnects automatically after bus-off, within ≤ 5 seconds (P95) | ⬜ |
| AC-16 | Error taxonomy: all error codes are logged and error counters are exported via `/api/v1/system/status` | ⬜ |

### Non-Functional Requirements (NFR)

| NFR | Requirement |
|---|---|
| Latency | End-to-end CAN → WebSocket ≤ 100 ms (P95) |
| Throughput | Sustained processing ≥ 1 000 signals/s |
| Memory | RSS ≤ 512 MB under normal operating conditions |
| Startup | Cold start → ready ≤ 5 seconds |
| Config validation | Report clear errors when config YAML is invalid. Use **Pydantic `BaseSettings`** to validate schema (type, range, required fields). Missing fields or incorrect types must fail immediately at startup with a clear message (field name, expected type, actual value) |
| Graceful shutdown | On SIGTERM/SIGINT: flush storage buffer, close CAN bus, close WS connections |
| Disk usage | SQLite DB ≤ 2 GB (with 30-day retention). Config: `storage.max_disk_mb: 2048` |
| CAN reconnect | Bus-off → reconnect ≤ 5 seconds (P95). Max 5 retries before alerting Supervisor |
| Error rate | Decode error rate ≤ 0.1% of total received frames (sustained) |

---

## 9. Implementation Roadmap

| Phase | Content | Priority |
|---|---|---|
| **Phase 1** | CAN Simulator + CANdb (candb) + CAN Reader/Writer | 🔴 High |
| **Phase 2** | Signal Processor + Storage (SQLite) + Alarm detection | 🔴 High |
| **Phase 3** | FastAPI REST + WebSocket endpoints + Config validation (Pydantic) | 🔴 High |
| **Phase 4** | Web Dashboard (gauge, chart, table, edit) + Offline fallback | 🟡 Medium |
| **Phase 5** | Alarm lifecycle (ACK/resolve/history) + Notification (email/webhook/toast) | 🟡 Medium |
| **Phase 6** | Security (2.11) + Rate limiting + Error Taxonomy (2.9) + Deployment (2.10, systemd/Docker) | 🟢 Low |
| **Phase 7** | Extensions: CAN FD, TimescaleDB, multi-bus, Prometheus metrics | 🟢 Low |

---

## 10. Glossary

| Term | Definition |
|---|---|
| CAN | Controller Area Network — serial communication protocol between ECUs in a vehicle |
| CAN FD | CAN with Flexible Data-rate — CAN extension with larger payloads (up to 64 bytes) and higher bitrates |
| DBC | Database CAN — file format describing CAN messages and signals (Vector standard) |
| A2L | ASAM MCD-2 MC description file — describes ECU measurement and calibration data |
| CANdb / candb | Custom JSON-based CAN database format used in this project |
| DLC | Data Length Code — number of data bytes in a CAN frame (0–8 for CAN 2.0, 0–64 for CAN FD) |
| ECU | Electronic Control Unit — electronic control module in the vehicle |
| Bus-off | CAN controller error state when the error counter exceeds the threshold and the node disconnects itself from the bus |
| mDNS | Multicast DNS — service discovery on the LAN without requiring a DNS server |
| RTSP | Real Time Streaming Protocol — video streaming protocol |
| GStreamer | Multimedia processing framework (used for DMS/OMS video) |
| DMS / OMS | Driver Monitoring System / Occupant Monitoring System — driver / passenger monitoring cameras |
| HMI | Human-Machine Interface — the user interface |
| NFR | Non-Functional Requirement — non-functional requirement (performance, security, ...) |
| RSS | Resident Set Size — the actual RAM currently used by the process |
| ASGI | Asynchronous Server Gateway Interface — interface standard for Python async web servers |
