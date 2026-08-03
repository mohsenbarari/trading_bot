"""Strictly-prior intrinsic labels for coin relationship research.

These helpers create an auditable *research target* (coin bubble relative to a
real prior melted-gold anchor).  They neither calculate a user-facing rate nor
provide a production fallback.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Mapping, Sequence

from core.market_intelligence.melted_relationships import MeltedMarketEvent


COIN_RELATIONSHIP_LABEL_VERSION = "COIN_INTRINSIC_RELATIONSHIP_LABELS_V1"
RIALS_PER_PROJECT_TOMAN = 1_000.0
COIN_METHGAL_MULTIPLIERS = {
    "امام": 2.253,
    "بهار": 2.253,
    "نیم بهار": 2.253 / 2.0,
    "نیم تاریخ پایین": 2.253 / 2.0,
    "ربع بهار": 2.253 / 4.0,
    "ربع تاریخ پایین": 2.253 / 4.0,
    "یک گرمی": 2.253 / 8.130,
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("coin_relationship_timestamp_timezone_required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ConfirmedCoinTrade:
    occurred_at_utc: datetime
    commodity: str
    settlement: str
    trade_form: str
    project_price: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at_utc", _utc(self.occurred_at_utc))
        if not self.commodity or self.project_price <= 0 or not math.isfinite(self.project_price):
            raise ValueError("coin_relationship_trade_invalid")


@dataclass(frozen=True)
class CoinIntrinsicLabel:
    occurred_at_utc: datetime
    commodity: str
    settlement: str
    trade_form: str
    actual_project_price: float
    melted_anchor_market: str
    melted_anchor_at_utc: datetime
    melted_anchor_project_price: float
    intrinsic_project_price: float
    bubble_ratio: float


def _preferred_melted_markets(settlement: str) -> tuple[str, ...]:
    """Return policy order without mixing conditional and unconditional flow."""

    normalized = settlement.upper()
    if normalized in {"CASH", "TODAY"}:
        return (
            "PHYSICAL:TODAY:GENERIC",
            "PHYSICAL:UNKNOWN:GENERIC",
            "PAPER:TODAY:NORMAL",
            "PAPER:TODAY:GENERIC",
            "PAPER:TODAY:REVERSE",
            "PAPER:TODAY:SWIM",
        )
    return (
        "PAPER:TOMORROW:NORMAL",
        "PAPER:TOMORROW:GENERIC",
        "PAPER:TOMORROW:REVERSE",
        "PAPER:TOMORROW:SWIM",
    )


def select_strictly_prior_melted_anchor(
    events_by_market: Mapping[str, Sequence[MeltedMarketEvent]],
    *,
    occurred_at_utc: datetime,
    settlement: str,
    max_age: timedelta,
) -> tuple[str, MeltedMarketEvent] | None:
    """Select a real source event before the trade, never at/after it."""

    cutoff = _utc(occurred_at_utc)
    for market_key in _preferred_melted_markets(settlement):
        events = events_by_market.get(market_key, ())
        times = [event.observed_at_utc for event in events]
        index = bisect_left(times, cutoff) - 1
        if index < 0:
            continue
        event = events[index]
        if cutoff - event.observed_at_utc <= max_age:
            return market_key, event
    return None


def build_coin_intrinsic_label(
    trade: ConfirmedCoinTrade,
    *,
    events_by_market: Mapping[str, Sequence[MeltedMarketEvent]],
    max_anchor_age: timedelta,
) -> CoinIntrinsicLabel | None:
    """Build a strictly-prior bubble target only when its reference is sound."""

    multiple = COIN_METHGAL_MULTIPLIERS.get(trade.commodity)
    if multiple is None:
        return None
    selected = select_strictly_prior_melted_anchor(
        events_by_market,
        occurred_at_utc=trade.occurred_at_utc,
        settlement=trade.settlement,
        max_age=max_anchor_age,
    )
    if selected is None:
        return None
    market_key, anchor = selected
    melted_project = anchor.price / RIALS_PER_PROJECT_TOMAN
    intrinsic = melted_project * multiple
    if intrinsic <= 0.0 or not math.isfinite(intrinsic):
        return None
    return CoinIntrinsicLabel(
        occurred_at_utc=trade.occurred_at_utc,
        commodity=trade.commodity,
        settlement=trade.settlement,
        trade_form=trade.trade_form,
        actual_project_price=trade.project_price,
        melted_anchor_market=market_key,
        melted_anchor_at_utc=anchor.observed_at_utc,
        melted_anchor_project_price=melted_project,
        intrinsic_project_price=intrinsic,
        bubble_ratio=trade.project_price / intrinsic - 1.0,
    )
