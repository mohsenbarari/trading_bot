"""Hermetic tests for proposal-runner lock and controlled STOP ordering."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterator

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import pytest

from scripts.two_site_controlled_stop import (
    ControlledStopError,
    publish_controlled_stop,
)
from scripts.two_site_proposal_runner import (
    acquire_proposal_lock,
    proposal_cli_context,
    release_proposal_lock,
)
from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_broker_route import _check_pre_dispatch_stop

mp_ctx = (
    multiprocessing.get_context("fork")
    if "fork" in multiprocessing.get_all_start_methods()
    else multiprocessing.get_context()
)


def _runner_provider_first_target(root: Path, cli: Path, pipe_conn: Any) -> None:
    try:
        with proposal_cli_context(root, cli=cli) as (_local, _disposable_cli, _evidence):
            _check_pre_dispatch_stop(root)
            true_binary = shutil.which("true") or "/bin/true"
            proc = subprocess.Popen(
                [true_binary], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            proc.wait(timeout=10)
            pipe_conn.send({"status": "POPEN_COMPLETE", "pid": proc.pid})
            release_cmd = pipe_conn.recv()
            if release_cmd != "RELEASE":
                raise RuntimeError(f"unexpected handshake message: {release_cmd}")
        pipe_conn.send({"status": "LOCK_RELEASED"})
    except Exception as exc:
        pipe_conn.send({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})


def _publisher_target(
    root: Path,
    pipe_conn: Any,
    lock_wait_seconds: float = 30.0,
) -> None:
    try:
        pipe_conn.send({"status": "PUBLISHER_STARTED"})
        stop_path = publish_controlled_stop(root, lock_wait_seconds=lock_wait_seconds)
        pipe_conn.send({"status": "PUBLISHED", "stop_path": str(stop_path)})
    except Exception as exc:
        pipe_conn.send({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})


def _runner_stop_first_target(root: Path, cli: Path, pipe_conn: Any) -> None:
    popen_called = False
    try:
        with proposal_cli_context(root, cli=cli) as (_local, _disposable_cli, _evidence):
            try:
                _check_pre_dispatch_stop(root)
            except BrokerError as exc:
                pipe_conn.send(
                    {
                        "status": "GUARD_BLOCKED",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "popen_called": False,
                    }
                )
                return
            true_binary = shutil.which("true") or "/bin/true"
            proc = subprocess.Popen(
                [true_binary], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            popen_called = True
            proc.wait(timeout=10)
            pipe_conn.send({"status": "POPEN_CALLED", "popen_called": True})
    except Exception as exc:
        pipe_conn.send(
            {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "popen_called": popen_called,
            }
        )


@pytest.fixture
def private_root() -> Iterator[Path]:
    temp_dir = tempfile.mkdtemp(prefix="two_site_test_")
    root = Path(temp_dir).resolve()
    root.chmod(0o700)
    cli_path = root / "dummy_cli.sh"
    cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli_path.chmod(0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_provider_first_ordering_holds_lock_until_release_handshake(
    private_root: Path,
) -> None:
    cli_path = private_root / "dummy_cli.sh"
    runner_parent_conn, runner_child_conn = mp_ctx.Pipe()
    pub_parent_conn, pub_child_conn = mp_ctx.Pipe()

    runner_proc = mp_ctx.Process(
        target=_runner_provider_first_target,
        args=(private_root, cli_path, runner_child_conn),
    )
    pub_proc = mp_ctx.Process(
        target=_publisher_target,
        args=(private_root, pub_child_conn, 30.0),
    )

    try:
        runner_proc.start()
        assert runner_parent_conn.poll(timeout=10.0), "timed out waiting for runner POPEN_COMPLETE"
        runner_msg = runner_parent_conn.recv()
        assert runner_msg.get("status") == "POPEN_COMPLETE", f"runner error: {runner_msg}"

        stop_path = private_root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        assert not stop_path.exists()

        pub_proc.start()
        assert pub_parent_conn.poll(timeout=10.0), "timed out waiting for publisher start"
        pub_start_msg = pub_parent_conn.recv()
        assert pub_start_msg.get("status") == "PUBLISHER_STARTED"

        # Publisher must be blocked waiting on the runner lock; verify STOP remains absent
        time.sleep(0.05)
        assert not stop_path.exists()
        assert not pub_parent_conn.poll(0.05)
        assert pub_proc.is_alive()
        assert not stop_path.exists()

        # Deterministic release handshake: runner exits lock context only after release
        runner_parent_conn.send("RELEASE")
        assert runner_parent_conn.poll(timeout=10.0), "timed out waiting for LOCK_RELEASED"
        runner_done_msg = runner_parent_conn.recv()
        assert runner_done_msg.get("status") == "LOCK_RELEASED"

        runner_proc.join(timeout=10.0)
        assert not runner_proc.is_alive()
        assert runner_proc.exitcode == 0

        # With lock released, publisher acquires lock and completes durable publication
        assert pub_parent_conn.poll(timeout=10.0), "timed out waiting for publisher to finish"
        pub_done_msg = pub_parent_conn.recv()
        assert pub_done_msg.get("status") == "PUBLISHED"

        pub_proc.join(timeout=10.0)
        assert not pub_proc.is_alive()
        assert pub_proc.exitcode == 0

        # Assert durable STOP properties
        assert stop_path.is_file()
        st = stop_path.stat()
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert st.st_nlink == 1
        assert stop_path.read_bytes() == b"STOP\n"
    finally:
        for p in (runner_proc, pub_proc):
            if p.is_alive():
                p.terminate()
                p.join(timeout=5.0)


def test_stop_first_ordering_blocks_popen_and_preserves_stop(
    private_root: Path,
) -> None:
    cli_path = private_root / "dummy_cli.sh"
    pub_parent_conn, pub_child_conn = mp_ctx.Pipe()
    runner_parent_conn, runner_child_conn = mp_ctx.Pipe()

    pub_proc = mp_ctx.Process(
        target=_publisher_target,
        args=(private_root, pub_child_conn, 30.0),
    )
    runner_proc = mp_ctx.Process(
        target=_runner_stop_first_target,
        args=(private_root, cli_path, runner_child_conn),
    )

    try:
        # 1. STOP-first actual controlled publication completes durably
        pub_proc.start()
        assert pub_parent_conn.poll(timeout=10.0), "timed out waiting for publisher start"
        pub_start_msg = pub_parent_conn.recv()
        assert pub_start_msg.get("status") == "PUBLISHER_STARTED"

        assert pub_parent_conn.poll(timeout=10.0), "timed out waiting for publication"
        pub_done_msg = pub_parent_conn.recv()
        assert pub_done_msg.get("status") == "PUBLISHED"

        pub_proc.join(timeout=10.0)
        assert not pub_proc.is_alive()
        assert pub_proc.exitcode == 0

        stop_path = private_root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        assert stop_path.is_file()
        st_before = stop_path.stat()
        assert stat.S_IMODE(st_before.st_mode) == 0o600
        assert st_before.st_nlink == 1
        content_before = stop_path.read_bytes()
        assert content_before == b"STOP\n"

        # 2. Child enters proposal_cli_context and invokes _check_pre_dispatch_stop
        runner_proc.start()
        assert runner_parent_conn.poll(timeout=10.0), "timed out waiting for runner message"
        runner_msg = runner_parent_conn.recv()

        assert runner_msg.get("status") == "GUARD_BLOCKED"
        assert runner_msg.get("error_type") == "BrokerError"
        assert "stop requested before continuation dispatch" in runner_msg.get("error_message", "")
        assert not runner_msg.get("popen_called")

        runner_proc.join(timeout=10.0)
        assert not runner_proc.is_alive()
        assert runner_proc.exitcode == 0

        # Assert STOP inode, content, mode, and nlink are preserved
        assert stop_path.is_file()
        st_after = stop_path.stat()
        assert st_after.st_ino == st_before.st_ino
        assert stat.S_IMODE(st_after.st_mode) == 0o600
        assert st_after.st_nlink == 1
        assert stop_path.read_bytes() == content_before
    finally:
        for p in (runner_proc, pub_proc):
            if p.is_alive():
                p.terminate()
                p.join(timeout=5.0)


def test_unsafe_marker_symlink_fails_closed(private_root: Path) -> None:
    stop_dir = private_root / ".local" / "two-site-refactor" / "autopilot"
    stop_dir.mkdir(parents=True, exist_ok=True)
    target_file = private_root / "target_file"
    target_file.write_text("target", encoding="utf-8")
    stop_path = stop_dir / "STOP"
    stop_path.symlink_to(target_file)

    with pytest.raises(ControlledStopError, match="symlink"):
        publish_controlled_stop(private_root)

    with pytest.raises(BrokerError, match="stop requested before continuation dispatch"):
        _check_pre_dispatch_stop(private_root)

    stop_path.unlink()
    stop_path.symlink_to(private_root / "nonexistent_target")

    with pytest.raises(ControlledStopError, match="symlink"):
        publish_controlled_stop(private_root)

    with pytest.raises(BrokerError, match="stop requested before continuation dispatch"):
        _check_pre_dispatch_stop(private_root)


def test_unsafe_marker_invalid_permissions_fails_publisher(private_root: Path) -> None:
    stop_dir = private_root / ".local" / "two-site-refactor" / "autopilot"
    stop_dir.mkdir(parents=True, exist_ok=True)
    stop_path = stop_dir / "STOP"
    stop_path.write_bytes(b"STOP\n")
    stop_path.chmod(0o666)

    with pytest.raises(ControlledStopError, match="existing STOP must have mode 0600"):
        publish_controlled_stop(private_root)


def test_publisher_timeout_fails_closed_when_lock_held(private_root: Path) -> None:
    lock_descriptor, _local = acquire_proposal_lock(private_root)
    stop_path = private_root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    try:
        with pytest.raises(
            ControlledStopError, match="timed out waiting for proposal runner lock"
        ):
            publish_controlled_stop(private_root, lock_wait_seconds=0.05)
        assert not stop_path.exists()
    finally:
        release_proposal_lock(lock_descriptor)
