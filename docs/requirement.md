# CAN-HMI System — Requirement Specification

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
| 0.8.1 | 2026-03-21 | HMI Team | Add frontend modes (dev/user) with client-side whitelist and subscription filtering |

---

## 1. Tổng quan hệ thống

Hệ thống cho phép **đọc/ghi tín hiệu CAN bus của xe**, xử lý dữ liệu, lưu trữ và phục vụ qua **FastAPI + WebSocket** để người dùng có thể **xem real-time** và **chỉnh sửa thông số online** từ giao diện web.

**Design patterns chính:**
- **Pipeline** — Signal processing chain (smoothing → alarm check → computed signals)
- **Observer / Pub-Sub** — WebSocket broadcast tới nhiều client, signal store notify
- **Repository** — Storage abstraction (SQLite adapter, có thể swap sang TimescaleDB/InfluxDB)
- **Factory** — FastAPI application factory (`create_app()`)
- **Strategy** — CAN database parser (can.json) load signal definitions at startup

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

## 2. Các module và yêu cầu chi tiết

### 2.1 CAN Simulator (Vehicle Simulator)

> Mô phỏng ECU xe gửi/nhận CAN frame trên virtual CAN bus.

| Hạng mục | Yêu cầu |
|---|---|
| Mục đích | Phát CAN frame theo CANdb / candb để phát triển/test mà không cần phần cứng |
| Giao thức | CAN 2.0B, hỗ trợ mở rộng CAN FD (tùy chọn) |
| CANdb | Load file `config/can.json` chứa message/signal definitions để encode signal → CAN frame |
| Kịch bản | Hỗ trợ scenario file (JSON/YAML) định nghĩa chuỗi tín hiệu theo thời gian |
| Tốc độ | Configurable cycle time per message (mặc định 10–100 ms) |
| Interface | `python-can` virtual interface (`virtual`, `socketcan`, `pcan`, `vector`) |
| Nhiễu | Tùy chọn thêm noise/jitter để mô phỏng thực tế |
| CLI | `python -m src.can_simulator.cli --config config/system.json --scenario scenarios/city_drive.yaml` |

> Simulator và processor load signal definitions từ **`config/can.json`**. File này chứa toàn bộ message/signal metadata cần thiết cho decode/encode.

#### CANdb (candb) format

Hệ thống phải hỗ trợ đọc/ghi dữ liệu định nghĩa message/signal theo chuẩn **CANdb (candb)** để dễ tích hợp với dữ liệu mô tả từ các công cụ khác.

Ví dụ đơn giản (JSON):

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

> **Lưu ý:** Các format DBC và A2L đã được loại bỏ. Hệ thống chỉ sử dụng **`config/can.json`** làm nguồn duy nhất cho message/signal definitions.

Yêu cầu hỗ trợ:
- Load từ **1 file JSON** (`config/can.json`).
- Parser trả về cấu trúc message/signal chung để dùng chung cho decoder/encoder.
- Hỗ trợ `start_bit: null` (auto-allocation) và auto min/max calculation.

**Tín hiệu mẫu cần mô phỏng (tất cả tín hiệu trong `config/can.json`):**

| Tín hiệu mẫu | Message (ID) | Unit | Range | Cycle |
|---|---|---|---|---|
| HMI_CrashSeverity | INC_HMI_CrashInfo (128) |  | 0–7 | TBD |
| HMI_CrashImpactTrigger | INC_HMI_CrashInfo (128) |  | 0–1 | TBD |

> **Lưu ý:** Danh sách đầy đủ tín hiệu được định nghĩa trong `config/can.json`. Hệ thống tự động load toàn bộ signal từ file này khi khởi động.

---

### 2.2 CAN Reader / Writer

> Đọc CAN frame từ bus, decode thành signal; encode signal và ghi ngược lên bus.

| Hạng mục | Yêu cầu |
|---|---|
| Thư viện | `python-can` (CAN I/O) — parser tự viết từ `can.json` |
| Bus factory | `bus_factory.py` — Factory tạo CAN bus instance (hỗ trợ virtual, socketcan, pcan, vector, kvaser, serial) |
| Parser | `parser.py` — `DatabaseLoader` load `config/can.json`, decode/encode CAN frame bằng bit manipulation tự viết |
| Decode | Tự động decode frame → signal dựa trên can.json (bit extraction + factor/offset) |
| Encode + Send | Nhận lệnh thay đổi signal → encode → gửi CAN frame |
| Async | Chạy non-blocking (`asyncio`) để không block main loop |
| Filter | Hỗ trợ CAN ID filter (chỉ nhận message quan tâm) |
| Bus config | Đọc từ file config (JSON): interface, channel, bitrate. Hỗ trợ nhiều kênh CAN đồng thời |
| Output | Push decoded signal dict vào internal queue (asyncio.Queue) |
| Queue policy | Hỗ trợ 3 chính sách khi queue đầy: `block`, `reject`, `drop_oldest` |
| Reconnect | Tự reconnect khi bus lỗi với exponential backoff (1s → 30s), max retries configurable |
| Error handling | Tự reconnect khi bus lỗi, log cảnh báo |

**Cấu trúc decoded signal (sau khi decode frame):**

Định dạng chuẩn gồm 4 phần chính:
1) **Message config** — metadata từ config/can.json
2) **Raw message** — frame nhận được từ CAN bus
3) **Signal configs** — định nghĩa bit layout, factor/offset từ can.json
4) **Decoded signal values** — giá trị thực sau khi decode (`raw × factor + offset`)

