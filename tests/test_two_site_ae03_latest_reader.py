#!/usr/bin/env python3
"""Hermetic characterization tests for AE03 latest rescope receipt reader."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

try:
    from scripts.two_site_codex_autopilot import (
        AutopilotError,
        CONTROL_PATHS,
        PROTECTED_REFS,
        _RESCOPE_RECEIPT_FILENAMES,
        load_latest_rescope_receipt,
        load_rescope_receipt,
        serialize_rescope_receipt,
    )
except ModuleNotFoundError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        AutopilotError,
        CONTROL_PATHS,
        PROTECTED_REFS,
        _RESCOPE_RECEIPT_FILENAMES,
        load_latest_rescope_receipt,
        load_rescope_receipt,
        serialize_rescope_receipt,
    )

HEAD_SHA = "014e48d53918f838b1e2b1a407ed180575f8bd08"
OTHER_HEAD_SHA = "1234567890abcdef1234567890abcdef12345678"
DUMMY_SHA = "a" * 40


def _make_valid_repo(head: str = HEAD_SHA) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: DUMMY_SHA for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): DUMMY_SHA for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "test-assignment",
            "stage_id": "P1-00b",
            "sha256": "b" * 64,
            "expires_at": "2026-12-31T00:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


def _make_sample_result(head: str = HEAD_SHA) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "Rescope needed for next iteration",
        "commit": None,
        "next_action": "Rescope assignment boundaries",
        "blockers": ["dependency issue"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


@pytest.fixture
def state_root():
    tmp = tempfile.mkdtemp()
    root = Path(tmp).resolve()
    os.chmod(root, 0o700)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize(
    ("presence", "expected_outcome"),
    [
        ((False, False, False), "none"),
        ((True, False, False), 1),
        ((True, True, False), 2),
        ((True, True, True), 3),
        ((False, True, False), "error"),
        ((False, False, True), "error"),
        ((False, True, True), "error"),
        ((True, False, True), "error"),
    ],
)
def test_all_eight_presence_combinations(state_root, presence, expected_outcome):
    repo = _make_valid_repo()
    filenames = list(_RESCOPE_RECEIPT_FILENAMES.keys())
    for idx, is_present in enumerate(presence, start=1):
        filename = filenames[idx - 1]
        if is_present:
            payload = serialize_rescope_receipt(_make_sample_result(), repo, attempt=idx)
            p = state_root / filename
            p.write_bytes(payload)
            os.chmod(p, 0o600)

    if expected_outcome == "none":
        result = load_latest_rescope_receipt(state_root, repo)
        assert result is None
    elif isinstance(expected_outcome, int):
        result = load_latest_rescope_receipt(state_root, repo)
        assert result is not None
        assert result["attempt"] == expected_outcome
        assert result["schema_version"] == 1
    else:
        with pytest.raises(AutopilotError, match="non-contiguous rescope receipt sequence"):
            load_latest_rescope_receipt(state_root, repo)


def test_malformed_leaf_propagates_without_fallback(state_root):
    repo = _make_valid_repo()
    filenames = list(_RESCOPE_RECEIPT_FILENAMES.keys())

    # Leaf 1 valid, leaf 2 malformed JSON -> must fail closed, no fallback to receipt 1
    payload1 = serialize_rescope_receipt(_make_sample_result(), repo, attempt=1)
    p1 = state_root / filenames[0]
    p1.write_bytes(payload1)
    os.chmod(p1, 0o600)

    p2 = state_root / filenames[1]
    p2.write_bytes(b'{"malformed_json": ')
    os.chmod(p2, 0o600)

    with pytest.raises(AutopilotError):
        load_latest_rescope_receipt(state_root, repo)

    # Leaf 1 and 2 valid, leaf 3 malformed JSON -> must fail closed, no fallback to receipt 2
    payload2 = serialize_rescope_receipt(_make_sample_result(), repo, attempt=2)
    p2.write_bytes(payload2)
    os.chmod(p2, 0o600)

    p3 = state_root / filenames[2]
    p3.write_bytes(b"not-a-valid-envelope")
    os.chmod(p3, 0o600)

    with pytest.raises(AutopilotError):
        load_latest_rescope_receipt(state_root, repo)


def test_weak_mode_propagates_without_fallback(state_root):
    repo = _make_valid_repo()
    filenames = list(_RESCOPE_RECEIPT_FILENAMES.keys())

    # Leaf 1 valid (0600), leaf 2 has weak permissions 0644 -> must fail closed
    p1 = state_root / filenames[0]
    p1.write_bytes(serialize_rescope_receipt(_make_sample_result(), repo, attempt=1))
    os.chmod(p1, 0o600)

    p2 = state_root / filenames[1]
    p2.write_bytes(serialize_rescope_receipt(_make_sample_result(), repo, attempt=2))
    os.chmod(p2, 0o644)

    with pytest.raises(AutopilotError, match="rescope receipt mode must be 0600"):
        load_latest_rescope_receipt(state_root, repo)


def test_stale_repository_propagates_without_fallback(state_root):
    repo_current = _make_valid_repo(head=HEAD_SHA)
    repo_stale = _make_valid_repo(head=OTHER_HEAD_SHA)
    filenames = list(_RESCOPE_RECEIPT_FILENAMES.keys())

    # Leaf 1 is valid under repo_current
    p1 = state_root / filenames[0]
    p1.write_bytes(serialize_rescope_receipt(_make_sample_result(HEAD_SHA), repo_current, attempt=1))
    os.chmod(p1, 0o600)

    # Reading with repo_stale must fail closed and never return leaf 1
    with pytest.raises(AutopilotError, match="receipt repository facts do not match expected repository facts"):
        load_latest_rescope_receipt(state_root, repo_stale)


def test_no_file_mutation_or_creation(state_root):
    repo = _make_valid_repo()
    filenames = list(_RESCOPE_RECEIPT_FILENAMES.keys())
    for idx, fn in enumerate(filenames, start=1):
        p = state_root / fn
        p.write_bytes(serialize_rescope_receipt(_make_sample_result(), repo, attempt=idx))
        os.chmod(p, 0o600)

    before_snapshot = {}
    for item in sorted(state_root.iterdir()):
        st = item.stat(follow_symlinks=False)
        before_snapshot[item.name] = (st.st_size, st.st_mtime_ns, item.read_bytes())

    result = load_latest_rescope_receipt(state_root, repo)
    assert result is not None
    assert result["attempt"] == 3

    after_snapshot = {}
    for item in sorted(state_root.iterdir()):
        st = item.stat(follow_symlinks=False)
        after_snapshot[item.name] = (st.st_size, st.st_mtime_ns, item.read_bytes())

    assert before_snapshot == after_snapshot


def test_all_three_leaves_inspected_in_order_and_no_arbitrary_scan(state_root, monkeypatch):
    repo = _make_valid_repo()
    call_log: list[str] = []
    real_load = load_rescope_receipt

    def spy_load(sr, er, filename):
        call_log.append(filename)
        return real_load(sr, er, filename)

    monkeypatch.setattr("scripts.two_site_codex_autopilot.load_rescope_receipt", spy_load)

    # Place an unexpected extraneous file in directory; reader must not touch or scan it
    extra = state_root / "rescope_receipt_4.json"
    extra.write_text("arbitrary content")

    result = load_latest_rescope_receipt(state_root, repo)
    assert result is None
    assert call_log == list(_RESCOPE_RECEIPT_FILENAMES.keys())
