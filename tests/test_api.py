"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []

    async def get_alarm_by_id(self, alarm_id):
        import time

        from src.storage.repository import AlarmRecord

        if alarm_id == 1:
            return AlarmRecord(
                id=1,
                signal_name="CoolantTemp",
                level="critical",
                value=110.0,
                threshold=100.0,
                description="over temp",
                triggered_at=time.time(),
                acknowledged=False,
                resolved_at=None,
            )
        return None

    async def insert_signal(self, r):
        pass

    async def insert_signals_bulk(self, records):
        pass

    async def insert_alarm(self, a):
        return 1

    async def acknowledge_alarm(self, i):
        return True

    async def resolve_alarm(self, i):
        return True

    async def delete_old_signals(self, o):
        return 0

    async def get_signal_config(self, signal_name):
        return None

    async def upsert_signal_config(self, record):
        pass


class _FakeReader:
    def __init__(self, *, thread_alive: bool, last_frame_timestamp: float, fatal_error: str | None = None):
        self._state = {
            "thread_alive": thread_alive,
            "last_frame_timestamp": last_frame_timestamp,
            "fatal_error": fatal_error,
        }

    def get_runtime_state(self):
        return dict(self._state)


class _FakeWriter:
    def __init__(self):
        self.writes: list[tuple[str, float]] = []

    async def send_signal(self, signal_name, value):
        self.writes.append((signal_name, value))

    async def send_signals_batch(self, values):
        for signal_name, value in values.items():
            self.writes.append((signal_name, value))
        return values, []


