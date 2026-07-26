"""Credential-free promotion readiness decisions for the three-site data plane."""

from __future__ import annotations

from typing import Any


def data_plane_readiness_reasons(
    *,
    protocol_enabled: bool,
    protocol_strict: bool,
    dark_standby: bool,
    unresolved_conflicts: int,
    unapplied_checkpoints: int,
    blocked_receipts: int,
    ambiguous_effects: int,
    undelivered_deliveries: int,
    require_global_convergence: bool,
    blob_parity_ready: bool,
    recovery_manifest_required: bool,
    recovery_manifest_current: bool,
) -> tuple[str, ...]:
    """Return every fail-closed reason without reading configuration or secrets."""

    reasons: list[str] = []
    if not protocol_enabled or not protocol_strict:
        reasons.append("dr_event_protocol_not_strict")
    if dark_standby:
        reasons.append("dark_standby_mode_active")
    counts = {
        "dr_conflicts_unresolved": unresolved_conflicts,
        "dr_projection_checkpoint_incomplete": unapplied_checkpoints,
        "dr_receipt_gap_or_quarantine": blocked_receipts,
        "dr_effects_ambiguous": ambiguous_effects,
    }
    for reason, value in counts.items():
        if type(value) is not int or value < 0:
            raise ValueError("readiness counts must be non-negative integers")
        if value:
            reasons.append(reason)
    if type(undelivered_deliveries) is not int or undelivered_deliveries < 0:
        raise ValueError("delivery count must be a non-negative integer")
    if require_global_convergence and undelivered_deliveries:
        reasons.append("dr_destination_delivery_incomplete")
    if not blob_parity_ready:
        reasons.append("dr_blob_parity_incomplete")
    if recovery_manifest_required and not recovery_manifest_current:
        reasons.append("dr_recovery_manifest_missing_or_stale")
    return tuple(reasons)


def source_tail_application_reasons(
    *,
    boundary: dict[str, Any],
    target_observation: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Require the target's applied receipt and transaction hash at the source tail."""

    try:
        final_sequence = int(boundary["final_sequence"])
        final_hash = str(boundary["final_transaction_hash"])
    except (KeyError, TypeError, ValueError):
        return ("source_tail_boundary_invalid",)
    if final_sequence < 0 or len(final_hash) != 64:
        return ("source_tail_boundary_invalid",)
    if final_sequence == 0:
        return ()
    if target_observation is None:
        return ("source_tail_not_applied",)
    try:
        applied_sequence = int(target_observation["contiguous_applied_sequence"])
    except (KeyError, TypeError, ValueError):
        return ("source_tail_not_applied",)
    if (
        applied_sequence < final_sequence
        or target_observation.get("receipt_status") != "applied"
        or target_observation.get("transaction_hash") != final_hash
    ):
        return ("source_tail_not_applied",)
    return ()
