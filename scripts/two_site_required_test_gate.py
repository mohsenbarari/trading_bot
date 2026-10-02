"""Required-test coverage gate feedback module.

Pure module does not itself authorize Reviewer; pipeline owns admission, and live acceptance needs separate receipts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
except ImportError:
    from two_site_pre_review_receipt import validate_test_receipt

try:
    from scripts.two_site_pre_review_tests import parse_test_spec
except ImportError:
    from two_site_pre_review_tests import parse_test_spec

__all__ = ["required_selector_gate_feedback"]

_MAX_FINDINGS = 32
_MAX_FINDING_BYTES = 2048
_MAX_EVIDENCE = 32
_MAX_EVIDENCE_BYTES = 8192


def required_selector_gate_feedback(
    receipt: Mapping[str, Any],
    proposal_sha256: str,
) -> dict[str, Any] | None:
    """Evaluate candidate test receipt for unresolved mandatory test coverage."""
    validated = validate_test_receipt(receipt, proposal_sha256)

    raw_unsupported = validated.get("unsupported_specs", [])
    all_tokens: set[str] = set()

    for spec in raw_unsupported:
        tokens = parse_test_spec(spec)
        if tokens is not None:
            all_tokens.update(tokens)

    if not all_tokens:
        return None

    canonical_tokens = sorted(all_tokens)
    count = len(canonical_tokens)
    canonical_json = json.dumps(canonical_tokens, separators=(",", ":")).encode("utf-8")
    root_digest = hashlib.sha256(canonical_json).hexdigest()

    preview_items: list[str] = []
    remaining_bytes = 1000
    for tok in canonical_tokens:
        tok_bytes = tok.encode("utf-8")
        if len(tok_bytes) + 2 > remaining_bytes:
            if not preview_items:
                preview_items.append(tok_bytes[:remaining_bytes].decode("utf-8", errors="ignore") + "...")
            break
        preview_items.append(tok)
        remaining_bytes -= (len(tok_bytes) + 2)
        if len(preview_items) >= 10:
            break

    preview_str = ", ".join(preview_items)
    if count > len(preview_items):
        preview_str += f", ... (+{count - len(preview_items)} more)"

    finding = (
        f"Unresolved required test selectors (missing or budget omission) "
        f"[root: {root_digest}] ({count} total): {preview_str}. "
        f"Restore missing tests or rescope task; never delete mandatory requirements to bypass."
    )
    finding_bytes = finding.encode("utf-8")
    if len(finding_bytes) > _MAX_FINDING_BYTES:
        finding = finding_bytes[:_MAX_FINDING_BYTES].decode("utf-8", errors="ignore")

    evidence = (
        f"Candidate test receipt state={validated['state']}, returncode={validated['returncode']}; "
        f"{count} unresolved required test selector(s) across recognized unsupported specifications. "
        f"Root digest: {root_digest}."
    )
    evidence_bytes = evidence.encode("utf-8")
    if len(evidence_bytes) > _MAX_EVIDENCE_BYTES:
        evidence = evidence_bytes[:_MAX_EVIDENCE_BYTES].decode("utf-8", errors="ignore")

    return {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": validated["proposal_sha256"],
        "findings": [finding],
        "test_evidence": [evidence],
    }
