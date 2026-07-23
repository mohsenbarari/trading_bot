"""Deterministic underlying-only market-regime detection for Shadow inference.

Coin offers are deliberately excluded. The detector consumes only melted gold,
Herat USD, USDT, ounce, and standardized IME observations that were available
at or before the requested cutoff.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


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
    if not _table_exists(connection, "external_market_observations"):
        return []
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
        settlement_terms=(
            ("TOMORROW",)
            if tomorrow
            else ("TODAY", "UNKNOWN")
        ),
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
        settlement_terms=(
            ("TOMORROW",)
            if tomorrow
            else ("TODAY", "UNKNOWN")
        ),
        trade_forms=("PAPER",) if tomorrow else ("PHYSICAL",),
    )
    if not usd_rows and not tomorrow:
        usd_rows = _price_event_rows(
            connection,
            start=start,
            end=end,
            instrument="USD_HERAT",
            settlement_terms=("TODAY", "UNKNOWN"),
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
