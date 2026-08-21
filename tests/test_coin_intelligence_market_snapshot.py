"""Point-in-time and atomic-publication tests for P4-A snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotError,
    MarketSnapshotUnavailable,
    build_market_snapshot,
    publish_market_snapshot_atomically,
    validate_market_snapshot,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class MarketSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.market_store = Path(self.tempdir.name) / "market.sqlite3"
        self.snapshot_path = Path(self.tempdir.name) / "snapshot.json"
        self.connection = connect_market_store(self.market_store)
        initialize_market_store(self.connection)
        self.now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def _store(
        self,
        *,
        identity: str,
        source_code: str,
        instrument: str,
        price: int | float,
        price_unit: str,
        event_time: datetime,
        available_time: datetime | None = None,
        settlement: str = "UNKNOWN",
        trade_form: str = "UNKNOWN",
        event_type: str = "QUOTE",
        side: str = "MID",
    ) -> None:
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=derive_event_key("snapshot-test", identity),
                source_code=source_code,
                source_family="EXTERNAL_MARKET",
                event_time_utc=event_time,
                available_at_utc=available_time or event_time,
                instrument=instrument,
                market_label="TEST_MARKET",
                settlement_term=settlement,
                trade_form=trade_form,
                event_type=event_type,
                side=side,
                price=price,
                price_unit=price_unit,
                currency="USD" if instrument == "XAUUSD" else "TOMAN",
                parse_confidence=1.0,
                parser_version="snapshot-test-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="snapshot-test-v1",
            ),
        )
        self.connection.commit()

    def test_snapshot_obeys_event_and_availability_cutoffs(self) -> None:
        self._store(
            identity="paper-known-late",
            source_code="MELTED_FLOW",
            instrument="MELTED_GOLD_FLOW",
            price=80_000_000,
            price_unit="TOMAN_PER_MESGHAL_750",
            event_time=self.now - timedelta(minutes=1),
            available_time=self.now + timedelta(minutes=1),
            settlement="TOMORROW",
            trade_form="PAPER_NORMAL",
            event_type="TRADE",
            side="BUY",
        )

        before_available = build_market_snapshot(
            self.connection,
            as_of_utc=self.now,
        )
        after_available = build_market_snapshot(
            self.connection,
            as_of_utc=self.now + timedelta(minutes=2),
        )

        self.assertEqual(
            before_available["signals"]["MELTED_PAPER_TOMORROW"]["status"],
            "MISSING",
        )
        observed = after_available["signals"]["MELTED_PAPER_TOMORROW"]
        self.assertEqual(observed["status"], "FRESH")
        self.assertEqual(observed["latest_price"], 80_000_000.0)
        self.assertEqual(observed["event_counts"], {"TRADE": 1})

    def test_snapshot_keeps_herat_and_usdt_separate(self) -> None:
        event_time = self.now - timedelta(seconds=20)
        self._store(
            identity="herat",
            source_code="USD_HERAT",
            instrument="USD_HERAT",
            price=114_000,
            price_unit="TOMAN_PER_USD",
            event_time=event_time,
            settlement="TOMORROW",
            trade_form="PAPER_NORMAL",
            event_type="OFFER",
            side="BUY",
        )
        self._store(
            identity="usdt",
            source_code="USDT_PROVIDER",
            instrument="USDT_IRT",
            price=116_000,
            price_unit="TOMAN_PER_USDT",
            event_time=event_time,
        )

        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)

        herat = snapshot["signals"]["USD_HERAT_TOMORROW"]
        usdt = snapshot["signals"]["USDT_IRT"]
        self.assertEqual(herat["latest_price"], 114_000.0)
        self.assertEqual(usdt["latest_price"], 116_000.0)
        self.assertNotEqual(herat["price_unit"], usdt["price_unit"])
        self.assertEqual(
            usdt["method"],
            "external_reference_not_herat_substitution_v1",
        )

    def test_snapshot_exposes_unsettled_aggregate_paper_without_relabeling_it(self) -> None:
        self._store(
            identity="aggregate-paper-unsettled",
            source_code="MELTED_AGGREGATE",
            instrument="MELTED_GOLD_AGGREGATE",
            price=80_500_000,
            price_unit="TOMAN_PER_MESGHAL_750",
            event_time=self.now - timedelta(seconds=20),
            settlement="UNKNOWN",
            trade_form="PAPER_NORMAL",
            event_type="QUOTE",
        )

        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)
        signal = snapshot["signals"]["MELTED_PAPER_UNSPECIFIED"]
        physical = snapshot["signals"]["MELTED_PHYSICAL_UNSPECIFIED"]

        self.assertEqual(signal["status"], "FRESH")
        self.assertEqual(signal["latest_price"], 80_500_000.0)
        self.assertEqual(physical["status"], "MISSING")
        rate = next(
            item
            for item in snapshot["rates"]["items"]
            if item["commodity_code"] == "BAHAR"
            and item["settlement_term"] == "CASH"
        )
        self.assertEqual(rate["confidence"], "LOW_PAPER_FALLBACK")
        self.assertEqual(
            rate["underlying_source"],
            "PUBLIC_PAPER_UNSPECIFIED_CASH_BRIDGE",
        )

    def test_snapshot_embeds_structural_low_date_range_without_coin_offer(self) -> None:
        self._store(
            identity="private-physical",
            source_code="PRIVATE_GOLD_CHANNEL",
            instrument="MELTED_GOLD_PRIVATE",
            price=80_300_000,
            price_unit="TOMAN_PER_MESGHAL_750",
            event_time=self.now - timedelta(seconds=20),
            settlement="TODAY",
            trade_form="PHYSICAL",
            event_type="QUOTE",
        )

        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)
        rate = next(
            item for item in snapshot["rates"]["items"]
            if item["commodity_code"] == "BAHAR" and item["settlement_term"] == "CASH"
        )
        self.assertEqual((snapshot["snapshot_status"], rate["status"], rate["estimated_project_price"]), ("PARTIAL_COIN_RATE_STATE", "ESTIMATED", 180_900))
        self.assertEqual(rate["underlying_age_seconds"], 20.0)

    def test_rate_schema_enforces_confidence_age_relations_and_counts(self) -> None:
        self._store(
            identity="private-physical-schema",
            source_code="PRIVATE_GOLD_CHANNEL",
            instrument="MELTED_GOLD_PRIVATE",
            price=80_300_000,
            price_unit="TOMAN_PER_MESGHAL_750",
            event_time=self.now - timedelta(seconds=20),
            settlement="TODAY",
            trade_form="PHYSICAL",
            event_type="QUOTE",
        )
        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)
        rate = next(
            item
            for item in snapshot["rates"]["items"]
            if item["commodity_code"] == "BAHAR" and item["settlement_term"] == "CASH"
        )
        self.assertEqual(rate["confidence"], "MEDIUM")
        self.assertIsNone(rate["anchor_age_seconds"])
        validate_market_snapshot(snapshot)

        for field, value, reason in (
            ("underlying_age_seconds", None, "snapshot_underlying_age_invalid"),
            ("confidence", "LOW", "snapshot_rate_confidence_invalid"),
            ("anchor_age_seconds", 1.0, "snapshot_medium_confidence_anchor_invalid"),
        ):
            with self.subTest(field=field):
                mutated = json.loads(json.dumps(snapshot))
                target = next(
                    item
                    for item in mutated["rates"]["items"]
                    if item["commodity_code"] == "BAHAR" and item["settlement_term"] == "CASH"
                )
                target[field] = value
                with self.assertRaisesRegex(MarketSnapshotError, reason):
                    validate_market_snapshot(mutated)

        mutated = json.loads(json.dumps(snapshot))
        mutated["rates"]["estimated_count"] += 1
        with self.assertRaisesRegex(MarketSnapshotError, "snapshot_estimated_count_invalid"):
            validate_market_snapshot(mutated)

    def test_atomic_publish_preserves_valid_snapshot_on_invalid_replacement(self) -> None:
        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)
        digest = publish_market_snapshot_atomically(self.snapshot_path, snapshot)
        loaded = AtomicMarketSnapshotProvider(self.snapshot_path).load()

        self.assertEqual(len(digest), 64)
        self.assertEqual(loaded["generated_at_utc"], "2026-08-04T10:00:00Z")
        invalid = dict(snapshot)
        invalid["raw_text"] = "must never be published"
        with self.assertRaises(MarketSnapshotError):
            publish_market_snapshot_atomically(self.snapshot_path, invalid)
        self.assertEqual(
            AtomicMarketSnapshotProvider(self.snapshot_path).load()["generated_at_utc"],
            "2026-08-04T10:00:00Z",
        )
        self.snapshot_path.write_text('{"schema_version":999}', encoding="utf-8")
        with self.assertRaises(MarketSnapshotUnavailable):
            AtomicMarketSnapshotProvider(self.snapshot_path).load()

    def test_snapshot_provider_rejects_alias_tamper_and_insecure_metadata(self) -> None:
        snapshot = build_market_snapshot(self.connection, as_of_utc=self.now)
        publish_market_snapshot_atomically(self.snapshot_path, snapshot)
        payload = self.snapshot_path.read_bytes()
        digest = sha256(payload).hexdigest()
        loaded = AtomicMarketSnapshotProvider(
            self.snapshot_path,
            expected_sha256=digest,
        ).load()
        self.assertEqual(loaded["generated_at_utc"], "2026-08-04T10:00:00Z")

        with self.assertRaisesRegex(MarketSnapshotUnavailable, "snapshot_digest_mismatch"):
            AtomicMarketSnapshotProvider(
                self.snapshot_path,
                expected_sha256="0" * 64,
            ).load()

        alias = self.snapshot_path.with_name("snapshot-link.json")
        alias.symlink_to(self.snapshot_path)
        with self.assertRaises(MarketSnapshotUnavailable):
            AtomicMarketSnapshotProvider(alias).load()

        hardlink = self.snapshot_path.with_name("snapshot-hardlink.json")
        os.link(self.snapshot_path, hardlink)
        with self.assertRaisesRegex(MarketSnapshotUnavailable, "snapshot_file_invalid"):
            AtomicMarketSnapshotProvider(self.snapshot_path).load()
        hardlink.unlink()

        self.snapshot_path.chmod(0o666)
        with self.assertRaisesRegex(MarketSnapshotUnavailable, "snapshot_file_invalid"):
            AtomicMarketSnapshotProvider(self.snapshot_path).load()


if __name__ == "__main__":
    unittest.main()
