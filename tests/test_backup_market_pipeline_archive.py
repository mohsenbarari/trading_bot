"""Offline safety contracts for Market Pipeline backup and restore evidence."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import backup_market_pipeline_archive as backup


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
IMAGE_SIGNATURE = "d" * 64
REAL_SECURE_DIRECTORY = backup._secure_directory


def _env_values(root: Path) -> dict[str, str]:
    return {
        "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
        "MARKET_PIPELINE_IMAGE": IMAGE_ID,
        "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        "MARKET_WEB_DATA_ROOT": str(root / "market-data-production"),
        "MARKET_PRIVATE_BIND_IP": "10.240.1.20",
        "MARKET_WEB_PRIVATE_IP": "10.240.1.20",
        "MARKET_BOT_PRIVATE_IP": "10.240.1.10",
        "MARKET_POSTGRES_PASSWORD_FILE": "/srv/trading-bot/secure/market/postgres-password",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE": "/srv/trading-bot/secure/market/account1.json",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE": "/srv/trading-bot/secure/market/account2.json",
        "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE": "/srv/trading-bot/secure/market/account2-hmac",
        "MARKET_RESEARCH_ENCRYPTION_KEY_FILE": "/srv/trading-bot/secure/market/research-archive.key",
        "MARKET_TRANSPORT_CA_FILE": "/srv/trading-bot/secure/market/ca.pem",
        "MARKET_WEB_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/web-cert.pem",
        "MARKET_WEB_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/web-key.pem",
        "MARKET_HMAC_ACTIVE_FILE": "/srv/trading-bot/secure/market/hmac-active",
        "MARKET_HMAC_PREVIOUS_FILE": "/srv/trading-bot/secure/market/hmac-previous",
        "MARKET_POSTGRES_USER": "market_data",
        "MARKET_POSTGRES_DB": "market_archive",
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _fixture_secure_directory(path: Path, *, create: bool) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    if not path.is_dir() or path.is_symlink() or path.stat().st_mode & 0o777 != 0o700:
        raise backup.BackupError("backup_directory_owner_mode_invalid")
    return path


class BackupMarketPipelineArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-backup-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.env_file = self.root / "web.release.env"
        self.values = _env_values(self.root)
        _write_env(self.env_file, self.values)
        self.postgres_root = Path(self.values["MARKET_WEB_DATA_ROOT"]) / "postgres"
        self.postgres_root.mkdir(parents=True, mode=0o700)
        self.backup_dir = self.root / "production-backups"
        self.receipt = self.backup_dir / "market-pipeline-backup-receipt.json"
        self.source_guard = mock.patch.object(backup, "validate_source")
        self.directory_guard = mock.patch.object(
            backup, "_secure_directory", side_effect=_fixture_secure_directory
        )
        self.source_guard.start()
        self.directory_guard.start()

    def tearDown(self) -> None:
        self.directory_guard.stop()
        self.source_guard.stop()
        self.temporary.cleanup()

    def _verify(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "env_file": self.env_file,
            "receipt": self.receipt,
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "maximum_age_seconds": 3600,
        }
        arguments.update(overrides)
        return backup.verify_receipt(**arguments)  # type: ignore[arg-type]

    def test_initial_empty_store_receipt_is_non_mutating_and_fresh(self) -> None:
        payload = backup.create_backup(
            env_file=self.env_file,
            backup_dir=self.backup_dir,
            receipt=self.receipt,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
        )
        self.assertEqual(payload["status"], "INITIAL_EMPTY")
        self.assertIsNone(payload["backup"])
        self.assertFalse(payload["database_mutated"])
        self.assertFalse(payload["services_started"])
        self.assertFalse(payload["off_host_copy_required"])
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._verify(), payload)
        with self.assertRaisesRegex(backup.BackupError, "stale"):
            self._verify(now=backup.utc_now() + timedelta(hours=2))

    def test_initial_empty_receipt_fails_if_store_changes_before_migration(self) -> None:
        backup.create_backup(
            env_file=self.env_file,
            backup_dir=self.backup_dir,
            receipt=self.receipt,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
        )
        (self.postgres_root / "unexpected").write_text("partial-init\n", encoding="utf-8")
        with self.assertRaisesRegex(backup.BackupError, "initial_store_changed"):
            self._verify()

    def test_restored_artifact_receipt_is_exact_and_tamper_evident(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = (
            self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"synthetic-custom-dump")
        artifact.chmod(0o600)
        payload = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_sha256": backup.file_digest(self.env_file),
            "source": {
                "container_id": "e" * 64,
                "database": "market_archive",
                "database_size_bytes": 12345,
                "database_identity_sha256": "f" * 64,
                "schema_versions": [1, 2],
                "table_count": 26,
                "fact_count": 99,
            },
            "backup": {
                "path": str(artifact),
                "sha256": backup.file_digest(artifact),
                "size_bytes": artifact.stat().st_size,
                "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS",
                "schema_versions": [1, 2],
                "table_count": 26,
                "fact_count": 99,
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        backup._write_receipt(self.receipt, payload)
        self.assertEqual(self._verify(), payload)
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(backup.BackupError, "artifact_drifted"):
            self._verify()

    def test_receipt_rejects_extra_fields_and_restore_count_drift(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = (
            self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"dump")
        artifact.chmod(0o600)
        base = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_sha256": backup.file_digest(self.env_file),
            "source": {
                "container_id": "e" * 64,
                "database": "market_archive",
                "database_size_bytes": 1,
                "database_identity_sha256": "f" * 64,
                "schema_versions": [1],
                "table_count": 1,
                "fact_count": 1,
            },
            "backup": {
                "path": str(artifact),
                "sha256": backup.file_digest(artifact),
                "size_bytes": 4,
                "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS",
                "schema_versions": [1],
                "table_count": 1,
                "fact_count": 2,
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        backup._write_receipt(self.receipt, base)
        with self.assertRaisesRegex(backup.BackupError, "metadata_invalid"):
            self._verify()
        base["unexpected_raw"] = "must-not-be-accepted"
        backup._write_receipt(self.receipt, base)
        with self.assertRaisesRegex(backup.BackupError, "schema_invalid"):
            self._verify()

    def test_database_inventory_binds_single_healthy_container_and_mount(self) -> None:
        container_id = "e" * 64
        document = {
            "Id": container_id,
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {
                "Image": backup.POSTGRES_IMAGE,
                "Labels": {
                    "com.docker.compose.project": "market-private-pipeline-production",
                    "com.docker.compose.service": "market-database",
                },
            },
            "Mounts": [
                {
                    "Source": str(self.postgres_root),
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        }

        def fake_run(arguments: list[str], *, label: str) -> str:
            joined = " ".join(arguments)
            if arguments[:3] == ["docker", "ps", "-q"]:
                return container_id
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([document])
            sql = arguments[-1]
            if "to_regclass" in sql:
                return "t"
            if "string_agg" in sql:
                return "1,2"
            if "market_facts" in sql:
                return "42"
            if "information_schema.tables" in sql:
                return "26"
            if "pg_control_system" in sql:
                return "123456789"
            if "pg_database_size" in sql:
                return "1048576"
            raise AssertionError((label, joined))

        with mock.patch.object(backup, "_run_text", side_effect=fake_run):
            report = backup.inspect_source_database(self.values)
        self.assertEqual(report["schema_versions"], [1, 2])
        self.assertEqual(report["fact_count"], 42)
        self.assertEqual(report["table_count"], 26)
        self.assertNotIn("system_identifier", report)

    def test_database_inventory_rejects_initialized_database_without_archive_schema(self) -> None:
        container_id = "e" * 64
        document = {
            "Id": container_id,
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {
                "Image": backup.POSTGRES_IMAGE,
                "Labels": {
                    "com.docker.compose.project": "market-private-pipeline-production",
                    "com.docker.compose.service": "market-database",
                },
            },
            "Mounts": [
                {
                    "Source": str(self.postgres_root),
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        }

        def fake_run(arguments: list[str], *, label: str) -> str:
            if arguments[:3] == ["docker", "ps", "-q"]:
                return container_id
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([document])
            if "to_regclass" in arguments[-1]:
                return "f"
            raise AssertionError((label, arguments))

        with mock.patch.object(backup, "_run_text", side_effect=fake_run):
            with self.assertRaisesRegex(backup.BackupError, "schema_unavailable"):
                backup.inspect_source_database(self.values)

    def test_release_env_forbids_primary_authority(self) -> None:
        values = dict(self.values)
        values["MARKET_PIPELINE_FEED_MODE"] = "PRIVATE_PRIMARY"
        _write_env(self.env_file, values)
        with self.assertRaisesRegex(backup.BackupError, "identity_mismatch"):
            backup.validate_release_env(
                self.env_file, release_sha=RELEASE_SHA, image_id=IMAGE_ID
            )

    def test_receipt_and_backup_must_be_isolated_from_database_root(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "destination_invalid"):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.root / "elsewhere.json",
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        with self.assertRaisesRegex(backup.BackupError, "directory_overlap"):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.postgres_root,
                receipt=self.postgres_root / "market-pipeline-backup-receipt.json",
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )

    def test_restore_runtime_is_isolated_pinned_and_label_owned(self) -> None:
        source = Path(backup.__file__).read_text(encoding="utf-8")
        restore = source.split("def restore_smoke(", 1)[1].split("\ndef _write_receipt", 1)[0]
        self.assertIn('"--network", "none"', restore)
        self.assertIn("POSTGRES_IMAGE", restore)
        self.assertIn("io.gold-trade.market-backup-run", restore)
        self.assertNotIn("--publish", restore)
        self.assertIn('"--exit-on-error"', restore)
        self.assertIn("_assert_restore_resource_absent", restore)

    def test_production_backup_directory_rejects_tmp(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "tmp_forbidden"):
            REAL_SECURE_DIRECTORY(self.backup_dir, create=False)


if __name__ == "__main__":
    unittest.main()
