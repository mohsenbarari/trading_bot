#!/usr/bin/env python3
"""Discover leakage-safe melted-market relationships in Shadow mode.

This job answers a narrow research question: given only information that was
already observable at a Tehran-market cutoff, which *melted-gold* order-flow,
settlement and calendar features consistently preceded a later movement in a
separate melted segment or in the external coin reference feed?

It never estimates a user-facing price, writes no production table, changes no
runtime weight, and cannot promote a relationship automatically.  Reports and
optional feature datasets deliberately contain aggregate numeric information
only: no offer text, source post id, or participant identity.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
from typing import Iterable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.melted_relationships import (
    FEATURE_VERSION,
    MeltedMarketEvent,
    build_melted_window_features,
    cross_market_spread_bps,
    iran_calendar_features,
    market_key_from_fields,
)
from core.market_intelligence.coin_relationships import (
    ConfirmedCoinTrade,
    build_coin_intrinsic_label,
)


DISCOVERY_VERSION = "MELTED_MARKET_RELATIONSHIP_DISCOVERY_V1_SHADOW_20260803"
DEFAULT_WINDOWS = (1, 3, 5, 10, 15)
DEFAULT_HORIZON_MINUTES = 5
DEFAULT_SAMPLE_STEP_MINUTES = 5
DEFAULT_MAX_TARGET_AGE_MINUTES = 3
MINIMUM_SAMPLES = 40
MINIMUM_ABSOLUTE_CORRELATION = 0.10
MINIMUM_DIRECTIONAL_AGREEMENT = 0.52
NUMERIC_FEATURES = (
    "event_count",
    "offer_count",
    "trade_count",
    "offer_imbalance",
    "trade_imbalance",
    "trade_share",
    "confirmed_quantity_sum",
    "price_change_bps",
    "latest_directional_run_signed",
    "latest_trade_run_signed",
    "staleness_seconds",
)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event_time_timezone_required")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _floor_minute(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("relationship_shadow_output_must_be_outside_repository")
    return resolved


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _open_jsonl(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    handle = temporary.open("w", encoding="utf-8")
    os.chmod(temporary, 0o600)
    return handle, temporary


def _finite(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _signed(side: object, run: object) -> float | None:
    numeric = _finite(run)
    if numeric is None:
        return None
    if side == "BUY":
        return numeric
    if side == "SELL":
        return -numeric
    return 0.0


def _numeric_features(features: dict) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for name in NUMERIC_FEATURES:
        value = _finite(features.get(name))
        if value is not None:
            normalized[name] = value
    signed_direction = _signed(
        features.get("latest_directional_side"),
        features.get("latest_directional_run"),
    )
    if signed_direction is not None:
        normalized["latest_directional_run_signed"] = signed_direction
    signed_trade = _signed(
        features.get("latest_trade_side"), features.get("latest_trade_run")
    )
    if signed_trade is not None:
        normalized["latest_trade_run_signed"] = signed_trade
    return normalized


def _calendar_numeric(as_of: datetime) -> dict[str, float]:
    calendar = iran_calendar_features(as_of)
    minute = float(calendar["tehran_minute_of_day"])
    weekday = float(calendar["tehran_weekday_iso"])
    jalali_month = float(calendar["jalali_month"])
    return {
        "tehran_hour_sin": math.sin(2.0 * math.pi * minute / 1440.0),
        "tehran_hour_cos": math.cos(2.0 * math.pi * minute / 1440.0),
        "tehran_weekday_sin": math.sin(2.0 * math.pi * weekday / 7.0),
        "tehran_weekday_cos": math.cos(2.0 * math.pi * weekday / 7.0),
        "jalali_month_sin": math.sin(2.0 * math.pi * jalali_month / 12.0),
        "jalali_month_cos": math.cos(2.0 * math.pi * jalali_month / 12.0),
    }


@dataclass(frozen=True)
class TargetPoint:
    market_key: str
    observed_at_utc: datetime
    price: float


@dataclass(frozen=True)
class SupportQuoteEvent:
    """A non-order-flow support observation with its real source timestamp."""

    observed_at_utc: datetime
    source_key: str
    price: float


@dataclass
class RunningStats:
    sample_count: int = 0
    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_xx: float = 0.0
    sum_yy: float = 0.0
    sum_xy: float = 0.0
    directional_same: int = 0

    def add(self, value: float, target: float) -> None:
        if not math.isfinite(value) or not math.isfinite(target):
            return
        self.sample_count += 1
        self.sum_x += value
        self.sum_y += target
        self.sum_xx += value * value
        self.sum_yy += target * target
        self.sum_xy += value * target
        if value == 0.0 or target == 0.0 or (value > 0.0) == (target > 0.0):
            self.directional_same += 1

    def correlation(self) -> float | None:
        if self.sample_count < 2:
            return None
        count = float(self.sample_count)
        numerator = count * self.sum_xy - self.sum_x * self.sum_y
        left = count * self.sum_xx - self.sum_x * self.sum_x
        right = count * self.sum_yy - self.sum_y * self.sum_y
        if left <= 0.0 or right <= 0.0:
            return None
        return numerator / math.sqrt(left * right)

    def as_dict(self) -> dict[str, float | int | None]:
        correlation = self.correlation()
        return {
            "sample_count": self.sample_count,
            "pearson_correlation": (
                round(correlation, 6) if correlation is not None else None
            ),
            "directional_agreement": (
                round(self.directional_same / self.sample_count, 6)
                if self.sample_count
                else None
            ),
        }


@dataclass
class CandidateStats:
    fit: RunningStats = field(default_factory=RunningStats)
    validation: RunningStats = field(default_factory=RunningStats)
    test: RunningStats = field(default_factory=RunningStats)

    def partition(self, name: str) -> RunningStats:
        return getattr(self, name)


def _row_target_key(row: sqlite3.Row) -> tuple[str, bool] | None:
    instrument = str(row["instrument"] or "")
    if instrument == "MELTED_GOLD_FLOW":
        return market_key_from_fields(
            trade_form=str(row["trade_form"] or "UNKNOWN"),
            settlement=str(row["settlement_term"] or "UNKNOWN"),
            market_label=str(row["market_label"] or ""),
        )
    if instrument == "GOLD_COIN":
        trade_form = str(row["trade_form"] or "UNKNOWN").upper()
        if trade_form not in {"PAPER", "PHYSICAL"}:
            return None
        # The public reference feed has no coin-kind dimension.  It is kept
        # separate from product-specific project offers and is used only as a
        # generic external coin-return target in research.
        return f"COIN_REFERENCE:{trade_form}", False
    return None


def _support_key(row: sqlite3.Row) -> tuple[str, bool] | None:
    """Keep support-series dimensions explicit instead of replacing sources."""

    instrument = str(row["instrument"] or "")
    label = str(row["market_label"] or "")
    conditional = "شرطی" in label
    form = str(row["trade_form"] or "UNKNOWN").upper()
    settlement = str(row["settlement_term"] or "UNKNOWN").upper()
    if instrument == "MELTED_GOLD":
        return f"MELTED_REFERENCE:{form}:{settlement}", conditional
    if instrument == "USD_HERAT":
        return f"USD_HERAT:{form}:{settlement}", conditional
    if instrument in {"XAUUSD", "GOLD_UNION_QUOTE", "AED_DUBAI"}:
        return instrument, conditional
    return None


def load_market_data(
    database: Path,
    *,
    include_coin_reference: bool,
    include_conditional: bool,
    since_utc: datetime | None,
    until_utc: datetime | None,
) -> tuple[
    dict[str, list[MeltedMarketEvent]],
    dict[str, list[SupportQuoteEvent]],
    dict[str, list[TargetPoint]],
    dict,
]:
    """Load only numeric normalized events; no post text or participant data."""

    database = database.expanduser().resolve()
    if not database.exists():
        raise ValueError("market_database_not_found")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT instrument, market_label, settlement_term, trade_form, event_type,
               side, price_num, quantity_num, event_time_utc
        FROM price_events
        WHERE instrument IN (
            'MELTED_GOLD_FLOW', 'GOLD_COIN', 'MELTED_GOLD', 'USD_HERAT',
            'XAUUSD', 'GOLD_UNION_QUOTE', 'AED_DUBAI'
        )
          AND price_num IS NOT NULL AND price_num > 0
          AND event_time_utc IS NOT NULL
        ORDER BY event_time_utc ASC, id ASC
    """
    melted: dict[str, list[MeltedMarketEvent]] = {}
    targets: dict[str, list[TargetPoint]] = {}
    support: dict[str, list[SupportQuoteEvent]] = {}
    discarded = {"conditional": 0, "outside_interval": 0, "unsupported": 0}
    try:
        for row in connection.execute(query):
            try:
                observed_at = _parse_utc(str(row["event_time_utc"]))
                price = _finite(row["price_num"])
            except ValueError:
                discarded["unsupported"] += 1
                continue
            if price is None or price <= 0:
                discarded["unsupported"] += 1
                continue
            if since_utc is not None and observed_at < since_utc:
                discarded["outside_interval"] += 1
                continue
            if until_utc is not None and observed_at > until_utc:
                discarded["outside_interval"] += 1
                continue
            instrument = str(row["instrument"])
            if instrument == "MELTED_GOLD_FLOW":
                target_info = _row_target_key(row)
                if target_info is None:
                    discarded["unsupported"] += 1
                    continue
                market_key, conditional = target_info
                if conditional and not include_conditional:
                    discarded["conditional"] += 1
                    continue
                event_type = str(row["event_type"] or "").upper()
                side = str(row["side"] or "UNKNOWN").upper()
                if event_type not in {"OFFER", "TRADE"}:
                    discarded["unsupported"] += 1
                    continue
                if side not in {"BUY", "SELL"}:
                    side = "UNKNOWN"
                quantity = _finite(row["quantity_num"])
                event = MeltedMarketEvent(
                    observed_at_utc=observed_at,
                    market_key=market_key,
                    event_type=event_type,
                    side=side,
                    price=price,
                    quantity=quantity,
                    conditional=conditional,
                )
                melted.setdefault(market_key, []).append(event)
                targets.setdefault(market_key, []).append(
                    TargetPoint(market_key, observed_at, price)
                )
            elif instrument == "GOLD_COIN" and include_coin_reference:
                target_info = _row_target_key(row)
                if target_info is None:
                    discarded["unsupported"] += 1
                    continue
                market_key, conditional = target_info
                if conditional and not include_conditional:
                    discarded["conditional"] += 1
                    continue
                targets.setdefault(market_key, []).append(
                    TargetPoint(market_key, observed_at, price)
                )
            elif instrument == "GOLD_COIN":
                # The caller intentionally disabled the external reference
                # target; it is not a malformed normalized observation.
                continue
            else:
                support_info = _support_key(row)
                if support_info is None:
                    discarded["unsupported"] += 1
                    continue
                support_key, support_conditional = support_info
                if support_conditional and not include_conditional:
                    discarded["conditional"] += 1
                    continue
                support.setdefault(support_key, []).append(
                    SupportQuoteEvent(observed_at, support_key, price)
                )
    finally:
        connection.close()
    return melted, support, targets, discarded


