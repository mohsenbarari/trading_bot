#!/usr/bin/env python3
"""Annotate offer/trade quality and classify the short-term market regime.

The text extractor remains an immutable description of what participants
wrote.  This module adds a separate, auditable market-quality layer:

* active offers are live range observations for at most five minutes;
* expired offers never remain executable book boundaries;
* timed-out offers remain lower-quality historical bubble labels;
* offers outside the entire opposite-side book in a normal/range market, and
  trades linked to them, are excluded from both inference and training;
* a crossed quote may survive only when independent melted/FX inputs confirm
  a matching directional regime.

No LLM is used here.  Price/order-book invariants must be deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


OFFER_LIVE_SECONDS = 5 * 60
OFFER_ACTIVE_LIVE_WEIGHT = 1.0
OFFER_EARLY_EXPIRED_WEIGHT = 0.5
OFFER_TIMEOUT_TRAINING_WEIGHT = 1.0 / 3.0
OFFER_POST_TTL_HALF_LIFE_SECONDS = 5 * 60
CONFIRMED_TRADE_TRAINING_WEIGHT = 1.5
PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT = 2.0
NORMAL_MARKET_OUTLIER_RULE = (
    "SELL_BELOW_LOWEST_ACTIVE_BUY_OR_BUY_ABOVE_HIGHEST_ACTIVE_SELL"
)
OPPOSITE_BOOK_REFERENCE = "OUTER_EXTREME_OF_ACTIVE_OPPOSITE_BOOK"
AMBIGUOUS_PRICE_METHODS = {
    "contextual_tail",
    "contextual_tail_unrounded",
    "reply_contextual_tail",
}
AMBIGUOUS_PRICE_REASON = "AMBIGUOUS_CONTEXTUAL_TAIL_WITHOUT_CURRENT_MARKET_RANGE"
EXTREME_PRICE_REASON = "EXTREME_STRICTLY_PRIOR_LOCAL_PRICE_DISCONTINUITY"
EXTREME_LINKED_TRADE_REASON = (
    "TRADE_LINKED_TO_EXTREME_STRICTLY_PRIOR_LOCAL_PRICE_DISCONTINUITY"
)
EXTREME_LOOKBACK_SECONDS = 20 * 60
EXTREME_MIN_REFERENCES = 3
EXTREME_MAX_REFERENCES = 20
EXTREME_MIN_DEVIATION = 0.05
EXTREME_VOLATILITY_MULTIPLIER = 8.0

DEFAULT_CONVERSATION_DB = Path(
    os.environ.get("COIN_CONVERSATION_DB", "conversation_events.candidate.sqlite3")
)
DEFAULT_MARKET_DB = Path(os.environ.get("COIN_MARKET_DB", "market_prices.sqlite3"))


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def offer_lifecycle_weights(
    age_seconds: float, *, status: str = "ACTIVE"
) -> dict[str, float | bool | str]:
    """Return independent live and historical weights for one offer.

    ``online_learning_weight`` decays after minute five and is useful for a
    future incremental learner.  ``historical_training_weight`` intentionally
    stays at one third after timeout; otherwise a month of historical offers
    would disappear numerically.  Long-horizon recency decay is applied later
    by the estimator in days, not here in minutes.
    """

    age = max(0.0, float(age_seconds))
    normalized_status = str(status or "ACTIVE").upper()
    active = normalized_status == "ACTIVE"
    if age < OFFER_LIVE_SECONDS:
        if active:
            return {
                "phase": "ACTIVE_LIVE",
                "range_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "flow_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "online_learning_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "historical_training_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "book_eligible": True,
            }
        return {
            "phase": "EARLY_EXPIRED",
            "range_weight": 0.0,
            "flow_weight": OFFER_EARLY_EXPIRED_WEIGHT,
            "online_learning_weight": OFFER_EARLY_EXPIRED_WEIGHT,
            "historical_training_weight": OFFER_EARLY_EXPIRED_WEIGHT,
            "book_eligible": False,
        }

    elapsed = age - OFFER_LIVE_SECONDS
    decayed = OFFER_TIMEOUT_TRAINING_WEIGHT * 0.5 ** (
        elapsed / OFFER_POST_TTL_HALF_LIFE_SECONDS
    )
    return {
        "phase": "HISTORICAL_ONLY",
        "range_weight": 0.0,
        "flow_weight": 0.0,
        "online_learning_weight": decayed,
        "historical_training_weight": OFFER_TIMEOUT_TRAINING_WEIGHT,
        "book_eligible": False,
    }


def _component_from_rows(
    rows: Sequence[tuple[str, float]],
    *,
    name: str,
    reliability: float,
    direction_threshold_percent: float,
    end: datetime,
    maximum_staleness_seconds: int,
) -> dict[str, Any] | None:
    if len(rows) < 2:
        return None
    ordered = sorted((parse_time(stamp), float(price)) for stamp, price in rows)
    if (end - ordered[-1][0]).total_seconds() > maximum_staleness_seconds:
        return None
    distinct_times = {stamp for stamp, _ in ordered}
    if len(distinct_times) < 2:
        return None
    sample_size = min(20, max(1, len(ordered) // 5))
    early = statistics.median(price for _, price in ordered[:sample_size])
    late = statistics.median(price for _, price in ordered[-sample_size:])
    if early <= 0 or late <= 0:
        return None
    return_percent = (late / early - 1.0) * 100.0
    changes = [
        abs(current / previous - 1.0) * 100.0
        for (_, previous), (_, current) in zip(ordered, ordered[1:])
        if previous > 0
    ]
    volatility_percent = (
        statistics.median(changes) * math.sqrt(max(1, len(changes)))
        if changes
        else 0.0
    )
    strength = math.tanh(return_percent / direction_threshold_percent)
    return {
        "name": name,
        "sample_count": len(ordered),
        "first_observed_utc": iso_utc(ordered[0][0]),
        "last_observed_utc": iso_utc(ordered[-1][0]),
        "first_price": early,
        "last_price": late,
        "return_percent": return_percent,
        "volatility_percent": volatility_percent,
        "direction_strength": strength,
        "reliability": reliability,
    }


def _price_event_rows(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    instrument: str,
    settlement_terms: Iterable[str] | None = None,
    trade_forms: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    clauses = ["instrument=?", "event_time_utc>=?", "event_time_utc<=?"]
    parameters: list[Any] = [instrument, iso_utc(start), iso_utc(end)]
    for column, values in (
        ("settlement_term", tuple(settlement_terms or ())),
        ("trade_form", tuple(trade_forms or ())),
    ):
        if values:
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"{column} IN ({placeholders})")
            parameters.extend(values)
    return [
        (str(row[0]), float(row[1]))
        for row in connection.execute(
            f"""
            SELECT event_time_utc, price_num
            FROM price_events
            WHERE {' AND '.join(clauses)} AND price_num>0
            ORDER BY event_time_utc, id
            """,
            parameters,
        )
    ]


def _external_rows(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    instrument_code: str,
    quote_kinds: Sequence[str],
) -> list[tuple[str, float]]:
    placeholders = ",".join("?" for _ in quote_kinds)
    return [
        (str(row[0]), float(row[1]))
        for row in connection.execute(
            f"""
            SELECT observed_at_utc, normalized_price_num
            FROM external_market_observations
            WHERE instrument_code=?
              AND quote_kind IN ({placeholders})
              AND observed_at_utc>=? AND observed_at_utc<=?
              AND normalized_price_num>0
            ORDER BY observed_at_utc, id
            """,
            (instrument_code, *quote_kinds, iso_utc(start), iso_utc(end)),
        )
    ]


def detect_market_regime(
    connection: sqlite3.Connection,
    end: datetime,
    settlement: str,
    *,
    window_seconds: int = 10 * 60,
) -> dict[str, Any]:
    """Classify RANGE/UP/DOWN/SHOCK from independent underlying inputs.

    Coin offers are deliberately absent.  Melted gold and FX dominate.  When
    only USDT and IME are present, their nominal weights are 0.75 and 0.25, so
    USDT remains the stronger direction reference as requested.
    """

    end = end.astimezone(timezone.utc)
    start = end - timedelta(seconds=window_seconds)
    tomorrow = str(settlement).upper() == "TOMORROW"
    components: list[dict[str, Any]] = []

    melted_primary = _price_event_rows(
        connection,
        start=start,
        end=end,
        instrument="MELTED_GOLD",
        settlement_terms=("TOMORROW", "UNKNOWN") if tomorrow else ("TODAY",),
        trade_forms=("PAPER",) if tomorrow else ("PHYSICAL",),
    )
    if not melted_primary:
        melted_primary = _price_event_rows(
            connection,
            start=start,
            end=end,
            instrument="MELTED_GOLD",
            trade_forms=("PAPER",),
        )
        melted_reliability = 0.70
    else:
        melted_reliability = 1.00
    melted = _component_from_rows(
        melted_primary,
        name="MELTED_GOLD",
        reliability=melted_reliability,
        direction_threshold_percent=0.18,
        end=end,
        maximum_staleness_seconds=120,
    )
    if melted:
        components.append(melted)

    usd_rows = _price_event_rows(
        connection,
        start=start,
        end=end,
        instrument="USD_HERAT",
        settlement_terms=("TOMORROW",) if tomorrow else ("TODAY",),
        trade_forms=("PAPER",) if tomorrow else ("PHYSICAL",),
    )
    if not usd_rows and not tomorrow:
        usd_rows = _price_event_rows(
            connection,
            start=start,
            end=end,
            instrument="USD_HERAT",
            settlement_terms=("TODAY",),
            trade_forms=("PAPER",),
        )
        usd_reliability = 0.65
    else:
        usd_reliability = 0.85
    usd = _component_from_rows(
        usd_rows,
        name="USD_HERAT",
        reliability=usd_reliability,
        direction_threshold_percent=0.12,
        end=end,
        maximum_staleness_seconds=180,
    )
    if usd:
        components.append(usd)

    usdt = _component_from_rows(
        _external_rows(
            connection,
            start=start,
            end=end,
            instrument_code="USDT_IRT",
            quote_kinds=("MID", "CLOSE"),
        ),
        name="USDT_IRT",
        reliability=0.75,
        direction_threshold_percent=0.12,
        end=end,
        maximum_staleness_seconds=180,
    )
    if usdt:
        components.append(usdt)

    xau = _component_from_rows(
        _price_event_rows(
            connection,
            start=start,
            end=end,
            instrument="XAUUSD",
        ),
        name="XAUUSD",
        reliability=0.35,
        direction_threshold_percent=0.08,
        end=end,
        maximum_staleness_seconds=180,
    )
    if xau:
        components.append(xau)

    ime = _component_from_rows(
        _external_rows(
            connection,
            start=start,
            end=end,
            instrument_code="IME_GOLD_BAR",
            quote_kinds=("LAST", "CLOSE"),
        ),
        name="IME_GOLD_BAR",
        reliability=0.25,
        direction_threshold_percent=0.15,
        end=end,
        maximum_staleness_seconds=15 * 60,
    )
    if ime:
        components.append(ime)

    if not components:
        return {
            "status": "NO_DATA",
            "regime": "UNKNOWN",
            "direction_score": None,
            "confidence": 0.0,
            "volatility_percent": None,
            "window_seconds": window_seconds,
            "components": [],
            "usdt_preferred_over_ime": True,
        }

    total_weight = sum(float(row["reliability"]) for row in components)
    score = sum(
        float(row["reliability"]) * float(row["direction_strength"])
        for row in components
    ) / total_weight
    volatility = sum(
        float(row["reliability"]) * float(row["volatility_percent"])
        for row in components
    ) / total_weight
    strong_positive = [
        row for row in components if float(row["direction_strength"]) >= 0.35
    ]
    strong_negative = [
        row for row in components if float(row["direction_strength"]) <= -0.35
    ]
    core = {"MELTED_GOLD", "USD_HERAT", "USDT_IRT"}
    positive_core = sum(str(row["name"]) in core for row in strong_positive)
    negative_core = sum(str(row["name"]) in core for row in strong_negative)
    conflict = bool(strong_positive and strong_negative)

    if conflict and (positive_core or negative_core):
        regime = "SHOCK"
    elif score >= 0.35 and positive_core:
        regime = "UP"
    elif score <= -0.35 and negative_core:
        regime = "DOWN"
    elif volatility >= 0.18:
        regime = "SHOCK"
    else:
        regime = "RANGE"

    coverage_confidence = min(1.0, total_weight / 1.60)
    agreement = 1.0 - min(1.0, 2.0 * min(
        sum(float(row["reliability"]) for row in strong_positive),
        sum(float(row["reliability"]) for row in strong_negative),
    ) / max(total_weight, 1e-9))
    confidence = coverage_confidence * (agreement if regime != "RANGE" else 1.0)
    return {
        "status": "OBSERVED",
        "regime": regime,
        "direction_score": score,
        "confidence": max(0.0, min(1.0, confidence)),
        "volatility_percent": volatility,
        "window_seconds": window_seconds,
        "components": components,
        "component_weights_normalized": {
            str(row["name"]): float(row["reliability"]) / total_weight
            for row in components
        },
        "usdt_preferred_over_ime": True,
    }


def crossed_offer_decision(
    *,
    side: str,
    price: int,
    opposite_outer_boundary_price: int | None,
    regime: dict[str, Any],
) -> dict[str, Any]:
    """Reject only quotes beyond the entire active opposite-side book.

    The outer boundary for a sell is the lowest active buy; for a buy it is
    the highest active sell. Crossing only the best quote remains valid.
    """

    normalized_side = str(side).upper()
    crossed = bool(
        opposite_outer_boundary_price is not None
        and (
            (
                normalized_side == "SELL"
                and price < opposite_outer_boundary_price
            )
            or (
                normalized_side == "BUY"
                and price > opposite_outer_boundary_price
            )
        )
    )
    if not crossed:
        return {
            "cross_state": "NOT_CROSSED",
            "eligible": True,
            "exclusion_reason": None,
        }
    expected_regime = "DOWN" if normalized_side == "SELL" else "UP"
    observed_regime = str(regime.get("regime") or "UNKNOWN")
    confidence = float(regime.get("confidence") or 0.0)
    if observed_regime == expected_regime and confidence >= 0.55:
        return {
            "cross_state": "CROSSED_DIRECTIONALLY_CONFIRMED",
            "eligible": True,
            "exclusion_reason": None,
        }
    reason = (
        "CROSSED_OFFER_IN_NORMAL_MARKET"
        if observed_regime == "RANGE"
        else "CROSSED_OFFER_WITHOUT_MATCHING_UNDERLYING_REGIME"
    )
    return {
        "cross_state": "CROSSED_EXCLUDED",
        "eligible": False,
        "exclusion_reason": reason,
    }


def extreme_price_discontinuity(
    *,
    price: int,
    strictly_prior_prices: Sequence[int],
    regime_volatility_percent: float | None,
) -> dict[str, Any]:
    """Reject only extreme local jumps; ordinary directional moves survive.

    This is a unit/typo safety gate, not a price correction.  It requires at
    least three prior same-market observations and widens with independently
    observed underlying volatility.
    """

    references = [
        int(value)
        for value in strictly_prior_prices[-EXTREME_MAX_REFERENCES:]
        if int(value) > 0
    ]
    if len(references) < EXTREME_MIN_REFERENCES:
        return {
            "extreme": False,
            "reference_count": len(references),
            "reference_median": None,
            "relative_deviation": None,
            "threshold": None,
        }
    reference = float(statistics.median(references))
    relative_deviation = abs(int(price) - reference) / reference
    threshold = max(
        EXTREME_MIN_DEVIATION,
        EXTREME_VOLATILITY_MULTIPLIER
        * float(regime_volatility_percent or 0.0)
        / 100.0,
    )
    return {
        "extreme": relative_deviation > threshold,
        "reference_count": len(references),
        "reference_median": reference,
        "relative_deviation": relative_deviation,
        "threshold": threshold,
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def annotate_database(conversation_db: Path, market_db: Path) -> dict[str, Any]:
    """Populate derived quality tables without mutating extracted facts."""

    conversation = sqlite3.connect(conversation_db)
    conversation.row_factory = sqlite3.Row
    market = sqlite3.connect(f"file:{market_db.resolve()}?mode=ro", uri=True)
    try:
        conversation.executescript(
            """
            CREATE TABLE IF NOT EXISTS offer_market_quality (
                offer_id INTEGER PRIMARY KEY,
                event_time_utc TEXT NOT NULL,
                lifecycle_phase TEXT NOT NULL,
                live_range_weight REAL NOT NULL,
                live_flow_weight REAL NOT NULL,
                historical_training_weight REAL NOT NULL,
                realtime_eligible INTEGER NOT NULL,
                training_eligible INTEGER NOT NULL,
                cross_state TEXT NOT NULL,
                crossing_reference_price INTEGER,
                market_regime TEXT NOT NULL,
                regime_score REAL,
                regime_confidence REAL NOT NULL,
                regime_volatility_percent REAL,
                exclusion_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS trade_market_quality (
                trade_id INTEGER PRIMARY KEY,
                linked_offer_id INTEGER,
                training_eligible INTEGER NOT NULL,
                realtime_eligible INTEGER NOT NULL,
                training_weight REAL NOT NULL,
                market_regime TEXT NOT NULL,
                regime_score REAL,
                regime_confidence REAL NOT NULL,
                cross_state TEXT NOT NULL,
                exclusion_reason TEXT
            );
            DELETE FROM offer_market_quality;
            DELETE FROM trade_market_quality;
            """
        )
        offer_columns = _table_columns(conversation, "offers")
        offer_price_method = (
            "o.price_method" if "price_method" in offer_columns else "NULL"
        )
        offers = [
            dict(row)
            for row in conversation.execute(
                f"""
                SELECT o.id, o.import_id, o.message_id, o.offer_index,
                       o.commodity, o.price, o.quantity, o.side, o.settlement,
                       o.trade_form, o.confidence,
                       {offer_price_method} AS price_method,
                       m.event_time_utc,
                       m.sender_hash
                FROM offers AS o
                JOIN messages AS m
                  ON m.import_id=o.import_id AND m.message_id=o.message_id
                ORDER BY m.event_time_utc, o.id
                """
            )
        ]
        regime_cache: dict[tuple[str, str], dict[str, Any]] = {}

        def regime_at(value: str, settlement: str) -> dict[str, Any]:
            event = parse_time(value)
            minute = event.replace(second=0, microsecond=0)
            key = (iso_utc(minute), settlement)
            if key not in regime_cache:
                regime_cache[key] = detect_market_regime(market, event, settlement)
            return regime_cache[key]

        active: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        recent_references: dict[
            tuple[str, str, str], list[dict[str, Any]]
        ] = defaultdict(list)
        quality_by_offer: dict[int, dict[str, Any]] = {}
        offer_lookup: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for row in offers:
            event = parse_time(str(row["event_time_utc"]))
            key = (
                str(row["commodity"]),
                str(row["settlement"]),
                str(row["trade_form"]),
            )
            active[key] = [
                item
                for item in active[key]
                if 0
                <= (event - parse_time(str(item["event_time_utc"]))).total_seconds()
                < OFFER_LIVE_SECONDS
            ]
            sender = row.get("sender_hash")
            if sender:
                active[key] = [
                    item
                    for item in active[key]
                    if not (
                        item.get("sender_hash") == sender
                        and str(item["side"]) == str(row["side"])
                    )
                ]
            opposite = [
                item
                for item in active[key]
                if str(item["side"]) != str(row["side"])
                and str(item["side"]) in {"BUY", "SELL"}
            ]
            if str(row["side"]) == "SELL":
                opposite_outer_boundary = min(
                    (int(item["price"]) for item in opposite if item["side"] == "BUY"),
                    default=None,
                )
            elif str(row["side"]) == "BUY":
                opposite_outer_boundary = max(
                    (int(item["price"]) for item in opposite if item["side"] == "SELL"),
                    default=None,
                )
            else:
                opposite_outer_boundary = None
            regime = regime_at(str(row["event_time_utc"]), str(row["settlement"]))
            recent_references[key] = [
                item
                for item in recent_references[key]
                if 0
                <= (
                    event - parse_time(str(item["event_time_utc"]))
                ).total_seconds()
                <= EXTREME_LOOKBACK_SECONDS
            ]
            discontinuity = extreme_price_discontinuity(
                price=int(row["price"]),
                strictly_prior_prices=[
                    int(item["price"]) for item in recent_references[key]
                ],
                regime_volatility_percent=regime.get("volatility_percent"),
            )
            decision = crossed_offer_decision(
                side=str(row["side"]),
                price=int(row["price"]),
                opposite_outer_boundary_price=opposite_outer_boundary,
                regime=regime,
            )
            ambiguous_price = (
                str(row.get("price_method") or "") in AMBIGUOUS_PRICE_METHODS
            )
            extreme_price = bool(discontinuity["extreme"])
            eligible = (
                bool(decision["eligible"])
                and not ambiguous_price
                and not extreme_price
            )
            quality = {
                "offer_id": int(row["id"]),
                "event_time_utc": str(row["event_time_utc"]),
                "lifecycle_phase": "HISTORICAL_ONLY",
                "live_range_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "live_flow_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "historical_training_weight": OFFER_TIMEOUT_TRAINING_WEIGHT,
                "realtime_eligible": int(eligible),
                "training_eligible": int(eligible),
                "cross_state": str(decision["cross_state"]),
                "crossing_reference_price": opposite_outer_boundary,
                "market_regime": str(regime["regime"]),
                "regime_score": regime.get("direction_score"),
                "regime_confidence": float(regime.get("confidence") or 0.0),
                "regime_volatility_percent": regime.get("volatility_percent"),
                "exclusion_reason": (
                    AMBIGUOUS_PRICE_REASON
                    if ambiguous_price
                    else (
                        EXTREME_PRICE_REASON
                        if extreme_price
                        else decision["exclusion_reason"]
                    )
                ),
            }
            quality_by_offer[int(row["id"])] = quality
            offer_lookup[(int(row["import_id"]), int(row["message_id"]))].append(row)
            if eligible:
                if decision["cross_state"] == "CROSSED_DIRECTIONALLY_CONFIRMED":
                    if str(row["side"]) == "SELL":
                        active[key] = [
                            item
                            for item in active[key]
                            if not (item["side"] == "BUY" and int(item["price"]) > int(row["price"]))
                        ]
                    elif str(row["side"]) == "BUY":
                        active[key] = [
                            item
                            for item in active[key]
                            if not (item["side"] == "SELL" and int(item["price"]) < int(row["price"]))
                        ]
                active[key].append(row)
                recent_references[key].append(row)

        trade_columns = _table_columns(conversation, "confirmed_trades")
        base_eligibility = (
            "t.training_eligible" if "training_eligible" in trade_columns else "1"
        )
        trades = [
            dict(row)
            for row in conversation.execute(
                f"""
                SELECT t.*, {base_eligibility} AS base_training_eligible
                FROM confirmed_trades AS t
                ORDER BY t.event_time_utc, t.id
                """
            )
        ]
        trade_quality: list[dict[str, Any]] = []
        completed_offer_ids: set[int] = set()
        for trade in trades:
            candidates = offer_lookup.get(
                (int(trade["import_id"]), int(trade["offer_message_id"])), []
            ) if trade.get("offer_message_id") is not None else []
            candidates = [
                row
                for row in candidates
                if str(row["commodity"]) == str(trade["commodity"])
                and str(row["settlement"]) == str(trade["settlement"])
                and str(row["trade_form"]) == str(trade["trade_form"])
            ]
            linked = (
                min(candidates, key=lambda row: abs(int(row["price"]) - int(trade["price"])))
                if candidates
                else None
            )
            linked_quality = (
                quality_by_offer.get(int(linked["id"])) if linked is not None else None
            )
            if linked is not None:
                completed_offer_ids.add(int(linked["id"]))
            excluded_cross = bool(
                linked_quality
                and linked_quality["cross_state"] == "CROSSED_EXCLUDED"
            )
            ambiguous_price = (
                str(trade.get("price_method") or "") in AMBIGUOUS_PRICE_METHODS
                or bool(
                    linked_quality
                    and linked_quality["exclusion_reason"] == AMBIGUOUS_PRICE_REASON
                )
            )
            extreme_price = bool(
                linked_quality
                and linked_quality["exclusion_reason"] == EXTREME_PRICE_REASON
            )
            eligible = (
                bool(trade["base_training_eligible"])
                and not excluded_cross
                and not ambiguous_price
                and not extreme_price
            )
            regime = regime_at(str(trade["event_time_utc"]), str(trade["settlement"]))
            trade_quality.append(
                {
                    "trade_id": int(trade["id"]),
                    "linked_offer_id": int(linked["id"]) if linked is not None else None,
                    "training_eligible": int(eligible),
                    "realtime_eligible": int(
                        not excluded_cross
                        and not ambiguous_price
                        and not extreme_price
                    ),
                    "training_weight": (
                        CONFIRMED_TRADE_TRAINING_WEIGHT if eligible else 0.0
                    ),
                    "market_regime": str(regime["regime"]),
                    "regime_score": regime.get("direction_score"),
                    "regime_confidence": float(regime.get("confidence") or 0.0),
                    "cross_state": (
                        str(linked_quality["cross_state"])
                        if linked_quality
                        else "UNLINKED_TRADE"
                    ),
                    "exclusion_reason": (
                        "TRADE_LINKED_TO_CROSSED_OFFER_IN_NORMAL_MARKET"
                        if excluded_cross
                        else (
                            AMBIGUOUS_PRICE_REASON
                            if ambiguous_price
                            else (
                                EXTREME_LINKED_TRADE_REASON
                                if extreme_price
                                else (
                                    "BASE_TRAINING_INELIGIBLE"
                                    if not bool(trade["base_training_eligible"])
                                    else None
                                )
                            )
                        )
                    ),
                }
            )

        for offer_id in completed_offer_ids:
            quality = quality_by_offer[offer_id]
            if quality["training_eligible"]:
                quality["training_eligible"] = 0
                quality["exclusion_reason"] = "SUPERSEDED_BY_CONFIRMED_TRADE"

        conversation.executemany(
            """
            INSERT INTO offer_market_quality(
                offer_id, event_time_utc, lifecycle_phase, live_range_weight,
                live_flow_weight, historical_training_weight,
                realtime_eligible, training_eligible, cross_state,
                crossing_reference_price, market_regime, regime_score,
                regime_confidence, regime_volatility_percent, exclusion_reason
            ) VALUES (
                :offer_id, :event_time_utc, :lifecycle_phase, :live_range_weight,
                :live_flow_weight, :historical_training_weight,
                :realtime_eligible, :training_eligible, :cross_state,
                :crossing_reference_price, :market_regime, :regime_score,
                :regime_confidence, :regime_volatility_percent, :exclusion_reason
            )
            """,
            list(quality_by_offer.values()),
        )
        conversation.executemany(
            """
            INSERT INTO trade_market_quality(
                trade_id, linked_offer_id, training_eligible,
                realtime_eligible, training_weight, market_regime,
                regime_score, regime_confidence, cross_state, exclusion_reason
            ) VALUES (
                :trade_id, :linked_offer_id, :training_eligible,
                :realtime_eligible, :training_weight, :market_regime,
                :regime_score, :regime_confidence, :cross_state, :exclusion_reason
            )
            """,
            trade_quality,
        )
        conversation.commit()
        summary = {
            "schema_version": 1,
            "conversation_database": str(conversation_db.resolve()),
            "market_database": str(market_db.resolve()),
            "offer_live_seconds": OFFER_LIVE_SECONDS,
            "offers_total": len(offers),
            "offers_crossed_excluded": sum(
                row["cross_state"] == "CROSSED_EXCLUDED"
                for row in quality_by_offer.values()
            ),
            "offers_crossed_directionally_confirmed": sum(
                row["cross_state"] == "CROSSED_DIRECTIONALLY_CONFIRMED"
                for row in quality_by_offer.values()
            ),
            "offers_ambiguous_price_excluded": sum(
                row["exclusion_reason"] == AMBIGUOUS_PRICE_REASON
                for row in quality_by_offer.values()
            ),
            "offers_extreme_price_excluded": sum(
                row["exclusion_reason"] == EXTREME_PRICE_REASON
                for row in quality_by_offer.values()
            ),
            "offers_superseded_by_trade": len(completed_offer_ids),
            "offers_training_eligible": sum(
                bool(row["training_eligible"]) for row in quality_by_offer.values()
            ),
            "trades_total": len(trades),
            "trades_training_eligible": sum(
                bool(row["training_eligible"]) for row in trade_quality
            ),
            "trades_crossed_excluded": sum(
                row["cross_state"] == "CROSSED_EXCLUDED" for row in trade_quality
            ),
            "trades_ambiguous_price_excluded": sum(
                row["exclusion_reason"] == AMBIGUOUS_PRICE_REASON
                for row in trade_quality
            ),
            "trades_extreme_price_excluded": sum(
                row["exclusion_reason"] == EXTREME_LINKED_TRADE_REASON
                for row in trade_quality
            ),
            "regime_cache_entries": len(regime_cache),
            "policy": {
                "active_offer_live_weight": OFFER_ACTIVE_LIVE_WEIGHT,
                "expired_offer_weight": OFFER_EARLY_EXPIRED_WEIGHT,
                "timeout_training_weight": OFFER_TIMEOUT_TRAINING_WEIGHT,
                "confirmed_trade_training_weight": CONFIRMED_TRADE_TRAINING_WEIGHT,
                "crossed_normal_offer_and_linked_trade": "EXCLUDED",
                "normal_market_outlier_rule": NORMAL_MARKET_OUTLIER_RULE,
                "opposite_book_reference": OPPOSITE_BOOK_REFERENCE,
                "contextual_tail_without_current_market_range": "EXCLUDED",
                "extreme_strictly_prior_local_price_discontinuity": "EXCLUDED",
                "regime_inputs_exclude_coin_offers": True,
                "usdt_weight_exceeds_ime_weight": True,
            },
        }
        return summary
    finally:
        market.close()
        conversation.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    args = parser.parse_args()
    result = annotate_database(args.conversation_db, args.market_db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
