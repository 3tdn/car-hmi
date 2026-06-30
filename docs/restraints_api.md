# Restraints Video Match API

## Tổng quan

Backend cung cấp REST API để HMI (frontend) tìm video restraint phù hợp nhất với điều kiện va chạm và thông số người ngồi. Backend tự đọc CAN signal từ xe để bổ sung thông tin còn thiếu.

---

## Endpoint: `GET /api/restraints/match`

### Request — HMI gởi lên BE

Tất cả tham số đều là **query string** (không có body).

| Parameter | Bắt buộc | Kiểu | Ví dụ | Mô tả |
|---|---|---|---|---|
| `weight` | ✅ | `float` | `75.0` | Cân nặng người ngồi (kg) → BE tự suy ra percentile |
| `height` | ✅ | `float` | `175.0` | Chiều cao (cm) → lưu vào context, không dùng cho scoring |
| `crash_severity` | ✅ | `string` | `"40"` hoặc `"OLC18"` | Mức độ va chạm: velocity km/h (35/40/50/56) hoặc OLC code |
| `seatbelt_system` | ✅ | `string` | `"SLL"` | Loại seatbelt: `SLL` / `CLL` / `MSLL` |
| `seat` | ❌ | `string` | `"fl"` | Ghế: `fl` (front-left) hoặc `fr` (front-right). Mặc định: `fl` |
| `seat_x_mm` | ❌ | `float` | `100.0` | Vị trí ghế mm từ SPS sensor (0=frontmost, 227=rearmost). Nếu bỏ qua → BE tự đọc CAN |

**URL ví dụ:**
```
GET /api/restraints/match?weight=75&height=175&crash_severity=40&seatbelt_system=SLL&seat=fl&seat_x_mm=100
```

---

### Quy trình xử lý trong BE

```
HMI Request
    │
    ├─ 1. Tính percentile từ cân nặng
    │       < 65 kg  → 5th %
    │       65–90 kg → 50th %
    │       > 90 kg  → 95th %
    │
    ├─ 2. Resolve velocity từ crash_severity
    │       "40"    → 40 km/h
    │       "OLC18" → 40 km/h  (OLC lookup table)
    │       Hợp lệ: 35 / 40 / 50 / 56 km/h
    │
    ├─ 3. Validate seatbelt_system ∈ {SLL, CLL, MSLL}
    │
    ├─ 4. Đọc CAN signals live từ signal store
    │       seat=fl → OMS_FL_OccupantClassification, OMS_FL_OutOfPosition, SPS_FL_SeatDirectionX
    │       seat=fr → OMS_FR_OccupantClassification, OMS_FR_OutOfPosition, SPS_FR_SeatDirectionX
    │
    ├─ 5. Xác định seat_position zone
    │       Ưu tiên: seat_x_mm param > CAN SPS signal > default "mid"
    │       0 – 56.75 mm   → "front"
    │       56.75 – 170.25 → "mid"
    │       ≥ 170.25 mm    → "rear"
    │
    ├─ 6. Resolve percentile hiệu dụng
    │       CAN OMS_OccupantClassification (nếu có) ghi đè weight-derived
    │       1 → 5th %, 2 → 50th %, 3 → 95th %
    │
    ├─ 7. Quét thư mục media/
    │       Parse tên file theo schema: {percentile}p_{seat_position}_{velocity}_{seatbelt}.ext
    │       Ví dụ: 50p_mid_40_SLL.mp4
    │
    └─ 8. Scoring từng file (max ~7.0 điểm)
            +3.0  seatbelt system khớp chính xác
            +2.0  percentile khớp chính xác
            +1.0  seat_position zone khớp
            +0–1  velocity gần nhất: score = 1 − |Δv| / 21  (max diff = |56−35| = 21)
```

---

### OLC → Velocity mapping

| OLC code | Velocity |
|---|---|
| OLC16 | 35 km/h |
| OLC18 | 40 km/h |
| OLC26 | 50 km/h |
| OLC33 | 56 km/h |

---

### Response — BE trả lại FE

**Khi tìm thấy video:**
```json
{
  "matched": true,
  "score": 6.952,
  "video": {
    "filename": "50p_mid_40_SLL.mp4",
    "percentile": 50,
    "seat_position": "mid",
    "velocity_kmh": 40,
    "seatbelt": "SLL",
    "url": "/api/restraints/video/50p_mid_40_SLL.mp4"
  },
  "context": {
    "weight_kg": 75.0,
    "height_cm": 175.0,
    "derived_percentile": 50,
    "effective_percentile": 50,
    "can_percentile": null,
    "target_velocity_kmh": 40,
    "seatbelt_system": "SLL",
    "seat": "fl",
    "seat_x_mm": 100.0,
    "seat_x_source": "hmi_param",
    "seat_position_zone": "mid",
    "out_of_position": false,
    "candidates_found": 12
  }
}
```

**Khi không tìm thấy:**
```json
{
  "matched": false,
  "video": null,
  "score": 0,
  "context": { "..." : "..." }
}
```

**Trường `seat_x_source`:**

| Giá trị | Ý nghĩa |
|---|---|
| `"hmi_param"` | Lấy từ `seat_x_mm` query param do HMI gởi |
| `"can_signal"` | Lấy từ CAN signal `SPS_FL/FR_SeatDirectionX` |
| `"default"` | Không có dữ liệu → dùng mặc định `"mid"` |

---

## Endpoint: `GET /api/restraints/video/{filename}`

Serve file video để `<video>` tag phát trực tiếp.

```
GET /api/restraints/video/50p_mid_40_SLL.mp4
→ FileResponse (video/mp4)
```

- Chống path traversal: từ chối filename chứa `..`, `/`, `\`
- Trả 404 nếu file không tồn tại trong thư mục `media/`

---

## Video filename schema

```
{percentile}p_{seat_position}_{velocity}_{seatbelt}.ext
```

| Field | Giá trị hợp lệ |
|---|---|
| `percentile` | `5` / `50` / `95` |
| `seat_position` | `front` / `mid` / `rear` |
| `velocity` | `35` / `40` / `50` / `56` |
| `seatbelt` | `SLL` / `CLL` / `MSLL` |

Ví dụ: `50p_mid_40_SLL.mp4`, `5p_front_35_CLL.webm`

---

## Ưu tiên dữ liệu

| Thuộc tính | Ưu tiên 1 | Ưu tiên 2 | Ưu tiên 3 |
|---|---|---|---|
| **Percentile** | CAN `OMS_FL/FR_OccupantClassification` | `weight` param | — |
| **Seat zone** | `seat_x_mm` param (HMI explicit) | CAN `SPS_FL/FR_SeatDirectionX` | Default `"mid"` |

---

## CAN Signals sử dụng

| Signal | Message ID | Transmitter | Mô tả |
|---|---|---|---|
| `OMS_FL_OccupantClassification` | 179 | SIMI | Phân loại người ngồi ghế FL (1=5%, 2=50%, 3=95%) |
| `OMS_FR_OccupantClassification` | 180 | SIMI | Phân loại người ngồi ghế FR |
| `OMS_FL_OutOfPosition` | 179 | SIMI | Cờ out-of-position ghế FL (nonzero = OOP) |
| `OMS_FR_OutOfPosition` | 180 | SIMI | Cờ out-of-position ghế FR |
| `SPS_FL_SeatDirectionX` | 181 | PANTHER | Vị trí ghế FL theo trục X (mm, 0–227) |
| `SPS_FR_SeatDirectionX` | 182 | PANTHER | Vị trí ghế FR theo trục X (mm, 0–227) |
