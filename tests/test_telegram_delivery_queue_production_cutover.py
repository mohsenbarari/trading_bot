import hashlib
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
from unittest.mock import Mock, patch

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

    def deploy_official(self, _authority_path=None, _authority_digest=None):
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
        self.inventories = iter(
            (
                {"count": 1, "owner": "queue-v1", "overlap": False},
                {"count": 1, "owner": "queue-v1", "overlap": False},
            )
        )

    def executor_inventory(self):
        return next(self.inventories)

    def deploy_official(self, _authority_path=None, _authority_digest=None):
        self.deploy_calls += 1
        if self.fail_first_deploy and self.deploy_calls == 1:
            raise cutover.ProductionCutoverError("SIMULATED_REDEPLOY_FAILURE")
        return {"status": "completed", "official_script": True}

    def release_schema_inventory(self, _expected_database_name):
        return {
            "status": "verified",
            "release_sha": "c" * 40,
            "schema_head": "ff5a6b7c8d9e",
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
                "schema_head": "ff5a6b7c8d9e",
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

    def test_official_deploy_marks_inherited_source_lock_only_with_authority(self):
        operations = cutover.ProductionOperations.__new__(cutover.ProductionOperations)
        operations.manifest = Path("/secure/online.env")
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
        operations.deploy_official(Path("/secure/authority.json"), "a" * 64)
        authority_env = operations._run.call_args.kwargs["env"]
        self.assertEqual(
            authority_env["PRODUCTION_SOURCE_LOCK_INHERITED_CONFIRM"],
            "verified-cutover-held-lock",
        )

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
        self.assertIn("config --format json", operations._host.call_args.args[1][2])

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
