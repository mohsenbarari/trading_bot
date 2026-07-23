from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from core.market_intelligence.bundle import load_runtime_bundle
from core.market_intelligence.pipeline import (
    CoinIntelligenceShadowPipeline,
    ShadowCycleAlreadyRunning,
    ShadowPipelineError,
    exclusive_cycle_lock,
    inspect_candidate_snapshot,
)
from tests.test_coin_intelligence_snapshot_producer import (
    CUTOFF,
    _create_market_database,
)


FIXED_CLOCK = datetime(
    2026,
    7,
    23,
    12,
    5,
    tzinfo=timezone.utc,
)


def _minimal_snapshot(
    *,
    generated_at: str,
    version: str,
    center: int,
    lower: int | None = None,
    upper: int | None = None,
) -> dict:
    rate = {
        "commodity_name": "امام",
        "status": "ESTIMATED",
        "estimated_project_price": center,
        "tolerance": {
            "lower_project_price": lower or int(center * 0.98),
            "upper_project_price": upper or int(center * 1.02),
        },
    }
    return {
        "generated_at_utc": generated_at,
        "snapshot_version": version,
        "bundle_version": "test-bundle",
        "settlements": {
            "CASH": {"rates": [dict(rate)]},
            "TOMORROW": {"rates": [dict(rate)]},
        },
    }


class CoinIntelligenceShadowPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.market_db = self.root / "market.sqlite3"
        self.snapshot = self.root / "snapshot.json"
        self.health = self.root / "health.json"
        self.lock = self.root / "cycle.lock"
        self.pipeline = CoinIntelligenceShadowPipeline(
            load_runtime_bundle(),
            clock=lambda: FIXED_CLOCK,
        )

    def test_successful_cycle_publishes_snapshot_and_health(self) -> None:
        _create_market_database(self.market_db)

        result = self.pipeline.run(
            market_db=self.market_db,
            snapshot_path=self.snapshot,
            health_path=self.health,
            lock_path=self.lock,
            as_of=CUTOFF,
            collection_step=lambda: {
                "MELTED_AGGREGATE": {
                    "messages": 10,
                    "events": 5,
                    "ignored": 5,
                    "secret": "must-not-persist",
                }
            },
        )

        stored_snapshot = json.loads(
            self.snapshot.read_text(encoding="utf-8")
        )
        stored_health = json.loads(
            self.health.read_text(encoding="utf-8")
        )
        self.assertEqual(result.snapshot, stored_snapshot)
        self.assertEqual(stored_health["status"], "COMPLETED")
        self.assertEqual(
            stored_health["input_quality"]["status"],
            "HEALTHY",
        )
        self.assertEqual(
            stored_health["collection_summary"],
            {
                "source_count": 1,
                "messages": 10,
                "events": 5,
                "ignored": 5,
            },
        )
        serialized_health = json.dumps(
            stored_health,
            ensure_ascii=False,
        )
        self.assertNotIn("must-not-persist", serialized_health)
        self.assertNotIn("78000000", serialized_health)

    def test_same_cutoff_is_an_idempotent_success(self) -> None:
        _create_market_database(self.market_db)
        first = self.pipeline.run(
            market_db=self.market_db,
            snapshot_path=self.snapshot,
            health_path=self.health,
            lock_path=self.lock,
            as_of=CUTOFF,
        )
        second = self.pipeline.run(
            market_db=self.market_db,
            snapshot_path=self.snapshot,
            health_path=self.health,
            lock_path=self.lock,
            as_of=CUTOFF,
        )

        self.assertEqual(
            first.snapshot["snapshot_version"],
            second.snapshot["snapshot_version"],
        )
        self.assertEqual(second.health["status"], "COMPLETED")

    def test_invalid_schema_preserves_previous_snapshot(self) -> None:
        self.snapshot.write_text(
            '{"known":"good"}',
            encoding="utf-8",
        )
        connection = sqlite3.connect(self.market_db)
        try:
            connection.execute("CREATE TABLE wrong(value TEXT)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            ShadowPipelineError,
            "pipeline_input_schema_invalid",
        ):
            self.pipeline.run(
                market_db=self.market_db,
                snapshot_path=self.snapshot,
                health_path=self.health,
                lock_path=self.lock,
                as_of=CUTOFF,
            )

        self.assertEqual(
            self.snapshot.read_text(encoding="utf-8"),
            '{"known":"good"}',
        )
        health = json.loads(self.health.read_text(encoding="utf-8"))
        self.assertEqual(health["status"], "FAILED")
        self.assertEqual(
            health["failure_reason"],
            "pipeline_input_schema_invalid",
        )

    def test_no_fresh_anchor_preserves_previous_snapshot(self) -> None:
        _create_market_database(self.market_db)
        self.snapshot.write_text(
            '{"known":"good"}',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ShadowPipelineError,
            "NO_FRESH_CURRENT_PRICE_ANCHOR",
        ):
            self.pipeline.run(
                market_db=self.market_db,
                snapshot_path=self.snapshot,
                health_path=self.health,
                lock_path=self.lock,
                as_of=CUTOFF + timedelta(days=7),
            )

        self.assertEqual(
            self.snapshot.read_text(encoding="utf-8"),
            '{"known":"good"}',
        )
        health = json.loads(self.health.read_text(encoding="utf-8"))
        self.assertEqual(
            health["input_quality"]["status"],
            "INSUFFICIENT",
        )

    def test_overlapping_paths_fail_before_any_write(self) -> None:
        _create_market_database(self.market_db)

        with self.assertRaisesRegex(
            ShadowPipelineError,
            "pipeline_paths_overlap",
        ):
            self.pipeline.run(
                market_db=self.market_db,
                snapshot_path=self.market_db,
                health_path=self.health,
                lock_path=self.lock,
                as_of=CUTOFF,
            )

        connection = sqlite3.connect(self.market_db)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM price_events"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertGreater(count, 0)

    def test_nonblocking_lock_rejects_concurrent_cycle(self) -> None:
        with exclusive_cycle_lock(self.lock):
            with self.assertRaisesRegex(
                ShadowCycleAlreadyRunning,
                "pipeline_cycle_already_running",
            ):
                with exclusive_cycle_lock(self.lock):
                    pass


