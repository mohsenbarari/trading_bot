"""Offline contracts for the two-pass Market Pipeline archive migration."""

from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from scripts import migrate_market_pipeline_archive as migration


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
    def _run(self, backup_document: dict[str, object]) -> tuple[dict[str, object], mock.Mock]:
        run_mock = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        migration_outputs = iter(
            (
                '{"status":"applied","version":2,"table_count":26}',
                '{"status":"already_current","version":2,"table_count":26}',
            )
        )

        def text(arguments: list[str], *, label: str) -> str:
            if "market-migration" in arguments:
                return next(migration_outputs)
            sql = arguments[-1]
            if "string_agg" in sql:
                return "1,2"
            if "information_schema.tables" in sql:
                return "26"
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

    def test_invalid_second_pass_is_rejected(self) -> None:
        with self.assertRaisesRegex(migration.MigrationError, "contract_invalid"):
            migration._migration_result(
                '{"status":"applied","version":2,"table_count":26}', second=True
            )

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
                        "--confirm", "wrong",
                    ]
                )
        self.assertEqual(code, 1)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
