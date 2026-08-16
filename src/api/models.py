"""Pydantic request/response models for the REST API."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

# ── Camera stream model ────────────────────────────────────────────────────


class CameraStatusResponse(BaseModel):
    """Status of the MJPEG camera stream proxy."""

    enabled: bool = Field(..., description="Whether the camera stream is enabled in config")
    stream_url: str = Field(..., description="MJPEG source URL proxied by CarPC")
    connected: bool = Field(..., description="Whether CarPC is currently able to connect to the camera")
    viewer_count: int = Field(..., description="Number of clients currently viewing the stream through CarPC")
    last_error: str | None = Field(None, description="Most recent upstream error, if any")

# ── Signal models ─────────────────────────────────────────────────────────


class SignalValueResponse(BaseModel):
    """Current value of a CAN signal."""

    signal_name: str = Field(..., description="Unique signal identifier")
    std_name: str | None = Field(None, description="Normalized name from sync_dict, if available")
    value: float = Field(..., description="Decoded real value")
    unit: str | None = Field(None, description="Measurement unit (for example: km/h, °C)")
    timestamp: float = Field(..., description="Unix timestamp (seconds) when the value was read")


class AccessWarning(BaseModel):
    """Access/profile warning information for a profile-scoped operation."""

    code: str = Field(..., description="Warning or access error code")
    message: str = Field(..., description="Short description for the frontend")
    profile_name: str | None = Field(None, description="Profile name currently in effect")
    required_permission: str | None = Field(None, description="Permission required for the operation")
    signal_name: str | None = Field(None, description="Affected signal name if this is a single-signal warning")
    signals: list[str] = Field(default_factory=list, description="List of signals skipped in the batch")


class SignalListResponse(BaseModel):
    """List of signal values returned by the API."""

    items: list[SignalValueResponse] = Field(..., description="List of signals")
    total: int = Field(..., description="Total number of signals in the list")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Access warnings, if any")


class WriteSignalRequest(BaseModel):
    """Request to write a value onto the CAN bus."""

    value: float = Field(..., description="Value to write to the CAN bus")


class BatchSignalWriteItem(BaseModel):
    """One signal in a batch write request."""

    signal_name: str = Field(..., description="Signal name")
    value: float = Field(..., description="Value to write")


class BatchSignalWrite(BaseModel):
    """Request to write multiple CAN signals at once — POST /signals/batch_update."""

    signals: list[BatchSignalWriteItem] = Field(..., description="List of signals to be written")


# ── Signal metadata models (available signals) ───────────────────────────


class SignalMetadata(BaseModel):
    """Full metadata for one signal — returned by GET /signals/available."""

    signal_name: str = Field(..., description="Unique signal identifier")
    std_name: str | None = Field(None, description="Normalized name from sync_dict, if available")
    tag: list[str] | None = Field(None, description="Tags inferred from the signal name or DBC configuration")
    unit: str | None = Field(None, description="Measurement unit")
    min_value: float | None = Field(None, description="Minimum valid value")
    max_value: float | None = Field(None, description="Maximum valid value")
    writable: bool = Field(False, description="Whether the signal can be written via the API")
    states: list[dict] | None = Field(None, description="List of enum states [{value, description}], or None for continuous numeric signals")
    group_name: str | None = Field(None, description="Functional group (for example: engine, body)")
    widget_type: str | None = Field(None, description="Frontend widget type")
    # Alarm thresholds
    alarm_warning_high: float | None = Field(None, description="High warning threshold")
    alarm_warning_low: float | None = Field(None, description="Low warning threshold")
    alarm_critical_high: float | None = Field(None, description="High critical threshold")
    alarm_critical_low: float | None = Field(None, description="Low critical threshold")
    # Current snapshot (optional, included for convenience)
    value: float | None = Field(None, description="Current value (snapshot, optional)")
    status: str | None = Field(None, description="Current alarm status (ok/warning/critical)")
    timestamp: float | None = Field(None, description="Unix timestamp of the latest read")


class SignalMetadataListResponse(BaseModel):
    """List of signal metadata."""

    signals_info: list[SignalMetadata] = Field(..., description="List of signal metadata")
    total: int = Field(..., description="Total number of signals")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Access warnings, if any")


# ── Subscribe request model (WS command) ──────────────────────────────────


class SubscribeRequest(BaseModel):
    """Client → Server over WS: subscribe/unsubscribe.

    Demo format (preferred):
        {"type": "subscribe", "signals": ["SignalName", "*", "alarms", "metrics"]}
        {"type": "unsubscribe", "signals": ["SignalName"]}
        {"type": "ping"}
    Legacy format (backward compat):
        {"action": "subscribe", "channels": ["SignalName"], "mode": "continuous"}
    """

    type: str | None = Field(None, description="Demo format: 'subscribe' | 'unsubscribe' | 'ping'")
    signals: list[str] | str | None = Field(None, description="Demo: list of signal names or '*'")
    action: str | None = Field(None, description="Legacy: 'subscribe' | 'unsubscribe'")
    channels: list[str] | None = Field(None, description="Legacy: list of channels")
    mode: str = Field(
        "continuous",
        description="continuous = stream continuously, once = send once then stop",
    )


# ── Alarm models ─────────────────────────────────────────────────────────


class AlarmResponse(BaseModel):
    """Detailed information about an alarm event."""

    id: int = Field(..., description="Auto-increment alarm ID in the database")
    signal_name: str = Field(..., description="Signal name that triggered the alarm")
    level: str = Field(..., description="Alarm level: 'warning' or 'critical'")
    value: float = Field(..., description="Signal value at the time of trigger")
    threshold: float = Field(..., description="Threshold crossed to trigger the alarm")
    description: str = Field(..., description="Alarm description")
    triggered_at: float = Field(..., description="Unix timestamp when the alarm triggered")
    acknowledged: bool = Field(..., description="True if the user has acknowledged the alarm")
    resolved_at: float | None = Field(None, description="Unix timestamp when the alarm was resolved (None if still active)")


class AlarmListResponse(BaseModel):
    """List of alarms returned by the API."""

    items: list[AlarmResponse] = Field(..., description="List of alarms")
    total: int = Field(..., description="Total alarm count")


# ── Configuration models ────────────────────────────────────────────────────────


class SignalConfigResponse(BaseModel):
    """Display configuration and metadata for a signal."""

    signal_name: str = Field(..., description="Signal identifier")
    unit: str | None = Field(None, description="Measurement unit")
    min_value: float | None = Field(None, description="Minimum valid value")
    max_value: float | None = Field(None, description="Maximum valid value")
    group_name: str | None = Field(None, description="Functional grouping")
    widget_type: str | None = Field(None, description="Frontend widget type")
    writable: bool = Field(False, description="Whether the signal is writable")


class UpdateSignalConfigRequest(BaseModel):
    """Request to update a partial signal configuration (PATCH)."""

    unit: str | None = Field(None, description="New measurement unit")
    min_value: float | None = Field(None, description="New minimum value")
    max_value: float | None = Field(None, description="New maximum value")
    widget_type: str | None = Field(None, description="New widget type")
    writable: bool | None = Field(None, description="Allow writes or not")


# ── System / health models ────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Overall system health status."""

    status: str = Field(..., description="Overall status: 'ok', 'degraded', or 'error'")
    uptime_seconds: float = Field(..., description="Number of seconds the system has been running continuously")
    bus_connected: bool = Field(..., description="True if the CAN bus connection is active")
    db_connected: bool = Field(..., description="True if the database connection is active")


