"""Local-only contract tests for the WA-FI PostgreSQL 15 helper container."""

from __future__ import annotations

import ast
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.append_only_sync_delta_batch import canonical_json_bytes
import core.physical_wa_fi_postgres_helper_container as helper_container


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wa_fi_postgres_helper_container.py"
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class RecordingRunner:
    """Pure fake runner: it never starts Docker or a process."""

    def __init__(self, *, exit_code: int = 0, exception: Exception | None = None) -> None:
        self.exit_code = exit_code
        self.exception = exception
        self.calls: list[helper_container.PhysicalWaFiPostgresHelperContainerInvocation] = []

    def run(self, *, invocation):
        self.calls.append(invocation)
        if self.exception is not None:
            raise self.exception
        artifact = invocation.helper_output_directory / "base.tar"
        artifact.write_bytes(b"synthetic physical base backup")
        artifact.chmod(0o600)
        os.chown(artifact, invocation.helper_uid, invocation.helper_gid)
        return helper_container.PhysicalWaFiPostgresHelperContainerRunnerResult(
            exit_code=self.exit_code
        )


@unittest.skipUnless(os.geteuid() == 0, "root-owned local policy fixtures require root")
class PhysicalWaFiPostgresHelperContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-helper-container-")
        self.root = Path(self.temporary.name).resolve()
        self.config_path = self.root / "base-backup-helper-container.json"
        self.attestation_path = self.root / "installation-attestation.json"
        self.manifest_lock_path = self.root / "manifest-lock.json"
        self.auth_preflight_path = self.root / "local-base-backup-auth-preflight.json"
        self.runtime_identity_path = self.root / "postgres-image-runtime-identity-attestation.json"
        self.docker_binary = self.root / "docker"
        self.output_directory = self.root / "capture-output"
        self.docker_binary.write_bytes(b"synthetic fixed docker client")
        self.docker_binary.chmod(0o755)
        self.output_directory.mkdir(mode=0o700)
        self.output_directory.chmod(0o700)
        self.capture_configuration_sha256 = "a" * 64
        # Test-only attested values; production policy never hard-codes them.
        self.runtime_uid = 999
        self.runtime_gid = 999
        self.synthetic_owners: dict[Path, tuple[int, int]] = {}
        self._write_rendered_binding()
        self.config = self._config()
        self._write_policy(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _helper(self, **changes: object) -> dict[str, object]:
        result: dict[str, object] = {
            "postgres_major": 15,
            "image": "registry.example/postgres@sha256:" + "fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
            "docker_binary_sha256": sha(self.docker_binary.read_bytes()),
            "network_mode": "none",
            "pull_policy": "never",
            "container_root_filesystem_read_only": True,
            "drop_all_capabilities": True,
            "no_new_privileges": True,
            "pids_limit": 64,
            "socket_volume": "physical_fi_postgres_socket",
            "socket_mount_target": "/var/run/postgresql",
            "socket_mount_read_only": True,
            "socket_directory_owner": "postgres",
            "socket_directory_mode": "0710",
            "socket_file_name": ".s.PGSQL.5432",
            "socket_file_owner": "postgres",
            "socket_file_group": "postgres",
            "socket_file_mode": "0770",
            "output_mount_target": "/capture",
            "output_directory_mode": "0700",
            "entrypoint": "pg_basebackup",
            "source_port": 5432,
            "source_role": "physical_backup",
            "password_prompt": "forbidden",
        }
        result.update(changes)
        return result

    def _config(self, **changes: object) -> dict[str, object]:
        result: dict[str, object] = {
            "schema": helper_container.PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_SCHEMA,
            "version": 1,
            "enabled": True,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "capture_configuration_sha256": self.capture_configuration_sha256,
            "deployment_manifest_lock_sha256": sha(self.manifest_lock_path.read_bytes()),
            "local_base_backup_auth_preflight_sha256": sha(self.auth_preflight_path.read_bytes()),
            "postgres_runtime_identity_attestation_sha256": sha(self.runtime_identity_path.read_bytes()),
            "helper": self._helper(),
        }
        result.update(changes)
        unpinned = dict(result)
        unpinned.pop("configuration_sha256", None)
        result["configuration_sha256"] = sha(canonical_json_bytes(unpinned))
        return result

    def _attestation(self, config: dict[str, object], **changes: object) -> dict[str, object]:
        result: dict[str, object] = {
            "schema": helper_container.PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION_SCHEMA,
            "version": 1,
            "configuration_sha256": config["configuration_sha256"],
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "capture_configuration_sha256": self.capture_configuration_sha256,
            "deployment_manifest_lock_sha256": config["deployment_manifest_lock_sha256"],
            "local_base_backup_auth_preflight_sha256": config[
                "local_base_backup_auth_preflight_sha256"
            ],
            "postgres_runtime_identity_attestation_sha256": config[
                "postgres_runtime_identity_attestation_sha256"
            ],
            "docker_binary": str(self.docker_binary),
            "helper": config["helper"],
        }
        result.update(changes)
        return result

    def _write_policy(self, config: dict[str, object], *, attestation: dict[str, object] | None = None) -> None:
        self.config_path.write_bytes(canonical_json_bytes(config))
        self.config_path.chmod(0o600)
        value = self._attestation(config) if attestation is None else attestation
        self.attestation_path.write_bytes(canonical_json_bytes(value))
        self.attestation_path.chmod(0o600)

    def _write_rendered_binding(self) -> None:
        identity = {
            "schema": "gold-trade-physical-postgres-runtime-identity-attestation-v1",
            "version": 1,
            "postgres_image": self._helper()["image"],
            "image_digest": "sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
            "platform": "linux/amd64",
            "effective_uid": self.runtime_uid,
            "effective_gid": self.runtime_gid,
            "pg_basebackup_entrypoint": "pg_basebackup",
        }
        identity_raw = canonical_json_bytes(identity)
        identity_sha256 = sha(identity_raw)
        manifest_lock = {
            "status": "default-off-not-launch-authorized",
            "campaign_id": "physical-helper-container-20260731",
            "release_sha": "d" * 40,
            "postgres_major": 15,
            "postgres_runtime_identity": {
                "image_digest": "sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
                "platform": "linux/amd64",
                "effective_uid": self.runtime_uid,
                "effective_gid": self.runtime_gid,
                "attestation_sha256": identity_sha256,
            },
            "primary": {"postgres_socket_volume": "physical_fi_postgres_socket"},
            "route": {
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "direct_fi_to_ir_postgres_control": False,
            },
        }
        preflight = {
            "schema": helper_container.PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA,
            "status": "default-off-not-launch-authorized",
            "campaign_id": manifest_lock["campaign_id"],
            "release_sha": manifest_lock["release_sha"],
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "direct_fi_to_ir_postgres_control": False,
            "postgres_major": 15,
            "postgres_runtime_identity": manifest_lock["postgres_runtime_identity"],
            "postgres_socket_volume": "physical_fi_postgres_socket",
            "local_base_backup": {
                "transport": "unix-socket-only",
                "socket_directory": "/var/run/postgresql",
                "port": 5432,
                "replication_role": "physical_backup",
                "peer_os_users": ["postgres"],
                "max_wal_senders": 1,
                "tcp_hba": "reject",
                "helper_execution": "digest-pinned-image-attested-container-v1",
            },
            "pg_hba_sha256": "e" * 64,
            "pg_ident_sha256": "f" * 64,
            "postgresql_conf_sha256": "1" * 64,
            "required_role_attributes": {
                "role": "physical_backup",
                "login": True,
                "replication": True,
                "superuser": False,
                "createdb": False,
                "createrole": False,
                "bypassrls": False,
                "inherit": False,
                "password_authentication": "forbidden",
            },
            "not_a_role_creation_authorization": True,
            "not_a_launch_authorization": True,
        }
        self.manifest_lock_path.write_bytes(canonical_json_bytes(manifest_lock) + b"\n")
        self.auth_preflight_path.write_bytes(canonical_json_bytes(preflight) + b"\n")
        self.runtime_identity_path.write_bytes(identity_raw)
        self.manifest_lock_path.chmod(0o600)
        self.auth_preflight_path.chmod(0o600)
        self.runtime_identity_path.chmod(0o600)

    @contextmanager
    def _paths(self):
        original_lstat = os.lstat

        def fake_chown(path, uid, gid):
            self.synthetic_owners[Path(path)] = (uid, gid)

        def fake_lstat(path):
            metadata = original_lstat(path)
            owner = self.synthetic_owners.get(Path(path))
            if owner is None:
                return metadata
            return type(
                "SyntheticStat",
                (),
                {
                    "st_mode": metadata.st_mode,
                    "st_uid": owner[0],
                    "st_gid": owner[1],
                    "st_nlink": metadata.st_nlink,
                    "st_size": metadata.st_size,
                    "st_dev": metadata.st_dev,
                    "st_ino": metadata.st_ino,
                },
            )()

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.multiple(
                    helper_container,
                    FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_CONFIG=self.config_path,
                    FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION=self.attestation_path,
                    FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY=self.docker_binary,
                    FIXED_WA_FI_POSTGRES_MANIFEST_LOCK=self.manifest_lock_path,
                    FIXED_WA_FI_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT=self.auth_preflight_path,
                    FIXED_WA_FI_POSTGRES_RUNTIME_IDENTITY_ATTESTATION=self.runtime_identity_path,
                )
            )
            stack.enter_context(mock.patch.object(helper_container.os, "chown", side_effect=fake_chown))
            stack.enter_context(mock.patch.object(helper_container.os, "lstat", side_effect=fake_lstat))
            yield

    def _request(self, **changes: object):
        values: dict[str, object] = {
            "capture_configuration_sha256": self.capture_configuration_sha256,
            "capture_output_root": self.output_directory,
            "writer_epoch": 73,
            "writer_lease_id": "writer-lease-73",
            "witness_transition_id": "witness-transition-73",
            "witnessed_term_proof_sha256": "c" * 64,
        }
        values.update(changes)
        return helper_container.PhysicalWaFiPostgresHelperContainerCaptureRequest(**values)

    def _build(self, request=None):
        with self._paths():
            return helper_container.build_wa_fi_postgres_helper_container_invocation(
                (), request=self._request() if request is None else request
            )

    def _execute(self, runner: RecordingRunner, request=None):
        with self._paths():
            return helper_container.execute_wa_fi_postgres_helper_container_capture(
                (),
                request=self._request() if request is None else request,
                runner=runner,
            )

    def test_exact_digest_pinned_nonroot_helper_argv_collects_root_owned_artifact(self) -> None:
        runner = RecordingRunner()
        result = self._execute(runner)

        self.assertEqual(1, len(runner.calls))
        invocation = runner.calls[0]
        self.assertEqual(self.docker_binary, invocation.docker_binary)
        self.assertEqual((), invocation.environment)
        self.assertEqual(
            (
                str(self.docker_binary),
                "--context=default",
                "run",
                "--pull=never",
                "--rm",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=64",
                "--user=999:999",
                "--entrypoint=pg_basebackup",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--env=PGPASSFILE=/dev/null",
                "--mount",
                "type=volume,src=physical_fi_postgres_socket,dst=/var/run/postgresql,readonly",
                "--mount",
                "type=bind,src=" + str(invocation.helper_output_directory) + ",dst=/capture",
                "registry.example/postgres@sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
                "--host=/var/run/postgresql",
                "--port=5432",
                "--username=physical_backup",
                "--no-password",
                "--format=tar",
                "--wal-method=none",
                "--checkpoint=fast",
                "--pgdata=/capture",
            ),
            invocation.arguments,
        )
        self.assertNotIn("--network=host", invocation.arguments)
        self.assertNotIn("--publish", invocation.arguments)
        self.assertNotIn("--publish-all", invocation.arguments)
        self.assertFalse(any("docker.sock" in value for value in invocation.arguments))
        self.assertFalse(any("PGPASSWORD" in value for value in invocation.arguments))
        self.assertEqual(self.runtime_uid, invocation.helper_uid)
        self.assertEqual(self.runtime_gid, invocation.helper_gid)
        self.assertEqual(self.output_directory / "base.tar", result.collected_artifact_path)
        self.assertEqual(0, result.collected_artifact_path.stat().st_uid)
        self.assertEqual(0o600, result.collected_artifact_path.stat().st_mode & 0o777)
        self.assertEqual(result.invocation_sha256, invocation.invocation_sha256)
        self.assertEqual(result.configuration_sha256, self.config["configuration_sha256"])
        self.assertEqual(
            result.installation_attestation_sha256,
            sha(self.attestation_path.read_bytes()),
        )

    def test_default_off_and_invalid_runtime_never_call_runner(self) -> None:
        runner = RecordingRunner()
        disabled = self._config(enabled=False)
        self._write_policy(disabled)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_DISABLED$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

        malformed = self._config()
        malformed["helper"] = self._helper(network_mode="host")
        malformed = self._config(helper=malformed["helper"])
        self._write_policy(malformed)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_CONFIG_INVALID$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

    def test_attestation_and_docker_identity_are_separately_pinned(self) -> None:
        runner = RecordingRunner()
        different_helper = self._helper(image="registry.example/postgres@sha256:" + "d" * 64)
        self._write_policy(
            self.config,
            attestation=self._attestation(self.config, helper=different_helper),
        )
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_ATTESTATION_INVALID$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

    def test_rendered_socket_hba_role_preflight_is_hash_bound_before_runner(self) -> None:
        runner = RecordingRunner()
        changed = self.auth_preflight_path.read_bytes().replace(
            b'"max_wal_senders":1', b'"max_wal_senders":0'
        )
        self.assertNotEqual(changed, self.auth_preflight_path.read_bytes())
        self.auth_preflight_path.write_bytes(changed)
        self.auth_preflight_path.chmod(0o600)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_AUTH_PREFLIGHT_BINDING_MISMATCH$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

        self._write_rendered_binding()
        self.config = self._config()
        self._write_policy(self.config)
        changed_lock = self.manifest_lock_path.read_bytes().replace(
            b'"postgres_major":15', b'"postgres_major":14'
        )
        self.assertNotEqual(changed_lock, self.manifest_lock_path.read_bytes())
        self.manifest_lock_path.write_bytes(changed_lock)
        self.manifest_lock_path.chmod(0o600)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_MANIFEST_LOCK_BINDING_MISMATCH$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

        self._write_rendered_binding()
        self.config = self._config()
        self._write_policy(self.config)
        self.docker_binary.write_bytes(b"replaced docker client")
        self.docker_binary.chmod(0o755)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_DOCKER_IDENTITY_INVALID$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

    def test_capture_binding_and_root_owned_empty_output_are_required(self) -> None:
        runner = RecordingRunner()
        mismatch = self._request(capture_configuration_sha256="d" * 64)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_CAPTURE_BINDING_MISMATCH$",
        ):
            self._execute(runner, mismatch)
        self.assertEqual([], runner.calls)

        self.output_directory.chmod(0o755)
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

        self.output_directory.chmod(0o700)
        (self.output_directory / "unexpected").write_text("x", encoding="ascii")
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE$",
        ):
            self._execute(runner)
        self.assertEqual([], runner.calls)

    def test_runtime_identity_and_socket_permissions_are_not_caller_tunable(self) -> None:
        runner = RecordingRunner()
        cases = (
            self._helper(socket_mount_read_only=False),
            self._helper(socket_directory_mode="0770"),
            self._helper(socket_file_mode="0777"),
            self._helper(output_directory_mode="0777"),
            self._helper(image="registry.example/postgres:15"),
        )
        for changed_helper in cases:
            with self.subTest(changed_helper=changed_helper):
                config = self._config(helper=changed_helper)
                self._write_policy(config)
                with self.assertRaisesRegex(
                    helper_container.PhysicalWaFiPostgresHelperContainerError,
                    "^HELPER_CONTAINER_CONFIG_INVALID$",
                ):
                    self._execute(runner)
                self.assertEqual([], runner.calls)
        self._write_policy(self.config)

    def test_errors_are_redacted_and_runner_is_injected_only(self) -> None:
        injected = "https://secret.invalid/?token=must-not-leak"
        runner = RecordingRunner(exception=RuntimeError(injected))
        with self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_RUNNER_FAILED$",
        ) as raised:
            self._execute(runner)
        self.assertNotIn(injected, str(raised.exception))
        self.assertEqual(1, len(runner.calls))

        with self._paths(), self.assertRaisesRegex(
            helper_container.PhysicalWaFiPostgresHelperContainerError,
            "^HELPER_CONTAINER_ARGUMENTS_FORBIDDEN$",
        ):
            helper_container.build_wa_fi_postgres_helper_container_invocation(
                ("--image=caller-controlled",), request=self._request()
            )

    def test_root_runtime_and_module_surface_reject_process_or_network_use(self) -> None:
        runner = RecordingRunner()
        with self._paths(), mock.patch.object(helper_container.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                helper_container.PhysicalWaFiPostgresHelperContainerError,
                "^HELPER_CONTAINER_ROOT_RUNTIME_REQUIRED$",
            ):
                helper_container.execute_wa_fi_postgres_helper_container_capture(
                    (), request=self._request(), runner=runner
                )
        self.assertEqual([], runner.calls)

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        forbidden_calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    forbidden_calls.add(node.func.attr)
        self.assertTrue(
            {"subprocess", "socket", "docker", "requests", "boto3", "urllib"}.isdisjoint(imported)
        )
        self.assertTrue({"system", "popen", "execv", "execve", "execvp"}.isdisjoint(forbidden_calls))


if __name__ == "__main__":
    unittest.main()
