# 02 — API Reference

> Toàn bộ REST endpoints và WebSocket protocol của CAN-HMI backend  
> Base URL: `http://<host>:8000` (mặc định `localhost:8000`)

---

## Authentication

| Phương thức | Mô tả |
|---|---|
| REST | Header `X-API-Key: <key>` |
| WebSocket | Query param `?token=<key>` |

Nếu `api_key` trong `config/system.json` để trống hoặc là placeholder (`change-me-in-production`) → **auth tắt** (dev mode, không cần gửi key).

---

## REST Endpoints

### Signals

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/signals` | Lấy snapshot tất cả tín hiệu hiện tại |
| `GET` | `/signals/available` | Metadata đầy đủ (unit, min/max, alarm thresholds, writable) |
| `GET` | `/signals/{name}` | Lấy giá trị 1 tín hiệu |
| `GET` | `/signals/{name}/history` | Lịch sử tín hiệu (time-series từ DB) |
| `PUT` | `/signals/{name}` | Ghi giá trị vào CAN Bus (trả về 202 Accepted) |

> Note: The API supports both canonical signal names and standardized `std_name` aliases. Clients may call endpoints with either form (e.g. `/signals/HMI_FL_OccupantAge_years` or `/signals/HMI_FL_OccupantAge`) — the server resolves `std_name` → canonical internally. Responses include an optional `std_name` field when a mapping exists. See `STD_NAME_USAGE.md` for examples.

**GET /signals** — response:
```json
{
  "items": [
    {"signal_name": "VehicleSpeed", "value": 84.1, "unit": "km/h", "timestamp": 1742000000.0},
    {"signal_name": "EngineRPM",    "value": 2500.0, "unit": "rpm", "timestamp": 1742000000.1}
  ],
  "total": 2
}
```

**GET /signals/available** — response (một phần tử):
```json
{
  "items": [{
    "signal_name": "BrakePressure",
    "std_name": "BrakePressure", 
    "unit": "bar",
    "min_value": 0,
    "max_value": 200,
    "writable": false,
    "group_name": "chassis",
    "widget_type": "gauge",
    "alarm_warning_high": null,
    "alarm_critical_high": 180.0,
    "value": 12.5,
    "status": "ok",
    "timestamp": 1742000000.5
  }],
  "total": 210
}
```

**GET /signals/{name}/history** — query params:

| Param | Type | Default | Mô tả |
|---|---|---|---|
| `start` | float | null | Unix timestamp bắt đầu |
| `end` | float | null | Unix timestamp kết thúc |
| `limit` | int | 100 | Số bản ghi tối đa (max 10 000) |
| `offset` | int | 0 | Phân trang |

**PUT /signals/{name}** — request body:
```json
{"value": 60.0}
```
Response `202 Accepted`:
```json
{"signal_name": "VehicleSpeed", "value": 60.0, "queued_at": 1742000001.0}
```

---

### Alarms

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/alarms` | Danh sách lịch sử cảnh báo |
| `GET` | `/alarms/{id}` | Chi tiết 1 cảnh báo |
| `POST` | `/alarms/{id}/acknowledge` | Xác nhận đã nhận cảnh báo |
| `POST` | `/alarms/{id}/resolve` | Đánh dấu cảnh báo đã xử lý |

**GET /alarms** — query params:

| Param | Type | Mô tả |
|---|---|---|
| `signal_name` | string | Lọc theo tên tín hiệu |
| `level` | string | `info` / `warning` / `critical` |
| `acknowledged` | bool | Lọc trạng thái xác nhận |
| `start`, `end` | float | Khoảng thời gian |
| `limit`, `offset` | int | Phân trang |

**Alarm object**:
```json
{
  "id": 42,
  "signal_name": "EngineRPM",
  "level": "critical",
  "value": 7650.0,
  "threshold": 7500.0,
  "description": "Engine RPM alarm",
  "triggered_at": 1742000100.0,
  "acknowledged": false,
  "resolved_at": null
}
```

---

