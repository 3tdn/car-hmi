"""Bộ điều phối ứng dụng — khởi tạo và phối hợp tất cả các thành phần."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from src.core.config import AppConfig, load_config
from src.core.signal_store import SignalStore

logger = logging.getLogger(__name__)


def _setup_logging(cfg: AppConfig) -> None:
    log_cfg = cfg.logging
    level = getattr(logging, log_cfg.level, logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_path = Path(log_cfg.file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=log_cfg.max_size_mb * 1024 * 1024,
        backupCount=log_cfg.backup_count,
        encoding="utf-8",
    )
    handlers.append(file_handler)

    logging.basicConfig(level=level, format=fmt, handlers=handlers)


class AppRunner:
    """Bộ điều phối: khởi tạo và chạy tất cả các thành phần của hệ thống.

    Thứ tự khởi động
    ------------------
    1. Ghi log
    2. Tải cơ sở dữ liệu CAN  (quét DBC / CANdb)
    3. Lưu trữ             (khởi tạo schema SQLite)
    4. Bus CAN              (mở giao diện)
    5. CAN Reader           (giải mã → hàng đợi)
    6. CAN Writer           (mã hóa → bus)
    7. Signal Pipeline      (lọc → cảnh báo → store → DB)
    8. CAN Simulator        (tùy chọn, chế độ dev)
    9. FastAPI server       (REST + WebSocket)
    10. Watchdog            (giám sát sức khỏe)
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = SignalStore()
        self._shutting_down = False
        self._tasks: list[asyncio.Task] = []
        # Tham chiếu đến các thành phần (khởi tạo trong start())
        self._pipeline = None
        self._reader = None
        self._writer = None
        self._simulator = None
        self._simulator_bus = None
        self._db_conn = None
        self._repo = None
        self._bus = None
        self._bus_factory = None
        self._fastapi_server = None
        self._ws_manager = None

    async def start(self) -> None:
        """Khởi động tất cả thành phần và chặn chửd cho đến khi tắt."""
        _setup_logging(self.config)
        logger.info("CAN-HMI starting up (config validated ✓)")

        loop = asyncio.get_running_loop()
        # Đăng ký xử lý tín hiệu. Trên một số nền tảng (nhất là Windows) vòng lặp sự kiện
        # không hỗ trợ add_signal_handler; khi đó dùng signal.signal() đồng bộ và lập lịch
        # coroutine an toàn trên luồng event loop.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown()))
            except NotImplementedError:

                def _sync_handler(_signum, _frame):
                    # Lập lịch tắt trong luồng event loop
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(self.shutdown()))

                signal.signal(sig, _sync_handler)

        try:
            await self._init_components(loop)
            logger.info(
                "System running — API on http://%s:%d",
                self.config.api.host,
                self.config.api.port,
            )
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            # Phát hiện lỗi nhiệm vụ nghiêm trọng và kích hoạt tắt
            for task, result in zip(self._tasks, results, strict=True):
                if isinstance(result, Exception):
                    logger.error("Task '%s' failed: %s", task.get_name(), result)
            if any(isinstance(r, Exception) for r in results):
                await self.shutdown()
        except Exception as exc:
            logger.critical("Fatal startup error: %s", exc, exc_info=True)
            await self.shutdown()
            raise

    async def _init_components(self, loop: asyncio.AbstractEventLoop) -> None:
        from src.can_io.bus_factory import create_bus
        from src.can_io.parser import DatabaseLoader
        from src.can_io.reader import CANReader
        from src.can_io.writer import CANWriter
        from src.processor.alarms import AlarmChecker
        from src.processor.computed import ComputedSignals
        from src.processor.filters import RateLimiter, SmoothingFilter
        from src.processor.pipeline import SignalPipeline
        from src.storage.database import init_db
        from src.storage.repository import SQLiteRepository

        can_cfg = self.config.can
        proc_cfg = self.config.processor
        store_cfg = self.config.storage
        sim_cfg = self.config.simulator

        # 1. CAN DB ─────────────────────────────────────────────────────────────
        db_loader = DatabaseLoader(format_hint=can_cfg.can_db_format)
        db_loader.add_paths(can_cfg.can_db_files)
        db_loader.add_paths(can_cfg.can_db_dirs)
        db_loader.add_paths(can_cfg.a2l_dirs)
        logger.info(db_loader.summary())

        # Khởi tạo SignalStore với tất cả tín hiệu đã biết (giá trị ban đầu + đơn vị)
        try:
            import time

            initial_values: dict[str, float] = {}
            units: dict[str, str] = {}
            for name, sig in db_loader.signals.items():
                units[name] = sig.unit or None
                if sig.minimum is not None:
                    initial_values[name] = float(sig.minimum)
                elif sig.maximum is not None:
                    initial_values[name] = float(sig.maximum)
                else:
                    initial_values[name] = 0.0
            # Cập nhật hàng loạt để frontend biết tất cả tín hiệu có sẵn
            await self.store.bulk_update(initial_values, timestamp=time.time(), units=units)
            logger.info("Seeded SignalStore with %d signals", len(initial_values))
        except Exception:
            logger.exception("Failed to seed SignalStore with DB signals")

        # 2. Storage ────────────────────────────────────────────────────────────
        db_path = Path(store_cfg.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_conn = await init_db(str(db_path))
        self._repo = SQLiteRepository(self._db_conn)

        # 3. CAN Bus ────────────────────────────────────────────────────────────
        def _bus_factory():
            return create_bus(can_cfg)

        # Lưu factory để dùng sau (v.d. bus simulator) — có thể tạo thêm
        # instance bus với cùng cấu hình.
        self._bus_factory = _bus_factory
        self._bus = _bus_factory()

        # 4. Signal Pipeline ────────────────────────────────────────────────────
        rx_queue: asyncio.Queue = asyncio.Queue(maxsize=proc_cfg.max_queue_size)
        # Keep a reference for watchdog/metrics reporting
        self._rx_queue = rx_queue

        self._pipeline = SignalPipeline(
            input_queue=rx_queue,
            signal_store=self.store,
            repository=self._repo,
            queue_policy=proc_cfg.queue_policy,
            batch_size=store_cfg.batch_size,
            batch_interval_sec=store_cfg.batch_interval_sec,
        )
        self._pipeline.add_stage(SmoothingFilter(window=proc_cfg.smoothing_window))
        self._pipeline.add_stage(RateLimiter(max_hz=proc_cfg.max_update_rate_hz))
        self._pipeline.add_stage(ComputedSignals())

        # Kiểm tra cảnh báo
        alarm_configs = self._load_alarm_configs()
        if alarm_configs:
            checker = AlarmChecker(alarm_configs)
            checker.add_alarm_handler(self._on_alarm)
            self._pipeline.add_stage(checker)

        # 5. CAN Reader ─────────────────────────────────────────────────────────
        self._reader = CANReader(
            bus=self._bus,
            db=db_loader,
            queue=rx_queue,
            bus_factory=_bus_factory,
            queue_policy=proc_cfg.queue_policy,
        )

        # 6. Trình ghi CAN ─────────────────────────────────────────────────────────
        self._writer = CANWriter(bus=self._bus, db=db_loader)

        # 7. CAN Simulator (tùy chọn) ───────────────────────────────────────────
        if sim_cfg.enabled:
            self._simulator = await self._build_simulator(db_loader, sim_cfg)

        # 8. Máy chủ FastAPI ─────────────────────────────────────────────────────
        self._fastapi_server = await self._build_api_server()

        # Lên lịch tất cả tác vụ async
        self._tasks = [
            asyncio.create_task(self._pipeline.start(), name="pipeline"),
            asyncio.create_task(self._reader.start(), name="can-reader"),
        ]
        if self._simulator:
            self._tasks.append(asyncio.create_task(self._simulator.start(), name="simulator"))
        if self._fastapi_server:
            self._tasks.append(asyncio.create_task(self._fastapi_server(), name="api"))
        if self.config.supervisor.watchdog_interval_sec > 0:
            self._tasks.append(asyncio.create_task(self._watchdog(), name="watchdog"))
        # Push system metrics qua WS cho subscriber
        self._tasks.append(asyncio.create_task(self._metrics_broadcaster(), name="metrics-push"))
        # Dọn dẹp dữ liệu cũ theo retention_days
        self._tasks.append(asyncio.create_task(self._retention_cleanup(), name="retention"))


    def _load_alarm_configs(self) -> list:
        """Tải cấu hình ngưỡng cảnh báo từ config/alarms.yaml."""
        import yaml

        from src.processor.alarms import AlarmConfig

        alarm_path = Path("config/alarms.yaml")
        if not alarm_path.exists():
            logger.warning("config/alarms.yaml not found — no alarm thresholds loaded")
            return []
        raw = yaml.safe_load(alarm_path.read_text(encoding="utf-8"))
        configs = []
        for signal_name, entry in raw.get("alarms", {}).items():
            configs.append(
                AlarmConfig(
                    signal=signal_name,
                    critical_high=entry.get("critical_high"),
                    warning_high=entry.get("warning_high"),
                    warning_low=entry.get("warning_low"),
                    critical_low=entry.get("critical_low"),
                )
            )
        logger.info("Loaded %d alarm threshold configs", len(configs))
        return configs

    async def _on_alarm(self, alarm) -> None:
        """Lưu cảnh báo vào storage và đẩy qua WebSocket."""
        import time

        from src.storage.repository import AlarmRecord

        try:
            alarm_id = await self._repo.insert_alarm(
                AlarmRecord(
                    id=None,
                    signal_name=alarm.signal,
                    level=alarm.level,
                    value=alarm.value,
                    threshold=alarm.threshold,
                    description=alarm.description,
                    triggered_at=alarm.timestamp or time.time(),
                )
            )
            logger.warning(
                "ALARM [%s] %s = %s (threshold %s)",
                alarm.level,
                alarm.signal,
                alarm.value,
                alarm.threshold,
            )
            if self._ws_manager:
                await self._ws_manager.broadcast_alarm(
                    {
                        "id": alarm_id,
                        "signal_name": alarm.signal,
                        "level": alarm.level,
                        "value": alarm.value,
                        "threshold": alarm.threshold,
                        "description": alarm.description,
                        "triggered_at": alarm.timestamp,
                    }
                )
        except Exception as exc:
            logger.error("Failed to store/broadcast alarm: %s", exc)

    async def _build_simulator(self, db_loader, sim_cfg) -> object | None:
        """Xây dựng CANSimulator nếu cấu hình simulator tồn tại."""
        from src.can_simulator.scenario_loader import ScenarioLoader
        from src.can_simulator.simulator import CANSimulator, RandomCANSimulator

        # Ưu tiên dùng cấu hình riêng của simulator nếu có
        sim_conf_path = Path("src/can_simulator/config.json")

        # Dùng bus ảo riêng cho simulator để reader có thể nhận frame
        # (bus ảo python-can gửi giữa các instance khác nhau cùng channel).
        sim_bus = self._bus_factory()
        self._simulator_bus = sim_bus

        if sim_conf_path.exists():
            try:
                import json

                conf = json.loads(sim_conf_path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("Failed to read simulator config %s", sim_conf_path)
                conf = {}

            mode = conf.get("mode", "random")
            if mode == "random":
                # Tùy chọn cho phép simulator tải thêm đường dẫn DBC
                extra = conf.get("dbc_paths") or []
                if extra:
                    db_loader.add_paths(extra)

                update_hz = float(conf.get("update_hz", 1.0))
                max_delta_pct = float(conf.get("max_delta_percent", 10.0))
                min_val = conf.get("min_value")
                max_val = conf.get("max_value")
                cycle_ms = sim_cfg.default_cycle_ms

                return RandomCANSimulator(
                    bus=sim_bus,
                    db=db_loader,
                    default_cycle_ms=cycle_ms,
                    update_hz=update_hz,
                    min_value=min_val,
                    max_value=max_val,
                    max_delta_percent=max_delta_pct,
                    loop=True,
                )

            # fallback sang chế độ scenario nếu được yêu cầu
            if mode == "scenario":
                scenario_path = Path("scenarios/city_drive.yaml")
                if not scenario_path.exists():
                    logger.info("No scenario file found — simulator will not run")
                    return None
                loader = ScenarioLoader()
                scenario = loader.load(scenario_path)
                return CANSimulator(
                    bus=sim_bus,
                    db=db_loader,
                    scenario=scenario,
                    default_cycle_ms=sim_cfg.default_cycle_ms,
                    loop=True,
                )

            logger.warning(
                "Unknown simulator mode '%s' in %s — simulator disabled", mode, sim_conf_path
            )
            return None

        # Hành vi mặc định: kiểm tra file scenario
        scenario_path = Path("scenarios/city_drive.yaml")
        if not scenario_path.exists():
            logger.info("No scenario file found — simulator will not run")
            return None
        loader = ScenarioLoader()
        scenario = loader.load(scenario_path)
        return CANSimulator(
            bus=sim_bus,
            db=db_loader,
            scenario=scenario,
            default_cycle_ms=sim_cfg.default_cycle_ms,
            loop=True,
        )

    async def _build_api_server(self):
        """Xây dựng FastAPI + coroutine chạy uvicorn."""
        try:
            import uvicorn

            from src.api.app import create_app
        except ImportError:
            logger.warning("fastapi/uvicorn not installed — API server disabled")
            return None

        api_cfg = self.config.api
        app = create_app(
            signal_store=self.store,
            repository=self._repo,
            can_reader=self._reader,
            api_key=api_cfg.api_key,
            cors_origins=api_cfg.cors_origins,
        )
        # Expose runtime objects so config endpoints can attempt to apply changes
        app.state.pipeline = self._pipeline
        app.state.runner = self
        app.state.writer = self._writer
        app.state.rx_queue = self._rx_queue
        self._ws_manager = app.state.ws_manager

        # Đăng ký SignalStore để phát sóng qua WebSocket
        async def _broadcast_signal(name: str, sv) -> None:
            await self._ws_manager.broadcast_signal(name, sv.value, sv.timestamp)

        self.store.subscribe(_broadcast_signal)

        server_config = uvicorn.Config(
            app,
            host=api_cfg.host,
            port=api_cfg.port,
            log_level=self.config.logging.level.lower(),
            access_log=False,
        )
        server = uvicorn.Server(server_config)

        async def _serve_safe() -> None:
            """Bọ server.serve vào try/except để chuyển SystemExit thành ngoại lệ thông thường."""
            try:
                await server.serve()
            except SystemExit as exc:
                raise RuntimeError(f"API server exited with code {exc.code}") from exc

        return _serve_safe

    async def migrate_rx_queue(self, new_maxsize: int, timeout: float = 5.0) -> dict:
        """Migrate the runtime RX queue to a new maxsize.

        Best-effort: switches the CAN reader to the new queue, then drains
        the old queue into the new one for up to `timeout` seconds.
        Returns a summary dict with `migrated` count.
        """
        import asyncio
        import time

        old_q = getattr(self, "_rx_queue", None)
        if old_q is None:
            return {"ok": False, "reason": "no_rx_queue"}

        # If already same size, nothing to do
        try:
            old_size = int(old_q.maxsize)
        except Exception:
            old_size = None
        if old_size == int(new_maxsize):
            return {"ok": True, "migrated": 0, "new_maxsize": new_maxsize}

        new_q: asyncio.Queue = asyncio.Queue(maxsize=int(new_maxsize))

        # Route new incoming frames to new queue first
        if self._reader and hasattr(self._reader, "set_queue"):
            try:
                self._reader.set_queue(new_q)
            except Exception:
                pass

        migrated = 0
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            try:
                item = old_q.get_nowait()
            except Exception:
                break
            try:
                new_q.put_nowait(item)
                migrated += 1
            except asyncio.QueueFull:
                # cannot accept more items; stop migrating
                break

        # Finally, tell the pipeline to use the new queue
        if self._pipeline and hasattr(self._pipeline, "set_input_queue"):
            try:
                self._pipeline.set_input_queue(new_q)
            except Exception:
                pass

        # Keep reference for metrics/watchdog
        self._rx_queue = new_q
        return {"ok": True, "migrated": migrated, "new_maxsize": new_maxsize}

    async def _metrics_broadcaster(self) -> None:
        """Broadcast system metrics qua WS tới subscriber đã đăng ký channel 'metrics'."""
        from src.core.system_metrics import collect_system_metrics, metrics_to_dict

        interval = 3  # seconds — consistent with frontend polling interval
        while not self._shutting_down:
            await asyncio.sleep(interval)
            if self._ws_manager is None:
                continue
            try:
                rx_queue = getattr(self, "_rx_queue", None)
                start_time = getattr(getattr(self._ws_manager, "", None), "", None)
                # Use app.state.start_time if available
                m = collect_system_metrics(rx_queue=rx_queue, start_time=None)
                await self._ws_manager.broadcast_metrics(metrics_to_dict(m))
            except Exception:
                logger.debug("Failed to broadcast metrics", exc_info=True)

    async def _retention_cleanup(self) -> None:
        """Xóa bản ghi signal_log cũ hơn retention_days mỗi 1 giờ."""
        import time as _time_mod

        retention_sec = self.config.storage.retention_days * 86400
        while not self._shutting_down:
            await asyncio.sleep(3600)
            if self._shutting_down:
                break
            try:
                cutoff = _time_mod.time() - retention_sec
                deleted = await self._repo.delete_old_signals(cutoff)
                if deleted:
                    logger.info("Retention cleanup: deleted %d old signal records", deleted)
            except Exception:
                logger.exception("Retention cleanup failed")

    async def _watchdog(self) -> None:
        """Kiểm tra sức khỏe định kỳ — ghi log trạng thái và có thể khởi động lại các thành phần."""
        interval = self.config.supervisor.watchdog_interval_sec
        while not self._shutting_down:
            await asyncio.sleep(interval)
            alive = [t.get_name() for t in self._tasks if not t.done()]
            done = [t.get_name() for t in self._tasks if t.done()]
            if done:
                logger.warning("Tasks finished unexpectedly: %s", done)
            # Report reader/pipeline metrics if available
            try:
                rx_q_size = self._rx_queue.qsize() if hasattr(self, "_rx_queue") else None
            except Exception:
                rx_q_size = None

            reader_metrics = None
            if self._reader:
                try:
                    reader_metrics = self._reader.get_metrics()
                except Exception:
                    reader_metrics = None

            logger.info("Watchdog — alive tasks: %s | rx_queue_size=%s | reader_metrics=%s", alive, rx_q_size, reader_metrics)

    async def shutdown(self) -> None:
        """Tắt đúng cách: flush pipeline, dừng reader, đóng DB."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutting down (timeout=%ds)...", self.config.shutdown.timeout_sec)

        if self._reader:
            self._reader.stop()
        if self._simulator:
            self._simulator.stop()
        if self._pipeline:
            self._pipeline.stop()
            try:
                await asyncio.wait_for(self._pipeline.flush(), timeout=5.0)
            except TimeoutError:
                logger.warning("Pipeline flush timed out")

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass

        if self._simulator_bus:
            try:
                self._simulator_bus.shutdown()
            except Exception:
                pass

        if self._db_conn:
            await self._db_conn.close()

        logger.info("Shutdown complete.")

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down


# ── CLI entry-point ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="can-hmi",
        description="CAN-HMI Signal Processing & API Server",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config/bus.yaml",
        help="Path to configuration YAML file (default: config/bus.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.log_level:
        cfg.logging.level = args.log_level

    runner = AppRunner(cfg)
    try:
        asyncio.run(runner.start())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
