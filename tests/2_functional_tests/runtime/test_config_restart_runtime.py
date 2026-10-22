"""Run the real 30-second restart on the same launcher used by Render.

Opt in with RUN_RUNTIME_SMOKE=1. Only virtual CAN is used.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from src.core.config_manager import read_config


@pytest.mark.runtime
@pytest.mark.skipif(os.getenv("RUN_RUNTIME_SMOKE") != "1", reason="real 30-second restart is opt-in")
def test_config_patch_restarts_backend_after_30_seconds_without_frontend(tmp_path):
    root = Path(__file__).resolve().parents[3]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = read_config()
    config["api"].update(host="127.0.0.1", port=port, api_key="restart-runtime-test")
    config["can"][0].update(interface="virtual", channel=f"restart-{port}", channel_tracking_signals=[])
    config["simulator"].update(enabled=True, random_mode=False, can_db_file=config["can"][0]["can_db_file"])
    config["camera"]["enabled"] = False
    config["audio"]["enabled"] = False
    config["status_monitor"]["enabled"] = False
    config["logging"].update(file_path=str(tmp_path / "app.log"), level="WARNING")
    config_path = tmp_path / "system.json"
    config_path.write_text(json.dumps(config))
    environment = os.environ.copy()
    environment["PORT"] = str(port)
    environment["CAR_HMI_API_KEY"] = "restart-runtime-test"
    environment["CAR_HMI_REQUIRE_API_KEY"] = "true"
    output = (tmp_path / "service.log").open("w")
    parent = subprocess.Popen(
        [sys.executable, "-m", "src.core.service", "--config", str(config_path)],
        cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT,
    )
    children = set()
    headers = {"X-API-Key": "restart-runtime-test", "X-Dev-Mode": "true"}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=2) as client:
            def ready():
                try:
                    return client.get("/system/ready").json().get("ready", False)
                except (httpx.HTTPError, ValueError):
                    return False

            deadline = time.monotonic() + 15
            while not ready():
                assert parent.poll() is None, (tmp_path / "service.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "service.log").read_text()
                time.sleep(0.05)
            original_pid = psutil.Process(parent.pid).children()[0].pid
            children.add(original_pid)
            response = client.patch("/config/system", json={"simulator": {"random_mode": True}})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["reboot_scheduled"]
            assert result["pending_reboot_paths"] == ["simulator.random_mode"]
            assert json.loads(config_path.read_text())["simulator"]["random_mode"] is True
            scheduled = client.get("/config/system").json()
            assert scheduled["reboot_at"] == result["reboot_at"]

            started = time.monotonic()
            new_pid = original_pid
            deadline = started + 45
            while new_pid == original_pid or not ready():
                assert parent.poll() is None, (tmp_path / "service.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "service.log").read_text()
                current = psutil.Process(parent.pid).children()
                children.update(child.pid for child in current)
                if current:
                    new_pid = current[0].pid
                time.sleep(0.1)
            assert time.monotonic() - started >= 29
            assert new_pid != original_pid
            assert not psutil.pid_exists(original_pid)
            snapshot = client.get("/config/system").json()
            assert snapshot["config"]["simulator"]["random_mode"] is True
            assert not snapshot["reboot_required"] and not snapshot["reboot_scheduled"]
            assert snapshot["reboot_at"] is None
            assert "Backend restart requested" in (tmp_path / "service.log").read_text()
    finally:
        parent.terminate()
        try:
            parent.wait(timeout=15)
        except subprocess.TimeoutExpired:
            for pid in children:
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
            parent.wait(timeout=5)
        output.close()
    assert all(not psutil.pid_exists(pid) for pid in children)
