"""Dormant read-only verification helper for two-site READY pre-review receipts."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from scripts.two_site_agent_sandbox import selectors
from scripts.two_site_proposal_pipeline import (
    load_verifier_pre_review_receipt,
)
from scripts.two_site_quota_ready_receipt_artifacts import (
    validate_ready_receipt_artifacts,
)

__all__ = ["verify_ready_pre_review_receipt"]

_REDACTED_ERROR = "pre-review receipt verification failed"
_EXPECTED_READY_KEYS = frozenset({
    "original_run_prefix",
    "correction_count",
    "pre_review_test_path",
    "pre_review_test_sha256",
    "pre_review_test_state",
    "proposal_sha256",
})


def verify_ready_pre_review_receipt(
    root: Path,
    ready: Mapping[str, object],
    expected_selectors: list[str],
) -> dict[str, object]:
    """Verify pre-review test receipt binding against READY continuation metadata."""
    if not isinstance(root, Path):
        raise ValueError(_REDACTED_ERROR) from None

    if type(expected_selectors) is not list or not (1 <= len(expected_selectors) <= 200):
        raise ValueError(_REDACTED_ERROR) from None

    for item in expected_selectors:
        if type(item) is not str or not item:
            raise ValueError(_REDACTED_ERROR) from None

    try:
        canon_selectors = selectors(expected_selectors)
    except Exception:
        raise ValueError(_REDACTED_ERROR) from None

    if type(canon_selectors) is not list or canon_selectors != expected_selectors:
        raise ValueError(_REDACTED_ERROR) from None

    try:
        validated_ready = validate_ready_receipt_artifacts(ready)
    except Exception:
        raise ValueError(_REDACTED_ERROR) from None

    if (
        type(validated_ready) is not dict
        or frozenset(validated_ready.keys()) != _EXPECTED_READY_KEYS
    ):
        raise ValueError(_REDACTED_ERROR) from None

    loader_artifacts = {
        "original_run_prefix": validated_ready["original_run_prefix"],
        "correction_count": validated_ready["correction_count"],
        "pre_review_test_path": validated_ready["pre_review_test_path"],
        "pre_review_test_sha256": validated_ready["pre_review_test_sha256"],
        "pre_review_test_state": validated_ready["pre_review_test_state"],
        "proposal_sha256": validated_ready["proposal_sha256"],
    }

    try:
        receipt = load_verifier_pre_review_receipt(root, loader_artifacts)
    except Exception:
        raise ValueError(_REDACTED_ERROR) from None

    if not isinstance(receipt, Mapping) or isinstance(receipt, (str, bytes)):
        raise ValueError(_REDACTED_ERROR) from None

    if type(receipt.get("state")) is not str or receipt.get("state") != "PASSED":
        raise ValueError(_REDACTED_ERROR) from None

    if (
        type(receipt.get("proposal_sha256")) is not str
        or receipt.get("proposal_sha256") != loader_artifacts["proposal_sha256"]
    ):
        raise ValueError(_REDACTED_ERROR) from None

    if (
        type(receipt.get("selectors")) is not list
        or receipt.get("selectors") != canon_selectors
    ):
        raise ValueError(_REDACTED_ERROR) from None

    if (
        type(receipt.get("unsupported_specs")) is not list
        or receipt.get("unsupported_specs") != []
    ):
        raise ValueError(_REDACTED_ERROR) from None

    return {
        "status": "RECEIPT_BINDING_OBSERVED_NOT_ACCEPTED",
        "receipt_sha256": loader_artifacts["pre_review_test_sha256"],
        "proposal_sha256": loader_artifacts["proposal_sha256"],
        "selector_count": len(canon_selectors),
    }
