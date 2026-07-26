"""Pure ordering policy for the three-site DR projection stream.

This module intentionally has no runtime settings or database imports so
campaign tooling can independently exercise the exact production ordering
decision without acquiring application credentials.
"""

from __future__ import annotations


def projection_version_decision(
    *,
    stored_epoch: int | None,
    stored_sequence: int | None,
    stored_origin_site: str | None,
    incoming_epoch: int,
    incoming_sequence: int,
    incoming_origin_site: str,
) -> str:
    """Apply a newer authority term, suppress stale terms, quarantine split brain."""

    if stored_epoch is None:
        return "apply"
    if incoming_epoch < stored_epoch:
        return "stale"
    if incoming_epoch > stored_epoch:
        return "apply"
    if stored_origin_site != incoming_origin_site:
        return "conflict"
    if incoming_sequence <= int(stored_sequence or 0):
        return "stale"
    return "apply"
