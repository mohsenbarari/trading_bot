"""Hermetic tests for two-site quota continuation terminal storage static tamper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import stat
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    MAX_TERMINAL_CONTINUATION_RECORD_BYTES,
    MAX_TERMINAL_RECORDS_CAPACITY,
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


def _setup_fresh_intent_scenario(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> tuple[Path, dict[str, Any], dict[str, Any], Path]:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]
    sc = ctx["sc"]
    resume_request = ctx["resume_request"]
    now_epoch = ctx["now_epoch"]

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
    return root, resume_request, response, term_dir


def _setup_published_receipt_scenario(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> tuple[Path, dict[str, Any], Path, bytes]:
    root, resume_request, response, term_dir = _setup_fresh_intent_scenario(broker_repo)
    publish_terminal_continuation_record(root, resume_request, response)
    term_file = term_dir / f"{resume_request['request_id']}.json"
    valid_bytes = term_file.read_bytes()
    return root, resume_request, term_file, valid_bytes


def test_reader_rejects_symlink_target_without_outside_mutation(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    tmp_path: Path,
) -> None:
    root, resume_request, term_file, _ = _setup_published_receipt_scenario(broker_repo)
    outside_file = tmp_path / "outside_target.json"
    outside_content = b'{"outside":"immutable_payload"}'
    outside_file.write_bytes(outside_content)
    outside_file.chmod(0o600)
    term_file.unlink()
    term_file.symlink_to(outside_file)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="is not a regular file",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])

    assert outside_file.read_bytes() == outside_content


def test_reader_rejects_hardlink_target(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, term_file, _ = _setup_published_receipt_scenario(broker_repo)
    hardlink_file = root / "extra_receipt_link.json"
    os.link(term_file, hardlink_file)
    assert term_file.stat().st_nlink > 1

    with pytest.raises(
        TerminalContinuationStorageError,
        match="invalid mode or link count",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])


def test_reader_rejects_wrong_mode_target(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, term_file, _ = _setup_published_receipt_scenario(broker_repo)
    term_file.chmod(0o644)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="invalid mode or link count",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])


def test_reader_rejects_oversize_target(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, term_file, _ = _setup_published_receipt_scenario(broker_repo)
    with open(term_file, "r+b") as f:
        f.truncate(MAX_TERMINAL_CONTINUATION_RECORD_BYTES + 1)
    assert term_file.stat().st_size == MAX_TERMINAL_CONTINUATION_RECORD_BYTES + 1

    with pytest.raises(
        TerminalContinuationStorageError,
        match="size out of bounds",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])


@pytest.mark.parametrize(
    ("tamper_mode", "expected_match"),
    [
        ("whitespace", "receipt payload must be canonical unique JSON dict"),
        ("duplicate_key", "duplicate JSON key in receipt"),
    ],
)
def test_reader_rejects_noncanonical_and_duplicate_key_json(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    tamper_mode: str,
    expected_match: str,
) -> None:
    root, resume_request, term_file, valid_bytes = _setup_published_receipt_scenario(
        broker_repo
    )
    if tamper_mode == "whitespace":
        tampered_bytes = valid_bytes + b" "
    elif tamper_mode == "duplicate_key":
        tampered_bytes = valid_bytes.replace(
            b'"schema_version":"1.0"',
            b'"schema_version":"1.0","schema_version":"1.0"',
            1,
        )
    else:
        raise AssertionError(f"unexpected tamper_mode: {tamper_mode!r}")

    term_file.write_bytes(tampered_bytes)
    term_file.chmod(0o600)

    with pytest.raises(TerminalContinuationStorageError, match=expected_match):
        read_terminal_continuation_record(root, resume_request["request_id"])


@pytest.mark.parametrize(
    ("symlink_target_name", "error_comp"),
    [
        (TERMINAL_DIR_NAME, TERMINAL_DIR_NAME),
        ("provider-broker", "provider-broker"),
    ],
)
def test_writer_rejects_symlink_directory(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    tmp_path: Path,
    symlink_target_name: str,
    error_comp: str,
) -> None:
    root, resume_request, response, term_dir = _setup_fresh_intent_scenario(broker_repo)
    if symlink_target_name == TERMINAL_DIR_NAME:
        symlink_path = term_dir
    elif symlink_target_name == "provider-broker":
        symlink_path = term_dir.parent
    else:
        raise AssertionError(f"unexpected symlink_target_name: {symlink_target_name!r}")

    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    if symlink_path.is_symlink() or symlink_path.is_file():
        symlink_path.unlink()
    elif symlink_path.is_dir():
        shutil.rmtree(symlink_path)

    outside_dir = tmp_path / f"outside_{symlink_target_name}"
    outside_dir.mkdir(mode=0o700)
    canary = outside_dir / "canary.txt"
    canary.write_text("outside_intact", encoding="utf-8")
    symlink_path.symlink_to(outside_dir)

    with pytest.raises(
        TerminalContinuationStorageError,
        match=f"{error_comp} is not a directory or is a symlink",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    assert canary.read_text(encoding="utf-8") == "outside_intact"
    assert list(outside_dir.iterdir()) == [canary]
    assert not (outside_dir / f"{resume_request['request_id']}.json").exists()


def test_writer_rejects_wrong_mode_terminal_directory(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, response, term_dir = _setup_fresh_intent_scenario(broker_repo)
    term_dir.parent.mkdir(parents=True, exist_ok=True)
    term_dir.parent.chmod(0o700)
    term_dir.mkdir(exist_ok=True)
    term_dir.chmod(0o755)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="terminal-continuation mode must be 0700",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    target = term_dir / f"{resume_request['request_id']}.json"
    assert not target.exists()


def test_writer_rejects_unexpected_directory_entry_no_pruning(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, response, term_dir = _setup_fresh_intent_scenario(broker_repo)
    term_dir.parent.mkdir(parents=True, exist_ok=True)
    term_dir.parent.chmod(0o700)
    term_dir.mkdir(exist_ok=True)
    term_dir.chmod(0o700)
    unexpected = term_dir / "unexpected.txt"
    unexpected.write_text("unexpected_data", encoding="utf-8")
    unexpected.chmod(0o600)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="unexpected entry in terminal directory",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    target = term_dir / f"{resume_request['request_id']}.json"
    assert not target.exists()
    assert unexpected.is_file()
    assert unexpected.read_text(encoding="utf-8") == "unexpected_data"
    assert os.listdir(term_dir) == ["unexpected.txt"]


def test_writer_rejects_capacity_limit_no_pruning(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, resume_request, response, term_dir = _setup_fresh_intent_scenario(broker_repo)
    term_dir.parent.mkdir(parents=True, exist_ok=True)
    term_dir.parent.chmod(0o700)
    term_dir.mkdir(exist_ok=True)
    term_dir.chmod(0o700)
    existing_entries = [f"{i:032x}.json" for i in range(MAX_TERMINAL_RECORDS_CAPACITY)]
    assert resume_request["request_id"] not in existing_entries

    for name in existing_entries:
        p = term_dir / name
        p.write_text("{}", encoding="utf-8")
        p.chmod(0o600)

    with pytest.raises(
        TerminalContinuationStorageError,
        match="terminal directory capacity limit reached",
    ):
        publish_terminal_continuation_record(root, resume_request, response)

    target = term_dir / f"{resume_request['request_id']}.json"
    assert not target.exists()
    assert len(os.listdir(term_dir)) == MAX_TERMINAL_RECORDS_CAPACITY
    assert set(os.listdir(term_dir)) == set(existing_entries)
