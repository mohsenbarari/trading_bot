"""Tests for the explicit, local-only snapshot publisher command."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import connect_market_store, initialize_market_store, upsert_observation
from scripts.publish_coin_intelligence_snapshot import main


class PublishCoinIntelligenceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.market_dir = self.root / "market"
        self.snapshot_dir = self.root / "snapshots"
        self.market_dir.mkdir()
        self.snapshot_dir.mkdir()
        self.store_path = self.market_dir / "market.sqlite3"
        self.snapshot_path = self.snapshot_dir / "coin-rates.json"
        self.now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        self.connection = connect_market_store(self.store_path)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def _seed_rate_ready_store(self) -> None:
        event_key = derive_event_key("snapshot-cli-test", "physical-gold")
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=event_key,
                source_code="PRIVATE_GOLD_CHANNEL",
                source_family="TELEGRAM_PRIVATE",
                event_time_utc=self.now - timedelta(seconds=10),
                available_at_utc=self.now - timedelta(seconds=10),
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
                parser_version="snapshot-cli-test-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="snapshot-cli-test-v1",
            ),
        )
        # The snapshot is intentionally evaluated at a historical instant.
        # Keep the fixture's ingestion time at that same instant so the
        # point-in-time query does not correctly classify it as future data.
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (
                (self.now - timedelta(seconds=10))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                event_key,
            ),
        )
        self.connection.commit()

    def _invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(arguments)
        return result, json.loads(stream.getvalue())

    def test_publish_and_check_are_local_and_privacy_safe(self) -> None:
        self._seed_rate_ready_store()
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:00:00Z",
        )
        self.assertEqual((result, payload["status"], len(str(payload["snapshot_digest"]))), (0, "PUBLISHED", 64))
        with patch("scripts.publish_coin_intelligence_snapshot._utc_now", return_value=self.now):
            result, payload = self._invoke(
                "check",
                "--runtime-root", str(self.root),
                "--snapshot", "snapshots/coin-rates.json",
            )
        self.assertEqual((result, payload["status"], payload["estimated_rate_count"]), (0, "FRESH", 3))
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_outside_paths_and_missing_parent_fail_closed(self) -> None:
        outside_store = self.root.parent / "outside.sqlite3"
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", str(outside_store),
            "--snapshot", "snapshots/coin-rates.json",
        )
        self.assertEqual((result, payload["reason"]), (2, "market_store_outside_runtime_root"))
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "missing/coin-rates.json",
        )
        self.assertEqual((result, payload["reason"]), (2, "snapshot_parent_unavailable"))
        self.assertFalse((self.root / "missing").exists())

    def test_lock_contention_is_reported_without_another_publish(self) -> None:
        self._seed_rate_ready_store()
        lock_path = self.snapshot_path.with_name(".coin-rates.json.lock")
        with lock_path.open("w", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, payload = self._invoke(
                "publish",
                "--runtime-root", str(self.root),
                "--market-store", "market/market.sqlite3",
                "--snapshot", "snapshots/coin-rates.json",
                "--as-of-utc", "2026-08-04T12:00:00Z",
            )
        self.assertEqual((result, payload["status"], payload["reason"]), (75, "BUSY", "snapshot_publish_in_progress"))
        self.assertFalse(self.snapshot_path.exists())

    def test_check_rejects_a_stale_snapshot(self) -> None:
        self._seed_rate_ready_store()
        self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:00:00Z",
        )
        with patch(
            "scripts.publish_coin_intelligence_snapshot._utc_now",
            return_value=self.now + timedelta(seconds=121),
        ):
            result, payload = self._invoke(
                "check",
                "--runtime-root", str(self.root),
                "--snapshot", "snapshots/coin-rates.json",
            )
        self.assertEqual((result, payload["status"], payload["reason"]), (3, "STALE", "SNAPSHOT_STALE_OR_FUTURE"))

    def test_default_no_data_publish_preserves_last_valid_snapshot(self) -> None:
        self._seed_rate_ready_store()
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:00:00Z",
        )
        self.assertEqual((result, payload["status"]), (0, "PUBLISHED"))
        original = self.snapshot_path.read_bytes()

        self.connection.execute("DELETE FROM market_observations")
        self.connection.commit()
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:01:00Z",
        )

        self.assertEqual(
            (
                result,
                payload["status"],
                payload["reason"],
                payload["estimated_rate_count"],
            ),
            (3, "NOT_RATE_READY", "NO_ESTIMATED_COIN_RATES", 0),
        )
        self.assertEqual(self.snapshot_path.read_bytes(), original)

    def test_explicit_staging_no_data_publish_is_fresh_and_structurally_valid(self) -> None:
        staging_snapshot = self.snapshot_dir / "staging" / "coin-rates.json"
        staging_snapshot.parent.mkdir()
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/staging/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:00:00Z",
            "--publish-staging-no-data-snapshot",
            "--environment", "staging",
            "--confirm", "publish-staging-no-data-snapshot",
        )
        self.assertEqual(
            (
                result,
                payload["status"],
                payload["reason"],
                payload["estimated_rate_count"],
                payload["no_data_rate_count"],
                len(str(payload["snapshot_digest"])),
            ),
            (
                0,
                "PUBLISHED_NO_DATA",
                "NO_ESTIMATED_COIN_RATES",
                0,
                14,
                64,
            ),
        )
        snapshot = json.loads(staging_snapshot.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["snapshot_status"], "NO_DATA_COIN_RATE_STATE")
        self.assertEqual(snapshot["rates"]["estimated_count"], 0)
        self.assertTrue(
            all(item["status"] == "NO_DATA" for item in snapshot["rates"]["items"])
        )

        with patch("scripts.publish_coin_intelligence_snapshot._utc_now", return_value=self.now):
            result, payload = self._invoke(
                "check",
                "--runtime-root", str(self.root),
                "--snapshot", "snapshots/staging/coin-rates.json",
            )
        self.assertEqual(
            (result, payload["status"], payload["reason"], payload["estimated_rate_count"]),
            (0, "FRESH_NO_DATA", "NO_ESTIMATED_COIN_RATES", 0),
        )

        snapshot["rates"] = {}
        staging_snapshot.write_text(json.dumps(snapshot), encoding="utf-8")
        with patch("scripts.publish_coin_intelligence_snapshot._utc_now", return_value=self.now):
            result, payload = self._invoke(
                "check",
                "--runtime-root", str(self.root),
                "--snapshot", "snapshots/staging/coin-rates.json",
            )
        self.assertEqual(
            (result, payload["status"], payload["reason"]),
            (3, "UNAVAILABLE", "snapshot_validation_failed"),
        )

    def test_check_rejects_unmarked_zero_rate_snapshot(self) -> None:
        staging_snapshot = self.snapshot_dir / "staging" / "coin-rates.json"
        staging_snapshot.parent.mkdir()
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/staging/coin-rates.json",
            "--as-of-utc", "2026-08-04T12:00:00Z",
            "--publish-staging-no-data-snapshot",
            "--environment", "staging",
            "--confirm", "publish-staging-no-data-snapshot",
        )
        self.assertEqual((result, payload["status"]), (0, "PUBLISHED_NO_DATA"))
        snapshot = json.loads(staging_snapshot.read_text(encoding="utf-8"))
        snapshot["snapshot_status"] = "PARTIAL_COIN_RATE_STATE"
        staging_snapshot.write_text(json.dumps(snapshot), encoding="utf-8")

        with patch("scripts.publish_coin_intelligence_snapshot._utc_now", return_value=self.now):
            result, payload = self._invoke(
                "check",
                "--runtime-root", str(self.root),
                "--snapshot", "snapshots/staging/coin-rates.json",
            )
        self.assertEqual(
            (result, payload["status"], payload["reason"]),
            (3, "UNAVAILABLE", "SNAPSHOT_NO_DATA_STATE_INVALID"),
        )

    def test_staging_no_data_publish_requires_explicit_scope_and_confirmation(self) -> None:
        result, payload = self._invoke(
            "publish",
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--snapshot", "snapshots/coin-rates.json",
            "--publish-staging-no-data-snapshot",
        )
        self.assertEqual(
            (result, payload["status"], payload["reason"]),
            (2, "FAILED", "staging_no_data_publish_authority_invalid"),
        )

    def test_documented_direct_execution_resolves_the_local_package(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/publish_coin_intelligence_snapshot.py"
        completed = subprocess.run(
            [sys.executable, str(script), "check", "--runtime-root", str(self.root), "--snapshot", "missing.json"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 3)
        payload = json.loads(completed.stdout)
        self.assertEqual((payload["status"], payload["reason"]), ("UNAVAILABLE", "snapshot_file_unavailable"))
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
