"""Các model Pydantic request/response cho REST API."""

from __future__ import annotations

import re
from typing import Literal, cast

from pydantic import BaseModel, Field, field_validator

# ── Model camera stream ────────────────────────────────────────────────────


class CameraStatusResponse(BaseModel):
    """Trạng thái proxy MJPEG camera stream."""

    enabled: bool = Field(..., description="Camera stream có được bật trong config hay không")
    stream_url: str = Field(..., description="URL MJPEG nguồn mà CarPC proxy tới")
    connected: bool = Field(..., description="CarPC hiện có đang kết nối được tới camera hay không")
    viewer_count: int = Field(..., description="Số client đang xem stream qua CarPC")
    last_error: str | None = Field(None, description="Lỗi upstream gần nhất (nếu có)")

# ── Model tín hiệu ─────────────────────────────────────────────────────────


class SignalValueResponse(BaseModel):
    """Giá trị tức thời của một tín hiệu CAN."""

    signal_name: str = Field(..., description="Tên định danh của tín hiệu")
    std_name: str | None = Field(None, description="Tên chuẩn hóa theo sync_dict (nếu có)")
    value: float = Field(..., description="Giá trị số thực đã được giải mã")
    unit: str | None = Field(None, description="Đơn vị đo lường (ví dụ: km/h, °C)")
    timestamp: float = Field(..., description="Unix timestamp (giây) khi đọc được giá trị")


class AccessWarning(BaseModel):
    """Thông tin cảnh báo/quyền truy cập cho thao tác theo profile."""

    code: str = Field(..., description="Mã cảnh báo hoặc lỗi truy cập")
    message: str = Field(..., description="Mô tả ngắn gọn cho frontend")
    profile_name: str | None = Field(None, description="Tên profile đang được áp dụng")
    required_permission: str | None = Field(None, description="Quyền bắt buộc cho thao tác")
    signal_name: str | None = Field(None, description="Tên signal bị ảnh hưởng nếu là single-signal warning")
    signals: list[str] = Field(default_factory=list, description="Danh sách signal bị bỏ qua trong batch")


class SignalListResponse(BaseModel):
    """Danh sách giá trị tín hiệu trả về từ API."""

    items: list[SignalValueResponse] = Field(..., description="Danh sách các tín hiệu")
    total: int = Field(..., description="Tổng số tín hiệu trong danh sách")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Cảnh báo quyền truy cập nếu có")


class WriteSignalRequest(BaseModel):
    """Yêu cầu ghi một giá trị lên CAN bus."""

    value: float = Field(..., description="Giá trị cần ghi lên CAN bus")


class BatchSignalWriteItem(BaseModel):
    """Một signal trong batch write request."""

    signal_name: str = Field(..., description="Tên tín hiệu")
    value: float = Field(..., description="Giá trị cần ghi")


class BatchSignalWrite(BaseModel):
    """Yêu cầu ghi nhiều tín hiệu CAN cùng lúc — POST /signals/batch_update."""

    signals: list[BatchSignalWriteItem] = Field(..., description="Danh sách signal cần ghi")


# ── Model metadata tín hiệu (available signals) ───────────────────────────


class SignalMetadata(BaseModel):
    """Full metadata cho 1 signal — trả về bởi GET /signals/available."""

    signal_name: str = Field(..., description="Tên định danh duy nhất của tín hiệu")
    std_name: str | None = Field(None, description="Tên chuẩn hóa theo sync_dict (nếu có)")
    tag: list[str] | None = Field(None, description="Các tag suy ra từ tên signal hoặc cấu hình DBC")
    unit: str | None = Field(None, description="Đơn vị đo lường")
    min_value: float | None = Field(None, description="Giá trị tối thiểu hợp lệ")
    max_value: float | None = Field(None, description="Giá trị tối đa hợp lệ")
    writable: bool = Field(False, description="Tín hiệu có thể ghi từ API hay không")
    states: list[dict] | None = Field(None, description="Danh sách enum states [{value, description}], None nếu là số liên tục")
    group_name: str | None = Field(None, description="Nhóm chức năng (ví dụ: engine, body)")
    widget_type: str | None = Field(None, description="Loại widget hiển thị trên frontend")
    # Alarm thresholds
    alarm_warning_high: float | None = Field(None, description="Ngưỡng cảnh báo mức cao (warning)")
    alarm_warning_low: float | None = Field(None, description="Ngưỡng cảnh báo mức thấp (warning)")
    alarm_critical_high: float | None = Field(None, description="Ngưỡng nguy hiểm mức cao (critical)")
    alarm_critical_low: float | None = Field(None, description="Ngưỡng nguy hiểm mức thấp (critical)")
    # Current snapshot (optional, included for convenience)
    value: float | None = Field(None, description="Giá trị hiện tại (snapshot, tùy chọn)")
    status: str | None = Field(None, description="Trạng thái alarm hiện tại (ok/warning/critical)")
    timestamp: float | None = Field(None, description="Unix timestamp của lần đọc gần nhất")


