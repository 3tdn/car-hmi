# Restraints Video Match API

## Overview

The backend provides a REST API so the HMI (frontend) can find the best-matching restraints video based on crash conditions and occupant parameters. The backend reads the latest relevant values from `SignalStore` to fill in information that is not supplied explicitly.

---

## Endpoint: `GET /api/restraints/match`

### Request — HMI sends to BE

All parameters are **query string** parameters (no body).

| Parameter | Required | Type | Example | Description |
|---|---|---|---|---|
| `weight` | ✅ | `float` | `75.0` | Occupant weight (kg) → BE derives the percentile automatically |
| `height` | ✅ | `float` | `175.0` | Occupant height (cm) → stored in context, not used for scoring |
| `crash_severity` | ✅ | `int` | `40` | Velocity in km/h: 35, 40, 50, or 56. OLC codes are not accepted by the HTTP route. |
| `seatbelt_system` | ✅ | `string` | `"SLL"` | Seatbelt type: `SLL` / `CLL` / `MSLL` |
| `seat` | ❌ | `string` | `"fl"` | Seat: `fl` (front-left) or `fr` (front-right). Default: `fl` |
| `seat_x_mm` | ❌ | `float` | `100.0` | Seat position in mm from the SPS sensor (0=frontmost, 227=rearmost). If omitted → BE reads the latest `SignalStore` value |

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
    │       40 → 40 km/h (integer query parameter)
    │       Valid: 35 / 40 / 50 / 56 km/h
    │
    ├─ 3. Validate seatbelt_system ∈ {SLL, CLL, MSLL}
    │
    ├─ 4. Read the latest signal values from SignalStore
    │       seat=fl → OMS_FL_OccupantClassification, OMS_FL_OutOfPosition, SPS_FL_SeatDirectionX
    │       seat=fr → OMS_FR_OccupantClassification, OMS_FR_OutOfPosition, SPS_FR_SeatDirectionX
    │
    ├─ 5. Determine the seat_position zone
    │       Priority: seat_x_mm param > SignalStore SPS value > default "mid"
    │       0 – 56.75 mm   → "front"
    │       56.75 – 170.25 → "mid"
    │       ≥ 170.25 mm    → "rear"
    │
    ├─ 6. Resolve the effective percentile
    │       SignalStore OMS_OccupantClassification (if mappable) overrides weight-derived
    │       class 0 → 5th %, class 1 → 50th %, class 2 → 95th %
    │
    ├─ 7. Scan the media/ directory
    │       Parse filenames using the schema: {percentile}p_{seat_position}_{velocity}_{seatbelt}.ext
    │       Example: 50p_mid_40_SLL.mp4
    │
    └─ 8. Score each file (max 7.5 points)
            +3.0  exact seatbelt system match
            +2.0  exact percentile match
            +1.0  seat_position zone match
            +1.5  exact velocity match (no partial score)
```

---

### Severity input

The HTTP route validates `crash_severity` as an integer. If a frontend uses OLC labels,
it must convert them to an accepted velocity before requesting this endpoint; sending
`OLC18` directly produces a validation error (HTTP 422).

### OMS classification source

The processing pipeline publishes the frontend-facing
`OMS_xx_OccupantClassification` values according to `oms_config`:

- `bypass_simi_input: false`: keep the class decoded from CAN/SIMI.
- `bypass_simi_input: true`: derive class `0`/`1`/`2` from the mapped
  `OMS_xx_OccupantWeightMean` value using `class_config: [low, high]`.
- The boundaries are `< low` → `0`, `low <= weight <= high` → `1`, and `> high` → `2`.

The runtime source names intentionally omit the old `_kg` suffix. The match endpoint then
translates classes `0`/`1`/`2` into the available video buckets `5p`/`50p`/`95p`. The active
DBC describes those three classes as 25%/50%/95% occupants, while this route currently maps
class `0` to the media catalog's `5p` bucket; that application-level translation is separate
from the DBC label.

> Current limitation: this endpoint reads the latest value stored under the signal name but
> does not check its receive timestamp or provenance. A DBC-seeded initial value or a stale
> value can therefore override the percentile derived from the request's `weight`. Treat
> `can_percentile` as a SignalStore-derived value, not proof of fresh CAN traffic.

### Response — BE returns to FE

**When a video is found:**
```json
{
  "matched": true,
  "score": 7.5,
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
| `"can_signal"` | Taken from the latest `SignalStore` value for `SPS_FL/FR_SeatDirectionX` |
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
| **Percentile** | Latest `SignalStore` `OMS_FL/FR_OccupantClassification` value | `weight` param | — |
| **Seat zone** | `seat_x_mm` param (explicit from HMI) | Latest `SignalStore` `SPS_FL/FR_SeatDirectionX` value | Default `"mid"` |

---

## CAN Signals used

| Signal | Message ID | Transmitter | Description |
|---|---|---|---|
| `OMS_FL_OccupantClassification` | 179 | SIMI | FL seat occupant classification (0=5%, 1=50%, 2=95%) |
| `OMS_FR_OccupantClassification` | 180 | SIMI | FR seat occupant classification (0=5%, 1=50%, 2=95% in this route) |
| `OMS_FL_OutOfPosition` | 179 | SIMI | FL seat out-of-position flag (nonzero = OOP) |
| `OMS_FR_OutOfPosition` | 180 | SIMI | FR seat out-of-position flag |
| `SPS_FL_SeatDirectionX` | 181 | PANTHER | FL seat X-axis position (mm, 0–227) |
| `SPS_FR_SeatDirectionX` | 182 | PANTHER | FR seat X-axis position (mm, 0–227) |

## Frontend usage and errors

These routes are public. All match inputs are query parameters. Resolve `video.url` against
the backend origin, for example `new URL(result.video.url, window.API_BASE).href`, so a
separately hosted frontend requests the correct server. Check `matched` before assigning
it to a `<video>` element. Video responses are binary `FileResponse` with `video/mp4`
content type in the current route, even if a discovered filename has another suffix.

Missing/malformed query values return HTTP 422. Unsupported velocities, seatbelt systems,
or seat values return route-specific errors; missing video files return HTTP 404.
See the [API reference](api_reference.md) for exact error strings and response formats,
and the [frontend integration guide](frontend_integration.md) for chart/video examples.
