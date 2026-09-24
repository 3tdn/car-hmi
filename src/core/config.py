"""Global configuration loader using Pydantic BaseSettings."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CANConfig(BaseModel):
    """Configuration for a CAN bus channel."""

    interface: str = "virtual"
    # python-can driver name: "socketcan", "virtual", "kvaser", "pcan", …
    channel: str = "vcan0"
    # OS channel name (vcan0, can0) or device name depending on the interface
    bitrate: int = Field(default=500_000, gt=0)
    # Bus bitrate in bit/s (500_000 = classic CAN, 2_000_000 = nominal CAN FD)
    can_db_file: str = "db/can_db/p_v2.dbc"
    # DBC file describing messages/signals for this channel — read directly (via cantools)
    # by CANReader/CANWriter, no can.json export step needed.
    channel_tracking_signals: list[str] = Field(default_factory=list)
    # For channel='auto', probe only messages containing these signals.
    # An empty list preserves discovery using all messages with signals in the DBC.

    @field_validator("channel_tracking_signals")
    @classmethod
    def validate_channel_tracking_signals(cls, signals: list[str]) -> list[str]:
        if any(not signal.strip() for signal in signals):
            raise ValueError("channel_tracking_signals must contain non-empty signal names")
        return signals


class SimulatorConfig(BaseModel):
    """Configuration for the built-in CAN simulator (used in dev/test environments)."""

    enabled: bool = True
    # Enable/disable the simulator; it should be disabled (false) on a real vehicle
    random_mode: bool = False
    # If True, the simulator transmits random signal values
    # If False, the simulator transmits values incremented by 1 unit (or 1 state)
    default_cycle_ms: int = Field(default=50, gt=0)
    # Transmit period per message in ms; reducing it increases bus load
    can_db_file: str = "db/can_db/p_v2.dbc"
    # DBC file containing the messages the simulator will transmit (read directly via cantools)


class APIConfig(BaseModel):
    """Configuration for the REST API and WebSocket server (FastAPI / Uvicorn)."""

    host: str = "0.0.0.0"
    # Bind address; "0.0.0.0" = listen on all interfaces, "127.0.0.1" = local only
    port: int = Field(default=8000, ge=1, le=65535)
    # HTTP port; change it if there is a conflict or if running behind a reverse proxy
    api_key: str = "change-me-in-production"
    # Bearer token used for API authentication; MUST be changed before deploying to production
    ws_metrics_interval_sec: float = Field(default=3.0, gt=0)
    # Interval for sending system metrics snapshots over WebSocket (seconds)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"])
    # Exact origins or IPv4 patterns where "x"/"*" matches one numeric segment


class CameraConfig(BaseModel):
    """Configuration for the camera stream proxy (MJPEG)."""

    enabled: bool = False
    # Enable/disable the camera stream route
    stream_url: str = "http://192.168.2.119:8080/stream"
    # Source MJPEG stream URL (CarPC accesses the camera through this IP:port)
    # NOTE: the camera-side MJPG server allows only ONE simultaneous connection
    # (source-side mutex) — CarPC must open exactly one upstream connection and fan it out
    # to many clients (multiple end devices) viewing simultaneously.
    reconnect_interval_sec: float = Field(default=3.0, gt=0)
    # Wait time before retrying the upstream connection after a disconnect/error
    connect_timeout_sec: float = Field(default=5.0, gt=0)
    # Timeout for establishing the TCP connection to the camera (seconds)
    read_timeout_sec: float = Field(default=10.0, gt=0)
    # Timeout for reading data between two consecutive chunks from the camera (seconds)
    chunk_size: int = Field(default=4096, gt=0)
    # Size of each chunk read from upstream and fanned out to clients (bytes)
    subscriber_queue_size: int = Field(default=64, gt=0)
    # Maximum number of buffered chunks for each slow client before old frames are dropped
    startup_wait_sec: float = Field(default=5.0, gt=0)
    # Maximum time to wait for detecting the real Content-Type/boundary from upstream
    # before returning the response to the client (ensures the correct MJPEG boundary header)
    fps_log_interval_sec: float = Field(default=5.0, gt=0)
    # Interval (seconds) for logging the actual upstream camera FPS (estimated from the
    # JPEG EOI 0xFFD9 marker) — used for monitoring/diagnostics and does not affect relaying.


class StatusMonitorConfig(BaseModel):
    """Configuration for status monitoring of COM_Status_* signals."""

    enabled: bool = False
    # Enable/disable the status monitor.
    interval_sec: float = Field(default=10.0, gt=0)
    # Periodic ping interval (seconds).
    ping_timeout_sec: float = Field(default=1.5, gt=0)
    # Maximum timeout for each ping command (seconds).
    targets: dict[str, str] = Field(default_factory=dict)
    # Map signal_name -> target.
    # - Ethernet signal: target is the host/IP/URL to ping.
    # - CAN signal: target is the reference signal name used to check freshness.


class OMSConfig(BaseModel):
    """Configuration for optional weight-derived OMS occupant classification."""

    bypass_simi_input: bool = False
    # False: keep the classification decoded from CAN/SIMI.
    # True: replace it with a class derived from the mapped mean-weight signal.
    class_config: list[float] = Field(default_factory=lambda: [65.0, 90.0])
    # Class boundaries: weight < low -> 0, low <= weight <= high -> 1, weight > high -> 2.
    target_signal: dict[str, str] = Field(
        default_factory=lambda: {
            "OMS_FR_OccupantClassification": "OMS_FR_OccupantWeightMean",
            "OMS_FL_OccupantClassification": "OMS_FL_OccupantWeightMean",
            "OMS_RL1_OccupantClassification": "OMS_RL1_OccupantWeightMean",
            "OMS_RL2_OccupantClassification": "OMS_RL2_OccupantWeightMean",
            "OMS_RR1_OccupantClassification": "OMS_RR1_OccupantWeightMean",
        }
    )
    # Map output OccupantClassification signal -> source OccupantWeightMean signal.

    @field_validator("class_config")
    @classmethod
    def validate_class_config(cls, thresholds: list[float]) -> list[float]:
        if len(thresholds) != 2:
            raise ValueError("class_config must contain exactly two values")
        low, high = thresholds
        if not all(math.isfinite(value) for value in thresholds):
            raise ValueError("class_config must contain finite values")
        if low < 0 or low >= high:
            raise ValueError("class_config must be non-negative and strictly increasing")
        return thresholds

    @field_validator("target_signal")
    @classmethod
    def validate_target_signal(cls, targets: dict[str, str]) -> dict[str, str]:
        if not targets:
            raise ValueError("target_signal must contain at least one mapping")
        normalized = {
            str(target).strip(): str(source).strip() for target, source in targets.items()
        }
        if any(not target or not source for target, source in normalized.items()):
            raise ValueError("target_signal names must be non-empty")
        return normalized


class DevModeConfig(BaseModel):
    """Dev Mode configuration."""

    block_timeout_sec: float = Field(default=60.0, gt=0)
    require_seat_connected: bool = True
    bypass_check_CAN_status: bool = False
    # Allow Dev Mode to write signals without requiring COM_Status_*Can to be online.


class StorageConfig(BaseModel):
    """Configuration for SQLite historical signal data storage."""

    sqlite_path: str = "data/signals.db"
    # Path to the SQLite file
    batch_size: int = Field(default=100, ge=1)
    # Number of records accumulated before flushing to DB; increase it to reduce write I/O frequency
    batch_interval_sec: float = Field(default=2.0, gt=0)
    # Maximum time between flushes even if the buffer is not full (seconds)
    retention_days: int = Field(default=30, ge=0)
    # Number of days to retain data; older records will be deleted by the retention task
    max_disk_mb: int = Field(default=2048, ge=0)
    # DB size limit (MB); when exceeded, the retention task trims oldest rows and runs VACUUM


class ProcessorConfig(BaseModel):
    """Configuration for the signal processing pipeline."""

    max_update_rate_hz: float = Field(default=10.0, ge=0)
    # Maximum update rate for each signal into SignalStore (Hz); frames beyond this are dropped
    max_queue_size: int = Field(default=10_000, ge=1)
    # Maximum size of the RX queue (number of DecodedFrame objects); increase for high-load bursts
    queue_policy: Literal["drop_oldest", "reject"] = "reject"
    # Behavior when the queue is full:
    #   "drop_oldest" — discard the oldest frame, keep the new one (fresh data, recommended)
    #   "reject"      — discard the newly arrived frame (leave the queue unchanged,
    #                   may lose the latest signal updates)
    batch_drain_size: int = Field(default=200, ge=1)
    # Maximum number of frames drained from the queue in each pipeline loop.
    # The pipeline merges frames with the same signal_id → only the latest value is kept, reducing
    # processing from N → 1 under high load. Increase it if dropped frames still appear in logs.


class WriterConfig(BaseModel):
    """Configuration for the CAN Writer (writing control commands to the bus)."""

    rate_limit_per_sec: int = Field(default=10, ge=1)
    # Maximum number of frames written per second; prevents bus flooding on command bursts
    burst: int = Field(default=5, ge=1)
    # Number of frames allowed to exceed rate_limit in a burst (token bucket burst size)
    periodic_mode: bool = False
    # If True, each write sends continuously at periodic_time_step ms intervals
    # for periodic_duration ms, ignoring rate_limit_per_sec and burst
    periodic_time_step: int = Field(default=20, ge=1)
    # Time between repeated sends (ms) when periodic_mode=True
    periodic_duration: int = Field(default=10000, ge=0)
    # Stop repeated sends after periodic_duration ms from the first send
    use_prevalue_for_unwritten_signal: bool = True
    # How to encode signals in a written CAN message that are not included in the request:
    #   True  — reuse their latest SignalStore value when available (read-modify-write)
    #   False — encode their physical value as 0


class ReaderConfig(BaseModel):
    """Configuration for the CAN reader (CANReader)."""

    frequency_piority: float = Field(default=0.0, ge=0)
    # Time threshold (seconds) for prioritizing low-frequency-changing signals.
    # If > 0, signals that have not been enqueued within this interval will be
    # forced into the queue even if their value is unchanged (heartbeat), bypassing the
    # message-level dedup check — ensuring "rarely changing" signals are not missed.
    # Example: 1.0 = a stable signal is always refreshed into the queue after > 1 s.
    # 0.0 = disable this feature (enqueue only when the value changes).
    only_send_signal_update: bool = False
    # Controls the WS signal payload:
    #   False = send the full set of subscribed signals (latest snapshot)
    #   True  = send only signals that changed in the current batch
    stale_threshold_sec: float = Field(default=30.0, ge=0)
    # Maximum age threshold (seconds) for the most recent CAN frame.
    # If this threshold is exceeded, health/readiness treats the reader as stale
    # and CANReader closes/reconnects the silent bus.


class ShutdownConfig(BaseModel):
    """Configuration for the application shutdown sequence."""

    timeout_sec: int = Field(default=10, ge=1)
    # Maximum time (seconds) to wait for async tasks to finish cleanly before forcing cancellation


class SupervisorConfig(BaseModel):
    """Configuration for the system health watchdog."""

    watchdog_interval_sec: int = Field(default=5, ge=0)
    # Watchdog interval for checking task status (seconds); logs a warning if a task dies


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Minimum log level to record; use "DEBUG" for detailed trace output during debugging
    file_path: str = "logs/can-hmi.log"
    # Path to the log file; the directory will be created automatically if missing
    max_size_mb: int = Field(default=50, ge=1)
    # Maximum size per log file (MB) before rotating to a new file
    backup_count: int = Field(default=5, ge=0)
    # Number of old log files retained after rotation (can-hmi.log.1 … can-hmi.log.N)


class AdaptiveRestraintConfig(BaseModel):
    db_path: str = "db/adaptive_restraint_db/synthetic_data_out_gui.db"
    csv_path: str = "db/adaptive_restraint_db/synthetic_data_out_gui.csv"


class ProfilesConfig(BaseModel):
    profiles_path: str = "config/profiles.json"
    sessions_path: str = "data/profile_sessions.json"
    default_profile_permission: list[str] = Field(default_factory=lambda: ["read"])
    session_online_ttl_seconds: int = Field(default=600, ge=1)
    session_history_limit: int = Field(default=50, ge=1)
    session_cleanup_interval_sec: float = Field(default=5.0, gt=0)


class ConfigManagementConfig(BaseModel):
    backup_retention_count: int = Field(default=20, ge=1, le=200)


class AppConfig(BaseModel):
    """Overall CAN-HMI application configuration — aggregates all configuration groups."""

    can: list[CANConfig] = Field(default_factory=lambda: [CANConfig()])
    # List of CAN channels; each item is an independent bus (vcan0, vcan1, can0, …)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    # Built-in CAN simulator configuration
    api: APIConfig = Field(default_factory=APIConfig)
    # REST API / WebSocket configuration
    adaptive_restraint: AdaptiveRestraintConfig = Field(default_factory=AdaptiveRestraintConfig)
    profiles: ProfilesConfig = Field(default_factory=ProfilesConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    # Camera stream proxy (MJPEG) configuration
    status_monitor: StatusMonitorConfig = Field(default_factory=StatusMonitorConfig)
    # COM status monitor configuration (Ethernet + CAN reference)
    oms_config: OMSConfig = Field(default_factory=OMSConfig)
    # Optional frontend-facing OMS classification derived from CAN occupant weight
    devmode: DevModeConfig = Field(default_factory=DevModeConfig)
    # Seat selection and signal writing configuration for Dev Mode
    storage: StorageConfig = Field(default_factory=StorageConfig)
    # Historical data storage configuration
    processor: ProcessorConfig = Field(default_factory=ProcessorConfig)
    # Signal processing pipeline configuration
    reader: ReaderConfig = Field(default_factory=ReaderConfig)
    # CAN reader configuration
    writer: WriterConfig = Field(default_factory=WriterConfig)
    # CAN Writer configuration
    shutdown: ShutdownConfig = Field(default_factory=ShutdownConfig)
    # Application shutdown configuration
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    # Watchdog configuration
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    # Logging configuration
    config_management: ConfigManagementConfig = Field(default_factory=ConfigManagementConfig)

    @field_validator("can")
    @classmethod
    def validate_can(cls, v: list[CANConfig]) -> list[CANConfig]:
        if not v:
            raise ValueError("At least one CAN channel must be configured in 'can'")
        auto_channels = [entry for entry in v if entry.channel == "auto"]
        if auto_channels:
            if len(v) != 1:
                raise ValueError("CAN channel 'auto' can only be used in single-channel mode")
            if auto_channels[0].interface != "socketcan":
                raise ValueError("CAN channel 'auto' requires interface 'socketcan'")
        # Check for duplicate channel names
        seen: set[str] = set()
        for entry in v:
            if entry.channel in seen:
                raise ValueError(f"Duplicate CAN channel name: '{entry.channel}'")
            seen.add(entry.channel)
        return v


def load_config(path: str | Path) -> AppConfig:
    """Load and validate AppConfig from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig.model_validate(data or {})


