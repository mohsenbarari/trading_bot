"""Tests for the explicit, read-only Snapshot publisher boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_snapshot import AtomicMarketSnapshotProvider
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.snapshot_publisher import (
    MarketSnapshotPublisherError,
    publish_rate_ready_snapshot,
)


class SnapshotPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "market.sqlite3"
        self.snapshot_path = Path(self.tempdir.name) / "snapshot.json"
        self.now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
        self.connection = connect_market_store(self.store_path)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def _write_physical_gold(self, identity: str, at: datetime) -> None:
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=derive_event_key("publisher-test", identity),
                source_code="PRIVATE_GOLD_CHANNEL",
                source_family="TELEGRAM_PRIVATE",
                event_time_utc=at,
                available_at_utc=at,
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PHYSICAL",
                settlement_term="TODAY",
                trade_form="PHYSICAL",
                event_type="QUOTE",
                side="MID",
                price=80_300_000,
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
                parse_confidence=1.0,
                parser_version="publisher-test-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="publisher-test-v1",
            ),
        )
        self.connection.commit()

    def test_publishes_rate_ready_snapshot_from_read_only_store(self) -> None:
        self._write_physical_gold("fresh", self.now - timedelta(seconds=20))
        result = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        loaded = AtomicMarketSnapshotProvider(self.snapshot_path).load()
        self.assertEqual(
            (result.status, len(result.snapshot_digest), result.estimated_rate_count),
            ("PUBLISHED", 64, 3),
        )
        self.assertEqual(loaded["generated_at_utc"], "2026-08-04T10:00:00Z")

    def test_empty_store_does_not_replace_the_last_valid_snapshot(self) -> None:
        self._write_physical_gold("initial", self.now - timedelta(seconds=20))
        first = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        empty_path = Path(self.tempdir.name) / "empty.sqlite3"
        empty = connect_market_store(empty_path)
        initialize_market_store(empty)
        empty.close()
        result = publish_rate_ready_snapshot(
            market_store_path=empty_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        self.assertEqual(
            (result.status, result.reason),
            ("NOT_RATE_READY", "NO_ESTIMATED_COIN_RATES"),
        )
        self.assertEqual(
            AtomicMarketSnapshotProvider(self.snapshot_path).load()["generated_at_utc"],
            "2026-08-04T10:00:00Z",
        )
        self.assertIsNotNone(first.snapshot_digest)

    def test_missing_store_or_same_target_fails_before_creating_an_artifact(self) -> None:
        missing = Path(self.tempdir.name) / "missing.sqlite3"
        with self.assertRaisesRegex(MarketSnapshotPublisherError, "store_unavailable"):
            publish_rate_ready_snapshot(
                market_store_path=missing,
                snapshot_path=self.snapshot_path,
                as_of_utc=self.now,
            )
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(MarketSnapshotPublisherError, "store_target_conflict"):
            publish_rate_ready_snapshot(
                market_store_path=self.store_path,
                snapshot_path=self.store_path,
                as_of_utc=self.now,
            )


if __name__ == "__main__":
    unittest.main()
