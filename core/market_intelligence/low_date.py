"""Physical melted-gold anchor for low-date coin families.

Only explicit PHYSICAL melted-gold observations are primary. A bounded paper
delta bridge is visible, freshness-limited, and never relabelled as physical.
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

LOW_DATE_COEFFICIENTS = {
    "بهار": 2.253,
    "نیم تاریخ پایین": 2.253 / 2.0,
    "ربع تاریخ پایین": 2.253 / 4.0,
}
PHYSICAL_MARKET_LABELS = ("آبشده نقدی", "آبشده رسمی")
PAPER_BRIDGE_LABELS = (
    "آبشده حواله",
    "آبشده غیررسمی",
    "آبشده امروزی",
)
EXPECTED_PRICE_UNIT = "IRT_PER_MESGHAL_750"


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
        raise ValueError("quantile needs values")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True, slots=True)
class PhysicalMeltedReference:
    status: str
    average_price_toman: float | None
    lower_price_toman: float | None
    upper_price_toman: float | None
    sample_count: int
    last_event_utc: str | None
    age_seconds: float | None
    trade_form: str
    price_unit: str
    selection: str
    maximum_age_seconds: int
    reason: str | None = None
    physical_base_price_toman: float | None = None
    physical_base_event_utc: str | None = None
    bridge_market_label: str | None = None
    bridge_paper_then_toman: float | None = None
    bridge_paper_now_toman: float | None = None
    bridge_delta_toman: float | None = None
    bridge_current_age_seconds: float | None = None
    bridge_horizon_seconds: float | None = None
    uncertainty_relative: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def no_reference(
    reason: str,
    *,
    maximum_age_seconds: int,
    last_event_utc: str | None = None,
    age_seconds: float | None = None,
) -> PhysicalMeltedReference:
    return PhysicalMeltedReference(
        status="NO_DATA",
        average_price_toman=None,
        lower_price_toman=None,
        upper_price_toman=None,
        sample_count=0,
        last_event_utc=last_event_utc,
        age_seconds=age_seconds,
        trade_form="PHYSICAL",
        price_unit=EXPECTED_PRICE_UNIT,
        selection="EXPLICIT_PHYSICAL_MELTED_ONLY",
        maximum_age_seconds=maximum_age_seconds,
        reason=reason,
    )


def _robust_window(
    connection: sqlite3.Connection,
    *,
    trade_form: str,
    market_label: str,
    end: datetime,
    seconds: int,
) -> dict[str, Any] | None:
    start = end - timedelta(seconds=seconds)
    rows = connection.execute(
        """
        SELECT event_time_utc, price_num
        FROM price_events
        WHERE instrument='MELTED_GOLD'
          AND trade_form=?
          AND market_label=?
          AND price_unit=?
          AND event_time_utc>=?
          AND event_time_utc<=?
        ORDER BY event_time_utc, id
        """,
        (
            trade_form,
            market_label,
            EXPECTED_PRICE_UNIT,
            iso_utc(start),
            iso_utc(end),
        ),
    ).fetchall()
    values = [
        float(row["price_num"])
        for row in rows
        if row["price_num"] is not None and float(row["price_num"]) > 0
    ]
    if not values:
        return None
    median = float(statistics.median(values))
    accepted_rows = [
        row
        for row in rows
        if row["price_num"] is not None
        and float(row["price_num"]) > 0
        and abs(float(row["price_num"]) - median) / median <= 0.03
    ]
    if not accepted_rows:
        return None
    accepted = [float(row["price_num"]) for row in accepted_rows]
    return {
        "median": float(statistics.median(accepted)),
        "lower": quantile(accepted, 0.10),
        "upper": quantile(accepted, 0.90),
        "sample_count": len(accepted),
        "last_event_utc": str(accepted_rows[-1]["event_time_utc"]),
    }


def read_physical_melted_reference(
    database_path: Path,
    *,
    as_of: datetime | str,
    maximum_age_seconds: int = 900,
    averaging_window_seconds: int = 300,
    enable_paper_delta_bridge: bool = True,
    maximum_bridge_age_seconds: int = 345_600,
    paper_bridge_window_seconds: int = 300,
    maximum_current_paper_age_seconds: int = 120,
    minimum_paper_samples_per_endpoint: int = 3,
    maximum_bridge_delta_relative: float = 0.25,
) -> PhysicalMeltedReference:
    """Read a robust, strictly-prior physical melted reference."""
    observed_at = parse_time(as_of)
    if (
        maximum_age_seconds <= 0
        or averaging_window_seconds <= 0
        or maximum_bridge_age_seconds < maximum_age_seconds
        or paper_bridge_window_seconds <= 0
        or maximum_current_paper_age_seconds <= 0
        or minimum_paper_samples_per_endpoint <= 0
        or not 0 < maximum_bridge_delta_relative <= 1
    ):
        raise ValueError("reference windows must be positive")
    uri = f"file:{database_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """
            SELECT event_time_utc, price_num
            FROM price_events
            WHERE instrument='MELTED_GOLD'
              AND trade_form='PHYSICAL'
              AND market_label IN (?, ?)
              AND price_unit=?
              AND event_time_utc<=?
            ORDER BY event_time_utc DESC, id DESC
            LIMIT 1
            """,
            (
                *PHYSICAL_MARKET_LABELS,
                EXPECTED_PRICE_UNIT,
                iso_utc(observed_at),
            ),
        ).fetchone()
        if latest is None:
            return no_reference(
                "NO_EXPLICIT_PHYSICAL_MELTED_QUOTE",
                maximum_age_seconds=maximum_age_seconds,
            )
        latest_at = parse_time(str(latest["event_time_utc"]))
        age = max(0.0, (observed_at - latest_at).total_seconds())
        physical_windows = [
            _robust_window(
                connection,
                trade_form="PHYSICAL",
                market_label=label,
                end=latest_at,
                seconds=averaging_window_seconds,
            )
            for label in PHYSICAL_MARKET_LABELS
        ]
        physical_windows = [
            window for window in physical_windows if window is not None
        ]
        if not physical_windows:
            return no_reference(
                "PHYSICAL_MELTED_WINDOW_EMPTY",
                maximum_age_seconds=maximum_age_seconds,
                last_event_utc=iso_utc(latest_at),
                age_seconds=age,
            )
        physical_values = [
            float(window["median"]) for window in physical_windows
        ]
        physical_center = float(statistics.median(physical_values))
        physical_lower = min(
            float(window["lower"]) for window in physical_windows
        )
        physical_upper = max(
            float(window["upper"]) for window in physical_windows
        )
        physical_samples = sum(
            int(window["sample_count"]) for window in physical_windows
        )
        if age <= maximum_age_seconds:
            return PhysicalMeltedReference(
                status="OBSERVED",
                average_price_toman=physical_center,
                lower_price_toman=physical_lower,
                upper_price_toman=physical_upper,
                sample_count=physical_samples,
                last_event_utc=iso_utc(latest_at),
                age_seconds=age,
                trade_form="PHYSICAL",
                price_unit=EXPECTED_PRICE_UNIT,
                selection="EXPLICIT_PHYSICAL_MELTED_ROBUST_5M",
                maximum_age_seconds=maximum_age_seconds,
                reason=None,
                physical_base_price_toman=physical_center,
                physical_base_event_utc=iso_utc(latest_at),
            )
        if not enable_paper_delta_bridge:
            return no_reference(
                "EXPLICIT_PHYSICAL_MELTED_QUOTE_STALE",
                maximum_age_seconds=maximum_age_seconds,
                last_event_utc=iso_utc(latest_at),
                age_seconds=age,
            )
        if age > maximum_bridge_age_seconds:
            return no_reference(
                "PHYSICAL_BASE_EXCEEDS_PAPER_BRIDGE_HORIZON",
                maximum_age_seconds=maximum_age_seconds,
                last_event_utc=iso_utc(latest_at),
                age_seconds=age,
            )
        for label in PAPER_BRIDGE_LABELS:
            then_window = _robust_window(
                connection,
                trade_form="PAPER",
                market_label=label,
                end=latest_at,
                seconds=paper_bridge_window_seconds,
            )
            now_window = _robust_window(
                connection,
                trade_form="PAPER",
                market_label=label,
                end=observed_at,
                seconds=paper_bridge_window_seconds,
            )
            if then_window is None or now_window is None:
                continue
            if (
                int(then_window["sample_count"])
                < minimum_paper_samples_per_endpoint
                or int(now_window["sample_count"])
                < minimum_paper_samples_per_endpoint
            ):
                continue
            current_paper_at = parse_time(
                str(now_window["last_event_utc"])
            )
            current_paper_age = max(
                0.0, (observed_at - current_paper_at).total_seconds()
            )
            if current_paper_age > maximum_current_paper_age_seconds:
                continue
            paper_then = float(then_window["median"])
            paper_now = float(now_window["median"])
            if abs(paper_then - physical_center) / physical_center > 0.08:
                continue
            delta = paper_now - paper_then
            if abs(delta) / physical_center > maximum_bridge_delta_relative:
                continue
            bridged_center = physical_center + delta
            if bridged_center <= 0:
                continue
            bridged_lower = physical_lower + (
                float(now_window["lower"]) - float(then_window["upper"])
            )
            bridged_upper = physical_upper + (
                float(now_window["upper"]) - float(then_window["lower"])
            )
            if min(bridged_lower, bridged_upper) <= 0:
                continue
            if bridged_lower > bridged_upper:
                bridged_lower, bridged_upper = bridged_upper, bridged_lower
            endpoint_spread = max(
                (
                    float(then_window["upper"])
                    - float(then_window["lower"])
                )
                / paper_then,
                (
                    float(now_window["upper"])
                    - float(now_window["lower"])
                )
                / paper_now,
            )
            age_uncertainty = min(
                0.01,
                max(0.0, age - maximum_age_seconds)
                / maximum_bridge_age_seconds
                * 0.01,
            )
            uncertainty = min(
                0.03,
                0.0025 + endpoint_spread + age_uncertainty,
            )
            return PhysicalMeltedReference(
                status="BRIDGED",
                average_price_toman=bridged_center,
                lower_price_toman=bridged_lower,
                upper_price_toman=bridged_upper,
                sample_count=int(now_window["sample_count"]),
                last_event_utc=str(now_window["last_event_utc"]),
                age_seconds=age,
                trade_form="PHYSICAL",
                price_unit=EXPECTED_PRICE_UNIT,
                selection="PHYSICAL_BASE_PLUS_SAME_PAPER_LABEL_DELTA",
                maximum_age_seconds=maximum_age_seconds,
                reason=None,
                physical_base_price_toman=physical_center,
                physical_base_event_utc=iso_utc(latest_at),
                bridge_market_label=label,
                bridge_paper_then_toman=paper_then,
                bridge_paper_now_toman=paper_now,
                bridge_delta_toman=delta,
                bridge_current_age_seconds=current_paper_age,
                bridge_horizon_seconds=age,
                uncertainty_relative=uncertainty,
            )
        return no_reference(
            "NO_SAFE_SAME_LABEL_PAPER_DELTA_BRIDGE",
            maximum_age_seconds=maximum_age_seconds,
            last_event_utc=iso_utc(latest_at),
            age_seconds=age,
        )
    finally:
        connection.close()


def _trusted_live_coin_anchor(rate: Mapping[str, Any]) -> bool:
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


def _round_toman(value: float) -> int:
    return int(round(float(value) / 50_000.0) * 50_000)


def apply_low_date_intrinsic_overlay(
    state: Mapping[str, Any],
    reference: PhysicalMeltedReference | Mapping[str, Any],
    *,
    fallback_relative_tolerance: float = 0.006,
    maximum_regime_extra: float = 0.012,
    maximum_signal_disagreement_extra: float = 0.006,
    maximum_directional_skew: float = 0.006,
    maximum_total_tolerance: float = 0.04,
) -> dict[str, Any]:
    """Return a copied state with formula-based low-date bands when eligible."""
    if not 0 < fallback_relative_tolerance <= 0.05:
        raise ValueError("fallback_relative_tolerance must be in (0, 0.05]")
    for name, value in (
        ("maximum_regime_extra", maximum_regime_extra),
        (
            "maximum_signal_disagreement_extra",
            maximum_signal_disagreement_extra,
        ),
        ("maximum_directional_skew", maximum_directional_skew),
        ("maximum_total_tolerance", maximum_total_tolerance),
    ):
        if not 0 <= value <= 0.05:
            raise ValueError(f"{name} must be in [0, 0.05]")
    reference_payload = (
        reference.to_dict()
        if isinstance(reference, PhysicalMeltedReference)
        else dict(reference)
    )
    output = deepcopy(dict(state))
    output["low_date_physical_reference"] = reference_payload
    if str(reference_payload.get("status")) not in {"OBSERVED", "BRIDGED"}:
        return output
    if str(reference_payload.get("trade_form")) != "PHYSICAL":
        raise ValueError("low-date overlay rejects non-physical melted references")
    if str(reference_payload.get("price_unit")) != EXPECTED_PRICE_UNIT:
        raise ValueError("low-date overlay received an incompatible price unit")

    average = float(reference_payload["average_price_toman"])
    lower_melted = float(reference_payload["lower_price_toman"])
    upper_melted = float(reference_payload["upper_price_toman"])
    if min(average, lower_melted, upper_melted) <= 0:
        raise ValueError("low-date physical reference must be positive")

    for payload in (output.get("settlements") or {}).values():
        if not isinstance(payload, dict):
            continue
        for rate in payload.get("rates") or []:
            if not isinstance(rate, dict):
                continue
            commodity = str(rate.get("commodity_name") or "")
            coefficient = LOW_DATE_COEFFICIENTS.get(commodity)
            if coefficient is None or _trusted_live_coin_anchor(rate):
                continue
            regime = payload.get("market_regime") or {}
            regime_name = str(regime.get("regime") or "UNKNOWN")
            regime_confidence = float(regime.get("confidence") or 0.0)
            regime_volatility = float(
                regime.get("volatility_percent") or 0.0
            ) / 100.0
            regime_extra = min(
                maximum_regime_extra,
                regime_volatility * (1.0 + regime_confidence),
            )
            if regime_name == "SHOCK":
                regime_extra = max(regime_extra, 0.003)
            component_weights = regime.get(
                "component_weights_normalized"
            ) or {}
            supporting_signals: list[dict[str, Any]] = []
            weighted_directions: list[tuple[float, float]] = []
            for component in regime.get("components") or []:
                if not isinstance(component, Mapping):
                    continue
                name = str(component.get("name") or "")
                if not name:
                    continue
                direction = float(
                    component.get("direction_strength") or 0.0
                )
                weight = max(
                    0.0,
                    float(component_weights.get(name) or 0.0),
                )
                weighted_directions.append((direction, weight))
                supporting_signals.append(
                    {
                        "name": name,
                        "return_percent": float(
                            component.get("return_percent") or 0.0
                        ),
                        "direction_strength": direction,
                        "reliability": float(
                            component.get("reliability") or 0.0
                        ),
                        "normalized_regime_weight": weight,
                    }
                )
            total_signal_weight = sum(
                weight for _, weight in weighted_directions
            )
            if total_signal_weight > 0:
                weighted_direction = sum(
                    direction * weight
                    for direction, weight in weighted_directions
                ) / total_signal_weight
                direction_dispersion = math.sqrt(
                    sum(
                        weight * (direction - weighted_direction) ** 2
                        for direction, weight in weighted_directions
                    )
                    / total_signal_weight
                )
            else:
                weighted_direction = 0.0
                direction_dispersion = 0.0
            signal_disagreement_extra = min(
                maximum_signal_disagreement_extra,
                direction_dispersion * 0.004,
            )
            reference_uncertainty = float(
                reference_payload.get("uncertainty_relative") or 0.0
            )
            base_tolerance = min(
                maximum_total_tolerance,
                fallback_relative_tolerance
                + reference_uncertainty
                + regime_extra
                + signal_disagreement_extra,
            )
            direction_score = float(
                regime.get("direction_score") or weighted_direction
            )
            directional_skew = min(
                maximum_directional_skew,
                abs(direction_score)
                * maximum_directional_skew
                * (0.5 + 0.5 * regime_confidence),
            )
            negative_tolerance = min(
                maximum_total_tolerance,
                base_tolerance
                + (directional_skew if direction_score < 0 else 0.0),
            )
            positive_tolerance = min(
                maximum_total_tolerance,
                base_tolerance
                + (directional_skew if direction_score > 0 else 0.0),
            )
            center_toman = _round_toman(average * coefficient)
            lower_toman = _round_toman(
                lower_melted
                * coefficient
                * (1.0 - negative_tolerance)
            )
            upper_toman = _round_toman(
                upper_melted
                * coefficient
                * (1.0 + positive_tolerance)
            )
            lower_toman = min(lower_toman, center_toman)
            upper_toman = max(upper_toman, center_toman)
            rate.update(
                {
                    "status": "ESTIMATED",
                    "llm_value": int(round(center_toman / 1000.0)),
                    "intrinsic_toman": center_toman,
                    "bubble_ratio": 0.0,
                    "estimated_price_toman": center_toman,
                    "estimated_project_price": int(
                        round(center_toman / 1000.0)
                    ),
                    "tolerance": {
                        "lower_price_toman": lower_toman,
                        "upper_price_toman": upper_toman,
                        "lower_project_price": int(round(lower_toman / 1000.0)),
                        "upper_project_price": int(round(upper_toman / 1000.0)),
                        "negative_tolerance_percent": (
                            (center_toman - lower_toman) / center_toman * 100.0
                        ),
                        "positive_tolerance_percent": (
                            (upper_toman - center_toman) / center_toman * 100.0
                        ),
                        "bias": "LOW_DATE_PHYSICAL_INTRINSIC",
                    },
                    "confidence": (
                        "INTRINSIC_PHYSICAL_ANCHOR"
                        if reference_payload.get("status") == "OBSERVED"
                        else "INTRINSIC_BRIDGED_PHYSICAL_ANCHOR"
                    ),
                    "method": (
                        "LOW_DATE_PHYSICAL_MELTED_INTRINSIC_FALLBACK"
                        if reference_payload.get("status") == "OBSERVED"
                        else "LOW_DATE_PHYSICAL_BASE_PLUS_PAPER_DELTA_FALLBACK"
                    ),
                    "low_date_intrinsic_anchor": {
                        "formula": f"MELTED_PHYSICAL*{coefficient:.9f}",
                        "melted_average_toman": average,
                        "melted_sample_count": int(
                            reference_payload.get("sample_count") or 0
                        ),
                        "melted_last_event_utc": reference_payload.get(
                            "last_event_utc"
                        ),
                        "reference_status": reference_payload.get("status"),
                        "reference_selection": reference_payload.get(
                            "selection"
                        ),
                        "reference_uncertainty_relative": reference_uncertainty,
                        "market_regime": regime_name,
                        "market_regime_confidence": regime_confidence,
                        "market_direction_score": direction_score,
                        "market_regime_tolerance_extra": regime_extra,
                        "signal_direction_dispersion": direction_dispersion,
                        "signal_disagreement_tolerance_extra": (
                            signal_disagreement_extra
                        ),
                        "directional_tolerance_skew": directional_skew,
                        "negative_tolerance_relative": negative_tolerance,
                        "positive_tolerance_relative": positive_tolerance,
                        "total_tolerance_relative": max(
                            negative_tolerance,
                            positive_tolerance,
                        ),
                        "supporting_signals": supporting_signals,
                        "supporting_signal_role": (
                            "REGIME_DIRECTION_AND_INTERVAL_UNCERTAINTY;"
                            "NOT_A_SECOND_DIRECT_PRICE_MULTIPLIER"
                        ),
                        "live_coin_anchor_used": False,
                    },
                }
            )
    return output
