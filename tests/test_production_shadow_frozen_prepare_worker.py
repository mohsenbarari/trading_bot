from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import tempfile
import textwrap
import unittest
from unittest import mock

from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import production_shadow_frozen_prepare_worker as WORKER


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
RELEASE_SHA = "1" * 40
TREE_SHA = "2" * 40
CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class Authority:
    def __init__(self) -> None:
        self.challenges: list[dict[str, object]] = []
        self.mutate = None

    def __call__(
        self,
        challenge: dict[str, object],
        boundary: str,
    ) -> dict[str, object]:
        self.challenges.append(dict(challenge))
        response = dict(challenge)
        response.update(
            {
                "schema": WORKER.AUTHORITY_RESPONSE_SCHEMA,
                "status": "verified-live",
                "challenge_sha256": hashlib.sha256(
                    canonical(challenge)
                ).hexdigest(),
                "response_nonce": hashlib.sha256(
                    f"response:{len(self.challenges)}".encode("ascii")
                ).hexdigest(),
                "controller_lock_held": True,
                "controller_authoritative": True,
                "journal_status": "phase_started",
                "journal_state_sha256": SHA_B,
                "journal_event_tail_sha256": SHA_C,
                "journal_event_count": 100 + len(self.challenges),
                "completed_phases": list(
                    CONTROLLER.PHASES[
                        : CONTROLLER.PHASES.index(
                            str(challenge["phase"])
                        )
                    ]
                ),
                "started_phase": challenge["phase"],
                "business_write_allowed": False,
                "current_mutation_allowed": False,
                "legacy_mutation_allowed": False,
                "production_traffic_mutation_allowed": False,
                "external_network_payload_allowed": False,
                "object_storage_mutation_allowed": False,
            }
        )
        if self.mutate is not None:
            self.mutate(response)
        return response


def role_details(*, phase: str, satisfied: bool) -> dict[str, object]:
    post = phase == "shadow_roles_post_migration"
    return {
        "expected_roles": ["webapp_fi_app"],
        "observed_roles": ["webapp_fi_app"] if satisfied else [],
        "missing_role_count": 0 if satisfied else 1,
        "closed_role_count": 1 if satisfied else 0,
        "excessive_grant_count": 0,
        "explicit_grant_count": 12 if post and satisfied else 0,
        "unsafe_public_privilege_count": 0,
        "exact_release_grant_policy_verified": post and satisfied,
        "role_state_sha256": SHA_B,
        "grant_set_sha256": SHA_C,
        "least_privilege_role_set_verified": satisfied,
        "schema_fingerprint_sha256": (
            SHA_D if phase == "shadow_roles_post_migration" else None
        ),
    }


def migration_details(*, satisfied: bool) -> dict[str, object]:
    return {
        "reviewed_concurrent_indexes": ["ix_reviewed"],
        "invalid_unready_indexes": [] if satisfied else ["ix_reviewed"],
        "invalid_unready_index_count": 0 if satisfied else 1,
        "off_chain_revision_count": 0,
        "migration_corridor": ["source", "target"],
        "migration_corridor_sha256": SHA_B,
        "schema_fingerprint_sha256": SHA_D if satisfied else None,
        "database_row_count": 10 if satisfied else None,
        "database_table_count": 2 if satisfied else None,
    }


def fence_details(
    *,
    satisfied: bool,
    writer: bool | None,
) -> dict[str, object]:
    return {
        "database_fenced": satisfied,
        "database_event_fence_verified": satisfied,
        "writer_trigger_count": 2,
        "enabled_writer_trigger_count": 2 if satisfied else 1,
        "writer_fenced": writer,
        "unfenced_writer_count": 0 if satisfied and writer is not False else 1,
        "fence_configuration_sha256": SHA_B,
        "schema_fingerprint_sha256": SHA_D,
        "database_row_count": 10,
        "database_table_count": 2,
        "least_privilege_role_set_verified": satisfied,
        "exact_release_grant_policy_verified": satisfied,
        "grant_set_sha256": SHA_C,
        "role_state_sha256": SHA_B,
    }


def writer_trigger_row(
    table: str,
    *,
    enabled: str = "A",
    owner: str = "postgres",
    body_sha256: str = WORKER.WEB_GRANTS.WRITER_FUNCTION_PROSRC_SHA256,
) -> str:
    return "\t".join(
        (
            table,
            enabled,
            "31",
            "true",
            "0",
            "",
            "9000",
            "public",
            "trading_bot_enforce_writer_term",
            "",
            owner,
            "postgres",
            "plpgsql",
            "f",
            "v",
            "u",
            "false",
            "false",
            "0",
            "pg_catalog",
            "trigger",
            "true",
            str(WORKER.WEB_GRANTS.WRITER_FUNCTION_PROSRC_BYTES),
            body_sha256,
            "1",
            "search_path=public, pg_temp",
        )
    )


def observation(
    context: WORKER.LoadedRequest,
    step: str,
    *,
    satisfied: bool,
) -> dict[str, object]:
    phase = str(context.document["phase"])
    role = str(context.document["role"])
    current = (
        str(context.manifest.target_migration_revision)
        if satisfied or phase != "shadow_migrate"
        else str(context.manifest.source_database.alembic_revision)
    )
    if step in {"roles-pre", "roles-post"}:
        details = role_details(phase=phase, satisfied=satisfied)
    elif step == "migrate":
        details = migration_details(satisfied=satisfied)
    else:
        writer = (
            satisfied
            if role == "webapp_ir"
            else None
        )
        details = fence_details(satisfied=satisfied, writer=writer)
    return {
        "phase": phase,
        "step": step,
        "role": role,
        "source_revision": context.manifest.source_database.alembic_revision,
        "target_revision": context.manifest.target_migration_revision,
        "current_revision": current,
        "database_container_count": 1,
        "oneoff_container_count": 0,
        "network_present": True,
        "named_volume_count": 0,
        "satisfied": satisfied,
        "details": details,
        "business_write_observed": False,
        "public_or_private_app_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "external_network_contacted": False,
        "ssh_contacted": False,
        "object_storage_contacted": False,
    }


def execution(
    context: WORKER.LoadedRequest,
    step: str,
    *,
    invoked: bool = True,
) -> dict[str, object]:
    service = next(
        row[1] for row in context.steps if row[0] == step
    )
    return {
        "step": step,
        "service": service,
        "command_invoked": invoked,
        "output_sha256": SHA_E if invoked else None,
        "output_bytes": 12 if invoked else 0,
        "repaired_concurrent_indexes": [],
        "pull_performed": False,
        "build_performed": False,
        "compose_down_performed": False,
        "volume_mutated": False,
        "public_or_private_app_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "external_network_contacted": False,
        "ssh_contacted": False,
        "object_storage_contacted": False,
    }


class FakeBackend:
    def __init__(
        self,
        context: WORKER.LoadedRequest,
        *,
        satisfied: bool = False,
        crash_after_run: bool = False,
        fail_without_change: bool = False,
    ) -> None:
        self.context = context
        self.state = {
            step: satisfied for step, _service, _timeout in context.steps
        }
        self.crash_after_run = crash_after_run
        self.fail_without_change = fail_without_change
        self.observe_calls: list[str] = []
        self.run_calls: list[str] = []
        self.cancel_calls: list[tuple[str, int, str]] = []

    def observe(self, step: str) -> dict[str, object]:
        self.observe_calls.append(step)
        return observation(
            self.context,
            step,
            satisfied=self.state[step],
        )

    def run_step(
        self,
        step: str,
        *,
        attempt: int,
        started_event_sha256: str,
    ) -> dict[str, object]:
        self.assert_run_identity = (
            attempt,
            started_event_sha256,
        )
        self.run_calls.append(step)
        if self.fail_without_change:
            raise RuntimeError("simulated command loss")
        self.state[step] = True
        if self.crash_after_run:
            raise RuntimeError("simulated lost output")
        return execution(self.context, step)

    def cancel_active_oneoff(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
    ) -> dict[str, object]:
        self.cancel_calls.append(
            (step, attempt, started_event_sha256)
        )
        return {
            "residue_count": 0,
            "residue_identity_sha256": None,
            "removed_count": 0,
            "persistent_volume_removed": False,
            "generation_data_mutated": False,
        }


class FrozenPrepareWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def context(
        self,
        phase: str,
        role: str,
        *,
        source_revision: str = "source",
        target_revision: str = "target",
    ) -> WORKER.LoadedRequest:
        generation = self.root / "generation" / role.replace("_", "-")
        generation.mkdir(parents=True, mode=0o700)
        for path in (
            self.root / "generation",
            generation,
        ):
            path.chmod(0o700)
        output = generation / "prepare-phases" / phase
        document = {
            "schema": WORKER.REQUEST_SCHEMA,
            "status": "authorized-input",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "role": role,
            "phase": phase,
            "operation": WORKER.PHASE_OPERATIONS[phase],
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
            "controller_manifest_path": "/root/controller.json",
            "controller_manifest_sha256": SHA_A,
            "plan_sha256": SHA_B,
            "role_manifest_path": "/root/role.json",
            "role_manifest_sha256": SHA_C,
            "restore_completion_path": "/root/completion.json",
            "restore_completion_sha256": SHA_D,
            "restore_phase_evidence_path": "/root/restore-evidence.json",
            "restore_phase_evidence_sha256": SHA_E,
            "restore_generation_sha256": "3" * 64,
            "prepare_worker_path": "/srv/release/worker.py",
            "prepare_worker_sha256": "4" * 64,
            "prior_result_path": None,
            "prior_result_sha256": None,
            "output_root": str(output),
            "constraints": {
                field: True for field in WORKER.CONSTRAINT_FIELDS
            },
        }
        manifest = SimpleNamespace(
            role=role,
            source_database=SimpleNamespace(
                alembic_revision=source_revision
            ),
            target_migration_revision=target_revision,
            paths=SimpleNamespace(
                secret_generation_root=generation,
                project_name=f"project-{role}",
                release_root=generation / "release",
            ),
            environment_path=generation / "runtime.env",
            prepare_compose_path=generation / "prepare.yml",
            restore_generation_sha256=document[
                "restore_generation_sha256"
            ],
        )
        prior_result = None
        if phase == "shadow_roles_post_migration":
            prior_result = {
                "semantic": {
                    "schema_fingerprint_sha256": SHA_D,
                    "schema_fingerprint_algorithm": (
                        WORKER.SCHEMA_FINGERPRINT_ALGORITHM
                    ),
                }
            }
        elif phase == "shadow_fence":
            prior_result = {
                "semantic": {
                    "migrated_schema_fingerprint_sha256": SHA_D,
                    "post_migration_grant_set_sha256": SHA_C,
                    "role_state_sha256": SHA_B,
                    "schema_fingerprint_algorithm": (
                        WORKER.SCHEMA_FINGERPRINT_ALGORITHM
                    ),
                }
            }
        return WORKER.LoadedRequest(
            document=document,
            sha256="5" * 64,
            path=generation / "request.json",
            manifest=manifest,
            controller_manifest={},
            plan={},
            restore_completion={},
            restore_phase_evidence={},
            prior_result=prior_result,
            output_root=output,
            steps=WORKER.STEP_SERVICES[(phase, role)],
        )

    def sql_backend(self, runner: object) -> tuple[
        WORKER.LoadedRequest,
        WORKER.LocalDockerPrepareBackend,
    ]:
        context = self.context("shadow_migrate", "bot_fi")
        manifest = context.manifest
        manifest.operation_id = OPERATION_ID
        manifest.postgres_image_id = f"sha256:{'8' * 64}"
        manifest.role_compose_path = (
            manifest.paths.release_root / "compose.restore.yml"
        )
        manifest.paths.restore_input_root = (
            manifest.paths.secret_generation_root / "restore-input"
        )
        manifest.paths.uploads = (
            manifest.paths.secret_generation_root / "uploads"
        )
        manifest.paths.audit = (
            manifest.paths.secret_generation_root / "audit"
        )
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.context = context
        backend.manifest = manifest
        backend.runner = runner
        backend.corridor = ("source", "target")
        backend.concurrent_indexes = ("ix_reviewed",)
        backend._sql_scope = None
        backend._sql_contract_cache = None
        backend._prepare_contract_cache = {}
        return context, backend

    def execute(
        self,
        context: WORKER.LoadedRequest,
        backend: FakeBackend,
        authority: Authority | None = None,
    ) -> dict[str, object]:
        authority = authority or Authority()
        with (
            mock.patch.object(WORKER, "load_request", return_value=context),
            mock.patch.object(
                WORKER,
                "LocalDockerPrepareBackend",
                return_value=backend,
            ),
        ):
            read_fd, write_fd = os.pipe()
            try:
                return WORKER.execute(
                    request_path=Path("/unused"),
                    apply=True,
                    confirm=WORKER.confirmation_phrase(context),
                    authority_verifier=authority,
                    control_fd=read_fd,
                )
            finally:
                os.close(read_fd)
                os.close(write_fd)

    def request_fixture(self) -> tuple[Path, dict[str, object], object]:
        role = "webapp_fi"
        phase = "shadow_roles_pre_migration"
        generation = self.root / "installed-generation" / "webapp-fi"
        requests = generation / "prepare-requests"
        requests.mkdir(parents=True, mode=0o700)
        for path in (
            self.root / "installed-generation",
            generation,
            requests,
        ):
            path.chmod(0o700)
        controller_path = self.root / "controller.json"
        completion_path = self.root / "completion.json"
        evidence_path = self.root / "restore-evidence.json"
        role_manifest_path = generation / "role-manifest.json"
        document: dict[str, object] = {
            "schema": WORKER.REQUEST_SCHEMA,
            "status": "authorized-input",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "role": role,
            "phase": phase,
            "operation": WORKER.PHASE_OPERATIONS[phase],
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
            "controller_manifest_path": str(controller_path),
            "controller_manifest_sha256": SHA_A,
            "plan_sha256": SHA_B,
            "role_manifest_path": str(role_manifest_path),
            "role_manifest_sha256": SHA_C,
            "restore_completion_path": str(completion_path),
            "restore_completion_sha256": SHA_D,
            "restore_phase_evidence_path": str(evidence_path),
            "restore_phase_evidence_sha256": SHA_E,
            "restore_generation_sha256": "3" * 64,
            "prepare_worker_path": str(
                generation
                / "release"
                / "scripts"
                / "production_shadow_frozen_prepare_worker.py"
            ),
            "prepare_worker_sha256": "4" * 64,
            "prior_result_path": None,
            "prior_result_sha256": None,
            "output_root": str(
                generation / "prepare-phases" / phase
            ),
            "constraints": {
                field: True for field in WORKER.CONSTRAINT_FIELDS
            },
        }
        payload = canonical(document) + b"\n"
        digest = hashlib.sha256(payload).hexdigest()
        request_path = requests / f"{phase}-{digest}.json"
        request_path.write_bytes(payload)
        request_path.chmod(0o600)
        manifest = SimpleNamespace(
            canonical_sha256=SHA_C,
            operation_id=OPERATION_ID,
            role=role,
            release_sha=RELEASE_SHA,
            release_tree_sha=TREE_SHA,
            restore_generation_sha256="3" * 64,
            controller_manifest_sha256=SHA_A,
            paths=SimpleNamespace(
                secret_generation_root=generation,
                release_root=generation / "release",
            ),
        )
        return request_path, document, manifest

    def test_exact_controller_phase_and_role_contract(self) -> None:
        self.assertEqual(
            WORKER.PHASES,
            (
                "shadow_roles_pre_migration",
                "shadow_migrate",
                "shadow_roles_post_migration",
                "shadow_fence",
            ),
        )
        self.assertEqual(
            WORKER.PHASE_ROLES["shadow_roles_pre_migration"],
            ("webapp_fi", "webapp_ir"),
        )
        self.assertEqual(
            WORKER.STEP_SERVICES[("shadow_fence", "bot_fi")],
            (("database-fence", "bot_fi_db_fencing", 900),),
        )
        self.assertEqual(
            {
                key: WORKER.STEP_SERVICES[key]
                for key in (
                    ("shadow_roles_post_migration", "bot_fi"),
                    ("shadow_roles_post_migration", "webapp_fi"),
                    ("shadow_roles_post_migration", "webapp_ir"),
                    ("shadow_fence", "bot_fi"),
                    ("shadow_fence", "webapp_fi"),
                    ("shadow_fence", "webapp_ir"),
                )
            },
            {
                ("shadow_roles_post_migration", "bot_fi"): (
                    ("roles-post", "bot_fi_db_roles", 900),
                ),
                ("shadow_roles_post_migration", "webapp_fi"): (
                    (
                        "roles-post",
                        "webapp_fi_db_roles_post_migration",
                        900,
                    ),
                ),
                ("shadow_roles_post_migration", "webapp_ir"): (
                    (
                        "roles-post",
                        "webapp_ir_db_roles_post_migration",
                        900,
                    ),
                ),
                ("shadow_fence", "bot_fi"): (
                    ("database-fence", "bot_fi_db_fencing", 900),
                ),
                ("shadow_fence", "webapp_fi"): (
                    ("database-fence", "webapp_fi_db_fencing", 900),
                ),
                ("shadow_fence", "webapp_ir"): (
                    ("database-fence", "webapp_ir_db_fencing", 900),
                    ("writer-fence", "webapp_ir_writer_fence", 900),
                ),
            },
        )
        self.assertEqual(WORKER.PHASE_EXECUTION_BLOCKERS, {})
        def webapp_phase_command(
            site: str,
            *,
            phase: str,
            confirmation: str,
        ) -> tuple[str, ...]:
            return (
                "python",
                "scripts/activate_three_site_database_fencing.py",
                "--phase",
                phase,
                "--site",
                site,
                "--application-role",
                f"{site}_app",
                "--projection-role",
                f"{site}_projection",
                "--receiver-role",
                f"{site}_receiver",
                "--delivery-role",
                f"{site}_delivery",
                "--blob-role",
                f"{site}_blob",
                "--effect-role",
                f"{site}_effect",
                "--control-role",
                f"{site}_control",
                "--observer-role",
                f"{site}_observer",
                "--operator",
                "production-shadow-compose",
                "--apply",
                "--confirm",
                confirmation,
            )

        expected_commands = {
            "bot_fi_migration": ("python", "manage.py"),
            "bot_fi_db_roles": (
                "python",
                "scripts/provision_bot_database_roles.py",
                "--phase",
                "roles-grants",
                "--role-prefix",
                "bot_fi",
                "--apply",
                "--confirm",
                "APPLY-BOT-DATABASE-ROLE-GRANTS",
            ),
            "bot_fi_db_fencing": (
                "python",
                "scripts/provision_bot_database_roles.py",
                "--phase",
                "fence",
                "--role-prefix",
                "bot_fi",
                "--apply",
                "--confirm",
                "ENABLE-BOT-DATABASE-FENCING",
            ),
            "webapp_fi_db_roles": (
                "python",
                "scripts/provision_three_site_database_roles.py",
                "--role-prefix",
                "webapp_fi",
            ),
            "webapp_fi_migration": ("python", "manage.py"),
            "webapp_fi_db_roles_post_migration": webapp_phase_command(
                "webapp_fi",
                phase="grants",
                confirmation="APPLY-THREE-SITE-DATABASE-GRANTS",
            ),
            "webapp_fi_db_fencing": webapp_phase_command(
                "webapp_fi",
                phase="fence",
                confirmation="ENABLE-THREE-SITE-DATABASE-FENCING",
            ),
            "webapp_ir_db_roles": (
                "python",
                "scripts/provision_three_site_database_roles.py",
                "--role-prefix",
                "webapp_ir",
            ),
            "webapp_ir_migration": ("python", "manage.py"),
            "webapp_ir_db_roles_post_migration": webapp_phase_command(
                "webapp_ir",
                phase="grants",
                confirmation="APPLY-THREE-SITE-DATABASE-GRANTS",
            ),
            "webapp_ir_db_fencing": webapp_phase_command(
                "webapp_ir",
                phase="fence",
                confirmation="ENABLE-THREE-SITE-DATABASE-FENCING",
            ),
            "webapp_ir_writer_fence": (
                "python",
                "scripts/manage_webapp_writer.py",
                "fence",
                "--expected-epoch",
                "1",
                "--expected-active-site",
                "webapp_fi",
                "--operator",
                "__OPERATION_BOUND_OPERATOR__",
                "--reason",
                "initialize WebApp-IR as an operation-bound locally fenced standby",
                "--apply",
                "--confirm",
                "writer:fence:webapp_ir:1:1",
            ),
        }
        self.assertEqual(WORKER.PREPARE_SERVICE_COMMANDS, expected_commands)
        for service, expected in expected_commands.items():
            with self.subTest(service=service):
                resolved = tuple(
                    f"production-shadow:{OPERATION_ID}"
                    if token == "__OPERATION_BOUND_OPERATOR__"
                    else token
                    for token in expected
                )
                self.assertEqual(
                    WORKER._prepare_service_command(
                        service,
                        operation_id=OPERATION_ID,
                    ),
                    resolved,
                )

    def test_load_request_binds_digest_namespace_and_all_closures(self) -> None:
        request_path, document, manifest = self.request_fixture()
        original_read_json = WORKER._read_json

        def read_json(path, *, label, maximum=WORKER.MAX_JSON_BYTES):  # noqa: ANN001
            if Path(path) == request_path:
                return original_read_json(
                    Path(path),
                    label=label,
                    maximum=maximum,
                )
            if Path(path) == Path(str(document["restore_completion_path"])):
                return {"completion": True}, b"completion", SHA_D
            if Path(path) == Path(str(document["restore_phase_evidence_path"])):
                return {"evidence": True}, b"evidence", SHA_E
            raise AssertionError(f"unexpected read: {path}")

        controller = {
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
        }
        with (
            mock.patch.object(
                WORKER.RESTORE,
                "load_role_manifest",
                return_value=manifest,
            ),
            mock.patch.object(
                WORKER.CONTROLLER,
                "read_root_only_manifest",
                return_value=(controller, SHA_A),
            ),
            mock.patch.object(
                WORKER.CONTROLLER,
                "render_plan",
                return_value={"plan_sha256": SHA_B},
            ),
            mock.patch.object(WORKER, "_read_json", side_effect=read_json),
            mock.patch.object(
                WORKER,
                "_validate_restore_completion",
                side_effect=lambda value, request: value,
            ),
            mock.patch.object(
                WORKER,
                "_validate_restore_phase_evidence",
                side_effect=lambda value, request: value,
            ),
            mock.patch.object(WORKER, "_verify_immutable_prepare_worker"),
        ):
            loaded = WORKER.load_request(request_path)
        self.assertEqual(loaded.sha256, request_path.stem.rsplit("-", 1)[1])
        self.assertEqual(loaded.manifest, manifest)
        self.assertEqual(
            loaded.steps,
            (("roles-pre", "webapp_fi_db_roles", 600),),
        )

    def test_load_request_rejects_non_digest_namespace(self) -> None:
        request_path, _document, manifest = self.request_fixture()
        wrong = request_path.with_name("wrong.json")
        wrong.write_bytes(request_path.read_bytes())
        wrong.chmod(0o600)
        with mock.patch.object(
            WORKER.RESTORE,
            "load_role_manifest",
            return_value=manifest,
        ):
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "digest derived",
            ):
                WORKER.load_request(wrong)

    def test_plan_is_non_mutating_and_does_not_call_backend(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)
        with mock.patch.object(WORKER, "load_request", return_value=context):
            result = WORKER.execute(
                request_path=Path("/unused"),
                apply=False,
            )
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["output_mutated"])
        self.assertFalse(result["runtime_mutated"])
        self.assertFalse(context.output_root.exists())
        self.assertEqual(backend.observe_calls, [])
        self.assertEqual(backend.run_calls, [])

    def test_plan_names_exact_split_phase_as_executable(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_post_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)
        with mock.patch.object(WORKER, "load_request", return_value=context):
            plan = WORKER.execute(
                request_path=Path("/unused"),
                apply=False,
            )
        self.assertTrue(plan["installed_compose_exact_phase_executable"])
        self.assertIsNone(plan["installed_compose_blocker"])
        self.assertEqual(
            plan["steps"],
            [
                {
                    "step": "roles-post",
                    "service": "webapp_fi_db_roles_post_migration",
                    "timeout_seconds": 900,
                    "local_docker_unix_socket_only": True,
                }
            ],
        )

    def test_cli_apply_is_always_disabled(self) -> None:
        with mock.patch("builtins.print") as printed:
            status = WORKER.main(
                [
                    "--request",
                    "/root/request.json",
                    "--apply",
                    "--confirm",
                    "anything",
                ]
            )
        self.assertEqual(status, 1)
        payload = json.loads(printed.call_args.args[0])
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("standalone apply is disabled", payload["error"])
        self.assertFalse(payload["external_network_contacted"])

    def test_apply_requires_exact_confirmation_and_live_authority(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)
        with mock.patch.object(WORKER, "load_request", return_value=context):
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "confirmation",
            ):
                WORKER.execute(
                    request_path=Path("/unused"),
                    apply=True,
                    confirm="wrong",
                )
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "live authority",
            ):
                WORKER.execute(
                    request_path=Path("/unused"),
                    apply=True,
                    confirm=WORKER.confirmation_phrase(context),
                )
        self.assertFalse(context.output_root.exists())

    def test_apply_requires_controller_liveness_pipe(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        with (
            mock.patch.object(WORKER, "load_request", return_value=context),
            self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "liveness pipe",
            ),
        ):
            WORKER.execute(
                request_path=Path("/unused"),
                apply=True,
                confirm=WORKER.confirmation_phrase(context),
                authority_verifier=Authority(),
            )

    def test_pre_roles_success_is_create_only_and_bound(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)
        authority = Authority()
        result = self.execute(context, backend, authority)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(backend.run_calls, ["roles-pre"])
        self.assertEqual(len(authority.challenges), 6)
        self.assertTrue(result["runtime_mutated"])
        self.assertFalse(result["result"]["business_write_observed"])
        self.assertFalse(result["result"]["current_mutated"])
        self.assertFalse(result["result"]["legacy_mutated"])
        self.assertFalse(result["result"]["production_traffic_mutated"])
        self.assertFalse(result["result"]["external_network_contacted"])
        self.assertFalse(result["result"]["ssh_contacted"])
        self.assertFalse(result["result"]["object_storage_contacted"])
        result_path = Path(str(result["result_path"]))
        evidence_path = Path(str(result["result"]["evidence_path"]))
        self.assertEqual(stat_mode(result_path), 0o600)
        self.assertEqual(stat_mode(evidence_path), 0o600)
        self.assertEqual(stat_mode(context.output_root), 0o700)
        for artifact, kind in (
            (result["result"], "result"),
            (
                json.loads(evidence_path.read_text(encoding="ascii")),
                "evidence",
            ),
        ):
            publication_authority = artifact[
                "publication_authority"
            ]
            self.assertEqual(
                artifact["publication_authority_sha256"],
                hashlib.sha256(
                    canonical(publication_authority)
                ).hexdigest(),
            )
            core = {
                key: value
                for key, value in artifact.items()
                if key
                not in {
                    "publication_authority",
                    "publication_authority_sha256",
                }
            }
            self.assertEqual(
                publication_authority["publication_kind"],
                kind,
            )
            self.assertEqual(
                publication_authority[
                    "publication_payload_sha256"
                ],
                hashlib.sha256(canonical(core) + b"\n").hexdigest(),
            )

    def test_completed_replay_requires_fresh_authority_and_reuses_result(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        first = FakeBackend(context)
        initial = self.execute(context, first)
        replay_backend = FakeBackend(context, satisfied=True)
        authority = Authority()
        replay = self.execute(context, replay_backend, authority)
        self.assertEqual(replay["result_sha256"], initial["result_sha256"])
        self.assertEqual(replay["result_publication"], "reused")
        self.assertEqual(replay_backend.run_calls, [])
        self.assertEqual(len(authority.challenges), 3)
        self.assertEqual(
            [
                challenge["boundary"]
                for challenge in authority.challenges
            ],
            [
                "open:phase-lock",
                "publish:evidence",
                "publish:result",
            ],
        )

    def test_completed_replay_fails_when_live_authority_is_revoked(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        self.execute(context, FakeBackend(context))
        replay_backend = FakeBackend(context, satisfied=True)

        def revoked(_challenge, _boundary):  # noqa: ANN001
            raise RuntimeError("revoked")

        with (
            mock.patch.object(WORKER, "load_request", return_value=context),
            mock.patch.object(
                WORKER,
                "LocalDockerPrepareBackend",
                return_value=replay_backend,
            ),
            self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "live authority verifier failed",
            ),
        ):
            read_fd, write_fd = os.pipe()
            try:
                WORKER.execute(
                    request_path=Path("/unused"),
                    apply=True,
                    confirm=WORKER.confirmation_phrase(context),
                    authority_verifier=revoked,
                    control_fd=read_fd,
                )
            finally:
                os.close(read_fd)
                os.close(write_fd)

    def test_completed_replay_rejects_tampered_publication_authority(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        initial = self.execute(context, FakeBackend(context))
        evidence_path = Path(
            str(initial["result"]["evidence_path"])
        )
        evidence = json.loads(evidence_path.read_text(encoding="ascii"))
        evidence["publication_authority_sha256"] = SHA_A
        payload = canonical(evidence) + b"\n"
        tampered_path = evidence_path.with_name(
            f"{context.document['phase']}-"
            f"{hashlib.sha256(payload).hexdigest()}.json"
        )
        tampered_path.write_bytes(payload)
        tampered_path.chmod(0o600)
        evidence_path.unlink()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "evidence publication authority binding differs",
        ):
            self.execute(
                context,
                FakeBackend(context, satisfied=True),
            )

    def test_migration_lost_output_reconciles_exact_target(self) -> None:
        context = self.context("shadow_migrate", "bot_fi")
        crashing = FakeBackend(context, crash_after_run=True)
        with self.assertRaisesRegex(RuntimeError, "lost output"):
            self.execute(context, crashing)
        self.assertEqual(len(crashing.cancel_calls), 1)
        journal = WORKER._load_journal(context)
        self.assertEqual(journal.active_step, "migrate")
        self.assertEqual(journal.active_attempt, 1)
        resumed = FakeBackend(context, satisfied=True)
        authority = Authority()
        result = self.execute(context, resumed, authority)
        self.assertEqual(resumed.run_calls, [])
        self.assertEqual(len(authority.challenges), 5)
        self.assertEqual(
            result["result"]["semantic"]["alembic_chain_state"],
            "target",
        )
        self.assertTrue(result["runtime_mutated"])

    def test_generic_run_failure_invokes_active_attempt_safety_cleanup(
        self,
    ) -> None:
        context = self.context("shadow_migrate", "bot_fi")
        backend = FakeBackend(context, fail_without_change=True)
        with self.assertRaisesRegex(RuntimeError, "command loss"):
            self.execute(context, backend)
        self.assertEqual(len(backend.cancel_calls), 1)
        self.assertEqual(backend.cancel_calls[0][:2], ("migrate", 1))
        self.assertEqual(
            backend.cancel_calls[0][2],
            WORKER._load_journal(context).active_started_sha256,
        )

    def test_authority_cancellation_propagates_after_safety_cleanup(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)

        class CancellingAuthority(Authority):
            def __call__(self, challenge, boundary):  # noqa: ANN001
                if boundary.startswith("after:"):
                    raise WORKER.FrozenPrepareCancellation(
                        "simulated authority cancellation"
                    )
                return super().__call__(challenge, boundary)

        with self.assertRaisesRegex(
            WORKER.FrozenPrepareCancellation,
            "simulated authority cancellation",
        ):
            self.execute(
                context,
                backend,
                CancellingAuthority(),
            )
        self.assertEqual(len(backend.cancel_calls), 1)
        self.assertEqual(
            WORKER._load_journal(context).active_step,
            "roles-pre",
        )

    def test_final_readback_must_equal_journaled_final_observation(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )

        class DriftingBackend(FakeBackend):
            def observe(self, step):  # noqa: ANN001
                result = super().observe(step)
                if self.state[step] and len(self.observe_calls) >= 3:
                    result["details"] = {
                        **result["details"],
                        "role_state_sha256": SHA_A,
                    }
                return result

        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "final observation differs",
        ):
            self.execute(context, DriftingBackend(context))
        self.assertFalse(
            (context.output_root / "evidence").exists()
        )
        self.assertFalse(
            (context.output_root / "result").exists()
        )

    def test_post_phase_rejects_prior_schema_fingerprint_drift_before_run(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_post_migration",
            "webapp_fi",
        )
        assert context.prior_result is not None
        context.prior_result["semantic"][
            "schema_fingerprint_sha256"
        ] = SHA_A
        backend = FakeBackend(context)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "differs from prior phase",
        ):
            self.execute(context, backend)
        self.assertEqual(backend.run_calls, [])
        self.assertFalse(
            (context.output_root / "journal" / "events").exists()
        )

    def test_fence_rejects_current_role_grant_drift_before_run(
        self,
    ) -> None:
        context = self.context("shadow_fence", "webapp_fi")

        class GrantDriftBackend(FakeBackend):
            def observe(self, step):  # noqa: ANN001
                result = super().observe(step)
                result["details"] = {
                    **result["details"],
                    "grant_set_sha256": SHA_A,
                    "exact_release_grant_policy_verified": False,
                    "least_privilege_role_set_verified": False,
                }
                return result

        backend = GrantDriftBackend(context, satisfied=True)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "role or grant inventory differs",
        ):
            self.execute(context, backend)
        self.assertEqual(backend.run_calls, [])
        self.assertFalse(
            (context.output_root / "journal" / "events").exists()
        )

    def test_post_roles_lost_output_is_rerun_not_state_adopted(self) -> None:
        context = self.context(
            "shadow_roles_post_migration",
            "webapp_fi",
        )
        crashing = FakeBackend(context, crash_after_run=True)
        with self.assertRaisesRegex(RuntimeError, "lost output"):
            self.execute(context, crashing)
        resumed = FakeBackend(context, satisfied=True)
        result = self.execute(context, resumed)
        self.assertEqual(resumed.run_calls, ["roles-post"])
        self.assertEqual(
            set(result["result"]["semantic"]),
            {
                "least_privilege_role_set_verified",
                "excessive_grant_count",
                "post_migration_grant_set_sha256",
                "role_state_sha256",
                "migrated_schema_fingerprint_sha256",
                "schema_fingerprint_algorithm",
            },
        )
        self.assertEqual(result["journal_event_count"], 4)
        events = WORKER._load_journal(context).events
        self.assertEqual(
            [
                event["attempt"]
                for event in events
                if event["kind"] != "finalized"
            ],
            [1, 2, 2],
        )

    def test_pre_roles_lost_output_reruns_secret_rotation(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        with self.assertRaisesRegex(RuntimeError, "lost output"):
            self.execute(
                context,
                FakeBackend(context, crash_after_run=True),
            )
        resumed = FakeBackend(context, satisfied=True)
        self.execute(context, resumed)
        self.assertEqual(resumed.run_calls, ["roles-pre"])

    def test_foreign_already_migrated_database_is_rejected(self) -> None:
        context = self.context("shadow_migrate", "bot_fi")
        backend = FakeBackend(context, satisfied=True)
        authority = Authority()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "already-migrated",
        ):
            self.execute(context, backend, authority)
        self.assertEqual(
            [challenge["boundary"] for challenge in authority.challenges],
            ["open:phase-lock"],
        )
        self.assertEqual(backend.run_calls, [])
        self.assertFalse(
            (context.output_root / "journal" / "events").exists()
        )

    def test_equal_source_and_target_is_an_explicit_noop(self) -> None:
        context = self.context(
            "shadow_migrate",
            "bot_fi",
            source_revision="same",
            target_revision="same",
        )
        backend = FakeBackend(context, satisfied=True)
        result = self.execute(context, backend)
        self.assertEqual(backend.run_calls, [])
        self.assertFalse(result["runtime_mutated"])
        self.assertEqual(result["completed_steps"], ["migrate"])

    def test_tampered_authority_phase_prefix_fails_before_intent(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        backend = FakeBackend(context)
        authority = Authority()
        authority.mutate = lambda response: response.update(
            completed_phases=[]
        )
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "phase prefix",
        ):
            self.execute(context, backend, authority)
        self.assertEqual(backend.run_calls, [])
        self.assertFalse(
            (context.output_root / "journal" / "events").exists()
        )

    def test_journal_hash_tamper_is_rejected(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        self.execute(context, FakeBackend(context))
        event_path = sorted(
            (context.output_root / "journal" / "events").iterdir()
        )[0]
        event = json.loads(event_path.read_text(encoding="ascii"))
        event["role"] = "webapp_ir"
        event_path.write_text(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        event_path.chmod(0o600)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "journal event binding",
        ):
            WORKER._load_journal(context)

    def test_prior_result_is_recomputed_from_request_journal_and_evidence(
        self,
    ) -> None:
        prior = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        published = self.execute(prior, FakeBackend(prior))
        document = dict(published["result"])
        result_path = Path(str(published["result_path"]))
        current_document = {
            **prior.document,
            "phase": "shadow_migrate",
            "operation": WORKER.PHASE_OPERATIONS["shadow_migrate"],
        }
        with mock.patch.object(
            WORKER,
            "load_request",
            return_value=prior,
        ):
            verified = WORKER._validate_prior_result(
                document,
                expected_phase="shadow_roles_pre_migration",
                request=current_document,
                manifest=prior.manifest,
                result_path=result_path,
                result_sha256=str(published["result_sha256"]),
            )
            self.assertEqual(verified["semantic"], document["semantic"])
            forged = dict(document)
            forged["semantic"] = {}
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "semantic differs",
            ):
                WORKER._validate_prior_result(
                    forged,
                    expected_phase="shadow_roles_pre_migration",
                    request=current_document,
                    manifest=prior.manifest,
                    result_path=result_path,
                    result_sha256=str(published["result_sha256"]),
                )
            forged = dict(document)
            forged["journal_event_count"] = -1
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "does not close",
            ):
                WORKER._validate_prior_result(
                    forged,
                    expected_phase="shadow_roles_pre_migration",
                    request=current_document,
                    manifest=prior.manifest,
                    result_path=result_path,
                    result_sha256=str(published["result_sha256"]),
                )
            forged = json.loads(json.dumps(document))
            forged["publication_authority_sha256"] = SHA_A
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "result publication authority binding differs",
            ):
                WORKER._validate_prior_result(
                    forged,
                    expected_phase="shadow_roles_pre_migration",
                    request=current_document,
                    manifest=prior.manifest,
                    result_path=result_path,
                    result_sha256=str(published["result_sha256"]),
                )

    def test_retries_are_bounded_after_three_unresolved_attempts(self) -> None:
        context = self.context("shadow_migrate", "bot_fi")
        for expected_attempt in (1, 2, 3):
            backend = FakeBackend(context, fail_without_change=True)
            with self.assertRaisesRegex(RuntimeError, "command loss"):
                self.execute(context, backend)
            self.assertEqual(
                WORKER._load_journal(context).active_attempt,
                expected_attempt,
            )
        authority = Authority()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "bounded recovery attempts",
        ):
            self.execute(
                context,
                FakeBackend(context, fail_without_change=True),
                authority,
            )
        self.assertEqual(
            [challenge["boundary"] for challenge in authority.challenges],
            ["open:phase-lock"],
        )

    def test_bot_fence_runs_exact_split_fence_service(self) -> None:
        context = self.context("shadow_fence", "bot_fi")
        backend = FakeBackend(context, satisfied=True)
        result = self.execute(context, backend)
        self.assertEqual(backend.run_calls, ["database-fence"])
        self.assertTrue(result["runtime_mutated"])
        self.assertFalse(
            result["result"]["semantic"]["bot_fence_verification_only"]
        )
        self.assertEqual(
            result["result"]["semantic"]["fenced_database_count"],
            1,
        )

    def test_webapp_ir_fence_runs_database_then_writer_fence(self) -> None:
        context = self.context("shadow_fence", "webapp_ir")
        backend = FakeBackend(context)
        result = self.execute(context, backend)
        self.assertEqual(
            backend.run_calls,
            ["database-fence", "writer-fence"],
        )
        self.assertTrue(result["result"]["semantic"]["writer_fenced"])
        self.assertEqual(
            set(result["result"]["semantic"]),
            {
                "fenced_database_count",
                "unfenced_writer_count",
                "database_event_fence_verified",
                "migrated_schema_fingerprint_sha256",
                "post_migration_grant_set_sha256",
                "role_state_sha256",
                "schema_fingerprint_algorithm",
                "fence_configuration_sha256",
                "writer_fenced",
                "bot_fence_verification_only",
            },
        )
        self.assertEqual(result["journal_event_count"], 5)

    def test_phase_semantic_source_has_no_duplicate_literal_keys(
        self,
    ) -> None:
        tree = ast.parse(
            textwrap.dedent(
                inspect.getsource(WORKER._phase_semantic)
            )
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
            ]
            self.assertEqual(
                len(keys),
                len(set(keys)),
                f"duplicate literal key in _phase_semantic: {keys}",
            )

    def test_journal_owned_oneoff_cleanup_is_authorized_and_recorded(
        self,
    ) -> None:
        context = self.context("shadow_migrate", "bot_fi")
        with self.assertRaisesRegex(RuntimeError, "lost output"):
            self.execute(
                context,
                FakeBackend(context, crash_after_run=True),
            )

        class ResidueBackend(FakeBackend):
            def inspect_residue(self, **_kwargs):  # noqa: ANN003
                return {
                    "residue_count": 1,
                    "residue_identity_sha256": SHA_A,
                }

            def cleanup_residue(self, **_kwargs):  # noqa: ANN003
                return {
                    "residue_count": 1,
                    "residue_identity_sha256": SHA_A,
                    "removed_count": 1,
                    "persistent_volume_removed": False,
                    "generation_data_mutated": False,
                }

        backend = ResidueBackend(context, satisfied=True)
        authority = Authority()
        self.execute(context, backend, authority)
        self.assertIn(
            "cleanup:migrate:attempt:1",
            [
                challenge["boundary"]
                for challenge in authority.challenges
            ],
        )
        events = WORKER._load_journal(context).events
        cleanup = [event for event in events if event["kind"] == "cleanup"]
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0]["semantic"]["removed_count"], 1)

    def test_secure_reader_rejects_group_readable_and_symlink(self) -> None:
        path = self.root / "input.json"
        path.write_text("{}\n", encoding="ascii")
        path.chmod(0o640)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "root-owned mode",
        ):
            WORKER._read_secure_bytes(
                path,
                label="test input",
                maximum=1024,
            )
        path.chmod(0o600)
        link = self.root / "link.json"
        link.symlink_to(path)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "unavailable or unsafe",
        ):
            WORKER._read_secure_bytes(
                link,
                label="test link",
                maximum=1024,
            )

    def test_create_only_document_refuses_existing_different_bytes(self) -> None:
        directory = self.root / "private"
        directory.mkdir(mode=0o700)
        target = directory / "record.json"
        target.write_text('{"different":true}\n', encoding="ascii")
        target.chmod(0o600)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "create-only",
        ):
            WORKER._persist_new_document(
                directory,
                filename=target.name,
                document={"expected": True},
                label="test record",
            )

    def test_foreign_publication_namespace_is_rejected(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        evidence = context.output_root / "evidence"
        evidence.mkdir(parents=True, mode=0o700)
        for path in (
            context.output_root.parent,
            context.output_root,
            evidence,
        ):
            path.chmod(0o700)
        foreign = evidence / "foreign.json"
        foreign.write_text("{}\n", encoding="ascii")
        foreign.chmod(0o600)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "foreign publication",
        ):
            self.execute(context, FakeBackend(context))

    def test_local_service_command_is_unix_socket_and_prepare_only(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )

        class Runner:
            def __init__(self) -> None:
                self.calls: list[tuple[list[str], dict[str, str]]] = []

            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                self.calls.append((list(arguments), dict(env)))
                return '{"status":"applied"}'

        runner = Runner()
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.context = context
        backend.manifest = context.manifest
        backend.runner = runner
        with (
            mock.patch.object(
                WORKER.RESTORE,
                "_compose_environment",
                return_value=(dict(WORKER.RESTORE.SAFE_ENV), {}),
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_capture_runtime_path_identities",
                return_value={},
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_recheck_runtime_path_identities",
            ),
        ):
            digest, size = backend._run_service(
                step="roles-pre",
                attempt=1,
                started_event_sha256=SHA_A,
                service="webapp_fi_db_roles",
                timeout=600,
            )
        arguments, environment = runner.calls[0]
        self.assertEqual(
            arguments[:2],
            [
                "/usr/bin/docker",
                "--host=unix:///run/docker.sock",
            ],
        )
        self.assertIn(str(context.manifest.prepare_compose_path), arguments)
        self.assertIn("--no-deps", arguments)
        self.assertIn("--pull", arguments)
        self.assertIn("never", arguments)
        self.assertIn("--name", arguments)
        self.assertIn(
            "trading-bot.production.prepare-request="
            f"{context.sha256}",
            arguments,
        )
        self.assertIn(
            "trading-bot.production.prepare-started-event="
            f"{SHA_A}",
            arguments,
        )
        self.assertEqual(
            arguments[-5:],
            [
                "webapp_fi_db_roles",
                "python",
                "scripts/provision_three_site_database_roles.py",
                "--role-prefix",
                "webapp_fi",
            ],
        )
        self.assertNotIn("/usr/bin/ssh", arguments)
        self.assertNotIn("scp", arguments)
        self.assertEqual(environment["DOCKER_CONFIG"], "/nonexistent")
        self.assertEqual(digest, hashlib.sha256(b'{"status":"applied"}').hexdigest())
        self.assertEqual(size, len(b'{"status":"applied"}'))

    def test_prepare_residue_requires_exact_v152_config_and_hostconfig(
        self,
    ) -> None:
        identifier = "7" * 64
        image_id = f"sha256:{'8' * 64}"
        project = "project-webapp-fi"
        service = "webapp_fi_db_roles"
        config_hash = "9" * 64
        ca_path = self.root / "ca.crt"
        prepare_path = self.root / "prepare.yml"
        expected_production_labels = {
            "trading-bot.production.operation-id": OPERATION_ID,
            "trading-bot.production.prepare-generation": "3" * 64,
            "trading-bot.production.prepare-phase": (
                "shadow_roles_pre_migration"
            ),
            "trading-bot.production.prepare-request": "5" * 64,
            "trading-bot.production.prepare-step": "roles-pre",
            "trading-bot.production.prepare-attempt": "1",
            "trading-bot.production.prepare-started-event": SHA_A,
        }
        contract = SimpleNamespace(
            user="",
            exposed_ports={},
            healthcheck=None,
            image_id=image_id,
            volumes={},
            working_dir="/app",
            entrypoint=None,
            on_build=None,
            stop_signal="",
            stop_timeout=10,
            shell=None,
            labels={"org.example.release": "exact"},
            config_hash=config_hash,
            cgroup_parent="prepare.slice",
            nano_cpus=1_000_000_000,
            memory=1024**3,
            pids_limit=256,
            log_config={
                "Type": "json-file",
                "Config": {"max-file": "5", "max-size": "20m"},
            },
        )
        labels = {
            **contract.labels,
            **expected_production_labels,
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.config-hash": config_hash,
        }
        config = {
            field: None
            for field in WORKER.RESTORE.CONTAINER_CONFIG_FIELDS
        }
        config.update(
            {
                "Hostname": identifier[:12],
                "Domainname": "",
                "User": "",
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "ExposedPorts": {},
                "Tty": False,
                "OpenStdin": False,
                "StdinOnce": False,
                "Env": ["A=B"],
                "Cmd": list(
                    WORKER._prepare_service_command(
                        service,
                        operation_id=OPERATION_ID,
                    )
                ),
                "Healthcheck": None,
                "ArgsEscaped": False,
                "Image": image_id,
                "Volumes": {},
                "WorkingDir": "/app",
                "Entrypoint": None,
                "NetworkDisabled": False,
                "OnBuild": None,
                "Labels": labels,
                "StopSignal": "",
                "StopTimeout": 10,
                "Shell": None,
            }
        )
        host = {
            field: None for field in WORKER.RESTORE.HOST_CONFIG_FIELDS
        }
        for field in (
            "BlkioWeightDevice",
            "BlkioDeviceReadBps",
            "BlkioDeviceWriteBps",
            "BlkioDeviceReadIOps",
            "BlkioDeviceWriteIOps",
            "Devices",
            "Dns",
            "DnsOptions",
            "DnsSearch",
            "Ulimits",
        ):
            host[field] = []
        for field in (
            "Annotations",
            "PortBindings",
            "StorageOpt",
            "Sysctls",
            "Tmpfs",
        ):
            host[field] = {}
        for field in (
            "CpuShares",
            "BlkioWeight",
            "CpuPeriod",
            "CpuQuota",
            "CpuRealtimePeriod",
            "CpuRealtimeRuntime",
            "MemoryReservation",
            "MemorySwappiness",
            "CpuCount",
            "CpuPercent",
            "IOMaximumIOps",
            "IOMaximumBandwidth",
            "OomScoreAdj",
        ):
            host[field] = 0
        for field in (
            "ContainerIDFile",
            "VolumeDriver",
            "Cgroup",
            "PidMode",
            "UTSMode",
            "UsernsMode",
            "Isolation",
            "CpusetCpus",
            "CpusetMems",
        ):
            host[field] = ""
        host.update(
            {
                "Memory": contract.memory,
                "MemorySwap": contract.memory,
                "CgroupParent": contract.cgroup_parent,
                "NanoCpus": contract.nano_cpus,
                "OomKillDisable": False,
                "Init": False,
                "PidsLimit": contract.pids_limit,
                "Binds": [
                    f"{ca_path}:/run/production-dr-ca/ca.crt:ro"
                ],
                "LogConfig": contract.log_config,
                "NetworkMode": f"{project}_webapp_fi",
                "RestartPolicy": {
                    "Name": "no",
                    "MaximumRetryCount": 0,
                },
                "AutoRemove": True,
                "ConsoleSize": [0, 0],
                "CapAdd": None,
                "CapDrop": None,
                "CgroupnsMode": "private",
                "IpcMode": "private",
                "Privileged": False,
                "PublishAllPorts": False,
                "ReadonlyRootfs": False,
                "ShmSize": 64 * 1024 * 1024,
                "Runtime": "runc",
                "MaskedPaths": [],
                "ReadonlyPaths": list(
                    WORKER.RESTORE.READONLY_PATHS
                ),
            }
        )
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(
            operation_id=OPERATION_ID,
            app_image_id=image_id,
            ca_path=ca_path,
            prepare_compose_path=prepare_path,
            paths=SimpleNamespace(project_name=project),
        )
        row = {
            "Id": identifier,
            "Name": f"/{project}-prepare-exact",
            "Image": image_id,
            "Config": config,
            "HostConfig": host,
        }

        def validate(candidate):  # noqa: ANN001
            with mock.patch.object(
                WORKER.RESTORE,
                "_expected_masked_paths",
                return_value=[],
            ):
                return backend._validate_prepare_oneoff_runtime(
                    candidate,
                    identifier=identifier,
                    expected_name=f"{project}-prepare-exact",
                    expected_service=service,
                    expected_network=f"{project}_webapp_fi",
                    expected_production_labels=(
                        expected_production_labels
                    ),
                    contract=contract,
                    environment={"A": "B"},
                )

        self.assertRegex(validate(row), r"^[0-9a-f]{64}$")
        for section, field, replacement in (
            ("Config", "Env", ["A=C"]),
            ("Config", "User", "root"),
            ("Config", "Entrypoint", ["/bin/sh"]),
            ("Config", "WorkingDir", "/tmp"),
            ("HostConfig", "VolumesFrom", ["foreign"]),
            ("HostConfig", "ContainerIDFile", "/tmp/id"),
            ("HostConfig", "DeviceCgroupRules", ["c 1:3 rwm"]),
            ("HostConfig", "Runtime", "foreign"),
            ("HostConfig", "MaskedPaths", ["/foreign"]),
            ("HostConfig", "ReadonlyPaths", ["/foreign"]),
        ):
            with self.subTest(section=section, field=field):
                candidate = {
                    **row,
                    section: {
                        **row[section],
                        field: replacement,
                    },
                }
                with self.assertRaises(
                    WORKER.FrozenPrepareWorkerError
                ):
                    validate(candidate)
        for section, field in (
            ("Config", "Env"),
            ("HostConfig", "VolumesFrom"),
        ):
            with self.subTest(section=section, missing=field):
                altered = dict(row[section])
                del altered[field]
                with self.assertRaises(
                    WORKER.FrozenPrepareWorkerError
                ):
                    validate({**row, section: altered})
        for section in ("Config", "HostConfig"):
            with self.subTest(section=section, unknown=True):
                with self.assertRaises(
                    WORKER.FrozenPrepareWorkerError
                ):
                    validate(
                        {
                            **row,
                            section: {
                                **row[section],
                                "UnknownV152Field": None,
                            },
                        }
                    )

    def test_docker_runner_strips_hostile_material_process_controls(self) -> None:
        class Delegate:
            def __init__(self) -> None:
                self.environment = None

            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                self.environment = dict(env)
                return "ok"

            def stream(self, arguments, *, timeout, env):  # noqa: ANN001
                self.environment = dict(env)
                return "stream"

        delegate = Delegate()
        runner = WORKER.SanitizedDockerRunner(delegate)
        incoming = {
            **WORKER.RESTORE.SAFE_ENV,
            "PRODUCTION_SHADOW_PROJECT": "bound-project",
            "LD_PRELOAD": "/root/evil.so",
            "PYTHONPATH": "/root/evil",
            "AWS_ACCESS_KEY_ID": "not-forwarded",
            "BOT_TOKEN": "not-forwarded",
        }
        self.assertEqual(
            runner.run(
                ["/usr/bin/docker"],
                timeout=1,
                env=incoming,
            ),
            "ok",
        )
        self.assertEqual(
            delegate.environment,
            {
                **WORKER.RESTORE.SAFE_ENV,
                "PRODUCTION_SHADOW_PROJECT": "bound-project",
            },
        )

    def test_control_disconnect_kills_runner_descendants_and_cleans_oneoff(
        self,
    ) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )
        sentinel = self.root / "cancelled-descendant-survived"
        program = (
            "import os,signal,time\n"
            "if os.fork() == 0:\n"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " time.sleep(3.0)\n"
            f" open({str(sentinel)!r},'wb').write(b'survived')\n"
            " os._exit(0)\n"
            "time.sleep(60)\n"
        )

        class CancellableBackend(FakeBackend):
            def __init__(self, loaded_context):  # noqa: ANN001
                super().__init__(loaded_context)
                self.residue = False
                self.cleanup_called = False

            def run_step(
                self,
                step,
                *,
                attempt,
                started_event_sha256,
            ):  # noqa: ANN001
                self.residue = True
                WORKER.RESTORE._bounded_command(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        program,
                    ],
                    timeout=30,
                    env={"PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL,
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
                raise AssertionError("cancelled runner returned")

            def cancel_active_oneoff(
                self,
                *,
                step,
                attempt,
                started_event_sha256,
            ):  # noqa: ANN001
                self.cleanup_called = True
                self.residue = False
                return {
                    "residue_count": 1,
                    "residue_identity_sha256": SHA_A,
                    "removed_count": 1,
                    "persistent_volume_removed": False,
                    "generation_data_mutated": False,
                }

        backend = CancellableBackend(context)
        read_fd, write_fd = os.pipe()
        closer = threading.Thread(
            target=lambda: (time.sleep(0.25), os.close(write_fd)),
            daemon=True,
        )
        closer.start()
        try:
            with (
                mock.patch.object(
                    WORKER,
                    "load_request",
                    return_value=context,
                ),
                mock.patch.object(
                    WORKER,
                    "LocalDockerPrepareBackend",
                    return_value=backend,
                ),
                self.assertRaisesRegex(
                    WORKER.FrozenPrepareCancellation,
                    "liveness pipe reached EOF",
                ),
            ):
                WORKER.execute(
                    request_path=Path("/unused"),
                    apply=True,
                    confirm=WORKER.confirmation_phrase(context),
                    authority_verifier=Authority(),
                    control_fd=read_fd,
                )
            closer.join(timeout=1)
            self.assertTrue(backend.cleanup_called)
            self.assertFalse(backend.residue)
            time.sleep(3.2)
            self.assertFalse(sentinel.exists())
        finally:
            os.close(read_fd)

    def test_cancellation_waits_for_delayed_compose_create_and_removes_it(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.runner = object()
        backend._prepare_residue = mock.Mock(
            side_effect=(
                [],
                [("delayed-container", {})],
                [],
                [],
            )
        )
        backend._sql_residues = mock.Mock(return_value=[])
        backend.cleanup_residue = mock.Mock(
            return_value={
                "residue_count": 1,
                "residue_identity_sha256": hashlib.sha256(
                    canonical(["delayed-container"])
                ).hexdigest(),
                "removed_count": 1,
                "persistent_volume_removed": False,
                "generation_data_mutated": False,
            }
        )
        clock = iter(
            (0.0, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 3.2)
        )
        with (
            mock.patch.object(
                WORKER.time,
                "monotonic",
                side_effect=lambda: next(clock),
            ),
            mock.patch.object(WORKER.time, "sleep") as sleep,
        ):
            result = backend.cancel_active_oneoff(
                step="roles-pre",
                attempt=1,
                started_event_sha256=SHA_A,
            )
        self.assertEqual(result["residue_count"], 1)
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(backend._prepare_residue.call_count, 4)
        backend.cleanup_residue.assert_called_once()
        self.assertEqual(sleep.call_count, 3)

    def test_termination_signals_mark_liveness_cancelled(self) -> None:
        for signum in (signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signum=signum):
                read_fd, write_fd = os.pipe()
                try:
                    with WORKER.ControllerLivenessGuard(
                        read_fd
                    ) as guard:
                        with self.assertRaises(
                            WORKER.FrozenPrepareCancellation
                        ):
                            guard._handle_signal(signum, None)
                        self.assertTrue(guard.cancelled)
                finally:
                    os.close(read_fd)
                    os.close(write_fd)

    def test_schema_fingerprint_is_catalog_not_business_rows(self) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        queries: list[str] = []

        def psql(sql: str, *, timeout: int = 300) -> str:
            queries.append(sql)
            if "WITH schema_records" in sql:
                return (
                    'column\tpublic.example.1.id\t{"not_null": true}\n'
                    'trigger\tpublic.example.guard\t{"enabled": "O"}'
                )
            return "1"

        backend._psql = psql
        result = backend._database_fingerprint()
        self.assertEqual(
            result["schema_fingerprint_algorithm"],
            WORKER.SCHEMA_FINGERPRINT_ALGORITHM,
        )
        self.assertEqual(result["database_table_count"], 1)
        self.assertEqual(result["schema_object_count"], 2)
        query = queries[0]
        self.assertIn("pg_attribute", query)
        self.assertIn("pg_constraint", query)
        self.assertIn("pg_index", query)
        self.assertIn("pg_trigger", query)
        self.assertIn("pg_proc", query)
        self.assertIn("relrowsecurity", query)
        self.assertIn("relreplident", query)
        self.assertIn("pg_policy", query)
        self.assertIn("pg_sequence", query)
        self.assertIn("pg_get_viewdef", query)
        self.assertIn("pg_type", query)
        self.assertIn("pg_extension", query)
        self.assertIn("pg_rewrite", query)
        self.assertIn("pg_inherits", query)
        self.assertIn("indisreplident", query)
        self.assertIn("pg_foreign_table", query)
        self.assertIn("pg_foreign_server", query)
        self.assertIn("pg_event_trigger", query)
        self.assertIn("pg_statistic_ext", query)
        self.assertIn("pg_publication_rel", query)
        self.assertIn("pg_publication_namespace", query)
        self.assertIn("pg_get_userbyid", query)
        self.assertIn("acldefault", query)
        self.assertIn("aclexplode", query)
        self.assertIn("acl.grantee=0", query)
        self.assertIn("grantee.rolname NOT IN", query)
        self.assertIn("'webapp_fi_app'", query)
        self.assertNotIn("'webapp_ir_app'", query)
        publication_relation = query.split(
            "FROM pg_publication_rel",
            1,
        )[1].split("UNION ALL", 1)[0]
        publication_namespace = query.split(
            "FROM pg_publication_namespace",
            1,
        )[1].split("UNION ALL", 1)[0]
        self.assertNotIn(
            "namespace.nspname='public'",
            publication_relation,
        )
        self.assertNotIn(
            "namespace.nspname='public'",
            publication_namespace,
        )
        self.assertNotIn("SELECT * FROM public.", query)

    def test_psql_line_bound_is_byte_exact_and_fingerprint_bounded(
        self,
    ) -> None:
        long_value = "x" * (WORKER.DEFAULT_MAX_SQL_LINE_BYTES + 1)
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "output is invalid",
        ):
            WORKER._psql_lines(long_value, label="default")
        self.assertEqual(
            WORKER._psql_lines(
                long_value,
                label="fingerprint",
                maximum_line_bytes=WORKER.FINGERPRINT_MAX_SQL_LINE_BYTES,
            ),
            [long_value],
        )
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "maximum line bound",
        ):
            WORKER._psql_lines(
                "ok",
                label="unbounded",
                maximum_line_bytes=(
                    WORKER.FINGERPRINT_MAX_SQL_LINE_BYTES + 1
                ),
            )

    def test_schema_fingerprint_excludes_only_current_site_runtime_acls(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        schema_row = [
            'relation\tpublic.example\t{"acl": []}'
        ]
        queries: list[str] = []

        def psql(sql: str, *, timeout: int = 300) -> str:
            queries.append(sql)
            if "WITH schema_records" in sql:
                return "\n".join(schema_row)
            return "1"

        backend._psql = psql
        baseline = backend._database_fingerprint()[
            "schema_fingerprint_sha256"
        ]
        current_site_grant = backend._database_fingerprint()[
            "schema_fingerprint_sha256"
        ]
        schema_row[:] = [
            "relation\tpublic.example\t"
            '{"acl": [{"grantee": "webapp_ir_app", '
            '"privilege": "SELECT", "grantable": false}]}'
        ]
        foreign_role_grant = backend._database_fingerprint()[
            "schema_fingerprint_sha256"
        ]
        schema_row[:] = [
            "relation\tpublic.example\t"
            '{"acl": [{"grantee": "PUBLIC", '
            '"privilege": "SELECT", "grantable": false}]}'
        ]
        public_grant = backend._database_fingerprint()[
            "schema_fingerprint_sha256"
        ]

        self.assertEqual(baseline, current_site_grant)
        self.assertNotEqual(baseline, foreign_role_grant)
        self.assertNotEqual(baseline, public_grant)
        fingerprint_query = queries[0]
        for role in WORKER.EXPECTED_RUNTIME_ROLES["webapp_fi"]:
            self.assertIn(f"'{role}'", fingerprint_query)
        for role in WORKER.EXPECTED_RUNTIME_ROLES["webapp_ir"]:
            self.assertNotIn(f"'{role}'", fingerprint_query)
        self.assertIn("acl.grantee=0", fingerprint_query)

    def test_psql_uses_worker_owned_read_only_path(self) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend._execute_sql = mock.Mock(return_value="target")
        self.assertEqual(
            backend._psql(
                "SELECT version_num FROM public.alembic_version"
            ),
            "target",
        )
        backend._execute_sql.assert_called_once_with(
            sql="SELECT version_num FROM public.alembic_version",
            sql_kind="read-only",
            timeout=300,
            reviewed_index=None,
        )

    def test_worker_owned_psql_persists_exact_read_only_intent_before_create(
        self,
    ) -> None:
        class Runner:
            def __init__(self) -> None:
                self.calls: list[tuple[list[str], float]] = []
                self.intent_root: Path | None = None
                self.intent_seen_before_run = False

            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                self.calls.append((list(arguments), float(timeout)))
                assert self.intent_root is not None
                self.intent_seen_before_run = (
                    self.intent_root.is_dir()
                    and len(list(self.intent_root.iterdir())) == 1
                )
                return "target"

        runner = Runner()
        context, backend = self.sql_backend(runner)
        runner.intent_root = context.output_root / "sql-intents"
        backend._bind_sql_scope(
            step="migrate",
            attempt=0,
            started_event_sha256=WORKER.ZERO_SHA256,
            stage="pre-start-observe",
        )
        with (
            mock.patch.object(
                backend,
                "_sql_runtime_contract",
                return_value=object(),
            ),
            mock.patch.object(
                backend,
                "_cleanup_sql_oneoffs",
                return_value=[],
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_compose_environment",
                return_value=(dict(WORKER.RESTORE.SAFE_ENV), {}),
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_capture_runtime_path_identities",
                return_value={},
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_recheck_runtime_path_identities",
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_psql",
                side_effect=AssertionError("legacy psql delegation"),
            ),
        ):
            self.assertEqual(
                backend._psql(
                    "SELECT version_num FROM public.alembic_version",
                    timeout=30,
                ),
                "target",
            )

        self.assertTrue(runner.intent_seen_before_run)
        intent_paths = list(runner.intent_root.iterdir())
        self.assertEqual(len(intent_paths), 1)
        intent = json.loads(intent_paths[0].read_text(encoding="ascii"))
        self.assertEqual(
            intent_paths[0].name,
            f"{intent['intent_sha256']}.json",
        )
        self.assertEqual(intent["stage"], "pre-start-observe")
        self.assertEqual(intent["attempt"], 0)
        self.assertEqual(
            intent["started_event_sha256"],
            WORKER.ZERO_SHA256,
        )
        self.assertEqual(intent["sql_kind"], "read-only")
        self.assertIs(intent["transaction_read_only"], True)
        command_sql = intent["command"][-1]
        self.assertIn("BEGIN TRANSACTION READ ONLY", command_sql)
        self.assertIn("SET LOCAL transaction_read_only TO on", command_sql)
        self.assertIn("SET LOCAL statement_timeout TO '30000ms'", command_sql)
        self.assertIn("SET LOCAL lock_timeout TO '5000ms'", command_sql)
        self.assertIn("SET LOCAL search_path TO pg_catalog", command_sql)
        arguments = runner.calls[0][0]
        name = arguments[arguments.index("--name") + 1]
        self.assertEqual(
            name,
            backend._sql_oneoff_name(intent["intent_sha256"]),
        )
        self.assertIn(
            "trading-bot.production.prepare-sql-kind=read-only",
            arguments,
        )
        self.assertIn(
            "trading-bot.production.prepare-sql-intent="
            f"{intent['intent_sha256']}",
            arguments,
        )
        self.assertIn("bot_fi_restore_tool", arguments)

    def test_worker_owned_psql_rejects_transaction_escape(self) -> None:
        context, backend = self.sql_backend(object())
        backend._bind_sql_scope(
            step="migrate",
            attempt=0,
            started_event_sha256=WORKER.ZERO_SHA256,
            stage="pre-start-observe",
        )
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "query is invalid",
        ):
            backend._psql("SELECT 1; COMMIT")
        self.assertFalse((context.output_root / "sql-intents").exists())

    def test_execute_binds_pre_post_and_final_sql_scopes(self) -> None:
        context = self.context(
            "shadow_roles_pre_migration",
            "webapp_fi",
        )

        class ScopeBackend(FakeBackend):
            def __init__(self, value):  # noqa: ANN001
                super().__init__(value)
                self.scopes: list[dict[str, object]] = []
                self.recovery_checked = False

            def _bind_sql_scope(self, **scope):  # noqa: ANN003, ANN202
                self.scopes.append(dict(scope))

            def recover_sql_oneoffs(self) -> dict[str, object]:
                self.recovery_checked = True
                return {
                    "residue_count": 0,
                    "residue_identity_sha256": None,
                    "removed_count": 0,
                    "persistent_volume_removed": False,
                    "generation_data_mutated": False,
                }

        backend = ScopeBackend(context)
        self.execute(context, backend)
        self.assertTrue(backend.recovery_checked)
        self.assertEqual(
            [scope["stage"] for scope in backend.scopes],
            [
                "pre-start-observe",
                "post-run-observe",
                "final-readback",
            ],
        )
        self.assertEqual(backend.scopes[0]["attempt"], 0)
        self.assertEqual(
            backend.scopes[0]["started_event_sha256"],
            WORKER.ZERO_SHA256,
        )
        self.assertEqual(backend.scopes[1]["attempt"], 1)
        self.assertEqual(
            backend.scopes[1]["started_event_sha256"],
            backend.scopes[2]["started_event_sha256"],
        )
        self.assertRegex(
            str(backend.scopes[1]["started_event_sha256"]),
            r"^[0-9a-f]{64}$",
        )

    def test_drop_requires_active_journal_and_cleans_delayed_oneoff(
        self,
    ) -> None:
        class CancellingRunner:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []
                self.intent_sha256: str | None = None
                self.request_sha256: str | None = None
                self.ps_calls = 0
                self.removed: list[str] = []

            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                arguments = list(arguments)
                self.calls.append(arguments)
                command = arguments[len(WORKER.RESTORE.DOCKER_BASE)]
                if command == "compose":
                    for value in arguments:
                        marker = (
                            "trading-bot.production.prepare-sql-intent="
                        )
                        if value.startswith(marker):
                            self.intent_sha256 = value[len(marker) :]
                        request_marker = (
                            "trading-bot.production.prepare-request="
                        )
                        if value.startswith(request_marker):
                            self.request_sha256 = value[
                                len(request_marker) :
                            ]
                    raise WORKER.FrozenPrepareCancellation(
                        "hostile cancellation"
                    )
                if command == "ps":
                    self.ps_calls += 1
                    return (
                        f"{'7' * 64}\n"
                        if self.ps_calls == 2
                        else ""
                    )
                if command == "inspect":
                    assert self.intent_sha256 is not None
                    return json.dumps(
                        [
                            {
                                "Id": "7" * 64,
                                "Config": {
                                    "Labels": {
                                        "com.docker.compose.oneoff": "True",
                                        (
                                            "trading-bot.production."
                                            "prepare-request"
                                        ): self.request_sha256,
                                        (
                                            "trading-bot.production."
                                            "prepare-sql-intent"
                                        ): self.intent_sha256,
                                    }
                                },
                            }
                        ]
                    )
                if command == "rm":
                    self.removed.append(arguments[-1])
                    return ""
                raise AssertionError(arguments)

        runner = CancellingRunner()
        context, backend = self.sql_backend(runner)
        backend._bind_sql_scope(
            step="migrate",
            attempt=1,
            started_event_sha256=SHA_A,
            stage="step-execution",
        )
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "lacks active authority",
        ):
            backend._drop_reviewed_index("ix_reviewed")
        self.assertFalse((context.output_root / "sql-intents").exists())

        active_journal = WORKER.JournalState(
            events=(),
            completed_steps=(),
            active_step="migrate",
            active_attempt=1,
            active_started_sha256=SHA_A,
            finalized=False,
            tail_sha256=SHA_A,
        )
        with (
            mock.patch.object(
                WORKER,
                "_load_journal",
                return_value=active_journal,
            ),
            mock.patch.object(
                backend,
                "_sql_runtime_contract",
                return_value=object(),
            ),
            mock.patch.object(
                backend,
                "_validate_sql_oneoff_runtime",
            ) as validate_runtime,
            mock.patch.object(
                WORKER.RESTORE,
                "_compose_environment",
                return_value=(dict(WORKER.RESTORE.SAFE_ENV), {}),
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_capture_runtime_path_identities",
                return_value={},
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_recheck_runtime_path_identities",
            ),
            mock.patch.object(
                WORKER,
                "CANCELLATION_QUIESCENCE_SECONDS",
                0.03,
            ),
            mock.patch.object(
                WORKER,
                "CANCELLATION_POLL_SECONDS",
                0.01,
            ),
            self.assertRaisesRegex(
                WORKER.FrozenPrepareCancellation,
                "hostile cancellation",
            ),
        ):
            backend._drop_reviewed_index("ix_reviewed")

        self.assertEqual(runner.removed, ["7" * 64])
        self.assertGreaterEqual(runner.ps_calls, 4)
        validate_runtime.assert_called_once()
        compose_calls = [
            call
            for call in runner.calls
            if call[len(WORKER.RESTORE.DOCKER_BASE)] == "compose"
        ]
        self.assertEqual(len(compose_calls), 1)
        arguments = compose_calls[0]
        self.assertEqual(arguments.count("--command"), 5)
        self.assertIn("SET transaction_read_only TO off", arguments)
        self.assertIn(
            'DROP INDEX CONCURRENTLY IF EXISTS public."ix_reviewed"',
            arguments,
        )
        self.assertIn(
            "trading-bot.production.prepare-sql-kind="
            "drop-reviewed-index",
            arguments,
        )
        intent_path = next(
            (context.output_root / "sql-intents").iterdir()
        )
        intent = json.loads(intent_path.read_text(encoding="ascii"))
        self.assertEqual(intent["reviewed_index"], "ix_reviewed")
        self.assertIs(intent["transaction_read_only"], False)
        self.assertEqual(intent["started_event_sha256"], SHA_A)

    def test_deadline_runner_clamps_every_nested_call(self) -> None:
        class Delegate:
            def __init__(self) -> None:
                self.timeouts: list[float] = []

            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                self.timeouts.append(float(timeout))
                return "ok"

            def stream(self, arguments, *, timeout, env):  # noqa: ANN001
                self.timeouts.append(float(timeout))
                return "stream"

        delegate = Delegate()
        runner = WORKER.DeadlineDockerRunner(
            delegate,
            deadline=110.0,
        )
        with mock.patch.object(
            WORKER.time,
            "monotonic",
            side_effect=(100.0, 105.0, 111.0),
        ):
            self.assertEqual(
                runner.run([], timeout=60, env={}),
                "ok",
            )
            self.assertEqual(
                runner.stream([], timeout=60, env={}),
                "stream",
            )
            with self.assertRaisesRegex(
                WORKER.FrozenPrepareWorkerError,
                "absolute deadline",
            ):
                runner.run([], timeout=60, env={})
        self.assertEqual(delegate.timeouts, [10.0, 5.0])

    def test_early_sql_cancellation_gets_only_one_cleanup_window(
        self,
    ) -> None:
        class Runner:
            def run(self, arguments, *, timeout, env, stdin=None):  # noqa: ANN001
                raise WORKER.FrozenPrepareCancellation("early cancellation")

        _context, backend = self.sql_backend(Runner())
        backend._bind_sql_scope(
            step="migrate",
            attempt=0,
            started_event_sha256=WORKER.ZERO_SHA256,
            stage="pre-start-observe",
        )
        with (
            mock.patch.object(
                backend,
                "_sql_runtime_contract",
                return_value=object(),
            ),
            mock.patch.object(
                backend,
                "_cleanup_sql_with_cancellation_retry",
                return_value=([], None),
            ) as cleanup,
            mock.patch.object(
                WORKER.RESTORE,
                "_compose_environment",
                return_value=(dict(WORKER.RESTORE.SAFE_ENV), {}),
            ),
            mock.patch.object(
                WORKER.RESTORE,
                "_capture_runtime_path_identities",
                return_value={},
            ),
            mock.patch.object(
                WORKER.time,
                "monotonic",
                side_effect=(100.0, 101.0, 102.0),
            ),
            self.assertRaisesRegex(
                WORKER.FrozenPrepareCancellation,
                "early cancellation",
            ),
        ):
            backend._psql("SELECT 1", timeout=300)
        self.assertEqual(
            cleanup.call_args.kwargs["deadline"],
            102.0 + WORKER.CANCELLATION_MAX_WAIT_SECONDS,
        )

    def test_import_path_and_imported_module_hashes_are_pinned(self) -> None:
        self.assertEqual(
            Path(sys.path[0]).resolve(),
            WORKER.REPO_ROOT,
        )
        self.assertEqual(
            set(WORKER.TRUSTED_IMPORTED_MODULE_SHA256),
            {
                relative
                for _module, relative in (
                    WORKER.TRUSTED_IMPORTED_MODULE_PATHS.values()
                )
            },
        )
        for module, relative in (
            WORKER.TRUSTED_IMPORTED_MODULE_PATHS.values()
        ):
            path = Path(module.__file__)
            self.assertEqual(path, WORKER.REPO_ROOT / relative)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                WORKER.TRUSTED_IMPORTED_MODULE_SHA256[relative],
            )

    def test_runtime_role_inventory_rejects_any_direct_or_public_grant(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        queries: list[str] = []

        def role_rows() -> str:
            return "\n".join(
                "\t".join(
                    [
                        role,
                        "true",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "-1",
                        "infinity",
                    ]
                )
                for role in WORKER.EXPECTED_RUNTIME_ROLES["webapp_fi"]
            )

        def psql(sql: str) -> str:
            queries.append(sql)
            if "FROM pg_roles role" in sql:
                return role_rows()
            if sql == "SELECT current_database()":
                return "appdb"
            if "public_acl_inventory" in sql:
                return ""
            if "FROM (" in sql:
                return (
                    "table\tpublic\tusers\t\tDELETE\t"
                    "webapp_fi_observer\tfalse"
                )
            raise AssertionError(sql)

        backend._psql = psql
        inventory = backend._role_inventory()
        self.assertFalse(
            inventory["least_privilege_role_set_verified"]
        )
        self.assertEqual(inventory["excessive_grant_count"], 1)
        self.assertFalse(
            inventory["exact_release_grant_policy_verified"]
        )
        direct_query = next(
            query
            for query in queries
            if ") grants ORDER BY" in query
        )
        public_query = next(
            query
            for query in queries
            if "public_acl_inventory" in query
        )
        role_query = next(
            query for query in queries if "FROM pg_roles role" in query
        )
        for catalog in (
            "pg_class",
            "pg_attribute",
            "pg_proc",
            "pg_type",
            "pg_database",
            "pg_namespace",
            "pg_tablespace",
            "pg_language",
            "pg_foreign_data_wrapper",
            "pg_foreign_server",
            "pg_largeobject_metadata",
            "pg_parameter_acl",
            "pg_default_acl",
            "pg_auth_members",
        ):
            self.assertIn(catalog, direct_query)
            if catalog != "pg_auth_members":
                self.assertIn(catalog, public_query)
        for owner_catalog in (
            "pg_foreign_data_wrapper",
            "pg_foreign_server",
            "pg_event_trigger",
            "pg_extension",
            "pg_publication",
            "pg_subscription",
            "pg_statistic_ext",
            "pg_collation",
            "pg_conversion",
            "pg_operator",
            "pg_opclass",
            "pg_opfamily",
            "pg_ts_config",
            "pg_ts_dict",
            "pg_default_acl",
            "pg_user_mapping",
            "pg_shdepend",
        ):
            self.assertIn(owner_catalog, role_query)
        self.assertNotIn("namespace.nspname='public'", role_query)
        self.assertNotIn(
            "database_row.datname=current_database()",
            direct_query,
        )
        self.assertNotIn(
            "database_row.datname=current_database()",
            public_query,
        )
        public_language_branch = public_query.split(
            "FROM pg_language language",
            1,
        )[1].split("UNION ALL", 1)[0]
        self.assertIn(
            "WHEN language.lanpltrusted",
            public_language_branch,
        )
        self.assertIn(
            "ELSE coalesce(language.lanacl, '{}'::aclitem[])",
            public_language_branch,
        )
        self.assertNotIn(
            "parameter.parname='session_replication_role'",
            direct_query,
        )
        self.assertIn("membership.admin_option", direct_query)
        self.assertNotIn("membership.inherit_option", direct_query)
        self.assertNotIn("membership.set_option", direct_query)

        def public_psql(sql: str) -> str:
            if "FROM pg_roles role" in sql:
                return role_rows()
            if sql == "SELECT current_database()":
                return "appdb"
            if "public_acl_inventory" in sql:
                return "\n".join(
                    (
                        "schema\tpublic\tpublic\tCREATE\tfalse",
                        "foreign-server\tpg_catalog\tupstream\t"
                        "USAGE\tfalse",
                        "schema\tcustom\tcustom\tUSAGE\tfalse",
                        "database\t\tpostgres\tCONNECT\tfalse",
                        "schema\tpublic\tpublic\tUSAGE\tfalse",
                        "trusted-language\tpg_catalog\tplpgsql\t"
                        "USAGE\tfalse",
                        "trusted-language\tpg_catalog\tsql\t"
                        "USAGE\tfalse",
                    )
                )
            if "FROM (" in sql:
                return ""
            raise AssertionError(sql)

        backend._psql = public_psql
        inventory = backend._role_inventory()
        self.assertFalse(
            inventory["least_privilege_role_set_verified"]
        )
        self.assertEqual(
            inventory["unsafe_public_privilege_count"],
            7,
        )
        self.assertEqual(inventory["public_privilege_count"], 7)

    def test_expected_grants_require_canonical_projection_allowlists(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        canonical_tables = list(
            WORKER.C431_POLICY.PROJECTION_TABLES
        )
        table_allowlist = list(canonical_tables)
        field_allowlist = [
            (table, "id") for table in canonical_tables
        ]

        def psql(sql: str) -> str:
            if (
                "FROM pg_class class JOIN pg_namespace namespace"
                in sql
            ):
                return "\n".join(
                    f"{table}\tr" for table in canonical_tables
                )
            if (
                "table_name='dr_events'" in sql
                and "column_name<>'source_xid'" in sql
            ):
                return "id"
            if (
                "FROM public.dr_projection_table_allowlist"
                in sql
            ):
                return "\n".join(table_allowlist)
            if (
                "FROM information_schema.columns" in sql
                and "table_name IN (" in sql
            ):
                return "\n".join(
                    f"{table}\tid" for table in canonical_tables
                )
            if (
                "FROM public.dr_projection_field_allowlist"
                in sql
            ):
                return "\n".join(
                    f"{table}\t{column}"
                    for table, column in field_allowlist
                )
            if "procedure.oid=to_regprocedure" in sql:
                return (
                    "public."
                    "trading_bot_cleanup_expired_replay_nonces"
                    "(timestamp with time zone,integer)"
                )
            if sql == "SELECT current_database()":
                return "appdb"
            raise AssertionError(sql)

        backend._psql = psql
        self.assertTrue(backend._expected_release_grants())

        missing_table = table_allowlist.pop()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "table allowlist differs",
        ):
            backend._expected_release_grants()
        table_allowlist.append(missing_table)

        table_allowlist.append("poisoned_projection")
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "table allowlist differs",
        ):
            backend._expected_release_grants()
        table_allowlist[:] = canonical_tables

        removed = field_allowlist.pop()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "field allowlist differs",
        ):
            backend._expected_release_grants()
        field_allowlist.append(removed)

        field_allowlist.append(("users", "poisoned_field"))
        field_allowlist.sort()
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "field allowlist differs",
        ):
            backend._expected_release_grants()

    def test_post_migration_role_inventory_requires_exact_release_policy(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        backend.context = SimpleNamespace(
            document={"phase": "shadow_roles_post_migration"}
        )
        exact_grant = [
            "table",
            "public",
            "users",
            "",
            "SELECT",
            "webapp_fi_app",
            "false",
        ]

        def role_rows() -> str:
            return "\n".join(
                "\t".join(
                    [
                        role,
                        "true",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "-1",
                        "infinity",
                    ]
                )
                for role in WORKER.EXPECTED_RUNTIME_ROLES["webapp_fi"]
            )

        direct_rows = ["\t".join(exact_grant)]
        public_rows = ["type\tpublic\tstatus\tUSAGE\tfalse"]

        def psql(sql: str) -> str:
            if "FROM pg_roles role" in sql:
                return role_rows()
            if sql == "SELECT current_database()":
                return "appdb"
            if "public_acl_inventory" in sql:
                return "\n".join(public_rows)
            if (
                "SELECT type_row.typname FROM pg_type type_row" in sql
                and "namespace.nspname='public'" in sql
            ):
                return "status"
            if ") grants ORDER BY" in sql:
                return "\n".join(direct_rows)
            raise AssertionError(sql)

        backend._psql = psql
        with mock.patch.object(
            backend,
            "_expected_release_grants",
            return_value=[exact_grant],
        ):
            inventory = backend._role_inventory()
            self.assertTrue(
                inventory["exact_release_grant_policy_verified"]
            )
            self.assertTrue(
                inventory["exact_public_type_usage_verified"]
            )
            self.assertTrue(
                inventory["least_privilege_role_set_verified"]
            )
            self.assertEqual(inventory["excessive_grant_count"], 0)

            direct_rows.append(
                "table\tpublic\tusers\t\tUPDATE\t"
                "webapp_fi_app\tfalse"
            )
            inventory = backend._role_inventory()
            self.assertFalse(
                inventory["exact_release_grant_policy_verified"]
            )
            self.assertFalse(
                inventory["least_privilege_role_set_verified"]
            )
            self.assertEqual(inventory["grant_policy_delta_count"], 1)
            self.assertEqual(inventory["excessive_grant_count"], 1)

            direct_rows[:] = ["\t".join(exact_grant)]
            public_rows.append(
                "database\t\tpostgres\tCONNECT\tfalse"
            )
            inventory = backend._role_inventory()
            self.assertFalse(
                inventory["exact_release_grant_policy_verified"]
            )
            self.assertFalse(
                inventory["least_privilege_role_set_verified"]
            )
            self.assertEqual(
                inventory["unsafe_public_privilege_count"],
                1,
            )

    def test_unreviewed_invalid_index_is_rejected(self) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.concurrent_indexes = ("ix_reviewed",)
        backend.corridor = ("source", "target")
        backend._psql = lambda sql: "ix_foreign\tfalse\tfalse"
        with self.assertRaisesRegex(
            WORKER.FrozenPrepareWorkerError,
            "unreviewed invalid",
        ):
            backend._index_inventory()

    def test_ir_fence_readback_requires_runtime_triggers_and_writer_state(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_ir")
        backend.context = SimpleNamespace(
            document={"operation_id": OPERATION_ID}
        )

        def psql(sql: str) -> str:
            if "FROM public.dr_database_runtime" in sql:
                return (
                    "true\twebapp_ir\twebapp_ir_app\t"
                    "webapp_ir_projection\twebapp_ir_control\ttrue"
                )
            if "FROM pg_trigger" in sql:
                return "\n".join(
                    writer_trigger_row(table)
                    for table in WORKER.EXPECTED_WRITER_TRIGGER_TABLES
                )
            if "FROM public.webapp_writer_state" in sql:
                return (
                    "\t1\tfenced\t"
                    f"{CAMPAIGN_ID}\tproduction-shadow:{OPERATION_ID}\t"
                    "initialize WebApp-IR as an operation-bound locally "
                    "fenced standby\t0\t0"
                )
            if "FROM public.webapp_writer_transitions" in sql:
                return (
                    f"{CAMPAIGN_ID}\twebapp\tfence\twebapp_fi\t\t1\t1\t"
                    f"production-shadow:{OPERATION_ID}\t"
                    "initialize WebApp-IR as an operation-bound locally "
                    "fenced standby\t0"
                )
            raise AssertionError(sql)

        backend._psql = psql
        result = backend._database_fence_inventory()
        self.assertTrue(result["database_fenced"])
        self.assertTrue(result["writer_fenced"])
        self.assertEqual(result["unfenced_writer_count"], 0)

    def test_fence_readback_rejects_one_missing_writer_trigger(self) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        backend.context = SimpleNamespace(
            document={"operation_id": OPERATION_ID}
        )

        def psql(sql: str) -> str:
            if "FROM public.dr_database_runtime" in sql:
                return (
                    "true\twebapp_fi\twebapp_fi_app\t"
                    "webapp_fi_projection\twebapp_fi_control\ttrue"
                )
            if "FROM pg_trigger" in sql:
                return "\n".join(
                    writer_trigger_row(table)
                    for table in WORKER.EXPECTED_WRITER_TRIGGER_TABLES[:-1]
                )
            raise AssertionError(sql)

        backend._psql = psql
        result = backend._database_fence_inventory()
        self.assertFalse(result["database_fenced"])
        self.assertFalse(result["database_event_fence_verified"])

    def test_fence_readback_rejects_origin_only_writer_triggers(self) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        backend.context = SimpleNamespace(
            document={"operation_id": OPERATION_ID}
        )

        def psql(sql: str) -> str:
            if "FROM public.dr_database_runtime" in sql:
                return (
                    "true\twebapp_fi\twebapp_fi_app\t"
                    "webapp_fi_projection\twebapp_fi_control\ttrue"
                )
            if "FROM pg_trigger" in sql:
                return "\n".join(
                    writer_trigger_row(table, enabled="O")
                    for table in WORKER.EXPECTED_WRITER_TRIGGER_TABLES
                )
            raise AssertionError(sql)

        backend._psql = psql
        result = backend._database_fence_inventory()
        self.assertFalse(result["database_fenced"])
        self.assertFalse(result["database_event_fence_verified"])
        self.assertEqual(result["enabled_writer_trigger_count"], 0)

    def test_fence_readback_rejects_writer_function_body_drift(
        self,
    ) -> None:
        backend = object.__new__(WORKER.LocalDockerPrepareBackend)
        backend.manifest = SimpleNamespace(role="webapp_fi")
        backend.context = SimpleNamespace(
            document={"operation_id": OPERATION_ID}
        )

        def psql(sql: str) -> str:
            if "FROM public.dr_database_runtime" in sql:
                return (
                    "true\twebapp_fi\twebapp_fi_app\t"
                    "webapp_fi_projection\twebapp_fi_control\ttrue"
                )
            if "FROM pg_trigger" in sql:
                return "\n".join(
                    writer_trigger_row(
                        table,
                        body_sha256=SHA_A,
                    )
                    for table in WORKER.EXPECTED_WRITER_TRIGGER_TABLES
                )
            raise AssertionError(sql)

        backend._psql = psql
        result = backend._database_fence_inventory()
        self.assertFalse(result["database_fenced"])
        self.assertFalse(result["database_event_fence_verified"])


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
