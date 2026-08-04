"""Contract tests for the shared shadow observation library."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.market_intelligence.coin_catalog import CatalogCoinCommodityInference
from core.market_intelligence.coin_inference_shadow import observe_coin_inference_shadow


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
                "core.market_intelligence.coin_inference_shadow.infer_coin_commodity_from_published_snapshot",
                return_value=SimpleNamespace(),
            ) as infer,
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
        self.assertEqual(infer.await_count if isinstance(infer, AsyncMock) else infer.call_count, 1)
        self.assertEqual(append.await_args.args[1].source_surface, "TELEGRAM_BOT")
        self.assertEqual(append.await_args.args[1].submitted_project_price, 186_800)


if __name__ == "__main__":
    unittest.main()
