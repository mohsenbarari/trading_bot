#!/usr/bin/env python3
"""Conservative price-to-commodity ranking for the shadow coin estimator.

The ranker does not estimate market prices.  It consumes a versioned snapshot
of already-estimated commodity ranges and decides whether a normalized offer
price identifies exactly one commodity with enough evidence.  Ambiguity is an
expected result and must be surfaced to the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SUPPORTED_PRICE_UNITS = {
    "PROJECT_THOUSAND_TOMAN",
    "TOMAN_FULL",
}

SUPPORTED_SETTLEMENTS = {"CASH", "TOMORROW"}
SUPPORTED_TRADE_FORMS = {"PHYSICAL", "PAPER"}


def parse_time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AmbiguityZone:
    commodities: tuple[str, ...]
    lower_price: int
    upper_price: int
    reason: str


@dataclass(frozen=True, slots=True)
class FamilyBoundary:
    low_date_commodity: str
    premium_commodity: str
    reason: str


@dataclass(frozen=True, slots=True)
class RankerPolicy:
    policy_version: str
    minimum_auto_probability: float
    minimum_auto_probability_margin: float
    minimum_auto_decision_confidence: float
    maximum_auto_relative_outside: float
    maximum_candidate_relative_outside: float
    maximum_snapshot_age_seconds: int
    overlap_requires_confirmation: bool
    softmax_temperature: float
    center_distance_weight: float
    outside_distance_weight: float
    reliability_penalty_weight: float
    minimum_scale_relative: float
    minimum_scale_absolute: int
    confidence_reliability: Mapping[str, float]
    hard_ambiguity_zones: tuple[AmbiguityZone, ...]
    dynamic_family_boundaries_enabled: bool
    dynamic_family_requires_physical_anchor: bool
    dynamic_family_guard_relative: float
    dynamic_family_guard_absolute: int
    dynamic_family_boundaries: tuple[FamilyBoundary, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RankerPolicy":
        if int(payload.get("schema_version") or 0) != 1:
            raise ValueError("unsupported commodity-ranker policy schema")
        automatic = payload["automatic_decision"]
        scoring = payload["scoring"]
        confidence = payload["confidence_reliability"]
        zones = []
        for row in payload.get("hard_price_ambiguity_zones") or ():
            zones.append(
                AmbiguityZone(
                    commodities=tuple(str(item) for item in row["commodities"]),
                    lower_price=int(row["lower_project_price"]),
                    upper_price=int(row["upper_project_price"]),
                    reason=str(row["reason"]),
                )
            )
        dynamic = payload.get("dynamic_family_boundaries") or {}
        family_boundaries = []
        for row in dynamic.get("pairs") or ():
            family_boundaries.append(
                FamilyBoundary(
                    low_date_commodity=str(row["low_date_commodity"]),
                    premium_commodity=str(row["premium_commodity"]),
                    reason=str(row["reason"]),
                )
            )
        policy = cls(
            policy_version=str(payload["policy_version"]),
            minimum_auto_probability=float(automatic["minimum_probability"]),
            minimum_auto_probability_margin=float(
                automatic["minimum_probability_margin"]
            ),
            minimum_auto_decision_confidence=float(
                automatic["minimum_decision_confidence"]
            ),
            maximum_auto_relative_outside=float(
                automatic["maximum_relative_outside"]
            ),
            maximum_candidate_relative_outside=float(
                automatic["maximum_candidate_relative_outside"]
            ),
            maximum_snapshot_age_seconds=int(
                automatic["maximum_snapshot_age_seconds"]
            ),
            overlap_requires_confirmation=bool(
                automatic["overlap_requires_confirmation"]
            ),
            softmax_temperature=float(scoring["softmax_temperature"]),
            center_distance_weight=float(scoring["center_distance_weight"]),
            outside_distance_weight=float(scoring["outside_distance_weight"]),
            reliability_penalty_weight=float(
                scoring["reliability_penalty_weight"]
            ),
            minimum_scale_relative=float(scoring["minimum_scale_relative"]),
            minimum_scale_absolute=int(scoring["minimum_scale_absolute"]),
            confidence_reliability={
                str(key): float(value) for key, value in confidence.items()
            },
            hard_ambiguity_zones=tuple(zones),
            dynamic_family_boundaries_enabled=bool(
                dynamic.get("enabled", False)
            ),
            dynamic_family_requires_physical_anchor=bool(
                dynamic.get("require_low_date_physical_anchor", True)
            ),
            dynamic_family_guard_relative=float(
                dynamic.get("guard_band_relative", 0.003)
            ),
            dynamic_family_guard_absolute=int(
                dynamic.get("guard_band_absolute", 250)
            ),
            dynamic_family_boundaries=tuple(family_boundaries),
        )
        policy.validate()
        return policy

    @classmethod
    def from_path(cls, path: Path) -> "RankerPolicy":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("commodity-ranker policy must be an object")
        return cls.from_mapping(value)

    def validate(self) -> None:
        probabilities = (
            self.minimum_auto_probability,
            self.minimum_auto_probability_margin,
            self.minimum_auto_decision_confidence,
        )
        if any(not 0 <= value <= 1 for value in probabilities):
            raise ValueError("decision probability thresholds must be in [0, 1]")
        if not 0 <= self.maximum_auto_relative_outside <= 1:
            raise ValueError("maximum_auto_relative_outside must be in [0, 1]")
        if (
            self.maximum_candidate_relative_outside
            < self.maximum_auto_relative_outside
        ):
            raise ValueError("candidate distance cannot be stricter than auto distance")
        if self.maximum_snapshot_age_seconds <= 0:
            raise ValueError("maximum_snapshot_age_seconds must be positive")
        if self.softmax_temperature <= 0:
            raise ValueError("softmax_temperature must be positive")
        if self.minimum_scale_relative <= 0 or self.minimum_scale_absolute <= 0:
            raise ValueError("distance scales must be positive")
        for zone in self.hard_ambiguity_zones:
            if len(zone.commodities) < 2:
                raise ValueError("ambiguity zones need at least two commodities")
            if zone.lower_price <= 0 or zone.lower_price > zone.upper_price:
                raise ValueError("invalid ambiguity-zone price range")
        if not 0 < self.dynamic_family_guard_relative <= 0.05:
            raise ValueError("dynamic family guard relative must be in (0, 0.05]")
        if self.dynamic_family_guard_absolute <= 0:
            raise ValueError("dynamic family guard absolute must be positive")
        for boundary in self.dynamic_family_boundaries:
            if (
                not boundary.low_date_commodity
                or not boundary.premium_commodity
                or boundary.low_date_commodity == boundary.premium_commodity
            ):
                raise ValueError("invalid dynamic family boundary")

    def with_decision_thresholds(
        self,
        *,
        minimum_probability: float,
        minimum_probability_margin: float,
        maximum_relative_outside: float,
    ) -> "RankerPolicy":
        policy = replace(
            self,
            minimum_auto_probability=float(minimum_probability),
            minimum_auto_probability_margin=float(
                minimum_probability_margin
            ),
            maximum_auto_relative_outside=float(maximum_relative_outside),
        )
        policy.validate()
        return policy


@dataclass(frozen=True, slots=True)
class RateBand:
    commodity: str
    center: int
    lower: int
    upper: int
    confidence_label: str
    reliability: float
    sample_count: int | None
    method: str | None


@dataclass(frozen=True, slots=True)
class RankedCommodity:
    commodity: str
    probability: float
    decision_confidence: float
    cost: float
    center_price: int
    lower_price: int
    upper_price: int
    inside_range: bool
    center_distance: int
    outside_distance: int
    relative_outside: float
    range_reliability: float
    range_confidence_label: str
    sample_count: int | None


@dataclass(frozen=True, slots=True)
class CommodityInferenceResult:
    schema_version: int
    policy_version: str
    status: str
    normalized_price: int | None
    price_unit: str
    inferred_commodity: str | None
    commodity_confidence: float
    alternative_commodities: tuple[str, ...]
    candidates: tuple[RankedCommodity, ...]
    estimated_range: tuple[int, int] | None
    settlement: str
    trade_form: str
    model_version: str | None
    feature_schema_version: str | None
    input_snapshot_version: str | None
    input_freshness_seconds: float | None
    warnings: tuple[str, ...]
    requires_user_confirmation: bool
    decision_reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidates"] = [asdict(item) for item in self.candidates]
        value["alternative_commodities"] = list(self.alternative_commodities)
        value["warnings"] = list(self.warnings)
        if self.estimated_range is not None:
            value["estimated_range"] = list(self.estimated_range)
        return value


def project_price(value: int | float, unit: str) -> int:
    if unit not in SUPPORTED_PRICE_UNITS:
        raise ValueError(f"unsupported price unit: {unit}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("price must be a positive finite number")
    if unit == "TOMAN_FULL":
        numeric /= 1000.0
    normalized = int(round(numeric))
    if normalized <= 0:
        raise ValueError("normalized project price must be positive")
    return normalized


def _softmax_costs(costs: Sequence[float], temperature: float) -> list[float]:
    if not costs:
        return []
    logits = [-float(cost) / temperature for cost in costs]
    maximum = max(logits)
    exponentials = [math.exp(value - maximum) for value in logits]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _band_from_rate(
    rate: Mapping[str, Any],
    reliability_map: Mapping[str, float],
) -> RateBand | None:
    if str(rate.get("status")) not in {
        "ESTIMATED",
        "OBSERVED",
        "OBSERVED_ANCHOR",
    }:
        return None
    commodity = str(rate.get("commodity_name") or "").strip()
    if not commodity:
        return None
    center_raw = rate.get("estimated_project_price")
    if center_raw is None:
        center_raw = rate.get("center_project_price")
    if center_raw is None:
        return None
    center = int(round(float(center_raw)))
    tolerance = rate.get("tolerance") or {}
    lower_raw = tolerance.get("lower_project_price")
    upper_raw = tolerance.get("upper_project_price")
    if lower_raw is None:
        lower_raw = rate.get("lower_project_price")
    if upper_raw is None:
        upper_raw = rate.get("upper_project_price")
    if lower_raw is None or upper_raw is None:
        half_width = max(250, int(round(center * 0.02)))
        lower = center - half_width
        upper = center + half_width
    else:
        lower = int(round(float(lower_raw)))
        upper = int(round(float(upper_raw)))
    if center <= 0 or lower <= 0 or upper <= 0:
        return None
    if lower > upper:
        lower, upper = upper, lower
    lower = min(lower, center)
    upper = max(upper, center)
    label = str(rate.get("confidence") or "NONE")
    reliability = float(reliability_map.get(label, 0.25))
    sample_raw = rate.get("training_sample_count")
    return RateBand(
        commodity=commodity,
        center=center,
        lower=lower,
        upper=upper,
        confidence_label=label,
        reliability=max(0.0, min(1.0, reliability)),
        sample_count=int(sample_raw) if sample_raw is not None else None,
        method=str(rate.get("method")) if rate.get("method") is not None else None,
    )


def estimator_state_snapshot(
    state: Mapping[str, Any],
    *,
    settlement: str,
    trade_form: str = "PHYSICAL",
    model_version: str | None = None,
    feature_schema_version: str = "LEGACY_ESTIMATOR_STATE_V1",
    snapshot_version: str | None = None,
) -> dict[str, Any]:
    if settlement not in SUPPORTED_SETTLEMENTS:
        raise ValueError(f"unsupported settlement: {settlement}")
    if trade_form != "PHYSICAL":
        raise ValueError(
            "legacy estimator state exposes only the physical coin-rate view"
        )
    settlements = state.get("settlements") or {}
    payload = settlements.get(settlement)
    if not isinstance(payload, Mapping):
        rates: list[Mapping[str, Any]] = []
    else:
        rates = list(payload.get("rates") or [])
    generated = str(state.get("generated_at_utc") or "")
    version = snapshot_version or (
        f"legacy-{generated}-{settlement}-{trade_form}"
    )
    return {
        "schema_version": 1,
        "generated_at_utc": generated,
        "settlement": settlement,
        "trade_form": trade_form,
        "model_version": model_version,
        "feature_schema_version": feature_schema_version,
        "snapshot_version": version,
        "rates": rates,
        "low_date_physical_reference": state.get(
            "low_date_physical_reference"
        ),
    }


def _dynamic_family_choice(
    normalized_price: int,
    bands: Sequence[RateBand],
    *,
    policy: RankerPolicy,
    snapshot: Mapping[str, Any],
    bypass_for_explicit_constraint: bool,
) -> tuple[FamilyBoundary | None, str | None, bool, int | None, int | None]:
    """Return (pair, selected family, forced ambiguity, boundary, guard)."""
    if (
        not policy.dynamic_family_boundaries_enabled
        or bypass_for_explicit_constraint
    ):
        return None, None, False, None, None
    reference = snapshot.get("low_date_physical_reference") or {}
    has_physical_anchor = (
        str(reference.get("status")) in {"OBSERVED", "BRIDGED"}
        and str(reference.get("trade_form")) == "PHYSICAL"
    )
    if policy.dynamic_family_requires_physical_anchor and not has_physical_anchor:
        return None, None, False, None, None

    by_name = {band.commodity: band for band in bands}
    matches: list[
        tuple[float, FamilyBoundary, RateBand, RateBand, int, int]
    ] = []
    for pair in policy.dynamic_family_boundaries:
        low_date = by_name.get(pair.low_date_commodity)
        premium = by_name.get(pair.premium_commodity)
        if low_date is None or premium is None:
            continue
        # A family boundary is meaningful only while the low-date intrinsic
        # center remains below its premium sibling.
        if low_date.center >= premium.center:
            continue
        envelope_lower = int(
            min(low_date.lower, low_date.center)
            * (1.0 - policy.maximum_candidate_relative_outside)
        )
        envelope_upper = int(
            max(premium.upper, premium.center)
            * (1.0 + policy.maximum_candidate_relative_outside)
        )
        if not envelope_lower <= normalized_price <= envelope_upper:
            continue
        boundary_price = int(round((low_date.center + premium.center) / 2.0))
        guard = max(
            policy.dynamic_family_guard_absolute,
            int(
                round(
                    boundary_price
                    * (
                        policy.dynamic_family_guard_relative
                        + min(
                            0.03,
                            max(
                                0.0,
                                float(
                                    reference.get(
                                        "uncertainty_relative", 0.0
                                    )
                                    or 0.0
                                ),
                            ),
                        )
                    )
                )
            ),
        )
        relative_distance = abs(normalized_price - boundary_price) / max(
            1.0, boundary_price
        )
        matches.append(
            (
                relative_distance,
                pair,
                low_date,
                premium,
                boundary_price,
                guard,
            )
        )
    if not matches:
        return None, None, False, None, None
    _, pair, low_date, premium, boundary_price, guard = min(
        matches,
        key=lambda row: row[0],
    )
    if normalized_price < boundary_price - guard:
        return (
            pair,
            low_date.commodity,
            False,
            boundary_price,
            guard,
        )
    if normalized_price > boundary_price + guard:
        return (
            pair,
            premium.commodity,
            False,
            boundary_price,
            guard,
        )
    return pair, None, True, boundary_price, guard


class CommodityRanker:
    def __init__(self, policy: RankerPolicy) -> None:
        self.policy = policy

    def _empty(
        self,
        *,
        status: str,
        normalized_price: int | None,
        price_unit: str,
        settlement: str,
        trade_form: str,
        snapshot: Mapping[str, Any] | None,
        warnings: Iterable[str],
        reason: str,
        freshness: float | None = None,
    ) -> CommodityInferenceResult:
        source = snapshot or {}
        return CommodityInferenceResult(
            schema_version=1,
            policy_version=self.policy.policy_version,
            status=status,
            normalized_price=normalized_price,
            price_unit=price_unit,
            inferred_commodity=None,
            commodity_confidence=0.0,
            alternative_commodities=(),
            candidates=(),
            estimated_range=None,
            settlement=settlement,
            trade_form=trade_form,
            model_version=(
                str(source.get("model_version"))
                if source.get("model_version") is not None
                else None
            ),
            feature_schema_version=(
                str(source.get("feature_schema_version"))
                if source.get("feature_schema_version") is not None
                else None
            ),
            input_snapshot_version=(
                str(source.get("snapshot_version"))
                if source.get("snapshot_version") is not None
                else None
            ),
            input_freshness_seconds=freshness,
            warnings=tuple(warnings),
            requires_user_confirmation=True,
            decision_reason=reason,
        )

    def infer(
        self,
        price: int | float,
        *,
        price_unit: str,
        settlement: str,
        trade_form: str,
        snapshot: Mapping[str, Any],
        now: datetime | str | None = None,
        allowed_commodities: Iterable[str] | None = None,
    ) -> CommodityInferenceResult:
        if settlement not in SUPPORTED_SETTLEMENTS:
            return self._empty(
                status="UNSUPPORTED_MARKET_DIMENSION",
                normalized_price=None,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("UNSUPPORTED_SETTLEMENT",),
                reason="UNSUPPORTED_SETTLEMENT",
            )
        if trade_form not in SUPPORTED_TRADE_FORMS:
            return self._empty(
                status="UNSUPPORTED_MARKET_DIMENSION",
                normalized_price=None,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("UNSUPPORTED_TRADE_FORM",),
                reason="UNSUPPORTED_TRADE_FORM",
            )
        try:
            normalized = project_price(price, price_unit)
        except (TypeError, ValueError):
            return self._empty(
                status="INVALID_PRICE",
                normalized_price=None,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("INVALID_OR_AMBIGUOUS_PRICE",),
                reason="PRICE_NORMALIZATION_REQUIRED",
            )
        if str(snapshot.get("settlement")) != settlement:
            return self._empty(
                status="UNSUPPORTED_MARKET_DIMENSION",
                normalized_price=normalized,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("SNAPSHOT_SETTLEMENT_MISMATCH",),
                reason="SNAPSHOT_SETTLEMENT_MISMATCH",
            )
        if str(snapshot.get("trade_form")) != trade_form:
            return self._empty(
                status="UNSUPPORTED_MARKET_DIMENSION",
                normalized_price=normalized,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("SNAPSHOT_TRADE_FORM_MISMATCH",),
                reason="SNAPSHOT_TRADE_FORM_MISMATCH",
            )
        generated_raw = snapshot.get("generated_at_utc")
        try:
            generated = parse_time(str(generated_raw))
        except (TypeError, ValueError):
            return self._empty(
                status="NO_MARKET_DATA",
                normalized_price=normalized,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("SNAPSHOT_TIME_MISSING",),
                reason="SNAPSHOT_TIME_MISSING",
            )
        observed_now = parse_time(now or datetime.now(timezone.utc))
        freshness = max(0.0, (observed_now - generated).total_seconds())
        stale = freshness > self.policy.maximum_snapshot_age_seconds
        allowed = (
            {str(item) for item in allowed_commodities}
            if allowed_commodities is not None
            else None
        )
        all_bands = []
        for rate in snapshot.get("rates") or ():
            if not isinstance(rate, Mapping):
                continue
            band = _band_from_rate(rate, self.policy.confidence_reliability)
            if band is not None:
                all_bands.append(band)
        dynamic_pair, dynamic_selected, dynamic_forced, _, _ = (
            _dynamic_family_choice(
                normalized,
                all_bands,
                policy=self.policy,
                snapshot=snapshot,
                bypass_for_explicit_constraint=(
                    allowed is not None and len(allowed) == 1
                ),
            )
        )
        bands = [
            band
            for band in all_bands
            if (allowed is None or band.commodity in allowed)
            and (
                dynamic_selected is None
                or band.commodity not in {
                    dynamic_pair.low_date_commodity,
                    dynamic_pair.premium_commodity,
                }
                or band.commodity == dynamic_selected
            )
        ]
        if not bands:
            return self._empty(
                status="NO_MARKET_DATA",
                normalized_price=normalized,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("NO_ESTIMATED_COMMODITY_RANGES",),
                reason="NO_ESTIMATED_COMMODITY_RANGES",
                freshness=freshness,
            )

        raw_rows: list[tuple[RateBand, float, bool, int, int, float]] = []
        for band in bands:
            half_width = max(
                float(self.policy.minimum_scale_absolute),
                band.center * self.policy.minimum_scale_relative,
                (band.upper - band.lower) / 2.0,
            )
            center_distance = abs(normalized - band.center)
            outside_distance = max(
                band.lower - normalized,
                normalized - band.upper,
                0,
            )
            relative_outside = outside_distance / max(1.0, band.center)
            if (
                relative_outside
                > self.policy.maximum_candidate_relative_outside
            ):
                continue
            inside = outside_distance == 0
            cost = (
                self.policy.center_distance_weight
                * center_distance
                / half_width
                + self.policy.outside_distance_weight
                * outside_distance
                / half_width
                + self.policy.reliability_penalty_weight
                * (1.0 - band.reliability)
            )
            raw_rows.append(
                (
                    band,
                    cost,
                    inside,
                    center_distance,
                    outside_distance,
                    relative_outside,
                )
            )
        if not raw_rows:
            return self._empty(
                status="AMBIGUOUS",
                normalized_price=normalized,
                price_unit=price_unit,
                settlement=settlement,
                trade_form=trade_form,
                snapshot=snapshot,
                warnings=("PRICE_OUTSIDE_ALL_CURRENT_RANGES",),
                reason="NO_PLAUSIBLE_COMMODITY_RANGE",
                freshness=freshness,
            )

        probabilities = _softmax_costs(
            [row[1] for row in raw_rows],
            self.policy.softmax_temperature,
        )
        ranked = []
        for row, probability in zip(raw_rows, probabilities):
            band, cost, inside, center_distance, outside_distance, relative = row
            decision_confidence = probability * (
                0.75 + 0.25 * band.reliability
            )
            ranked.append(
                RankedCommodity(
                    commodity=band.commodity,
                    probability=round(probability, 8),
                    decision_confidence=round(decision_confidence, 8),
                    cost=round(cost, 8),
                    center_price=band.center,
                    lower_price=band.lower,
                    upper_price=band.upper,
                    inside_range=inside,
                    center_distance=center_distance,
                    outside_distance=outside_distance,
                    relative_outside=round(relative, 8),
                    range_reliability=round(band.reliability, 8),
                    range_confidence_label=band.confidence_label,
                    sample_count=band.sample_count,
                )
            )
        ranked.sort(key=lambda item: (item.probability, -item.cost), reverse=True)
        best = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        margin = (
            best.probability - second.probability
            if second is not None
            else best.probability
        )
        inside_candidates = [
            item for item in ranked if item.inside_range
        ]
        overlap = len(inside_candidates) > 1
        hard_zone = next(
            (
                zone
                for zone in self.policy.hard_ambiguity_zones
                if best.commodity in zone.commodities
                and zone.lower_price <= normalized <= zone.upper_price
                and not (
                    dynamic_pair is not None
                    and set(zone.commodities)
                    == {
                        dynamic_pair.low_date_commodity,
                        dynamic_pair.premium_commodity,
                    }
                )
                and not (
                    allowed is not None
                    and len(allowed) == 1
                    and best.commodity in allowed
                )
            ),
            None,
        )
        warnings: list[str] = []
        if overlap:
            warnings.append("OVERLAPPING_COMMODITY_RANGES")
        if hard_zone is not None:
            warnings.append("DOMAIN_PRICE_OVERLAP")
        if dynamic_pair is not None:
            warnings.append("LOW_DATE_INTRINSIC_BOUNDARY_USED")
        if dynamic_forced:
            warnings.append("DYNAMIC_FAMILY_BOUNDARY_GUARD")
        if best.range_reliability < 0.5:
            warnings.append("LOW_CONFIDENCE_PRICE_RANGE")
        if stale:
            warnings.append("STALE_INPUT_SNAPSHOT")
        automatic = (
            not stale
            and best.relative_outside
            <= self.policy.maximum_auto_relative_outside
            and best.probability >= self.policy.minimum_auto_probability
            and margin >= self.policy.minimum_auto_probability_margin
            and best.decision_confidence
            >= self.policy.minimum_auto_decision_confidence
            and not (
                self.policy.overlap_requires_confirmation and overlap
            )
            and hard_zone is None
            and not dynamic_forced
        )
        if stale:
            status = "STALE_INPUT"
            reason = "SNAPSHOT_EXCEEDS_MAXIMUM_AGE"
        elif automatic:
            status = "INFERRED"
            reason = "UNIQUE_HIGH_CONFIDENCE_RANGE_MATCH"
        elif dynamic_forced and dynamic_pair is not None:
            status = "AMBIGUOUS"
            reason = dynamic_pair.reason
        elif hard_zone is not None:
            status = "AMBIGUOUS"
            reason = hard_zone.reason
        elif overlap:
            status = "AMBIGUOUS"
            reason = "MULTIPLE_RANGES_CONTAIN_PRICE"
        elif best.probability < self.policy.minimum_auto_probability:
            status = "AMBIGUOUS"
            reason = "TOP_PROBABILITY_BELOW_THRESHOLD"
        elif margin < self.policy.minimum_auto_probability_margin:
            status = "AMBIGUOUS"
            reason = "TOP_TWO_MARGIN_BELOW_THRESHOLD"
        elif (
            best.decision_confidence
            < self.policy.minimum_auto_decision_confidence
        ):
            status = "AMBIGUOUS"
            reason = "RANGE_CONFIDENCE_BELOW_THRESHOLD"
        else:
            status = "AMBIGUOUS"
            reason = "PRICE_OUTSIDE_AUTOMATIC_DISTANCE"

        alternatives = [
            item.commodity
            for item in ranked[:3]
            if item.commodity != best.commodity
        ]
        if hard_zone is not None:
            for commodity in hard_zone.commodities:
                if commodity != best.commodity and commodity not in alternatives:
                    alternatives.append(commodity)
        if dynamic_forced and dynamic_pair is not None:
            for commodity in (
                dynamic_pair.low_date_commodity,
                dynamic_pair.premium_commodity,
            ):
                if commodity != best.commodity and commodity not in alternatives:
                    alternatives.append(commodity)

        return CommodityInferenceResult(
            schema_version=1,
            policy_version=self.policy.policy_version,
            status=status,
            normalized_price=normalized,
            price_unit=price_unit,
            inferred_commodity=best.commodity if automatic else None,
            commodity_confidence=(
                best.decision_confidence if automatic else 0.0
            ),
            alternative_commodities=tuple(alternatives[:3]),
            candidates=tuple(ranked[:5]),
            estimated_range=(
                (best.lower_price, best.upper_price) if automatic else None
            ),
            settlement=settlement,
            trade_form=trade_form,
            model_version=(
                str(snapshot.get("model_version"))
                if snapshot.get("model_version") is not None
                else None
            ),
            feature_schema_version=(
                str(snapshot.get("feature_schema_version"))
                if snapshot.get("feature_schema_version") is not None
                else None
            ),
            input_snapshot_version=(
                str(snapshot.get("snapshot_version"))
                if snapshot.get("snapshot_version") is not None
                else None
            ),
            input_freshness_seconds=freshness,
            warnings=tuple(warnings),
            requires_user_confirmation=not automatic,
            decision_reason=reason,
        )


def active_model_sha(path: Path) -> str:
    return sha256_file(path)
