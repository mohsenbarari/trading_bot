from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_finland_source_snapshots as MODULE
from scripts import produce_production_shadow_source_snapshot as SOURCE


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def binding_document(role: str) -> dict:
    project = SOURCE.SOURCE_PROJECTS[role]
    return {
        "schema": SOURCE.BINDING_SCHEMA,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "role": role,
        "source_project": project,
        "containers": dict(SOURCE.SOURCE_CONTAINERS),
        "images": {
            **SOURCE.SOURCE_IMAGE_REFERENCES[role],
            "restore_postgres": (
                f"trading_bot_postgres_boottime:15-{RELEASE_SHA}"
            ),
        },
        "volumes": {
            kind: f"{project}_{suffix}"
            for kind, suffix in SOURCE.VOLUME_SUFFIXES.items()
        },
        "controller_manifest_sha256": "1" * 64,
        "approval_sha256": "2" * 64,
        "mode": "live-baseline",
    }


def completed(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def external_liveness_pipe(
    hold_seconds: float,
) -> tuple[int, subprocess.Popen[bytes]]:
    read_fd, write_fd = os.pipe()
    try:
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time,sys; time.sleep(float(sys.argv[1]))",
                str(hold_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(write_fd,),
            start_new_session=True,
        )
    finally:
        os.close(write_fd)
    return read_fd, holder


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.project_prefix = root / "project"
        self.secret_prefix = root / "secret"
        self.output_root = root / "source-output"
        self.known_hosts = root / "known_hosts"
        self.identity = root / "id_ed25519"
        for path in (
            self.project_prefix,
            self.secret_prefix,
            self.output_root.parent,
        ):
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)
        for path, payload in (
            (self.known_hosts, b"webapp-fi ssh-ed25519 test\n"),
            (self.identity, b"test-private-key\n"),
        ):
            path.write_bytes(payload)
            path.chmod(0o600)
        self.bindings: dict[str, Path] = {}
        for role in MODULE.ROLES:
            path = root / f"{role}.json"
            path.write_bytes(canonical_bytes(binding_document(role)))
            path.chmod(0o600)
            self.bindings[role] = path
        operation_secret = self.secret_prefix / OPERATION_ID
        operation_secret.mkdir(mode=0o700)
        operation_secret.chmod(0o700)
        controller = operation_secret / "controller"
        controller.mkdir(mode=0o700)
        controller.chmod(0o700)
        for role in MODULE.ROLES:
            role_root = operation_secret / MODULE.ROLE_PATHS[role]
            role_root.mkdir(mode=0o700)
            role_root.chmod(0o700)

    def patches(self):
        return (
            mock.patch.object(
                MODULE,
                "PROJECT_ROOT_PREFIX",
                self.project_prefix,
            ),
            mock.patch.object(
                MODULE,
                "SECRET_ROOT_PREFIX",
                self.secret_prefix,
            ),
            mock.patch.object(
                MODULE,
                "SOURCE_OUTPUT_ROOT",
                self.output_root,
            ),
            mock.patch.object(MODULE, "KNOWN_HOSTS", self.known_hosts),
        )


class FinlandSourceSnapshotOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)
        self.patchers = self.fixture.patches()
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def binding(self, role: str) -> SOURCE.SnapshotBinding:
        return SOURCE.load_binding(self.fixture.bindings[role])

    def base_arguments(self) -> dict:
        return {
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "bot_fi_binding": self.fixture.bindings["bot_fi"],
            "webapp_fi_binding": self.fixture.bindings["webapp_fi"],
            "ssh_identity": self.fixture.identity,
        }

    def test_host_cli_imports_under_isolated_python_outside_release_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(MODULE.__file__).resolve()),
                    "--help",
                ],
                cwd=directory,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/nonexistent",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith(b"usage:"))
        self.assertEqual(completed.stderr, b"")

    def test_default_plan_has_no_commands_io_or_mutation(self):
        runner = mock.Mock(side_effect=AssertionError("plan executed a command"))
        result = MODULE.orchestrate(**self.base_arguments(), runner=runner)
        self.assertEqual(result["schema"], MODULE.PLAN_SCHEMA)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["docker_contacted"])
        self.assertFalse(result["network_io"])
        self.assertEqual(MODULE.SAFE_ENV["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(
            MODULE.SAFE_ENV["GIT_CONFIG_GLOBAL"],
            "/dev/null",
        )
        self.assertEqual(
            MODULE.SAFE_ENV["GIT_CONFIG_SYSTEM"],
            "/dev/null",
        )
        self.assertEqual(MODULE.SAFE_ENV["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(MODULE.SAFE_ENV["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(MODULE.SAFE_ENV["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertIn("--no-optional-locks", MODULE.GIT_CONFIG_ARGUMENTS)
        self.assertIn(
            "core.fsmonitor=false",
            MODULE.GIT_CONFIG_ARGUMENTS,
        )
        self.assertIn(
            "core.untrackedCache=false",
            MODULE.GIT_CONFIG_ARGUMENTS,
        )
        self.assertIn(
            "core.hooksPath=/dev/null",
            MODULE.GIT_CONFIG_ARGUMENTS,
        )
        self.assertFalse(result["filesystem_mutated"])
        self.assertFalse(result["production_mutated"])
        self.assertEqual(result["pull_policy"], "never")
        self.assertFalse(runner.called)
        self.assertFalse(
            (
                self.fixture.secret_prefix
                / OPERATION_ID
                / "controller"
                / "source-snapshots"
            ).exists()
        )
        remote = result["roles"]["webapp_fi"]
        self.assertEqual(remote["transport"], "trusted-ssh-scp")
        command = remote["snapshot_argv"][-1]
        self.assertNotRegex(command, r"[$`;|&<>\n\r]")
        self.assertIn("--host-request-b64", command)
        self.assertIn("BatchMode=yes", remote["snapshot_argv"])
        self.assertIn("IdentitiesOnly=yes", remote["snapshot_argv"])
        self.assertIn("StrictHostKeyChecking=yes", remote["snapshot_argv"])
        self.assertIn(str(MODULE.WEBAPP_FI_PORT), remote["snapshot_argv"])
        self.assertNotIn("--control-fd", remote["snapshot_argv"])
        self.assertNotIn(
            "--control-fd",
            result["roles"]["bot_fi"]["snapshot_argv"],
        )

    def test_exact_release_git_checks_are_read_only_and_isolated(self):
        release_root = self.root / "release"
        scripts = release_root / "scripts"
        scripts.mkdir(parents=True, mode=0o700)
        release_root.chmod(0o700)
        agent = scripts / MODULE.AGENT_RELATIVE.name
        producer = scripts / MODULE.PRODUCER_RELATIVE.name
        agent.write_bytes(b"# agent\n")
        producer.write_bytes(b"# producer\n")
        agent.chmod(0o700)
        producer.chmod(0o700)
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(arguments, **kwargs):
            argv = list(arguments)
            calls.append((argv, dict(kwargs)))
            if "symbolic-ref" in argv:
                return completed(returncode=1)
            if "--show-toplevel" in argv:
                return completed(stdout=f"{release_root}\n".encode("ascii"))
            if "HEAD" in argv:
                return completed(stdout=f"{RELEASE_SHA}\n".encode("ascii"))
            return completed(stdout=b"")

        MODULE._validate_exact_release(
            release_root,
            RELEASE_SHA,
            runner=runner,
            required_uid=os.getuid(),
            agent_path=agent,
        )

        self.assertEqual(len(calls), 4)
        for argv, kwargs in calls:
            self.assertEqual(argv[0], MODULE.GIT)
            self.assertIn("--no-optional-locks", argv)
            self.assertIn("core.fsmonitor=false", argv)
            self.assertIn("core.untrackedCache=false", argv)
            self.assertIn("core.hooksPath=/dev/null", argv)
            self.assertIn("core.fileMode=true", argv)
            self.assertEqual(
                kwargs["env"]["GIT_CONFIG_GLOBAL"],
                "/dev/null",
            )
            self.assertEqual(kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_plan_rejects_confirm_and_cross_controller_bindings(self):
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "valid only",
        ):
            MODULE.orchestrate(
                **self.base_arguments(),
                confirm="unexpected",
            )
        changed = binding_document("webapp_fi")
        changed["approval_sha256"] = "3" * 64
        self.fixture.bindings["webapp_fi"].write_bytes(
            canonical_bytes(changed)
        )
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "one controller closure",
        ):
            MODULE.orchestrate(**self.base_arguments())

    def test_remote_argv_and_scp_paths_are_fixed_and_injection_free(self):
        paths = MODULE.canonical_paths(OPERATION_ID, RELEASE_SHA)
        request = MODULE.build_host_request(
            action="snapshot",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="webapp_fi",
            binding_sha256=self.binding("webapp_fi").canonical_sha256,
        )
        remote = [
            MODULE.PYTHON,
            str(paths["agent"]),
            "--host-request-b64",
            MODULE.encode_host_request(request),
        ]
        argv = MODULE.ssh_arguments(
            self.fixture.identity,
            remote_arguments=remote,
        )
        self.assertEqual(argv[0], MODULE.SSH)
        self.assertNotRegex(argv[-1], r"[$`;|&<>'\"\n\r]")
        self.assertEqual(argv[-1].split(), remote)

        upload = MODULE.scp_upload_arguments(
            self.fixture.identity,
            source=self.fixture.bindings["webapp_fi"],
            remote_destination=paths["roles"]["webapp_fi"][
                "binding_transfer"
            ],
        )
        self.assertEqual(upload[0], MODULE.SCP)
        self.assertIn("BatchMode=yes", upload)
        self.assertIn("StrictHostKeyChecking=yes", upload)
        download = MODULE.scp_download_arguments(
            self.fixture.identity,
            remote_source=(
                paths["roles"]["webapp_fi"]["snapshot"]
                / SOURCE.MANIFEST_FILE
            ),
            destination=(
                paths["roles"]["webapp_fi"]["collection"]
                / f".{SOURCE.MANIFEST_FILE}.transfer"
            ),
        )
        self.assertEqual(download[0], MODULE.SCP)
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "operation-derived",
        ):
            MODULE.scp_download_arguments(
                self.fixture.identity,
                remote_source=self.root / SOURCE.MANIFEST_FILE,
                destination=self.root / ".foreign.transfer",
            )
        colon_source = self.root / "binding:foreign.json"
        colon_source.write_bytes(b"{}")
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "canonical",
        ):
            MODULE.scp_upload_arguments(
                self.fixture.identity,
                source=colon_source,
                remote_destination=paths["roles"]["webapp_fi"][
                    "binding_transfer"
                ],
            )
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "unsafe",
        ):
            MODULE._remote_command([MODULE.PYTHON, "bad;command"])

    def test_host_binding_partial_is_reconciled_but_foreign_path_blocks(self):
        paths = MODULE.canonical_paths(OPERATION_ID, RELEASE_SHA)
        binding = self.binding("webapp_fi")
        request = MODULE.build_host_request(
            action="prepare-binding",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="webapp_fi",
            binding_sha256=binding.canonical_sha256,
        )
        transfer = paths["roles"]["webapp_fi"]["binding_transfer"]
        transfer.write_bytes(b"partial")
        transfer.chmod(0o600)
        result = MODULE._prepare_host_binding(request, required_uid=0)
        self.assertTrue(result["need_transfer"])
        self.assertTrue(result["partial_reconciled"])
        self.assertFalse(transfer.exists())

        target = self.root / "foreign"
        target.write_bytes(b"foreign")
        target.chmod(0o600)
        transfer.symlink_to(target)
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "ownership|foreign",
        ):
            MODULE._prepare_host_binding(request, required_uid=0)
        self.assertTrue(transfer.is_symlink())
        self.assertEqual(target.read_bytes(), b"foreign")

    def test_host_binding_is_create_only_and_exact_idempotent(self):
        paths = MODULE.canonical_paths(OPERATION_ID, RELEASE_SHA)
        binding = self.binding("bot_fi")
        request = MODULE.build_host_request(
            action="snapshot",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
            binding_sha256=binding.canonical_sha256,
        )
        transfer = paths["roles"]["bot_fi"]["binding_transfer"]
        transfer.write_bytes(self.fixture.bindings["bot_fi"].read_bytes())
        transfer.chmod(0o600)
        installed = MODULE._promote_host_binding(
            request,
            required_uid=0,
        )
        final = paths["roles"]["bot_fi"]["binding"]
        self.assertEqual(installed.canonical_sha256, binding.canonical_sha256)
        self.assertTrue(final.exists())
        self.assertFalse(transfer.exists())
        installed_again = MODULE._promote_host_binding(
            request,
            required_uid=0,
        )
        self.assertEqual(
            installed_again.canonical_sha256,
            binding.canonical_sha256,
        )
        # Resume the exact crash point after link publication but before
        # operation-owned transfer cleanup.
        transfer.hardlink_to(final)
        prepared = MODULE._prepare_host_binding(request, required_uid=0)
        self.assertFalse(prepared["need_transfer"])
        self.assertTrue(prepared["partial_reconciled"])
        self.assertFalse(transfer.exists())
        self.assertEqual(final.stat().st_nlink, 1)
        final.write_bytes(b"{}")
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "differs|invalid",
        ):
            MODULE._promote_host_binding(request, required_uid=0)

    def test_collection_partial_resume_and_tamper_fail_closed(self):
        collection = self.root / "collection"
        collection.mkdir(mode=0o700)
        collection.chmod(0o700)
        source = self.root / "artifact"
        source.write_bytes(b"complete-artifact")
        source.chmod(0o600)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = collection / "database.dump"
        partial = collection / ".database.dump.transfer"
        partial.write_bytes(b"truncated")
        partial.chmod(0o600)

        MODULE._copy_local_partial(
            source,
            partial,
            expected_sha256=digest,
            expected_bytes=source.stat().st_size,
            required_uid=0,
            maximum=1024,
        )
        publication = MODULE._publish_collection_file(
            partial,
            destination,
            expected_sha256=digest,
            expected_bytes=source.stat().st_size,
            required_uid=0,
            maximum=1024,
        )
        self.assertEqual(publication, "created")
        self.assertFalse(partial.exists())
        self.assertEqual(destination.read_bytes(), b"complete-artifact")

        # Resume the exact crash point after create-only link publication.
        partial.hardlink_to(destination)
        reused = MODULE._publish_collection_file(
            partial,
            destination,
            expected_sha256=digest,
            expected_bytes=source.stat().st_size,
            required_uid=0,
            maximum=1024,
        )
        self.assertEqual(reused, "reused")
        self.assertFalse(partial.exists())
        self.assertEqual(destination.stat().st_nlink, 1)

        destination.write_bytes(b"tampered")
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "differs",
        ):
            MODULE._publish_collection_file(
                partial,
                destination,
                expected_sha256=digest,
                expected_bytes=source.stat().st_size,
                required_uid=0,
                maximum=1024,
            )
        self.assertEqual(destination.read_bytes(), b"tampered")

    def test_collection_foreign_partial_is_never_removed(self):
        collection = self.root / "collection"
        collection.mkdir(mode=0o700)
        target = self.root / "target"
        target.write_bytes(b"do-not-touch")
        target.chmod(0o600)
        partial = collection / ".audit.tar.gz.transfer"
        partial.symlink_to(target)
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "foreign",
        ):
            MODULE._prepare_collection_partial(
                partial,
                expected_sha256="f" * 64,
                expected_bytes=10,
                required_uid=0,
                maximum=1024,
            )
        self.assertTrue(partial.is_symlink())
        self.assertEqual(target.read_bytes(), b"do-not-touch")

    def test_output_root_is_create_if_absent_and_foreign_symlink_blocks(self):
        self.assertFalse(self.fixture.output_root.exists())
        self.assertEqual(
            MODULE._ensure_host_output_root(required_uid=0),
            "created",
        )
        self.assertEqual(
            MODULE._ensure_host_output_root(required_uid=0),
            "reused",
        )
        self.fixture.output_root.rmdir()
        target = self.root / "outside"
        target.mkdir(mode=0o700)
        self.fixture.output_root.symlink_to(target)
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "unsafe",
        ):
            MODULE._ensure_host_output_root(required_uid=0)
        self.assertTrue(self.fixture.output_root.is_symlink())

    def test_host_agent_runs_only_exact_producer_with_bound_confirmation(self):
        paths = MODULE.canonical_paths(OPERATION_ID, RELEASE_SHA)
        release_root = paths["release_root"]
        (release_root / "scripts").mkdir(parents=True, mode=0o700)
        release_root.chmod(0o700)
        paths["agent"].write_bytes(b"# agent\n")
        paths["agent"].chmod(0o700)
        paths["producer"].write_bytes(b"# producer\n")
        paths["producer"].chmod(0o700)
        binding = self.binding("bot_fi")
        paths["roles"]["bot_fi"]["binding_transfer"].write_bytes(
            self.fixture.bindings["bot_fi"].read_bytes()
        )
        paths["roles"]["bot_fi"]["binding_transfer"].chmod(0o600)
        request = MODULE.build_host_request(
            action="snapshot",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
            binding_sha256=binding.canonical_sha256,
        )
        producer_result = {
            "schema": SOURCE.MANIFEST_SCHEMA,
            "status": "applied",
            "operation_id": OPERATION_ID,
            "role": "bot_fi",
            "mode": "live-baseline",
            "manifest": str(paths["roles"]["bot_fi"]["manifest"]),
            "zero_residue": True,
        }
        calls: list[list[str]] = []
        runner_options: list[dict[str, object]] = []

        def runner(arguments, **kwargs):
            calls.append(list(arguments))
            runner_options.append(dict(kwargs))
            return completed(stdout=canonical_bytes(producer_result) + b"\n")

        manifest = {
            "artifacts": {
                kind: {
                    "sha256": character * 64,
                    "bytes": index,
                    "restored_tree_sha256": None,
                }
                for index, (kind, character) in enumerate(
                    (
                        ("database-backup", "3"),
                        ("uploads-archive", "4"),
                        ("audit-archive", "5"),
                    ),
                    1,
                )
            }
        }
        fake_files = {
            filename: {"sha256": str(index + 6) * 64, "bytes": index + 10}
            for index, filename in enumerate(MODULE.SNAPSHOT_FILENAMES)
        }
        read_fd, holder = external_liveness_pipe(5)
        try:
            with (
                mock.patch.object(MODULE, "_validate_exact_release"),
                mock.patch.object(
                    MODULE.FINLAND_STAGE,
                    "_verify_role_host",
                ),
                mock.patch.object(MODULE, "_ensure_host_output_root"),
                mock.patch.object(
                    SOURCE,
                    "verify_completed_output",
                    return_value=manifest,
                ),
                mock.patch.object(
                    MODULE,
                    "_snapshot_file_inventory",
                    return_value=fake_files,
                ),
            ):
                result = MODULE.host_agent(
                    MODULE.encode_host_request(request),
                    runner=runner,
                    observed_host_addresses={MODULE.BOT_FI_HOST},
                    agent_path=paths["agent"],
                    control_fd=read_fd,
                )
        finally:
            os.close(read_fd)
            holder.terminate()
            holder.wait(timeout=1)
        self.assertEqual(result["status"], "snapshotted")
        self.assertFalse(result["source_mutated"])
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[0], MODULE.PYTHON)
        self.assertEqual(
            argv[1:4],
            ["-I", "-B", str(paths["producer"])],
        )
        self.assertIn("--output-root", argv)
        self.assertEqual(argv[argv.index("--output-root") + 1], str(MODULE.SOURCE_OUTPUT_ROOT))
        self.assertEqual(
            argv[argv.index("--confirm") + 1],
            SOURCE.confirmation_phrase(binding),
        )
        control_index = argv.index("--control-fd")
        passed_control = int(argv[control_index + 1], 10)
        self.assertEqual(
            runner_options[0]["pass_fds"],
            (passed_control,),
        )
        self.assertTrue(runner_options[0]["close_fds"])
        self.assertTrue(runner_options[0]["start_new_session"])
        self.assertNotIn("pull", " ".join(argv).lower())
        self.assertNotIn("build", " ".join(argv).lower())

    def test_apply_failure_resumes_from_durable_completed_role(self):
        payloads = {
            SOURCE.MANIFEST_FILE: b'{"manifest":"fixture"}',
            SOURCE.ARTIFACT_FILES["database-backup"]: b"database",
            SOURCE.ARTIFACT_FILES["uploads-archive"]: b"uploads",
            SOURCE.ARTIFACT_FILES["audit-archive"]: b"audit",
        }
        paths = MODULE.canonical_paths(OPERATION_ID, RELEASE_SHA)
        bot_source = paths["roles"]["bot_fi"]["snapshot"]
        bot_source.mkdir(parents=True, mode=0o700)
        bot_source.chmod(0o700)
        for filename, payload in payloads.items():
            path = bot_source / filename
            path.write_bytes(payload)
            path.chmod(0o600)

        def host_result(role: str) -> dict:
            return {
                "schema": MODULE.HOST_RESULT_SCHEMA,
                "status": "snapshotted",
                "snapshot_status": "applied",
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "role": role,
                "mode": "live-baseline",
                "binding_sha256": self.binding(role).canonical_sha256,
                "files": {
                    filename: {
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                    }
                    for filename, payload in payloads.items()
                },
                "zero_residue": True,
                "pull_policy": "never",
                "scratch_network_mode": "none",
                "source_mutated": False,
                "current_mutated": False,
                "source_stopped_or_restarted": False,
                "redis_restored": False,
            }

        calls: list[tuple[str, str]] = []

        def runner(arguments, **_kwargs):
            if arguments[0] == MODULE.SCP:
                # Uploads have local source first. Downloads have local partial last.
                if arguments[-2].startswith(
                    f"{MODULE.WEBAPP_FI_USER}@{MODULE.WEBAPP_FI_HOST}:"
                ):
                    remote = arguments[-2].split(":", 1)[1]
                    filename = Path(remote).name
                    destination = Path(arguments[-1])
                    destination.write_bytes(payloads[filename])
                    destination.chmod(0o600)
                return completed()
            if arguments[0] == MODULE.SSH:
                remote_arguments = arguments[-1].split()
                encoded = remote_arguments[
                    remote_arguments.index("--host-request-b64") + 1
                ]
            else:
                encoded = arguments[arguments.index("--host-request-b64") + 1]
            request = MODULE.decode_host_request(encoded)
            calls.append((request["role"], request["action"]))
            if request["action"] == "prepare-binding":
                result = {
                    "schema": MODULE.HOST_PREPARE_SCHEMA,
                    "status": "prepared",
                    "operation_id": OPERATION_ID,
                    "release_sha": RELEASE_SHA,
                    "role": request["role"],
                    "binding_sha256": request["binding_sha256"],
                    "need_transfer": True,
                    "partial_reconciled": False,
                    "docker_contacted": False,
                    "production_mutated": False,
                }
            else:
                result = host_result(request["role"])
            return completed(stdout=canonical_bytes(result) + b"\n")

        def verify_collection(*, role, binding, paths):
            manifest = paths["roles"][role]["collection"] / SOURCE.MANIFEST_FILE
            self.assertEqual(manifest.read_bytes(), payloads[SOURCE.MANIFEST_FILE])
            return {
                "manifest_path": str(manifest),
                "manifest_sha256": hashlib.sha256(
                    manifest.read_bytes()
                ).hexdigest(),
            }

        confirmation = MODULE.confirmation_phrase(OPERATION_ID, RELEASE_SHA)

        def checkpoint(name: str) -> None:
            if name == "after-role:bot_fi":
                raise RuntimeError("injected controller interruption")

        with (
            mock.patch.object(
                MODULE.FINLAND_STAGE,
                "_verify_role_host",
            ),
            mock.patch.object(
                MODULE,
                "_verify_collected_role",
                side_effect=verify_collection,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                MODULE.orchestrate(
                    **self.base_arguments(),
                    apply=True,
                    confirm=confirmation,
                    runner=runner,
                    checkpoint=checkpoint,
                    observed_host_addresses={MODULE.BOT_FI_HOST},
                )
            first_calls = list(calls)
            calls.clear()
            result = MODULE.orchestrate(
                **self.base_arguments(),
                apply=True,
                confirm=confirmation,
                runner=runner,
                observed_host_addresses={MODULE.BOT_FI_HOST},
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            first_calls,
            [
                ("bot_fi", "prepare-binding"),
                ("bot_fi", "snapshot"),
            ],
        )
        self.assertEqual(
            calls,
            [
                ("webapp_fi", "prepare-binding"),
                ("webapp_fi", "snapshot"),
            ],
        )
        journal_path = Path(result["journal_path"])
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["completed_roles"], list(MODULE.ROLES))
        self.assertEqual(
            journal["state_sha256"],
            MODULE._state_sha256(journal),
        )
        self.assertEqual(journal_path.stat().st_mode & 0o777, 0o600)

    def test_apply_wrong_confirmation_performs_no_mutation_or_network(self):
        runner = mock.Mock(side_effect=AssertionError("network contacted"))
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "confirmation mismatch",
        ):
            MODULE.orchestrate(
                **self.base_arguments(),
                apply=True,
                confirm="wrong",
                runner=runner,
            )
        self.assertFalse(runner.called)
        self.assertFalse(
            (
                self.fixture.secret_prefix
                / OPERATION_ID
                / "controller"
                / "source-snapshots"
            ).exists()
        )

    def test_apply_requires_root_main_thread_before_io(self):
        confirmation = MODULE.confirmation_phrase(
            OPERATION_ID,
            RELEASE_SHA,
        )
        runner = mock.Mock(side_effect=AssertionError("command executed"))
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "must run as root",
        ):
            MODULE.orchestrate(
                **self.base_arguments(),
                apply=True,
                confirm=confirmation,
                runner=runner,
                required_uid=1,
            )
        with (
            mock.patch.object(
                MODULE.threading,
                "current_thread",
                return_value=object(),
            ),
            mock.patch.object(
                MODULE.threading,
                "main_thread",
                return_value=object(),
            ),
            self.assertRaisesRegex(
                MODULE.FinlandSourceSnapshotOrchestratorError,
                "main thread",
            ),
        ):
            MODULE.orchestrate(
                **self.base_arguments(),
                apply=True,
                confirm=confirmation,
                runner=runner,
            )
        self.assertFalse(runner.called)

    def test_main_blocks_mixed_host_and_controller_arguments(self):
        request = MODULE.build_host_request(
            action="prepare-binding",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
            binding_sha256=self.binding("bot_fi").canonical_sha256,
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = MODULE.main(
                [
                    "--host-request-b64",
                    MODULE.encode_host_request(request),
                    "--operation-id",
                    OPERATION_ID,
                ]
            )
        self.assertEqual(status, 1)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("binding_sha256", result)
        self.assertNotIn("http", stderr.getvalue().lower())

    def test_main_requires_host_control_fd_and_rejects_it_for_controller(
        self,
    ):
        request = MODULE.build_host_request(
            action="prepare-binding",
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            role="bot_fi",
            binding_sha256=self.binding("bot_fi").canonical_sha256,
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = MODULE.main(
                [
                    "--host-request-b64",
                    MODULE.encode_host_request(request),
                ]
            )
        self.assertEqual(status, 1)
        self.assertIn("requires liveness", stderr.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = MODULE.main(["--control-fd", "0"])
        self.assertEqual(status, 1)
        self.assertIn("valid only for a host request", stderr.getvalue())

        read_fd, holder = external_liveness_pipe(5)
        stdout = io.StringIO()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "host_agent",
                    return_value={"status": "prepared"},
                ) as host_agent,
                redirect_stdout(stdout),
            ):
                status = MODULE.main(
                    [
                        "--host-request-b64",
                        MODULE.encode_host_request(request),
                        "--control-fd",
                        str(read_fd),
                    ]
                )
            self.assertEqual(status, 0)
            host_agent.assert_called_once_with(
                MODULE.encode_host_request(request),
                control_fd=read_fd,
            )
        finally:
            os.close(read_fd)
            holder.terminate()
            holder.wait(timeout=1)

    def test_main_redacts_unexpected_failure(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "orchestrate",
                side_effect=RuntimeError("private token value"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = MODULE.main(
                [
                    "--operation-id",
                    OPERATION_ID,
                    "--release-sha",
                    RELEASE_SHA,
                    "--bot-fi-binding",
                    str(self.fixture.bindings["bot_fi"]),
                    "--webapp-fi-binding",
                    str(self.fixture.bindings["webapp_fi"]),
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("private token", stderr.getvalue())


class FinlandSourceSnapshotProcessFencingTests(unittest.TestCase):
    def test_host_liveness_rejects_worker_held_writer_end(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.FinlandSourceSnapshotOrchestratorError,
                "writer end",
            ):
                MODULE.ControllerLivenessGuard(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_preclosed_liveness_fails_before_host_work_and_restores_signals(
        self,
    ) -> None:
        old_handlers = {
            signum: signal.getsignal(signum)
            for signum in MODULE.ControllerLivenessGuard._HANDLED_SIGNALS
        }
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(
                MODULE.FinlandSourceSnapshotCancellation,
                "EOF",
            ):
                with MODULE.ControllerLivenessGuard(read_fd):
                    self.fail("preclosed liveness entered host work")
        finally:
            os.close(read_fd)
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in MODULE.ControllerLivenessGuard._HANDLED_SIGNALS
            },
            old_handlers,
        )

    def test_host_signals_are_single_catchable_cancellation(self) -> None:
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            with self.subTest(signum=signum):
                read_fd, holder = external_liveness_pipe(5)
                try:
                    with MODULE.ControllerLivenessGuard(read_fd) as guard:
                        with self.assertRaises(
                            MODULE.FinlandSourceSnapshotCancellation
                        ):
                            guard._handle_signal(signum, None)
                        self.assertIsNone(
                            guard._handle_signal(signal.SIGTERM, None)
                        )
                finally:
                    os.close(read_fd)
                    holder.terminate()
                    holder.wait(timeout=1)

    def test_controller_host_invocation_uses_pipe_without_writer_leak(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "inspect-control.py"
            helper.write_text(
                (
                    "import fcntl,json,os,stat,sys\n"
                    "index=sys.argv.index('--control-fd')\n"
                    "fd=int(sys.argv[index+1])\n"
                    "target=os.fstat(fd)\n"
                    "writers=[]\n"
                    "for name in os.listdir('/proc/self/fd'):\n"
                    " try:\n"
                    "  candidate=int(name)\n"
                    "  row=os.fstat(candidate)\n"
                    "  flags=fcntl.fcntl(candidate, fcntl.F_GETFL)\n"
                    " except (OSError,ValueError):\n"
                    "  continue\n"
                    " if ((row.st_dev,row.st_ino)=="
                    "(target.st_dev,target.st_ino) and "
                    "flags & os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR)):\n"
                    "  writers.append(candidate)\n"
                    "result={'control_fd':fd,'fifo':"
                    "stat.S_ISFIFO(target.st_mode),'writers':writers}\n"
                    "print(json.dumps(result,sort_keys=True,"
                    "separators=(',',':')))\n"
                ),
                encoding="ascii",
            )
            with (
                mock.patch.object(MODULE, "PYTHON", sys.executable),
                mock.patch.object(
                    MODULE,
                    "encode_host_request",
                    return_value="encoded",
                ),
            ):
                result = MODULE._invoke_host(
                    role="bot_fi",
                    request={},
                    paths={"agent": helper},
                    ssh_identity=Path("/unused"),
                    runner=None,
                )
        self.assertEqual(result["control_fd"], 0)
        self.assertTrue(result["fifo"])
        self.assertEqual(result["writers"], [])

    def test_transport_baseexception_closes_liveness_writer(self) -> None:
        class HostAbort(BaseException):
            pass

        retained_read = -1

        def runner(_arguments, **kwargs):
            nonlocal retained_read
            retained_read = os.dup(kwargs["stdin"])
            raise HostAbort()

        with (
            mock.patch.object(
                MODULE,
                "encode_host_request",
                return_value="encoded",
            ),
            self.assertRaises(HostAbort),
        ):
            MODULE._invoke_host(
                role="webapp_fi",
                request={},
                paths={"agent": Path("/safe/agent.py")},
                ssh_identity=Path("/safe/id"),
                runner=runner,
            )
        try:
            self.assertGreaterEqual(retained_read, 0)
            os.set_blocking(retained_read, False)
            self.assertEqual(os.read(retained_read, 1), b"")
        finally:
            if retained_read >= 0:
                os.close(retained_read)

    def test_bounded_command_handles_flood_timeout_and_detached_children(
        self,
    ) -> None:
        for descriptor, label in ((1, "stdout"), (2, "stderr")):
            with (
                self.subTest(label=label),
                mock.patch.object(
                    MODULE,
                    (
                        "MAX_COMMAND_OUTPUT_BYTES"
                        if descriptor == 1
                        else "MAX_COMMAND_ERROR_BYTES"
                    ),
                    1024,
                ),
                self.assertRaisesRegex(
                    MODULE.FinlandSourceSnapshotOrchestratorError,
                    label,
                ),
            ):
                MODULE._bounded_command(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os,sys\n"
                            f"descriptor={descriptor}\n"
                            "while True:\n"
                            " os.write(descriptor, b'x' * 65536)\n"
                        ),
                    ],
                    timeout=2,
                )
        with self.assertRaisesRegex(
            MODULE.FinlandSourceSnapshotOrchestratorError,
            "timed out",
        ):
            MODULE._bounded_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                timeout=0.05,
            )

        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "delayed-setsid-survivor"
            program = (
                "import os,signal,sys,time\n"
                "child=os.fork()\n"
                "if child == 0:\n"
                " os.setsid()\n"
                " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                " time.sleep(0.5)\n"
                " open(sys.argv[1], 'wb').write(b'survived')\n"
                " os._exit(0)\n"
                "time.sleep(5)\n"
            )
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_GROUP_TERM_SECONDS",
                    0.05,
                ),
                mock.patch.object(MODULE, "PROCESS_POLL_SECONDS", 0.005),
                self.assertRaisesRegex(
                    MODULE.FinlandSourceSnapshotOrchestratorError,
                    "timed out",
                ),
            ):
                MODULE._bounded_command(
                    [sys.executable, "-c", program, str(sentinel)],
                    timeout=0.1,
                )
            time.sleep(0.6)
            self.assertFalse(sentinel.exists())

            rapid_sentinel = Path(directory) / "rapid-setsid-survivor"
            rapid_program = (
                "import os,signal,sys,time\n"
                "child=os.fork()\n"
                "if child == 0:\n"
                " os.setsid()\n"
                " os.close(1); os.close(2)\n"
                " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                " time.sleep(0.4)\n"
                " open(sys.argv[1], 'wb').write(b'survived')\n"
                " os._exit(0)\n"
                "os._exit(0)\n"
            )
            result = MODULE._bounded_command(
                [
                    sys.executable,
                    "-c",
                    rapid_program,
                    str(rapid_sentinel),
                ],
                timeout=2,
            )
            self.assertEqual(result.returncode, 0)
            time.sleep(0.5)
            self.assertFalse(rapid_sentinel.exists())

    def test_bounded_command_reaps_adopted_double_fork_zombies(self) -> None:
        baseline = MODULE._direct_child_baseline()
        program = (
            "import os\n"
            "if os.fork() == 0:\n"
            " if os.fork() == 0:\n"
            "  os._exit(0)\n"
            " os._exit(0)\n"
            "os._exit(0)\n"
        )
        result = MODULE._bounded_command(
            [sys.executable, "-c", program],
            timeout=2,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            MODULE._direct_child_baseline() - baseline,
            frozenset(),
        )

    def test_source_control_fd_does_not_leak_other_descriptors(self) -> None:
        control_read, control_write = os.pipe()
        secret_read, secret_write = os.pipe()
        try:
            secret = os.fstat(secret_read)
            program = (
                "import json,os,sys\n"
                "target=(int(sys.argv[1]),int(sys.argv[2]))\n"
                "leaked=[]\n"
                "for name in os.listdir('/proc/self/fd'):\n"
                " try:\n"
                "  fd=int(name); row=os.fstat(fd)\n"
                " except (OSError,ValueError):\n"
                "  continue\n"
                " if (row.st_dev,row.st_ino)==target:\n"
                "  leaked.append(fd)\n"
                "print(json.dumps({'leaked':leaked},sort_keys=True,"
                "separators=(',',':')))\n"
            )
            result = MODULE._bounded_command(
                [
                    sys.executable,
                    "-c",
                    program,
                    str(secret.st_dev),
                    str(secret.st_ino),
                ],
                timeout=2,
                pass_fds=(control_read,),
            )
            self.assertEqual(json.loads(result.stdout), {"leaked": []})
        finally:
            for descriptor in (
                control_read,
                control_write,
                secret_read,
                secret_write,
            ):
                os.close(descriptor)

    def test_controller_eof_during_source_kills_detached_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "source-survivor"
            producer = root / "source-producer.py"
            producer.write_text(
                (
                    "import os,signal,sys,time\n"
                    "index=sys.argv.index('--control-fd')\n"
                    "os.fstat(int(sys.argv[index+1]))\n"
                    "child=os.fork()\n"
                    "if child == 0:\n"
                    " os.setsid()\n"
                    " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    " time.sleep(0.5)\n"
                    f" open({str(sentinel)!r}, 'wb').write(b'survived')\n"
                    " os._exit(0)\n"
                    "time.sleep(5)\n"
                ),
                encoding="ascii",
            )
            read_fd, holder = external_liveness_pipe(0.05)
            try:
                with (
                    mock.patch.object(
                        MODULE,
                        "PROCESS_GROUP_TERM_SECONDS",
                        0.05,
                    ),
                    mock.patch.object(
                        MODULE,
                        "PROCESS_POLL_SECONDS",
                        0.005,
                    ),
                    MODULE.ControllerLivenessGuard(read_fd) as guard,
                ):
                    with self.assertRaises(
                        MODULE.FinlandSourceSnapshotCancellation
                    ):
                        MODULE._run_command(
                            [
                                sys.executable,
                                str(producer),
                                "--control-fd",
                                str(guard.control_fd),
                            ],
                            runner=None,
                            timeout=2,
                            allowed=frozenset({sys.executable}),
                            pass_fds=(guard.control_fd,),
                        )
            finally:
                os.close(read_fd)
                holder.wait(timeout=1)
            time.sleep(0.6)
            self.assertFalse(sentinel.exists())

    def test_remote_ssh_timeout_kills_detached_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "ssh-survivor"
            transport = root / "ssh-transport.py"
            transport.write_text(
                (
                    "import os,signal,sys,time\n"
                    "child=os.fork()\n"
                    "if child == 0:\n"
                    " os.setsid()\n"
                    " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    " time.sleep(0.5)\n"
                    " open(sys.argv[1], 'wb').write(b'survived')\n"
                    " os._exit(0)\n"
                    "time.sleep(5)\n"
                ),
                encoding="ascii",
            )
            with (
                mock.patch.object(MODULE, "SSH", sys.executable),
                mock.patch.object(
                    MODULE,
                    "encode_host_request",
                    return_value="encoded",
                ),
                mock.patch.object(
                    MODULE,
                    "ssh_arguments",
                    return_value=[
                        sys.executable,
                        str(transport),
                        str(sentinel),
                    ],
                ),
                mock.patch.object(
                    MODULE,
                    "HOST_COMMAND_TIMEOUT_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_GROUP_TERM_SECONDS",
                    0.05,
                ),
                mock.patch.object(MODULE, "PROCESS_POLL_SECONDS", 0.005),
                self.assertRaisesRegex(
                    MODULE.FinlandSourceSnapshotOrchestratorError,
                    "timed out",
                ),
            ):
                MODULE._invoke_host(
                    role="webapp_fi",
                    request={},
                    paths={"agent": Path("/safe/agent.py")},
                    ssh_identity=Path("/safe/id"),
                    runner=None,
                )
            time.sleep(0.6)
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
