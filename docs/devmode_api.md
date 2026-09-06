# Dev Mode & ELK API Specification

## Objectives

This document describes the APIs required for two main features in the developer UI:

1. Dev Mode: allows selecting seats so signals can be sent to them simultaneously
2. ELK (E-Locking): monitors the connection state and ELK state of each seat, and resets the failure state.

---

## 1. Common conventions

- Seat ID: `fl`, `fr`, `rl1`, `rl2`, `rr1`
- Time: ISO 8601 UTC, for example `2026-08-13T15:21:37.312Z`
- Default timeout: `60` seconds (`block_timeout_sec`)
- Valid `block_timeout_sec` range: `1` to `3600` seconds.
- Lock-management APIs require a non-empty `X-Client-Id` header.
- The server is always responsible for managing locks/dev-mode sessions; FE only sends requests and displays status.
- If a seat is not connected or its ECU has failed, BE must return status `disabled` or `error` accordingly and must not allow the signal to be applied.

### Standard states

- `selected`: the seat is selected in Dev Mode
- `disabled`: FE must gray out Dev Mode functionality for that seat
- `lock`: ELK lock
- `unlock`: ELK unlock
- `failure`: ELK failure
- `ok`: there is no failure anywhere in the system
- `failure_detected`: overall system state when at least 1 ELK failure exists

---

## 2. Dev Mode — seat selection

### Endpoint

- `POST /api/devmode/seats/select`

### Purpose

FE tells the backend which seats are currently selected for Dev Mode. When this action occurs, the backend creates a timeout that blocks other modes from operating on the related seats for a specified period.

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

### Successful response

Only seats that were actually updated are returned.

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

### Error response per seat

When some seats are rejected, the server returns only the seats that were updated or rejected; it does not include extra information for seats that were not changed.

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

- If all seats are rejected: HTTP status `409 Conflict`
- If only some seats apply successfully, the server still returns `200 OK` and marks errors only on the rejected seats

### Per-seat write lock (blocks other sections)

- The lock is granted to the “section” = the value of FE header `X-Client-Id`.
- A request without `X-Client-Id` is rejected with HTTP `400 Bad Request`.
- When a seat is locked, every write request from another section to signals for that seat is blocked:
  - `PUT /signals/{signal_name}` → `423 Locked`, with detail containing `code = devmode_seat_locked`
  - `POST /signals/batch_update` → the locked signal is skipped and warning `devmode_seat_locked` is returned
- The seat is inferred from the token in the signal name (`ACR_FL_RetractRequest`, `HB_Request_FL`, `ISB_FL_ColorRed` → `fl`).
- The lock automatically expires after `block_timeout_sec` (default `60`, configured at `devmode.block_timeout_sec` in `config/system.json`).
- FE should renew the lock by calling `POST /api/devmode/seats/select` again (at half the timeout interval).

### Supporting endpoints

- `GET /api/devmode/catalog` — list of seats, signal families, and states so FE can build the tabs + buttons
- `GET /api/devmode/status` — current lock state for each seat (`selected`, `owned`, `connected`, `expires_at`, `remaining_sec`)
- `POST /api/devmode/exit` — exit Dev Mode and release all locks owned by the current section

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

- `status_stale_timeout_sec` is the stale threshold used to mark signal status / ELK as no longer fresh in View B.
- FE reads this field to determine `Status Unknown` when a signal is missing or too old.

#### Auto-renew lock behavior

- When FE selects at least 1 seat in Dev Mode, the frontend automatically renews the lock on a cycle of about half `block_timeout_sec`.
- On every renewal, FE resends `POST /api/devmode/seats/select` with a map of the currently selected seats.
- If renewal fails because a seat is locked by another section or the backend returns an error, FE logs it and does not drop the lock automatically unless the user leaves Dev Mode or the timeout expires.
- The goal is to keep the current section's lock alive throughout the Dev Mode session without requiring extra user action.

---

## 3. Dev Mode — send signals

### Endpoint

- `POST /api/devmode/signals`

### Purpose

FE asks the backend to inject or set the state of a signal for multiple seats at the same time in Dev Mode.

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

### Successful response

Only seats that were actually updated are returned. The signal name is the shared family name, for example `HB_Request`.

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

- `ACR_RetractRequest`: refer to the corresponding signal `ACR_FL_RetractRequest`, (`5` or `10->25`)
- `ABL_RetractRequest`: refer to the corresponding signal `ABL_FL_RetractRequest`, (`0->5`, `11`, `12`)
- `ISB_Color`: color code as integer or hex-to-dec, for example `rgb(0, 255, 0) => 65280`
- `HB_Request`: `0`, `1`, `2`

### Mapping to actual CAN signals

| Signal family | Signal written to the bus |
| --- | --- |
| `ACR_RetractRequest` | `ACR_{SEAT}_RetractRequest` |
| `ABL_RetractRequest` | `ABL_{SEAT}_RetractRequest` |
| `HB_Request` | `HB_Request_{SEAT}` |
| `ISB_Color` | `ISB_{SEAT}_ColorRed` + `ISB_{SEAT}_ColorGreen` + `ISB_{SEAT}_ColorBlue` (split RGB) |

If a seat does not have a corresponding signal in the DBC (for example `HB_Request_FL`), that seat returns
`error = signal_not_available`, while the remaining seats are still applied.

---

## 4. ELK (E-Locking) — use existing signals

### Purpose