def _write_profiles(path, *, active, profiles, client_sessions=None, sessions_path=None):
    payload = {
        "active": active,
        "profiles": profiles,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sessions_path is not None:
        sessions_payload = {"client_sessions": client_sessions or {}}
        sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_signals_no_auth(client):
    resp = await client.get("/signals")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_signals_with_auth(client):
    resp = await client.get("/signals", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    # Với profile-based access mới, request có thể bị lọc theo scope profile.
    # Mặc định fixture không gửi X-Profile-Name nên total hiện tại = 0.
    assert data["total"] == 0
    assert data["items"] == []
    assert isinstance(data.get("warnings", []), list)


@pytest.mark.asyncio
async def test_get_signal_not_found(client):
    resp = await client.get("/signals/Unknown", headers={"X-API-Key": "test-key"})
    # Quyền profile được kiểm tra trước signal existence => có thể 403 thay vì 404.
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] in {"profile_not_selected", "profile_signal_denied"}


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_ready_endpoint(client):
    resp = await client.get("/system/ready")
    assert resp.status_code == 200
    assert "ready" in resp.json()


@pytest.mark.asyncio
async def test_health_endpoint_error_on_reader_fatal():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    now = time.time()
    app = create_app(
        store,
        _FakeRepo(),
        can_readers=[_FakeReader(thread_alive=False, last_frame_timestamp=now - 120.0, fatal_error="reconnect_failed")],
        api_key="",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_ready_false_when_reader_frames_stale():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    now = time.time()
    app = create_app(
        store,
        _FakeRepo(),
        can_readers=[_FakeReader(thread_alive=True, last_frame_timestamp=now - 120.0, fatal_error=None)],
        api_key="",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/system/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["details"]["readers_recent_frames"] is False


@pytest.mark.asyncio
async def test_list_alarms(client):
    resp = await client.get("/alarms", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_alarm_by_id(client):
    resp = await client.get("/alarms/1", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["signal_name"] == "CoolantTemp"

    resp_not_found = await client.get("/alarms/99", headers={"X-API-Key": "test-key"})
    assert resp_not_found.status_code == 404
    detail = resp_not_found.json()["detail"]
    assert detail["code"] == "alarm_not_found"
    assert detail["alarm_id"] == 99


# ── System Metrics tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_metrics_endpoint(client):
    """GET /system/metrics trả về JSON đầy đủ thông tin tài nguyên."""
    resp = await client.get("/system/metrics")
    assert resp.status_code == 200
    data = resp.json()

    # CPU fields
    assert "cpu_percent" in data
    assert isinstance(data["cpu_percent"], (int, float))
    assert "cpu_percent_per_core" in data
    assert isinstance(data["cpu_percent_per_core"], list)
    assert data["cpu_count_logical"] > 0

    # RAM fields
    assert data["ram_total_mb"] > 0
    assert "ram_percent" in data

    # Process fields
    assert data["process_pid"] > 0
    assert data["process_memory_rss_mb"] >= 0

    # Disk fields
    assert data["disk_total_gb"] > 0

    # Application-specific
    assert "queue_size" in data
    assert "queue_maxsize" in data
    assert "heap_allocated_mb" in data
    assert "gc_objects" in data
    assert "asyncio_tasks" in data
    assert "python_version" in data
    assert "platform" in data
    assert data["timestamp"] > 0


@pytest.mark.asyncio
async def test_system_metrics_cpu_cores(client):
    """cpu_percent_per_core phải có đúng số phần tử = cpu_count_logical."""
    resp = await client.get("/system/metrics")
    data = resp.json()
    assert len(data["cpu_percent_per_core"]) == data["cpu_count_logical"]


@pytest.mark.asyncio
async def test_system_metrics_no_auth_required(client):
    """System metrics endpoint không yêu cầu auth (giống /health)."""
    resp = await client.get("/system/metrics")
    assert resp.status_code == 200


# ── Available signals endpoint tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_available_signals_requires_auth(client):
    resp = await client.get("/signals/available")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_available_signals_returns_metadata(client):
    """GET /signals/available trả về metadata đầy đủ cho mỗi signal."""
    resp = await client.get("/signals/available", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "signals_info" in data
    assert "total" in data
    assert data["total"] >= 1
    # VehicleSpeed should be present from the fixture
    names = [item["signal_name"] for item in data["signals_info"]]
    assert "VehicleSpeed" in names
    sample = data["signals_info"][0]
    # Metadata fields should exist (even if None)
    assert "unit" in sample
    assert "writable" in sample
    assert "alarm_warning_high" in sample


# ── Profile endpoint tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_create_and_get_with_permission(monkeypatch, tmp_path):
    """POST/GET profile phải lưu và trả về trường permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        create_resp = await c.post(
            "/api/profile",
            headers={"X-API-Key": "test-key"},
            json={
                "name": "driver",
                "signals": [{"name": "VehicleSpeed", "permission": ["read", "write"]}, {"name": "FuelLevel", "permission": ["read", "write"]}],
                "description": "Driver view",
            },
        )

        assert create_resp.status_code == 201
        created = create_resp.json()
        created_map = {s["name"]: sorted(s["permission"]) for s in created["signals"]}
        assert created_map["VehicleSpeed"] == ["read", "write"]
        assert created_map["FuelLevel"] == ["read", "write"]

        get_resp = await c.get("/api/profile?name=driver", headers={"X-API-Key": "test-key"})

    assert get_resp.status_code == 200
    fetched = get_resp.json()
    fetched_map = {s["name"]: sorted(s["permission"]) for s in fetched["signals"]}
    assert fetched_map["VehicleSpeed"] == ["read", "write"]
    assert fetched_map["FuelLevel"] == ["read", "write"]

    saved = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert "client_sessions" not in saved
    saved_map = {s["name"]: sorted(s["permission"]) for s in saved["profiles"]["driver"]["signals"]}
    assert saved_map["VehicleSpeed"] == ["read", "write"]
    assert saved_map["FuelLevel"] == ["read", "write"]


@pytest.mark.asyncio
async def test_create_second_profile_requires_full_permission(monkeypatch, tmp_path):
    """Tạo profile mới sau bootstrap phải có full permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/profile",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={
                "name": "editor",
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Editor",
            },
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_active_profile_success(monkeypatch, tmp_path):
    """PUT /api/profile/active đổi active profile khi có full permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}, {"name": "FuelLevel", "permission": ["full"]}],
                "description": "Admin",
            },
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            },
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
            json={"name": "operator"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] == "operator"
    assert data["warnings"] == []

    saved = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert saved["active"] == "operator"


@pytest.mark.asyncio
async def test_set_active_profile_requires_full_permission(monkeypatch, tmp_path):
    """PUT /api/profile/active bị chặn với profile thiếu full permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            },
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={"name": "operator"},
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "profile_permission_denied"
    assert detail["required_permission"] == "full"


@pytest.mark.asyncio
async def test_set_active_profile_allows_dev_mode_override(monkeypatch, tmp_path):
    """PUT /api/profile/active cho phép đổi profile khi bật dev mode header."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            },
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={
                "X-API-Key": "test-key",
                "X-Profile-Name": "viewer",
                "X-Dev-Mode": "true",
            },
            json={"name": "operator"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] == "operator"


@pytest.mark.asyncio
async def test_set_active_profile_tracks_per_client_session(monkeypatch, tmp_path):
    """PUT /api/profile/active với X-Client-Id chỉ cập nhật session của client đó."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            },
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            },
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={
                "X-API-Key": "test-key",
                "X-Profile-Name": "admin",
                "X-Client-Id": "client-a",
            },
            json={"name": "operator"},
        )

        list_for_client = await c.get(
            "/api/profiles",
            headers={"X-API-Key": "test-key", "X-Client-Id": "client-a"},
        )
        list_global = await c.get("/api/profiles", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] == "operator"
    assert body["client_id"] == "client-a"
    assert body["global_active"] == "admin"

    assert list_for_client.status_code == 200
    assert list_for_client.json()["active"] == "operator"

    assert list_global.status_code == 200
    assert list_global.json()["active"] == "admin"


@pytest.mark.asyncio
async def test_list_profile_sessions_returns_client_mapping(monkeypatch, tmp_path):
    """GET /api/profile/sessions trả map client -> active profile."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    now = time.time()
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            },
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
        },
        client_sessions={
            "client-a": {"active": "viewer", "updated_at": now},
            "client-b": {"active": "admin", "updated_at": now - 10},
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(
            "/api/profile/sessions",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["online_total"] == 2
    assert data["offline_total"] == 0
    assert data["sessions"][0]["client_id"] == "client-a"
    assert data["sessions"][0]["active"] == "viewer"
    assert data["sessions"][0]["status"] == "online"
    assert "last_seen" in data["sessions"][0]
    assert data["ttl_seconds"] > 0
    by_profile = {item["profile_name"]: item for item in data["by_profile"]}
    assert by_profile["viewer"]["total"] == 1
    assert by_profile["viewer"]["online"] == 1
    assert by_profile["viewer"]["offline"] == 0
    assert by_profile["admin"]["total"] == 1


@pytest.mark.asyncio
async def test_profile_heartbeat_updates_last_seen(monkeypatch, tmp_path):
    """POST /api/profile/heartbeat cập nhật last_seen cho client session."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
        client_sessions={
            "client-a": {"active": "admin", "updated_at": time.time() - 100, "last_seen": time.time() - 100}
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/profile/heartbeat",
            headers={"X-API-Key": "test-key", "X-Client-Id": "client-a"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["client_id"] == "client-a"
    assert data["active"] == "admin"
    assert data["last_seen"] > (time.time() - 10)


@pytest.mark.asyncio
async def test_profile_heartbeat_requires_client_id(monkeypatch, tmp_path):
    """POST /api/profile/heartbeat trả 400 nếu thiếu X-Client-Id."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/profile/heartbeat", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "client_id_required"


@pytest.mark.asyncio
async def test_profile_offline_marks_session_offline(monkeypatch, tmp_path):
    """POST /api/profile/offline đánh dấu session offline ngay."""
    import src.api.routes.profiles as profile_routes

    monkeypatch.setattr(profile_routes, "SESSION_ONLINE_TTL_SECONDS", 30)

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    now = time.time()
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
        client_sessions={
            "client-a": {"active": "admin", "updated_at": now, "last_seen": now}
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/profile/offline",
            headers={"X-API-Key": "test-key", "X-Client-Id": "client-a"},
        )

        sessions_resp = await c.get(
            "/api/profile/sessions",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )

    assert resp.status_code == 200
    offline_payload = resp.json()
    assert offline_payload["client_id"] == "client-a"

    assert sessions_resp.status_code == 200
    sessions_data = sessions_resp.json()
    by_client = {item["client_id"]: item for item in sessions_data["sessions"]}
    assert by_client["client-a"]["status"] == "offline"


@pytest.mark.asyncio
async def test_profile_sessions_offline_trimmed_only_when_over_top_50(monkeypatch, tmp_path):
    """Không prune theo timeout; chỉ trim session offline nằm ngoài top 50 mới nhất."""
    import src.api.routes.profiles as profile_routes

    monkeypatch.setattr(profile_routes, "SESSION_ONLINE_TTL_SECONDS", 5)
    monkeypatch.setattr(profile_routes, "SESSION_HISTORY_LIMIT", 50)

    now = time.time()
    sessions_path = tmp_path / "profile_sessions.json"
    client_sessions = {
        "fresh-client": {"active": "admin", "updated_at": now, "last_seen": now},
        "offline-client": {"active": "admin", "updated_at": now - 6, "last_seen": now - 6},
    }
    # Add 50 newer sessions so offline-client is pushed out of top-50.
    for i in range(50):
        ts = now - (i * 0.1)
        client_sessions[f"recent-{i}"] = {"active": "admin", "updated_at": ts, "last_seen": ts}

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
        client_sessions=client_sessions,
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(
            "/api/profile/sessions",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 51
    assert payload["online_total"] == 51
    assert payload["offline_total"] == 0
    by_client = {item["client_id"]: item for item in payload["sessions"]}
    assert by_client["fresh-client"]["status"] == "online"
    assert "offline-client" not in by_client


@pytest.mark.asyncio
async def test_profile_sessions_offline_kept_when_within_top_50(monkeypatch, tmp_path):
    """Session offline vẫn được giữ nếu tổng session chưa vượt ngưỡng lưu lịch sử."""
    import src.api.routes.profiles as profile_routes

    monkeypatch.setattr(profile_routes, "SESSION_ONLINE_TTL_SECONDS", 5)
    monkeypatch.setattr(profile_routes, "SESSION_HISTORY_LIMIT", 50)

    now = time.time()
    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
        client_sessions={
            "fresh-client": {"active": "admin", "updated_at": now, "last_seen": now},
            "offline-client": {"active": "admin", "updated_at": now - 6, "last_seen": now - 6},
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(
            "/api/profile/sessions",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["online_total"] == 1
    assert payload["offline_total"] == 1
    by_client = {item["client_id"]: item for item in payload["sessions"]}
    assert by_client["offline-client"]["status"] == "offline"
    by_profile = {item["profile_name"]: item for item in payload["by_profile"]}
    assert by_profile["admin"]["total"] == 2
    assert by_profile["admin"]["online"] == 1
    assert by_profile["admin"]["offline"] == 1


@pytest.mark.asyncio
async def test_get_profile_without_name_uses_client_session(monkeypatch, tmp_path):
    """GET /api/profile ưu tiên profile theo session của client-id."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            },
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
        },
        client_sessions={
            "client-a": {"active": "viewer", "updated_at": time.time()},
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(
            "/api/profile",
            headers={"X-API-Key": "test-key", "X-Client-Id": "client-a"},
        )

    assert resp.status_code == 200
    assert resp.json()["name"] == "viewer"


@pytest.mark.asyncio
async def test_set_active_profile_not_found(monkeypatch, tmp_path):
    """PUT /api/profile/active trả 404 nếu profile đích không tồn tại."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
            json={"name": "missing-profile"},
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "profile_not_found"
    assert detail["profile_name"] == "missing-profile"


@pytest.mark.asyncio
async def test_delete_profile_removes_profile_and_matching_sessions(monkeypatch, tmp_path):
    """DELETE /api/profile/{name} xóa profile và session đang trỏ tới profile đó."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            },
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
        },
        client_sessions={
            "client-a": {"active": "viewer", "updated_at": time.time(), "last_seen": time.time()},
            "client-b": {"active": "admin", "updated_at": time.time(), "last_seen": time.time()},
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.delete(
            "/api/profile/viewer",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )

    assert resp.status_code == 204
    saved_profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert "viewer" not in saved_profiles["profiles"]
    assert saved_profiles["active"] == "admin"
    saved_sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "client-a" not in saved_sessions["client_sessions"]
    assert "client-b" in saved_sessions["client_sessions"]


@pytest.mark.asyncio
async def test_write_signal_requires_write_permission(monkeypatch, tmp_path):
    """Signal write bị chặn với profile chỉ có read permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={"value": 77.0},
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "profile_permission_denied"
    assert detail["required_permission"] == "write"
    assert detail["profile_name"] == "viewer"


@pytest.mark.asyncio
async def test_write_signal_allows_write_permission(monkeypatch, tmp_path):
    """Signal write được phép với profile có write permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 77.0},
        )

    assert resp.status_code == 202
    assert app.state.writer.writes == [("VehicleSpeed", 77.0)]


@pytest.mark.asyncio
async def test_batch_write_filters_signals_outside_profile_scope(monkeypatch, tmp_path):
    """Batch write chỉ queue signal hợp lệ và trả warnings cho phần bị bỏ qua."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={
                "signals": [
                    {"signal_name": "VehicleSpeed", "value": 80.0},
                    {"signal_name": "FuelLevel", "value": 25.0},
                ]
            },
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["queued"] == [{"signal_name": "VehicleSpeed", "value": 80.0}]
    assert data["warnings"][0]["code"] == "profile_signal_filtered"
    assert data["warnings"][0]["signals"] == ["FuelLevel"]


def test_ws_subscribe_ack_warns_for_signal_outside_profile(monkeypatch, tmp_path):
    """Subscribe ack trả warnings khi client yêu cầu signal ngoài profile scope."""
    import src.api.routes.profiles as profile_routes
    from starlette.testclient import TestClient

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals?api_key=ws-secret&profile_name=viewer") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["FuelLevel"]}))
            ack = json.loads(ws.receive_text())

    assert ack["type"] == "subscribe_ack"
    assert ack["channels"] == []
    assert ack["warnings"][0]["code"] == "profile_signal_denied"


# ── APIKeyAuth.verify() unit tests ──────────────────────────────────────────


def test_api_key_auth_verify_valid():
    """verify() returns True for correct key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("my-secret") is True


def test_api_key_auth_verify_invalid():
    """verify() returns False for wrong or missing key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("wrong") is False
    assert auth.verify(None) is False
    assert auth.verify("") is False


def test_api_key_auth_verify_disabled():
    """verify() always returns True when auth is disabled (empty key)."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("")
    assert auth.verify(None) is True
    assert auth.verify("anything") is True


# ── Config reset endpoint test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_general_config(monkeypatch, tmp_path):
    """POST /config/general/reset calls write_default_bus and returns ok + default dict."""
    import src.core.config_manager as cm
    import src.api.routes.profiles as profile_routes

    default_payload = {"can": [{"interface": "virtual", "channel": "vcan0"}], "api": {}}
    monkeypatch.setattr(cm, "write_default_bus", lambda path=None: default_payload)

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/config/general/reset",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "default" in data
    assert data["default"] == default_payload


@pytest.mark.asyncio
async def test_get_signal_config_not_found_returns_structured_error(client):
    resp = await client.get("/config/signal/Unknown", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "signal_config_not_found"
    assert detail["signal_name"] == "Unknown"


# ── WebSocket auth tests ─────────────────────────────────────────────────────


def test_ws_auth_rejected_without_key():
    """WebSocket connection is rejected when auth is enabled and no key is provided."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app, raise_server_exceptions=False) as sc:
        with pytest.raises(Exception):
            with sc.websocket_connect("/ws/signals") as ws:
                ws.receive_text()


def test_ws_auth_accepted_with_valid_key():
    """WebSocket connection succeeds with valid API key in query string."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals?api_key=ws-secret") as ws:
            pass  # connection established — no exception raised


def test_ws_no_auth_when_disabled():
    """WebSocket connects freely when auth is disabled (empty api_key)."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals") as ws:
            pass  # should connect without any key


def test_ws_subscribe_signal_payload_format():
    """WS signal frame uses timestamp + signals[{name,std_name,value}] format."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    mgr = app.state.ws_manager

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribe_ack"

            import asyncio

            asyncio.run(mgr.broadcast_signal("VehicleSpeed", 23.0, 1717243200.123))
            frame = json.loads(ws.receive_text())

            assert "timestamp" in frame
            assert isinstance(frame.get("signals"), list)
            assert len(frame["signals"]) == 1
            sig = frame["signals"][0]
            assert set(sig.keys()) == {"name", "std_name", "value"}
            assert sig["name"] == "VehicleSpeed"
            assert sig["std_name"] == "VehicleSpeed"
            assert sig["value"] == 23.0