class SignalMetadataListResponse(BaseModel):
    """Danh sách metadata tín hiệu."""

    signals_info: list[SignalMetadata] = Field(..., description="Danh sách metadata tín hiệu")
    total: int = Field(..., description="Tổng số tín hiệu")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Cảnh báo quyền truy cập nếu có")


# ── Model subscribe request (WS command) ──────────────────────────────────


class SubscribeRequest(BaseModel):
    """Client → Server qua WS: subscribe/unsubscribe.

    Demo format (ưu tiên):
        {"type": "subscribe", "signals": ["SignalName", "*", "alarms", "metrics"]}
        {"type": "unsubscribe", "signals": ["SignalName"]}
        {"type": "ping"}
    Legacy format (backward compat):
        {"action": "subscribe", "channels": ["SignalName"], "mode": "continuous"}
    """

    type: str | None = Field(None, description="Demo format: 'subscribe' | 'unsubscribe' | 'ping'")
    signals: list[str] | str | None = Field(None, description="Demo: danh sách tên signal hoặc '*'")
    action: str | None = Field(None, description="Legacy: 'subscribe' | 'unsubscribe'")
    channels: list[str] | None = Field(None, description="Legacy: danh sách kênh")
    mode: str = Field(
        "continuous",
        description="continuous = stream liên tục, once = gửi giá trị rồi dừng",
    )


# ── Model cảnh báo ─────────────────────────────────────────────────────────


class AlarmResponse(BaseModel):
    """Thông tin chi tiết của một sự kiện cảnh báo."""

    id: int = Field(..., description="ID tự tăng của cảnh báo trong database")
    signal_name: str = Field(..., description="Tên tín hiệu kích hoạt cảnh báo")
    level: str = Field(..., description="Mức cảnh báo: 'warning' hoặc 'critical'")
    value: float = Field(..., description="Giá trị tín hiệu tại thời điểm kích hoạt")
    threshold: float = Field(..., description="Ngưỡng đã vượt qua để kích hoạt cảnh báo")
    description: str = Field(..., description="Mô tả nội dung cảnh báo")
    triggered_at: float = Field(..., description="Unix timestamp khi cảnh báo được kích hoạt")
    acknowledged: bool = Field(..., description="True nếu người dùng đã xác nhận cảnh báo")
    resolved_at: float | None = Field(None, description="Unix timestamp khi cảnh báo được giải quyết (None nếu còn active)")


class AlarmListResponse(BaseModel):
    """Danh sách cảnh báo trả về từ API."""

    items: list[AlarmResponse] = Field(..., description="Danh sách cảnh báo")
    total: int = Field(..., description="Tổng số cảnh báo")


# ── Model cấu hình ────────────────────────────────────────────────────────


class SignalConfigResponse(BaseModel):
    """Cấu hình hiển thị và metadata của một tín hiệu."""

    signal_name: str = Field(..., description="Tên định danh tín hiệu")
    unit: str | None = Field(None, description="Đơn vị đo lường")
    min_value: float | None = Field(None, description="Giá trị tối thiểu hợp lệ")
    max_value: float | None = Field(None, description="Giá trị tối đa hợp lệ")
    group_name: str | None = Field(None, description="Nhóm chức năng")
    widget_type: str | None = Field(None, description="Loại widget hiển thị trên frontend")
    writable: bool = Field(False, description="Tín hiệu có thể ghi hay không")


