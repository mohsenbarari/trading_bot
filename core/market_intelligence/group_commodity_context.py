"""Causal commodity resolution and price validation for Telegram offers.

Static price bands are only a cold-start fallback. Every parsed commodity,
including an explicit one, is checked against strictly-prior market evidence
from the same settlement and trade form. An unnamed commodity may be resolved
when the evidence is decisive. An explicit commodity is never silently
rewritten: a strong price contradiction is marked for abstention instead.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Mapping

from core.market_intelligence.group_offer_parser import COMMODITY_RANGES


CONTEXTUAL_METHODS = frozenset(
    {
        "reply_parent_explicit",
        "reply_parent_context",
        "local_market_price_anchor",
    }
)
BLOCKING_VALIDATION_STATUSES = frozenset(
    {"AMBIGUOUS_PRICE_CONTEXT", "EXPLICIT_PRICE_CONTEXT_CONFLICT"}
)
PRIMARY_ANCHOR_METHODS = frozenset(
    {"explicit", "reply_parent_explicit", "reply_parent_context"}
)
MAX_ANCHOR_AGE_SECONDS = 45 * 60
MAX_WINNER_DISTANCE = 0.015
MAX_PARENT_DISTANCE = 0.015
MIN_RUNNER_UP_MARGIN = 0.005
MIN_SECONDARY_CLUSTER_SIZE = 3


def _distance(price: int, center: float) -> float:
    return abs(float(price) - center) / center


def _plausible(commodity: str, price: int) -> bool:
    price_range = COMMODITY_RANGES.get(commodity)
    return bool(price_range and price_range[0] <= price <= price_range[1])


def _anchor_center(rows: list[Mapping[str, Any]]) -> float:
    """Return a robust center without allowing message volume to dominate."""

    primary_prices = [
        int(row["price"])
        for row in rows
        if str(row.get("commodity_method") or "") in PRIMARY_ANCHOR_METHODS
    ]
    all_prices = [int(row["price"]) for row in rows]
    # Explicit observations are the label-quality tier. When present, their
    # center leads; the surrounding implicit cluster is still reported as
    # evidence but cannot pull an explicit commodity toward another family.
    return float(median(primary_prices or all_prices))


def _plausible_commodities(price: int) -> list[str]:
    return [commodity for commodity in COMMODITY_RANGES if _plausible(commodity, price)]


def _local_candidates(
    *,
    price: int,
    settlement: str,
    trade_form: str,
    as_of_epoch: float,
    prior_offers: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for anchor in prior_offers:
        try:
            anchor_time = float(anchor["event_epoch"])
            anchor_price = int(anchor["price"])
        except (KeyError, TypeError, ValueError):
            continue
        age = as_of_epoch - anchor_time
        if not 0 < age <= MAX_ANCHOR_AGE_SECONDS:
            continue
        if str(anchor.get("settlement") or "UNKNOWN") != settlement:
            continue
        if str(anchor.get("trade_form") or "UNKNOWN") != trade_form:
            continue
        validation_status = str(anchor.get("commodity_validation_status") or "")
        if validation_status == "EXPLICIT_PRICE_CONTEXT_CONFLICT":
            continue
        if (
            validation_status == "AMBIGUOUS_PRICE_CONTEXT"
            and str(anchor.get("commodity_method") or "") != "price_inference"
        ):
            continue
        commodity = str(anchor.get("commodity") or "")
        if not commodity or not _plausible(commodity, price):
            continue
        if str(anchor.get("price_method") or "") != "full":
            continue
        if float(anchor.get("confidence") or 0) < 0.84:
            continue
        if not _plausible(commodity, anchor_price):
            continue
        grouped[commodity].append(anchor)

    candidates: list[dict[str, Any]] = []
    for commodity, anchors in grouped.items():
        primary_count = sum(
            str(anchor.get("commodity_method") or "") in PRIMARY_ANCHOR_METHODS
            for anchor in anchors
        )
        if primary_count == 0 and len(anchors) < MIN_SECONDARY_CLUSTER_SIZE:
            continue
        center = _anchor_center(anchors)
        candidates.append(
            {
                "commodity": commodity,
                "center": center,
                "distance": _distance(price, center),
                "anchor_count": len(anchors),
                "primary_anchor_count": primary_count,
            }
        )
    candidates.sort(key=lambda item: (item["distance"], -item["primary_anchor_count"]))
    return candidates


def _decisive_winner(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates or candidates[0]["distance"] > MAX_WINNER_DISTANCE:
        return None
    winner = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    if runner_up and runner_up["distance"] - winner["distance"] < MIN_RUNNER_UP_MARGIN:
        return None
    return winner


def _candidate_evidence(
    winner: Mapping[str, Any], runner_up: Mapping[str, Any] | None
) -> dict[str, Any]:
    return {
        "kind": "strictly_prior_local_market_prices",
        "center": round(float(winner["center"])),
        "relative_distance": round(float(winner["distance"]), 6),
        "anchor_count": int(winner["anchor_count"]),
        "primary_anchor_count": int(winner["primary_anchor_count"]),
        "runner_up": (
            {
                "commodity": runner_up["commodity"],
                "center": round(float(runner_up["center"])),
                "relative_distance": round(float(runner_up["distance"]), 6),
            }
            if runner_up
            else None
        ),
    }


def resolve_offer_commodity(
    offer: Mapping[str, Any],
    *,
    as_of_epoch: float,
    parent_offers: Iterable[Mapping[str, Any]] = (),
    prior_offers: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve/validate commodity using reply and strictly-prior price evidence."""

    resolved = dict(offer)
    is_explicit = str(resolved.get("commodity_method") or "") == "explicit"

    try:
        price = int(resolved["price"])
    except (KeyError, TypeError, ValueError):
        return resolved

    settlement = str(resolved.get("settlement") or "UNKNOWN")
    trade_form = str(resolved.get("trade_form") or "UNKNOWN")
    claimed_commodity = str(resolved.get("commodity") or "")

    if is_explicit and not _plausible(claimed_commodity, price):
        resolved["commodity_validation_status"] = "EXPLICIT_PRICE_CONTEXT_CONFLICT"
        resolved["commodity_evidence"] = {
            "kind": "outside_plausible_commodity_range",
            "claimed_commodity": claimed_commodity,
            "price": price,
        }
        resolved["confidence"] = min(float(resolved.get("confidence") or 0), 0.49)
        return resolved

    compatible_parents: list[Mapping[str, Any]] = []
    for parent in parent_offers:
        if commodity_context_requires_abstention(parent):
            continue
        commodity = str(parent.get("commodity") or "")
        try:
            parent_price = int(parent["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            commodity
            and _plausible(commodity, price)
            and _distance(price, float(parent_price)) <= MAX_PARENT_DISTANCE
        ):
            compatible_parents.append(parent)
    if compatible_parents:
        parent = min(
            compatible_parents,
            key=lambda item: _distance(price, float(int(item["price"]))),
        )
        parent_method = str(parent.get("commodity_method") or "")
        inherited_commodity = str(parent["commodity"])
        inherited_method = (
            "reply_parent_explicit"
            if parent_method == "explicit"
            else "reply_parent_context"
        )
        if is_explicit:
            if inherited_commodity != claimed_commodity:
                resolved["commodity_validation_status"] = "EXPLICIT_PRICE_CONTEXT_CONFLICT"
                resolved["confidence"] = min(
                    float(resolved.get("confidence") or 0), 0.49
                )
            else:
                resolved["commodity_validation_status"] = "PRICE_CONTEXT_CONSISTENT"
            resolved["commodity_evidence"] = {
                "kind": inherited_method,
                "claimed_commodity": claimed_commodity,
                "context_commodity": inherited_commodity,
                "parent_price": int(parent["price"]),
                "relative_distance": round(
                    _distance(price, float(int(parent["price"]))), 6
                ),
            }
            return resolved
        resolved["commodity"] = inherited_commodity
        resolved["commodity_method"] = inherited_method
        resolved["confidence"] = max(float(resolved.get("confidence") or 0), 0.97)
        resolved["commodity_validation_status"] = "PRICE_CONTEXT_RESOLVED"
        resolved["commodity_evidence"] = {
            "kind": resolved["commodity_method"],
            "parent_price": int(parent["price"]),
            "relative_distance": round(
                _distance(price, float(int(parent["price"]))), 6
            ),
        }
        return resolved

    candidates = _local_candidates(
        price=price,
        settlement=settlement,
        trade_form=trade_form,
        as_of_epoch=as_of_epoch,
        prior_offers=prior_offers,
    )
    winner = _decisive_winner(candidates)
    runner_up = candidates[1] if len(candidates) > 1 else None
    if is_explicit:
        if winner is None:
            resolved["commodity_validation_status"] = "PRICE_CONTEXT_UNVERIFIED"
            return resolved
        resolved["commodity_evidence"] = {
            **_candidate_evidence(winner, runner_up),
            "claimed_commodity": claimed_commodity,
            "context_commodity": winner["commodity"],
        }
        if winner["commodity"] != claimed_commodity:
            resolved["commodity_validation_status"] = "EXPLICIT_PRICE_CONTEXT_CONFLICT"
            resolved["confidence"] = min(float(resolved.get("confidence") or 0), 0.49)
        else:
            resolved["commodity_validation_status"] = "PRICE_CONTEXT_CONSISTENT"
        return resolved

    if winner is None:
        plausible = _plausible_commodities(price)
        if len(plausible) > 1:
            resolved["commodity_validation_status"] = "AMBIGUOUS_PRICE_CONTEXT"
            resolved["commodity_evidence"] = {
                "kind": "insufficient_decisive_price_context",
                "plausible_commodities": plausible,
            }
        else:
            resolved["commodity_validation_status"] = "PRICE_CONTEXT_UNVERIFIED"
        return resolved

    resolved["commodity"] = winner["commodity"]
    resolved["commodity_method"] = "local_market_price_anchor"
    confidence = 0.96 if winner["primary_anchor_count"] else 0.91
    resolved["confidence"] = max(float(resolved.get("confidence") or 0), confidence)
    resolved["commodity_validation_status"] = "PRICE_CONTEXT_RESOLVED"
    resolved["commodity_evidence"] = _candidate_evidence(winner, runner_up)
    return resolved


def is_strong_contextual_resolution(offer: Mapping[str, Any]) -> bool:
    return (
        str(offer.get("commodity_method") or "") in CONTEXTUAL_METHODS
        and float(offer.get("confidence") or 0) >= 0.90
    )


def commodity_context_requires_abstention(offer: Mapping[str, Any]) -> bool:
    return (
        str(offer.get("commodity_validation_status") or "")
        in BLOCKING_VALIDATION_STATUSES
    )
