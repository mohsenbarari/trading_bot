"""Legacy-compatible selection of real XAU quotes consumed by inference.

The public channel is captured event-by-event on ``wa-fi`` for auditability.
The established estimator contract is narrower: within each fixed fifteen
second UTC bucket only the latest real quote is an input sample.  This module
does not poll, interpolate, forward-fill, or create a synthetic observation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, TypeVar

from .market_contracts import normalize_utc


XAU_MODEL_INPUT_BUCKET_SECONDS = 15
# A bucket is exported only after this small grace period.  Telethon delivery
# is normally sub-second; the grace prevents exporting an early quote just as
# the UTC bucket closes while keeping the effective input comfortably inside
# the estimator's real 90-second window.
XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS = 5
_Row = TypeVar("_Row")


def xau_model_input_bucket(event_time_utc: object) -> int:
    normalized = normalize_utc(
        event_time_utc,
        field_name="xau_model_input_event_time_utc",
    )
    moment = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return int(moment.astimezone(timezone.utc).timestamp()) // XAU_MODEL_INPUT_BUCKET_SECONDS


def latest_real_xau_rows(rows: Iterable[_Row]) -> tuple[_Row, ...]:
    """Return the latest supplied row in each bucket, ordered by event time.

    Callers must supply rows in ascending ``event_time_utc,id`` order.  A later
    row replaces only the in-memory candidate for its bucket; the captured raw
    row remains untouched in the source Store.
    """

    latest: dict[int, _Row] = {}
    for row in rows:
        latest[xau_model_input_bucket(row["event_time_utc"])] = row  # type: ignore[index]
    return tuple(latest[bucket] for bucket in sorted(latest))


__all__ = [
    "XAU_MODEL_INPUT_BUCKET_SECONDS",
    "XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS",
    "latest_real_xau_rows",
    "xau_model_input_bucket",
]
