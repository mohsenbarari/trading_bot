from __future__ import annotations

from hashlib import sha256
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import upgrade_market_pipeline_bluegreen as upgrade
from scripts import migrate_market_pipeline_archive as migration
from scripts import quiesce_production_legacy_market_collectors as legacy_handoff


RELEASE = "a" * 40
OLD_PROJECT = "market-private-pipeline-shadow"
NEW_PROJECT = "market-private-pipeline-primary"
IMAGE = "sha256:" + "b" * 64
RELEASE_TREE = "e" * 40


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
            + "MARKET_PIPELINE_MODE=live\n"
            + "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            + "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY=1\n"
            + "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE=PRIVATE_PRIMARY\n",
            encoding="utf-8",
        )
        self.old_env.chmod(0o600)
        self.new_env.chmod(0o600)
        self.journal = self.root / "journal" / "upgrade.json"
        self.release_root = self.root / "release"
        (self.release_root / "deploy/market-data").mkdir(parents=True)
        (self.release_root / "deploy/market-data/compose.yml").write_text(
            "services: {}\n", encoding="utf-8"
        )
        (self.release_root / "deploy/market-data/compose.web.yml").write_text(
            "services: {}\n", encoding="utf-8"
        )

    def release_binding(self) -> dict[str, str]:
        return {
            "release_root": str(self.release_root),
            "release_root_path_sha256": sha256(
                str(self.release_root).encode("utf-8")
            ).hexdigest(),
            "release_tree": RELEASE_TREE,
            "compose_sha256": upgrade._sha256(
                self.release_root / "deploy/market-data/compose.yml"
            ),
            "compose_web_sha256": upgrade._sha256(
                self.release_root / "deploy/market-data/compose.web.yml"
            ),
        }

    def legacy_receipt(
        self,
        *,
        host_role: str = "web",
        status: str = "QUIESCED",
        prepared_journal_sha256: str | None = None,
        marker_authority_sha256: str | None = None,
    ) -> tuple[Path, str, Path]:
        path = self.root / f"legacy-collectors-{host_role}.json"
        lock = self.root / f"production-release-{host_role}.lock"
        lock.touch(mode=0o600)
        lock.chmod(0o600)
        lock_info = lock.lstat()
        lock_payload = {
            "schema": "market_pipeline_maintenance_lock/1.0",
            "environment": "production",
            "host_role": host_role,
            "release_sha": RELEASE,
            "nonce_sha256": "8" * 64,
            "journal_path_sha256": sha256(str(path).encode("utf-8")).hexdigest(),
            "device": lock_info.st_dev,
            "inode": lock_info.st_ino,
        }
        lock.write_text(json.dumps(lock_payload), encoding="utf-8")
        lock.chmod(0o600)
        units = {
            unit: {
                "unit_sha256": "f" * 64,
                "active": False,
                "enabled": False,
                "source_codes": sorted(
                    upgrade.LEGACY_COLLECTOR_SOURCE_OWNERSHIP[unit]
                ),
            }
            for unit in legacy_handoff.ROLE_UNITS[host_role]
        }
        authority_transfer = None
        if status == "AUTHORITY_TRANSFERRING":
            if not prepared_journal_sha256 or not marker_authority_sha256:
                raise AssertionError("prepared authority binding required")
            authority_transfer = {
                "bluegreen_journal_path_sha256": sha256(
                    str(self.journal).encode("utf-8")
                ).hexdigest(),
                "prepared_bluegreen_journal_sha256": (
                    prepared_journal_sha256
                ),
                "authorization_bluegreen_journal_sha256": None,
                "marker_authority_sha256": marker_authority_sha256,
            }
        path.write_text(
            json.dumps(
                {
                    "schema": legacy_handoff.SCHEMA,
                    "status": status,
                    "host_role": host_role,
                    "release_sha": RELEASE,
                    "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "verified_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "prior_units": units,
                    "current_units": units,
                    "maintenance_lock": lock_payload,
                    "primary_verification_sha256": None,
                    "primary_rollback_sha256": None,
                    "authority_transfer": authority_transfer,
                    "state_deleted": False,
                    "secrets_disclosed": False,
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path, sha256(path.read_bytes()).hexdigest(), lock

    def web_upgrade_journal(self) -> dict[str, object]:
        rows = [
            self.identity(service, running=False)
            for service in upgrade.ROLE_SERVICES["web"]
        ]
        markers: dict[str, object] = {}
        for marker_role, account in (
            ("market-capture-account1", "account1"),
            ("market-capture-account2", "account2"),
        ):
            parent = self.root / "sessions" / account
            parent.mkdir(parents=True)
            parent.chmod(0o700)
            path = parent / "authority-container.json"
            prior = {
                "contract": upgrade.AUTHORITY_CONTRACT,
                "authority": "container",
                "role": marker_role,
                "release_sha": "d" * 40,
            }
            path.write_text(
                json.dumps(prior, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            markers[marker_role] = {
                "path": str(path),
                "payload": prior,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        payload: dict[str, object] = {
            "schema": upgrade.SCHEMA,
            "journal_contract_revision": upgrade.JOURNAL_CONTRACT_REVISION,
            "status": "database_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            **self.release_binding(),
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
            "old_env": str(self.old_env),
            "new_env": str(self.new_env),
            "old_env_sha256": sha256(self.old_env.read_bytes()).hexdigest(),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "new_image_id": IMAGE,
            "services": rows,
            "markers": markers,
            "marker_transition": {
                "status": "NOT_STARTED",
                "authorized_at_utc": None,
                "entries": {},
            },
            "backup_receipt_sha256": "1" * 64,
            "source_backup_receipt_sha256": "1" * 64,
            "offhost_backup_receipt_sha256": "2" * 64,
            "offhost_backup_binding": {},
            "new_capture_ids": {},
            "product_authority_changed": False,
            "state_deleted": False,
            "secrets_disclosed": False,
        }
        upgrade._atomic_json(self.journal, payload, exclusive=True)
        return payload

    @staticmethod
    def marker_load_for_test(
        path: Path, *, role: str, release_sha: str | None = None
    ) -> dict[str, object]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("contract") != upgrade.AUTHORITY_CONTRACT
            or payload.get("authority") != "container"
            or payload.get("role") != role
            or (
                release_sha is not None
                and payload.get("release_sha") != release_sha
            )
        ):
            raise upgrade.UpgradeError("test_marker_invalid")
        return payload

    @staticmethod
    def marker_write_for_test(path: Path, payload: dict[str, object]) -> None:
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def interrupt_before_legacy_authority_wal(
        self,
    ) -> tuple[Path, str, Path, Path, str]:
        self.web_upgrade_journal()
        receipt, digest, lock = self.legacy_receipt()
        with (
            patch.object(upgrade, "_ids", return_value=[]),
            patch.object(upgrade, "_new_database_identity", return_value={}),
            patch.object(upgrade, "_new_identity", return_value={}),
            patch.object(upgrade, "_systemd_state", return_value=False),
            patch.object(upgrade, "_load_marker", side_effect=self.marker_load_for_test),
            patch.object(upgrade, "_write_marker", side_effect=self.marker_write_for_test),
            patch.object(legacy_handoff, "OPERATION_LOCK_PATH", lock),
        ):
            upgrade.prepare_capture_authority(
                journal=self.journal,
                role="web",
                release_sha=RELEASE,
                web_legacy_collector_receipt=receipt,
                expected_web_legacy_collector_receipt_sha256=digest,
                web_maintenance_lock_path=lock,
            )
            prepared_digest = upgrade._sha256(self.journal)
            marker_digest = upgrade._marker_authority_digest(
                upgrade._read_journal(self.journal)
            )
            bot_receipt, bot_digest, _bot_lock = self.legacy_receipt(
                host_role="bot",
                status="AUTHORITY_TRANSFERRING",
                prepared_journal_sha256=prepared_digest,
                marker_authority_sha256=marker_digest,
            )
            with (
                patch.object(
                    legacy_handoff,
                    "prepare_capture_authority_transfer_with_held_lock",
                    side_effect=SystemExit("synthetic-sigkill-window"),
                ),
                self.assertRaisesRegex(SystemExit, "synthetic-sigkill-window"),
            ):
                upgrade.authorize_captures(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    web_legacy_collector_receipt=receipt,
                    expected_web_legacy_collector_receipt_sha256=digest,
                    web_maintenance_lock_path=lock,
                    bot_legacy_collector_receipt=bot_receipt,
                    expected_bot_legacy_collector_receipt_sha256=bot_digest,
                )
        blue = upgrade._read_journal(self.journal)
        legacy = legacy_handoff._read(
            receipt, release_sha=RELEASE, host_role="web"
        )
        self.assertEqual(blue["marker_transition"]["status"], "PREPARED")
        self.assertEqual(legacy["status"], "QUIESCED")
        self.assertIsNone(legacy["authority_transfer"])
        return receipt, digest, lock, bot_receipt, bot_digest

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def offhost_receipt(
        self, *, source_receipt_sha256: str, plaintext_sha256: str,
        plaintext_size_bytes: int, verified_at_utc: str,
    ) -> tuple[Path, str]:
        path = self.root / "offhost.json"
        artifact_name = (
            "market-archive-before-aaaaaaaaaaaa-20260828T120000Z-1234abcd.dump.enc"
        )
        payload = {
            "schema": "market_pipeline_backup_offhost_copy/2.0",
            "status": "PASS",
            "verified_at_utc": verified_at_utc,
            "release_sha": RELEASE,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE,
            "image_input_signature": "f" * 64,
            "web_role_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "host_preflight_receipt_sha256": "2" * 64,
            "source_backup_receipt_sha256": source_receipt_sha256,
            "backup_status": "PASS",
            "artifact": {
                "name": artifact_name,
                "ciphertext_sha256": "3" * 64,
                "ciphertext_size_bytes": 123,
                "plaintext_sha256": plaintext_sha256,
                "plaintext_size_bytes": plaintext_size_bytes,
                "authentication_hmac_sha256": "4" * 64,
                "encryption_algorithm": "AES-256-CBC+PBKDF2-HMAC-SHA256",
                "kdf": "PBKDF2-HMAC-SHA256",
                "kdf_iterations": 600000,
                "encryption_receipt_sha256": "5" * 64,
                "encryption_receipt_path": (
                    "/secure/market-archive-before-aaaaaaaaaaaa-"
                    "20260828T120000Z-1234abcd.dump.encryption.json"
                ),
                "bot_copy_path": f"/secure/{artifact_name}",
            },
            "off_host_copy_status": "PASS_ENCRYPTED_VERIFIED",
            "database_mutated": False,
            "services_started": False,
            "product_authority_changed": False,
            "telegram_capture_cutover_authorized": False,
            "secrets_disclosed": False,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
        return path, sha256(path.read_bytes()).hexdigest()

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
            patch.object(upgrade, "_release_root_binding", return_value=self.release_binding()),
        ):
            payload = upgrade.plan(
                role="bot",
                old_env=self.old_env,
                new_env=self.new_env,
                journal=self.journal,
                release_sha=RELEASE,
                release_tree=RELEASE_TREE,
                release_root=self.release_root,
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
        with (
            patch.object(upgrade, "_project_services", return_value={"unexpected"}),
            patch.object(upgrade, "_release_root_binding", return_value=self.release_binding()),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "project_inventory_invalid"):
                upgrade.plan(
                    role="bot",
                    old_env=self.old_env,
                    new_env=self.new_env,
                    journal=self.journal,
                    release_sha=RELEASE,
                    release_tree=RELEASE_TREE,
                    release_root=self.release_root,
                    old_project=OLD_PROJECT,
                    new_project=NEW_PROJECT,
                )

    def test_release_root_binding_rejects_wrong_root_and_compose_tamper(self) -> None:
        binding = upgrade._release_root_binding(
            self.release_root,
            release_sha=RELEASE,
            release_tree=RELEASE_TREE,
        )
        payload = {"release_sha": RELEASE, **binding}
        with self.assertRaisesRegex(
            upgrade.UpgradeError, "release_root_binding_drift"
        ):
            upgrade._validate_release_root_binding(
                payload, supplied_root=self.root / "wrong"
            )
        (self.release_root / "deploy/market-data/compose.web.yml").write_text(
            "services: {tampered: {}}\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
                upgrade.UpgradeError, "release_root_binding_drift"
            ):
            upgrade._validate_release_root_binding(payload)

    def test_control_inputs_reject_intermediate_symlinks(self) -> None:
        actual = self.root / "actual"
        actual.mkdir()
        env = actual / "runtime.env"
        env.write_text("VALUE=1\n", encoding="utf-8")
        env.chmod(0o600)
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(
            upgrade.UpgradeError, "secure_file_invalid"
        ):
            upgrade._secure_read(alias / "runtime.env")

        actual_release = actual / "release"
        (actual_release / "deploy/market-data").mkdir(parents=True)
        for name in ("compose.yml", "compose.web.yml"):
            (actual_release / "deploy/market-data" / name).write_text(
                "services: {}\n", encoding="utf-8"
            )
        with self.assertRaisesRegex(
            upgrade.UpgradeError, "release_root_invalid"
        ):
            upgrade._release_root_binding(
                alias / "release",
                release_sha=RELEASE,
                release_tree=RELEASE_TREE,
            )

    def test_sealed_compose_uses_exact_bytes_after_source_tamper(self) -> None:
        self.journal.parent.mkdir(mode=0o700)
        payload = {
            "release_sha": RELEASE,
            **self.release_binding(),
            "new_env": str(self.new_env),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
        }
        compose = self.release_root / "deploy/market-data/compose.yml"
        compose_web = self.release_root / "deploy/market-data/compose.web.yml"
        original = {
            "compose": compose.read_bytes(),
            "compose_web": compose_web.read_bytes(),
            "env": self.new_env.read_bytes(),
        }
        with upgrade._sealed_compose_invocation(
            journal=self.journal,
            payload=payload,
            release_root=self.release_root,
        ) as command:
            compose.write_text("services: {tampered: {}}\n", encoding="utf-8")
            compose_web.write_text(
                "services: {tampered_web: {}}\n", encoding="utf-8"
            )
            self.new_env.write_text("TAMPERED=1\n", encoding="utf-8")
            self.new_env.chmod(0o600)
            env_path = Path(command[command.index("--env-file") + 1])
            file_positions = [
                index + 1 for index, value in enumerate(command) if value == "-f"
            ]
            compose_paths = [Path(command[index]) for index in file_positions]
            self.assertEqual(compose_paths[0].read_bytes(), original["compose"])
            self.assertEqual(
                compose_paths[1].read_bytes(), original["compose_web"]
            )
            self.assertEqual(env_path.read_bytes(), original["env"])
            self.assertNotEqual(compose_paths[0], compose)
            self.assertNotEqual(env_path, self.new_env)
        self.assertFalse(env_path.exists())
        self.assertFalse(compose_paths[0].exists())

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

    def test_env_binding_rejects_nonlive_primary_runtime(self) -> None:
        text = self.new_env.read_text(encoding="utf-8")
        self.new_env.write_text(
            text.replace("MARKET_PIPELINE_MODE=live", "MARKET_PIPELINE_MODE=fixture"),
            encoding="utf-8",
        )
        self.new_env.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradeError, "env_binding_invalid"):
            upgrade._validate_envs(
                role="bot",
                old_env=self.old_env,
                new_env=self.new_env,
                release_sha=RELEASE,
                old_project=OLD_PROJECT,
                new_project=NEW_PROJECT,
            )

    def test_env_binding_rejects_missing_primary_authority_flag(self) -> None:
        text = self.new_env.read_text(encoding="utf-8")
        self.new_env.write_text(
            text.replace("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY=1\n", ""),
            encoding="utf-8",
        )
        self.new_env.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradeError, "env_binding_invalid"):
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
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PASS",
            "backup": {"sha256": "6" * 64, "size_bytes": 456},
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
        offhost, offhost_digest = self.offhost_receipt(
            source_receipt_sha256=digest,
            plaintext_sha256="6" * 64,
            plaintext_size_bytes=456,
            verified_at_utc=receipt_payload["created_at_utc"],
        )
        database = self.identity("market-database")
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "workload_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "old_env": str(self.old_env),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "services": [database],
        }
        state = {"running": True}

        def identity(*_args, **_kwargs):
            return {**database, "running": state["running"]}

        def run(arguments, **_kwargs):
            if arguments[1] in {"update", "stop"}:
                self.assertEqual(payload["status"], "database_quiesce_prepared")
                self.assertEqual(
                    payload["offhost_backup_receipt_sha256"], offhost_digest
                )
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
            patch.object(upgrade.backup, "verify_receipt", return_value=receipt_payload) as verify_backup,
        ):
            result = upgrade.quiesce_database(
                journal=self.journal,
                role="web",
                release_sha=RELEASE,
                backup_receipt=receipt,
                expected_backup_receipt_sha256=digest,
                offhost_backup_receipt=offhost,
                expected_offhost_backup_receipt_sha256=offhost_digest,
                release_tree=RELEASE_TREE,
                image_id=IMAGE,
                image_input_signature="f" * 64,
                backup_maximum_age_seconds=3600,
            )
        verify_backup.assert_called_once()
        self.assertEqual(result["status"], "database_quiesced")
        self.assertEqual(result["backup_receipt_sha256"], digest)
        self.assertEqual(
            result["offhost_backup_binding"]["off_host_copy_status"],
            "PASS_ENCRYPTED_VERIFIED",
        )

        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(
                upgrade.backup,
                "verify_receipt",
                side_effect=upgrade.backup.BackupError("backup_restore_receipt_invalid"),
            ),
        ):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.quiesce_database(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    backup_receipt=receipt,
                    expected_backup_receipt_sha256=digest,
                    offhost_backup_receipt=offhost,
                    expected_offhost_backup_receipt_sha256=offhost_digest,
                    release_tree=RELEASE_TREE,
                    image_id=IMAGE,
                    image_input_signature="f" * 64,
                    backup_maximum_age_seconds=3600,
                )

        offhost_payload = json.loads(offhost.read_text(encoding="utf-8"))
        offhost_payload["backup_status"] = "INITIAL_EMPTY"
        offhost.write_text(json.dumps(offhost_payload), encoding="utf-8")
        offhost.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradeError, "offhost.*invalid"):
            upgrade._verify_offhost_backup_receipt(
                path=offhost,
                expected_sha256=sha256(offhost.read_bytes()).hexdigest(),
                source_receipt=receipt_payload,
                source_receipt_sha256=digest,
                release_sha=RELEASE,
                release_tree=RELEASE_TREE,
                image_id=IMAGE,
                image_input_signature="f" * 64,
                expected_web_role_env_sha256=sha256(
                    self.new_env.read_bytes()
                ).hexdigest(),
                maximum_age_seconds=3600,
            )

    def test_offhost_receipt_rejects_stale_and_path_mismatch(self) -> None:
        created = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(microsecond=0)
        source = {
            "created_at_utc": created.isoformat(),
            "status": "PASS",
            "backup": {"sha256": "6" * 64, "size_bytes": 456},
        }
        source_digest = "7" * 64
        stale, stale_digest = self.offhost_receipt(
            source_receipt_sha256=source_digest,
            plaintext_sha256="6" * 64,
            plaintext_size_bytes=456,
            verified_at_utc=created.isoformat(),
        )
        with self.assertRaisesRegex(upgrade.UpgradeError, "offhost.*invalid"):
            upgrade._verify_offhost_backup_receipt(
                path=stale,
                expected_sha256=stale_digest,
                source_receipt=source,
                source_receipt_sha256=source_digest,
                release_sha=RELEASE,
                release_tree=RELEASE_TREE,
                image_id=IMAGE,
                image_input_signature="f" * 64,
                expected_web_role_env_sha256=sha256(
                    self.new_env.read_bytes()
                ).hexdigest(),
                maximum_age_seconds=3600,
            )

        document = json.loads(stale.read_text(encoding="utf-8"))
        document["artifact"]["encryption_receipt_path"] = (
            "/different/" + Path(
                document["artifact"]["encryption_receipt_path"]
            ).name
        )
        stale.write_text(json.dumps(document), encoding="utf-8")
        stale.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradeError, "offhost.*invalid"):
            upgrade._verify_offhost_backup_receipt(
                path=stale,
                expected_sha256=sha256(stale.read_bytes()).hexdigest(),
                source_receipt=source,
                source_receipt_sha256=source_digest,
                release_sha=RELEASE,
                release_tree=RELEASE_TREE,
                image_id=IMAGE,
                image_input_signature="f" * 64,
                expected_web_role_env_sha256=sha256(
                    self.new_env.read_bytes()
                ).hexdigest(),
                maximum_age_seconds=None,
            )

    def test_offhost_receipt_tamper_after_wal_fails_before_docker_mutation(self) -> None:
        created = datetime.now(timezone.utc).isoformat()
        source = {
            "created_at_utc": created,
            "status": "PASS",
            "backup": {"sha256": "6" * 64, "size_bytes": 456},
        }
        backup_receipt = self.root / "backup-race.json"
        backup_receipt.write_text(json.dumps(source), encoding="utf-8")
        backup_receipt.chmod(0o600)
        source_digest = sha256(backup_receipt.read_bytes()).hexdigest()
        offhost, offhost_digest = self.offhost_receipt(
            source_receipt_sha256=source_digest,
            plaintext_sha256="6" * 64,
            plaintext_size_bytes=456,
            verified_at_utc=created,
        )
        database = self.identity("market-database")
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "workload_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "old_env": str(self.old_env),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "services": [database],
        }

        def durable_write(_journal, current):
            if current.get("status") == "database_quiesce_prepared":
                offhost.write_text("{}\n", encoding="utf-8")
                offhost.chmod(0o600)

        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
            patch.object(upgrade, "_ids", return_value=[]),
            patch.object(upgrade, "_identity") as docker_identity,
            patch.object(upgrade, "_run") as docker_mutation,
            patch.object(upgrade, "_atomic_json", side_effect=durable_write),
            patch.object(upgrade.backup, "verify_receipt", return_value=source),
        ):
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "secure_file_digest_mismatch"
            ):
                upgrade.quiesce_database(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    backup_receipt=backup_receipt,
                    expected_backup_receipt_sha256=source_digest,
                    offhost_backup_receipt=offhost,
                    expected_offhost_backup_receipt_sha256=offhost_digest,
                    release_tree=RELEASE_TREE,
                    image_id=IMAGE,
                    image_input_signature="f" * 64,
                    backup_maximum_age_seconds=3600,
                )
        docker_identity.assert_not_called()
        docker_mutation.assert_not_called()

    def test_capture_authority_refuses_while_any_old_owner_runs(self) -> None:
        receipt, digest, lock = self.legacy_receipt()
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
            patch.object(upgrade, "_systemd_state", return_value=False),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "old_owner_still_running"):
                upgrade.prepare_capture_authority(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    web_legacy_collector_receipt=receipt,
                    expected_web_legacy_collector_receipt_sha256=digest,
                    web_maintenance_lock_path=lock,
                )

    def test_capture_authority_requires_fresh_quiesced_legacy_receipt(self) -> None:
        receipt, digest, lock = self.legacy_receipt()
        payload = {
            "schema": upgrade.SCHEMA,
            "status": "database_quiesced",
            "role": "web",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
        }
        document = json.loads(receipt.read_text())
        document["current_units"]["coin-capture.service"]["active"] = True
        receipt.write_text(json.dumps(document), encoding="utf-8")
        receipt.chmod(0o600)
        with (
            patch.object(upgrade, "_read_journal", return_value=payload),
            patch.object(upgrade, "_validate_journal"),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "receipt_invalid"):
                upgrade.prepare_capture_authority(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    web_legacy_collector_receipt=receipt,
                    expected_web_legacy_collector_receipt_sha256=sha256(receipt.read_bytes()).hexdigest(),
                    web_maintenance_lock_path=lock,
                )

    def test_capture_authority_rejects_source_ownership_drift(self) -> None:
        receipt, _digest, lock = self.legacy_receipt()
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["current_units"]["market-channel-capture.service"][
            "source_codes"
        ] = ["MELTED_PRIMARY_FLOW"]
        receipt.write_text(json.dumps(document), encoding="utf-8")
        receipt.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradeError, "receipt_invalid"):
            upgrade._verify_legacy_collector_handoff(
                receipt,
                expected_sha256=sha256(receipt.read_bytes()).hexdigest(),
                release_sha=RELEASE,
                maintenance_lock_path=lock,
            )

    def test_legacy_handoff_rechecks_live_systemd_after_receipt(self) -> None:
        receipt, digest, lock = self.legacy_receipt()
        with patch.object(
            upgrade,
            "_systemd_state",
            side_effect=lambda action, unit: action == "is-active" and unit.endswith(".service"),
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "live_overlap"):
                upgrade._verify_legacy_collector_handoff(
                    receipt,
                    expected_sha256=digest,
                    release_sha=RELEASE,
                    maintenance_lock_path=lock,
                )

    def test_legacy_handoff_rejects_enabled_inactive_standalone_owner(self) -> None:
        receipt, digest, lock = self.legacy_receipt()

        def systemd_state(action: str, unit: str) -> bool:
            return (
                action == "is-enabled"
                and unit == "coin-capture.service"
            )

        with patch.object(
            upgrade,
            "_systemd_state",
            side_effect=systemd_state,
        ):
            with self.assertRaisesRegex(upgrade.UpgradeError, "live_overlap"):
                upgrade._verify_legacy_collector_handoff(
                    receipt,
                    expected_sha256=digest,
                    release_sha=RELEASE,
                    maintenance_lock_path=lock,
                )

    def test_bot_authority_receipt_is_role_bound_and_fail_closed(self) -> None:
        payload = self.web_upgrade_journal()
        upgrade._prepare_marker_transition(payload)
        upgrade._atomic_json(self.journal, payload)
        prepared_digest = upgrade._sha256(self.journal)
        marker_digest = upgrade._marker_authority_digest(payload)
        receipt, digest, _lock = self.legacy_receipt(
            host_role="bot",
            status="AUTHORITY_TRANSFERRING",
            prepared_journal_sha256=prepared_digest,
            marker_authority_sha256=marker_digest,
        )
        verified = upgrade._verify_bot_authority_handoff(
            receipt,
            expected_sha256=digest,
            release_sha=RELEASE,
            bluegreen_journal=self.journal,
            prepared_bluegreen_journal_sha256=prepared_digest,
            marker_authority_sha256=marker_digest,
        )
        self.assertEqual(verified["host_role"], "bot")
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["current_units"][
            "coin-public-market-telegram.service"
        ]["enabled"] = True
        receipt.write_text(json.dumps(document), encoding="utf-8")
        receipt.chmod(0o600)
        with self.assertRaisesRegex(
            upgrade.UpgradeError, "bot_collector_receipt_invalid"
        ):
            upgrade._verify_bot_authority_handoff(
                receipt,
                expected_sha256=sha256(receipt.read_bytes()).hexdigest(),
                release_sha=RELEASE,
                bluegreen_journal=self.journal,
                prepared_bluegreen_journal_sha256=prepared_digest,
                marker_authority_sha256=marker_digest,
            )

    def test_maintenance_inode_guard_rejects_a_separate_process_lock(self) -> None:
        _receipt, _digest, lock = self.legacy_receipt()
        script = (
            "import fcntl,os,sys,time; "
            "fd=os.open(sys.argv[1],os.O_RDWR); "
            "fcntl.flock(fd,fcntl.LOCK_EX); "
            "print('ready',flush=True); time.sleep(10)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(lock)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "maintenance_transition_locked"
            ):
                with upgrade._maintenance_inode_guard(
                    lock, release_sha=RELEASE
                ):
                    self.fail("a second process must never share authority")
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()

    def test_marker_transition_resumes_after_write_before_journal(self) -> None:
        prior = {
            "contract": upgrade.AUTHORITY_CONTRACT,
            "authority": "container",
            "role": "market-capture-account1",
            "release_sha": "d" * 40,
        }
        payload = {
            "release_sha": RELEASE,
            "markers": {
                "market-capture-account1": {
                    "path": "/secure/account1.json",
                    "payload": prior,
                    "sha256": upgrade._json_digest(prior),
                }
            },
            "marker_transition": {
                "status": "NOT_STARTED",
                "authorized_at_utc": None,
                "entries": {},
            },
        }
        upgrade._prepare_marker_transition(payload)
        entry = payload["marker_transition"]["entries"]["market-capture-account1"]
        current = {"digest": entry["prior_sha256"]}

        def write_marker(_path, marker):
            self.assertEqual(marker, entry["target_payload"])
            current["digest"] = entry["target_sha256"]

        writes = {"count": 0}

        def interrupted_write(_journal, _payload):
            writes["count"] += 1
            if writes["count"] == 2:
                raise OSError("simulated crash after marker rename")

        with (
            patch.object(upgrade, "_sha256", side_effect=lambda _path: current["digest"]),
            patch.object(upgrade, "_write_marker", side_effect=write_marker),
            patch.object(upgrade, "_load_marker"),
            patch.object(upgrade, "_atomic_json", side_effect=interrupted_write),
        ):
            with self.assertRaises(OSError):
                upgrade._apply_marker_transition(journal=self.journal, payload=payload)

        # Model reload from the last durable PREPARED journal: the marker is
        # already target, while its durable row is still pending.
        entry["status"] = "PENDING"
        payload["marker_transition"]["status"] = "APPLYING"
        with (
            patch.object(upgrade, "_sha256", side_effect=lambda _path: current["digest"]),
            patch.object(upgrade, "_write_marker") as rewrite,
            patch.object(upgrade, "_load_marker"),
            patch.object(upgrade, "_atomic_json"),
        ):
            upgrade._apply_marker_transition(journal=self.journal, payload=payload)
        rewrite.assert_not_called()
        self.assertEqual(payload["marker_transition"]["status"], "COMPLETE")
        self.assertEqual(entry["status"], "APPLIED")

    def test_sigkill_after_legacy_final_wal_completes_blue_journal(self) -> None:
        receipt_path, _digest, lock = self.legacy_receipt()
        self.journal.parent.mkdir(mode=0o700)
        self.journal.write_text("{}\n", encoding="utf-8")
        self.journal.chmod(0o600)
        prior = {
            "contract": upgrade.AUTHORITY_CONTRACT,
            "authority": "container",
            "role": "market-capture-account1",
            "release_sha": "d" * 40,
        }
        payload = {
            "release_sha": RELEASE,
            "markers": {
                "market-capture-account1": {
                    "path": "/secure/account1.json",
                    "payload": prior,
                    "sha256": upgrade._json_digest(prior),
                }
            },
            "marker_transition": {
                "status": "NOT_STARTED",
                "authorized_at_utc": None,
                "entries": {},
            },
        }
        upgrade._prepare_marker_transition(payload)
        payload["marker_transition"]["status"] = "COMPLETE"
        for row in payload["marker_transition"]["entries"].values():
            row["status"] = "APPLIED"
        lock_payload = json.loads(lock.read_text(encoding="utf-8"))
        authority = {
            "bluegreen_journal_path_sha256": sha256(
                str(self.journal).encode("utf-8")
            ).hexdigest(),
            "prepared_bluegreen_journal_sha256": "7" * 64,
            "authorization_bluegreen_journal_sha256": upgrade._sha256(
                self.journal
            ),
            "marker_authority_sha256": upgrade._marker_authority_digest(payload),
        }
        receipt = {
            "status": "AUTHORITY_TRANSFERRED",
            "maintenance_lock": lock_payload,
            "authority_transfer": authority,
        }
        descriptor = os.open(lock, os.O_RDWR)
        try:
            with (
                patch.object(upgrade, "_systemd_state", return_value=False),
                patch.object(upgrade, "_verify_authorized_markers"),
                patch.object(upgrade, "_atomic_json") as durable_write,
            ):
                recovered = upgrade._recover_completed_authority_binding(
                    payload=payload,
                    journal=self.journal,
                    receipt_path=receipt_path,
                    receipt=receipt,
                    release_sha=RELEASE,
                    descriptor=descriptor,
                    held_lock=lock_payload,
                )
            durable_write.assert_called_once()
        finally:
            os.close(descriptor)
        self.assertEqual(recovered["legacy_authority_transfer"], authority)
        self.assertEqual(
            recovered["legacy_collector_receipt_sha256"],
            upgrade._sha256(receipt_path),
        )

    def test_prelegacy_crash_reload_can_resume_forward(self) -> None:
        receipt, digest, lock, bot_receipt, bot_digest = (
            self.interrupt_before_legacy_authority_wal()
        )
        with (
            patch.object(upgrade, "_ids", return_value=[]),
            patch.object(upgrade, "_new_database_identity", return_value={}),
            patch.object(upgrade, "_new_identity", return_value={}),
            patch.object(upgrade, "_systemd_state", return_value=False),
            patch.object(upgrade, "_load_marker", side_effect=self.marker_load_for_test),
            patch.object(upgrade, "_write_marker", side_effect=self.marker_write_for_test),
            patch.object(legacy_handoff, "OPERATION_LOCK_PATH", lock),
            patch.object(legacy_handoff, "APPROVED_ROOT", self.root),
            patch.object(
                legacy_handoff,
                "_assert_quiesced",
                return_value=legacy_handoff._read(
                    receipt, release_sha=RELEASE, host_role="web"
                )["current_units"],
            ),
        ):
            result = upgrade.authorize_captures(
                journal=self.journal,
                role="web",
                release_sha=RELEASE,
                web_legacy_collector_receipt=receipt,
                expected_web_legacy_collector_receipt_sha256=digest,
                web_maintenance_lock_path=lock,
                bot_legacy_collector_receipt=bot_receipt,
                expected_bot_legacy_collector_receipt_sha256=bot_digest,
            )
        reloaded = upgrade._read_journal(self.journal)
        legacy = legacy_handoff._read(
            receipt, release_sha=RELEASE, host_role="web"
        )
        self.assertEqual(result["status"], "captures_authorized")
        self.assertEqual(reloaded["status"], "captures_authorized")
        self.assertEqual(legacy["status"], "AUTHORITY_TRANSFERRED")
        for role, row in reloaded["marker_transition"]["entries"].items():
            self.assertEqual(row["status"], "APPLIED")
            self.assertEqual(upgrade._sha256(Path(row["path"])), row["target_sha256"])
            self.marker_load_for_test(
                Path(row["path"]), role=role, release_sha=RELEASE
            )

    def test_authority_rechecks_systemd_immediately_before_marker_write(self) -> None:
        payload = self.web_upgrade_journal()
        receipt, digest, lock = self.legacy_receipt()
        calls = {"count": 0}

        def systemd_state(action: str, _unit: str) -> bool:
            calls["count"] += 1
            # Receipt validation checks active and enabled state for every
            # overlapping unit.  The first probe of the terminal pre-marker
            # recheck then observes a race.
            initial_probes = 2 * len(upgrade.LEGACY_COLLECTOR_UNITS)
            return calls["count"] > initial_probes and action == "is-active"

        with (
            patch.object(upgrade, "_ids", return_value=[]),
            patch.object(upgrade, "_new_database_identity", return_value={}),
            patch.object(upgrade, "_new_identity", return_value={}),
            patch.object(upgrade, "_systemd_state", side_effect=systemd_state),
            patch.object(upgrade, "_load_marker", side_effect=self.marker_load_for_test),
            patch.object(upgrade, "_write_marker", side_effect=self.marker_write_for_test) as write_marker,
            patch.object(legacy_handoff, "OPERATION_LOCK_PATH", lock),
            patch.object(legacy_handoff, "APPROVED_ROOT", self.root),
            patch.object(
                legacy_handoff,
                "_assert_quiesced",
                return_value=legacy_handoff._read(
                    receipt, release_sha=RELEASE, host_role="web"
                )["current_units"],
            ),
        ):
            upgrade.prepare_capture_authority(
                journal=self.journal,
                role="web",
                release_sha=RELEASE,
                web_legacy_collector_receipt=receipt,
                expected_web_legacy_collector_receipt_sha256=digest,
                web_maintenance_lock_path=lock,
            )
            bot_receipt, bot_digest, _bot_lock = self.legacy_receipt(
                host_role="bot",
                status="AUTHORITY_TRANSFERRING",
                prepared_journal_sha256=upgrade._sha256(self.journal),
                marker_authority_sha256=upgrade._marker_authority_digest(
                    upgrade._read_journal(self.journal)
                ),
            )
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "legacy_collector_live_overlap"
            ):
                upgrade.authorize_captures(
                    journal=self.journal,
                    role="web",
                    release_sha=RELEASE,
                    web_legacy_collector_receipt=receipt,
                    expected_web_legacy_collector_receipt_sha256=digest,
                    web_maintenance_lock_path=lock,
                    bot_legacy_collector_receipt=bot_receipt,
                    expected_bot_legacy_collector_receipt_sha256=bot_digest,
                )
        write_marker.assert_not_called()
        reloaded = upgrade._read_journal(self.journal)
        self.assertEqual(reloaded["marker_transition"]["status"], "PREPARED")
        for row in reloaded["marker_transition"]["entries"].values():
            self.assertEqual(upgrade._sha256(Path(row["path"])), row["prior_sha256"])
        self.assertEqual(payload["status"], "database_quiesced")

    def test_prelegacy_crash_reload_rolls_back_without_legacy_restore_wal(self) -> None:
        receipt, _digest, lock, _bot_receipt, _bot_digest = (
            self.interrupt_before_legacy_authority_wal()
        )
        payload = upgrade._read_journal(self.journal)
        rows = {row["service"]: dict(row) for row in payload["services"]}
        running = {service: False for service in rows}
        restart = {
            service: (row["restart_name"] if service == "market-migration" else "no")
            for service, row in rows.items()
        }
        by_id = {row["container_id"]: service for service, row in rows.items()}

        def ids(project, service, *, running=False):
            del running
            if project == OLD_PROJECT:
                return [rows[service]["container_id"]]
            return []

        def identity(container_id, *, project, service):
            self.assertEqual(project, OLD_PROJECT)
            self.assertEqual(by_id[container_id], service)
            row = dict(rows[service])
            row["running"] = running[service]
            row["health"] = "healthy" if running[service] else None
            row["restart_name"] = restart[service]
            return row

        def run(arguments, **_kwargs):
            command = arguments[1]
            container_id = arguments[-1]
            service = by_id[container_id]
            if command == "start":
                running[service] = True
            elif command == "update":
                restart[service] = next(
                    value.split("=", 1)[1]
                    for value in arguments
                    if value.startswith("--restart=")
                ).split(":", 1)[0]
            else:
                self.fail(f"unexpected Docker mutation: {arguments}")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch.object(upgrade, "_project_services", return_value=set()),
            patch.object(upgrade, "_ids", side_effect=ids),
            patch.object(upgrade, "_identity", side_effect=identity),
            patch.object(upgrade, "_run", side_effect=run) as docker_run,
            patch.object(upgrade, "_systemd_state", return_value=False),
            patch.object(upgrade, "_load_marker", side_effect=self.marker_load_for_test),
            patch.object(upgrade, "_write_marker", side_effect=self.marker_write_for_test),
            patch.object(legacy_handoff, "OPERATION_LOCK_PATH", lock),
            patch.object(legacy_handoff, "APPROVED_ROOT", self.root),
            patch.object(
                legacy_handoff,
                "mark_capture_authority_restored_with_held_lock",
            ) as legacy_restore,
        ):
            result = upgrade.rollback(
                journal=self.journal, role="web", release_sha=RELEASE
            )
            mutation_count = docker_run.call_count
            lock.write_text("{}\n", encoding="utf-8")
            lock.chmod(0o600)
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "maintenance_lock_invalid"
            ):
                upgrade.rollback(
                    journal=self.journal, role="web", release_sha=RELEASE
                )
            self.assertEqual(docker_run.call_count, mutation_count)
        legacy_restore.assert_not_called()
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertEqual(
            legacy_handoff._read(
                receipt, release_sha=RELEASE, host_role="web"
            )["status"],
            "QUIESCED",
        )
        self.assertTrue(
            all(running[service] for service in upgrade.RESTORE_ORDER["web"])
        )

    def test_prelegacy_target_marker_fails_before_docker_mutation(self) -> None:
        _receipt, _digest, lock, _bot_receipt, _bot_digest = (
            self.interrupt_before_legacy_authority_wal()
        )
        payload = upgrade._read_journal(self.journal)
        row = next(iter(payload["marker_transition"]["entries"].values()))
        self.marker_write_for_test(Path(row["path"]), row["target_payload"])
        with (
            patch.object(upgrade, "_systemd_state", return_value=False),
            patch.object(upgrade, "_load_marker", side_effect=self.marker_load_for_test),
            patch.object(upgrade, "_run") as docker_mutation,
            patch.object(legacy_handoff, "OPERATION_LOCK_PATH", lock),
            patch.object(legacy_handoff, "APPROVED_ROOT", self.root),
        ):
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "prelegacy_authority_state_invalid"
            ):
                upgrade.rollback(
                    journal=self.journal, role="web", release_sha=RELEASE
                )
        docker_mutation.assert_not_called()

    def test_rollback_preflight_rejects_unknown_new_service_before_mutation(self) -> None:
        rows = [self.identity(service, running=False) for service in upgrade.ROLE_SERVICES["bot"]]
        payload = {
            "role": "bot",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
            "new_image_id": IMAGE,
            "services": rows,
        }
        with patch.object(upgrade, "_project_services", return_value={"unknown-service"}):
            with self.assertRaisesRegex(upgrade.UpgradeError, "new_inventory_invalid"):
                upgrade._rollback_preflight(payload, role="bot")

    def test_legacy_schema_is_rollback_only_and_terminally_revalidated(self) -> None:
        rows = [
            self.identity(service, running=service != "market-migration")
            for service in upgrade.ROLE_SERVICES["bot"]
        ]
        payload = {
            "schema": upgrade.LEGACY_SCHEMA,
            "status": "planned",
            "role": "bot",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
            "old_env": str(self.old_env),
            "new_env": str(self.new_env),
            "old_env_sha256": sha256(self.old_env.read_bytes()).hexdigest(),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "new_image_id": IMAGE,
            "services": rows,
            "markers": {},
            "product_authority_changed": False,
            "state_deleted": False,
            "secrets_disclosed": False,
        }
        upgrade._atomic_json(self.journal, payload, exclusive=True)
        with patch.object(upgrade, "_run") as mutation:
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "legacy_journal_forward_forbidden"
            ):
                upgrade.quiesce_workload(
                    journal=self.journal, role="bot", release_sha=RELEASE
                )
        mutation.assert_not_called()

        payload["status"] = "ROLLED_BACK"
        upgrade._atomic_json(self.journal, payload)
        by_service = {row["service"]: row for row in rows}
        with (
            patch.object(upgrade, "_project_services", return_value=set()),
            patch.object(
                upgrade,
                "_ids",
                side_effect=lambda project, service: (
                    [by_service[service]["container_id"]]
                    if project == OLD_PROJECT
                    else []
                ),
            ),
            patch.object(
                upgrade,
                "_identity",
                side_effect=lambda _container_id, *, project, service: dict(
                    by_service[service]
                ),
            ),
        ):
            result = upgrade.rollback(
                journal=self.journal, role="bot", release_sha=RELEASE
            )
        self.assertEqual(result["status"], "ROLLED_BACK")

    def test_terminal_blue_rollback_drift_fails_without_mutation(self) -> None:
        rows = [
            self.identity(service, running=service != "market-migration")
            for service in upgrade.ROLE_SERVICES["bot"]
        ]
        payload = {
            "schema": upgrade.SCHEMA,
            "journal_contract_revision": upgrade.JOURNAL_CONTRACT_REVISION,
            "status": "ROLLED_BACK",
            "role": "bot",
            "release_sha": RELEASE,
            "old_project": OLD_PROJECT,
            "new_project": NEW_PROJECT,
            "old_env": str(self.old_env),
            "new_env": str(self.new_env),
            "old_env_sha256": sha256(self.old_env.read_bytes()).hexdigest(),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
            "new_image_id": IMAGE,
            "services": rows,
            "markers": {},
            "product_authority_changed": False,
            "state_deleted": False,
            "secrets_disclosed": False,
        }
        upgrade._atomic_json(self.journal, payload, exclusive=True)
        by_service = {row["service"]: row for row in rows}

        def identity(_container_id, *, project, service):
            self.assertEqual(project, OLD_PROJECT)
            row = dict(by_service[service])
            if service == upgrade.ROLE_SERVICES["bot"][0]:
                row["running"] = False
                row["health"] = None
            return row

        with (
            patch.object(upgrade, "_project_services", return_value=set()),
            patch.object(
                upgrade,
                "_ids",
                side_effect=lambda project, service: (
                    [by_service[service]["container_id"]]
                    if project == OLD_PROJECT
                    else []
                ),
            ),
            patch.object(upgrade, "_identity", side_effect=identity),
            patch.object(upgrade, "_run") as mutation,
        ):
            with self.assertRaisesRegex(
                upgrade.UpgradeError, "rollback_terminal_state_drift"
            ):
                upgrade.rollback(
                    journal=self.journal, role="bot", release_sha=RELEASE
                )
        mutation.assert_not_called()

    def test_new_database_identity_uses_pinned_postgres_and_exact_bind_root(self) -> None:
        container_id = "e" * 64
        payload = {
            "new_project": NEW_PROJECT,
            "new_env": str(self.new_env),
            "new_env_sha256": sha256(self.new_env.read_bytes()).hexdigest(),
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
