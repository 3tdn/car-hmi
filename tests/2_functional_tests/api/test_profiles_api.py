"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.devmode_locks import get_seat_lock_registry, reset_seat_lock_registry
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

async def test_profile_create_and_get_with_permission(monkeypatch, tmp_path):
    """POST/GET profile must persist and return the permission field."""
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

async def test_create_second_profile_requires_full_permission(monkeypatch, tmp_path):
    """Creating a new profile after bootstrap requires full permission."""
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

async def test_set_active_profile_success(monkeypatch, tmp_path):
    """PUT /api/profile/active switches the active profile when full permission is present."""
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

async def test_set_active_profile_requires_full_permission(monkeypatch, tmp_path):
    """PUT /api/profile/active is blocked for profiles without full permission."""
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

async def test_set_active_profile_allows_dev_mode_override(monkeypatch, tmp_path):
    """PUT /api/profile/active allows switching profiles when the dev mode header is enabled."""
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

async def test_set_active_profile_tracks_per_client_session(monkeypatch, tmp_path):
    """PUT /api/profile/active with X-Client-Id updates only that client's session."""
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

async def test_list_profile_sessions_returns_client_mapping(monkeypatch, tmp_path):
    """GET /api/profile/sessions returns the client -> active profile mapping."""
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

async def test_profile_heartbeat_updates_last_seen(monkeypatch, tmp_path):
    """POST /api/profile/heartbeat updates last_seen for the client session."""
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

async def test_profile_heartbeat_requires_client_id(monkeypatch, tmp_path):
    """POST /api/profile/heartbeat returns 400 if X-Client-Id is missing."""
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

async def test_profile_offline_marks_session_offline(monkeypatch, tmp_path):
    """POST /api/profile/offline marks the session offline immediately."""
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

async def test_profile_offline_releases_devmode_locks_immediately(monkeypatch, tmp_path):
    """POST /api/profile/offline must release that client's Dev Mode lock immediately."""
    import src.api.routes.profiles as profile_routes

    reset_seat_lock_registry()
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
    await store.update("COM_Status_PumaFLCan", 1.0, timestamp=now)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()

    owner_headers = {"X-API-Key": "test-key", "X-Client-Id": "client-a", "X-Dev-Mode": "true"}
    intruder_headers = {"X-API-Key": "test-key", "X-Client-Id": "client-b", "X-Dev-Mode": "true"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        lock_resp = await c.post(
            "/api/devmode/seats/select",
            headers=owner_headers,
            json={"seats": {"fl": True}, "block_timeout_sec": 120},
        )
        assert lock_resp.status_code == 200

        blocked = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=intruder_headers,
            json={"value": 5},
        )
        assert blocked.status_code == 423

        offline_resp = await c.post(
            "/api/profile/offline",
            headers={"X-API-Key": "test-key", "X-Client-Id": "client-a"},
        )
        assert offline_resp.status_code == 200

        after_offline = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=intruder_headers,
            json={"value": 5},
        )

    assert after_offline.status_code == 202
    reset_seat_lock_registry()

def test_release_devmode_locks_for_offline_sessions(monkeypatch, tmp_path):
    """Cleanup helper only processes owners holding locks and releases offline owners."""
    import src.api.routes.profiles as profile_routes

    reset_seat_lock_registry()
    monkeypatch.setattr(profile_routes, "SESSION_ONLINE_TTL_SECONDS", 30)
    monkeypatch.setattr(profile_routes, "SESSION_HISTORY_LIMIT", 5000)

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
            "client-offline": {"active": "admin", "updated_at": now - 120, "last_seen": now - 120},
            "client-online": {"active": "admin", "updated_at": now, "last_seen": now},
            "client-offline-unrelated": {
                "active": "admin",
                "updated_at": now - 120,
                "last_seen": now - 120,
            },
        },
        sessions_path=sessions_path,
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", sessions_path)

    registry = get_seat_lock_registry()
    registry.acquire("fl", "client-offline", timeout_sec=300, now=now)
    registry.acquire("fr", "client-online", timeout_sec=300, now=now)
    registry.acquire("rl1", "client-offline-unrelated", timeout_sec=300, now=now)

    released_clients = profile_routes.release_devmode_locks_for_offline_sessions(
        now=now,
        owner_ids={"client-offline", "client-online"},
    )

    assert released_clients == ["client-offline"]
    assert registry.lock_for_seat("fl", now=now) is None
    assert registry.lock_for_seat("fr", now=now) is not None
    assert registry.lock_for_seat("rl1", now=now) is not None
    reset_seat_lock_registry()