class UpdateSignalConfigRequest(BaseModel):
    """Yêu cầu cập nhật một phần cấu hình tín hiệu (PATCH)."""

    unit: str | None = Field(None, description="Đơn vị đo lường mới")
    min_value: float | None = Field(None, description="Giá trị tối thiểu mới")
    max_value: float | None = Field(None, description="Giá trị tối đa mới")
    widget_type: str | None = Field(None, description="Loại widget mới")
    writable: bool | None = Field(None, description="Cho phép ghi hay không")


# ── Model hệ thống / sức khỏe ────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Trạng thái sức khỏe tổng thể của hệ thống."""

    status: str = Field(..., description="Trạng thái tổng quan: 'ok', 'degraded', hoặc 'error'")
    uptime_seconds: float = Field(..., description="Số giây hệ thống đã chạy liên tục")
    bus_connected: bool = Field(..., description="True nếu kết nối CAN bus đang hoạt động")
    db_connected: bool = Field(..., description="True nếu kết nối database đang hoạt động")


class ReadinessResponse(BaseModel):
    """Trạng thái sẵn sàng phục vụ request của ứng dụng."""

    ready: bool = Field(..., description="True nếu ứng dụng sẵn sàng nhận request")
    details: dict[str, bool] = Field(..., description="Chi tiết trạng thái từng thành phần (key: tên thành phần, value: sẵn sàng hay không)")


# ── Model thông tin tài nguyên CarPC ──────────────────────────────────────


class SystemMetricsResponse(BaseModel):
    """Thông tin tài nguyên hệ thống và tiến trình ứng dụng (CarPC metrics)."""

    timestamp: float = Field(..., description="Unix timestamp khi thu thập số liệu")

    # CPU
    cpu_percent: float = Field(..., description="Mức sử dụng CPU tổng thể (%)")
    cpu_percent_per_core: list[float] = Field(..., description="Mức sử dụng từng nhân CPU (%)")
    cpu_count_logical: int = Field(..., description="Số nhân CPU logic")
    cpu_count_physical: int = Field(..., description="Số nhân CPU vật lý")
    cpu_freq_current_mhz: float = Field(..., description="Tần số CPU hiện tại (MHz)")
    cpu_freq_max_mhz: float = Field(..., description="Tần số CPU tối đa (MHz)")

    # Process
    process_cpu_percent: float = Field(..., description="Mức sử dụng CPU của tiến trình ứng dụng (%)")
    process_memory_rss_mb: float = Field(..., description="Bộ nhớ RSS của tiến trình (MB)")
    process_memory_vms_mb: float = Field(..., description="Bộ nhớ ảo (VMS) của tiến trình (MB)")
    process_memory_percent: float = Field(..., description="Phần trăm RAM hệ thống mà tiến trình sử dụng")
    process_threads: int = Field(..., description="Số luồng (thread) của tiến trình")
    process_open_files: int = Field(..., description="Số file descriptor đang mở")
    process_pid: int = Field(..., description="PID của tiến trình ứng dụng")

    # RAM
    ram_total_mb: float = Field(..., description="Tổng RAM vật lý (MB)")
    ram_available_mb: float = Field(..., description="RAM còn khả dụng (MB)")
    ram_used_mb: float = Field(..., description="RAM đang được sử dụng (MB)")
    ram_percent: float = Field(..., description="Mức sử dụng RAM (%)")

    # Swap
    swap_total_mb: float = Field(..., description="Tổng dung lượng swap (MB)")
    swap_used_mb: float = Field(..., description="Swap đang sử dụng (MB)")
    swap_percent: float = Field(..., description="Mức sử dụng swap (%)")

    # Disk
    disk_total_gb: float = Field(..., description="Tổng dung lượng ổ đĩa (GB)")
    disk_used_gb: float = Field(..., description="Dung lượng ổ đĩa đã dùng (GB)")
    disk_free_gb: float = Field(..., description="Dung lượng ổ đĩa còn trống (GB)")
    disk_percent: float = Field(..., description="Mức sử dụng ổ đĩa (%)")

    # Network I/O
    net_bytes_sent: int = Field(..., description="Tổng số byte đã gửi qua mạng")
    net_bytes_recv: int = Field(..., description="Tổng số byte đã nhận qua mạng")
    net_packets_sent: int = Field(..., description="Tổng số gói tin đã gửi")
    net_packets_recv: int = Field(..., description="Tổng số gói tin đã nhận")

    # Application-specific
    queue_size: int = Field(..., description="Số phần tử hiện có trong hàng đợi xử lý tín hiệu")
    queue_maxsize: int = Field(..., description="Kích thước tối đa của hàng đợi")
    queue_usage_percent: float = Field(..., description="Mức sử dụng hàng đợi (%)")
    heap_allocated_mb: float = Field(..., description="Bộ nhớ heap Python đang cấp phát (MB)")
    gc_objects: int = Field(..., description="Số đối tượng Python đang được Garbage Collector theo dõi")
    asyncio_tasks: int = Field(..., description="Số tác vụ asyncio đang chạy")
    uptime_seconds: float = Field(..., description="Thời gian ứng dụng đã chạy (giây)")
    python_version: str = Field(..., description="Phiên bản Python đang sử dụng")
    platform: str = Field(..., description="Thông tin hệ điều hành / nền tảng")