def load_confirmed_coin_trade_targets(
    database: Path,
    *,
    since_utc: datetime | None,
    until_utc: datetime | None,
) -> tuple[dict[str, list[TargetPoint]], list[ConfirmedCoinTrade], dict[str, object]]:
    """Load product-specific confirmed trades without reading text or people.

    This adapter intentionally uses only the already normalized confirmed trade
    training table.  Offers are valuable market evidence, but do not become an
    outcome label merely because they were posted.
    """

    database = database.expanduser().resolve()
    if not database.exists():
        raise ValueError("coin_trade_target_database_not_found")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    query = """
        SELECT occurred_at_utc, commodity, settlement, trade_form, price
        FROM confirmed_trade_training_examples
        WHERE price > 0 AND occurred_at_utc IS NOT NULL
        ORDER BY occurred_at_utc ASC, id ASC
    """
    targets: dict[str, list[TargetPoint]] = {}
    trades: list[ConfirmedCoinTrade] = []
    discarded = 0
    try:
        for row in connection.execute(query):
            try:
                observed_at = _parse_utc(str(row["occurred_at_utc"]))
                price = _finite(row["price"])
            except ValueError:
                discarded += 1
                continue
            if price is None or price <= 0:
                discarded += 1
                continue
            if since_utc is not None and observed_at < since_utc:
                continue
            if until_utc is not None and observed_at > until_utc:
                continue
            commodity = str(row["commodity"] or "").strip()
            settlement = str(row["settlement"] or "UNKNOWN").upper()
            trade_form = str(row["trade_form"] or "UNKNOWN").upper()
            if not commodity or settlement == "" or trade_form == "":
                discarded += 1
                continue
            key = f"COIN_TRADE:{commodity}:{settlement}:{trade_form}"
            targets.setdefault(key, []).append(TargetPoint(key, observed_at, price))
            trades.append(
                ConfirmedCoinTrade(
                    occurred_at_utc=observed_at,
                    commodity=commodity,
                    settlement=settlement,
                    trade_form=trade_form,
                    project_price=price,
                )
            )
    except sqlite3.OperationalError as exc:
        raise ValueError("coin_trade_target_schema_invalid") from exc
    finally:
        connection.close()
    return targets, trades, {
        "enabled": True,
        "target_rows": sum(len(values) for values in targets.values()),
        "target_markets": {key: len(values) for key, values in sorted(targets.items())},
        "discarded_rows": discarded,
    }


