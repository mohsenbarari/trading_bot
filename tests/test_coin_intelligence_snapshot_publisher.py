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

    def _write_physical_gold(
        self,
        identity: str,
        at: datetime,
        *,
        price: int = 80_300_000,
    ) -> None:
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
                price=price,
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
                parse_confidence=1.0,
                parser_version="publisher-test-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="publisher-test-v1",
            ),
        )
        # Point-in-time snapshots exclude rows inserted after the evaluation
        # instant.  Keep this historical fixture temporally coherent instead
        # of inheriting the wall-clock insert time of the test run.
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (
                at.astimezone(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                derive_event_key("publisher-test", identity),
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

    def test_unchanged_input_still_refreshes_time_dependent_snapshot(self) -> None:
        self._write_physical_gold("quiet-market", self.now - timedelta(seconds=20))
        first = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        later = self.now + timedelta(seconds=60)
        second = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=later,
        )

        loaded = AtomicMarketSnapshotProvider(self.snapshot_path).load()
        self.assertEqual((first.status, second.status), ("PUBLISHED", "PUBLISHED"))
        self.assertNotEqual(first.snapshot_digest, second.snapshot_digest)
        self.assertEqual(second.generated_at_utc, "2026-08-04T10:01:00Z")
        self.assertEqual(loaded["generated_at_utc"], "2026-08-04T10:01:00Z")

    def test_same_key_price_correction_is_not_hidden_by_watermark(self) -> None:
        at = self.now - timedelta(seconds=20)
        self._write_physical_gold("edited-price", at, price=80_300_000)
        first = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        first_snapshot = AtomicMarketSnapshotProvider(self.snapshot_path).load()
        first_bahar = next(
            item
            for item in first_snapshot["rates"]["items"]
            if item["commodity_code"] == "BAHAR" and item["settlement_term"] == "CASH"
        )

        self._write_physical_gold("edited-price", at, price=81_000_000)
        second = publish_rate_ready_snapshot(
            market_store_path=self.store_path,
            snapshot_path=self.snapshot_path,
            as_of_utc=self.now,
        )
        second_snapshot = AtomicMarketSnapshotProvider(self.snapshot_path).load()
        second_bahar = next(
            item
            for item in second_snapshot["rates"]["items"]
            if item["commodity_code"] == "BAHAR" and item["settlement_term"] == "CASH"
        )

        self.assertEqual((first.status, second.status), ("PUBLISHED", "PUBLISHED"))
        self.assertEqual(first.input_watermark, second.input_watermark)
        self.assertNotEqual(first.snapshot_digest, second.snapshot_digest)
        self.assertNotEqual(
            first_bahar["estimated_project_price"],
            second_bahar["estimated_project_price"],
        )

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
