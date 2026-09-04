"""Global configuration loader using Pydantic BaseSettings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CANConfig(BaseModel):
    """Configuration for a CAN bus channel."""

    interface: str = "virtual"
    # python-can driver name: "socketcan", "virtual", "kvaser", "pcan", …
    channel: str = "vcan0"
    # OS channel name (vcan0, can0) or device name depending on the interface
    bitrate: int = 500_000
    # Bus bitrate in bit/s (500_000 = classic CAN, 2_000_000 = nominal CAN FD)
    can_json_path: str = "config/can.json"
    # JSON file describing messages/signals for this channel (exported from DBC by gen_can_json.py)
    can_db_files: list[str] = Field(default_factory=list)
    # List of DBC / A2L file paths to load additionally (supplementing can_json_path)
    can_db_dirs: list[str] = Field(default_factory=list)
    # Directory containing DBC/A2L files; all valid files in the directory will be loaded
    a2l_dirs: list[str] = Field(default_factory=list)
    # Directory containing A2L (ASAP2) files used to load ECU signal definitions
    can_db_format: Literal["auto", "dbc", "a2l"] = "auto"
    # DB format: "auto" = detect from file extension, "dbc" or "a2l" = force a specific format


class SimulatorConfig(BaseModel):
    """Configuration for the built-in CAN simulator (used in dev/test environments)."""

    enabled: bool = True
    # Enable/disable the simulator; it should be disabled (false) on a real vehicle
    random_mode: bool = False
    # If True, the simulator transmits random signal values
    # If False, the simulator transmits values incremented by 1 unit (or 1 state)
    default_cycle_ms: int = 50
    # Transmit period per message in ms; reducing it increases bus load
    can_json_path: str = "config/can.json"
    # JSON file containing the messages the simulator will transmit (typically the combined can.json)


class APIConfig(BaseModel):
    """Configuration for the REST API and WebSocket server (FastAPI / Uvicorn)."""

    host: str = "0.0.0.0"
    # Bind address; "0.0.0.0" = listen on all interfaces, "127.0.0.1" = local only
    port: int = 8000
    # HTTP port; change it if there is a conflict or if running behind a reverse proxy
    api_key: str = "change-me-in-production"
    # Bearer token used for API authentication; MUST be changed before deploying to production
    ws_heartbeat_interval_sec: float = 5.0
    # Interval for sending keepalive pings to WebSocket clients (seconds)
    ws_metrics_interval_sec: float = 3.0
    # Interval for sending system metrics snapshots over WebSocket (seconds)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"])
    # List of origins allowed by CORS; add frontend URLs if served from another domain


class CameraConfig(BaseModel):
    """Configuration for the camera stream proxy (MJPEG)."""

    enabled: bool = False
    # Enable/disable the camera stream route
    stream_url: str = "http://192.168.2.119:8080/stream"
    # Source MJPEG stream URL (CarPC accesses the camera through this IP:port)
    # NOTE: the camera-side MJPG server allows only ONE simultaneous connection
    # (source-side mutex) — CarPC must open exactly one upstream connection and fan it out
    # to many clients (multiple end devices) viewing simultaneously.
    reconnect_interval_sec: float = 3.0
    # Wait time before retrying the upstream connection after a disconnect/error
    connect_timeout_sec: float = 5.0
    # Timeout for establishing the TCP connection to the camera (seconds)
    read_timeout_sec: float = 10.0
    # Timeout for reading data between two consecutive chunks from the camera (seconds)
    chunk_size: int = 4096
    # Size of each chunk read from upstream and fanned out to clients (bytes)
    subscriber_queue_size: int = 64
    # Maximum number of buffered chunks for each slow client before old frames are dropped
    startup_wait_sec: float = 5.0
    # Maximum time to wait for detecting the real Content-Type/boundary from upstream
    # before returning the response to the client (ensures the correct MJPEG boundary header)
    fps_log_interval_sec: float = 5.0
    # Interval (seconds) for logging the actual upstream camera FPS (estimated from the
    # JPEG EOI 0xFFD9 marker) — used for monitoring/diagnostics and does not affect relaying.


class StatusMonitorConfig(BaseModel):
    """Configuration for status monitoring of COM_Status_* signals."""

    enabled: bool = False
    # Enable/disable the status monitor.
    interval_sec: float = 10.0
    # Periodic ping interval (seconds).
    ping_timeout_sec: float = 1.5
    # Maximum timeout for each ping command (seconds).
    targets: dict[str, str] = Field(default_factory=dict)
    # Map signal_name -> target.
    # - Ethernet signal: target is the host/IP/URL to ping.
    # - CAN signal: target is the reference signal name used to check freshness.


class DevModeConfig(BaseModel):
    """Dev Mode configuration."""

    block_timeout_sec: float = 60.0
    require_seat_connected: bool = True
    pypass_check_CAN_status: bool = False
    # Allow Dev Mode to write signals without requiring COM_Status_*Can to be online.


class StorageConfig(BaseModel):
    """Configuration for historical signal data storage."""

    engine: Literal["sqlite", "timescaledb", "influxdb"] = "sqlite"
    # Storage backend: "sqlite" for dev/embedded, "timescaledb"/"influxdb" for production
    sqlite_path: str = "data/signals.db"
    # Path to the SQLite file (used only when engine="sqlite")
    batch_size: int = 100
    # Number of records accumulated before flushing to DB; increase it to reduce write I/O frequency
    batch_interval_sec: float = 2.0
    # Maximum time between flushes even if the buffer is not full (seconds)
    retention_days: int = 30
    # Number of days to retain data; older records will be deleted by the retention task
    max_disk_mb: int = 2048
    # DB size limit (MB); when exceeded, the retention task trims oldest rows and runs VACUUM


class ProcessorConfig(BaseModel):
    """Configuration for the signal processing pipeline."""

    smoothing_window: int = 5
    # Smoothing window size (SmoothingFilter): number of samples used for the moving average
    max_update_rate_hz: float = 10.0
    # Maximum update rate for each signal into SignalStore (Hz); frames beyond this are dropped
    max_queue_size: int = 10_000
    # Maximum size of the RX queue (number of DecodedFrame objects); increase it for high-load bursts
    queue_policy: Literal["drop_oldest", "reject"] = "reject"
    # Behavior when the queue is full:
    #   "drop_oldest" — discard the oldest frame and accept the new frame (prefer fresh data, recommended)
    #   "reject"      — discard the newly arrived frame (leave the queue unchanged, may lose the latest signal updates)
    batch_drain_size: int = 200
    # Maximum number of frames drained from the queue in each pipeline loop.
    # The pipeline merges frames with the same signal_id → only the latest value is kept, reducing
    # processing from N → 1 under high load. Increase it if dropped frames still appear in logs.


class WriterConfig(BaseModel):
    """Configuration for the CAN Writer (writing control commands to the bus)."""

    rate_limit_per_sec: int = 10
    # Maximum number of frames written per second; prevents bus flooding when many commands arrive at once
    burst: int = 5
    # Number of frames allowed to exceed rate_limit in a burst (token bucket burst size)
    periodic_mode: bool = False
    # If True, each write sends continuously at periodic_time_step ms intervals
    # for periodic_duration ms, ignoring rate_limit_per_sec and burst
    periodic_time_step: int = 20
    # Time between repeated sends (ms) when periodic_mode=True
    periodic_duration: int = 10000
    # Stop repeated sends after periodic_duration ms from the first send


class ReaderConfig(BaseModel):
    """Configuration for the CAN reader (CANReader)."""

    frequency_piority: float = 0.0
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
    stale_threshold_sec: float = 30.0
    # Maximum age threshold (seconds) for the most recent CAN frame.
    # If this threshold is exceeded, health/readiness will treat the reader as stale.


class ShutdownConfig(BaseModel):
    """Configuration for the application shutdown sequence."""

    timeout_sec: int = 10
    # Maximum time (seconds) to wait for async tasks to finish cleanly before forcing cancellation


class SupervisorConfig(BaseModel):
    """Configuration for the system health watchdog."""

    watchdog_interval_sec: int = 5
    # Watchdog interval for checking task status (seconds); logs a warning if a task dies


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Minimum log level to record; use "DEBUG" for detailed trace output during debugging
    file_path: str = "logs/can-hmi.log"
    # Path to the log file; the directory will be created automatically if missing
    max_size_mb: int = 50
    # Maximum size per log file (MB) before rotating to a new file
    backup_count: int = 5
    # Number of old log files retained after rotation (can-hmi.log.1 … can-hmi.log.N)


class AppConfig(BaseModel):
    """Overall CAN-HMI application configuration — aggregates all configuration groups."""

    can: list[CANConfig] = Field(default_factory=lambda: [CANConfig()])
    # List of CAN channels; each item is an independent bus (vcan0, vcan1, can0, …)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    # Built-in CAN simulator configuration
    api: APIConfig = Field(default_factory=APIConfig)
    # REST API / WebSocket configuration
    camera: CameraConfig = Field(default_factory=CameraConfig)
    # Camera stream proxy (MJPEG) configuration
    status_monitor: StatusMonitorConfig = Field(default_factory=StatusMonitorConfig)
    # COM status monitor configuration (Ethernet + CAN reference)
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

    @field_validator("can")
    @classmethod
    def validate_can(cls, v: list[CANConfig]) -> list[CANConfig]:
        if not v:
            raise ValueError("At least one CAN channel must be configured in 'can'")
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
