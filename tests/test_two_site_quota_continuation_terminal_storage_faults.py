"""Hermetic dynamic storage fault tests for two-site quota continuation terminal storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import stat
import traceback
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    TERMINAL_DIR_NAME,
    TerminalContinuationStorageError,
    publish_terminal_continuation_record,
    read_terminal_continuation_record,
)
from scripts.two_site_quota_resume_submission_intent import (
    publish_quota_resume_submission_intent,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


def _setup_intent_and_response(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any], Path, dict[Path, bytes]]:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root: Path = ctx["root"]
    sc: dict[str, Any] = ctx["sc"]
    resume_request: dict[str, Any] = ctx["resume_request"]
    now_epoch: float = ctx["now_epoch"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        publish_quota_resume_submission_intent(
            root, resume_request, now_epoch=now_epoch
        )

    response = build_continuation_broker_response(
        resume_request, {"state": "READY_FOR_CODEX"}, now_epoch=now_epoch
    )
    term_dir = (
        root / ".local" / "two-site-refactor" / "provider-broker" / TERMINAL_DIR_NAME
    )
    runs_dir: Path = ctx["runs_dir"]
    artifacts_snapshot = {p: p.read_bytes() for p in runs_dir.glob("*.json")}
    assert ctx["claim_file"] in artifacts_snapshot
    return ctx, root, resume_request, response, term_dir, artifacts_snapshot


def test_scenario_a_pre_link_failure_cleans_temp_preserves_intent(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, root, resume_request, response, term_dir, artifacts_snapshot = (
        _setup_intent_and_response(broker_repo)
    )
    target_path = term_dir / f"{resume_request['request_id']}.json"

    def fail_link(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.EIO, "simulated pre-link disk error")

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(TerminalContinuationStorageError, match="cannot link target"):
        publish_terminal_continuation_record(root, resume_request, response)

    assert not target_path.exists()
    if term_dir.exists():
        temp_files = [p for p in term_dir.iterdir() if p.name.startswith(".")]
        assert temp_files == []

    for path, expected_bytes in artifacts_snapshot.items():
        assert path.is_file()
        assert path.read_bytes() == expected_bytes


def test_scenario_b_post_link_temp_unlink_failure_ambiguous_preserves_target(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, root, resume_request, response, term_dir, artifacts_snapshot = (
        _setup_intent_and_response(broker_repo)
    )
    target_path = term_dir / f"{resume_request['request_id']}.json"

    real_unlink = os.unlink

    def fail_temp_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        p_str = os.fspath(path) if not isinstance(path, int) else ""
        if ".tmp." in p_str:
            raise OSError(errno.EIO, "simulated post-link temp unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_temp_unlink)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="ambiguous post-link publication failure: cannot unlink temp",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    assert target_path.is_file()
    st = target_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 2

    with pytest.raises(
        TerminalContinuationStorageError,
        match="invalid mode or link count",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])

    assert target_path.is_file()
    for path, expected_bytes in artifacts_snapshot.items():
        assert path.is_file()
        assert path.read_bytes() == expected_bytes


def test_scenario_c_post_link_directory_fsync_failure_ambiguous_no_replay(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, root, resume_request, response, term_dir, artifacts_snapshot = (
        _setup_intent_and_response(broker_repo)
    )
    target_path = term_dir / f"{resume_request['request_id']}.json"

    real_fsync = os.fsync

    def fail_dir_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "simulated directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_dir_fsync)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="ambiguous post-link publication failure: cannot fsync directory",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    assert target_path.is_file()
    st = target_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    canonical_bytes = target_path.read_bytes()

    with pytest.raises(
        TerminalContinuationStorageError,
        match="terminal record already exists",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    assert target_path.read_bytes() == canonical_bytes

    monkeypatch.undo()
    read_record = read_terminal_continuation_record(
        root, resume_request["request_id"], expected_request=resume_request
    )
    assert read_record["request_id"] == resume_request["request_id"]

    for path, expected_bytes in artifacts_snapshot.items():
        assert path.is_file()
        assert path.read_bytes() == expected_bytes


def test_scenario_d_parent_path_swap_during_scan_caught_post_link(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx, root, resume_request, response, term_dir, artifacts_snapshot = (
        _setup_intent_and_response(broker_repo)
    )
    parent_dir = term_dir.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    moved_parent = parent_dir.with_name(f"{parent_dir.name}.moved")

    outside_dir = tmp_path / "outside_canary_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    canary_file = outside_dir / "canary.txt"
    canary_file.write_text("outside_canary_intact", encoding="utf-8")

    real_listdir = os.listdir
    swapped = False

    def listdir_hook(path: Any = None) -> list[str]:
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(parent_dir, moved_parent)
            os.symlink(outside_dir, parent_dir)
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", listdir_hook)

    try:
        with pytest.raises(
            TerminalContinuationStorageError,
            match="ambiguous post-link publication failure",
        ):
            publish_terminal_continuation_record(root, resume_request, response)

        assert canary_file.read_text(encoding="utf-8") == "outside_canary_intact"
        assert list(outside_dir.iterdir()) == [canary_file]

        moved_target = (
            moved_parent / TERMINAL_DIR_NAME / f"{resume_request['request_id']}.json"
        )
        assert moved_target.is_file()
    finally:
        if parent_dir.is_symlink():
            parent_dir.unlink()
        if moved_parent.exists():
            os.rename(moved_parent, parent_dir)

    target_path = term_dir / f"{resume_request['request_id']}.json"
    assert target_path.is_file()
    for path, expected_bytes in artifacts_snapshot.items():
        assert path.is_file()
        assert path.read_bytes() == expected_bytes


def test_scenario_e_sensitive_response_marker_suppressed_in_error(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    ctx, root, resume_request, response, term_dir, artifacts_snapshot = (
        _setup_intent_and_response(broker_repo)
    )
    target_path = term_dir / f"{resume_request['request_id']}.json"

    marker = "SENSITIVE_RESPONSE_MARKER_AE03_SECRET"
    bad_response = dict(response, stderr=marker)

    with pytest.raises(TerminalContinuationStorageError) as exc_info:
        publish_terminal_continuation_record(root, resume_request, bad_response)

    err = exc_info.value
    err_str = str(err)
    tb_str = "".join(traceback.format_exception(err))

    assert marker not in err_str
    assert marker not in tb_str
    assert not target_path.exists()

    for path, expected_bytes in artifacts_snapshot.items():
        assert path.is_file()
        assert path.read_bytes() == expected_bytes
