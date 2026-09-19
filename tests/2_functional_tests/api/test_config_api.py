"""Integration tests for signal and system configuration APIs."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.config import AppConfig
from src.core.config_manager import SystemConfigManager, write_config
from src.core.config_policy import (
    ConfigFieldPolicyError,
    diff_paths,
    load_field_policies,
    match_policy,
    validate_policy_values,
)
from src.core.paths import DEFAULT_CONFIG_FIELDS_PATH, DEFAULT_CONFIG_PATH
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def insert_signal(self, _record):
        pass

    async def insert_signals_bulk(self, _records):
        pass

    async def delete_old_signals(self, _older_than):
        return 0

    async def get_signal_config(self, _signal_name):
        return None

    async def upsert_signal_config(self, _record):
        pass


class _FakeRunner:
    def __init__(self, config: dict):
        self.config = AppConfig.model_validate(config)
        self.applied: list[list[str]] = []

    async def apply_system_config(self, new_config: AppConfig, changed_paths: list[str]):
        self.config = new_config
        self.applied.append(changed_paths)
        return {
            "applied": [
                path
                for path in changed_paths
                if path.startswith(("reader.", "processor.", "writer.", "storage."))
            ],
            "unavailable": [],
        }


class _FailOnceRunner(_FakeRunner):
    async def apply_system_config(self, new_config: AppConfig, changed_paths: list[str]):
        self.config = new_config
        self.applied.append(changed_paths)
        if len(self.applied) == 1:
            raise RuntimeError("simulated runtime apply failure")
        return {"applied": changed_paths, "unavailable": []}


def _write_profiles(path, *, active, profiles):
    path.write_text(json.dumps({"active": active, "profiles": profiles}), encoding="utf-8")


@pytest_asyncio.fixture
async def config_client(tmp_path, monkeypatch):
    """Every config mutation is isolated under pytest's ``tmp_path``."""
    import src.api.routes.profiles as profile_routes

    source = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    source["can"][0].update(interface="virtual", channel="vcan0", channel_tracking_signals=[])
    source["future_extension"] = {"must_survive": 42}
    config_path = tmp_path / "config" / "system.json"
    template_path = tmp_path / "config" / "system_bk.json"
    backup_dir = tmp_path / "config" / "backups"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(source), encoding="utf-8")
    template = deepcopy(source)
    template["reader"]["stale_threshold_sec"] = 12.0
    template_path.write_text(json.dumps(template), encoding="utf-8")
    manager = SystemConfigManager(config_path, template_path, backup_dir)

    profiles_path = tmp_path / "profiles.json"
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
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(
        store,
        _FakeRepo(),
        api_key="test-key",
        system_config_manager=manager,
    )
    runner = _FakeRunner(source)
    app.state.runner = runner
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, manager, runner


def _headers(profile: str = "admin") -> dict[str, str]:
    return {"X-API-Key": "test-key", "X-Profile-Name": profile}


