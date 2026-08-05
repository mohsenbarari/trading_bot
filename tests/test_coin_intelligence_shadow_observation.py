"""Contract tests for the shared shadow observation library."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.market_intelligence.coin_catalog import CatalogCoinCommodityInference
from core.market_intelligence.coin_inference_shadow import observe_coin_inference_shadow
from core.market_intelligence.market_snapshot import MarketSnapshotUnavailable


class CoinInferenceShadowObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_uses_one_snapshot_decision_and_defers_commit(self) -> None:
        db = SimpleNamespace()
        decision = CatalogCoinCommodityInference(
            status="ABSTAIN",
            settlement_term="CASH",
            candidates=(),
            snapshot_generated_at_utc=None,
            snapshot_receipt=None,
            reason="SNAPSHOT_UNAVAILABLE",
        )
        with (
            patch(
                "core.market_intelligence.coin_inference_shadow.AtomicMarketSnapshotProvider.load",
                side_effect=MarketSnapshotUnavailable("unavailable"),
            ) as load,
            patch(
                "core.market_intelligence.coin_inference_shadow.resolve_coin_inference_against_catalog",
                new=AsyncMock(return_value=decision),
            ),
            patch(
                "core.market_intelligence.coin_inference_shadow.append_coin_inference_audit",
                new=AsyncMock(),
            ) as append,
            patch("core.market_intelligence.coin_inference_shadow.secrets.token_hex", return_value="c" * 64),
        ):
            observation = await observe_coin_inference_shadow(
                db,
                snapshot_path="/safe/snapshot.json",
                submitted_project_price=186_800,
                settlement_term="CASH",
                source_surface="TELEGRAM_BOT",
            )

        self.assertEqual((observation.decision_key, observation.decision), ("c" * 64, decision))
        self.assertEqual(load.call_count, 1)
        self.assertEqual(append.await_args.args[1].source_surface, "TELEGRAM_BOT")
        self.assertEqual(append.await_args.args[1].submitted_project_price, 186_800)

    async def test_observation_freezes_source_and_regime_from_the_same_snapshot(self) -> None:
        db = SimpleNamespace()
        ranker_result = SimpleNamespace(
            settlement_term="TOMORROW",
            candidates=(SimpleNamespace(commodity_code="IMAM"),),
        )
        decision = CatalogCoinCommodityInference(
            status="CONFIRM",
            settlement_term="TOMORROW",
            candidates=(SimpleNamespace(),),
            snapshot_generated_at_utc="2026-08-05T09:00:00Z",
            snapshot_receipt="a" * 64,
            reason="MULTIPLE_OR_LOW_CONFIDENCE_CANDIDATES",
        )
        snapshot = {
            "rates": {
                "items": [
                    {
                        "commodity_code": "IMAM",
                        "settlement_term": "TOMORROW",
                        "underlying_source": "PRIVATE_PAPER_TOMORROW",
                        "market_regime": "UP",
                    }
                ]
            },
            "market_regime": {"label": "DOWN"},
        }
        with (
            patch(
                "core.market_intelligence.coin_inference_shadow.AtomicMarketSnapshotProvider.load",
                return_value=snapshot,
            ),
            patch(
                "core.market_intelligence.coin_inference_shadow.infer_coin_commodity",
                return_value=ranker_result,
            ) as infer,
            patch(
                "core.market_intelligence.coin_inference_shadow.resolve_coin_inference_against_catalog",
                new=AsyncMock(return_value=decision),
            ),
            patch(
                "core.market_intelligence.coin_inference_shadow.append_coin_inference_audit",
                new=AsyncMock(),
            ) as append,
        ):
            await observe_coin_inference_shadow(
                db,
                snapshot_path="/safe/snapshot.json",
                submitted_project_price=186_800,
                settlement_term="TOMORROW",
                source_surface="WEBAPP",
            )

        self.assertEqual(infer.call_count, 1)
        audit_command = append.await_args.args[1]
        self.assertEqual(
            (audit_command.dominant_underlying_source, audit_command.market_regime),
            ("PRIVATE_PAPER_TOMORROW", "UP"),
        )


if __name__ == "__main__":
    unittest.main()