# ── Profile models ──────────────────────────────────────────────────────────


ProfilePermission = Literal["read", "write", "full"]
_PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_PROFILE_PERMISSION_ORDER = ("read", "write", "full")


def _normalize_profile_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Profile name must not be empty")
    if not _PROFILE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("Profile name contains unsupported characters")
    return normalized


def _normalize_profile_signals(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in values:
        signal = item.strip()
        if not signal or signal in seen:
            continue
        seen.add(signal)
        unique.append(signal)
    return unique


def _normalize_profile_permissions(values: list[ProfilePermission]) -> list[ProfilePermission]:
    if not values:
        raise ValueError("At least one permission is required")

    ordered: list[ProfilePermission] = []
    seen: set[str] = set()
    for permission in _PROFILE_PERMISSION_ORDER:
        if permission in values and permission not in seen:
            ordered.append(cast(ProfilePermission, permission))
            seen.add(permission)
    if "full" in seen:
        return ["full"]
    return ordered


class ProfileCreate(BaseModel):
    """Yêu cầu tạo profile mới — POST /api/profile."""

    name: str = Field(..., description="Tên profile (duy nhất)")
    signals: list[str] = Field(default_factory=list, description="Danh sách tên signal trong profile")
    permission: list[ProfilePermission] = Field(
        default_factory=lambda: ["read"],
        description="Quyền thao tác của profile: read, write, full",
    )
    description: str | None = Field(None, description="Mô tả ngắn về profile")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_profile_name(value)

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, value: list[str]) -> list[str]:
        return _normalize_profile_signals(value)

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: list[ProfilePermission]) -> list[ProfilePermission]:
        return _normalize_profile_permissions(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ProfileUpdate(BaseModel):
    """Yêu cầu cập nhật profile (optimistic lock) — PUT /api/profile."""

    name: str = Field(..., description="Tên profile cần cập nhật")
    signals: list[str] = Field(..., description="Danh sách tên signal mới")
    # BEGIN LEGACY COMPAT: permission là optional để frontend cũ (chưa có permission field) vẫn hoạt động.
    # Nếu không gửi → giữ nguyên giá trị cũ. Xoá khi tất cả frontend đã cập nhật.
    permission: list[ProfilePermission] | None = Field(
        None,
        description="Quyền thao tác của profile: read, write, full (bỏ trống để giữ nguyên giá trị cũ)",
    )
    # END LEGACY COMPAT
    description: str | None = Field(None, description="Mô tả ngắn")
    section_id: str = Field(
        ...,
        description="section_id hiện tại (lấy từ GET /api/profile). Dùng để tránh ghi đè đồng thời — 409 nếu mismatch.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_profile_name(value)

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, value: list[str]) -> list[str]:
        return _normalize_profile_signals(value)

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: list[ProfilePermission] | None) -> list[ProfilePermission] | None:
        if value is None:
            return None
        return _normalize_profile_permissions(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) != 12:
            raise ValueError("section_id must have exactly 12 characters")
        return normalized


class ProfileSetActiveRequest(BaseModel):
    """Yêu cầu đổi active profile trên server."""

    name: str = Field(..., description="Tên profile sẽ trở thành active")


class ProfileResponse(BaseModel):
    """Thông tin một profile."""

    name: str = Field(..., description="Tên profile")
    signals: list[str] = Field(..., description="Danh sách tên signal")
    permission: list[ProfilePermission] = Field(..., description="Quyền thao tác của profile")
    description: str | None = Field(None, description="Mô tả")
    section_id: str = Field(..., description="Hash dùng cho optimistic locking")


