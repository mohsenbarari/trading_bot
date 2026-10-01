"""Hermetic unit tests for scripts/two_site_quarantine_followup_proof.py under AE03."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

import scripts.two_site_quarantine_followup_proof as proof_mod
from scripts.two_site_maintenance_transition_record import (
    PROTECTED_REFS,
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)
from scripts.two_site_quarantine_followup_proof import (
    MAX_RECEIPT_BYTES,
    TARGET_BEHAVIOR_ID,
    QuarantineFollowupProofError,
    derive_receipt_path,
    prove_recovery_followups,
)
from scripts.two_site_quarantine_recovery_record import (
    build_quarantine_recovery_record,
    serialize_quarantine_recovery_record,
)

RECEIPT_FIXTURE: dict[str, Any] = {
    "isolation": "bubblewrap",
    "proposal_sha256": "8" * 64,
    "returncode": 0,
    "selectors": ["tests/test_p1_00_example.py"],
    "snapshot_bytes": 100,
    "snapshot_files": 1,
    "state": "PASSED",
    "stderr_tail": "",
    "stdout_tail": "ok",
    "unsupported_specs": [],
}


def _make_fixtures() -> tuple[dict[str, Any], bytes]:
    receipt_bytes = json.dumps(RECEIPT_FIXTURE, sort_keys=True).encode("utf-8") + b"\n"
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    protected = {ref: "3" * 40 for ref in sorted(PROTECTED_REFS)}
    prior_repo = {
        "assignment_scope": {
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "assignment_id": "aid-1",
            "expires_at": "2026-10-02T00:00:00Z",
            "forbidden_paths": [],
            "sha256": "1" * 64,
            "stage_id": "P1-00b",
        },
        "control_blobs": {"scripts/two_site_codex_autopilot.py": "2" * 40},
        "head": "1" * 40,
        "protected_refs": copy.deepcopy(protected),
        "status": "",
    }
    prior_trans = build_maintenance_transition_record(
        prior_checkpoint_sha256="9" * 64,
        prior_head="0" * 40,
        new_repository=prior_repo,
        maintenance_commits=[{
            "head": "1" * 40,
            "parent": "0" * 40,
            "paths": ["scripts/two_site_codex_autopilot.py"],
            "proposal_sha256": "4" * 64,
            "reviewer_run_id": "rev-1",
            "test_evidence_sha256": "5" * 64,
            "worker_run_id": "work-1",
        }],
    )
    prior_sha = hashlib.sha256(serialize_maintenance_transition_record(prior_trans)).hexdigest()
    recovery_repo = {
        "assignment_scope": copy.deepcopy(prior_repo["assignment_scope"]),
        "control_blobs": {"scripts/two_site_codex_autopilot.py": "2" * 40},
        "head": "7" * 40,
        "protected_refs": copy.deepcopy(protected),
        "status": "",
    }
    rec = build_quarantine_recovery_record(
        prior_checkpoint_sha256="9" * 64,
        prior_maintenance_transition_sha256=prior_sha,
        failed_run_id="failed-run-1",
        failed_result_sha256="a" * 64,
        failed_commit_head="eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4",
        repair_commit_head="6" * 40,
        new_repository=recovery_repo,
        followup_commits=[{
            "head": "7" * 40,
            "parent": "6" * 40,
            "paths": ["scripts/two_site_codex_autopilot.py"],
            "proposal_sha256": "8" * 64,
            "reviewer_run_id": "stage-reviewer-0",
            "test_evidence_sha256": receipt_sha,
            "worker_run_id": "stage-worker-0",
        }],
        final_validation_sha256="b" * 64,
    )
    return rec, receipt_bytes


def test_derive_receipt_path() -> None:
    path1 = derive_receipt_path("p1-00b-worker-0")
    assert path1 == Path(".local/two-site-refactor/pipeline/p1-00b/pre-review-tests-0.json")
    path2 = derive_receipt_path("wa-087-worker-8-retry-2")
    assert path2 == Path(".local/two-site-refactor/pipeline/wa-087/pre-review-tests-8.json")
    with pytest.raises(QuarantineFollowupProofError, match="invalid worker_run_id"):
        derive_receipt_path("stage-worker-9")
    with pytest.raises(QuarantineFollowupProofError, match="invalid worker_run_id"):
        derive_receipt_path("no-prefix-format")
    with pytest.raises(QuarantineFollowupProofError, match="worker_run_id must be a string"):
        derive_receipt_path(123)  # type: ignore[arg-type]


def test_prove_recovery_followups_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, receipt_bytes = _make_fixtures()
    git_calls: list[Any] = []
    chain_calls: list[tuple[str, Any]] = []
    prop_calls: list[tuple[str, str, Any]] = []
    evidence_calls: list[tuple[Path, str]] = []

    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: rec["new_repository"])
    monkeypatch.setattr(proof_mod, "validate_accepted_repository", lambda repo: repo)
    monkeypatch.setattr(proof_mod, "verify_declared_maintenance_git_chain", lambda r, ad: git_calls.append(ad))
    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: receipt_bytes)
    monkeypatch.setattr(
        proof_mod, "validate_gemini_chain", lambda r, src, ev: chain_calls.append((src, ev)) or {"prop": src}
    )
    monkeypatch.setattr(
        proof_mod, "validate_committed_proposal", lambda r, b, a, p: prop_calls.append((b, a, p))
    )
    monkeypatch.setattr(
        proof_mod,
        "inspect_canonical_evidence",
        lambda r, bid: evidence_calls.append((r, bid))
        or {"status": "VALID", "seed_count": 5, "requested_coverage": "PROVEN_FOR_REQUESTED_SCENARIO"},
    )

    res = prove_recovery_followups(Path("/test/root"), rec)
    assert res["followup_count"] == 1
    assert len(git_calls) == 1
    assert git_calls[0]["prior_head"] == "6" * 40
    assert len(chain_calls) == 1
    assert chain_calls[0] == (
        "6" * 40,
        {"gemini_reviewer_run_id": "stage-reviewer-0", "gemini_worker_run_id": "stage-worker-0", "proposal_sha256": "8" * 64},
    )
    assert prop_calls == [("6" * 40, "7" * 40, {"prop": "6" * 40})]
    assert evidence_calls == [(Path("/test/root"), TARGET_BEHAVIOR_ID)]

    rec_bytes = serialize_quarantine_recovery_record(rec)
    res_bytes = prove_recovery_followups(Path("/test/root"), rec_bytes)
    assert res_bytes["followup_count"] == 1


def test_wrong_head_or_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, _ = _make_fixtures()
    with pytest.raises(QuarantineFollowupProofError, match="root must be a Path"):
        prove_recovery_followups("/not/a/path", rec)  # type: ignore[arg-type]
    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: (_ for _ in ()).throw(ValueError("dirty")))
    with pytest.raises(QuarantineFollowupProofError, match="live repository validation failed"):
        prove_recovery_followups(Path("/test/root"), rec)

    mutated = copy.deepcopy(rec["new_repository"])
    mutated["head"] = "8" * 40
    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: mutated)
    monkeypatch.setattr(proof_mod, "validate_accepted_repository", lambda repo: repo)
    with pytest.raises(QuarantineFollowupProofError, match="live accepted repo does not match record"):
        prove_recovery_followups(Path("/test/root"), rec)


def test_tampered_or_failed_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, receipt_bytes = _make_fixtures()
    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: rec["new_repository"])
    monkeypatch.setattr(proof_mod, "validate_accepted_repository", lambda repo: repo)
    monkeypatch.setattr(proof_mod, "verify_declared_maintenance_git_chain", lambda r, ad: None)

    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: receipt_bytes + b" ")
    with pytest.raises(QuarantineFollowupProofError, match="receipt sha256 mismatch"):
        prove_recovery_followups(Path("/test/root"), rec)

    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: (_ for _ in ()).throw(FileNotFoundError("no file")))
    with pytest.raises(QuarantineFollowupProofError, match="failed reading receipt"):
        prove_recovery_followups(Path("/test/root"), rec)

    failed_receipt = copy.deepcopy(RECEIPT_FIXTURE)
    failed_receipt["state"] = "FAILED"
    failed_receipt["returncode"] = 1
    f_bytes = json.dumps(failed_receipt, sort_keys=True).encode("utf-8") + b"\n"
    r_fail = copy.deepcopy(rec)
    r_fail["followup_commits"][0]["test_evidence_sha256"] = hashlib.sha256(f_bytes).hexdigest()
    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: f_bytes)
    with pytest.raises(QuarantineFollowupProofError, match="receipt validation failed"):
        prove_recovery_followups(Path("/test/root"), r_fail)


def test_git_verifier_and_gemini_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, receipt_bytes = _make_fixtures()
    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: rec["new_repository"])
    monkeypatch.setattr(proof_mod, "validate_accepted_repository", lambda repo: repo)
    monkeypatch.setattr(
        proof_mod, "verify_declared_maintenance_git_chain", lambda r, ad: (_ for _ in ()).throw(ValueError("git fail"))
    )
    with pytest.raises(QuarantineFollowupProofError, match="maintenance git proof failed"):
        prove_recovery_followups(Path("/test/root"), rec)

    monkeypatch.setattr(proof_mod, "verify_declared_maintenance_git_chain", lambda r, ad: None)
    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: receipt_bytes)
    monkeypatch.setattr(
        proof_mod, "validate_gemini_chain", lambda r, s, e: (_ for _ in ()).throw(ValueError("gemini fail"))
    )
    with pytest.raises(QuarantineFollowupProofError, match="commit chain validation failed"):
        prove_recovery_followups(Path("/test/root"), rec)


def test_inventory_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, receipt_bytes = _make_fixtures()
    monkeypatch.setattr(proof_mod, "validate_repository", lambda r, require_clean=True: rec["new_repository"])
    monkeypatch.setattr(proof_mod, "validate_accepted_repository", lambda repo: repo)
    monkeypatch.setattr(proof_mod, "verify_declared_maintenance_git_chain", lambda r, ad: None)
    monkeypatch.setattr(proof_mod, "bounded_file_bytes", lambda r, p, lim, lbl: receipt_bytes)
    monkeypatch.setattr(proof_mod, "validate_gemini_chain", lambda r, s, e: {})
    monkeypatch.setattr(proof_mod, "validate_committed_proposal", lambda r, b, a, p: None)

    monkeypatch.setattr(proof_mod, "inspect_canonical_evidence", lambda r, b: {"status": "INVENTORY_INCONSISTENT"})
    with pytest.raises(QuarantineFollowupProofError, match="canonical evidence status is not VALID"):
        prove_recovery_followups(Path("/test/root"), rec)

    monkeypatch.setattr(proof_mod, "inspect_canonical_evidence", lambda r, b: {"status": "VALID", "seed_count": 0, "requested_coverage": "PROVEN_FOR_REQUESTED_SCENARIO"})
    with pytest.raises(QuarantineFollowupProofError, match="canonical evidence seed_count must be positive int"):
        prove_recovery_followups(Path("/test/root"), rec)

    monkeypatch.setattr(proof_mod, "inspect_canonical_evidence", lambda r, b: {"status": "VALID", "seed_count": 5, "requested_coverage": "UNKNOWN"})
    with pytest.raises(QuarantineFollowupProofError, match="canonical evidence requested_coverage is not PROVEN"):
        prove_recovery_followups(Path("/test/root"), rec)
