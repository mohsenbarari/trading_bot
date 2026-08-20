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
import re
import secrets
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from core.pack_commodities import PACK_COMMODITY_CODE_TO_BASE_RATE_CODE

from .coin_catalog import (
    CatalogCoinCommodityEditCandidate,
    CatalogCoinCommodityInference,
    resolve_coin_inference_against_catalog,
    resolve_coin_inference_edit_candidates,
)
from .coin_inference import (
    COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY,
    CoinCommodityInference,
    infer_coin_commodity,
    normalize_coin_inference_candidate_scope,
)
from .coin_inference_audit import CoinInferenceAuditCommand, append_coin_inference_audit
from .market_snapshot import AtomicMarketSnapshotProvider, MarketSnapshotUnavailable


@dataclass(frozen=True, slots=True)
class CoinInferenceShadowObservation:
    """A proposed decision plus its opaque audit receipt.

    It is intentionally not an OfferCreate command and contains no user,
    message, text, note, or Telegram identifier.
    """

    decision_key: str
    decision: CatalogCoinCommodityInference
    edit_candidates: tuple[CatalogCoinCommodityEditCandidate, ...] = ()


_SAFE_SOURCE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _snapshot_market_context(snapshot: object, ranker_result: object) -> tuple[str | None, str]:
    """Freeze safe source/regime labels from the exact ranked Snapshot."""

    if not isinstance(snapshot, dict):
        return None, "UNKNOWN"
    rate_item = None
    candidates = getattr(ranker_result, "candidates", ()) or ()
    if candidates:
        code = str(getattr(candidates[0], "commodity_code", "")).upper()
        rate_code = PACK_COMMODITY_CODE_TO_BASE_RATE_CODE.get(code, code)
        settlement = str(getattr(ranker_result, "settlement_term", "")).upper()
        rates = snapshot.get("rates")
        if isinstance(rates, dict):
            for item in rates.get("items") or ():
                if (
                    isinstance(item, dict)
                    and str(item.get("commodity_code") or "").upper() == rate_code
                    and str(item.get("settlement_term") or "").upper() == settlement
                ):
                    rate_item = item
                    break
    source = None
    regime = None
    if rate_item is not None:
        source = rate_item.get("underlying_source")
        regime = rate_item.get("market_regime")
    if regime is None:
        market_regime = snapshot.get("market_regime")
        if isinstance(market_regime, dict):
            regime = market_regime.get("label")
    normalized_regime = str(regime or "UNKNOWN").strip().upper()
    if normalized_regime not in {"NORMAL", "UP", "DOWN", "VOLATILE"}:
        normalized_regime = "UNKNOWN"
    normalized_source = str(source or "").strip().upper() or None
    if normalized_source is not None and not _SAFE_SOURCE_CODE_PATTERN.fullmatch(normalized_source):
        normalized_source = None
    return normalized_source, normalized_regime


def _confirmation_only_projection(
    decision: CatalogCoinCommodityInference,
) -> CatalogCoinCommodityInference:
    """Make a unique raw model result explicitly confirmable for rollout UX.

    The append-only audit keeps the original ``AUTO_SELECT`` result for
    measurement.  This projection affects only what the current caller may
    apply to an offer, so turning the rollout switch off cannot rewrite history
    or make the model appear less decisive than it was.
    """

    if decision.status != "AUTO_SELECT":
        return decision
    return CatalogCoinCommodityInference(
        status="CONFIRM",
        settlement_term=decision.settlement_term,
        candidates=decision.candidates,
        snapshot_generated_at_utc=decision.snapshot_generated_at_utc,
        snapshot_receipt=decision.snapshot_receipt,
        reason="AUTO_SELECTION_REQUIRES_CONFIRMATION",
    )


async def observe_coin_inference_shadow(
    db: AsyncSession,
    *,
    snapshot_path: Path | str,
    submitted_project_price: int,
    settlement_term: str,
    source_surface: str,
    now_utc: datetime | None = None,
    candidate_scope: str = "ALL",
    force_confirmation: bool = False,
) -> CoinInferenceShadowObservation:
    """Rank, catalog-resolve, and append one shadow decision without commit.

    All errors propagate to the caller.  This preserves one atomic caller
    transaction: an unavailable inference must never partly persist an audit
    row, and a valid inference never changes an offer or parser result.
    """

    now = now_utc or datetime.now(timezone.utc)
    try:
        snapshot = AtomicMarketSnapshotProvider(snapshot_path).load()
    except MarketSnapshotUnavailable:
        # Preserve the established unavailable-Snapshot abstention contract
        # without retrying the same unavailable path or accidentally loading a
        # newer Snapshot for the same decision.
        snapshot = None
        ranker_result = CoinCommodityInference(
            status="ABSTAIN",
            settlement_term=str(settlement_term or "").upper(),
            candidates=(),
            snapshot_generated_at_utc=None,
            snapshot_receipt=None,
            reason="SNAPSHOT_UNAVAILABLE",
        )
    else:
        ranker_result = infer_coin_commodity(
            snapshot,
            price_project_thousand_toman=submitted_project_price,
            settlement_term=settlement_term,
            now_utc=now,
            candidate_scope=candidate_scope,
        )
    dominant_underlying_source, market_regime = _snapshot_market_context(snapshot, ranker_result)
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
            dominant_underlying_source=dominant_underlying_source,
            market_regime=market_regime,
        ),
    )
    presentation_decision = (
        _confirmation_only_projection(catalog_result)
        if force_confirmation
        else catalog_result
    )
    edit_candidates: tuple[CatalogCoinCommodityEditCandidate, ...] = ()
    if (
        force_confirmation
        and len(presentation_decision.candidates) == 1
        and isinstance(snapshot, Mapping)
        and normalize_coin_inference_candidate_scope(candidate_scope)
        != COIN_INFERENCE_CANDIDATE_SCOPE_PACK_ONLY
    ):
        edit_candidates = await resolve_coin_inference_edit_candidates(
            db,
            presentation_decision,
            snapshot=snapshot,
            submitted_project_price=submitted_project_price,
            candidate_scope=candidate_scope,
        )
    return CoinInferenceShadowObservation(
        decision_key=decision_key,
        decision=presentation_decision,
        edit_candidates=edit_candidates,
    )


__all__ = [
    "CoinInferenceShadowObservation",
    "observe_coin_inference_shadow",
]
