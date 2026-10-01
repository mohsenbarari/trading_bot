"""Dormant pure observational proof for quarantined failed cycle and repair under AE03."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        RESULT_KEYS, _is_committed_halt_receipt, validate_committed_proposal, validate_gemini_chain
    )
    from scripts.two_site_maintenance_transition_record import (
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES, parse_maintenance_transition_record
    )
    from scripts.two_site_quarantine_recovery_record import (
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES, parse_quarantine_recovery_record, validate_quarantine_recovery_record
    )
    from scripts.two_site_proposal_runner import unique_json
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        RESULT_KEYS, _is_committed_halt_receipt, validate_committed_proposal, validate_gemini_chain
    )
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES, parse_maintenance_transition_record
    )
    from two_site_quarantine_recovery_record import (  # type: ignore[no-redef]
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES, parse_quarantine_recovery_record, validate_quarantine_recovery_record
    )
    try:
        from two_site_proposal_runner import unique_json  # type: ignore[no-redef]
    except ImportError:
        def unique_json(text: str) -> Any:
            def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                d = dict(pairs)
                if len(d) != len(pairs):
                    raise ValueError("Duplicate key")
                return d
            return json.loads(text, object_pairs_hook=_pairs)

MAX_FAILED_RESULT_BYTES: int = 65536
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REPAIR_EVIDENCE_KEYS = frozenset({"gemini_reviewer_run_id", "gemini_worker_run_id", "proposal_sha256"})


class QuarantinePairProofError(ValueError):
    """Raised when quarantine pair proof validation fails."""


__all__ = [
    "MAX_FAILED_RESULT_BYTES",
    "QuarantinePairProofError",
    "REPAIR_EVIDENCE_KEYS",
    "prove_quarantined_pair",
]


def prove_quarantined_pair(
    root: Path,
    recovery_record: dict[str, Any] | bytes,
    prior_transition_bytes: bytes,
    failed_result_bytes: bytes,
    repair_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Observational proof for quarantined failed commit and its repair."""
    if not isinstance(root, Path):
        raise QuarantinePairProofError("root must be a Path")

    if isinstance(recovery_record, bytes):
        if len(recovery_record) > MAX_QUARANTINE_RECOVERY_RECORD_BYTES:
            raise QuarantinePairProofError("recovery_record bytes exceed maximum allowed length")
        try:
            record = parse_quarantine_recovery_record(recovery_record)
        except Exception as exc:
            raise QuarantinePairProofError("recovery_record parsing failed") from exc
    elif isinstance(recovery_record, dict):
        try:
            record = validate_quarantine_recovery_record(recovery_record)
        except Exception as exc:
            raise QuarantinePairProofError("recovery_record validation failed") from exc
    else:
        raise QuarantinePairProofError("recovery_record must be bytes or dict")

    if not isinstance(prior_transition_bytes, bytes):
        raise QuarantinePairProofError("prior_transition_bytes must be bytes")
    if not (1 <= len(prior_transition_bytes) <= MAX_MAINTENANCE_TRANSITION_RECORD_BYTES):
        raise QuarantinePairProofError("prior_transition_bytes length out of bounds")

    prior_sha = hashlib.sha256(prior_transition_bytes).hexdigest()
    if prior_sha != record["prior_maintenance_transition_sha256"]:
        raise QuarantinePairProofError("prior_transition_bytes sha256 mismatch")

    try:
        prior_transition = parse_maintenance_transition_record(prior_transition_bytes)
    except Exception as exc:
        raise QuarantinePairProofError("prior_transition parsing failed") from exc

    if prior_transition["prior_checkpoint_sha256"] != record["prior_checkpoint_sha256"]:
        raise QuarantinePairProofError("shared prior_checkpoint_sha256 mismatch")

    new_repo = prior_transition.get("new_repository")
    if not isinstance(new_repo, dict) or "head" not in new_repo:
        raise QuarantinePairProofError("prior_transition missing new_repository head")
    prior_head = new_repo["head"]

    if not isinstance(failed_result_bytes, bytes):
        raise QuarantinePairProofError("failed_result_bytes must be bytes")
    if not (1 <= len(failed_result_bytes) <= MAX_FAILED_RESULT_BYTES):
        raise QuarantinePairProofError("failed_result_bytes length out of bounds")

    failed_sha = hashlib.sha256(failed_result_bytes).hexdigest()
    if failed_sha != record["failed_result_sha256"]:
        raise QuarantinePairProofError("failed_result_bytes sha256 mismatch")

    try:
        failed_text = failed_result_bytes.decode("utf-8")
        failed_result = unique_json(failed_text)
    except UnicodeDecodeError as exc:
        raise QuarantinePairProofError("failed_result_bytes is not valid UTF-8") from exc
    except Exception as exc:
        raise QuarantinePairProofError("failed_result JSON parsing failed") from exc

    if not isinstance(failed_result, dict) or set(failed_result.keys()) != RESULT_KEYS:
        raise QuarantinePairProofError("failed_result keys do not match RESULT_KEYS")
    if not _is_committed_halt_receipt(failed_result):
        raise QuarantinePairProofError("failed_result is not a committed halt receipt")
    if failed_result.get("state") != "BLOCKED":
        raise QuarantinePairProofError("failed_result state must be BLOCKED")
    if failed_result.get("disposition") != "HALT":
        raise QuarantinePairProofError("failed_result disposition must be HALT")
    if failed_result.get("category") != "EXTERNAL_GATE":
        raise QuarantinePairProofError("failed_result category must be EXTERNAL_GATE")

    failed_commit_head = record["failed_commit_head"]
    if failed_result.get("commit") != failed_commit_head:
        raise QuarantinePairProofError("failed_result commit must match record.failed_commit_head")

    blockers = failed_result.get("blockers")
    if not isinstance(blockers, list) or len(blockers) == 0:
        raise QuarantinePairProofError("failed_result blockers must be non-empty")
    if failed_result.get("gemini_worker_run_id") == failed_result.get("gemini_reviewer_run_id"):
        raise QuarantinePairProofError("failed_result worker and reviewer run IDs must be distinct")

    if not isinstance(repair_evidence, dict) or set(repair_evidence.keys()) != REPAIR_EVIDENCE_KEYS:
        raise QuarantinePairProofError("repair_evidence keys mismatch")

    rep_worker = repair_evidence.get("gemini_worker_run_id")
    rep_reviewer = repair_evidence.get("gemini_reviewer_run_id")
    rep_prop_sha = repair_evidence.get("proposal_sha256")
    if (
        not isinstance(rep_worker, str)
        or not _RUN_ID_RE.fullmatch(rep_worker)
        or not isinstance(rep_reviewer, str)
        or not _RUN_ID_RE.fullmatch(rep_reviewer)
        or rep_worker == rep_reviewer
        or not isinstance(rep_prop_sha, str)
        or not _HEX_64_RE.fullmatch(rep_prop_sha)
    ):
        raise QuarantinePairProofError("repair_evidence fields invalid")

    repair_commit_head = record["repair_commit_head"]
    try:
        failed_proposal = validate_gemini_chain(root, prior_head, failed_result)
        validate_committed_proposal(root, prior_head, failed_commit_head, failed_proposal)
    except Exception as exc:
        raise QuarantinePairProofError("failed cycle chain validation failed") from exc

    try:
        repair_proposal = validate_gemini_chain(root, failed_commit_head, repair_evidence)
        validate_committed_proposal(root, failed_commit_head, repair_commit_head, repair_proposal)
    except Exception as exc:
        raise QuarantinePairProofError("repair cycle chain validation failed") from exc

    return {
        "failed_commit_head": failed_commit_head,
        "failed_proposal": failed_proposal,
        "prior_head": prior_head,
        "recovery_record": record,
        "repair_commit_head": repair_commit_head,
        "repair_proposal": repair_proposal,
    }
