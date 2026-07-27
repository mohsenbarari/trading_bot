from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import run_three_site_staging_role_migration as role_migration
from scripts.run_three_site_staging_role_migration import (
    LocalRoleBackend,
    RoleMigrationError,
    _open_seed_artifact,
    _sanitized_runtime_compose,
    _secure_json,
    _verify_resume_approval,
    apply_action,
    main,
    migration_resume_subject,
)
from scripts.three_site_staging_migration_journal import MigrationJournal
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64
COMPOSE_SHA = "c" * 64
ENV_SHA = "d" * 64


class _Backend:
    def __init__(self, role: str, *, fail: str | None = None):
        self.role = role
        self.fail = fail
        self.calls: list[str] = []
        self.writer_lease_retry_flags: list[bool] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"failed {name}")

    def restore_seed(self) -> None:
        self._call("restore_seed")

    def configure_database(self) -> None:
        self._call("configure_database")

    def start_private(self) -> None:
        self._call("start_private")

    def bootstrap_writer_lease(
        self,
        *,
        request_id: str,
        retrying: bool = False,
    ) -> dict:
        self.writer_lease_retry_flags.append(retrying)
        self._call(f"bootstrap_writer_lease:{request_id}")
        return {"status": "initialized", "retrying": retrying}

    def start_workers(self) -> None:
        self._call("start_workers")

    def start_public(self) -> None:
        self._call("start_public")

    def attest_writer_state(self) -> dict:
        self._call("attest_writer_state")
        return {
            "active_site": "webapp_fi",
            "writer_epoch": 1,
            "control_state": "active",
        }


def _journal(path: Path, role: str) -> MigrationJournal:
    journal = MigrationJournal(path)
    journal.create(
        campaign_id=CAMPAIGN_ID,
        release_sha=RELEASE_SHA,
        plan_sha256=PLAN_SHA,
        role=role,
        role_compose_sha256=COMPOSE_SHA,
        role_env_sha256=ENV_SHA,
        image_inventory_sha256="e" * 64,
    )
    return journal


def _context() -> dict:
    return {
        "verified_plan": {
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "plan_sha256": PLAN_SHA,
        }
    }


def _write_evidence(
    path: Path,
    *,
    schema: str,
    role: str,
    journal: MigrationJournal,
) -> None:
    extra = {"campaign_journals_sha256": "e" * 64}
    if schema == "three-site-staging-routing-hold-v1":
        extra["routing_observation_sha256"] = "f" * 64
    elif schema == "three-site-staging-role-acceptance-v1":
        extra["acceptance_observation_sha256"] = "f" * 64
    path.write_text(
        json.dumps(
            {
                "schema": schema,
                "status": "passed",
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
                "plan_sha256": PLAN_SHA,
                "role": role,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "role_journal_state_sha256": journal.load()["state_sha256"],
                **extra,
            }
        )
    )
    path.chmod(0o600)


class ThreeSiteStagingRoleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.release_patch = patch.object(
            role_migration,
            "_verify_exact_release",
        )
        self.verify_release = self.release_patch.start()
        self.addCleanup(self.release_patch.stop)

    @staticmethod
    def _local_backend() -> LocalRoleBackend:
        backend = object.__new__(LocalRoleBackend)
        backend.prefix = ["docker", "compose"]
        backend.db_service = "webapp_fi_db"
        backend._psql = MagicMock(return_value="0")
        return backend

    def test_database_migration_quiescence_rejects_running_application_service(self):
        backend = self._local_backend()

        def fake_run(arguments, **_kwargs):
            if arguments[-2:] == ["config", "--services"]:
                return "webapp_fi_db\nwebapp_fi_redis\nwebapp_fi_api\nwebapp_fi_migration"
            if arguments[-3:] == ["ps", "-q", "webapp_fi_api"]:
                return "running-container-id"
            return ""

        with patch.object(role_migration, "_run", side_effect=fake_run):
            with self.assertRaisesRegex(RoleMigrationError, "service to be stopped"):
                backend._assert_database_migration_quiescent()
        backend._psql.assert_not_called()

    def test_target_seed_binds_signed_manifest_and_exact_object_identity(self):
        manifest = {
            "objects": [
                {
                    "kind": kind,
                    "object_key": f"fixed/{kind}.age",
                    "version_id": f"version-{kind}",
                    "ciphertext_sha256": hashlib.sha256(
                        f"cipher-{kind}".encode()
                    ).hexdigest(),
                    "ciphertext_bytes": 200,
                    "plaintext_sha256": hashlib.sha256(
                        f"plain-{kind}".encode()
                    ).hexdigest(),
                    "plaintext_bytes": 100,
                    "publication_intent": hashlib.sha256(
                        f"intent-{kind}".encode()
                    ).hexdigest(),
                }
                for kind in ("postgres", "uploads", "audit")
            ]
        }
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        document = {
            "schema": "three-site-staging-target-seed-v2",
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "target_role": "bot_fi",
            "source_role": "bot_fi",
            "seed_manifest_sha256": manifest_sha,
            "mode": "restore",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "objects": [
                {**row, "path": f"/secure/{row['kind']}"}
                for row in manifest["objects"]
            ],
        }
        for field, replacement in (
            ("object_key", "foreign/key.age"),
            ("version_id", "foreign-version"),
            ("ciphertext_sha256", "f" * 64),
            ("ciphertext_bytes", 201),
            ("plaintext_sha256", "e" * 64),
            ("plaintext_bytes", 101),
            ("publication_intent", "d" * 64),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(document)
                tampered["objects"][0][field] = replacement
                with self.assertRaisesRegex(
                    RoleMigrationError,
                    "differs from the signed manifest",
                ):
                    role_migration._target_seed(
                        tampered,
                        role="bot_fi",
                        campaign_id=CAMPAIGN_ID,
                        release_sha=RELEASE_SHA,
                        seed_manifest=manifest,
                        signed_manifest_sha256=manifest_sha,
                    )
        tampered = copy.deepcopy(document)
        tampered["seed_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(RoleMigrationError, "identity is invalid"):
            role_migration._target_seed(
                tampered,
                role="bot_fi",
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                seed_manifest=manifest,
                signed_manifest_sha256=manifest_sha,
            )

    def test_database_migration_quiescence_requires_stable_zero_clients(self):
        backend = self._local_backend()

        def fake_run(arguments, **_kwargs):
            if arguments[-2:] == ["config", "--services"]:
                return "webapp_fi_db\nwebapp_fi_redis\nwebapp_fi_api\nwebapp_fi_migration"
            return ""

        with patch.object(role_migration, "_run", side_effect=fake_run), patch.object(
            role_migration.time, "sleep"
        ):
            backend._assert_database_migration_quiescent()
        self.assertEqual(backend._psql.call_count, 3)

        backend._psql.reset_mock()
        backend._psql.return_value = "1"
        with patch.object(role_migration, "_run", side_effect=fake_run), patch.object(
            role_migration.time, "sleep"
        ):
            with self.assertRaisesRegex(RoleMigrationError, "three consecutive zero-client"):
                backend._assert_database_migration_quiescent()

    def test_database_migration_quiescence_tolerates_transient_restore_client(self):
        backend = self._local_backend()

        def fake_run(arguments, **_kwargs):
            if arguments[-2:] == ["config", "--services"]:
                return "webapp_fi_db\nwebapp_fi_redis\nwebapp_fi_api\nwebapp_fi_migration"
            return ""

        backend._psql.side_effect = ["1", "0", "0", "0"]
        with patch.object(role_migration, "_run", side_effect=fake_run), patch.object(
            role_migration.time, "sleep"
        ):
            backend._assert_database_migration_quiescent()
        self.assertEqual(backend._psql.call_count, 4)

    def test_private_start_waits_for_app_release_and_tls_health_on_every_role(self):
        for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness"):
            with self.subTest(role=role):
                backend = object.__new__(LocalRoleBackend)
                backend.role = role
                backend.prefix = ["docker", "compose"]
                backend._mutation_boundary = MagicMock()
                backend._wait_services_ready = MagicMock()
                backend._wait_infrastructure_ready = MagicMock()
                with patch.object(role_migration, "_run", return_value=""):
                    backend.start_private()
                backend._wait_services_ready.assert_called_once_with(
                    role_migration.ROLE_PRIVATE[role][:-1]
                )
                backend._wait_infrastructure_ready.assert_called_once_with(
                    role_migration.ROLE_PRIVATE[role][-1:]
                )

    def test_public_start_brings_redis_up_before_application_services(self):
        for role in ("bot_fi", "webapp_fi", "webapp_ir"):
            with self.subTest(role=role):
                backend = object.__new__(LocalRoleBackend)
                backend.role = role
                backend.prefix = ["docker", "compose"]
                backend._mutation_boundary = MagicMock()
                backend._wait_services_ready = MagicMock()
                backend._wait_infrastructure_ready = MagicMock()
                calls: list[list[str]] = []

                def fake_run(arguments, **_kwargs):
                    calls.append(arguments)
                    return ""

                with patch.object(role_migration, "_run", side_effect=fake_run):
                    backend.start_public()
                self.assertEqual(
                    [arguments[-1] for arguments in calls],
                    list(role_migration.ROLE_PUBLIC[role]),
                )
                self.assertTrue(calls[0][-1].endswith("_redis"))
                backend._wait_services_ready.assert_called_once_with(
                    role_migration.ROLE_PUBLIC[role][1:]
                )
                backend._wait_infrastructure_ready.assert_called_once_with(
                    role_migration.ROLE_PUBLIC[role][:1]
                )

    def test_service_start_retries_a_transient_compose_failure(self):
        backend = object.__new__(LocalRoleBackend)
        backend.prefix = ["docker", "compose"]
        backend._mutation_boundary = MagicMock()
        with patch.object(
            role_migration,
            "_run",
            side_effect=[role_migration.RoleMigrationError("transient"), ""],
        ) as run, patch.object(role_migration.time, "sleep") as sleep:
            backend._start_services(("webapp_ir_api",))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(backend._mutation_boundary.call_count, 2)
        for invocation in run.call_args_list:
            arguments = invocation.args[0]
            self.assertIn("--no-build", arguments)
            self.assertEqual(arguments[arguments.index("--pull") + 1], "never")
        sleep.assert_called_once_with(2)

    def test_sanitized_runtime_compose_pins_images_and_disables_build_pull(self):
        reference = f"example/app:{RELEASE_SHA}"
        image_id = "sha256:" + "1" * 64
        rendered = _sanitized_runtime_compose(
            (
                "services:\n"
                "  api:\n"
                "    image: ${APP_IMAGE:?required}\n"
                "    build: .\n"
            ).encode(),
            env={"APP_IMAGE": reference},
            image_inventory={
                "images": [
                    {
                        "reference": reference,
                        "image_id": image_id,
                    }
                ]
            },
        )
        document = role_migration.yaml.safe_load(rendered)
        self.assertEqual(document["services"]["api"]["image"], image_id)
        self.assertEqual(document["services"]["api"]["pull_policy"], "never")
        self.assertNotIn("build", document["services"]["api"])

    def test_pinned_seed_fd_survives_path_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private"
            root.mkdir(mode=0o700)
            path = root / "postgres.dump"
            payload = b"original signed database backup"
            path.write_bytes(payload)
            path.chmod(0o600)
            item = {
                "kind": "postgres",
                "path": str(path),
                "plaintext_sha256": hashlib.sha256(payload).hexdigest(),
                "plaintext_bytes": len(payload),
            }
            descriptor, metadata = _open_seed_artifact(item)
            try:
                path.rename(root / "original.dump")
                path.write_bytes(b"replacement")
                path.chmod(0o600)
                backend = object.__new__(LocalRoleBackend)
                backend._seed_handles = {
                    "postgres": (descriptor, metadata, item)
                }
                with backend._seed_source("postgres") as source:
                    self.assertEqual(source.read(), payload)
            finally:
                os.close(descriptor)

    def test_live_image_attestation_rejects_store_drift(self):
        backend = object.__new__(LocalRoleBackend)
        image_id = "sha256:" + "1" * 64
        raw = {
            "Id": image_id,
            "RepoDigests": ["example/app@sha256:" + "2" * 64],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": RELEASE_SHA,
                }
            },
        }
        backend.context = {
            "image_inventory": {
                "images": [
                    {
                        "reference": f"example/app:{RELEASE_SHA}",
                        "image_id": image_id,
                        "repo_digests": raw["RepoDigests"],
                        "release_label": RELEASE_SHA,
                        "content_descriptor": {"descriptor": "signed"},
                        "content_identity": "3" * 64,
                    }
                ]
            }
        }
        with patch.object(
            role_migration,
            "image_content_descriptor",
            return_value=({"descriptor": "signed"}, "3" * 64),
        ), patch.object(
            role_migration,
            "_run",
            return_value=json.dumps([raw]),
        ):
            backend._attest_images()
            drifted = copy.deepcopy(raw)
            drifted["Id"] = "sha256:" + "4" * 64
            with patch.object(
                role_migration,
                "_run",
                return_value=json.dumps([drifted]),
            ):
                with self.assertRaisesRegex(RoleMigrationError, "differs"):
                    backend._attest_images()

    def test_resume_approval_is_bound_to_checkpoint_and_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            state = _journal(Path(directory) / "role.json", "bot_fi").load()
            now = datetime.now(timezone.utc).replace(microsecond=0)
            enrollment = create_enrollment(
                operator="person-1",
                password="test migration resume passphrase",
                now=now,
                scrypt_n=2**14,
            )
            subject = migration_resume_subject(state, action="restore-seed")
            token, _issuer_state, _audit = authenticate_and_issue(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password="test migration resume passphrase",
                totp=totp_code(enrollment.totp_secret, at=now)[1],
                recovery_code=None,
                action="approve_migration_resume",
                environment="staging",
                subject=subject,
                ttl_seconds=600,
                now=now,
            )
            verified = _verify_resume_approval(
                token,
                approval_policy=enrollment.policy_payload,
                state=state,
                action="restore-seed",
            )
            self.assertEqual(
                verified["checkpoint_state_sha256"],
                state["state_sha256"],
            )
            with self.assertRaisesRegex(RoleMigrationError, "checkpoint/action"):
                migration_resume_subject(
                    state,
                    action="configure-database",
                )
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.begin_phase("seed_restored")
            journal.complete_phase("seed_restored")
            advanced_state = journal.load()
            with self.assertRaisesRegex(RoleMigrationError, "resume approval"):
                _verify_resume_approval(
                    token,
                    approval_policy=enrollment.policy_payload,
                    state=advanced_state,
                    action="configure-database",
                )

    def test_writer_lease_bootstrap_is_image_guarded_and_no_pull(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "webapp_fi"
        backend.prefix = ["docker", "compose"]
        backend.context = {
            "verified_plan": {
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
            }
        }
        backend.project_name = "campaign-webapp-fi"
        backend._runtime_service_images = {
            "webapp_fi_writer_control": "sha256:" + "1" * 64,
        }
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
            "campaign-webapp-fi_writer_witness_egress",
        }
        backend._mutation_boundary = MagicMock()
        renewed = {"status": "renewed"}
        renewal_container = "2" * 64
        backend._start_writer_renewal_agent = MagicMock(
            return_value=renewal_container
        )
        backend._prove_writer_renewal = MagicMock(
            return_value=renewed
        )
        request_id = "44444444-4444-4444-8444-444444444444"
        receipt = {
            "status": "initialized",
            "campaign_id": CAMPAIGN_ID,
            "request_id": request_id,
            "release_sha": RELEASE_SHA,
            "holder_site": "webapp_fi",
            "writer_epoch": 1,
            "lease_id": "lease-1",
            "witness_transition_id": "transition-1",
            "proof_hash": "5" * 64,
            "expires_at": (
                datetime.now(timezone.utc)
                + role_migration.timedelta(minutes=3)
            ).isoformat(),
        }
        def fake_run(arguments, **_kwargs):
            if arguments[:3] == [role_migration.DOCKER, "ps", "-a"]:
                return ""
            return json.dumps(receipt)

        with patch.object(role_migration, "_run", side_effect=fake_run) as run:
            self.assertEqual(
                backend.bootstrap_writer_lease(request_id=request_id),
                renewed,
            )
        backend._mutation_boundary.assert_called_once_with()
        arguments = next(
            call.args[0]
            for call in run.call_args_list
            if "run" in call.args[0]
        )
        self.assertEqual(arguments[arguments.index("--pull") + 1], "never")
        self.assertNotIn("--build", arguments)
        self.assertIn("webapp_fi_writer_control", arguments)
        self.assertEqual(
            arguments[arguments.index("--request-id") + 1],
            request_id,
        )
        backend._start_writer_renewal_agent.assert_called_once_with()
        backend._prove_writer_renewal.assert_called_once_with(
            initial_proof_hash=receipt["proof_hash"],
            container=renewal_container,
        )

    def test_writer_state_attestation_is_read_only(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "webapp_fi"
        backend.prefix = ["docker", "compose"]
        backend._writer_state_snapshot = MagicMock(
            return_value={
                "active_site": "webapp_fi",
                "writer_epoch": 1,
                "control_state": "active",
                "witness_lease_id": "lease-1",
                "witness_proof_hash": "a" * 64,
                "lease_seconds_remaining": 90,
            }
        )
        backend._wait_services_ready = MagicMock()
        backend._mutation_boundary = MagicMock()

        def fake_run(arguments, **_kwargs):
            if arguments[-3:] == [
                "ps",
                "-q",
                "webapp_fi_writer_control",
            ]:
                return "1" * 64
            if arguments[:2] == [role_migration.DOCKER, "inspect"]:
                return "true"
            raise AssertionError(arguments)

        with patch.object(role_migration, "_run", side_effect=fake_run) as run:
            result = backend.attest_writer_state()

        self.assertEqual(result["witness_lease_id"], "lease-1")
        backend._mutation_boundary.assert_not_called()
        self.assertFalse(
            any("up" in call.args[0] for call in run.call_args_list)
        )

    def test_writer_bootstrap_reconciles_only_exact_created_residue(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "webapp_fi"
        backend.prefix = ["docker", "compose"]
        backend.context = {
            "verified_plan": {
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
            }
        }
        backend.project_name = "campaign-webapp-fi"
        image_id = "sha256:" + "1" * 64
        backend._runtime_service_images = {
            "webapp_fi_writer_control": image_id,
        }
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
            "campaign-webapp-fi_writer_witness_egress",
        }
        backend._mutation_boundary = MagicMock()
        renewal_container = "2" * 64
        backend._start_writer_renewal_agent = MagicMock(
            return_value=renewal_container
        )
        backend._prove_writer_renewal = MagicMock(
            return_value={"status": "renewed"}
        )
        request_id = "77777777-7777-4777-8777-777777777777"
        required = (
            f"bootstrap-writer:{CAMPAIGN_ID}:{request_id}:{RELEASE_SHA}"
        )
        command = [
            "python",
            "scripts/bootstrap_three_site_staging_writer_lease.py",
            "--campaign-id",
            CAMPAIGN_ID,
            "--request-id",
            request_id,
            "--expected-release-sha",
            RELEASE_SHA,
            "--apply",
            "--confirm",
            required,
        ]
        container_name = (
            "ts-writer-bootstrap-"
            + hashlib.sha256(
                f"{CAMPAIGN_ID}:{request_id}".encode()
            ).hexdigest()[:24]
        )
        container_id = "8" * 64
        raw_container = {
            "Id": container_id,
            "Name": f"/{container_name}",
            "Image": image_id,
            "Config": {
                "Image": image_id,
                "Cmd": command,
                "Labels": {
                    "com.docker.compose.project": backend.project_name,
                    "com.docker.compose.service":
                    "webapp_fi_writer_control",
                    "com.docker.compose.oneoff": "True",
                },
            },
            "HostConfig": {
                "AutoRemove": True,
                "Privileged": False,
                "PortBindings": {},
                "NetworkMode": "campaign-webapp-fi_webapp_fi",
            },
            "State": {"Status": "created", "Running": False},
            "NetworkSettings": {
                "Networks": {
                    "campaign-webapp-fi_webapp_fi": {},
                    "campaign-webapp-fi_writer_witness_egress": {},
                }
            },
        }
        receipt = {
            "status": "initialized",
            "campaign_id": CAMPAIGN_ID,
            "request_id": request_id,
            "release_sha": RELEASE_SHA,
            "holder_site": "webapp_fi",
            "writer_epoch": 1,
            "lease_id": "lease-1",
            "witness_transition_id": "transition-1",
            "proof_hash": "9" * 64,
            "expires_at": (
                datetime.now(timezone.utc)
                + role_migration.timedelta(minutes=3)
            ).isoformat(),
        }
        removed = False

        def fake_run(arguments, **_kwargs):
            nonlocal removed
            if arguments[:3] == [role_migration.DOCKER, "ps", "-a"]:
                return "" if removed else container_id
            if arguments[:3] == [
                role_migration.DOCKER,
                "container",
                "inspect",
            ]:
                return json.dumps([raw_container])
            if arguments[:2] == [role_migration.DOCKER, "rm"]:
                removed = True
                return container_id
            if "run" in arguments:
                return json.dumps(receipt)
            raise AssertionError(arguments)

        with patch.object(
            role_migration,
            "_run",
            side_effect=fake_run,
        ) as run:
            self.assertEqual(
                backend.bootstrap_writer_lease(request_id=request_id),
                {"status": "renewed"},
            )
        self.assertTrue(
            any(
                call.args[0][:4]
                == [role_migration.DOCKER, "rm", "-f", "-v"]
                for call in run.call_args_list
            )
        )
        self.assertEqual(backend._mutation_boundary.call_count, 2)

    def test_writer_renewal_agent_starts_without_pull_build_or_recreate(self):
        backend = object.__new__(LocalRoleBackend)
        backend.prefix = ["docker", "compose"]
        backend.project_name = "campaign-webapp-fi"
        image_id = "sha256:" + "1" * 64
        backend._runtime_service_images = {
            "webapp_fi_writer_control": image_id,
        }
        backend._writer_runtime_command = (
            "python",
            "-m",
            "scripts.run_writer_control_agent",
        )
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
            "campaign-webapp-fi_writer_witness_egress",
        }
        backend._mutation_boundary = MagicMock()
        backend._wait_services_ready = MagicMock()
        container = "3" * 64
        config_hash = "4" * 64
        started = False
        raw_container = {
            "Id": container,
            "Image": image_id,
            "Config": {
                "Image": image_id,
                "Cmd": list(backend._writer_runtime_command),
                "Labels": {
                    "com.docker.compose.project": backend.project_name,
                    "com.docker.compose.service":
                    "webapp_fi_writer_control",
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.config-hash": config_hash,
                },
            },
            "HostConfig": {
                "AutoRemove": False,
                "Privileged": False,
                "PortBindings": {},
                "NetworkMode": "campaign-webapp-fi_webapp_fi",
            },
            "State": {"Status": "running", "Running": True},
            "NetworkSettings": {
                "Networks": {
                    "campaign-webapp-fi_webapp_fi": {},
                    "campaign-webapp-fi_writer_witness_egress": {},
                },
            },
        }

        def fake_run(arguments, **_kwargs):
            nonlocal started
            if "config" in arguments and "--hash" in arguments:
                return f"webapp_fi_writer_control {config_hash}"
            if arguments[-4:] == [
                "ps",
                "-a",
                "-q",
                "webapp_fi_writer_control",
            ]:
                return container if started else ""
            if "up" in arguments:
                started = True
                return ""
            if arguments[:3] == [
                role_migration.DOCKER,
                "container",
                "inspect",
            ]:
                return json.dumps([raw_container])
            return ""

        with patch.object(role_migration, "_run", side_effect=fake_run) as run:
            self.assertEqual(
                backend._start_writer_renewal_agent(),
                container,
            )

        start = next(
            call.args[0]
            for call in run.call_args_list
            if "up" in call.args[0]
        )
        self.assertIn("--no-build", start)
        self.assertIn("--no-recreate", start)
        self.assertEqual(start[start.index("--pull") + 1], "never")
        backend._mutation_boundary.assert_called_once_with()
        backend._wait_services_ready.assert_called_once_with(
            ("webapp_fi_writer_control",),
            stable_seconds=0,
        )

    def test_writer_renewal_agent_rejects_stale_wrong_image_before_start(self):
        backend = object.__new__(LocalRoleBackend)
        backend.prefix = ["docker", "compose"]
        backend.project_name = "campaign-webapp-fi"
        expected_image = "sha256:" + "1" * 64
        backend._runtime_service_images = {
            "webapp_fi_writer_control": expected_image,
        }
        backend._writer_runtime_command = (
            "python",
            "-m",
            "scripts.run_writer_control_agent",
        )
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
        }
        backend._mutation_boundary = MagicMock()
        backend._wait_services_ready = MagicMock()
        container = "5" * 64
        config_hash = "6" * 64
        stale = {
            "Id": container,
            "Image": "sha256:" + "9" * 64,
            "Config": {
                "Image": "sha256:" + "9" * 64,
                "Cmd": list(backend._writer_runtime_command),
                "Labels": {
                    "com.docker.compose.project": backend.project_name,
                    "com.docker.compose.service":
                    "webapp_fi_writer_control",
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.config-hash": config_hash,
                },
            },
            "HostConfig": {
                "AutoRemove": False,
                "Privileged": False,
                "PortBindings": {},
                "NetworkMode": "campaign-webapp-fi_webapp_fi",
            },
            "State": {"Status": "exited", "Running": False},
            "NetworkSettings": {
                "Networks": {
                    "campaign-webapp-fi_webapp_fi": {},
                },
            },
        }

        def fake_run(arguments, **_kwargs):
            if "config" in arguments and "--hash" in arguments:
                return f"webapp_fi_writer_control {config_hash}"
            if arguments[-4:] == [
                "ps",
                "-a",
                "-q",
                "webapp_fi_writer_control",
            ]:
                return container
            if arguments[:3] == [
                role_migration.DOCKER,
                "container",
                "inspect",
            ]:
                return json.dumps([stale])
            raise AssertionError(arguments)

        with patch.object(role_migration, "_run", side_effect=fake_run) as run:
            with self.assertRaisesRegex(
                RoleMigrationError,
                "differs from signed Compose",
            ):
                backend._start_writer_renewal_agent()

        self.assertFalse(
            any("up" in call.args[0] for call in run.call_args_list)
        )
        backend._mutation_boundary.assert_not_called()

    def test_writer_bootstrap_retry_reruns_same_request_when_import_is_absent(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "webapp_fi"
        backend.prefix = ["docker", "compose"]
        backend.context = {
            "verified_plan": {
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
            }
        }
        backend.project_name = "campaign-webapp-fi"
        backend._runtime_service_images = {
            "webapp_fi_writer_control": "sha256:" + "1" * 64,
        }
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
        }
        backend._mutation_boundary = MagicMock()
        renewal_container = "4" * 64
        backend._start_writer_renewal_agent = MagicMock(
            return_value=renewal_container
        )
        backend._prove_writer_renewal = MagicMock(
            return_value={"status": "renewed"}
        )
        backend._writer_state_snapshot = MagicMock(
            return_value={
                "active_site": "webapp_fi",
                "writer_epoch": 1,
                "control_state": "active",
                "witness_lease_id": None,
                "witness_proof_hash": None,
                "witness_lease_expires_at": None,
                "lease_seconds_remaining": None,
            }
        )
        request_id = "99999999-9999-4999-8999-999999999999"
        receipt = {
            "status": "initialized",
            "campaign_id": CAMPAIGN_ID,
            "request_id": request_id,
            "release_sha": RELEASE_SHA,
            "holder_site": "webapp_fi",
            "writer_epoch": 1,
            "lease_id": "lease-1",
            "witness_transition_id": "transition-1",
            "proof_hash": "5" * 64,
            "expires_at": (
                datetime.now(timezone.utc)
                + role_migration.timedelta(minutes=3)
            ).isoformat(),
        }
        bootstrap_commands = []

        def fake_run(arguments, **_kwargs):
            if arguments[:3] == [role_migration.DOCKER, "ps", "-a"]:
                return ""
            if "run" in arguments:
                bootstrap_commands.append(arguments)
                if len(bootstrap_commands) == 1:
                    raise RoleMigrationError(
                        "simulated failure before local lease import"
                    )
                return json.dumps(receipt)
            raise AssertionError(arguments)

        with patch.object(role_migration, "_run", side_effect=fake_run):
            with self.assertRaisesRegex(
                RoleMigrationError,
                "before local lease import",
            ):
                backend.bootstrap_writer_lease(
                    request_id=request_id,
                    retrying=False,
                )
            self.assertEqual(
                backend.bootstrap_writer_lease(
                    request_id=request_id,
                    retrying=True,
                ),
                {"status": "renewed"},
            )

        self.assertEqual(len(bootstrap_commands), 2)
        self.assertEqual(bootstrap_commands[0], bootstrap_commands[1])
        self.assertEqual(
            bootstrap_commands[1][bootstrap_commands[1].index("--request-id") + 1],
            request_id,
        )
        backend._prove_writer_renewal.assert_called_once_with(
            initial_proof_hash=receipt["proof_hash"],
            container=renewal_container,
        )

    def test_writer_bootstrap_retry_survives_original_lease_expiry(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "webapp_fi"
        backend.prefix = ["docker", "compose"]
        backend.context = {
            "verified_plan": {
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
            }
        }
        backend.project_name = "campaign-webapp-fi"
        backend._runtime_service_images = {
            "webapp_fi_writer_control": "sha256:" + "1" * 64,
        }
        backend._writer_bootstrap_networks = {
            "campaign-webapp-fi_webapp_fi",
        }
        backend._mutation_boundary = MagicMock()
        renewal_container = "6" * 64
        events = []

        def start_agent():
            events.append("start-agent")
            return renewal_container

        backend._start_writer_renewal_agent = MagicMock(
            side_effect=start_agent
        )
        renewed = {
            "witness_lease_id": "lease-1",
            "witness_proof_hash": "7" * 64,
            "lease_seconds_remaining": 120,
        }
        prove_attempts = 0

        def prove_renewal(**_kwargs):
            nonlocal prove_attempts
            prove_attempts += 1
            events.append("prove-renewal")
            if prove_attempts == 1:
                raise RuntimeError("simulated kill after local lease import")
            return renewed

        backend._prove_writer_renewal = MagicMock(
            side_effect=prove_renewal
        )
        backend._writer_state_snapshot = MagicMock(
            return_value={
                "active_site": "webapp_fi",
                "writer_epoch": 1,
                "control_state": "active",
                "witness_lease_id": "lease-1",
                "witness_proof_hash": "8" * 64,
                "witness_lease_expires_at": "future-renewed-expiry",
                "lease_seconds_remaining": 120,
            }
        )
        request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        first_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        receipt = {
            "status": "initialized",
            "campaign_id": CAMPAIGN_ID,
            "request_id": request_id,
            "release_sha": RELEASE_SHA,
            "holder_site": "webapp_fi",
            "writer_epoch": 1,
            "lease_id": "lease-1",
            "witness_transition_id": "transition-1",
            "proof_hash": "5" * 64,
            "expires_at": (
                first_now + role_migration.timedelta(seconds=60)
            ).isoformat(),
        }
        bootstrap_calls = 0

        def fake_run(arguments, **_kwargs):
            nonlocal bootstrap_calls
            if arguments[:3] == [role_migration.DOCKER, "ps", "-a"]:
                return ""
            if "run" in arguments:
                bootstrap_calls += 1
                events.append("bootstrap-import")
                return json.dumps(receipt)
            raise AssertionError(arguments)

        class MovingDateTime(datetime):
            current = first_now

            @classmethod
            def now(cls, tz=None):
                value = cls.current
                return value if tz is not None else value.replace(tzinfo=None)

        with patch.object(role_migration, "_run", side_effect=fake_run), patch.object(
            role_migration,
            "datetime",
            MovingDateTime,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "after local lease import",
            ):
                backend.bootstrap_writer_lease(
                    request_id=request_id,
                    retrying=False,
                )
            MovingDateTime.current = first_now + role_migration.timedelta(
                seconds=120
            )
            self.assertEqual(
                backend.bootstrap_writer_lease(
                    request_id=request_id,
                    retrying=True,
                ),
                renewed,
            )

        self.assertEqual(bootstrap_calls, 1)
        self.assertEqual(
            events[:3],
            ["start-agent", "bootstrap-import", "prove-renewal"],
        )
        self.assertEqual(events[3:], ["start-agent", "prove-renewal"])

    def test_writer_bootstrap_execution_lock_blocks_concurrent_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "webapp_fi")
            backend = _Backend("webapp_fi")
            with journal.writer_lease_execution_lock():
                with self.assertRaisesRegex(
                    Exception,
                    "already active",
                ):
                    apply_action(
                        action="bootstrap-writer-lease",
                        journal=journal,
                        backend=backend,
                        context=_context(),
                        evidence_path=None,
                        writer_lease_request_id=(
                            "88888888-8888-4888-8888-888888888888"
                        ),
                    )
            self.assertEqual(backend.calls, [])

    def test_writer_lease_bootstrap_ambiguous_failure_retries_same_request(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "webapp_fi")
            for action in (
                "restore-seed",
                "configure-database",
                "start-private",
            ):
                apply_action(
                    action=action,
                    journal=journal,
                    backend=_Backend("webapp_fi"),
                    context=_context(),
                    evidence_path=None,
                )
            request_id = "55555555-5555-4555-8555-555555555555"
            failed = _Backend(
                "webapp_fi",
                fail=f"bootstrap_writer_lease:{request_id}",
            )
            with self.assertRaisesRegex(RuntimeError, "failed"):
                apply_action(
                    action="bootstrap-writer-lease",
                    journal=journal,
                    backend=failed,
                    context=_context(),
                    evidence_path=None,
                    writer_lease_request_id=request_id,
                )
            interrupted = journal.load()
            self.assertEqual(
                interrupted["writer_lease_request_id"],
                request_id,
            )
            retry = _Backend("webapp_fi")
            completed = apply_action(
                action="bootstrap-writer-lease",
                journal=journal,
                backend=retry,
                context=_context(),
                evidence_path=None,
                writer_lease_request_id=request_id,
            )
            self.assertIn(
                "writer_lease_bootstrapped",
                completed["completed_phases"],
            )
            self.assertEqual(
                retry.calls,
                [f"bootstrap_writer_lease:{request_id}"],
            )
            self.assertEqual(retry.writer_lease_retry_flags, [True])
            with self.assertRaisesRegex(
                RoleMigrationError,
                "request id differs",
            ):
                migration_resume_subject(
                    interrupted,
                    action="bootstrap-writer-lease",
                    writer_lease_request_id=(
                        "66666666-6666-4666-8666-666666666666"
                    ),
                )

    def test_checkpoint_change_blocks_action_before_backend_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "bot_fi")
            backend = _Backend("bot_fi")
            with self.assertRaisesRegex(RoleMigrationError, "changed after"):
                apply_action(
                    action="restore-seed",
                    journal=journal,
                    backend=backend,
                    context=_context(),
                    evidence_path=None,
                    expected_checkpoint_sha256="0" * 64,
                )
            self.assertEqual(backend.calls, [])

    def test_release_drift_after_phase_begin_blocks_backend_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "bot_fi")
            backend = _Backend("bot_fi")
            self.verify_release.side_effect = [
                None,
                RoleMigrationError("release drift"),
                RoleMigrationError("release drift"),
            ]
            with self.assertRaisesRegex(RoleMigrationError, "release drift"):
                apply_action(
                    action="restore-seed",
                    journal=journal,
                    backend=backend,
                    context=_context(),
                    evidence_path=None,
                )
            self.assertEqual(backend.calls, [])

    def test_service_readiness_reads_role_specific_runtime_release_from_container(self):
        cases = (
            ("webapp_fi", "webapp_fi_dr_receiver", "RELEASE_SHA"),
            ("witness", "witness_api", "WRITER_WITNESS_RELEASE_SHA"),
        )
        for role, service, release_key in cases:
            with self.subTest(role=role):
                backend = object.__new__(LocalRoleBackend)
                backend.role = role
                backend.prefix = ["docker", "compose"]
                backend.context = {"verified_plan": {"release_sha": RELEASE_SHA}}
                calls: list[list[str]] = []

                def fake_run(arguments, **_kwargs):
                    calls.append(arguments)
                    if arguments[-3:] == ["ps", "-q", service]:
                        return "container-id"
                    if arguments[-1] == "container-id" and "{{json .State}}" in arguments:
                        return json.dumps(
                            {"Running": True, "Health": {"Status": "healthy"}}
                        )
                    if arguments[-1] == "container-id" and "{{json .Config.Env}}" in arguments:
                        return json.dumps([f"{release_key}={RELEASE_SHA}", "TZ=UTC"])
                    raise AssertionError(arguments)

                with patch.object(role_migration, "_run", side_effect=fake_run):
                    backend._wait_services_ready((service,), stable_seconds=0)
                self.assertFalse(
                    any("exec" in arguments for arguments in calls),
                    "readiness must not import application settings inside the container",
                )

    def test_witness_readiness_rejects_missing_witness_release_identity(self):
        backend = object.__new__(LocalRoleBackend)
        backend.role = "witness"
        backend.prefix = ["docker", "compose"]
        backend.context = {"verified_plan": {"release_sha": RELEASE_SHA}}

        def fake_run(arguments, **_kwargs):
            if arguments[-3:] == ["ps", "-q", "witness_api"]:
                return "container-id"
            if "{{json .State}}" in arguments:
                return json.dumps({"Running": True, "Health": {"Status": "healthy"}})
            if "{{json .Config.Env}}" in arguments:
                return json.dumps([f"RELEASE_SHA={RELEASE_SHA}", "TZ=UTC"])
            raise AssertionError(arguments)

        with patch.object(role_migration, "_run", side_effect=fake_run):
            with self.assertRaisesRegex(RoleMigrationError, "release identity mismatch"):
                backend._wait_services_ready(("witness_api",), stable_seconds=0)

    def test_webapp_role_requires_ordered_external_barriers_and_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = _journal(root / "role.json", "webapp_fi")
            backend = _Backend("webapp_fi")
            context = _context()
            apply_action(
                action="restore-seed", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            apply_action(
                action="configure-database", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            apply_action(
                action="start-private", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            request_id = "33333333-3333-4333-8333-333333333333"
            apply_action(
                action="bootstrap-writer-lease",
                journal=journal,
                backend=backend,
                context=context,
                evidence_path=None,
                writer_lease_request_id=request_id,
            )
            apply_action(
                action="attest-writer-state", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            barrier = root / "barrier.json"
            _write_evidence(
                barrier,
                schema="three-site-staging-private-barrier-v1",
                role="webapp_fi",
                journal=journal,
            )
            apply_action(
                action="start-workers", journal=journal, backend=backend,
                context=context, evidence_path=barrier,
            )
            hold = root / "hold.json"
            _write_evidence(
                hold,
                schema="three-site-staging-routing-hold-v1",
                role="webapp_fi",
                journal=journal,
            )
            apply_action(
                action="start-public", journal=journal, backend=backend,
                context=context, evidence_path=hold,
            )
            acceptance = root / "acceptance.json"
            _write_evidence(
                acceptance,
                schema="three-site-staging-role-acceptance-v1",
                role="webapp_fi",
                journal=journal,
            )
            committed = apply_action(
                action="accept", journal=journal, backend=backend,
                context=context, evidence_path=acceptance,
            )
            self.assertEqual(committed["status"], "committed")
            self.assertEqual(
                backend.calls,
                [
                    "restore_seed", "configure_database", "start_private",
                    f"bootstrap_writer_lease:{request_id}", "attest_writer_state",
                    "start_workers", "start_public",
                ],
            )
            committed_states = {"webapp_fi": committed}
            from scripts.three_site_staging_migration_journal import ROLE_PHASES
            for other_role in ("bot_fi", "webapp_ir", "witness"):
                other = _journal(root / f"{other_role}.json", other_role)
                for phase in ROLE_PHASES[other_role]:
                    other.begin_phase(phase)
                    other.complete_phase(phase)
                committed_states[other_role] = other.commit(
                    acceptance_evidence_sha256="9" * 64
                )
            role_journals = {
                role: state["state_sha256"] for role, state in committed_states.items()
            }
            global_payload = {
                "schema": "three-site-staging-global-commit-v2",
                "status": "passed",
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
                "plan_sha256": PLAN_SHA,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "campaign_journals_sha256": hashlib.sha256(
                    json.dumps(
                        role_journals, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "role_journals": role_journals,
                "committed_role_states": committed_states,
                "all_roles_committed": True,
            }
            mixed_campaign = copy.deepcopy(global_payload)
            mixed_state = mixed_campaign["committed_role_states"]["bot_fi"]
            mixed_state["campaign_id"] = (
                "22222222-2222-4222-8222-222222222222"
            )
            mixed_state["state_sha256"] = hashlib.sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in mixed_state.items()
                        if key != "state_sha256"
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            mixed_campaign["role_journals"]["bot_fi"] = mixed_state[
                "state_sha256"
            ]
            mixed_campaign["campaign_journals_sha256"] = hashlib.sha256(
                json.dumps(
                    mixed_campaign["role_journals"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            mixed_path = root / "mixed-global-commit.json"
            mixed_path.write_text(json.dumps(mixed_campaign))
            mixed_path.chmod(0o600)
            with self.assertRaisesRegex(
                RoleMigrationError,
                "journal state/hash",
            ):
                role_migration._verify_global_commit(
                    mixed_path,
                    role="webapp_fi",
                    state=committed,
                )
            global_commit = root / "global-commit.json"
            global_commit.write_text(
                json.dumps(global_payload)
            )
            global_commit.chmod(0o600)
            self.assertEqual(
                main(
                    [
                        "finish", "--role", "webapp_fi", "--journal", str(journal.path),
                        "--evidence", str(global_commit),
                    ]
                ),
                0,
            )
            self.assertEqual(journal.load()["status"], "finished")

    def test_failed_phase_is_not_forward_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "bot_fi")
            backend = _Backend("bot_fi", fail="restore_seed")
            with self.assertRaisesRegex(RuntimeError, "failed restore_seed"):
                apply_action(
                    action="restore-seed", journal=journal, backend=backend,
                    context=_context(), evidence_path=None,
                )
            self.assertEqual(journal.load()["status"], "rollback_required")
            with self.assertRaisesRegex(Exception, "current state"):
                apply_action(
                    action="restore-seed", journal=journal, backend=_Backend("bot_fi"),
                    context=_context(), evidence_path=None,
                )

    def test_sensitive_json_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema":"one","schema":"two"}')
            path.chmod(0o600)
            with self.assertRaisesRegex(RoleMigrationError, "duplicate key"):
                _secure_json(path, label="duplicate")

    def test_sensitive_json_rejects_non_private_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "input.json"
            path.write_text('{"schema":"one"}')
            path.chmod(0o640)
            with self.assertRaisesRegex(RoleMigrationError, "mode-0600"):
                _secure_json(path, label="input")
            path.chmod(0o600)
            link = root / "link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(RoleMigrationError, "unavailable or unsafe"):
                _secure_json(link, label="input")

    def test_status_needs_only_role_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "role.json"
            _journal(path, "witness")
            self.assertEqual(
                main(["status", "--role", "witness", "--journal", str(path)]),
                0,
            )

    def test_resume_subject_cli_writes_once_for_exact_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = _journal(root / "role.json", "bot_fi")
            output = root / "resume-subject.json"
            arguments = [
                "resume-subject",
                "--role",
                "bot_fi",
                "--journal",
                str(journal.path),
                "--next-action",
                "restore-seed",
                "--subject-output",
                str(output),
            ]
            mismatched = [
                *arguments,
                "--writer-lease-request-id",
                "33333333-3333-4333-8333-333333333333",
            ]
            self.assertEqual(main(mismatched), 1)
            self.assertFalse(output.exists())
            self.assertEqual(main(arguments), 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads(output.read_text()),
                migration_resume_subject(
                    journal.load(),
                    action="restore-seed",
                ),
            )
            self.assertEqual(main(arguments), 1)

    def test_rollback_rejects_bundle_not_bound_to_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "role.json"
            _journal(path, "witness")
            compose = root / "compose.yml"
            compose.write_text("name: different\n")
            compose.chmod(0o640)
            env = root / "role.env"
            env.write_text("WITNESS_POSTGRES_USER=witness\nWITNESS_POSTGRES_DB=witness\n")
            env.chmod(0o600)
            self.assertNotEqual(hashlib.sha256(compose.read_bytes()).hexdigest(), COMPOSE_SHA)
            self.assertEqual(
                main(
                    [
                        "rollback", "--role", "witness", "--journal", str(path),
                        "--role-compose", str(compose), "--env-file", str(env),
                    ]
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
