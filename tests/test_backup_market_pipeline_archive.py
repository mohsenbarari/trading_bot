"""Offline safety contracts for Market Pipeline backup and restore evidence."""

from __future__ import annotations

from datetime import timedelta
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

from scripts import backup_market_pipeline_archive as backup


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
IMAGE_SIGNATURE = "d" * 64
REAL_SECURE_DIRECTORY = backup._secure_directory


def _invariants(*, fact_count: int, table_count: int = 1) -> dict[str, object]:
    tables = {"market_facts": fact_count}
    for index in range(1, table_count):
        tables[f"table_{index}"] = 0
    return {
        "table_row_counts": tables,
        "sequence_values": {
            "market_facts_id_seq": {"last_value": 101, "is_called": True}
        },
        "schema_catalog_sha256": "1" * 64,
        "schema_objects_sha256": "2" * 64,
    }


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

    def _write_bound_receipt(self, payload: dict[str, object]) -> None:
        run_id = "deadbeefdeadbeef"
        payload["backup_run_id"] = run_id
        payload.setdefault("source_after", deepcopy(payload["source"]))
        artifact = payload.get("backup")
        artifact_path = (
            Path(artifact["path"])
            if isinstance(artifact, dict)
            else self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-{run_id[:8]}.dump"
        )
        journal = {
            "schema": backup.JOURNAL_SCHEMA,
            "status": "COMPLETE",
            "backup_status": payload["status"],
            "run_id": run_id,
            "created_at_utc": payload["created_at_utc"],
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_sha256": backup.file_digest(self.env_file),
            "receipt_path": str(self.receipt),
            "artifact_path": str(artifact_path),
            "candidate_path": str(self.backup_dir / f".{artifact_path.name}.pending"),
            "source_before": payload["source"],
            "source_after": payload["source_after"],
            "backup": payload["backup"],
            "restore_smoke": payload["restore_smoke"],
            "restore_resources": None,
            "secrets_disclosed": False,
        }
        backup._write_journal(backup._journal_path(self.backup_dir), journal)
        backup._write_receipt(self.receipt, payload)

    def test_initial_empty_store_receipt_is_non_mutating_and_fresh(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
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
        self.assertEqual(
            self._verify(
                now=backup.utc_now() + timedelta(hours=2),
                maximum_age_seconds=None,
            ),
            payload,
        )
        with self.assertRaisesRegex(backup.BackupError, "stale"):
            self._verify(
                now=backup.utc_now() - timedelta(minutes=2),
                maximum_age_seconds=None,
            )

    def test_initial_empty_receipt_fails_if_store_changes_before_migration(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
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
                **_invariants(fact_count=99, table_count=26),
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
                **_invariants(fact_count=99, table_count=26),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(payload)
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
                **_invariants(fact_count=1),
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
                **_invariants(fact_count=2),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(base)
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
            if label == "backup_project_workload_inventory":
                return "market-database"
            if arguments[:3] == ["docker", "ps", "-q"]:
                return container_id
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([document])
            sql = arguments[-1]
            if "to_regclass" in sql:
                return "t"
            if label == "backup_table_names":
                return "market_facts\nmarket_offers"
            if label == "backup_table_row_count":
                return "42" if sql.endswith("market_facts") else "7"
            if label == "backup_sequence_names":
                return "market_facts_id_seq"
            if label == "backup_sequence_value":
                return "99|t"
            if label == "backup_schema_catalogue":
                return '[{"table_name":"market_facts"}]'
            if label == "backup_schema_objects":
                return '[{"kind":"index","identity":"market_facts_pkey"}]'
            if "string_agg" in sql:
                return "1,2"
            if "pg_control_system" in sql:
                return "123456789"
            if "pg_database_size" in sql:
                return "1048576"
            raise AssertionError((label, joined))

        with mock.patch.object(backup, "_run_text", side_effect=fake_run):
            report = backup.inspect_source_database(self.values)
        self.assertEqual(report["schema_versions"], [1, 2])
        self.assertEqual(report["fact_count"], 42)
        self.assertEqual(report["table_count"], 2)
        self.assertEqual(report["table_row_counts"], {"market_facts": 42, "market_offers": 7})
        self.assertEqual(
            report["sequence_values"],
            {"market_facts_id_seq": {"last_value": 99, "is_called": True}},
        )
        self.assertNotIn("system_identifier", report)

    def test_sequence_is_called_accepts_verbose_boolean_text(self) -> None:
        seen = {"names": 0, "values": 0}

        def query(sql: str, *, label: str) -> str:
            if label == "backup_table_names":
                return "market_facts"
            if label == "backup_table_row_count":
                return "3"
            if label == "backup_sequence_names":
                seen["names"] += 1
                return "market_facts_id_seq"
            if label == "backup_sequence_value":
                seen["values"] += 1
                return "12|false"
            if label == "backup_schema_catalogue":
                return "[]"
            if label == "backup_schema_objects":
                return "[]"
            if "string_agg" in sql:
                return "1,2,3"
            raise AssertionError(label)

        report = backup._database_invariants(query)
        self.assertEqual(seen["values"], 1)
        self.assertEqual(
            report["sequence_values"],
            {"market_facts_id_seq": {"last_value": 12, "is_called": False}},
        )

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
            if label == "backup_project_workload_inventory":
                return "market-database"
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
        self.assertIn('"-c", "SELECT 1"', restore)
        self.assertIn("consecutive >= 2", restore)

    def test_initial_empty_complete_journal_recovers_missing_receipt(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            first = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
            self.receipt.unlink()
            recovered = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        self.assertEqual(recovered, first)
        self.assertEqual(self._verify(), first)

    def test_refresh_resumes_after_receipt_was_archived(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            first = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
            run_id = first["backup_run_id"]
            archived_receipt = (
                self.backup_dir / f"market-pipeline-backup-receipt.{run_id}.json"
            )
            # Reproduce SIGKILL between archiving the receipt and archiving the
            # matching journal.  Resume may only accept this exact payload.
            self.receipt.replace(archived_receipt)
            refreshed = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                refresh_complete=True,
            )
        self.assertNotEqual(refreshed["backup_run_id"], run_id)
        self.assertTrue(
            (self.backup_dir / f"market-pipeline-backup-journal.{run_id}.json").is_file()
        )
        self.assertEqual(self._verify(), refreshed)

    def test_pending_dump_is_resumed_without_second_pg_dump(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        destination = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        candidate = self.backup_dir / f".{destination.name}.pending"
        candidate.write_bytes(b"completed-custom-dump")
        candidate.chmod(0o600)
        validation = mock.Mock(returncode=0)
        with mock.patch.object(backup.subprocess, "run", return_value=validation) as runner:
            backup._write_dump(
                container_id="e" * 64,
                user="market_data",
                database="market_archive",
                destination=destination,
            )
        self.assertEqual(runner.call_count, 1)
        self.assertIn("pg_restore", runner.call_args.args[0])
        self.assertEqual(destination.read_bytes(), b"completed-custom-dump")
        self.assertFalse(candidate.exists())

    def test_partial_pending_dump_is_not_promoted(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        destination = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        candidate = self.backup_dir / f".{destination.name}.pending"
        candidate.write_bytes(b"partial")
        candidate.chmod(0o600)
        results = [
            mock.Mock(returncode=1),  # resumed candidate validation
            mock.Mock(returncode=9),  # fresh pg_dump fails in this fixture
        ]
        with mock.patch.object(backup.subprocess, "run", side_effect=results):
            with self.assertRaisesRegex(backup.BackupError, "pg_dump_failed_rc_9"):
                backup._write_dump(
                    container_id="e" * 64,
                    user="market_data",
                    database="market_archive",
                    destination=destination,
                )
        self.assertFalse(destination.exists())
        self.assertFalse(candidate.exists())

    def test_journal_tamper_is_rejected(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        journal_path = backup._journal_path(self.backup_dir)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["unexpected"] = "tamper"
        backup._write_journal(journal_path, journal)
        with self.assertRaisesRegex(backup.BackupError, "journal_schema_invalid"):
            self._verify()

    def test_source_after_mismatch_is_rejected_without_overwrite(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"dump")
        artifact.chmod(0o600)
        source = {
            "container_id": "e" * 64,
            "database": "market_archive",
            "database_size_bytes": 100,
            "database_identity_sha256": "f" * 64,
            "schema_versions": [1],
            "table_count": 1,
            "fact_count": 7,
            **_invariants(fact_count=7),
        }
        source_after = deepcopy(source)
        source_after["fact_count"] = 8
        source_after["table_row_counts"]["market_facts"] = 8
        payload: dict[str, object] = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_sha256": backup.file_digest(self.env_file),
            "source": source,
            "source_after": source_after,
            "backup": {
                "path": str(artifact), "sha256": backup.file_digest(artifact),
                "size_bytes": 4, "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS", "schema_versions": [1], "table_count": 1,
                "fact_count": 7, **_invariants(fact_count=7),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(payload)
        with self.assertRaisesRegex(backup.BackupError, "metadata_invalid"):
            self._verify()

    def test_writer_workload_must_be_quiesced(self) -> None:
        with mock.patch.object(
            backup,
            "_running_project_services",
            return_value=["market-capture-account1", "market-database"],
        ):
            with self.assertRaisesRegex(backup.BackupError, "writer_workloads_not_quiesced"):
                backup._assert_writer_workloads_quiesced(
                    self.values["MARKET_PIPELINE_PROJECT_NAME"]
                )
            with self.assertRaisesRegex(backup.BackupError, "writer_workloads_not_quiesced"):
                backup.inspect_source_database(self.values)

    def test_hot_backup_allows_running_writers_at_inspect_gate(self) -> None:
        with mock.patch.object(
            backup,
            "_running_project_services",
            return_value=["market-capture-account1", "market-database"],
        ):
            with mock.patch.object(
                backup,
                "_container_ids",
                side_effect=backup.BackupError("stopped_at_container_lookup"),
            ):
                with self.assertRaisesRegex(backup.BackupError, "stopped_at_container_lookup"):
                    backup.inspect_source_database(self.values, require_quiesce=False)
        self.assertNotEqual(backup.CREATE_CONFIRMATION, backup.HOT_CREATE_CONFIRMATION)
        self.assertIn("hot-backup", backup.HOT_CREATE_CONFIRMATION)

    def test_hot_window_allows_row_drift_but_not_schema_or_identity_drift(self) -> None:
        before = {
            "database_identity_sha256": "a" * 64,
            "schema_versions": [1, 2],
            "schema_catalog_sha256": "1" * 64,
            "schema_objects_sha256": "2" * 64,
            "table_count": 2,
            "fact_count": 10,
            "table_row_counts": {"market_facts": 10, "market_offers": 1},
            "sequence_values": {"market_facts_id_seq": {"last_value": 10, "is_called": True}},
        }
        after = dict(before)
        after["fact_count"] = 12
        after["table_row_counts"] = {"market_facts": 12, "market_offers": 1}
        after["sequence_values"] = {
            "market_facts_id_seq": {"last_value": 12, "is_called": True}
        }
        self.assertTrue(
            backup._source_window_compatible(
                before, after, allow_running_writers=True
            )
        )
        self.assertFalse(
            backup._source_window_compatible(
                before, after, allow_running_writers=False
            )
        )
        drifted_schema = dict(after)
        drifted_schema["schema_catalog_sha256"] = "3" * 64
        self.assertFalse(
            backup._source_window_compatible(
                before, drifted_schema, allow_running_writers=True
            )
        )
        drifted_identity = dict(after)
        drifted_identity["database_identity_sha256"] = "b" * 64
        self.assertFalse(
            backup._source_window_compatible(
                before, drifted_identity, allow_running_writers=True
            )
        )
        restore = {
            "schema_versions": [1, 2],
            "schema_catalog_sha256": "1" * 64,
            "schema_objects_sha256": "2" * 64,
            "table_count": 2,
            "fact_count": 11,
            "table_row_counts": {"market_facts": 11, "market_offers": 1},
            "sequence_values": {"market_facts_id_seq": {"last_value": 11, "is_called": True}},
        }
        self.assertTrue(
            backup._restore_window_compatible(
                before, after, restore, allow_running_writers=True
            )
        )
        self.assertFalse(
            backup._restore_window_compatible(
                before, after, restore, allow_running_writers=False
            )
        )

    def test_hot_retry_abandons_empty_prepared_journal(self) -> None:
        journal = {
            "status": "PREPARED",
            "backup": None,
            "artifact_path": str(self.backup_dir / "market-archive-before-aaaaaaaaaaaa-20260101T000000Z-01234567.dump"),
            "candidate_path": str(self.backup_dir / ".market-archive-before-aaaaaaaaaaaa-20260101T000000Z-01234567.dump.pending"),
        }
        self.backup_dir.mkdir(mode=0o700)
        path = self.backup_dir / "market-pipeline-backup-journal.json"
        path.write_text("{}\n", encoding="utf-8")
        self.assertTrue(backup._empty_prepared_journal(journal, root=self.backup_dir))
        backup._abandon_empty_prepared_journal(path, journal, root=self.backup_dir)
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(backup.BackupError, "not_abandonable"):
            backup._abandon_empty_prepared_journal(
                path,
                {**journal, "status": "DUMP_READY"},
                root=self.backup_dir,
            )

    def test_orphan_cleanup_refuses_wrong_owner_label(self) -> None:
        inspected = subprocess.CompletedProcess(
            [], 0, stdout="different-run\n", stderr=""
        )
        with (
            mock.patch.object(backup.subprocess, "run", return_value=inspected),
            mock.patch.object(backup, "_run_text") as destructive,
        ):
            with self.assertRaisesRegex(backup.BackupError, "owner_mismatch"):
                backup._cleanup_owned_restore_resource(
                    "container", "market_pipeline_restore_0123456789abcdef",
                    "market-pipeline-backup-0123456789abcdef",
                )
        destructive.assert_not_called()

    def test_production_backup_directory_rejects_tmp(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "tmp_forbidden"):
            REAL_SECURE_DIRECTORY(self.backup_dir, create=False)


if __name__ == "__main__":
    unittest.main()
