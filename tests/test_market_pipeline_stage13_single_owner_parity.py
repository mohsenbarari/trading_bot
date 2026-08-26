from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.capture_event_adapter import initialize_capture_adapter
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.shadow_parity import verify_parity_report
from core.market_intelligence.single_owner_parity import (
    SingleOwnerParityError,
    _copy_exact_prefix,
    compare_facts,
    compare_market_stores,
    compare_snapshots,
    exclusive_existing_lock,
    read_private_key,
    run_single_owner_parity,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_KEY = b"stage13-identity-key-material-at-least-32-bytes"
SIGNING_KEY = b"stage13-signing-key-material-at-least-32-bytes"


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def market_event(when: datetime) -> dict[str, object]:
    text = "4630.10 test-private-raw-marker"
    return {
        "schema": "market_channel_event",
        "schema_version": "1.0",
        "event_id": "70000000-0000-7000-8000-000000000001",
        "event_type": "message_created",
        "source": {
            "market": "coin_intelligence",
            "source_id": "XAUUSD",
            "source_family": "TELEGRAM_PUBLIC",
            "parser_profile": "XAUUSD",
        },
        "message": {
            "message_id": "987654321",
            "published_at_utc": stamp(when),
            "edited_at_utc": None,
            "text": text,
            "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "entities": [],
            "is_forwarded": False,
        },
        "producer": {
            "available_at_utc": stamp(when + timedelta(seconds=1)),
            "is_backfill": False,
        },
    }


def coin_event(when: datetime) -> dict[str, object]:
    return {
        "schema": "coin_group_event",
        "schema_version": "2.0",
        "event_id": "60000000-0000-7000-8000-000000000001",
        "event_type": "message_created",
        "source": {"market": "coin", "source_id": "GROUP_1"},
        "message": {
            "message_id": "123456789",
            "published_at_utc": stamp(when),
            "edited_at_utc": None,
            "text": "امام فروش نقدی 188600 پنج تا",
            "content_type": "text",
            "is_forwarded": False,
            "is_backfill": False,
            "sender": {
                "peer_id": "sensitive-peer-identity",
                "kind": "user",
                "display_name": None,
            },
            "reply": {"status": "not_reply", "message_id": None},
        },
        "producer": {"available_at_utc": stamp(when + timedelta(seconds=1))},
    }


class SingleOwnerParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.market_store = self.root / "source" / "market.sqlite3"
        self.staging_store = self.root / "source" / "capture.sqlite3"
        market = connect_market_store(self.market_store)
        try:
            initialize_market_store(market)
        finally:
            market.close()
        staging = connect_coin_group_staging(
            self.staging_store, repository_root=REPO_ROOT
        )
        try:
            initialize_capture_adapter(staging)
        finally:
            staging.close()
        self.lock = self.root / "source" / "writer.lock"
        self.lock.write_bytes(b"")
        self.lock.chmod(0o600)
        self.market_spool = self.root / "capture" / "market"
        self.coin_spool = self.root / "capture" / "coin"
        self.market_spool.mkdir(parents=True)
        self.coin_spool.mkdir(parents=True)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir(mode=0o700)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.when = self.now - timedelta(minutes=1)
        day = self.when.date().isoformat()
        old_market = market_event(self.now - timedelta(minutes=10))
        old_market["event_id"] = "70000000-0000-7000-8000-000000000002"
        old_market["message"]["message_id"] = "987654320"
        (self.market_spool / f"events-{day}.jsonl").write_text(
            json.dumps(old_market, ensure_ascii=False)
            + "\n"
            + json.dumps(market_event(self.when), ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        (self.coin_spool / f"events-{day}.jsonl").write_text(
            json.dumps(coin_event(self.when), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_replay(self, *, artifact_name: str = "artifacts") -> tuple[dict[str, object], Path]:
        artifact = self.root / artifact_name
        result = run_single_owner_parity(
            repository_root=REPO_ROOT,
            baseline_code_root=REPO_ROOT,
            candidate_code_root=REPO_ROOT,
            baseline_market_store=self.market_store,
            baseline_staging_store=self.staging_store,
            baseline_writer_lock=self.lock,
            market_spool_dir=self.market_spool,
            coin_spool_dir=self.coin_spool,
            scratch_root=self.scratch,
            artifact_dir=artifact,
            identity_key=IDENTITY_KEY,
            signing_key=SIGNING_KEY,
            signing_key_id="stage13-test:v1",
            window_start=self.now - timedelta(minutes=2),
            window_end=self.now,
            python_executable=Path(sys.executable).resolve(),
            maximum_records=100,
            lock_timeout_seconds=0.1,
            subprocess_timeout_seconds=60,
        )
        return result, artifact

    def test_same_release_replay_is_redacted_signed_hold_and_cleans_raw_workspace(self):
        result, artifact = self.run_replay()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["severity_1_count"], 0)
        self.assertEqual(result["severity_2_count"], 0)
        self.assertEqual(
            result["promotion_recommendation"],
            "HOLD_STAGE12_LIVE_PARITY_REQUIRED",
        )
        self.assertEqual(sorted(path.name for path in artifact.iterdir()), ["capture-manifest.json", "report.json"])
        self.assertEqual((artifact.stat().st_mode & 0o777), 0o700)
        for path in artifact.iterdir():
            self.assertEqual((path.stat().st_mode & 0o777), 0o600)
        report = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(verify_parity_report(report, key=SIGNING_KEY))
        self.assertEqual(report["capture_complete_record_count"], 3)
        self.assertEqual(report["capture_window_record_count"], 2)
        self.assertEqual(report["replay_record_count"], 2)
        for lane in report["lanes"].values():
            self.assertEqual(lane["ingest_counters"]["records"], 2)
            self.assertEqual(lane["ingest_counters"]["stale_market_skipped"], 0)
        tampered = deepcopy(report)
        tampered["capture_window_record_count"] += 1
        self.assertFalse(verify_parity_report(tampered, key=SIGNING_KEY))
        serialized = json.dumps(
            {
                "report": report,
                "manifest": json.loads((artifact / "capture-manifest.json").read_text(encoding="utf-8")),
            },
            ensure_ascii=False,
        )
        self.assertNotIn("test-private-raw-marker", serialized)
        self.assertNotIn("sensitive-peer-identity", serialized)
        self.assertNotIn("987654321", serialized)
        self.assertNotIn("123456789", serialized)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_exact_prefix_freeze_ignores_concurrent_append(self):
        source = self.root / "append-race.jsonl"
        destination = self.root / "frozen.jsonl"
        original = b'{"first":1}\n'
        appended = b'{"later":2}\n'
        source.write_bytes(original)
        real_read = os.read
        invoked = False

        def append_before_read(descriptor: int, size: int) -> bytes:
            nonlocal invoked
            if not invoked:
                invoked = True
                with source.open("ab") as handle:
                    handle.write(appended)
                    handle.flush()
                    os.fsync(handle.fileno())
            return real_read(descriptor, size)

        with patch("core.market_intelligence.single_owner_parity.os.read", side_effect=append_before_read):
            _device, _inode, frozen_size = _copy_exact_prefix(source, destination)
        self.assertEqual(frozen_size, len(original))
        self.assertEqual(destination.read_bytes(), original)
        self.assertEqual(source.read_bytes(), original + appended)

    def test_complete_corrupt_record_fails_closed_without_artifact(self):
        path = next(self.coin_spool.iterdir())
        with path.open("ab") as handle:
            handle.write(b"{not-json}\n")
        artifact = self.root / "failed-artifacts"
        with self.assertRaisesRegex(SingleOwnerParityError, "complete_record_invalid"):
            self.run_replay(artifact_name=artifact.name)
        self.assertFalse(artifact.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_busy_writer_lock_fails_without_waiting(self):
        descriptor = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(SingleOwnerParityError, "writer_lock_busy"):
                with exclusive_existing_lock(self.lock, timeout_seconds=0):
                    self.fail("lock unexpectedly acquired")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_private_key_allows_service_group_read_but_rejects_world_access(self):
        key_path = self.root / "service-key"
        key_path.write_bytes(IDENTITY_KEY)
        key_path.chmod(0o440)
        self.assertEqual(read_private_key(key_path, field="test_key"), IDENTITY_KEY)
        key_path.chmod(0o444)
        with self.assertRaisesRegex(SingleOwnerParityError, "permissions_invalid"):
            read_private_key(key_path, field="test_key")

    def test_fact_differences_are_classified_without_financial_values(self):
        baseline = {
            "a" * 64: {
                "unit": ("UNIT_A", "IRT", "COIN"),
                "lifecycle": ("OFFER", "ELIGIBLE", 0),
                "economic": ("GROUP_1", "GROUP", "COIN_IMAM", "BOOK", "CASH", "PHYSICAL", "SELL", 188600.0, 5.0),
            }
        }
        candidate = deepcopy(baseline)
        candidate["a" * 64]["unit"] = ("UNIT_B", "IRT", "COIN")
        candidate["a" * 64]["lifecycle"] = ("TRADE", "ELIGIBLE", 0)
        candidate["a" * 64]["economic"] = (*candidate["a" * 64]["economic"][:-2], 199999.0, 5.0)
        result = compare_facts(baseline, candidate)
        self.assertEqual(
            set(result["difference_counts"]),
            {"FACT_LIFECYCLE_MISMATCH", "FACT_PARSER_MISMATCH", "FACT_UNIT_MISMATCH"},
        )
        serialized = json.dumps(result)
        self.assertNotIn("188600", serialized)
        self.assertNotIn("199999", serialized)

    def test_final_store_comparison_catches_added_unit_lifecycle_and_parser_drift(self):
        baseline_path = self.root / "compare" / "baseline.sqlite3"
        candidate_path = self.root / "compare" / "candidate.sqlite3"
        connection = connect_market_store(baseline_path)
        try:
            initialize_market_store(connection)
            upsert_observation(
                connection,
                MarketObservation(
                    event_key=derive_event_key("stage13", "shared"),
                    source_code="GROUP_1",
                    source_family="GROUP",
                    event_time_utc=self.when,
                    available_at_utc=self.when + timedelta(seconds=1),
                    instrument="COIN_IMAM",
                    market_label="COIN_GROUP_OFFER",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type="OFFER",
                    side="SELL",
                    price="188600",
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    currency="IRT",
                    quantity="5",
                    quantity_unit="COIN",
                    parse_confidence=1.0,
                    parser_version="baseline-v1",
                    quality_state="ELIGIBLE",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        shutil.copyfile(baseline_path, candidate_path)
        candidate = sqlite3.connect(candidate_path)
        try:
            candidate.execute(
                "UPDATE market_observations SET price_unit=?,event_type=?,price_num=?",
                ("TOMAN_PER_COIN", "TRADE", 199999.0),
            )
            candidate.commit()
        finally:
            candidate.close()
        # Add a valid extra fact through the canonical writer.
        candidate = connect_market_store(candidate_path)
        try:
            upsert_observation(
                candidate,
                MarketObservation(
                    event_key=derive_event_key("stage13", "added"),
                    source_code="XAUUSD",
                    source_family="EXTERNAL_MARKET",
                    event_time_utc=self.when,
                    available_at_utc=self.when + timedelta(seconds=1),
                    instrument="XAUUSD",
                    market_label="GLOBAL_SPOT",
                    settlement_term="SPOT",
                    trade_form="NOT_APPLICABLE",
                    event_type="QUOTE",
                    side="MID",
                    price="4630.1",
                    price_unit="USD_PER_TROY_OUNCE",
                    currency="USD",
                    parse_confidence=1.0,
                    parser_version="candidate-v1",
                    quality_state="ELIGIBLE",
                ),
            )
            candidate.commit()
        finally:
            candidate.close()
        result = compare_market_stores(
            baseline_path,
            candidate_path,
            identity_key=IDENTITY_KEY,
        )
        self.assertEqual(
            result["difference_counts"],
            {
                "CANDIDATE_FACT_ADDED": 1,
                "FACT_LIFECYCLE_MISMATCH": 1,
                "FACT_PARSER_MISMATCH": 1,
                "FACT_UNIT_MISMATCH": 1,
            },
        )
        self.assertEqual(
            result["difference_counts_by_source"]["CANDIDATE_FACT_ADDED"],
            {"XAUUSD": 1},
        )
        self.assertEqual(
            result["difference_counts_by_instrument"]["FACT_UNIT_MISMATCH"],
            {"COIN_IMAM": 1},
        )
        serialized = json.dumps(result)
        self.assertNotIn("188600", serialized)
        self.assertNotIn("199999", serialized)

    def test_external_snapshot_metadata_is_not_a_consumed_value_mismatch(self):
        signal = {
            "status": "FRESH",
            "price_unit": "USD_PER_TROY_OUNCE",
            "latest_price": 4630.1,
            "weighted_median_price": 4630.0,
            "mean_price": 4630.0,
            "median_price": 4630.0,
            "minimum_price": 4629.9,
            "maximum_price": 4630.1,
            "observation_count": 5,
        }
        baseline = {"signals": {"XAUUSD": signal}, "rates": {"items": []}}
        candidate = deepcopy(baseline)
        candidate["signals"]["XAUUSD"]["observation_count"] = 7
        metadata = compare_snapshots(baseline, candidate, same_fact_inputs=False)
        self.assertEqual(metadata["severity_1_count"], 0)
        self.assertEqual(metadata["severity_2_count"], 1)
        self.assertEqual(
            metadata["issues"][0]["code"], "SNAPSHOT_METADATA_MISMATCH"
        )
        candidate["signals"]["XAUUSD"]["latest_price"] = 4631.0
        value = compare_snapshots(baseline, candidate, same_fact_inputs=False)
        self.assertEqual(value["severity_1_count"], 1)
        self.assertEqual(
            value["issues"][0]["code"], "CONSUMED_EXTERNAL_VALUE_MISMATCH"
        )


if __name__ == "__main__":
    unittest.main()