def build_coin_intrinsic_dataset(
    melted: dict[str, list[MeltedMarketEvent]],
    support: dict[str, list[SupportQuoteEvent]],
    trades: list[ConfirmedCoinTrade],
    *,
    windows: tuple[int, ...],
    max_anchor_age_minutes: int,
    write_dataset,
) -> dict[str, object]:
    """Build sparse, product-specific bubble labels for a later challenger."""

    event_times = {
        key: [event.observed_at_utc for event in values]
        for key, values in melted.items()
    }
    support_times = {
        key: [event.observed_at_utc for event in values]
        for key, values in support.items()
    }
    bubble_by_market: dict[str, list[float]] = {}
    produced = 0
    skipped = 0
    for trade in sorted(trades, key=lambda item: item.occurred_at_utc):
        label = build_coin_intrinsic_label(
            trade,
            events_by_market=melted,
            max_anchor_age=timedelta(minutes=max_anchor_age_minutes),
        )
        if label is None:
            skipped += 1
            continue
        # The target trade is never visible to its own feature row.
        cutoff = label.occurred_at_utc - timedelta(microseconds=1)
        features: dict[str, float] = _calendar_numeric(cutoff)
        for source_key, source_events in melted.items():
            for window_minutes in windows:
                feature_set = build_melted_window_features(
                    _slice_events(
                        source_events,
                        event_times[source_key],
                        as_of_utc=cutoff,
                        window=timedelta(minutes=window_minutes),
                    ),
                    as_of_utc=cutoff,
                    window=timedelta(minutes=window_minutes),
                )
                for name, value in _numeric_features(feature_set).items():
                    features[f"{source_key}|{window_minutes}m|{name}"] = value
        for source_key, source_events in support.items():
            for window_minutes in windows:
                quote_features = _build_support_quote_features(
                    source_events,
                    support_times[source_key],
                    as_of_utc=cutoff,
                    window=timedelta(minutes=window_minutes),
                )
                for name, value in quote_features.items():
                    numeric = _finite(value)
                    if numeric is not None:
                        features[f"SUPPORT:{source_key}|{window_minutes}m|{name}"] = numeric
        target_market = f"{label.commodity}:{label.settlement}:{label.trade_form}"
        bubble_by_market.setdefault(target_market, []).append(label.bubble_ratio)
        if write_dataset is not None:
            record = {
                "schema_version": "COIN_INTRINSIC_RELATIONSHIP_DATASET_V1_SHADOW_20260803",
                "available_at_utc": _iso_utc(cutoff),
                "realized_at_utc": _iso_utc(label.occurred_at_utc),
                "commodity": label.commodity,
                "settlement": label.settlement,
                "trade_form": label.trade_form,
                "melted_anchor_market": label.melted_anchor_market,
                "melted_anchor_age_seconds": round(
                    (label.occurred_at_utc - label.melted_anchor_at_utc).total_seconds(), 6
                ),
                "intrinsic_project_price": round(label.intrinsic_project_price, 6),
                "actual_project_price": round(label.actual_project_price, 6),
                "bubble_ratio": round(label.bubble_ratio, 8),
                "features": {name: round(value, 8) for name, value in sorted(features.items())},
            }
            write_dataset.write(json.dumps(record, ensure_ascii=False) + "\n")
        produced += 1
    return {
        "status": "SHADOW_LABELS_NOT_TRAINED",
        "max_anchor_age_minutes": max_anchor_age_minutes,
        "produced_labels": produced,
        "skipped_without_eligible_prior_anchor_or_formula": skipped,
        "bubble_summary": {
            market: {
                "count": len(values),
                "median_ratio": round(statistics.median(values), 8),
                "minimum_ratio": round(min(values), 8),
                "maximum_ratio": round(max(values), 8),
            }
            for market, values in sorted(bubble_by_market.items())
        },
    }


