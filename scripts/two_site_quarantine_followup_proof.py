"""Pure observational proof for commits after validated repair under AE03."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        validate_accepted_repository,
        validate_committed_proposal,
        validate_gemini_chain,
        validate_repository,
    )
    from scripts.two_site_cycle_tools import inspect_canonical_evidence
    from scripts.two_site_maintenance_transition_git import (
        verify_declared_maintenance_git_chain,
    )
    from scripts.two_site_maintenance_transition_record import (
        build_maintenance_transition_record,
    )
    from scripts.two_site_pre_review_receipt import validate_test_receipt
    from scripts.two_site_proposal_runner import bounded_file_bytes, unique_json
    from scripts.two_site_quarantine_recovery_record import (
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES,
        parse_quarantine_recovery_record,
        validate_quarantine_recovery_record,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        validate_accepted_repository,
        validate_committed_proposal,
        validate_gemini_chain,
        validate_repository,
    )
    from two_site_cycle_tools import inspect_canonical_evidence  # type: ignore[no-redef]
    from two_site_maintenance_transition_git import (  # type: ignore[no-redef]
        verify_declared_maintenance_git_chain,
    )
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        build_maintenance_transition_record,
    )
    from two_site_pre_review_receipt import validate_test_receipt  # type: ignore[no-redef]
    from two_site_proposal_runner import bounded_file_bytes, unique_json  # type: ignore[no-redef]
    from two_site_quarantine_recovery_record import (  # type: ignore[no-redef]
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES,
        parse_quarantine_recovery_record,
        validate_quarantine_recovery_record,
    )

MAX_RECEIPT_BYTES: int = 16384
MAX_FOLLOWUP_COMMITS: int = 8
TARGET_BEHAVIOR_ID: str = (
    "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183"
)
_WORKER_RUN_ID_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)-worker-([0-8])(?:-retry-[0-9]+)?$"
)


class QuarantineFollowupProofError(ValueError):
    """Raised when quarantine follow-up proof validation fails."""


__all__ = [
    "MAX_RECEIPT_BYTES",
    "QuarantineFollowupProofError",
    "TARGET_BEHAVIOR_ID",
    "derive_receipt_path",
    "prove_recovery_followups",
]


def derive_receipt_path(worker_run_id: str) -> Path:
    """Derive fixed pre-review receipt path from worker_run_id."""
    if not isinstance(worker_run_id, str):
        raise QuarantineFollowupProofError("worker_run_id must be a string")
    match = _WORKER_RUN_ID_RE.fullmatch(worker_run_id)
    if not match:
        raise QuarantineFollowupProofError(f"invalid worker_run_id: {worker_run_id}")
    prefix, n = match.group(1), match.group(2)
    if prefix in {".", ".."} or "/" in prefix or "\\" in prefix or ".." in prefix or prefix.startswith("."):
        raise QuarantineFollowupProofError(f"invalid worker_run_id prefix: {prefix}")
    return Path(".local/two-site-refactor/pipeline") / prefix / f"pre-review-tests-{n}.json"


def prove_recovery_followups(
    root: Path,
    recovery_record: dict[str, Any] | bytes,
) -> dict[str, Any]:
    """Observational proof for commits after the independently validated repair."""
    if not isinstance(root, Path):
        raise QuarantineFollowupProofError("root must be a Path")

    if isinstance(recovery_record, bytes):
        if len(recovery_record) > MAX_QUARANTINE_RECOVERY_RECORD_BYTES:
            raise QuarantineFollowupProofError("recovery_record bytes exceed limit")
        try:
            record = parse_quarantine_recovery_record(recovery_record)
        except Exception as exc:
            raise QuarantineFollowupProofError("recovery_record parsing failed") from exc
    elif isinstance(recovery_record, dict):
        try:
            record = validate_quarantine_recovery_record(recovery_record)
        except Exception as exc:
            raise QuarantineFollowupProofError("recovery_record validation failed") from exc
    else:
        raise QuarantineFollowupProofError("recovery_record must be bytes or dict")

    try:
        live_repo = validate_repository(root, require_clean=True)
        live_accepted = validate_accepted_repository(live_repo)
    except Exception as exc:
        raise QuarantineFollowupProofError(f"live repository validation failed: {exc}") from exc

    if live_accepted != record["new_repository"]:
        raise QuarantineFollowupProofError("live accepted repo does not match record")

    followup_commits = record.get("followup_commits")
    if not isinstance(followup_commits, list) or not (
        1 <= len(followup_commits) <= MAX_FOLLOWUP_COMMITS
    ):
        raise QuarantineFollowupProofError("invalid followup_commits count")

    try:
        adapter = build_maintenance_transition_record(
            prior_checkpoint_sha256=record["prior_checkpoint_sha256"],
            prior_head=record["repair_commit_head"],
            new_repository=record["new_repository"],
            maintenance_commits=followup_commits,
        )
        verify_declared_maintenance_git_chain(root, adapter)
    except Exception as exc:
        raise QuarantineFollowupProofError(f"maintenance git proof failed: {exc}") from exc

    validated_receipts: list[dict[str, Any]] = []
    validated_proposals: list[dict[str, Any]] = []

    for commit in followup_commits:
        head = commit["head"]
        parent = commit["parent"]
        receipt_path = derive_receipt_path(commit["worker_run_id"])

        try:
            raw_receipt = bounded_file_bytes(
                root, receipt_path, MAX_RECEIPT_BYTES, f"receipt {receipt_path}"
            )
        except Exception as exc:
            raise QuarantineFollowupProofError(
                f"failed reading receipt {receipt_path} for {head}: {exc}"
            ) from exc

        if hashlib.sha256(raw_receipt).hexdigest() != commit["test_evidence_sha256"]:
            raise QuarantineFollowupProofError(f"receipt sha256 mismatch for {head}")

        try:
            parsed = unique_json(raw_receipt.decode("utf-8"))
        except Exception as exc:
            raise QuarantineFollowupProofError(f"receipt JSON decode failed for {head}: {exc}") from exc

        try:
            norm_receipt = validate_test_receipt(
                parsed, commit["proposal_sha256"], require_passed=True
            )
        except Exception as exc:
            raise QuarantineFollowupProofError(f"receipt validation failed for {head}: {exc}") from exc
        validated_receipts.append(norm_receipt)

        gemini_proof = {
            "gemini_reviewer_run_id": commit["reviewer_run_id"],
            "gemini_worker_run_id": commit["worker_run_id"],
            "proposal_sha256": commit["proposal_sha256"],
        }
        try:
            proposal = validate_gemini_chain(root, parent, gemini_proof)
            validate_committed_proposal(root, parent, head, proposal)
        except Exception as exc:
            raise QuarantineFollowupProofError(
                f"commit chain validation failed for {head}: {exc}"
            ) from exc
        validated_proposals.append(proposal)

    try:
        evidence = inspect_canonical_evidence(root, TARGET_BEHAVIOR_ID)
    except Exception as exc:
        raise QuarantineFollowupProofError(f"canonical evidence inspection failed: {exc}") from exc

    if not isinstance(evidence, dict) or evidence.get("status") != "VALID":
        raise QuarantineFollowupProofError("canonical evidence status is not VALID")
    seed_count = evidence.get("seed_count")
    if type(seed_count) is not int or isinstance(seed_count, bool) or seed_count <= 0:
        raise QuarantineFollowupProofError("canonical evidence seed_count must be positive int")
    if evidence.get("requested_coverage") != "PROVEN_FOR_REQUESTED_SCENARIO":
        raise QuarantineFollowupProofError("canonical evidence requested_coverage is not PROVEN")

    return {
        "canonical_evidence": evidence,
        "followup_count": len(validated_proposals),
        "proposals": validated_proposals,
        "receipts": validated_receipts,
        "recovery_record": record,
    }
