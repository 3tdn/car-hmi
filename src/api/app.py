"""Factory tạo ứng dụng FastAPI."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.auth import APIKeyAuth
from src.api.routes import alarms, config, signals, system
from src.api.websocket import ConnectionManager

logger = logging.getLogger(__name__)


def create_app(
    signal_store,  # SignalStore
    repository,  # ISignalRepository
    can_reader=None,  # CANReader | None
    api_key: str = "",
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Xây dựng và cấu hình ứng dụng FastAPI."""
    app = FastAPI(
        title="CAN-HMI Signal API",
        version="0.1.0",
        description="Real-time CAN bus signal monitoring and control API",
    )

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
    app.state.reader = can_reader
    app.state.start_time = time.time()
    app.state.ws_manager = ConnectionManager()

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
    app.include_router(system.router, prefix="/system", tags=["System"])
    # Điểm cuối WebSocket (không có auth dep — auth xử lý trong handshake ws)
    app.include_router(signals.ws_router, prefix="/ws", tags=["WebSocket"])

    # Phục vụ frontend tích hợp sẵn (nếu có) tại gốc ứng dụng
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        logger.info("Serving frontend from %s", frontend_dir)
    else:
        logger.debug("Frontend directory not found at %s", frontend_dir)

    return app
