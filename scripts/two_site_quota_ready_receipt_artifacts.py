"""Pure strict input validator for two-site READY continuation receipt artifacts."""

from __future__ import annotations

import re
from typing import Mapping

try:
    from scripts.two_site_provider_broker import (
        RUN_PREFIX_RE,
        BrokerError,
    )
except ImportError:
    try:
        from two_site_provider_broker import (  # type: ignore
            RUN_PREFIX_RE,
            BrokerError,
        )
    except ImportError:
        BrokerError = ValueError  # type: ignore
        RUN_PREFIX_RE = None  # type: ignore

if RUN_PREFIX_RE is None or not hasattr(RUN_PREFIX_RE, "fullmatch"):
    RUN_PREFIX_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,60}$")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_KEYS = (
    "run_prefix",
    "correction_count",
    "pre_review_test_path",
    "pre_review_test_sha256",
    "pre_review_test_state",
    "proposal_sha256",
)


class ReadyReceiptArtifactsError(BrokerError):
    """Raised when READY receipt artifacts validation fails."""


QuotaReadyReceiptArtifactsError = ReadyReceiptArtifactsError


def validate_ready_receipt_artifacts(ready: Mapping[str, object]) -> dict[str, object]:
    """Validate strict six-field READY metadata and return load_verifier_pre_review_receipt artifact dict."""
    if not isinstance(ready, Mapping) or isinstance(ready, (str, bytes)):
        raise ReadyReceiptArtifactsError(
            f"ready must be a Mapping, got {type(ready).__name__}"
        )

    for key in _REQUIRED_KEYS:
        if key not in ready:
            raise ReadyReceiptArtifactsError(f"missing required key: {key!r}")

    run_prefix = ready["run_prefix"]
    if type(run_prefix) is not str:
        raise ReadyReceiptArtifactsError(
            f"run_prefix must be exact str, got {type(run_prefix).__name__}"
        )

    correction_count = ready["correction_count"]
    if type(correction_count) is not int or type(correction_count) is bool:
        raise ReadyReceiptArtifactsError(
            f"correction_count must be exact int 0..2, got {correction_count!r}"
        )

    pre_review_test_path = ready["pre_review_test_path"]
    if type(pre_review_test_path) is not str:
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_path must be exact str, got {type(pre_review_test_path).__name__}"
        )

    pre_review_test_sha256 = ready["pre_review_test_sha256"]
    if type(pre_review_test_sha256) is not str:
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_sha256 must be exact str, got {type(pre_review_test_sha256).__name__}"
        )

    pre_review_test_state = ready["pre_review_test_state"]
    if type(pre_review_test_state) is not str:
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_state must be exact str, got {type(pre_review_test_state).__name__}"
        )

    proposal_sha256 = ready["proposal_sha256"]
    if type(proposal_sha256) is not str:
        raise ReadyReceiptArtifactsError(
            f"proposal_sha256 must be exact str, got {type(proposal_sha256).__name__}"
        )

    if not (0 <= correction_count <= 2):
        raise ReadyReceiptArtifactsError(
            f"correction_count must be exact int 0..2, got {correction_count!r}"
        )

    if not RUN_PREFIX_RE.fullmatch(run_prefix):
        raise ReadyReceiptArtifactsError(
            f"run_prefix invalid: {run_prefix!r}"
        )

    if not _SHA256_RE.fullmatch(proposal_sha256):
        raise ReadyReceiptArtifactsError(
            f"proposal_sha256 must be 64 lowercase hex, got {proposal_sha256!r}"
        )

    if not _SHA256_RE.fullmatch(pre_review_test_sha256):
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_sha256 must be 64 lowercase hex, got {pre_review_test_sha256!r}"
        )

    if pre_review_test_state != "PASSED":
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_state must be 'PASSED', got {pre_review_test_state!r}"
        )

    expected_path = (
        f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-{correction_count}.json"
    )
    if pre_review_test_path != expected_path:
        raise ReadyReceiptArtifactsError(
            f"pre_review_test_path mismatch: {pre_review_test_path!r} != {expected_path!r}"
        )

    return {
        "original_run_prefix": run_prefix,
        "correction_count": correction_count,
        "pre_review_test_path": pre_review_test_path,
        "pre_review_test_sha256": pre_review_test_sha256,
        "pre_review_test_state": pre_review_test_state,
        "proposal_sha256": proposal_sha256,
    }


__all__ = [
    "QuotaReadyReceiptArtifactsError",
    "ReadyReceiptArtifactsError",
    "validate_ready_receipt_artifacts",
]
