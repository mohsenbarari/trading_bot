from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.contracts import SnapshotUnavailableError
from core.market_intelligence.snapshot import AtomicJsonSnapshotProvider


class CoinIntelligenceSnapshotTests(unittest.TestCase):
    def test_valid_local_estimator_state_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            expected = {
                "generated_at_utc": "2026-07-23T12:00:00Z",
                "settlements": {"CASH": {"rates": []}},
            }
            path.write_text(
                json.dumps(expected, ensure_ascii=False),
                encoding="utf-8",
            )

            actual = AtomicJsonSnapshotProvider(path).load()

        self.assertEqual(actual, expected)

    def test_missing_or_invalid_snapshot_abstains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(
                SnapshotUnavailableError,
                "snapshot_file_unavailable",
            ):
                AtomicJsonSnapshotProvider(missing).load()

            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(
                SnapshotUnavailableError,
                "snapshot_json_invalid",
            ):
                AtomicJsonSnapshotProvider(invalid).load()

    def test_oversized_snapshot_is_rejected_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("12345", encoding="utf-8")

            with self.assertRaisesRegex(
                SnapshotUnavailableError,
                "snapshot_file_too_large",
            ):
                AtomicJsonSnapshotProvider(path, maximum_bytes=4).load()


if __name__ == "__main__":
    unittest.main()
