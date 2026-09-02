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
    def test_live_capture_starting_is_process_healthy_during_backfill(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MARKET_PIPELINE_STATE_ROOT": directory},
            clear=False,
        ), patch("os.kill"):
            state = Path(directory) / "market-capture-account1"
            state.mkdir(parents=True)
            (state / "health.json").write_text(
                json.dumps(
                    {
                        "schema": "market_capture_engine/1.0",
                        "role": "market-capture-account1",
                        "mode": "live",
                        "status": "live-starting",
                        "updated_at_utc": foundation.utc_now()
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "pid": os.getpid(),
                        "sources": {
                            "MELTED_PRIMARY_FLOW": {},
                            "MELTED_AGGREGATE": {},
                            "MELTED_FLOW": {},
                            "USD_HERAT": {},
                            "XAUUSD": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                foundation.run_healthcheck("market-capture-account1", 60), 0
            )

    def test_live_mode_stage10_roles_bind_the_release_revision(self):
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
                mode, revision = foundation.validate_fixture_environment(
                    "market-capture-account1"
                )
                self.assertEqual((mode, revision), ("live", "a" * 40))
                mode, revision = foundation.validate_fixture_environment(
                    "market-processor"
                )
                self.assertEqual((mode, revision), ("live", "a" * 40))
                mode, revision = foundation.validate_fixture_environment(
                    "market-capture-external"
                )
                self.assertEqual((mode, revision), ("live", "a" * 40))
                mode, revision = foundation.validate_fixture_environment(
                    "coin-estimator"
                )
                self.assertEqual((mode, revision), ("live", "a" * 40))
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
            gap["items"][0]["delivery_sequence"] = 3
            gap["items"][0]["fact"]["fact_id"] = "8" * 64
            gap["items"][0]["fact"]["source_sequence"] = 2
            gap["items_hash"] = content_hash(gap["items"])
            status, rejection = foundation.apply_fact_batch(
                "market-fact-receiver", gap
            )
            self.assertEqual(status, 409)
            self.assertEqual(rejection["rejection_reason_codes"], ["SEQUENCE_GAP"])

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

    def test_shared_resource_locks_live_on_session_and_market_store_mounts(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "MARKET_PIPELINE_STATE_ROOT": str(Path(directory) / "state"),
                "MARKET_PIPELINE_SESSION_ROOT": str(Path(directory) / "session"),
                "MARKET_PIPELINE_MARKET_STORE_PATH": str(
                    Path(directory) / "store" / "market.sqlite"
                ),
            },
            clear=False,
        ):
            capture_locks = foundation.owner_lock_paths(
                "market-capture-account1"
            )
            adapter_locks = foundation.owner_lock_paths("market-store-adapter")
            self.assertEqual(capture_locks[-1], Path(directory) / "session/owner.lock")
            self.assertEqual(adapter_locks[-1], Path(directory) / "store/owner.lock")

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
        capture_account1 = web.split("  market-capture-account1:", 1)[1].split(
            "  market-capture-account2:", 1
        )[0]
        capture_account2 = web.split("  market-capture-account2:", 1)[1].split(
            "  market-capture-external:", 1
        )[0]
        self.assertIn('restart: "on-failure"', capture_account1)
        self.assertIn('restart: "on-failure"', capture_account2)
        self.assertEqual(web.count("ports:"), 1)
        self.assertEqual(bot.count("ports:"), 1)
        receiver_mount = bot.split(
            "target: /var/lib/market-data/receiver", 1
        )[1].split("depends_on:", 1)[0]
        self.assertIn("read_only: false", receiver_mount)
        adapter = bot.split("  market-store-adapter:", 1)[1].split(
            "  coin-estimator:", 1
        )[0]
        self.assertIn("timeout: 8s", adapter)
        receiver = bot.split("  market-fact-receiver:", 1)[1].split(
            "  market-store-adapter:", 1
        )[0]
        self.assertIn("timeout: 8s", receiver)
        estimator = bot.split("  coin-estimator:", 1)[1].split(
            "  estimator-snapshot-sender:", 1
        )[0]
        self.assertIn("timeout: 8s", estimator)
        self.assertIn("MARKET_PIPELINE_ESTIMATOR_INTERVAL_SECONDS", estimator)
        self.assertIn("MARKET_PIPELINE_ESTIMATOR_CPUS", estimator)
        processor = web.split("  market-processor:", 1)[1].split(
            "  market-fact-sync-worker:", 1
        )[0]
        calibration_mount = processor.split(
            "target: /var/lib/market-data/calibration/coin-groups", 1
        )[1].split("depends_on:", 1)[0]
        self.assertIn("read_only: false", calibration_mount)
        snapshot_receiver = web.split("  estimator-snapshot-receiver:", 1)[1].split(
            "\nsecrets:", 1
        )[0]
        self.assertIn(
            "SQLITE_TMPDIR: /var/lib/market-data/state/"
            "estimator-snapshot-receiver/sqlite-tmp",
            snapshot_receiver,
        )

    def test_adapter_wal_reader_mount_must_not_be_filesystem_read_only(self):
        base_service = {
            "user": "10001:10001",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "profiles": ["bot"],
            "environment": {
                "MARKET_PIPELINE_RECEIVER_DB_PATH": (
                    "/var/lib/market-data/receiver/market-fact-receiver/"
                    "market-fact-receiver.sqlite3"
                )
            },
            "volumes": [
                {
                    "type": "bind",
                    "source": "/tmp/receiver",
                    "target": "/var/lib/market-data/receiver",
                    "read_only": False,
                }
            ],
        }
        services = {
            name: {
                **copy.deepcopy(base_service),
                "profiles": ["bot"],
                "ports": (
                    [{"host_ip": "127.0.0.1", "target": 9443}]
                    if name == "market-fact-receiver"
                    else []
                ),
            }
            for name in manager.EXPECTED_SERVICES["bot"]
        }
        services["market-fact-receiver"]["environment"] = {}
        services["market-fact-receiver"]["volumes"] = []
        services["coin-estimator"]["environment"] = {}
        services["coin-estimator"]["volumes"] = []
        services["estimator-snapshot-sender"]["environment"] = {}
        services["estimator-snapshot-sender"]["volumes"] = []
        manager.audit_compose({"services": services}, role="bot", fixture=True)
        services["market-store-adapter"]["volumes"][0]["read_only"] = True
        with self.assertRaisesRegex(
            manager.Stage3Error,
            "compose_adapter_receiver_wal_mount_read_only",
        ):
            manager.audit_compose({"services": services}, role="bot", fixture=True)

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

    def test_live_preflight_accepts_only_registry_digest_or_exact_local_image_id(self):
        image_id = "sha256:" + ("a" * 64)
        inspect_document = [
            {
                "Id": image_id,
                "Size": 123,
                "Architecture": "amd64",
                "Os": "linux",
                "RepoDigests": [],
                "Config": {
                    "User": "10001:10001",
                    "Env": [],
                    "Labels": {
                        "org.opencontainers.image.revision": "b" * 40,
                        "org.opencontainers.image.version": "stage13-shadow",
                    },
                },
            }
        ]
        inspect_document[0]["Created"] = "2026-01-01T00:00:00Z"
        inspect_document[0]["RootFS"] = {"Type": "layers", "Layers": ["sha256:" + "d" * 64]}
        with patch.object(manager, "run", return_value=json.dumps(inspect_document)):
            metadata = manager.image_metadata(image_id, "b" * 40, fixture=False)
        self.assertEqual(metadata["image_id"], image_id)
        self.assertEqual(
            metadata["portable_content_digest"],
            manager.portable_image_content_digest(inspect_document[0]),
        )
        with self.assertRaisesRegex(
            manager.Stage3Error, "release_image_must_be_digest_pinned"
        ):
            manager.image_metadata("market-pipeline:mutable", "b" * 40, fixture=False)
        inspect_document[0]["Id"] = "sha256:" + ("c" * 64)
        with patch.object(manager, "run", return_value=json.dumps(inspect_document)):
            with self.assertRaisesRegex(
                manager.Stage3Error, "release_local_image_id_mismatch"
            ):
                manager.image_metadata(image_id, "b" * 40, fixture=False)

    def test_secret_contract_requires_root_only_parent_and_shared_runtime_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "fixture-secret"
            secret.write_text("fixture-only", encoding="utf-8")
            os.chmod(secret, 0o440)
            environment = {
                key: str(secret) for key in manager.SECRET_ENV_KEYS["bot"]
            }
            secret_info = secret.stat()
            with patch.object(
                manager, "SECRET_FILE_GID", secret_info.st_gid
            ):
                findings = manager.inspect_secret_contract("bot", environment)
            self.assertTrue(all(item["status"] == "ok" for item in findings))
            os.chmod(secret, 0o444)
            with patch.object(
                manager, "SECRET_FILE_GID", secret_info.st_gid
            ):
                findings = manager.inspect_secret_contract("bot", environment)
            self.assertTrue(
                all(item["status"] == "file_mode_mismatch" for item in findings)
            )
            self.assertEqual(manager.SECRET_FILE_GID, 10001)


if __name__ == "__main__":
    unittest.main()