### Config

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/config` | Danh sách cấu hình hiển thị tất cả tín hiệu |
| `GET` | `/config/signal/{name}` | Cấu hình 1 tín hiệu |
| `PATCH` | `/config/signal/{name}` | Cập nhật cấu hình tín hiệu |
| `GET` | `/config/processor` | Cấu hình processor hiện tại |
| `POST` | `/config/processor` | Cập nhật processor config (áp dụng live) |
| `GET` | `/config/general` | Toàn bộ app config (JSON) |
| `PATCH` | `/config/general` | Cập nhật một phần app config |
| `POST` | `/config/general/reset` | Reset app config về mặc định |
| `GET` | `/config/alarms` | Cấu hình ngưỡng cảnh báo (raw YAML → JSON) |
| `POST` | `/config/alarms` | Cập nhật toàn bộ alarms config |
| `POST` | `/config/alarms/reset` | Reset alarms config về rỗng |

**PATCH /config/signal/{name}** — request body (tất cả optional):
```json
{
  "unit": "km/h",
  "min_value": 0.0,
  "max_value": 300.0,
  "widget_type": "gauge",
  "writable": false
}
```

**POST /config/processor** — request body:
```json
{"max_queue_size": 5000, "queue_policy": "drop_oldest"}
```
> Lưu ý: thay đổi `max_queue_size` sẽ trigger **live queue migration** (không restart app).

---

### Adaptive Restraint

> Phân tích rủi ro chấn thương (injury risk) theo cấu hình chiếm dụng xe và hệ thống an toàn.  
> Dữ liệu nguồn: SQLite DB tạo từ CSV → tự động cache sang NumPy `.npz` lần đầu chạy (~140 ms load từ lần 2, so với ~2600 ms từ SQLite).  
> Cấu hình đường dẫn trong `config/system.json` → `adaptive_restraint.db_path` / `adaptive_restraint.csv_path`.  
> Nếu DB chưa sẵn sàng, các endpoint trả về **503 Service Unavailable**.

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/adaptive_restraint/available` | Lấy tất cả giá trị lọc khả dụng (System, Age, Seatbelt, Velocity, Weight, Height, Distance) |
| `GET` | `/adaptive_restraint/chart_info` | Tính thống kê box-plot + raw data preview theo các bộ lọc |

**GET /adaptive_restraint/available** — response:
```json
{
  "System":   ["fusion", "camera", "non_adapt"],
  "Age":      ["35y", "65y"],
  "Seatbelt": ["3-point", "..."],
  "Velocity": [40, 50, 56],
  "Weight":   [49.0, 58.67, 70.0],
  "Height":   [155.0, 159.67, 170.0],
  "Distance": [1440, 1534, 1620]
}
```

**GET /adaptive_restraint/chart_info** — query parameters (tất cả optional, mặc định = chọn tất cả):

| Param | Type | Ví dụ |
|---|---|---|
| `System` | `list[str]` | `System=fusion&System=camera` |
| `Age` | `list[str]` | `Age=35y` |
| `Seatbelt` | `list[str]` | `Seatbelt=3-point` |
| `Velocity` | `list[float]` | `Velocity=40&Velocity=56` |
| `Weight` | `list[float]` | `Weight=49.0&Weight=58.67` |
| `Height` | `list[float]` | `Height=159.67` |
| `Distance` | `list[float]` | `Distance=1440` |
| `RawData` | `bool` | `RawData=false` — mặc định `true`; set `false` để bỏ `raw_rows` khỏi response (giảm payload) |

Response:
```json
{
  "controls": {
    "System": ["fusion", "camera"],
    "Age": ["35y"],
    "Seatbelt": ["3-point"],
    "Velocity": [40.0, 56.0],
    "Weight": [49.0],
    "Height": [159.67],
    "Distance": [1440.0],
    "RawData": true
  },
  "datas": [
    {
      "injury_risk_fusion_35y": {
        "values": [0.0031, 0.0045, "..."],
        "min": 0.0012,
        "max": 0.0198,
        "lower fence": 0.0012,
        "q1": 0.0028,
        "median": 0.0041,
        "q3": 0.0065,
        "upper fence": 0.0115
      }
    }
  ],
  "available_options": {
    "Velocity": [40, 50, 56],
    "Weight":   [49.0, 58.67, 70.0],
    "Height":   [155.0, 159.67, 170.0],
    "Distance": [1440, 1534, 1620],
    "Seatbelt": ["3-point"]
  },
  "raw_rows": [
    {
      "weight": 49.0,
      "seat_position": 1440,
      "height": 159.67,
      "velocity [km/h]": 40,
      "Seatbelt Component": "3-point",
      "injury_risk_fusion_35y": 0.0031,
      "..."
    }
  ]
}
```

