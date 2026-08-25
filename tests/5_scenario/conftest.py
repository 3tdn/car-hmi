"""Shared fixtures/helpers for end-to-end scenario tests.

Scenario tests trong `tests/5_scenario/` mô phỏng luồng nghiệp vụ thật (nhiều
request nối tiếp nhau) thay vì kiểm tra 1 endpoint đơn lẻ. Các helper dưới đây
được tái dùng giữa các file test_scenario_*.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.api.app import create_app
from src.core.devmode_locks import reset_seat_lock_registry
from src.core.signal_store import SignalStore


class FakeRepo:
    """Repository giả — scenario test không cần lưu SQLite thật."""

    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []

    async def get_alarm_by_id(self, _alarm_id):
        return None

    async def insert_signal(self, _record):
        pass

    async def insert_signals_bulk(self, _records):
        pass

    async def insert_alarm(self, _alarm):
        return 1

    async def acknowledge_alarm(self, _alarm_id):
        return True

    async def resolve_alarm(self, _alarm_id):
        return True

    async def delete_old_signals(self, _older_than):
        return 0

    async def get_signal_config(self, _signal_name):
        return None

    async def upsert_signal_config(self, _record):
        pass


class FakeWriter:
    """CAN writer giả — ghi nhận mọi lần ghi để assert lại trong test."""

    def __init__(self):
        self.writes: list[tuple[str, float]] = []
        self.unavailable_signals: set[str] = set()

    async def send_signal(self, signal_name, value):
        if signal_name in self.unavailable_signals:
            raise ValueError(f"Signal '{signal_name}' not found")
        self.writes.append((signal_name, value))

    async def send_signals_batch(self, values):
        errors = []
        sent = {}
        for signal_name, value in values.items():
            if signal_name in self.unavailable_signals:
                errors.append({"signal_name": signal_name, "error": "signal_not_available"})
                continue
            sent[signal_name] = value
            self.writes.append((signal_name, value))
        return sent, errors


def write_profiles_file(
    path: Path,
    *,
    active: str,
    profiles: dict,
    client_sessions: dict | None = None,
    sessions_path: Path | None = None,
) -> None:
    """Ghi file profiles.json (+ profile_sessions.json tuỳ chọn) cho scenario test."""
    payload = {"active": active, "profiles": profiles}
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sessions_path is not None:
        sessions_payload = {"client_sessions": client_sessions or {}}
        sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")


async def build_app(
    monkeypatch,
    tmp_path: Path,
    *,
    active: str,
    profiles: dict,
    api_key: str = "test-key",
    initial_signals: dict[str, float] | None = None,
    unavailable_signals: set[str] | None = None,
):
    """Dựng FastAPI app dùng profiles.json/profile_sessions.json tạm trong tmp_path.

    Trả về tuple (app, writer) để test vừa gọi API vừa assert side-effect ghi CAN.
    """
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    write_profiles_file(
        profiles_path,
        active=active,
        profiles=profiles,
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    now = time.time()
    for name, value in (initial_signals or {}).items():
        await store.update(name, value, timestamp=now)

    app = create_app(store, FakeRepo(), api_key=api_key)
    writer = FakeWriter()
    writer.unavailable_signals = unavailable_signals or set()
    app.state.writer = writer
    return app, writer


@pytest.fixture(autouse=True)
def _clean_seat_lock_registry():
    """Mỗi scenario test bắt đầu/kết thúc với devmode lock registry sạch."""
    reset_seat_lock_registry()
    yield
    reset_seat_lock_registry()


@pytest.fixture
def app_builder():
    """Trả về hàm async `build_app` để test tự gọi với (monkeypatch, tmp_path, ...).

    Đóng gói dưới dạng fixture (thay vì import trực tiếp) vì thư mục
    `5_scenario` không phải tên package hợp lệ để import bằng dotted path.
    """
    return build_app


@pytest.fixture
def app_builder_sync(monkeypatch, tmp_path):
    """Biến thể đồng bộ của `build_app`, dùng cho test WebSocket qua `TestClient`.

    `starlette.testclient.TestClient` chạy trong một event loop riêng nên các
    test WS cần app đã dựng xong (sync) thay vì phải `await` trong test.
    """
    import asyncio

    def _build(**kwargs):
        return asyncio.run(build_app(monkeypatch, tmp_path, **kwargs))[0]

    return _build
