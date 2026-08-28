from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import run_release_bound_product_readiness as wrapper


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
SNAPSHOT_SHA = "c" * 64
IMAGE_ID = "sha256:" + "d" * 64
CONTAINER_ID = "e" * 64


class _Stdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class ReleaseBoundProductReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.control_root = self.base / RELEASE_SHA
        self.scripts = self.control_root / "scripts"
        self.scripts.mkdir(parents=True)
        os.chmod(self.control_root, 0o700)
        os.chmod(self.scripts, 0o700)
        self.trusted_script = b"# trusted release readiness bytes\n"
        self.readiness = self.scripts / "check_production_coin_inference_readiness.py"
        self.readiness.write_bytes(self.trusted_script)
        os.chmod(self.readiness, 0o600)
        manifest_payload = (
            f"{sha256(self.trusted_script).hexdigest()}  "
            "./scripts/check_production_coin_inference_readiness.py\n"
        ).encode()
        self.manifest = self.control_root / wrapper.CONTROL_MANIFEST
        self.manifest.write_bytes(manifest_payload)
        os.chmod(self.manifest, 0o600)
        self.manifest_sha = sha256(manifest_payload).hexdigest()

    def _argv(self, **changes: str) -> list[str]:
        values = {
            "role": "bot",
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "control_root": str(self.control_root),
            "expected_control_manifest_sha256": self.manifest_sha,
            "container": "trading_bot_bot",
            "project": "trading_bot",
            "expected_image_id": IMAGE_ID,
            "expected_snapshot_sha256": SNAPSHOT_SHA,
            "confirm": wrapper.CONFIRMATION,
        }
        values.update(changes)
        return [
            "--role",
            values["role"],
            "--release-sha",
            values["release_sha"],
            "--release-tree",
            values["release_tree"],
            "--control-root",
            values["control_root"],
            "--expected-control-manifest-sha256",
            values["expected_control_manifest_sha256"],
            "--container",
            values["container"],
            "--project",
            values["project"],
            "--expected-image-id",
            values["expected_image_id"],
            "--expected-snapshot-sha256",
            values["expected_snapshot_sha256"],
            "--confirm",
            values["confirm"],
        ]

    @staticmethod
    def _container_document(
        *,
        project: str = "trading_bot",
        started_at: str = "2026-08-28T12:00:00.000000000Z",
        restart_count: int = 0,
    ) -> bytes:
        return json.dumps(
            [
                {
                    "Id": CONTAINER_ID,
                    "Name": "/trading_bot_bot",
                    "Image": IMAGE_ID,
                    "State": {
                        "Running": True,
                        "Pid": 12345,
                        "StartedAt": started_at,
                    },
                    "RestartCount": restart_count,
                    "Config": {
                        # The controller deliberately keeps the live Product
                        # authority LEGACY until its final CAS.  The wrapper's
                        # delegated probe must not depend on changing this.
                        "Env": [
                            "PYTHONPATH=/ambient-hostile",
                            "PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY",
                            "PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH=/ambient/wrong.json",
                            "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=999",
                            "COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED=false",
                            "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED=false",
                            "OFFER_MODEL_PRICE_GUARD_ENABLED=false",
                            "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED=true",
                        ],
                        "Labels": {
                            "com.docker.compose.project": project,
                            "com.docker.compose.service": "bot",
                        }
                    },
                }
            ]
        ).encode()

    @staticmethod
    def _image_document(
        *, release_sha: str = RELEASE_SHA, release_tree: str = RELEASE_TREE
    ) -> bytes:
        return json.dumps(
            [
                {
                    "Id": IMAGE_ID,
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.revision": release_sha,
                            "io.gold-trade.release.tree": release_tree,
                        }
                    },
                }
            ]
        ).encode()

    @staticmethod
    def _ready_payload() -> bytes:
        return (
            json.dumps(
                {
                    "status": "READY",
                    "authority": "PRIVATE_PRIMARY",
                    "snapshot_digest": SNAPSHOT_SHA,
                    "snapshot_hash": "1" * 64,
                    "snapshot_version": 1,
                    "snapshot_age_seconds": 1.25,
                    "rate_cell_count": 14,
                    "required_source_input_trace_count": 9,
                    "source_input_trace_sha256": "f" * 64,
                    "mount_read_only": True,
                    "enabled_flags": {
                        "preview": True,
                        "selection": True,
                        "guard": True,
                    },
                    "secrets_disclosed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    def _runner(
        self,
        *,
        readiness_payload: bytes | None = None,
        readiness_returncode: int = 0,
        image_release_sha: str = RELEASE_SHA,
        image_release_tree: str = RELEASE_TREE,
        observed: list[tuple[list[str], bytes | None]] | None = None,
    ):
        delegated = self._ready_payload() if readiness_payload is None else readiness_payload

        def run(command, *, input_bytes=None, timeout_seconds=180):
            del timeout_seconds
            command = list(command)
            if observed is not None:
                observed.append((command, input_bytes))
            if command[:4] == [wrapper.DOCKER_BINARY, "inspect", "--type", "container"]:
                return subprocess.CompletedProcess(
                    command, 0, self._container_document(), b""
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "image", "inspect"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    self._image_document(
                        release_sha=image_release_sha,
                        release_tree=image_release_tree,
                    ),
                    b"",
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]:
                return subprocess.CompletedProcess(
                    command, readiness_returncode, delegated, b""
                )
            raise AssertionError(command)

        return run

    @contextmanager
    def _main_output(self):
        output = _Stdout()
        with mock.patch.object(wrapper.sys, "stdout", output):
            yield output.buffer

    def test_success_passes_exact_single_json_and_exact_script_bytes(self) -> None:
        observed: list[tuple[list[str], bytes | None]] = []
        delegated = self._ready_payload()
        with mock.patch.object(
            wrapper, "_run", side_effect=self._runner(observed=observed)
        ):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), delegated)
        command, input_bytes = next(
            item
            for item in observed
            if item[0][:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]
        )
        self.assertEqual(input_bytes, self.trusted_script)
        self.assertEqual(command[:3], [wrapper.DOCKER_BINARY, "exec", "-i"])
        environment: list[str] = []
        index = 3
        while command[index] == "-e":
            environment.append(command[index + 1])
            index += 2
        self.assertEqual(environment, list(wrapper.ISOLATED_PRIVATE_PRIMARY_ENV))
        keys = [item.split("=", 1)[0] for item in environment]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all("=" in item for item in environment))
        self.assertEqual(command[index], CONTAINER_ID)
        self.assertNotIn("--role", command)
        self.assertNotIn("bot", command[index + 1 :])
        self.assertEqual(command[index + 1 : index + 3], ["python3", "-c"])
        self.assertEqual(command[index + 3], wrapper.READINESS_BOOTSTRAP)
        self.assertEqual(
            command[index + 4], sha256(self.trusted_script).hexdigest()
        )

    def test_live_legacy_container_runs_exact_isolated_private_primary_probe(
        self,
    ) -> None:
        live = json.loads(self._container_document())[0]
        live_environment = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in live["Config"]["Env"]
        }
        self.assertEqual(
            live_environment["PRODUCT_ESTIMATOR_SNAPSHOT_MODE"], "LEGACY"
        )
        observed: list[tuple[list[str], bytes | None]] = []
        with mock.patch.object(
            wrapper, "_run", side_effect=self._runner(observed=observed)
        ):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), self._ready_payload())
        command = next(
            item[0]
            for item in observed
            if item[0][:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]
        )
        isolated: list[str] = []
        index = 3
        while command[index] == "-e":
            isolated.append(command[index + 1])
            index += 2
        self.assertEqual(
            tuple(isolated),
            (
                "PYTHONPATH=/app",
                "PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY",
                f"PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH={wrapper.CONTAINER_SNAPSHOT}",
                "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=120",
                "COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED=true",
                "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED=true",
                "OFFER_MODEL_PRICE_GUARD_ENABLED=true",
                "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED=false",
            ),
        )
        self.assertEqual(len(isolated), len({item.split("=", 1)[0] for item in isolated}))
        self.assertNotIn("PYTHONPATH=/ambient-hostile", isolated)
        self.assertNotIn("PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY", isolated)
        self.assertEqual(command[index], CONTAINER_ID)

    def test_wrong_project_fails_before_docker(self) -> None:
        with mock.patch.object(wrapper, "_run") as run:
            with self._main_output() as output:
                result = wrapper.main(self._argv(project="wrong-project"))
        self.assertEqual(result, 2)
        run.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue())["reason_code"],
            "product_role_binding_invalid",
        )

    def test_wrong_image_release_is_rejected(self) -> None:
        with mock.patch.object(
            wrapper,
            "_run",
            side_effect=self._runner(image_release_sha="f" * 40),
        ):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(output.getvalue())["reason_code"],
            "product_image_release_mismatch",
        )

    def test_tampered_readiness_script_is_rejected(self) -> None:
        self.readiness.write_bytes(b"# tampered\n")
        os.chmod(self.readiness, 0o600)
        with mock.patch.object(wrapper, "_run") as run:
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        run.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue())["reason_code"],
            "readiness_script_digest_mismatch",
        )

    def test_malicious_working_directory_cannot_replace_release_script(self) -> None:
        hostile = self.base / "hostile"
        (hostile / "scripts").mkdir(parents=True)
        (hostile / "scripts" / "check_production_coin_inference_readiness.py").write_text(
            "raise SystemExit('hostile')\n", encoding="utf-8"
        )
        original = Path.cwd()
        observed: list[tuple[list[str], bytes | None]] = []
        try:
            os.chdir(hostile)
            with mock.patch.object(
                wrapper, "_run", side_effect=self._runner(observed=observed)
            ):
                with self._main_output() as output:
                    result = wrapper.main(self._argv())
        finally:
            os.chdir(original)
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), self._ready_payload())
        self.assertEqual(
            next(
                item[1]
                for item in observed
                if item[0][:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]
            ),
            self.trusted_script,
        )

    def test_future_or_stale_delegated_failure_passes_through_exactly(self) -> None:
        delegated = (
            b'{"reason":"private_primary_snapshot_stale_or_future",'
            b'"secrets_disclosed":false,"status":"BLOCKED"}\n'
        )
        with mock.patch.object(
            wrapper,
            "_run",
            side_effect=self._runner(
                readiness_payload=delegated, readiness_returncode=2
            ),
        ):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), delegated)

    def test_multiple_json_documents_are_not_filtered_or_passed_through(self) -> None:
        delegated = self._ready_payload() + self._ready_payload()
        with mock.patch.object(
            wrapper,
            "_run",
            side_effect=self._runner(readiness_payload=delegated),
        ):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            json.loads(lines[0])["reason_code"],
            "readiness_output_not_single_json",
        )

    def test_readiness_file_change_during_execution_is_detected(self) -> None:
        delegated = self._ready_payload()

        def runner(command, *, input_bytes=None, timeout_seconds=180):
            command = list(command)
            if command[:4] == [wrapper.DOCKER_BINARY, "inspect", "--type", "container"]:
                return subprocess.CompletedProcess(
                    command, 0, self._container_document(), b""
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "image", "inspect"]:
                return subprocess.CompletedProcess(
                    command, 0, self._image_document(), b""
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]:
                self.assertEqual(input_bytes, self.trusted_script)
                self.readiness.write_bytes(b"# replaced during execution\n")
                os.chmod(self.readiness, 0o600)
                return subprocess.CompletedProcess(command, 0, delegated, b"")
            raise AssertionError((command, timeout_seconds))

        with mock.patch.object(wrapper, "_run", side_effect=runner):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(output.getvalue())["reason_code"],
            "control_file_changed_during_execution",
        )

    def test_bootstrap_preserves_release_file_identity_for_stdin_bytes(self) -> None:
        payload = (
            b"from pathlib import Path\n"
            b"print(Path(__file__).resolve().parents[1])\n"
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper.READINESS_BOOTSTRAP,
                sha256(payload).hexdigest(),
            ],
            input=payload,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout, b"/app\n")

    def test_bootstrap_executes_the_real_readiness_script_without_stdin_file_error(
        self,
    ) -> None:
        repository = Path(wrapper.__file__).resolve().parents[1]
        payload = (repository / wrapper.READINESS_RELATIVE_PATH).read_bytes()
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper.READINESS_BOOTSTRAP,
                sha256(payload).hexdigest(),
                "--help",
            ],
            input=payload,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repository)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertIn(b"private-primary-consumer", completed.stdout)
        self.assertNotIn(b"IndexError", completed.stderr)

    def test_container_restart_during_readiness_is_rejected(self) -> None:
        inspections = 0

        def runner(command, *, input_bytes=None, timeout_seconds=180):
            nonlocal inspections
            del timeout_seconds
            command = list(command)
            if command[:4] == [
                wrapper.DOCKER_BINARY,
                "inspect",
                "--type",
                "container",
            ]:
                inspections += 1
                return subprocess.CompletedProcess(
                    command,
                    0,
                    self._container_document(
                        started_at=(
                            "2026-08-28T12:00:00.000000000Z"
                            if inspections == 1
                            else "2026-08-28T12:01:00.000000000Z"
                        ),
                        restart_count=inspections - 1,
                    ),
                    b"",
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "image", "inspect"]:
                return subprocess.CompletedProcess(
                    command, 0, self._image_document(), b""
                )
            if command[:3] == [wrapper.DOCKER_BINARY, "exec", "-i"]:
                self.assertEqual(input_bytes, self.trusted_script)
                return subprocess.CompletedProcess(
                    command, 0, self._ready_payload(), b""
                )
            raise AssertionError(command)

        with mock.patch.object(wrapper, "_run", side_effect=runner):
            with self._main_output() as output:
                result = wrapper.main(self._argv())
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(output.getvalue())["reason_code"],
            "product_container_changed_during_readiness",
        )

    def test_hostile_path_is_not_inherited_by_docker_subprocess(self) -> None:
        completed = subprocess.CompletedProcess(
            [wrapper.DOCKER_BINARY, "version"], 0, b"{}", b""
        )
        with mock.patch.dict(os.environ, {"PATH": str(self.base / "hostile")}), mock.patch.object(
            wrapper.subprocess, "run", return_value=completed
        ) as run:
            observed = wrapper._run([wrapper.DOCKER_BINARY, "version"])
        self.assertIs(observed, completed)
        _args, kwargs = run.call_args
        self.assertEqual(_args[0][0], "/usr/bin/docker")
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin"})
        self.assertEqual(kwargs["cwd"], "/")


if __name__ == "__main__":
    unittest.main()
