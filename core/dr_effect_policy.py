"""Pure Writer-term fencing decisions for durable provider effects."""

from __future__ import annotations


RETRYABLE_EFFECT_STATES = frozenset({"pending", "failed"})
TERMINAL_EFFECT_STATES = frozenset(
    {"succeeded", "cancelled_stale_epoch"}
)


def effect_epoch_decision(
    *,
    effect_writer_epoch: int | None,
    current_writer_epoch: int,
    status: str,
) -> str:
    """Classify a retained effect without permitting an old term to execute."""

    if current_writer_epoch < 1:
        return "fenced_invalid_current_epoch"
    if status in TERMINAL_EFFECT_STATES:
        return "retain_terminal"
    if status not in RETRYABLE_EFFECT_STATES:
        return "retain_nonclaimable"
    if effect_writer_epoch != current_writer_epoch:
        return "cancel_stale_epoch"
    return "claim_current_epoch"
