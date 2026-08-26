"""Causal price/context validation for first-pass coin-group offers.

This module is deliberately not an LLM and never defaults an omitted coin to
Imam.  It accepts only already-approved, privacy-minimized price anchors and
uses facts that were available strictly before the group offer arrived.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from statistics import median
from typing import Iterable

from .coin_groups import (
    COIN_GROUP_PARSER_VERSION,
    CoinGroupMessageInput,
    ParsedCoinGroupOffer,
    _PRICE_BOUNDS,
    coin_group_offer_field_evidence,
    parse_coin_group_offers,
)
from .market_contracts import MarketObservation, MarketStoreContractError, derive_event_key, normalize_utc


COIN_GROUP_RESOLUTION_VERSION = "coin-group-context-v7-near-time-range"
MINIMUM_ANCHOR_COUNT = 2
MAXIMUM_RELATIVE_DISTANCE = 0.015
MINIMUM_RUNNER_UP_MARGIN = 0.005
MAXIMUM_ANCHOR_AGE_SECONDS = 2 * 60 * 60
PREFERRED_ANCHOR_AGE_SECONDS = 5 * 60
_NormalizedAnchor = tuple[str, int, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class CoinPriceAnchor:
    """An approved price in the same project unit; no private source fields."""

    commodity_code: str
    price_project_thousand_toman: int
    event_time_utc: datetime | str
    available_at_utc: datetime | str
    settlement_term: str
    trade_form: str
    quality_state: str = "ELIGIBLE"
    evidence_kind: str = "CANONICAL"


@dataclass(frozen=True, slots=True)
class ResolvedCoinGroupOffer:
    """A parsed offer with a deterministic model-eligibility decision."""

    offer_index: int
    commodity_code: str | None
    price_project_thousand_toman: int
    quantity: int
    side: str
    settlement_term: str
    trade_form: str
    is_conditional: bool
    quality_state: str
    resolution_reason: str
    anchor_count: int
    relative_distance: float | None
    authoritative_anchor_count: int = 0


def _strict_timestamp(value: datetime | str, *, name: str) -> str:
    try:
        return normalize_utc(value, field_name=name)
    except MarketStoreContractError as exc:
        raise ValueError(str(exc)) from exc


def _normalize_anchor_uncached(
    anchor: CoinPriceAnchor,
) -> _NormalizedAnchor | None:
    code = str(anchor.commodity_code or "").strip().upper()
    if code not in _PRICE_BOUNDS:
        return None
    try:
        price = int(anchor.price_project_thousand_toman)
    except (TypeError, ValueError):
        return None
    low, high = _PRICE_BOUNDS[code]
    if not low <= price <= high or str(anchor.quality_state).upper() != "ELIGIBLE":
        return None
    settlement = str(anchor.settlement_term or "").upper()
    trade_form = str(anchor.trade_form or "").upper()
    if settlement not in {"CASH", "TODAY", "TOMORROW"} or trade_form not in {
        "PHYSICAL", "PAPER_NORMAL", "PAPER_REVERSE", "PAPER_SWIM"
    }:
        return None
    try:
        event_time = _strict_timestamp(anchor.event_time_utc, name="coin_anchor_event_time_utc")
        available_at = _strict_timestamp(anchor.available_at_utc, name="coin_anchor_available_at_utc")
    except ValueError:
        return None
    if available_at < event_time:
        return None
    evidence_kind = str(anchor.evidence_kind or "").strip().upper()
    if evidence_kind not in {
        "CANONICAL",
        "GROUP_DERIVED",
        "HUMAN_REVIEWED",
        "MODEL_SNAPSHOT",
        "PROVISIONAL_EXPLICIT_CLUSTER",
    }:
        return None
    return code, price, event_time, available_at, settlement, trade_form, evidence_kind


@lru_cache(maxsize=32_768)
def _normalized_anchor_cached(
    anchor: CoinPriceAnchor,
) -> _NormalizedAnchor | None:
    """Validate an immutable anchor once per collector process."""

    return _normalize_anchor_uncached(anchor)


def _normalized_anchor(
    anchor: CoinPriceAnchor,
) -> _NormalizedAnchor | None:
    # ``CoinPriceAnchor`` is frozen and its contract contains only hashable
    # scalar values.  Keep a defensive fallback for malformed callers so the
    # cache never changes the resolver's previous fail-closed behaviour.
    try:
        return _normalized_anchor_cached(anchor)
    except TypeError:
        return _normalize_anchor_uncached(anchor)


@lru_cache(maxsize=32_768)
def _anchor_event_stamp(event_time_utc: str) -> datetime:
    """Parse one already-normalized anchor timestamp once."""

    return datetime.fromisoformat(event_time_utc.replace("Z", "+00:00"))


@dataclass(slots=True)
class _AnchorBucket:
    event_stamps: list[datetime]
    anchors: list[_NormalizedAnchor]


class CoinPriceAnchorIndex:
    """Mutable same-book/time index with the exact resolver anchor contract."""

    def __init__(self, anchors: Iterable[CoinPriceAnchor] = ()) -> None:
        self._buckets: dict[tuple[str, str], _AnchorBucket] = {}
        for anchor in anchors:
            self.add(anchor)

    def add(self, anchor: CoinPriceAnchor) -> None:
        normalized = _normalized_anchor(anchor)
        if normalized is None:
            return
        event_stamp = _anchor_event_stamp(normalized[2])
        key = (normalized[4], normalized[5])
        bucket = self._buckets.setdefault(key, _AnchorBucket([], []))
        position = bisect_right(bucket.event_stamps, event_stamp)
        bucket.event_stamps.insert(position, event_stamp)
        bucket.anchors.insert(position, normalized)

    def matching(
        self,
        *,
        settlement_term: str,
        trade_form: str,
        source_event_stamp: datetime,
    ) -> tuple[_NormalizedAnchor, ...]:
        bucket = self._buckets.get((settlement_term, trade_form))
        if bucket is None:
            return ()
        lower = source_event_stamp - timedelta(seconds=MAXIMUM_ANCHOR_AGE_SECONDS)
        start = bisect_left(bucket.event_stamps, lower)
        # Strict causality excludes anchors at the source event itself.
        stop = bisect_left(bucket.event_stamps, source_event_stamp)
        return tuple(bucket.anchors[start:stop])

    def reference_prices(
        self,
        *,
        settlement_term: str,
        trade_form: str,
        source_event_time_utc: str,
        source_available_at_utc: str,
    ) -> dict[str, tuple[int, ...]]:
        """Return causal, preferably near-time prices for parser scale choice."""

        source_stamp = _anchor_event_stamp(source_event_time_utc)
        grouped: dict[str, list[tuple[int, datetime]]] = {}
        for normalized in self.matching(
            settlement_term=settlement_term,
            trade_form=trade_form,
            source_event_stamp=source_stamp,
        ):
            (
                code,
                price,
                event_time,
                available_at,
                _settlement,
                _form,
                _kind,
            ) = normalized
            if available_at > source_available_at_utc:
                continue
            grouped.setdefault(code, []).append(
                (price, _anchor_event_stamp(event_time))
            )
        result: dict[str, tuple[int, ...]] = {}
        preferred_lower = source_stamp - timedelta(
            seconds=PREFERRED_ANCHOR_AGE_SECONDS
        )
        for code, values in grouped.items():
            preferred = [price for price, stamp in values if stamp >= preferred_lower]
            result[code] = tuple(
                preferred
                if len(preferred) >= MINIMUM_ANCHOR_COUNT
                else (price for price, _stamp_value in values)
            )
        return result


def _candidate_centers(
    parsed: ParsedCoinGroupOffer,
    *,
    source_event_time_utc: str,
    source_available_at_utc: str,
    anchors: Iterable[CoinPriceAnchor] | CoinPriceAnchorIndex,
    supplemental_anchors: Iterable[CoinPriceAnchor] = (),
) -> list[tuple[str, float, int, float, int, bool, bool]]:
    """Return strictly-prior same-book centers as code/center/count/distance."""

    grouped: dict[str, list[tuple[int, str, datetime]]] = {}
    source_stamp = datetime.fromisoformat(
        source_event_time_utc.replace("Z", "+00:00")
    )
    if isinstance(anchors, CoinPriceAnchorIndex):
        normalized_anchors: Iterable[_NormalizedAnchor | None] = anchors.matching(
            settlement_term=parsed.settlement_term,
            trade_form=parsed.trade_form,
            source_event_stamp=source_stamp,
        )
    else:
        normalized_anchors = (_normalized_anchor(anchor) for anchor in anchors)
    combined_anchors = (
        *normalized_anchors,
        *(_normalized_anchor(anchor) for anchor in supplemental_anchors),
    )
    for normalized in combined_anchors:
        if normalized is None:
            continue
        (
            code,
            price,
            event_time,
            available_at,
            settlement,
            form,
            evidence_kind,
        ) = normalized
        # Both publication and availability must be earlier.  A source event
        # that exists in the future cannot rewrite a historical label, and a
        # delayed fact cannot leak into the offer's contemporaneous decision.
        if event_time >= source_event_time_utc or available_at > source_available_at_utc:
            continue
        anchor_stamp = _anchor_event_stamp(event_time)
        if (source_stamp - anchor_stamp).total_seconds() > MAXIMUM_ANCHOR_AGE_SECONDS:
            continue
        if settlement != parsed.settlement_term or form != parsed.trade_form:
            continue
        grouped.setdefault(code, []).append((price, evidence_kind, anchor_stamp))
    candidates: list[tuple[str, float, int, float, int, bool, bool]] = []
    preferred_lower = source_stamp - timedelta(
        seconds=PREFERRED_ANCHOR_AGE_SECONDS
    )
    for code, evidence in grouped.items():
        recent = [item for item in evidence if item[2] >= preferred_lower]
        if len(recent) >= MINIMUM_ANCHOR_COUNT or any(
            kind == "HUMAN_REVIEWED" for _, kind, _stamp_value in recent
        ):
            evidence = recent
        prices = [item[0] for item in evidence]
        human_reviewed = any(
            kind == "HUMAN_REVIEWED" for _, kind, _stamp_value in evidence
        )
        model_snapshot = any(
            kind == "MODEL_SNAPSHOT" for _, kind, _stamp_value in evidence
        )
        if len(prices) < MINIMUM_ANCHOR_COUNT and not human_reviewed:
            continue
        low, high = _PRICE_BOUNDS[code]
        if not low <= parsed.price_project_thousand_toman <= high:
            continue
        center = float(median(prices))
        distance = abs(parsed.price_project_thousand_toman - center) / center
        # One explicit operator review has the contradiction strength of the
        # normal two-anchor canonical quorum while remaining causal by its
        # review availability timestamp.
        authoritative_count = sum(
            kind == "CANONICAL" for _, kind, _stamp_value in evidence
        )
        authoritative_count += MINIMUM_ANCHOR_COUNT * sum(
            kind == "HUMAN_REVIEWED" for _, kind, _stamp_value in evidence
        )
        candidates.append(
            (
                code,
                center,
                len(prices),
                distance,
                authoritative_count,
                human_reviewed,
                model_snapshot,
            )
        )
    return sorted(candidates, key=lambda item: (item[3], -item[2], item[0]))


def _resolve_one(
    parsed: ParsedCoinGroupOffer,
    *,
    offer_index: int,
    source_event_time_utc: str,
    source_available_at_utc: str,
    anchors: Iterable[CoinPriceAnchor] | CoinPriceAnchorIndex,
    supplemental_anchors: Iterable[CoinPriceAnchor] = (),
) -> ResolvedCoinGroupOffer:
    candidates = _candidate_centers(
        parsed,
        source_event_time_utc=source_event_time_utc,
        source_available_at_utc=source_available_at_utc,
        anchors=anchors,
        supplemental_anchors=supplemental_anchors,
    )
    winner = candidates[0] if candidates else None
    runner_up = candidates[1] if len(candidates) > 1 else None
    plausible_codes = {
        code
        for code, (low, high) in _PRICE_BOUNDS.items()
        if low <= parsed.price_project_thousand_toman <= high
    }
    covered_codes = {candidate[0] for candidate in candidates}
    overlap_coverage_complete = bool(
        parsed.commodity_code is not None
        or len(plausible_codes) <= 1
        or plausible_codes.issubset(covered_codes)
        or bool(winner and winner[5])
    )
    decisive = bool(
        winner
        and overlap_coverage_complete
        and winner[3] <= MAXIMUM_RELATIVE_DISTANCE
        and (
            runner_up is None
            or runner_up[3] - winner[3] >= MINIMUM_RUNNER_UP_MARGIN
        )
    )
    claimed = parsed.commodity_code
    if claimed is not None:
        if decisive:
            assert winner is not None
            (
                winner_code,
                _,
                anchor_count,
                distance,
                authoritative_count,
                _human_reviewed,
                model_snapshot,
            ) = winner
            if (
                claimed != winner_code
                and authoritative_count >= MINIMUM_ANCHOR_COUNT
            ):
                # An explicit parser result is never silently relabelled.  A
                # contradiction can reject it only when the causal evidence is
                # authoritative; group-derived context is advisory.
                return ResolvedCoinGroupOffer(
                    offer_index=offer_index,
                    commodity_code=claimed,
                    price_project_thousand_toman=parsed.price_project_thousand_toman,
                    quantity=parsed.quantity,
                    side=parsed.side,
                    settlement_term=parsed.settlement_term,
                    trade_form=parsed.trade_form,
                    is_conditional=parsed.is_conditional,
                    quality_state="REJECTED",
                    resolution_reason=(
                        "EXPLICIT_COMMODITY_CONFLICTS_WITH_STRICTLY_PRIOR_SAME_BOOK_PRICE"
                    ),
                    anchor_count=anchor_count,
                    relative_distance=round(distance, 6),
                    authoritative_anchor_count=authoritative_count,
                )
            if claimed == winner_code:
                reason = (
                    "EXPLICIT_COMMODITY_VALIDATED_BY_STRICTLY_PRIOR_SAME_BOOK_PRICE"
                    if authoritative_count
                    else (
                        "EXPLICIT_COMMODITY_SUPPORTED_BY_STRICTLY_PRIOR_MODEL_PRICE_RANGE"
                        if model_snapshot
                        else "EXPLICIT_COMMODITY_SUPPORTED_BY_COHERENT_PRIOR_GROUP_CLUSTER"
                    )
                )
            else:
                reason = (
                    "EXPLICIT_COMMODITY_RETAINED_DESPITE_NONAUTHORITATIVE_PRICE_CONFLICT"
                )
            return ResolvedCoinGroupOffer(
                offer_index=offer_index,
                commodity_code=claimed,
                price_project_thousand_toman=parsed.price_project_thousand_toman,
                quantity=parsed.quantity,
                side=parsed.side,
                settlement_term=parsed.settlement_term,
                trade_form=parsed.trade_form,
                is_conditional=parsed.is_conditional,
                quality_state="ELIGIBLE",
                resolution_reason=reason,
                anchor_count=anchor_count,
                relative_distance=round(distance, 6),
                authoritative_anchor_count=authoritative_count,
            )
        # The parser already established commodity, side, price, quantity,
        # settlement and form.  No fresh price context means "no conflict",
        # not "unknown"; otherwise a market reopen deadlocks every valid row.
        return ResolvedCoinGroupOffer(
            offer_index=offer_index,
            commodity_code=claimed,
            price_project_thousand_toman=parsed.price_project_thousand_toman,
            quantity=parsed.quantity,
            side=parsed.side,
            settlement_term=parsed.settlement_term,
            trade_form=parsed.trade_form,
            is_conditional=parsed.is_conditional,
            quality_state="ELIGIBLE",
            resolution_reason="EXPLICIT_COMMODITY_ACCEPTED_WITHOUT_AUTHORITATIVE_CONFLICT",
            anchor_count=winner[2] if winner else 0,
            relative_distance=round(winner[3], 6) if winner else None,
            authoritative_anchor_count=winner[4] if winner else 0,
        )
    if not decisive:
        reason = (
            "OVERLAPPING_COMMODITY_BOOKS_REQUIRE_STRICTLY_PRIOR_ANCHOR_COVERAGE"
            if winner and not overlap_coverage_complete
            else "INSUFFICIENT_OR_AMBIGUOUS_STRICTLY_PRIOR_SAME_BOOK_ANCHORS"
        )
        return ResolvedCoinGroupOffer(
            offer_index=offer_index,
            commodity_code=claimed,
            price_project_thousand_toman=parsed.price_project_thousand_toman,
            quantity=parsed.quantity,
            side=parsed.side,
            settlement_term=parsed.settlement_term,
            trade_form=parsed.trade_form,
            is_conditional=parsed.is_conditional,
            quality_state="PENDING_REVIEW",
            resolution_reason=reason,
            anchor_count=winner[2] if winner else 0,
            relative_distance=round(winner[3], 6) if winner else None,
            authoritative_anchor_count=winner[4] if winner else 0,
        )
    assert winner is not None
    (
        winner_code,
        _,
        anchor_count,
        distance,
        authoritative_count,
        _human_reviewed,
        model_snapshot,
    ) = winner
    state = "ELIGIBLE"
    reason = (
        "UNNAMED_COMMODITY_RESOLVED_BY_STRICTLY_PRIOR_SAME_BOOK_PRICE"
        if authoritative_count
        else (
            "UNNAMED_COMMODITY_RESOLVED_BY_STRICTLY_PRIOR_MODEL_PRICE_RANGE"
            if model_snapshot
            else "UNNAMED_COMMODITY_RESOLVED_BY_COHERENT_PRIOR_GROUP_CLUSTER"
        )
    )
    code = winner_code
    return ResolvedCoinGroupOffer(
        offer_index=offer_index,
        commodity_code=code,
        price_project_thousand_toman=parsed.price_project_thousand_toman,
        quantity=parsed.quantity,
        side=parsed.side,
        settlement_term=parsed.settlement_term,
        trade_form=parsed.trade_form,
        is_conditional=parsed.is_conditional,
        quality_state=state,
        resolution_reason=reason,
        anchor_count=anchor_count,
        relative_distance=round(distance, 6),
        authoritative_anchor_count=authoritative_count,
    )


def resolve_coin_group_offers(
    source: CoinGroupMessageInput,
    *,
    anchors: Iterable[CoinPriceAnchor] | CoinPriceAnchorIndex,
    parsed_offers: Iterable[ParsedCoinGroupOffer] | None = None,
    supplemental_anchors: Iterable[CoinPriceAnchor] = (),
) -> list[ResolvedCoinGroupOffer]:
    """Resolve all parser candidates using only facts known before the message."""

    event_time = _strict_timestamp(source.published_at_utc, name="coin_group_published_at_utc")
    available_at = _strict_timestamp(source.available_at_utc, name="coin_group_available_at_utc")
    if available_at < event_time:
        raise ValueError("coin_group_available_before_published")
    materialized_anchors = (
        anchors if isinstance(anchors, CoinPriceAnchorIndex) else tuple(anchors)
    )
    materialized_supplemental_anchors = tuple(supplemental_anchors)
    parsed_values = (
        tuple(parsed_offers)
        if parsed_offers is not None
        else tuple(parse_coin_group_offers(source))
    )
    return [
        _resolve_one(
            parsed,
            offer_index=index,
            source_event_time_utc=event_time,
            source_available_at_utc=available_at,
            anchors=materialized_anchors,
            supplemental_anchors=materialized_supplemental_anchors,
        )
        for index, parsed in enumerate(parsed_values)
    ]


def resolved_coin_group_observations(
    source: CoinGroupMessageInput,
    *,
    anchors: Iterable[CoinPriceAnchor],
    resolution_available_at_utc: datetime | str | None = None,
    resolved_offers: Iterable[ResolvedCoinGroupOffer] | None = None,
) -> list[MarketObservation]:
    """Project resolved results without text, identity, or future leakage.

    If a reconciliation runs after the source event, callers must set
    ``resolution_available_at_utc`` to that later instant.  This preserves the
    causal availability boundary for historical replay and snapshots.
    """

    event_time = _strict_timestamp(source.published_at_utc, name="coin_group_published_at_utc")
    source_available = _strict_timestamp(source.available_at_utc, name="coin_group_available_at_utc")
    resolution_available = _strict_timestamp(
        resolution_available_at_utc or source_available,
        name="coin_group_resolution_available_at_utc",
    )
    if resolution_available < source_available:
        raise ValueError("coin_group_resolution_available_before_source_available")
    observations: list[MarketObservation] = []
    resolved_values = (
        tuple(resolved_offers)
        if resolved_offers is not None
        else tuple(resolve_coin_group_offers(source, anchors=anchors))
    )
    for resolved in resolved_values:
        commodity = resolved.commodity_code or "UNRESOLVED"
        parsed_evidence = coin_group_offer_field_evidence(
            source.text,
            ParsedCoinGroupOffer(
                commodity_code=resolved.commodity_code,
                price_project_thousand_toman=resolved.price_project_thousand_toman,
                quantity=resolved.quantity,
                side=resolved.side,
                settlement_term=resolved.settlement_term,
                trade_form=resolved.trade_form,
                is_conditional=resolved.is_conditional,
                quality_state=resolved.quality_state,
                resolution_reason=resolved.resolution_reason,
            ),
        )
        reason = resolved.resolution_reason
        instrument_evidence = (
            "HUMAN_REVIEWED_CORRECTION"
            if "HUMAN" in reason
            else "TEMPORAL_MODEL_ANCHORS"
            if "MODEL_PRICE_RANGE" in reason
            else "TEMPORAL_CANONICAL_ANCHORS"
            if "STRICTLY_PRIOR_SAME_BOOK_PRICE" in reason
            else "TEMPORAL_GROUP_CLUSTER"
            if "COHERENT_PRIOR_GROUP_CLUSTER" in reason
            else "EXPLICIT_COMMODITY_TOKEN"
            if resolved.commodity_code is not None
            else "TEMPORAL_RESOLUTION_REQUIRED"
        )
        field_evidence = {
            **parsed_evidence,
            "instrument": (instrument_evidence,),
        }
        observations.append(
            MarketObservation(
                event_key=derive_event_key(
                    "coin-group-offer-v1", source.group_number, source.source_event_id, resolved.offer_index
                ),
                source_code=f"GROUP_{int(source.group_number)}",
                source_family="GROUP",
                event_time_utc=event_time,
                available_at_utc=resolution_available,
                instrument="COIN_" + commodity,
                market_label="GROUP_COIN_" + commodity,
                settlement_term=resolved.settlement_term,
                trade_form=resolved.trade_form,
                event_type="OFFER",
                side=resolved.side,
                price=Decimal(resolved.price_project_thousand_toman),
                price_unit="PROJECT_THOUSAND_TOMAN",
                currency="TOMAN",
                quantity=resolved.quantity,
                quantity_unit="COIN_COUNT",
                parse_confidence=0.98 if resolved.quality_state == "ELIGIBLE" else 0.0,
                parser_version=COIN_GROUP_PARSER_VERSION + "+" + COIN_GROUP_RESOLUTION_VERSION,
                quality_state=resolved.quality_state,
                quality_policy_version="coin-group-context-v1",
                is_conditional=resolved.is_conditional,
                attributes={
                    "group_number": int(source.group_number),
                    "commodity_resolution": "VALIDATED" if resolved.quality_state == "ELIGIBLE" else "UNRESOLVED",
                    "resolution_reason": resolved.resolution_reason,
                    "anchor_count": resolved.anchor_count,
                    "authoritative_anchor_count": resolved.authoritative_anchor_count,
                    "relative_distance": resolved.relative_distance,
                    "field_evidence": field_evidence,
                },
            )
        )
    return observations