_PLACEHOLDER_API_KEYS = {"change-me-in-production", "changeme", "default"}


def apply_environment_overrides(
    config: AppConfig,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Apply deployment-only overrides without storing secrets in JSON.

    Render supplies the public HTTP port through ``PORT``. The API key is kept
    outside the repository in ``CAR_HMI_API_KEY``. Setting
    ``CAR_HMI_REQUIRE_API_KEY=true`` makes startup fail instead of accidentally
    exposing protected routes with placeholder authentication.
    """
    env = os.environ if environ is None else environ

    raw_port = env.get("PORT")
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("PORT must be an integer between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be an integer between 1 and 65535")
        config.api.port = port

    raw_api_key = env.get("CAR_HMI_API_KEY")
    api_key = raw_api_key.strip() if raw_api_key is not None else ""
    if api_key:
        config.api.api_key = api_key

    require_api_key = env.get("CAR_HMI_REQUIRE_API_KEY", "").strip().lower()
    if require_api_key in {"1", "true", "yes", "on"}:
        effective_key = config.api.api_key.strip().lower()
        if not effective_key or effective_key in _PLACEHOLDER_API_KEYS:
            raise ValueError(
                "CAR_HMI_API_KEY must be set to a non-placeholder value when "
                "CAR_HMI_REQUIRE_API_KEY=true"
            )

    return config