- `No Failure`: FE reads ELK state directly from the CAN signals already streamed by the existing system.
- `Failure Detected`: FE marks failure when the ELK signal reports `-1`, or when CAN signal status is not updated within the timeout.
- If at least 1 seat is in `failure`, the overall system state is `failure_detected`.

### Source signals in DBC v7 (TBD means not yet present in DBC v7, only a placeholder)

Use only signals that already exist in DBC v7; do not create any new API/WS:
- `ELK_FL_ActuatorStatus`, `ELK_FR_ActuatorStatus`, `ELK_RL1_ActuatorStatus`, `ELK_RL2_ActuatorStatus`, `ELK_RR1_ActuatorStatus`
- `COM_Status_PumaFLCan`, `COM_Status_PumaFRCan`, `COM_Status_PumaRL1Can`, `COM_Status_PumaRL2Can`, `COM_Status_PumaRR1Can`
- `COM_Status_PumaFLEthernet`, `COM_Status_PumaFREthernet`, `COM_Status_PumaRL1Ethernet`, `COM_Status_PumaRL2Ethernet`, `COM_Status_PumaRR1Ethernet`
- `COM_Status_PantherCan`, `COM_Status_PantherEthernet`
- `COM_Status_NvidiaJetsonCan`, `COM_Status_NvidiaJetsonEthernet`
- `ELK_ResetErrorFlags`: if HMI/FE needs to send a reset command, use the existing REST signal write instead of a dedicated endpoint

### Existing API/WS to use

- Existing WebSocket: `WS /ws/signals`
- Existing REST:
  - `GET /signals/available` to fetch metadata + signal names
  - `GET /signals/{signal_name}` to get the latest value
  - `PUT /signals/{signal_name}` to write one signal
  - `POST /signals/batch_update` to write multiple signals at once

### Processing rules

- `status` and `can_communication` are derived from existing CAN signals; there is no dedicated endpoint per seat.
- Signal connectivity that is missing or not updated within `reader.stale_threshold_sec` is treated as `not_connected`.
- FE subscribes to ELK/CAN status signals through the existing WS, or polls via `GET /signals/{signal_name}` if needed.
- `failure_detected` is derived from signal data, signal timeout, or `ELK_*_ActuatorStatus` having value `1` or `2`.
- If a seat is not connected or its ECU has failed, FE marks it as `disabled` / `failure` based on the signal value or timeout.
- unknown: when `ELK_*_ActuatorStatus` has value `3` after reset is pressed and no response has yet been received from the ECU, FE shows `Status Unknown` (gray) for that seat.

### Status mapping

- `0`: CAN communication lost / disconnected - red
- `1`: CAN communication OK / connected - green
- `0`: ELK `ok` - green
- `1`: ELK `failure at previous` - yellow
- `2`: ELK `failure now` - red
- `3`: invalid state, wait response after reset - gray
- missing or stale signal: `Status Unknown` (not `No Failure`)

### Reset error flag

- If ECU error reset is needed: use the existing signal `ELK_ResetErrorFlags` via `PUT /signals/ELK_ResetErrorFlags` with payload:

```json
{ "value": 1 }
```

- FE currently sends `1` when the user clicks `Reset E-Locking Failure Memory`.

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
* `"signals"` or `"channels"` can be used interchangeably in the request body, but `"signals"` is preferred for clarity.

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

### Note about values
- `0`: CAN communication lost / disconnected - red
- `1`: CAN communication OK / connected - green
- `0`: ELK `no failure` - green
- `1`: ELK `failure at previous` - yellow
- `2`: ELK `failure now` - red
- `3`: invalid state, wait response after reset - gray

> Ethernet and some status signals are not present in DBC v7
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
- If Inflate/Exflate airbag control is needed, use the existing signals `SEAL_AirbagRequestInflate`/`SEAL_AirbagRequestExflate`
via `PUT /signals/SEAL_InflateAirbag` or `PUT /signals/SEAL_ExflateAirbag` with payload:

```json
{ "value": 1 }
```

---

## 7. Main API summary

- `GET /signals/available` => check whether the signal exists in candb
- `GET /signals/{signal_name}` => get the current value of a signal
- `PUT /signals/{signal_name}` => update the value of a signal
- `POST /api/devmode/signals` => update the value of a signal for a group of seats in Dev Mode
- `POST /signals/batch_update` => update multiple signal values at once (if not using `POST /api/devmode/signals`)
- `WS /ws/signals`

## 8. Dev Mode view summary
Dev-mode view: (distinguished from the user view)
Devmode has 2 views.
view a:
The view has 5 switches representing the 5 seats, each of which can be selected or not.
There are 4 tabs (ACR, ABL, ISB, HB corresponding to the signals `ACR_RetractRequest`, `ABL_RetractRequest`, `ISB_Color`, `HB_Request`); each tab has n buttons corresponding to the n states of each signal.
The function of this view is to change the signal state for multiple seats at once.
note: when turning on a seat switch, remember to notify the backend so it can create a 1-minute timeout to block other modes from operating on that seat
View b:
the view contains these components:
PUMA per seat: shows CAN, Ethernet, ELK signals
PANTHER (PANTHER computer): shows CAN, Ethernet signals
NVIDIA JETSON (NVIDIA JETSON computer): shows CAN, Ethernet signals
each component shows the connection/disconnection status of the signals



add 1 button to request "reset Elocking Failure Memory"
