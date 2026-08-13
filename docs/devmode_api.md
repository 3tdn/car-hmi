# Dev Mode & ELK API Specification

## Mục tiêu

Tài liệu này mô tả API cần thiết cho hai chức năng chính trong giao diện developer:

1. Dev Mode: cho phép chọn ghế để gửi signal cùng lúc
2. ELK (E-Locking): theo dõi trạng thái kết nối/ELK của từng seat và tổng hợp trạng thái failure toàn hệ thống.

---

## 1. Quy ước chung

- Seat ID: `fl`, `fr`, `rl1`, `rl2`, `rr1`
- Thời gian: ISO 8601 UTC, ví dụ `2026-08-13T15:21:37.312Z`
- Default timeout: `60` giây (`block_timeout_sec`)
- Server luôn là nơi quản lý lock/dev-mode session; FE chỉ gửi yêu cầu và hiển thị trạng thái.
- Nếu một ghế không connected hoặc ECU lỗi, BE phải trả trạng thái `disabled` hoặc `error` tương ứng và không cho phép apply signal.

### Trạng thái chuẩn

- `selected`: ghế được chọn trong Dev Mode
- `disabled`: FE phải gray out chức năng Dev Mode của ghế đó
- `lock`: ELK lock
- `unlock`: ELK unlock
- `failure`: ELK failure
- `ok`: không có fail nào trong hệ thống
- `failure_detected`: tổng trạng thái hệ thống nếu có ít nhất 1 ELK failure

---

## 2. Dev Mode — chọn ghế

### Endpoint

- `POST /api/devmode/seats/select`

### Mục đích

FE báo backend ghế nào đang được chọn cho Dev Mode. Khi có hành động này, backend sẽ tạo timeout để chặn các mode khác hoạt động trên các ghế liên quan trong khoảng thời gian xác định.

### Request body

```json
{
  "seats": {
    "fl": true,
    "fr": true,
    "rl1": true,
    "rl2": false,
    "rr1": false
  },
  "block_timeout_sec": 60
}
```

### Response thành công

Chỉ trả về các ghế thực sự được update.

```json
{
  "applied": {
    "fl": { "selected": true, "applied_at": "2026-08-13T15:21:37.312Z" },
    "fr": { "selected": true, "applied_at": "2026-08-13T15:21:37.412Z" },
    "rl1": { "selected": true, "applied_at": "2026-08-13T15:21:37.512Z" }
  },
  "expires_at": "2026-08-13T15:22:37.312Z"
}
```

### Response lỗi theo từng ghế

Khi có ghế reject, server chỉ trả về các ghế bị update hoặc bị reject; không trả thêm thông tin cho ghế không được cập nhật.

```json
{
  "applied": {
    "fl": { "selected": true, "applied_at": "2026-08-13T15:21:37.312Z" },
    "rl1": {
      "selected": false,
      "error": "seat_not_connected",
      "reason": "ECU is not connected or not responding",
      "applied_at": "2026-08-13T15:21:37.512Z"
    },
    "rl2": {
      "selected": false,
      "error": "seat_not_connected",
      "reason": "ECU is not connected or not responding",
      "applied_at": "2026-08-13T15:21:37.612Z"
    }
  },
  "expires_at": "2026-08-13T15:22:37.312Z"
}
```

- Nếu tất cả ghế đều bị reject: HTTP status `409 Conflict`
- Nếu một phần ghế apply ok, server vẫn trả `200 OK` và chỉ đánh dấu lỗi ở các ghế bị reject

---

## 3. Dev Mode — gửi signal

### Endpoint

- `POST /api/devmode/signals`

### Mục đích

FE yêu cầu backend inject hoặc set state của một signal cho nhiều ghế cùng lúc trong Dev Mode.

### Request body

```json
{
  "signal_name": "HB_Request",
  "value": 2,
  "seats": {
    "fl": true,
    "fr": true,
    "rl1": true,
    "rl2": false,
    "rr1": false
  },
  "block_timeout_sec": 60
}
```

### Response thành công

Chỉ trả về các ghế thực sự được update. Tên signal là tên chung của họ signal, ví dụ `HB_Request`.

```json
{
  "applied": {
    "fl": {
      "signal_name": "HB_Request",
      "value": 2,
      "applied_at": "2026-08-13T15:21:37.312Z"
    },
    "fr": {
      "signal_name": "HB_Request",
      "value": 2,
      "applied_at": "2026-08-13T15:21:37.412Z"
    },
    "rl1": {
      "signal_name": "HB_Request",
      "error": "seat_not_connected",
      "reason": "ECU is not connected or not responding",
      "applied_at": "2026-08-13T15:21:37.512Z"
    }
  },
  "expires_at": "2026-08-13T15:22:37.312Z"
}
```

