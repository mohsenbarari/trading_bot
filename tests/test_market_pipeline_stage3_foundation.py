import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence import private_pipeline_foundation as foundation
from scripts import manage_market_pipeline_stage3 as manager


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "market_private_pipeline"
DEPLOY = REPO_ROOT / "deploy" / "market-data"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class MarketPipelineStage3FoundationTests(unittest.TestCase):
    def test_live_mode_is_fail_closed_and_fixture_binds_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "session"
            sessions.mkdir()
            environment = {
                "MARKET_PIPELINE_MODE": "live",
                "MARKET_PIPELINE_RELEASE_SHA": "a" * 40,
                "MARKET_PIPELINE_IMAGE_REVISION": "a" * 40,
                "MARKET_PIPELINE_STATE_ROOT": str(root / "state"),
                "MARKET_PIPELINE_SESSION_ROOT": str(sessions),
            }
            with patch.dict(os.environ, environment, clear=False), patch(
                "os.geteuid", return_value=10001
            ):
                with self.assertRaisesRegex(
                    foundation.FoundationError, "not_available_at_stage3"
                ):
                    foundation.validate_fixture_environment(
                        "market-capture-account1"
                    )
                os.environ["MARKET_PIPELINE_MODE"] = "fixture"
                mode, revision = foundation.validate_fixture_environment(
                    "market-capture-account1"
                )
                self.assertEqual(mode, "fixture")
                self.assertEqual(revision, "a" * 40)

    def test_durable_atomic_write_and_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state" / "health.json"
            foundation.atomic_json_write(target, {"status": "ok"})
            self.assertEqual(json.loads(target.read_text()), {"status": "ok"})
            lock = root / "owner.lock"
            with foundation.exclusive_lock(lock):
                with self.assertRaisesRegex(
                    foundation.FoundationError, "already_held"
                ):
                    with foundation.exclusive_lock(lock):
                        self.fail("a second owner must never acquire the same lock")

    def test_fact_fixture_is_durable_idempotent_and_gap_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MARKET_PIPELINE_STATE_ROOT": directory},
            clear=False,
        ):
            batch = fixture("market_fact_batch.json")
            status, accepted = foundation.apply_fact_batch(
                "market-fact-receiver", batch
            )
            self.assertEqual(status, 200)
            self.assertEqual(accepted["accepted_count"], 1)
            status, duplicate = foundation.apply_fact_batch(
                "market-fact-receiver", batch
            )
            self.assertEqual(status, 200)
            self.assertEqual(duplicate["duplicate_count"], 1)

            gap = copy.deepcopy(batch)
            gap["batch_id"] = "9" * 64
            gap["first_sequence"] = 3
            gap["last_sequence"] = 3
            gap["items"][0]["fact_id"] = "8" * 64
            gap["items"][0]["source_sequence"] = 3
            gap["items_hash"] = content_hash(gap["items"])
            status, rejection = foundation.apply_fact_batch(
                "market-fact-receiver", gap
            )
            self.assertEqual(status, 409)
            self.assertEqual(rejection["reason_code"], "SEQUENCE_GAP")

    def test_snapshot_version_regression_does_not_replace_latest(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MARKET_PIPELINE_STATE_ROOT": directory},
            clear=False,
        ):
            latest = fixture("estimator_snapshot.json")
            latest["snapshot_version"] = 2
            status, _ = foundation.apply_estimator_snapshot(
                "estimator-snapshot-receiver", latest
            )
            self.assertEqual(status, 200)
            older = fixture("estimator_snapshot.json")
            older["snapshot_id"] = "7" * 64
            status, rejection = foundation.apply_estimator_snapshot(
                "estimator-snapshot-receiver", older
            )
            self.assertEqual(status, 409)
            self.assertEqual(
                rejection["reason_code"], "SNAPSHOT_VERSION_REGRESSION"
            )
            persisted = json.loads(
                (
                    Path(directory)
                    / "estimator-snapshot-receiver"
                    / "latest-estimator-snapshot.json"
                ).read_text()
            )
            self.assertEqual(persisted["snapshot_version"], 2)

    def test_sqlite_market_store_survives_reinitialization(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "MARKET_PIPELINE_MARKET_STORE_PATH": str(
                    Path(directory) / "store" / "market.sqlite"
                )
            },
            clear=False,
        ):
            foundation.initialize_market_store_fixture(
                "market-store-adapter", "a" * 40
            )
            foundation.initialize_market_store_fixture(
                "market-store-adapter", "b" * 40
            )
            connection = foundation.sqlite3.connect(
                Path(directory) / "store" / "market.sqlite"
            )
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT release_sha FROM stage3_foundation_state"
                    ).fetchone()[0],
                    "b" * 40,
                )
            finally:
                connection.close()

    def test_compose_is_split_by_host_and_pins_security_contract(self):
        base = (DEPLOY / "compose.yml").read_text(encoding="utf-8")
        web = (DEPLOY / "compose.web.yml").read_text(encoding="utf-8")
        bot = (DEPLOY / "compose.bot.yml").read_text(encoding="utf-8")
        dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python:3.11-slim-bookworm@sha256:", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("postgres:15-alpine@sha256:", base)
        self.assertIn("read_only: true", base)
        self.assertIn("no-new-privileges:true", base)
        self.assertIn("cap_drop:", base)
        self.assertNotIn("market_capture_account", bot)
        self.assertNotIn("market_bot_transport", web)
        self.assertEqual(web.count("ports:"), 1)
        self.assertEqual(bot.count("ports:"), 1)

    def test_bind_and_path_guards_reject_public_or_broad_targets(self):
        self.assertEqual(
            manager.validate_bind_ip("127.0.0.1", fixture=True), "127.0.0.1"
        )
        self.assertEqual(
            manager.validate_bind_ip("10.0.0.2", fixture=False), "10.0.0.2"
        )
        with self.assertRaises(manager.Stage3Error):
            manager.validate_bind_ip("0.0.0.0", fixture=False)
        with self.assertRaises(manager.Stage3Error):
            manager.validate_bind_ip("8.8.8.8", fixture=False)
        with self.assertRaises(manager.Stage3Error):
            manager.validate_data_root(Path("/srv"))
        self.assertEqual(
            manager.validate_data_root(Path("/tmp/market-stage3-fixture")),
            Path("/tmp/market-stage3-fixture"),
        )


if __name__ == "__main__":
    unittest.main()
