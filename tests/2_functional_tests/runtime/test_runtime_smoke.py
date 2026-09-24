"""Runtime smoke test: start app via run script, then verify API/WS behavior.

This suite is intentionally opt-in because it launches the full application process.
Enable with: RUN_RUNTIME_SMOKE=1
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import websockets

RUNTIME_API_KEY = "runtime-smoke-key"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _make_runtime_config(tmp_path: Path, port: int) -> Path:
    root = _project_root()
    source = root / "config" / "system.json"
    cfg = json.loads(source.read_text(encoding="utf-8"))

    cfg.setdefault("api", {})
    cfg["api"]["host"] = "127.0.0.1"
    cfg["api"]["port"] = port
    cfg["api"]["api_key"] = RUNTIME_API_KEY

    cfg.setdefault("storage", {})
    cfg["storage"]["sqlite_path"] = str(tmp_path / "runtime_smoke.db")

    cfg.setdefault("devmode", {})
    cfg["devmode"]["require_seat_connected"] = False

    cfg.setdefault("logging", {})
    cfg["logging"]["file_path"] = str(tmp_path / "runtime_smoke.log")

    out = tmp_path / "runtime_system.json"
    out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return out


def _wait_http_ready(base_url: str, timeout_sec: float = 45.0) -> None:
    deadline = time.time() + timeout_sec
    last_err: Exception | None = None
    with httpx.Client(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                resp = client.get(f"{base_url}/system/health")
                if resp.status_code == 200:
                    return
            except Exception as exc:  # pragma: no cover - retry loop
                last_err = exc
            time.sleep(0.5)

    if last_err is not None:
        raise AssertionError(f"App did not become ready: {last_err}")
    raise AssertionError("App did not become ready before timeout")


async def _ws_subscribe_ack(port: int) -> None:
    ws_url = f"ws://127.0.0.1:{port}/ws/subscribe?api_key={RUNTIME_API_KEY}"
    async with websockets.connect(ws_url, open_timeout=5, close_timeout=5) as ws:
        await ws.send(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        ack = json.loads(raw)
        assert ack.get("type") == "subscribe_ack"


@pytest.mark.runtime
def test_runtime_smoke_api_ws_and_combo_flow(tmp_path: Path):
    if os.getenv("RUN_RUNTIME_SMOKE") != "1":
        assert True, "Runtime smoke disabled; set RUN_RUNTIME_SMOKE=1 for full process check"
        return
    if os.name == "nt":
        assert True, "Runtime smoke Linux launcher is not applicable on Windows"
        return

    root = _project_root()
    port = _free_port()
    runtime_cfg = _make_runtime_config(tmp_path, port)
    base_url = f"http://127.0.0.1:{port}"

    out_log = (tmp_path / "runtime_stdout.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["bash", "scripts/run_linux.sh", str(runtime_cfg), "WARNING", str(port)],
        cwd=root,
        stdout=out_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        _wait_http_ready(base_url)

        headers = {"X-API-Key": RUNTIME_API_KEY}
        owner_headers = {
            **headers,
            "X-Client-Id": "runtime-owner",
            "X-Dev-Mode": "true",
        }
        intruder_headers = {
            **headers,
            "X-Client-Id": "runtime-intruder",
            "X-Dev-Mode": "true",
        }

        with httpx.Client(base_url=base_url, timeout=8.0) as client:
            health = client.get("/system/health")
            assert health.status_code == 200

            no_auth = client.get("/signals")
            assert no_auth.status_code == 401

            list_auth = client.get("/signals", headers=headers)
            assert list_auth.status_code == 200

            lock = client.post(
                "/api/devmode/seats/select",
                headers=owner_headers,
                json={"seats": {"fl": True}, "block_timeout_sec": 30},
            )
            assert lock.status_code in (200, 409)

            if lock.status_code == 200:
                blocked = client.put(
                    "/signals/ACR_FL_RetractRequest",
                    headers=intruder_headers,
                    json={"value": 5},
                )
                assert blocked.status_code in (202, 423)

                release = client.post("/api/devmode/exit", headers=owner_headers)
                assert release.status_code == 200

                after_release = client.put(
                    "/signals/ACR_FL_RetractRequest",
                    headers=intruder_headers,
                    json={"value": 5},
                )
                assert after_release.status_code != 423

        asyncio.run(_ws_subscribe_ack(port))
    finally:
        with contextlib.suppress(Exception):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGKILL)
        out_log.close()