```json
{
    // --- 1) Message config (từ config + can.json) ------------------------------
    "message_config": {
        "msg_name": "SBS_WMS_FR_Response",
        "msg_id": 401,
        "dlc": 4,
        "cycle_ms": 20,                                 // nếu có cấu hình trong config
        "description": "Engine status message",
        "db": "Interface_Panther_To_CarPC_generated.dbc",                    // hoặc "candb" / "config"
        "src": "PANTHER"                                // node nguồn
    },

    // --- 2) Raw CAN frame (từ python-can) ---------------------------------------
    "raw_message": {
        "timestamp": 1742000000.123,                    // epoch seconds (từ python-can frame.timestamp)
        "bus": "vcan0",                                 // tùy chọn nếu multi-bus
        "msg_id": 401,
        "is_extended": false,
        "is_fd": false,
        "data": ["0x0C", "0x1E", "..."],          // raw bytes
        "raw_hex": "0C1E..."              // xâu hex (tùy chọn)
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
            "dst": ["PANTHER", "CAR_PC"]                    // node đích
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
            "dst": ["PANTHER", "CAR_PC"]                    // node đích
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
            "dst": ["IC"]                    // node đích
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

> **Ghi chú:**
> - `message_config` là metadata dùng cho mapping giữa config và can.json (ví dụ: cycle time, mô tả, nguồn dữ liệu).
> - `signal_configs` gồm các thông tin lấy từ can.json (factor/offset/unit/bit layout/description), dùng để encode/decode.
> - `signals` là giá trị đã decode từ `raw_message` dựa trên `signal_configs`.
> - Decoder/encoder phải hỗ trợ cả CANdb (candb) để giữ nhất quán tên/số ID và cấu hình tính toán.


---

### 2.3 Signal Processor

> Xử lý, lọc, biến đổi tín hiệu thô trước khi lưu và phát ra ngoài.
>
> **Lưu ý:** Processor nên hỗ trợ nhiều kênh CAN đồng thời, mỗi kênh có file can.json riêng. Tín hiệu từ tất cả các kênh được gộp vào chung một pipeline xử lý.
>
| Hạng mục | Yêu cầu |
|---|---|
| Smoothing | Moving average, exponential moving average (configurable window, `method: "moving_avg"` hoặc `"ema"`) |
| Rate limiting | Giới hạn tần số cập nhật per signal (ví dụ: max 10 Hz cho UI) |
| Alarm / Threshold | Định nghĩa ngưỡng cảnh báo per signal (YAML config), phát hiện state change (none→warning→critical) |
| Unit conversion | Hỗ trợ convert đơn vị (raw → engineering value đã có từ can.json, thêm custom nếu cần) |
| Computed signals | Hỗ trợ virtual signal tính từ nhiều signal khác (ví dụ: `Power = RPM × Torque / 9549`) — `ComputedSignals` stage với pluggable formulas |
| Output | Signal dict đã xử lý → push vào broadcast queue + gửi cho storage |
| Backpressure | Khi queue đầy: áp dụng `queue_policy` (drop_oldest / block / reject). Queue size configurable (`max_queue_size`, default 10 000) |
| Pipeline | 4 stages theo thứ tự: SmoothingFilter → RateLimiter → ComputedSignals → AlarmChecker |
| Batch storage | Buffer signal records, flush khi đạt `batch_size` hoặc `batch_interval_sec` |

**Alarm threshold config mẫu (`config/alarms.json`):**

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

> **Lưu ý:** Format alarm config là dict (key = signal name), không phải list. Auto-generate bằng script `scripts/gen_alarms_from_dbc.py`.

---

### 2.4 Storage

> Lưu trữ time-series signal data và cấu hình.

Mục tiêu:
- Lưu trữ giá trị tín hiệu (signal) theo thời gian để phục vụ truy vấn lịch sử, biểu đồ, phân tích.
- Lưu trữ cấu hình tín hiệu (min/max, unit, writable) để frontend và backend cùng dùng.
- Hỗ trợ batch insert và retention để tránh quá tải I/O.

**Cấu hình (đã có trong `config/system.json`):**

```yaml
storage:
  engine: sqlite              # sqlite | timescaledb | influxdb
  sqlite_path: data/signals.db
  batch_size: 100
  batch_interval_sec: 2
  retention_days: 30
