"""Hermetic unit tests for scripts/two_site_quarantine_pair_proof.py under AE03."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

import scripts.two_site_quarantine_pair_proof as proof_mod
from scripts.two_site_maintenance_transition_record import (
    PROTECTED_REFS,
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)
from scripts.two_site_quarantine_pair_proof import (
    MAX_FAILED_RESULT_BYTES,
    QuarantinePairProofError,
    prove_quarantined_pair,
)
from scripts.two_site_quarantine_recovery_record import (
    build_quarantine_recovery_record,
    serialize_quarantine_recovery_record,
)

FAILED_RESULT_FIXTURE: dict[str, Any] = {
    "schema_version": "1.0",
    "state": "BLOCKED",
    "disposition": "HALT",
    "category": "EXTERNAL_GATE",
    "summary": "Committed evidence but post-commit inventory reader failed.",
    "commit": "eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4",
    "next_action": "Separately reviewed correction required.",
    "blockers": ["continuation 11 invalid"],
    "gemini_worker_run_id": "p1-00b-terminate-evidence-079-worker-0",
    "gemini_reviewer_run_id": "p1-00b-terminate-evidence-079-reviewer-0",
    "proposal_sha256": "7c56e357f8bcd151e762f144f1ed902d82b11f1ec224e64cdc2324b6d4f4d047",
}

REPAIR_EVIDENCE_FIXTURE: dict[str, Any] = {
    "gemini_worker_run_id": "repair-worker-run-1",
    "gemini_reviewer_run_id": "repair-reviewer-run-1",
    "proposal_sha256": "a" * 64,
}


def _make_fixtures() -> tuple[dict[str, Any], bytes, bytes, dict[str, Any]]:
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
    prior_bytes = serialize_maintenance_transition_record(prior_trans)
    prior_sha = hashlib.sha256(prior_bytes).hexdigest()

    failed_bytes = (
        json.dumps(FAILED_RESULT_FIXTURE, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    failed_sha = hashlib.sha256(failed_bytes).hexdigest()

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
        failed_result_sha256=failed_sha,
        failed_commit_head="eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4",
        repair_commit_head="6" * 40,
        new_repository=recovery_repo,
        followup_commits=[{
            "head": "7" * 40,
            "parent": "6" * 40,
            "paths": ["scripts/two_site_codex_autopilot.py"],
            "proposal_sha256": "8" * 64,
            "reviewer_run_id": "rev-f",
            "test_evidence_sha256": "9" * 64,
            "worker_run_id": "work-f",
        }],
        final_validation_sha256="b" * 64,
    )
    return rec, prior_bytes, failed_bytes, copy.deepcopy(REPAIR_EVIDENCE_FIXTURE)


def test_prove_quarantined_pair_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    rec, prior_bytes, failed_bytes, repair_ev = _make_fixtures()
    chain_calls: list[tuple[str, Any]] = []
    prop_calls: list[tuple[str, str, Any]] = []

    monkeypatch.setattr(
        proof_mod,
        "validate_gemini_chain",
        lambda r, src, res: chain_calls.append((src, res)) or {"prop": src},
    )
    monkeypatch.setattr(
        proof_mod,
        "validate_committed_proposal",
        lambda r, bef, aft, p: prop_calls.append((bef, aft, p)),
    )

    res = prove_quarantined_pair(Path("/test/root"), rec, prior_bytes, failed_bytes, repair_ev)
    assert res["failed_commit_head"] == "eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4"
    assert res["repair_commit_head"] == "6" * 40
    assert res["prior_head"] == "1" * 40
    assert len(chain_calls) == 2
    assert len(prop_calls) == 2
    assert chain_calls[0] == ("1" * 40, FAILED_RESULT_FIXTURE)
    assert prop_calls[0] == ("1" * 40, "eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4", {"prop": "1" * 40})
    assert chain_calls[1] == ("eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4", repair_ev)
    assert prop_calls[1] == ("eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4", "6" * 40, {"prop": "eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4"})

    rec_bytes = serialize_quarantine_recovery_record(rec)
    res_bytes = prove_quarantined_pair(Path("/test/root"), rec_bytes, prior_bytes, failed_bytes, repair_ev)
    assert res_bytes["failed_commit_head"] == res["failed_commit_head"]


def test_field_mutation_status_vs_state_disposition() -> None:
    rec, prior_bytes, _, repair_ev = _make_fixtures()
    # status replaces state
    mut1 = copy.deepcopy(FAILED_RESULT_FIXTURE)
    del mut1["state"]
    mut1["status"] = "BLOCKED"
    b1 = json.dumps(mut1, sort_keys=True).encode("utf-8") + b"\n"
    r1 = copy.deepcopy(rec)
    r1["failed_result_sha256"] = hashlib.sha256(b1).hexdigest()
    with pytest.raises(QuarantinePairProofError):
        prove_quarantined_pair(Path("/test/root"), r1, prior_bytes, b1, repair_ev)

    # status replaces disposition
    mut2 = copy.deepcopy(FAILED_RESULT_FIXTURE)
    del mut2["disposition"]
    mut2["status"] = "HALT"
    b2 = json.dumps(mut2, sort_keys=True).encode("utf-8") + b"\n"
    r2 = copy.deepcopy(rec)
    r2["failed_result_sha256"] = hashlib.sha256(b2).hexdigest()
    with pytest.raises(QuarantinePairProofError):
        prove_quarantined_pair(Path("/test/root"), r2, prior_bytes, b2, repair_ev)

    # disposition != HALT
    mut3 = copy.deepcopy(FAILED_RESULT_FIXTURE)
    mut3["disposition"] = "CONTINUE"
    b3 = json.dumps(mut3, sort_keys=True).encode("utf-8") + b"\n"
    r3 = copy.deepcopy(rec)
    r3["failed_result_sha256"] = hashlib.sha256(b3).hexdigest()
    with pytest.raises(QuarantinePairProofError):
        prove_quarantined_pair(Path("/test/root"), r3, prior_bytes, b3, repair_ev)


def test_bad_hashes_and_wrong_commit() -> None:
    rec, prior_bytes, failed_bytes, repair_ev = _make_fixtures()
    with pytest.raises(QuarantinePairProofError, match="failed_result_bytes sha256 mismatch"):
        prove_quarantined_pair(Path("/test/root"), rec, prior_bytes, failed_bytes + b" ", repair_ev)

    with pytest.raises(QuarantinePairProofError, match="prior_transition_bytes sha256 mismatch"):
        prove_quarantined_pair(Path("/test/root"), rec, prior_bytes + b" ", failed_bytes, repair_ev)

    mut = copy.deepcopy(FAILED_RESULT_FIXTURE)
    mut["commit"] = "d" * 40
    b = json.dumps(mut, sort_keys=True).encode("utf-8") + b"\n"
    r = copy.deepcopy(rec)
    r["failed_result_sha256"] = hashlib.sha256(b).hexdigest()
    with pytest.raises(QuarantinePairProofError, match="failed_result commit must match"):
        prove_quarantined_pair(Path("/test/root"), r, prior_bytes, b, repair_ev)


def test_wrong_prior_transition_and_repair_evidence() -> None:
    rec, prior_bytes, failed_bytes, repair_ev = _make_fixtures()
    bad_rec = copy.deepcopy(rec)
    bad_rec["prior_checkpoint_sha256"] = "8" * 64
    with pytest.raises(QuarantinePairProofError, match="shared prior_checkpoint_sha256 mismatch"):
        prove_quarantined_pair(Path("/test/root"), bad_rec, prior_bytes, failed_bytes, repair_ev)

    bad_ev = copy.deepcopy(repair_ev)
    bad_ev["gemini_reviewer_run_id"] = bad_ev["gemini_worker_run_id"]
    with pytest.raises(QuarantinePairProofError, match="repair_evidence fields invalid"):
        prove_quarantined_pair(Path("/test/root"), rec, prior_bytes, failed_bytes, bad_ev)

    bad_json = b"{\"not_valid"
    bad_json_rec = copy.deepcopy(rec)
    bad_json_rec["failed_result_sha256"] = hashlib.sha256(bad_json).hexdigest()
    with pytest.raises(QuarantinePairProofError, match="failed_result JSON parsing failed"):
        prove_quarantined_pair(Path("/test/root"), bad_json_rec, prior_bytes, bad_json, repair_ev)

    oversized = b"x" * (MAX_FAILED_RESULT_BYTES + 1)
    with pytest.raises(QuarantinePairProofError, match="failed_result_bytes length out of bounds"):
        prove_quarantined_pair(Path("/test/root"), rec, prior_bytes, oversized, repair_ev)
