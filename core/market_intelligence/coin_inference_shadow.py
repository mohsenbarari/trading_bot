"""Shared, shadow-only observation of one catalog-bound coin inference.

This module deliberately has no configuration, HTTP, Telegram, offer mutation,
or commit/rollback ownership.  A caller supplies the already-authorized local
Snapshot path and database session, then decides whether to expose, commit, or
discard the observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from .coin_catalog import CatalogCoinCommodityInference, resolve_coin_inference_against_catalog
from .coin_inference import infer_coin_commodity_from_published_snapshot
from .coin_inference_audit import CoinInferenceAuditCommand, append_coin_inference_audit


@dataclass(frozen=True, slots=True)
class CoinInferenceShadowObservation:
    """A proposed decision plus its opaque audit receipt.

    It is intentionally not an OfferCreate command and contains no user,
    message, text, note, or Telegram identifier.
    """

    decision_key: str
    decision: CatalogCoinCommodityInference


async def observe_coin_inference_shadow(
    db: AsyncSession,
    *,
    snapshot_path: Path | str,
    submitted_project_price: int,
    settlement_term: str,
    source_surface: str,
    now_utc: datetime | None = None,
    candidate_scope: str = "ALL",
) -> CoinInferenceShadowObservation:
    """Rank, catalog-resolve, and append one shadow decision without commit.

    All errors propagate to the caller.  This preserves one atomic caller
    transaction: an unavailable inference must never partly persist an audit
    row, and a valid inference never changes an offer or parser result.
    """

    now = now_utc or datetime.now(timezone.utc)
    ranker_result = infer_coin_commodity_from_published_snapshot(
        snapshot_path,
        price_project_thousand_toman=submitted_project_price,
        settlement_term=settlement_term,
        now_utc=now,
        candidate_scope=candidate_scope,
    )
    catalog_result = await resolve_coin_inference_against_catalog(db, ranker_result)
    decision_key = secrets.token_hex(32)
    await append_coin_inference_audit(
        db,
        CoinInferenceAuditCommand(
            decision_key=decision_key,
            source_surface=source_surface,
            submitted_project_price=submitted_project_price,
            decision=catalog_result,
            candidate_scope=candidate_scope,
        ),
    )
    return CoinInferenceShadowObservation(
        decision_key=decision_key,
        decision=catalog_result,
    )


__all__ = [
    "CoinInferenceShadowObservation",
    "observe_coin_inference_shadow",
]