```

| Hạng mục | Yêu cầu |
|---|---|
| Engine mặc định | **SQLite** (zero-config, file-based) |
| Engine mở rộng | Có thể xây thêm adapter cho **TimescaleDB** / **InfluxDB** khi cần |
| Schema | `signal_log` ghi thời gian, msg_name, signal_name, giá trị, unit |
| Config store | `signal_config` lưu meta: min/max/writable/unit/description |
| Batch insert | Buffer + batch insert mỗi N record hoặc mỗi T giây |
| Retention | Auto-purge dữ liệu cũ hơn N ngày (configurable) |
| Export | Hỗ trợ export CSV/JSON qua API |

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

> **Gợi ý triển khai:**
> - API ghi (write) đẩy dữ liệu vào queue → worker dùng batch insert để ghi vào SQLite.
> - Mỗi N giây (hoặc mỗi N record), flush batch xuống DB.
> - Retention: chạy job định kỳ xóa bản ghi cũ hơn `retention_days`.

---

### 2.5 FastAPI / WebSocket Server

> Cung cấp REST API + WebSocket để đọc, ghi tín hiệu, nhận real-time stream và nhận video stream từ camera DMS/OMS (GStreamer over Ethernet).

> **Lưu ý:** Toàn bộ phần CAN RW, xử lý tín hiệu, storage và FastAPI/WebSocket đều nằm trong khối **CarPC** (bao gồm cả stream video). Ở chế độ sản xuất, CarPC sẽ chạy như một node duy nhất đảm nhiệm cả CAN và giao tiếp mạng.

#### REST API Endpoints

> **Lưu ý:** Route prefix thực tế là `/signals`, `/alarms`, `/config`, `/system` (không có `/api/v1/`). OpenAPI docs tại `/docs`.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/signals` | Danh sách tất cả signal hiện tại (snapshot) |
| GET | `/signals/available` | **Full metadata** của tất cả signals (unit, min/max, alarm thresholds, widget, value, status) — gọi 1 lần khi client khởi động |
| GET | `/signals/{signal_name}` | Giá trị hiện tại của 1 signal |
| PUT | `/signals/{signal_name}` | **Ghi giá trị mới** cho signal (gửi xuống CAN bus) — trả 202 ACCEPTED |
| GET | `/signals/{signal_name}/history` | Lịch sử signal (query params: `start`, `end`, `limit`, `offset`) |
| GET | `/alarms` | Danh sách alarm (filter: `signal_name`, `level`, `acknowledged`, `start`, `end`, `limit`, `offset`) |
| GET | `/alarms/{alarm_id}` | Chi tiết 1 alarm |
| POST | `/alarms/{alarm_id}/acknowledge` | Acknowledge một alarm (đánh dấu đã xem) |
| POST | `/alarms/{alarm_id}/resolve` | Resolve một alarm (đánh dấu đã xử lý) |
| GET | `/config` | Danh sách signal config (unit, widget, writable) |
| GET | `/config/signal/{signal_name}` | Lấy config cho 1 signal |
| PATCH | `/config/signal/{signal_name}` | Cập nhật config cho signal (upsert vào signal_config table) |
| GET | `/config/processor` | Xem processor config (max_queue_size, queue_policy) |
| POST | `/config/processor` | Cập nhật processor config (live apply attempt) |
| GET | `/config/general` | Xem full application config (AppConfig.model_dump()) |
| PATCH | `/config/general` | Patch application config (partial update, persist system.json) |
| POST | `/config/general/reset` | Reset application config về defaults |
| GET | `/config/alarms` | Xem alarms config (raw YAML as JSON) |
| POST | `/config/alarms` | Cập nhật alarms config (overwrite) |
| POST | `/config/alarms/reset` | Reset alarms config về empty default |
| GET | `/system/health` | Liveness probe — trả `200` + `{"status":"ok","uptime_seconds":...}` |
| GET | `/system/ready` | Readiness probe — trả `200` khi tất cả component đã init |
| GET | `/system/metrics` | Thông tin tài nguyên CarPC: CPU, RAM, disk, swap, queue, heap, network, async tasks, uptime, platform |

**Signal Naming (std_name mapping)**

The system supports optional standardized signal aliases (`std_name`) which map to canonical signal names. This enables user-friendly names in the frontend and external integrations while preserving canonical names internally. The mapping file is referenced in `config/system.json` under `signal.sync_dict` (default: `config/signal_std_name.json`). The server resolves `std_name` to canonical names when processing requests and includes an optional `std_name` field in responses when a mapping exists.

Configuration example (excerpt of `config/system.json`):

```json
"signal": {
  "sync_dict": "config/signal_std_name.json"
}
```

#### Error Response Format

Tất cả lỗi trả về dạng JSON thống nhất:

```json
{
  "error": {
    "code": "SIGNAL_NOT_FOUND",
    "message": "Signal 'FooBar' not found",
    "status": 404
  }
}
```

| HTTP Status | Mô tả |
|---|---|
| 400 | Bad Request — tham số không hợp lệ |
| 401 | Unauthorized — thiếu hoặc sai API key |
| 404 | Not Found — signal/resource không tồn tại |
| 429 | Too Many Requests — vượt rate limit |
| 500 | Internal Server Error |

#### WebSocket Endpoints

| Path | Mô tả |
|---|---|
| `ws://host/ws/signals` | (Legacy) Stream signal updates real-time (topic: SIGNALS) |
| `ws://host/ws/alarms` | (Legacy) Stream alarm events (topic: ALARMS) |
| `ws://host/ws/all` | (Legacy) Stream tất cả events — signals + alarms (topic: ALL) |
| `ws://host/ws/subscribe` | **Subscribe protocol** — client gửi JSON subscribe/unsubscribe để chọn kênh nhận dữ liệu |

> **Lưu ý:** Endpoint `/ws/subscribe` là giao thức mới, cho phép per-signal subscription để giảm băng thông. Legacy endpoints vẫn hoạt động để backward-compatible.

**WebSocket message format (implemented in `api/websocket.py`):**

Signal broadcast format:
```json
{"type": "signal", "signal": "VehicleSpeed", "value": 85.3, "timestamp": 1742000000.123}
```

Alarm broadcast format:
```json
{"type": "alarm", "signal": "CoolantTemp", "level": "critical", "value": 97.2, "threshold": 95, "timestamp": 1742000000.456}
```

Metrics broadcast format (via subscribe):
```json
{"type": "metrics", "cpu_percent": 23.4, "ram_percent": 41.0, "disk_percent": 15.2, "...": "..."}
```

Subscribe ack format:
```json
{"type": "subscribe_ack", "action": "subscribe", "channels": ["EngineSpeed", "alarms"], "mode": "continuous"}
```

**Subscribe Protocol (`/ws/subscribe`):**

Client gửi JSON command sau khi WS connected:
```json
{"action": "subscribe", "channels": ["EngineSpeed", "BatterySOC", "alarms", "metrics"], "mode": "continuous"}
{"action": "subscribe", "channels": ["*"], "mode": "continuous"}
{"action": "subscribe", "channels": ["EngineTemp"], "mode": "once"}
{"action": "unsubscribe", "channels": ["BatterySOC"]}
```

