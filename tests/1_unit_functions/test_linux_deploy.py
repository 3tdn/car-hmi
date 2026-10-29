"""Check frontend ACL scope and systemd paths without installing host services."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failure", ["install", "build", None])
def test_frontend_prepare_rejects_failed_install_or_build_even_with_old_dist(tmp_path, failure):
    frontend = tmp_path / "web ui with spaces"
    (frontend / "dist").mkdir(parents=True)
    index = frontend / "dist/index.html"
    index.write_text("old build")
    harness = r'''
source "$1"
FRONTEND_DIR="$2"
npm() {
    if [[ "$3" == ci ]]; then
        [[ "$4" == --include=dev ]] || return 88
        [[ "$TEST_FAILURE" != install ]] || return 7
    else
        [[ "$TEST_FAILURE" != build ]] || return 9
        printf 'new build' > "$2/dist/index.html"
    fi
}
if prepare_frontend; then exit 0; else exit $?; fi
'''
    result = subprocess.run(
        ["bash", "-c", harness, "prepare", str(ROOT / "scripts/linux_common.sh"), str(frontend)],
        env={**os.environ, "TEST_FAILURE": failure or ""},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == {"install": 7, "build": 9, None: 0}[failure], result.stderr
    assert index.read_text() == ("old build" if failure else "new build")


@pytest.mark.parametrize("absolute", [True, False])
def test_frontend_path_from_env_follows_renamed_directory(tmp_path, absolute):
    if not shutil.which("node") or not shutil.which("npm"):
        pytest.skip("Node.js and npm are required")
    project = tmp_path / "api service"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("linux_common.sh", "linux_env.py"):
        shutil.copy(ROOT / "scripts" / name, scripts)
    frontend = tmp_path / "web ui"
    frontend.mkdir()
    for name in ("package.json", "package-lock.json"):
        (frontend / name).write_text("{}")
    for name in ("web ui", "renamed frontend with spaces"):
        renamed = frontend.with_name(name)
        if frontend != renamed:
            frontend.rename(renamed)
            frontend = renamed
        value = str(frontend) if absolute else os.path.relpath(frontend, project)
        (project / ".env").write_text(f'CAR_HMI_FRONTEND_PATH="{value}"\n')
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; require_frontend_runtime || exit $?; printf "%s" "$FRONTEND_DIR"',
             "path-test", str(scripts / "linux_common.sh")],
            cwd=tmp_path, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout).resolve() == frontend


def test_missing_frontend_path_does_not_guess_a_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CAR_HMI_FRONTEND_PATH"}
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/linux_common.sh", scripts)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; require_frontend_runtime', "path-test",
         str(scripts / "linux_common.sh")],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "Set CAR_HMI_FRONTEND_PATH" in result.stderr


@pytest.mark.parametrize("folder", ["backend with spaces", 'backend "quoted" 100%'])
def test_frontend_unit_quotes_private_environment_path(tmp_path, folder):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze is required")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    project = tmp_path / folder
    templates = project / "deploy"
    templates.mkdir(parents=True)
    shutil.copy(ROOT / "deploy/can-hmi-frontend.service", templates)
    env_file = project / ".env"
    env_file.write_text("PRIVATE_TEST_VALUE=hidden\n")
    renderer = (ROOT / "scripts/deploy_linux.sh").read_text().split("<<'PY'\n", 1)[1]
    renderer = renderer.split("\nPY\n", 1)[0]
    subprocess.run(
        [
            sys.executable, "-", str(project), str(tmp_path), "can-hmi-frontend.service",
            "FRONTEND_DIR", str(project), "NODE_BIN", node,
            "ENV_FILE", str(env_file), "BACKEND_URL", "http://127.0.0.1:8000",
            "FRONTEND_CONFIG_FILE", "/etc/systemd/system/can-hmi-frontend.cjs",
        ],
        input=renderer, text=True, check=True,
    )
    unit = tmp_path / "can-hmi-frontend.service"
    escaped = str(env_file).replace("%", "%%").replace('"', '\\"')
    assert f'InaccessiblePaths="{escaped}"' in unit.read_text()
    result = subprocess.run(
        ["systemd-analyze", "verify", str(unit)], text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr


def test_frontend_acl_preserves_private_files_and_does_not_follow_symlinks(tmp_path):
    if not shutil.which("setfacl") or not shutil.which("getfacl"):
        pytest.skip("acl tools are required")
    probe = tmp_path / "acl-probe"
    probe.touch()
    result = subprocess.run(
        ["setfacl", "-m", "u:65534:r--", str(probe)], text=True, capture_output=True,
        check=False,
    )
    if "Operation not supported" in result.stderr:
        pytest.skip("The temporary filesystem does not support POSIX ACLs")
    assert result.returncode == 0, result.stderr
    private_home = tmp_path / "private home"
    frontend = private_home / "hmi"
    assets = frontend / "js"
    assets.mkdir(parents=True)
    backend = private_home / "backend"
    backend.mkdir()
    secret = backend / ".env"
    secret.write_text("PRIVATE_TEST_VALUE=hidden\n")
    script = assets / "app.js"
    script.write_text("console.log('public asset');\n")
    (frontend / "external-link").symlink_to(secret)
    node = private_home / "node runtime/bin/node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\nexit 0\n")
    for directory in (private_home, frontend, assets, backend, node.parent, node.parent.parent):
        directory.chmod(0o700)
    for file in (secret, script):
        file.chmod(0o600)
    node.chmod(0o700)
    # Model a different user's initial access via the 'other' permission bits.
    # Actual setfacl/getfacl run on this user's temporary files, without sudo.
    harness = r'''
set -euo pipefail
source "$1"
root() {
    if [[ "$1" == runuser ]]; then
        "$TEST_PYTHON" - "${@:6}" <<'PY'
import stat
import sys
from pathlib import Path
flag, filename = sys.argv[1:]
mask = stat.S_IXOTH if flag == '-x' else stat.S_IROTH
sys.exit(0 if Path(filename).stat().st_mode & mask else 1)
PY
    else
        "$@"
    fi
}
grant_frontend_access 65534 "$2" "$3"
'''
    subprocess.run(
        ["bash", "-c", harness, "test-acl", str(ROOT / "scripts/linux_frontend_access.sh"),
         str(frontend), str(node)],
        env={**os.environ, "TEST_PYTHON": sys.executable}, check=True,
    )

    def acl(path):
        return subprocess.check_output(["getfacl", "-c", "-n", str(path)], text=True)

    assert "user:65534:r-x" in acl(frontend)
    assert "user:65534:r--" in acl(script)
    assert "user:65534:--x" in acl(private_home)
    assert "user:65534:r-x" in acl(node)
    assert "user:65534:" not in acl(secret)
    assert "user:65534:" not in acl(backend)
    assert private_home.stat().st_mode & 0o007 == 0
    assert script.stat().st_mode & 0o007 == 0
