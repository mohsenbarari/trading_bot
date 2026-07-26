from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.contracts import SnapshotUnavailableError
from core.market_intelligence.service import CoinIntelligenceShadowService


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _rate(
    commodity: str,
    center: int,
    lower: int,
    upper: int,
    confidence: str = "MEDIUM",
) -> dict:
    return {
        "commodity_name": commodity,
        "status": "ESTIMATED",
        "estimated_project_price": center,
        "confidence": confidence,
        "training_sample_count": 40,
        "tolerance": {
            "lower_project_price": lower,
            "upper_project_price": upper,
        },
    }


def _state(*, generated_at: datetime = NOW) -> dict:
    return {
        "generated_at_utc": generated_at.isoformat().replace(
            "+00:00",
            "Z",
        ),
        "settlements": {
            "CASH": {
                "rates": [
                    _rate("امام", 185_000, 182_000, 188_000),
                    _rate("بهار", 178_000, 175_000, 181_000, "LOW"),
                    _rate("نیم بهار", 94_000, 92_000, 96_000),
                    _rate("نیم تاریخ پایین", 87_500, 87_000, 88_000),
                    _rate("ربع بهار", 54_000, 52_500, 56_000),
                    _rate(
                        "ربع تاریخ پایین",
                        44_000,
                        43_500,
                        44_500,
                        "LOW",
                    ),
                    _rate("یک گرمی", 29_000, 28_500, 29_500),
                ]
            },
            "TOMORROW": {"rates": []},
        },
    }


class StaticProvider:
    def __init__(self, value: dict) -> None:
        self.value = value

    def load(self) -> dict:
        return self.value


class UnavailableProvider:
    def load(self) -> dict:
        raise SnapshotUnavailableError("unavailable")


class CoinIntelligenceShadowServiceTests(unittest.TestCase):
    def test_canonical_rate_is_exposed_as_non_authoritative_range(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(_state())
        )

        prediction = service.estimate_canonical_rate(
            commodity="ربع بهار",
            settlement="cash",
            now=NOW,
        )

        self.assertEqual(prediction.status, "ESTIMATED")
        self.assertEqual(prediction.center_project_price, 54_000)
        self.assertEqual(prediction.lower_project_price, 52_500)
        self.assertEqual(prediction.upper_project_price, 56_000)
        self.assertEqual(prediction.commodity, "ربع بهار")
        self.assertEqual(prediction.settlement, "CASH")

    def test_canonical_rate_rejects_stale_snapshot(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(
                _state(generated_at=NOW - timedelta(days=1))
            )
        )

        prediction = service.estimate_canonical_rate(
            commodity="امام",
            settlement="cash",
            now=NOW,
        )

        self.assertEqual(prediction.status, "STALE_INPUT")
        self.assertIsNone(prediction.center_project_price)

    def test_canonical_rate_does_not_map_alias_to_output(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(_state())
        )

        prediction = service.estimate_canonical_rate(
            commodity="امامی",
            settlement="cash",
            now=NOW,
        )

        self.assertEqual(prediction.status, "NO_MARKET_DATA")
        self.assertEqual(
            prediction.decision_reason,
            "NON_CANONICAL_COMMODITY",
        )

    def test_unique_price_is_observed_without_returning_database_id(
        self,
    ) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(_state())
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(observation.status, "INFERRED")
        self.assertEqual(observation.inferred_commodity, "ربع بهار")
        self.assertFalse(observation.agrees_with_current)
        self.assertFalse(observation.requires_user_confirmation)
        self.assertFalse(hasattr(observation, "commodity_id"))

    def test_stale_snapshot_cannot_infer(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(
                _state(generated_at=NOW - timedelta(days=1))
            )
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(observation.status, "STALE_INPUT")
        self.assertIsNone(observation.inferred_commodity)
        self.assertTrue(observation.requires_user_confirmation)

    def test_missing_snapshot_is_a_bounded_shadow_outcome(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=UnavailableProvider()
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(observation.status, "SNAPSHOT_UNAVAILABLE")
        self.assertIsNone(observation.inferred_commodity)
        self.assertTrue(observation.requires_user_confirmation)

    def test_snapshot_alias_cannot_become_model_output(self) -> None:
        state = _state()
        state["settlements"]["CASH"]["rates"] = [
            _rate("امامی", 54_000, 52_500, 56_000)
        ]
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(state)
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(observation.status, "NO_MARKET_DATA")
        self.assertIsNone(observation.inferred_commodity)
        self.assertTrue(observation.requires_user_confirmation)

    def test_unknown_settlement_abstains_before_loading_bundle(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=UnavailableProvider(),
            bundle_path="/does/not/exist",
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="later",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(
            observation.status,
            "UNSUPPORTED_MARKET_DIMENSION",
        )

    def test_paper_form_is_not_scored_against_physical_rate_bands(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(_state())
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            trade_form="paper",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(observation.status, "NO_MARKET_DATA")
        self.assertIsNone(observation.inferred_commodity)
        self.assertEqual(
            observation.decision_reason,
            "PAPER_RATE_SNAPSHOT_NOT_AVAILABLE",
        )

    def test_unknown_trade_form_abstains_before_loading_bundle(self) -> None:
        service = CoinIntelligenceShadowService(
            snapshot_provider=UnavailableProvider(),
            bundle_path="/does/not/exist",
        )

        observation = service.observe_implicit_commodity(
            price=54_200,
            settlement="cash",
            trade_form="forward",
            current_commodity="امام",
            now=NOW,
        )

        self.assertEqual(
            observation.status,
            "UNSUPPORTED_MARKET_DIMENSION",
        )
        self.assertEqual(
            observation.decision_reason,
            "UNSUPPORTED_TRADE_FORM",
        )


if __name__ == "__main__":
    unittest.main()
