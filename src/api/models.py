"""Các model Pydantic request/response cho REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Model tín hiệu ─────────────────────────────────────────────────────────


class SignalValueResponse(BaseModel):
    """Giá trị tức thời của một tín hiệu CAN."""

    signal_name: str = Field(..., description="Tên định danh của tín hiệu")
    value: float = Field(..., description="Giá trị số thực đã được giải mã")
    unit: str | None = Field(None, description="Đơn vị đo lường (ví dụ: km/h, °C)")
    timestamp: float = Field(..., description="Unix timestamp (giây) khi đọc được giá trị")


class SignalListResponse(BaseModel):
    """Danh sách giá trị tín hiệu trả về từ API."""

    items: list[SignalValueResponse] = Field(..., description="Danh sách các tín hiệu")
    total: int = Field(..., description="Tổng số tín hiệu trong danh sách")


class WriteSignalRequest(BaseModel):
    """Yêu cầu ghi một giá trị lên CAN bus."""

    value: float = Field(..., description="Giá trị cần ghi lên CAN bus")


# ── Model metadata tín hiệu (available signals) ───────────────────────────


class SignalMetadata(BaseModel):
    """Full metadata cho 1 signal — trả về bởi GET /signals/available."""

    signal_name: str = Field(..., description="Tên định danh duy nhất của tín hiệu")
    unit: str | None = Field(None, description="Đơn vị đo lường")
    min_value: float | None = Field(None, description="Giá trị tối thiểu hợp lệ")
    max_value: float | None = Field(None, description="Giá trị tối đa hợp lệ")
    writable: bool = Field(False, description="Tín hiệu có thể ghi từ API hay không")
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

    items: list[SignalMetadata] = Field(..., description="Danh sách metadata tín hiệu")
    total: int = Field(..., description="Tổng số tín hiệu")


# ── Model subscribe request (WS command) ──────────────────────────────────


class SubscribeRequest(BaseModel):
    """Client → Server qua WS: subscribe/unsubscribe channels."""

    action: str = Field(
        ...,
        pattern="^(subscribe|unsubscribe)$",
        description="Hành động: 'subscribe' để đăng ký hoặc 'unsubscribe' để huỷ đăng ký",
    )
    channels: list[str] = Field(
        ...,
        description="Danh sách kênh: tên tín hiệu, 'alarms', 'metrics', hoặc '*' cho tất cả tín hiệu",
    )
    mode: str = Field(
        "continuous",
        pattern="^(continuous|once)$",
        description="continuous = stream liên tục, once = gửi giá trị hiện tại rồi dừng",
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


# Processor config models (runtime API)
class ProcessorConfigResponse(BaseModel):
    """Cấu hình hiện tại của processor pipeline."""

    max_queue_size: int = Field(..., description="Kích thước tối đa của hàng đợi tín hiệu")
    queue_policy: str = Field(..., description="Chính sách khi hàng đợi đầy: 'drop_oldest' hoặc 'reject'")


class UpdateProcessorConfigRequest(BaseModel):
    """Yêu cầu cập nhật cấu hình processor (PATCH)."""

    max_queue_size: int | None = Field(None, description="Kích thước hàng đợi mới")
    queue_policy: Literal["drop_oldest", "reject"] | None = Field(None, description="Chính sách xử lý khi hàng đợi đầy")
