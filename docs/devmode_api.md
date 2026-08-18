# Dev Mode & ELK API Specification

## Mục tiêu

Tài liệu này mô tả API cần thiết cho hai chức năng chính trong giao diện developer:

1. Dev Mode: cho phép chọn ghế để gửi signal cùng lúc
2. ELK (E-Locking): theo dõi trạng thái kết nối và ELK của từng seat và reset trạng thái failure .

---

## 1. Quy ước chung

- Seat ID: `fl`, `fr`, `rl1`, `rl2`, `rr1`
- Thời gian: ISO 8601 UTC, ví dụ `2026-08-13T15:21:37.312Z`
- Default timeout: `60` giây (`block_timeout_sec`)
- `block_timeout_sec` hợp lệ từ `1` đến `3600` giây.
- Các API quản lý lock yêu cầu header `X-Client-Id` khác rỗng.
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

### Khoá ghi theo ghế (block các section khác)

- Khoá được cấp cho “section” = giá trị header `X-Client-Id` của FE.
- Request thiếu `X-Client-Id` bị từ chối với HTTP `400 Bad Request`.
- Khi ghế bị khoá, mọi request ghi từ section khác lên tín hiệu của ghế đó bị chặn:
  - `PUT /signals/{signal_name}` → `423 Locked`, detail có `code = devmode_seat_locked`
  - `POST /signals/batch_update` → tín hiệu bị bỏ qua và trả warning `devmode_seat_locked`
- Ghế được suy ra từ token trong tên tín hiệu (`ACR_FL_RetractRequest`, `HB_Request_FL`, `ISB_FL_ColorRed` → `fl`).
- Khoá tự hết hạn sau `block_timeout_sec` (mặc định `60`, cấu hình tại `devmode.block_timeout_sec` trong `config/system.json`).
- FE nên gia hạn khoá bằng cách gọi lại `POST /api/devmode/seats/select` (nửa chu kỳ timeout).

### Endpoint phụ trợ

- `GET /api/devmode/catalog` — danh sách seat, các họ signal và state để FE dựng tab + nút bấm
- `GET /api/devmode/status` — trạng thái khoá hiện tại của từng ghế (`selected`, `owned`, `connected`, `expires_at`, `remaining_sec`)
- `POST /api/devmode/exit` — thoát Dev Mode, nhả toàn bộ khoá của section hiện tại

#### Response `GET /api/devmode/catalog`

```json
{
  "seats": ["fl", "fr", "rl1", "rl2", "rr1"],
  "families": [
    {
      "signal_name": "ACR_RetractRequest",
      "kind": "state",
      "states": [
        { "value": 5, "description": "Haptic" },
        { "value": 10, "description": "Retract level 10" }
      ]
    }
  ],
  "block_timeout_sec": 60,
  "status_stale_timeout_sec": 30
}
```

- `status_stale_timeout_sec` là ngưỡng thời gian stale dùng để đánh dấu signal status / ELK không còn fresh trong View B.
- FE đọc field này để tính `Status Unknown` khi signal thiếu hoặc quá cũ.

#### Auto-renew lock behavior

- Khi FE chọn ít nhất 1 ghế trong Dev Mode, frontend tự động gia hạn lock theo chu kỳ khoảng nửa `block_timeout_sec`.
- Mỗi lần renew, FE gửi lại `POST /api/devmode/seats/select` với map các seat đang được chọn.
- Nếu renew thất bại vì seat bị lock bởi section khác hoặc backend trả lỗi, FE ghi log và không bỏ lock tự động trừ khi người dùng rời Dev Mode hoặc timeout hết hạn.
- Mục tiêu là giữ lock cho section hiện tại sống trong suốt phiên Dev Mode mà không cần user thao tác thêm.

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

### Ánh xạ sang tín hiệu CAN thật

| Họ signal | Tín hiệu ghi lên bus |
| --- | --- |
| `ACR_RetractRequest` | `ACR_{SEAT}_RetractRequest` |
| `ABL_RetractRequest` | `ABL_{SEAT}_RetractRequest` |
| `HB_Request` | `HB_Request_{SEAT}` |
| `ISB_Color` | `ISB_{SEAT}_ColorRed` + `ISB_{SEAT}_ColorGreen` + `ISB_{SEAT}_ColorBlue` (tách RGB) |

Ghế nào không có tín hiệu tương ứng trong DBC (ví dụ `HB_Request_FL`) sẽ trả về
`error = signal_not_available` cho ghế đó, các ghế còn lại vẫn được apply.

---

## 4. ELK (E-Locking) — dùng signals có sẵn

### Mục đích

- `No Failure`: FE đọc trực tiếp trạng thái ELK từ các signal CAN đang stream qua hệ thống hiện có.
- `Failure Detected`: FE đánh dấu fail khi signal ELK báo `-1`, hoặc khi các signal CAN status không cập nhật trong timeout.
- Nếu có ít nhất 1 seat đang `failure`, trạng thái hệ thống là `failure_detected`.