class ReadinessResponse(BaseModel):
    """Readiness status for processing incoming requests."""

    ready: bool = Field(..., description="True if the application is ready to accept requests")
    details: dict[str, bool] = Field(..., description="Status of each component (key: component name, value: ready or not)")


# ── CarPC resource information model ──────────────────────────────────────


class SystemMetricsResponse(BaseModel):
    """System resource and application process information (CarPC metrics)."""

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
    signals: list["ProfileSignal"] = Field(default_factory=list, description="Danh sách signal và permission riêng cho từng signal")
    exinfo: dict[str, Any] = Field(default_factory=dict, description="Dữ liệu tùy ý dành cho frontend")
    description: str | None = Field(None, description="Mô tả ngắn về profile")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_profile_name(value)

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, value: list["ProfileSignal"]) -> list["ProfileSignal"]:
        unique: dict[str, ProfileSignal] = {}
        for item in value:
            unique[item.name] = item
        return list(unique.values())

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
    signals: list["ProfileSignal"] = Field(..., description="Danh sách signal và permission riêng cho từng signal")
    exinfo: dict[str, Any] | None = Field(None, description="Dữ liệu tùy ý dành cho frontend (bỏ trống để giữ nguyên)")
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
    def validate_signals(cls, value: list["ProfileSignal"]) -> list["ProfileSignal"]:
        unique: dict[str, ProfileSignal] = {}
        for item in value:
            unique[item.name] = item
        return list(unique.values())

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
    signals: list["ProfileSignal"] = Field(..., description="Danh sách signal và permission riêng cho từng signal")
    exinfo: dict[str, Any] = Field(default_factory=dict, description="Dữ liệu tùy ý dành cho frontend")
    description: str | None = Field(None, description="Mô tả")
    section_id: str = Field(..., description="Hash dùng cho optimistic locking")


class ProfileSignal(BaseModel):
    """Signal scope trong profile với permission riêng cho signal."""

    name: str = Field(..., description="Tên signal")
    permission: list[ProfilePermission] = Field(
        default_factory=lambda: ["read"],
        description="Quyền cho signal: read, write, full",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Signal name must not be empty")
        return normalized

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: list[ProfilePermission]) -> list[ProfilePermission]:
        return _normalize_profile_permissions(value)


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


# ── Dev Mode models ──────────────────────────────────────────────────────────


class DevModeSeatSelectRequest(BaseModel):
    """Select/deselect seats in Dev Mode — POST /api/devmode/seats/select."""

    seats: dict[str, bool] = Field(
        ...,
        description="Map seat_id → selected (fl, fr, rl1, rl2, rr1)",
    )
    block_timeout_sec: float | None = Field(
        None,
        ge=1,
        le=3600,
        allow_inf_nan=False,
        description=(
            "How long other sections stay blocked from writing the seat (seconds); "
            "defaults to 60"
        ),
    )


class DevModeSignalRequest(BaseModel):
    """Apply one signal family to several seats at once — POST /api/devmode/signals."""

    signal_name: str = Field(
        ...,
        description=(
            "Signal family: ACR_RetractRequest | ABL_RetractRequest | "
            "ISB_Color | HB_Request"
        ),
    )
    value: float = Field(..., description="Value applied to every selected seat")
    seats: dict[str, bool] = Field(..., description="Map seat_id → whether the value is applied")
    block_timeout_sec: float | None = Field(
        None,
        ge=1,
        le=3600,
        allow_inf_nan=False,
        description="Seat lock renewal duration (seconds); defaults to 60",
    )


ProfileCreate.model_rebuild()
ProfileUpdate.model_rebuild()
ProfileResponse.model_rebuild()
ProfileSignal.model_rebuild()
