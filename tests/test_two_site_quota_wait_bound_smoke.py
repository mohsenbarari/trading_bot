from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

try:
    from scripts import two_site_quota_wait_storage as storage_mod
    from scripts.two_site_quota_wait_schedule import initial_wait_schedule
    from scripts.two_site_quota_wait_storage import WaitStorageError, read_initial_wait_record
except ImportError:
    import two_site_quota_wait_storage as storage_mod  # type: ignore
    from two_site_quota_wait_schedule import initial_wait_schedule  # type: ignore
    from two_site_quota_wait_storage import WaitStorageError, read_initial_wait_record  # type: ignore

_CID = "cycle-2026-20260925T010203Z-123"
_EXPECTED_KEYS = (
    "controller_run_id", "request_id", "reservation", "request_sha256",
    "terminal_receipt_sha256", "response_sha256", "semantic_task_fingerprint",
    "rescope_epoch", "rescope_attempt", "quota_attempt", "quota_evidence", "repository",
)


@pytest.fixture
def wait_fixture(tmp_path: Path) -> tuple[Path, str, dict[str, Any], dict[str, Any]]:
    root = tmp_path.resolve(strict=True)
    os.chmod(root, 0o700)
    run_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / _CID
    run_dir.mkdir(parents=True, mode=0o700)
    curr = root
    for comp in (".local", "two-site-refactor", "autopilot", "runs", _CID):
        curr = curr / comp
        os.chmod(curr, 0o700)

    evidence: dict[str, Any] = {
        "kind": "QUOTA_RESET_UNKNOWN", "provider": "gemini", "model": "gemini-test",
        "conversation_id": "conv-1", "reset_at_utc": None,
    }
    schedule = initial_wait_schedule(evidence, "2026-09-25T01:03:00Z")
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "state": "WAITING_QUOTA",
        "controller_run_id": _CID,
        "request_id": "e" * 32,
        "reservation": {
            "controller_run_id": _CID, "run_prefix": "ae03.c5b-test",
            "task_path": ".local/two-site-refactor/tasks/task.json",
            "task_sha256": "b" * 64, "source_head": "a" * 40,
            "assignment_id": "assign-1", "assignment_sha256": "c" * 64,
        },
        "request_sha256": "f" * 64,
        "terminal_receipt_sha256": "1" * 64,
        "response_sha256": "2" * 64,
        "semantic_task_fingerprint": "3" * 64,
        "rescope_epoch": "rescope-" + ("4" * 64),
        "rescope_attempt": 1,
        "quota_attempt": {"role": "worker", "run_id": "ae03.c5b-worker-0", "status_sha256": "d" * 64},
        "quota_evidence": evidence,
        "schedule": schedule,
        "repository": {
            "head": "a" * 40, "status": "",
            "protected_refs": {"refs/heads/main": "a" * 40},
            "control_blobs": {".local/two-site-refactor/control.json": "a" * 40},
            "assignment_scope": {
                "assignment_id": "assign-1", "stage_id": "P1-00b", "sha256": "c" * 64,
                "expires_at": "2026-09-25T02:00:00Z",
                "allowed_paths": [".local/two-site-refactor/tasks/task.json"], "forbidden_paths": [],
            },
        },
    }
    wait_file = run_dir / "quota-wait.json"
    wait_file.write_bytes(json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    os.chmod(wait_file, 0o600)
    expected = {k: copy.deepcopy(record[k]) for k in _EXPECTED_KEYS}
    return root, _CID, record, expected


def test_read_initial_wait_record_valid_roundtrip(
    wait_fixture: tuple[Path, str, dict[str, Any], dict[str, Any]],
) -> None:
    root, cid, record, expected = wait_fixture
    result = read_initial_wait_record(root, cid, expected)
    assert result == record
    assert result is not record
    assert result is not expected
    assert result["reservation"] is not record["reservation"]


def test_read_initial_wait_record_missing_expected_key_pre_io(
    wait_fixture: tuple[Path, str, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, cid, _record, expected = wait_fixture
    del expected["request_id"]
    called = False

    def _spy(*args: object, **kwargs: object) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("read_initial_wait_record_bytes must not be called")

    monkeypatch.setattr(storage_mod, "read_initial_wait_record_bytes", _spy)
    with pytest.raises(WaitStorageError, match="expected keys"):
        read_initial_wait_record(root, cid, expected)
    assert not called
