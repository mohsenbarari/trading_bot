"""Canonical, source-prioritized market-regime classification.

The classifier reads only normalized, eligible Market Store facts.  Private
melted-gold observations are the anchor; Herat USD is the primary leading
confirmation, while XAUUSD and the live coin books are supporting signals.
Direction and volatility are kept as separate axes because a market may be
both directional and volatile at the same time.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import sqlite3
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .market_contracts import normalize_utc


CANONICAL_MARKET_REGIME_METHOD = "private-melted-led-market-regime-v2"
CANONICAL_MARKET_REGIME_STATES = ("RANGE", "UP", "DOWN", "SHOCK", "UNKNOWN")

_COIN_INSTRUMENTS = (
    "COIN_IMAM",
    "COIN_BAHAR",
    "COIN_HALF_BAHAR",
    "COIN_QUARTER_BAHAR",
    "COIN_HALF_LOW_DATE",
    "COIN_QUARTER_LOW_DATE",
    "COIN_ONE_GRAM",
)


def _utc(value: datetime | str) -> datetime:
    serialized = normalize_utc(value, field_name="market_regime_time_utc")
    return datetime.fromisoformat(serialized.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _weighted_median(values: Iterable[tuple[float, int]]) -> float:
    ordered = sorted((float(value), max(1, int(weight))) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    threshold = (total + 1) / 2
    cumulative = 0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _event_weight(event_type: str) -> int:
    return 3 if str(event_type).upper() == "TRADE" else 1


def _read_rows(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    instruments: Sequence[str],
    settlements: Sequence[str] = (),
    trade_forms: Sequence[str] = (),
    event_types: Sequence[str] = (),
    source_codes: Sequence[str] = (),
) -> list[dict[str, Any]]:
    clauses = [
        f"instrument IN ({','.join('?' for _ in instruments)})",
        "quality_state = 'ELIGIBLE'",
        "is_conditional = 0",
        "event_time_utc >= ?",
        "event_time_utc <= ?",
        "available_at_utc <= ?",
        "(CASE WHEN instr(inserted_at_utc, '.')=0 "
        "THEN replace(inserted_at_utc, 'Z', '.000000Z') "
        "ELSE inserted_at_utc END) <= ?",
        "price_num > 0",
    ]
    parameters: list[Any] = [
        *instruments,
        _iso(start),
        _iso(end),
        _iso(end),
        _iso(end).replace("Z", ".000000Z"),
    ]
    for column, values in (
        ("settlement_term", settlements),
        ("trade_form", trade_forms),
        ("event_type", event_types),
        ("source_code", source_codes),
    ):
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
    tables = ["market_observations"]
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_observations_archive'"
    ).fetchone():
        tables.append("market_observations_archive")
    rows: list[tuple[Any, ...]] = []
    for table in tables:
        rows.extend(
            connection.execute(
                f"""
                SELECT instrument, source_code, event_time_utc, event_type, price_num
                FROM {table}
                WHERE {' AND '.join(clauses)}
                ORDER BY event_time_utc, id
                """,
                parameters,
            ).fetchall()
        )
    rows.sort(key=lambda row: str(row[2]))
    columns = ("instrument", "source_code", "event_time_utc", "event_type", "price_num")
    return [dict(zip(columns, row)) for row in rows]


def _minute_centers(rows: Sequence[Mapping[str, Any]]) -> list[tuple[datetime, float]]:
    buckets: dict[datetime, list[tuple[float, int]]] = defaultdict(list)
    for row in rows:
        observed = _utc(str(row["event_time_utc"])).replace(second=0, microsecond=0)
        buckets[observed].append(
            (float(row["price_num"]), _event_weight(str(row["event_type"])))
        )
    return [
        (stamp, _weighted_median(values))
        for stamp, values in sorted(buckets.items())
        if values
    ]


def _component(
    rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    base_weight: float,
    direction_threshold_percent: float,
    volatility_threshold_percent: float,
    target_minutes: int,
    minimum_minutes: int,
    maximum_staleness_seconds: int,
    end: datetime,
    source_role: str,
) -> dict[str, Any] | None:
    centers = _minute_centers(rows)
    if len(centers) < minimum_minutes:
        return None
    latest_age = max(0.0, (end - centers[-1][0]).total_seconds())
    if latest_age > maximum_staleness_seconds:
        return None
    edge = min(3, max(1, len(centers) // 4))
    early = statistics.median(value for _, value in centers[:edge])
    late = statistics.median(value for _, value in centers[-edge:])
    if early <= 0 or late <= 0:
        return None
    return_percent = (late / early - 1.0) * 100.0
    signed_changes = [
        (current / previous - 1.0) * 100.0
        for (_, previous), (_, current) in zip(centers, centers[1:])
        if previous > 0
    ]
    absolute_path = sum(abs(value) for value in signed_changes)
    consistency = (
        min(1.0, abs(sum(signed_changes)) / absolute_path)
        if absolute_path > 1e-12
        else 1.0
    )
    volatility_percent = (
        statistics.median(abs(value) for value in signed_changes)
        * math.sqrt(max(1, len(signed_changes)))
        if signed_changes
        else 0.0
    )
    raw_strength = math.tanh(return_percent / direction_threshold_percent)
    direction_strength = raw_strength * (0.55 + 0.45 * consistency)
    sample_factor = min(1.0, len(centers) / max(1, target_minutes))
    freshness_factor = max(
        0.55,
        1.0 - 0.45 * latest_age / max(1, maximum_staleness_seconds),
    )
    effective_weight = base_weight * sample_factor * freshness_factor
    return {
        "name": name,
        "source_role": source_role,
        "minute_count": len(centers),
        "event_count": len(rows),
        "first_observed_utc": _iso(centers[0][0]),
        "last_observed_utc": _iso(centers[-1][0]),
        "latest_age_seconds": latest_age,
        "first_price": early,
        "last_price": late,
        "return_percent": return_percent,
        "volatility_percent": volatility_percent,
        "volatility_ratio": volatility_percent / volatility_threshold_percent,
        "direction_consistency": consistency,
        "direction_strength": direction_strength,
        "base_weight": base_weight,
        "effective_weight": effective_weight,
        "sample_factor": sample_factor,
        "freshness_factor": freshness_factor,
        "source_codes": sorted({str(row["source_code"]) for row in rows}),
    }


def _private_melted_component(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    settlement: str,
) -> dict[str, Any] | None:
    term = "TODAY" if settlement == "CASH" else "TOMORROW"
    policies = [
        (term, ("PAPER_NORMAL",), ("QUOTE",), ("PRIVATE_GOLD_PAPER_MINUTE",), 2.40, "PRIVATE_MINUTE"),
        (term, ("PAPER_NORMAL",), ("OFFER", "TRADE"), ("PRIVATE_GOLD_CHANNEL",), 2.20, "PRIVATE_RAW"),
        (term, ("PHYSICAL",), ("OFFER", "TRADE"), ("PRIVATE_GOLD_CHANNEL",), 1.90, "PRIVATE_PHYSICAL"),
    ]
    if settlement == "CASH":
        policies.extend(
            [
                ("TOMORROW", ("PAPER_NORMAL",), ("QUOTE",), ("PRIVATE_GOLD_PAPER_MINUTE",), 1.55, "PRIVATE_TOMORROW_CASH_BRIDGE"),
                ("TOMORROW", ("PAPER_NORMAL",), ("OFFER", "TRADE"), ("PRIVATE_GOLD_CHANNEL",), 1.40, "PRIVATE_RAW_TOMORROW_CASH_BRIDGE"),
            ]
        )
    for selected_term, forms, events, sources, weight, role in policies:
        rows = _read_rows(
            connection,
            start=start,
            end=end,
            instruments=("MELTED_GOLD_PRIVATE",),
            settlements=(selected_term,),
            trade_forms=forms,
            event_types=events,
            source_codes=sources,
        )
        component = _component(
            rows,
            name="PRIVATE_MELTED_GOLD",
            base_weight=weight,
            direction_threshold_percent=0.08,
            volatility_threshold_percent=0.14,
            target_minutes=6,
            minimum_minutes=3,
            maximum_staleness_seconds=180,
            end=end,
            source_role=role,
        )
        if component is not None:
            component["selected_settlement_term"] = selected_term
            return component
    return None


def _herat_component(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    settlement: str,
) -> dict[str, Any] | None:
    terms = [("TODAY" if settlement == "CASH" else "TOMORROW", 1.25, "HERAT_MATCHED")]
    if settlement == "CASH":
        terms.append(("TOMORROW", 0.85, "HERAT_TOMORROW_CASH_BRIDGE"))
    for term, weight, role in terms:
        rows = _read_rows(
            connection,
            start=start,
            end=end,
            instruments=("USD_HERAT",),
            settlements=(term,),
            trade_forms=("PAPER_NORMAL",),
            event_types=("OFFER", "TRADE", "QUOTE"),
            source_codes=("USD_HERAT",),
        )
        component = _component(
            rows,
            name="USD_HERAT",
            base_weight=weight,
            direction_threshold_percent=0.06,
            volatility_threshold_percent=0.10,
            target_minutes=5,
            minimum_minutes=3,
            maximum_staleness_seconds=240,
            end=end,
            source_role=role,
        )
        if component is not None:
            component["selected_settlement_term"] = term
            return component
    return None


def _xau_component(
    connection: sqlite3.Connection, *, start: datetime, end: datetime
) -> dict[str, Any] | None:
    rows = _read_rows(
        connection,
        start=start,
        end=end,
        instruments=("XAUUSD",),
        settlements=("SPOT",),
        event_types=("QUOTE", "REFERENCE"),
        source_codes=("XAUUSD",),
    )
    return _component(
        rows,
        name="XAUUSD",
        base_weight=0.55,
        direction_threshold_percent=0.035,
        volatility_threshold_percent=0.06,
        target_minutes=6,
        minimum_minutes=3,
        maximum_staleness_seconds=180,
        end=end,
        source_role="GLOBAL_CONFIRMATION",
    )


def _coin_market_component(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    settlement: str,
) -> dict[str, Any] | None:
    rows = _read_rows(
        connection,
        start=start,
        end=end,
        instruments=_COIN_INSTRUMENTS,
        settlements=(settlement,),
        trade_forms=("PHYSICAL",),
        event_types=("OFFER", "TRADE"),
        source_codes=("GROUP_1", "GROUP_2"),
    )
    by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_instrument[str(row["instrument"])].append(row)
    members: list[dict[str, Any]] = []
    for instrument, instrument_rows in sorted(by_instrument.items()):
        member = _component(
            instrument_rows,
            name=instrument,
            base_weight=1.0,
            direction_threshold_percent=0.10,
            volatility_threshold_percent=0.18,
            target_minutes=4,
            minimum_minutes=2,
            maximum_staleness_seconds=240,
            end=end,
            source_role="COIN_BOOK_MEMBER",
        )
        if member is not None:
            members.append(member)
    if not members:
        return None
    member_weights = [min(2.0, math.sqrt(max(1, int(row["event_count"])))) for row in members]
    total = sum(member_weights)
    strength = sum(
        weight * float(row["direction_strength"])
        for row, weight in zip(members, member_weights)
    ) / total
    volatility_ratio = sum(
        weight * float(row["volatility_ratio"])
        for row, weight in zip(members, member_weights)
    ) / total
    latest = max(_utc(str(row["last_observed_utc"])) for row in members)
    earliest = min(_utc(str(row["first_observed_utc"])) for row in members)
    base_weight = 0.80
    breadth_factor = min(1.0, 0.55 + 0.20 * len(members))
    freshness_factor = max(0.55, 1.0 - 0.45 * (end - latest).total_seconds() / 240)
    effective_weight = base_weight * breadth_factor * freshness_factor
    return {
        "name": "LIVE_COIN_MARKET",
        "source_role": "COIN_BOOK_CONFIRMATION",
        "minute_count": sum(int(row["minute_count"]) for row in members),
        "event_count": sum(int(row["event_count"]) for row in members),
        "instrument_count": len(members),
        "instruments": [str(row["name"]) for row in members],
        "first_observed_utc": _iso(earliest),
        "last_observed_utc": _iso(latest),
        "latest_age_seconds": max(0.0, (end - latest).total_seconds()),
        "first_price": None,
        "last_price": None,
        "return_percent": statistics.median(float(row["return_percent"]) for row in members),
        "volatility_percent": statistics.median(float(row["volatility_percent"]) for row in members),
        "volatility_ratio": volatility_ratio,
        "direction_consistency": statistics.median(float(row["direction_consistency"]) for row in members),
        "direction_strength": strength,
        "base_weight": base_weight,
        "effective_weight": effective_weight,
        "sample_factor": breadth_factor,
        "freshness_factor": freshness_factor,
        "source_codes": sorted({code for row in members for code in row["source_codes"]}),
        "member_components": members,
    }


def detect_canonical_market_regime(
    connection: sqlite3.Connection,
    end: datetime,
    settlement: str,
    *,
    window_seconds: int = 10 * 60,
) -> dict[str, Any]:
    """Classify a settlement from the canonical, priority-ordered evidence."""

    end = end.astimezone(timezone.utc).replace(microsecond=0)
    settlement = str(settlement).upper()
    if settlement not in {"CASH", "TOMORROW"}:
        raise ValueError("market_regime_settlement_invalid")
    start = end - timedelta(seconds=max(180, int(window_seconds)))
    components = [
        component
        for component in (
            _private_melted_component(
                connection, start=start, end=end, settlement=settlement
            ),
            _herat_component(connection, start=start, end=end, settlement=settlement),
            _xau_component(connection, start=start, end=end),
            _coin_market_component(
                connection, start=start, end=end, settlement=settlement
            ),
        )
        if component is not None
    ]
    if not components:
        return {
            "status": "NO_DATA",
            "quality": "NO_DATA",
            "regime": "UNKNOWN",
            "direction_state": "UNKNOWN",
            "volatility_state": "UNKNOWN",
            "phase": "UNKNOWN",
            "direction_score": None,
            "confidence": 0.0,
            "volatility_percent": None,
            "volatility_score": None,
            "window_seconds": window_seconds,
            "components": [],
            "method": CANONICAL_MARKET_REGIME_METHOD,
            "private_melted_anchor_present": False,
            "coin_offers_used_as_regime_input": True,
        }

    total_weight = sum(float(row["effective_weight"]) for row in components)
    score = sum(
        float(row["effective_weight"]) * float(row["direction_strength"])
        for row in components
    ) / total_weight
    volatility_score = sum(
        float(row["effective_weight"]) * float(row["volatility_ratio"])
        for row in components
    ) / total_weight
    volatility_percent = sum(
        float(row["effective_weight"]) * float(row["volatility_percent"])
        for row in components
    ) / total_weight

    private = next((row for row in components if row["name"] == "PRIVATE_MELTED_GOLD"), None)
    supporting = [row for row in components if row is not private]
    strong_positive = [row for row in components if float(row["direction_strength"]) >= 0.30]
    strong_negative = [row for row in components if float(row["direction_strength"]) <= -0.30]
    supporting_positive = sum(float(row["direction_strength"]) >= 0.24 for row in supporting)
    supporting_negative = sum(float(row["direction_strength"]) <= -0.24 for row in supporting)
    private_positive = bool(private and float(private["direction_strength"]) >= 0.20)
    private_negative = bool(private and float(private["direction_strength"]) <= -0.20)
    positive_gate = private_positive or supporting_positive >= 2
    negative_gate = private_negative or supporting_negative >= 2
    if score >= 0.32 and positive_gate:
        direction_state = "UP"
    elif score <= -0.32 and negative_gate:
        direction_state = "DOWN"
    else:
        direction_state = "RANGE"

    positive_weight = sum(float(row["effective_weight"]) for row in strong_positive)
    negative_weight = sum(float(row["effective_weight"]) for row in strong_negative)
    conflict = bool(strong_positive and strong_negative)
    private_opposed = bool(
        private
        and (
            (float(private["direction_strength"]) >= 0.30 and supporting_negative >= 2)
            or (float(private["direction_strength"]) <= -0.30 and supporting_positive >= 2)
        )
    )
    balanced_conflict = min(positive_weight, negative_weight) >= 0.75
    severe_conflict = private_opposed or balanced_conflict

    if volatility_score >= 2.50 or severe_conflict:
        volatility_state = "SHOCK"
    elif volatility_score >= 1.00 or conflict:
        volatility_state = "VOLATILE"
    elif volatility_score >= 0.35:
        volatility_state = "NORMAL"
    else:
        volatility_state = "CALM"
    phase = "TRANSITION" if conflict or (direction_state == "RANGE" and abs(score) >= 0.20) else "STABLE"

    if volatility_state == "SHOCK":
        regime = "SHOCK"
    elif direction_state in {"UP", "DOWN"}:
        regime = direction_state
    elif volatility_state == "VOLATILE":
        regime = "SHOCK"
    else:
        regime = "RANGE"

    coverage = min(1.0, total_weight / 3.40)
    disagreement = 2.0 * min(positive_weight, negative_weight) / max(total_weight, 1e-9)
    agreement = max(0.0, 1.0 - min(1.0, disagreement))
    confidence = coverage * agreement
    quality = "FULL" if private is not None and len(supporting) >= 1 else "DEGRADED"
    if private is None:
        confidence = min(confidence, 0.65)
    if len(components) == 1:
        confidence = min(confidence, 0.45)

    return {
        "status": "OBSERVED",
        "quality": quality,
        "regime": regime,
        "direction_state": direction_state,
        "volatility_state": volatility_state,
        "phase": phase,
        "direction_score": score,
        "confidence": max(0.0, min(1.0, confidence)),
        "volatility_percent": volatility_percent,
        "volatility_score": volatility_score,
        "window_seconds": window_seconds,
        "components": components,
        "component_weights_normalized": {
            str(row["name"]): float(row["effective_weight"]) / total_weight
            for row in components
        },
        "method": CANONICAL_MARKET_REGIME_METHOD,
        "private_melted_anchor_present": private is not None,
        "coin_offers_used_as_regime_input": True,
        "conflict": conflict,
        "severe_conflict": severe_conflict,
    }


def product_market_regime(regime: Mapping[str, Any]) -> dict[str, Any]:
    """Project the canonical classifier into the product snapshot vocabulary."""

    if str(regime.get("status") or "").upper() != "OBSERVED":
        return {
            "status": "ABSTAIN",
            "reason": "INSUFFICIENT_FRESH_MARKET_REGIME_EVIDENCE",
            "inputs": [],
            "method": CANONICAL_MARKET_REGIME_METHOD,
        }
    if float(regime.get("confidence") or 0.0) < 0.35:
        return {
            "status": "ABSTAIN",
            "reason": "MARKET_REGIME_CONFIDENCE_BELOW_PRODUCT_GATE",
            "inputs": [
                str(row.get("name")) for row in regime.get("components") or ()
            ],
            "method": CANONICAL_MARKET_REGIME_METHOD,
        }
    legacy = str(regime.get("regime") or "UNKNOWN").upper()
    label = {
        "RANGE": "NORMAL",
        "UP": "UP",
        "DOWN": "DOWN",
        "SHOCK": "VOLATILE",
    }.get(legacy)
    if label is None:
        return {
            "status": "ABSTAIN",
            "reason": "MARKET_REGIME_UNKNOWN",
            "inputs": [],
            "method": CANONICAL_MARKET_REGIME_METHOD,
        }
    return {
        "status": "OBSERVED",
        "label": label,
        "direction_state": regime.get("direction_state"),
        "volatility_state": regime.get("volatility_state"),
        "phase": regime.get("phase"),
        "direction_score": regime.get("direction_score"),
        "confidence": regime.get("confidence"),
        "inputs": [str(row.get("name")) for row in regime.get("components") or ()],
        "method": CANONICAL_MARKET_REGIME_METHOD,
    }


def operational_market_regime(regime: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed before a regime can influence estimator calculations.

    Raw classifier fields remain available for diagnostics, but a projection
    that abstains must not bias rates, tolerances, or hold-down state.
    """

    output = deepcopy(dict(regime))
    projection = product_market_regime(output)
    output["product_projection"] = projection
    if projection.get("status") == "OBSERVED":
        return output
    output.update(
        {
            "classifier_status": output.get("status"),
            "classifier_regime": output.get("regime"),
            "classifier_direction_state": output.get("direction_state"),
            "classifier_volatility_state": output.get("volatility_state"),
            "classifier_direction_score": output.get("direction_score"),
            "classifier_confidence": output.get("confidence"),
            "status": "ABSTAIN",
            "regime": "UNKNOWN",
            "direction_state": "UNKNOWN",
            "volatility_state": "UNKNOWN",
            "phase": "UNKNOWN",
            "direction_score": None,
            "confidence": 0.0,
        }
    )
    return output