class ProfilesResponse(BaseModel):
    """Danh sách tất cả profiles."""

    profiles: list[ProfileResponse]
    total: int
    active: str | None = Field(None, description="Tên profile đang active")
    global_active: str | None = Field(None, description="Tên active profile ở mức global")
    client_id: str | None = Field(None, description="Client ID nếu request có gửi X-Client-Id")


class ActiveProfileResponse(BaseModel):
    """Kết quả đổi active profile."""

    active: str = Field(..., description="Tên profile đang active sau khi cập nhật")
    global_active: str | None = Field(None, description="Tên active profile ở mức global")
    client_id: str | None = Field(None, description="Client ID nếu cập nhật theo session client")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Cảnh báo nếu trạng thái không thay đổi")


class ClientProfileSession(BaseModel):
    """Phiên profile hiện tại của một client."""

    client_id: str = Field(..., description="Client ID từ header X-Client-Id")
    active: str = Field(..., description="Tên profile active cho client này")
    updated_at: float = Field(..., description="Unix timestamp lần cập nhật gần nhất")
    last_seen: float = Field(..., description="Unix timestamp heartbeat gần nhất")
    status: Literal["online", "offline"] = Field(..., description="Trạng thái online/offline theo TTL")


class ProfileSessionProfileStat(BaseModel):
    """Thống kê số session theo từng profile active."""

    profile_name: str = Field(..., description="Tên profile đang được session kích hoạt")
    total: int = Field(..., description="Tổng số session đang active profile này")
    online: int = Field(..., description="Số session online của profile này")
    offline: int = Field(..., description="Số session offline của profile này")


class ProfileSessionsResponse(BaseModel):
    """Danh sách session active profile theo từng client."""

    sessions: list[ClientProfileSession] = Field(default_factory=list, description="Danh sách map client -> active profile")
    total: int = Field(..., description="Tổng số session client")
    online_total: int = Field(0, description="Tổng số session đang online")
    offline_total: int = Field(0, description="Tổng số session đang offline")
    by_profile: list[ProfileSessionProfileStat] = Field(
        default_factory=list,
        description="Thống kê số session active theo từng profile",
    )
    global_active: str | None = Field(None, description="Active profile mặc định ở mức global")
    ttl_seconds: int = Field(..., description="TTL dùng để đánh giá online/offline")
    server_time: float = Field(..., description="Unix timestamp hiện tại trên server")


class ProfileHeartbeatResponse(BaseModel):
    """Kết quả cập nhật heartbeat cho client session."""

    client_id: str = Field(..., description="Client ID đã được heartbeat")
    active: str | None = Field(None, description="Profile đang active cho client hoặc fallback global")
    last_seen: float = Field(..., description="Unix timestamp heartbeat mới nhất")
    ttl_seconds: int = Field(..., description="TTL session hiện hành")


# ── System info model ────────────────────────────────────────────────────────


class SystemInfoResponse(BaseModel):
    """Thông tin tổng quan dự án và trạng thái hệ thống — GET /api/info."""

    name: str = Field(..., description="Tên ứng dụng")
    version: str = Field(..., description="Phiên bản API")
    description: str = Field(..., description="Mô tả")
    uptime_seconds: float = Field(..., description="Thời gian đã chạy (giây)")
    bus_connected: bool = Field(..., description="CAN bus có kết nối không")
    db_connected: bool = Field(..., description="Database có kết nối không")
    signal_count: int = Field(..., description="Số tín hiệu đang có trong store")


# Processor config models (runtime API)
class ProcessorConfigResponse(BaseModel):
    """Cấu hình hiện tại của processor pipeline."""

    max_queue_size: int = Field(..., description="Kích thước tối đa của hàng đợi tín hiệu")
    queue_policy: str = Field(..., description="Chính sách khi hàng đợi đầy: 'drop_oldest' hoặc 'reject'")


class UpdateProcessorConfigRequest(BaseModel):
    """Yêu cầu cập nhật cấu hình processor (PATCH)."""

    max_queue_size: int | None = Field(None, description="Kích thước hàng đợi mới")
    queue_policy: Literal["drop_oldest", "reject"] | None = Field(None, description="Chính sách xử lý khi hàng đợi đầy")