def chronological_boundaries(points: Iterable[TargetPoint]) -> tuple[datetime, datetime]:
    times = sorted({point.observed_at_utc for point in points})
    if len(times) < 5:
        raise ValueError("insufficient_target_time_coverage")
    return times[int(len(times) * 0.60)], times[int(len(times) * 0.80)]


def chronological_partition(
    available_at_utc: datetime,
    *,
    validation_start: datetime,
    test_start: datetime,
) -> str:
    if available_at_utc < validation_start:
        return "fit"
    if available_at_utc < test_start:
        return "validation"
    return "test"


def _target_samples(
    points: list[TargetPoint],
    *,
    horizon: timedelta,
    max_target_age: timedelta,
    step: timedelta,
) -> Iterator[tuple[datetime, float, datetime, float]]:
    """Yield current/future target prices with strict future realization."""

    ordered = sorted(points, key=lambda point: point.observed_at_utc)
    times = [point.observed_at_utc for point in ordered]
    current = _floor_minute(ordered[0].observed_at_utc)
    end = _floor_minute(ordered[-1].observed_at_utc) - horizon
    while current <= end:
        prior_index = bisect_right(times, current) - 1
        future_index = bisect_left(times, current + horizon)
        if prior_index >= 0 and future_index < len(ordered):
            anchor = ordered[prior_index]
            realized = ordered[future_index]
            if (
                current - anchor.observed_at_utc <= max_target_age
                and realized.observed_at_utc > current
                and realized.observed_at_utc <= current + horizon + max_target_age
                and anchor.price > 0.0
            ):
                target_return = (realized.price / anchor.price - 1.0) * 10_000.0
                yield current, anchor.price, realized.observed_at_utc, target_return
        current += step