def stabilize_market_regime(
    candidate: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply a short, explicit hold-down to transient regime label changes.

    The first source-bound observation is accepted immediately.  Later label
    changes need two consecutive refreshes; a direct UP↔DOWN reversal needs
    three.  Loss of all data is never hidden by the hold-down.
    """

    output = deepcopy(dict(candidate))
    raw_regime = str(output.get("regime") or "UNKNOWN").upper()
    output["raw_regime"] = raw_regime
    output["raw_direction_state"] = output.get("direction_state")
    output["raw_volatility_state"] = output.get("volatility_state")
    if str(output.get("status") or "").upper() != "OBSERVED":
        output.update(
            {
                "candidate_regime": None,
                "candidate_streak": 0,
                "stabilization_required": 0,
                "stabilized": False,
            }
        )
        return output

    previous = previous if isinstance(previous, Mapping) else {}
    previous_method = str(previous.get("method") or "")
    previous_regime = str(previous.get("regime") or "").upper()
    if (
        previous_method != CANONICAL_MARKET_REGIME_METHOD
        or previous_regime not in CANONICAL_MARKET_REGIME_STATES
        or previous_regime == "UNKNOWN"
    ):
        output.update(
            {
                "candidate_regime": None,
                "candidate_streak": 0,
                "stabilization_required": 0,
                "stabilized": False,
            }
        )
        return output
    if raw_regime == previous_regime:
        output.update(
            {
                "candidate_regime": None,
                "candidate_streak": 0,
                "stabilization_required": 0,
                "stabilized": False,
            }
        )
        return output

    previous_candidate = str(previous.get("candidate_regime") or "").upper()
    previous_streak = int(previous.get("candidate_streak") or 0)
    streak = previous_streak + 1 if previous_candidate == raw_regime else 1
    direct_reversal = {raw_regime, previous_regime} == {"UP", "DOWN"}
    required = 3 if direct_reversal else 2
    if streak >= required:
        output.update(
            {
                "candidate_regime": None,
                "candidate_streak": 0,
                "stabilization_required": required,
                "stabilized": True,
            }
        )
        return output

    output["regime"] = previous_regime
    if previous.get("direction_state") is not None:
        output["direction_state"] = previous.get("direction_state")
    if previous.get("volatility_state") is not None:
        output["volatility_state"] = previous.get("volatility_state")
    output["phase"] = "TRANSITION"
    output["confidence"] = min(
        float(output.get("confidence") or 0.0),
        float(previous.get("confidence") or 0.0),
    )
    output.update(
        {
            "candidate_regime": raw_regime,
            "candidate_streak": streak,
            "stabilization_required": required,
            "stabilized": True,
        }
    )
    return output


__all__ = [
    "CANONICAL_MARKET_REGIME_METHOD",
    "CANONICAL_MARKET_REGIME_STATES",
    "detect_canonical_market_regime",
    "operational_market_regime",
    "product_market_regime",
    "stabilize_market_regime",
]
