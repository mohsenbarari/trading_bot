from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import upgrade_market_pipeline_bluegreen as upgrade
from scripts import migrate_market_pipeline_archive as migration


RELEASE = "a" * 40
OLD_PROJECT = "market-private-pipeline-shadow"
NEW_PROJECT = "market-private-pipeline-primary"
IMAGE = "sha256:" + "b" * 64


class MarketPipelineBlueGreenUpgradeTests(unittest.TestCase):
    def test_postgres_image_binding_matches_migration_gate(self) -> None:
        self.assertEqual(upgrade.POSTGRES_IMAGE, migration.POSTGRES_IMAGE)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.old_env = self.root / "old.env"
        self.new_env = self.root / "new.env"
        common = (
            "MARKET_BOT_DATA_ROOT=/srv/market-bot\n"
            "MARKET_WEB_DATA_ROOT=/srv/market-web\n"
        )
        self.old_env.write_text(
            common + f"MARKET_PIPELINE_PROJECT_NAME={OLD_PROJECT}\n",
            encoding="utf-8",
        )
        self.new_env.write_text(
            common
            + f"MARKET_PIPELINE_PROJECT_NAME={NEW_PROJECT}\n"
            + f"MARKET_PIPELINE_RELEASE_SHA={RELEASE}\n"
            + f"MARKET_PIPELINE_IMAGE={IMAGE}\n"
            + "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            + "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE=PRIVATE_PRIMARY\n",
            encoding="utf-8",
        )
        self.old_env.chmod(0o600)
        self.new_env.chmod(0o600)
        self.journal = self.root / "journal" / "upgrade.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def identity(service: str, *, running: bool = True) -> dict[str, object]:
        return {
            "container_id": sha256(service.encode("utf-8")).hexdigest(),
            "service": service,
            "image_id": "sha256:" + "c" * 64,
            "release_sha": "d" * 40,
            "restart_name": "on-failure",
            "restart_maximum_retry_count": 0,
            "running": running,
            "health": "healthy" if running else None,
        }

    def test_plan_binds_exact_old_runtime_and_keeps_new_project_empty(self) -> None:
        rows = {service: self.identity(service) for service in upgrade.ROLE_SERVICES["bot"]}
        with (
            patch.object(
                upgrade,
                "_project_services",
                side_effect=lambda project: set(rows) if project == OLD_PROJECT else set(),
            ),
            patch.object(upgrade, "_ids", side_effect=lambda project, service: [rows[service]["container_id"]]),
            patch.object(upgrade, "_identity", side_effect=lambda _id, project, service: rows[service]),
        ):
            payload = upgrade.plan(
                role="bot",
                old_env=self.old_env,
                new_env=self.new_env,
                journal=self.journal,
                release_sha=RELEASE,
                old_project=OLD_PROJECT,
                new_project=NEW_PROJECT,
            )

        self.assertEqual(payload["status"], "planned")
        self.assertEqual(len(payload["services"]), 4)
        self.assertFalse(payload["product_authority_changed"])
        self.assertFalse(payload["state_deleted"])
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("TOKEN", self.journal.read_text(encoding="utf-8"))

    def test_plan_rejects_any_preexisting_new_project_container(self) -> None:
        with patch.object(upgrade, "_project_services", return_value={"unexpected"}):
            with self.assertRaisesRegex(upgrade.UpgradeError, "project_inventory_invalid"):
                upgrade.plan(
                    role="bot",
                    old_env=self.old_env,
                    new_env=self.new_env,
                    journal=self.journal,
                    release_sha=RELEASE,
                    old_project=OLD_PROJECT,
                    new_project=NEW_PROJECT,
                )

    def test_env_binding_rejects_data_root_drift_and_nonprimary_lane(self) -> None:
        text = self.new_env.read_text(encoding="utf-8")
        self.new_env.write_text(
            text.replace("/srv/market-bot", "/srv/other").replace(
                "PRIVATE_PRIMARY", "PRIVATE_SHADOW"
            ),
            encoding="utf-8",
        )
        self.new_env.chmod(0o600)
        with self.assertRaises(upgrade.UpgradeError):
            upgrade._validate_envs(
                role="bot",
                old_env=self.old_env,
                new_env=self.new_env,
                release_sha=RELEASE,
                old_project=OLD_PROJECT,
                new_project=NEW_PROJECT,
            )

    def test_quiesce_stops_only_recorded_workload_in_reverse_order(self) -> None:
        rows = [self.identity(service) for service in upgrade.ROLE_SERVICES["bot"]]
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "planned",
            "role": "bot",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
            "services": rows,
        }
        running = {row["container_id"] for row in rows}
        calls: list[list[str]] = []

        def identity(container_id: str, *, project: str, service: str):
            row = dict(next(item for item in rows if item["container_id"] == container_id))
            row["running"] = container_id in running
            row["health"] = "healthy" if row["running"] else None
            return row

        def run(arguments, *, label, allow_failure=False):
            del label, allow_failure
            calls.append(list(arguments))
            if arguments[1] == "stop":
                running.discard(arguments[-1])
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(
                upgrade,
                "_ids",
                side_effect=lambda project, service, running=False: (
                    [next(item for item in rows if item["service"] == service)["container_id"]]
                    if not running
                    else []
                ),
            ),
            patch.object(upgrade, "_identity", side_effect=identity),
            patch.object(upgrade, "_run", side_effect=run),
            patch.object(upgrade, "_atomic_json"),
        ):
            result = upgrade.quiesce_workload(
                journal=self.journal, role="bot", release_sha=RELEASE
            )

        stopped = [command[-1] for command in calls if command[1] == "stop"]
        self.assertEqual(
            stopped,
            [
                next(row["container_id"] for row in rows if row["service"] == service)
                for service in upgrade.QUIESCE_ORDER["bot"]
            ],
        )
        self.assertEqual(result["status"], "workload_quiesced")

    def test_database_quiesce_requires_reconciled_exact_backup_receipt(self) -> None:
        receipt = self.root / "backup.json"
        receipt_payload = {
            "status": "PASS",
            "source": {"schema_versions": [1, 2, 3], "table_count": 28, "fact_count": 9},
            "restore_smoke": {
                "status": "PASS",
                "cleanup_status": "PASS",
                "schema_versions": [1, 2, 3],
                "table_count": 28,
                "fact_count": 9,
            },
        }
        receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
        receipt.chmod(0o600)
        digest = sha256(receipt.read_bytes()).hexdigest()
        database = self.identity("market-database")
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "workload_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "services": [database],
        }
        state = {"running": True}

        def identity(*_args, **_kwargs):
            return {**database, "running": state["running"]}

        def run(arguments, **_kwargs):
            if arguments[1] == "stop":
                state["running"] = False
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(upgrade, "_ids", return_value=[]),
            patch.object(upgrade, "_identity", side_effect=identity),
            patch.object(upgrade, "_run", side_effect=run),
            patch.object(upgrade, "_atomic_json"),
        ):
            result = upgrade.quiesce_database(
                journal=self.journal,
                role="web",
                release_sha=RELEASE,
                backup_receipt=receipt,
                expected_backup_receipt_sha256=digest,
            )
        self.assertEqual(result["status"], "database_quiesced")
        self.assertEqual(result["backup_receipt_sha256"], digest)

        receipt_payload["restore_smoke"]["fact_count"] = 8
        receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
        receipt.chmod(0o600)
        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
        ):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.quiesce_database(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    backup_receipt=receipt,
                    expected_backup_receipt_sha256=sha256(receipt.read_bytes()).hexdigest(),
                )

    def test_capture_authority_refuses_while_any_old_owner_runs(self) -> None:
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "database_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
        }
        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(upgrade, "_ids", return_value=["c" * 64]),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "old_owner_still_running"):
                upgrade.authorize_captures(
                    journal=self.journal, role="web", release_sha=RELEASE
                )

    def test_new_database_identity_uses_pinned_postgres_and_exact_bind_root(self) -> None:
        container_id = "e" * 64
        payload = {
            "new_project": NEW_PROJECT,
            "new_env": str(self.new_env),
        }
        document = {
            "Id": container_id,
            "Config": {
                "Image": upgrade.POSTGRES_IMAGE,
                "Labels": {
                    "com.docker.compose.project": NEW_PROJECT,
                    "com.docker.compose.service": "market-database",
                },
            },
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Mounts": [
                {
                    "Destination": "/var/lib/postgresql/data",
                    "Source": "/srv/market-web/postgres",
                }
            ],
        }
        with (
            patch.object(upgrade, "_ids", return_value=[container_id]),
            patch.object(upgrade, "_inspect", return_value=document),
        ):
            self.assertEqual(
                upgrade._new_database_identity(payload)["container_id"], container_id
            )
            document["Mounts"][0]["Source"] = "/srv/wrong/postgres"
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "new_database_identity_invalid"
            ):
                upgrade._new_database_identity(payload)

    def test_verify_rejects_old_owner_or_unexpected_new_service(self) -> None:
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "workload_quiesced",
            "role": "bot",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
        }
        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(upgrade, "_ids", return_value=["c" * 64]),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "old_owner_running"):
                upgrade.verify(journal=self.journal, role="bot", release_sha=RELEASE)


if __name__ == "__main__":
    unittest.main()