def _slice_events(
    events: list[MeltedMarketEvent],
    event_times: list[datetime],
    *,
    as_of_utc: datetime,
    window: timedelta,
) -> list[MeltedMarketEvent]:
    start_index = bisect_left(event_times, as_of_utc - window)
    end_index = bisect_right(event_times, as_of_utc)
    return events[start_index:end_index]


def _build_support_quote_features(
    events: list[SupportQuoteEvent],
    event_times: list[datetime],
    *,
    as_of_utc: datetime,
    window: timedelta,
) -> dict[str, float | int | None]:
    """Point-in-time support quote features; never fabricate an absent feed."""

    accepted = events[
        bisect_left(event_times, as_of_utc - window) : bisect_right(
            event_times, as_of_utc
        )
    ]
    if not accepted:
        return {
            "quote_count": 0,
            "quote_return_bps": None,
            "quote_staleness_seconds": None,
        }
    first = accepted[0].price
    last = accepted[-1].price
    return {
        "quote_count": len(accepted),
        "quote_return_bps": (
            (last / first - 1.0) * 10_000.0 if first > 0.0 else None
        ),
        "quote_staleness_seconds": int(
            (as_of_utc - accepted[-1].observed_at_utc).total_seconds()
        ),
    }


def _stable_candidate(
    stats: CandidateStats,
    *,
    min_samples: int,
    min_absolute_correlation: float,
    min_directional_agreement: float,
) -> bool:
    rows = [stats.fit.as_dict(), stats.validation.as_dict(), stats.test.as_dict()]
    correlations = [row["pearson_correlation"] for row in rows]
    return (
        all(int(row["sample_count"]) >= min_samples for row in rows)
        and all(value is not None for value in correlations)
        and all(abs(float(value)) >= min_absolute_correlation for value in correlations)
        and all(
            float(row["directional_agreement"] or 0.0)
            >= min_directional_agreement
            for row in rows
        )
        and len({float(value) > 0.0 for value in correlations}) == 1
    )


