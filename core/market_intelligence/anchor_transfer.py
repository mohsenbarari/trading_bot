"""Strictly-prior coin-anchor transfer for Shadow range estimation.

This runtime-only module consumes validated normalized observations and the
precomputed anchor policy bundled with the repository. Training and raw data
access stay outside the serving path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Mapping, Sequence

from core.market_intelligence.low_date import read_physical_melted_reference

PRICE_MULTIPLIER = 1_000
IMAM_COEFFICIENT = 2.253
COMMODITY_COEFFICIENTS = {
    "امام": 2.253,
    "بهار": 2.253,
    "نیم بهار": 2.253 / 2.0,
    "نیم تاریخ پایین": 2.253 / 2.0,
    "ربع بهار": 2.253 / 4.0,
    "ربع تاریخ پایین": 2.253 / 4.0,
    "یک گرمی": 2.253 / 8.130,
}
LOW_DATE_COMMODITIES = {
    "بهار",
    "نیم تاریخ پایین",
    "ربع تاریخ پایین",
}
REGIMES = {"RANGE", "UP", "DOWN", "SHOCK", "UNKNOWN"}


def parse_time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile needs at least one value")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _round_toman(value: float) -> int:
    return int(round(float(value) / 50_000.0) * 50_000)


@dataclass(frozen=True, slots=True)
class MarketPoint:
    status: str
    value: float | None
    lower: float | None
    upper: float | None
    sample_count: int
    last_event_utc: str | None
    age_seconds: float | None
    source: str
    uncertainty_relative: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketContext:
    status: str
    as_of_utc: str
    settlement: str
    melted: MarketPoint
    generic_imam: MarketPoint
    usd: MarketPoint
    xauusd: MarketPoint
    ime_melted: MarketPoint
    ime_imam: MarketPoint
    theoretical_melted_toman: float | None
    imam_bubble_toman: float | None
    cross_signal_disagreement_relative: float
    uncertainty_relative: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CoinEvent:
    event_id: str
    event_time_utc: str
    commodity: str
    settlement: str
    trade_form: str
    price_toman: float
    side: str
    event_kind: str
    confidence: float
    weight: float
    regime: str


@dataclass(frozen=True, slots=True)
class EnrichedCoinEvent:
    event: CoinEvent
    context: MarketContext
    intrinsic_toman: float
    commodity_bubble_toman: float


def no_point(source: str, reason: str) -> MarketPoint:
    return MarketPoint(
        status="NO_DATA",
        value=None,
        lower=None,
        upper=None,
        sample_count=0,
        last_event_utc=None,
        age_seconds=None,
        source=source,
        uncertainty_relative=0.0,
        reason=reason,
    )


def _market_point(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    instrument: str,
    market_labels: Sequence[str],
    trade_forms: Sequence[str] | None = None,
    settlement_terms: Sequence[str] | None = None,
    window_seconds: int = 180,
    maximum_age_seconds: int = 900,
) -> MarketPoint:
    if not market_labels:
        raise ValueError("market_labels cannot be empty")
    labels_sql = ",".join("?" for _ in market_labels)
    form_clause = ""
    params: list[Any] = [instrument, *market_labels]
    if trade_forms:
        form_clause = (
            f" AND trade_form IN ({','.join('?' for _ in trade_forms)})"
        )
        params.extend(trade_forms)
    settlement_clause = ""
    if settlement_terms:
        settlement_clause = (
            f" AND settlement_term IN ({','.join('?' for _ in settlement_terms)})"
        )
        params.extend(settlement_terms)
    params.append(iso_utc(as_of))
    latest = connection.execute(
        f"""
        SELECT event_time_utc
        FROM price_events
        WHERE instrument=?
          AND market_label IN ({labels_sql})
          {form_clause}
          {settlement_clause}
          AND event_time_utc<=?
          AND parse_confidence>=0.70
        ORDER BY event_time_utc DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    source = "|".join(market_labels)
    if latest is None:
        return no_point(source, "NO_STRICTLY_PRIOR_QUOTE")
    latest_at = parse_time(str(latest["event_time_utc"]))
    age = max(0.0, (as_of - latest_at).total_seconds())
    if age > maximum_age_seconds:
        return MarketPoint(
            status="NO_DATA",
            value=None,
            lower=None,
            upper=None,
            sample_count=0,
            last_event_utc=iso_utc(latest_at),
            age_seconds=age,
            source=source,
            uncertainty_relative=0.0,
            reason="QUOTE_STALE",
        )
    start = latest_at - timedelta(seconds=window_seconds)
    query_params: list[Any] = [instrument, *market_labels]
    if trade_forms:
        query_params.extend(trade_forms)
    if settlement_terms:
        query_params.extend(settlement_terms)
    query_params.extend((iso_utc(start), iso_utc(latest_at)))
    rows = connection.execute(
        f"""
        SELECT event_time_utc, price_num
        FROM price_events
        WHERE instrument=?
          AND market_label IN ({labels_sql})
          {form_clause}
          {settlement_clause}
          AND event_time_utc>=?
          AND event_time_utc<=?
          AND parse_confidence>=0.70
        ORDER BY event_time_utc, id
        """,
        query_params,
    ).fetchall()
    values = [
        float(row["price_num"])
        for row in rows
        if row["price_num"] is not None and float(row["price_num"]) > 0
    ]
    if not values:
        return no_point(source, "ROBUST_WINDOW_EMPTY")
    median = float(statistics.median(values))
    accepted = [
        value
        for value in values
        if abs(value - median) / median <= 0.025
    ]
    if not accepted:
        return no_point(source, "ALL_QUOTES_REJECTED")
    center = float(statistics.median(accepted))
    lower = quantile(accepted, 0.10)
    upper = quantile(accepted, 0.90)
    spread = max(0.0, upper - lower) / center
    age_extra = min(0.005, age / maximum_age_seconds * 0.002)
    return MarketPoint(
        status="OBSERVED",
        value=center,
        lower=lower,
        upper=upper,
        sample_count=len(accepted),
        last_event_utc=iso_utc(latest_at),
        age_seconds=age,
        source=source,
        uncertainty_relative=min(0.03, 0.001 + spread + age_extra),
    )