class CandidateSnapshotQualityTests(unittest.TestCase):
    def test_short_horizon_large_jump_is_rejected(self) -> None:
        previous = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=100_000,
        )
        candidate = _minimal_snapshot(
            generated_at="2026-07-23T12:01:00Z",
            version="v2",
            center=150_000,
        )

        result = inspect_candidate_snapshot(
            candidate,
            previous=previous,
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(
            any(
                row["kind"] == "CENTER_CHANGE"
                for row in result["rejections"]
            )
        )

    def test_wide_or_inverted_interval_is_rejected(self) -> None:
        wide = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=100_000,
            lower=70_000,
            upper=130_000,
        )
        self.assertEqual(
            inspect_candidate_snapshot(wide, previous=None)["status"],
            "REJECTED",
        )

        inverted = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v2",
            center=100_000,
            lower=101_000,
            upper=102_000,
        )
        with self.assertRaisesRegex(
            ShadowPipelineError,
            "snapshot_interval_order_invalid",
        ):
            inspect_candidate_snapshot(inverted, previous=None)

    def test_no_data_rate_is_rejected_without_numeric_exception(
        self,
    ) -> None:
        candidate = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=100_000,
        )
        candidate["settlements"]["CASH"]["rates"][0] = {
            "commodity_name": "امام",
            "status": "NO_DATA",
        }

        result = inspect_candidate_snapshot(
            candidate,
            previous=None,
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            result["rejections"][0]["kind"],
            "RATE_NO_DATA",
        )

    def test_required_rate_matrix_must_be_complete(self) -> None:
        candidate = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=100_000,
        )

        result = inspect_candidate_snapshot(
            candidate,
            previous=None,
            expected_commodities=("امام", "ربع بهار"),
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            {
                (
                    row.get("kind"),
                    row.get("settlement"),
                    row.get("commodity"),
                )
                for row in result["rejections"]
                if row["kind"] == "RATE_MISSING"
            },
            {
                ("RATE_MISSING", "CASH", "ربع بهار"),
                ("RATE_MISSING", "TOMORROW", "ربع بهار"),
            },
        )

    def test_same_version_with_different_content_is_rejected(self) -> None:
        previous = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=100_000,
        )
        candidate = _minimal_snapshot(
            generated_at="2026-07-23T12:00:00Z",
            version="v1",
            center=101_000,
        )

        result = inspect_candidate_snapshot(
            candidate,
            previous=previous,
        )

        self.assertTrue(
            any(
                row["kind"] == "SNAPSHOT_VERSION_COLLISION"
                for row in result["rejections"]
            )
        )


class ShadowPipelineCliTests(unittest.TestCase):
    def test_offline_cli_runs_one_complete_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_db = root / "market.sqlite3"
            snapshot = root / "snapshot.json"
            health = root / "health.json"
            lock = root / "cycle.lock"
            _create_market_database(market_db)

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_coin_intelligence_shadow_cycle.py",
                    "--acknowledge-shadow-only",
                    "--market-db",
                    str(market_db),
                    "--snapshot",
                    str(snapshot),
                    "--health",
                    str(health),
                    "--lock",
                    str(lock),
                    "--as-of",
                    "2026-07-23T12:00:00Z",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(
                output["status"],
                "SHADOW_CYCLE_COMPLETE",
            )
            self.assertTrue(snapshot.is_file())
            self.assertEqual(
                json.loads(health.read_text(encoding="utf-8"))["status"],
                "COMPLETED",
            )

    def test_cli_requires_explicit_shadow_acknowledgement(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/run_coin_intelligence_shadow_cycle.py",
                "--market-db",
                "/tmp/unused-market.sqlite3",
                "--snapshot",
                "/tmp/unused-snapshot.json",
                "--health",
                "/tmp/unused-health.json",
                "--lock",
                "/tmp/unused-cycle.lock",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            json.loads(completed.stderr)["status"],
            "SHADOW_CYCLE_REJECTED",
        )


if __name__ == "__main__":
    unittest.main()