def test_release_devmode_locks_owner_filter_soak_quantifies_scan_reduction(monkeypatch, tmp_path):
    """Soak test: with N lock owners and M sessions, owner-filter only inspects N sessions."""
    import src.api.routes.profiles as profile_routes

    reset_seat_lock_registry()
    monkeypatch.setattr(profile_routes, "SESSION_ONLINE_TTL_SECONDS", 30)
    monkeypatch.setattr(profile_routes, "SESSION_HISTORY_LIMIT", 5000)

    # Keep N small enough for CI speed, M large enough to show complexity gap.
    owner_count = 5
    session_count = 3000
    now = time.time()

    profiles_path = tmp_path / "profiles.json"
    sessions_path = tmp_path / "profile_sessions.json"

    client_sessions = {
        f"owner-{i}": {
            "active": "admin",
            "updated_at": now - 120,
            "last_seen": now - 120,
        }
        for i in range(owner_count)
    }
    for i in range(session_count - owner_count):
        client_sessions[f"noise-{i}"] = {
            "active": "admin",
            "updated_at": now,
            "last_seen": now,
        }

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

    registry = get_seat_lock_registry()
    seat_pool = ["fl", "fr", "rl1", "rl2", "rr1"]
    for i in range(owner_count):
        registry.acquire(seat_pool[i], f"owner-{i}", timeout_sec=300, now=now + i)

    call_counter = {"count": 0}
    original_session_is_online = profile_routes._session_is_online

    def counted_session_is_online(state, *, now, ttl_seconds=None):
        call_counter["count"] += 1
        return original_session_is_online(state, now=now, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(profile_routes, "_session_is_online", counted_session_is_online)

    call_counter["count"] = 0
    profile_routes.release_devmode_locks_for_offline_sessions(now=now, owner_ids=None)
    full_scan_checks = call_counter["count"]

    # Recreate locks for the filtered-run measurement.
    reset_seat_lock_registry()
    registry = get_seat_lock_registry()
    owner_ids = set()
    for i in range(owner_count):
        owner = f"owner-{i}"
        owner_ids.add(owner)
        registry.acquire(seat_pool[i], owner, timeout_sec=300, now=now + i)

    call_counter["count"] = 0
    profile_routes.release_devmode_locks_for_offline_sessions(now=now, owner_ids=owner_ids)
    filtered_checks = call_counter["count"]

    assert full_scan_checks >= session_count
    assert filtered_checks == owner_count
    assert full_scan_checks / max(1, filtered_checks) >= 100
    reset_seat_lock_registry()

async def test_profile_sessions_offline_trimmed_only_when_over_top_50(monkeypatch, tmp_path):
    """Do not prune by timeout; only trim offline sessions outside the newest top 50."""
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

async def test_profile_sessions_offline_kept_when_within_top_50(monkeypatch, tmp_path):
    """An offline session is still kept if total sessions have not exceeded the history retention threshold."""
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

async def test_get_profile_without_name_uses_client_session(monkeypatch, tmp_path):
    """GET /api/profile prefers the profile from the client-id session."""
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

async def test_set_active_profile_not_found(monkeypatch, tmp_path):
    """PUT /api/profile/active returns 404 if the target profile does not exist."""
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

async def test_delete_profile_removes_profile_and_matching_sessions(monkeypatch, tmp_path):
    """DELETE /api/profile/{name} removes the profile and any sessions pointing to it."""
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
