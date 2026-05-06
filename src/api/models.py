"""Các model Pydantic request/response cho REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Model tín hiệu ─────────────────────────────────────────────────────────


class SignalValueResponse(BaseModel):
    signal_name: str
    value: float
    unit: str | None = None
    timestamp: float


class SignalListResponse(BaseModel):
    items: list[SignalValueResponse]
    total: int


class WriteSignalRequest(BaseModel):
    value: float = Field(..., description="Value to write to the CAN bus")


# ── Model metadata tín hiệu (available signals) ───────────────────────────


class SignalMetadata(BaseModel):
    """Full metadata cho 1 signal — trả về bởi GET /signals/available."""

    signal_name: str
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    writable: bool = False
    group_name: str | None = None
    widget_type: str | None = None
    # Alarm thresholds
    alarm_warning_high: float | None = None
    alarm_warning_low: float | None = None
    alarm_critical_high: float | None = None
    alarm_critical_low: float | None = None
    # Current snapshot (optional, included for convenience)
    value: float | None = None
    status: str | None = None
    timestamp: float | None = None


class SignalMetadataListResponse(BaseModel):
    items: list[SignalMetadata]
    total: int


# ── Model subscribe request (WS command) ──────────────────────────────────


class SubscribeRequest(BaseModel):
    """Client → Server qua WS: subscribe/unsubscribe channels."""

    action: str = Field(..., pattern="^(subscribe|unsubscribe)$")
    channels: list[str] = Field(
        ...,
        description="List of channel names: signal names, 'alarms', 'metrics', or '*' for all signals",
    )
    mode: str = Field(
        "continuous",
        pattern="^(continuous|once)$",
        description="continuous = stream updates, once = send current value then stop",
    )


# ── Model cảnh báo ─────────────────────────────────────────────────────────


class AlarmResponse(BaseModel):
    id: int
    signal_name: str
    level: str
    value: float
    threshold: float
    description: str
    triggered_at: float
    acknowledged: bool
    resolved_at: float | None = None


class AlarmListResponse(BaseModel):
    items: list[AlarmResponse]
    total: int


# ── Model cấu hình ────────────────────────────────────────────────────────


class SignalConfigResponse(BaseModel):
    signal_name: str
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    group_name: str | None = None
    widget_type: str | None = None
    writable: bool = False


class UpdateSignalConfigRequest(BaseModel):
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    widget_type: str | None = None
    writable: bool | None = None


# ── Model hệ thống / sức khỏe ────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str  # ok | degraded | error
    uptime_seconds: float
    bus_connected: bool
    db_connected: bool


class ReadinessResponse(BaseModel):
    ready: bool
    details: dict[str, bool]


# ── Model thông tin tài nguyên CarPC ──────────────────────────────────────


class SystemMetricsResponse(BaseModel):
    timestamp: float

    # CPU
    cpu_percent: float
    cpu_percent_per_core: list[float]
    cpu_count_logical: int
    cpu_count_physical: int
    cpu_freq_current_mhz: float
    cpu_freq_max_mhz: float

    # Process
    process_cpu_percent: float
    process_memory_rss_mb: float
    process_memory_vms_mb: float
    process_memory_percent: float
    process_threads: int
    process_open_files: int
    process_pid: int

    # RAM
    ram_total_mb: float
    ram_available_mb: float
    ram_used_mb: float
    ram_percent: float

    # Swap
    swap_total_mb: float
    swap_used_mb: float
    swap_percent: float

    # Disk
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent: float

    # Network I/O
    net_bytes_sent: int
    net_bytes_recv: int
    net_packets_sent: int
    net_packets_recv: int

    # Application-specific
    queue_size: int
    queue_maxsize: int
    queue_usage_percent: float
    heap_allocated_mb: float
    gc_objects: int
    asyncio_tasks: int
    uptime_seconds: float
    python_version: str
    platform: str


# Processor config models (runtime API)
class ProcessorConfigResponse(BaseModel):
    max_queue_size: int
    queue_policy: str


class UpdateProcessorConfigRequest(BaseModel):
    max_queue_size: int | None = None
    queue_policy: Literal["drop_oldest", "reject"] | None = None