@pytest.mark.asyncio
async def test_get_system_config_returns_policy_and_redacts_secret(config_client):
    client, _, _ = config_client
    response = await client.get("/config/system", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["config"]["api"]["api_key"] == "********"
    policies = {item["path"]: item for item in payload["fields"]}
    assert payload["fields_schema_version"] == 1
    assert policies["reader.stale_threshold_sec"]["reload_level"] == "live"
    assert policies["can.*.channel"]["reload_level"] == "reboot"
    assert policies["api.api_key"]["editable"] is False
    assert policies["can.*.can_db_file"]["type"] == "string"
    assert policies["can.*.can_db_file"]["validation"]["must_parse_as"] == "dbc"
    assert policies["can.*.can_db_file"]["ui"]["control"] == "file-path"
    assert payload["paths"]["field_definitions"] == "config/system.fields.json"
    assert payload["paths"]["backup_directory"] == "config/backups"


def test_system_field_policy_file_is_valid_and_has_unique_paths():
    policies = load_field_policies(DEFAULT_CONFIG_FIELDS_PATH)

    paths = [policy.path for policy in policies]
    assert len(paths) == len(set(paths))
    assert "can.*.can_db_file" in paths

    raw = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    leaf_paths = diff_paths({}, raw)
    assert [path for path in leaf_paths if match_policy(path) is None] == []
    assert validate_policy_values(raw, leaf_paths) == []
    assert raw["profiles"]["session_cleanup_interval_sec"] == pytest.approx(5.0)


def test_system_field_policy_loader_rejects_duplicate_paths(tmp_path):
    fields_path = tmp_path / "system.fields.json"
    field = {
        "path": "reader.stale_threshold_sec",
        "title": "Stale threshold",
        "type": "number",
        "reload_level": "live",
        "description": "Test field.",
        "ui": {"control": "number"},
    }
    fields_path.write_text(
        json.dumps({"schema_version": 1, "fields": [field, field]}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigFieldPolicyError, match="Duplicate field policy path"):
        load_field_policies(fields_path)


def test_system_field_policy_loader_rejects_unknown_validation_key(tmp_path):
    fields_path = tmp_path / "system.fields.json"
    fields_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fields": [
                    {
                        "path": "reader.stale_threshold_sec",
                        "title": "Stale threshold",
                        "type": "number",
                        "reload_level": "live",
                        "description": "Test field.",
                        "validation": {"allow_value": []},
                        "ui": {"control": "number"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigFieldPolicyError, match="unsupported key"):
        load_field_policies(fields_path)


@pytest.mark.asyncio
async def test_reload_endpoint_applies_external_live_config_change(config_client):
    client, manager, runner = config_client
    external = manager.read()
    new_threshold = external["reader"]["stale_threshold_sec"] + 1.0
    external["reader"]["stale_threshold_sec"] = new_threshold
    write_config(external, manager.config_path)

    response = await client.post("/config/system/reload", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["reload"]["live"] == ["reader.stale_threshold_sec"]
    assert payload["runtime"]["applied"] == ["reader.stale_threshold_sec"]
    assert runner.config.reader.stale_threshold_sec == pytest.approx(new_threshold)
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_reload_endpoint_reports_unavailable_runner(config_client):
    client, manager, _ = config_client
    client._transport.app.state.runner = None

    response = await client.post("/config/system/reload", headers=_headers())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_runtime_unavailable"
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_patch_is_partial_preserves_unknown_fields_and_live_reloads(config_client):
    client, manager, runner = config_client
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": 7.5}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reload"]["live"] == ["reader.stale_threshold_sec"]
    assert payload["reboot_required"] is False
    assert runner.applied == [["reader.stale_threshold_sec"]]
    saved = manager.read()
    assert saved["reader"]["stale_threshold_sec"] == 7.5
    assert saved["future_extension"] == {"must_survive": 42}
    assert saved["camera"]["stream_url"]
    assert len(manager.list_backups()) == 1


@pytest.mark.asyncio
async def test_patch_rolls_back_disk_and_runtime_when_live_apply_fails(config_client):
    client, manager, _ = config_client
    before = manager.read()
    runner = _FailOnceRunner(before)
    client._transport.app.state.runner = runner

    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": 7.75}},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "system_config_runtime_apply_failed"
    assert manager.read() == before
    assert runner.config.reader.stale_threshold_sec == pytest.approx(
        before["reader"]["stale_threshold_sec"]
    )
    assert runner.applied == [
        ["reader.stale_threshold_sec"],
        ["reader.stale_threshold_sec"],
    ]
    assert len(manager.list_backups()) == 1


@pytest.mark.asyncio
async def test_general_aliases_keep_legacy_response_shapes(config_client):
    client, manager, _ = config_client

    patch_response = await client.patch(
        "/config/general",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": 7.25}},
    )
    reset_response = await client.post("/config/general/reset", headers=_headers())

    assert patch_response.status_code == 200
    assert patch_response.json()["reader"]["stale_threshold_sec"] == pytest.approx(7.25)
    assert "config" not in patch_response.json()
    assert reset_response.status_code == 200
    assert reset_response.json()["ok"] is True
    assert reset_response.json()["default"]["reader"]["stale_threshold_sec"] == pytest.approx(
        12.0
    )
    assert manager.read()["reader"]["stale_threshold_sec"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_patch_supports_multiple_can_channels_and_requires_reboot(config_client):
    client, manager, _ = config_client
    current_bus = manager.read()["can"][0]
    second_bus = {
        **current_bus,
        "channel": "vcan1",
        "bitrate": 250000,
    }
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"can": [current_bus, second_bus]},
    )

    assert response.status_code == 200
    assert set(response.json()["reload"]["reboot"]) == {
        "can.1.bitrate",
        "can.1.can_db_file",
        "can.1.channel",
        "can.1.interface",
        "can.1.channel_tracking_signals",
    }
    assert response.json()["reboot_required"] is True
    assert [item["channel"] for item in manager.read()["can"]] == ["vcan0", "vcan1"]


@pytest.mark.asyncio
async def test_patch_channel_tracking_signals_requires_reboot(config_client):
    client, manager, _ = config_client
    channel = {**manager.read()["can"][0], "channel_tracking_signals": ["COM_Status_ElkCan"]}
    response = await client.patch("/config/system", headers=_headers(), json={"can": [channel]})

    assert response.status_code == 200
    assert response.json()["reload"]["reboot"] == ["can.0.channel_tracking_signals.0"]
    assert manager.read()["can"][0]["channel_tracking_signals"] == ["COM_Status_ElkCan"]


@pytest.mark.asyncio
async def test_patch_rejects_missing_dbc_from_field_validation(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()

    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"can": [{**manager.read()["can"][0], "can_db_file": "missing.dbc"}]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_field_validation_failed"
    assert manager.config_path.read_bytes() == before
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_patch_rejects_coerced_scalar_type_from_field_validation(config_client):
    client, manager, _ = config_client

    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": "7.5"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_field_validation_failed"
    assert manager.read()["reader"]["stale_threshold_sec"] != "7.5"


@pytest.mark.asyncio
async def test_adding_can_channel_rejects_unsupported_child_fields(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    second_bus = {
        **manager.read()["can"][0],
        "channel": "vcan1",
        "unsupported_child": True,
    }

    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"can": [manager.read()["can"][0], second_bus]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_field_unsupported"
    assert manager.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_patch_supports_reboot_level_list_values(config_client):
    client, manager, _ = config_client
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"api": {"cors_origins": ["http://localhost:8000", "http://hmi.local"]}},
    )

    assert response.status_code == 200
    assert response.json()["reload"]["reboot"] == ["api.cors_origins.1"]
    assert manager.read()["api"]["cors_origins"][-1] == "http://hmi.local"


@pytest.mark.asyncio
async def test_patch_rejects_immutable_field_without_writing(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"storage": {"sqlite_path": "data/other.db"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_field_immutable"
    assert manager.config_path.read_bytes() == before
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_patch_requires_same_full_permission_as_profile_mutation(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    response = await client.patch(
        "/config/system",
        headers=_headers("viewer"),
        json={"reader": {"stale_threshold_sec": 8.0}},
    )

    assert response.status_code == 403
    assert manager.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_backup_reset_and_restore_stay_inside_tmp_path(config_client):
    client, manager, _ = config_client
    real_config_before = DEFAULT_CONFIG_PATH.read_bytes()
    initial = manager.read()
    backup_response = await client.post("/config/system/backups", headers=_headers())
    backup_id = backup_response.json()["backup"]["id"]

    await client.patch(
        "/config/system",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": 99.0}},
    )
    reset_response = await client.post("/config/system/reset", headers=_headers())
    assert reset_response.status_code == 200
    assert manager.read()["reader"]["stale_threshold_sec"] == 12.0

    restore_response = await client.post(
        f"/config/system/backups/{backup_id}/restore", headers=_headers()
    )
    assert restore_response.status_code == 200
    assert manager.read() == initial
    assert all(path.parent == manager.backup_dir for path in manager.backup_dir.glob("*.json"))
    assert DEFAULT_CONFIG_PATH.read_bytes() == real_config_before


@pytest.mark.asyncio
async def test_backup_retention_prunes_oldest_files(config_client):
    client, manager, _ = config_client
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"config_management": {"backup_retention_count": 2}},
    )
    assert response.status_code == 200

    manual_ids = []
    for _ in range(3):
        response = await client.post("/config/system/backups", headers=_headers())
        assert response.status_code == 201
        manual_ids.append(response.json()["backup"]["id"])

    response = await client.get("/config/system/backups", headers=_headers())
    assert response.status_code == 200
    retained_ids = {item["id"] for item in response.json()["backups"]}
    assert retained_ids == set(manual_ids[-2:])
    assert len(list(manager.backup_dir.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_delete_backup_removes_only_selected_file(config_client):
    client, manager, _ = config_client
    config_before = manager.config_path.read_bytes()
    first_response = await client.post("/config/system/backups", headers=_headers())
    second_response = await client.post("/config/system/backups", headers=_headers())
    first = first_response.json()["backup"]
    second = second_response.json()["backup"]

    response = await client.delete(
        f"/config/system/backups/{first['id']}", headers=_headers()
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "deleted": first}
    assert manager.config_path.read_bytes() == config_before
    assert [item["id"] for item in manager.list_backups()] == [second["id"]]
    assert not (manager.backup_dir / f"{first['id']}.json").exists()


@pytest.mark.asyncio
async def test_delete_backup_requires_permission_and_valid_existing_id(config_client):
    client, manager, _ = config_client
    created = await client.post("/config/system/backups", headers=_headers())
    backup_id = created.json()["backup"]["id"]

    forbidden = await client.delete(
        f"/config/system/backups/{backup_id}", headers=_headers("viewer")
    )
    invalid = await client.delete(
        "/config/system/backups/bad:id", headers=_headers()
    )
    missing = await client.delete(
        "/config/system/backups/missing-backup", headers=_headers()
    )

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "system_config_backup_invalid"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "system_config_backup_not_found"
    assert [item["id"] for item in manager.list_backups()] == [backup_id]


@pytest.mark.asyncio
async def test_legacy_backup_index_is_not_listed_restored_or_pruned(config_client):
    client, manager, _ = config_client
    manager.backup_dir.mkdir(parents=True)
    index_path = manager.backup_dir / "index.json"
    index_path.write_text("[]", encoding="utf-8")

    list_response = await client.get("/config/system/backups", headers=_headers())
    restore_response = await client.post(
        "/config/system/backups/index/restore", headers=_headers()
    )
    delete_response = await client.delete(
        "/config/system/backups/index", headers=_headers()
    )

    assert list_response.status_code == 200
    assert list_response.json()["backups"] == []
    assert restore_response.status_code == 404
    assert delete_response.status_code == 404

    current = manager.read()
    current["config_management"]["backup_retention_count"] = 1
    write_config(current, manager.config_path)
    for _ in range(2):
        response = await client.post("/config/system/backups", headers=_headers())
        assert response.status_code == 201

    assert len(manager.list_backups()) == 1
    assert index_path.read_text(encoding="utf-8") == "[]"


@pytest.mark.asyncio
async def test_restore_reports_missing_and_invalid_backup_ids(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()

    missing = await client.post(
        "/config/system/backups/missing-backup/restore",
        headers=_headers(),
    )
    invalid = await client.post(
        "/config/system/backups/bad:id/restore",
        headers=_headers(),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "system_config_backup_not_found"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "system_config_backup_invalid"
    assert manager.config_path.read_bytes() == before
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_backup_reports_invalid_current_config_without_creating_file(config_client):
    client, manager, _ = config_client
    invalid = manager.read()
    invalid["processor"]["queue_policy"] = "not-supported"
    write_config(invalid, manager.config_path)

    response = await client.post("/config/system/backups", headers=_headers())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_validation_failed"
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_restore_rejects_invalid_backup_without_modifying_config(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    invalid = manager.read()
    invalid["processor"]["queue_policy"] = "not-supported"
    manager.backup_dir.mkdir(parents=True)
    write_config(invalid, manager.backup_dir / "invalid-content.json")

    response = await client.post(
        "/config/system/backups/invalid-content/restore",
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_validation_failed"
    assert manager.config_path.read_bytes() == before
    assert [item["id"] for item in manager.list_backups()] == ["invalid-content"]


@pytest.mark.asyncio
async def test_invalid_patch_is_validated_before_write(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    response = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"processor": {"max_queue_size": 0}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_config_validation_failed"
    assert manager.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_empty_unsupported_and_noop_patches_do_not_create_backups(config_client):
    client, manager, _ = config_client
    before = manager.config_path.read_bytes()
    current_threshold = manager.read()["reader"]["stale_threshold_sec"]

    empty = await client.patch("/config/system", headers=_headers(), json={})
    unsupported = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"unsupported_section": {"enabled": True}},
    )
    noop = await client.patch(
        "/config/system",
        headers=_headers(),
        json={"reader": {"stale_threshold_sec": current_threshold}},
    )

    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "system_config_patch_empty"
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "system_config_field_unsupported"
    assert noop.status_code == 200
    assert noop.json()["changed_paths"] == []
    assert manager.config_path.read_bytes() == before
    assert manager.list_backups() == []


@pytest.mark.asyncio
async def test_get_signal_config_not_found_returns_structured_error(config_client):
    client, _, _ = config_client
    response = await client.get("/config/signal/Unknown", headers=_headers())
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "signal_config_not_found"
    assert detail["signal_name"] == "Unknown"
