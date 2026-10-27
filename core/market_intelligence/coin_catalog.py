"""Fail-closed mapping from product-neutral coin inference to local catalog IDs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.commodity import Commodity
from core.pack_commodities import PACK_BASE_RATE_CODE_TO_COMMODITY_CODE

from .coin_inference import (
    CANONICAL_COMMODITY_NAMES,
    COIN_CANDIDATE_FAMILY_BY_CODE,
    COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
    COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
    COIN_INFERENCE_NEARBY_PRICE_RANGE_PERCENT,
    COIN_LOW_DATE_COMMODITY_CODES,
    CoinCommodityCandidate,
    CoinCommodityInference,
    normalize_coin_inference_candidate_scope,
)


COIN_CATALOG_RESOLUTION_VERSION = "coin-catalog-resolution-v2"
COIN_INFERENCE_EDIT_PRICE_RANGE_PERCENT = COIN_INFERENCE_NEARBY_PRICE_RANGE_PERCENT


@dataclass(frozen=True, slots=True)
class CatalogCoinCommodityCandidate:
    """One inference candidate resolved against this site's commodity catalog."""

    commodity_id: int
    commodity_code: str
    commodity_name: str
    center_project_price: int
    lower_project_price: int
    upper_project_price: int
    confidence: str
    distance_to_center_relative: float


@dataclass(frozen=True, slots=True)
class CatalogCoinCommodityInference:
    """A local catalog projection; this object is not an offer-create command."""

    status: str
    settlement_term: str
    candidates: tuple[CatalogCoinCommodityCandidate, ...]
    snapshot_generated_at_utc: str | None
    snapshot_receipt: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidates": [asdict(item) for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class CatalogCoinCommodityEditCandidate:
    """One explicit same-family catalog choice shown after the user asks to edit."""

    commodity_id: int
    commodity_code: str
    commodity_name: str


def _abstain(inference: CoinCommodityInference, reason: str) -> CatalogCoinCommodityInference:
    return CatalogCoinCommodityInference(
        status="ABSTAIN",
        settlement_term=inference.settlement_term,
        candidates=(),
        snapshot_generated_at_utc=inference.snapshot_generated_at_utc,
        snapshot_receipt=inference.snapshot_receipt,
        reason=reason,
    )