| Field | Type | Mô tả |
|---|---|---|
| `action` | `"subscribe"` \| `"unsubscribe"` | Đăng ký hoặc hủy đăng ký |
| `channels` | `string[]` | Danh sách: tên signal, `"alarms"`, `"metrics"`, hoặc `"*"` (tất cả signals) |
| `mode` | `"continuous"` \| `"once"` | `continuous`: nhận liên tục; `once`: nhận 1 lần rồi tự hủy |

> **Lưu ý:**
> - `ConnectionManager` quản lý connections với topic-based subscription (`SubscriptionTopic` enum: SIGNALS, ALARMS, ALL) cho legacy endpoints, và per-channel `_ClientSubscription` cho `/ws/subscribe`.
> - Broadcast sử dụng `asyncio.gather()` để gửi đồng thời tới tất cả clients, tự remove stale connections.
> - Frontend flow mới: (1) GET /signals/available → cache metadata; (2) WS /ws/subscribe → subscribe channels → nhận value+timestamp nhẹ. Giảm băng thông đáng kể so với broadcast ALL.

#### 1) `signal_config` (static metadata, gửi khi client subscribe)

```json
{
  "type": "signal_config",
  "timestamp": 1742000000.100,
  "signal": "VehicleSpeed",
  "msg_name": "ESP_VehicleDynamics1",
  "display_name": "Vehicle Speed",
  "unit": "km/h",
  "min": 0,
  "max": 655.35,
  "writable": false
}
```

#### 2) `signal_update` (real-time signal values)

```json
{
  "type": "signal_update",
  "timestamp": 1742000000.123,
  "data": {
    "VehicleSpeed": {
      "value": 85.3,
      "status": "ok"          // ok | warning | critical
    },
    "EngineRPM": {
      "value": 3200,
      "status": "ok"
    }
  }
}
```

#### 3) `alarm` (cảnh báo/ngưỡng)

```json
{
  "type": "alarm",
  "timestamp": 1742000000.456,
  "signal": "CoolantTemp",
  "level": "critical",            // one of: info / warning / critical
  "value": 97.2,
  "threshold": 95,
  "description": "Coolant temperature vượt ngưỡng critical (>= 95°C)"
}
```

#### 4) `snapshot` (khi client mới kết nối)

```json
{
  "type": "snapshot",
  "timestamp": 1742000000.500,
  "data": {
    "VehicleSpeed": { "value": 84.1, "status": "ok" },
    "EngineRPM": { "value": 3100, "status": "ok" }
  }
}
```

> **Ghi chú:**
> - Client có thể mở kết nối `ws://host/ws/signals` để nhận toàn bộ tín hiệu, hoặc `ws://host/ws/signals/{name}` để nhận một tín hiệu cụ thể.
> - Server có thể mở rộng message type (ví dụ: `system_status`, `config_update`) tuỳ nhu cầu.

#### 5) `heartbeat` (keep-alive)

```json
{
  "type": "heartbeat",
  "timestamp": 1742000005.000,
  "uptime_sec": 1425
}
```

> **Reconnection strategy:**
> - Server gửi `heartbeat` mỗi **5 giây** (configurable qua `ws_heartbeat_interval_sec`).
> - Client phát hiện mất kết nối nếu không nhận heartbeat trong **15 giây**.
> - Client tự động reconnect với **exponential backoff** (1s → 2s → 4s → … → max 30s).
> - Sau reconnect, server gửi lại `signal_config` + `snapshot` để client đồng bộ trạng thái.

| Hạng mục | Yêu cầu |
|---|---|
| Framework | FastAPI + uvicorn |
| Video stream | GStreamer RTP/RTSP stream từ camera DMS/OMS qua Ethernet (CarPC) |
| Auth | REST: API key (header `X-API-Key`), tùy chọn JWT. WebSocket: token qua query param `?token=` khi connect |
| Rate limit | Giới hạn write request (tránh spam CAN bus) |
| CORS | Cho phép cross-origin từ frontend dev server |
| Docs | Auto-gen Swagger UI tại `/docs` |
| Validation | Pydantic model cho request/response |

---

### 2.6 Web Demo Dashboard (Frontend)

> Giao diện web hiển thị real-time signal và cho phép chỉnh sửa thông số.

| Hạng mục | Yêu cầu |
|---|---|
| Tech | HTML + CSS + Vanilla JS (hoặc Vue.js/React tùy chọn mở rộng) |
| Serve | Static files phục vụ bởi FastAPI (`/static`) |
| Real-time | Kết nối WebSocket nhận signal stream |
| Hiển thị | Dashboard chia thành các widget card cho từng nhóm signal |
| Browser support | Chrome/Edge ≥ 120, Firefox ≥ 120. Responsive: desktop (≥ 1024px) + tablet (≥ 768px) |
| Offline / fallback | Khi mất kết nối WS: hiển thị banner "Disconnected", giữ last-known values (grey-out stale), auto-reconnect |
| Accessibility | Semantic HTML, aria-labels cho widgets, keyboard navigation cho edit controls, color contrast ≥ 4.5:1 |

**Các thành phần giao diện cho:**

| Widget | Mô tả |
|---|---|
| **Gauge** | Đồng hồ tốc độ, RPM (vòng cung hoặc số lớn) |
| **Line Chart** | Biểu đồ real-time cho CoolantTemp, BatteryVoltage (rolling 60s) |
| **Status Panel** | GearPosition, TurnSignal, DoorStatus dưới dạng icon/badge |
| **Alarm Bar** | Thanh cảnh báo trên cùng, đổi màu theo level |
| **Signal Table** | Bảng all signals: name, value, unit, min, max, writable |
| **Edit Control** | Ô input + nút Send cho signal writable (gửi PUT request → CAN) |
| **History Modal** | Click signal → popup chart lịch sử (fetch từ REST API) |
| **System Info** | Bus status, message count, uptime |

