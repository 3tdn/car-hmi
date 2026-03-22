# 04 — Luồng dữ liệu & Signal Pipeline

> Mô tả chi tiết từng bước dữ liệu đi từ CAN Bus → WebSocket client  
> Phiên bản: 0.8.0

---

## 1. Tổng quan luồng dữ liệu (End-to-End)

```
[Vehicle ECU / Simulator]
         │
         │  CAN frame (raw bytes, 8 byte max / CAN FD 64 byte)
         │  Message ID + Data
         ▼
┌─────────────────────────────────────────────┐
│  CANReader (can_io/reader.py)               │
│  ─────────────────────────────────────────  │
│  1. Nhận can.Message từ bus (async)         │
│  2. Lookup message ID trong DatabaseLoader  │
│  3. Decode: raw bytes → dict[str, float]    │
│     VD: {VehicleSpeed: 84.1, EngineRPM: 2500.0}│
│  4. Tạo DecodedFrame(raw, signals, msg_name)│
│  5. Đẩy vào asyncio.Queue                  │
└───────────────────┬─────────────────────────┘
                    │  asyncio.Queue[DecodedFrame]
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

---

## 2. CAN Frame Decode

### Ví dụ thực tế

**DBC file** (`db/can_db/m_dummy.dbc`):
```dbc
BO_ 256 VCU_Status: 8 VCU
 SG_ VehicleSpeed : 0|16@1+ (0.01,0) [0|655.35] "km/h" Dashboard
 SG_ EngineRPM    : 16|16@1+ (0.125,0) [0|8191.875] "rpm" Dashboard
```

**CAN frame nhận được**:
```
Message ID: 0x100 (256)
Data:       [0x1A, 0x15, 0xC8, 0x13, ...]
```

**Sau decode**:
```python
{
    "VehicleSpeed": 84.1,    # 0x151A * 0.01 = 54.02... (ví dụ minh họa)
    "EngineRPM": 2500.0
}
```

### DatabaseLoader — Strategy Pattern

```
add_paths(["db/can_db/"])
       │
       ▼
Scan files → chọn parser theo extension:
  .dbc  → DBCParser (cantools)
  .json → CANdbJsonParser
  .a2l  → _A2LParser (read-only)
       │
       ▼
_messages: dict[int, ParsedMessage]   ← lookup by msg_id
_signals:  dict[str, ParsedSignal]    ← lookup by name
```

---

## 3. Signal Processing Pipeline — Chi tiết 4 Stage

### Stage 1: SmoothingFilter

**Mục đích**: Làm mượt nhiễu tín hiệu analog (cảm biến, ADC).

**Thuật toán**: Sliding window (Moving Average) hoặc EMA (Exponential Moving Average).

```
Input:  [84.1, 85.3, 83.8, 86.0, 84.5]   (5 giá trị gần nhất)
Output: 84.74                              (trung bình)
```

**Config** (`bus.yaml`):
```yaml
processor:
  smoothing_window: 5   # kích thước cửa sổ trượt
```

---

### Stage 2: RateLimiter

**Mục đích**: Tránh spam tín hiệu thay đổi quá nhanh ra WebSocket và DB. ECU có thể gửi cùng message ID ở 10 ms/frame nhưng frontend chỉ cần 50 ms/frame.

```
last_update["VehicleSpeed"] = T
frame arrives at T + 5ms  → Δt = 5ms < 50ms → DROP
frame arrives at T + 60ms → Δt = 60ms > 50ms → PASS
```

**Config**:
```yaml
processor:
  max_update_rate_hz: 20   # = 50ms minimum interval
```

---

### Stage 3: ComputedSignals

**Mục đích**: Tính toán tín hiệu phái sinh (virtual signals) không có trực tiếp trên bus.

**Ví dụ công thức**:
```python
# Engine Power (kW)
power_kw = engine_rpm * torque_nm / 9549.0