> **`datas`**: một entry cho mỗi tổ hợp `System × Age` được chọn.  
> **`available_options`**: faceted-search — với mỗi dimension D, trả về các giá trị còn tồn tại trong dữ liệu khi áp dụng tất cả bộ lọc *trừ* D. Frontend dùng trường này để làm mờ (gray-out) các item không còn kết quả nếu được chọn.  
> **`raw_rows`**: giới hạn tối đa 100 dòng; bị bỏ qua khi `RawData=false`.  
> Giá trị `injury_risk_*` là tỷ lệ thô (0–1). Frontend nhân × 100 để hiển thị %.

---

### System

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/system/health` | Liveness probe — trả về `ok` / `degraded` |
| `GET` | `/system/ready` | Readiness probe — CAN bus + DB sẵn sàng? |
| `GET` | `/system/metrics` | Tài nguyên CarPC: CPU, RAM, disk, queue, asyncio tasks… |

**GET /system/health**:
```json
{
  "status": "ok",
  "uptime_seconds": 3600.5,
  "bus_connected": true,
  "db_connected": true
}
```

**GET /system/metrics** — một phần response:
```json
{
  "timestamp": 1742000000.0,
  "cpu_percent": 12.4,
  "ram_percent": 34.2,
  "ram_used_mb": 512.0,
  "queue_size": 42,
  "queue_maxsize": 10000,
  "queue_usage_percent": 0.42,
  "asyncio_tasks": 8,
  "uptime_seconds": 3600.5
}
```

---

## WebSocket Endpoints

### Legacy (backward-compatible)

| Endpoint | Mô tả |
|---|---|
| `WS /ws/signals` | Stream tất cả signal updates |
| `WS /ws/alarms` | Stream alarm events |
| `WS /ws/all` | Stream cả signals + alarms |

Message format từ server:
```json
{"type": "signal", "signal": "VehicleSpeed", "value": 84.1, "timestamp": 1742000000.0}
{"type": "alarm",  "signal_name": "EngineRPM", "level": "critical", "value": 7650.0, ...}
```

---

### Subscribe Protocol (mới — `/ws/subscribe`)

Client chủ động chọn kênh muốn nhận, giảm tải mạng và xử lý.

**Kết nối**: `ws://host:8000/ws/subscribe`

**Subscribe** — client gửi:
```json
{
  "action": "subscribe",
  "channels": ["VehicleSpeed", "EngineRPM", "alarms", "metrics"],
  "mode": "continuous",
  "rate_ms": 100
}
```

| Field | Giá trị | Mô tả |
|---|---|---|
| `action` | `subscribe` / `unsubscribe` | |
| `channels` | list signal names | Dùng `"*"` để subscribe tất cả signals |
| `mode` | `continuous` / `once` | `once` = gửi 1 lần rồi tự unsubscribe |
| `rate_ms` | int (optional) | Minimum interval giữa 2 lần push (client-side rate limit) |

**Ack** từ server:
```json
{"type": "subscribe_ack", "action": "subscribe", "channels": ["VehicleSpeed"], "mode": "continuous", "rate_ms": 100}
```

**Unsubscribe**:
```json
{"action": "unsubscribe", "channels": ["VehicleSpeed"]}
```

**Channels đặc biệt**:
- `"alarms"` — nhận alarm events
- `"metrics"` — nhận system metrics push (3 giây/lần từ server)
- `"*"` — subscribe tất cả signals

---

## Frontend Modes

Frontend có 2 chế độ chọn qua dropdown, lưu trong `localStorage`:

| Mode | Mô tả |
|---|---|
| `dev` | Subscribe tất cả signals (`"*"`), hiển thị đầy đủ |
| `user` | Chỉ subscribe whitelist tín hiệu curated + `alarms` + `metrics` |

Whitelist hiện tại định nghĩa client-side trong `frontend/js/app.js` (`USER_SIGNAL_WHITELIST`).

---

## Error Codes

| HTTP Code | Ý nghĩa |
|---|---|
| `200 OK` | Thành công |
| `202 Accepted` | Lệnh ghi CAN đã được queue |
| `401 Unauthorized` | Thiếu hoặc sai API key |
| `404 Not Found` | Signal / alarm không tồn tại |
| `409 Conflict` | Alarm đã acknowledged / resolved |
| `422 Unprocessable Entity` | Sai schema request body (Pydantic) |
| `503 Service Unavailable` | CAN writer không khả dụng hoặc Adaptive Restraint DB chưa sẵn sàng |

WebSocket close codes:
- `4001` — Unauthorized (invalid token)
