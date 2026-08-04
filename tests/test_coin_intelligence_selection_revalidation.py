"""Final-submit guards for price-based commodity selection."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.market_intelligence.coin_catalog import (
    CatalogCoinCommodityCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference_selection import (
    CoinInferenceSelectionRejected,
    revalidate_coin_inference_selection,
)


def candidate(commodity_id: int = 71) -> CatalogCoinCommodityCandidate:
    return CatalogCoinCommodityCandidate(
        commodity_id=commodity_id,
        commodity_code="IMAM",
        commodity_name="امام",
        center_project_price=186_900,
        lower_project_price=185_500,
        upper_project_price=188_300,
        confidence="HIGH",
        distance_to_center_relative=0.000535,
    )


def decision(*candidates: CatalogCoinCommodityCandidate) -> CatalogCoinCommodityInference:
    return CatalogCoinCommodityInference(
        status="AUTO_SELECT" if len(candidates) == 1 else "CONFIRM",
        settlement_term="CASH",
        candidates=tuple(candidates),
        snapshot_generated_at_utc="2026-08-04T10:00:00Z",
        snapshot_receipt="b" * 64,
        reason=None,
    )


def receipt(*, selected_commodity_id: int = 71, status: str = "AUTO_SELECT"):
    return SimpleNamespace(
        source_surface="WEBAPP",
        settlement_term="CASH",
        candidate_scope="ALL",
        submitted_project_price=186_800,
        decision_status=status,
        selected_commodity_id=selected_commodity_id if status == "AUTO_SELECT" else None,
    )


class CoinInferenceSelectionRevalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_candidate_must_match_the_original_auto_choice(self) -> None:
        current = decision(candidate())
        with (
            patch(
                "core.market_intelligence.coin_inference_selection.load_coin_inference_audit",
                new=AsyncMock(return_value=receipt()),
            ),
            patch(
                "core.market_intelligence.coin_inference_selection.infer_coin_commodity_from_published_snapshot",
                return_value=object(),
            ),
            patch(
                "core.market_intelligence.coin_inference_selection.resolve_coin_inference_against_catalog",
                new=AsyncMock(return_value=current),
            ),
        ):
            result = await revalidate_coin_inference_selection(
                SimpleNamespace(),
                snapshot_path="/safe/snapshot.json",
                decision_key="a" * 64,
                selected_commodity_id=71,
                submitted_project_price=186_800,
                settlement_term="CASH",
                source_surface="WEBAPP",
            )
        self.assertEqual(result.candidate.commodity_id, 71)

    async def test_candidate_change_rejects_submission_before_offer_creation(self) -> None:
        current = decision(candidate(72))
        with (
            patch(
                "core.market_intelligence.coin_inference_selection.load_coin_inference_audit",
                new=AsyncMock(return_value=receipt()),
            ),
            patch(
                "core.market_intelligence.coin_inference_selection.infer_coin_commodity_from_published_snapshot",
                return_value=object(),
            ),
            patch(
                "core.market_intelligence.coin_inference_selection.resolve_coin_inference_against_catalog",
                new=AsyncMock(return_value=current),
            ),
        ):
            with self.assertRaisesRegex(CoinInferenceSelectionRejected, "CANDIDATE_CHANGED"):
                await revalidate_coin_inference_selection(
                    SimpleNamespace(),
                    snapshot_path="/safe/snapshot.json",
                    decision_key="a" * 64,
                    selected_commodity_id=71,
                    submitted_project_price=186_800,
                    settlement_term="CASH",
                    source_surface="WEBAPP",
                )

    async def test_receipt_cannot_be_reused_for_another_price_or_surface(self) -> None:
        with patch(
            "core.market_intelligence.coin_inference_selection.load_coin_inference_audit",
            new=AsyncMock(return_value=receipt()),
        ):
            with self.assertRaisesRegex(CoinInferenceSelectionRejected, "RECEIPT_MISMATCH"):
                await revalidate_coin_inference_selection(
                    SimpleNamespace(),
                    snapshot_path="/safe/snapshot.json",
                    decision_key="a" * 64,
                    selected_commodity_id=71,
                    submitted_project_price=186_900,
                    settlement_term="CASH",
                    source_surface="WEBAPP",
                )


if __name__ == "__main__":
    unittest.main()
