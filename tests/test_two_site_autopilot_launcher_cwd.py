import os
import shutil
import subprocess
from pathlib import Path
import pytest

LAUNCHER_SRC = Path(__file__).resolve().parent.parent / "scripts" / "two_site_autopilot_tmux.sh"


def test_launcher_starts_broker_before_supervisor_and_stops_both():
    source = LAUNCHER_SRC.read_text(encoding="utf-8")
    broker_start = source.index('tmux new-session -d -s "$broker_session"')
    broker_health = source.index('two_site_provider_broker.py --root "$root" status', broker_start)
    supervisor_start = source.index('tmux new-session -d -s "$session"')
    assert broker_start < broker_health < supervisor_start
    assert 'tmux kill-session -t "=$broker_session"' in source


def init_repo(path: Path, branch: str = "refactor/two-site-architecture") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    (path / ".gitkeep").touch()
    subprocess.run(["git", "-C", str(path), "add", ".gitkeep"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(path), "branch", "-M", branch], check=True)
    return path


def install_launcher(base_dir: Path) -> Path:
    launcher = base_dir / "scripts" / "two_site_autopilot_tmux.sh"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LAUNCHER_SRC, launcher)
    launcher.chmod(0o755)
    return launcher


@pytest.fixture
def test_env(tmp_path):
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text('#!/bin/sh\ncase "${1:-}" in\n  has-session) exit 1 ;;\n  *) exit 0 ;;\nesac\n')
    tmux.chmod(0o755)
    jq = bin_dir / "jq"
    jq.write_text('#!/bin/sh\n[ "${1:-}" = "." ] && shift\nexec cat "$@"\n')
    jq.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def test_launcher_cwd_isolation_and_status(tmp_path, test_env):
    repo_with_space = init_repo(tmp_path / "repo with space")
    launcher = install_launcher(repo_with_space)

    status_file = repo_with_space / ".local/two-site-refactor/autopilot/status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text('{"phase": "legit_status"}\n')

    jq_res = subprocess.run(["jq", ".", str(status_file)], env=test_env, capture_output=True, text=True)
    assert jq_res.returncode == 0 and "legit_status" in jq_res.stdout

    caller_repo = init_repo(tmp_path / "caller repo", branch="main")
    caller_status = caller_repo / ".local/two-site-refactor/autopilot/status.json"
    caller_status.parent.mkdir(parents=True, exist_ok=True)
    caller_status.write_text('{"phase": "foreign_status"}\n')

    snapshot_legit = {p: p.stat().st_mtime_ns for p in repo_with_space.rglob("*")}
    snapshot_caller = {p: p.stat().st_mtime_ns for p in caller_repo.rglob("*")}

    res = subprocess.run([str(launcher), "status"], cwd=str(caller_repo), env=test_env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "AUTOPILOT=NOT_RUNNING session=two-site-autopilot" in res.stdout
    assert "legit_status" in res.stdout
    assert "foreign_status" not in res.stdout

    assert {p: p.stat().st_mtime_ns for p in repo_with_space.rglob("*")} == snapshot_legit
    assert {p: p.stat().st_mtime_ns for p in caller_repo.rglob("*")} == snapshot_caller


@pytest.mark.parametrize("kind", ["wrong_branch", "non_repo", "symlink_alias"])
def test_launcher_refusals(tmp_path, test_env, kind):
    if kind == "wrong_branch":
        repo = init_repo(tmp_path / "wrong_branch", branch="main")
        launcher = install_launcher(repo)
        res = subprocess.run([str(launcher), "status"], cwd=str(tmp_path), env=test_env, capture_output=True, text=True)
        assert res.returncode == 2
        assert "branch must be refactor/two-site-architecture" in res.stderr
    elif kind == "non_repo":
        launcher = install_launcher(tmp_path / "non_repo")
        res = subprocess.run([str(launcher), "status"], cwd=str(tmp_path), env=test_env, capture_output=True, text=True)
        assert res.returncode == 2
        assert "not inside the integration repository" in res.stderr
    else:
        legit = init_repo(tmp_path / "legit_repo")
        target = install_launcher(legit)
        other = init_repo(tmp_path / "other_repo")
        alias = other / "scripts" / "two_site_autopilot_tmux.sh"
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias.symlink_to(target)
        res = subprocess.run([str(alias), "status"], cwd=str(tmp_path), env=test_env, capture_output=True, text=True)
        assert res.returncode == 2
        assert "symlink alias" in res.stderr
