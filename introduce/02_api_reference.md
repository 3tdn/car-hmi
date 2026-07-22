# 02 — API Reference

> Toan bo REST endpoints va WebSocket protocol cua CAN-HMI backend  
> Base URL: `http://<host>:8000` (mac dinh `localhost:8000`)

---

## Authentication va Access Control

| Kenh | Co che |
|---|---|
| REST | Header `X-API-Key: <key>` |
| WebSocket | Query param `?api_key=<key>` |

Neu `api_key` trong `config/system.json` de trong hoac la placeholder (`change-me-in-production`) thi auth REST/WS se duoc tat (dev mode).

He thong hien tai bo sung profile-based access control:
- Header `X-Profile-Name`: profile duoc ap dung cho request.
- Header `X-Client-Id`: dinh danh session cua tung frontend client (de map active profile theo client).
- Header `X-Dev-Mode: true`: bo qua check permission cho mot so thao tac profile mutate trong moi truong dev.

Permission cua profile:
- `read`: doc signal/du lieu.
- `write`: ghi signal, acknowledge/resolve alarm.
- `full`: toan quyen (bao gom config mutate va profile mutate).

---

## REST Endpoints

### Signals

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/signals` | Lay snapshot signal hien tai (co the kem `warnings`) |
| `GET` | `/signals/available` | Metadata day du + alarm thresholds + current value |
| `GET` | `/signals/{name}` | Lay gia tri 1 signal |
| `GET` | `/signals/{name}/history` | Lich su signal tu DB |
| `PUT` | `/signals/{name}` | Ghi 1 signal vao CAN (202 Accepted) |
| `POST` | `/signals/batch_update` | Ghi nhieu signal cung luc (batch) |

API ho tro ca canonical signal name va `std_name` alias. Server tu resolve alias ve canonical truoc khi doc/ghi.

Ghi chu hanh vi quyen truy cap:
- Neu profile active khong cho phep mot signal, endpoint doc co the tra `200` voi `warnings` va danh sach da bi loc.
- Voi endpoint single-signal (`GET /signals/{name}`, `PUT /signals/{name}`), check permission/profile duoc xu ly truoc check ton tai signal, vi vay co the nhan `403` thay vi `404` neu signal nam ngoai scope profile.

**GET /signals** (vi du):
```json
{
  "items": [
    {"signal_name": "VehicleSpeed", "std_name": "VehicleSpeed", "value": 84.1, "unit": "km/h", "timestamp": 1742000000.0}
  ],
  "total": 1,
  "warnings": []
}
```

**POST /signals/batch_update** request:
```json
{
  " ": [
    {"signal_name": "VehicleSpeed", "value": 80.0},
    {"signal_name": "FuelLevel", "value": 25.0}
  ]
}
```

Response co the tra warning neu mot so signal nam ngoai scope cua profile:
```json
{
  "queued": [{"signal_name": "VehicleSpeed", "value": 80.0}],
  "count": 1,
  "queued_at": 1742000001.0,
  "errors": [],
  "warnings": [
    {
      "code": "profile_signal_filtered",
      "required_permission": "write",
      "signals": ["FuelLevel"]
    }
  ]
}
```

---

### Alarms

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/alarms` | Danh sach lich su canh bao |
| `GET` | `/alarms/{id}` | Chi tiet 1 canh bao |
| `POST` | `/alarms/{id}/acknowledge` | Xac nhan canh bao (can `write`) |
| `POST` | `/alarms/{id}/resolve` | Resolve canh bao (can `write`) |

Neu alarm khong ton tai/da xu ly roi thi server tra detail co code co cau truc (`alarm_not_found`, `alarm_acknowledge_conflict`, `alarm_resolve_conflict`).

---

### Config

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/config` | Danh sach config hien thi signal |
| `GET` | `/config/signal/{name}` | Config 1 signal |
| `PATCH` | `/config/signal/{name}` | Cap nhat config signal (can `full`) |
| `GET` | `/config/processor` | Lay processor config |
| `POST` | `/config/processor` | Cap nhat processor config (can `full`) |
| `GET` | `/config/general` | Lay full app config |
| `PATCH` | `/config/general` | Patch app config (can `full`) |
| `POST` | `/config/general/reset` | Reset app config ve mac dinh (can `full`) |
| `GET` | `/config/alarms` | Lay alarms config |
| `POST` | `/config/alarms` | Cap nhat alarms config (can `full`) |
| `POST` | `/config/alarms/reset` | Reset alarms config (can `full`) |

Luu y: thay doi `max_queue_size` se thu migrate runtime RX queue ma khong can restart app.

---

### Profiles

Tat ca profile endpoints nam duoi prefix `/api`.

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/api/profiles` | Liet ke profiles + active + global_active |
| `GET` | `/api/profile` | Lay profile theo `name` query hoac active profile |
| `POST` | `/api/profile` | Tao profile moi |
| `PUT` | `/api/profile` | Cap nhat profile (optimistic lock qua `section_id`) |
| `DELETE` | `/api/profile/{name}` | Xoa profile |
| `PUT` | `/api/profile/active` | Dat active profile (global hoac theo `X-Client-Id`) |
| `GET` | `/api/profile/sessions` | Liet ke map client -> active profile + online/offline |
| `POST` | `/api/profile/heartbeat` | Cap nhat heartbeat cho client session |
| `POST` | `/api/profile/offline` | Danh dau client session offline ngay lap tuc |