### Supported signal names

- `ACR_RetractRequest`
- `ABL_RetractRequest`
- `ISB_Color`
- `HB_Request`

### Value mapping

- `ACR_RetractRequest`: tham khảo tín hiệu tương ứng ACR_FL_RetractRequest, (`5` hoặc `10->25`)
- `ABL_RetractRequest`: tham khảo tín hiệu tương ứng ABL_FL_RetractRequest, (`0->5`, `11`, `12`)
- `ISB_Color`: mã màu dạng integer hoặc hex-to-dec, ví dụ `rgb(0, 255, 0) => 65280`
- `HB_Request`: `0`, `1`, `2`

---

## 4. ELK (E-Locking) — trạng thái tổng hợp

### Mục đích

- `No Failure`: FE nhận được giá trị ELK hợp lệ.
- `Failure Detected`: FE không nhận được giá trị ELK.
- Nếu có ít nhất 1 seat đang `failure`, trạng thái hệ thống là `failure_detected`.

### Endpoint

- `POST /api/devmode/elk-reset`

### Request body

```json
{}
```

### Response

```json
{
  "overall_status": "failure_detected",
  "seats": {
    "rl1": { "value": -1 },
    "rl2": { "value": 0 },
    "rr1": { "value": 1 }
  }
}
```

### Status mapping

- `0`: ELK `lock`
- `1`: ELK `unlock`
- `-1`: ELK `failure`
- `overall_status = "ok"`: không có seat nào fail
- `overall_status = "failure_detected"`: có ít nhất 1 seat fail

---

## 5. WebSocket subscribe

### Endpoint

- `WS /ws/subscribe`

### Example request

```json
{
  "channels": ["connection", "elk"],
  "rate_ms": 1000
}
```

### Ack format

```json
{
  "timestamp": "2026-08-13T15:21:37.312Z",
  "signals": [
    {
      "name": "PUMA_FL_CAN_ConnectionStatus",
      "std_name": "PUMA_FL_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_FL_Ethernet_ConnectionStatus",
      "std_name": "PUMA_FL_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_FR_CAN_ConnectionStatus",
      "std_name": "PUMA_FR_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_FR_Ethernet_ConnectionStatus",
      "std_name": "PUMA_FR_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RL1_CAN_ConnectionStatus",
      "std_name": "PUMA_RL1_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RL1_Ethernet_ConnectionStatus",
      "std_name": "PUMA_RL1_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RL1_ELK_Status",
      "std_name": "PUMA_RL1_ELK_Status",
      "value": 0
    },
    {
      "name": "PUMA_RL2_CAN_ConnectionStatus",
      "std_name": "PUMA_RL2_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RL2_Ethernet_ConnectionStatus",
      "std_name": "PUMA_RL2_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RL2_ELK_Status",
      "std_name": "PUMA_RL2_ELK_Status",
      "value": 1
    },
    {
      "name": "PUMA_RR1_CAN_ConnectionStatus",
      "std_name": "PUMA_RR1_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RR1_Ethernet_ConnectionStatus",
      "std_name": "PUMA_RR1_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_RR1_ELK_Status",
      "std_name": "PUMA_RR1_ELK_Status",
      "value": -1
    },
    {
      "name": "PUMA_PANTHER_CAN_ConnectionStatus",
      "std_name": "PUMA_PANTHER_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_PANTHER_Ethernet_ConnectionStatus",
      "std_name": "PUMA_PANTHER_Ethernet_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_JETSON_CAN_ConnectionStatus",
      "std_name": "PUMA_JETSON_CAN_ConnectionStatus",
      "value": true
    },
    {
      "name": "PUMA_JETSON_Ethernet_ConnectionStatus",
      "std_name": "PUMA_JETSON_Ethernet_ConnectionStatus",
      "value": true
    }
  ]
}
```

### Note về value

- `true`: connected
- `false`: disconnected
- `0`: ELK `lock`
- `1`: ELK `unlock`
- `-1`: ELK `failure`

---

## 7. Tóm tắt API chính

- `POST /api/devmode/seats/select`
- `POST /api/devmode/signals`
- `POST /api/devmode/elk-reset`
- `WS /ws/subscribe`
