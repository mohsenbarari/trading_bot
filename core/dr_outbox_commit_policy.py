"""Pure root-transaction decision for immutable DR event finalization."""

from __future__ import annotations


def outbox_commit_action(
    *,
    nested_transaction: bool,
    event_count: int,
    already_finalized: bool,
) -> str:
    """Return defer, no-op, or finalize for one SQLAlchemy commit boundary."""

    if type(event_count) is not int or event_count < 0:
        raise ValueError("DR outbox event count must be a non-negative integer")
    if nested_transaction:
        return "defer"
    if event_count == 0 or already_finalized:
        return "no_op"
    return "finalize"
