"""Causal price/context validation for first-pass coin-group offers.

This module is deliberately not an LLM and never defaults an omitted coin to
Imam.  It accepts only already-approved, privacy-minimized price anchors and
uses facts that were available strictly before the group offer arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from .coin_groups import (
    COIN_GROUP_PARSER_VERSION,
    CoinGroupMessageInput,
    ParsedCoinGroupOffer,
    _PRICE_BOUNDS,
    parse_coin_group_offers,
)
from .market_contracts import MarketObservation, MarketStoreContractError, derive_event_key, normalize_utc


COIN_GROUP_RESOLUTION_VERSION = "coin-group-context-v4-overlap-coverage"
MINIMUM_ANCHOR_COUNT = 2
MAXIMUM_RELATIVE_DISTANCE = 0.015
MINIMUM_RUNNER_UP_MARGIN = 0.005
MAXIMUM_ANCHOR_AGE_SECONDS = 2 * 60 * 60


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


def _normalized_anchor(
    anchor: CoinPriceAnchor,
) -> tuple[str, int, str, str, str, str, str] | None:
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
        "PROVISIONAL_EXPLICIT_CLUSTER",
    }:
        return None
    return code, price, event_time, available_at, settlement, trade_form, evidence_kind


def _candidate_centers(
    parsed: ParsedCoinGroupOffer,
    *,
    source_event_time_utc: str,
    source_available_at_utc: str,
    anchors: Iterable[CoinPriceAnchor],
) -> list[tuple[str, float, int, float, int]]:
    """Return strictly-prior same-book centers as code/center/count/distance."""

    grouped: dict[str, list[tuple[int, str]]] = {}
    source_stamp = datetime.fromisoformat(
        source_event_time_utc.replace("Z", "+00:00")
    )
    for anchor in anchors:
        normalized = _normalized_anchor(anchor)
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
        anchor_stamp = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        if (source_stamp - anchor_stamp).total_seconds() > MAXIMUM_ANCHOR_AGE_SECONDS:
            continue
        if settlement != parsed.settlement_term or form != parsed.trade_form:
            continue
        grouped.setdefault(code, []).append((price, evidence_kind))
    candidates: list[tuple[str, float, int, float, int]] = []
    for code, evidence in grouped.items():
        prices = [item[0] for item in evidence]
        if len(prices) < MINIMUM_ANCHOR_COUNT:
            continue
        low, high = _PRICE_BOUNDS[code]
        if not low <= parsed.price_project_thousand_toman <= high:
            continue
        center = float(median(prices))
        distance = abs(parsed.price_project_thousand_toman - center) / center
        authoritative_count = sum(kind == "CANONICAL" for _, kind in evidence)
        candidates.append((code, center, len(prices), distance, authoritative_count))
    return sorted(candidates, key=lambda item: (item[3], -item[2], item[0]))


def _resolve_one(
    parsed: ParsedCoinGroupOffer,
    *,
    offer_index: int,
    source_event_time_utc: str,
    source_available_at_utc: str,
    anchors: Iterable[CoinPriceAnchor],
) -> ResolvedCoinGroupOffer:
    candidates = _candidate_centers(
        parsed,
        source_event_time_utc=source_event_time_utc,
        source_available_at_utc=source_available_at_utc,
        anchors=anchors,
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
    winner_code, _, anchor_count, distance, authoritative_count = winner
    if claimed is not None and claimed != winner_code:
        # Never silently rewrite an explicit commodity.  A price conflict is
        # useless as an offer or a later linked-trade label until a human or
        # another authoritative source resolves it.
        state = (
            "REJECTED"
            if authoritative_count >= MINIMUM_ANCHOR_COUNT
            else "PENDING_REVIEW"
        )
        reason = (
            "EXPLICIT_COMMODITY_CONFLICTS_WITH_STRICTLY_PRIOR_SAME_BOOK_PRICE"
            if state == "REJECTED"
            else "EXPLICIT_COMMODITY_CONFLICTS_ONLY_WITH_GROUP_DERIVED_PRICE"
        )
        code = claimed
    else:
        state = "ELIGIBLE"
        if authoritative_count:
            reason = (
                "EXPLICIT_COMMODITY_VALIDATED_BY_STRICTLY_PRIOR_SAME_BOOK_PRICE"
                if claimed is not None
                else "UNNAMED_COMMODITY_RESOLVED_BY_STRICTLY_PRIOR_SAME_BOOK_PRICE"
            )
        else:
            reason = (
                "EXPLICIT_COMMODITY_VALIDATED_BY_COHERENT_PRIOR_GROUP_CLUSTER"
                if claimed is not None
                else "UNNAMED_COMMODITY_RESOLVED_BY_COHERENT_PRIOR_GROUP_CLUSTER"
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
    anchors: Iterable[CoinPriceAnchor],
    parsed_offers: Iterable[ParsedCoinGroupOffer] | None = None,
) -> list[ResolvedCoinGroupOffer]:
    """Resolve all parser candidates using only facts known before the message."""

    event_time = _strict_timestamp(source.published_at_utc, name="coin_group_published_at_utc")
    available_at = _strict_timestamp(source.available_at_utc, name="coin_group_available_at_utc")
    if available_at < event_time:
        raise ValueError("coin_group_available_before_published")
    materialized_anchors = tuple(anchors)
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
                },
            )
        )
    return observations