def _external_point(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    instrument_code: str,
    quote_kinds: Sequence[str] = ("LAST", "MID", "CLOSE"),
    maximum_age_seconds: int = 259_200,
) -> MarketPoint:
    if (
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='external_market_observations'
            """
        ).fetchone()
        is None
    ):
        return no_point(
            instrument_code,
            "EXTERNAL_MARKET_OBSERVATIONS_TABLE_MISSING",
        )
    kinds_sql = ",".join("?" for _ in quote_kinds)
    row = connection.execute(
        f"""
        SELECT observed_at_utc, normalized_price_num, quote_kind
        FROM external_market_observations
        WHERE instrument_code=?
          AND quote_kind IN ({kinds_sql})
          AND normalized_price_num IS NOT NULL
          AND observed_at_utc<=?
        ORDER BY observed_at_utc DESC,
          CASE quote_kind WHEN 'LAST' THEN 0 WHEN 'MID' THEN 1 ELSE 2 END,
          id DESC
        LIMIT 1
        """,
        (instrument_code, *quote_kinds, iso_utc(as_of)),
    ).fetchone()
    if row is None:
        return no_point(instrument_code, "NO_STRICTLY_PRIOR_EXTERNAL_QUOTE")
    observed = parse_time(str(row["observed_at_utc"]))
    age = max(0.0, (as_of - observed).total_seconds())
    if age > maximum_age_seconds:
        return MarketPoint(
            status="NO_DATA",
            value=None,
            lower=None,
            upper=None,
            sample_count=0,
            last_event_utc=iso_utc(observed),
            age_seconds=age,
            source=f"{instrument_code}:{row['quote_kind']}",
            uncertainty_relative=0.0,
            reason="EXTERNAL_QUOTE_STALE",
        )
    value = float(row["normalized_price_num"])
    return MarketPoint(
        status="OBSERVED",
        value=value,
        lower=value,
        upper=value,
        sample_count=1,
        last_event_utc=iso_utc(observed),
        age_seconds=age,
        source=f"{instrument_code}:{row['quote_kind']}",
        uncertainty_relative=min(
            0.02,
            0.002 + age / maximum_age_seconds * 0.01,
        ),
    )


def _usdt_point(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    maximum_age_seconds: int = 900,
) -> MarketPoint:
    return _external_point(
        connection,
        as_of=as_of,
        instrument_code="USDT_IRT",
        quote_kinds=("MID", "CLOSE"),
        maximum_age_seconds=maximum_age_seconds,
    )


def read_market_context(
    market_db: Path,
    *,
    as_of: datetime | str,
    settlement: str,
    maximum_primary_age_seconds: int = 300,
) -> MarketContext:
    observed_at = parse_time(as_of)
    if settlement not in {"CASH", "TOMORROW"}:
        raise ValueError(f"unsupported settlement: {settlement}")
    uri = f"file:{market_db.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if settlement == "CASH":
            physical = read_physical_melted_reference(
                market_db,
                as_of=observed_at,
                maximum_age_seconds=900,
                enable_paper_delta_bridge=True,
                maximum_bridge_age_seconds=345_600,
                maximum_current_paper_age_seconds=(
                    maximum_primary_age_seconds
                ),
            )
            if physical.status in {"OBSERVED", "BRIDGED"}:
                melted = MarketPoint(
                    status=physical.status,
                    value=physical.average_price_toman,
                    lower=physical.lower_price_toman,
                    upper=physical.upper_price_toman,
                    sample_count=physical.sample_count,
                    last_event_utc=physical.last_event_utc,
                    age_seconds=physical.age_seconds,
                    source=physical.selection,
                    uncertainty_relative=physical.uncertainty_relative,
                )
            else:
                melted = no_point(
                    "EXPLICIT_PHYSICAL_MELTED",
                    physical.reason or "NO_PHYSICAL_MELTED",
                )
            generic_imam = _market_point(
                connection,
                as_of=observed_at,
                instrument="GOLD_COIN",
                market_labels=("سکه نقدی",),
                trade_forms=("PHYSICAL",),
                settlement_terms=("TODAY",),
                maximum_age_seconds=maximum_primary_age_seconds,
            )
            usd = _market_point(
                connection,
                as_of=observed_at,
                instrument="USD_HERAT",
                market_labels=("دلار هرات امروزی فیزیکی",),
                trade_forms=("PHYSICAL",),
                settlement_terms=("TODAY",),
                maximum_age_seconds=max(
                    1_800,
                    maximum_primary_age_seconds,
                ),
            )
            ime_melted = _external_point(
                connection,
                as_of=observed_at,
                instrument_code="IME_GOLD_BAR",
            )
            ime_imam = _external_point(
                connection,
                as_of=observed_at,
                instrument_code="IME_GOLD_COIN_IMAM",
            )
        else:
            melted = _market_point(
                connection,
                as_of=observed_at,
                instrument="MELTED_GOLD",
                market_labels=(
                    "آبشده حواله",
                    "آبشده غیررسمی",
                    "آبشده امروزی",
                ),
                trade_forms=("PAPER",),
                maximum_age_seconds=maximum_primary_age_seconds,
            )
            generic_imam = _market_point(
                connection,
                as_of=observed_at,
                instrument="GOLD_COIN",
                market_labels=("سکه نقدی",),
                trade_forms=("PHYSICAL",),
                settlement_terms=("TOMORROW",),
                maximum_age_seconds=maximum_primary_age_seconds,
            )
            usd = _market_point(
                connection,
                as_of=observed_at,
                instrument="USD_HERAT",
                market_labels=("دلار هرات فردایی کاغذی",),
                trade_forms=("PAPER",),
                maximum_age_seconds=max(
                    1_800,
                    maximum_primary_age_seconds,
                ),
            )
            ime_melted = no_point(
                "IME_GOLD_BAR",
                "CASH_EXCHANGE_REFERENCE_NOT_DIRECT_TOMORROW_INPUT",
            )
            ime_imam = no_point(
                "IME_GOLD_COIN_IMAM",
                "CASH_EXCHANGE_REFERENCE_NOT_DIRECT_TOMORROW_INPUT",
            )

        usdt = _usdt_point(connection, as_of=observed_at)
        if usd.status == "NO_DATA" and usdt.status == "OBSERVED":
            usd = MarketPoint(
                **(
                    usdt.to_dict()
                    | {
                        "source": "USDT_IRT_PROXY",
                        "uncertainty_relative": min(
                            0.03,
                            usdt.uncertainty_relative + 0.003,
                        ),
                    }
                )
            )
        xauusd = _market_point(
            connection,
            as_of=observed_at,
            instrument="XAUUSD",
            market_labels=("اونس جهانی",),
            trade_forms=("NOT_APPLICABLE",),
            maximum_age_seconds=max(
                3_600,
                maximum_primary_age_seconds,
            ),
        )
    finally:
        connection.close()

    theoretical = None
    if usd.value is not None and xauusd.value is not None:
        theoretical = usd.value * xauusd.value / 9.572737
    imam_bubble = None
    if melted.value is not None and generic_imam.value is not None:
        imam_bubble = generic_imam.value - IMAM_COEFFICIENT * melted.value

    disagreement_rows: list[float] = []
    if (
        melted.value is not None
        and theoretical is not None
        and theoretical > 0
    ):
        # The absolute physical-gold bubble is legitimate. For uncertainty,
        # compare movements through the bounded relative gap, not raw equality.
        disagreement_rows.append(
            min(0.03, abs(melted.value - theoretical) / melted.value)
        )
    if (
        settlement == "CASH"
        and generic_imam.value is not None
        and ime_imam.value is not None
    ):
        disagreement_rows.append(
            min(
                0.03,
                abs(generic_imam.value - ime_imam.value)
                / generic_imam.value,
            )
        )
    disagreement = (
        float(statistics.median(disagreement_rows))
        if disagreement_rows
        else 0.0
    )
    uncertainty = min(
        0.05,
        melted.uncertainty_relative
        + generic_imam.uncertainty_relative
        + disagreement * 0.20,
    )
    status = (
        "OBSERVED"
        if melted.value is not None and imam_bubble is not None
        else "NO_DATA"
    )
    return MarketContext(
        status=status,
        as_of_utc=iso_utc(observed_at),
        settlement=settlement,
        melted=melted,
        generic_imam=generic_imam,
        usd=usd,
        xauusd=xauusd,
        ime_melted=ime_melted,
        ime_imam=ime_imam,
        theoretical_melted_toman=theoretical,
        imam_bubble_toman=imam_bubble,
        cross_signal_disagreement_relative=disagreement,
        uncertainty_relative=uncertainty,
    )
def beta_for(
    calibration: Mapping[str, Any],
    commodity: str,
    regime: str,
) -> float:
    row = calibration["commodities"][commodity]
    return float(
        (row.get("regime_betas") or {}).get(
            regime,
            row["global_beta"],
        )
    )


def predict_from_anchor(
    anchor: EnrichedCoinEvent,
    current_context: MarketContext,
    *,
    calibration: Mapping[str, Any],
    current_regime: str,
) -> dict[str, Any] | None:
    commodity = anchor.event.commodity
    if (
        current_context.melted.value is None
        or anchor.context.melted.value is None
    ):
        return None
    beta = beta_for(calibration, commodity, current_regime)
    raw_melted_delta = (
        float(current_context.melted.value)
        - float(anchor.context.melted.value)
    )
    theoretical_delta = None
    if (
        current_context.theoretical_melted_toman is not None
        and anchor.context.theoretical_melted_toman is not None
    ):
        theoretical_delta = (
            float(current_context.theoretical_melted_toman)
            - float(anchor.context.theoretical_melted_toman)
        )
    # Melted gold is primary. Dollar × ounce is a conservative cross-check,
    # not a replacement, and is used only when its movement is reasonably
    # consistent with the observed melted market.
    melted_delta = raw_melted_delta
    theoretical_weight = 0.0
    if (
        theoretical_delta is not None
        and abs(theoretical_delta - raw_melted_delta)
        <= max(2_500_000.0, 0.035 * float(anchor.context.melted.value))
    ):
        theoretical_weight = 0.25
        melted_delta = (
            (1.0 - theoretical_weight) * raw_melted_delta
            + theoretical_weight * theoretical_delta
        )
    imam_bubble_delta = None
    bubble_delta = 0.0
    transfer_mode = "STRUCTURAL_UNDERLYING_DELTA"
    if (
        current_context.imam_bubble_toman is not None
        and anchor.context.imam_bubble_toman is not None
    ):
        imam_bubble_delta = (
            float(current_context.imam_bubble_toman)
            - float(anchor.context.imam_bubble_toman)
        )
        bubble_delta = beta * imam_bubble_delta
        transfer_mode = "STRUCTURAL_PLUS_OBSERVED_IMAM_BUBBLE_DELTA"
    structural_delta = COMMODITY_COEFFICIENTS[commodity] * melted_delta
    residual_decay = 1.0
    anchor_residual = (
        anchor.event.price_toman
        - COMMODITY_COEFFICIENTS[commodity]
        * float(anchor.context.melted.value)
    )
    age_seconds = max(
        0.0,
        (
            parse_time(current_context.as_of_utc)
            - parse_time(anchor.event.event_time_utc)
        ).total_seconds(),
    )
    if commodity in LOW_DATE_COMMODITIES:
        # Sparse low-date bubbles decay toward structural gold value instead
        # of remaining permanent after one old offer.
        residual_decay = 0.5 ** (age_seconds / (7 * 86_400))
        structural_now = (
            COMMODITY_COEFFICIENTS[commodity]
            * float(current_context.melted.value)
        )
        estimate = (
            structural_now
            + residual_decay * anchor_residual
            + bubble_delta
        )
    else:
        estimate = (
            anchor.event.price_toman
            + structural_delta
            + bubble_delta
        )
    return {
        "estimated_price_toman": estimate,
        "transfer_mode": transfer_mode,
        "beta": beta,
        "raw_melted_delta_toman": raw_melted_delta,
        "theoretical_melted_delta_toman": theoretical_delta,
        "theoretical_crosscheck_weight": theoretical_weight,
        "structural_melted_delta_toman": structural_delta,
        "imam_bubble_delta_toman": imam_bubble_delta,
        "commodity_bubble_delta_toman": bubble_delta,
        "anchor_residual_toman": anchor_residual,
        "anchor_residual_decay": residual_decay,
        "anchor_age_seconds": age_seconds,
    }


def latest_anchors(
    events: Sequence[EnrichedCoinEvent],
    *,
    as_of: datetime | str,
) -> dict[tuple[str, str], EnrichedCoinEvent]:
    cutoff = iso_utc(parse_time(as_of))
    output: dict[tuple[str, str], EnrichedCoinEvent] = {}
    for row in events:
        if row.event.event_time_utc >= cutoff:
            break
        output[(row.event.commodity, row.event.settlement)] = row
    return output
def _market_point_from_mapping(
    payload: Mapping[str, Any],
) -> MarketPoint:
    return MarketPoint(
        status=str(payload["status"]),
        value=(
            float(payload["value"])
            if payload.get("value") is not None
            else None
        ),
        lower=(
            float(payload["lower"])
            if payload.get("lower") is not None
            else None
        ),
        upper=(
            float(payload["upper"])
            if payload.get("upper") is not None
            else None
        ),
        sample_count=int(payload.get("sample_count") or 0),
        last_event_utc=(
            str(payload["last_event_utc"])
            if payload.get("last_event_utc") is not None
            else None
        ),
        age_seconds=(
            float(payload["age_seconds"])
            if payload.get("age_seconds") is not None
            else None
        ),
        source=str(payload.get("source") or ""),
        uncertainty_relative=float(
            payload.get("uncertainty_relative") or 0.0
        ),
        reason=(
            str(payload["reason"])
            if payload.get("reason") is not None
            else None
        ),
    )


def enriched_event_from_mapping(
    payload: Mapping[str, Any],
) -> EnrichedCoinEvent:
    event_payload = payload["event"]
    context_payload = payload["context"]
    event = CoinEvent(
        event_id=str(
            event_payload.get("event_id")
            or f"RUNTIME_ANCHOR:{event_payload['commodity']}"
        ),
        event_time_utc=str(event_payload["event_time_utc"]),
        commodity=str(event_payload["commodity"]),
        settlement=str(event_payload["settlement"]),
        trade_form=str(event_payload["trade_form"]),
        price_toman=float(event_payload["price_toman"]),
        side=str(event_payload["side"]),
        event_kind=str(event_payload["event_kind"]),
        confidence=float(event_payload["confidence"]),
        weight=float(event_payload["weight"]),
        regime=str(event_payload["regime"]),
    )
    context = MarketContext(
        status=str(context_payload["status"]),
        as_of_utc=str(context_payload["as_of_utc"]),
        settlement=str(context_payload["settlement"]),
        melted=_market_point_from_mapping(context_payload["melted"]),
        generic_imam=_market_point_from_mapping(
            context_payload["generic_imam"]
        ),
        usd=_market_point_from_mapping(context_payload["usd"]),
        xauusd=_market_point_from_mapping(context_payload["xauusd"]),
        ime_melted=_market_point_from_mapping(
            context_payload["ime_melted"]
        ),
        ime_imam=_market_point_from_mapping(
            context_payload["ime_imam"]
        ),
        theoretical_melted_toman=(
            float(context_payload["theoretical_melted_toman"])
            if context_payload.get("theoretical_melted_toman")
            is not None
            else None
        ),
        imam_bubble_toman=(
            float(context_payload["imam_bubble_toman"])
            if context_payload.get("imam_bubble_toman") is not None
            else None
        ),
        cross_signal_disagreement_relative=float(
            context_payload.get(
                "cross_signal_disagreement_relative"
            )
            or 0.0
        ),
        uncertainty_relative=float(
            context_payload.get("uncertainty_relative") or 0.0
        ),
    )
    return EnrichedCoinEvent(
        event=event,
        context=context,
        intrinsic_toman=float(payload["intrinsic_toman"]),
        commodity_bubble_toman=float(
            payload["commodity_bubble_toman"]
        ),
    )


def current_regime_for_state(
    state: Mapping[str, Any],
    settlement: str,
) -> tuple[str, float, float]:
    payload = (state.get("settlements") or {}).get(settlement) or {}
    regime = payload.get("market_regime") or {}
    name = str(regime.get("regime") or "UNKNOWN")
    if name not in REGIMES:
        name = "UNKNOWN"
    return (
        name,
        float(regime.get("direction_score") or 0.0),
        float(regime.get("confidence") or 0.0),
    )


def calibration_qhat(
    calibration: Mapping[str, Any],
    commodity: str,
) -> float:
    row = calibration["commodities"][commodity]
    value = row.get("relative_error_qhat")
    if value is None:
        value = calibration.get("global_relative_error_qhat", 0.01)
    return max(0.0025, min(0.03, float(value)))


def _trusted_live_anchor(rate: Mapping[str, Any]) -> bool:
    anchor = rate.get("group_offer_anchor") or {}
    if str(anchor.get("status")) != "OBSERVED":
        return False
    if int(anchor.get("trade_count") or 0) >= 1:
        return True
    return (
        int(anchor.get("offer_count") or 0) >= 4
        and anchor.get("best_bid_toman") is not None
        and anchor.get("best_ask_toman") is not None
    )


def _strong_low_date_rate(rate: Mapping[str, Any]) -> bool:
    if str(rate.get("commodity_name") or "") not in LOW_DATE_COMMODITIES:
        return False
    if str(rate.get("status") or "") != "ESTIMATED":
        return False
    method = str(rate.get("method") or "").upper()
    return any(
        marker in method
        for marker in ("LOW_DATE", "PHYSICAL_MELTED", "INTRINSIC")
    )


def apply_coin_anchor_transfer_overlay(
    state: Mapping[str, Any],
    *,
    market_db: Path,
    enriched_events: Sequence[EnrichedCoinEvent],
    calibration: Mapping[str, Any],
) -> dict[str, Any]:
    output = deepcopy(dict(state))
    generated = str(output["generated_at_utc"])
    anchors = latest_anchors(enriched_events, as_of=generated)
    contexts = {
        settlement: read_market_context(
            market_db,
            as_of=generated,
            settlement=settlement,
        )
        for settlement in ("CASH", "TOMORROW")
    }
    output["coin_anchor_transfer"] = {
        "status": "SHADOW",
        "generated_at_utc": generated,
        "contexts": {
            settlement: context.to_dict()
            for settlement, context in contexts.items()
        },
    }
    for settlement, payload in (output.get("settlements") or {}).items():
        if settlement not in contexts or not isinstance(payload, dict):
            continue
        context = contexts[settlement]
        regime_name, direction_score, regime_confidence = (
            current_regime_for_state(output, settlement)
        )
        for rate in payload.get("rates") or []:
            if (
                not isinstance(rate, dict)
                or _trusted_live_anchor(rate)
                or _strong_low_date_rate(rate)
            ):
                continue
            commodity = str(rate.get("commodity_name") or "")
            if commodity not in COMMODITY_COEFFICIENTS:
                continue
            anchor = anchors.get((commodity, settlement))
            anchor_selection = "SAME_SETTLEMENT"
            if anchor is None:
                alternate = (
                    "TOMORROW" if settlement == "CASH" else "CASH"
                )
                anchor = anchors.get((commodity, alternate))
                anchor_selection = "EXPLICIT_CROSS_SETTLEMENT_FALLBACK"
            movement_context = context
            movement_reference_settlement = settlement
            if (
                anchor is not None
                and anchor.context.settlement in contexts
                and anchor.context.melted.value is not None
            ):
                # Some historical cash-coin anchors were deliberately paired
                # with a contemporaneous paper melted reference because the
                # physical quote was stale. Compare that paper reference with
                # the same paper market now; do not compare its absolute level
                # directly with current physical melted gold.
                movement_reference_settlement = anchor.context.settlement
                movement_context = contexts[movement_reference_settlement]
            transfer = (
                predict_from_anchor(
                    anchor,
                    movement_context,
                    calibration=calibration,
                    current_regime=regime_name,
                )
                if anchor is not None
                else None
            )
            direct_imam = (
                commodity == "امام"
                and context.generic_imam.status == "OBSERVED"
                and context.generic_imam.value is not None
            )
            if direct_imam:
                estimate = float(context.generic_imam.value)
                method = (
                    "DIRECT_GENERIC_IMAM_CASH_ROBUST"
                    if settlement == "CASH"
                    else "DIRECT_GENERIC_IMAM_TOMORROW_PHYSICAL_ROBUST"
                )
                base_qhat = float(
                    calibration.get(
                        "direct_imam_relative_error_qhat",
                        0.004,
                    )
                )
                anchor_payload = (
                    asdict(anchor.event) if anchor is not None else None
                )
                transfer_payload = transfer
            elif transfer is not None and anchor is not None:
                settlement_basis_adjustment = 0.0
                if (
                    anchor.event.settlement != settlement
                    and contexts[anchor.event.settlement].melted.value is not None
                    and context.melted.value is not None
                ):
                    settlement_basis_adjustment = (
                        COMMODITY_COEFFICIENTS[commodity]
                        * (
                            float(context.melted.value)
                            - float(
                                contexts[
                                    anchor.event.settlement
                                ].melted.value
                            )
                        )
                    )
                transfer["settlement_basis_adjustment_toman"] = (
                    settlement_basis_adjustment
                )
                transfer["anchor_selection"] = anchor_selection
                transfer["movement_reference_settlement"] = (
                    movement_reference_settlement
                )
                estimate = (
                    float(transfer["estimated_price_toman"])
                    + settlement_basis_adjustment
                )
                method = (
                    "LAST_TRUSTED_COIN_ANCHOR_"
                    f"{transfer['transfer_mode']}"
                )
                if anchor_selection != "SAME_SETTLEMENT":
                    method += "_CROSS_SETTLEMENT_BASIS_ADJUSTED"
                base_qhat = calibration_qhat(calibration, commodity)
                anchor_payload = asdict(anchor.event)
                transfer_payload = transfer
            else:
                continue

            if direct_imam:
                signal_extra = min(
                    0.004,
                    context.generic_imam.uncertainty_relative * 0.25
                    + (0.0015 if settlement == "TOMORROW" else 0.0005),
                )
            else:
                signal_extra = min(
                    0.008,
                    movement_context.uncertainty_relative * 0.20
                    + (
                        0.002
                        if transfer_payload.get("transfer_mode")
                        == "STRUCTURAL_UNDERLYING_DELTA"
                        else 0.0
                    )
                    + (
                        0.002
                        if transfer_payload.get("anchor_selection")
                        != "SAME_SETTLEMENT"
                        else 0.0
                    ),
                )
            age_extra = 0.0
            if transfer_payload is not None and not direct_imam:
                age_days = float(
                    transfer_payload.get("anchor_age_seconds") or 0.0
                ) / 86_400.0
                age_extra = min(0.006, max(0.0, age_days - 1.0) * 0.00025)
            base_tolerance = min(
                0.03,
                max(0.0025, base_qhat + signal_extra + age_extra),
            )
            directional_extra = min(
                0.004,
                abs(direction_score)
                * 0.004
                * (0.5 + 0.5 * regime_confidence),
            )
            negative = base_tolerance + (
                directional_extra if direction_score < 0 else 0.0
            )
            positive = base_tolerance + (
                directional_extra if direction_score > 0 else 0.0
            )
            rounded = _round_toman(estimate)
            lower = _round_toman(estimate * (1.0 - negative))
            upper = _round_toman(estimate * (1.0 + positive))
            lower = min(lower, rounded)
            upper = max(upper, rounded)
            rate.update(
                {
                    "status": "ESTIMATED",
                    "llm_value": int(round(rounded / PRICE_MULTIPLIER)),
                    "estimated_price_toman": rounded,
                    "estimated_project_price": int(
                        round(rounded / PRICE_MULTIPLIER)
                    ),
                    "tolerance": {
                        "lower_price_toman": lower,
                        "upper_price_toman": upper,
                        "lower_project_price": int(
                            round(lower / PRICE_MULTIPLIER)
                        ),
                        "upper_project_price": int(
                            round(upper / PRICE_MULTIPLIER)
                        ),
                        "negative_tolerance_percent": (
                            (rounded - lower) / rounded * 100
                        ),
                        "positive_tolerance_percent": (
                            (upper - rounded) / rounded * 100
                        ),
                        "base_calibrated_percent": base_qhat * 100,
                        "signal_uncertainty_extra_percent": signal_extra * 100,
                        "anchor_age_extra_percent": age_extra * 100,
                        "directional_extra_percent": directional_extra * 100,
                        "bias": (
                            "POSITIVE"
                            if direction_score > 0.05
                            else (
                                "NEGATIVE"
                                if direction_score < -0.05
                                else "NEUTRAL"
                            )
                        ),
                    },
                    "confidence": (
                        "HIGH_DIRECT_MARKET_REFERENCE"
                        if direct_imam and settlement == "CASH"
                        else (
                            "HIGH_DIRECT_MARKET_REFERENCE"
                            if direct_imam
                            else "ANCHOR_TRANSFER"
                        )
                    ),
                    "method": method,
                    "coin_anchor_transfer": {
                        "anchor": anchor_payload,
                        "transfer": transfer_payload,
                        "current_context_status": context.status,
                        "current_melted_source": context.melted.source,
                        "current_generic_imam_source": (
                            context.generic_imam.source
                        ),
                        "cross_signal_disagreement_relative": (
                            context.cross_signal_disagreement_relative
                        ),
                        "market_regime": regime_name,
                        "market_direction_score": direction_score,
                        "live_coin_anchor_used": False,
                    },
                }
            )
    return output