def discover(
    melted: dict[str, list[MeltedMarketEvent]],
    support: dict[str, list[SupportQuoteEvent]],
    targets: dict[str, list[TargetPoint]],
    *,
    windows: tuple[int, ...],
    horizon_minutes: int,
    sample_step_minutes: int,
    max_target_age_minutes: int,
    min_samples: int,
    min_absolute_correlation: float,
    min_directional_agreement: float,
    write_dataset,
) -> dict:
    if not melted:
        raise ValueError("no_melted_events_available")
    all_points = [point for values in targets.values() for point in values]
    validation_start, test_start = chronological_boundaries(all_points)
    event_times = {
        key: [event.observed_at_utc for event in values]
        for key, values in melted.items()
    }
    support_times = {
        key: [event.observed_at_utc for event in values]
        for key, values in support.items()
    }
    horizon = timedelta(minutes=horizon_minutes)
    max_target_age = timedelta(minutes=max_target_age_minutes)
    step = timedelta(minutes=sample_step_minutes)
    stats_by_candidate: dict[tuple[str, str], CandidateStats] = {}
    generated_rows = 0
    target_sample_count: dict[str, int] = {}
    for target_key, target_points in sorted(targets.items()):
        for cutoff, target_anchor, realized_at, target_return in _target_samples(
            target_points,
            horizon=horizon,
            max_target_age=max_target_age,
            step=step,
        ):
            partition = chronological_partition(
                cutoff,
                validation_start=validation_start,
                test_start=test_start,
            )
            row_features: dict[str, float] = {}
            latest_features: dict[str, dict] = {}
            for source_key, source_events in melted.items():
                for window_minutes in windows:
                    window = timedelta(minutes=window_minutes)
                    feature_set = build_melted_window_features(
                        _slice_events(
                            source_events,
                            event_times[source_key],
                            as_of_utc=cutoff,
                            window=window,
                        ),
                        as_of_utc=cutoff,
                        window=window,
                    )
                    if window_minutes == max(windows):
                        latest_features[source_key] = feature_set
                    for name, value in _numeric_features(feature_set).items():
                        row_features[
                            f"{source_key}|{window_minutes}m|{name}"
                        ] = value
            for source_key, source_events in support.items():
                for window_minutes in windows:
                    quote_features = _build_support_quote_features(
                        source_events,
                        support_times[source_key],
                        as_of_utc=cutoff,
                        window=timedelta(minutes=window_minutes),
                    )
                    for name, value in quote_features.items():
                        numeric = _finite(value)
                        if numeric is not None:
                            row_features[
                                f"SUPPORT:{source_key}|{window_minutes}m|{name}"
                            ] = numeric
            # A directional cross-market spread is meaningful only between
            # same-unit melted markets.  Coin-reference feeds use another unit
            # and never receive this feature.
            if target_key in latest_features:
                target_price_features = latest_features[target_key]
                for source_key, source_features in latest_features.items():
                    if source_key == target_key:
                        continue
                    spread = cross_market_spread_bps(
                        source_features, target_price_features
                    )
                    if spread is not None:
                        row_features[
                            f"{source_key}|{max(windows)}m|spread_vs_{target_key}_bps"
                        ] = spread
            for name, value in _calendar_numeric(cutoff).items():
                row_features[f"CALENDAR|{name}"] = value
            for feature_name, feature_value in row_features.items():
                candidate = stats_by_candidate.setdefault(
                    (feature_name, target_key), CandidateStats()
                )
                candidate.partition(partition).add(feature_value, target_return)
            if write_dataset is not None:
                record = {
                    "schema_version": DISCOVERY_VERSION,
                    "feature_version": FEATURE_VERSION,
                    "available_at_utc": _iso_utc(cutoff),
                    "realized_at_utc": _iso_utc(realized_at),
                    "target_market": target_key,
                    "target_anchor_price": round(target_anchor, 6),
                    "target_return_bps": round(target_return, 8),
                    "features": {
                        name: round(value, 8) for name, value in sorted(row_features.items())
                    },
                }
                write_dataset.write(json.dumps(record, ensure_ascii=False) + "\n")
            target_sample_count[target_key] = target_sample_count.get(target_key, 0) + 1
            generated_rows += 1
    ranked = []
    for (feature_name, target_key), candidate in stats_by_candidate.items():
        test = candidate.test.as_dict()
        if int(test["sample_count"]) < min_samples:
            continue
        ranked.append(
            {
                "feature_name": feature_name,
                "target_market": target_key,
                "fit": candidate.fit.as_dict(),
                "validation": candidate.validation.as_dict(),
                "test": test,
                "stable_sign_across_splits": _stable_candidate(
                    candidate,
                    min_samples=min_samples,
                    min_absolute_correlation=min_absolute_correlation,
                    min_directional_agreement=min_directional_agreement,
                ),
            }
        )
    ranked.sort(
        key=lambda row: (
            not bool(row["stable_sign_across_splits"]),
            -abs(float(row["test"]["pearson_correlation"] or 0.0)),
            -int(row["test"]["sample_count"]),
        )
    )
    return {
        "version": DISCOVERY_VERSION,
        "status": "SHADOW_RESEARCH_NOT_PROMOTED",
        "feature_version": FEATURE_VERSION,
        "leakage_controls": {
            "feature_cutoff": "all source events are at_or_before available_at_utc",
            "target": "realized price event is strictly after available_at_utc",
            "split": "chronological 60/20/20 by availability time",
            "automatic_promotion": False,
        },
        "research_contract": {
            "conditional_melted_events": "excluded unless explicitly requested",
            "coin_reference": "generic external target only; never a product-specific project coin quote",
            "data_output": "aggregate numeric features only; no post text, ids, or participant data",
            "support_sources": "source-specific return/count/staleness features; a missing feed is absent, never substituted",
        },
        "parameters": {
            "feature_windows_minutes": list(windows),
            "target_horizon_minutes": horizon_minutes,
            "sample_step_minutes": sample_step_minutes,
            "max_target_age_minutes": max_target_age_minutes,
            "minimum_samples_per_split": min_samples,
            "minimum_absolute_correlation_per_split": min_absolute_correlation,
            "minimum_directional_agreement_per_split": min_directional_agreement,
        },
        "split_boundaries": {
            "validation_start_utc": _iso_utc(validation_start),
            "test_start_utc": _iso_utc(test_start),
        },
        "melted_market_event_counts": {
            key: len(values) for key, values in sorted(melted.items())
        },
        "support_quote_event_counts": {
            key: len(values) for key, values in sorted(support.items())
        },
        "target_sample_counts": target_sample_count,
        "generated_feature_rows": generated_rows,
        "ranked_candidates": ranked,
        "promotion": {
            "automatic_promotion": False,
            "required_before_any_future_promotion": [
                "independent walk-forward comparison against the current structural estimator",
                "coverage and error review by market regime, Tehran time and Jalali calendar cohorts",
                "manual review of stable relationships and explicit versioned approval",
            ],
        },
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value_must_be_positive")
    return parsed


def _timestamp_argument(value: str) -> datetime:
    try:
        return _parse_utc(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp_must_be_iso8601_with_timezone") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--market-db", type=Path, required=True)
    parser.add_argument(
        "--coin-trade-target-db",
        type=Path,
        help="Normalized confirmed project coin-trade dataset; never raw group text.",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--coin-intrinsic-dataset",
        type=Path,
        help="Aggregate product-specific coin-bubble labels for a later challenger.",
    )
    parser.add_argument("--include-conditional", action="store_true")
    parser.add_argument("--without-coin-reference", action="store_true")
    parser.add_argument("--since", type=_timestamp_argument)
    parser.add_argument("--until", type=_timestamp_argument)
    parser.add_argument("--horizon-minutes", type=_positive_int, default=DEFAULT_HORIZON_MINUTES)
    parser.add_argument("--sample-step-minutes", type=_positive_int, default=DEFAULT_SAMPLE_STEP_MINUTES)
    parser.add_argument("--max-target-age-minutes", type=_positive_int, default=DEFAULT_MAX_TARGET_AGE_MINUTES)
    parser.add_argument("--minimum-samples", type=_positive_int, default=MINIMUM_SAMPLES)
    parser.add_argument(
        "--minimum-absolute-correlation",
        type=float,
        default=MINIMUM_ABSOLUTE_CORRELATION,
    )
    parser.add_argument(
        "--minimum-directional-agreement",
        type=float,
        default=MINIMUM_DIRECTIONAL_AGREEMENT,
    )
    parser.add_argument(
        "--windows-minutes",
        type=_positive_int,
        nargs="+",
        default=list(DEFAULT_WINDOWS),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    if args.until is not None and args.since is not None and args.until <= args.since:
        raise SystemExit("--until must be after --since")
    if not 0.0 <= args.minimum_absolute_correlation <= 1.0:
        raise SystemExit("--minimum-absolute-correlation must be in [0, 1]")
    if not 0.0 <= args.minimum_directional_agreement <= 1.0:
        raise SystemExit("--minimum-directional-agreement must be in [0, 1]")
    report_path = _outside_repository(args.report)
    dataset_path = _outside_repository(args.dataset) if args.dataset else None
    coin_intrinsic_dataset_path = (
        _outside_repository(args.coin_intrinsic_dataset)
        if args.coin_intrinsic_dataset
        else None
    )
    if dataset_path is not None and dataset_path == report_path:
        raise SystemExit("--dataset and --report must be distinct")
    if (
        coin_intrinsic_dataset_path is not None
        and coin_intrinsic_dataset_path in {report_path, dataset_path}
    ):
        raise SystemExit("--coin-intrinsic-dataset must be distinct from other outputs")
    if coin_intrinsic_dataset_path is not None and args.coin_trade_target_db is None:
        raise SystemExit("--coin-intrinsic-dataset requires --coin-trade-target-db")
    melted, support, targets, discarded = load_market_data(
        args.market_db,
        include_coin_reference=not args.without_coin_reference,
        include_conditional=args.include_conditional,
        since_utc=args.since,
        until_utc=args.until,
    )
    coin_trade_input: dict[str, object] = {"enabled": False}
    coin_trades: list[ConfirmedCoinTrade] = []
    if args.coin_trade_target_db is not None:
        coin_targets, coin_trades, coin_trade_input = load_confirmed_coin_trade_targets(
            args.coin_trade_target_db,
            since_utc=args.since,
            until_utc=args.until,
        )
        for key, values in coin_targets.items():
            targets.setdefault(key, []).extend(values)
    dataset_handle = None
    dataset_temp = None
    coin_intrinsic_handle = None
    coin_intrinsic_temp = None
    try:
        if dataset_path is not None:
            dataset_handle, dataset_temp = _open_jsonl(dataset_path)
        if coin_intrinsic_dataset_path is not None:
            coin_intrinsic_handle, coin_intrinsic_temp = _open_jsonl(
                coin_intrinsic_dataset_path
            )
        report = discover(
            melted,
            support,
            targets,
            windows=tuple(sorted(set(args.windows_minutes))),
            horizon_minutes=args.horizon_minutes,
            sample_step_minutes=args.sample_step_minutes,
            max_target_age_minutes=args.max_target_age_minutes,
            min_samples=args.minimum_samples,
            min_absolute_correlation=args.minimum_absolute_correlation,
            min_directional_agreement=args.minimum_directional_agreement,
            write_dataset=dataset_handle,
        )
        if coin_intrinsic_dataset_path is not None:
            report["coin_intrinsic_labels"] = build_coin_intrinsic_dataset(
                melted,
                support,
                coin_trades,
                windows=tuple(sorted(set(args.windows_minutes))),
                max_anchor_age_minutes=args.max_target_age_minutes,
                write_dataset=coin_intrinsic_handle,
            )
        report["input"] = {
            "market_database": str(args.market_db.expanduser().resolve()),
            "since_utc": _iso_utc(args.since) if args.since else None,
            "until_utc": _iso_utc(args.until) if args.until else None,
            "discarded_rows": discarded,
            "confirmed_coin_trade_targets": coin_trade_input,
        }
        _write_json_atomic(report_path, report)
        if dataset_handle is not None and dataset_temp is not None:
            dataset_handle.close()
            dataset_temp.replace(dataset_path)
        if coin_intrinsic_handle is not None and coin_intrinsic_temp is not None:
            coin_intrinsic_handle.close()
            coin_intrinsic_temp.replace(coin_intrinsic_dataset_path)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "feature_rows": report["generated_feature_rows"],
                    "ranked_candidates": len(report["ranked_candidates"]),
                    "report": str(report_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if dataset_handle is not None and not dataset_handle.closed:
            dataset_handle.close()
        if dataset_temp is not None and dataset_temp.exists():
            dataset_temp.unlink()
        if coin_intrinsic_handle is not None and not coin_intrinsic_handle.closed:
            coin_intrinsic_handle.close()
        if coin_intrinsic_temp is not None and coin_intrinsic_temp.exists():
            coin_intrinsic_temp.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
