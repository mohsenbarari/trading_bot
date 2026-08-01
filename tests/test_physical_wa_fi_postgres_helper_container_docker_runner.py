from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import core.physical_wa_fi_postgres_helper_container_docker_runner as docker_runner
from core.physical_wa_fi_postgres_helper_container import (
    PhysicalWaFiPostgresHelperContainerInvocation,
)


IMAGE = (
    "registry.example/postgres@sha256:"
    "fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786"
)
VOLUME = "physical_fi_postgres_socket"
UID = 999
GID = 999
HASH = "a" * 64


class PhysicalWaFiPostgresHelperContainerDockerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-docker-runner-")
        self.root = Path(self.temporary.name)
        self.docker_binary = self.root / "docker"
        self.docker_binary.write_bytes(b"synthetic-docker-binary")
        self.capture_root = self.root / "capture"
        self.capture_root.mkdir()
        self.helper_output = self.capture_root / ("pg-basebackup-helper-" + "a" * 32)
        self.helper_output.mkdir()
        self.binary_sha256 = hashlib.sha256(self.docker_binary.read_bytes()).hexdigest()
        self.config = docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunnerConfig(
            enabled=True,
            docker_binary=self.docker_binary,
            docker_binary_sha256=self.binary_sha256,
            helper_image=IMAGE,
            socket_volume=VOLUME,
            helper_uid=UID,
            helper_gid=GID,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _invocation(self, **changes: object) -> PhysicalWaFiPostgresHelperContainerInvocation:
        arguments = (
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
            "type=volume,src=" + VOLUME + ",dst=/var/run/postgresql,readonly",
            "--mount",
            "type=bind,src=" + str(self.helper_output) + ",dst=/capture",
            IMAGE,
            "--host=/var/run/postgresql",
            "--port=5432",
            "--username=physical_backup",
            "--no-password",
            "--format=tar",
            "--wal-method=none",
            "--checkpoint=fast",
            "--pgdata=/capture",
        )
        values: dict[str, object] = {
            "docker_binary": self.docker_binary,
            "docker_binary_sha256": self.binary_sha256,
            "helper_image": IMAGE,
            "arguments": arguments,
            "environment": (),
            "capture_output_root": self.capture_root,
            "helper_output_directory": self.helper_output,
            "helper_uid": UID,
            "helper_gid": GID,
            "configuration_sha256": HASH,
            "installation_attestation_sha256": "b" * 64,
            "capture_configuration_sha256": "c" * 64,
            "deployment_manifest_lock_sha256": "d" * 64,
            "local_base_backup_auth_preflight_sha256": "e" * 64,
            "postgres_runtime_identity_attestation_sha256": "f" * 64,
            "writer_epoch": 73,
            "writer_lease_id": "writer-lease-73",
            "witness_transition_id": "witness-transition-73",
            "witnessed_term_proof_sha256": "1" * 64,
            "invocation_sha256": "2" * 64,
        }
        values.update(changes)
        raw = PhysicalWaFiPostgresHelperContainerInvocation(**values)
        if "invocation_sha256" not in changes:
            raw = replace(raw, invocation_sha256=docker_runner._invocation_sha256(raw))
        return raw

    def _runner(self) -> docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunner:
        return docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunner(self.config)

    @contextmanager
    def _valid_run_context(self):
        with (
            patch.object(docker_runner.os, "geteuid", return_value=0),
            patch.object(docker_runner, "_validate_capture_paths"),
            patch.object(
                docker_runner,
                "_secure_docker_binary_sha256",
                return_value=self.binary_sha256,
            ),
        ):
            yield

    def test_default_disabled_and_nonroot_never_invoke_subprocess(self) -> None:
        with patch.object(docker_runner.subprocess, "run") as run:
            with self.assertRaisesRegex(
                docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunnerError,
                "HELPER_DOCKER_RUNNER_DISABLED",
            ):
                docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunner().run(
                    invocation=object()  # type: ignore[arg-type]
                )
        run.assert_not_called()

        with (
            patch.object(docker_runner.os, "geteuid", return_value=1000),
            patch.object(docker_runner.subprocess, "run") as run,
            self.assertRaisesRegex(
                docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunnerError,
                "HELPER_DOCKER_RUNNER_ROOT_REQUIRED",
            ),
        ):
            self._runner().run(invocation=object())  # type: ignore[arg-type]
        run.assert_not_called()

    def test_valid_invocation_runs_the_exact_argv_with_safe_subprocess_kwargs(self) -> None:
        invocation = self._invocation()
        with (
            self._valid_run_context(),
            patch.object(
                docker_runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(list(invocation.arguments), 0),
            ) as run,
        ):
            result = self._runner().run(invocation=invocation)

        self.assertEqual(0, result.exit_code)
        run.assert_called_once_with(
            list(invocation.arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            shell=False,
            timeout=docker_runner.FIXED_WA_FI_POSTGRES_HELPER_DOCKER_RUNNER_TIMEOUT_SECONDS,
            check=False,
        )

    def test_tampered_environment_pins_or_argv_fail_before_subprocess(self) -> None:
        valid = self._invocation()
        tampered = (
            replace(valid, environment=(("PGPASSWORD", "super-secret"),)),
            replace(valid, docker_binary=Path("/usr/bin/docker")),
            replace(valid, docker_binary_sha256="b" * 64),
            replace(valid, arguments=valid.arguments[:-1] + ("--pgdata=/capture;id",)),
            replace(valid, helper_image="postgres:latest"),
        )
        for value in tampered:
            with self.subTest(change=type(value).__name__):
                with (
                    self._valid_run_context(),
                    patch.object(docker_runner.subprocess, "run") as run,
                    self.assertRaises(docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunnerError) as raised,
                ):
                    self._runner().run(invocation=value)
                run.assert_not_called()
                self.assertNotIn("super-secret", str(raised.exception))

    def test_timeout_os_error_and_nonzero_are_safe_runner_results(self) -> None:
        invocation = self._invocation()
        cases = (
            (
                subprocess.TimeoutExpired(list(invocation.arguments), timeout=1),
                124,
            ),
            (OSError("unavailable"), 125),
            (subprocess.CompletedProcess(list(invocation.arguments), 42), 42),
            (subprocess.CompletedProcess(list(invocation.arguments), -9), 125),
        )
        for outcome, expected in cases:
            with self.subTest(outcome=type(outcome).__name__):
                kwargs = (
                    {"side_effect": outcome}
                    if isinstance(outcome, BaseException)
                    else {"return_value": outcome}
                )
                with (
                    self._valid_run_context(),
                    patch.object(docker_runner.subprocess, "run", **kwargs),
                ):
                    result = self._runner().run(invocation=invocation)
                self.assertEqual(expected, result.exit_code)

    def test_constructor_performs_no_process_action(self) -> None:
        with patch.object(docker_runner.subprocess, "run") as run:
            runner = docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunner(self.config)
        self.assertIsInstance(
            runner,
            docker_runner.PhysicalWaFiPostgresHelperContainerDockerRunner,
        )
        run.assert_not_called()


class PhysicalWaFiPostgresHelperContainerDockerRunnerStaticTests(unittest.TestCase):
    def test_runner_has_no_network_ssh_or_shell_execution_surface(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "physical_wa_fi_postgres_helper_container_docker_runner.py"
        )
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        forbidden = {
            "aiohttp",
            "boto3",
            "botocore",
            "http",
            "httpx",
            "paramiko",
            "requests",
            "socket",
            "urllib",
        }
        self.assertFalse(
            [
                value
                for value in imports
                if value in forbidden or value.startswith(("boto.", "urllib."))
            ]
        )
        self.assertNotIn("subprocess.Popen", text)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ]
        self.assertEqual(1, len(calls))
        keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
        self.assertIsInstance(keywords.get("shell"), ast.Constant)
        self.assertIs(keywords["shell"].value, False)
        self.assertNotIn("print(", text)
        self.assertNotIn("logging", text)


if __name__ == "__main__":
    unittest.main()
