"""Exercise fast deployment without changing host services or installing packages."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "backend with spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("dev_deploy_linux.sh", "linux_common.sh", "linux_env.py"):
        shutil.copy(ROOT / "scripts" / name, scripts)
    entrypoint = project / ".venv/bin/can-hmi"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.touch(mode=0o755)
    frontend = tmp_path / "arbitrarily named frontend"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist/index.html").write_text("old frontend")
    (frontend / "node_modules/vite/bin").mkdir(parents=True)
    (frontend / "node_modules/vite/bin/vite.js").touch()
    for name in ("package.json", "package-lock.json"):
        (frontend / name).write_text("{}")
    (project / ".env").write_text(f'CAR_HMI_FRONTEND_PATH="../{frontend.name}"\n')
    commands = tmp_path / "commands"
    commands.mkdir()
    operations = tmp_path / "operations.jsonl"
    prologue = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_OPERATIONS'], 'a') as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *args]) + '\\n')
'''
    stubs = {
        "systemctl": '''
if args[0] == 'show':
    prop = next(a.split('=', 1)[1] for a in args if a.startswith('--property='))
    if prop == 'LoadState': print('not-found' if os.getenv('TEST_MISSING_UNIT') else 'loaded')
    elif prop == 'User': print('test-frontend')
    else:
        key = 'TEST_BACKEND' if args[1] == 'can-hmi.service' else 'TEST_FRONTEND'
        print(os.getenv('TEST_WRONG_DIRECTORY', os.environ[key]))
elif args[0] == 'restart' and os.getenv('TEST_FAILURE') == 'restart': sys.exit(5)
elif args[0] == 'is-active' and os.getenv('TEST_FAILURE') == 'active': sys.exit(3)
''',
        "npm": '''
assert args[:4] == ['--prefix', os.environ['TEST_FRONTEND'], 'run', 'build'], args
assert args[4:6] == ['--', '--outDir'], args
out = Path(args[6]); out.mkdir(parents=True)
(out / 'partial.js').write_text('partial')
if os.getenv('TEST_FAILURE') == 'build': sys.exit(9)
if os.getenv('TEST_FAILURE') != 'no-index':
    (out / 'index.html').write_text('new frontend')
    (out / 'assets').mkdir()
    (out / 'assets/app.js').write_text('new asset')
''',
        "setfacl": "if os.getenv('TEST_FAILURE') == 'acl': sys.exit(1)\n",
        "mv": '''
paths = [a for a in args if a != '--']
if (os.getenv('TEST_FAILURE') == 'publish' and '/.dev-deploy.' in paths[0]
        and not paths[0].endswith('/previous-dist')):
    sys.exit(13)
os.execv('/bin/mv', ['mv', *args])
''',
        "runuser": "sys.exit(0)\n",
        "node": "sys.exit(0)\n",
        "sleep": "sys.exit(0)\n",
        "sudo": "os.execvp(args[0], args)\n",
    }
    for name, body in stubs.items():
        command = commands / name
        command.write_text(prologue + body)
        command.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CAR_HMI_")},
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "TEST_OPERATIONS": str(operations),
        "TEST_BACKEND": str(project), "TEST_FRONTEND": str(frontend),
    }

    def run(*args, **overrides):
        result = subprocess.run(
            ["bash", str(scripts / "dev_deploy_linux.sh"), *args],
            cwd=tmp_path, env={**env, **overrides}, text=True, capture_output=True, check=False,
        )
        calls = [json.loads(line) for line in operations.read_text().splitlines()] if operations.exists() else []
        return result, calls

    return project, frontend, run


@pytest.mark.parametrize("mode", [None, "--frontend-only", "--backend-only"])
def test_fast_deploy_updates_only_selected_services(deployment, mode):
    project, frontend, run = deployment
    if mode == "--backend-only":
        shutil.rmtree(frontend)
    elif mode == "--frontend-only":
        (project / ".venv/bin/can-hmi").unlink()
    result, calls = run(*([mode] if mode else []))
    assert result.returncode == 0, result.stderr
    selected = ["can-hmi.service"] if mode == "--backend-only" else (
        ["can-hmi-frontend.service"] if mode else ["can-hmi.service", "can-hmi-frontend.service"]
    )
    assert [c for c in calls if c[:2] == ["systemctl", "restart"]] == [["systemctl", "restart", *selected]]
    if mode == "--backend-only":
        assert not [c for c in calls if c[0] in ("npm", "node", "setfacl", "runuser")]
    else:
        assert (frontend / "dist/index.html").read_text() == "new frontend"
        assert (frontend / "dist/assets/app.js").read_text() == "new asset"
        acl_calls = [c for c in calls if c[0] == "setfacl"]
        assert len(acl_calls) == 2
        assert all("node_modules" not in c[-1] for c in acl_calls)
        assert not list(frontend.glob(".dev-deploy.*"))
    assert not [c for c in calls if c[0] == "npm" and "ci" in c]
    assert not [c for c in calls if c[:2] in (["systemctl", "enable"], ["systemctl", "daemon-reload"])]


@pytest.mark.parametrize("failure", ["build", "no-index", "acl", "publish", "restart", "active"])
def test_failed_fast_deploy_preserves_previous_dist(deployment, failure):
    _, frontend, run = deployment
    result, calls = run(TEST_FAILURE=failure)
    assert result.returncode != 0
    assert (frontend / "dist/index.html").read_text() == "old frontend"
    assert not list(frontend.glob(".dev-deploy.*"))
    if failure in ("build", "no-index", "acl", "publish"):
        assert not [c for c in calls if c[:2] == ["systemctl", "restart"]]


@pytest.mark.parametrize("failure", ["missing-unit", "wrong-directory", "missing-dependencies"])
def test_fast_deploy_rejects_incomplete_or_stale_install_before_build(deployment, failure):
    project, frontend, run = deployment
    overrides = {}
    if failure == "missing-unit":
        overrides["TEST_MISSING_UNIT"] = "1"
    elif failure == "wrong-directory":
        overrides["TEST_WRONG_DIRECTORY"] = str(project.parent)
    else:
        shutil.rmtree(frontend / "node_modules")
    result, calls = run(**overrides)
    assert result.returncode != 0
    assert (frontend / "dist/index.html").read_text() == "old frontend"
    assert not [c for c in calls if c[0] == "npm" or c[:2] == ["systemctl", "restart"]]


@pytest.mark.parametrize("args,code", [(["--help"], 0), (["--invalid"], 2), (["--backend-only", "--frontend-only"], 2)])
def test_fast_deploy_cli_has_no_side_effects(deployment, args, code):
    _, _, run = deployment
    result, calls = run(*args)
    assert result.returncode == code
    assert calls == []
