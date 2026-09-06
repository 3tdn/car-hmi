# Camera Live View — MJPEG Stream Proxy

## Background

The vehicle camera publishes MJPEG video through a fixed URL on the CarPC local network:

```
http://192.168.2.119:8080/stream
```

The MJPG server on the camera side allows **ONLY 1 concurrent connection** (there is a mutex at
the source) — if 2 clients open direct connections to the camera at the same time, the second
connection is rejected or hangs.

Meanwhile, the HMI needs to allow **multiple end devices** (each device is one user)
to watch the stream simultaneously. The solution: CarPC (backend) opens **exactly 1** connection to
the camera, reads the MJPEG byte stream, and **fans out** (broadcasts) that data to multiple clients
downloading over HTTP from CarPC. Each client never touches the camera directly, so the source's
1-connection limit is not violated.

## Changes implemented

### Backend

| File | Change |
|---|---|
| `src/core/camera_stream.py` | **New.** `CameraStreamProxy` — manages a single upstream MJPEG connection, fans out byte chunks to multiple subscribers (one `asyncio.Queue` per client), automatically reconnects on failure, and drops old chunks for slow clients to avoid broadcast congestion. |
| `src/api/routes/camera.py` | **New.** `GET /api/camera/stream` (MJPEG `StreamingResponse`, each client subscribes through the proxy) and `GET /api/camera/status` (connection status, viewer count, most recent error). |
| `src/core/config.py` | Add `CameraConfig` (enabled, stream_url, timeouts, reconnect interval, chunk size, queue size, startup_wait) and the `camera` field in `AppConfig`. |
| `config/system.json` | Add a `"camera"` section with `stream_url = "http://192.168.2.119:8080/stream"`, `enabled = true`. |
| `src/api/app.py` | Read `camera` config via `read_config()`, initialize `CameraStreamProxy` when `enabled`, store it in `app.state.camera_proxy`, register router `/api/camera`, and clean it up on shutdown via `app.router.on_shutdown`. |
| `src/api/models.py` | Add `CameraStatusResponse`. |
| `pyproject.toml` | Add `httpx` to the main dependencies (it previously existed only in `dev`) — used to open the asynchronous streaming connection to the camera. |

### Frontend

| File | Change |
|---|---|
| `frontend/index.html` | Add a "Camera Live View" card with `<img id="camera-stream-img">` pointing to `/api/camera/stream` (MJPEG displayed natively through the `<img>` tag) and a connection status badge. |
| `frontend/js/api.js` | Add `cameraStreamUrl()` and `fetchCameraStatus()`. |
| `frontend/js/app.js` | Add `initCameraStream()` — sets the `<img>` `src`, polls `/api/camera/status` every 5s to update the badge/viewer count, and automatically retries when the stream fails. |

### Tests

| File | Change |
|---|---|
| `tests/test_camera_stream.py` | **New.** Mock `httpx.AsyncClient` to test `CameraStreamProxy` (fan-out from 1 upstream → multiple subscribers, viewer count, timeout content-type) and route `/api/camera/status` (200 when a proxy exists, 503 when not configured). |

### Other

- `ruff.toml`: add `src/core/camera_stream.py` to the ignore list for rule `S110` (the reconnect loop catches a broad `Exception` but logs it, so this is not a bug).

## How it works (processing flow)

```
Client A ──┐
Client B ──┼─→ GET /api/camera/stream (CarPC) ──┐
Client C ──┘                                     │
                                                  ▼
                                     CameraStreamProxy (single connection)
                                                  │
                                                  ▼
                                   http://192.168.2.119:8080/stream (camera)
```

1. The first client calls `GET /api/camera/stream` → the proxy has no upstream connection yet, so
   it starts itself (`open_subscription()`), waits up to `startup_wait_sec` to determine the real
   `Content-Type`/boundary from the camera.
2. Subsequent clients share the same open upstream connection — no additional connections
   to the camera are created.
3. Each byte chunk read from the camera is broadcast (copied) to all currently open
   subscribers.
4. When the last client closes the connection, the proxy automatically stops the upstream (releasing
   resources); when a new subscriber appears, the proxy reconnects.
5. If the upstream fails or the network drops, the proxy retries automatically after `reconnect_interval_sec`.

## Configuration (`config/system.json` → `camera`)

```json
{
  "camera": {
    "enabled": true,
    "stream_url": "http://192.168.2.119:8080/stream",
    "reconnect_interval_sec": 3.0,
    "connect_timeout_sec": 5.0,
    "read_timeout_sec": 10.0,
    "chunk_size": 4096,
    "subscriber_queue_size": 64,
    "startup_wait_sec": 5.0
  }
}
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/camera/stream` | Returns the MJPEG stream (`multipart/x-mixed-replace`), used directly as the `src` of an `<img>` tag. |
| GET | `/api/camera/status` | `{ enabled, stream_url, connected, viewer_count, last_error }`. |

## Notes / TODO

- Need to confirm with SIMI why the camera supports only 1 concurrent connection (mutex) — for now,
  the fan-out proxy solution on the CarPC side already solves this limitation at the application
  layer, with no camera-side changes required.
- The route currently does not require an API key (following the `restraints`/`system` pattern); `dependencies=[auth_dep]`
  can be added if stream access needs to be restricted.
