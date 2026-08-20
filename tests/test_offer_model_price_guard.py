from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.services.offer_model_price_guard import (
    BUY_PRICE_OUTLIER_MESSAGE,
    OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE,
    SELL_PRICE_OUTLIER_MESSAGE,
    evaluate_offer_model_price_guard,
    evaluate_offer_model_price_snapshot,
)


NOW = datetime(2026, 8, 20, 6, 10, tzinfo=timezone.utc)


def snapshot_for(
    code: str,
    *,
    settlement: str = "CASH",
    lower: int = 100_000,
    upper: int = 110_000,
    generated_at: datetime = NOW,
) -> dict:
    return {
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "rates": {
            "items": [
                {
                    "commodity_code": code,
                    "settlement_term": settlement,
                    "status": "ESTIMATED",
                    "estimated_project_price": (lower + upper) // 2,
                    "lower_project_price": lower,
                    "upper_project_price": upper,
                }
            ]
        },
    }


class OfferModelPriceSnapshotTests(unittest.TestCase):
    COMMODITIES = {
        "IMAM": "امام",
        "BAHAR": "بهار",
        "HALF_BAHAR": "نیم بهار",
        "HALF_LOW_DATE": "نیم تاریخ پایین",
        "QUARTER_BAHAR": "ربع بهار",
        "QUARTER_LOW_DATE": "ربع تاریخ پایین",
        "ONE_GRAM": "یک گرمی",
    }

    def decision(
        self,
        code: str,
        *,
        offer_type: str,
        price: int,
        opened_at: datetime | None = None,
        settlement: str = "cash",
        generated_at: datetime = NOW,
    ):
        return evaluate_offer_model_price_snapshot(
            snapshot_for(
                code,
                settlement="TOMORROW" if settlement == "tomorrow" else "CASH",
                generated_at=generated_at,
            ),
            commodity_name=self.COMMODITIES[code],
            settlement_type=settlement,
            offer_type=offer_type,
            proposed_price=price,
            now_utc=NOW,
            market_opened_at=opened_at,
        )

    def test_all_product_tolerances_enforce_only_outside_the_exact_boundary(self):
        for code, expected_bps in OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE.items():
            with self.subTest(code=code, side="sell"):
                sell_boundary = 110_000 * (10_000 + expected_bps) // 10_000
                allowed = self.decision(code, offer_type="sell", price=sell_boundary)
                rejected = self.decision(code, offer_type="sell", price=sell_boundary + 1)
                self.assertTrue(allowed.allowed)
                self.assertFalse(rejected.allowed)
                self.assertEqual(rejected.message, SELL_PRICE_OUTLIER_MESSAGE)
                self.assertEqual(rejected.effective_tolerance_bps, expected_bps)

            with self.subTest(code=code, side="buy"):
                numerator = 100_000 * (10_000 - expected_bps)
                buy_boundary = (numerator + 9_999) // 10_000
                allowed = self.decision(code, offer_type="buy", price=buy_boundary)
                rejected = self.decision(code, offer_type="buy", price=buy_boundary - 1)
                self.assertTrue(allowed.allowed)
                self.assertFalse(rejected.allowed)
                self.assertEqual(rejected.message, BUY_PRICE_OUTLIER_MESSAGE)
                self.assertEqual(rejected.effective_tolerance_bps, expected_bps)

    def test_first_fifteen_minutes_double_tolerance_and_boundary_is_exclusive(self):
        opening = NOW - timedelta(minutes=14, seconds=59)
        opening_decision = self.decision(
            "IMAM",
            offer_type="sell",
            price=111_100,
            opened_at=opening,
        )
        self.assertTrue(opening_decision.allowed)
        self.assertTrue(opening_decision.opening_window_applied)
        self.assertEqual(opening_decision.effective_tolerance_bps, 100)

        after_window = self.decision(
            "IMAM",
            offer_type="sell",
            price=111_100,
            opened_at=NOW - timedelta(minutes=15),
        )
        self.assertFalse(after_window.allowed)
        self.assertFalse(after_window.opening_window_applied)
        self.assertEqual(after_window.effective_tolerance_bps, 50)

    def test_exact_settlement_range_is_selected(self):
        decision = self.decision(
            "QUARTER_BAHAR",
            offer_type="buy",
            price=98_500,
            settlement="tomorrow",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.settlement_term, "TOMORROW")

    def test_stale_future_missing_and_unsupported_evidence_fail_open(self):
        stale = self.decision(
            "IMAM",
            offer_type="sell",
            price=999_999,
            generated_at=NOW - timedelta(seconds=121),
        )
        future = self.decision(
            "IMAM",
            offer_type="sell",
            price=999_999,
            generated_at=NOW + timedelta(seconds=1),
        )
        unsupported = evaluate_offer_model_price_snapshot(
            snapshot_for("IMAM"),
            commodity_name="طلای متفرقه",
            settlement_type="cash",
            offer_type="sell",
            proposed_price=999_999,
            now_utc=NOW,
            market_opened_at=None,
        )
        missing = evaluate_offer_model_price_snapshot(
            snapshot_for("IMAM", settlement="TOMORROW"),
            commodity_name="امام",
            settlement_type="cash",
            offer_type="sell",
            proposed_price=999_999,
            now_utc=NOW,
            market_opened_at=None,
        )

        self.assertEqual(stale.status, "ABSTAINED")
        self.assertEqual(future.status, "ABSTAINED")
        self.assertEqual(unsupported.reason, "COMMODITY_UNSUPPORTED")
        self.assertEqual(missing.reason, "MODEL_RANGE_UNAVAILABLE")
        self.assertTrue(all(item.allowed for item in (stale, future, unsupported, missing)))


class OfferModelPriceGuardLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_guard_fails_open_without_database_or_schedule_reads(self):
        db = SimpleNamespace(get=AsyncMock())
        with patch(
            "core.services.offer_model_price_guard.settings",
            SimpleNamespace(
                offer_model_price_guard_enabled=False,
                coin_intelligence_inference_snapshot_path="/safe/coin-rates.json",
            ),
        ), patch(
            "core.services.offer_model_price_guard.evaluate_current_market_schedule",
            new=AsyncMock(),
        ) as schedule:
            decision = await evaluate_offer_model_price_guard(
                db,
                commodity_id=1,
                settlement_type="cash",
                offer_type="sell",
                proposed_price=999_999,
                now_utc=NOW,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "FEATURE_DISABLED")
        db.get.assert_not_awaited()
        schedule.assert_not_awaited()

    async def test_unconfigured_snapshot_fails_open_without_database_or_schedule_reads(self):
        db = SimpleNamespace(get=AsyncMock())
        with patch(
            "core.services.offer_model_price_guard.settings",
            SimpleNamespace(
                offer_model_price_guard_enabled=True,
                coin_intelligence_inference_snapshot_path=None,
            ),
        ), patch(
            "core.services.offer_model_price_guard.evaluate_current_market_schedule",
            new=AsyncMock(),
        ) as schedule:
            decision = await evaluate_offer_model_price_guard(
                db,
                commodity_id=1,
                settlement_type="cash",
                offer_type="sell",
                proposed_price=999_999,
                now_utc=NOW,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "SNAPSHOT_PATH_UNCONFIGURED")
        db.get.assert_not_awaited()
        schedule.assert_not_awaited()

    async def test_loaded_snapshot_uses_catalog_name_and_real_open_transition(self):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(name="امام")))
        evaluation = SimpleNamespace(
            is_open=True,
            current_transition_at=NOW - timedelta(minutes=10),
        )
        with patch(
            "core.services.offer_model_price_guard.settings",
            SimpleNamespace(
                offer_model_price_guard_enabled=True,
                coin_intelligence_inference_snapshot_path="/safe/coin-rates.json",
                offer_model_price_guard_max_snapshot_age_seconds=120,
            ),
        ), patch(
            "core.services.offer_model_price_guard.AtomicMarketSnapshotProvider.load",
            return_value=snapshot_for("IMAM"),
        ), patch(
            "core.services.offer_model_price_guard.evaluate_current_market_schedule",
            new=AsyncMock(return_value=evaluation),
        ) as schedule:
            decision = await evaluate_offer_model_price_guard(
                db,
                commodity_id=71,
                settlement_type="cash",
                offer_type="sell",
                proposed_price=111_101,
                now_utc=NOW,
            )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.opening_window_applied)
        db.get.assert_awaited_once()
        schedule.assert_awaited_once()

    async def test_recent_close_transition_does_not_double_tolerance(self):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(name="امام")))
        evaluation = SimpleNamespace(
            is_open=False,
            current_transition_at=NOW - timedelta(minutes=10),
        )
        with patch(
            "core.services.offer_model_price_guard.settings",
            SimpleNamespace(
                offer_model_price_guard_enabled=True,
                coin_intelligence_inference_snapshot_path="/safe/coin-rates.json",
                offer_model_price_guard_max_snapshot_age_seconds=120,
            ),
        ), patch(
            "core.services.offer_model_price_guard.AtomicMarketSnapshotProvider.load",
            return_value=snapshot_for("IMAM"),
        ):
            decision = await evaluate_offer_model_price_guard(
                db,
                commodity_id=71,
                settlement_type="cash",
                offer_type="sell",
                proposed_price=111_000,
                market_evaluation=evaluation,
                now_utc=NOW,
            )

        self.assertFalse(decision.allowed)
        self.assertFalse(decision.opening_window_applied)
        self.assertEqual(decision.effective_tolerance_bps, 50)

    async def test_schedule_failure_fails_open_instead_of_blocking_registration(self):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(name="امام")))
        with patch(
            "core.services.offer_model_price_guard.settings",
            SimpleNamespace(
                offer_model_price_guard_enabled=True,
                coin_intelligence_inference_snapshot_path="/safe/coin-rates.json",
                offer_model_price_guard_max_snapshot_age_seconds=120,
            ),
        ), patch(
            "core.services.offer_model_price_guard.AtomicMarketSnapshotProvider.load",
            return_value=snapshot_for("IMAM"),
        ), patch(
            "core.services.offer_model_price_guard.evaluate_current_market_schedule",
            new=AsyncMock(side_effect=RuntimeError("schedule unavailable")),
        ):
            decision = await evaluate_offer_model_price_guard(
                db,
                commodity_id=71,
                settlement_type="cash",
                offer_type="sell",
                proposed_price=999_999,
                now_utc=NOW,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "MARKET_SCHEDULE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
