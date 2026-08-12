"""Deterministic, point-in-time coin ranges from canonical Market Store facts.

This is a conservative structural engine, not an LLM.  It estimates low-date
coins from melted gold and transfers a same-commodity coin anchor through the
observed underlying move.  All inputs retain source/form/settlement boundaries;
no USDT→Herat substitution or hidden Rial/Toman conversion is permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
import sqlite3
from statistics import median
from typing import Any, Iterable

from .market_contracts import normalize_utc
from .price_magnitude_policy import RIAL_PER_TOMAN, TRUE_IRT_MESGHAL_FLOOR
from .private_gold import filter_comparable_private_gold_physical_rows


COIN_RATE_ENGINE_VERSION = "coin-rate-engine-v5"
PROJECT_TOMAN_PER_UNIT = 1_000.0  # 1 project unit = 1,000 toman
_MESGHAL_TOMAN_MIN = 30_000_000.0
_MESGHAL_TOMAN_MAX = 200_000_000.0


def _canonical_mesghal_toman(price_num: float) -> float | None:
    """Normalize mesghal toman; convert residual true-rial leftovers once."""

    if price_num <= 0 or not math.isfinite(price_num):
        return None
    value = float(price_num)
    if value >= float(TRUE_IRT_MESGHAL_FLOOR):
        # Residual rows still stored as rial under the old contract.
        value /= float(RIAL_PER_TOMAN)
    if not _MESGHAL_TOMAN_MIN <= value <= _MESGHAL_TOMAN_MAX:
        return None
    return value


COIN_SPECS: dict[str, tuple[float, bool]] = {
    "IMAM": (2.253, False),
    "BAHAR": (2.253, True),
    "HALF_BAHAR": (2.253 / 2.0, False),
    "QUARTER_BAHAR": (2.253 / 4.0, False),
    "HALF_LOW_DATE": (2.253 / 2.0, True),
    "QUARTER_LOW_DATE": (2.253 / 4.0, True),
    "ONE_GRAM": (2.253 / 8.130, False),
}
_SETTLEMENTS = ("CASH", "TOMORROW")
_MAX_ANCHOR_AGE_SECONDS = 7 * 86_400
_HERAT_CORRECTION_WEIGHT = {"CASH": 0.35, "TOMORROW": 0.60}


@dataclass(frozen=True, slots=True)
class MeltedPoint:
    value_project: float | None
    age_seconds: float | None
    spread_relative: float
    source_kind: str | None
    fallback: bool


@dataclass(frozen=True, slots=True)
class HeratPoint:
    """A source-separated Herat point used only as an anchor bridge."""

    value_toman: float | None
    age_seconds: float | None
    spread_relative: float
    source_kind: str | None
    fallback: bool


@dataclass(frozen=True, slots=True)
class CoinRateEstimate:
    commodity_code: str
    settlement_term: str
    status: str
    estimated_project_price: int | None
    lower_project_price: int | None
    upper_project_price: int | None
    confidence: str
    method: str
    underlying_source: str | None
    anchor_age_seconds: float | None
    market_regime: str
    reason: str | None = None
    herat_source: str | None = None
    herat_basis_relative: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc(value: datetime | str, *, name: str) -> datetime:
    normalized = normalize_utc(value, field_name=name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rows(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    instrument: str,
    settlement_terms: Iterable[str],
    trade_forms: Iterable[str],
    price_unit: str,
    event_types: Iterable[str] = ("OFFER", "TRADE", "QUOTE", "REFERENCE"),
    include_comparable_conditional: bool = False,
) -> list[sqlite3.Row]:
    settlements = tuple(settlement_terms)
    forms = tuple(trade_forms)
    kinds = tuple(event_types)
    conditional_clause = "" if include_comparable_conditional else "AND is_conditional = 0"
    rows = list(
        connection.execute(
            f"""
            SELECT id, event_time_utc, available_at_utc, price_num, event_type,
                   is_conditional
            FROM market_observations
            WHERE instrument = ?
              AND settlement_term IN ({','.join('?' for _ in settlements)})
              AND trade_form IN ({','.join('?' for _ in forms)})
              AND event_type IN ({','.join('?' for _ in kinds)})
              AND quality_state = 'ELIGIBLE'
              {conditional_clause}
              AND price_unit = ?
              AND event_time_utc <= ?
              AND available_at_utc <= ?
            ORDER BY event_time_utc DESC, id DESC
            LIMIT 250
            """,
            (
                instrument,
                *settlements,
                *forms,
                *kinds,
                price_unit,
                _iso(as_of),
                _iso(as_of),
            ),
        ).fetchall()
    )
    return (
        filter_comparable_private_gold_physical_rows(rows)
        if include_comparable_conditional
        else rows
    )


def _robust_project_point(rows: list[sqlite3.Row], *, as_of: datetime, source_kind: str, fallback: bool, maximum_age: int) -> MeltedPoint:
    if not rows:
        return MeltedPoint(None, None, 0.0, None, fallback)
    latest_at = _utc(str(rows[0]["event_time_utc"]), name="rate_event_time_utc")
    age = max(0.0, (as_of - latest_at).total_seconds())
    if age > maximum_age:
        return MeltedPoint(None, age, 0.0, source_kind, fallback)
    window: list[float] = []
    for row in rows:
        if (_utc(str(row["event_time_utc"]), name="rate_event_time_utc") - latest_at).total_seconds() < -60:
            continue
        canonical = _canonical_mesghal_toman(float(row["price_num"]))
        if canonical is None:
            continue
        window.append(canonical / PROJECT_TOMAN_PER_UNIT)
    if not window:
        return MeltedPoint(None, age, 0.0, source_kind, fallback)
    center = float(median(window))
    accepted = [value for value in window if abs(value - center) / center <= 0.025]
    if not accepted:
        return MeltedPoint(None, age, 0.0, source_kind, fallback)
    spread = (max(accepted) - min(accepted)) / center if len(accepted) > 1 else 0.0
    return MeltedPoint(float(median(accepted)), age, spread, source_kind, fallback)


def _melted_point(connection: sqlite3.Connection, *, as_of: datetime, settlement: str) -> MeltedPoint:
    if settlement == "CASH":
        policies = (
            ("MELTED_GOLD_PRIVATE", ("TODAY",), ("PHYSICAL",), "PRIVATE_PHYSICAL_TODAY", False, 900),
            ("MELTED_GOLD_AGGREGATE", ("UNKNOWN",), ("PHYSICAL",), "PUBLIC_PHYSICAL_UNSPECIFIED", False, 900),
            ("MELTED_GOLD_PRIVATE", ("TODAY",), ("PAPER_NORMAL",), "PRIVATE_PAPER_TODAY", True, 180),
            ("MELTED_GOLD_FLOW", ("TODAY",), ("PAPER_NORMAL",), "PUBLIC_PAPER_TODAY", True, 180),
            # After bank hours the cash book can go quiet while the tomorrow
            # paper market remains active.  Treat the latter as an explicit,
            # low-confidence *cash bridge*, not as a physical quote.  A
            # same-settlement cash coin anchor carries the cash/paper basis;
            # this preserves a useful range instead of abstaining until the
            # next physical offer arrives.
            ("MELTED_GOLD_PRIVATE", ("TOMORROW",), ("PAPER_NORMAL",), "PRIVATE_PAPER_TOMORROW_CASH_BRIDGE", True, 180),
            ("MELTED_GOLD_FLOW", ("TOMORROW",), ("PAPER_NORMAL",), "PUBLIC_PAPER_TOMORROW_CASH_BRIDGE", True, 180),
        )
    else:
        policies = (
            ("MELTED_GOLD_PRIVATE", ("TOMORROW",), ("PHYSICAL",), "PRIVATE_PHYSICAL_TOMORROW", False, 900),
            ("MELTED_GOLD_PRIVATE", ("TOMORROW",), ("PAPER_NORMAL",), "PRIVATE_PAPER_TOMORROW", True, 180),
            ("MELTED_GOLD_FLOW", ("TOMORROW",), ("PAPER_NORMAL",), "PUBLIC_PAPER_TOMORROW", True, 180),
        )
    for instrument, terms, forms, label, fallback, maximum_age in policies:
        point = _robust_project_point(
            _rows(
                connection,
                as_of=as_of,
                instrument=instrument,
                settlement_terms=terms,
                trade_forms=forms,
                price_unit="TOMAN_PER_MESGHAL_750",
                include_comparable_conditional=(
                    instrument == "MELTED_GOLD_PRIVATE" and "PHYSICAL" in forms
                ),
            ),
            as_of=as_of,
            source_kind=label,
            fallback=fallback,
            maximum_age=maximum_age,
        )
        if point.value_project is not None:
            return point
    return MeltedPoint(None, None, 0.0, None, False)


def _robust_herat_point(
    rows: list[sqlite3.Row],
    *,
    as_of: datetime,
    source_kind: str,
    fallback: bool,
    maximum_age: int,
) -> HeratPoint:
    if not rows:
        return HeratPoint(None, None, 0.0, None, fallback)
    latest_at = _utc(str(rows[0]["event_time_utc"]), name="herat_event_time_utc")
    age = max(0.0, (as_of - latest_at).total_seconds())
    if age > maximum_age:
        return HeratPoint(None, age, 0.0, source_kind, fallback)
    window = [
        float(row["price_num"])
        for row in rows
        if (_utc(str(row["event_time_utc"]), name="herat_event_time_utc") - latest_at).total_seconds() >= -60
        and float(row["price_num"]) > 0
    ]
    if not window:
        return HeratPoint(None, age, 0.0, source_kind, fallback)
    center = float(median(window))
    accepted = [value for value in window if abs(value - center) / center <= 0.025]
    if not accepted:
        return HeratPoint(None, age, 0.0, source_kind, fallback)
    spread = (max(accepted) - min(accepted)) / center if len(accepted) > 1 else 0.0
    return HeratPoint(float(median(accepted)), age, spread, source_kind, fallback)


def _herat_point(connection: sqlite3.Connection, *, as_of: datetime, settlement: str) -> HeratPoint:
    """Read a Herat driver without substituting USDT or mixing market forms.

    The cash coin book prefers explicit physical Herat.  The tomorrow book
    prefers paper-tomorrow.  A paper fallback remains visible in the method
    label and is used only as a *relative anchor bridge*, never as a cash
    dollar quote or a standalone coin price.
    """

    if settlement == "CASH":
        policies = (
            (("UNKNOWN",), ("PHYSICAL",), "HERAT_PHYSICAL_CASH", False, 900),
            (("TODAY",), ("PAPER_NORMAL",), "HERAT_PAPER_TODAY_CASH_BRIDGE", True, 300),
            (("TOMORROW",), ("PAPER_NORMAL",), "HERAT_PAPER_TOMORROW_CASH_BRIDGE", True, 300),
        )
    else:
        policies = (
            (("TOMORROW",), ("PAPER_NORMAL",), "HERAT_PAPER_TOMORROW", False, 300),
            (("TODAY",), ("PAPER_NORMAL",), "HERAT_PAPER_TODAY_TOMORROW_BRIDGE", True, 300),
        )
    for terms, forms, label, fallback, maximum_age in policies:
        point = _robust_herat_point(
            _rows(
                connection,
                as_of=as_of,
                instrument="USD_HERAT",
                settlement_terms=terms,
                trade_forms=forms,
                price_unit="TOMAN_PER_USD",
            ),
            as_of=as_of,
            source_kind=label,
            fallback=fallback,
            maximum_age=maximum_age,
        )
        if point.value_toman is not None:
            return point
    return HeratPoint(None, None, 0.0, None, False)


def _coin_anchor(connection: sqlite3.Connection, *, as_of: datetime, code: str, settlement: str) -> tuple[float, datetime] | None:
    rows = _rows(
        connection,
        as_of=as_of,
        instrument="COIN_" + code,
        settlement_terms=(settlement,),
        trade_forms=("PHYSICAL",),
        price_unit="PROJECT_THOUSAND_TOMAN",
        event_types=("TRADE", "OFFER"),
    )
    if not rows:
        return None
    # ``_rows`` is already ordered by economic event time, then id.  Prefer a
    # confirmed trade over an offer, but keep that chronological ordering
    # within the selected evidence type.  An older backfilled trade can have a
    # larger SQLite id and must not displace the newer market anchor.
    row = next(
        (item for item in rows if str(item["event_type"]) == "TRADE"),
        rows[0],
    )
    event_time = _utc(str(row["event_time_utc"]), name="coin_anchor_event_time_utc")
    age = max(0.0, (as_of - event_time).total_seconds())
    if age > _MAX_ANCHOR_AGE_SECONDS:
        return None
    price = float(row["price_num"])
    return (price, event_time) if price > 0 else None


def _ime_imam_point(connection: sqlite3.Connection, *, as_of: datetime) -> float | None:
    rows = _rows(
        connection,
        as_of=as_of,
        instrument="IME_GOLD_COIN_IMAM",
        settlement_terms=("SPOT",),
        trade_forms=("NOT_APPLICABLE",),
        price_unit="TOMAN_PER_COIN",
        event_types=("QUOTE", "REFERENCE"),
    )
    if not rows:
        return None
    event_time = _utc(str(rows[0]["event_time_utc"]), name="ime_imam_event_time_utc")
    if (as_of - event_time).total_seconds() > 3600:
        return None
    value = float(rows[0]["price_num"]) / PROJECT_TOMAN_PER_UNIT
    return value if value > 0 else None


def _paper_regime(connection: sqlite3.Connection, *, as_of: datetime) -> str:
    rows = _rows(
        connection,
        as_of=as_of,
        instrument="MELTED_GOLD_PRIVATE",
        settlement_terms=("TOMORROW",),
        trade_forms=("PAPER_NORMAL",),
        price_unit="TOMAN_PER_MESGHAL_750",
    )
    values = [float(row["price_num"]) for row in rows[:20] if float(row["price_num"]) > 0]
    if len(values) < 3:
        return "NORMAL"
    latest = values[0]
    baseline = float(median(values[1:]))
    relative = latest / baseline - 1.0
    dispersion = (max(values) - min(values)) / baseline
    if dispersion >= 0.012:
        return "VOLATILE"
    if relative >= 0.0015:
        return "UP"
    if relative <= -0.0015:
        return "DOWN"
    return "NORMAL"


def _round_project(value: float) -> int:
    return max(1, int(round(value / 50.0) * 50))


def _tolerance(
    *,
    point: MeltedPoint,
    anchor_age: float | None,
    regime: str,
    structural_only: bool,
    herat_basis_relative: float | None = None,
    herat_fallback: bool = False,
) -> tuple[float, float]:
    base = 0.005 if anchor_age is not None else (0.006 if structural_only else 0.011)
    source_extra = min(0.004, point.spread_relative + (0.002 if point.fallback else 0.0))
    age_extra = min(0.006, max(0.0, (anchor_age or 0.0) - 300.0) / 86_400.0 * 0.001)
    directional = 0.003 if regime in {"UP", "DOWN"} else 0.005 if regime == "VOLATILE" else 0.0
    herat_extra = 0.0
    if herat_basis_relative is not None:
        herat_extra = min(0.003, abs(herat_basis_relative) * 0.25 + (0.001 if herat_fallback else 0.0))
    lower = min(0.018, base + source_extra + age_extra + herat_extra + (directional if regime == "DOWN" else 0.0))
    upper = min(0.018, base + source_extra + age_extra + herat_extra + (directional if regime == "UP" else 0.0))
    if regime == "VOLATILE":
        lower = upper = min(0.02, base + source_extra + age_extra + herat_extra + directional)
    return lower, upper


def build_coin_rate_estimates(connection: sqlite3.Connection, *, as_of_utc: datetime | str) -> list[CoinRateEstimate]:
    """Build ranges from facts known at ``as_of``; no write or network side effect."""

    as_of = _utc(as_of_utc, name="coin_rate_as_of_utc")
    regime = _paper_regime(connection, as_of=as_of)
    output: list[CoinRateEstimate] = []
    for settlement in _SETTLEMENTS:
        current = _melted_point(connection, as_of=as_of, settlement=settlement)
        for code, (coefficient, low_date) in COIN_SPECS.items():
            if current.value_project is None:
                output.append(CoinRateEstimate(code, settlement, "NO_DATA", None, None, None, "NONE", "ABSTAIN_NO_FRESH_MELTED", None, None, regime, "NO_FRESH_MELTED"))
                continue
            intrinsic = current.value_project * coefficient
            anchor = _coin_anchor(connection, as_of=as_of, code=code, settlement=settlement)
            estimate: float | None = None
            method = ""
            anchor_age: float | None = None
            structural_only = False
            herat_source: str | None = None
            herat_basis_relative: float | None = None
            herat_fallback = False
            if anchor is not None:
                anchor_price, anchor_time = anchor
                anchor_melted = _melted_point(connection, as_of=anchor_time, settlement=settlement)
                if anchor_melted.value_project is not None:
                    old_intrinsic = anchor_melted.value_project * coefficient
                    residual = anchor_price - old_intrinsic
                    anchor_age = max(0.0, (as_of - anchor_time).total_seconds())
                    if low_date:
                        residual *= 0.5 ** (anchor_age / (7 * 86_400))
                        method = "LOW_DATE_INTRINSIC_PLUS_DECAYED_SAME_SETTLEMENT_ANCHOR"
                    else:
                        method = "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER"
                    estimate = intrinsic + residual
                    # Melted gold already includes much of the dollar move.
                    # Herat therefore corrects only the relative basis move
                    # that melted did not explain; this prevents double
                    # counting while making fresh paper Herat material at a
                    # coin-anchor transfer.
                    current_herat = _herat_point(connection, as_of=as_of, settlement=settlement)
                    anchor_herat = _herat_point(connection, as_of=anchor_time, settlement=settlement)
                    if (
                        current_herat.value_toman is not None
                        and anchor_herat.value_toman is not None
                        and current_herat.source_kind == anchor_herat.source_kind
                    ):
                        melted_change = current.value_project / anchor_melted.value_project - 1.0
                        herat_change = current_herat.value_toman / anchor_herat.value_toman - 1.0
                        herat_basis_relative = herat_change - melted_change
                        estimate += old_intrinsic * _HERAT_CORRECTION_WEIGHT[settlement] * herat_basis_relative
                        herat_source = current_herat.source_kind
                        herat_fallback = current_herat.fallback or anchor_herat.fallback
                        method += "_WITH_HERAT_BASIS_BRIDGE"
            if estimate is None and code == "IMAM" and settlement == "CASH":
                ime = _ime_imam_point(connection, as_of=as_of)
                if ime is not None:
                    estimate = ime
                    method = "IME_IMAM_DIRECT_CASH_REFERENCE"
            if estimate is None and low_date:
                estimate = intrinsic
                method = "LOW_DATE_MELTED_INTRINSIC"
                structural_only = True
            if estimate is None:
                output.append(CoinRateEstimate(code, settlement, "NO_DATA", None, None, None, "NONE", "ABSTAIN_NO_SAFE_SAME_COMMODITY_ANCHOR", current.source_kind, None, regime, "NO_SAFE_SAME_COMMODITY_ANCHOR"))
                continue
            negative, positive = _tolerance(
                point=current,
                anchor_age=anchor_age,
                regime=regime,
                structural_only=structural_only,
                herat_basis_relative=herat_basis_relative,
                herat_fallback=herat_fallback,
            )
            center = _round_project(estimate)
            lower = min(center, _round_project(estimate * (1.0 - negative)))
            upper = max(center, _round_project(estimate * (1.0 + positive)))
            confidence = "HIGH" if anchor_age is not None and not current.fallback else "MEDIUM" if not current.fallback else "LOW_PAPER_FALLBACK"
            output.append(
                CoinRateEstimate(
                    code,
                    settlement,
                    "ESTIMATED",
                    center,
                    lower,
                    upper,
                    confidence,
                    method,
                    current.source_kind,
                    anchor_age,
                    regime,
                    herat_source=herat_source,
                    herat_basis_relative=herat_basis_relative,
                )
            )
    return output
