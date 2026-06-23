"""Bộ tải cấu hình toàn cục sử dụng Pydantic BaseSettings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CANConfig(BaseModel):
    """Cấu hình một kênh CAN bus."""

    interface: str = "virtual"
    # Tên driver python-can: "socketcan", "virtual", "kvaser", "pcan", …
    channel: str = "vcan0"
    # Tên kênh OS (vcan0, can0) hoặc tên thiết bị tuỳ theo interface
    bitrate: int = 500_000
    # Tốc độ bus tính bằng bit/s (500_000 = CAN classic, 2_000_000 = CAN FD nominal)
    can_json_path: str = "config/can.json"
    # File JSON mô tả message/signal cho kênh này (xuất từ DBC bằng gen_can_json.py)
    can_db_files: list[str] = Field(default_factory=list)
    # Danh sách đường dẫn tới file DBC / A2L cần nạp thêm (bổ sung cho can_json_path)
    can_db_dirs: list[str] = Field(default_factory=list)
    # Thư mục chứa file DBC/A2L; tất cả file hợp lệ trong thư mục sẽ được nạp
    a2l_dirs: list[str] = Field(default_factory=list)
    # Thư mục chứa file A2L (ASAP2) để nạp định nghĩa tín hiệu ECU
    can_db_format: Literal["auto", "dbc", "a2l"] = "auto"
    # Định dạng DB: "auto" = tự nhận theo đuôi file, "dbc" hoặc "a2l" ép kiểu cụ thể


class SimulatorConfig(BaseModel):
    """Cấu hình CAN simulator nội bộ (dùng trong môi trường dev/test)."""

    enabled: bool = True
    # Bật/tắt simulator; nên tắt (false) trên xe thật
    random_mode: bool = False
    # Nếu True, simulator sẽ phát giá trị tín hiệu random
    # Nếu False, simulator sẽ phát giá trị tăng dần lên 1 đơn vị (hoặc 1 state)
    default_cycle_ms: int = 50
    # Chu kỳ phát mỗi message tính bằng ms; giảm xuống làm tăng tải bus
    can_json_path: str = "config/can.json"
    # File JSON chứa danh sách message simulator sẽ phát (thường dùng can.json tổng hợp)


class APIConfig(BaseModel):
    """Cấu hình REST API và WebSocket server (FastAPI / Uvicorn)."""

    host: str = "0.0.0.0"
    # Địa chỉ bind; "0.0.0.0" = lắng nghe tất cả interface, "127.0.0.1" = chỉ local
    port: int = 8000
    # Cổng HTTP; đổi nếu bị xung đột hoặc cần chạy sau reverse proxy
    api_key: str = "change-me-in-production"
    # Bearer token dùng để xác thực API; PHẢI đổi trước khi deploy lên môi trường thật
    ws_heartbeat_interval_sec: float = 5.0
    # Khoảng thời gian gửi ping keepalive tới WebSocket client (giây)
    ws_metrics_interval_sec: float = 3.0
    # Khoảng thời gian gửi snapshot metrics hệ thống qua WebSocket (giây)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"])
    # Danh sách origin được phép CORS; thêm URL frontend nếu chạy trên domain khác


class StorageConfig(BaseModel):
    """Cấu hình lưu trữ dữ liệu tín hiệu lịch sử."""

    engine: Literal["sqlite", "timescaledb", "influxdb"] = "sqlite"
    # Backend lưu trữ: "sqlite" cho dev/embedded, "timescaledb"/"influxdb" cho production
    sqlite_path: str = "data/signals.db"
    # Đường dẫn file SQLite (chỉ dùng khi engine="sqlite")
    batch_size: int = 100
    # Số bản ghi tích luỹ trước khi flush xuống DB; tăng để giảm số lần write I/O
    batch_interval_sec: float = 2.0
    # Thời gian tối đa giữa hai lần flush dù buffer chưa đầy (giây)
    retention_days: int = 30
    # Số ngày giữ dữ liệu; bản ghi cũ hơn sẽ bị xoá bởi retention task
    max_disk_mb: int = 2048
    # Giới hạn dung lượng DB (MB); khi vượt ngưỡng, retention task sẽ xóa oldest rows và VACUUM


class ProcessorConfig(BaseModel):
    """Cấu hình pipeline xử lý tín hiệu."""

    smoothing_window: int = 5
    # Kích thước cửa sổ làm mượt (SmoothingFilter): số mẫu dùng cho moving average
    max_update_rate_hz: float = 10.0
    # Tần suất cập nhật tối đa mỗi tín hiệu vào SignalStore (Hz); frame vượt quá sẽ bị bỏ qua
    max_queue_size: int = 10_000
    # Kích thước tối đa của RX queue (số DecodedFrame); tăng nếu burst tải cao
    queue_policy: Literal["drop_oldest", "reject"] = "reject"
    # Hành vi khi queue đầy:
    #   "drop_oldest" — bỏ frame cũ nhất, nhận frame mới (ưu tiên data tươi, khuyến nghị)
    #   "reject"      — bỏ frame mới đến (giữ nguyên queue, có thể gây mất signal mới)
    batch_drain_size: int = 200
    # Số frame tối đa được drain khỏi queue trong mỗi lần lặp pipeline.
    # Pipeline merge các frame cùng signal_id → chỉ giữ giá trị mới nhất, giảm số lần
    # xử lý từ N → 1 khi tải cao. Tăng nếu vẫn còn dropped frames trong log.


class WriterConfig(BaseModel):
    """Cấu hình CAN Writer (ghi lệnh điều khiển xuống bus)."""

    rate_limit_per_sec: int = 10
    # Số frame ghi tối đa mỗi giây; ngăn flood bus khi nhiều lệnh đến cùng lúc
    burst: int = 5
    # Số frame được phép ghi liên tiếp vượt rate_limit (token bucket burst size)
    periodic_mode: bool = False
    # Nếu True, mỗi lần write sẽ gửi liên tục theo chu kỳ periodic_time_step ms
    # trong khoảng periodic_duration ms, bỏ qua rate_limit_per_sec và burst
    periodic_time_step: int = 20
    # Thời gian giữa 2 lần gửi liên tục (ms) khi periodic_mode=True
    periodic_duration: int = 10000
    # Dừng gửi liên tục sau periodic_duration ms kể từ lần gửi đầu tiên


class ShutdownConfig(BaseModel):
    """Cấu hình trình tự tắt ứng dụng."""

    timeout_sec: int = 10
    # Thời gian tối đa (giây) chờ các task async kết thúc sạch trước khi buộc cancel


class SupervisorConfig(BaseModel):
    """Cấu hình watchdog giám sát sức khoẻ hệ thống."""

    watchdog_interval_sec: int = 5
    # Chu kỳ watchdog kiểm tra trạng thái các task (giây); log cảnh báo nếu task chết


class LoggingConfig(BaseModel):
    """Cấu hình logging."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Mức log tối thiểu được ghi; dùng "DEBUG" để trace chi tiết khi debug
    file_path: str = "logs/can-hmi.log"
    # Đường dẫn file log; thư mục sẽ được tạo tự động nếu chưa tồn tại
    max_size_mb: int = 50
    # Kích thước tối đa mỗi file log (MB) trước khi rotate sang file mới
    backup_count: int = 5
    # Số file log cũ giữ lại sau khi rotate (can-hmi.log.1 … can-hmi.log.N)


class AppConfig(BaseModel):
    """Cấu hình tổng thể ứng dụng CAN-HMI — tổng hợp tất cả các nhóm cấu hình."""

    can: list[CANConfig] = Field(default_factory=lambda: [CANConfig()])
    # Danh sách kênh CAN; mỗi phần tử là một bus độc lập (vcan0, vcan1, can0, …)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    # Cấu hình CAN simulator nội bộ
    api: APIConfig = Field(default_factory=APIConfig)
    # Cấu hình REST API / WebSocket
    storage: StorageConfig = Field(default_factory=StorageConfig)
    # Cấu hình lưu trữ dữ liệu lịch sử
    processor: ProcessorConfig = Field(default_factory=ProcessorConfig)
    # Cấu hình pipeline xử lý tín hiệu
    writer: WriterConfig = Field(default_factory=WriterConfig)
    # Cấu hình CAN Writer
    shutdown: ShutdownConfig = Field(default_factory=ShutdownConfig)
    # Cấu hình tắt ứng dụng
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    # Cấu hình watchdog
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    # Cấu hình logging

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
    """Tải và xác thực AppConfig từ file JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig.model_validate(data or {})
