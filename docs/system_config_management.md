# Quản lý system config

`config/system.json` chỉ chứa giá trị runtime. Policy, mô tả, validation và thông tin render
GUI nằm trong `config/system.fields.json`, được backend load như nguồn sự thật duy nhất. GUI đọc
policy từ `GET /config/system`; vì vậy field bị khóa và mức reload không bị hard-code ở frontend.

Mỗi definition dùng path dạng `reader.stale_threshold_sec`; wildcard `*` khớp đúng một segment,
ví dụ `can.*.can_db_file` áp dụng cho mọi CAN channel. Backend tự sinh `editable` từ
`reload_level`, validate metadata khi khởi động và validate giá trị thay đổi trước khi ghi file.
Các constraint đặc thù của DBC kiểm tra đuôi `.dbc`, file tồn tại và parse được.

## Mức áp dụng

| Mức | Ý nghĩa |
|---|---|
| `live` | Lưu file và đồng bộ ngay các object runtime đang tham chiếu giá trị đó. |
| `reboot` | Cho phép lưu, nhưng phải gọi `POST /system/reboot` để áp dụng đầy đủ. |
| `immutable` | API từ chối thay đổi. Đây là secret, đường dẫn tài nguyên đang mở hoặc field chưa được runtime triển khai. |

## Các giá trị có thể thay đổi live

| Nhóm | Field |
|---|---|
| API | `api.ws_metrics_interval_sec` |
| Storage | `storage.batch_size`, `storage.batch_interval_sec`, `storage.retention_days`, `storage.max_disk_mb` |
| Processor | `processor.max_update_rate_hz`, `processor.max_queue_size`, `processor.queue_policy`, `processor.batch_drain_size` |
| Reader | `reader.frequency_piority`, `reader.only_send_signal_update`, `reader.stale_threshold_sec` |
| Writer | `writer.periodic_mode`, `writer.periodic_time_step`, `writer.periodic_duration`, `writer.use_prevalue_for_unwritten_signal` |
| Runtime | `shutdown.timeout_sec`, `logging.level` |
| Dev Mode | `devmode.block_timeout_sec`, `devmode.require_seat_connected`, `devmode.bypass_check_CAN_status` |
| Config manager | `config_management.backup_retention_count` |

Khi đổi `processor.max_queue_size`, runner chuyển reader sang queue mới, drain dữ liệu còn lại
và đổi pipeline sang queue mới. Các field reader/writer/pipeline/WebSocket và app state được
đồng bộ trên cùng lần update.

## Các giá trị được sửa nhưng cần reboot

| Nhóm | Field |
|---|---|
| Multi-CAN | `can` (thêm/xóa channel), `can.*.interface`, `can.*.channel`, `can.*.bitrate`, `can.*.can_db_file` |
| Simulator | `simulator.enabled`, `simulator.random_mode`, `simulator.default_cycle_ms`, `simulator.can_db_file` |
| API server | `api.host`, `api.port`, `api.cors_origins` |
| Profile | `profiles.default_profile_permission`, `profiles.session_online_ttl_seconds`, `profiles.session_history_limit`, `profiles.session_cleanup_interval_sec` |
| Camera | toàn bộ `camera.*` |
| Status monitor | `status_monitor.enabled`, `status_monitor.interval_sec`, `status_monitor.ping_timeout_sec`, `status_monitor.targets.*` |
| Supervisor/log | `supervisor.watchdog_interval_sec`, `logging.max_size_mb`, `logging.backup_count` |

Response của PATCH/reset/restore có `reboot_required` và danh sách cụ thể trong
`reload.reboot`. Việc lưu config không tự reboot hệ thống.

## Các giá trị không được sửa qua API

| Field | Lý do |
|---|---|
| `adaptive_restraint.db_path`, `adaptive_restraint.csv_path` | Module đang giữ tham chiếu dữ liệu đã mở/cache. |
| `api.api_key` | Secret xác thực; GET luôn che thành `********`. |
| `api.ws_heartbeat_interval_sec` | Chưa được WebSocket runtime sử dụng. |
| `profiles.profiles_path`, `profiles.sessions_path` | Đường dẫn dữ liệu/quyền truy cập cố định trong vòng đời process. |
| `profiles.allow_legacy_profile_mutations` | Chưa được runtime triển khai. |
| `storage.engine`, `storage.sqlite_path` | Không thay backend/database đang mở trong process. |
| `processor.smoothing_window` | Chưa có smoothing stage trong pipeline hiện tại. |
| `writer.rate_limit_per_sec`, `writer.burst` | Token-bucket writer chưa được triển khai. |
| `logging.file_path` | File handler đã mở; không đổi qua runtime API. |

Field không có trong policy cũng bị từ chối nếu client cố thay đổi. Field lạ đã có sẵn trong
file vẫn được giữ nguyên khi patch một field khác, giúp forward compatibility mà không cho
client tự thêm cấu hình không được hỗ trợ.

## API và quyền

Các endpoint ghi dùng cùng `require_profile_permission(..., "full")` như cập nhật profile:

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/config/system` | Config đã che secret, field policy và đường dẫn logic. |
| `PATCH` | `/config/system` | Deep partial patch, validate trước khi ghi, không làm mất field ngoài patch. |
| `POST` | `/config/system/reload` | Đọc lại file và áp dụng các field `live`. |
| `POST` | `/config/system/reset` | Reset từ template cố định. |
| `GET` | `/config/system/backups` | Liệt kê backup. |
| `POST` | `/config/system/backups` | Tạo backup thủ công. |
| `POST` | `/config/system/backups/{id}/restore` | Restore backup và tự backup trạng thái hiện tại trước. |
| `POST` | `/system/reboot` | Graceful reboot qua supervisor; vẫn yêu cầu API key thật và `X-Dev-Mode: true`. |

Alias `/config/general`, `/config/general/reset` được giữ để tương thích client cũ.

## Đường dẫn cố định và an toàn test

- Config runtime: `config/system.json`
- Template reset: `config/system_bk.json`
- Field definitions: `config/system.fields.json`
- Backup: `config/backups/*.json`
- Số backup giữ lại: `config_management.backup_retention_count` (1–200)

API không nhận path từ client. Backup id được kiểm tra và chỉ resolve bên trong thư mục backup.
Mọi test update/reset/backup dùng `tmp_path` và inject `SystemConfigManager` với ba đường dẫn tạm;
test không ghi vào `config/system.json` thật.
