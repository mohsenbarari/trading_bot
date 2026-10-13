"""Dormant supervisor finalization composition boundary for two-site quota continuation.

Composes validated publication of accepted lineage record followed by
final acknowledgement record under caller-held exclusive Supervisor lock.

Boundary and authority constraints:
1. Dormant composition boundary: contains NO live hooks, NO monitor/supervisor loop,
   NO Provider execution, wake, replay, pruning, or queue dispatch authority.
2. Caller MUST hold exclusive Supervisor lock throughout invocation and provide an eligible
   transition result already validated by validate_transition. Called only after
   validate_transition and before advisory fields or ordinary checkpoint/status.
   The function does NOT claim to prove lock ownership.
3. Fail-closed: if lineage publication raises, final acknowledgement is NEVER called.
4. Ambiguous post-lineage: once lineage record publication succeeds and returns, ANY
   subsequent failure or exception in final acknowledgement is an explicit ambiguous
   finalization error retaining the original cause; published records are retained and
   never deleted, retried, or overwritten.
5. No mutable status authority: does not infer identifiers from mutable status.json or
   fabricate upstream.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from scripts.two_site_quota_final_acceptance_bridge import (
    publish_validated_final_acceptance_lineage_locked,
)
from scripts.two_site_quota_final_ack_bridge import (
    publish_validated_final_ack_locked,
)

__all__ = [
    "AmbiguousFinalizationError",
    "SupervisorFinalizationAmbiguousError",
    "SupervisorFinalizationError",
    "finalize_accepted_quota_continuation_locked",
]


class SupervisorFinalizationError(Exception):
    """Base exception for supervisor finalization errors."""


class SupervisorFinalizationAmbiguousError(SupervisorFinalizationError):
    """Raised when finalization fails ambiguously after lineage publication."""


AmbiguousFinalizationError = SupervisorFinalizationAmbiguousError


def _check_stop_markers(root: Path) -> None:
    """Observe canonical STOP paths without following links; not an OS-atomic fence."""
    if not isinstance(root, Path):
        return  # Preserve the original bridge's invalid-root rejection.
    for marker in (root / "STOP", root / ".local/two-site-refactor/autopilot/STOP"):
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        raise SupervisorFinalizationError("quota finalization stopped")


def finalize_accepted_quota_continuation_locked(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    before_repository: dict[str, Any],
    expected_transition_result: dict[str, Any],
    acknowledged_at_utc: str,
) -> dict[str, Any]:
    """Publish accepted lineage then final acknowledgement under exclusive Supervisor lock.

    Caller precondition: caller holds exclusive Supervisor lock throughout invocation and
    has obtained expected_transition_result from validate_transition before adding advisories.
    This function does NOT claim to prove lock ownership.

    Returns a copy of the verified final acknowledgement record only after both bridges complete.
    """
    try:
        _check_stop_markers(root)
    except OSError as exc:
        raise SupervisorFinalizationError("STOP marker inspection failed") from exc

    # 1. Publish validated final acceptance lineage
    lineage_record = publish_validated_final_acceptance_lineage_locked(
        root=root,
        original_controller_run_id=original_controller_run_id,
        continuation_controller_run_id=continuation_controller_run_id,
        before_repository=before_repository,
        expected_transition_result=expected_transition_result,
    )

    # 2. Publish validated final acknowledgement with exact lineage record and same parameters
    try:
        _check_stop_markers(root)
        ack_record = publish_validated_final_ack_locked(
            root=root,
            original_controller_run_id=original_controller_run_id,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            accepted_lineage_record=lineage_record,
            acknowledged_at_utc=acknowledged_at_utc,
        )
        _check_stop_markers(root)
        return copy.deepcopy(ack_record)
    except BaseException as exc:
        raise SupervisorFinalizationAmbiguousError(
            "ambiguous finalization failure after lineage publication; "
            "immutable records retained in custody"
        ) from exc
