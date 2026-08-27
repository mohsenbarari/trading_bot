"""Shadow-only API contract for the product coin inference preview."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import CoinInferencePreviewRequest, preview_coin_commodity_inference
from core.market_intelligence.coin_catalog import CatalogCoinCommodityCandidate, CatalogCoinCommodityInference
from core.market_intelligence.coin_inference_shadow import CoinInferenceShadowObservation


class _DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def catalog(status: str = "AUTO_SELECT") -> CatalogCoinCommodityInference:
    candidates = () if status == "ABSTAIN" else (
        CatalogCoinCommodityCandidate(
            commodity_id=71,
            commodity_code="IMAM",
            commodity_name="امام",
            center_project_price=186_900,
            lower_project_price=185_500,
            upper_project_price=188_300,
            confidence="HIGH",
            distance_to_center_relative=0.000535,
        ),
    )
    return CatalogCoinCommodityInference(
        status=status,
        settlement_term="TOMORROW",
        candidates=candidates,
        snapshot_generated_at_utc="2026-08-04T10:00:00Z" if status != "ABSTAIN" else None,
        snapshot_receipt="b" * 64 if status != "ABSTAIN" else None,
        reason=None if status != "ABSTAIN" else "PRICE_OUTSIDE_PUBLISHED_RANGES",
    )


class CoinInferencePreviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_preview_is_not_available(self) -> None:
        with patch("api.routers.offers.settings.coin_intelligence_inference_preview_enabled", False):
            with self.assertRaises(HTTPException) as exc_info:
                await preview_coin_commodity_inference(
                    CoinInferencePreviewRequest(price=186_800, settlement_type="tomorrow"),
                    db=_DB(),
                    _current_user=SimpleNamespace(id=5),
                )
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_preview_is_shadow_only_and_audited_before_returning_auto_select(self) -> None:
        db = _DB()
        with (
            patch("api.routers.offers.settings.coin_intelligence_inference_preview_enabled", True),
            patch("api.routers.offers.settings.coin_intelligence_inference_snapshot_path", "/safe/snapshot.json"),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(return_value=CoinInferenceShadowObservation("a" * 64, catalog())),
            ) as observe,
        ):
            result = await preview_coin_commodity_inference(
                CoinInferencePreviewRequest(price=186_800, settlement_type="tomorrow"),
                db=db,
                _current_user=SimpleNamespace(id=5),
            )
        self.assertEqual((result.status, result.candidates[0].commodity_id, db.commits, db.rollbacks), ("AUTO_SELECT", 71, 1, 0))
        observe.assert_awaited_once()
        self.assertEqual(observe.await_args.kwargs["source_surface"], "WEBAPP")
        self.assertEqual(observe.await_args.kwargs["snapshot_reader"].mode, "LEGACY")
        self.assertRegex(result.decision_key, r"^[a-f0-9]{64}$")

    async def test_valid_abstention_is_returned_not_replaced_by_default_imam(self) -> None:
        db = _DB()
        with (
            patch("api.routers.offers.settings.coin_intelligence_inference_preview_enabled", True),
            patch("api.routers.offers.settings.coin_intelligence_inference_snapshot_path", "/safe/snapshot.json"),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(return_value=CoinInferenceShadowObservation("a" * 64, catalog("ABSTAIN"))),
            ),
        ):
            result = await preview_coin_commodity_inference(
                CoinInferencePreviewRequest(price=190_000, settlement_type="cash"),
                db=db,
                _current_user=SimpleNamespace(id=5),
            )
        self.assertEqual((result.status, result.reason, result.candidates, db.commits), ("ABSTAIN", "PRICE_OUTSIDE_PUBLISHED_RANGES", [], 1))

    async def test_catalog_or_audit_failure_returns_unavailable_and_rolls_back(self) -> None:
        db = _DB()
        with (
            patch("api.routers.offers.settings.coin_intelligence_inference_preview_enabled", True),
            patch("api.routers.offers.settings.coin_intelligence_inference_snapshot_path", "/safe/snapshot.json"),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(side_effect=RuntimeError("db unavailable")),
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await preview_coin_commodity_inference(
                    CoinInferencePreviewRequest(price=186_800, settlement_type="tomorrow"),
                    db=db,
                    _current_user=SimpleNamespace(id=5),
                )
        self.assertEqual((exc_info.exception.status_code, db.commits, db.rollbacks), (503, 0, 1))


if __name__ == "__main__":
    unittest.main()
