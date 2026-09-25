"""Reviewer correction feedback generator for two-site proposal pipeline."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import normalize_finding
except ImportError:
    from two_site_proposal_pipeline import normalize_finding

try:
    from scripts.two_site_proposal_runner import (
        ProposalError,
        validate_correction_feedback,
    )
except ImportError:
    from two_site_proposal_runner import (
        ProposalError,
        validate_correction_feedback,
    )

try:
    from scripts.two_site_pre_review_receipt import (
        SandboxError,
        validate_test_receipt,
    )
except ImportError:
    try:
        from two_site_pre_review_receipt import (
            SandboxError,
            validate_test_receipt,
        )
    except ImportError:
        try:
            from scripts.two_site_agent_sandbox import SandboxError
            from scripts.two_site_pre_review_receipt import validate_test_receipt
        except ImportError:
            from two_site_agent_sandbox import SandboxError
            from two_site_pre_review_receipt import validate_test_receipt

__all__ = [
    "ProposalError",
    "build_reviewer_correction_feedback",
    "normalize_finding",
]

SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_FINDINGS = 32
MAX_SELECTORS = 200


def build_reviewer_correction_feedback(
    reviewer_result: Mapping[str, Any],
    worker_sha: str,
    receipt: Mapping[str, Any],
    receipt_sha: str,
    correction_count: int,
    seen_normalized_findings: set[str],
) -> tuple[dict[str, Any], set[str]]:
    """Build detached reviewer correction feedback payload and updated seen findings."""
    if not isinstance(worker_sha, str) or not SHA_PATTERN.fullmatch(worker_sha):
        raise ProposalError("worker_sha must be a lowercase 64-hex SHA")
    if not isinstance(receipt_sha, str) or not SHA_PATTERN.fullmatch(receipt_sha):
        raise ProposalError("receipt_sha must be a lowercase 64-hex SHA")

    if type(correction_count) is not int:
        raise ProposalError("correction_count must be a built-in integer")
    if correction_count >= 2:
        raise ProposalError("correction exhaustion: maximum 2 corrections exceeded")
    if correction_count not in (0, 1):
        raise ProposalError(f"invalid correction_count: {correction_count}")

    if not isinstance(seen_normalized_findings, (set, frozenset)):
        raise ProposalError("seen_normalized_findings must be a set")
    for item in seen_normalized_findings:
        if not isinstance(item, str):
            raise ProposalError("seen_normalized_findings elements must be strings")

    if not isinstance(reviewer_result, Mapping) or isinstance(reviewer_result, (str, bytes)):
        raise ProposalError("reviewer_result must be a mapping")

    status = reviewer_result.get("status")
    if status != "CHANGES_REQUIRED":
        raise ProposalError(f"reviewer_result status must be CHANGES_REQUIRED, got {status!r}")

    raw_findings = reviewer_result.get("findings")
    if not isinstance(raw_findings, list):
        raise ProposalError("reviewer_result findings must be a list")
    if not (1 <= len(raw_findings) <= MAX_FINDINGS):
        raise ProposalError(f"reviewer_result findings must contain 1..{MAX_FINDINGS} entries")

    new_seen = set(seen_normalized_findings)
    current_seen_norm: set[str] = set()

    for finding in raw_findings:
        if not isinstance(finding, str) or not finding.strip():
            raise ProposalError("reviewer_result findings entries must be non-empty strings")
        norm = normalize_finding(finding)
        if norm in current_seen_norm:
            raise ProposalError(f"duplicate normalized finding in current findings: {finding}")
        if norm in new_seen:
            raise ProposalError(f"recurring normalized findings: {finding}")
        current_seen_norm.add(norm)

    try:
        norm_receipt = validate_test_receipt(
            receipt,
            worker_sha,
            require_passed=True,
        )
    except SandboxError as exc:
        raise ProposalError(f"invalid test receipt: {exc}") from exc

    new_seen.update(current_seen_norm)

    evidence_str = (
        f"receipt_sha256={receipt_sha} proposal_sha256={worker_sha} "
        f"selector_count={len(norm_receipt['selectors'])}"
    )

    feedback_payload: dict[str, Any] = {
        "reviewer": "gemini",
        "reviewed_proposal_sha256": worker_sha,
        "findings": list(raw_findings),
        "test_evidence": [evidence_str],
    }

    validate_correction_feedback(json.dumps(feedback_payload), worker_sha)

    return feedback_payload, new_seen
