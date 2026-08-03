from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
import unittest


UTC = timezone.utc
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "discover_melted_market_relationships_shadow.py"
)
MODULE = runpy.run_path(str(SCRIPT))
TargetPoint = MODULE["TargetPoint"]
SupportQuoteEvent = MODULE["SupportQuoteEvent"]
_build_support_quote_features = MODULE["_build_support_quote_features"]
_target_samples = MODULE["_target_samples"]


class MeltedRelationshipDiscoveryTests(unittest.TestCase):
    def test_target_realization_is_strictly_later_than_cutoff(self):
        points = [
            TargetPoint("PAPER:TOMORROW:NORMAL", NOW, 100.0),
            TargetPoint(
                "PAPER:TOMORROW:NORMAL", NOW + timedelta(minutes=5), 101.0
            ),
        ]
        samples = list(
            _target_samples(
                points,
                horizon=timedelta(minutes=5),
                max_target_age=timedelta(minutes=1),
                step=timedelta(minutes=1),
            )
        )
        self.assertEqual(len(samples), 1)
        available_at, _, realized_at, target_return = samples[0]
        self.assertGreater(realized_at, available_at)
        self.assertAlmostEqual(target_return, 100.0)

    def test_support_quote_features_never_include_later_quote(self):
        events = [
            SupportQuoteEvent(NOW - timedelta(minutes=2), "USD_HERAT:PAPER:TODAY", 100.0),
            SupportQuoteEvent(NOW - timedelta(minutes=1), "USD_HERAT:PAPER:TODAY", 101.0),
            SupportQuoteEvent(NOW + timedelta(minutes=1), "USD_HERAT:PAPER:TODAY", 120.0),
        ]
        features = _build_support_quote_features(
            events,
            [event.observed_at_utc for event in events],
            as_of_utc=NOW,
            window=timedelta(minutes=5),
        )
        self.assertEqual(features["quote_count"], 2)
        self.assertAlmostEqual(features["quote_return_bps"], 100.0)
        self.assertEqual(features["quote_staleness_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