### Signal nguồn trong DBC v7 (TBD là chưa có trong DBC v7, chỉ là placeholder)

Chỉ dùng các signal hiện có trong DBC v7, không tạo thêm API/WS mới:
- `ELK_FL_ActuatorStatus`, `ELK_FR_ActuatorStatus`, `ELK_RL1_ActuatorStatus`, `ELK_RL2_ActuatorStatus`, `ELK_RR1_ActuatorStatus`
- `COM_Status_PumaFLCan`, `COM_Status_PumaFRCan`, `COM_Status_PumaRL1Can`, `COM_Status_PumaRL2Can`, `COM_Status_PumaRR1Can`
- `COM_Status_PumaFLEthernet`, `COM_Status_PumaFREthernet`, `COM_Status_PumaRL1Ethernet`, `COM_Status_PumaRL2Ethernet`, `COM_Status_PumaRR1Ethernet`
- `COM_Status_PantherCan`, `COM_Status_PantherEthernet`
- `COM_Status_NvidiaJetsonCan`, `COM_Status_NvidiaJetsonEthernet`
- `ELK_ResetErrorFlags`: nếu cần gửi lệnh reset từ HMI/FE, dùng REST write signal đã có sẵn thay vì endpoint riêng

### API/WS hiện có để dùng

- WebSocket hiện có: `WS /ws/signals`
- REST hiện có:
  - `GET /signals/available` để lấy metadata + signal names
  - `GET /signals/{signal_name}` để lấy giá trị mới nhất
  - `PUT /signals/{signal_name}` để write một signal
  - `POST /signals/batch_update` để write nhiều signal cùng lúc

### Quy tắc xử lý

- `status` và `can_communication` được suy ra từ các signal CAN đã có; không tạo endpoint riêng cho từng seat.
- Signal connectivity bị thiếu hoặc không cập nhật trong `reader.stale_threshold_sec` được coi là `not_connected`.
- FE subscribe các signal ELK/CAN status qua WS hiện có, hoặc poll qua `GET /signals/{signal_name}` nếu cần.
- `failure_detected` được suy ra từ dữ liệu signal hoặc timeout mất signal, hoặc ELK_*_ActuatorStatus có giá trị `1` hoặc `2`.
- Nếu một ghế không connected hoặc ECU lỗi, FE đánh dấu `disabled` / `failure` dựa trên giá trị signal hoặc timeout.
- unknown : `ELK_*_ActuatorStatus` với giá trị `3` sau khi nhấn reset và chưa nhận được phản hồi từ ECU, FE hiển thị `Status Unknown` (gray) cho ghế đó.

### Status mapping

- `0`: CAN communication lost / disconnected - red
- `1`: CAN communication OK / connected - green
- `0`: ELK `ok` - green
- `1`: ELK `failure at previous` - yellow
- `2`: ELK `failure now` - red
- `3`: invalid state, wait response after reset - gray
- missing hoặc stale signal: `Status Unknown` (không phải `No Failure`)

### Reset error flag

- Nếu cần reset lỗi trên ECU: dùng signal có sẵn `ELK_ResetErrorFlags` qua `PUT /signals/ELK_ResetErrorFlags` với payload:

```json
{ "value": 1 }
```

- FE hiện tại gửi `1` khi user click `Reset E-Locking Failure Memory`.

---

## 5. WebSocket subscribe

### Endpoint

- `WS /ws/signals`

### Example request

```json
{
  "signals": [
    "ELK_FL_ActuatorStatus",
    "ELK_FR_ActuatorStatus",
    "ELK_RL1_ActuatorStatus",
    "ELK_RL2_ActuatorStatus",
    "ELK_RR1_ActuatorStatus",
    "COM_Status_PumaFLCan",
    "COM_Status_PumaFRCan",
    "COM_Status_PumaRL1Can",
    "COM_Status_PumaRL2Can",
    "COM_Status_PumaRR1Can",
    "COM_Status_PumaFLEthernet",
    "COM_Status_PumaFREthernet",
    "COM_Status_PumaRL1Ethernet",
    "COM_Status_PumaRL2Ethernet",
    "COM_Status_PumaRR1Ethernet",
    "COM_Status_PantherCan",
    "COM_Status_PantherEthernet",
    "COM_Status_NvidiaJetsonCan",
    "COM_Status_NvidiaJetsonEthernet"
  ],
  "rate_ms": 1000
}
```
* "signals" or "channels" can be used interchangeably in the request body, but "signals" is preferred for clarity.

### Ack format

