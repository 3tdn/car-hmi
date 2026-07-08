# Camera Live View — MJPEG Stream Proxy

## Bối cảnh

Camera của xe phát video dạng MJPEG qua một URL cố định trên mạng nội bộ CarPC:

```
http://192.168.2.119:8080/stream
```

MJPG server phía camera chỉ cho phép **DUY NHẤT 1 kết nối đồng thời** (có mutex ở
phía nguồn) — nếu 2 client cùng mở kết nối trực tiếp tới camera, kết nối thứ 2 sẽ
bị từ chối hoặc treo.

Trong khi đó, HMI cần cho phép **nhiều thiết bị đầu cuối** (mỗi thiết bị là 1 user)
cùng xem stream cùng lúc. Giải pháp: CarPC (backend) mở **đúng 1** kết nối tới
camera, đọc byte-stream MJPEG và **fan-out** (broadcast) dữ liệu đó cho nhiều client
tải qua HTTP tới CarPC. Mỗi client không đụng trực tiếp tới camera nên không vi phạm
giới hạn 1-connection của nguồn.

## Những thay đổi đã thực hiện

### Backend

| File | Thay đổi |
|---|---|
| `src/core/camera_stream.py` | **Mới.** `CameraStreamProxy` — quản lý 1 kết nối upstream MJPEG duy nhất, fan-out chunk byte cho nhiều subscriber (mỗi client 1 `asyncio.Queue`), tự động reconnect khi lỗi, drop chunk cũ cho client chậm để tránh nghẽn broadcast. |
| `src/api/routes/camera.py` | **Mới.** `GET /api/camera/stream` (StreamingResponse MJPEG, mỗi client subscribe qua proxy) và `GET /api/camera/status` (trạng thái kết nối, số viewer, lỗi gần nhất). |
| `src/core/config.py` | Thêm `CameraConfig` (enabled, stream_url, timeouts, reconnect interval, chunk size, queue size, startup_wait) và field `camera` trong `AppConfig`. |
| `config/system.json` | Thêm section `"camera"` với `stream_url = "http://192.168.2.119:8080/stream"`, `enabled = true`. |
| `src/api/app.py` | Đọc `camera` config qua `read_config()`, khởi tạo `CameraStreamProxy` nếu `enabled`, lưu vào `app.state.camera_proxy`, đăng ký router `/api/camera`, và cleanup khi shutdown qua `app.router.on_shutdown`. |
| `src/api/models.py` | Thêm `CameraStatusResponse`. |
| `pyproject.toml` | Thêm `httpx` vào dependencies chính (trước đây chỉ có ở `dev`) — dùng để mở kết nối streaming bất đồng bộ tới camera. |

### Frontend

| File | Thay đổi |
|---|---|
| `frontend/index.html` | Thêm card "Camera Live View" với `<img id="camera-stream-img">` trỏ tới `/api/camera/stream` (MJPEG hiển thị native qua thẻ `<img>`) và badge trạng thái kết nối. |
| `frontend/js/api.js` | Thêm `cameraStreamUrl()` và `fetchCameraStatus()`. |
| `frontend/js/app.js` | Thêm `initCameraStream()` — set `src` cho `<img>`, poll `/api/camera/status` mỗi 5s để cập nhật badge/viewer count, tự động retry khi stream lỗi. |

### Tests

| File | Thay đổi |
|---|---|
| `tests/test_camera_stream.py` | **Mới.** Mock `httpx.AsyncClient` để test `CameraStreamProxy` (fan-out 1 upstream → nhiều subscriber, viewer count, timeout content-type) và route `/api/camera/status` (200 khi có proxy, 503 khi chưa cấu hình). |

### Khác

- `ruff.toml`: thêm `src/core/camera_stream.py` vào danh sách ignore rule `S110` (reconnect loop bắt Exception rộng nhưng có log, không phải bug).

## Cách hoạt động (luồng xử lý)

```
Client A ──┐
Client B ──┼─→ GET /api/camera/stream (CarPC) ──┐
Client C ──┘                                     │
                                                  ▼
                                     CameraStreamProxy (1 kết nối duy nhất)
                                                  │
                                                  ▼
                                   http://192.168.2.119:8080/stream (camera)
```

1. Client đầu tiên gọi `GET /api/camera/stream` → proxy chưa có kết nối upstream nên
   tự khởi động (`open_subscription()`), chờ tối đa `startup_wait_sec` để xác định
   `Content-Type`/boundary thật từ camera.
2. Các client tiếp theo dùng chung kết nối upstream đã mở — không tạo thêm kết nối
   tới camera.
3. Mỗi chunk byte đọc được từ camera được broadcast (copy) cho tất cả subscriber
   đang mở.
4. Khi client cuối cùng đóng kết nối, proxy tự động dừng upstream (giải phóng tài
   nguyên); khi có subscriber mới, proxy reconnect.
5. Nếu upstream lỗi/rớt mạng, proxy tự retry sau `reconnect_interval_sec`.

## Cấu hình (`config/system.json` → `camera`)

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

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/camera/stream` | Trả về MJPEG stream (multipart/x-mixed-replace), dùng trực tiếp làm `src` của thẻ `<img>`. |
| GET | `/api/camera/status` | `{ enabled, stream_url, connected, viewer_count, last_error }`. |

## Lưu ý / TODO

- Cần xác nhận với SIMI lý do camera chỉ hỗ trợ 1 kết nối đồng thời (mutex) — hiện
  tại giải pháp proxy fan-out phía CarPC đã giải quyết được giới hạn này ở tầng
  ứng dụng, không cần thay đổi phía camera.
- Route hiện chưa yêu cầu API key (giống pattern `restraints`/`system`); có thể bổ
  sung `dependencies=[auth_dep]` nếu cần hạn chế truy cập stream.
