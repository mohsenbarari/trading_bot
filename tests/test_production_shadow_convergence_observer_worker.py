from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest import mock

from core.sync_parity import business_snapshot_fingerprint
from scripts import production_shadow_convergence_observer_worker as MODULE
from scripts import production_shadow_convergence_runtime_targets as TARGETS


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "a" * 40


def raw_parity() -> dict[str, object]:
    record = {
        "identity_hash": "3" * 64,
        "identity_fields": ["id"],
        "business_hash": "4" * 64,
        "local_only_hash": "5" * 64,
        "volatile_hash": "6" * 64,
        "identity_label": "must-not-leave-worker",
    }
    def fingerprint(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "deep",
        "table_count": 1,
        "max_rows_per_table": 100,
        "tables": {
            "offers": {
                "table": "offers",
                "row_count": 1,
                "truncated": False,
                "duplicate_identity_count": 0,
                "duplicate_identity_hashes": [],
                "records_hash": fingerprint([{
                    "identity_hash": record["identity_hash"],
                    "business_hash": record["business_hash"],
                    "local_only_hash": record["local_only_hash"],
                    "volatile_hash": record["volatile_hash"],
                }]),
                "business_records_hash": fingerprint([{
                    "identity_hash": record["identity_hash"],
                    "business_hash": record["business_hash"],
                }]),
                "records": [record],
            }
        },
    }


def raw_snapshot(role: str, *, observed: datetime) -> dict[str, object]:
    peers = [item for item in MODULE.RUNTIME_SNAPSHOT_ROLES if item != role]
    return {
        "schema": "three-site-staging-convergence-site-snapshot-v1",
        "campaign_id": CAMPAIGN_ID,
        "release_sha": RELEASE_SHA,
        "plan_sha256": "b" * 64,
        "site": role,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "producer_epoch": 1,
        "source_streams": [
            {
                "destination_site": peer,
                "source_sequence": 0,
                "source_transaction_hash": "0" * 64,
            }
            for peer in peers
        ],
        "destination_streams": [
            {
                "origin_site": peer,
                "producer_epoch": 1,
                "received_sequence": 0,
                "applied_sequence": 0,
                "received_transaction_hash": "0" * 64,
                "applied_transaction_hash": "0" * 64,
            }
            for peer in peers
        ],
        "unresolved_conflict_count": 0,
        "database_snapshot": raw_parity(),
        "blob_records": [],
    }


