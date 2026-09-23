from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

from scripts import two_site_codex_autopilot as ap
from scripts import two_site_provider_broker as broker
from scripts import two_site_preparation_runtime_gate as gate
from tests.test_two_site_cycle_packet import repo
from tests.test_two_site_preparation_boundary import _setup_valid_boundary


def _setup_runtime_repo(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root = ctx["root"], ctx["state_root"]
    broker_dir = root / broker.STATE_REL
    proposals_dir = root / ".local/two-site-refactor/proposals"

    broker_dir.mkdir(parents=True, exist_ok=True)
    for q in ("requests", "claimed", "responses"):
        (broker_dir / q).mkdir(parents=True, exist_ok=True)

    status_file = broker_dir / "status.json"
    status_file.write_text(json.dumps({"state": "IDLE", "updated_at": time.time()}))

    client_lock = broker_dir / "client.lock"
    runner_lock = proposals_dir / ".runner.lock"
    sup_lock = state_root / ".supervisor.lock"
    client_lock.touch()
    runner_lock.touch()
    sup_lock.touch()

    return {
        "ctx": ctx,
        "root": root,
        "state_root": state_root,
        "broker_dir": broker_dir,
        "proposals_dir": proposals_dir,
        "status_file": status_file,
        "client_lock": client_lock,
        "runner_lock": runner_lock,
        "sup_lock": sup_lock,
        "git": ctx["git"],
    }


def test_real_locked_resolved_boundary(repo, monkeypatch):
    env = _setup_runtime_repo(repo, monkeypatch)
    root, state_root, git = env["root"], env["state_root"], env["git"]
    broker_dir, proposals_dir = env["broker_dir"], env["proposals_dir"]
    client_lock, runner_lock, sup_lock = env["client_lock"], env["runner_lock"], env["sup_lock"]

    head_before = git("rev-parse", "HEAD")
    status_before = git("status", "--porcelain=v1")
    refs_before = ap.ref_snapshot(root)
    bytes_before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    held_verified = False
    real_boundary = gate.validate_resolved_preparation_boundary

    def wrapped_boundary(r, s):
        nonlocal held_verified
        for lp in (client_lock, runner_lock):
            fd = os.open(lp, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    raise AssertionError(f"lock {lp} was not held")
                except BlockingIOError:
                    pass
            finally:
                os.close(fd)
        held_verified = True
        return real_boundary(r, s)

    monkeypatch.setattr(gate, "validate_resolved_preparation_boundary", wrapped_boundary)

    with ap.exclusive_supervisor_lock(sup_lock):
        gate.validate_legacy_preparation_startup(root, state_root)

    assert held_verified
    assert git("rev-parse", "HEAD") == head_before
    assert git("status", "--porcelain=v1") == status_before
    assert ap.ref_snapshot(root) == refs_before
    bytes_after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert bytes_before == bytes_after

    for q in ("requests", "claimed", "responses"):
        with os.scandir(broker_dir / q) as it:
            assert next(it, None) is None

    with broker.exclusive_broker_lock(client_lock):
        pass
    with broker.exclusive_broker_lock(runner_lock):
        pass


@pytest.mark.parametrize(
    "case",
    [
        "running",
        "stale",
        "future",
        "bool",
        "nan",
        "huge_pos",
        "huge_neg",
        "requests_item",
        "claimed_item",
        "responses_item",
        "requests_tmp",
        "held_client_lock",
        "held_runner_lock",
        "stop_normal",
        "stop_broken_symlink",
        "status_symlink",
        "status_hardlink",
        "status_fifo",
        "status_oversized",
    ],
)
def test_rejects_busy_ambiguous_and_unsafe(repo, monkeypatch, case):
    env = _setup_runtime_repo(repo, monkeypatch)
    root, state_root = env["root"], env["state_root"]
    broker_dir = env["broker_dir"]
    status_file = env["status_file"]
    client_lock, runner_lock, sup_lock = env["client_lock"], env["runner_lock"], env["sup_lock"]

    queue_file = None
    expected_bytes = b""
    held_fd = None

    if case == "running":
        status_file.write_text(json.dumps({"state": "RUNNING", "updated_at": time.time()}))
    elif case == "stale":
        status_file.write_text(json.dumps({"state": "IDLE", "updated_at": time.time() - 3600}))
    elif case == "future":
        status_file.write_text(json.dumps({"state": "IDLE", "updated_at": time.time() + 3600}))
    elif case == "bool":
        status_file.write_text(json.dumps({"state": "IDLE", "updated_at": True}))
    elif case == "nan":
        status_file.write_text('{"state": "IDLE", "updated_at": NaN}')
    elif case == "huge_pos":
        status_file.write_text(f'{{"state\": \"IDLE\", \"updated_at\": {10**500}}}')
    elif case == "huge_neg":
        status_file.write_text(f'{{"state\": \"IDLE\", \"updated_at\": {-10**500}}}')
    elif case == "requests_item":
        queue_file = broker_dir / "requests" / "req1.json"
        expected_bytes = b'{"req": 1}'
        queue_file.write_bytes(expected_bytes)
    elif case == "claimed_item":
        queue_file = broker_dir / "claimed" / "clm1.json"
        expected_bytes = b'{"clm": 1}'
        queue_file.write_bytes(expected_bytes)
    elif case == "responses_item":
        queue_file = broker_dir / "responses" / "res1.json"
        expected_bytes = b'{"res": 1}'
        queue_file.write_bytes(expected_bytes)
    elif case == "requests_tmp":
        queue_file = broker_dir / "requests" / "pending.tmp"
        expected_bytes = b"temporary"
        queue_file.write_bytes(expected_bytes)
    elif case == "held_client_lock":
        held_fd = os.open(client_lock, os.O_RDWR)
        fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif case == "held_runner_lock":
        held_fd = os.open(runner_lock, os.O_RDWR)
        fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif case == "stop_normal":
        (state_root / "STOP").write_text("halt")
    elif case == "stop_broken_symlink":
        os.symlink(str(state_root / "does_not_exist"), str(state_root / "STOP"))
    elif case == "status_symlink":
        target = broker_dir / "real_status.json"
        target.write_text(json.dumps({"state": "IDLE", "updated_at": time.time()}))
        status_file.unlink()
        os.symlink(str(target), str(status_file))
    elif case == "status_hardlink":
        link_target = broker_dir / "status_link.json"
        os.link(str(status_file), str(link_target))
    elif case == "status_fifo":
        status_file.unlink()
        os.mkfifo(str(status_file))
    elif case == "status_oversized":
        status_file.write_bytes(b" " * (broker.MAX_REQUEST_BYTES + 1))

    try:
        with ap.exclusive_supervisor_lock(sup_lock):
            with pytest.raises(ap.AutopilotError):
                gate.validate_legacy_preparation_startup(root, state_root)
    finally:
        if held_fd is not None:
            fcntl.flock(held_fd, fcntl.LOCK_UN)
            os.close(held_fd)

    if queue_file is not None:
        assert queue_file.is_file()
        assert queue_file.read_bytes() == expected_bytes

    with broker.exclusive_broker_lock(client_lock):
        pass
    with broker.exclusive_broker_lock(runner_lock):
        pass


def test_package_and_standalone_imports():
    import scripts.two_site_codex_autopilot as ap_pkg
    import scripts.two_site_preparation_runtime_gate as gate_pkg

    module_path = Path(gate_pkg.__file__).resolve()
    assert module_path.is_file()
    assert callable(gate_pkg.validate_legacy_preparation_startup)

    try:
        gate_pkg.validate_legacy_preparation_startup(None, None)
    except ap_pkg.AutopilotError:
        pass
    else:
        raise AssertionError("expected AutopilotError from package gate")

    scripts_dir = str(module_path.parent)
    code = """
import two_site_preparation_runtime_gate as g
assert callable(g.validate_legacy_preparation_startup)
import two_site_codex_autopilot as ap
try:
    g.validate_legacy_preparation_startup(None, None)
except ap.AutopilotError:
    pass
else:
    raise AssertionError('expected AutopilotError')
"""
    cmd = [sys.executable, "-c", code]
    env = dict(os.environ)
    env["PYTHONPATH"] = scripts_dir
    res = subprocess.run(cmd, cwd=scripts_dir, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Standalone import failed: {res.stderr}"
