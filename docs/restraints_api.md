# Restraints Video Match API

## Overview

The backend provides a REST API so the HMI (frontend) can find the best-matching restraints video based on crash conditions and occupant parameters. The backend automatically reads CAN signals from the vehicle to fill in any missing information.

---

## Endpoint: `GET /api/restraints/match`

### Request — HMI sends to BE

All parameters are **query string** parameters (no body).

| Parameter | Required | Type | Example | Description |
|---|---|---|---|---|
| `weight` | ✅ | `float` | `75.0` | Occupant weight (kg) → BE derives the percentile automatically |
| `height` | ✅ | `float` | `175.0` | Occupant height (cm) → stored in context, not used for scoring |
| `crash_severity` | ✅ | `string` | `"40"` or `"OLC18"` | Crash severity: velocity in km/h (35/40/50/56) or OLC code |
| `seatbelt_system` | ✅ | `string` | `"SLL"` | Seatbelt type: `SLL` / `CLL` / `MSLL` |
| `seat` | ❌ | `string` | `"fl"` | Seat: `fl` (front-left) or `fr` (front-right). Default: `fl` |
| `seat_x_mm` | ❌ | `float` | `100.0` | Seat position in mm from the SPS sensor (0=frontmost, 227=rearmost). If omitted → BE reads CAN automatically |

**Example URL:**
```
GET /api/restraints/match?weight=75&height=175&crash_severity=40&seatbelt_system=SLL&seat=fl&seat_x_mm=100
```

---

### Processing flow in BE

```
HMI Request
    │
    ├─ 1. Compute percentile from weight
    │       < 65 kg  → 5th %
    │       65–90 kg → 50th %
    │       > 90 kg  → 95th %
    │
    ├─ 2. Resolve velocity from crash_severity
    │       "40"    → 40 km/h
    │       "OLC18" → 40 km/h  (OLC lookup table)
    │       Valid: 35 / 40 / 50 / 56 km/h
    │
    ├─ 3. Validate seatbelt_system ∈ {SLL, CLL, MSLL}
    │
    ├─ 4. Read live CAN signals from the signal store
    │       seat=fl → OMS_FL_OccupantClassification, OMS_FL_OutOfPosition, SPS_FL_SeatDirectionX
    │       seat=fr → OMS_FR_OccupantClassification, OMS_FR_OutOfPosition, SPS_FR_SeatDirectionX
    │
    ├─ 5. Determine the seat_position zone
    │       Priority: seat_x_mm param > CAN SPS signal > default "mid"
    │       0 – 56.75 mm   → "front"
    │       56.75 – 170.25 → "mid"
    │       ≥ 170.25 mm    → "rear"
    │
    ├─ 6. Resolve the effective percentile
    │       CAN OMS_OccupantClassification (if available) overrides weight-derived
    │       1 → 5th %, 2 → 50th %, 3 → 95th %
    │
    ├─ 7. Scan the media/ directory
    │       Parse filenames using the schema: {percentile}p_{seat_position}_{velocity}_{seatbelt}.ext
    │       Example: 50p_mid_40_SLL.mp4
    │
    └─ 8. Score each file (max ~7.0 points)
            +3.0  exact seatbelt system match
            +2.0  exact percentile match
            +1.0  seat_position zone match
            +0–1  nearest velocity: score = 1 − |Δv| / 21  (max diff = |56−35| = 21)
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

### Response — BE returns to FE

**When a video is found:**
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

**When no match is found:**
```json
{
  "matched": false,
  "video": null,
  "score": 0,
  "context": { "..." : "..." }
}
```

**Field `seat_x_source`:**

| Value | Meaning |
|---|---|
| `"hmi_param"` | Taken from the `seat_x_mm` query parameter sent by HMI |
| `"can_signal"` | Taken from CAN signal `SPS_FL/FR_SeatDirectionX` |
| `"default"` | No data available → use default `"mid"` |

---

## Endpoint: `GET /api/restraints/video/{filename}`

Serve the video file so the `<video>` tag can play it directly.

```
GET /api/restraints/video/50p_mid_40_SLL.mp4
→ FileResponse (video/mp4)
```

- Prevent path traversal: reject filenames containing `..`, `/`, `\`
- Return 404 if the file does not exist in the `media/` directory

---

## Video filename schema

```
{percentile}p_{seat_position}_{velocity}_{seatbelt}.ext
```

| Field | Valid values |
|---|---|
| `percentile` | `5` / `50` / `95` |
| `seat_position` | `front` / `mid` / `rear` |
| `velocity` | `35` / `40` / `50` / `56` |
| `seatbelt` | `SLL` / `CLL` / `MSLL` |

Example: `50p_mid_40_SLL.mp4`, `5p_front_35_CLL.webm`

---

## Data priority

| Attribute | Priority 1 | Priority 2 | Priority 3 |
|---|---|---|---|
| **Percentile** | CAN `OMS_FL/FR_OccupantClassification` | `weight` param | — |
| **Seat zone** | `seat_x_mm` param (explicit from HMI) | CAN `SPS_FL/FR_SeatDirectionX` | Default `"mid"` |

---

## CAN Signals used

| Signal | Message ID | Transmitter | Description |
|---|---|---|---|
| `OMS_FL_OccupantClassification` | 179 | SIMI | FL seat occupant classification (1=5%, 2=50%, 3=95%) |
| `OMS_FR_OccupantClassification` | 180 | SIMI | FR seat occupant classification |
| `OMS_FL_OutOfPosition` | 179 | SIMI | FL seat out-of-position flag (nonzero = OOP) |
| `OMS_FR_OutOfPosition` | 180 | SIMI | FR seat out-of-position flag |
| `SPS_FL_SeatDirectionX` | 181 | PANTHER | FL seat X-axis position (mm, 0–227) |
| `SPS_FR_SeatDirectionX` | 182 | PANTHER | FR seat X-axis position (mm, 0–227) |