async def _find_exact_canonical_commodity(
    db: AsyncSession,
    *,
    canonical_name: str,
) -> Commodity | None:
    """Find exactly one catalog row by the natural name; aliases are forbidden."""

    result = await db.execute(select(Commodity).where(Commodity.name == canonical_name))
    rows = list(result.scalars().all())
    if len(rows) != 1:
        return None
    commodity = rows[0]
    try:
        commodity_id = int(getattr(commodity, "id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if commodity_id <= 0 or getattr(commodity, "name", None) != canonical_name:
        return None
    return commodity


async def resolve_coin_inference_against_catalog(
    db: AsyncSession,
    inference: CoinCommodityInference,
) -> CatalogCoinCommodityInference:
    """Map all candidates by exact ``commodities.name`` or abstain as a whole.

    The ranker intentionally knows no site-local IDs.  Mapping every candidate
    (not only the top candidate) prevents a later confirmation UI from silently
    falling back to an alias or an unrelated ID.
    """

    if inference.status == "ABSTAIN":
        return _abstain(inference, inference.reason or "INFERENCE_ABSTAINED")
    if inference.status not in {"AUTO_SELECT", "CONFIRM"} or not inference.candidates:
        return _abstain(inference, "INFERENCE_STATUS_INVALID")

    resolved: list[CatalogCoinCommodityCandidate] = []
    for candidate in inference.candidates:
        commodity = await _find_exact_canonical_commodity(
            db,
            canonical_name=candidate.commodity_name,
        )
        if commodity is None:
            return _abstain(inference, "CATALOG_CANONICAL_NAME_UNAVAILABLE")
        resolved.append(
            CatalogCoinCommodityCandidate(
                commodity_id=int(commodity.id),
                commodity_code=candidate.commodity_code,
                commodity_name=candidate.commodity_name,
                center_project_price=candidate.center_project_price,
                lower_project_price=candidate.lower_project_price,
                upper_project_price=candidate.upper_project_price,
                confidence=candidate.confidence,
                distance_to_center_relative=candidate.distance_to_center_relative,
            )
        )
    return CatalogCoinCommodityInference(
        status=inference.status,
        settlement_term=inference.settlement_term,
        candidates=tuple(resolved),
        snapshot_generated_at_utc=inference.snapshot_generated_at_utc,
        snapshot_receipt=inference.snapshot_receipt,
        reason=inference.reason,
    )


async def resolve_coin_inference_edit_candidates(
    db: AsyncSession,
    inference: CatalogCoinCommodityInference,
    *,
    snapshot: Mapping[str, Any],
    submitted_project_price: int,
    candidate_scope: str = "ALL",
) -> tuple[CatalogCoinCommodityEditCandidate, ...]:
    """Return nearby existing same-family commodities for explicit correction.

    These are not model candidates and must never inherit the inference receipt.
    A choice is relevant only when its published center rate is within ten
    percent (inclusive) of the submitted offer price.  The exact Snapshot used
    for the inference is supplied by the caller, so Bot and Web never compute
    or widen this range independently.
    """

    if inference.status not in {"AUTO_SELECT", "CONFIRM"} or not inference.candidates:
        return ()
    try:
        submitted_price = int(submitted_project_price)
    except (TypeError, ValueError):
        return ()
    if submitted_price <= 0 or not isinstance(snapshot, Mapping):
        return ()
    scope = normalize_coin_inference_candidate_scope(candidate_scope)
    first_code = inference.candidates[0].commodity_code
    family = COIN_CANDIDATE_FAMILY_BY_CODE.get(first_code)
    if family is None:
        return ()

    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping):
        return ()
    nearby_distances: dict[str, int] = {}
    for item in rates.get("items") or ():
        if not isinstance(item, Mapping) or item.get("status") != "ESTIMATED":
            continue
        rate_code = str(item.get("commodity_code") or "")
        code = (
            PACK_BASE_RATE_CODE_TO_COMMODITY_CODE.get(rate_code, "")
            if scope == COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY
            else rate_code
        )
        if (
            code not in CANONICAL_COMMODITY_NAMES
            or COIN_CANDIDATE_FAMILY_BY_CODE.get(code) != family
            or str(item.get("settlement_term") or "").upper() != inference.settlement_term
            or (
                scope == COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY
                and code not in COIN_LOW_DATE_COMMODITY_CODES
            )
        ):
            continue
        center = item.get("estimated_project_price")
        if not isinstance(center, int) or isinstance(center, bool) or center <= 0:
            continue
        distance = abs(center - submitted_price)
        if distance * 100 > submitted_price * COIN_INFERENCE_EDIT_PRICE_RANGE_PERCENT:
            continue
        nearby_distances[code] = distance

    model_order = [candidate.commodity_code for candidate in inference.candidates]
    ordered_codes = [code for code in model_order if code in nearby_distances]
    ordered_codes.extend(
        code
        for code in sorted(
            (code for code in nearby_distances if code not in ordered_codes),
            key=lambda code: (nearby_distances[code], code),
        )
    )

    resolved: list[CatalogCoinCommodityEditCandidate] = []
    for code in ordered_codes:
        name = CANONICAL_COMMODITY_NAMES[code]
        commodity = await _find_exact_canonical_commodity(db, canonical_name=name)
        if commodity is None:
            continue
        resolved.append(
            CatalogCoinCommodityEditCandidate(
                commodity_id=int(commodity.id),
                commodity_code=code,
                commodity_name=name,
            )
        )
    return tuple(resolved)


__all__ = [
    "COIN_CATALOG_RESOLUTION_VERSION",
    "COIN_INFERENCE_EDIT_PRICE_RANGE_PERCENT",
    "CatalogCoinCommodityCandidate",
    "CatalogCoinCommodityEditCandidate",
    "CatalogCoinCommodityInference",
    "resolve_coin_inference_against_catalog",
    "resolve_coin_inference_edit_candidates",
]
