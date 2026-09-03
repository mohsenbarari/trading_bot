import hashlib
import fcntl
import json
import os
import signal
import tempfile
import unittest
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

from scripts import cutover_telegram_delivery_queue_production as cutover
from scripts import plan_telegram_delivery_queue_production as planner


class FakeOperations:
    def __init__(
        self,
        _manifest: Path,
        *,
        fail_first_deploy: bool = False,
        interrupt_first_deploy: bool = False,
    ) -> None:
        fail_first_deploy = fail_first_deploy or interrupt_first_deploy
        inventories = [
                {"count": 1, "owner": "legacy", "overlap": False},
                {"count": 0, "owner": None, "overlap": False},
        ]
        if fail_first_deploy:
            inventories.extend(
                (
                    {"count": 0, "owner": None, "overlap": False},
                    {"count": 1, "owner": "legacy", "overlap": False},
                )
            )
        else:
            inventories.append(
                {"count": 1, "owner": "queue-v1", "overlap": False}
            )
        self.inventories = iter(inventories)
        self.fail_first_deploy = fail_first_deploy
        self.interrupt_first_deploy = interrupt_first_deploy
        self.deploy_calls = 0
        self.mutations: list[str] = []

    def executor_inventory(self):
        return next(self.inventories)

    def stop_producers(self):
        self.mutations.append("stop_producers")
        return [
            (role, service)
            for role in ("foreign", "iran")
            for service in ("app", "sync")
        ]

    def resume_producers(self, _stopped):
        self.mutations.append("resume_producers")

    def wait_for_drain(self, _timeout, _poll):
        return {
            "status": "drained",
            "roles": {
                role: {key: 0 for key in cutover.OPEN_QUEUE_KEYS}
                for role in ("foreign", "iran")
            },
        }

    def stop_bot(self):
        self.mutations.append("stop_bot")

    def start_bot(self):
        self.mutations.append("start_bot")

    def deploy_official(self, _authority_path=None, _authority_digest=None, **_kwargs):
        self.deploy_calls += 1
        self.mutations.append("deploy")
        if self.fail_first_deploy and self.deploy_calls == 1:
            if self.interrupt_first_deploy:
                raise KeyboardInterrupt("simulated signal")
            raise cutover.ProductionCutoverError("SIMULATED_DEPLOY_FAILURE")
        return {"status": "completed", "official_script": True}

    def runtime_contract(self, _values, *, expected_owner):
        return {"status": "verified", "owner": expected_owner, "tokens_disclosed": False}

    def queue_health(self, _database_name):
        return {"status": "passed", "decision": "continue"}

    def b2b_lane_probe(self):
        return {"status": "passed", "synthetic_mutations": 0}


class FakeRollbackOperations(FakeOperations):
    def __init__(self, manifest: Path) -> None:
        super().__init__(manifest)
        self.inventories = iter(
            (
                {"count": 1, "owner": "queue-v1", "overlap": False},
                {"count": 0, "owner": None, "overlap": False},
                {"count": 1, "owner": "legacy", "overlap": False},
            )
        )


class FakeRedeployOperations:
    def __init__(self, _manifest: Path, *, fail_first_deploy: bool = False) -> None:
        self.fail_first_deploy = fail_first_deploy
        self.deploy_calls = 0
        self.private_primary_attestations = []
        self.inventories = iter(
            (
                {"count": 1, "owner": "queue-v1", "overlap": False},
                {"count": 1, "owner": "queue-v1", "overlap": False},
            )
        )

    def executor_inventory(self):
        return next(self.inventories)

    def deploy_official(
        self,
        _authority_path=None,
        _authority_digest=None,
        *,
        private_primary_attestation=None,
        **_kwargs,
    ):
        self.deploy_calls += 1
        self.private_primary_attestations.append(private_primary_attestation)
        if self.fail_first_deploy and self.deploy_calls == 1:
            raise cutover.ProductionCutoverError("SIMULATED_REDEPLOY_FAILURE")
        return {"status": "completed", "official_script": True}

    def release_schema_inventory(self, _expected_database_name):
        return {
            "status": "verified",
            "release_sha": "c" * 40,
            "schema_head": "ff6c7d8e9f01",
            "queue_table_count": len(planner.REQUIRED_QUEUE_TABLES),
            "database_identity_sha256": {
                "foreign": "d" * 64,
                "iran": "e" * 64,
            },
        }

    def runtime_contract(self, _values, *, expected_owner):
        return {"status": "verified", "owner": expected_owner}

    def queue_health(self, _database_name):
        return {"status": "passed", "decision": "continue"}

    def b2b_lane_probe(self):
        return {"status": "passed", "synthetic_mutations": 0}


class FakeInterruptedRecoveryOperations:
    def __init__(self, manifest: Path, *, owner: str | None, artifact_dir: Path) -> None:
        self.manifest = manifest
        self.owner = owner
        self.artifact_dir = artifact_dir
        self.observed_wal: list[tuple[str, str]] = []

    def _status(self) -> str:
        paths = list(self.artifact_dir.glob("production-queue-phase-*.json"))
        assert len(paths) == 1
        return json.loads(paths[0].read_text(encoding="utf-8"))["status"]

    def executor_inventory(self):
        return {
            "count": 0 if self.owner is None else 1,
            "owner": self.owner,
            "overlap": False,
        }

    def stop_producers(self):
        self.observed_wal.append(("stop_producers", self._status()))
        return [("foreign", "app"), ("iran", "app")]

    def wait_for_drain(self, _timeout, _poll):
        self.observed_wal.append(("wait_for_drain", self._status()))
        return {"status": "drained"}

    def stop_bot(self):
        self.observed_wal.append(("stop_bot", self._status()))
        self.owner = None

    def deploy_official(
        self,
        _authority_path=None,
        _authority_digest=None,
        *,
        private_primary_attestation=None,
        **_kwargs,
    ):
        del private_primary_attestation
        self.observed_wal.append(("deploy", self._status()))
        manifest_values = planner.parse_env_file(self.manifest)
        source = Path(manifest_values["RUNTIME_ENV_SOURCE_PATH"])
        self.owner = planner.source_profile(planner.parse_env_file(source))
        return {"status": "completed", "official_script": True}

    def runtime_contract(self, _values, *, expected_owner):
        if self.owner != expected_owner:
            raise AssertionError((self.owner, expected_owner))
        return {"status": "verified", "owner": expected_owner}

    def queue_health(self, _database_name):
        return {"status": "passed", "decision": "continue"}