# Battery Power  
battery_power = battery_voltage * battery_current / 1000.0
```

Các formula được đăng ký thông qua:
```python
computed.add_formula("EnginePower", lambda s: s.get("EngineRPM", 0) * s.get("ActualTorque", 0) / 9549.0)
```

---

### Stage 4: AlarmChecker

**Mục đích**: Phát hiện tín hiệu vượt ngưỡng và kích hoạt cảnh báo.

**Ngưỡng** (`config/alarms.yaml`):
```yaml
alarms:
  EngineRPM:
    critical_high: 7500.0
  BrakePressure:
    critical_high: 180.0 
value >= critical_high  →  status = "critical",  level = "critical"
value >= warning_high   →  status = "warning",   level = "warning"
value <= critical_low   →  status = "critical",  level = "critical"
value <= warning_low    →  status = "warning",   level = "warning"
otherwise               →  status = "ok"
```

**Khi alarm kích hoạt**:
1. `AlarmChecker` emit `Alarm` event qua handler callback
2. Handler (`AppRunner._on_alarm`) chạy:
   - INSERT vào `alarm_log` table trong SQLite
   - `ConnectionManager.broadcast_alarm()` → push JSON qua WebSocket tới tất cả subscribers kênh `alarms`

---

## 4. Backpressure — Queue Policy

Khi pipeline xử lý chậm hơn CAN reader produce (v.d. CPU bận, DB slow):

| Policy | Hành vi |
|---|---|
| `drop_oldest` | Xóa frame cũ nhất trong queue, thêm frame mới (mặc định) |
| `block` | CANReader block cho đến khi queue có chỗ |
| `reject` | Bỏ qua frame mới, log warning |

Thay đổi live (không restart app):
```
POST /config/processor
{"queue_policy": "drop_oldest", "max_queue_size": 5000}
```

---

## 5. Storage — Batch Insert

Để tránh ghi DB quá nhiều lần (mỗi signal update một lần):

```
Signal updates → Buffer list[SignalRecord]
                      │
              Buffer đầy (batch_size=100)
                   hoặc
              Timer tick (batch_interval_sec=2.0)
                      │
                      ▼
              SQLite batch INSERT
              (1 transaction nhiều rows)
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
                 if client.wants_signal("VehicleSpeed"):  # hoặc "*"
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

`AppRunner._metrics_broadcaster()` chạy mỗi 3 giây, thu thập system metrics qua `psutil` và push tới WebSocket clients đã subscribe kênh `"metrics"`:

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

Frontend dev mode hiển thị panel metrics real-time từ stream này.

---

## 8. Startup Sequence

```
can-hmi                    (CLI entry point: src/core/runner.py:main())
    │
    ▼
AppRunner.start()
    ├── _setup_logging()
    ├── DatabaseLoader.add_paths()     ← scan db/ thư mục
    ├── SignalStore.bulk_update()      ← seed tất cả signal names + units
    ├── init_db() / SQLiteRepository  ← create tables nếu chưa có
    ├── create_bus()                  ← mở CAN interface
    ├── SignalPipeline + stages        ← thêm 4 stage
    ├── CANReader                     ← async producer
    ├── CANWriter                     ← encode + send
    ├── CANSimulator (if enabled)     ← virtual bus producer
    ├── FastAPI + uvicorn             ← serve REST + WS + static frontend
    ├── Watchdog task                 ← health monitoring
    └── Metrics broadcaster task      ← push metrics qua WS
         │
         └── asyncio.gather(*tasks)   ← tất cả chạy song song
```

---

## 9. Graceful Shutdown

Khi nhận `SIGINT` hoặc `SIGTERM`:

```
AppRunner.shutdown()
    ├── _shutting_down = True         ← dừng watchdog + metrics loops
    ├── CANReader.stop()             ← drain queue, đóng bus
    ├── CANSimulator.stop()          ← nếu đang chạy
    ├── SignalPipeline.flush()       ← flush buffer còn lại vào DB
    ├── SQLiteRepository.close()     ← đóng DB connection
    └── FastAPI shutdown             ← close WebSocket connections
```

Timeout mặc định: 10 giây trước khi force exit.
