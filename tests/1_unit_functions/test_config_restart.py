"""Config restarts are debounced, serialized with updates, and never triggered by failed writes."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.core.config import AppConfig
from src.core.config_manager import (
    ConfigUpdateError,
    SystemConfigManager,
    read_config,
    write_config,
)
from src.core.runner import AppRunner


@pytest_asyncio.fixture
async def config_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("CAR_HMI_API_KEY", raising=False)
    monkeypatch.delenv("CAR_HMI_REQUIRE_API_KEY", raising=False)
    config = AppConfig.model_validate(read_config())
    config.api.port = 8123
    path = tmp_path / "system.json"
    template = tmp_path / "system_bk.json"
    write_config(config.model_dump(mode="json"), path)
    write_config(config.model_dump(mode="json"), template)
    manager = SystemConfigManager(path, template, tmp_path / "backups")
    runner = AppRunner(config, config_path=path)
    runner.request_reboot = AsyncMock(return_value=True)
    try:
        yield manager, runner
    finally:
        task = manager._restart_task
        manager.cancel_scheduled_restart()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


async def test_default_countdown_is_30_seconds_and_revert_cancels(config_runtime):
    manager, runner = config_runtime
    result = await manager.patch({"api": {"port": 8124}}, runner)
    seconds = (datetime.fromisoformat(result["reboot_at"]) - datetime.now(UTC)).total_seconds()
    assert 28 <= seconds <= 30
    assert result["reboot_scheduled"] and result["pending_reboot_paths"] == ["api.port"]
    assert manager.public_snapshot()["reboot_at"] == result["reboot_at"]
    original_task = manager._restart_task
    restored = await manager.patch({"api": {"port": 8123}}, runner)
    assert not restored["reboot_scheduled"] and not restored["reboot_required"]
    assert restored["reboot_at"] is None
    await asyncio.gather(original_task, return_exceptions=True)
    runner.request_reboot.assert_not_awaited()


async def test_debounce_only_for_successful_changed_reboot_fields(config_runtime):
    manager, runner = config_runtime
    await manager.patch({"api": {"port": 8124}}, runner)
    original = manager._restart_task
    await manager.patch({"api": {"port": 8125}}, runner)
    replacement = manager._restart_task
    assert replacement is not original
    await asyncio.gather(original, return_exceptions=True)
    deadline = manager._restart_deadline
    await manager.patch({"reader": {"stale_threshold_sec": 42}}, runner)
    await manager.patch({"api": {"port": 8125}}, runner)  # no-op
    with pytest.raises(ConfigUpdateError):
        await manager.patch({"api": {"port": -1}}, runner)
    assert manager._restart_task is replacement and manager._restart_deadline == deadline


async def test_timer_waits_for_config_transaction_then_restarts_once(config_runtime, monkeypatch):
    import src.core.config_manager as config_module

    manager, runner = config_runtime
    monkeypatch.setattr(config_module, "AUTO_REBOOT_DELAY_SEC", 0.03)
    await manager.patch({"api": {"port": 8124}}, runner)
    old_timer = manager._restart_task
    applying, release = asyncio.Event(), asyncio.Event()
    original_apply = runner.apply_system_config

    async def slow_apply(config, paths):
        applying.set()
        await release.wait()
        return await original_apply(config, paths)

    monkeypatch.setattr(runner, "apply_system_config", slow_apply)
    update = asyncio.create_task(manager.patch({"api": {"port": 8125}}, runner))
    try:
        await applying.wait()
        await asyncio.sleep(0.08)
        runner.request_reboot.assert_not_awaited()
    finally:
        release.set()
        await update
    await asyncio.gather(old_timer, return_exceptions=True)
    await asyncio.wait_for(manager._restart_task, 2)
    runner.request_reboot.assert_awaited_once()


async def test_apply_failure_rolls_back_without_changing_existing_timer(config_runtime, monkeypatch):
    manager, runner = config_runtime
    await manager.patch({"api": {"port": 8124}}, runner)
    timer = manager._restart_task
    deadline = manager._restart_deadline
    saved = manager.config_path.read_text()
    original_apply = runner.apply_system_config

    async def fail_apply(config, paths):
        result = await original_apply(config, paths)
        if config.api.port == 8125:
            raise RuntimeError("injected apply failure")
        return result

    monkeypatch.setattr(runner, "apply_system_config", fail_apply)
    with pytest.raises(ConfigUpdateError, match="injected apply failure"):
        await manager.patch({"api": {"port": 8125}}, runner)
    assert manager.config_path.read_text() == saved
    assert runner.config.api.port == 8124
    assert manager._restart_task is timer and manager._restart_deadline == deadline


async def test_reset_restore_reload_use_the_same_restart_rules(config_runtime):
    manager, runner = config_runtime
    changed = await manager.patch({"api": {"port": 8124}}, runner)
    restored = await manager.restore(changed["backup"]["id"], runner)
    assert not restored["reboot_scheduled"]
    raw = manager.read()
    raw["api"]["port"] = 8125
    write_config(raw, manager.config_path)
    reloaded = await manager.reload_runtime(runner)
    assert reloaded["reboot_scheduled"]
    assert reloaded["pending_reboot_paths"] == ["api.port"]
    reset = await manager.reset(runner)
    assert not reset["reboot_scheduled"]


async def test_port_environment_override_does_not_schedule_an_ineffective_restart(config_runtime, monkeypatch):
    manager, runner = config_runtime
    monkeypatch.setenv("PORT", "8123")
    result = await manager.patch({"api": {"port": 8124}}, runner)
    assert not result["reboot_required"] and not result["reboot_scheduled"]
    assert runner.config.api.port == 8123


async def test_shutdown_cancels_timer_and_new_writes_are_rejected(config_runtime):
    manager, runner = config_runtime
    await manager.patch({"api": {"port": 8124}}, runner)
    timer = manager._restart_task
    runner._api_app = SimpleNamespace(state=SimpleNamespace(system_config_manager=manager))
    await runner.shutdown()
    await asyncio.gather(timer, return_exceptions=True)
    assert not manager.restart_status()["reboot_scheduled"]
    with pytest.raises(ConfigUpdateError, match="Backend is restarting"):
        await manager.patch({"api": {"port": 8125}}, runner)
    runner.request_reboot.assert_not_awaited()


async def test_unused_default_permissions_are_read_only(config_runtime):
    manager, runner = config_runtime
    with pytest.raises(ConfigUpdateError, match="Immutable config field"):
        await manager.patch({"profiles": {"default_profile_permission": ["full"]}}, runner)
    assert manager.list_backups() == []
