import hashlib
import io
import json
import os
import copy
import tempfile
import unittest
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

import yaml

from scripts import plan_telegram_delivery_queue_production as plan
from scripts.deploy_config import resolve_deploy_settings


class TelegramDeliveryQueueProductionPlanTests(unittest.TestCase):
    def complete_queue_values(self) -> dict[str, str]:
        values = {
            **plan.QUEUE_PROFILE,
            "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER": "producer-only",
            "TELEGRAM_NON_BOT_BOT_TOKEN": "",
            "BOT_TOKEN": "production-primary-token",
            "BOT_USERNAME": "production_primary_bot",
            "POSTGRES_DB": "production_db",
            "CHANNEL_ID": "-100123",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100123",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "100",
        }
        for index in range(1, 6):
            values.update(
                {
                    f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true",
                    f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"production-publisher-token-{index}",
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(100 + index),
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": f"production_publisher_{index}_bot",
                }
            )
        return values

    def complete_staging_values(self) -> dict[str, str]:
        values = {
            **plan.QUEUE_PROFILE,
            "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER": "producer-only",
            "BOT_TOKEN": "staging-primary-token",
            "BOT_USERNAME": "staging_primary_bot",
            "CHANNEL_ID": "-100456",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100456",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "200",
        }
        for index in range(1, 6):
            values.update(
                {
                    f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true",
                    f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"staging-publisher-token-{index}",
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(200 + index),
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": f"staging_publisher_{index}_bot",
                }
            )
        return values

    def write_env(self, path: Path, values: dict[str, str]) -> None:
        path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def test_plan_is_strictly_read_only_and_has_no_apply_command(self):
        payload = plan.build_plan()
        self.assertEqual(payload["commands"], ["plan", "status", "preflight"])
        self.assertFalse(payload["apply_supported"])
        self.assertNotIn("apply", payload["commands"])
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                plan.parse_args(["apply"])

    def test_plan_report_passes_redaction_scan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "plan.json"
            evidence = plan._write_report(report, plan.build_plan())
            self.assertEqual(evidence["security_scan"], "clean")
            self.assertRegex(evidence["report_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("production-primary-token", report.read_text(encoding="utf-8"))

    def test_incomplete_manifest_cannot_fall_back_to_deploy_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "master.env"
            source.write_text("BOT_TOKEN=missing-identities\n", encoding="utf-8")
            source.chmod(0o600)
            manifest = root / "online.env"
            self.write_env(
                manifest,
                {
                    "RUNTIME_ENV_SOURCE_PATH": str(source),
                    "FOREIGN_RUNTIME_ENV_PATH": str(root / "foreign.env"),
                    "IRAN_RUNTIME_ENV_PATH": str(root / "iran.env"),
                    "LOCAL_PROJECT_DIR": str(plan.REPO_ROOT),
                    "IRAN_APP_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                    "IRAN_PUBLIC_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                    "FOREIGN_PUBLIC_DOMAIN": plan.PRODUCTION_FOREIGN_DOMAIN,
                },
            )
            with self.assertRaisesRegex(
                plan.ReadinessBlocked, "BLOCKED_EXPLICIT_PRODUCTION_IDENTITY"
            ):
                plan._immutable_source(manifest)

    def test_production_target_resolution_ignores_hostile_shell_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            key = root / "production-key"
            key.write_text("test-key\n", encoding="utf-8")
            key.chmod(0o600)
            manifest = root / "online.env"
            manifest_values = {
                "IRAN_HOST": "manifest-production-host.invalid",
                "IRAN_SSH_USER": "manifest-user",
                "IRAN_SSH_PORT": "22022",
                "IRAN_SSH_PRIVATE_KEY_PATH": str(key),
                "IRAN_PROJECT_DIR": plan.PRODUCTION_IRAN_PROJECT_DIR,
            }
            self.write_env(manifest, manifest_values)
            hostile = {
                "IRAN_HOST": "polluted-shell-host.invalid",
                "IRAN_SSH_USER": "polluted-user",
                "IRAN_SSH_PORT": "1",
                "IRAN_SSH_PRIVATE_KEY_PATH": "/polluted/key",
            }
            with patch.dict(os.environ, hostile, clear=False):
                settings = resolve_deploy_settings(
                    manifest_path=str(manifest), environ={}
                )
            for key_name in (
                "IRAN_HOST",
                "IRAN_SSH_USER",
                "IRAN_SSH_PORT",
                "IRAN_SSH_PRIVATE_KEY_PATH",
                "IRAN_PROJECT_DIR",
            ):
                self.assertEqual(settings[key_name], manifest_values[key_name])

    def test_host_status_requires_explicit_key_only_ssh_before_inspection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "online.env"
            self.write_env(
                manifest,
                {
                    "IRAN_HOST": "production-iran.invalid",
                    "IRAN_SSH_USER": "root",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_PROJECT_DIR": plan.PRODUCTION_IRAN_PROJECT_DIR,
                    "IRAN_SSH_AUTH_METHOD": "password",
                    "IRAN_SSH_PASSWORD": "must-not-be-used",
                },
            )
            with patch.object(
                plan,
                "_inspect_local_container",
                side_effect=AssertionError("host inspection must not start"),
            ):
                with self.assertRaisesRegex(
                    plan.ReadinessBlocked, "BLOCKED_PRODUCTION_KEY_ONLY_SSH"
                ):
                    plan.host_status(manifest)

    def test_remote_inspector_disables_agent_and_password_fallback(self):
        runner = Mock(
            side_effect=(
                subprocess.CompletedProcess([], 0, stdout="current\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="iran\n", stderr=""),
            )
        )
        settings = {
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_PRIVATE_KEY_PATH": "/secure/production-key",
            "IRAN_SSH_TARGET": "root@production.invalid",
        }
        with patch.object(plan, "_run", runner):
            self.assertEqual(plan._inspect_remote_iran(settings), ("current", "iran"))
        argv = runner.call_args_list[0].args[0]
        for option in (
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
        ):
            self.assertIn(option, argv)

    def test_missing_publishers_blocks_before_git_host_or_provider_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "master.env"
            staging = root / "staging.env"
            manifest = root / "online.env"
            self.write_env(
                source,
                {
                    **plan.QUEUE_PROFILE,
                    "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER": "producer-only",
                    "BOT_TOKEN": "primary-only",
                    "POSTGRES_DB": "production_db",
                    "CHANNEL_ID": "-100123",
                    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100123",
                    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "100",
                },
            )
            self.write_env(staging, {"BOT_TOKEN": "different-staging-primary"})
            self.write_env(
                manifest,
                {
                    "RUNTIME_ENV_SOURCE_PATH": str(source),
                    "FOREIGN_RUNTIME_ENV_PATH": str(root / "foreign.env"),
                    "IRAN_RUNTIME_ENV_PATH": str(root / "iran.env"),
                    "LOCAL_PROJECT_DIR": str(plan.REPO_ROOT),
                    "IRAN_APP_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                    "IRAN_PUBLIC_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                    "FOREIGN_PUBLIC_DOMAIN": plan.PRODUCTION_FOREIGN_DOMAIN,
                    "IRAN_HOST": "production-iran.invalid",
                    "IRAN_SSH_USER": "root",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_PROJECT_DIR": plan.PRODUCTION_IRAN_PROJECT_DIR,
                },
            )
            gateway = Mock(side_effect=AssertionError("provider must not be called"))
            inspector = Mock(side_effect=AssertionError("hosts must not be touched"))
            with patch.object(plan, "git_binding", side_effect=AssertionError("git gate is later")):
                with self.assertRaisesRegex(plan.ReadinessBlocked, "BLOCKED_CREDENTIALS"):
                    plan.run_preflight(
                        manifest,
                        staging,
                        None,
                        None,
                        gateway=gateway,
                        host_inspector=inspector,
                    )
            gateway.assert_not_called()
            inspector.assert_not_called()

    def test_complete_preflight_enforces_every_read_only_gate_and_redacts_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "master.env"
            staging = root / "staging.env"
            manifest = root / "online.env"
            backup = root / "backup.json"
            report_path = root / "preflight-report.json"
            values = self.complete_queue_values()
            values.update(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
                    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
                    "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
                }
            )
            for index in range(1, 6):
                values[f"TELEGRAM_PUBLISHER_{index}_ENABLED"] = "false"
            self.write_env(source, values)
            self.write_env(staging, self.complete_staging_values())
            manifest_values = {
                "RUNTIME_ENV_SOURCE_PATH": str(source),
                "FOREIGN_RUNTIME_ENV_PATH": str(root / "foreign.env"),
                "IRAN_RUNTIME_ENV_PATH": str(root / "iran.env"),
                "LOCAL_PROJECT_DIR": str(plan.REPO_ROOT),
                "IRAN_APP_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                "IRAN_PUBLIC_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
                "FOREIGN_PUBLIC_DOMAIN": plan.PRODUCTION_FOREIGN_DOMAIN,
                "IRAN_HOST": "production-iran.invalid",
                "IRAN_SSH_USER": "root",
                "IRAN_SSH_PORT": "37067",
                "IRAN_PROJECT_DIR": plan.PRODUCTION_IRAN_PROJECT_DIR,
            }
            self.write_env(
                manifest,
                manifest_values,
            )
            binding = {
                "branch": "main",
                "head": "b" * 40,
                "tree": "c" * 40,
                "origin_main": "b" * 40,
                "worktree": "clean",
            }
            database_identities = {
                role: plan.database_identity_sha256(
                    role, values["POSTGRES_DB"], str(9000 + index)
                )
                for index, role in enumerate(("foreign", "iran"))
            }
            schema_head = "abc123"
            results = []
            for role in ("foreign", "iran"):
                files = []
                pulled_files = []
                for kind in ("db", "redis", "uploads", "audit"):
                    artifact = root / f"{role}-{kind}.artifact"
                    artifact.write_bytes(
                        f"{role}-{kind}-backup".encode("utf-8")
                    )
                    artifact.chmod(0o600)
                    remote_path = f"/srv/trading-bot/backups/{artifact.name}"
                    files.append(
                        {
                            "kind": kind,
                            "path": str(artifact)
                            if role == "foreign"
                            else remote_path,
                            "sha256": hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            "bytes": artifact.stat().st_size,
                        }
                    )
                    if role == "iran":
                        pulled_files.append(
                            {
                                "remote_path": remote_path,
                                "local_path": str(artifact),
                            }
                        )
                item = {
                    "role": role,
                    "command_role": role,
                    "status": "ok",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project_label": "trading_bot" if role == "foreign" else "current",
                    "release_sha": binding["head"],
                    "database_name": values["POSTGRES_DB"],
                    "database_identity_sha256": database_identities[role],
                    "schema_head": schema_head,
                    "target_binding_sha256": plan.backup_target_binding_sha256(
                        role, manifest_values
                    ),
                    "files": files,
                    "restore_smoke": {"status": "passed", "table_count": 20},
                }
                if role == "iran":
                    item["pulled_files"] = pulled_files
                results.append(item)
            backup.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "results": results,
                    }
                ),
                encoding="utf-8",
            )
            backup.chmod(0o600)
            backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()

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

            hosts = {
                "ready": True,
                "foreign_project_exact": True,
                "foreign_roles_exact": True,
                "iran_project_exact": True,
                "iran_role_exact": True,
                "release_sha_exact": True,
                "schema_head_and_queue_tables_exact": True,
                "database_identity_exact": True,
                "_database_identity_sha256": database_identities,
                "_schema_head": schema_head,
                "host_or_address_values_disclosed": False,
            }
            with patch.object(plan, "git_binding", return_value=binding):
                payload = plan.run_preflight(
                    manifest,
                    staging,
                    backup,
                    backup_digest,
                    target_queue_cutover=True,
                    gateway=gateway,
                    host_inspector=Mock(return_value=hosts),
                )
            self.assertEqual(
                payload["status"], "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY"
            )
            self.assertEqual(payload["source_profile"], "legacy")
            self.assertEqual(payload["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertTrue(payload["target_queue_cutover"])
            self.assertEqual(payload["provider"]["read_only_provider_call_count"], 36)
            self.assertEqual(payload["provider"]["staging_identity_count"], 6)
            evidence = plan._write_report(report_path, payload)
            self.assertEqual(evidence["security_scan"], "clean")
            rendered = report_path.read_text(encoding="utf-8")
            for identity in plan._identities(values)[0]:
                self.assertNotIn(identity.token, rendered)
            self.assertEqual(
                [set(item) for item in payload["provider"]["identities"]],
                [{"role", "status"}] * 6,
            )

    def test_distinct_credentials_pass_and_unapproved_staging_reuse_is_rejected(self):
        production = self.complete_queue_values()
        ready, identities = plan.credential_status(
            production, self.complete_staging_values()
        )
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["publisher_count"], 5)
        self.assertEqual(len(identities), 6)
        rendered = json.dumps(ready, sort_keys=True)
        self.assertNotIn("production-primary-token", rendered)
        self.assertNotIn("production-publisher-token", rendered)

        staging_collision = self.complete_staging_values()
        staging_collision["TELEGRAM_PUBLISHER_1_BOT_TOKEN"] = (
            "production-publisher-token-1"
        )
        collision, _ = plan.credential_status(production, staging_collision)
        self.assertEqual(
            collision["status"],
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
        )
        self.assertTrue(collision["staging_token_collision"])
        self.assertIn("BLOCKED_STAGING_PUBLISHER_REUSE", collision["blockers"])
        self.assertFalse(collision["shared_publisher_update_ownership_supported"])

        rotated_staging = self.complete_staging_values()
        rotated_staging.update(
            {
                "BOT_TOKEN": "different-rotated-staging-token",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "100",
                "BOT_USERNAME": "production_primary_bot",
            }
        )
        rotated_token_collision, _ = plan.credential_status(
            production, rotated_staging
        )
        self.assertEqual(
            rotated_token_collision["status"], "BLOCKED_STAGING_PRIMARY_REUSE"
        )
        self.assertTrue(rotated_token_collision["staging_expected_id_collision"])
        self.assertTrue(rotated_token_collision["staging_expected_username_collision"])

        incomplete, _ = plan.credential_status(
            production, {"BOT_TOKEN": "staging-only-token"}
        )
        self.assertIn(
            "BLOCKED_STAGING_COLLISION_EVIDENCE", incomplete["blockers"]
        )

        channel_staging = self.complete_staging_values()
        channel_staging["CHANNEL_ID"] = production["CHANNEL_ID"]
        channel_staging["TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID"] = production[
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID"
        ]
        channel_collision, _ = plan.credential_status(production, channel_staging)
        self.assertIn("BLOCKED_STAGING_CHANNEL_REUSE", channel_collision["blockers"])

    def test_exact_owner_approved_shared_fleet_is_diagnostic_but_never_ready(self):
        production = self.complete_queue_values()
        staging = self.complete_staging_values()
        production[plan.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
        staging[plan.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
        for index in range(1, 6):
            for suffix in ("BOT_TOKEN", "EXPECTED_BOT_ID", "EXPECTED_USERNAME"):
                staging[f"TELEGRAM_PUBLISHER_{index}_{suffix}"] = production[
                    f"TELEGRAM_PUBLISHER_{index}_{suffix}"
                ]

        status, identities = plan.credential_status(production, staging)
        self.assertEqual(
            status["status"],
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
        )
        self.assertEqual(len(identities), 6)
        self.assertTrue(status["shared_publisher_fleet_opt_in"])
        self.assertTrue(status["shared_publisher_fleet_exact"])
        self.assertTrue(status["shared_publisher_rate_safety"])
        self.assertFalse(status["shared_publisher_update_ownership_supported"])
        self.assertIn(
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
            status["blockers"],
        )
        self.assertLess(status["shared_publisher_max_combined_channel_rps_per_bot"], 2)
        self.assertFalse(status["staging_primary_collision"])
        self.assertFalse(status["staging_channel_collision"])

        staging["TELEGRAM_DELIVERY_QUEUE_DESTINATION_MIN_INTERVAL_SECONDS"] = "0.5"
        unsafe, _ = plan.credential_status(production, staging)
        self.assertEqual(
            unsafe["status"],
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
        )
        self.assertIn("BLOCKED_SHARED_PUBLISHER_RATE_SAFETY", unsafe["blockers"])

    def test_shared_publisher_fleet_rejects_partial_cross_role_and_primary_reuse(self):
        production = self.complete_queue_values()
        staging = self.complete_staging_values()
        production[plan.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
        staging[plan.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"
        for index in range(1, 6):
            for suffix in ("BOT_TOKEN", "EXPECTED_BOT_ID", "EXPECTED_USERNAME"):
                staging[f"TELEGRAM_PUBLISHER_{index}_{suffix}"] = production[
                    f"TELEGRAM_PUBLISHER_{index}_{suffix}"
                ]
        staging["TELEGRAM_PUBLISHER_1_BOT_TOKEN"], staging[
            "TELEGRAM_PUBLISHER_2_BOT_TOKEN"
        ] = (
            staging["TELEGRAM_PUBLISHER_2_BOT_TOKEN"],
            staging["TELEGRAM_PUBLISHER_1_BOT_TOKEN"],
        )
        partial, _ = plan.credential_status(production, staging)
        self.assertEqual(
            partial["status"],
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
        )
        self.assertIn("BLOCKED_PARTIAL_SHARED_PUBLISHER_FLEET", partial["blockers"])

        staging = self.complete_staging_values()
        staging["BOT_TOKEN"] = production["TELEGRAM_PUBLISHER_1_BOT_TOKEN"]
        staging["TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID"] = production[
            "TELEGRAM_PUBLISHER_1_EXPECTED_BOT_ID"
        ]
        staging["BOT_USERNAME"] = production[
            "TELEGRAM_PUBLISHER_1_EXPECTED_USERNAME"
        ]
        primary_reused, _ = plan.credential_status(production, staging)
        self.assertIn("BLOCKED_STAGING_PRIMARY_REUSE", primary_reused["blockers"])

    def test_shared_opt_in_is_never_ready_without_runtime_ingress_support(self):
        production = self.complete_queue_values()
        staging = self.complete_staging_values()
        production[plan.SHARED_PUBLISHER_FLEET_OPT_IN_KEY] = "true"

        status, _ = plan.credential_status(production, staging)

        self.assertEqual(
            status["status"],
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
        )
        self.assertIn(
            plan.SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER,
            status["blockers"],
        )
        self.assertFalse(status["shared_publisher_update_ownership_supported"])

    def test_provider_readback_checks_all_six_identities_and_redacts_ids(self):
        values = self.complete_queue_values()
        status, identities = plan.credential_status(
            values, self.complete_staging_values()
        )
        self.assertEqual(status["status"], "ready")

        def gateway(identity, method, payload):
            if method == "getMe":
                return {"id": identity.bot_id, "username": identity.username}
            if method == "getChat":
                return {"id": int(values["CHANNEL_ID"]), "type": "channel"}
            permissions = {
                "status": "administrator",
                "is_anonymous": False,
                "can_manage_chat": True,
                "can_post_messages": True,
                "can_edit_messages": True,
                "can_delete_messages": True,
                "can_restrict_members": True,
            }
            return permissions

        report = plan.provider_preflight(values, identities, gateway=gateway)
        self.assertEqual(report["identity_count"], 6)
        self.assertEqual(report["read_only_provider_call_count"], 18)
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn(values["CHANNEL_ID"], rendered)
        for identity in identities:
            self.assertNotIn(identity.token, rendered)
            self.assertNotIn(str(identity.bot_id), rendered)

    def test_backup_requires_both_roles_and_restore_smoke(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "backup.json"
            manifest_values = {
                "LOCAL_PROJECT_DIR": str(plan.REPO_ROOT),
                "FOREIGN_PUBLIC_DOMAIN": plan.PRODUCTION_FOREIGN_DOMAIN,
                "IRAN_HOST": "production-iran.invalid",
                "IRAN_SSH_USER": "root",
                "IRAN_SSH_PORT": "37067",
                "IRAN_PROJECT_DIR": plan.PRODUCTION_IRAN_PROJECT_DIR,
                "IRAN_APP_DOMAIN": plan.PRODUCTION_IRAN_APP_DOMAIN,
            }
            release_sha = "d" * 40
            database_name = "production_db"
            database_identities = {
                role: plan.database_identity_sha256(
                    role, database_name, str(7000 + index)
                )
                for index, role in enumerate(("foreign", "iran"))
            }
            schema_head = "abc123"
            results = []
            for role in ("foreign", "iran"):
                files = []
                pulled_files = []
                for kind in ("db", "redis", "uploads", "audit"):
                    artifact = root / f"{role}-{kind}.artifact"
                    artifact.write_bytes(
                        f"{role}-{kind}-backup".encode("utf-8")
                    )
                    artifact.chmod(0o600)
                    remote_path = f"/srv/trading-bot/backups/{artifact.name}"
                    files.append(
                        {
                            "kind": kind,
                            "path": str(artifact)
                            if role == "foreign"
                            else remote_path,
                            "sha256": hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            "bytes": artifact.stat().st_size,
                        }
                    )
                    if role == "iran":
                        pulled_files.append(
                            {
                                "remote_path": remote_path,
                                "local_path": str(artifact),
                            }
                        )
                item = {
                    "role": role,
                    "command_role": role,
                    "status": "ok",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project_label": "trading_bot" if role == "foreign" else "current",
                    "release_sha": release_sha,
                    "database_name": database_name,
                    "database_identity_sha256": database_identities[role],
                    "schema_head": schema_head,
                    "target_binding_sha256": plan.backup_target_binding_sha256(
                        role, manifest_values
                    ),
                    "files": files,
                    "restore_smoke": {"status": "passed", "table_count": 20},
                }
                if role == "iran":
                    item["pulled_files"] = pulled_files
                results.append(item)
            payload = {
                "status": "ok",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            report = plan._backup_status(
                path,
                digest,
                manifest_values=manifest_values,
                expected_release_sha=release_sha,
                expected_database_name=database_name,
                expected_database_identities=database_identities,
                expected_schema_head=schema_head,
            )
            self.assertEqual(report["restore_smoke"], "passed")
            self.assertTrue(report["fresh"])
            self.assertFalse(report["source_paths_disclosed"])

            incomplete_artifacts = copy.deepcopy(payload)
            incomplete_artifacts["results"][0]["files"] = [
                item
                for item in incomplete_artifacts["results"][0]["files"]
                if item["kind"] != "audit"
            ]
            path.write_text(json.dumps(incomplete_artifacts), encoding="utf-8")
            incomplete_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(
                plan.ReadinessBlocked, "BLOCKED_BACKUP_RESTORE_SMOKE"
            ):
                plan._backup_status(
                    path,
                    incomplete_digest,
                    manifest_values=manifest_values,
                    expected_release_sha=release_sha,
                    expected_database_name=database_name,
                    expected_database_identities=database_identities,
                    expected_schema_head=schema_head,
                )
            path.write_text(json.dumps(payload), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()

            binding_mutations = {
                "wrong-host": ("target_binding_sha256", "f" * 64),
                "wrong-project": ("project_label", "staging"),
                "wrong-release": ("release_sha", "e" * 40),
                "wrong-db": ("database_name", "another_database"),
            }
            for label, (key, value) in binding_mutations.items():
                with self.subTest(label=label):
                    mutated = copy.deepcopy(payload)
                    mutated["results"][1][key] = value
                    path.write_text(json.dumps(mutated), encoding="utf-8")
                    mutated_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    with self.assertRaisesRegex(
                        plan.ReadinessBlocked, "BLOCKED_BACKUP_RUNTIME_BINDING"
                    ):
                        plan._backup_status(
                            path,
                            mutated_digest,
                            manifest_values=manifest_values,
                            expected_release_sha=release_sha,
                            expected_database_name=database_name,
                            expected_database_identities=database_identities,
                            expected_schema_head=schema_head,
                        )
            path.write_text(json.dumps(payload), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()

            (root / "iran-db.artifact").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                plan.ReadinessBlocked, "BLOCKED_BACKUP_ARTIFACT_DRIFT"
            ):
                plan._backup_status(
                    path,
                    digest,
                    manifest_values=manifest_values,
                    expected_release_sha=release_sha,
                    expected_database_name=database_name,
                    expected_database_identities=database_identities,
                    expected_schema_head=schema_head,
                )
            (root / "iran-db.artifact").write_bytes(b"iran-db-backup")

            payload["created_at"] = (
                datetime.now(timezone.utc)
                - timedelta(seconds=plan.BACKUP_MAXIMUM_AGE_SECONDS + 1)
            ).isoformat()
            path.write_text(json.dumps(payload), encoding="utf-8")
            stale_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(plan.ReadinessBlocked, "BLOCKED_BACKUP_FRESHNESS"):
                plan._backup_status(
                    path,
                    stale_digest,
                    manifest_values=manifest_values,
                    expected_release_sha=release_sha,
                    expected_database_name=database_name,
                    expected_database_identities=database_identities,
                    expected_schema_head=schema_head,
                )

    def test_compose_role_projection_is_exact_for_legacy_and_queue(self):
        foreign = yaml.safe_load((plan.REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        iran = yaml.safe_load((plan.REPO_ROOT / "docker-compose.iran.yml").read_text(encoding="utf-8"))
        producer_flags = {
            "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
            "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
            "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
        }
        producer_routing_flags = {
            "TELEGRAM_MULTI_PUBLISHER_ENABLED": (
                "${TELEGRAM_MULTI_PUBLISHER_ENABLED:-false}"
            ),
            "TELEGRAM_B2B_DISPATCH_ENABLED": (
                "${TELEGRAM_B2B_DISPATCH_ENABLED:-false}"
            ),
        }
        always_empty = {
            *(f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN" for index in range(1, 6)),
            "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
            "TELEGRAM_MONITORING_BOT_TOKEN",
        }
        for service in ("app", "sync_worker", "migration"):
            environment = foreign["services"][service]["environment"]
            self.assertEqual(
                environment["BOT_TOKEN"], "${TELEGRAM_NON_BOT_BOT_TOKEN:-}"
            )
            for key, expected in producer_flags.items():
                self.assertEqual(environment[key], expected)
            for key, expected in producer_routing_flags.items():
                self.assertEqual(environment[key], expected)
            for key in always_empty:
                self.assertEqual(environment[key], "")
            for index in range(1, 6):
                self.assertEqual(environment[f"TELEGRAM_PUBLISHER_{index}_ENABLED"], "false")

        bot = foreign["services"]["bot"]["environment"]
        self.assertEqual(
            bot["TELEGRAM_DELIVERY_EXECUTION_OWNER"],
            "${TELEGRAM_DELIVERY_EXECUTION_OWNER:-legacy}",
        )
        self.assertNotIn("BOT_TOKEN", bot)

        for service in ("app", "sync_worker", "migration"):
            environment = iran["services"][service]["environment"]
            self.assertEqual(environment["BOT_TOKEN"], "")
            for key, expected in producer_flags.items():
                self.assertEqual(environment[key], expected)
            for key, expected in producer_routing_flags.items():
                self.assertEqual(environment[key], expected)
            for key in always_empty:
                self.assertEqual(environment[key], "")


if __name__ == "__main__":
    unittest.main()
