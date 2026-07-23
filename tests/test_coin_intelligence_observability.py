from __future__ import annotations

import unittest

from core.market_intelligence.contracts import ShadowObservation
from core.market_intelligence.observability import record_shadow_observation
from core.metrics import metrics_response_body, registry


class CoinIntelligenceObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_metrics_are_bounded_and_contain_no_raw_offer_data(self) -> None:
        record_shadow_observation(
            ShadowObservation(
                status="INFERRED",
                settlement="CASH",
                current_commodity="امام",
                inferred_commodity="ربع بهار",
                agrees_with_current=False,
                requires_user_confirmation=False,
                decision_reason="UNIQUE_HIGH_CONFIDENCE_RANGE_MATCH",
                bundle_version="bundle-version",
                snapshot_version="snapshot-version",
            )
        )

        rendered = metrics_response_body()

        self.assertIn('current_commodity="imam"', rendered)
        self.assertIn('inferred_commodity="quarter_bahar"', rendered)
        self.assertIn('comparison="disagree"', rendered)
        self.assertNotIn("امام", rendered)
        self.assertNotIn("ربع", rendered)
        self.assertNotIn("75800", rendered)
        self.assertNotIn("snapshot-version", rendered)


if __name__ == "__main__":
    unittest.main()
