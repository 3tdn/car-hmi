"""Factory tạo ứng dụng FastAPI."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.auth import APIKeyAuth
from src.api.routes import (
    adaptive_restraint,
    alarms,
    camera,
    config,
    devmode,
    profiles,
    restraints,
    signals,
    system,
)
from src.api.websocket import ConnectionManager
from src.core.camera_stream import CameraStreamProxy
from src.core.config_manager import DBCJobManager, read_config
from src.core.signal_name_mapper import SignalNameMapper

logger = logging.getLogger(__name__)


def create_app(
    signal_store,  # SignalStore
    repository,  # ISignalRepository
    can_readers=None,  # list[CANReader] | None
    api_key: str = "",
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Xây dựng và cấu hình ứng dụng FastAPI."""
    app = FastAPI(
        title="CAN-HMI Signal API",
        version="0.1.0",
        description="Real-time CAN bus signal monitoring and control API",
    )
    app.state.shutting_down = False

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Trạng thái chia sẻ — truy cập qua request.app.state
    app.state.store = signal_store
    app.state.repo = repository
    app.state.readers = can_readers or []
    app.state.start_time = time.time()

    # Load signal name mapper from sync_dict config
    _cfg = read_config()
    _sig_cfg = _cfg.get("signal", {})
    _reader_cfg = _cfg.get("reader", {})
    _profile_cfg = _cfg.get("profiles", {})
    signal_name_mapper = SignalNameMapper(_sig_cfg.get("sync_dict"))
    app.state.signal_name_mapper = signal_name_mapper
    app.state.reader_stale_threshold_sec = float(_reader_cfg.get("stale_threshold_sec", 30.0))
    app.state.config_snapshot = _cfg
    app.state.signal_catalog = {}
    app.state.alarm_snapshot = {"alarms": {}}

    # Seed runtime metadata snapshots from the current config so request handlers
    # can avoid repeated disk reads for signal metadata and alarms.
    try:
        import json
        from pathlib import Path

        for ch in _cfg.get("can", []):
            can_json_path = Path(ch.get("can_json_path", ""))
            if not can_json_path.exists():
                continue
            ch_raw = json.loads(can_json_path.read_text(encoding="utf-8")) or {}
            for msg_data in ch_raw.get("messages", {}).values():
                for sig_name, sig_data in msg_data.get("signals", {}).items():
                    app.state.signal_catalog.setdefault(
                        sig_name,
                        {
                            "min_value": sig_data.get("minimum"),
                            "max_value": sig_data.get("maximum"),
                            "unit": sig_data.get("unit") or None,
                            "writable": bool(sig_data.get("TX", False)),
                            "states": sig_data.get("states") or None,
                            "tag": sig_data.get("tag") or signals._infer_signal_tags(sig_name) or None,
                        },
                    )
    except Exception:
        logger.debug("Failed to build runtime signal catalog from config", exc_info=True)

    try:
        from src.core.config_manager import read_alarms

        app.state.alarm_snapshot = read_alarms()
    except Exception:
        logger.debug("Failed to load runtime alarms snapshot", exc_info=True)

    app.state.profile_session_cleanup_task = None

    try:
        cleanup_interval_sec = max(1.0, float(_profile_cfg.get("session_cleanup_interval_sec", 5.0)))
    except (TypeError, ValueError):
        cleanup_interval_sec = 5.0
    app.state.devmode_cleanup_interval_sec = cleanup_interval_sec


    app.state.ws_manager = ConnectionManager(signal_name_mapper=signal_name_mapper)
    app.state.dbc_job_manager = DBCJobManager()

    async def _stop_profile_session_cleanup_task() -> None:
        app.state.shutting_down = True
        task = app.state.profile_session_cleanup_task
        app.state.profile_session_cleanup_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app.router.on_shutdown.append(_stop_profile_session_cleanup_task)

    # Camera stream proxy — fan-out cho nhiều client dù camera upstream chỉ
    # cho phép 1 kết nối đồng thời (mutex phía nguồn).
    _cam_cfg = app.state.config_snapshot.get("camera", {})
    if _cam_cfg.get("enabled", False):
        camera_proxy = CameraStreamProxy(
            stream_url=_cam_cfg.get("stream_url", "http://192.168.2.119:8080/stream"),
            reconnect_interval_sec=_cam_cfg.get("reconnect_interval_sec", 3.0),
            connect_timeout_sec=_cam_cfg.get("connect_timeout_sec", 5.0),
            read_timeout_sec=_cam_cfg.get("read_timeout_sec", 10.0),
            chunk_size=_cam_cfg.get("chunk_size", 4096),
            subscriber_queue_size=_cam_cfg.get("subscriber_queue_size", 64),
            startup_wait_sec=_cam_cfg.get("startup_wait_sec", 5.0),
            fps_log_interval_sec=_cam_cfg.get("fps_log_interval_sec", 5.0),
        )
        app.state.camera_proxy = camera_proxy

        async def _close_camera_proxy() -> None:
            await camera_proxy.aclose()

        app.router.on_shutdown.append(_close_camera_proxy)
    else:
        app.state.camera_proxy = None
        logger.info("Camera stream disabled via config/system.json ('camera.enabled' = false)")

    # Xài API key mặc định là 'auth disabled' cho môi trường local/demo.
    if api_key and api_key.strip().lower() in {"change-me-in-production", "changeme", "default"}:
        logger.warning("API key is set to a placeholder value; authentication disabled.")
        api_key = ""

    app.state.auth = APIKeyAuth(api_key)

    auth_dep = Depends(app.state.auth)

    # Đăng ký các router
    app.include_router(signals.router, prefix="/signals", tags=["Signals"], dependencies=[auth_dep])
    app.include_router(alarms.router, prefix="/alarms", tags=["Alarms"], dependencies=[auth_dep])
    app.include_router(config.router, prefix="/config", tags=["Config"], dependencies=[auth_dep])
    app.include_router(adaptive_restraint.router, prefix="/adaptive_restraint", tags=["Adaptive Restraint"])
    app.include_router(system.router, prefix="/system", tags=["System"])
    app.include_router(restraints.router, prefix="/api/restraints", tags=["Restraints"])
    app.include_router(camera.router, prefix="/api/camera", tags=["Camera"])
    app.include_router(devmode.router, prefix="/api/devmode", tags=["Dev Mode"], dependencies=[auth_dep])
    # /api/info — thông tin hệ thống theo demo spec
    app.include_router(system.router, prefix="/api", tags=["System Info"])
    # Profile management
    app.include_router(profiles.router, prefix="/api", tags=["Profiles"], dependencies=[auth_dep])
    # Điểm cuối WebSocket (không có auth dep — auth xử lý trong handshake ws)
    app.include_router(signals.ws_router, prefix="/ws", tags=["WebSocket"])

    # Phục vụ frontend tích hợp sẵn tại gốc ứng dụng.
    # Ưu tiên dist/ (npm build output); fallback về frontend/ (vanilla).
    project_root = Path(__file__).resolve().parents[2]
    frontend_dist = project_root / "frontend" / "dist"
    frontend_vanilla = project_root / "frontend"
    frontend_dir = frontend_dist if frontend_dist.exists() else frontend_vanilla
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        logger.info("Serving frontend from %s", frontend_dir)
    else:
        logger.debug("Frontend directory not found at %s", frontend_dir)

    return app