def stream_reader(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


class CompletedCollectorProcess:
    def __init__(self, *, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stream_reader(stdout)
        self.stderr = stream_reader(stderr)
        self.returncode = returncode
        self.kill_count = 0
        self.wait_count = 0

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_count += 1
        return self.returncode


def host_identity_proof(
    request: dict[str, object],
    *,
    observed: datetime = NOW,
    observed_host: str | None = None,
) -> dict[str, object]:
    expected_host = str(request["expected_host"])
    document: dict[str, object] = {
        "schema": MODULE.HOST_IDENTITY_PROOF_SCHEMA,
        "expected_host": expected_host,
        "observed_host": observed_host or expected_host,
        "address_family": "inet",
        "interface": "eth0",
        "collector": "kernel-ip-json",
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "host_identity_proof_sha256": MODULE.ZERO_SHA256,
    }
    document["host_identity_proof_sha256"] = MODULE._host_identity_proof_digest(document)
    return document


class RoleObserverWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.project_root = self.root / "project"
        self.secret_root = self.root / "secret"
        self.project_root.mkdir(mode=0o700)
        self.secret_root.mkdir(mode=0o700)
        self.patches = (
            mock.patch.object(MODULE, "PROJECT_ROOT_PREFIX", self.project_root),
            mock.patch.object(MODULE, "SECRET_ROOT_PREFIX", self.secret_root),
        )
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def runtime_environment(self, role: str, *, release_sha: str) -> dict[str, str]:
        username = f"{role}_observer"
        database = f"{role}_shadow"
        password = "test-password"
        return {
            "TZ": "UTC",
            "ENVIRONMENT": "production",
            "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
            "THREE_SITE_DR_ENABLED": "true",
            "DR_EVENT_PROTOCOL_ENABLED": "true",
            "DR_EVENT_PROTOCOL_STRICT": "true",
            "RELEASE_SHA": release_sha,
            "SERVER_MODE": "foreign" if role == "bot_fi" else "iran",
            "LOGICAL_AUTHORITY": "foreign" if role == "bot_fi" else "webapp",
            "PHYSICAL_SITE": role,
            "DATABASE_URL": f"postgresql+asyncpg://{username}:{password}@{role}_db/{database}",
            "SYNC_DATABASE_URL": f"postgresql://{username}:{password}@{role}_db/{database}",
            "POSTGRES_USER": username,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": database,
            "FRONTEND_URL": "https://example.invalid",
            "JWT_SECRET_KEY": "test-only-observer-key",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "DR_PRODUCER_EPOCH": "1",
            "DR_BLOB_ROOT": "/srv/trading-bot/uploads/blobs",
        }

    def runtime_target_set(self, *, release_sha: str) -> dict[str, object]:
        rows = {
            role: TARGETS.derive_runtime_target_binding(
                self.runtime_environment(role, release_sha=release_sha),
                role=role,
                release_sha=release_sha,
            )["runtime_target_row"]
            for role in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
        }
        document: dict[str, object] = {
            "schema": TARGETS.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": release_sha,
            "canonical_compose_sha256": "b" * 64,
            "roles": rows,
            "target_set_sha256": "0" * 64,
        }
        document["target_set_sha256"] = TARGETS.runtime_target_set_digest(document)
        return document

    def runtime_target_binding(self, role: str, *, release_sha: str) -> dict[str, object]:
        targets = self.runtime_target_set(release_sha=release_sha)
        return TARGETS.build_observer_runtime_target_binding(
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            manifest_sha256="7" * 64,
            canonical_compose_sha256="b" * 64,
            role=role,
            convergence_runtime_targets=TARGETS.runtime_target_set_descriptor(targets),
            runtime_target_row=targets["roles"][role],
            role_material_sha256="c" * 64,
            role_runtime_image_ids={
                "app": "sha256:d" + "0" * 63,
                "postgres": "sha256:e" + "0" * 63,
                "redis": "sha256:f" + "0" * 63,
                "nginx": "sha256:a" + "0" * 63,
            },
        )

    def request(self, *, role: str = "bot_fi") -> dict[str, object]:
        runtime_binding = (
            self.runtime_target_binding(role, release_sha=RELEASE_SHA)["binding_sha256"]
            if role in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
            else None
        )
        return MODULE.build_request(
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=TREE_SHA,
            manifest_sha256="7" * 64,
            runtime_target_binding_sha256=runtime_binding,
            plan_sha256="b" * 64,
            approval_sha256="8" * 64,
            role=role,
            expected_host="127.0.0.1",
            phase_started_at=NOW - timedelta(seconds=10),
            worker_sha256="9" * 64,
            max_rows_per_table=100,
        )

    def install_collector_runtime_config(
        self,
        request: dict[str, object],
    ) -> tuple[Path, dict[str, str]]:
        role = str(request["role"])
        environment = self.runtime_environment(role, release_sha=str(request["release_sha"]))
        targets = self.runtime_target_set(release_sha=str(request["release_sha"]))
        binding = self.runtime_target_binding(role, release_sha=str(request["release_sha"]))
        self.assertEqual(request["runtime_target_binding_sha256"], binding["binding_sha256"])
        document: dict[str, object] = {
            "schema": MODULE.COLLECTOR_RUNTIME_CONFIG_SCHEMA,
            "campaign_id": request["campaign_id"],
            "operation_id": request["operation_id"],
            "release_sha": request["release_sha"],
            "role": role,
            "request_sha256": request["request_sha256"],
            "runtime_target_binding_sha256": binding["binding_sha256"],
            "environment": environment,
            "config_sha256": MODULE.ZERO_SHA256,
        }
        document["config_sha256"] = MODULE._runtime_config_digest(document)
        path = MODULE._canonical_collector_runtime_config_path(request)
        path.parent.mkdir(mode=0o700, parents=True)
        directory = path.parent
        while directory != self.secret_root.parent:
            directory.chmod(0o700)
            if directory == self.secret_root:
                break
            directory = directory.parent
        path.write_bytes(MODULE._canonical_json(document))
        path.chmod(0o600)
        binding_path = MODULE._canonical_runtime_target_binding_path(request)
        binding_path.write_bytes(TARGETS._canonical_json(binding))
        binding_path.chmod(0o600)
        targets_path = MODULE._canonical_runtime_target_set_path(request)
        targets_path.write_bytes(TARGETS._canonical_json(targets))
        targets_path.chmod(0o600)
        return path, environment

    def install_compose_execution_inputs(self, request: dict[str, object]) -> dict[str, object]:
        role = str(request["role"])
        operation = str(request["operation_id"])
        self.install_collector_runtime_config(request)
        binding = self.runtime_target_binding(role, release_sha=str(request["release_sha"]))
        project_name = MODULE._compose_role_project_name(operation_id=operation, role=role)
        runtime_dir = MODULE._canonical_collector_runtime_directory(request)
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = runtime_dir
        while directory != self.secret_root.parent:
            directory.chmod(0o700)
            if directory == self.secret_root:
                break
            directory = directory.parent
        release_root = f"{MODULE.PROJECT_ROOT_PREFIX}/{operation}/releases/{request['release_sha']}"
        input_root = f"{MODULE.SECRET_ROOT_PREFIX}/{operation}/convergence-observer-runtime/{role}"
        immutable_compose_path = MODULE._canonical_compose_execution_plan_path(request).parent / "compose-observer-execution.yml"
        immutable_environment_path = MODULE._canonical_compose_execution_plan_path(request).parent / "compose-observer-execution.env"
        collector_path = (
            Path(release_root)
            / MODULE.CONTAINER_COLLECTOR_RELATIVE
        )
        collector_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        collector_bytes = (
            Path(__file__).resolve().parents[1]
            / MODULE.CONTAINER_COLLECTOR_RELATIVE
        ).read_bytes()
        collector_path.write_bytes(collector_bytes)
        collector_path.chmod(0o600)
        collector_delegate_path = (
            Path(release_root) / "scripts/collect_three_site_staging_convergence_snapshot.py"
        )
        collector_delegate_bytes = (
            Path(__file__).resolve().parents[1]
            / "scripts/collect_three_site_staging_convergence_snapshot.py"
        ).read_bytes()
        collector_delegate_path.write_bytes(collector_delegate_bytes)
        collector_delegate_path.chmod(0o600)
        core_init_path = Path(release_root) / "core" / "__init__.py"
        core_init_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        core_init_bytes = b"# fixture core package\n"
        core_init_path.write_bytes(core_init_bytes)
        core_init_path.chmod(0o600)
        models_init_path = Path(release_root) / "models" / "__init__.py"
        models_init_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        models_init_bytes = b"# fixture models package\n"
        models_init_path.write_bytes(models_init_bytes)
        models_init_path.chmod(0o600)
        source_manifest = {
            "schema": "production-shadow-container-collector-source-manifest-v1",
            "release_sha": request["release_sha"],
            "release_tree_sha": request["release_tree_sha"],
            "files": {
                "scripts/collect_production_shadow_compose_runtime_snapshot.py": hashlib.sha256(collector_bytes).hexdigest(),
                "scripts/collect_three_site_staging_convergence_snapshot.py": hashlib.sha256(collector_delegate_bytes).hexdigest(),
                "core/__init__.py": hashlib.sha256(core_init_bytes).hexdigest(),
                "models/__init__.py": hashlib.sha256(models_init_bytes).hexdigest(),
            },
            "source_manifest_sha256": "0" * 64,
        }
        source_manifest["source_manifest_sha256"] = MODULE._sha256({
            key: value for key, value in source_manifest.items() if key != "source_manifest_sha256"
        })
        source_manifest_path = runtime_dir / "collector-source-manifest.json"
        source_manifest_bytes = MODULE._canonical_json(source_manifest)
        source_manifest_path.write_bytes(source_manifest_bytes)
        source_manifest_path.chmod(0o600)
        compose_bytes = (
            "services:\n"
            f"  {role}_sync_observer:\n"
            f"    image: {binding['role_runtime_image_ids']['app']}\n"
            "    pull_policy: never\n"
            f"    env_file: [{immutable_environment_path}]\n"
            "    command: [python, -c, \"raise SystemExit('invoke with docker compose run')\"]\n"
        ).encode("ascii")
        environment_bytes = b"FIXTURE=1\n"
        immutable_compose_path.write_bytes(compose_bytes)
        immutable_environment_path.write_bytes(environment_bytes)
        immutable_compose_path.chmod(0o600)
        immutable_environment_path.chmod(0o600)
        Path(release_root).mkdir(mode=0o700, parents=True, exist_ok=True)
        Path(release_root).chmod(0o700)
        plan: dict[str, object] = {
            "schema": MODULE.COMPOSE_EXECUTION_PLAN_SCHEMA,
            "status": "planned-not-executed",
            "campaign_id": request["campaign_id"],
            "operation_id": operation,
            "release_sha": request["release_sha"],
            "manifest_sha256": request["manifest_sha256"],
            "canonical_compose_sha256": binding["canonical_compose_sha256"],
            "role": role,
            "service": f"{role}_sync_observer",
            "profile": f"{role.replace('_', '-')}-observe",
            "project_name": project_name,
            "role_compose_path": str(immutable_compose_path),
            "role_compose_sha256": hashlib.sha256(compose_bytes).hexdigest(),
            "role_environment_path": str(immutable_environment_path),
            "role_environment_sha256": hashlib.sha256(environment_bytes).hexdigest(),
            "collector_path": str(collector_path),
            "collector_sha256": hashlib.sha256(collector_bytes).hexdigest(),
            "collector_delegate_sha256": hashlib.sha256(collector_delegate_bytes).hexdigest(),
            "collector_closure_sha256": MODULE._sha256({
                "collector_sha256": hashlib.sha256(collector_bytes).hexdigest(),
                "delegate_sha256": hashlib.sha256(collector_delegate_bytes).hexdigest(),
                "source_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
            }),
            "collector_source_manifest_path": str(source_manifest_path),
            "collector_source_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
            "collector_argv": MODULE._compose_container_collector_argv(
                campaign_id=str(request["campaign_id"]),
                operation_id=operation,
                release_sha=str(request["release_sha"]),
                source_manifest_path=str(source_manifest_path),
            ),
            "config_probe_argv": [
                MODULE.COMPOSE_DOCKER, "compose", "--project-name", project_name,
                "--env-file", str(immutable_environment_path), "--file",
                str(immutable_compose_path), "--profile", f"{role.replace('_', '-')}-observe",
                "config", "--format", "json",
            ],
            "resolved_observer_service_sha256": "0" * 64,
            "role_material_sha256": binding["role_material_sha256"],
            "role_material_inspection_sha256": "0" * 64,
            "runtime_target_binding_sha256": request["runtime_target_binding_sha256"],
            "runtime_image_ids": binding["role_runtime_image_ids"],
            "internal_network": role,
            "network_name": MODULE._compose_network_name(operation_id=operation, role=role),
            "release_mount": MODULE._compose_mount(release_root),
            "runtime_input_mount": MODULE._compose_mount(input_root),
            "container_id_file": MODULE._compose_container_id_file(operation_id=operation, role=role),
            "compose_argv": [],
            "cleanup_probe_argv": [],
            "timeout_seconds": MODULE.COMPOSE_EXECUTION_TIMEOUT_SECONDS,
            "max_stdout_bytes": MODULE.COMPOSE_EXECUTION_MAX_STDOUT_BYTES,
            "max_stderr_bytes": MODULE.COMPOSE_EXECUTION_MAX_STDERR_BYTES,
            "production_mutation_forbidden": True,
            "object_storage_contact_forbidden": True,
            "plan_sha256": MODULE.ZERO_SHA256,
        }
        plan["role_material_inspection_sha256"] = MODULE._compose_inspection_digest(
            operation_id=operation,
            role=role,
            role_material_sha256=str(plan["role_material_sha256"]),
            role_compose_sha256=str(plan["role_compose_sha256"]),
            role_environment_sha256=str(plan["role_environment_sha256"]),
        )
        plan["compose_argv"] = [
            MODULE.COMPOSE_DOCKER, "compose", "--project-name", project_name, "--env-file",
            plan["role_environment_path"], "--file", plan["role_compose_path"], "--profile",
            plan["profile"], "run", "--cidfile", plan["container_id_file"], "--rm",
            "--no-deps", plan["service"],
        ]
        resolved_service = {
            "image": binding["role_runtime_image_ids"]["app"],
            "pull_policy": "never",
            "profiles": [f"{role.replace('_', '-')}-observe"],
            "restart": "no",
            "command": ["python", "-c", "raise SystemExit('invoke with docker compose run')"],
            "depends_on": {f"{role}_db": {"condition": "service_healthy"}},
            "networks": [role],
            "volumes": [
                MODULE._compose_mount(release_root),
                MODULE._compose_mount(input_root),
            ],
            "env_file": [str(immutable_environment_path)],
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        plan["resolved_observer_service_sha256"] = MODULE._sha256(resolved_service)
        plan["cleanup_probe_argv"] = [
            MODULE.COMPOSE_DOCKER, "ps", "--all", "--quiet", "--filter",
            f"label=com.docker.compose.project={project_name}", "--filter",
            "label=com.docker.compose.oneoff=True",
        ]
        plan["plan_sha256"] = MODULE._compose_plan_digest(plan)
        material: dict[str, object] = {
            "schema": MODULE.COMPOSE_EXECUTION_MATERIAL_SCHEMA,
            "campaign_id": request["campaign_id"],
            "operation_id": operation,
            "release_sha": request["release_sha"],
            "manifest_sha256": request["manifest_sha256"],
            "role": role,
            "runtime_target_binding_sha256": request["runtime_target_binding_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "role_material_archive_inspection_sha256": "9" * 64,
            "collector_source_manifest_sha256": plan["collector_source_manifest_sha256"],
            "material_sha256": MODULE.ZERO_SHA256,
        }
        material["material_sha256"] = MODULE._compose_material_digest(material)
        plan_path = MODULE._canonical_compose_execution_plan_path(request)
        material_path = MODULE._canonical_compose_execution_material_path(request)
        plan_path.write_bytes(MODULE._canonical_json(plan))
        material_path.write_bytes(MODULE._canonical_json(material))
        plan_path.chmod(0o600)
        material_path.chmod(0o600)
        return plan

    def install_release_collector(self, request: dict[str, object], payload: bytes = b"# fixed release collector\n") -> Path:
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role=str(request["role"]),
        )
        collector_path = paths["release_root"] / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        paths["release_root"].chmod(0o700)
        collector_path.write_bytes(payload)
        collector_path.chmod(0o600)
        return collector_path

    def test_plan_does_not_contact_runtime_or_transport(self) -> None:
        plan = MODULE.build_plan(self.request())
        self.assertEqual(plan["default_action"], "plan")
        self.assertFalse(plan["worker_ssh_io"])
        self.assertFalse(plan["worker_object_storage_io"])
        self.assertFalse(plan["worker_peer_network_io"])
        self.assertEqual(plan["supported_observations"], ["database_parity", "dr_convergence"])
        self.assertIn("queue_state", plan["unavailable_observations"])

    def test_runtime_target_validation_never_executes_held_git_blob(self) -> None:
        request = self.request()
        self.install_collector_runtime_config(request)
        marker = self.root / "dynamic-contract-bypass"
        payload = (
            b"__import__('builtins').open(" + repr(os.fspath(marker)).encode("ascii")
            + b", 'w').write('unexpected')\n"
        )
        with mock.patch.object(
            MODULE,
            "_run_held_release_git",
            return_value=payload,
        ) as read_blob:
            environment = MODULE._collector_environment(request, release_root_descriptor=7)
        self.assertEqual(environment["PHYSICAL_SITE"], "bot_fi")
        self.assertFalse(marker.exists())
        read_blob.assert_not_called()

    def test_runtime_target_validation_never_executes_dynamic_open_or_import(self) -> None:
        request = self.request()
        self.install_collector_runtime_config(request)
        marker = self.root / "dynamic-open-bypass"
        payload = (
            b"open(" + repr(os.fspath(marker)).encode("ascii")
            + b", 'w').write(__import__('os').getcwd())\n"
        )
        with mock.patch.object(
            MODULE,
            "_run_held_release_git",
            return_value=payload,
        ) as read_blob:
            MODULE._collector_environment(request, release_root_descriptor=7)
        self.assertFalse(marker.exists())
        read_blob.assert_not_called()

    def test_static_runtime_target_helper_matches_published_contract(self) -> None:
        environment = self.runtime_environment("bot_fi", release_sha=RELEASE_SHA)
        targets = self.runtime_target_set(release_sha=RELEASE_SHA)
        binding = self.runtime_target_binding("bot_fi", release_sha=RELEASE_SHA)
        self.assertEqual(
            MODULE._derive_runtime_target_binding(
                environment,
                role="bot_fi",
                release_sha=RELEASE_SHA,
            ),
            TARGETS.derive_runtime_target_binding(
                environment,
                role="bot_fi",
                release_sha=RELEASE_SHA,
            ),
        )
        self.assertEqual(
            MODULE._validate_runtime_target_payload_descriptor(
                TARGETS._canonical_json(targets),
                binding["convergence_runtime_targets"],
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                canonical_compose_sha256="b" * 64,
                label="test target set",
            ),
            targets,
        )
        self.assertEqual(
            MODULE._validate_observer_runtime_target_binding(
                binding,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                manifest_sha256="7" * 64,
                role="bot_fi",
                label="test binding",
            ),
            binding,
        )

    def test_observe_has_no_public_clock_override(self) -> None:
        with self.assertRaises(TypeError):
            MODULE.observe(self.request(), now=NOW)

    def test_observe_has_no_public_snapshot_collector_override(self) -> None:
        async def hostile_collector(_request):  # noqa: ANN001
            return raw_snapshot("bot_fi", observed=NOW)

        with self.assertRaises(TypeError):
            MODULE.observe(self.request(), snapshot_collector=hostile_collector)

    def test_observe_has_no_public_command_or_worker_path_override(self) -> None:
        with self.assertRaises(TypeError):
            MODULE.observe(self.request(), runner=mock.Mock())
        with self.assertRaises(TypeError):
            MODULE.observe(self.request(), executing_worker_path=Path("/outside"))

    def test_observe_uses_real_collector_output_and_redacts_identity_label(self) -> None:
        request = self.request()
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }

        async def collector(_request):  # noqa: ANN001
            return raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))

        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
        ):
            document = asyncio.run(
                MODULE._observe_for_test(
                    request,
                    snapshot_collector=collector,
                    host_identity_proof_collector=host_identity_proof,
                )
            )
        self.assertEqual(document["available_observations"], ["database_parity", "dr_convergence"])
        self.assertFalse(document["production_mutated"])
        self.assertFalse(document["worker_transport_contacted"])
        self.assertEqual(document["host_identity_proof"]["observed_host"], "127.0.0.1")
        snapshot = document["runtime_snapshot"]
        self.assertIsInstance(snapshot, dict)
        record = snapshot["redacted_parity_snapshot"]["tables"]["offers"]["records"][0]
        self.assertEqual(set(record), {"identity_hash", "business_hash", "local_only_hash", "volatile_hash"})
        self.assertNotIn("identity_label", str(snapshot))
        self.assertEqual(
            snapshot["database"]["business_fingerprint_sha256"],
            business_snapshot_fingerprint(snapshot["redacted_parity_snapshot"]),
        )
        MODULE.validate_attestation(document, request=request, now=NOW)
        self.assertEqual(document["compose_execution"]["cleanup_verified"], True)

    def test_default_runtime_observation_fails_closed_without_compose_material(self) -> None:
        request = self.request()
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }
        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
            mock.patch.object(
                MODULE,
                "_collect_runtime_snapshot_from_verified_release",
                side_effect=AssertionError("host Python collector must not be a fallback"),
            ) as legacy_collector,
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "Compose execution"):
                asyncio.run(
                    MODULE._observe_for_test(
                        request,
                        host_identity_proof_collector=host_identity_proof,
                    )
                )
        legacy_collector.assert_not_called()

    def test_compose_executor_uses_only_fixed_plan_and_inspect_receipt(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        self.assertEqual(
            plan["collector_argv"][-3:],
            [
                "--source-manifest-path",
                plan["collector_source_manifest_path"],
                "--plan-sha256",
            ],
        )
        cid_path = Path(str(plan["container_id_file"]))
        cid = "a" * 64
        observed_commands: list[list[str]] = []
        test_case = self

        class FakeComposeProcess:
            returncode = 0

            def poll(self) -> None:
                return None

            def communicate(self, *, timeout: int) -> tuple[bytes, bytes]:
                test_case.assertEqual(timeout, MODULE.COMPOSE_EXECUTION_TIMEOUT_SECONDS)
                return (
                    json.dumps(raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))).encode("ascii"),
                    b"",
                )

            def kill(self) -> None:
                raise AssertionError("successful mocked Compose process must not be killed")

        def spawn(argv, **_kwargs):  # noqa: ANN001
            self.assertEqual(
                argv,
                [
                    *plan["compose_argv"],
                    *plan["collector_argv"],
                    request["plan_sha256"],
                    "--max-rows-per-table",
                    str(request["max_rows_per_table"]),
                ],
            )
            cid_path.write_text(cid + "\n", encoding="ascii")
            cid_path.chmod(0o600)
            return FakeComposeProcess()

        operation_label = "trading-bot.production.operation-id"

        def runner(argv, **_kwargs):  # noqa: ANN001
            observed_commands.append(list(argv))
            if argv == plan["cleanup_probe_argv"]:
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if argv == plan["config_probe_argv"]:
                resolved_service = {
                    "image": plan["runtime_image_ids"]["app"],
                    "pull_policy": "never",
                    "profiles": [plan["profile"]],
                    "restart": "no",
                    "command": ["python", "-c", "raise SystemExit('invoke with docker compose run')"],
                    "depends_on": {f"{request['role']}_db": {"condition": "service_healthy"}},
                    "networks": [request["role"]],
                    "volumes": [plan["release_mount"], plan["runtime_input_mount"]],
                    "env_file": [plan["role_environment_path"]],
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                }
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({
                        "services": {plan["service"]: resolved_service},
                        "networks": {
                            plan["internal_network"]: {
                                "labels": {operation_label: OPERATION_ID},
                                "internal": True,
                            }
                        },
                    }).encode("ascii"),
                    b"",
                )
            if argv == [MODULE.COMPOSE_DOCKER, "inspect", cid]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps([{
                        "Id": cid,
                        "Image": plan["runtime_image_ids"]["app"],
                        "Config": {"Labels": {
                            "com.docker.compose.project": plan["project_name"],
                            "com.docker.compose.service": plan["service"],
                            "com.docker.compose.oneoff": "True",
                            operation_label: OPERATION_ID,
                        }, "Image": plan["runtime_image_ids"]["app"]},
                        "NetworkSettings": {"Networks": {plan["network_name"]: {}}},
                    }]).encode("ascii"),
                    b"",
                )
            if argv == [MODULE.COMPOSE_DOCKER, "network", "inspect", plan["network_name"]]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps([{
                        "Id": "b" * 64,
                        "Name": plan["network_name"],
                        "Internal": True,
                        "Labels": {operation_label: OPERATION_ID},
                    }]).encode("ascii"),
                    b"",
                )
            raise AssertionError(f"unexpected subprocess argv: {argv!r}")

        with (
            mock.patch.object(MODULE.subprocess, "Popen", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_collect_runtime_snapshot_from_verified_release",
                side_effect=AssertionError("host Python collector must not run"),
            ) as legacy_collector,
        ):
            snapshot, proof = MODULE._execute_compose_runtime_observer(request, runner=runner)
        self.assertEqual(snapshot["site"], "bot_fi")
        self.assertEqual(proof["execution_plan_sha256"], plan["plan_sha256"])
        self.assertTrue(proof["cleanup_verified"])
        self.assertFalse(cid_path.exists())
        self.assertEqual(observed_commands.count(plan["cleanup_probe_argv"]), 2)
        legacy_collector.assert_not_called()

    def test_compose_executor_rejects_changed_container_collector_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        collector_path = Path(str(plan["collector_path"]))
        collector_path.write_bytes(b"changed")
        collector_path.chmod(0o600)
        with mock.patch.object(MODULE.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "container collector digest differs",
            ):
                MODULE._execute_compose_runtime_observer(
                    request,
                    runner=mock.Mock(),
                )
        popen.assert_not_called()

    def test_compose_executor_rejects_changed_container_collector_delegate_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        delegate = Path(str(plan["collector_path"])).parent / "collect_three_site_staging_convergence_snapshot.py"
        delegate.write_bytes(b"changed")
        delegate.chmod(0o600)
        with mock.patch.object(MODULE.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "collector delegate digest differs",
            ):
                MODULE._execute_compose_runtime_observer(request, runner=mock.Mock())
        popen.assert_not_called()

    def test_compose_executor_rejects_source_manifest_symlink_ancestor_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        release_root = Path(str(plan["collector_path"])).parents[1]
        scripts = release_root / "scripts"
        parked = release_root / "scripts-real"
        scripts.rename(parked)
        scripts.symlink_to(parked, target_is_directory=True)
        with mock.patch.object(MODULE.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "collector source entry is unavailable|release-relative file is unavailable",
            ):
                MODULE._execute_compose_runtime_observer(request, runner=mock.Mock())
        popen.assert_not_called()

    def test_compose_executor_rejects_missing_source_manifest_entry_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        release_root = Path(str(plan["collector_path"])).parents[1]
        (release_root / "core" / "__init__.py").unlink()
        with mock.patch.object(MODULE.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "collector source entry is unavailable|release-relative file is unavailable",
            ):
                MODULE._execute_compose_runtime_observer(request, runner=mock.Mock())
        popen.assert_not_called()

    def test_compose_executor_rejects_source_manifest_entry_mutation_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        release_root = Path(str(plan["collector_path"])).parents[1]
        core_init = release_root / "core" / "__init__.py"
        original_read = MODULE.os.read
        changed = False

        def mutate_after_read(descriptor, size):  # noqa: ANN001
            nonlocal changed
            payload = original_read(descriptor, size)
            if payload == b"# fixture core package\n" and not changed:
                core_init.write_bytes(b"# mutated core package\n")
                core_init.chmod(0o600)
                changed = True
            return payload

        with (
            mock.patch.object(MODULE.os, "read", side_effect=mutate_after_read),
            mock.patch.object(MODULE.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "changed while read"):
                MODULE._execute_compose_runtime_observer(request, runner=mock.Mock())
        self.assertTrue(changed)
        popen.assert_not_called()

    def test_compose_executor_rejects_source_manifest_release_root_replacement_before_popen(self) -> None:
        request = self.request()
        plan = self.install_compose_execution_inputs(request)
        release_root = Path(str(plan["collector_path"])).parents[1]
        parked = release_root.with_name(f"{release_root.name}-replaced")
        original_read = MODULE.os.read
        changed = False

        def replace_root_after_read(descriptor, size):  # noqa: ANN001
            nonlocal changed
            payload = original_read(descriptor, size)
            if payload == b"# fixture core package\n" and not changed:
                release_root.rename(parked)
                release_root.mkdir(mode=0o700)
                changed = True
            return payload

        with (
            mock.patch.object(MODULE.os, "read", side_effect=replace_root_after_read),
            mock.patch.object(MODULE.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "release root.*changed while read"):
                MODULE._execute_compose_runtime_observer(request, runner=mock.Mock())
        self.assertTrue(changed)
        popen.assert_not_called()

    def test_request_cannot_carry_caller_authored_observation_values(self) -> None:
        hostile = self.request()
        hostile["snapshot"] = {"database_business_drift_count": 0}
        with self.assertRaises(MODULE.ConvergenceRoleObserverError):
            MODULE.validate_request(hostile, now=NOW)

    def test_request_requires_a_canonical_expected_host_ipv4(self) -> None:
        hostile = self.request()
        hostile["expected_host"] = "localhost"
        hostile["request_sha256"] = MODULE._request_digest(hostile)
        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "IPv4"):
            MODULE.validate_request(hostile, now=NOW)

    def test_mismatched_local_ip_fails_before_runtime_collector(self) -> None:
        request = self.request()
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }
        collector_calls: list[str] = []

        async def collector(_request):  # noqa: ANN001
            collector_calls.append("collector")
            return raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))

        def mismatch_runner(argv, **_kwargs):  # noqa: ANN001
            self.assertEqual(argv, [str(Path(MODULE.IP).resolve()), "-j", "-4", "addr", "show"])
            return subprocess.CompletedProcess(
                argv,
                0,
                b'[{"ifname":"eth0","addr_info":[{"family":"inet","local":"127.0.0.2"}]}]',
                b"",
            )

        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "not uniquely assigned"):
                asyncio.run(
                    MODULE._observe_for_test(
                        request,
                        snapshot_collector=collector,
                        runner=mismatch_runner,
                    )
                )
        self.assertEqual(collector_calls, [])

    def test_kernel_ip_proof_is_bound_to_the_local_expected_address(self) -> None:
        request = self.request()

        def runner(argv, **_kwargs):  # noqa: ANN001
            self.assertEqual(argv, [str(Path(MODULE.IP).resolve()), "-j", "-4", "addr", "show"])
            return subprocess.CompletedProcess(
                argv,
                0,
                b'[{"ifname":"eth0","addr_info":[{"family":"inet","local":"127.0.0.1"}]}]',
                b"",
            )

        with mock.patch.object(MODULE, "_utcnow", return_value=NOW):
            proof = MODULE._collect_local_host_identity_proof(request, runner=runner)
        self.assertEqual(proof["observed_host"], request["expected_host"])
        self.assertEqual(proof["interface"], "eth0")
        MODULE.validate_host_identity_proof(proof, request=request, now=NOW)

    def test_kernel_ip_probe_rejects_wrong_local_address(self) -> None:
        request = self.request()

        def runner(argv, **_kwargs):  # noqa: ANN001
            return subprocess.CompletedProcess(
                argv,
                0,
                b'[{"ifname":"eth0","addr_info":[{"family":"inet","local":"127.0.0.2"}]}]',
                b"",
            )

        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "not uniquely assigned"):
            MODULE._collect_local_host_identity_proof(request, runner=runner)

    def test_semantic_reduction_ignores_preloaded_legacy_modules(self) -> None:
        request = self.request()
        hostile_validator = ModuleType("scripts.build_three_site_staging_convergence_evidence")
        hostile_validator._validate_snapshot = mock.Mock(side_effect=AssertionError("must not run"))  # type: ignore[attr-defined]
        hostile_parity = ModuleType("core.sync_parity")
        hostile_parity.business_snapshot_fingerprint = mock.Mock(  # type: ignore[attr-defined]
            side_effect=AssertionError("must not run")
        )
        with mock.patch.dict(
            sys.modules,
            {
                "scripts.build_three_site_staging_convergence_evidence": hostile_validator,
                "core.sync_parity": hostile_parity,
            },
        ):
            summary = MODULE._summarize_runtime_snapshot(
                raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2)),
                request=request,
            )
        self.assertEqual(summary["database"]["row_count"], 1)
        hostile_validator._validate_snapshot.assert_not_called()  # type: ignore[attr-defined]
        hostile_parity.business_snapshot_fingerprint.assert_not_called()  # type: ignore[attr-defined]

    def test_fixed_isolated_collector_does_not_use_preloaded_or_outside_module(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        collector_path = release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        release_root.chmod(0o700)
        collector_path.write_text("# fixed release collector\n", encoding="ascii")
        collector_path.chmod(0o600)
        _, runtime_environment = self.install_collector_runtime_config(request)
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        observed_cwds: list[Path] = []

        async def spawn(*argv, **kwargs):  # noqa: ANN001, ANN202
            calls.append((argv, kwargs))
            observed_cwds.append(Path(str(kwargs["cwd"])).resolve())
            return CompletedCollectorProcess(
                stdout=json.dumps(
                    raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2)),
                    sort_keys=True,
                ).encode("ascii")
            )

        hostile_collector = ModuleType("scripts.collect_three_site_staging_convergence_snapshot")
        hostile_collector.collect = mock.Mock(side_effect=AssertionError("must not import"))  # type: ignore[attr-defined]
        expected_collector_sha256 = hashlib.sha256(collector_path.read_bytes()).hexdigest()
        caller_cwd = self.root / "caller-cwd"
        caller_cwd.mkdir(mode=0o700)
        (caller_cwd / ".env").write_text(
            "DATABASE_URL=postgresql+asyncpg://caller:caller@outside/caller\n",
            encoding="ascii",
        )
        previous_cwd = Path.cwd()
        try:
            with (
                mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
                mock.patch.object(
                    MODULE,
                    "_expected_release_file_sha256",
                    return_value=expected_collector_sha256,
                ),
                mock.patch.object(MODULE, "_verify_held_release_git_state"),
                mock.patch.dict(
                    os.environ,
                    {
                        "PYTHONPATH": "/outside",
                        "LD_PRELOAD": "/outside.so",
                        "DATABASE_URL": "postgresql+asyncpg://caller:caller@outside/caller",
                        "CALLER_ONLY": "must-not-cross-boundary",
                    },
                    clear=False,
                ),
                mock.patch.dict(
                    sys.modules,
                    {"scripts.collect_three_site_staging_convergence_snapshot": hostile_collector},
                ),
            ):
                os.chdir(caller_cwd)
                observed = asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        finally:
            os.chdir(previous_cwd)
        self.assertEqual(observed["site"], "bot_fi")
        self.assertEqual(calls[0][0][1], "-I")
        self.assertEqual(calls[0][0][2], "-S")
        self.assertTrue(str(calls[0][0][3]).startswith("/proc/self/fd/"))
        self.assertEqual(observed_cwds, [release_root])
        held_root_descriptor = int(str(calls[0][1]["cwd"]).rsplit("/", maxsplit=1)[1])
        self.assertEqual(
            calls[0][1]["env"][MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV],
            str(held_root_descriptor),
        )
        self.assertEqual(
            set(calls[0][1]["pass_fds"]),
            {
                int(str(calls[0][0][3]).rsplit("/", maxsplit=1)[1]),
                int(str(calls[0][1]["cwd"]).rsplit("/", maxsplit=1)[1]),
            },
        )
        self.assertNotIn("PYTHONPATH", calls[0][1]["env"])
        self.assertNotIn("LD_PRELOAD", calls[0][1]["env"])
        self.assertNotIn("CALLER_ONLY", calls[0][1]["env"])
        self.assertEqual(calls[0][1]["env"]["DATABASE_URL"], runtime_environment["DATABASE_URL"])
        self.assertEqual(
            calls[0][1]["env"][MODULE.COLLECTOR_FD_ENV],
            str(int(str(calls[0][0][3]).rsplit("/", maxsplit=1)[1])),
        )
        self.assertTrue(calls[0][1]["start_new_session"])
        hostile_collector.collect.assert_not_called()  # type: ignore[attr-defined]

    def test_public_observer_execution_requires_an_isolated_parent_interpreter(self) -> None:
        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "isolated Python"):
            asyncio.run(MODULE.observe(self.request()))

        worker = Path(MODULE.__file__)
        environment = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "/hostile"}
        normal = subprocess.run(
            [sys.executable, str(worker), "plan"],
            check=False,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(normal.returncode, 1)
        self.assertIn(b"isolated Python interpreter", normal.stderr)
        isolated = subprocess.run(
            [sys.executable, "-I", "-S", str(worker), "plan"],
            check=False,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr.decode("utf-8"))
        self.assertIn(b'"status":"planned"', isolated.stdout)
        preload = (
            "import runpy, sys, types; "
            "sys.modules['core'] = types.ModuleType('core'); "
            "sys.argv = ['observer', 'plan']; "
            f"runpy.run_path({str(worker)!r}, run_name='__main__')"
        )
        preloaded = subprocess.run(
            [sys.executable, "-I", "-S", "-c", preload],
            check=False,
            env={"PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(preloaded.returncode, 1)
        self.assertIn(b"cannot trust preloaded project modules", preloaded.stderr)

    def test_fixed_launcher_is_root_only_and_applies_clean_I_S_boundary(self) -> None:
        launcher = Path(MODULE.__file__).with_name(
            "production_shadow_convergence_observer_launcher"
        )
        source = launcher.read_text(encoding="ascii")
        metadata = launcher.stat(follow_symlinks=False)
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(metadata.st_mode & 0o777, 0o700)
        self.assertIn("exec /usr/bin/env -i", source)
        self.assertIn("/usr/bin/python3 -I -S", source)
        self.assertIn(MODULE.LAUNCHER_RELEASE_ROOT_FD_ENV, source)
        self.assertIn(MODULE.LAUNCHER_WORKER_FD_ENV, source)
        self.assertIn(MODULE.LAUNCHER_FD_ENV, source)
        self.assertIn("--execute-read-only", source)
        self.assertIn("os.close", source)

    def test_fixed_launcher_closes_an_inherited_descriptor_before_worker_import(self) -> None:
        launcher = Path(MODULE.__file__).with_name(
            "production_shadow_convergence_observer_launcher"
        )
        release_root = self.root / "launcher-fd-release"
        worker = release_root / MODULE.WORKER_RELATIVE
        worker.parent.mkdir(mode=0o700, parents=True)
        marker = self.root / "launcher-worker-fds.txt"
        worker.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "descriptors = []\n"
            "for entry in os.listdir('/proc/self/fd'):\n"
            "    try:\n"
            "        descriptor = int(entry)\n"
            "        os.fstat(descriptor)\n"
            "    except OSError:\n"
            "        continue\n"
            "    descriptors.append(descriptor)\n"
            "descriptors = ' '.join(str(item) for item in sorted(descriptors))\n"
            "Path(sys.argv[3]).write_text(descriptors, encoding='ascii')\n",
            encoding="ascii",
        )
        worker.chmod(0o700)
        release_root.chmod(0o700)
        sentinel_file = self.root / "launcher-inherited-descriptor"
        raw_sentinel = os.open(sentinel_file, os.O_RDWR | os.O_CREAT, 0o600)
        sentinel = os.dup2(raw_sentinel, 97, inheritable=True)
        os.close(raw_sentinel)
        try:
            result = subprocess.run(
                [
                    str(launcher),
                    "--release-root",
                    str(release_root),
                    "observe",
                    "--request",
                    str(marker),
                    "--execute-read-only",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
                pass_fds=(sentinel,),
            )
        finally:
            os.close(sentinel)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        observed = {int(value) for value in marker.read_text(encoding="ascii").split()}
        self.assertEqual(observed, {0, 1, 2, 3, 4, 5})
        self.assertNotIn(sentinel, observed)

    def test_direct_observe_rejects_absent_launcher_fd_handoff_before_request_read(self) -> None:
        worker = Path(MODULE.__file__)
        direct = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(worker),
                "observe",
                "--request",
                "/definitely/not/a/request.json",
                "--execute-read-only",
            ],
            check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(direct.returncode, 1)
        self.assertIn(b"observer launcher release root descriptor", direct.stderr)

    def test_public_observe_requires_a_root_only_launcher_descriptor_capability(self) -> None:
        self.install_release_collector(self.request())
        with mock.patch.object(MODULE, "_require_isolated_observer_execution"):
            with self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "observer launcher release root descriptor",
            ):
                asyncio.run(MODULE.observe(self.request()))

    def test_launcher_contract_requires_root_only_held_launcher_worker_and_release_fds(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        worker_path = paths["worker_path"]
        launcher_path = release_root / MODULE.LAUNCHER_RELATIVE
        worker_path.parent.mkdir(mode=0o700, parents=True)
        shutil.copy2(Path(MODULE.__file__), worker_path)
        shutil.copy2(
            Path(MODULE.__file__).with_name("production_shadow_convergence_observer_launcher"),
            launcher_path,
        )
        release_root.chmod(0o700)
        worker_path.chmod(0o700)
        launcher_path.chmod(0o700)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(release_root, directory_flags)
        worker_fd = os.open(worker_path, file_flags)
        launcher_fd = os.open(launcher_path, file_flags)
        environment = {
            MODULE.LAUNCHER_RELEASE_ROOT_FD_ENV: str(root_fd),
            MODULE.LAUNCHER_WORKER_FD_ENV: str(worker_fd),
            MODULE.LAUNCHER_FD_ENV: str(launcher_fd),
        }
        try:
            with (
                mock.patch.object(MODULE, "_require_isolated_observer_execution"),
                mock.patch.object(MODULE, "__file__", str(worker_path)),
                mock.patch.dict(os.environ, environment, clear=False),
            ):
                contract = MODULE._require_root_only_launcher_contract(request)
                self.assertEqual(contract.release_root_descriptor, root_fd)
                self.assertEqual(contract.worker_descriptor, worker_fd)
                self.assertEqual(contract.launcher_descriptor, launcher_fd)
                launcher_path.chmod(0o755)
                with self.assertRaisesRegex(
                    MODULE.ConvergenceRoleObserverError,
                    "launcher descriptor is not root-controlled",
                ):
                    MODULE._require_root_only_launcher_contract(request)
        finally:
            os.close(launcher_fd)
            os.close(worker_fd)
            os.close(root_fd)

    def test_release_request_and_output_chains_reject_symlink_ancestors(self) -> None:
        request = self.request()
        escaped_release = self.root / "escaped-release"
        escaped_release.mkdir(mode=0o700)
        (self.project_root / OPERATION_ID).symlink_to(escaped_release, target_is_directory=True)
        with (
            mock.patch.object(MODULE, "_require_isolated_observer_execution"),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "unsafe ancestor"),
        ):
            MODULE._require_root_only_launcher_contract(request)

        (self.project_root / OPERATION_ID).unlink()
        escaped_secret = self.root / "escaped-secret"
        escaped_secret.mkdir(mode=0o700)
        (self.secret_root / OPERATION_ID).symlink_to(escaped_secret, target_is_directory=True)
        request_path = self.secret_root / OPERATION_ID / "request.json"
        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "unsafe ancestor"):
            MODULE._load_request(request_path)
        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "output root"):
            MODULE._private_output_root(request)

    def test_direct_collector_api_cannot_import_runtime_or_collect_without_held_fds(self) -> None:
        from scripts import collect_three_site_staging_convergence_snapshot as collector

        self.assertFalse(collector._RUNTIME_IMPORTS_READY)
        self.assertIsNone(collector.settings)
        with self.assertRaisesRegex(
            collector.ConvergenceSnapshotError,
            "descriptor-bound isolated release",
        ):
            asyncio.run(
                collector.collect(
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256="b" * 64,
                    max_rows_per_table=1,
                )
            )

    def test_legacy_exporter_is_a_hard_disabled_non_egress_path(self) -> None:
        from scripts import export_three_site_staging_convergence_snapshot as exporter

        with self.assertRaisesRegex(
            exporter.ConvergenceExportError,
            "legacy direct convergence exporter is disabled",
        ):
            asyncio.run(
                exporter.export(
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256="b" * 64,
                    max_rows_per_table=1,
                    upload={},
                )
            )

    def test_collector_rejects_pythonpath_and_preloaded_core_before_project_import(self) -> None:
        release_root = Path(MODULE.__file__).resolve().parents[1]
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(release_root, flags)
        collector_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        collector_flags |= getattr(os, "O_NOFOLLOW", 0)
        collector_descriptor = os.open(
            release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE,
            collector_flags,
        )
        try:
            collector = f"/proc/self/fd/{collector_descriptor}"
            base_environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV: str(descriptor),
                MODULE.COLLECTOR_FD_ENV: str(collector_descriptor),
            }
            with_pythonpath = subprocess.run(
                [sys.executable, "-I", "-S", collector, "--help"],
                check=False,
                env={**base_environment, "PYTHONPATH": "/hostile"},
                pass_fds=(descriptor, collector_descriptor),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(with_pythonpath.returncode, 1)
            self.assertIn(b"must not receive PYTHONPATH", with_pythonpath.stderr)
            preload = (
                "import runpy, sys, types; "
                "sys.modules['core'] = types.ModuleType('core'); "
                "sys.argv = ['collector', '--help']; "
                f"runpy.run_path({collector!r}, run_name='__main__')"
            )
            preloaded = subprocess.run(
                [sys.executable, "-I", "-S", "-c", preload],
                check=False,
                env=base_environment,
                pass_fds=(descriptor, collector_descriptor),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            injected_path = (
                "import runpy, sys; "
                "sys.path.insert(0, '/tmp'); "
                "sys.argv = ['collector', '--help']; "
                f"runpy.run_path({collector!r}, run_name='__main__')"
            )
            path_result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", injected_path],
                check=False,
                env=base_environment,
                pass_fds=(descriptor, collector_descriptor),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            injected_finder = (
                "import runpy, sys; "
                "sys.meta_path.insert(0, type('HostileFinder', (), {})()); "
                "sys.argv = ['collector', '--help']; "
                f"runpy.run_path({collector!r}, run_name='__main__')"
            )
            finder_result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", injected_finder],
                check=False,
                env=base_environment,
                pass_fds=(descriptor, collector_descriptor),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        finally:
            os.close(collector_descriptor)
            os.close(descriptor)
        self.assertEqual(preloaded.returncode, 1)
        self.assertIn(b"cannot trust preloaded project modules", preloaded.stderr)
        self.assertEqual(path_result.returncode, 1)
        self.assertIn(b"interpreter path escaped trusted roots", path_result.stderr)
        self.assertEqual(finder_result.returncode, 1)
        self.assertIn(b"import finder state is unsafe", finder_result.stderr)

    def test_collector_imports_only_from_held_fd_root_after_path_replacement(self) -> None:
        release_root = self.root / "collector-fd-release"
        scripts = release_root / "scripts"
        core = release_root / "core"
        models = release_root / "models"
        for directory in (scripts, core, models):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        collector_source = Path(MODULE.__file__).with_name(
            "collect_three_site_staging_convergence_snapshot.py"
        )
        shutil.copy2(collector_source, scripts / collector_source.name)
        binding = MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV
        provenance_guard = (
            "import os\n"
            f"_root = '/proc/self/fd/' + os.environ[{binding!r}] + '/'\n"
            "if not __file__.startswith(_root):\n"
            "    raise RuntimeError('project import escaped held FD root')\n"
        )
        (core / "__init__.py").write_text(provenance_guard, encoding="ascii")
        (core / "config.py").write_text(provenance_guard + "settings = object()\n", encoding="ascii")
        (core / "db.py").write_text(provenance_guard + "AsyncSessionLocal = object()\n", encoding="ascii")
        (core / "dr_blob_plane.py").write_text(
            provenance_guard + "def _hash_file(_path):\n    return ('0' * 64, 0)\n",
            encoding="ascii",
        )
        (core / "runtime_identity.py").write_text(
            provenance_guard + "def resolve_runtime_identity():\n    return object()\n",
            encoding="ascii",
        )
        (core / "sync_parity.py").write_text(
            provenance_guard + "async def build_database_parity_snapshot(*_args, **_kwargs):\n    return {}\n",
            encoding="ascii",
        )
        (models / "__init__.py").write_text(provenance_guard, encoding="ascii")
        (models / "dr_event.py").write_text(
            provenance_guard
            + "class DrBlobManifest: pass\nclass DrConflictQuarantine: pass\n"
            + "class DrDestinationCursor: pass\nclass DrEvent: pass\n"
            + "class DrProducerCursor: pass\nclass DrStreamCheckpoint: pass\n",
            encoding="ascii",
        )
        release_root.chmod(0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(release_root, flags)
        collector_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        collector_flags |= getattr(os, "O_NOFOLLOW", 0)
        collector_descriptor = os.open(
            scripts / collector_source.name,
            collector_flags,
        )
        try:
            held_root = release_root.with_name(f"{release_root.name}.held")
            os.replace(release_root, held_root)
            release_root.mkdir(mode=0o700)
            hostile_core = release_root / "core"
            hostile_core.mkdir(mode=0o700)
            (hostile_core / "__init__.py").write_text("raise RuntimeError('replacement imported')\n", encoding="ascii")
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV: str(descriptor),
                MODULE.COLLECTOR_FD_ENV: str(collector_descriptor),
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    f"/proc/self/fd/{collector_descriptor}",
                    "--help",
                ],
                check=False,
                env=environment,
                pass_fds=(descriptor, collector_descriptor),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        finally:
            os.close(collector_descriptor)
            os.close(descriptor)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertIn(b"Collect one redacted", result.stdout)

    def test_collector_rejects_unverified_release_before_any_core_import(self) -> None:
        release_root = self.root / "unverified-collector-release"
        scripts = release_root / "scripts"
        core = release_root / "core"
        scripts.mkdir(mode=0o700, parents=True)
        core.mkdir(mode=0o700)
        collector_source = Path(MODULE.__file__).with_name(
            "collect_three_site_staging_convergence_snapshot.py"
        )
        collector_path = scripts / collector_source.name
        shutil.copy2(collector_source, collector_path)
        marker = self.root / "core-imported-before-provenance"
        (core / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="ascii",
        )
        release_root.chmod(0o700)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(release_root, directory_flags)
        collector_fd = os.open(collector_path, file_flags)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    f"/proc/self/fd/{collector_fd}",
                    "--campaign-id",
                    CAMPAIGN_ID,
                    "--release-sha",
                    RELEASE_SHA,
                    "--plan-sha256",
                    "b" * 64,
                ],
                check=False,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/nonexistent",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV: str(root_fd),
                    MODULE.COLLECTOR_FD_ENV: str(collector_fd),
                },
                pass_fds=(root_fd, collector_fd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        finally:
            os.close(collector_fd)
            os.close(root_fd)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Git readback", result.stderr)
        self.assertFalse(marker.exists(), "core import must follow held-release Git validation")

    def test_oversized_collector_stream_kills_and_reaps_the_child(self) -> None:
        request = self.request()
        collector_path = self.install_release_collector(request)
        self.install_collector_runtime_config(request)
        process: object | None = None

        class RunningProcess:
            def __init__(self) -> None:
                self.stdout = stream_reader(b"x" * 65)
                self.stderr = stream_reader(b"")
                self.returncode: int | None = None
                self.killed = 0
                self.waited = 0
                self.finished = asyncio.Event()

            def kill(self) -> None:
                self.killed += 1
                self.returncode = -9
                self.finished.set()

            async def wait(self) -> int:
                self.waited += 1
                await self.finished.wait()
                return int(self.returncode)

        async def spawn(*_argv, **_kwargs):  # noqa: ANN001, ANN202
            nonlocal process
            process = RunningProcess()
            return process

        with (
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            mock.patch.object(MODULE, "MAX_JSON_BYTES", 64),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "rejected"),
        ):
            asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertIsNotNone(process)
        self.assertEqual(process.killed, 1)  # type: ignore[union-attr]
        self.assertGreaterEqual(process.waited, 1)  # type: ignore[union-attr]

    def test_collector_timeout_kills_and_reaps_the_child(self) -> None:
        request = self.request()
        collector_path = self.install_release_collector(request)
        self.install_collector_runtime_config(request)
        process: object | None = None

        class RunningProcess:
            def __init__(self) -> None:
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.returncode: int | None = None
                self.killed = 0
                self.waited = 0
                self.finished = asyncio.Event()

            def kill(self) -> None:
                self.killed += 1
                self.returncode = -9
                self.finished.set()

            async def wait(self) -> int:
                self.waited += 1
                await self.finished.wait()
                return int(self.returncode)

        async def spawn(*_argv, **_kwargs):  # noqa: ANN001, ANN202
            nonlocal process
            process = RunningProcess()
            return process

        with (
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            mock.patch.object(MODULE, "COLLECTOR_TIMEOUT_SECONDS", 0.01),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "timed out"),
        ):
            asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertIsNotNone(process)
        self.assertEqual(process.killed, 1)  # type: ignore[union-attr]
        self.assertGreaterEqual(process.waited, 1)  # type: ignore[union-attr]

    def test_real_collector_timeout_uses_pidfd_containment_without_residue(self) -> None:
        request = self.request()
        prior_subreaper = MODULE._child_subreaper_enabled()
        marker = self.root / "timeout-collector.pid"
        collector_source = (
            "import os, time\n"
            f"open({str(marker)!r}, 'w', encoding='ascii').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        ).encode("ascii")
        collector_path = self.install_release_collector(request, collector_source)
        self.install_collector_runtime_config(request)
        native_pidfd_send_signal = signal.pidfd_send_signal
        pidfd_calls: list[int] = []

        def record_pidfd_signal(descriptor: int, *args: object, **kwargs: object) -> None:
            pidfd_calls.append(descriptor)
            native_pidfd_send_signal(descriptor, *args, **kwargs)

        with (
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            mock.patch.object(MODULE, "COLLECTOR_TIMEOUT_SECONDS", 0.5),
            mock.patch.object(
                MODULE.signal,
                "pidfd_send_signal",
                side_effect=record_pidfd_signal,
            ),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "timed out"),
        ):
            asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertTrue(marker.exists())
        collector_pid = int(marker.read_text(encoding="ascii"))
        self.assertTrue(pidfd_calls, "timeout cleanup must signal the held pidfd")
        for _ in range(80):
            if not Path(f"/proc/{collector_pid}").exists():
                break
            time.sleep(0.025)
        self.assertFalse(
            Path(f"/proc/{collector_pid}").exists(),
            "a timed-out collector must be killed and reaped",
        )
        self.assertEqual(MODULE._child_subreaper_enabled(), prior_subreaper)

    def test_unproven_collector_cleanup_fail_stops_the_one_shot_worker(self) -> None:
        request = self.request()
        collector_path = self.install_release_collector(request)
        self.install_collector_runtime_config(request)
        boundary = MODULE._CollectorContainmentBoundary(previous_subreaper=False)
        process: CompletedCollectorProcess | None = None

        async def spawn(*_argv: object, **_kwargs: object) -> CompletedCollectorProcess:
            nonlocal process
            process = CompletedCollectorProcess(
                stdout=json.dumps(
                    raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))
                ).encode("ascii")
            )
            return process

        with (
            mock.patch.object(MODULE, "_open_collector_containment_boundary", return_value=boundary),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_drain_collector_child_residue",
                side_effect=MODULE._CollectorCleanupError("cannot inspect children"),
            ),
            mock.patch.object(MODULE, "_close_collector_containment_boundary") as close_boundary,
            mock.patch.object(MODULE.os, "_exit") as fail_stop,
            self.assertRaisesRegex(AssertionError, "unexpectedly returned"),
        ):
            asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        close_boundary.assert_called_once_with(boundary, restore_subreaper=False)
        fail_stop.assert_called_once_with(70)

    def test_collector_cancellation_kills_and_reaps_the_child(self) -> None:
        request = self.request()
        collector_path = self.install_release_collector(request)
        self.install_collector_runtime_config(request)
        process: object | None = None
        spawned = asyncio.Event()

        class RunningProcess:
            def __init__(self) -> None:
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.returncode: int | None = None
                self.killed = 0
                self.waited = 0
                self.finished = asyncio.Event()

            def kill(self) -> None:
                self.killed += 1
                self.returncode = -9
                self.finished.set()

            async def wait(self) -> int:
                self.waited += 1
                await self.finished.wait()
                return int(self.returncode)

        async def spawn(*_argv, **_kwargs):  # noqa: ANN001, ANN202
            nonlocal process
            process = RunningProcess()
            spawned.set()
            return process

        async def cancel_after_spawn() -> None:
            task = asyncio.create_task(MODULE._collect_runtime_snapshot_from_verified_release(request))
            await spawned.wait()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with (
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
        ):
            asyncio.run(cancel_after_spawn())
        self.assertIsNotNone(process)
        self.assertEqual(process.killed, 1)  # type: ignore[union-attr]
        self.assertGreaterEqual(process.waited, 1)  # type: ignore[union-attr]

    def test_collector_cleanup_kills_the_new_process_group_not_only_the_direct_child(self) -> None:
        class GroupProcess:
            pid = 424242

            def __init__(self) -> None:
                self.returncode: int | None = None
                self.finished = asyncio.Event()

            async def wait(self) -> int:
                await self.finished.wait()
                return int(self.returncode)

        process = GroupProcess()
        # Model a direct collector that exited while a same-session helper may
        # still hold the pipes.  Cleanup must still target the group.
        process.returncode = 0

        def kill_group(pid: int, received_signal: signal.Signals) -> None:
            self.assertEqual(pid, process.pid)
            self.assertEqual(received_signal, signal.SIGKILL)
            process.returncode = -signal.SIGKILL
            process.finished.set()

        with mock.patch.object(MODULE.os, "killpg", side_effect=kill_group) as killpg:
            asyncio.run(MODULE._terminate_and_reap_collector(process, tasks=()))
        killpg.assert_called_once_with(process.pid, signal.SIGKILL)

    def test_collector_cleanup_fails_closed_instead_of_waiting_unbounded(self) -> None:
        class HangingProcess:
            returncode: int | None = None

            def kill(self) -> None:
                return None

            async def wait(self) -> int:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        with (
            mock.patch.object(MODULE, "COLLECTOR_REAP_TIMEOUT_SECONDS", 0.01),
            self.assertRaisesRegex(MODULE._CollectorCleanupError, "zero live residue"),
        ):
            asyncio.run(MODULE._terminate_and_reap_collector(HangingProcess(), tasks=()))

    def test_collector_cleanup_kills_a_real_descendant_in_the_collector_session(self) -> None:
        child_program = (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print(child.pid, flush=True); time.sleep(60)"
        )

        async def spawn_and_cleanup() -> int:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                child_program,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert process.stdout is not None
            raw_pid = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            descendant_pid = int(raw_pid.decode("ascii").strip())
            await MODULE._terminate_and_reap_collector(process, tasks=())
            return descendant_pid

        descendant_pid = asyncio.run(spawn_and_cleanup())
        state = ""
        for _ in range(40):
            status = Path(f"/proc/{descendant_pid}/status")
            if not status.exists():
                state = "gone"
                break
            state = next(
                (line for line in status.read_text(encoding="ascii").splitlines() if line.startswith("State:")),
                "",
            )
            if "Z" in state:
                break
            time.sleep(0.025)
        self.assertTrue(state == "gone" or "Z" in state, state)

    def test_collector_rejects_and_reaps_a_detached_setsid_descendant(self) -> None:
        request = self.request()
        prior_subreaper = MODULE._child_subreaper_enabled()
        marker = self.root / "detached-collector-child.pid"
        snapshot = raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))
        child_program = (
            "import os, time; "
            "os.setsid(); "
            f"open({str(marker)!r}, 'w', encoding='ascii').write(str(os.getpid())); "
            "time.sleep(60)"
        )
        collector_source = (
            "import json, os, subprocess, sys, time\n"
            "child = subprocess.Popen(\n"
            f"    [sys.executable, '-c', {child_program!r}],\n"
            "    stdin=subprocess.DEVNULL,\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            "    close_fds=True,\n"
            ")\n"
            "deadline = time.monotonic() + 5\n"
            f"while not os.path.exists({str(marker)!r}) and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            f"if not os.path.exists({str(marker)!r}):\n"
            "    raise SystemExit('detached child did not start')\n"
            f"sys.stdout.write({json.dumps(json.dumps(snapshot, sort_keys=True))})\n"
        ).encode("ascii")
        collector_path = self.install_release_collector(request, collector_source)
        self.install_collector_runtime_config(request)
        with (
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            self.assertRaisesRegex(
                MODULE.ConvergenceRoleObserverError,
                "detached descendant residue",
            ),
        ):
            asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertTrue(marker.exists())
        detached_pid = int(marker.read_text(encoding="ascii"))
        for _ in range(40):
            if not Path(f"/proc/{detached_pid}").exists():
                break
            time.sleep(0.025)
        self.assertFalse(
            Path(f"/proc/{detached_pid}").exists(),
            "a detached collector descendant must be killed and reaped",
        )
        self.assertEqual(MODULE._child_subreaper_enabled(), prior_subreaper)

    def test_collector_reaps_more_than_prestart_child_limit_of_detached_descendants(self) -> None:
        """Cleanup must process every bounded /proc child PID, not only 64."""

        request = self.request()
        prior_subreaper = MODULE._child_subreaper_enabled()
        marker_directory = self.root / "detached-collector-children"
        marker_directory.mkdir(mode=0o700)
        descendant_count = MODULE.MAX_COLLECTOR_ADOPTED_CHILDREN + 1
        snapshot = raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))
        child_program = (
            "import os, time; "
            "os.setsid(); "
            f"open(os.path.join({str(marker_directory)!r}, str(os.getpid())), 'x', encoding='ascii').close(); "
            "time.sleep(60)"
        )
        collector_source = (
            "import json, os, subprocess, sys, time\n"
            f"for _ in range({descendant_count}):\n"
            "    subprocess.Popen(\n"
            f"        [sys.executable, '-c', {child_program!r}],\n"
            "        stdin=subprocess.DEVNULL,\n"
            "        stdout=subprocess.DEVNULL,\n"
            "        stderr=subprocess.DEVNULL,\n"
            "        close_fds=True,\n"
            "    )\n"
            "deadline = time.monotonic() + 10\n"
            f"while len(os.listdir({str(marker_directory)!r})) < {descendant_count} and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            f"if len(os.listdir({str(marker_directory)!r})) != {descendant_count}:\n"
            "    raise SystemExit('detached children did not all start')\n"
            f"sys.stdout.write({json.dumps(json.dumps(snapshot, sort_keys=True))})\n"
        ).encode("ascii")
        collector_path = self.install_release_collector(request, collector_source)
        self.install_collector_runtime_config(request)
        descendant_pids: list[int] = []
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_expected_release_file_sha256",
                    return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
                ),
                mock.patch.object(MODULE, "_verify_held_release_git_state"),
                self.assertRaisesRegex(
                    MODULE.ConvergenceRoleObserverError,
                    "detached descendant residue",
                ),
            ):
                asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
            descendant_pids = sorted(int(path.name) for path in marker_directory.iterdir())
            self.assertEqual(len(descendant_pids), descendant_count)
            for _ in range(80):
                if not any(Path(f"/proc/{pid}").exists() for pid in descendant_pids):
                    break
                time.sleep(0.025)
            self.assertFalse(
                any(Path(f"/proc/{pid}").exists() for pid in descendant_pids),
                "every detached collector descendant must be killed and reaped",
            )
            self.assertEqual(MODULE._child_subreaper_enabled(), prior_subreaper)
        finally:
            # A failed regression must not leave a sleeping test process behind.
            for path in marker_directory.iterdir():
                try:
                    os.kill(int(path.name), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass

    def test_collector_child_inherits_only_explicit_descriptors(self) -> None:
        request = self.request()
        marker = self.root / "collector-child-fds.txt"
        snapshot = raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))
        collector_source = (
            "import os, sys\n"
            "descriptors = []\n"
            "for entry in os.listdir('/proc/self/fd'):\n"
            "    try:\n"
            "        descriptor = int(entry)\n"
            "        os.fstat(descriptor)\n"
            "    except OSError:\n"
            "        continue\n"
            "    descriptors.append(descriptor)\n"
            "descriptors = ' '.join(str(item) for item in sorted(descriptors))\n"
            f"open({str(marker)!r}, 'w', encoding='ascii').write(descriptors)\n"
            f"sys.stdout.write({json.dumps(json.dumps(snapshot, sort_keys=True))})\n"
        ).encode("ascii")
        collector_path = self.install_release_collector(request, collector_source)
        self.install_collector_runtime_config(request)
        sentinel_file = self.root / "collector-inherited-descriptor"
        sentinel = os.open(sentinel_file, os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(sentinel, True)
        real_spawn = asyncio.create_subprocess_exec
        launch: dict[str, object] = {}

        async def record_spawn(*argv, **kwargs):  # noqa: ANN001, ANN202
            launch["pass_fds"] = tuple(kwargs["pass_fds"])
            launch["close_fds"] = kwargs["close_fds"]
            return await real_spawn(*argv, **kwargs)

        try:
            with (
                mock.patch.object(
                    MODULE.asyncio,
                    "create_subprocess_exec",
                    side_effect=record_spawn,
                ),
                mock.patch.object(
                    MODULE,
                    "_expected_release_file_sha256",
                    return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
                ),
                mock.patch.object(MODULE, "_verify_held_release_git_state"),
            ):
                observed = asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        finally:
            os.close(sentinel)
        self.assertEqual(observed["site"], "bot_fi")
        self.assertTrue(launch["close_fds"])
        expected = {0, 1, 2, *(int(value) for value in launch["pass_fds"])}
        actual = {int(value) for value in marker.read_text(encoding="ascii").split()}
        self.assertEqual(actual, expected)
        self.assertNotIn(sentinel, actual)

    def test_release_collector_path_rejects_symlink_outside_verified_release(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        collector_path = paths["release_root"] / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        paths["release_root"].chmod(0o700)
        outside = self.root / "outside-collector.py"
        outside.write_text("# outside\n", encoding="ascii")
        outside.chmod(0o600)
        collector_path.symlink_to(outside)
        with (
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "unavailable"),
        ):
            with MODULE._open_verified_runtime_collector(request):
                self.fail("symlink collector must not be opened")

    def test_collector_rejects_release_dotenv_instead_of_loading_it(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        collector_path = release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        release_root.chmod(0o700)
        collector_path.write_text("# fixed release collector\n", encoding="ascii")
        collector_path.chmod(0o600)
        (release_root / ".env").write_text("DATABASE_URL=must-not-load\n", encoding="ascii")
        (release_root / ".env").chmod(0o600)
        with (
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
            self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "must not contain a .env"),
        ):
            with MODULE._open_verified_runtime_collector(request):
                self.fail("release .env must not be accepted")

    def test_collector_holds_the_verified_fd_across_path_replacement(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        collector_path = release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        release_root.chmod(0o700)
        original = b"# immutable original collector\n"
        collector_path.write_bytes(original)
        collector_path.chmod(0o600)
        self.install_collector_runtime_config(request)
        seen: dict[str, object] = {}

        async def spawn(*argv, **kwargs):  # noqa: ANN001, ANN202
            descriptor_path = Path(str(argv[3]))
            seen["before_replace"] = descriptor_path.read_bytes()
            replacement = collector_path.with_name("replacement-collector.py")
            replacement.write_bytes(b"# replaced pathname\n")
            replacement.chmod(0o600)
            os.replace(replacement, collector_path)
            seen["after_replace"] = descriptor_path.read_bytes()
            seen["pathname"] = collector_path.read_bytes()
            seen["raw_cwd"] = str(kwargs["cwd"])
            seen["cwd"] = Path(str(kwargs["cwd"])).resolve()
            seen["import_root_fd"] = kwargs["env"][MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV]
            return CompletedCollectorProcess(
                stdout=json.dumps(
                    raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2)),
                    sort_keys=True,
                ).encode("ascii")
            )

        with (
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=spawn),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(original).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
        ):
            observed = asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertEqual(observed["site"], "bot_fi")
        self.assertEqual(seen["before_replace"], original)
        self.assertEqual(seen["after_replace"], original)
        self.assertEqual(seen["pathname"], b"# replaced pathname\n")
        self.assertEqual(seen["cwd"], release_root)
        self.assertEqual(
            seen["import_root_fd"],
            str(int(str(seen["raw_cwd"]).rsplit("/", maxsplit=1)[1])),
        )

    def test_real_child_executes_held_collector_fd_after_replacement(self) -> None:
        request = self.request()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        collector_path = release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector_path.parent.mkdir(mode=0o700, parents=True)
        release_root.chmod(0o700)
        original_snapshot = raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))
        collector_path.write_text(
            "import json\nimport sys\n"
            f"sys.stdout.write({json.dumps(json.dumps(original_snapshot, sort_keys=True))})\n",
            encoding="ascii",
        )
        collector_path.chmod(0o600)
        self.install_collector_runtime_config(request)
        replacement = collector_path.with_name("replacement-collector.py")
        replacement.write_text("raise SystemExit('replacement must not execute')\n", encoding="ascii")
        replacement.chmod(0o600)
        real_spawn = asyncio.create_subprocess_exec

        async def replace_then_spawn(*argv, **kwargs):  # noqa: ANN001, ANN202
            os.replace(replacement, collector_path)
            return await real_spawn(*argv, **kwargs)

        with (
            mock.patch.object(MODULE.asyncio, "create_subprocess_exec", side_effect=replace_then_spawn),
            mock.patch.object(
                MODULE,
                "_expected_release_file_sha256",
                return_value=hashlib.sha256(
                    (
                        "import json\nimport sys\n"
                        f"sys.stdout.write({json.dumps(json.dumps(original_snapshot, sort_keys=True))})\n"
                    ).encode("ascii")
                ).hexdigest(),
            ),
            mock.patch.object(MODULE, "_verify_held_release_git_state"),
        ):
            observed = asyncio.run(MODULE._collect_runtime_snapshot_from_verified_release(request))
        self.assertEqual(observed, original_snapshot)
        self.assertIn("replacement must not execute", collector_path.read_text(encoding="ascii"))

    def test_git_blob_lookup_stays_on_held_release_directory_fd_after_replacement(self) -> None:
        staged = self.root / "staged-release"
        staged.mkdir(mode=0o700)
        subprocess.run([MODULE.GIT, "-C", str(staged), "init", "-q"], check=True)
        collector = staged / MODULE.RUNTIME_COLLECTOR_RELATIVE
        collector.parent.mkdir(mode=0o700)
        collector.write_bytes(b"# exact blob held through directory replacement\n")
        collector.chmod(0o600)
        # This tracked attribute and local filter are intentionally hostile.
        # The former `git status` proof path can invoke it while inspecting a
        # worktree.  Commit it before configuring the helper so test setup
        # itself cannot touch the marker.
        (staged / ".gitattributes").write_text(
            "scripts/*.py filter=observer-marker\n",
            encoding="ascii",
        )
        subprocess.run(
            [MODULE.GIT, "-C", str(staged), "add", ".gitattributes", MODULE.RUNTIME_COLLECTOR_RELATIVE.as_posix()],
            check=True,
        )
        subprocess.run(
            [
                MODULE.GIT,
                "-C",
                str(staged),
                "-c",
                "user.name=observer-test",
                "-c",
                "user.email=observer-test@example.invalid",
                "commit",
                "-qm",
                "exact collector",
            ],
            check=True,
        )
        subprocess.run(
            [MODULE.GIT, "-C", str(staged), "checkout", "-q", "--detach"],
            check=True,
        )
        helper_marker = self.root / "unexpected-git-fsmonitor-helper-ran"
        helper = self.root / "unexpected-git-fsmonitor-helper.sh"
        helper.write_text(
            "#!/bin/sh\n"
            f": > {helper_marker}\n"
            "exit 0\n",
            encoding="ascii",
        )
        helper.chmod(0o700)
        subprocess.run(
            [MODULE.GIT, "-C", str(staged), "config", "core.fsmonitor", str(helper)],
            check=True,
        )
        filter_marker = self.root / "unexpected-git-filter-helper-ran"
        filter_helper = self.root / "unexpected-git-filter-helper.sh"
        filter_helper.write_text(
            "#!/bin/sh\n"
            f": > {filter_marker}\n"
            "cat\n",
            encoding="ascii",
        )
        filter_helper.chmod(0o700)
        subprocess.run(
            [
                MODULE.GIT,
                "-C",
                str(staged),
                "config",
                "filter.observer-marker.clean",
                str(filter_helper),
            ],
            check=True,
        )
        release_sha = subprocess.run(
            [MODULE.GIT, "-C", str(staged), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        tree_sha = subprocess.run(
            [MODULE.GIT, "-C", str(staged), "rev-parse", "HEAD^{tree}"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        release_root.parent.mkdir(mode=0o700, parents=True)
        os.replace(staged, release_root)
        release_root.chmod(0o700)
        request = MODULE.build_request(
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            release_tree_sha=tree_sha,
            manifest_sha256="7" * 64,
            runtime_target_binding_sha256="a" * 64,
            plan_sha256="b" * 64,
            approval_sha256="8" * 64,
            role="bot_fi",
            expected_host="127.0.0.1",
            phase_started_at=NOW - timedelta(seconds=10),
            worker_sha256="9" * 64,
            max_rows_per_table=100,
        )
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(release_root, flags)
        try:
            held_root = release_root.with_name(f"{release_root.name}.held")
            os.replace(release_root, held_root)
            release_root.mkdir(mode=0o700)
            MODULE._verify_held_release_git_state(
                request,
                release_root_descriptor=descriptor,
            )
            observed = MODULE._expected_release_file_sha256(
                request,
                relative_path=MODULE.RUNTIME_COLLECTOR_RELATIVE,
                label="release-bound runtime collector",
                release_root_descriptor=descriptor,
            )
            from scripts import collect_three_site_staging_convergence_snapshot as collector

            with mock.patch.object(collector, "_HELD_RELEASE_ROOT_FD", descriptor):
                source_tree = collector._strict_git_bytes(
                    ["ls-tree", "-r", "-z", "--full-tree", release_sha],
                    label="held release source tree",
                    max_bytes=collector.MAX_GIT_TREE_BYTES,
                )
        finally:
            os.close(descriptor)
        self.assertEqual(
            observed,
            hashlib.sha256(
                b"# exact blob held through directory replacement\n"
            ).hexdigest(),
        )
        self.assertIn(MODULE.RUNTIME_COLLECTOR_RELATIVE.name.encode("ascii"), source_tree)
        self.assertFalse(
            helper_marker.exists(),
            "Git local core.fsmonitor helper must not execute during release verification",
        )
        self.assertFalse(
            filter_marker.exists(),
            "Git local filter helper must not execute during release verification",
        )

    def test_collector_git_blob_loader_rejects_skip_worktree_replacement_before_side_effect(self) -> None:
        source = self.root / "source-object-store"
        source.mkdir(mode=0o700)
        scripts = source / "scripts"
        core = source / "core"
        models = source / "models"
        for directory in (scripts, core, models):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        collector_source = Path(MODULE.__file__).with_name(
            "collect_three_site_staging_convergence_snapshot.py"
        )
        collector = scripts / collector_source.name
        shutil.copy2(collector_source, collector)
        collector.chmod(0o644)
        (core / "__init__.py").write_text(
            "from .side_effect import VALUE\n",
            encoding="ascii",
        )
        (core / "side_effect.py").write_text("VALUE = 'git-blob'\n", encoding="ascii")
        (models / "__init__.py").write_text("", encoding="ascii")
        subprocess.run([MODULE.GIT, "-C", str(source), "init", "-q"], check=True)
        subprocess.run([MODULE.GIT, "-C", str(source), "add", "scripts", "core", "models"], check=True)
        subprocess.run(
            [
                MODULE.GIT,
                "-C",
                str(source),
                "-c",
                "user.name=observer-test",
                "-c",
                "user.email=observer-test@example.invalid",
                "commit",
                "-qm",
                "source objects",
            ],
            check=True,
        )
        staged = self.root / "gitfile-alternates-release"
        separate_git_dir = self.root / "gitfile-alternates-metadata"
        subprocess.run(
            [
                MODULE.GIT,
                "clone",
                "-q",
                "--shared",
                f"--separate-git-dir={separate_git_dir}",
                str(source),
                str(staged),
            ],
            check=True,
        )
        subprocess.run([MODULE.GIT, "-C", str(staged), "checkout", "-q", "--detach"], check=True)
        self.assertTrue((staged / ".git").is_file(), "fixture must use a gitfile")
        self.assertTrue(
            (separate_git_dir / "objects" / "info" / "alternates").is_file(),
            "fixture must resolve objects through alternates",
        )
        release_sha = subprocess.run(
            [MODULE.GIT, "-C", str(staged), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        release_root.parent.mkdir(mode=0o700, parents=True)
        os.replace(staged, release_root)
        release_root.chmod(0o700)
        marker = self.root / "replaced-project-module-side-effect"

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(release_root, directory_flags)
        collector_fd = os.open(release_root / MODULE.RUNTIME_COLLECTOR_RELATIVE, file_flags)

        def run_loader() -> subprocess.CompletedProcess[bytes]:
            collector_path = f"/proc/self/fd/{collector_fd}"
            harness = (
                "import importlib, runpy; "
                f"scope = runpy.run_path({collector_path!r}, run_name='collector_under_test'); "
                "state = scope['_require_held_release_execution'].__globals__; "
                f"state['_HELD_RELEASE_ROOT_FD'] = {root_fd}; "
                f"state['_HELD_RELEASE_IMPORT_ROOT'] = '/proc/self/fd/{root_fd}'; "
                f"state['_HELD_COLLECTOR_FD'] = {collector_fd}; "
                f"state['_require_held_release_execution'](release_sha={release_sha!r}); "
                f"state['_verify_held_release_before_runtime_import']({release_sha!r}); "
                f"state['_install_verified_project_source_loader']({release_sha!r}); "
                "module = importlib.import_module('core'); "
                "raise SystemExit(0 if module.VALUE == 'git-blob' else 2)"
            )
            return subprocess.run(
                [sys.executable, "-I", "-S", "-c", harness],
                check=False,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/nonexistent",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    MODULE.COLLECTOR_RELEASE_ROOT_FD_ENV: str(root_fd),
                    MODULE.COLLECTOR_FD_ENV: str(collector_fd),
                },
                pass_fds=(root_fd, collector_fd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        try:
            first = run_loader()
            self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
            subprocess.run(
                [
                    MODULE.GIT,
                    "-C",
                    str(release_root),
                    "update-index",
                    "--skip-worktree",
                    "core/side_effect.py",
                ],
                check=True,
            )
            (release_root / "core" / "side_effect.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\nVALUE = 'replacement'\n",
                encoding="ascii",
            )
            second = run_loader()
        finally:
            os.close(collector_fd)
            os.close(root_fd)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn(b"held project module differs from the exact Git blob", second.stderr)
        self.assertFalse(marker.exists(), "replaced project source must fail before side effect")

    def test_exact_release_git_helpers_refuse_worktree_or_transport_commands(self) -> None:
        from scripts import collect_three_site_staging_convergence_snapshot as collector

        with self.assertRaisesRegex(
            MODULE.ConvergenceRoleObserverError,
            "fixed object read",
        ):
            MODULE._require_fixed_git_object_command(
                ["status", "--porcelain=v1"],
                release_sha=RELEASE_SHA,
            )
        with self.assertRaisesRegex(
            MODULE.ConvergenceRoleObserverError,
            "fixed object read",
        ):
            MODULE._require_fixed_git_object_command(
                ["remote", "-v"],
                release_sha=RELEASE_SHA,
            )
        with self.assertRaisesRegex(
            collector.ConvergenceSnapshotError,
            "fixed object read",
        ):
            collector._require_fixed_git_object_command(["status", "--porcelain=v1"])
        with self.assertRaisesRegex(
            collector.ConvergenceSnapshotError,
            "fixed object read",
        ):
            collector._require_fixed_git_object_command(["fetch", "origin"])

    def test_worker_verify_binds_launcher_and_worker_held_fds_to_git_blobs(self) -> None:
        staged = self.root / "worker-release"
        staged.mkdir(mode=0o700)
        scripts = staged / "scripts"
        scripts.mkdir(mode=0o700)
        worker = staged / MODULE.WORKER_RELATIVE
        launcher = staged / MODULE.LAUNCHER_RELATIVE
        shutil.copy2(Path(MODULE.__file__), worker)
        shutil.copy2(
            Path(MODULE.__file__).with_name("production_shadow_convergence_observer_launcher"),
            launcher,
        )
        worker.chmod(0o644)
        launcher.chmod(0o700)
        subprocess.run([MODULE.GIT, "-C", str(staged), "init", "-q"], check=True)
        subprocess.run([MODULE.GIT, "-C", str(staged), "add", "scripts"], check=True)
        subprocess.run(
            [
                MODULE.GIT,
                "-C",
                str(staged),
                "-c",
                "user.name=observer-test",
                "-c",
                "user.email=observer-test@example.invalid",
                "commit",
                "-qm",
                "exact worker and launcher",
            ],
            check=True,
        )
        subprocess.run([MODULE.GIT, "-C", str(staged), "checkout", "-q", "--detach"], check=True)
        release_sha = subprocess.run(
            [MODULE.GIT, "-C", str(staged), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        tree_sha = subprocess.run(
            [MODULE.GIT, "-C", str(staged), "rev-parse", "HEAD^{tree}"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        paths = MODULE.canonical_paths(
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            role="bot_fi",
        )
        release_root = paths["release_root"]
        release_root.parent.mkdir(mode=0o700, parents=True)
        os.replace(staged, release_root)
        release_root.chmod(0o700)
        worker = release_root / MODULE.WORKER_RELATIVE
        launcher = release_root / MODULE.LAUNCHER_RELATIVE
        request = MODULE.build_request(
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=release_sha,
            release_tree_sha=tree_sha,
            manifest_sha256="7" * 64,
            runtime_target_binding_sha256="a" * 64,
            plan_sha256="b" * 64,
            approval_sha256="8" * 64,
            role="bot_fi",
            expected_host="127.0.0.1",
            phase_started_at=NOW - timedelta(seconds=10),
            worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
            max_rows_per_table=100,
        )
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(release_root, directory_flags)
        worker_fd = os.open(worker, file_flags)
        launcher_fd = os.open(launcher, file_flags)
        environment = {
            MODULE.LAUNCHER_RELEASE_ROOT_FD_ENV: str(root_fd),
            MODULE.LAUNCHER_WORKER_FD_ENV: str(worker_fd),
            MODULE.LAUNCHER_FD_ENV: str(launcher_fd),
        }
        try:
            with (
                mock.patch.object(MODULE, "_require_isolated_observer_execution"),
                mock.patch.object(MODULE, "__file__", str(worker)),
                mock.patch.dict(os.environ, environment, clear=False),
            ):
                identity = MODULE.verify_exact_release(request)
                self.assertEqual(identity["head"], release_sha)
                self.assertEqual(identity["tree"], tree_sha)
                self.assertEqual(identity["worker_sha256"], request["worker_sha256"])
                self.assertNotEqual(
                    identity["release_root_sha256"],
                    hashlib.sha256(os.fspath(release_root).encode("ascii")).hexdigest(),
                )
                replacement = worker.with_name("replacement-worker.py")
                replacement.write_text("raise SystemExit('replacement')\n", encoding="ascii")
                replacement.chmod(0o644)
                os.replace(replacement, worker)
                with self.assertRaisesRegex(
                    MODULE.ConvergenceRoleObserverError,
                    "observer launcher worker descriptor is not root-controlled|worker descriptor differs from its canonical path",
                ):
                    MODULE.verify_exact_release(request)
        finally:
            os.close(launcher_fd)
            os.close(worker_fd)
            os.close(root_fd)

    def test_collector_runtime_config_rejects_missing_or_unbound_environment(self) -> None:
        request = self.request()
        path, _environment = self.install_collector_runtime_config(request)
        document = json.loads(path.read_text(encoding="ascii"))
        document["request_sha256"] = "0" * 64
        document["config_sha256"] = MODULE._runtime_config_digest(document)
        path.write_bytes(MODULE._canonical_json(document))
        path.chmod(0o600)
        with self.assertRaisesRegex(MODULE.ConvergenceRoleObserverError, "request_sha256"):
            MODULE._validate_collector_runtime_config(
                document,
                request=request,
                runtime_target_binding=self.runtime_target_binding(
                    "bot_fi",
                    release_sha=RELEASE_SHA,
                ),
            )

    def test_collector_runtime_config_rejects_database_target_drift_before_child(self) -> None:
        request = self.request()
        path, _environment = self.install_collector_runtime_config(request)
        document = json.loads(path.read_text(encoding="ascii"))
        environment = document["environment"]
        self.assertIsInstance(environment, dict)
        environment["DATABASE_URL"] = environment["DATABASE_URL"].replace(
            "@bot_fi_db/", "@127.0.0.1/"
        )
        environment["SYNC_DATABASE_URL"] = environment["SYNC_DATABASE_URL"].replace(
            "@bot_fi_db/", "@127.0.0.1/"
        )
        document["config_sha256"] = MODULE._runtime_config_digest(document)
        path.write_bytes(MODULE._canonical_json(document))
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.ConvergenceRoleObserverError,
            "cannot derive its target binding",
        ):
            MODULE._collector_environment(request, release_root_descriptor=7)

    def test_witness_has_no_fabricated_runtime_or_live_lease_observation(self) -> None:
        request = self.request(role="witness")
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }
        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
        ):
            document = asyncio.run(
                MODULE._observe_for_test(
                    request,
                    host_identity_proof_collector=host_identity_proof,
                )
            )
        self.assertEqual(document["available_observations"], [])
        self.assertIsNone(document["runtime_snapshot"])
        self.assertIn("witness_live", document["unavailable_observations"])
        self.assertEqual(document["host_identity_proof"]["observed_host"], "127.0.0.1")

    def test_publish_is_root_only_create_only_and_reuses_exact_payload(self) -> None:
        request = self.request()
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }

        async def collector(_request):  # noqa: ANN001
            return raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))

        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity) as exact_release,
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
        ):
            document = asyncio.run(
                MODULE._observe_for_test(
                    request,
                    snapshot_collector=collector,
                    host_identity_proof_collector=host_identity_proof,
                )
            )
            with mock.patch.object(MODULE, "_require_isolated_observer_execution"):
                path, first = MODULE.publish_attestation(document, request=request)
                again_path, second = MODULE.publish_attestation(document, request=request)
        self.assertEqual(exact_release.call_args_list[-1].args[0], request)
        self.assertEqual(first, "created")
        self.assertEqual(second, "reused")
        self.assertEqual(path, again_path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_uid, 0)

    def test_publish_never_treats_an_attestation_as_its_release_request(self) -> None:
        request = self.request()
        release_identity = {
            "release_root_sha256": "d" * 64,
            "head": RELEASE_SHA,
            "tree": TREE_SHA,
            "source_tree_bound": True,
            "worker_sha256": "9" * 64,
        }

        async def collector(_request):  # noqa: ANN001
            return raw_snapshot("bot_fi", observed=NOW - timedelta(seconds=2))

        with (
            mock.patch.object(MODULE, "verify_exact_release", return_value=release_identity),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
        ):
            attestation = asyncio.run(
                MODULE._observe_for_test(
                    request,
                    snapshot_collector=collector,
                    host_identity_proof_collector=host_identity_proof,
                )
            )
            with mock.patch.object(MODULE, "_require_isolated_observer_execution"):
                with self.assertRaisesRegex(
                    MODULE.ConvergenceRoleObserverError,
                    "observer request fields differ",
                ):
                    MODULE.publish_attestation(attestation, request=attestation)

class ReleaseRelativeNoFollowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "release"
        self.root.mkdir(mode=0o700)
        (self.root / "core").mkdir(mode=0o700)
        self.path = self.root / "core" / "module.py"
        self.path.write_bytes(b"ok")
        self.path.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_regular_file(self) -> None:
        self.assertEqual(MODULE._read_release_relative_nofollow(self.root, "core/module.py", max_size=32), b"ok")

    def test_allows_empty_package_initializer_only_when_explicit(self) -> None:
        package_init = self.root / "core" / "__init__.py"
        package_init.write_bytes(b"")
        package_init.chmod(0o600)
        with self.assertRaises(MODULE.ConvergenceRoleObserverError):
            MODULE._read_release_relative_nofollow(
                self.root,
                "core/__init__.py",
                max_size=32,
            )
        self.assertEqual(
            MODULE._read_release_relative_nofollow(
                self.root,
                "core/__init__.py",
                max_size=32,
                allow_empty=True,
            ),
            b"",
        )

    def test_rejects_symlink_ancestor_or_leaf(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir(mode=0o700)
        (outside / "module.py").write_bytes(b"outside")
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        self.path.unlink()
        self.path.symlink_to(outside / "module.py")
        for relative in ("link/module.py", "core/module.py"):
            with self.subTest(relative=relative):
                with self.assertRaises(MODULE.ConvergenceRoleObserverError):
                    MODULE._read_release_relative_nofollow(self.root, relative, max_size=32)

    def test_rejects_writable_ancestor_and_replacement(self) -> None:
        (self.root / "core").chmod(0o777)
        with self.assertRaises(MODULE.ConvergenceRoleObserverError):
            MODULE._read_release_relative_nofollow(self.root, "core/module.py", max_size=32)
        (self.root / "core").chmod(0o700)
        original = MODULE.os.read
        changed = False
        def replace(fd, size):  # noqa: ANN001
            nonlocal changed
            value = original(fd, size)
            if value and not changed:
                self.path.unlink()
                self.path.write_bytes(b"new")
                self.path.chmod(0o600)
                changed = True
            return value
        with mock.patch.object(MODULE.os, "read", side_effect=replace):
            with self.assertRaises(MODULE.ConvergenceRoleObserverError):
                MODULE._read_release_relative_nofollow(self.root, "core/module.py", max_size=32)


if __name__ == "__main__":
    unittest.main()
