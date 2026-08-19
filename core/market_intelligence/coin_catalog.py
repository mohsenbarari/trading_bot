"""Fail-closed mapping from product-neutral coin inference to local catalog IDs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.commodity import Commodity

from .coin_inference import (
    CANONICAL_COMMODITY_NAMES,
    COIN_CANDIDATE_FAMILY_BY_CODE,
    COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY,
    COIN_LOW_DATE_COMMODITY_CODES,
    CoinCommodityCandidate,
    CoinCommodityInference,
    normalize_coin_inference_candidate_scope,
)


COIN_CATALOG_RESOLUTION_VERSION = "coin-catalog-resolution-v1"


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
    candidate_scope: str = "ALL",
) -> tuple[CatalogCoinCommodityEditCandidate, ...]:
    """Return existing same-family commodities for an explicit user correction.

    These are not model candidates and must never inherit the inference receipt.
    They merely keep the correction list relevant (full/half/quarter/one-gram)
    while resolving every name against the local canonical catalog.
    """

    if inference.status not in {"AUTO_SELECT", "CONFIRM"} or not inference.candidates:
        return ()
    scope = normalize_coin_inference_candidate_scope(candidate_scope)
    first_code = inference.candidates[0].commodity_code
    family = COIN_CANDIDATE_FAMILY_BY_CODE.get(first_code)
    if family is None:
        return ()
    ordered_codes = [candidate.commodity_code for candidate in inference.candidates]
    ordered_codes.extend(
        code
        for code in CANONICAL_COMMODITY_NAMES
        if COIN_CANDIDATE_FAMILY_BY_CODE.get(code) == family and code not in ordered_codes
    )
    if scope == COIN_INFERENCE_CANDIDATE_SCOPE_LOW_DATE_ONLY:
        ordered_codes = [code for code in ordered_codes if code in COIN_LOW_DATE_COMMODITY_CODES]

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
    "CatalogCoinCommodityCandidate",
    "CatalogCoinCommodityEditCandidate",
    "CatalogCoinCommodityInference",
    "resolve_coin_inference_against_catalog",
    "resolve_coin_inference_edit_candidates",
]