**Layout mẫu:**

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

Dev Mode thì liệt kê hết ra signal (kể cả không hiển thị trên dashboard) để dễ debug. User Mode thì chỉ hiển thị signal quan trọng, ẩn các signal phụ trợ. Có thể toggle giữa 2 chế độ bằng thẻ tab trên giao diện.
```

---

## 2.7 Monitoring (Watchdog / Supervisor / mDNS)

> Cung cấp lớp giám sát và tự phục hồi cho toàn bộ hệ thống CarPC.

| Chức năng | Yêu cầu |
|---|---|
| Watchdog | Giám sát process chính (CAN RW, signal processor, FastAPI) và tự khởi động lại khi crash |
| Supervisor | Quản lý luồng, khởi động/tắt các thành phần: CAN Reader/Writer, Processor, Storage, FastAPI, Video Stream |
| mDNS | Quảng bá dịch vụ (FastAPI + WebSocket + video) trên mạng LAN để frontend dễ tìm |
| Health check | Endpoint `/health` trả trạng thái hệ thống (uptime, process, memory, disk, error) |
| Logging | Structured JSON logs (stdout + file rotation). Levels: DEBUG / INFO / WARNING / ERROR. Config: `logging.level`, `logging.file_path`, `logging.max_size_mb`, `logging.backup_count` |

### 2.7.1 CarPC System Metrics (`/system/metrics`)

> Theo dõi thông tin tài nguyên hệ thống CarPC real-time qua API endpoint và frontend dashboard.

**Thư viện:** `psutil>=5.9` (cross-platform system info).

**Endpoint:** `GET /system/metrics` — trả về JSON snapshot tài nguyên, poll mỗi 3 giây từ frontend.

| Nhóm | Metric | Mô tả |
|---|---|---|
| **CPU (system)** | `cpu_percent` | %CPU tổng hệ thống |
| | `cpu_percent_per_core` | %CPU mỗi core |
| | `cpu_count_logical` / `cpu_count_physical` | Số core logic / vật lý |
| | `cpu_freq_current_mhz` / `cpu_freq_max_mhz` | Tần số CPU hiện tại / tối đa |
| **CPU (process)** | `process_cpu_percent` | %CPU của process CAN-HMI |
| **RAM (system)** | `ram_total_mb`, `ram_used_mb`, `ram_available_mb`, `ram_percent` | Bộ nhớ RAM hệ thống |
| **Memory (process)** | `process_memory_rss_mb` | Resident Set Size (bộ nhớ thực tế) |
| | `process_memory_vms_mb` | Virtual Memory Size |
| | `process_memory_percent` | % RAM process chiếm |
| | `process_threads` | Số thread của process |
| | `process_open_files` | Số file descriptor đang mở |
| **Swap** | `swap_total_mb`, `swap_used_mb`, `swap_percent` | Swap space |
| **Disk** | `disk_total_gb`, `disk_used_gb`, `disk_free_gb`, `disk_percent` | Disk working directory |
| **Network I/O** | `net_bytes_sent`, `net_bytes_recv` | Tổng bytes gửi/nhận (cumulative) |
| | `net_packets_sent`, `net_packets_recv` | Tổng packets gửi/nhận |
| **Signal Queue** | `queue_size`, `queue_maxsize`, `queue_usage_percent` | Trạng thái `asyncio.Queue` (RX pipeline) |
| **Heap / GC** | `heap_allocated_mb` | RSS process (~ heap) |
| | `gc_objects` | Số object theo dõi bởi garbage collector |
| **Async Tasks** | `asyncio_tasks` | Số asyncio task đang chạy |
| **Runtime** | `uptime_seconds` | Thời gian chạy |
| | `python_version`, `platform` | Phiên bản Python, hệ điều hành |

**Response mẫu:**

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

**Frontend widget:** Panel "CarPC System Monitor" gồm 12 card (CPU, Process CPU, RAM, Process Memory, Disk, Swap, Queue, Heap/GC, Network, Async Tasks, Uptime, Platform) với progress bar màu sắc theo ngưỡng (xanh < 70%, vàng 70-90%, đỏ > 90%).

---

## 2.8 Đồng thời & Độ tin cậy (Concurrency, Backpressure & Graceful shutdown)

> Hướng dẫn vận hành để đảm bảo hệ thống ổn định dưới tải và khi xảy ra sự cố.

- Mô hình runtime:
  - Sử dụng `asyncio` làm runtime chính cho I/O-bound tasks (CAN reader/writer, FastAPI, WebSocket). Các tác vụ CPU-bound (ví dụ: xử lý tín hiệu nặng với numpy) nên offload sang `ProcessPoolExecutor` hoặc tách thành worker process/service riêng để tránh block event loop.
  - FastAPI chạy trên `uvicorn` (ASGI). Nếu scale bằng nhiều worker, lưu ý `SignalStore` là in-memory: khi dùng nhiều worker cần chuyển `SignalStore` sang shared store (Redis) hoặc dùng message broker.

- Hàng đợi & Backpressure:
  - Kết nối các thành phần bằng `asyncio.Queue` có `maxsize` (config: `processor.max_queue_size`). Khi queue đầy, áp dụng chính sách `queue_policy`: `block` | `drop_oldest` | `reject_writes` (mặc định: `reject_writes`).
  - Propagate backpressure: nếu storage/writer chậm, hệ thống nên giảm tốc độ broadcast (WS) hoặc trả lỗi `503` cho các write request mới.
  - Sử dụng batch insert (`storage.batch_size`, `batch_interval_sec`) để giảm I/O overhead và ổn định throughput.

- Ghi CAN an toàn:
  - Serialise các thao tác ghi trên mỗi bus bằng `asyncio.Lock` hoặc duy trì một writer task đơn để đảm bảo thứ tự và tránh tranh chấp trên interface.
  - Áp dụng rate-limit cho write (per-signal và global) và validate giá trị trước khi encode gửi xuống bus.

- Graceful shutdown & Flush:
  - Khi nhận `SIGINT`/`SIGTERM`: đánh dấu `shutting_down`, từ chối write mới (trả 503), gửi notification (`system_status`/`shutdown`) tới clients qua WS.
  - Gọi `storage.flush()` và chờ tối đa `shutdown_timeout_sec` (config, mặc định 10s) để persist buffer; nếu timeout, log warning và đóng kết nối an toàn.
  - Đóng CAN interface sau khi writer queue đã được xử lý, rồi đóng WebSocket connections.

- Giám sát & phục hồi:
  - Duy trì cả `/health` (liveness) và `/ready` (readiness). Supervisor/watchdog (systemd, docker restart policy, hoặc supervisor) sử dụng các endpoint này để quyết định restart.
  - Export metrics quan trọng: queue length, queue drops, storage backlog, write latency, error counts.

- Logging & Observability:
  - Structured JSON logs với fields: `timestamp`, `component`, `correlation_id`, `task`, `level`, `message`.
  - Bổ sung metrics cho Prometheus (ví dụ: `can_msgs_total`, `signal_updates_total`, `write_errors_total`, `queue_drops_total`).

- Scale & kiến trúc phân tán:
  - Single-process `asyncio` đơn giản và phù hợp khi dùng in-memory `SignalStore`. Để scale ngang, chuyển state ra ngoài (Redis) hoặc dùng message bus (NATS/RabbitMQ).
  - Lưu ý: nhiều uvicorn workers đồng thời yêu cầu shared state; nếu không có shared state, tránh dùng nhiều workers.

- Cấu hình đề xuất (thêm):
  - `processor.queue_policy: drop_oldest|block|reject`
  - `writer.rate_limit_per_sec`, `writer.burst`
  - `shutdown_timeout_sec: 10`
  - `supervisor.watchdog_interval_sec: 5`

- Kiểm thử:
  - Test backpressure: làm chậm storage để kiểm tra behaviour (drops, 503, sampling).
  - Test graceful shutdown: đảm bảo `storage.flush()` được gọi và writer queue được drained.

---

## 2.9 Error Taxonomy & Recovery

> Phân loại lỗi hệ thống và chiến lược phục hồi cho từng loại.

| Mã lỗi | Nguồn | Mô tả | Severity | Recovery Strategy |
|---|---|---|---|---|
| `ERR_CAN_BUS_OFF` | CAN I/O | Bus-off state do lỗi phần cứng hoặc quá tải | CRITICAL | Auto-reconnect với exponential backoff (1s → max 30s). Log lỗi, tạm dừng write. Sau 5 lần thất bại liên tiếp → alert Supervisor |
| `ERR_CAN_TIMEOUT` | CAN Reader | Không nhận frame trong thời gian kỳ vọng (> 3× cycle time) | WARNING | Log warning, gửi stale status cho signal. Nếu > 30s → đánh dấu signal offline |
| `ERR_PARSE_DB` | Parser | File can.json không hợp lệ hoặc bị hỏng | CRITICAL | Reject file, log chi tiết lỗi (file, line, reason). Hệ thống vẫn chạy với các file hợp lệ còn lại |
| `ERR_DECODE_FRAME` | CAN Reader | Frame nhận được không match can.json definition (DLC mismatch, unknown ID) | WARNING | Log + skip frame, increment error counter. Nếu error rate > threshold → alert |
| `ERR_STORAGE_WRITE` | Storage | SQLite write thất bại (disk full, lock, corruption) | CRITICAL | Retry 3 lần với backoff. Nếu vẫn thất bại → buffer in-memory (max 10 000 records), alert Supervisor. Khi DB phục hồi → flush buffer |
| `ERR_STORAGE_FULL` | Storage | Disk usage vượt ngưỡng `storage.max_disk_mb` | WARNING | Chạy retention purge ngay lập tức. Nếu vẫn không đủ → giảm batch size, log warning |
| `ERR_WS_SLOW_CLIENT` | WebSocket | Client không consume message kịp (send buffer > threshold) | WARNING | Drop oldest messages cho client đó (per-client buffer). Nếu client quá chậm > 60s → disconnect |
| `ERR_API_RATE_LIMIT` | FastAPI | Write request vượt rate limit | INFO | Trả HTTP 429 + `Retry-After` header |
| `ERR_CONFIG_INVALID` | Config | YAML config không hợp lệ (schema mismatch) | CRITICAL | Reject startup, log chi tiết lỗi validation (field, expected, got) |
| `ERR_ENCODER_FAIL` | CAN Writer | Encode signal thất bại (out of range, unknown signal) | WARNING | Reject write request (HTTP 400), log reason. Không gửi frame lên bus |

> **Error counters:** Mỗi loại lỗi có counter riêng, export qua Prometheus metric và hiển thị trong `/api/v1/system/status`.

---

## 2.10 Deployment & Service

> Hướng dẫn triển khai hệ thống dưới dạng service trên Linux embedded (CarPC).

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

### Docker (tùy chọn)

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

| Variable | Default | Mô tả |
|---|---|---|
| `CANHMI_CONFIG` | `config/system.json` | Path to main config file |
| `CANHMI_LOG_LEVEL` | `INFO` | Override logging level |
| `CANHMI_API_KEY` | (from config) | Override API key |
| `CANHMI_DB_PATH` | (from config) | Override SQLite path |

---

## 2.11 Security

> Bảo mật cho API, WebSocket, và CAN write path.

| Hạng mục | Yêu cầu |
|---|---|
| Authentication | API key (header `X-API-Key`) cho REST. JWT (optional) cho multi-user. WS: token via query param `?token=` |
| Authorization | Signal write access kiểm tra `writable` flag trong `signal_config`. Chỉ signal có `writable=true` mới cho phép PUT |
| Input validation | Pydantic model validate tất cả request body. Signal value phải nằm trong `[min, max]` range từ config/can.json |
| Rate limiting | Global: 100 req/s (configurable). Write: 10 req/s per signal. Trả HTTP 429 + `Retry-After` |
| TLS | Production: HTTPS (reverse proxy nginx/caddy). Dev: plain HTTP OK |
| Secret management | API key không hard-code; đọc từ env var `CANHMI_API_KEY` hoặc secret file. Config file không commit secret |
| CAN bus safety | Validate signal value range trước khi encode. Reject out-of-range writes (HTTP 400). Log tất cả write operations |
| CORS | Whitelist origins trong config (`api.cors_origins`). Không dùng `*` trong production |
| Headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` cho frontend |

