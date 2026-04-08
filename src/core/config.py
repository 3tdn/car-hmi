"""Bộ tải cấu hình toàn cục sử dụng Pydantic BaseSettings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CANConfig(BaseModel):
    interface: str = "virtual"
    channel: str = "vcan0"
    bitrate: int = 500000
    can_json_path: str = "config/can.json"


class SimulatorConfig(BaseModel):
    enabled: bool = True
    default_cycle_ms: int = 50
    can_json_path: str = "config/can.json"


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = "change-me-in-production"
    ws_heartbeat_interval_sec: int = 5
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"])


class StorageConfig(BaseModel):
    engine: Literal["sqlite", "timescaledb", "influxdb"] = "sqlite"
    sqlite_path: str = "data/signals.db"
    batch_size: int = 100
    batch_interval_sec: float = 2.0
    retention_days: int = 30
    max_disk_mb: int = 2048


class ProcessorConfig(BaseModel):
    smoothing_window: int = 5
    max_update_rate_hz: float = 10.0
    max_queue_size: int = 10_000
    queue_policy: Literal["drop_oldest", "block", "reject"] = "reject"


class WriterConfig(BaseModel):
    rate_limit_per_sec: int = 10
    burst: int = 5


class ShutdownConfig(BaseModel):
    timeout_sec: int = 10


class SupervisorConfig(BaseModel):
    watchdog_interval_sec: int = 5


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file_path: str = "logs/can-hmi.log"
    max_size_mb: int = 50
    backup_count: int = 5


class AppConfig(BaseModel):
    can: list[CANConfig] = Field(default_factory=lambda: [CANConfig()])
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    processor: ProcessorConfig = Field(default_factory=ProcessorConfig)
    writer: WriterConfig = Field(default_factory=WriterConfig)
    shutdown: ShutdownConfig = Field(default_factory=ShutdownConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("can")
    @classmethod
    def validate_can(cls, v: list[CANConfig]) -> list[CANConfig]:
        if not v:
            raise ValueError("At least one CAN channel must be configured")
        for i, ch in enumerate(v):
            if not ch.can_json_path:
                raise ValueError(f"can[{i}].can_json_path must be set")
        channels = [ch.channel for ch in v]
        if len(channels) != len(set(channels)):
            raise ValueError("Duplicate CAN channel names detected")
        paths = [ch.can_json_path for ch in v]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate can_json_path detected across channels")
        return v


def load_config(path: str | Path) -> AppConfig:
    """Tải và xác thực AppConfig từ file JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig.model_validate(data or {})