```json
{
  "timestamp": "2026-08-13T15:21:37.312Z",
  "signals": [
    { "name": "ELK_FL_ActuatorStatus", "std_name": "ELK_FL_ActuatorStatus", "value": 0},
    { "name": "ELK_FR_ActuatorStatus", "std_name": "ELK_FR_ActuatorStatus", "value": 1},
    { "name": "ELK_RL1_ActuatorStatus", "std_name": "ELK_RL1_ActuatorStatus", "value": 0},
    { "name": "ELK_RL2_ActuatorStatus", "std_name": "ELK_RL2_ActuatorStatus", "value": 1},
    { "name": "ELK_RR1_ActuatorStatus", "std_name": "ELK_RR1_ActuatorStatus", "value": 0},
    { "name": "COM_Status_PumaFLCan", "std_name": "COM_Status_PumaFLCan", "value": 1},
    { "name": "COM_Status_PumaFRCan", "std_name": "COM_Status_PumaFRCan", "value": 1},
    { "name": "COM_Status_PumaRL1Can", "std_name": "COM_Status_PumaRL1Can", "value": 0},
    { "name": "COM_Status_PumaRL2Can", "std_name": "COM_Status_PumaRL2Can", "value": 1},
    { "name": "COM_Status_PumaRR1Can", "std_name": "COM_Status_PumaRR1Can", "value": 0},
    { "name": "COM_Status_PumaFLEthernet", "std_name": "COM_Status_PumaFLEthernet", "value": 1},
    { "name": "COM_Status_PumaFREthernet", "std_name": "COM_Status_PumaFREthernet", "value": 1},
    { "name": "COM_Status_PumaRL1Ethernet", "std_name": "COM_Status_PumaRL1Ethernet", "value": 0},
    { "name": "COM_Status_PumaRL2Ethernet", "std_name": "COM_Status_PumaRL2Ethernet", "value": 1},
    { "name": "COM_Status_PumaRR1Ethernet", "std_name": "COM_Status_PumaRR1Ethernet", "value": 0},
    { "name": "COM_Status_PantherCan", "std_name": "COM_Status_PantherCan", "value": 1},
    { "name": "COM_Status_PantherEthernet", "std_name": "COM_Status_PantherEthernet", "value": 1},
    { "name": "COM_Status_NvidiaJetsonCan", "std_name": "COM_Status_NvidiaJetsonCan", "value": 0},
    { "name": "COM_Status_NvidiaJetsonEthernet", "std_name": "COM_Status_NvidiaJetsonEthernet", "value": 1}
  ]
}
```

### Note về value
- `0`: CAN communication lost / disconnected - red
- `1`: CAN communication OK / connected - green
- `0`: ELK `no failure` - green
- `1`: ELK `failure at previous` - yellow
- `2`: ELK `failure now` - red
- `3`: invalid state, wait response after reset - gray

> Không có Ethernet và một số signal status trong DBC v7
  COM_Status_PumaFLEthernet
  COM_Status_PumaFREthernet
  COM_Status_PumaRL1Ethernet
  COM_Status_PumaRL2Ethernet
  COM_Status_PumaRR1Ethernet
  COM_Status_PantherCan
  COM_Status_PantherEthernet
  COM_Status_NvidiaJetsonCan
  COM_Status_NvidiaJetsonEthernet
---

## 6. SEAL AIRBAG
- Nếu cần Inflate/Exflate airbag dùng signal có sẵn `SEAL_AirbagRequestInflate`/`SEAL_AirbagRequestExflate`
qua `PUT /signals/SEAL_InflateAirbag` hoặc `PUT /signals/SEAL_ExflateAirbag` với payload:

```json
{ "value": 1 }
```

---

## 7. Tóm tắt API chính

- `GET /signals/available` => check xem signal đã có trong candb ko
- `GET /signals/{signal_name}` => lấy giá trị hiện tại của 1 signal
- `PUT /signals/{signal_name}` => update giá trị của 1 signal
- `POST /api/devmode/signals` => update giá trị của signal của 1 loạt ghế trong Dev Mode
- `POST /signals/batch_update` => update giá trị của nhiều signal cùng lúc (nếu ko đùng các api POST /api/devmode/signals)
- `WS /ws/signals`

## 8. Tóm tắt View Dev Mode
View dev-mode: (phân biệt giữa dev-mode và view user)
Devmode có 2 view.
view a:
Trong view có 5 switch tượng trưng cho 5 ghế ngồi có thể được select hoặc không
có 4 tab (ACR, ABL, ISB, HB tương ứng với các signal `ACR_RetractRequest`, `ABL_RetractRequest`, `ISB_Color`, `HB_Request`), mỗi tab có n buton tương ứng với n states của mỗi signal.
chức năng trong view này là có thể chuyển state của signal cho nhiều ghế 1 lần.
note khi bật switch của ghế nào lên nhớ thông báo để cho backend biết và tạo timeout 1min để chặn các mode khác hoạt động trên ghế đó
View b:
trong view có các thành phần:
PUMA mỗi ghế: có show các tín hiệu CAN, Ethernet, elk
PANTHER (máy tính PANTHER): có show các tín hiệu CAN, Ethernet
NVIDIA JETSON (máy tính NVIDIA JETSON): có show các tín hiệu CAN, Ethernet
mỗi thành phần show các trạng thái (connect-disconnect) của các tín hiệu



thêm 1 button để request "reset Elocking Failure Memery"