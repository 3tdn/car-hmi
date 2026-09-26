"""Application coordinator — initializes and orchestrates all system components."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import logging.handlers
import signal
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import can

from src.core.config import AppConfig, apply_environment_overrides, load_config
from src.core.signal_store import SignalStore

logger = logging.getLogger(__name__)

_DEFAULT_ETHERNET_TARGETS: dict[str, str] = {
    "COM_Status_PumaFLEthernet": "192.168.1.101",
    "COM_Status_PumaFREthernet": "192.168.1.102",
    "COM_Status_PumaRL1Ethernet": "192.168.1.103",
    "COM_Status_PumaRL2Ethernet": "192.168.1.104",
    "COM_Status_PumaRR1Ethernet": "192.168.1.105",
    "COM_Status_PantherEthernet": "192.168.1.100",
}


def _extract_host(raw_target: str | None) -> str | None:
    if raw_target is None:
        return None

    target = str(raw_target).strip()
    if not target:
        return None

    if "://" in target:
        host = urlparse(target).hostname
        return host.strip() if host else None

    if target.count(":") == 1:
        maybe_host, maybe_port = target.rsplit(":", 1)
        if maybe_port.isdigit():
            return maybe_host.strip() or None

    return target


class _ShutdownNoiseFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.enabled:
            return True

        message = record.getMessage()
        if "ASGI callable returned without completing response" in message:
            return False

        if record.name.startswith(("uvicorn", "starlette")) and record.exc_info:
            exc_type, _, _ = record.exc_info
            try:
                import asyncio

                if issubclass(exc_type, asyncio.CancelledError):
                    return False
            except Exception:
                pass

        return True


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


def _reserve_api_socket(host: str, port: int) -> socket.socket:
    """Bind the API socket before opening CAN and API resources."""
    last_error: OSError | None = None
    for family, socktype, proto, _, address in socket.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        flags=socket.AI_PASSIVE,
    ):
        api_socket = socket.socket(family, socktype, proto)
        try:
            api_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            api_socket.bind(address)
            api_socket.listen(socket.SOMAXCONN)
            api_socket.setblocking(False)
            return api_socket
        except OSError as exc:
            last_error = exc
            api_socket.close()
    if last_error is None:
        raise OSError(f"No bind address resolved for {host}:{port}")
    raise last_error


class AppRunner:
    """Coordinator that initializes and runs all system components.

    Startup order
    -------------
    1. Logging
    2. Load CAN database (scan DBC / CANdb)
    3. Build the in-memory signal metadata catalog
    4. CAN bus (open interface)
    5. CAN Reader (decode → queue)
    6. CAN Writer (encode → bus)
    7. Signal Pipeline (filter → in-memory store)
    8. CAN Simulator (optional, dev mode)
    9. FastAPI server (REST + WebSocket)
    10. Watchdog (system health monitoring)
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._boot_config = config.model_dump(mode="json")
        self.store = SignalStore()
        self._shutting_down = False
        self._tasks: list[asyncio.Task] = []
        # Component references (created in start())
        self._pipeline = None
        self._rate_limiter = None
        self._oms_classifier = None
        self._readers: list = []
        self._writers: list = []
        self._writer_router = None
        self._simulator = None
        self._simulator_bus = None
        self._buses: list = []
        self._bus_factories: list = []
        self._db_loaders: list = []
        self._signal_metadata = None
        self._fastapi_server = None
        self._api_app = None
        self._shutdown_noise_filter = _ShutdownNoiseFilter()
        self._ws_manager = None
        self._start_time: float = 0.0
        self._uvicorn_server = None
        self._api_socket: socket.socket | None = None
        self._ping_unavailable_logged = False
        self._reboot_requested = False
        self._reboot_task: asyncio.Task[None] | None = None
        self.pending_reboot_paths: set[str] = set()

    async def start(self) -> None:
        """Start all components and block until shutdown."""
        _setup_logging(self.config)
        for logger_name in ("uvicorn.error", "starlette", "uvicorn.lifespan.on"):
            logging.getLogger(logger_name).addFilter(self._shutdown_noise_filter)
        logger.info("CAN-HMI starting up (config validated ✓)")
        self._start_time = time.time()

        # Reserve the public endpoint before opening the database or CAN bus.
        # This makes a duplicate instance fail before it can contend for those
        # shared resources and avoids Uvicorn's opaque startup exit code 3.
        try:
            self._api_socket = _reserve_api_socket(
                self.config.api.host,
                self.config.api.port,
            )
        except OSError as exc:
            raise RuntimeError(
                f"API endpoint {self.config.api.host}:{self.config.api.port} is unavailable: "
                f"{exc}. Another CAN-HMI instance or service may already be using this port."
            ) from exc

        loop = asyncio.get_running_loop()
        # Register signal handlers. On some platforms (especially Windows), the event loop
        # does not support add_signal_handler; in that case use synchronous signal.signal() and schedule the
        # coroutine safely onto the event-loop thread.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown()))
            except NotImplementedError:

                def _sync_handler(_signum, _frame):
                    # Schedule shutdown on the event-loop thread
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(self.shutdown()))

                signal.signal(sig, _sync_handler)

        try:
            await self._init_components(loop)
            logger.info(
                "System running — API on http://%s:%d",
                self.config.api.host,
                self.config.api.port,
            )
            # Take a stable snapshot because the watchdog may prune finished tasks
            # from self._tasks while we are waiting here.
            tasks = tuple(self._tasks)
            await self._wait_for_runtime_tasks(tasks)
            if self._reboot_requested:
                logger.info("Car-HMI reboot request completed; exiting for supervisor restart")
                return
        except asyncio.CancelledError:
            if not self._shutting_down:
                try:
                    await asyncio.shield(self.shutdown())
                except asyncio.CancelledError:
                    pass
            return
        except Exception as exc:
            logger.critical("Fatal application error: %s", exc, exc_info=True)
            await self.shutdown()
            raise

    async def _wait_for_runtime_tasks(
        self,
        tasks: tuple[asyncio.Task, ...],
    ) -> None:
        """Fail fast when any long-running runtime task raises or exits."""
        if not tasks:
            raise RuntimeError("No runtime tasks were started")

        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # An intentional shutdown cancels or stops these tasks. Wait for that
        # cleanup to finish without turning the first completed task into a
        # second failure.
        if self._shutting_down or self._reboot_requested:
            await asyncio.gather(*tasks, return_exceptions=True)
            return

        failures: list[str] = []
        primary_exception: BaseException | None = None
        for task in done:
            task_name = task.get_name()
            if task.cancelled():
                detail = f"Task '{task_name}' was cancelled unexpectedly"
            else:
                task_exception = task.exception()
                if task_exception is None:
                    detail = f"Task '{task_name}' exited unexpectedly"
                else:
                    detail = f"Task '{task_name}' failed: {task_exception}"
                    primary_exception = primary_exception or task_exception
            failures.append(detail)
            logger.error(detail)

        failure_summary = "; ".join(failures)
        await self.shutdown()
        if primary_exception is not None:
            raise RuntimeError(failure_summary) from primary_exception
        raise RuntimeError(failure_summary)

    async def _init_components(self, loop: asyncio.AbstractEventLoop) -> None:
        from src.can_io.bus_factory import create_bus, resolve_auto_match_ids
        from src.can_io.parser import DatabaseLoader
        from src.can_io.reader import CANReader
        from src.can_io.writer import CANWriter, CANWriterRouter
        from src.core.signal_metadata import SignalMetadataCatalog
        from src.processor.computed import ComputedSignals, OMSClassificationProcessor
        from src.processor.filters import RateLimiter
        from src.processor.pipeline import SignalPipeline

        can_channels = self.config.can
        proc_cfg = self.config.processor
        sim_cfg = self.config.simulator

        # 1. CAN DB — load each channel directly from its own DBC file ─────────
        all_signals: dict[str, object] = {}
        for ch_cfg in can_channels:
            db_loader = DatabaseLoader()
            db_loader.load_dbc(ch_cfg.can_db_file)
            self._db_loaders.append(db_loader)
            overlap_count = 0
            for sig_name in db_loader.signals:
                if sig_name in all_signals:
                    overlap_count += 1
            if overlap_count:
                logger.debug(
                    "Channel '%s': %d signals overlap with previously loaded channels",
                    ch_cfg.channel, overlap_count,
                )
            all_signals.update(db_loader.signals)
            logger.info(
                "Channel '%s': %s", ch_cfg.channel, db_loader.summary(),
            )

        # Initialize SignalStore with all known signals (initial values + units)
        try:
            import time

            initial_values: dict[str, float] = {}
            units: dict[str, str] = {}
            for name, sig in all_signals.items():
                units[name] = sig.unit or None
                if sig.minimum is not None:
                    initial_values[name] = float(sig.minimum)
                elif sig.maximum is not None:
                    initial_values[name] = float(sig.maximum)
                else:
                    initial_values[name] = 0.0
            await self.store.bulk_update(initial_values, timestamp=time.time(), units=units)
            logger.info(
                "Seeded SignalStore with %d signals from %d channels",
                len(initial_values), len(can_channels),
            )
        except Exception:
            logger.exception("Failed to seed SignalStore with DBC signals")

        # 2. In-memory DBC metadata
        self._signal_metadata = SignalMetadataCatalog()
        self._signal_metadata.replace_from_loaders(self._db_loaders)
        logger.info("Loaded metadata for %d signals into memory", len(self._signal_metadata))

        # 3. Signal Pipeline (share one queue across all channels) ───────────────
        rx_queue: asyncio.Queue = asyncio.Queue(maxsize=proc_cfg.max_queue_size)
        self._rx_queue = rx_queue

        self._pipeline = SignalPipeline(
            input_queue=rx_queue,
            signal_store=self.store,
            queue_policy=proc_cfg.queue_policy,
            batch_drain_size=proc_cfg.batch_drain_size,
        )
        self._rate_limiter = RateLimiter(max_hz=proc_cfg.max_update_rate_hz)
        self._pipeline.add_stage(self._rate_limiter)
        oms_cfg = self.config.oms_config
        self._oms_classifier = OMSClassificationProcessor(
            bypass_simi_input=oms_cfg.bypass_simi_input,
            class_config=oms_cfg.class_config,
            target_signals=oms_cfg.target_signal,
        )
        self._pipeline.add_stage(self._oms_classifier)
        self._pipeline.add_stage(ComputedSignals())

        # 4. Check the simulator early — before opening the bus ──────────────────
        # Automatically disable it if any channel uses real hardware (non-virtual)
        real_interfaces = [ch.interface for ch in can_channels if ch.interface != "virtual"]
        if sim_cfg.enabled and real_interfaces:
            logger.info(
                "Simulator auto-disabled — real CAN interface(s) detected: %s",
                ", ".join(real_interfaces),
            )
            sim_cfg = type(sim_cfg)(**{**sim_cfg.model_dump(), "enabled": False})

        # 5. CAN Bus + Reader + Writer — one set per channel ────────────────────
        writer_router = CANWriterRouter()
        for idx, ch_cfg in enumerate(can_channels):
            db_loader = self._db_loaders[idx]

            def _make_bus_factory(cfg=ch_cfg, loader=db_loader):
                match_ids = resolve_auto_match_ids(cfg, loader) if cfg.channel == "auto" else set()
                return lambda: create_bus(cfg, auto_match_ids=match_ids)

            bus_factory = _make_bus_factory()
            try:
                bus = bus_factory()
            except can.CanError as exc:
                if ch_cfg.channel != "auto":
                    raise
                bus = None
                logger.warning(
                    "Automatic CAN discovery unavailable at startup: %s — continuing in degraded mode",
                    exc,
                )
            self._bus_factories.append(bus_factory)
            self._buses.append(bus)

            writer = CANWriter(
                bus=bus,
                db=db_loader,
                signal_store=self.store,
                writer_config=self.config.writer,
            )
            self._writers.append(writer)

            async def _replace_channel_bus(
                replacement_bus,
                channel_index=idx,
                channel_writer=writer,
            ) -> None:
                await channel_writer.set_bus(replacement_bus)
                self._buses[channel_index] = replacement_bus

            async def _mark_channel_bus_unavailable(
                reason,
                channel_writer=writer,
            ) -> None:
                await channel_writer.set_bus(None, reason)

            reader = CANReader(
                bus=bus,
                db=db_loader,
                queue=rx_queue,
                filter_ids=set(db_loader.messages),
                bus_factory=bus_factory,
                queue_policy=proc_cfg.queue_policy,
                max_rate_hz=proc_cfg.max_update_rate_hz,
                priority_sec=self.config.reader.frequency_piority,
                stale_threshold_sec=self.config.reader.stale_threshold_sec,
                frontend_retry_enabled=ch_cfg.channel == "auto",
                on_bus_disconnecting=_mark_channel_bus_unavailable,
                on_bus_reconnected=_replace_channel_bus,
            )
            self._readers.append(reader)

            writer_router.register(db_loader, writer)

            logger.info(
                "CAN channel[%d] '%s' ready (interface=%s, bitrate=%d)",
                idx, ch_cfg.channel, ch_cfg.interface, ch_cfg.bitrate,
            )

        self._writer_router = writer_router

        # 6. CAN Simulator (optional) ────────────────────────────────────────────
        if sim_cfg.enabled:
            self._simulator = await self._build_simulator(sim_cfg)

        # 6. FastAPI server ──────────────────────────────────────────────────────
        self._fastapi_server = await self._build_api_server()

        # Schedule all async tasks
        self._tasks = [
            asyncio.create_task(self._pipeline.start(), name="pipeline"),
        ]
        for idx, reader in enumerate(self._readers):
            ch_name = can_channels[idx].channel
            self._tasks.append(
                asyncio.create_task(reader.start(), name=f"can-reader-{ch_name}")
            )
        if self._simulator:
            self._tasks.append(asyncio.create_task(self._simulator.start(), name="simulator"))
        if self._fastapi_server:
            self._tasks.append(asyncio.create_task(self._fastapi_server(), name="api"))
        if self.config.supervisor.watchdog_interval_sec > 0:
            self._tasks.append(asyncio.create_task(self._watchdog(), name="watchdog"))
        self._tasks.append(asyncio.create_task(self._metrics_broadcaster(), name="metrics-push"))

        monitor_cfg = self.config.status_monitor
        if monitor_cfg.enabled:
            ping_targets, can_reference_targets = self._build_status_targets()
            if ping_targets or can_reference_targets:
                self._tasks.append(
                    asyncio.create_task(
                        self._status_monitor(
                            ping_targets=ping_targets,
                            can_reference_targets=can_reference_targets,
                            interval_sec=max(1.0, float(monitor_cfg.interval_sec)),
                            ping_timeout_sec=max(0.2, float(monitor_cfg.ping_timeout_sec)),
                        ),
                        name="status-monitor",
                    )
                )
                logger.info(
                    "Status monitor enabled for %d signal(s) (ethernet=%d, can_ref=%d)",
                    len(ping_targets) + len(can_reference_targets),
                    len(ping_targets),
                    len(can_reference_targets),
                )
            else:
                logger.warning("Status monitor enabled but no valid targets configured")

    async def _build_simulator(self, sim_cfg) -> object | None:
        """Build CANSimulator if simulator configuration exists."""
        from src.can_simulator.simulator import CANSimulator

        can_db_file = Path(sim_cfg.can_db_file)
        if not can_db_file.exists():
            logger.warning("can_db_file '%s' not found — simulator disabled", can_db_file)
            return None

        sim_bus = self._bus_factories[0]() if self._bus_factories else None
        if sim_bus is None:
            logger.warning("No CAN bus factory available — simulator disabled")
            return None
        self._simulator_bus = sim_bus
        return CANSimulator(
            bus=sim_bus,
            can_db_file=can_db_file,
            cycle_ms=sim_cfg.default_cycle_ms,
            repeat=True,
            random_mode=getattr(sim_cfg, "random_mode", False),
        )

    async def _build_api_server(self):
        """Build FastAPI and the coroutine that runs uvicorn."""
        try:
            import uvicorn

            from src.api.app import create_app
        except ImportError as exc:
            raise RuntimeError(
                "API server dependencies could not be imported; refusing to run without the API"
            ) from exc

        api_cfg = self.config.api
        app = create_app(
            signal_store=self.store,
            signal_metadata=self._signal_metadata,
            can_readers=self._readers,
            api_key=api_cfg.api_key,
            cors_origins=api_cfg.cors_origins,
        )
        self._api_app = app
        # Expose runtime objects so config endpoints can attempt to apply changes
        app.state.pipeline = self._pipeline
        app.state.runner = self
        app.state.writer = self._writer_router
        app.state.rx_queue = self._rx_queue
        self._ws_manager = app.state.ws_manager
        self._ws_manager.set_only_send_signal_update(self.config.reader.only_send_signal_update)

        # Coalesce fast signal updates into a single WS frame to reduce chatter.
        ws_batch_window_s = 0.02
        pending_ws_updates: dict[str, tuple[float, float]] = {}
        ws_batch_task: asyncio.Task | None = None

        async def _flush_ws_batch() -> None:
            nonlocal ws_batch_task
            try:
                while True:
                    await asyncio.sleep(ws_batch_window_s)
                    if not pending_ws_updates:
                        ws_batch_task = None
                        return

                    batch = [
                        (name, value_ts[0], value_ts[1])
                        for name, value_ts in pending_ws_updates.items()
                    ]
                    pending_ws_updates.clear()
                    await self._ws_manager.broadcast_signals(batch)
            except Exception:
                logger.exception("WS batch flush failed")
                ws_batch_task = None

        # Register SignalStore for WebSocket broadcasting
        async def _broadcast_signal(name: str, sv) -> None:
            nonlocal ws_batch_task
            pending_ws_updates[name] = (sv.value, sv.timestamp)
            if ws_batch_task is None or ws_batch_task.done():
                ws_batch_task = asyncio.create_task(_flush_ws_batch())

        self.store.subscribe(_broadcast_signal)

        server_config = uvicorn.Config(
            app,
            host=api_cfg.host,
            port=api_cfg.port,
            log_level=self.config.logging.level.lower(),
            access_log=False,
        )
        server = uvicorn.Server(server_config)
        self._uvicorn_server = server

        async def _serve_safe() -> None:
            """Wrap server.serve in try/except to convert SystemExit into a normal exception."""
            try:
                sockets = [self._api_socket] if self._api_socket is not None else None
                await server.serve(sockets=sockets)
            except asyncio.CancelledError:
                # Expected when shutdown() cancels the api task — suppress noisy traceback.
                pass
            except OSError as exc:
                logger.error(
                    "API server failed to bind on %s:%d — %s (port already in use?)",
                    api_cfg.host, api_cfg.port, exc,
                )
                raise RuntimeError(f"API bind error: {exc}") from exc
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
        for reader in self._readers:
            if hasattr(reader, "set_queue"):
                try:
                    reader.set_queue(new_q)
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
        """Broadcast system metrics over WS to subscribers registered for the 'metrics' channel."""
        from src.core.system_metrics import collect_system_metrics, metrics_to_dict

        # metrics interval configurable via API config; default 3s -> allow float
        while not self._shutting_down:
            interval = float(getattr(self.config.api, "ws_metrics_interval_sec", 3.0))
            await asyncio.sleep(interval)
            if self._ws_manager is None:
                continue
            try:
                rx_queue = getattr(self, "_rx_queue", None)
                m = collect_system_metrics(rx_queue=rx_queue, start_time=self._start_time)
                await self._ws_manager.broadcast_metrics(metrics_to_dict(m))
            except Exception:
                logger.debug("Failed to broadcast metrics", exc_info=True)

    def _build_status_targets(self) -> tuple[dict[str, str], dict[str, str]]:
        cfg_targets = dict(getattr(self.config.status_monitor, "targets", {}) or {})

        for signal_name, host in _DEFAULT_ETHERNET_TARGETS.items():
            cfg_targets.setdefault(signal_name, host)

        jetson_signal = "COM_Status_NvidiaJetsonEthernet"
        if jetson_signal not in cfg_targets:
            cam_host = _extract_host(self.config.camera.stream_url)
            if cam_host:
                cfg_targets[jetson_signal] = cam_host

        ping_targets: dict[str, str] = {}
        can_reference_targets: dict[str, str] = {}
        for signal_name, target in cfg_targets.items():
            signal_key = str(signal_name).strip()
            if not signal_key:
                continue

            if signal_key.endswith("Ethernet"):
                host = _extract_host(target)
                if not host:
                    logger.warning(
                        "Ignoring status monitor ethernet target '%s' because host is empty (%r)",
                        signal_key,
                        target,
                    )
                    continue
                ping_targets[signal_key] = host
                continue

            ref_signal = str(target).strip() if target is not None else ""
            if not ref_signal:
                logger.warning(
                    "Ignoring status monitor CAN reference '%s' because reference signal is empty (%r)",
                    signal_key,
                    target,
                )
                continue
            can_reference_targets[signal_key] = ref_signal

        return ping_targets, can_reference_targets

    async def _ping_host(self, host: str, timeout_sec: float) -> bool:
        if sys.platform.startswith("win"):
            cmd = ["ping", "-n", "1", host]
        else:
            cmd = ["ping", "-c", "1", host]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            if not self._ping_unavailable_logged:
                logger.error("Ping command is not available in PATH; ethernet monitor disabled")
                self._ping_unavailable_logged = True
            return False
        except Exception:
            logger.debug("Failed to start ping for host %s", host, exc_info=True)
            return False

        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
            return proc.returncode == 0
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            return False
        except Exception:
            logger.debug("Ping failed for host %s", host, exc_info=True)
            return False

    @staticmethod
    def _can_reference_is_online(
        reference_signal: str,
        signal_value,
        *,
        now: float,
        interval_sec: float,
    ) -> bool:
        """Evaluate a CAN freshness reference without treating a fresh offline flag as online."""
        if signal_value is None:
            return False
        is_fresh = (now - float(signal_value.timestamp)) < interval_sec
        if reference_signal.startswith("COM_Status_") and reference_signal.endswith("Can"):
            return is_fresh and float(signal_value.value) > 0.0
        return is_fresh

    async def _status_monitor(
        self,
        *,
        ping_targets: dict[str, str],
        can_reference_targets: dict[str, str],
        interval_sec: float,
        ping_timeout_sec: float,
    ) -> None:
        signal_names = set(ping_targets) | set(can_reference_targets)

        while not self._shutting_down:
            try:
                if self._ws_manager is None or not await self._ws_manager.has_signal_interest(signal_names):
                    await asyncio.sleep(interval_sec)
                    continue

                now = time.time()
                updates: dict[str, float] = {}

                if ping_targets:
                    checks = await asyncio.gather(
                        *(self._ping_host(host, ping_timeout_sec) for host in ping_targets.values()),
                        return_exceptions=False,
                    )
                    updates.update(
                        {
                            signal_name: 1.0 if is_online else 0.0
                            for (signal_name, _), is_online in zip(
                                ping_targets.items(), checks, strict=True
                            )
                        }
                    )

                for signal_name, reference_signal in can_reference_targets.items():
                    ref_signal_value = await self.store.get(reference_signal)
                    is_connected = self._can_reference_is_online(
                        reference_signal,
                        ref_signal_value,
                        now=now,
                        interval_sec=interval_sec,
                    )
                    updates[signal_name] = 1.0 if is_connected else 0.0

                if updates:
                    await self.store.bulk_update(updates, timestamp=now)
            except Exception:
                logger.debug("Status monitor cycle failed", exc_info=True)

            await asyncio.sleep(interval_sec)

    async def _watchdog(self) -> None:
        """Perform periodic health checks — log status and potentially restart components."""
        interval = self.config.supervisor.watchdog_interval_sec
        while not self._shutting_down:
            await asyncio.sleep(interval)
            alive = [t.get_name() for t in self._tasks if not t.done()]
            done_tasks = [t for t in self._tasks if t.done()]
            done = [t.get_name() for t in done_tasks]
            if done:
                logger.warning("Tasks finished unexpectedly: %s", done)
                # Log exceptions from dead tasks for diagnostics
                for t in done_tasks:
                    exc = t.exception() if not t.cancelled() else None
                    if exc is not None:
                        logger.error("Task '%s' raised: %s", t.get_name(), exc, exc_info=exc)
                # Remove dead tasks so we don't report them again next cycle
                self._tasks = [t for t in self._tasks if not t.done()]
            # Report reader/pipeline metrics if available
            try:
                rx_q_size = self._rx_queue.qsize() if hasattr(self, "_rx_queue") else None
            except Exception:
                rx_q_size = None

            reader_metrics = None
            reader_fatal: list[dict] = []
            any_can_connected = False
            disconnected_status_signals: set[str] = set()
            has_unmapped_disconnected_reader = False
            if self._readers:
                try:
                    reader_metrics = {
                        f"ch{i}": r.get_metrics()
                        for i, r in enumerate(self._readers)
                    }
                    for idx, reader in enumerate(self._readers):
                        state = reader.get_runtime_state() if hasattr(reader, "get_runtime_state") else {}
                        if state.get("fatal_error"):
                            reader_fatal.append(
                                {
                                    "channel": idx,
                                    "fatal_error": state.get("fatal_error"),
                                    "last_error": state.get("last_error"),
                                }
                            )
                        if self._reader_state_is_connected(
                            state,
                            self.config.reader.stale_threshold_sec,
                        ):
                            any_can_connected = True
                        else:
                            mapped_statuses = self._can_status_signals_for_reader(idx)
                            if mapped_statuses:
                                disconnected_status_signals.update(mapped_statuses)
                            else:
                                has_unmapped_disconnected_reader = True
                except Exception:
                    reader_metrics = None

            logger.info("Watchdog — alive tasks: %s | rx_queue_size=%s | reader_metrics=%s", alive, rx_q_size, reader_metrics)

            if disconnected_status_signals:
                await self._set_can_status_disconnected(disconnected_status_signals)
            elif (
                self._readers
                and not any_can_connected
                and has_unmapped_disconnected_reader
            ):
                # Compatibility fallback for tests/custom readers that have no
                # aligned DatabaseLoader. Production readers use the per-DBC map.
                await self._set_can_status_disconnected()

            if reader_fatal:
                logger.critical(
                    "Detected unrecoverable CAN reader failure(s): %s — fail-fast shutdown to let supervisor restart process",
                    reader_fatal,
                )
                await self.shutdown()
                raise RuntimeError(f"Unrecoverable CAN reader failure: {reader_fatal}")

    @staticmethod
    def _reader_state_is_connected(state: dict, stale_threshold_sec: float) -> bool:
        """Assess reader connectivity while honoring zero as 'stale check disabled'."""
        age = state.get("last_recv_age_sec")
        age_is_healthy = stale_threshold_sec <= 0 or (
            age is not None and age <= stale_threshold_sec
        )
        return bool(
            state.get("thread_alive")
            and not state.get("fatal_error")
            and age_is_healthy
        )

    def _can_status_signals_for_reader(self, reader_index: int) -> set[str]:
        """Return CAN status signals owned by the reader's channel DBC."""
        if reader_index >= len(self._db_loaders):
            return set()
        return {
            name
            for name in self._db_loaders[reader_index].signals
            if name.startswith("COM_Status_") and name.endswith("Can")
        }

    async def _set_can_status_disconnected(
        self,
        signal_names: set[str] | None = None,
    ) -> None:
        """Mark all COM_Status_*Can signals offline while the bus is unavailable."""
        snapshot = await self.store.get_snapshot()
        offline = {
            name: 0.0
            for name, signal in snapshot.items()
            if name.startswith("COM_Status_") and name.endswith("Can")
            and (signal_names is None or name in signal_names)
            and float(signal.value) != 0.0
        }
        if offline:
            await self.store.bulk_update(offline, timestamp=time.time())

    async def retry_can_connections(self) -> list[bool]:
        """Request immediate reconnect processing for every configured CAN reader."""
        return [await reader.request_reconnect() for reader in self._readers]

    def notify_frontend_activity(self) -> int:
        """Wake disconnected CAN readers when HTTP/WS activity shows a frontend is active."""
        return sum(reader.notify_frontend_activity() for reader in self._readers)

    async def apply_system_config(self, new_config: AppConfig, changed_paths: list[str]) -> dict:
        """Apply every policy-approved live field and synchronize runtime references."""
        from src.core.config_policy import ReloadLevel, diff_paths, match_policy

        # Keep deployment-only port and secret overrides after a config PATCH,
        # reset, restore, or explicit runtime reload.
        new_config = apply_environment_overrides(new_config)

        live_paths = [
            path
            for path in changed_paths
            if (policy := match_policy(path)) is not None
            and policy.reload_level == ReloadLevel.LIVE
        ]
        live_set = set(live_paths)
        boot_diff = diff_paths(
            self._boot_config,
            new_config.model_dump(mode="json"),
        )
        self.pending_reboot_paths = {
            path
            for path in boot_diff
            if (policy := match_policy(path)) is None
            or policy.reload_level != ReloadLevel.LIVE
        }

        if "processor.max_queue_size" in live_set:
            await self.migrate_rx_queue(new_config.processor.max_queue_size)

        if self._pipeline is not None:
            self._pipeline.apply_runtime_config(
                queue_policy=new_config.processor.queue_policy,
                batch_drain_size=new_config.processor.batch_drain_size,
            )
        if self._rate_limiter is not None:
            self._rate_limiter.set_max_hz(new_config.processor.max_update_rate_hz)
        if self._oms_classifier is not None:
            oms_cfg = new_config.oms_config
            self._oms_classifier.apply_runtime_config(
                bypass_simi_input=oms_cfg.bypass_simi_input,
                class_config=oms_cfg.class_config,
                target_signals=oms_cfg.target_signal,
            )

        for reader in self._readers:
            reader.apply_runtime_config(
                queue_policy=new_config.processor.queue_policy,
                max_rate_hz=new_config.processor.max_update_rate_hz,
                priority_sec=new_config.reader.frequency_piority,
                stale_threshold_sec=new_config.reader.stale_threshold_sec,
            )
        for writer in self._writers:
            writer.apply_runtime_config(new_config.writer)

        if self._ws_manager is not None:
            self._ws_manager.set_only_send_signal_update(
                new_config.reader.only_send_signal_update
            )
        if self._api_app is not None:
            self._api_app.state.reader_stale_threshold_sec = (
                new_config.reader.stale_threshold_sec
            )
            self._api_app.state.devmode_bypass_can_status = (
                new_config.devmode.bypass_check_CAN_status
            )

        if "logging.level" in live_set:
            level = getattr(logging, new_config.logging.level, logging.INFO)
            root_logger = logging.getLogger()
            root_logger.setLevel(level)
            for handler in root_logger.handlers:
                handler.setLevel(level)

        self.config = new_config
        logger.info("Applied live system config fields: %s", live_paths)
        return {"applied": live_paths, "unavailable": []}

    async def request_reboot(self) -> bool:
        """Gracefully stop this process; systemd Restart=on-failure starts it again."""
        if self._shutting_down or self._reboot_requested:
            return False
        self._reboot_requested = True
        self._reboot_task = asyncio.create_task(self.shutdown(), name="reboot")
        return True

    async def shutdown(self) -> None:
        """Shut down cleanly: stop runtime components and close the config DB."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutting down (timeout=%ds)...", self.config.shutdown.timeout_sec)

        for reader in self._readers:
            reader.stop()
        if self._simulator:
            self._simulator.stop()
        if self._api_app is not None:
            camera_proxy = getattr(self._api_app.state, "camera_proxy", None)
            if camera_proxy is not None:
                try:
                    await camera_proxy.aclose()
                except Exception:
                    logger.debug("Camera proxy close failed during shutdown", exc_info=True)
            ws_manager = getattr(self._api_app.state, "ws_manager", None)
            if ws_manager is not None and hasattr(ws_manager, "close_all"):
                try:
                    await ws_manager.close_all()
                except Exception:
                    logger.debug("WebSocket close-all failed during shutdown", exc_info=True)
        if self._pipeline:
            self._pipeline.stop()

        # Signal uvicorn to stop gracefully *before* cancelling its task so
        # the lifespan context manager has a chance to exit cleanly.
        if self._uvicorn_server is not None:
            self._shutdown_noise_filter.enabled = True
            if self._api_app is not None:
                self._api_app.state.shutting_down = True
            self._uvicorn_server.handle_exit(sig=signal.SIGTERM, frame=None)
            api_shutdown_timeout = float(self.config.shutdown.timeout_sec)
            # Wait for the API task to return on its own (uvicorn drains the
            # lifespan queue and exits serve()). If that does not happen within
            # the configured shutdown window, ask uvicorn to force-exit rather
            # than cancelling the API task directly.
            api_tasks = [t for t in self._tasks if t.get_name() == "api"]
            if api_tasks:
                try:
                    await asyncio.wait_for(asyncio.shield(api_tasks[0]), timeout=api_shutdown_timeout)
                except TimeoutError:
                    logger.warning("API shutdown timed out after %.1fs; forcing uvicorn exit", api_shutdown_timeout)
                    self._uvicorn_server.force_exit = True
                    try:
                        await asyncio.wait_for(asyncio.shield(api_tasks[0]), timeout=1.0)
                    except TimeoutError:
                        logger.warning("API task still running after forced exit; continuing shutdown")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

        for task in self._tasks:
            if task.get_name() != "api":
                task.cancel()

        if self._tasks:
            await asyncio.gather(*(task for task in self._tasks if task.get_name() != "api"), return_exceptions=True)

        for bus in self._buses:
            if bus is None:
                continue
            try:
                bus.shutdown()
            except Exception:
                pass

        if self._simulator_bus:
            try:
                self._simulator_bus.shutdown()
            except Exception:
                pass

        if self._api_socket is not None:
            with contextlib.suppress(OSError):
                self._api_socket.close()
            self._api_socket = None

        logger.info("Shutdown complete.")

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def reboot_requested(self) -> bool:
        """True when the reboot API requested a supervisor-managed restart."""
        return self._reboot_requested


# ── CLI entry-point ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="can-hmi",
        description="CAN-HMI Signal Processing & API Server",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config/system.json",
        help="Path to configuration JSON file (default: config/system.json)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    cfg = apply_environment_overrides(load_config(args.config))
    if args.log_level:
        cfg.logging.level = args.log_level

    runner = AppRunner(cfg)
    try:
        asyncio.run(runner.start())
    except KeyboardInterrupt:
        sys.exit(0)
    if runner.reboot_requested:
        sys.exit(75)


if __name__ == "__main__":
    main()
