"""Offline contracts for the two-pass Market Pipeline archive migration."""

from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import migrate_market_pipeline_archive as migration
from core.market_intelligence.private_pipeline_foundation import (
    MARKET_SCHEMA_TABLE_COUNT as CORE_TABLE_COUNT,
    MARKET_SCHEMA_VERSION as CORE_SCHEMA_VERSION,
)


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
IMAGE_SIGNATURE = "d" * 64
CONTAINER_ID = "e" * 64


def _values() -> dict[str, str]:
    return {
        "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
        "MARKET_WEB_DATA_ROOT": "/srv/trading-bot/market-data-production",
        "MARKET_POSTGRES_USER": "market_data",
        "MARKET_POSTGRES_DB": "market_archive",
    }


def _document() -> dict[str, object]:
    return {
        "Id": CONTAINER_ID,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "HostConfig": {"RestartPolicy": {"Name": "on-failure"}},
        "Config": {
            "Image": migration.POSTGRES_IMAGE,
            "Labels": {
                "com.docker.compose.project": "market-private-pipeline-production",
                "com.docker.compose.service": "market-database",
            },
        },
        "Mounts": [
            {
                "Source": "/srv/trading-bot/market-data-production/postgres",
                "Destination": "/var/lib/postgresql/data",
            }
        ],
    }


class MigrateMarketPipelineArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-migration-")
        self.state_root = Path(self.temporary.name)
        self.state_root.chmod(0o700)
        self.journal = self.state_root / "migration-state.json"
        self.receipt = self.state_root / "migration-receipt.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_host_standalone_schema_binding_matches_runtime_contract(self) -> None:
        self.assertEqual(migration.MARKET_SCHEMA_VERSION, CORE_SCHEMA_VERSION)
        self.assertEqual(migration.MARKET_SCHEMA_TABLE_COUNT, CORE_TABLE_COUNT)

    def _run(self, backup_document: dict[str, object]) -> tuple[dict[str, object], mock.Mock]:
        run_mock = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        migration_outputs = iter(
            (
                '{"status":"applied","version":3,"table_count":28}',
                '{"status":"already_current","version":3,"table_count":28}',
            )
        )

        def text(arguments: list[str], *, label: str) -> str:
            if "market-migration" in arguments:
                return next(migration_outputs)
            sql = arguments[-1]
            if "string_agg" in sql:
                return "1,2,3"
            if "information_schema.tables" in sql:
                return "28"
            if "market_facts" in sql:
                return "9"
            raise AssertionError((label, arguments))

        initial = backup_document["status"] == "INITIAL_EMPTY"
        inventories = [[], [CONTAINER_ID]] if initial else [[CONTAINER_ID], [CONTAINER_ID]]
        with (
            mock.patch.object(migration.backup, "validate_release_env", return_value=_values()),
            mock.patch.object(migration.backup, "verify_receipt", return_value=backup_document),
            mock.patch.object(migration.backup, "file_digest", return_value="2" * 64),
            mock.patch.object(migration, "_container_ids", side_effect=inventories),
            mock.patch.object(
                migration,
                "_running_services",
                side_effect=[[], ["market-database"]]
                if initial
                else [["market-database"], ["market-database"]],
            ),
            mock.patch.object(migration, "_inspect", return_value=_document()),
            mock.patch.object(migration, "_run", run_mock),
            mock.patch.object(migration, "_text", side_effect=text),
        ):
            result = migration.run_migration(
                release_root=Path("/srv/trading-bot/market-pipeline-releases") / RELEASE_SHA,
                env_file=Path("/srv/trading-bot/market-pipeline-releases/web.env"),
                backup_receipt=Path("/root/secure-envs/backup.json"),
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                offhost_receipt_sha256="f" * 64,
                host_preflight_receipt_sha256="1" * 64,
                backup_maximum_age_seconds=3600,
                journal_path=self.journal,
                receipt_path=self.receipt,
            )
        return result, run_mock

    def test_initial_empty_creates_database_and_requires_second_pass_noop(self) -> None:
        result, run_mock = self._run(
            {
                "status": "INITIAL_EMPTY",
                "source": {"database_initialized": False},
            }
        )
        self.assertTrue(result["database_container_created"])
        self.assertTrue(result["database_mutated"])
        self.assertEqual(result["second_pass"]["status"], "already_current")
        self.assertEqual(result["running_services"], ["market-database"])
        commands = [call.args[0] for call in run_mock.call_args_list]
        start = next(command for command in commands if "up" in command)
        self.assertIn("--no-recreate", start)

    def test_existing_backup_keeps_exact_database_container(self) -> None:
        result, _run_mock = self._run(
            {
                "status": "PASS",
                "source": {"container_id": CONTAINER_ID},
            }
        )
        self.assertFalse(result["database_container_created"])
        self.assertEqual(result["before"]["container_id"], CONTAINER_ID)
        self.assertEqual(result["after"]["container_id"], CONTAINER_ID)
        migration.validate_receipt(
            result,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
            offhost_receipt_sha256="f" * 64,
            host_preflight_receipt_sha256="1" * 64,
            source_backup_receipt_sha256="2" * 64,
            web_role_env_sha256="2" * 64,
        )
        result["running_services"] = ["market-database", "market-capture-account1"]
        with self.assertRaisesRegex(migration.MigrationError, "identity_invalid"):
            migration.validate_receipt(
                result,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                offhost_receipt_sha256="f" * 64,
                host_preflight_receipt_sha256="1" * 64,
                source_backup_receipt_sha256="2" * 64,
                web_role_env_sha256="2" * 64,
            )

    def test_bluegreen_uses_stopped_source_and_creates_distinct_target_container(self) -> None:
        old_id = "1" * 64
        new_id = "2" * 64
        source_values = {
            **_values(),
            "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-shadow",
        }
        target_values = {
            **_values(),
            "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-primary",
        }

        def document(container_id: str, project: str, running: bool) -> dict[str, object]:
            value = _document()
            value["Id"] = container_id
            value["State"] = {
                "Running": running,
                "Health": {"Status": "healthy" if running else "exited"},
            }
            value["Config"]["Labels"]["com.docker.compose.project"] = project
            return value

        migration_outputs = iter(
            (
                '{"status":"applied","version":3,"table_count":28}',
                '{"status":"already_current","version":3,"table_count":28}',
            )
        )

        def text(arguments: list[str], *, label: str) -> str:
            del label
            if "market-migration" in arguments:
                return next(migration_outputs)
            sql = arguments[-1]
            if "string_agg" in sql:
                return "1,2,3"
            if "information_schema.tables" in sql:
                return "28"
            if "market_facts" in sql:
                return "9"
            raise AssertionError(arguments)

        with (
            mock.patch.object(migration, "_validate_primary_target_env", return_value=target_values),
            mock.patch.object(migration.backup, "validate_release_env", return_value=source_values),
            mock.patch.object(
                migration.backup,
                "verify_receipt",
                return_value={"status": "PASS", "source": {"container_id": old_id}},
            ),
            mock.patch.object(migration.backup, "file_digest", return_value="2" * 64),
            mock.patch.object(
                migration, "_container_ids", side_effect=[[old_id], [], [new_id]]
            ),
            mock.patch.object(
                migration,
                "_running_services",
                side_effect=[[], [], ["market-database"]],
            ),
            mock.patch.object(
                migration,
                "_inspect",
                side_effect=[
                    document(old_id, "market-private-pipeline-shadow", False),
                    document(new_id, "market-private-pipeline-primary", True),
                ],
            ),
            mock.patch.object(
                migration,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ),
            mock.patch.object(migration, "_text", side_effect=text),
        ):
            result = migration.run_migration(
                release_root=Path("/srv/release"),
                env_file=Path("/srv/target.env"),
                backup_env_file=Path("/srv/source.env"),
                backup_receipt=Path("/root/backup.json"),
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                offhost_receipt_sha256="f" * 64,
                host_preflight_receipt_sha256="1" * 64,
                backup_maximum_age_seconds=3600,
                journal_path=self.journal,
                receipt_path=self.receipt,
            )

        self.assertTrue(result["database_container_created"])
        self.assertEqual(result["before"], {"container_id": old_id, "running": False})
        self.assertEqual(result["after"]["container_id"], new_id)
        self.assertFalse(result["private_shadow_only"])

    def test_invalid_second_pass_is_rejected(self) -> None:
        with self.assertRaisesRegex(migration.MigrationError, "contract_invalid"):
            migration._migration_result(
                '{"status":"applied","version":3,"table_count":28}', second=True
            )

    def test_complete_remote_receipt_recovers_after_journal_lag(self) -> None:
        backup_document = {"status": "PASS", "source": {"container_id": CONTAINER_ID}}
        first, _runner = self._run(backup_document)
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        journal["status"] = "APPLYING"
        journal["receipt_sha256"] = None
        migration._atomic_json(self.journal, journal)

        def query(_container: str, _user: str, _database: str, sql: str) -> str:
            return "1,2,3" if "string_agg" in sql else "9"

        with (
            mock.patch.object(migration.backup, "validate_release_env", return_value=_values()),
            mock.patch.object(
                migration.backup, "verify_receipt", return_value=backup_document
            ) as backup_verifier,
            mock.patch.object(migration.backup, "file_digest", return_value="2" * 64),
            mock.patch.object(migration, "_running_services", return_value=["market-database"]),
            mock.patch.object(migration, "_container_ids", return_value=[CONTAINER_ID]),
            mock.patch.object(migration, "_inspect", return_value=_document()),
            mock.patch.object(migration, "_query", side_effect=query),
            mock.patch.object(migration, "_run") as mutator,
        ):
            recovered = migration.run_migration(
                release_root=Path("/srv/release"),
                env_file=Path("/srv/web.env"),
                backup_receipt=Path("/root/backup.json"),
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                offhost_receipt_sha256="f" * 64,
                host_preflight_receipt_sha256="1" * 64,
                backup_maximum_age_seconds=3600,
                journal_path=self.journal,
                receipt_path=self.receipt,
            )
        self.assertEqual(recovered, first)
        mutator.assert_not_called()
        self.assertIsNone(backup_verifier.call_args.kwargs["maximum_age_seconds"])
        self.assertEqual(
            json.loads(self.journal.read_text(encoding="utf-8"))["status"],
            "COMPLETE",
        )

    def test_complete_journal_without_receipt_fails_before_mutation(self) -> None:
        before = {"container_id": None, "running": False}
        expected = migration._journal_identity(
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
            offhost_receipt_sha256="f" * 64,
            host_preflight_receipt_sha256="1" * 64,
            source_backup_receipt_sha256="2" * 64,
            web_role_env_sha256="2" * 64,
            backup_status="INITIAL_EMPTY",
            before=before,
        )
        migration._atomic_json(
            self.journal,
            {
                **expected,
                "status": "COMPLETE",
                "receipt_path": str(self.receipt),
                "receipt_sha256": "3" * 64,
            },
        )
        with (
            mock.patch.object(migration.backup, "validate_release_env", return_value=_values()),
            mock.patch.object(
                migration.backup,
                "verify_receipt",
                return_value={"status": "INITIAL_EMPTY", "source": {"database_initialized": False}},
            ),
            mock.patch.object(migration.backup, "file_digest", return_value="2" * 64),
            mock.patch.object(migration, "_running_services", return_value=[]),
            mock.patch.object(migration, "_run") as mutator,
        ):
            with self.assertRaisesRegex(migration.MigrationError, "complete_receipt_missing"):
                migration.run_migration(
                    release_root=Path("/srv/release"),
                    env_file=Path("/srv/web.env"),
                    backup_receipt=Path("/root/backup.json"),
                    release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE,
                    image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                    offhost_receipt_sha256="f" * 64,
                    host_preflight_receipt_sha256="1" * 64,
                    backup_maximum_age_seconds=3600,
                    journal_path=self.journal,
                    receipt_path=self.receipt,
                )
        mutator.assert_not_called()

    def test_journal_extra_field_is_rejected(self) -> None:
        expected = migration._journal_identity(
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
            offhost_receipt_sha256="f" * 64,
            host_preflight_receipt_sha256="1" * 64,
            source_backup_receipt_sha256="2" * 64,
            web_role_env_sha256="2" * 64,
            backup_status="INITIAL_EMPTY",
            before={"container_id": None, "running": False},
        )
        payload = {
            **expected,
            "status": "PREPARED",
            "receipt_path": str(self.receipt),
            "receipt_sha256": None,
            "unexpected": "tamper",
        }
        with self.assertRaisesRegex(migration.MigrationError, "journal_schema_invalid"):
            migration._validate_journal(payload, expected=expected, receipt_path=self.receipt)

    def test_failed_initial_migration_stops_created_database_without_deleting_state(self) -> None:
        run_mock = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        stopped = _document()
        stopped["State"] = {"Running": False, "Health": {"Status": "unhealthy"}}
        stopped["HostConfig"] = {"RestartPolicy": {"Name": "no"}}
        with (
            mock.patch.object(migration.backup, "validate_release_env", return_value=_values()),
            mock.patch.object(
                migration.backup,
                "verify_receipt",
                return_value={
                    "status": "INITIAL_EMPTY",
                    "source": {"database_initialized": False},
                },
            ),
            mock.patch.object(migration.backup, "file_digest", return_value="2" * 64),
            mock.patch.object(migration, "_container_ids", side_effect=[[], [CONTAINER_ID]]),
            mock.patch.object(migration, "_running_services", return_value=[]),
            mock.patch.object(migration, "_inspect", side_effect=[_document(), stopped]),
            mock.patch.object(migration, "_run", run_mock),
            mock.patch.object(migration, "_text", return_value="not-json"),
        ):
            with self.assertRaisesRegex(migration.MigrationError, "output_invalid"):
                migration.run_migration(
                    release_root=Path("/srv/release"),
                    env_file=Path("/srv/web.env"),
                    backup_receipt=Path("/root/backup.json"),
                    release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE,
                    image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                    offhost_receipt_sha256="f" * 64,
                    host_preflight_receipt_sha256="1" * 64,
                    backup_maximum_age_seconds=3600,
                    journal_path=self.journal,
                    receipt_path=self.receipt,
                )
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertTrue(any(command[:3] == ["docker", "update", "--restart=no"] for command in commands))
        self.assertTrue(any(command[:2] == ["docker", "stop"] for command in commands))
        self.assertFalse(any(command[:3] == ["docker", "volume", "rm"] for command in commands))

    def test_source_has_no_capture_start_or_destructive_storage_command(self) -> None:
        source = Path(migration.__file__).read_text(encoding="utf-8")
        self.assertIn('"--no-recreate"', source)
        self.assertIn('"--restart=no"', source)
        for forbidden in ("docker volume rm", "down -v", "market-capture-account1", "market-capture-account2"):
            self.assertNotIn(forbidden, source)

    def test_cli_rejects_missing_confirmation_without_docker(self) -> None:
        with mock.patch.object(migration, "run_migration") as runner:
            with contextlib.redirect_stderr(io.StringIO()):
                code = migration.main(
                    [
                        "--release-root", "/srv/release",
                        "--env-file", "/srv/web.env",
                        "--backup-receipt", "/root/backup.json",
                        "--release-sha", RELEASE_SHA,
                        "--release-tree", RELEASE_TREE,
                        "--image-id", IMAGE_ID,
                        "--image-input-signature", IMAGE_SIGNATURE,
                        "--offhost-receipt-sha256", "f" * 64,
                        "--host-preflight-receipt-sha256", "1" * 64,
                        "--backup-maximum-age-seconds", "3600",
                        "--journal", str(self.journal),
                        "--receipt", str(self.receipt),
                        "--confirm", "wrong",
                    ]
                )
        self.assertEqual(code, 1)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
