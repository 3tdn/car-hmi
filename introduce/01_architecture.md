# 01 — Kiến trúc hệ thống

> Tài liệu kiến trúc nội bộ CAN-HMI — dành cho developer review  
> Phiên bản: 0.8.0

---

## 1. Tổng quan kiến trúc (C4 Level 2 — Containers)

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

| File | Trách nhiệm |
|---|---|
| `bus_factory.py` | Tạo instance `can.BusABC` theo config (socketcan, virtual, pcan, vector…) |
| `parser.py` | Load và parse `config/can.json` thành `ParsedMessage` / `ParsedSignal`. Decode/encode CAN frame bằng bit manipulation tự viết |
| `reader.py` | `CANReader`: async producer, đọc frame từ bus → giải mã → đưa vào `asyncio.Queue`. Hỗ trợ reconnect tự động và backpressure |
| `writer.py` | `CANWriter`: encode signal value → CAN frame → gửi lên bus. `CANWriterRouter`: định tuyến ghi tín hiệu đến đúng kênh CAN |

**Hỗ trợ nhiều kênh CAN**: Mỗi kênh (vcan0, vcan1…) có `DatabaseLoader`, `CANReader`, `CANWriter` riêng.
Tất cả reader đổ vào chung một `asyncio.Queue` → pipeline xử lý tập trung.
`CANWriterRouter` định tuyến O(1) tín hiệu ghi đến đúng writer/kênh.

**DatabaseLoader** là lớp trung tâm load can.json:
```python
db_loader = DatabaseLoader()
db_loader.load("config/can.json")   # load toàn bộ message/signal
frame = db_loader.decode_frame(msg_id, raw_bytes)  # → dict[str, float]
msg   = db_loader.encode_signal("VehicleSpeed", 60.0)  # → can.Message
```

---

### 2.2 `src/processor/` — Signal Processing Pipeline

Pipeline gồm 4 stage chạy tuần tự, áp dụng **Pipeline Pattern**:

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
│  Stage 2        │  RateLimiter       — Giới hạn tần suất cập nhật (max_hz)
│  Rate Limiter   │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Stage 3        │  ComputedSignals   — Tính tín hiệu phái sinh (VD: Power = RPM × Torque)
│  Computed       │
└────────┬────────┘
         ▼
┌─────────────────┐
│  Stage 4        │  AlarmChecker      — So sánh với ngưỡng config/alarms.json
│  Alarm Check    │                      Phát ra Alarm event → lưu DB + push WS
└────────┬────────┘
         ▼
    SignalStore (update)  +  SQLite batch insert
```

Mỗi stage implement abstract class `ProcessingStage`:
```python
class ProcessingStage(ABC):
    @abstractmethod
    def process(self, signals: dict[str, float]) -> dict[str, float]: ...
```

---

### 2.3 `src/core/signal_store.py` — Signal Store

**Observer Pattern**: SignalStore là subject, các subscriber (WebSocket manager, metrics) là observer.

```python
store = SignalStore()
store.subscribe(callback)          # đăng ký nhận cập nhật
await store.update("EngineRPM", 2500.0)  # → tự động notify tất cả subscribers
sv = store.get("EngineRPM")        # → SignalValue(value, status, timestamp, unit)
snap = store.get_snapshot()        # → toàn bộ cache hiện tại
```

---

### 2.4 `src/storage/` — Storage Layer

**Repository Pattern** với interface `ISignalRepository` cho phép swap backend:

```
ISignalRepository (ABC)
        │
        └── SQLiteRepository  (aiosqlite, async)
```

Database schema gồm 3 bảng:

| Bảng | Mục đích |
|---|---|
| `signal_log` | Chuỗi thời gian (timestamp, signal_name, value, unit) — có index |
| `alarm_log` | Lịch sử cảnh báo (level, value, threshold, acknowledged, resolved_at) |
| `signal_config` | Cấu hình hiển thị per-signal (unit, min, max, widget_type, writable) |

`DataExporter` cho phép xuất dữ liệu ra CSV / JSON qua API.

---

### 2.5 `src/api/` — FastAPI Backend

**Factory Pattern**: `create_app()` tạo FastAPI application và inject dependencies qua `app.state`.

```
FastAPI app
├── /signals        (REST)   — đọc snapshot, lịch sử, ghi ngược
├── /alarms         (REST)   — lịch sử alarm, acknowledge, resolve
├── /config         (REST)   — signal config, processor config, app config
├── /system         (REST)   — /health, /ready, /metrics
└── /ws/            (WS)     — /signals, /alarms, /all, /subscribe
```

Authentication: `X-API-Key` header (REST), `?token=` query param (WebSocket). Nếu `api_key` rỗng hoặc là placeholder → auth tắt (dev mode).

---

### 2.6 `src/can_simulator/` — CAN Simulator

Sử dụng `CANSimulator` đọc trực tiếp `can.json`, sinh giá trị ngẫu nhiên trong `[minimum, maximum]`:

| Chế độ | Class | Mô tả |
|---|---|---|
| `can_json` | `CANSimulator` | Sinh tín hiệu ngẫu nhiên [min, max] từ can.json theo chu kỳ cố định |

Simulator dùng **virtual bus** riêng, tách biệt với bus reader (python-can virtual cho phép nhiều instance cùng channel).

---

### 2.7 `src/core/runner.py` — Application Orchestrator

`AppRunner` là trung tâm điều phối khởi động toàn hệ thống theo thứ tự:

1. Setup logging (rotating file + console)
2. Load CAN database cho mỗi kênh (can.json per channel)
3. Seed SignalStore với initial values từ tất cả DB của các kênh
4. Khởi tạo SQLite storage
5. Tạo CAN Bus instance cho mỗi kênh
6. Khởi tạo Signal Pipeline + các stage (dùng chung 1 queue)
7. Tạo CANReader + CANWriter
8. Khởi động CAN Simulator (nếu bật)
9. Tạo FastAPI server (uvicorn)
10. Tạo Watchdog task + Metrics broadcaster task
11. `asyncio.gather()` tất cả tasks

---

## 3. Design Patterns áp dụng

| Pattern | Nơi áp dụng |
|---|---|
| **Pipeline** | `SignalPipeline` — chain processing stages |
| **Observer / Pub-Sub** | `SignalStore.subscribe()` — push tới WS clients |
| **Repository** | `ISignalRepository` / `SQLiteRepository` — tách storage logic |
| **Factory** | `create_app()` — FastAPI application factory; `create_bus()` — CAN bus factory |
| **Strategy** | `DatabaseLoader` — loads can.json, built-in bit-level decode/encode |

---

## 4. Sơ đồ PlantUML

Toàn bộ sơ đồ kiến trúc nằm trong `diagram/`:

| File | Loại |
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
# hoặc VS Code extension: PlantUML by jebbs
```

---

## 5. Cấu hình hệ thống

Tất cả runtime config trong `config/system.json`:

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
  api_key: ""                 # rỗng = auth tắt

storage:
  sqlite_path: data/signals.db
  batch_size: 100
  retention_days: 30
```

Ngưỡng cảnh báo trong `config/alarms.json`:
```yaml
alarms:
  EngineRPM:
    critical_high: 7500.0
  BrakePressure:
    critical_high: 180.0
```