class ProductionQueueCutoverTests(unittest.TestCase):
    def write_env(self, path: Path, values: dict[str, str]) -> None:
        path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def legacy_values(self, *, complete: bool = True) -> dict[str, str]:
        values = {
            "BOT_TOKEN": "production-central-token",
            "BOT_USERNAME": "production_central_bot",
            "CHANNEL_ID": "-100999",
            "POSTGRES_DB": "production_db",
            "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
            "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
            "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
            "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
            "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
            "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
            "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100999",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "1000",
        }
        if complete:
            for index in range(1, 6):
                values.update(
                    {
                        f"TELEGRAM_PUBLISHER_{index}_ENABLED": "false",
                        f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"production-publisher-token-{index}",
                        f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(1000 + index),
                        f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": f"production_publisher_{index}_bot",
                    }
                )
        return values

    def staging_values(self) -> dict[str, str]:
        values = {
            **planner.QUEUE_PROFILE,
            "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER": "producer-only",
            "BOT_TOKEN": "staging-central-token",
            "BOT_USERNAME": "staging_central_bot",
            "CHANNEL_ID": "-100888",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100888",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "2000",
        }
        for index in range(1, 6):
            values.update(
                {
                    f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true",
                    f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"staging-publisher-token-{index}",
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(2000 + index),
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": f"staging_publisher_{index}_bot",
                }
            )
        return values

    def fixture(self, root: Path, *, complete: bool = True):
        source = root / "master.env"
        staging = root / "staging.env"
        manifest = root / "online.env"
        ssh_key = root / "production-key"
        ssh_key.write_text("test-key", encoding="utf-8")
        ssh_key.chmod(0o600)
        self.write_env(source, self.legacy_values(complete=complete))
        self.write_env(staging, self.staging_values())
        self.write_env(
            manifest,
            {
                "RUNTIME_ENV_SOURCE_PATH": str(source),
                "FOREIGN_RUNTIME_ENV_PATH": str(root / "foreign.env"),
                "IRAN_RUNTIME_ENV_PATH": str(root / "iran.env"),
                "LOCAL_PROJECT_DIR": str(planner.REPO_ROOT),
                "FOREIGN_PUBLIC_DOMAIN": planner.PRODUCTION_FOREIGN_DOMAIN,
                "IRAN_HOST": "production-iran.invalid",
                "IRAN_SSH_USER": "root",
                "IRAN_SSH_PORT": "37067",
                "IRAN_PROJECT_DIR": planner.PRODUCTION_IRAN_PROJECT_DIR,
                "IRAN_APP_DOMAIN": planner.PRODUCTION_IRAN_APP_DOMAIN,
                "IRAN_PUBLIC_DOMAIN": planner.PRODUCTION_IRAN_APP_DOMAIN,
                "IRAN_SSH_AUTH_METHOD": "key",
                "IRAN_SSH_PRIVATE_KEY_PATH": str(ssh_key),
                "IRAN_SKIP_FOREIGN_DEPLOY": "0",
                "IRAN_DEPLOY_WITH_WAIT": "1",
                "IRAN_RUN_POST_DEPLOY_HEALTHCHECK": "1",
                "IRAN_ALLOW_DIRTY_RELEASE": "0",
                "IRAN_ALLOW_NON_MAIN_RELEASE": "0",
                "IRAN_ALLOW_RELEASE_BRANCH_DRIFT": "0",
                "IRAN_SHARED_DATA_MODE": "skip",
                "IRAN_SHARED_RESET_CONFIRM": "",
            },
        )
        return source, staging, manifest

    def binding(self):
        return {
            "branch": "main",
            "head": "a" * 40,
            "tree": "b" * 40,
            "origin_main": "a" * 40,
            "worktree": "clean",
        }

    def abandon_lock_as_dead_process(self, lock: cutover.ExclusiveRunLock) -> None:
        assert lock.descriptor is not None
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
        payload["owner_start_ticks"] = "0"
        os.lseek(lock.descriptor, 0, os.SEEK_SET)
        os.ftruncate(lock.descriptor, 0)
        os.write(
            lock.descriptor,
            (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        )
        os.fsync(lock.descriptor)
        os.close(lock.descriptor)
        lock.descriptor = None
        lock.held = False

    def test_verify_authority_cli_arguments_parse_without_conflict(self):
        parsed = cutover.parse_args(
            [
                "verify-deploy-authority",
                "--deploy-authority",
                "/secure/receipt.json",
                "--deploy-authority-sha256",
                "a" * 64,
            ]
        )
        self.assertEqual(parsed.command, "verify-deploy-authority")
        self.assertEqual(parsed.deploy_authority_sha256, "a" * 64)

    def test_private_primary_attestation_cli_is_all_or_nothing(self):
        parsed = cutover.parse_args(
            [
                "verify-deploy-authority",
                "--manifest",
                "/secure/online.env",
                "--deploy-authority",
                "/secure/authority.json",
                "--deploy-authority-sha256",
                "a" * 64,
                "--private-primary-manifest-sha256",
                "b" * 64,
                "--private-primary-manifest-receipt",
                "/secure/private-primary.json",
                "--private-primary-manifest-receipt-sha256",
                "c" * 64,
            ]
        )
        self.assertEqual(parsed.private_primary_manifest_sha256, "b" * 64)
        self.assertEqual(
            parsed.private_primary_manifest_receipt,
            Path("/secure/private-primary.json"),
        )
        missing = cutover.parse_args(
            [
                "verify-deploy-authority",
                "--private-primary-manifest-sha256",
                "b" * 64,
            ]
        )
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError,
            "BLOCKED_PRIVATE_PRIMARY_ATTESTATION",
        ):
            cutover.private_primary_deploy_attestation_from_args(missing)

    def write_preflight(self, path: Path, binding, backup_digest: str, source_digest: str) -> str:
        payload = {
            "schema_version": 1,
            "environment": "production",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
            "mode": "read-only",
            "git": binding,
            "queue_profile": {"ready": True, "mismatch_keys": []},
            "source_profile": "legacy",
            "source_sha256": source_digest,
            "target_queue_cutover": True,
            "credentials": {"status": "ready", "identity_count": 6, "publisher_count": 5},
            "backup": {
                "status": "verified",
                "digest": backup_digest,
                "target_binding_exact": True,
                "release_and_database_identity_exact": True,
            },
            "hosts": {
                "ready": True,
                "release_sha_exact": True,
                "schema_head_and_queue_tables_exact": True,
                "database_identity_exact": True,
            },
            "provider": {
                "status": "approved",
                "identity_count": 6,
                "staging_identity_count": 6,
            },
            "apply_supported": False,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_redeploy_preflight(
        self,
        path: Path,
        binding,
        backup_digest: str,
        source_digest: str,
    ) -> str:
        payload = {
            "schema_version": 1,
            "environment": "production",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "READY_FOR_QUEUE_V1_REDEPLOY",
            "mode": "read-only",
            "git": binding,
            "source_profile": "queue-v1",
            "source_sha256": source_digest,
            "credentials": {
                "status": "ready",
                "identity_count": 6,
                "publisher_count": 5,
            },
            "backup": {"status": "verified", "digest": backup_digest},
            "current_runtime": {
                "status": "verified",
                "release_sha": "c" * 40,
                "schema_head": "ff6c7d8e9f01",
            },
            "executor_inventory": {
                "count": 1,
                "owner": "queue-v1",
                "overlap": False,
            },
            "queue_health": {"status": "passed", "decision": "continue"},
            "runtime_role_contract": {
                "status": "verified",
                "owner": "queue-v1",
            },
            "provider": {
                "status": "approved",
                "identity_count": 6,
                "staging_identity_count": 6,
            },
            "apply_supported": False,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_confirmation_and_missing_credentials_block_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _source, staging, manifest = self.fixture(root, complete=False)
            factory = Mock(side_effect=AssertionError("operations must not be constructed"))
            with self.assertRaisesRegex(cutover.ProductionCutoverError, "APPLY_CONFIRMATION_MISMATCH"):
                cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=root / "missing",
                    preflight_digest="",
                    backup_receipt=root / "missing-backup",
                    backup_digest="",
                    secure_backup_dir=root,
                    artifact_dir=root,
                    confirmation="wrong",
                    operations_factory=factory,
                )
            with self.assertRaisesRegex(cutover.ProductionCutoverError, "BLOCKED_CREDENTIALS"):
                cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=root / "missing",
                    preflight_digest="",
                    backup_receipt=root / "missing-backup",
                    backup_digest="",
                    secure_backup_dir=root,
                    artifact_dir=root,
                    confirmation=cutover.APPLY_CONFIRMATION,
                    operations_factory=factory,
                )
            factory.assert_not_called()

    def test_owner_approved_shared_publishers_block_before_any_cutover_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            source_values = planner.parse_env_file(source)
            staging_values = planner.parse_env_file(staging)
            source_values[planner.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
            staging_values[planner.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
            for index in range(1, 6):
                for suffix in ("BOT_TOKEN", "EXPECTED_BOT_ID", "EXPECTED_USERNAME"):
                    staging_values[f"TELEGRAM_PUBLISHER_{index}_{suffix}"] = (
                        source_values[f"TELEGRAM_PUBLISHER_{index}_{suffix}"]
                    )
            self.write_env(source, source_values)
            self.write_env(staging, staging_values)

            factory = Mock(side_effect=AssertionError("operations must not be constructed"))
            preflight = Mock(side_effect=AssertionError("preflight must not run"))
            with patch.object(
                cutover,
                "git_binding",
                side_effect=AssertionError("git gate must remain later"),
            ), self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                planner.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
            ):
                cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=root / "missing",
                    preflight_digest="",
                    backup_receipt=root / "missing-backup",
                    backup_digest="",
                    secure_backup_dir=root,
                    artifact_dir=root,
                    confirmation=cutover.APPLY_CONFIRMATION,
                    operations_factory=factory,
                    preflight_runner=preflight,
                )
            factory.assert_not_called()
            preflight.assert_not_called()

    def test_unsafe_official_release_toggles_block_before_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _source, staging, manifest = self.fixture(root)
            manifest_values = planner.parse_env_file(manifest)
            manifest_values["IRAN_SHARED_DATA_MODE"] = "reset"
            self.write_env(manifest, manifest_values)
            factory = Mock(side_effect=AssertionError("operations must not be constructed"))
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_UNSAFE_PRODUCTION_RELEASE_PROFILE",
            ):
                cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=root / "missing",
                    preflight_digest="",
                    backup_receipt=root / "missing-backup",
                    backup_digest="",
                    secure_backup_dir=root,
                    artifact_dir=root,
                    confirmation=cutover.APPLY_CONFIRMATION,
                    operations_factory=factory,
                )
            factory.assert_not_called()

    def test_deploy_authority_is_bound_to_the_live_source_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, _staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            run_lock = cutover.ExclusiveRunLock(artifacts)
            run_lock.acquire()
            source_lock = cutover.ImmutableSourceLock(source)
            source_lock.acquire()
            try:
                journal = cutover.PhaseJournal(
                    artifacts,
                    command="apply",
                    source_sha256=cutover._sha256(source),
                    git_head=binding["head"],
                    run_lock=run_lock,
                )
                journal.update("source_switched")
                with patch.object(cutover, "git_binding", return_value=binding):
                    authority_path, authority_digest = cutover.create_deploy_authority(
                        artifacts,
                        source,
                        binding,
                        run_lock=run_lock,
                        journal=journal,
                    )
                    forged_dir = root / "forged"
                    forged_dir.mkdir(mode=0o700)
                    forged_path = forged_dir / authority_path.name
                    forged_path.write_bytes(authority_path.read_bytes())
                    forged_path.chmod(0o600)
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            forged_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                        )
                    verified = cutover.verify_deploy_authority(
                        manifest,
                        authority_path,
                        authority_digest,
                        expected_artifact_dir=artifacts,
                    )
                self.assertEqual(verified["target_owner"], "queue-v1")
                with patch.object(cutover, "git_binding", return_value=binding):
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            authority_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                        )
            finally:
                source_lock.release()
                run_lock.release()

    def test_private_primary_authority_binds_source_after_and_is_one_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, _staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            source_after = source.read_bytes()
            evidence = root / "private-primary-preparation.json"
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            evidence.chmod(0o600)
            attestation = cutover.bind_private_primary_deploy_attestation(
                manifest,
                manifest_sha256=cutover._sha256(manifest),
                receipt_path=evidence,
                receipt_sha256=cutover._sha256(evidence),
            )
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            run_lock = cutover.ExclusiveRunLock(artifacts)
            source_lock = cutover.ImmutableSourceLock(source)
            run_lock.acquire()
            source_lock.acquire()
            try:
                journal = cutover.PhaseJournal(
                    artifacts,
                    command="redeploy",
                    source_sha256=cutover._sha256(source),
                    git_head=binding["head"],
                    run_lock=run_lock,
                )
                journal.update("official_redeploy_authorizing")
                with patch.object(cutover, "git_binding", return_value=binding):
                    authority_path, authority_digest = (
                        cutover.create_deploy_authority(
                            artifacts,
                            source,
                            binding,
                            run_lock=run_lock,
                            journal=journal,
                            deploy_manifest=manifest,
                            private_primary_attestation=attestation,
                        )
                    )
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            authority_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                        )
                    source.write_text(
                        source.read_text(encoding="utf-8") + "UNRELATED_BINDING=changed\n",
                        encoding="utf-8",
                    )
                    source.chmod(0o600)
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            authority_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                            private_primary_attestation=attestation,
                        )
                    source.write_bytes(source_after)
                    source.chmod(0o600)
                    verified = cutover.verify_deploy_authority(
                        manifest,
                        authority_path,
                        authority_digest,
                        expected_artifact_dir=artifacts,
                        private_primary_attestation=attestation,
                    )
                    self.assertTrue(
                        verified["private_primary_manifest_attestation_bound"]
                    )
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            authority_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                            private_primary_attestation=attestation,
                        )
            finally:
                source_lock.release()
                run_lock.release()

    def test_shell_queue_gate_consumes_exact_private_attestation_authority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, _staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            preparation = root / "private-primary-preparation.json"
            preparation.write_text('{"status":"PASS"}\n', encoding="utf-8")
            preparation.chmod(0o600)
            attestation = cutover.bind_private_primary_deploy_attestation(
                manifest,
                manifest_sha256=cutover._sha256(manifest),
                receipt_path=preparation,
                receipt_sha256=cutover._sha256(preparation),
            )
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            run_lock = cutover.ExclusiveRunLock(artifacts)
            source_lock = cutover.ImmutableSourceLock(source)
            run_lock.acquire()
            source_lock.acquire()
            try:
                journal = cutover.PhaseJournal(
                    artifacts,
                    command="redeploy",
                    source_sha256=cutover._sha256(source),
                    git_head=binding["head"],
                    run_lock=run_lock,
                )
                journal.update("official_redeploy_authorizing")
                authority_path, authority_digest = cutover.create_deploy_authority(
                    artifacts,
                    source,
                    binding,
                    run_lock=run_lock,
                    journal=journal,
                    deploy_manifest=manifest,
                    private_primary_attestation=attestation,
                )
                fake_bin = root / "bin"
                fake_bin.mkdir(mode=0o700)
                fake_git = fake_bin / "git"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    f"  'rev-parse --abbrev-ref HEAD') echo main ;;\n"
                    f"  'rev-parse HEAD') echo {'a' * 40} ;;\n"
                    f"  'rev-parse HEAD^{{tree}}') echo {'b' * 40} ;;\n"
                    f"  'rev-parse origin/main') echo {'a' * 40} ;;\n"
                    "  'status --porcelain') exit 0 ;;\n"
                    "  *) exit 2 ;;\n"
                    "esac\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        """
