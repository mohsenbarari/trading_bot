from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
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

    def test_default_plan_has_no_commands_io_or_mutation(self):
        runner = mock.Mock(side_effect=AssertionError("plan executed a command"))
        result = MODULE.orchestrate(**self.base_arguments(), runner=runner)
        self.assertEqual(result["schema"], MODULE.PLAN_SCHEMA)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["docker_contacted"])
        self.assertFalse(result["network_io"])
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

        def runner(arguments, **_kwargs):
            calls.append(list(arguments))
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
            )
        self.assertEqual(result["status"], "snapshotted")
        self.assertFalse(result["source_mutated"])
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[0], MODULE.PYTHON)
        self.assertEqual(argv[1:3], ["-B", str(paths["producer"])])
        self.assertIn("--output-root", argv)
        self.assertEqual(argv[argv.index("--output-root") + 1], str(MODULE.SOURCE_OUTPUT_ROOT))
        self.assertEqual(
            argv[argv.index("--confirm") + 1],
            SOURCE.confirmation_phrase(binding),
        )
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
                encoded = arguments[-1].split()[-1]
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


if __name__ == "__main__":
    unittest.main()
