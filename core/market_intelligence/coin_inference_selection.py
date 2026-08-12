"""Fail-closed revalidation for a user-selected inferred commodity.

The browser or bot may display a decision produced seconds earlier, but it is
never trusted as an offer command.  At final submission this module reads the
append-only decision receipt and evaluates the price against the currently
published local snapshot again.  A stale, unavailable, or changed candidate
set is rejected before any Offer is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from .coin_catalog import CatalogCoinCommodityCandidate, CatalogCoinCommodityInference, resolve_coin_inference_against_catalog
from .coin_inference import infer_coin_commodity_from_published_snapshot, normalize_coin_inference_candidate_scope
from .coin_inference_audit import load_coin_inference_audit


class CoinInferenceSelectionRejected(ValueError):
    """The old inference receipt must not be used to create an offer."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CoinInferenceSelectionRevalidation:
    """A current catalog candidate proven safe for the submitted offer."""

    candidate: CatalogCoinCommodityCandidate
    decision: CatalogCoinCommodityInference


async def revalidate_coin_inference_selection(
    db: AsyncSession,
    *,
    snapshot_path: Path | str,
    decision_key: str,
    selected_commodity_id: int,
    submitted_project_price: int,
    settlement_term: str,
    source_surface: str,
    now_utc: datetime | None = None,
) -> CoinInferenceSelectionRevalidation:
    """Re-evaluate a displayed decision against the latest local snapshot.

    The receipt binds its surface, price, settlement, and candidate scope.  A
    newly computed result must still contain the user-selected local commodity
    id.  An original AUTO_SELECT receipt additionally pins the only permitted
    id, so a client cannot turn an automatic choice into a different one.
    """

    try:
        selected_id = int(selected_commodity_id)
        submitted_price = int(submitted_project_price)
    except (TypeError, ValueError) as exc:
        raise CoinInferenceSelectionRejected("SELECTION_INPUT_INVALID") from exc
    if selected_id <= 0 or submitted_price <= 0:
        raise CoinInferenceSelectionRejected("SELECTION_INPUT_INVALID")

    settlement = str(settlement_term or "").strip().upper()
    surface = str(source_surface or "").strip().upper()
    if settlement not in {"CASH", "TOMORROW"} or surface not in {"WEBAPP", "TELEGRAM_BOT"}:
        raise CoinInferenceSelectionRejected("SELECTION_CONTEXT_INVALID")

    receipt = await load_coin_inference_audit(db, decision_key=decision_key)
    if receipt is None:
        raise CoinInferenceSelectionRejected("SELECTION_RECEIPT_UNKNOWN")
    if (
        str(getattr(receipt, "source_surface", "")).upper() != surface
        or str(getattr(receipt, "settlement_term", "")).upper() != settlement
        or int(getattr(receipt, "submitted_project_price", 0) or 0) != submitted_price
    ):
        raise CoinInferenceSelectionRejected("SELECTION_RECEIPT_MISMATCH")
    if str(getattr(receipt, "decision_status", "")) not in {"AUTO_SELECT", "CONFIRM"}:
        raise CoinInferenceSelectionRejected("SELECTION_RECEIPT_NOT_SELECTABLE")
    if (
        str(getattr(receipt, "decision_status", "")) == "AUTO_SELECT"
        and int(getattr(receipt, "selected_commodity_id", 0) or 0) != selected_id
    ):
        raise CoinInferenceSelectionRejected("SELECTION_AUTO_CHOICE_MISMATCH")

    try:
        candidate_scope = normalize_coin_inference_candidate_scope(
            getattr(receipt, "candidate_scope", "ALL")
        )
        fresh_ranker_decision = infer_coin_commodity_from_published_snapshot(
            snapshot_path,
            price_project_thousand_toman=submitted_price,
            settlement_term=settlement,
            now_utc=now_utc or datetime.now(timezone.utc),
            candidate_scope=candidate_scope,
        )
        fresh_decision = await resolve_coin_inference_against_catalog(db, fresh_ranker_decision)
    except CoinInferenceSelectionRejected:
        raise
    except Exception as exc:
        raise CoinInferenceSelectionRejected("SELECTION_REVALIDATION_UNAVAILABLE") from exc

    if fresh_decision.status not in {"AUTO_SELECT", "CONFIRM"}:
        raise CoinInferenceSelectionRejected(
            "SELECTION_REVALIDATION_" + str(fresh_decision.reason or "ABSTAINED")
        )
    selected = next(
        (candidate for candidate in fresh_decision.candidates if candidate.commodity_id == selected_id),
        None,
    )
    if selected is None:
        raise CoinInferenceSelectionRejected("SELECTION_CANDIDATE_CHANGED")
    return CoinInferenceSelectionRevalidation(candidate=selected, decision=fresh_decision)


__all__ = [
    "CoinInferenceSelectionRejected",
    "CoinInferenceSelectionRevalidation",
    "revalidate_coin_inference_selection",
]