source "$1"
MANIFEST_PATH="$2"
PRODUCTION_RELEASE_LOCK_DIR="$3"
PRODUCTION_RELEASE_LOCK_PATH="$3/production-release.lock"
TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT="$4"
TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT_SHA256="$5"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_EXPECTED_SHA256="$6"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_PATH="$7"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_SHA256="$8"
verify_queue_cutover_deploy_authority
""",
                        "queue-authority-shell",
                        str(
                            cutover.REPO_ROOT
                            / "scripts/production_deploy_online.sh"
                        ),
                        str(manifest),
                        str(artifacts),
                        str(authority_path),
                        authority_digest,
                        attestation.manifest_sha256,
                        str(attestation.receipt_path),
                        attestation.receipt_sha256,
                    ],
                    cwd=cutover.REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    env={
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "PYTHONPATH": str(cutover.REPO_ROOT),
                        "LANG": os.environ.get("LANG", "C.UTF-8"),
                        "TZ": "UTC",
                    },
                )
                self.assertEqual(
                    result.returncode, 0, result.stderr + result.stdout
                )
                state_name = json.loads(
                    authority_path.read_text(encoding="utf-8")
                )["state_file"]
                state = json.loads(
                    (artifacts / state_name).read_text(encoding="utf-8")
                )
                self.assertEqual(state["status"], "consumed")
            finally:
                source_lock.release()
                run_lock.release()

    def test_swapped_release_lock_cannot_authorize_or_be_silently_unlinked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, _staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            run_lock = cutover.ExclusiveRunLock(artifacts)
            run_lock.acquire()
            source_lock = cutover.ImmutableSourceLock(source)
            source_lock.acquire()
            replacement_created = False
            try:
                journal = cutover.PhaseJournal(
                    artifacts,
                    command="apply",
                    source_sha256=cutover._sha256(source),
                    git_head=binding["head"],
                    run_lock=run_lock,
                )
                journal.update("source_switched")
                authority_path, authority_digest = cutover.create_deploy_authority(
                    artifacts,
                    source,
                    binding,
                    run_lock=run_lock,
                    journal=journal,
                )
                run_lock.path.unlink()
                run_lock.path.write_text(
                    json.dumps({"environment": "production", "forged": True}),
                    encoding="utf-8",
                )
                run_lock.path.chmod(0o600)
                replacement_created = True
                with patch.object(cutover, "git_binding", return_value=binding):
                    with self.assertRaisesRegex(
                        cutover.ProductionCutoverError,
                        "BLOCKED_QUEUE_DEPLOY_AUTHORITY",
                    ):
                        cutover.verify_deploy_authority(
                            manifest,
                            authority_path,
                            authority_digest,
                            expected_artifact_dir=artifacts,
                        )
                with self.assertRaisesRegex(
                    cutover.ProductionCutoverError,
                    "BLOCKED_RELEASE_LOCK_OWNERSHIP",
                ):
                    run_lock.release()
                self.assertTrue(run_lock.path.exists())
            finally:
                source_lock.release()
                if run_lock.held:
                    with self.assertRaises(cutover.ProductionCutoverError):
                        run_lock.release()
                if replacement_created:
                    run_lock.path.unlink(missing_ok=True)

    def test_executor_inventory_rejects_a_stray_second_production_bot(self):
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError, "EXECUTOR_INVENTORY_AMBIGUOUS"
        ):
            cutover.executor_inventory_from_observation(
                running_container_count=2,
                expected_container_name=True,
                process_count=2,
                host_process_count=2,
                iran_host_process_count=0,
                env={"TRADING_BOT_SERVICE": "bot", "SERVER_MODE": "foreign"},
                runtime_decision={
                    "mode": "queue-v1",
                    "legacy_workers_enabled": False,
                    "queue_worker_enabled": True,
                },
            )

        inventory = cutover.executor_inventory_from_observation(
            running_container_count=1,
            expected_container_name=True,
            process_count=1,
            host_process_count=1,
            iran_host_process_count=0,
            env={
                "TRADING_BOT_SERVICE": "bot",
                "SERVER_MODE": "foreign",
                "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
            },
            runtime_decision={
                "mode": "queue-v1",
                "legacy_workers_enabled": False,
                "queue_worker_enabled": True,
            },
        )
        self.assertEqual(inventory, {"count": 1, "owner": "queue-v1", "overlap": False})

    def test_runtime_enumeration_detects_an_unlabelled_bot_container(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations._docker = Mock(
            side_effect=(
                subprocess.CompletedProcess([], 0, stdout="app-id\nstray-id\n", stderr=""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout='["TRADING_BOT_SERVICE=app"]\t["uvicorn"]\t[]\tapp\n',
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout='[]\t["python","run_bot.py"]\t[]\t\n',
                    stderr="",
                ),
            )
        )
        self.assertEqual(
            operations._potential_bot_containers("foreign"), ["stray-id"]
        )

    def test_runtime_inventory_excludes_only_the_identity_bound_colocated_staging_bot(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        def observation(service: str):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    '["TRADING_BOT_SERVICE=bot","SERVER_MODE=foreign"]'
                    f"\ttrading_bot_staging\t{service}\n"
                ),
                stderr="",
            )

        operations._docker = Mock(return_value=observation("bot"))
        self.assertTrue(
            operations._is_allowed_colocated_staging_bot("foreign", "staging-id")
        )
        self.assertFalse(
            operations._is_allowed_colocated_staging_bot("iran", "staging-id")
        )
        operations._docker = Mock(return_value=observation("bot_executor"))
        self.assertTrue(
            operations._is_allowed_colocated_staging_bot(
                "foreign", "staging-executor-id"
            )
        )
        operations._docker = Mock(return_value=observation("unknown-bot"))
        self.assertFalse(
            operations._is_allowed_colocated_staging_bot("foreign", "unknown-id")
        )

    def test_host_inventory_subtracts_the_verified_colocated_staging_process(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations._host = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="python run_bot.py\npython run_bot.py\n",
                stderr="",
            )
        )
        operations._container_bot_process_count = Mock(return_value=1)
        self.assertEqual(
            operations._host_bot_process_count(
                "foreign", excluded_containers=("staging-id",)
            ),
            1,
        )

    def test_official_deploy_marks_inherited_source_lock_only_with_authority(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations.manifest = Path("/secure/online.env")
        operations.release_root = cutover.REPO_ROOT
        operations._open_release_deploy_script = Mock(
            side_effect=lambda: (
                os.open(
                    cutover.REPO_ROOT
                    / "scripts/production_deploy_online.sh",
                    os.O_RDONLY,
                ),
                cutover._sha256(
                    cutover.REPO_ROOT
                    / "scripts/production_deploy_online.sh"
                ),
            )
        )
        operations._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        with patch.dict(
            os.environ,
            {
                "COMPOSE_PROJECT_NAME": "polluted-staging-project",
                "IRAN_HOST": "polluted-host.invalid",
                "IRAN_SSH_PORT": "1",
                "PRODUCTION_RELEASE_RELAY_RECOVERY_MARKER": "/polluted/marker",
            },
            clear=False,
        ):
            operations.deploy_official()
        plain_env = operations._run.call_args.kwargs["env"]
        self.assertNotIn("PRODUCTION_SOURCE_LOCK_INHERITED_CONFIRM", plain_env)
        for forbidden in (
            "COMPOSE_PROJECT_NAME",
            "IRAN_HOST",
            "IRAN_SSH_PORT",
            "PRODUCTION_RELEASE_RELAY_RECOVERY_MARKER",
        ):
            self.assertNotIn(forbidden, plain_env)
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError,
            "BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED",
        ):
            operations.deploy_official(Path("/secure/authority.json"), "a" * 64)

    def test_release_deploy_script_is_fd_bound_and_fd_bootstrap_preserves_root(self):
        deploy_script = (
            cutover.REPO_ROOT / "scripts/production_deploy_online.sh"
        )
        descriptor = os.open(deploy_script, os.O_RDONLY)
        try:
            environment = {
                **os.environ,
                "PRODUCTION_RELEASE_ROOT_FD_EXEC_CONFIRM": (
                    "verified-release-root-fd-exec"
                ),
                "PRODUCTION_RELEASE_ROOT_FD_EXEC": str(cutover.REPO_ROOT),
                "PRODUCTION_RELEASE_ROOT_FD_EXEC_SHA256": hashlib.sha256(
                    str(cutover.REPO_ROOT).encode("utf-8")
                ).hexdigest(),
            }
            completed = subprocess.run(
                ["bash", f"/proc/self/fd/{descriptor}", "help"],
                cwd=Path("/tmp"),
                env=environment,
                pass_fds=(descriptor,),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("private-primary-release", completed.stdout)
            environment["PRODUCTION_RELEASE_ROOT_FD_EXEC_SHA256"] = "0" * 64
            refused = subprocess.run(
                ["bash", f"/proc/self/fd/{descriptor}", "help"],
                cwd=Path("/tmp"),
                env=environment,
                pass_fds=(descriptor,),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
        finally:
            os.close(descriptor)

        with tempfile.TemporaryDirectory() as tmpdir:
            release_root = Path(tmpdir) / "release"
            release_script = release_root / "scripts/production_deploy_online.sh"
            release_script.parent.mkdir(parents=True)
            release_script.write_bytes(deploy_script.read_bytes())
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations._control_release_mode = True
            operations.release_root = release_root
            held, expected_digest = operations._open_release_deploy_script()
            try:
                replacement = release_root / "scripts/replacement.sh"
                replacement.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
                os.replace(replacement, release_script)
                self.assertEqual(
                    hashlib.sha256(
                        os.pread(held, 20_000_000, 0)
                    ).hexdigest(),
                    expected_digest,
                )
                self.assertNotEqual(
                    hashlib.sha256(release_script.read_bytes()).hexdigest(),
                    expected_digest,
                )
            finally:
                os.close(held)

    def test_official_deploy_forwards_exact_private_primary_attestation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "online.env"
            manifest.write_text("SAFE=1\n", encoding="utf-8")
            manifest.chmod(0o600)
            receipt = root / "private-primary-preparation.json"
            receipt.write_text('{"status":"PASS"}\n', encoding="utf-8")
            receipt.chmod(0o600)
            attestation = cutover.bind_private_primary_deploy_attestation(
                manifest,
                manifest_sha256=cutover._sha256(manifest),
                receipt_path=receipt,
                receipt_sha256=cutover._sha256(receipt),
            )
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.manifest = manifest
            operations.release_root = cutover.REPO_ROOT
            deploy_script_digest = cutover._sha256(
                cutover.REPO_ROOT / "scripts/production_deploy_online.sh"
            )
            operations._open_release_deploy_script = Mock(
                side_effect=lambda: (
                    os.open(
                        cutover.REPO_ROOT
                        / "scripts/production_deploy_online.sh",
                        os.O_RDONLY,
                    ),
                    deploy_script_digest,
                )
            )
            supervisor_digest = cutover._sha256(
                cutover.FENCED_DEPLOY_SUPERVISOR
            )
            operations._open_control_supervisor = Mock(
                side_effect=lambda: (
                    os.open(cutover.FENCED_DEPLOY_SUPERVISOR, os.O_RDONLY),
                    supervisor_digest,
                    cutover.FENCED_DEPLOY_SUPERVISOR,
                )
            )
            operations._run = Mock(
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout="", stderr=""
                )
            )
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_PRIVATE_PRIMARY_ATTESTATION",
            ):
                operations.deploy_official(
                    private_primary_attestation=attestation
                )
            first_lock = root / "run.lock"
            second_lock = root / "source.lock"
            first_lock.write_text("lock\n", encoding="utf-8")
            second_lock.write_text("lock\n", encoding="utf-8")
            first_lock.chmod(0o600)
            second_lock.chmod(0o600)
            descriptors = (os.open(first_lock, os.O_RDWR), os.open(second_lock, os.O_RDWR))
            fence = root / "fence.json"
            fence.write_text("{}\n", encoding="utf-8")
            fence.chmod(0o600)
            try:
                with (
                    patch.object(
                        cutover,
                        "_prepare_deploy_child_fence",
                        return_value=(fence, "f" * 64),
                    ),
                    patch.object(
                        cutover,
                        "_read_deploy_child_fence",
                        return_value={
                            "status": "SUCCEEDED",
                            "returncode": 0,
                            "deploy_script_sha256": deploy_script_digest,
                            "product_readiness": {
                                "consumer_count": 3,
                                "required_source_input_trace_count": 9,
                            },
                        },
                    ),
                ):
                    operations.deploy_official(
                        root / "authority.json",
                        "a" * 64,
                        private_primary_attestation=attestation,
                        inherited_lock_descriptors=descriptors,
                    )
            finally:
                os.close(descriptors[0])
                os.close(descriptors[1])
            supervisor_argv = operations._run.call_args.args[0]
            self.assertEqual(supervisor_argv[0], sys.executable)
            self.assertRegex(supervisor_argv[1], r"^/proc/self/fd/[0-9]+$")
            separator = supervisor_argv.index("--")
            argv = supervisor_argv[separator + 1 :]
            self.assertEqual(
                argv[0],
                "bash",
            )
            self.assertRegex(argv[1], r"^/proc/self/fd/[0-9]+$")
            self.assertEqual(
                argv[2:],
                [
                    "--manifest",
                    str(manifest),
                    "--private-primary-manifest-sha256",
                    attestation.manifest_sha256,
                    "--private-primary-manifest-receipt",
                    str(receipt),
                    "--private-primary-manifest-receipt-sha256",
                    attestation.receipt_sha256,
                    "release",
                ],
            )
            child_env = operations._run.call_args.kwargs["env"]
            self.assertNotIn(receipt.read_text(encoding="utf-8"), child_env.values())

    def test_fenced_supervisor_is_manifest_digest_and_fd_bound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            supervisor = root / "scripts/run_fenced_production_deploy.py"
            supervisor.parent.mkdir(parents=True)
            supervisor.write_text("print('safe')\n", encoding="utf-8")
            supervisor.chmod(0o600)
            expected = hashlib.sha256(supervisor.read_bytes()).hexdigest()
            manifest = root / "control-payload.sha256"
            manifest.write_text(
                f"{expected}  ./scripts/run_fenced_production_deploy.py\n",
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.release_root = root
            operations._control_release_mode = True
            with (
                patch.object(cutover, "FENCED_DEPLOY_SUPERVISOR", supervisor),
                patch.object(cutover, "CONTROL_PAYLOAD_MANIFEST", manifest),
            ):
                descriptor, observed, observed_path = (
                    operations._open_control_supervisor()
                )
                try:
                    replacement = supervisor.with_name("replacement.py")
                    replacement.write_text(
                        "raise RuntimeError('hostile')\n", encoding="utf-8"
                    )
                    replacement.chmod(0o600)
                    os.replace(replacement, supervisor)
                    self.assertEqual(observed, expected)
                    self.assertEqual(observed_path, supervisor)
                    self.assertEqual(
                        hashlib.sha256(
                            os.pread(descriptor, 1_000_000, 0)
                        ).hexdigest(),
                        expected,
                    )
                    self.assertNotEqual(
                        hashlib.sha256(supervisor.read_bytes()).hexdigest(),
                        expected,
                    )
                finally:
                    os.close(descriptor)

    def test_fenced_supervisor_normal_checkout_uses_exact_approved_git_blob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            release = Path(tmpdir) / "release"
            supervisor = release / "scripts/run_fenced_production_deploy.py"
            supervisor.parent.mkdir(parents=True)
            supervisor.write_text("print('approved')\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(release)], check=True)
            subprocess.run(
                ["git", "-C", str(release), "config", "user.name", "Test"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(release), "config", "user.email",
                    "test@example.invalid",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(release), "add", "scripts"], check=True
            )
            subprocess.run(
                ["git", "-C", str(release), "commit", "-qm", "approved"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(release), "branch", "-M", "main"],
                check=True,
            )
            head = subprocess.check_output(
                ["git", "-C", str(release), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(
                [
                    "git", "-C", str(release), "update-ref",
                    "refs/remotes/origin/main", head,
                ],
                check=True,
            )
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.release_root = release
            operations._control_release_mode = False
            absent_manifest = release / "missing-control-payload.sha256"
            with (
                patch.object(
                    cutover, "CONTROL_PAYLOAD_MANIFEST", absent_manifest
                ),
                patch.dict(
                    os.environ,
                    {"PATH": str(release), "GIT_DIR": "/hostile"},
                    clear=False,
                ),
            ):
                descriptor, observed, observed_path = (
                    operations._open_control_supervisor()
                )
                try:
                    self.assertEqual(observed_path, supervisor)
                    self.assertEqual(
                        observed,
                        hashlib.sha256(supervisor.read_bytes()).hexdigest(),
                    )
                finally:
                    os.close(descriptor)
                (release / "untracked").write_text("drift\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    cutover.ProductionCutoverError,
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR",
                ):
                    operations._open_control_supervisor()

    def test_standalone_queue_uses_git_even_when_disk_manifest_is_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            release = Path(tmpdir) / "release"
            supervisor = release / "scripts/run_fenced_production_deploy.py"
            supervisor.parent.mkdir(parents=True)
            approved = "print('approved-git-blob')\n"
            supervisor.write_text(approved, encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(release)], check=True)
            subprocess.run(
                ["git", "-C", str(release), "config", "user.name", "Test"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(release), "config", "user.email",
                    "test@example.invalid",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(release), "add", "scripts"], check=True
            )
            subprocess.run(
                ["git", "-C", str(release), "commit", "-qm", "approved"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(release), "branch", "-M", "main"],
                check=True,
            )
            head = subprocess.check_output(
                ["git", "-C", str(release), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(
                [
                    "git", "-C", str(release), "update-ref",
                    "refs/remotes/origin/main", head,
                ],
                check=True,
            )
            hostile_manifest = Path(tmpdir) / "control-payload.sha256"
            hostile = "0" * 64 + "  ./scripts/run_fenced_production_deploy.py\n"
            hostile_manifest.write_text(hostile, encoding="utf-8")
            hostile_manifest.chmod(0o600)
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.release_root = release
            operations._control_release_mode = False
            hostile_bin = Path(tmpdir) / "hostile-bin"
            hostile_bin.mkdir()
            (hostile_bin / "git").write_text("#!/bin/sh\nexit 41\n", encoding="utf-8")
            (hostile_bin / "git").chmod(0o700)
            with (
                patch.object(cutover, "CONTROL_PAYLOAD_MANIFEST", hostile_manifest),
                patch.dict(
                    os.environ,
                    {
                        "PATH": str(hostile_bin),
                        "GIT_DIR": "/hostile",
                        "GIT_WORK_TREE": "/hostile-tree",
                    },
                    clear=False,
                ),
            ):
                descriptor, observed, observed_path = (
                    operations._open_control_supervisor()
                )
                try:
                    self.assertEqual(observed_path, supervisor)
                    self.assertEqual(
                        observed,
                        hashlib.sha256(approved.encode("utf-8")).hexdigest(),
                    )
                    self.assertNotEqual(observed, "0" * 64)
                finally:
                    os.close(descriptor)

    def test_control_release_supervisor_never_falls_back_when_manifest_missing(self):
        operations = cutover.ProductionOperations.__new__(
            cutover.ProductionOperations
        )
        operations.release_root = Path("/definitely/different/release")
        operations._control_release_mode = True
        with patch.object(
            cutover,
            "CONTROL_PAYLOAD_MANIFEST",
            Path("/definitely/missing/control-payload.sha256"),
        ):
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR",
            ):
                operations._open_control_supervisor()
    def test_fenced_deploy_survives_controller_sigkill_without_overlap(self):
        """A real SIGKILL must leave the exact inherited locks with the child."""

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_lock = root / "run.lock"
            source_lock = root / "source.lock"
            journal = root / "deploy-fence.json"
            ready = root / "controller-ready"
            started = root / "worker-started"
            finished = root / "worker-finished"
            active = root / "active-count"
            maximum = root / "maximum-count"
            counter_lock = root / "counter.lock"
            worker_script = root / "worker.py"
            deploy_script = root / "scripts/production_deploy_online.sh"
            deploy_script.parent.mkdir()
            deploy_script.write_text(
                "#!/bin/bash\nset -euo pipefail\nexec \"$@\"\n",
                encoding="utf-8",
            )
            deploy_script.chmod(0o700)
            for path in (run_lock, source_lock, counter_lock):
                path.write_text("lock\n", encoding="utf-8")
                path.chmod(0o600)
            active.write_text("0", encoding="ascii")
            maximum.write_text("0", encoding="ascii")
            worker_script.write_text(
                "\n".join(
                    (
                        "import fcntl, pathlib, sys, time",
                        "active, maximum, guard, started, finished = map(pathlib.Path, sys.argv[1:])",
                        "with guard.open('r+') as lock:",
                        "    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)",
                        "    current = int(active.read_text()) + 1",
                        "    active.write_text(str(current))",
                        "    maximum.write_text(str(max(current, int(maximum.read_text()))))",
                        "started.write_text('started')",
                        "time.sleep(1.2)",
                        "with guard.open('r+') as lock:",
                        "    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)",
                        "    active.write_text(str(int(active.read_text()) - 1))",
                        "finished.write_text('finished')",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(worker_script),
                str(active),
                str(maximum),
                str(counter_lock),
                str(started),
                str(finished),
            ]
            controller_code = "\n".join(
                (
                    "import fcntl, hashlib, json, os, pathlib, subprocess, sys, time",
                    "run_path, source_path, journal_path, ready_path = map(pathlib.Path, sys.argv[1:5])",
                    "supervisor = pathlib.Path(sys.argv[5])",
                    "deploy_path = pathlib.Path(sys.argv[6])",
                    "worker = sys.argv[7:]",
                    "descriptors = [os.open(run_path, os.O_RDWR), os.open(source_path, os.O_RDWR), os.open(deploy_path, os.O_RDONLY)]",
                    "[fcntl.flock(fd, fcntl.LOCK_EX) for fd in descriptors[:2]]",
                    "command = ['bash', f'/proc/self/fd/{descriptors[2]}', *worker]",
                    "def binding(fd):",
                    "    info = os.fstat(fd)",
                    "    path = pathlib.Path(os.readlink(f'/proc/self/fd/{fd}')).resolve(strict=True)",
                    "    return {'device': info.st_dev, 'inode': info.st_ino, 'path_sha256': hashlib.sha256(str(path).encode()).hexdigest()}",
                    "command_digest = hashlib.sha256(json.dumps(command, separators=(',', ':')).encode()).hexdigest()",
                    "deploy_digest = hashlib.sha256(deploy_path.read_bytes()).hexdigest()",
                    "payload = {'schema':'production_deploy_child_fence/1.0','status':'PREPARED','command_sha256':command_digest,'deploy_script_sha256':deploy_digest,'run_lock':binding(descriptors[0]),'source_lock':binding(descriptors[1]),'private_primary_required':False,'authority_sha256':'a'*64,'journal_file':'phase.json','source_sha256':'b'*64,'manifest_sha256':'c'*64,'secrets_disclosed':False}",
                    "body = (json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\\n').encode()",
                    "journal_path.write_bytes(body); journal_path.chmod(0o600)",
                    "digest = hashlib.sha256(body).hexdigest()",
                    "child = subprocess.Popen([sys.executable, str(supervisor), '--journal', str(journal_path), '--expected-journal-sha256', digest, '--run-lock-fd', str(descriptors[0]), '--source-lock-fd', str(descriptors[1]), '--deploy-script-fd', str(descriptors[2]), '--expected-deploy-script-sha256', deploy_digest, '--cwd', str(journal_path.parent), '--', *command], pass_fds=tuple(descriptors), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)",
                    "ready_path.write_text(str(child.pid))",
                    "time.sleep(60)",
                )
            )
            controller = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    controller_code,
                    str(run_lock),
                    str(source_lock),
                    str(journal),
                    str(ready),
                    str(cutover.FENCED_DEPLOY_SUPERVISOR),
                    str(deploy_script),
                    *command,
                ],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            supervisor_pid: int | None = None
            try:
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline and not started.exists():
                    if controller.poll() is not None:
                        self.fail(
                            "controller exited before worker start: "
                            + str(controller.stderr.read() if controller.stderr else "")
                        )
                    time.sleep(0.02)
                self.assertTrue(started.exists(), "fenced deploy did not start")
                supervisor_pid = int(ready.read_text(encoding="ascii"))

                os.kill(controller.pid, signal.SIGKILL)
                controller.wait(timeout=2.0)

                # A recovery contender cannot acquire either exact lock and
                # therefore cannot launch a second deploy while the inherited
                # child is alive.
                probe = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import fcntl,os,sys; "
                            "fds=[os.open(p,os.O_RDWR) for p in sys.argv[1:]]; "
                            "[fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB) for fd in fds]"
                        ),
                        str(run_lock),
                        str(source_lock),
                    ],
                    cwd=root,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(probe.returncode, 0)

                deadline = time.monotonic() + 8.0
                terminal: dict[str, object] = {}
                while time.monotonic() < deadline:
                    try:
                        terminal = json.loads(journal.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        terminal = {}
                    if terminal.get("status") == "SUCCEEDED":
                        break
                    time.sleep(0.02)
                self.assertEqual(terminal.get("status"), "SUCCEEDED")
                self.assertTrue(finished.exists())
                self.assertEqual(maximum.read_text(encoding="ascii"), "1")
                self.assertEqual(active.read_text(encoding="ascii"), "0")

                deadline = time.monotonic() + 3.0
                acquired_after_terminal = False
                while time.monotonic() < deadline:
                    descriptors = [
                        os.open(run_lock, os.O_RDWR),
                        os.open(source_lock, os.O_RDWR),
                    ]
                    try:
                        for descriptor in descriptors:
                            fcntl.flock(
                                descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                            )
                    except BlockingIOError:
                        time.sleep(0.02)
                    else:
                        acquired_after_terminal = True
                        break
                    finally:
                        for descriptor in descriptors:
                            os.close(descriptor)
                self.assertTrue(acquired_after_terminal)
            finally:
                if controller.poll() is None:
                    controller.kill()
                    controller.wait(timeout=2.0)
                if controller.stderr is not None:
                    controller.stderr.close()
                if supervisor_pid is not None:
                    try:
                        os.kill(supervisor_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_operations_remote_commands_are_strictly_key_only(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations.settings = {
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_TARGET": "root@production.invalid",
        }
        operations.ssh_key = Path("/secure/production-key")
        operations._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        operations._docker("iran", ["ps", "-q"])
        argv = operations._run.call_args.args[0]
        for option in (
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
        ):
            self.assertIn(option, argv)

    def test_private_primary_terminal_live_rechecks_are_value_free_and_exact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            local = root / "local"
            remote = Path("/srv/private-primary-remote")
            local.mkdir()
            snapshot = local / "latest-private-primary.json"
            snapshot.write_text('{"contract":"test"}\n', encoding="utf-8")
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.manifest_values = {
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": str(local),
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR": str(local),
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR": str(remote),
                "PRODUCTION_MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
            }
            inactive = [
                subprocess.CompletedProcess([], 3, stdout="", stderr="")
                for _ in range(6)
            ]
            disabled = [
                subprocess.CompletedProcess([], 1, stdout="", stderr="")
                for _ in range(3)
            ]
            remote_digest = subprocess.CompletedProcess(
                [],
                0,
                stdout=f"{digest}  {remote / 'latest-private-primary.json'}\n",
                stderr="",
            )
            operations._host = Mock(
                side_effect=[*inactive, *disabled, remote_digest]
            )
            operations._docker = Mock(
                side_effect=(
                    subprocess.CompletedProcess(
                        [], 0, stdout="receiver-container\n", stderr=""
                    ),
                    subprocess.CompletedProcess([], 0, stdout="0\n", stderr=""),
                )
            )

            self.assertEqual(
                operations.private_primary_legacy_inputs_off()[
                    "legacy_input_units_active"
                ],
                0,
            )
            self.assertEqual(
                operations.private_primary_snapshot_identity(
                    expected_digest=digest
                ),
                {
                    "status": "verified",
                    "snapshot_digest": digest,
                    "consumer_artifact_count": 3,
                },
            )
            self.assertEqual(
                operations.private_primary_publication_outbox_zero(),
                {"status": "verified", "open_outbox": 0},
            )

    def test_private_primary_terminal_recheck_rejects_legacy_restart(self):
        operations = cutover.ProductionOperations.__new__(
            cutover.ProductionOperations
        )
        operations._host = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="active\n", stderr=""
            )
        )
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError,
            "PRIVATE_PRIMARY_LEGACY_INPUT_ACTIVE",
        ):
            operations.private_primary_legacy_inputs_off()

    def test_product_runtime_mode_rechecks_all_three_live_consumers(self):
        operations = cutover.ProductionOperations.__new__(
            cutover.ProductionOperations
        )
        operations._running = Mock(return_value=True)
        operations._container_env = Mock(
            return_value={"PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "LEGACY"}
        )
        self.assertEqual(
            operations.product_estimator_runtime_mode("LEGACY"),
            {
                "status": "verified",
                "mode": "LEGACY",
                "consumer_count": 3,
                "values_disclosed": False,
            },
        )
        self.assertEqual(
            operations._container_env.call_args_list,
            [
                call("foreign", cutover.FOREIGN_CONTAINERS["app"]),
                call("foreign", cutover.FOREIGN_CONTAINERS["bot"]),
                call("iran", cutover.IRAN_CONTAINERS["app"]),
            ],
        )

    def test_product_runtime_mode_rejects_one_premature_private_consumer(self):
        operations = cutover.ProductionOperations.__new__(
            cutover.ProductionOperations
        )
        operations._running = Mock(return_value=True)
        operations._container_env = Mock(
            side_effect=(
                {"PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "LEGACY"},
                {"PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "PRIVATE_PRIMARY"},
            )
        )
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError, "BLOCKED_PRODUCT_RUNTIME_MODE"
        ):
            operations.product_estimator_runtime_mode("LEGACY")

    def test_private_primary_snapshot_recheck_rejects_post_probe_symlink_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            local = root / "local"
            local.mkdir()
            snapshot = local / "latest-private-primary.json"
            snapshot.write_text('{"contract":"expected"}\n', encoding="utf-8")
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            replacement = root / "replacement.json"
            replacement.write_text('{"contract":"expected"}\n', encoding="utf-8")
            remote = Path("/srv/private-primary-remote")
            operations = cutover.ProductionOperations.__new__(
                cutover.ProductionOperations
            )
            operations.manifest_values = {
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": str(local),
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR": str(local),
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR": str(remote),
            }

            def replace_during_remote_probe(_role, _argv):
                snapshot.unlink()
                snapshot.symlink_to(replacement)
                return subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=(
                        f"{digest}  "
                        f"{remote / 'latest-private-primary.json'}\n"
                    ),
                    stderr="",
                )

            operations._host = Mock(side_effect=replace_during_remote_probe)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID",
            ):
                operations.private_primary_snapshot_identity(
                    expected_digest=digest
                )

    def test_run_rm_migration_role_is_read_from_compose_without_a_container(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations.manifest_values = {"LOCAL_PROJECT_DIR": str(cutover.REPO_ROOT)}
        operations._host = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    {
                        "services": {
                            "migration": {
                                "environment": {
                                    "SERVER_MODE": "foreign",
                                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "producer-only",
                                }
                            }
                        }
                    }
                ),
                stderr="",
            )
        )
        env = operations._compose_service_env("foreign", "migration")
        self.assertEqual(env["SERVER_MODE"], "foreign")
        self.assertEqual(
            env["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "producer-only"
        )
        self.assertEqual(operations._host.call_args.args[1][:2], ["bash", "-lc"])
        self.assertIn("config --format json", operations._host.call_args.args[1][2])

    def test_queue_runtime_contract_accepts_producer_routing_on_non_bot_roles(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        api_env = {
            **cutover.api_process_contract().required,
            "SERVER_MODE": "foreign",
            **{key: "" for key in cutover.API_FORBIDDEN_TOKEN_KEYS},
        }
        iran_api_env = {**api_env, "SERVER_MODE": "iran"}
        source_values = {
            **cutover.bot_process_contract().required,
            "BOT_USERNAME": "production_central_bot",
            "CHANNEL_ID": "-100999",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100999",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "1000",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID": "1000",
            "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED": "false",
        }
        for key in cutover.TOKEN_KEYS:
            source_values[key] = f"secret-{key.lower()}"
        for index in range(1, 6):
            prefix = f"TELEGRAM_PUBLISHER_{index}"
            source_values.update(
                {
                    f"{prefix}_ENABLED": "true",
                    f"{prefix}_EXPECTED_BOT_ID": str(1000 + index),
                    f"{prefix}_EXPECTED_USERNAME": f"publisher_{index}_bot",
                }
            )
        bot_env = dict(source_values)

        operations._container_env = Mock(
            side_effect=lambda role, container: (
                bot_env
                if role == "foreign" and container == cutover.FOREIGN_CONTAINERS["bot"]
                else (api_env if role == "foreign" else iran_api_env)
            )
        )
        operations._compose_service_env = Mock(
            side_effect=lambda role, _service: api_env if role == "foreign" else iran_api_env
        )
        operations._docker = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )

        report = operations.runtime_contract(source_values, expected_owner="queue-v1")

        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["owner"], "queue-v1")

    def test_queue_runtime_contract_rejects_disabled_producer_routing(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        invalid_api_env = {
            **cutover.api_process_contract().required,
            "SERVER_MODE": "foreign",
            "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
            **{key: "" for key in cutover.API_FORBIDDEN_TOKEN_KEYS},
        }
        iran_invalid_api_env = {**invalid_api_env, "SERVER_MODE": "iran"}
        source_values = {
            **cutover.bot_process_contract().required,
            "BOT_USERNAME": "production_central_bot",
            "CHANNEL_ID": "-100999",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100999",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "1000",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID": "1000",
            "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED": "false",
        }
        for key in cutover.TOKEN_KEYS:
            source_values[key] = f"secret-{key.lower()}"
        for index in range(1, 6):
            prefix = f"TELEGRAM_PUBLISHER_{index}"
            source_values.update(
                {
                    f"{prefix}_ENABLED": "true",
                    f"{prefix}_EXPECTED_BOT_ID": str(1000 + index),
                    f"{prefix}_EXPECTED_USERNAME": f"publisher_{index}_bot",
                }
            )
        operations._container_env = Mock(
            side_effect=lambda role, container: (
                source_values
                if role == "foreign" and container == cutover.FOREIGN_CONTAINERS["bot"]
                else (
                    invalid_api_env
                    if role == "foreign"
                    else iran_invalid_api_env
                )
            )
        )
        operations._compose_service_env = Mock(
            side_effect=lambda role, _service: (
                invalid_api_env if role == "foreign" else iran_invalid_api_env
            )
        )
        operations._docker = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )

        with self.assertRaisesRegex(
            cutover.ProductionCutoverError, "POST_DEPLOY_ROLE_CONTRACT_FAILED"
        ):
            operations.runtime_contract(source_values, expected_owner="queue-v1")

    def test_contained_runner_stops_a_timed_out_descendant_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child_pid_file = root / "child.pid"
            program = (
                "import pathlib,subprocess,time; "
                "p=subprocess.Popen(['sleep','60']); "
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(60)"
            )
            result = cutover._run_contained_process(
                [sys.executable, "-c", program], cwd=root, timeout=0.5
            )
            self.assertEqual(result.returncode, 124)
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and Path(f"/proc/{child_pid}").exists():
                stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
                if stat_text.split()[2] == "Z":
                    break
                time.sleep(0.05)
            if Path(f"/proc/{child_pid}/stat").exists():
                self.assertEqual(
                    Path(f"/proc/{child_pid}/stat")
                    .read_text(encoding="utf-8")
                    .split()[2],
                    "Z",
                )

    def test_contained_cleanup_kills_pipe_holder_after_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child_pid_path = root / "pipe-holder.pid"
            child_ready_path = root / "pipe-holder.ready"
            child_code = (
                "import os,pathlib,signal,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "pathlib.Path(sys.argv[2]).write_text('ready'); "
                "time.sleep(60)"
            )
            leader_code = (
                "import pathlib,subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
                "ready=pathlib.Path(sys.argv[3]); "
                "deadline=time.monotonic()+5; "
                "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    leader_code,
                    child_code,
                    str(child_pid_path),
                    str(child_ready_path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            group_id = process.pid
            with self.assertRaises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.2)
            self.assertIsNotNone(process.poll(), "group leader must have exited")
            cutover._terminate_process_group(
                process,
                process_group_id=group_id,
                grace_seconds=0.1,
                kill_seconds=1.0,
            )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                stat_path = Path(f"/proc/{child_pid}/stat")
                if stat_path.exists() and stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                    break
                time.sleep(0.05)
            else:
                self.fail("leader-exited pipe holder survived bounded group cleanup")

    def test_contained_timeout_kills_term_ignoring_descendant_with_closed_pipes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child_pid_path = root / "closed-pipe-child.pid"
            escaped_marker = root / "closed-pipe-child.escaped"
            child_code = "\n".join(
                (
                    "import os, pathlib, signal, sys, time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                    "time.sleep(0.75)",
                    "pathlib.Path(sys.argv[2]).write_text('escaped')",
                    "time.sleep(60)",
                )
            )
            leader_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "time.sleep(60)"
            )
            original_cleanup = cutover._terminate_process_group

            def fast_cleanup(process, *, process_group_id=None):
                return original_cleanup(
                    process,
                    process_group_id=process_group_id,
                    grace_seconds=0.1,
                    kill_seconds=1.0,
                )

            child_pid = None
            try:
                with patch.object(
                    cutover,
                    "_terminate_process_group",
                    side_effect=fast_cleanup,
                ):
                    result = cutover._run_contained_process(
                        [
                            sys.executable,
                            "-c",
                            leader_code,
                            child_code,
                            str(child_pid_path),
                            str(escaped_marker),
                        ],
                        cwd=root,
                        timeout=0.2,
                    )
                self.assertEqual(result.returncode, 124)
                child_pid, child_group = map(
                    int, child_pid_path.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(cutover._process_group_has_live_members(child_group))
                time.sleep(0.85)
                self.assertFalse(escaped_marker.exists())
            finally:
                if child_pid is None and child_pid_path.exists():
                    child_pid = int(
                        child_pid_path.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_process_group_probe_distinguishes_zombie_from_live_member(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "raise SystemExit(0)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            stat_path = Path(f"/proc/{process.pid}/stat")
            deadline = time.monotonic() + 2.0
            state = ""
            while time.monotonic() < deadline:
                if stat_path.exists():
                    state = stat_path.read_text(encoding="utf-8").split()[2]
                    if state == "Z":
                        break
                time.sleep(0.01)
            self.assertEqual(state, "Z", "child did not reach deterministic zombie state")
            self.assertTrue(cutover._process_group_exists(process.pid))
            self.assertFalse(cutover._process_group_has_live_members(process.pid))
        finally:
            process.wait(timeout=2.0)

    def test_contained_normal_return_fails_closed_on_detached_descendant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child_identity = root / "normal-return-child.pid"
            escaped_marker = root / "normal-return-child.escaped"
            child_code = "\n".join(
                (
                    "import os, pathlib, signal, sys, time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                    "time.sleep(0.75)",
                    "pathlib.Path(sys.argv[2]).write_text('escaped')",
                )
            )
            leader_code = "\n".join(
                (
                    "import pathlib, subprocess, sys, time",
                    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                    "ready = pathlib.Path(sys.argv[2])",
                    "deadline = time.monotonic() + 5",
                    "while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)",
                    "raise SystemExit(0 if ready.exists() else 91)",
                )
            )
            child_pid = None
            try:
                result = cutover._run_contained_process(
                    [
                        sys.executable,
                        "-c",
                        leader_code,
                        child_code,
                        str(child_identity),
                        str(escaped_marker),
                    ],
                    cwd=root,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 125)
                child_pid, child_group = map(
                    int, child_identity.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(cutover._process_group_has_live_members(child_group))
                time.sleep(0.85)
                self.assertFalse(escaped_marker.exists())
            finally:
                if child_pid is None and child_identity.exists():
                    child_pid = int(
                        child_identity.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_contained_cleanup_never_uses_unbounded_communicate(self):
        process = Mock()
        process.pid = 5252
        process.stdout = Mock()
        process.stderr = Mock()
        process.communicate.side_effect = (
            subprocess.TimeoutExpired(["cutover"], 0.01),
            subprocess.TimeoutExpired(["cutover"], 0.01),
        )
        with patch.object(cutover.os, "killpg") as killpg, self.assertRaisesRegex(
            cutover.ProductionCutoverError, "CHILD_PROCESS_GROUP_NOT_STOPPED"
        ):
            cutover._terminate_process_group(
                process,
                process_group_id=5252,
                grace_seconds=0.01,
                kill_seconds=0.01,
            )
        signal_calls = [
            call
            for call in killpg.call_args_list
            if call.args[1] in {signal.SIGTERM, signal.SIGKILL}
        ]
        self.assertEqual(
            signal_calls,
            [
                unittest.mock.call(5252, signal.SIGTERM),
                unittest.mock.call(5252, signal.SIGKILL),
            ],
        )
        self.assertTrue(any(call.args[1] == 0 for call in killpg.call_args_list))
        self.assertEqual(process.communicate.call_count, 2)
        self.assertTrue(
            all("timeout" in call.kwargs for call in process.communicate.call_args_list)
        )
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_signal_during_partial_quiesce_resumes_already_stopped_producer(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations._running = Mock(return_value=True)
        operations._docker = Mock(
            side_effect=(
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                KeyboardInterrupt("simulated signal"),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            )
        )
        with self.assertRaises(KeyboardInterrupt):
            operations.stop_producers()
        self.assertEqual(operations._docker.call_count, 5)
        self.assertEqual(
            operations._docker.call_args_list[-1].args[1],
            ["start", cutover.FOREIGN_CONTAINERS["app"]],
        )

    def test_active_run_rm_migration_blocks_before_any_producer_stop(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations._docker = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="migration-container-id\n", stderr=""
            )
        )
        operations._running = Mock(
            side_effect=AssertionError("named producers must not be touched")
        )
        with self.assertRaisesRegex(
            cutover.ProductionCutoverError, "BLOCKED_ACTIVE_MIGRATION_PRODUCER"
        ):
            operations.stop_producers()
        operations._running.assert_not_called()

    def test_receipt_writer_rejects_permissive_and_symlink_artifact_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            permissive = root / "permissive"
            permissive.mkdir(mode=0o755)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_SECURE_ARTIFACT_DIRECTORY"
            ):
                cutover._write_secure_json(permissive, "receipt", {"status": "safe"})

            secure = root / "secure"
            secure.mkdir(mode=0o700)
            alias = root / "alias"
            alias.symlink_to(secure, target_is_directory=True)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_SECURE_ARTIFACT_DIRECTORY"
            ):
                cutover._write_secure_json(alias, "receipt", {"status": "safe"})

    def test_exclusive_cutover_lock_rejects_a_concurrent_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            first = cutover.ExclusiveRunLock(secure)
            second = cutover.ExclusiveRunLock(secure)
            first.acquire()
            try:
                with self.assertRaisesRegex(
                    cutover.ProductionCutoverError,
                    "BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER",
                ):
                    second.acquire()
            finally:
                first.release()

    def test_exact_pid_start_identity_allows_only_proven_stale_lock_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            abandoned = cutover.ExclusiveRunLock(secure)
            abandoned.acquire()
            self.abandon_lock_as_dead_process(abandoned)

            retry = cutover.ExclusiveRunLock(secure)
            retry.acquire()
            try:
                payload = json.loads(retry.path.read_text(encoding="utf-8"))
                self.assertEqual(payload["owner_pid"], os.getpid())
                self.assertNotEqual(payload["owner_start_ticks"], "0")
            finally:
                retry.release()

    def test_terminal_receipt_intent_recovers_crash_without_orphan_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            lock = cutover.ExclusiveRunLock(secure)
            lock.acquire()
            journal = cutover.PhaseJournal(
                secure,
                command="redeploy",
                source_sha256="1" * 64,
                git_head="2" * 40,
                run_lock=lock,
            )
            receipt = {
                "environment": "production",
                "status": "redeployed",
                "secrets_disclosed": False,
            }
            original_atomic_write = cutover._atomic_write

            def crash_before_receipt_publish(path, body):
                if path.name.startswith("receipt-crash-"):
                    raise KeyboardInterrupt("simulated kill boundary")
                return original_atomic_write(path, body)

            with patch.object(
                cutover, "_atomic_write", side_effect=crash_before_receipt_publish
            ), self.assertRaises(KeyboardInterrupt):
                cutover._commit_terminal_receipt(
                    journal,
                    secure,
                    prefix="receipt-crash",
                    receipt=receipt,
                    terminal_status="redeployed",
                )
            pending = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual(pending["status"], "terminal_receipt_pending")
            self.assertRegex(pending["pending_receipt_sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse((secure / pending["pending_receipt_file"]).exists())
            lock.release()

            cutover._recover_terminal_receipt_journals(secure)
            terminal = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual(terminal["status"], "redeployed")
            receipt_path = secure / terminal["receipt_file"]
            self.assertEqual(cutover._sha256(receipt_path), terminal["receipt_sha256"])
            self.assertEqual(
                [row["status"] for row in terminal["state_history"]][-2:],
                ["terminal_receipt_pending", "redeployed"],
            )

    def test_interrupted_apply_rollback_and_redeploy_recover_pre_state(self):
        for command in ("apply", "rollback", "redeploy"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                source, _staging, manifest = self.fixture(root)
                secure = root / "secure"
                artifacts = root / "artifacts"
                secure.mkdir(mode=0o700)
                artifacts.mkdir(mode=0o700)
                legacy = source.read_bytes()
                if command in {"rollback", "redeploy"}:
                    queue = cutover.upsert_env_lines(
                        legacy.decode("utf-8"),
                        cutover._queue_source_updates(planner.parse_env_file(source)),
                    ).encode("utf-8")
                    cutover._atomic_write(source, queue)
                expected_digest = cutover._sha256(source)
                snapshot = (
                    cutover._create_recovery_source_snapshot(
                        source, secure, expected_sha256=expected_digest
                    )
                    if command != "redeploy"
                    else None
                )
                lock = cutover.ExclusiveRunLock(artifacts)
                lock.acquire()
                journal = cutover.PhaseJournal(
                    artifacts,
                    command=command,
                    source_sha256=expected_digest,
                    git_head=self.binding()["head"],
                    run_lock=lock,
                    recovery_source_backup=snapshot,
                )
                journal.update("deploy_authorizing")
                if command == "apply":
                    cutover._atomic_write(
                        source,
                        cutover.upsert_env_lines(
                            legacy.decode("utf-8"),
                            cutover._queue_source_updates(
                                planner.parse_env_file(source)
                            ),
                        ).encode("utf-8"),
                    )
                    live_owner = "queue-v1"
                elif command == "rollback":
                    cutover._atomic_write(source, legacy)
                    live_owner = "legacy"
                else:
                    live_owner = None
                self.abandon_lock_as_dead_process(lock)
                operations = FakeInterruptedRecoveryOperations(
                    manifest, owner=live_owner, artifact_dir=artifacts
                )

                result = cutover._recover_interrupted_phase(
                    command=command,
                    manifest=manifest,
                    artifact_dir=artifacts,
                    binding=self.binding(),
                    operations_factory=lambda _manifest: operations,
                    recovery_backup_dir=(secure if command != "redeploy" else None),
                )

                desired = "legacy" if command == "apply" else "queue-v1"
                self.assertEqual(result["status"], "interrupted_recovered")
                self.assertEqual(operations.owner, desired)
                self.assertEqual(
                    planner.source_profile(planner.parse_env_file(source)), desired
                )
                terminal = json.loads(journal.path.read_text(encoding="utf-8"))
                self.assertEqual(terminal["status"], "interrupted_recovered")
                if live_owner != desired:
                    self.assertEqual(
                        operations.observed_wal,
                        [
                            (
                                "stop_producers",
                                "interrupted_recovery_producers_quiescing",
                            ),
                            ("wait_for_drain", "interrupted_recovery_drain_waiting"),
                            ("stop_bot", "interrupted_recovery_executor_stopping"),
                            (
                                "deploy",
                                "interrupted_recovery_deploy_authorizing",
                            ),
                        ],
                    )

    def test_exclusive_cutover_lock_atomically_adopts_market_maintenance_inode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            journal = secure / "market-maintenance.json"
            lock_path = secure / "production-release.lock"
            lock_path.touch(mode=0o600)
            metadata = lock_path.stat()
            maintenance = {
                "schema": "market_pipeline_maintenance_lock/1.0",
                "environment": "production",
                "release_sha": "a" * 40,
                "nonce_sha256": "9" * 64,
                "journal_path_sha256": hashlib.sha256(
                    str(journal).encode("utf-8")
                ).hexdigest(),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            lock_path.write_text(
                json.dumps(maintenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            lock_path.chmod(0o600)
            journal.write_text(
                json.dumps(
                    {
                        "schema": "production_legacy_market_collector_handoff/1.1",
                        "host_role": "bot",
                        "status": "PRIMARY_COMMITTED",
                        "release_sha": "a" * 40,
                        "maintenance_lock": maintenance,
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            journal_digest = hashlib.sha256(journal.read_bytes()).hexdigest()

            run_lock = cutover.ExclusiveRunLock(secure)
            with patch.object(
                cutover.market_handoff,
                "validate_committed_handoff",
                return_value={},
            ):
                run_lock.adopt_market_pipeline_maintenance(
                    journal=journal,
                    expected_journal_sha256=journal_digest,
                    expected_primary_verification_sha256="6" * 64,
                    release_sha="a" * 40,
                )
            try:
                adopted = lock_path.stat()
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(adopted.st_ino, metadata.st_ino)
                self.assertEqual(adopted.st_dev, metadata.st_dev)
                self.assertEqual(payload, maintenance)
                self.assertEqual(run_lock.binding()["inode"], metadata.st_ino)
                self.assertEqual(run_lock.binding()["nonce_sha256"], "9" * 64)
            finally:
                run_lock.release()
            self.assertFalse(lock_path.exists())

    def test_unsuccessful_promotion_restores_adoptable_market_maintenance_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            journal = secure / "production-market-maintenance.json"
            lock_path = secure / "production-release.lock"
            lock_path.touch(mode=0o600)
            metadata = lock_path.stat()
            maintenance = {
                "schema": "market_pipeline_maintenance_lock/1.0",
                "environment": "production",
                "release_sha": "d" * 40,
                "nonce_sha256": "8" * 64,
                "journal_path_sha256": hashlib.sha256(
                    str(journal).encode("utf-8")
                ).hexdigest(),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            lock_path.write_text(
                json.dumps(maintenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            lock_path.chmod(0o600)
            journal.write_text(
                json.dumps(
                    {
                        "schema": "production_legacy_market_collector_handoff/1.1",
                        "host_role": "bot",
                        "status": "PRIMARY_COMMITTED",
                        "release_sha": "d" * 40,
                        "maintenance_lock": maintenance,
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            journal_digest = hashlib.sha256(journal.read_bytes()).hexdigest()

            first = cutover.ExclusiveRunLock(secure)
            with patch.object(
                cutover.market_handoff,
                "validate_committed_handoff",
                return_value={},
            ):
                first.adopt_market_pipeline_maintenance(
                    journal=journal,
                    expected_journal_sha256=journal_digest,
                    expected_primary_verification_sha256="5" * 64,
                    release_sha="d" * 40,
                )
            first.restore_adopted_market_pipeline_maintenance()
            self.assertFalse(first.held)
            self.assertEqual(lock_path.stat().st_ino, metadata.st_ino)
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8")), maintenance
            )

            retry = cutover.ExclusiveRunLock(secure)
            with patch.object(
                cutover.market_handoff,
                "validate_committed_handoff",
                return_value={},
            ):
                retry.adopt_market_pipeline_maintenance(
                    journal=journal,
                    expected_journal_sha256=journal_digest,
                    expected_primary_verification_sha256="5" * 64,
                    release_sha="d" * 40,
                )
            retry.release()
            self.assertFalse(lock_path.exists())

    def test_market_maintenance_adoption_rejects_pending_queue_phase_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            pending = secure / "production-queue-phase-pending.json"
            pending.write_text('{"status":"prepared"}\n', encoding="utf-8")
            pending.chmod(0o600)
            journal = secure / "production-market-maintenance.json"
            journal.write_text("{}\n", encoding="utf-8")
            journal.chmod(0o600)
            lock = cutover.ExclusiveRunLock(secure)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_PENDING_PHASE_JOURNAL",
            ):
                lock.adopt_market_pipeline_maintenance(
                    journal=journal,
                    expected_journal_sha256=hashlib.sha256(
                        journal.read_bytes()
                    ).hexdigest(),
                    expected_primary_verification_sha256="4" * 64,
                    release_sha="e" * 40,
                )

    def test_exclusive_cutover_lock_rejects_tampered_market_maintenance_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            journal = secure / "market-maintenance.json"
            lock_path = secure / "production-release.lock"
            lock_path.touch(mode=0o600)
            metadata = lock_path.stat()
            maintenance = {
                "schema": "market_pipeline_maintenance_lock/1.0",
                "environment": "production",
                "release_sha": "b" * 40,
                "nonce_sha256": "7" * 64,
                "journal_path_sha256": hashlib.sha256(
                    str(journal).encode("utf-8")
                ).hexdigest(),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            lock_path.write_text(
                json.dumps({**maintenance, "release_sha": "c" * 40}) + "\n",
                encoding="utf-8",
            )
            lock_path.chmod(0o600)
            journal.write_text(
                json.dumps(
                    {
                        "schema": "production_legacy_market_collector_handoff/1.1",
                        "host_role": "bot",
                        "status": "PRIMARY_COMMITTED",
                        "release_sha": "b" * 40,
                        "maintenance_lock": maintenance,
                        "secrets_disclosed": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)

            run_lock = cutover.ExclusiveRunLock(secure)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_MARKET_MAINTENANCE_LOCK",
            ):
                run_lock.adopt_market_pipeline_maintenance(
                    journal=journal,
                    expected_journal_sha256=hashlib.sha256(
                        journal.read_bytes()
                    ).hexdigest(),
                    expected_primary_verification_sha256="3" * 64,
                    release_sha="b" * 40,
                )
            self.assertFalse(run_lock.held)
            self.assertTrue(lock_path.exists())

    def test_reconciliation_lock_allows_only_its_exact_failed_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secure = Path(tmpdir) / "artifacts"
            secure.mkdir(mode=0o700)
            failed = secure / "production-queue-phase-failed.json"
            failed.write_text('{"status":"recovery_failed"}\n', encoding="utf-8")
            failed.chmod(0o600)
            terminal = secure / "production-queue-phase-terminal.json"
            terminal.write_text('{"status":"redeployed"}\n', encoding="utf-8")
            terminal.chmod(0o600)
            lock = cutover.ExclusiveRunLock(secure)
            lock.acquire(allow_recovery_journal=failed)
            lock.release()

            other = secure / "production-queue-phase-other.json"
            other.write_text('{"status":"recovery_failed"}\n', encoding="utf-8")
            other.chmod(0o600)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_PENDING_PHASE_JOURNAL"
            ):
                lock.acquire(allow_recovery_journal=failed)

    def test_source_updater_lock_rejects_a_concurrent_mutator_before_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "master.env"
            source.write_text("PROFILE=legacy\n", encoding="utf-8")
            source.chmod(0o600)
            backup_dir = root / "backup"
            backup_dir.mkdir(mode=0o700)
            original = source.read_bytes()
            held = cutover.ImmutableSourceLock(source)
            held.acquire()
            try:
                with self.assertRaisesRegex(
                    cutover.ProductionCutoverError,
                    "BLOCKED_CONCURRENT_SOURCE_UPDATE",
                ):
                    cutover.backup_and_update_source(
                        source,
                        backup_dir,
                        {"PROFILE": "queue-v1"},
                        expected_source_sha256=hashlib.sha256(original).hexdigest(),
                    )
            finally:
                held.release()
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(backup_dir.iterdir()), [])

    def test_source_lock_requires_exact_mode_and_single_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "master.env"
            source.write_text("PROFILE=legacy\n", encoding="utf-8")
            lock_path = root / ".production-runtime-source.lock"
            lock_path.write_text("", encoding="utf-8")
            lock_path.chmod(0o640)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_IMMUTABLE_SOURCE_LOCK"
            ):
                cutover.ImmutableSourceLock(source).acquire()
            lock_path.chmod(0o600)
            alias = root / "lock-hardlink"
            os.link(lock_path, alias)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_IMMUTABLE_SOURCE_LOCK"
            ):
                cutover.ImmutableSourceLock(source).acquire()

    def test_guarded_apply_proves_legacy_zero_queue_and_writes_redacted_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            preflight = root / "preflight.json"
            binding = self.binding()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            preflight_digest = self.write_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeOperations(manifest)
            live = Mock(
                return_value={
                    "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
                    "source_sha256": source_digest,
                }
            )
            with patch.object(cutover, "git_binding", return_value=binding):
                result = cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=preflight,
                    preflight_digest=preflight_digest,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    secure_backup_dir=secure,
                    artifact_dir=artifacts,
                    confirmation=cutover.APPLY_CONFIRMATION,
                    operations_factory=lambda _manifest: fake,
                    preflight_runner=live,
                )
            self.assertEqual(result["status"], "applied")
            self.assertEqual(planner.source_profile(parse_env(source)), "queue-v1")
            self.assertEqual(fake.mutations[:3], ["stop_producers", "stop_bot", "deploy"])
            self.assertEqual(len(list(secure.glob("*.bak"))), 1)
            receipt = next(artifacts.glob("production-queue-cutover-*.json"))
            self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(), result["receipt_sha256"])
            self.assertEqual(cutover.scan_paths([receipt])["status"], "clean")
            backup_path = secure / result["source_backup_file"]
            verified = cutover.verify_apply_receipt(
                receipt,
                result["receipt_sha256"],
                source=source,
                source_backup_path=backup_path,
                source_backup_digest=result["source_backup_sha256"],
                binding=binding,
            )
            self.assertEqual(verified["status"], "verified")

            unrelated = secure / "unrelated-legacy.bak"
            unrelated.write_bytes(backup_path.read_bytes())
            unrelated.chmod(0o600)
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError, "BLOCKED_APPLY_RECEIPT_BINDING"
            ):
                cutover.verify_apply_receipt(
                    receipt,
                    result["receipt_sha256"],
                    source=source,
                    source_backup_path=unrelated,
                    source_backup_digest=hashlib.sha256(unrelated.read_bytes()).hexdigest(),
                    binding=binding,
                )
            live.assert_called_once()
            self.assertTrue(live.call_args.kwargs["target_queue_cutover"])

    def test_initial_inventory_failure_is_terminal_without_false_recovery_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            preflight = root / "preflight.json"
            binding = self.binding()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            preflight_digest = self.write_preflight(
                preflight, binding, backup_digest, source_digest
            )
            operations = Mock()
            operations.executor_inventory.side_effect = (
                cutover.ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
            )
            with patch.object(cutover, "git_binding", return_value=binding):
                with self.assertRaises(cutover.ProductionCutoverError) as raised:
                    cutover.apply_cutover(
                        manifest=manifest,
                        staging_env=staging,
                        preflight_report=preflight,
                        preflight_digest=preflight_digest,
                        backup_receipt=backup_receipt,
                        backup_digest=backup_digest,
                        secure_backup_dir=secure,
                        artifact_dir=artifacts,
                        confirmation=cutover.APPLY_CONFIRMATION,
                        operations_factory=lambda _manifest: operations,
                        preflight_runner=Mock(
                            return_value={
                                "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
                                "source_sha256": source_digest,
                            }
                        ),
                    )
            self.assertEqual(raised.exception.code, "EXECUTOR_INVENTORY_AMBIGUOUS")
            operations.stop_producers.assert_not_called()
            failed = next(
                artifacts.glob("production-queue-cutover-failed-*.json")
            )
            failed_payload = json.loads(failed.read_text(encoding="utf-8"))
            self.assertEqual(
                failed_payload["safe_recovery"]["status"],
                "not_required_before_mutation",
            )
            journal = next(artifacts.glob("production-queue-phase-*.json"))
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["status"],
                "failed_recovered",
            )

    def test_failure_after_source_switch_restores_legacy_and_emits_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            original = source.read_bytes()
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            preflight = root / "preflight.json"
            binding = self.binding()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            preflight_digest = self.write_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeOperations(manifest, fail_first_deploy=True)
            with patch.object(cutover, "git_binding", return_value=binding):
                with self.assertRaises(cutover.ProductionCutoverError) as raised:
                    cutover.apply_cutover(
                        manifest=manifest,
                        staging_env=staging,
                        preflight_report=preflight,
                        preflight_digest=preflight_digest,
                        backup_receipt=backup_receipt,
                        backup_digest=backup_digest,
                        secure_backup_dir=secure,
                        artifact_dir=artifacts,
                        confirmation=cutover.APPLY_CONFIRMATION,
                        operations_factory=lambda _manifest: fake,
                        preflight_runner=Mock(
                            return_value={
                                "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
                                "source_sha256": source_digest,
                            }
                        ),
                    )
            self.assertEqual(raised.exception.code, "SIMULATED_DEPLOY_FAILURE")
            self.assertIsNotNone(raised.exception.receipt_sha256)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(fake.deploy_calls, 2)
            failed = next(artifacts.glob("production-queue-cutover-failed-*.json"))
            self.assertEqual(cutover.scan_paths([failed])["status"], "clean")

    def test_keyboard_interrupt_after_source_switch_recovers_legacy_and_journals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            original = source.read_bytes()
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            preflight = root / "preflight.json"
            binding = self.binding()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            preflight_digest = self.write_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeOperations(manifest, interrupt_first_deploy=True)
            with patch.object(cutover, "git_binding", return_value=binding):
                with self.assertRaises(cutover.ProductionCutoverError) as raised:
                    cutover.apply_cutover(
                        manifest=manifest,
                        staging_env=staging,
                        preflight_report=preflight,
                        preflight_digest=preflight_digest,
                        backup_receipt=backup_receipt,
                        backup_digest=backup_digest,
                        secure_backup_dir=secure,
                        artifact_dir=artifacts,
                        confirmation=cutover.APPLY_CONFIRMATION,
                        operations_factory=lambda _manifest: fake,
                        preflight_runner=Mock(
                            return_value={
                                "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
                                "source_sha256": source_digest,
                            }
                        ),
                    )
            self.assertEqual(raised.exception.code, "UNEXPECTED_CUTOVER_FAILURE")
            self.assertEqual(source.read_bytes(), original)
            journal = next(artifacts.glob("production-queue-phase-*.json"))
            payload = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed_recovered")
            self.assertFalse((artifacts / "production-release.lock").exists())

    def test_guarded_queue_redeploy_keeps_source_and_uses_official_authority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            manifest_values = planner.parse_env_file(manifest)
            manifest_values.update(
                {
                    "PRODUCTION_BACKUP_RECEIPT_PATH": str(backup_receipt),
                    "PRODUCTION_BACKUP_RECEIPT_SHA256": backup_digest,
                }
            )
            self.write_env(manifest, manifest_values)
            original_source = source.read_bytes()
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            source_digest = hashlib.sha256(original_source).hexdigest()
            preflight = root / "redeploy-preflight.json"
            preflight_digest = self.write_redeploy_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeRedeployOperations(manifest)
            live = Mock(
                return_value={
                    "status": "READY_FOR_QUEUE_V1_REDEPLOY",
                    "source_sha256": source_digest,
                    "git": binding,
                }
            )
            with patch.object(cutover, "git_binding", return_value=binding):
                result = cutover.redeploy_queue_v1(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=preflight,
                    preflight_digest=preflight_digest,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    artifact_dir=artifacts,
                    confirmation=cutover.REDEPLOY_CONFIRMATION,
                    operations_factory=lambda _manifest: fake,
                    preflight_runner=live,
                )
            self.assertEqual(result["status"], "redeployed")
            self.assertEqual(fake.deploy_calls, 1)
            self.assertEqual(source.read_bytes(), original_source)
            self.assertEqual(planner.source_profile(parse_env(source)), "queue-v1")
            receipt = artifacts / result["receipt_file"]
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "redeployed")
            self.assertFalse(payload["source_profile_changed"])
            self.assertEqual(payload["synthetic_customer_mutations"], 0)
            live.assert_called_once()

    def test_queue_redeploy_preserves_transferred_market_maintenance_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            manifest_values = planner.parse_env_file(manifest)
            manifest_values.update(
                {
                    "PRODUCTION_BACKUP_RECEIPT_PATH": str(backup_receipt),
                    "PRODUCTION_BACKUP_RECEIPT_SHA256": backup_digest,
                }
            )
            self.write_env(manifest, manifest_values)
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            binding = self.binding()
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            handoffs = root / "handoffs"
            handoffs.mkdir(mode=0o700)
            release = "b" * 40
            journal = handoffs / f"bot-legacy-handoff-{release[:8]}.json"
            lock_path = artifacts / "production-release.lock"
            lock_path.touch(mode=0o600)
            metadata = lock_path.stat()
            maintenance = {
                "schema": "market_pipeline_maintenance_lock/1.0",
                "environment": "production",
                "host_role": "bot",
                "release_sha": release,
                "nonce_sha256": "7" * 64,
                "journal_path_sha256": hashlib.sha256(
                    str(journal).encode("utf-8")
                ).hexdigest(),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            lock_path.write_text(
                json.dumps(maintenance, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            lock_path.chmod(0o600)
            journal.write_text(
                json.dumps(
                    {
                        "schema": "production_legacy_market_collector_handoff/1.1",
                        "status": "AUTHORITY_TRANSFERRED",
                        "host_role": "bot",
                        "release_sha": release,
                        "maintenance_lock": maintenance,
                        "state_deleted": False,
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            preflight = root / "redeploy-preflight.json"
            preflight_digest = self.write_redeploy_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeRedeployOperations(manifest)
            live = Mock(
                return_value={
                    "status": "READY_FOR_QUEUE_V1_REDEPLOY",
                    "source_sha256": source_digest,
                    "git": binding,
                }
            )
            with (
                patch.object(cutover, "git_binding", return_value=binding),
                patch.object(
                    cutover.market_handoff,
                    "validate_transferred_handoff",
                    return_value={},
                ) as validate,
            ):
                result = cutover.redeploy_queue_v1(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=preflight,
                    preflight_digest=preflight_digest,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    artifact_dir=artifacts,
                    confirmation=cutover.REDEPLOY_CONFIRMATION,
                    operations_factory=lambda _manifest: fake,
                    preflight_runner=live,
                    market_handoff_dir=handoffs,
                )
            self.assertEqual(result["status"], "redeployed")
            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.stat().st_ino, metadata.st_ino)
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8")), maintenance
            )
            validate.assert_called_once()

    def test_queue_owned_redeploy_threads_exact_private_primary_attestation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            values = planner.queue_target_values(planner.parse_env_file(source))
            values["PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE"] = (
                "PRIVATE_PRIMARY"
            )
            self.write_env(source, values)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(
                backup_receipt.read_bytes()
            ).hexdigest()
            manifest_values = planner.parse_env_file(manifest)
            manifest_values.update(
                {
                    "PRODUCTION_BACKUP_RECEIPT_PATH": str(backup_receipt),
                    "PRODUCTION_BACKUP_RECEIPT_SHA256": backup_digest,
                }
            )
            self.write_env(manifest, manifest_values)
            preparation = root / "private-primary-preparation.json"
            preparation.write_text('{"status":"PASS"}\n', encoding="utf-8")
            preparation.chmod(0o600)
            attestation = cutover.bind_private_primary_deploy_attestation(
                manifest,
                manifest_sha256=cutover._sha256(manifest),
                receipt_path=preparation,
                receipt_sha256=cutover._sha256(preparation),
            )
            original_source = source.read_bytes()
            source_digest = hashlib.sha256(original_source).hexdigest()
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            binding = self.binding()
            preflight = root / "redeploy-preflight.json"
            preflight_digest = self.write_redeploy_preflight(
                preflight, binding, backup_digest, source_digest
            )
            fake = FakeRedeployOperations(manifest)
            live = Mock(
                return_value={
                    "status": "READY_FOR_QUEUE_V1_REDEPLOY",
                    "source_sha256": source_digest,
                    "git": binding,
                }
            )
            with patch.object(cutover, "git_binding", return_value=binding):
                result = cutover.redeploy_queue_v1(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=preflight,
                    preflight_digest=preflight_digest,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    artifact_dir=artifacts,
                    confirmation=cutover.REDEPLOY_CONFIRMATION,
                    private_primary_attestation=attestation,
                    operations_factory=lambda _manifest: fake,
                    preflight_runner=live,
                )
            self.assertEqual(result["status"], "redeployed")
            self.assertEqual(fake.private_primary_attestations, [attestation])
            self.assertEqual(source.read_bytes(), original_source)
            payload = json.loads(
                (artifacts / result["receipt_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["private_primary_manifest_attestation"],
                cutover._private_primary_attestation_binding(
                    manifest, attestation
                ),
            )

    def test_queue_redeploy_preflight_binds_current_runtime_backup_to_target_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            manifest_values = planner.parse_env_file(manifest)
            manifest_values.update(
                {
                    "PRODUCTION_BACKUP_RECEIPT_PATH": str(backup_receipt),
                    "PRODUCTION_BACKUP_RECEIPT_SHA256": backup_digest,
                }
            )
            self.write_env(manifest, manifest_values)
            binding = self.binding()
            fake = FakeRedeployOperations(manifest)

            def gateway(identity, method, payload):
                if method == "getMe":
                    return {"id": identity.bot_id, "username": identity.username}
                if method == "getChat":
                    return {"id": int(payload["chat_id"]), "type": "channel"}
                return {
                    "status": "administrator",
                    "is_anonymous": False,
                    "can_manage_chat": True,
                    "can_post_messages": True,
                    "can_edit_messages": True,
                    "can_delete_messages": True,
                    "can_restrict_members": True,
                }

            with (
                patch.object(cutover, "git_binding", return_value=binding),
                patch.object(
                    cutover,
                    "_backup_status",
                    return_value={
                        "status": "verified",
                        "digest": backup_digest,
                    },
                ) as backup_check,
            ):
                report = cutover.run_redeploy_preflight(
                    manifest=manifest,
                    staging_env=staging,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    operations_factory=lambda _manifest: fake,
                    gateway=gateway,
                )
            self.assertEqual(report["status"], "READY_FOR_QUEUE_V1_REDEPLOY")
            self.assertEqual(report["git"]["head"], "a" * 40)
            self.assertEqual(report["current_runtime"]["release_sha"], "c" * 40)
            self.assertNotEqual(
                report["git"]["head"], report["current_runtime"]["release_sha"]
            )
            self.assertEqual(report["provider"]["identity_count"], 6)
            self.assertEqual(report["provider"]["staging_identity_count"], 6)
            backup_check.assert_called_once()

    def test_queue_redeploy_failure_runs_exact_target_forward_reconcile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            self.write_env(
                source,
                planner.queue_target_values(planner.parse_env_file(source)),
            )
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            manifest_values = planner.parse_env_file(manifest)
            manifest_values.update(
                {
                    "PRODUCTION_BACKUP_RECEIPT_PATH": str(backup_receipt),
                    "PRODUCTION_BACKUP_RECEIPT_SHA256": backup_digest,
                }
            )
            self.write_env(manifest, manifest_values)
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            binding = self.binding()
            preflight = root / "redeploy-preflight.json"
            preflight_digest = self.write_redeploy_preflight(
                preflight, binding, backup_digest, source_digest
            )
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            fake = FakeRedeployOperations(manifest, fail_first_deploy=True)
            with patch.object(cutover, "git_binding", return_value=binding):
                with self.assertRaises(cutover.ProductionCutoverError) as raised:
                    cutover.redeploy_queue_v1(
                        manifest=manifest,
                        staging_env=staging,
                        preflight_report=preflight,
                        preflight_digest=preflight_digest,
                        backup_receipt=backup_receipt,
                        backup_digest=backup_digest,
                        artifact_dir=artifacts,
                        confirmation=cutover.REDEPLOY_CONFIRMATION,
                        operations_factory=lambda _manifest: fake,
                        preflight_runner=Mock(
                            return_value={
                                "status": "READY_FOR_QUEUE_V1_REDEPLOY",
                                "source_sha256": source_digest,
                                "git": binding,
                            }
                        ),
                    )
            self.assertEqual(raised.exception.code, "SIMULATED_REDEPLOY_FAILURE")
            self.assertEqual(fake.deploy_calls, 2)
            failed = next(
                artifacts.glob("production-queue-redeploy-failed-*.json")
            )
            payload = json.loads(failed.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["safe_recovery"]["status"],
                "queue_v1_forward_reconciled",
            )
            journal = next(artifacts.glob("production-queue-phase-*.json"))
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["status"],
                "failed_recovered",
            )

    def test_reconcile_failed_redeploy_preserves_journal_and_requires_live_proof(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            source = root / "source.env"
            source.write_text("PROFILE=queue-v1\n", encoding="utf-8")
            source.chmod(0o600)
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            binding = self.binding()
            journal = artifacts / "production-queue-phase-failed.json"
            failed = artifacts / "production-queue-redeploy-failed-proof.json"
            failed_payload = {
                "environment": "production",
                "command": "redeploy",
                "status": "failed",
                "git": binding,
                "safe_recovery": {"status": "recovery_failed_fail_closed"},
                "secrets_disclosed": False,
            }
            failed.write_text(
                json.dumps(failed_payload, sort_keys=True) + "\n", encoding="utf-8"
            )
            failed.chmod(0o600)
            failed_digest = hashlib.sha256(failed.read_bytes()).hexdigest()
            journal_payload = {
                "environment": "production",
                "command": "redeploy",
                "status": "recovery_failed",
                "git_head": binding["head"],
                "source_sha256": source_digest,
                "receipt_sha256": failed_digest,
                "secrets_disclosed": False,
            }
            journal.write_text(
                json.dumps(journal_payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            journal_bytes = journal.read_bytes()
            journal_digest = hashlib.sha256(journal_bytes).hexdigest()
            live = {
                "status": "READY_FOR_QUEUE_V1_REDEPLOY",
                "source_sha256": source_digest,
                "git": binding,
                "executor_inventory": {
                    "count": 1,
                    "owner": "queue-v1",
                    "overlap": False,
                },
                "current_runtime": {"status": "verified"},
                "runtime_role_contract": {
                    "status": "verified",
                    "owner": "queue-v1",
                },
                "queue_health": {"status": "passed", "decision": "continue"},
            }
            with (
                patch.object(cutover, "DEFAULT_ARTIFACT_DIR", artifacts),
                patch.object(cutover, "git_binding", return_value=binding),
                patch.object(
                    cutover,
                    "_static_redeploy_gate",
                    return_value=(source, {}, {}),
                ),
                patch.object(
                    cutover,
                    "verify_redeploy_preflight_evidence",
                    return_value={"status": "verified"},
                ),
            ):
                result = cutover.reconcile_failed_redeploy(
                    manifest=root / "manifest.env",
                    staging_env=root / "staging.env",
                    preflight_report=root / "preflight.json",
                    preflight_digest="a" * 64,
                    backup_receipt=root / "backup.json",
                    backup_digest="b" * 64,
                    failed_receipt=failed,
                    failed_digest=failed_digest,
                    phase_journal=journal,
                    phase_journal_digest=journal_digest,
                    artifact_dir=artifacts,
                    confirmation=cutover.RECONCILE_REDEPLOY_CONFIRMATION,
                    preflight_runner=Mock(return_value=live),
                )
            self.assertEqual(result["status"], "failed_recovered")
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["status"],
                "failed_recovered",
            )
            original = next(
                artifacts.glob("production-queue-recovery-original-*.json")
            )
            self.assertEqual(original.read_bytes(), journal_bytes)
            receipt = artifacts / result["receipt_file"]
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["database_mutations"],
                0,
            )

    def test_queue_redeploy_rejects_initial_cutover_preflight_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            binding = self.binding()
            path = root / "apply-preflight.json"
            backup_digest = "d" * 64
            source_digest = "e" * 64
            digest = self.write_preflight(
                path, binding, backup_digest, source_digest
            )
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "BLOCKED_REDEPLOY_PREFLIGHT_CONTRACT",
            ):
                cutover.verify_redeploy_preflight_evidence(
                    path,
                    digest,
                    backup_digest=backup_digest,
                    source_digest=source_digest,
                    binding=binding,
                )

    def test_queue_redeploy_confirmation_blocks_before_static_or_runtime_gates(self):
        with patch.object(
            cutover,
            "_static_redeploy_gate",
            side_effect=AssertionError("static gate must not run"),
        ):
            with self.assertRaisesRegex(
                cutover.ProductionCutoverError,
                "REDEPLOY_CONFIRMATION_MISMATCH",
            ):
                cutover.redeploy_queue_v1(
                    manifest=Path("/missing/manifest"),
                    staging_env=Path("/missing/staging"),
                    preflight_report=Path("/missing/preflight"),
                    preflight_digest="",
                    backup_receipt=Path("/missing/backup"),
                    backup_digest="",
                    artifact_dir=Path("/missing/artifacts"),
                    confirmation="wrong",
                )

    def test_rollback_requires_and_uses_the_bound_apply_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, staging, manifest = self.fixture(root)
            original = source.read_bytes()
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            backup_receipt = root / "backup.json"
            backup_receipt.write_text("{}", encoding="utf-8")
            backup_digest = hashlib.sha256(backup_receipt.read_bytes()).hexdigest()
            preflight = root / "preflight.json"
            binding = self.binding()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            preflight_digest = self.write_preflight(
                preflight, binding, backup_digest, source_digest
            )
            apply_ops = FakeOperations(manifest)
            with patch.object(cutover, "git_binding", return_value=binding):
                applied = cutover.apply_cutover(
                    manifest=manifest,
                    staging_env=staging,
                    preflight_report=preflight,
                    preflight_digest=preflight_digest,
                    backup_receipt=backup_receipt,
                    backup_digest=backup_digest,
                    secure_backup_dir=secure,
                    artifact_dir=artifacts,
                    confirmation=cutover.APPLY_CONFIRMATION,
                    operations_factory=lambda _manifest: apply_ops,
                    preflight_runner=Mock(
                        return_value={
                            "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
                            "source_sha256": source_digest,
                        }
                    ),
                )
                rollback_ops = FakeRollbackOperations(manifest)
                rolled_back = cutover.rollback_to_legacy(
                    manifest=manifest,
                    source_backup_path=secure / applied["source_backup_file"],
                    source_backup_digest=applied["source_backup_sha256"],
                    apply_receipt_path=artifacts / applied["receipt_file"],
                    apply_receipt_digest=applied["receipt_sha256"],
                    artifact_dir=artifacts,
                    confirmation=cutover.ROLLBACK_CONFIRMATION,
                    operations_factory=lambda _manifest: rollback_ops,
                )
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(rollback_ops.mutations[:3], [
                "stop_producers",
                "stop_bot",
                "deploy",
            ])


def parse_env(path: Path) -> dict[str, str]:
    return planner.parse_env_file(path)


if __name__ == "__main__":
    unittest.main()