---

## 3. Cấu trúc thư mục dự án

```
car-hmi/
├── docs/
│   └── requirement.md          # Tài liệu yêu cầu (file này)
├── README.md                   # Hướng dẫn cài đặt & chạy
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
│   │   ├── simulator.py        # CANSimulator — reads can.json, generates random signals
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

## 4. Luồng dữ liệu (Data Flow)

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

**Luồng ghi ngược (Write-back):**

1. User nhập giá trị mới trên Web Dashboard
2. Frontend gọi `PUT /signals/{signal_name}` với giá trị mới (`WriteSignalRequest`)
3. FastAPI validate (Pydantic) → chuyển cho CAN Writer
4. CAN Writer encode signal thành CAN frame (via DatabaseLoader) → gửi lên bus (async, với Lock)
5. CAN Simulator (hoặc ECU thật) nhận và phản hồi
6. Response: 202 ACCEPTED với `{"signal_name": ..., "value": ..., "queued_at": ...}`

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

| Loại test | Phạm vi | Công cụ |
|---|---|---|
| Unit test | Từng module riêng lẻ (simulator, reader, processor, storage) | `pytest` + `pytest-asyncio` |
| Integration test | API endpoints + WebSocket + CAN I/O kết hợp | `httpx` + `pytest` |
| E2E test | Full stack: Simulator → Reader → Processor → API → Dashboard | Manual / Playwright (tùy chọn) |
| Performance test | Throughput, latency theo NFR | `locust` hoặc custom benchmark |

**Mock & Fixtures:**
- CAN bus: dùng `python-can` virtual interface (`VirtualBus`) cho unit/integration test — không cần hardware.
- Storage: dùng in-memory SQLite (`:memory:`) cho test nhanh; file-based cho integration.
- API: `httpx.AsyncClient` + `TestClient` từ FastAPI.
- Clock: mock `time.time()` / `asyncio.get_event_loop().time()` cho deterministic timing tests.

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

## 6. Cấu hình (Configuration)

### system.json

```json
{
  "can": [
    {
      "interface": "virtual",
      "channel": "vcan0",
      "bitrate": 500000,
      "can_json_path": "config/can.json"
    },
    {
      "interface": "virtual",
      "channel": "vcan1",
      "bitrate": 500000,
      "can_json_path": "config/can1.json"
    }
  ],
  "simulator": {
    "enabled": true,
    "default_cycle_ms": 50,
    "can_json_path": "config/can.json"
  },
  "api": {
    "host": "0.0.0.0",
    "port": 8000,
    "api_key": "change-me-in-production",
    "ws_heartbeat_interval_sec": 5,
    "cors_origins": ["http://localhost:8000"]
  },
  "storage": {
    "engine": "sqlite",
    "sqlite_path": "data/signals.db",
    "batch_size": 100,
    "batch_interval_sec": 2.0,
    "retention_days": 30,
    "max_disk_mb": 2048
  },
  "processor": {
    "smoothing_window": 5,
    "max_update_rate_hz": 10.0,
    "max_queue_size": 10000,
    "queue_policy": "reject"
  },
  "writer": {
    "rate_limit_per_sec": 10,
    "burst": 5
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

## 7. Cách chạy hệ thống

### Development (local)

```bash
# 1. Cài đặt
pip install -e ".[dev]"

# 2. Chạy toàn bộ stack (simulator + reader + processor + API + frontend)
python -m src.core.runner --config config/system.json

# 3. Hoặc chạy từng phần riêng
python -m src.can_simulator --dbc-dir db/can_db/ --a2l-dir db/ecu_db/ # Chỉ simulator
python -m src.api.app --config config/system.json                     # Chỉ API server

# 4. Mở dashboard
# Truy cập http://localhost:8000 trên trình duyệt

# 5. Chạy tests
pytest --cov=src --cov-report=term-missing -q
ruff check src/ tests/
```

### Production (systemd / Docker)

Xem chi tiết tại **Section 2.10 — Deployment & Service**:
- **systemd:** copy `systemd/can-hmi.service` → `/etc/systemd/system/`, `systemctl enable --now can-hmi`
- **Docker:** `docker build -t can-hmi . && docker run -p 8000:8000 can-hmi`
- **CAN bus setup:** xem hướng dẫn `vcan` / `socketcan` trong section 2.10

---

## 8. Tiêu chí chấp nhận (Acceptance Criteria)

| # | Tiêu chí | Trạng thái |
|---|---|---|
| AC-1 | CAN Simulator phát frame đúng theo CANdb (candb), đúng cycle time | ⬜ |
| AC-2 | CAN Reader decode chính xác tất cả signal trong CANdb/candb | ⬜ |
| AC-3 | Signal Processor áp dụng smoothing, phát alarm khi vượt ngưỡng | ⬜ |
| AC-4 | Storage lưu được ≥ 1000 sample/s mà không mất dữ liệu | ⬜ |
| AC-5 | REST API trả về snapshot signal với latency < 50 ms | ⬜ |
| AC-6 | WebSocket push signal update với latency < 100 ms (end-to-end) | ⬜ |
| AC-7 | Web Dashboard hiển thị real-time gauge/chart cập nhật mượt | ⬜ |
| AC-8 | User có thể ghi giá trị signal từ Dashboard → CAN bus thành công | ⬜ |
| AC-9 | Tất cả unit test pass, coverage ≥ 80% | ⬜ |
| AC-10 | Hệ thống chạy ổn định ≥ 1 giờ liên tục không crash | ⬜ |
| AC-11 | Graceful shutdown: flush storage, drain writer queue, close WS trong ≤ `shutdown_timeout_sec` | ⬜ |
| AC-12 | Config validation báo lỗi rõ ràng khi YAML không hợp lệ (field name, expected vs got) | ⬜ |
| AC-13 | Alarm lifecycle: trigger → persist alarm_log → WS push → ACK → resolve hoạt động đầy đủ | ⬜ |
| AC-14 | Security: chỉ signal có `writable=true` cho phép PUT; API key required cho REST | ⬜ |
| AC-15 | CAN bus reconnect tự động sau bus-off, trong ≤ 5 giây (P95) | ⬜ |
| AC-16 | Error taxonomy: tất cả error codes được log và error counter export qua `/api/v1/system/status` | ⬜ |

### Non-Functional Requirements (NFR)

| NFR | Yêu cầu |
|---|---|
| Latency | End-to-end CAN → WebSocket ≤ 100 ms (P95) |
| Throughput | Xử lý ≥ 1 000 signals/s sustained |
| Memory | RSS ≤ 512 MB trong điều kiện vận hành bình thường |
| Startup | Cold start → ready ≤ 5 giây |
| Config validation | Báo lỗi rõ ràng khi config YAML không hợp lệ. Dùng **Pydantic `BaseSettings`** để validate schema (type, range, required fields). Những field thiếu hoặc sai type sẽ báo lỗi ngay khi startup với message rõ ràng (field name, expected type, got value) |
| Graceful shutdown | Khi nhận SIGTERM/SIGINT: flush storage buffer, close CAN bus, close WS connections |
| Disk usage | SQLite DB ≤ 2 GB (with retention 30 ngày). Config: `storage.max_disk_mb: 2048` |
| CAN reconnect | Bus-off → reconnect ≤ 5 giây (P95). Max 5 retries trước khi alert Supervisor |
| Error rate | Decode error rate ≤ 0.1% tổng số frame nhận được (sustained) |

---

## 9. Roadmap triển khai

| Phase | Nội dung | Ưu tiên |
|---|---|---|
| **Phase 1** | CAN Simulator + CANdb (candb) + CAN Reader/Writer | 🔴 Cao |
| **Phase 2** | Signal Processor + Storage (SQLite) + Alarm detection | 🔴 Cao |
| **Phase 3** | FastAPI REST + WebSocket endpoints + Config validation (Pydantic) | 🔴 Cao |
| **Phase 4** | Web Dashboard (gauge, chart, table, edit) + Offline fallback | 🟡 Trung bình |
| **Phase 5** | Alarm lifecycle (ACK/resolve/history) + Notification (email/webhook/toast) | 🟡 Trung bình |
| **Phase 6** | Security (2.11) + Rate-limit + Error Taxonomy (2.9) + Deployment (2.10, systemd/Docker) | 🟢 Thấp |
| **Phase 7** | Mở rộng: CAN FD, TimescaleDB, multi-bus, Prometheus metrics | 🟢 Thấp |

---

## 10. Glossary

| Thuật ngữ | Giải nghĩa |
|---|---|
| CAN | Controller Area Network — giao thức truyền thông nối tiếp giữa các ECU trên xe |
| CAN FD | CAN with Flexible Data-rate — mở rộng CAN với payload lớn hơn (lên tới 64 bytes) và bitrate cao hơn |
| DBC | Database CAN — định dạng file mô tả CAN messages và signals (Vector standard) |
| A2L | ASAM MCD-2 MC description file — mô tả measurement và calibration data của ECU |
| CANdb / candb | Custom JSON-based CAN database format dùng trong project này |
| DLC | Data Length Code — số byte dữ liệu trong một CAN frame (0–8 với CAN 2.0, 0–64 với CAN FD) |
| ECU | Electronic Control Unit — bộ điều khiển điện tử trên xe |
| Bus-off | Trạng thái lỗi của CAN controller khi error counter vượt ngưỡng, node tự ngắt khỏi bus |
| mDNS | Multicast DNS — phát hiện dịch vụ trên mạng LAN không cần DNS server |
| RTSP | Real Time Streaming Protocol — giao thức stream video |
| GStreamer | Framework xử lý multimedia (dùng cho video DMS/OMS) |
| DMS / OMS | Driver Monitoring System / Occupant Monitoring System — camera giám sát tài xế / hành khách |
| HMI | Human-Machine Interface — giao diện người–máy |
| NFR | Non-Functional Requirement — yêu cầu phi chức năng (hiệu năng, bảo mật, ...) |
| RSS | Resident Set Size — lượng RAM thực tế process đang dùng |
| ASGI | Asynchronous Server Gateway Interface — chuẩn giao tiếp cho Python async web servers |
