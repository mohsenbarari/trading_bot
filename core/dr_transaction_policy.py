"""Pure validation policy for one destination-visible DR transaction group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.dr_event_protocol import destination_transaction_hash


@dataclass(frozen=True)
class TransactionGroupDecision:
    action: str
    reason: str | None = None
    ordered_event_ids: tuple[str, ...] = ()


def decide_transaction_group(
    members: Iterable[dict[str, Any]],
    *,
    destination_site: str,
    next_sequence: int,
) -> TransactionGroupDecision:
    """Accept only a complete, contiguous and hash-bound destination group."""

    rows = [dict(member) for member in members]
    if not rows:
        return TransactionGroupDecision("deferred")
    try:
        rows.sort(key=lambda row: int(row["sequence"]))
    except (KeyError, TypeError, ValueError):
        return TransactionGroupDecision(
            "reject",
            "destination_transaction_metadata_missing",
        )
    first = rows[0]
    try:
        expected_size = int(first["transaction_size"])
        transaction_id = str(first["transaction_id"])
        first_position = int(first["transaction_position"])
    except (KeyError, TypeError, ValueError):
        return TransactionGroupDecision(
            "reject",
            "destination_transaction_metadata_missing",
        )
    if first_position != 1:
        return TransactionGroupDecision(
            "reject",
            "transaction_group_does_not_start_at_position_one",
        )
    if expected_size < 1:
        return TransactionGroupDecision(
            "reject",
            "transaction_group_cardinality_mismatch",
        )
    if len(rows) < expected_size:
        return TransactionGroupDecision("deferred")
    if len(rows) != expected_size:
        return TransactionGroupDecision(
            "reject",
            "transaction_group_cardinality_mismatch",
        )
    try:
        ordered = sorted(rows, key=lambda row: int(row["transaction_position"]))
        positions = [int(row["transaction_position"]) for row in ordered]
        sequences = [int(row["sequence"]) for row in ordered]
        signed_sequences = [
            int(row["envelope"]["destination_streams"][destination_site]["sequence"])
            for row in ordered
        ]
        event_ids = tuple(str(row["event_id"]) for row in ordered)
    except (KeyError, TypeError, ValueError):
        return TransactionGroupDecision(
            "reject",
            "destination_transaction_metadata_missing",
        )
    if (
        any(str(row.get("transaction_id") or "") != transaction_id for row in ordered)
        or positions != list(range(1, expected_size + 1))
        or sequences != list(range(int(next_sequence), int(next_sequence) + expected_size))
        or signed_sequences != sequences
        or len(set(event_ids)) != len(event_ids)
    ):
        return TransactionGroupDecision(
            "reject",
            "transaction_group_order_mismatch",
        )
    try:
        group_hash = destination_transaction_hash(
            [row["envelope"] for row in ordered],
            destination_site=destination_site,
        )
    except Exception:
        return TransactionGroupDecision(
            "reject",
            "destination_transaction_metadata_missing",
        )
    if any(str(row.get("transaction_hash") or "") != group_hash for row in ordered):
        return TransactionGroupDecision(
            "reject",
            "transaction_group_hash_mismatch",
        )
    return TransactionGroupDecision("ready", ordered_event_ids=event_ids)