Chi tiet quan trong:
- `PUT /api/profile` bat buoc `section_id` dung voi state hien tai; sai thi `409 profile_section_mismatch`.
- Neu gui `X-Client-Id`, `PUT /api/profile/active` chi doi active cho client do, khong doi `global_active`.
- Heartbeat/offline yeu cau `X-Client-Id`, neu thieu se tra `400 client_id_required`.
- Legacy mutate request (khong co `X-Profile-Name` va `X-Client-Id`) co the bi chan voi `403 profile_headers_required` tuy config.

---

### Camera

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/api/camera/stream` | Proxy MJPEG stream tu camera |
| `GET` | `/api/camera/status` | Trang thai ket noi stream, so viewers, loi gan nhat |

---

### Adaptive Restraint

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/adaptive_restraint/available` | Gia tri filter kha dung |
| `GET` | `/adaptive_restraint/chart_info` | Box-plot + raw data preview |

Neu DB adaptive restraint chua san sang thi endpoint se tra `503 Service Unavailable`.

---

### Restraints Video Match

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/api/restraints/match` | Tim video restraint phu hop nhat |
| `GET` | `/api/restraints/video/{filename}` | Stream file video |

Tai lieu chi tiet nam trong `docs/restraints_api.md`.

---

### System

| Method | Path | Mo ta |
|---|---|---|
| `GET` | `/system/info` | Thong tin tong quan app/system |
| `GET` | `/system/health` | Health check |
| `GET` | `/system/ready` | Readiness check |
| `GET` | `/system/metrics` | CPU/RAM/disk/network/process/queue metrics |

System router duoc mount them duoi `/api`, nen cac endpoint sau cung kha dung:
- `/api/info`
- `/api/health`
- `/api/ready`
- `/api/metrics`

---

## WebSocket Endpoints

| Endpoint | Mo ta |
|---|---|
| `WS /ws/signals` | Endpoint chinh, ho tro subscribe/unsubscribe/ping |
| `WS /ws/subscribe` | Alias backward-compatible cua `/ws/signals` |
| `WS /ws/alarms` | Push alarm stream theo topic |
| `WS /ws/all` | Push tat ca theo topic cu |

WS auth dung query param `api_key`.

### Command format cho `/ws/signals` va `/ws/subscribe`

Ho tro dong thoi 2 dinh dang message:

Demo format:
```json
{"type": "subscribe", "signals": ["VehicleSpeed", "*", "alarms", "metrics"]}
```
```json
{"type": "unsubscribe", "signals": ["VehicleSpeed"]}
```
```json
{"type": "ping"}
```

Legacy format:
```json
{"action": "subscribe", "channels": ["VehicleSpeed"], "mode": "continuous", "rate_ms": 100}
```

Ack format:
```json
{
  "type": "subscribe_ack",
  "action": "subscribe",
  "channels": ["VehicleSpeed"],
  "count": 1,
  "warnings": []
}
```

Signal frame format tu server:
```json
{
  "timestamp": "2026-05-20T10:00:00.123Z",
  "signals": [
    {"name": "VehicleSpeed", "std_name": "VehicleSpeed", "value": 23.0}
  ]
}
```

Alarm frame format:
```json
{"type": "alarm", "signal_name": "EngineRPM", "level": "critical", "value": 7650.0}
```

Metrics frame format:
```json
{"type": "metrics", "cpu_percent": 12.4, "ram_percent": 33.1}
```

### Profile filtering tren WS

- Neu client subscribe bang `*` nhung co profile scope, server chi cho phep cac signal trong profile.
- Neu request signal ngoai scope profile, ack se kem warning `profile_signal_denied`.
- Neu profile khong du `read`, ack se kem warning `profile_permission_denied`.

---

## Error Codes

| HTTP Code | Y nghia |
|---|---|
| `200 OK` | Thanh cong |
| `201 Created` | Tao profile thanh cong |
| `202 Accepted` | Lenh ghi CAN da duoc queue |
| `400 Bad Request` | Thieu header bat buoc, payload khong hop le |
| `401 Unauthorized` | Sai/thieu API key |
| `403 Forbidden` | Thieu quyen profile hoac khong co profile hop le |
| `404 Not Found` | Signal/profile/alarm khong ton tai |
| `409 Conflict` | Optimistic lock mismatch hoac trang thai conflict |
| `422 Unprocessable Entity` | Sai schema request body |
| `503 Service Unavailable` | CAN writer/DB service chua san sang |

WebSocket close code:
- `4401` - Unauthorized (invalid `api_key`)
