"""Pure, credential-free ordering policy for DR stream receipts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceiptDecision:
    action: str
    missing_from: int | None = None
    missing_to: int | None = None
    reason: str | None = None


def decide_receipt_sequence(
    *,
    contiguous_sequence: int,
    incoming_sequence: int,
    incoming_hash: str,
    existing_event_hash: str | None = None,
    existing_sequence_hash: str | None = None,
) -> ReceiptDecision:
    """Decide duplicate, gap, conflict, rewind, or contiguous application."""

    if existing_event_hash is not None:
        if existing_event_hash == incoming_hash:
            return ReceiptDecision("duplicate")
        return ReceiptDecision("quarantine", reason="same_event_id_different_hash")
    if existing_sequence_hash is not None and existing_sequence_hash != incoming_hash:
        return ReceiptDecision("quarantine", reason="same_sequence_different_hash")
    expected = int(contiguous_sequence) + 1
    if incoming_sequence < expected:
        return ReceiptDecision(
            "quarantine",
            reason="sequence_rewind_without_receipt",
        )
    if incoming_sequence > expected:
        return ReceiptDecision(
            "blocked_gap",
            missing_from=expected,
            missing_to=incoming_sequence - 1,
        )
    return ReceiptDecision("apply")
