"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.auth import APIKeyAuth
from src.api.routes import (
    adaptive_restraint,
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
from src.core.config_manager import SystemConfigManager

logger = logging.getLogger(__name__)

_IPV4_SEGMENT_REGEX = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_FRONTEND_ACTIVITY_EXCLUDED_PATHS = {
    "/health",
    "/ready",
    "/system/health",
    "/system/ready",
}


def _cors_ipv4_pattern(origin: str) -> str | None:
    """Convert x/* IPv4 labels in one origin into a full-match regex fragment."""
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None
    labels = (parsed.hostname or "").split(".")
    wildcard_labels = {"x", "*"}
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or len(labels) != 4
        or not any(label.lower() in wildcard_labels for label in labels)
        or any(
            label.lower() not in wildcard_labels
            and (not label.isdigit() or not 0 <= int(label) <= 255)
            for label in labels
        )
    ):
        return None

    host_pattern = r"\.".join(
        _IPV4_SEGMENT_REGEX if label.lower() in wildcard_labels else re.escape(label)
        for label in labels
    )
    port_pattern = f":{port}" if port is not None else ""
    return rf"{re.escape(parsed.scheme)}://{host_pattern}{port_pattern}"


def _split_cors_origins(origins: list[str]) -> tuple[list[str], str | None]:
    """Separate exact origins from supported IPv4 wildcard origins."""
    exact_origins: list[str] = []
    pattern_fragments: list[str] = []
    for origin in origins:
        if pattern := _cors_ipv4_pattern(origin):
            pattern_fragments.append(pattern)
        else:
            exact_origins.append(origin)
    regex = rf"(?:{'|'.join(pattern_fragments)})" if pattern_fragments else None
    return exact_origins, regex


def _notify_frontend_activity(scope: Scope) -> None:
    app = scope.get("app")
    runner = getattr(getattr(app, "state", None), "runner", None)
    notify = getattr(runner, "notify_frontend_activity", None)
    if not callable(notify):
        return
    try:
        notify()
    except Exception:
        logger.debug("Frontend-activity CAN retry notification failed", exc_info=True)


class _FrontendActivityMiddleware:
    """Wake CAN reconnect backoff on HTTP requests and WebSocket activity."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if (
            scope_type == "http"
            and scope.get("path") not in _FRONTEND_ACTIVITY_EXCLUDED_PATHS
        ):
            _notify_frontend_activity(scope)
        elif scope_type == "websocket":
            _notify_frontend_activity(scope)
            original_receive = receive

            async def receive_with_activity() -> Message:
                message = await original_receive()
                if message["type"] == "websocket.receive":
                    _notify_frontend_activity(scope)
                return message

            receive = receive_with_activity

        await self.app(scope, receive, send)


def create_app(
    signal_store,  # SignalStore
    *,
    can_readers=None,  # list[CANReader] | None
    signal_metadata=None,  # SignalMetadataCatalog | None
    api_key: str = "",
    cors_origins: list[str] | None = None,
    system_config_manager: SystemConfigManager | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="CAN-HMI Signal API",
        version="0.1.0",
        description="Real-time CAN bus signal monitoring and control API",
    )
    app.state.shutting_down = False

    # CORS
    exact_origins, origin_regex = _split_cors_origins(
        cors_origins if cors_origins is not None else ["*"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=exact_origins,
        allow_origin_regex=origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(_FrontendActivityMiddleware)

    # Shared state — access via request.app.state
    app.state.store = signal_store
    if signal_metadata is None:
        from src.core.signal_metadata import SignalMetadataCatalog

        signal_metadata = SignalMetadataCatalog()
    app.state.signal_metadata = signal_metadata
    app.state.readers = can_readers or []
    app.state.start_time = time.time()
    app.state.runner = None

    config_manager = system_config_manager or SystemConfigManager(repair_on_read=False)
    app.state.system_config_manager = config_manager
    _cfg = config_manager.read()
    _reader_cfg = _cfg.get("reader", {})
    _profile_cfg = _cfg.get("profiles", {})
    _devmode_cfg = _cfg.get("devmode", {})
    app.state.reader_stale_threshold_sec = float(_reader_cfg.get("stale_threshold_sec", 30.0))
    app.state.devmode_bypass_can_status = bool(
        _devmode_cfg.get("bypass_check_CAN_status", False)
    )
    app.state.profile_session_cleanup_task = None

    try:
        cleanup_interval_sec = max(
            1.0, float(_profile_cfg.get("session_cleanup_interval_sec", 5.0))
        )
    except (TypeError, ValueError):
        cleanup_interval_sec = 5.0
    app.state.devmode_cleanup_interval_sec = cleanup_interval_sec

    app.state.ws_manager = ConnectionManager()

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

    # Camera stream proxy — fan-out to multiple clients even though the upstream camera only
    # allows 1 concurrent connection (source-side mutex).
    _cam_cfg = _cfg.get("camera", {})
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

    # Treat default API keys as 'auth disabled' in local/demo environments.
    if api_key and api_key.strip().lower() in {"change-me-in-production", "changeme", "default"}:
        logger.warning("API key is set to a placeholder value; authentication disabled.")
        api_key = ""

    app.state.auth = APIKeyAuth(api_key)

    auth_dep = Depends(app.state.auth)

    # Register routers
    app.include_router(signals.router, prefix="/signals", tags=["Signals"], dependencies=[auth_dep])
    app.include_router(config.router, prefix="/config", tags=["Config"], dependencies=[auth_dep])
    app.include_router(
        adaptive_restraint.router, prefix="/adaptive_restraint", tags=["Adaptive Restraint"]
    )
    app.include_router(system.router, prefix="/system", tags=["System"])
    app.include_router(restraints.router, prefix="/api/restraints", tags=["Restraints"])
    app.include_router(camera.router, prefix="/api/camera", tags=["Camera"])
    app.include_router(
        devmode.router, prefix="/api/devmode", tags=["Dev Mode"], dependencies=[auth_dep]
    )
    # /api/info — system information per the demo spec
    app.include_router(system.router, prefix="/api", tags=["System Info"])
    # Profile management
    app.include_router(profiles.router, prefix="/api", tags=["Profiles"], dependencies=[auth_dep])
    # WebSocket endpoint (no auth dep — auth is handled in the WS handshake)
    app.include_router(signals.ws_router, prefix="/ws", tags=["WebSocket"])

    # Serve the bundled frontend at the application root.
    # Prefer dist/ (npm build output); fall back to frontend/ (vanilla).
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
