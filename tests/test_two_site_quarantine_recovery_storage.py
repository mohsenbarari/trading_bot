"""Hermetic unit tests for scripts/two_site_quarantine_recovery_storage.py under AE03."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterator
import pytest

try:
    from scripts.two_site_maintenance_transition_record import PROTECTED_REFS
except ImportError:
    from two_site_maintenance_transition_record import PROTECTED_REFS  # type: ignore[no-redef]

try:
    import scripts.two_site_quarantine_recovery_storage as storage_mod
    from scripts.two_site_quarantine_recovery_storage import (
        MAX_ENVELOPE_BYTES,
        QuarantineRecoveryStorageError,
        RECOVERY_LEAF_NAME,
        publish_quarantine_recovery_locked,
        read_quarantine_recovery_locked,
    )
except ImportError:
    import two_site_quarantine_recovery_storage as storage_mod  # type: ignore[no-redef]
    from two_site_quarantine_recovery_storage import (  # type: ignore[no-redef]
        MAX_ENVELOPE_BYTES,
        QuarantineRecoveryStorageError,
        RECOVERY_LEAF_NAME,
        publish_quarantine_recovery_locked,
        read_quarantine_recovery_locked,
    )


@contextmanager
def _mock_lock(root: Path) -> Iterator[None]:
    yield


def _make_fixtures(base_dir: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], bytes]:
    root = (base_dir / "repo").resolve()
    state_root = (root / ".local" / "two-site-refactor" / "autopilot").resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)

    protected = {ref: "3" * 40 for ref in sorted(PROTECTED_REFS)}

    # Unrelated 40-hex head fixture unrelated to 6d4
    chk_head = "1a2b3c4d5e" * 4
    prior_trans_bytes = b'{"dummy":"prior"}\n'
    prior_trans_sha = hashlib.sha256(prior_trans_bytes).hexdigest()

    runs_dir = state_root / "runs" / "failed-run-001"
    runs_dir.mkdir(parents=True, exist_ok=True)
    failed_res_file = runs_dir / "result.json"
    failed_res_bytes = b'{"state":"BLOCKED"}\n'
    failed_res_file.write_bytes(failed_res_bytes)
    os.chmod(failed_res_file, 0o600)
    failed_res_sha = hashlib.sha256(failed_res_bytes).hexdigest()

    chk_sha = "9" * 64
    chk_obj = {"repository": {"head": chk_head}}

    new_repo = {
        "assignment_scope": {
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "assignment_id": "aid-1",
            "expires_at": "2026-10-02T00:00:00Z",
            "forbidden_paths": [],
            "sha256": "1" * 64,
            "stage_id": "P1-00b",
        },
        "control_blobs": {"scripts/two_site_codex_autopilot.py": "2" * 40},
        "head": "7" * 40,
        "protected_refs": copy.deepcopy(protected),
        "status": "",
    }

    pct_dict = {
        "command": "pytest tests/test_two_site_quarantine_recovery_storage.py -q",
        "passed_count": 12,
        "returncode": 0,
    }
    val_dict = {
        "postcommit_tests": pct_dict,
        "repair_evidence": {
            "gemini_reviewer_run_id": "rev-run-1",
            "gemini_worker_run_id": "work-run-1",
            "proposal_sha256": "a" * 64,
        },
        "repository_head": "7" * 40,
        "schema_version": "1.0",
    }
    val_canon = json.dumps(val_dict, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    val_sha = hashlib.sha256(val_canon).hexdigest()

    rec_dict = {
        "failed_commit_head": "e" * 40,
        "failed_result_sha256": failed_res_sha,
        "failed_run_id": "failed-run-001",
        "final_validation_sha256": val_sha,
        "followup_commits": [{
            "head": "7" * 40,
            "parent": "6" * 40,
            "paths": ["scripts/two_site_codex_autopilot.py"],
            "proposal_sha256": "8" * 64,
            "reviewer_run_id": "rev-f",
            "test_evidence_sha256": "9" * 64,
            "worker_run_id": "work-f",
        }],
        "kind": "QUARANTINED_CYCLE_RECOVERY",
        "new_repository": new_repo,
        "prior_checkpoint_sha256": chk_sha,
        "prior_maintenance_transition_sha256": prior_trans_sha,
        "repair_commit_head": "6" * 40,
        "schema_version": "1.0",
    }

    envelope = {
        "record": rec_dict,
        "schema_version": "1.0",
        "validation": val_dict,
    }
    return root, state_root, envelope, chk_obj, prior_trans_bytes


def _apply_mock_env(
    monkeypatch: pytest.MonkeyPatch,
    chk_obj: dict[str, Any],
    chk_sha: str,
    prior_trans_bytes: bytes,
    prior_head: str,
    new_repo: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        storage_mod, "hold_idle_broker_client_lock_for_cold_adoption", _mock_lock
    )
    monkeypatch.setattr(
        storage_mod,
        "_pin_validated_root",
        lambda *args, **kwargs: os.open(str(args[0] if args else kwargs["root"]), os.O_RDONLY),
    )
    monkeypatch.setattr(
        storage_mod, "_check_stop_and_pending", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        storage_mod, "_load_and_audit_accepted_checkpoint", lambda *args, **kwargs: (chk_obj, chk_sha)
    )
    chk_head = chk_obj["repository"]["head"]
    monkeypatch.setattr(
        storage_mod,
        "_validate_persisted_repository",
        lambda *args, **kwargs: {"head": chk_head, "protected_refs": new_repo["protected_refs"]},
    )
    monkeypatch.setattr(storage_mod, "_recheck_repository", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage_mod, "_recheck_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage_mod, "open_maintenance_transitions_dir", lambda *args, **kwargs: 1000)
    monkeypatch.setattr(storage_mod, "list_maintenance_transition_leaves", lambda *args, **kwargs: ["t1.json"])
    monkeypatch.setattr(storage_mod, "read_maintenance_transition_bytes", lambda *args, **kwargs: prior_trans_bytes)
    monkeypatch.setattr(
        storage_mod, "parse_maintenance_transition_record", lambda *args, **kwargs: {"prior_head": prior_head}
    )
    monkeypatch.setattr(storage_mod, "validate_repository", lambda *args, **kwargs: new_repo)
    monkeypatch.setattr(storage_mod, "validate_accepted_repository", lambda *args, **kwargs: new_repo)
    monkeypatch.setattr(
        storage_mod, "prove_quarantined_pair", lambda *args, **kwargs: {"status": "PROVEN"}
    )
    monkeypatch.setattr(storage_mod, "prove_recovery_followups", lambda *args, **kwargs: {"status": "PROVEN"})


def test_publish_and_read_success_positive_unrelated_6d4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    # Unrelated other.tmp in state_root must be ignored by temp file scanning
    other_tmp = state_root / "other.tmp"
    other_tmp.write_text("unrelated temp file\n", encoding="utf-8")

    pub_res = publish_quarantine_recovery_locked(root, state_root, envelope)
    assert pub_res["schema_version"] == "1.0"
    assert pub_res["record"]["kind"] == "QUARANTINED_CYCLE_RECOVERY"
    assert (state_root / RECOVERY_LEAF_NAME).is_file()
    assert other_tmp.is_file()

    read_res = read_quarantine_recovery_locked(root, state_root)
    assert read_res == pub_res

    # Second publish fails as leaf already exists
    with pytest.raises(QuarantineRecoveryStorageError, match="already exists"):
        publish_quarantine_recovery_locked(root, state_root, envelope)

    # When leaf does not exist, reader returns None
    (state_root / RECOVERY_LEAF_NAME).unlink()
    assert read_quarantine_recovery_locked(root, state_root) is None


def test_checkpoint_sha_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = "0" * 64
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Checkpoint SHA256 does not match record prior_checkpoint_sha256",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)


def test_protected_refs_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    chk_head = chk_obj["repository"]["head"]
    mismatched_refs = copy.deepcopy(new_repo["protected_refs"])
    first_ref = sorted(mismatched_refs.keys())[0]
    mismatched_refs[first_ref] = "4" * 40
    monkeypatch.setattr(
        storage_mod,
        "_validate_persisted_repository",
        lambda *args, **kwargs: {"head": chk_head, "protected_refs": mismatched_refs},
    )

    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Checkpoint repository protected_refs does not match live repository protected_refs",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)


def test_own_orphan_temp_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    orphan_file = state_root / ".quarantine-recovery.orphan.1234.tmp"
    orphan_file.write_bytes(b"orphan-content")

    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Found own orphan quarantine recovery temporary artifact",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)

    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Found own orphan quarantine recovery temporary artifact",
    ):
        read_quarantine_recovery_locked(root, state_root)


def test_late_publish_stop_and_recheck_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    # 1. Late STOP check failure after link
    check_count = 0
    def _failing_stop(*args: Any, **kwargs: Any) -> None:
        nonlocal check_count
        check_count += 1
        if check_count >= 2:
            raise QuarantineRecoveryStorageError("STOP signal received")

    monkeypatch.setattr(storage_mod, "_check_stop_and_pending", _failing_stop)
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="STOP or pending recheck failed",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)

    (state_root / RECOVERY_LEAF_NAME).unlink(missing_ok=True)
    monkeypatch.setattr(storage_mod, "_check_stop_and_pending", lambda *args, **kwargs: None)

    # 2. Late repository recheck failure
    monkeypatch.setattr(
        storage_mod,
        "_recheck_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("repo head drifted")),
    )
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Repository recheck failed",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)

    (state_root / RECOVERY_LEAF_NAME).unlink(missing_ok=True)
    monkeypatch.setattr(storage_mod, "_recheck_repository", lambda *args, **kwargs: None)

    # 3. Late checkpoint recheck failure
    monkeypatch.setattr(
        storage_mod,
        "_recheck_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("checkpoint invalidated")),
    )
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Checkpoint recheck failed",
    ):
        publish_quarantine_recovery_locked(root, state_root, envelope)

    (state_root / RECOVERY_LEAF_NAME).unlink(missing_ok=True)
    monkeypatch.setattr(storage_mod, "_recheck_checkpoint", lambda *args, **kwargs: None)


def test_late_reader_recheck_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    publish_quarantine_recovery_locked(root, state_root, envelope)

    monkeypatch.setattr(
        storage_mod,
        "_recheck_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late checkpoint failure")),
    )
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Checkpoint recheck failed",
    ):
        read_quarantine_recovery_locked(root, state_root)


def test_leaf_symlink_and_hardlink_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state_root, envelope, chk_obj, prior_trans_bytes = _make_fixtures(tmp_path)
    chk_sha = envelope["record"]["prior_checkpoint_sha256"]
    prior_head = chk_obj["repository"]["head"]
    new_repo = envelope["record"]["new_repository"]
    _apply_mock_env(monkeypatch, chk_obj, chk_sha, prior_trans_bytes, prior_head, new_repo)

    target_file = state_root / "target_file.json"
    target_file.write_bytes(b"{}")
    os.chmod(target_file, 0o600)

    leaf = state_root / RECOVERY_LEAF_NAME

    # Symlink rejection
    leaf.symlink_to(target_file)
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Recovery leaf must not be a symlink",
    ):
        read_quarantine_recovery_locked(root, state_root)
    leaf.unlink()

    # Hardlink rejection (st_nlink > 1)
    os.link(str(target_file), str(leaf))
    with pytest.raises(
        QuarantineRecoveryStorageError,
        match="Recovery leaf must not have hard links",
    ):
        read_quarantine_recovery_locked(root, state_root)
