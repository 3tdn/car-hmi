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
    std_name: str | None = Field(None, description="Standard signal name; currently identical to signal_name")
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
    std_name: str | None = Field(None, description="Standard signal name; currently identical to signal_name")
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

    timestamp: float = Field(..., description="Unix timestamp when metrics were collected")

    # CPU
    cpu_percent: float = Field(..., description="Overall CPU usage (%)")
    cpu_percent_per_core: list[float] = Field(..., description="Per-core CPU usage (%)")
    cpu_count_logical: int = Field(..., description="Logical CPU core count")
    cpu_count_physical: int = Field(..., description="Physical CPU core count")
    cpu_freq_current_mhz: float = Field(..., description="Current CPU frequency (MHz)")
    cpu_freq_max_mhz: float = Field(..., description="Maximum CPU frequency (MHz)")

    # Process
    process_cpu_percent: float = Field(..., description="Application process CPU usage (%)")
    process_memory_rss_mb: float = Field(..., description="Process RSS memory (MB)")
    process_memory_vms_mb: float = Field(..., description="Process virtual memory (VMS) (MB)")
    process_memory_percent: float = Field(..., description="Percentage of system RAM used by the process")
    process_threads: int = Field(..., description="Process thread count")
    process_open_files: int = Field(..., description="Number of open file descriptors")
    process_pid: int = Field(..., description="Application process PID")

    # RAM
    ram_total_mb: float = Field(..., description="Total physical RAM (MB)")
    ram_available_mb: float = Field(..., description="Available RAM (MB)")
    ram_used_mb: float = Field(..., description="RAM currently in use (MB)")
    ram_percent: float = Field(..., description="RAM usage (%)")

    # Swap
    swap_total_mb: float = Field(..., description="Total swap capacity (MB)")
    swap_used_mb: float = Field(..., description="Swap currently in use (MB)")
    swap_percent: float = Field(..., description="Swap usage (%)")

    # Disk
    disk_total_gb: float = Field(..., description="Total disk capacity (GB)")
    disk_used_gb: float = Field(..., description="Used disk space (GB)")
    disk_free_gb: float = Field(..., description="Free disk space (GB)")
    disk_percent: float = Field(..., description="Disk usage (%)")

    # Network I/O
    net_bytes_sent: int = Field(..., description="Total bytes sent over the network")
    net_bytes_recv: int = Field(..., description="Total bytes received over the network")
    net_packets_sent: int = Field(..., description="Total packets sent")
    net_packets_recv: int = Field(..., description="Total packets received")

    # Application-specific
    queue_size: int = Field(..., description="Current number of items in the signal processing queue")
    queue_maxsize: int = Field(..., description="Maximum queue size")
    queue_usage_percent: float = Field(..., description="Queue usage (%)")
    heap_allocated_mb: float = Field(..., description="Allocated Python heap memory (MB)")
    gc_objects: int = Field(..., description="Number of Python objects tracked by the Garbage Collector")
    asyncio_tasks: int = Field(..., description="Number of running asyncio tasks")
    uptime_seconds: float = Field(..., description="Application uptime (seconds)")
    python_version: str = Field(..., description="Python version in use")
    platform: str = Field(..., description="Operating system / platform information")


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
    """Request to create a new profile — POST /api/profile."""

    name: str = Field(..., description="Profile name (unique)")
    signals: list["ProfileSignal"] = Field(default_factory=list, description="List of signals and per-signal permissions")
    exinfo: dict[str, Any] = Field(default_factory=dict, description="Arbitrary data for the frontend")
    description: str | None = Field(None, description="Short profile description")

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
    """Request to update a profile (optimistic lock) — PUT /api/profile."""

    name: str = Field(..., description="Profile name to update")
    signals: list["ProfileSignal"] = Field(..., description="List of signals and per-signal permissions")
    exinfo: dict[str, Any] | None = Field(None, description="Arbitrary data for the frontend (leave empty to keep unchanged)")
    description: str | None = Field(None, description="Short description")
    section_id: str = Field(
        ...,
        description="Current section_id (from GET /api/profile). Used to prevent concurrent overwrites — 409 on mismatch.",
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
    """Request to change the active profile on the server."""

    name: str = Field(..., description="Profile name to become active")


class ProfileResponse(BaseModel):
    """Profile information."""

    name: str = Field(..., description="Profile name")
    signals: list["ProfileSignal"] = Field(..., description="List of signals and per-signal permissions")
    exinfo: dict[str, Any] = Field(default_factory=dict, description="Arbitrary data for the frontend")
    description: str | None = Field(None, description="Description")
    section_id: str = Field(..., description="Hash used for optimistic locking")


class ProfileSignal(BaseModel):
    """Signal scope in a profile with signal-specific permissions."""

    name: str = Field(..., description="Signal name")
    permission: list[ProfilePermission] = Field(
        default_factory=lambda: ["read"],
        description="Permissions for the signal: read, write, full",
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
    """List of all profiles."""

    profiles: list[ProfileResponse]
    total: int
    active: str | None = Field(None, description="Currently active profile name")
    global_active: str | None = Field(None, description="Globally active profile name")
    client_id: str | None = Field(None, description="Client ID if the request includes X-Client-Id")


class ActiveProfileResponse(BaseModel):
    """Active profile change result."""

    active: str = Field(..., description="Active profile name after the update")
    global_active: str | None = Field(None, description="Globally active profile name")
    client_id: str | None = Field(None, description="Client ID if updated for a client session")
    warnings: list[AccessWarning] = Field(default_factory=list, description="Warnings if the state did not change")


class ClientProfileSession(BaseModel):
    """Current profile session for a client."""

    client_id: str = Field(..., description="Client ID from the X-Client-Id header")
    active: str = Field(..., description="Active profile name for this client")
    updated_at: float = Field(..., description="Unix timestamp of the latest update")
    last_seen: float = Field(..., description="Unix timestamp of the latest heartbeat")
    status: Literal["online", "offline"] = Field(..., description="Online/offline status based on TTL")


class ProfileSessionProfileStat(BaseModel):
    """Session counts by active profile."""

    profile_name: str = Field(..., description="Profile name currently active for the session")
    total: int = Field(..., description="Total sessions currently using this active profile")
    online: int = Field(..., description="Number of online sessions for this profile")
    offline: int = Field(..., description="Number of offline sessions for this profile")


class ProfileSessionsResponse(BaseModel):
    """List of active-profile sessions by client."""

    sessions: list[ClientProfileSession] = Field(default_factory=list, description="List mapping client -> active profile")
    total: int = Field(..., description="Total client sessions")
    online_total: int = Field(0, description="Total online sessions")
    offline_total: int = Field(0, description="Total offline sessions")
    by_profile: list[ProfileSessionProfileStat] = Field(
        default_factory=list,
        description="Statistics for active sessions by profile",
    )
    global_active: str | None = Field(None, description="Default active profile at the global level")
    ttl_seconds: int = Field(..., description="TTL used to determine online/offline status")
    server_time: float = Field(..., description="Current Unix timestamp on the server")


class ProfileHeartbeatResponse(BaseModel):
    """Client session heartbeat update result."""

    client_id: str = Field(..., description="Client ID whose heartbeat was updated")
    active: str | None = Field(None, description="Active profile for the client, or the global fallback")
    last_seen: float = Field(..., description="Unix timestamp of the latest heartbeat")
    ttl_seconds: int = Field(..., description="Current session TTL")


# ── System info model ────────────────────────────────────────────────────────


class SystemInfoResponse(BaseModel):
    """Project overview and system status — GET /api/info."""

    name: str = Field(..., description="Application name")
    version: str = Field(..., description="API version")
    description: str = Field(..., description="Description")
    uptime_seconds: float = Field(..., description="Uptime (seconds)")
    bus_connected: bool = Field(..., description="Whether the CAN bus is connected")
    db_connected: bool = Field(..., description="Whether the database is connected")
    signal_count: int = Field(..., description="Number of signals currently in the store")


# Processor config models (runtime API)
class ProcessorConfigResponse(BaseModel):
    """Current processor pipeline configuration."""

    max_queue_size: int = Field(..., description="Maximum signal queue size")
    queue_policy: str = Field(..., description="Policy when the queue is full: 'drop_oldest' or 'reject'")


class UpdateProcessorConfigRequest(BaseModel):
    """Request to update the processor configuration (PATCH)."""

    max_queue_size: int | None = Field(None, description="New queue size")
    queue_policy: Literal["drop_oldest", "reject"] | None = Field(None, description="Handling policy when the queue is full")


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
