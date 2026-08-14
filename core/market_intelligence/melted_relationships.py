"""Leakage-safe primitives for discovering melted-gold market relationships.

This module intentionally does *not* estimate a coin price.  It describes the
separate melted-gold markets at a historical cutoff: paper normal/reverse/swim,
physical conditional/unconditional, and today/tomorrow settlement.  Offline
research can then test whether a feature available at that cutoff explains a
future movement in another market.

The distinction is important: a relationship candidate is evidence for shadow
evaluation, never an automatic weight or a production price adjustment.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from statistics import fmean
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import jdatetime


TEHRAN = ZoneInfo("Asia/Tehran")
FEATURE_VERSION = "MELTED_RELATIONSHIP_FEATURES_V1"
MINUTES = (1, 3, 5, 10, 15)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("relationship_timestamp_timezone_required")
    return value.astimezone(timezone.utc)


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _imbalance(buy: int, sell: int) -> float | None:
    total = buy + sell
    return (buy - sell) / total if total else None


@dataclass(frozen=True)
class MeltedMarketEvent:
    """One normalized offer or confirmed-trade event from a melted market."""

    observed_at_utc: datetime
    market_key: str
    event_type: str
    side: str
    price: float
    quantity: float | None = None
    conditional: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at_utc", _utc(self.observed_at_utc))
        if self.event_type not in {"OFFER", "TRADE"}:
            raise ValueError("melted_event_type_invalid")
        if self.side not in {"BUY", "SELL", "UNKNOWN"}:
            raise ValueError("melted_event_side_invalid")
        if self.price <= 0 or not math.isfinite(self.price):
            raise ValueError("melted_event_price_invalid")
        if self.quantity is not None and (
            self.quantity < 0 or not math.isfinite(self.quantity)
        ):
            raise ValueError("melted_event_quantity_invalid")


def market_key_from_fields(
    *,
    trade_form: str,
    settlement: str,
    market_label: str,
) -> tuple[str, bool]:
    """Return a stable market key without silently mixing market variants."""

    form = str(trade_form or "UNKNOWN").upper()
    term = str(settlement or "UNKNOWN").upper()
    label = str(market_label or "")
    canonical_label = label.upper()
    conditional = "شرطی" in label or "CONDITIONAL" in canonical_label
    if form == "PAPER_NORMAL" or "کاغذی عادی" in label or "PAPER_NORMAL" in canonical_label:
        variant = "NORMAL"
    elif form == "PAPER_REVERSE" or "معکوس" in label or "PAPER_REVERSE" in canonical_label:
        variant = "REVERSE"
    elif form == "PAPER_SWIM" or "شنا" in label or "PAPER_SWIM" in canonical_label:
        variant = "SWIM"
    elif conditional:
        variant = "CONDITIONAL"
    else:
        variant = "GENERIC"
    form_family = "PAPER" if form.startswith("PAPER_") else form
    return f"{form_family}:{term}:{variant}", conditional


def iran_calendar_features(as_of_utc: datetime) -> dict[str, int | str]:
    """Calendar features at a cutoff, always in the Iran market timezone."""

    local = _utc(as_of_utc).astimezone(TEHRAN)
    jalali = jdatetime.datetime.fromgregorian(datetime=local.replace(tzinfo=None))
    return {
        "timezone": "Asia/Tehran",
        "tehran_hour": local.hour,
        "tehran_minute": local.minute,
        "tehran_minute_of_day": local.hour * 60 + local.minute,
        "tehran_weekday_iso": local.isoweekday(),
        "jalali_year": jalali.year,
        "jalali_month": jalali.month,
        "jalali_day": jalali.day,
    }


def _latest_run(events: Sequence[MeltedMarketEvent], *, event_type: str | None) -> tuple[str, int]:
    side = "UNKNOWN"
    length = 0
    for event in reversed(events):
        if event_type is not None and event.event_type != event_type:
            continue
        if event.side == "UNKNOWN":
            continue
        if side == "UNKNOWN":
            side = event.side
        if event.side != side:
            break
        length += 1
    return side, length


def build_melted_window_features(
    events: Iterable[MeltedMarketEvent],
    *,
    as_of_utc: datetime,
    window: timedelta,
) -> dict[str, float | int | str | None]:
    """Build strictly-prior-or-current features for one market and one window."""

    cutoff = _utc(as_of_utc)
    start = cutoff - window
    accepted = sorted(
        (
            event
            for event in events
            if start <= event.observed_at_utc <= cutoff
        ),
        key=lambda event: event.observed_at_utc,
    )
    offers = [event for event in accepted if event.event_type == "OFFER"]
    trades = [event for event in accepted if event.event_type == "TRADE"]
    buy_offers = sum(event.side == "BUY" for event in offers)
    sell_offers = sum(event.side == "SELL" for event in offers)
    buy_trades = sum(event.side == "BUY" for event in trades)
    sell_trades = sum(event.side == "SELL" for event in trades)
    prices = [event.price for event in accepted]
    quantities = [event.quantity for event in trades if event.quantity is not None]
    latest_side, latest_run = _latest_run(accepted, event_type=None)
    latest_trade_side, latest_trade_run = _latest_run(accepted, event_type="TRADE")
    first_price = prices[0] if prices else None
    last_price = prices[-1] if prices else None
    return {
        "feature_version": FEATURE_VERSION,
        "window_seconds": int(window.total_seconds()),
        "event_count": len(accepted),
        "offer_count": len(offers),
        "trade_count": len(trades),
        "buy_offer_count": buy_offers,
        "sell_offer_count": sell_offers,
        "buy_trade_count": buy_trades,
        "sell_trade_count": sell_trades,
        "offer_imbalance": _imbalance(buy_offers, sell_offers),
        "trade_imbalance": _imbalance(buy_trades, sell_trades),
        "trade_share": len(trades) / len(accepted) if accepted else None,
        "confirmed_quantity_sum": sum(quantities) if quantities else 0.0,
        "confirmed_quantity_count": len(quantities),
        "first_price": first_price,
        "last_price": last_price,
        "price_change_bps": (
            (last_price / first_price - 1.0) * 10_000
            if first_price and last_price
            else None
        ),
        "latest_directional_side": latest_side,
        "latest_directional_run": latest_run,
        "latest_trade_side": latest_trade_side,
        "latest_trade_run": latest_trade_run,
        "staleness_seconds": (
            int((cutoff - accepted[-1].observed_at_utc).total_seconds())
            if accepted
            else None
        ),
    }


def cross_market_spread_bps(
    left: Mapping[str, object], right: Mapping[str, object]
) -> float | None:
    """Return a directional spread only when both current prices are usable."""

    left_price = _finite(left.get("last_price"))
    right_price = _finite(right.get("last_price"))
    if left_price is None or right_price is None or right_price <= 0:
        return None
    return (left_price / right_price - 1.0) * 10_000


@dataclass(frozen=True)
class RelationshipObservation:
    """A candidate feature and a strictly later realized target movement."""

    feature_name: str
    target_name: str
    available_at_utc: datetime
    realized_at_utc: datetime
    feature_value: float
    target_return_bps: float

    def __post_init__(self) -> None:
        available = _utc(self.available_at_utc)
        realized = _utc(self.realized_at_utc)
        if realized <= available:
            raise ValueError("relationship_observation_requires_strictly_future_target")
        if _finite(self.feature_value) is None or _finite(self.target_return_bps) is None:
            raise ValueError("relationship_observation_value_invalid")
        object.__setattr__(self, "available_at_utc", available)
        object.__setattr__(self, "realized_at_utc", realized)


def _pearson(values: Sequence[float], targets: Sequence[float]) -> float | None:
    if len(values) < 2 or len(values) != len(targets):
        return None
    mean_value, mean_target = fmean(values), fmean(targets)
    numerator = sum((x - mean_value) * (y - mean_target) for x, y in zip(values, targets))
    left = math.sqrt(sum((x - mean_value) ** 2 for x in values))
    right = math.sqrt(sum((y - mean_target) ** 2 for y in targets))
    if not left or not right:
        return None
    return numerator / (left * right)


def rank_relationships(
    observations: Iterable[RelationshipObservation], *, min_samples: int = 30
) -> list[dict[str, float | int | str]]:
    """Rank descriptive shadow candidates; never return a production weight."""

    grouped: dict[tuple[str, str], list[RelationshipObservation]] = {}
    for item in observations:
        grouped.setdefault((item.feature_name, item.target_name), []).append(item)
    ranked: list[dict[str, float | int | str]] = []
    for (feature, target), rows in grouped.items():
        if len(rows) < min_samples:
            continue
        values = [row.feature_value for row in rows]
        targets = [row.target_return_bps for row in rows]
        correlation = _pearson(values, targets)
        if correlation is None:
            continue
        same_sign = sum((value == 0) or (target == 0) or ((value > 0) == (target > 0)) for value, target in zip(values, targets))
        ranked.append(
            {
                "feature_name": feature,
                "target_name": target,
                "sample_count": len(rows),
                "pearson_correlation": round(correlation, 6),
                "directional_agreement": round(same_sign / len(rows), 6),
                "first_available_at_utc": min(row.available_at_utc for row in rows).isoformat(),
                "last_available_at_utc": max(row.available_at_utc for row in rows).isoformat(),
            }
        )
    return sorted(
        ranked,
        key=lambda row: (abs(float(row["pearson_correlation"])), int(row["sample_count"])),
        reverse=True,
    )
