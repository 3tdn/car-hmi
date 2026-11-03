"""Exercise BE installation with missing/invalid .env without touching host systemd."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "content",
    [
        None,
        b"CAR_HMI_API_KEY=partial-secret\xff\n",
        'CAR_HMI_API_KEY=partial-secret\nPORT=9123\nTHIS IS NOT AN ASSIGNMENT\n',
        'CAR_HMI_API_KEY=partial-secret\nPORT="unterminated\n',
        'CAR_HMI_API_KEY=partial-secret\nPORT=not-a-port\n',
        'CAR_HMI_API_KEY=partial-secret\nPORT=70000\n',
        'CAR_HMI_API_KEY=partial-secret\nexit 19\n',
    ],
)
def test_deploy_installs_backend_with_defaults_on_environment_error(tmp_path, content):
    result, project, systemd = run_deploy(tmp_path, content)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[env] ERROR:" in result.stderr
    assert "partial-secret" not in result.stderr
    assert "Backend is installed, enabled, and running." in result.stdout
    assert (systemd / "can-hmi.env").read_text().strip() == ""
    assert "restart can-hmi.service" in (project / "systemctl.log").read_text()
    assert "EnvironmentFile=-" + str(systemd / "can-hmi.env") in (
        systemd / "can-hmi.service"
    ).read_text()
    if content is None:
        assert not (project / ".env").exists()


def test_deploy_preserves_valid_quoted_values_in_systemd_snapshot(tmp_path):
    result, project, systemd = run_deploy(
        tmp_path,
        'export PORT=9123 # trailing comment\nCAR_HMI_API_KEY="spaces # literal"\n'
        'CAR_HMI_FRONTEND_PATH="../missing frontend"\n',
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[env] ERROR:" not in result.stderr
    snapshot = (systemd / "can-hmi.env").read_text()
    assert 'PORT="9123"' in snapshot
    assert 'CAR_HMI_API_KEY="spaces # literal"' in snapshot
    assert 'CAR_HMI_FRONTEND_PATH="../missing frontend"' in snapshot
    assert (systemd / "can-hmi.env").stat().st_mode & 0o777 == 0o600
    assert 'PORT=9123 # trailing comment' in (project / '.env').read_text()


@pytest.mark.parametrize("state", ["active", "auto-restart", "inactive"])
def test_deploy_stops_frontend_before_port_cleanup_in_all_states(tmp_path, state):
    result, project, systemd = run_deploy(
        tmp_path, "CAR_HMI_FRONTEND_PORT=5234\n", frontend_state=state,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Frontend is installed, enabled, and running" in result.stdout, result.stderr
    calls = (project / "systemctl.log").read_text().splitlines()
    assert calls.index("stop can-hmi-frontend.service") < calls.index("inspect-port 5234")
    assert calls.index("inspect-port 5234") < calls.index("start can-hmi-frontend.service")
    assert 'Environment="CAR_HMI_FRONTEND_PORT=5234"' in (
        systemd / "can-hmi-frontend.service"
    ).read_text()


def run_deploy(tmp_path, content, frontend_state=None):
    project = tmp_path / "backend with spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "deploy_linux.sh", "linux_common.sh", "linux_frontend_access.sh",
        "linux_frontend_port.sh", "linux_env.py",
    ):
        shutil.copy(ROOT / "scripts" / name, scripts)
    shutil.copytree(ROOT / "deploy", project / "deploy")
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    (systemd / "can-hmi.env").write_text('PORT="9999"\n')
    runtime = tmp_path / "systemd-runtime"
    runtime.mkdir()
    script = scripts / "deploy_linux.sh"
    source = script.read_text()
    # Redirect only the systemd filesystem locations in this temporary copy;
    # exercise the real install/fallback logic with harmless systemctl stubs.
    source = source.replace("SYSTEMD_DIR=/etc/systemd/system", 'SYSTEMD_DIR="$TEST_SYSTEMD_DIR"')
    source = source.replace("[[ ! -d /run/systemd/system ]]", '[[ ! -d "$TEST_SYSTEMD_RUNTIME" ]]')
    script.write_text(source)
    venv = project / ".venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(sys.executable)
    executable = venv / "can-hmi"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    config = project / "config"
    config.mkdir()
    (config / "system_bk.json").write_text(json.dumps({"api": {"port": 8000}}))
    state_file = project / "frontend.state"
    if frontend_state is not None:
        frontend = tmp_path / "renamed frontend"
        (frontend / "dist").mkdir(parents=True)
        (frontend / "dist/index.html").write_text("frontend build")
        for name in ("package.json", "package-lock.json"):
            (frontend / name).write_text("{}")
        content += f'CAR_HMI_FRONTEND_PATH="{frontend}"\n'
        state_file.write_text(frontend_state)
    (project / ".env_bk").write_text("PORT=9999\n")
    if isinstance(content, bytes):
        (project / ".env").write_bytes(content)
    elif content is not None:
        (project / ".env").write_text(content)
    commands = tmp_path / "bin"
    commands.mkdir()
    stubs = {
        "sudo": '#!/bin/sh\nexec "$@"\n',
        "systemctl": '#!/bin/sh\necho "$*" >> "$TEST_SYSTEMCTL_LOG"\nexit 0\n',
        "systemd-analyze": "#!/bin/sh\nexit 0\n",
        "ffplay": "#!/bin/sh\nexit 0\n",
        "ffprobe": "#!/bin/sh\nexit 0\n",
        "getent": "#!/bin/sh\nexit 1\n",
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    if frontend_state is not None:
        stubs.update({
            "systemctl": '''#!/bin/sh
echo "$*" >> "$TEST_SYSTEMCTL_LOG"
case "$*" in
    "is-active --quiet can-hmi-frontend.service")
        test "$(cat "$TEST_FRONTEND_STATE")" = active ;;
    "stop can-hmi-frontend.service") echo inactive > "$TEST_FRONTEND_STATE" ;;
    "start can-hmi-frontend.service") echo active > "$TEST_FRONTEND_STATE" ;;
    *) exit 0 ;;
esac
''',
            "getent": '#!/bin/sh\ntest "$1" = passwd\n',
            "node": "#!/bin/sh\nexit 0\n",
            "npm": "#!/bin/sh\nexit 0\n",
            "setfacl": "#!/bin/sh\nexit 0\n",
            "runuser": "#!/bin/sh\nexit 0\n",
            "fuser": '#!/bin/sh\necho "inspect-port $3" >> "$TEST_SYSTEMCTL_LOG"\n',
        })
    for name, body in stubs.items():
        command = commands / name
        command.write_text(body)
        command.chmod(0o755)
    env = dict(os.environ)
    for key in ("PORT", "CAR_HMI_API_KEY", "CAR_HMI_FRONTEND_PATH", "CAR_HMI_FRONTEND_PORT"):
        env.pop(key, None)
    env.update(
        PATH=str(commands) + os.pathsep + env["PATH"],
        TEST_SYSTEMD_DIR=str(systemd), TEST_SYSTEMD_RUNTIME=str(runtime),
        TEST_SYSTEMCTL_LOG=str(project / "systemctl.log"),
        TEST_FRONTEND_STATE=str(state_file),
    )
    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path, env=env, text=True, capture_output=True,
        timeout=15, check=False,
    )
    return result, project, systemd
