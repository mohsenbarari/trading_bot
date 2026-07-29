from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from unittest import mock
import unittest

from scripts import production_shadow_finland_stage as MODULE


OPERATION_ID = "12345678-1234-4abc-8def-1234567890ab"
RELEASE_SHA = "1" * 40
RELEASE_TREE_SHA = "2" * 40


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def secure_file(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def external_liveness_pipe():
    control_read, control_write = os.pipe()
    stop_read, stop_write = os.pipe()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import os,sys;os.read(int(sys.argv[1]),1)",
            str(stop_read),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(control_write, stop_read),
        close_fds=True,
    )
    os.close(control_write)
    os.close(stop_read)
    return control_read, holder, stop_write


def stop_liveness_holder(
    holder: subprocess.Popen[bytes],
    stop_write: int,
) -> None:
    try:
        os.write(stop_write, b"x")
    except OSError:
        pass
    try:
        os.close(stop_write)
    except OSError:
        pass
    try:
        holder.wait(timeout=2)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=2)


def image_archive(
    role: str,
    *,
    release_sha: str = RELEASE_SHA,
    attack: str | None = None,
) -> tuple[bytes, dict, dict]:
    index = MODULE.IMAGE_ROLES.index(role) + 3
    labels: dict[str, str] = {}
    if role in MODULE.RELEASE_BOUND_IMAGE_ROLES:
        labels["org.opencontainers.image.revision"] = release_sha
    if role == "postgres":
        labels[MODULE.POSTGRES_RUNTIME_UID_LABEL] = "70"
        labels[MODULE.POSTGRES_RUNTIME_GID_LABEL] = "70"
    config = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-{index:02d}T00:00:00Z",
        "config": {
            "Env": [f"ROLE={role}"],
            "Labels": labels,
        },
        "rootfs": {
            "type": "layers",
            "diff_ids": [f"sha256:{index:x}" * 0 + "sha256:" + f"{index:x}" * 64],
        },
    }
    config_raw = canonical(config)
    config_name = hashlib.sha256(config_raw).hexdigest() + ".json"
    layer_name = f"{index:x}" * 64 + "/layer.tar"
    manifest = [
        {
            "Config": config_name,
            "RepoTags": None,
            "Layers": [layer_name],
        }
    ]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, payload in (
            ("manifest.json", canonical(manifest)),
            (config_name, config_raw),
            (layer_name, f"layer-{role}".encode("ascii")),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(payload))
        if attack == "traversal":
            payload = b"escape"
            member = tarfile.TarInfo("../escape")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        elif attack == "link":
            member = tarfile.TarInfo("bad-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/shadow"
            archive.addfile(member)
        elif attack == "duplicate":
            payload = b"duplicate"
            member = tarfile.TarInfo("manifest.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    descriptor, identity = MODULE.image_content_descriptor_from_archive(config)
    binding = {
        "archive_sha256": hashlib.sha256(stream.getvalue()).hexdigest(),
        "archive_bytes": len(stream.getvalue()),
        "config_digest": "sha256:" + hashlib.sha256(config_raw).hexdigest(),
        "content_descriptor": descriptor,
        "content_identity": identity,
    }
    inspect = {
        "Architecture": config["architecture"],
        "Os": config["os"],
        "Created": config["created"],
        "Config": config["config"],
        "RootFS": {
            "Type": config["rootfs"]["type"],
            "Layers": config["rootfs"]["diff_ids"],
        },
    }
    return stream.getvalue(), binding, inspect


class FakeRunner:
    def __init__(
        self,
        fixture: "StageFixture",
        *,
        preexisting: dict[str, dict] | None = None,
    ) -> None:
        self.fixture = fixture
        self.calls: list[list[str]] = []
        self.release_verification_launcher_modes: list[int | None] = []
        self.remote_removed = False
        self.loaded: dict[str, dict] = dict(preexisting or {})
        runtime_characters = {
            "app": "9",
            "postgres": "a",
            "redis": "b",
            "nginx": "c",
        }
        self.runtime_ids = {
            role: "sha256:" + runtime_characters[role] * 64
            for role in MODULE.IMAGE_ROLES
        }

    def __call__(self, arguments, **_kwargs):  # noqa: ANN001
        argv = [str(value) for value in arguments]
        self.calls.append(argv)
        stdout = b""
        if argv[0] == MODULE.GIT:
            if argv[1:3] == ["bundle", "list-heads"]:
                stdout = f"{RELEASE_SHA} HEAD\n".encode()
            elif "clone" in argv:
                release = Path(argv[-1])
                release.mkdir(mode=0o700)
                (release / ".git").mkdir(mode=0o700)
            elif "checkout" in argv and "--detach" in argv:
                release = Path(argv[argv.index("-C") + 1])
                self.fixture.materialize_release_tree(release)
            elif argv[-2:] == ["remote", "remove"] or (
                "remote" in argv and "remove" in argv
            ):
                self.remote_removed = True
            elif argv[-1] == "remote":
                stdout = b"" if self.remote_removed else b"origin\n"
            elif argv[-1] == "--show-toplevel":
                launcher = (
                    self.fixture.paths["release_root"]
                    / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
                )
                if launcher.exists() and not launcher.is_symlink():
                    self.release_verification_launcher_modes.append(
                        stat.S_IMODE(launcher.stat(follow_symlinks=False).st_mode)
                    )
                else:
                    self.release_verification_launcher_modes.append(None)
                stdout = (str(self.fixture.paths["release_root"]) + "\n").encode()
            elif argv[-1] == "HEAD^{tree}":
                stdout = (RELEASE_TREE_SHA + "\n").encode()
            elif argv[-2:] == ["--abbrev-ref", "HEAD"]:
                stdout = b"HEAD\n"
            elif argv[-1] == "HEAD":
                stdout = (RELEASE_SHA + "\n").encode()
            elif "status" in argv:
                stdout = b""
        elif argv[0] == MODULE.DOCKER:
            if argv[1:3] == ["image", "ls"]:
                stdout = (
                    ("\n".join(sorted(self.loaded)) + "\n").encode()
                    if self.loaded
                    else b""
                )
            elif argv[1:3] == ["image", "load"]:
                archive = Path(argv[argv.index("--input") + 1])
                role = archive.name.removesuffix("-image.tar")
                image_id = self.runtime_ids[role]
                image = dict(self.fixture.inspect_documents[role])
                image["Id"] = image_id
                self.loaded[image_id] = image
                stdout = b"Loaded image ID: destination-local\n"
            elif argv[1:3] == ["image", "inspect"]:
                image_id = argv[-1]
                stdout = canonical([self.loaded[image_id]])
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=stdout,
            stderr=b"",
        )


class StageFixture:
    def __init__(self, root: Path, *, partial_inputs: bool = False) -> None:
        self.root = root
        self.project_prefix = root / "project"
        self.secret_prefix = root / "secret"
        self.project_prefix.mkdir(mode=0o700)
        self.secret_prefix.mkdir(mode=0o700)
        self.stack = ExitStack()
        self.stack.enter_context(
            mock.patch.object(
                MODULE,
                "PROJECT_ROOT_PREFIX",
                self.project_prefix,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                MODULE,
                "SECRET_ROOT_PREFIX",
                self.secret_prefix,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                MODULE,
                "observe_local_ipv4_addresses",
                return_value={MODULE.ROLE_HOSTS["bot_fi"]},
            )
        )
        self.paths = MODULE.ensure_operation_directories(
            OPERATION_ID,
            RELEASE_SHA,
            "bot_fi",
        )
        self.agent_bytes = b"#!/usr/bin/env python3\n# fixed bootstrap\n"
        self.agent_sha256 = hashlib.sha256(self.agent_bytes).hexdigest()
        secure_file(self.paths["agent"], self.agent_bytes, 0o700)
        self.release_feature = "current"

        self.archives: dict[str, bytes] = {}
        self.image_bindings: dict[str, dict] = {}
        self.inspect_documents: dict[str, dict] = {}
        for role in MODULE.IMAGE_ROLES:
            archive, binding, inspect = image_archive(role)
            self.archives[role] = archive
            self.image_bindings[role] = binding
            self.inspect_documents[role] = inspect
        self.bundle = b"exact-release-bundle"
        self.document = self._manifest_document()
        self.manifest_bytes = canonical(self.document)
        self.manifest_sha256 = hashlib.sha256(self.manifest_bytes).hexdigest()
        self.write_inputs(partial=partial_inputs)
        self.request = {
            "schema": MODULE.REQUEST_SCHEMA,
            "action": "stage",
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "role": "bot_fi",
            "operation_manifest_sha256": self.manifest_sha256,
            "agent_sha256": self.agent_sha256,
            "pull_policy": "never",
        }
        self.runner = FakeRunner(self)

    def close(self) -> None:
        self.stack.close()

    def materialize_release_tree(self, release: Path) -> None:
        if self.release_feature == "legacy":
            return
        scripts = release / "scripts"
        scripts.mkdir(mode=0o755)
        scripts.chmod(0o755)
        producer = scripts / (
            "produce_production_shadow_convergence_source_set.py"
        )
        launcher = scripts / "production_shadow_convergence_source_set_launcher"
        if self.release_feature != "launcher-only":
            secure_file(
                producer,
                b"#!/usr/bin/env python3\n# source-set producer\n",
                0o644,
            )
        if self.release_feature == "producer-only":
            return
        if self.release_feature == "launcher-symlink":
            launcher.symlink_to("/etc/passwd")
            return
        if self.release_feature == "launcher-fifo":
            os.mkfifo(launcher, 0o600)
            return
        secure_file(launcher, b"#!/bin/sh\nexit 0\n", 0o755)

    def _manifest_document(self) -> dict:
        artifacts = {
            "release-bundle": {
                "kind": "release-bundle",
                "filename": "release.bundle",
                "sha256": hashlib.sha256(self.bundle).hexdigest(),
                "bytes": len(self.bundle),
                "format": "git-bundle",
            }
        }
        for role in MODULE.IMAGE_ROLES:
            kind = f"{role}-image-archive"
            binding = self.image_bindings[role]
            artifacts[kind] = {
                "kind": kind,
                "filename": f"{role}-image.tar",
                "sha256": binding["archive_sha256"],
                "bytes": binding["archive_bytes"],
                "format": "docker-archive",
            }
        return {
            "schema": MODULE.MANIFEST_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "role": "bot_fi",
            "project_name": self.paths["project_name"],
            "project_root": str(self.paths["project_root"]),
            "release_root": str(self.paths["release_root"]),
            "incoming_root": str(self.paths["incoming_root"]),
            "secret_role_root": str(self.paths["secret_role_root"]),
            "bootstrap_sha256": self.agent_sha256,
            "artifacts": artifacts,
            "image_artifacts": self.image_bindings,
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
            "pull_policy": "never",
        }

    def write_inputs(self, *, partial: bool = False) -> None:
        incoming = self.paths["incoming_root"]
        files = {
            MODULE.MANIFEST_FILENAME: self.manifest_bytes,
            "release.bundle": self.bundle,
            **{
                f"{role}-image.tar": self.archives[role]
                for role in MODULE.IMAGE_ROLES
            },
        }
        for filename, payload in files.items():
            destination = incoming / filename
            if partial:
                destination = MODULE.transfer_partial_path(destination)
            secure_file(destination, payload, 0o600)

    def replace_archive(
        self,
        role: str,
        archive: bytes,
        binding: dict,
        inspect: dict,
    ) -> None:
        self.archives[role] = archive
        self.image_bindings[role] = binding
        self.inspect_documents[role] = inspect
        self.document = self._manifest_document()
        self.manifest_bytes = canonical(self.document)
        self.manifest_sha256 = hashlib.sha256(self.manifest_bytes).hexdigest()
        manifest_path = self.paths["manifest"]
        manifest_path.unlink(missing_ok=True)
        secure_file(manifest_path, self.manifest_bytes, 0o600)
        archive_path = self.paths["incoming_root"] / f"{role}-image.tar"
        archive_path.unlink(missing_ok=True)
        secure_file(archive_path, archive, 0o600)
        self.request["operation_manifest_sha256"] = self.manifest_sha256


class ProductionShadowFinlandStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = StageFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.fixture.close()
        self.temporary.cleanup()

    def test_manifest_has_exact_schema_artifact_kinds_and_canonical_paths(self):
        manifest = MODULE.validate_manifest(self.fixture.document)
        self.assertEqual(
            manifest["schema"],
            "production-shadow-finland-image-stage-manifest-v1",
        )
        self.assertEqual(set(manifest), MODULE.MANIFEST_FIELDS)
        self.assertEqual(
            tuple(manifest["artifacts"]),
            MODULE.ARTIFACT_KINDS,
        )
        self.assertEqual(
            {
                kind: row["filename"]
                for kind, row in manifest["artifacts"].items()
            },
            MODULE.ARTIFACT_FILENAMES,
        )
        self.assertEqual(set(manifest["image_artifacts"]), set(MODULE.IMAGE_ROLES))
        for row in manifest["image_artifacts"].values():
            self.assertEqual(set(row), MODULE.IMAGE_ARTIFACT_FIELDS)
        self.assertNotIn("current", manifest["project_root"])
        self.assertNotIn("data", manifest["project_root"])

    def test_stage_loads_only_after_all_archives_verify_and_attests(self):
        result = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )
        self.assertEqual(result["status"], "staged")
        self.assertEqual(
            result["runtime_image_ids"],
            self.fixture.runner.runtime_ids,
        )
        for role in MODULE.IMAGE_ROLES:
            self.assertNotEqual(
                result["runtime_image_ids"][role],
                self.fixture.image_bindings[role]["config_digest"],
            )

        docker_calls = [
            call for call in self.fixture.runner.calls if call[0] == MODULE.DOCKER
        ]
        self.assertEqual(
            sum(call[1:3] == ["image", "load"] for call in docker_calls),
            4,
        )
        forbidden = {
            "build",
            "pull",
            "tag",
            "run",
            "create",
            "start",
            "stop",
            "compose",
            "service",
            "network",
            "volume",
            "rm",
        }
        self.assertFalse(
            any(token in forbidden for call in docker_calls for token in call)
        )
        attestation_path = Path(result["stage_attestation_path"])
        attestation = json.loads(attestation_path.read_bytes())
        self.assertEqual(set(attestation), MODULE.ATTESTATION_FIELDS)
        self.assertEqual(
            attestation["operation_manifest_sha256"],
            self.fixture.manifest_sha256,
        )
        self.assertEqual(attestation["runtime_image_ids"], result["runtime_image_ids"])
        self.assertEqual(
            hashlib.sha256(attestation_path.read_bytes()).hexdigest(),
            result["stage_attestation_sha256"],
        )
        self.assertEqual(stat.S_IMODE(attestation_path.stat().st_mode), 0o600)
        for field in (
            "images_built",
            "images_pulled",
            "containers_created",
            "containers_started",
            "services_started",
            "networks_created",
            "volumes_created",
            "current_mutated",
            "data_mutated",
        ):
            self.assertIs(attestation[field], False)

    def test_current_release_installs_launcher_before_release_verification(self):
        result = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )

        launcher = (
            self.fixture.paths["release_root"]
            / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        )
        metadata = launcher.stat(follow_symlinks=False)
        self.assertEqual(result["status"], "staged")
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertFalse(launcher.is_symlink())
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(metadata.st_gid, 0)
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        self.assertEqual(launcher.read_bytes(), b"#!/bin/sh\nexit 0\n")
        self.assertEqual(
            self.fixture.runner.release_verification_launcher_modes,
            [0o700, 0o700],
        )

    def test_generic_release_verification_does_not_require_stage_launcher_mode(self):
        release_root = self.fixture.paths["release_root"]
        release_root.mkdir(mode=0o700)
        (release_root / ".git").mkdir(mode=0o700)
        self.fixture.materialize_release_tree(release_root)
        self.fixture.runner.remote_removed = True
        launcher = (
            release_root / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        )

        MODULE._verify_materialized_release(
            release_root,
            bundle=self.fixture.paths["incoming_root"] / "release.bundle",
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            required_uid=0,
            runner=self.fixture.runner,
        )

        self.assertEqual(
            stat.S_IMODE(launcher.stat(follow_symlinks=False).st_mode),
            0o755,
        )

    def test_legacy_release_without_source_set_pair_remains_stageable(self):
        self.fixture.release_feature = "legacy"

        result = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )

        self.assertEqual(result["status"], "staged")
        self.assertEqual(
            self.fixture.runner.release_verification_launcher_modes,
            [None, None],
        )

    def test_incomplete_source_set_pair_is_rejected_before_release_verification(self):
        self.fixture.release_feature = "producer-only"

        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "incomplete convergence source-set feature",
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )

        self.assertEqual(
            self.fixture.runner.release_verification_launcher_modes,
            [],
        )
        self.assertFalse(
            any(call[0] == MODULE.DOCKER for call in self.fixture.runner.calls)
        )

    def test_symlinked_source_set_launcher_is_rejected_before_release_verification(self):
        self.fixture.release_feature = "launcher-symlink"

        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "convergence source-set launcher",
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )

        self.assertEqual(
            self.fixture.runner.release_verification_launcher_modes,
            [],
        )
        self.assertFalse(
            any(call[0] == MODULE.DOCKER for call in self.fixture.runner.calls)
        )

    def test_nonregular_source_set_launcher_is_rejected_before_opening_it(self):
        self.fixture.release_feature = "launcher-fifo"

        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "convergence source-set launcher is unsafe",
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )

        self.assertEqual(
            self.fixture.runner.release_verification_launcher_modes,
            [],
        )
        self.assertFalse(
            any(call[0] == MODULE.DOCKER for call in self.fixture.runner.calls)
        )

    def test_source_set_launcher_verification_opens_nonblocking(self):
        release_root = self.fixture.root / "nonblocking-launcher-release"
        release_root.mkdir(mode=0o700)
        self.fixture.materialize_release_tree(release_root)
        launcher = (
            release_root / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        )
        launcher.chmod(0o700)

        with mock.patch.object(MODULE.os, "open", wraps=os.open) as open_file:
            self.assertTrue(
                MODULE._verify_convergence_source_set_launcher(
                    release_root,
                    required_uid=0,
                )
            )

        opens = [
            call.args[1]
            for call in open_file.call_args_list
            if call.args and call.args[0] == launcher
        ]
        self.assertEqual(len(opens), 1)
        self.assertNotEqual(getattr(os, "O_NONBLOCK", 0), 0)
        self.assertTrue(opens[0] & os.O_NONBLOCK)

    def test_resume_normalizes_pre_hardening_launcher_before_image_rechecks(self):
        first = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )
        launcher = (
            self.fixture.paths["release_root"]
            / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        )
        launcher.chmod(0o755)
        image_loads_before = sum(
            call[0] == MODULE.DOCKER and call[1:3] == ["image", "load"]
            for call in self.fixture.runner.calls
        )

        resumed = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )

        self.assertEqual(first["status"], "staged")
        self.assertEqual(resumed["status"], "staged")
        self.assertEqual(
            stat.S_IMODE(launcher.stat(follow_symlinks=False).st_mode),
            0o700,
        )
        self.assertTrue(
            all(
                mode == 0o700
                for mode in self.fixture.runner.release_verification_launcher_modes
            )
        )
        self.assertEqual(
            sum(
                call[0] == MODULE.DOCKER and call[1:3] == ["image", "load"]
                for call in self.fixture.runner.calls
            ),
            image_loads_before,
        )

    def test_foreign_owned_source_set_launcher_is_rejected_before_mode_change(self):
        release_root = self.fixture.root / "foreign-owner-release"
        release_root.mkdir(mode=0o700)
        self.fixture.materialize_release_tree(release_root)
        launcher = (
            release_root / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        )
        metadata = launcher.stat(follow_symlinks=False)
        foreign_owner = os.stat_result(
            (
                metadata.st_mode,
                metadata.st_ino,
                metadata.st_dev,
                metadata.st_nlink,
                1,
                1,
                metadata.st_size,
                metadata.st_atime,
                metadata.st_mtime,
                metadata.st_ctime,
            )
        )

        with (
            mock.patch.object(MODULE.os, "fstat", return_value=foreign_owner),
            mock.patch.object(MODULE.os, "fchmod") as fchmod,
            self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "convergence source-set launcher is unsafe",
            ),
        ):
            MODULE._install_convergence_source_set_launcher(
                release_root,
                required_uid=0,
            )

        fchmod.assert_not_called()

    def test_release_child_metadata_normalizes_os_errors(self):
        unavailable = self.fixture.root / "unavailable"

        with mock.patch.object(Path, "stat", side_effect=OSError("denied")):
            with self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "unavailable is unavailable",
            ):
                MODULE._release_child_metadata(unavailable, label="unavailable")

    def test_source_set_pair_without_members_is_legacy_but_partial_or_unsafe_is_not(self):
        legacy_root = self.fixture.root / "empty-scripts-release"
        scripts = legacy_root / "scripts"
        scripts.mkdir(mode=0o755, parents=True)
        self.assertIsNone(
            MODULE._convergence_source_set_feature_paths(
                legacy_root,
                required_uid=0,
            )
        )

        unsafe_root = self.fixture.root / "unsafe-producer-release"
        unsafe_root.mkdir(mode=0o700)
        self.fixture.materialize_release_tree(unsafe_root)
        producer = unsafe_root / MODULE.CONVERGENCE_SOURCE_SET_PRODUCER_RELATIVE
        producer.unlink()
        os.mkfifo(producer, 0o600)
        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "producer is unsafe",
        ):
            MODULE._convergence_source_set_feature_paths(
                unsafe_root,
                required_uid=0,
            )

    def test_launcher_install_rejects_post_change_metadata_and_open_errors(self):
        release_root = self.fixture.root / "post-change-release"
        release_root.mkdir(mode=0o700)
        self.fixture.materialize_release_tree(release_root)
        launcher = release_root / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        before = launcher.stat(follow_symlinks=False)
        after = os.stat_result(
            (
                before.st_mode,
                before.st_ino,
                before.st_dev,
                before.st_nlink,
                before.st_uid,
                1,
                before.st_size,
                before.st_atime,
                before.st_mtime,
                before.st_ctime,
            )
        )
        with mock.patch.object(MODULE.os, "fstat", side_effect=[before, after]):
            with self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "launcher is unsafe",
            ):
                MODULE._install_convergence_source_set_launcher(
                    release_root,
                    required_uid=0,
                )

        with mock.patch.object(MODULE.os, "open", side_effect=OSError("denied")):
            with self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "mode could not be fixed",
            ):
                MODULE._install_convergence_source_set_launcher(
                    release_root,
                    required_uid=0,
                )

        self.assertTrue(
            MODULE._install_convergence_source_set_launcher(
                release_root,
                required_uid=0,
            )
        )

        simulated_after = os.stat_result(
            (
                stat.S_IFREG | 0o700,
                before.st_ino,
                before.st_dev,
                before.st_nlink,
                before.st_uid,
                before.st_gid,
                before.st_size,
                before.st_atime,
                before.st_mtime,
                before.st_ctime,
            )
        )
        # Exercise the cleanup guard's no-descriptor path without a real FD.
        with (
            mock.patch.object(MODULE.os, "open", return_value=-1),
            mock.patch.object(MODULE.os, "fstat", side_effect=[before, simulated_after]),
            mock.patch.object(MODULE.os, "fchmod"),
            mock.patch.object(MODULE.os, "fsync"),
            mock.patch.object(MODULE, "_fsync_directory"),
        ):
            self.assertTrue(
                MODULE._install_convergence_source_set_launcher(
                    release_root,
                    required_uid=0,
                )
            )

    def test_launcher_verification_rejects_changed_group(self):
        release_root = self.fixture.root / "wrong-group-release"
        release_root.mkdir(mode=0o700)
        self.fixture.materialize_release_tree(release_root)
        launcher = release_root / MODULE.CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
        metadata = launcher.stat(follow_symlinks=False)
        wrong_group = os.stat_result(
            (
                metadata.st_mode,
                metadata.st_ino,
                metadata.st_dev,
                metadata.st_nlink,
                metadata.st_uid,
                1,
                metadata.st_size,
                metadata.st_atime,
                metadata.st_mtime,
                metadata.st_ctime,
            )
        )

        @contextmanager
        def held_file(*_args, **_kwargs):
            with launcher.open("rb") as stream:
                yield stream, wrong_group

        with mock.patch.object(MODULE, "_held_file", held_file):
            with self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "launcher is unsafe",
            ):
                MODULE._verify_convergence_source_set_launcher(
                    release_root,
                    required_uid=0,
                )

    def test_all_archive_validation_precedes_first_docker_load(self):
        archive, binding, inspect = image_archive("nginx", attack="link")
        self.fixture.replace_archive("nginx", archive, binding, inspect)
        with self.assertRaisesRegex(MODULE.FinlandStageError, "link"):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )
        self.assertFalse(
            any(
                call[0] == MODULE.DOCKER and call[1:3] == ["image", "load"]
                for call in self.fixture.runner.calls
            )
        )

    def test_malicious_tar_traversal_duplicate_and_link_are_rejected(self):
        for attack in ("traversal", "duplicate", "link"):
            with self.subTest(attack=attack):
                archive, binding, _inspect = image_archive("app", attack=attack)
                path = self.fixture.root / f"{attack}.tar"
                secure_file(path, archive, 0o600)
                with self.assertRaises(MODULE.FinlandStageError):
                    MODULE.verify_image_archive(
                        path,
                        image_role="app",
                        release_sha=RELEASE_SHA,
                        expected=binding,
                    )

    def test_config_digest_and_descriptor_forgery_fail_closed(self):
        path = self.fixture.paths["incoming_root"] / "app-image.tar"
        expected = dict(self.fixture.image_bindings["app"])
        wrong_config = dict(expected)
        wrong_config["config_digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(MODULE.FinlandStageError, "descriptor differs"):
            MODULE.verify_image_archive(
                path,
                image_role="app",
                release_sha=RELEASE_SHA,
                expected=wrong_config,
            )

        forged = dict(expected)
        descriptor = dict(expected["content_descriptor"])
        descriptor["created"] = "2026-01-01T00:00:00Z"
        forged["content_descriptor"] = descriptor
        forged["content_identity"] = MODULE.verify_content_descriptor(descriptor)
        with self.assertRaisesRegex(MODULE.FinlandStageError, "descriptor differs"):
            MODULE.verify_image_archive(
                path,
                image_role="app",
                release_sha=RELEASE_SHA,
                expected=forged,
            )

    def test_baseexception_after_load_reconciles_without_reloading(self):
        crashed = False

        def checkpoint(name: str) -> None:
            nonlocal crashed
            if name == "after-load:app" and not crashed:
                crashed = True
                raise KeyboardInterrupt("simulated catchable interruption")

        with self.assertRaisesRegex(
            KeyboardInterrupt,
            "catchable interruption",
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
                checkpoint=checkpoint,
            )
        result = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )
        self.assertEqual(result["status"], "staged")
        app_loads = [
            call
            for call in self.fixture.runner.calls
            if call[0] == MODULE.DOCKER
            and call[1:3] == ["image", "load"]
            and call[-1].endswith("app-image.tar")
        ]
        self.assertEqual(len(app_loads), 1)

    def test_preexisting_semantic_match_is_rejected(self):
        image_id = "sha256:" + "e" * 64
        image = dict(self.fixture.inspect_documents["app"])
        image["Id"] = image_id
        self.fixture.runner = FakeRunner(
            self.fixture,
            preexisting={image_id: image},
        )
        with self.assertRaisesRegex(MODULE.FinlandStageError, "already existed"):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )
        self.assertFalse(
            any(
                call[0] == MODULE.DOCKER
                and call[1:3] == ["image", "load"]
                for call in self.fixture.runner.calls
            )
        )

    def test_transfer_partials_are_published_create_only(self):
        self.fixture.close()
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = StageFixture(
            Path(self.temporary.name),
            partial_inputs=True,
        )
        result = MODULE.stage_operation(
            self.fixture.request,
            runner=self.fixture.runner,
        )
        self.assertEqual(result["status"], "staged")
        incoming = self.fixture.paths["incoming_root"]
        self.assertTrue((incoming / MODULE.MANIFEST_FILENAME).is_file())
        self.assertFalse(
            MODULE.transfer_partial_path(
                incoming / MODULE.MANIFEST_FILENAME
            ).exists()
        )
        for filename in MODULE.ARTIFACT_FILENAMES.values():
            self.assertTrue((incoming / filename).is_file())
            self.assertFalse(
                MODULE.transfer_partial_path(incoming / filename).exists()
            )

    def test_existing_different_secret_manifest_is_never_overwritten(self):
        secret_manifest = self.fixture.paths["secret_manifest"]
        secure_file(secret_manifest, b"different-manifest", 0o600)
        with self.assertRaisesRegex(MODULE.FinlandStageError, "destination differs"):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )
        self.assertEqual(secret_manifest.read_bytes(), b"different-manifest")
        self.assertFalse(
            any(
                call[0] == MODULE.DOCKER
                and call[1:3] == ["image", "load"]
                for call in self.fixture.runner.calls
            )
        )

    def test_safe_hardlink_publication_windows_reconcile(self):
        payload = b"canonical-create-only-payload"
        destination = self.fixture.root / "published.json"
        temporary = destination.with_name(f".{destination.name}.materializing")
        secure_file(temporary, payload, 0o600)
        os.link(temporary, destination)
        self.assertEqual(destination.stat().st_nlink, 2)
        digest = MODULE._write_create_only(
            destination,
            payload,
            required_uid=0,
            mode=0o600,
            maximum=1024,
        )
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertFalse(temporary.exists())
        self.assertEqual(destination.stat().st_nlink, 1)

        incoming = self.fixture.root / "incoming-file"
        partial = MODULE.transfer_partial_path(incoming)
        secure_file(partial, payload, 0o600)
        os.link(partial, incoming)
        MODULE._publish_transfer_partial(
            incoming,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_bytes=len(payload),
            required_uid=0,
            mode=0o600,
        )
        self.assertFalse(partial.exists())
        self.assertEqual(incoming.read_bytes(), payload)
        self.assertEqual(incoming.stat().st_nlink, 1)

    def test_bootstrap_resume_reconciles_hardlink_publication_window(self):
        destination = self.fixture.paths["agent"]
        destination.unlink()
        partial = MODULE.transfer_partial_path(destination)
        secure_file(partial, self.fixture.agent_bytes, 0o700)
        os.link(partial, destination)
        request = {
            "schema": MODULE.BOOTSTRAP_REQUEST_SCHEMA,
            "action": "install-bootstrap",
            "operation_id": OPERATION_ID,
            "role": "bot_fi",
            "agent_sha256": self.fixture.agent_sha256,
        }

        result = MODULE.install_bootstrap(
            request,
            executing_path=partial,
            observed_host_addresses={MODULE.ROLE_HOSTS["bot_fi"]},
        )

        self.assertEqual(result["agent_sha256"], self.fixture.agent_sha256)
        self.assertFalse(partial.exists())
        self.assertEqual(destination.stat().st_nlink, 1)

    def test_unexpected_incoming_file_is_rejected_before_docker(self):
        secure_file(
            self.fixture.paths["incoming_root"] / "unexpected",
            b"unexpected",
            0o600,
        )
        with self.assertRaisesRegex(MODULE.FinlandStageError, "inventory"):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )
        self.assertFalse(
            any(
                call[0] == MODULE.DOCKER
                for call in self.fixture.runner.calls
            )
        )

    def test_role_host_mismatch_is_rejected_before_filesystem_or_commands(self):
        with mock.patch.object(
            MODULE,
            "observe_local_ipv4_addresses",
            return_value={MODULE.ROLE_HOSTS["webapp_fi"]},
        ):
            with self.assertRaisesRegex(MODULE.FinlandStageError, "host identity"):
                MODULE.stage_operation(
                    self.fixture.request,
                    runner=self.fixture.runner,
                )
        self.assertEqual(self.fixture.runner.calls, [])

    def test_tampered_journal_is_rejected_on_resume(self):
        crashed = False

        def checkpoint(name: str) -> None:
            nonlocal crashed
            if name == "after-phase:inputs-verified" and not crashed:
                crashed = True
                raise RuntimeError("pause")

        with self.assertRaises(RuntimeError):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
                checkpoint=checkpoint,
            )
        journal = self.fixture.paths["journal"]
        document = json.loads(journal.read_bytes())
        document["release_sha"] = "f" * 40
        secure_file(journal, canonical(document), 0o600)
        with self.assertRaisesRegex(MODULE.FinlandStageError, "state hash"):
            MODULE.stage_operation(
                self.fixture.request,
                runner=self.fixture.runner,
            )

    def test_nonroot_is_rejected_before_stage_commands(self):
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(MODULE.FinlandStageError, "root"):
                MODULE.stage_operation(
                    self.fixture.request,
                    runner=self.fixture.runner,
                )
        self.assertEqual(self.fixture.runner.calls, [])

    def test_cli_nonroot_gate_precedes_request_decoding(self):
        stderr = io.StringIO()
        with (
            mock.patch.object(MODULE.os, "geteuid", return_value=1000),
            mock.patch.object(
                MODULE,
                "_decode_request",
                side_effect=AssertionError("request was decoded"),
            ) as decode,
            mock.patch("sys.stderr", stderr),
        ):
            status = MODULE.main(["--request-b64", "not-a-request"])

        self.assertEqual(status, 1)
        decode.assert_not_called()
        self.assertIn("must run as root", stderr.getvalue())

    def test_request_base64_is_canonical_and_injection_is_rejected(self):
        encoded = MODULE.encode_request(self.fixture.request)
        self.assertEqual(
            MODULE._decode_request(encoded, bootstrap=False),
            self.fixture.request,
        )
        malicious = dict(self.fixture.request)
        malicious["operation_id"] = (
            OPERATION_ID + ";touch-/tmp/injected"
        )
        with self.assertRaisesRegex(MODULE.FinlandStageError, "UUIDv4"):
            MODULE._decode_request(
                MODULE.encode_request(malicious),
                bootstrap=False,
            )

    def test_signal_cancellation_is_catchable_reentrant_and_one_shot(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signum=signum):
                previous = signal.getsignal(signum)
                guard = MODULE.ControllerLivenessGuard(None)
                with guard:
                    with self.assertRaisesRegex(
                        MODULE.FinlandStageCancellation,
                        f"received signal {signum}",
                    ):
                        guard._handle_signal(  # noqa: SLF001
                            signum,
                            None,
                        )
                    guard._handle_signal(signum, None)  # noqa: SLF001
                    guard.check()
                self.assertIs(signal.getsignal(signum), previous)

    def test_liveness_rejects_a_writer_held_by_the_stage_process(self):
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.FinlandStageError,
                "writer end is held",
            ):
                MODULE.ControllerLivenessGuard(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_controller_eof_cancels_runner_and_reaps_double_fork(self):
        descendant_pid = self.fixture.root / "stage-descendant-pid"
        sentinel = self.fixture.root / "stage-descendant-survived"
        program = (
            "import os,signal,time\n"
            "if os.fork() == 0:\n"
            " os.setsid()\n"
            " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
            " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
            " time.sleep(0.7)\n"
            f" open({str(sentinel)!r},'wb').write(b'survived')\n"
            " os._exit(0)\n"
            f"while not os.path.exists({str(descendant_pid)!r}):"
            " time.sleep(0.005)\n"
            "time.sleep(60)\n"
        )
        control_read, holder, stop_write = external_liveness_pipe()

        def disconnect_when_ready() -> None:
            deadline = time.monotonic() + 2
            while (
                not descendant_pid.exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            stop_liveness_holder(holder, stop_write)

        closer = threading.Thread(
            target=disconnect_when_ready,
            daemon=True,
        )
        closer.start()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_GROUP_TERM_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaisesRegex(
                    MODULE.FinlandStageCancellation,
                    "liveness pipe reached EOF",
                ),
            ):
                with MODULE._execution_authority(control_read):
                    MODULE._default_runner(
                        [sys.executable, "-I", "-B", "-c", program],
                        input=None,
                        capture_output=True,
                        check=False,
                        timeout=5,
                        env={"PATH": "/usr/bin:/bin"},
                    )
            closer.join(timeout=2)
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())
            self.assertTrue(descendant_pid.is_file())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )
        finally:
            try:
                os.close(control_read)
            except OSError:
                pass
            if closer.is_alive():
                stop_liveness_holder(holder, stop_write)
                closer.join(timeout=2)

    def test_default_runner_bounds_each_stream_and_timeout(self):
        for descriptor, label in ((1, "stdout"), (2, "stderr")):
            with (
                self.subTest(stream=label),
                mock.patch.object(
                    MODULE,
                    "MAX_COMMAND_OUTPUT_BYTES",
                    1024,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_GROUP_TERM_SECONDS",
                    0.1,
                ),
                self.assertRaisesRegex(
                    MODULE.BoundedStageRunnerError,
                    f"{label} is oversized",
                ),
            ):
                MODULE._default_runner(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        f"import os,time;os.write({descriptor},b'x'*4096);"
                        "time.sleep(60)",
                    ],
                    input=None,
                    capture_output=True,
                    check=False,
                    timeout=5,
                    env={"PATH": "/usr/bin:/bin"},
                )
        with (
            mock.patch.object(
                MODULE,
                "PROCESS_GROUP_TERM_SECONDS",
                0.1,
            ),
            self.assertRaisesRegex(
                MODULE.BoundedStageRunnerError,
                "timed out",
            ),
        ):
            MODULE._default_runner(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                input=None,
                capture_output=True,
                check=False,
                timeout=0.1,
                env={"PATH": "/usr/bin:/bin"},
            )

    def test_cancelled_docker_load_reconciles_late_daemon_image(self):
        fixture = self.fixture

        class DelayedLoadRunner(FakeRunner):
            def __init__(self) -> None:
                super().__init__(fixture)
                self.pending_role: str | None = None
                self.polls = 0
                self.cancelled = False

            def __call__(self, arguments, **kwargs):  # noqa: ANN001
                argv = [str(value) for value in arguments]
                if (
                    argv[0] == MODULE.DOCKER
                    and argv[1:3] == ["image", "load"]
                    and not self.cancelled
                ):
                    self.calls.append(argv)
                    archive = Path(argv[argv.index("--input") + 1])
                    self.pending_role = archive.name.removesuffix(
                        "-image.tar"
                    )
                    self.cancelled = True
                    raise MODULE.FinlandStageCancellation(
                        "simulated controller cancellation"
                    )
                if (
                    self.pending_role is not None
                    and argv[0] == MODULE.DOCKER
                    and argv[1:3] == ["image", "ls"]
                ):
                    self.polls += 1
                    if self.polls == 3:
                        role = self.pending_role
                        image_id = self.runtime_ids[role]
                        image = dict(fixture.inspect_documents[role])
                        image["Id"] = image_id
                        self.loaded[image_id] = image
                        self.pending_role = None
                return super().__call__(arguments, **kwargs)

        runner = DelayedLoadRunner()
        with (
            mock.patch.object(
                MODULE,
                "DOCKER_LOAD_RECONCILE_SECONDS",
                0.5,
            ),
            mock.patch.object(
                MODULE,
                "DOCKER_LOAD_RECONCILE_INTERVAL_SECONDS",
                0.01,
            ),
            self.assertRaisesRegex(
                MODULE.FinlandStageCancellation,
                "simulated controller cancellation",
            ),
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=runner,
            )
        evidence_path = MODULE.image_load_reconciliation_path(
            self.fixture.paths,
            "app",
        )
        evidence_payload = evidence_path.read_bytes()
        evidence = json.loads(evidence_payload)
        self.assertEqual(
            set(evidence),
            MODULE.IMAGE_LOAD_RECONCILIATION_FIELDS,
        )
        self.assertEqual(
            evidence["runtime_image_id"],
            runner.runtime_ids["app"],
        )
        self.assertEqual(stat.S_IMODE(evidence_path.stat().st_mode), 0o600)
        journal = json.loads(self.fixture.paths["journal"].read_bytes())
        self.assertEqual(
            journal["runtime_image_ids"]["app"],
            runner.runtime_ids["app"],
        )
        secure_file(evidence_path, b"{}", 0o600)
        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "create-only destination differs",
        ):
            MODULE.stage_operation(
                self.fixture.request,
                runner=runner,
            )
        secure_file(evidence_path, evidence_payload, 0o600)

        result = MODULE.stage_operation(
            self.fixture.request,
            runner=runner,
        )
        self.assertEqual(result["status"], "staged")
        app_loads = [
            call
            for call in runner.calls
            if call[0] == MODULE.DOCKER
            and call[1:3] == ["image", "load"]
            and call[-1].endswith("app-image.tar")
        ]
        self.assertEqual(len(app_loads), 1)
        forbidden = {
            "build",
            "pull",
            "tag",
            "run",
            "create",
            "start",
            "stop",
            "compose",
            "service",
            "network",
            "volume",
            "rm",
            "rmi",
        }
        self.assertFalse(
            any(token in forbidden for call in runner.calls for token in call)
        )

    def test_late_daemon_reconciliation_window_is_bounded(self):
        calls = 0

        def no_match(*_args, **_kwargs):  # noqa: ANN002, ANN003
            nonlocal calls
            calls += 1
            return []

        started = time.monotonic()
        with (
            mock.patch.object(
                MODULE,
                "_runtime_semantic_matches",
                side_effect=no_match,
            ),
            mock.patch.object(
                MODULE,
                "DOCKER_LOAD_RECONCILE_SECONDS",
                0.03,
            ),
            mock.patch.object(
                MODULE,
                "DOCKER_LOAD_RECONCILE_INTERVAL_SECONDS",
                0.005,
            ),
        ):
            matches = MODULE._poll_late_runtime_image(
                self.fixture.image_bindings["app"],
                runner=self.fixture.runner,
            )
        self.assertEqual(matches, [])
        self.assertGreaterEqual(calls, 2)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_git_and_docker_argv_environments_are_exact(self):
        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "Git command argv",
        ):
            MODULE._run(
                [MODULE.GIT, "status"],
                timeout=1,
                env=MODULE.SAFE_GIT_ENV,
                runner=self.fixture.runner,
            )
        with self.assertRaisesRegex(
            MODULE.FinlandStageError,
            "Docker command environment",
        ):
            MODULE._run(
                [
                    MODULE.DOCKER,
                    "image",
                    "ls",
                    "--all",
                    "--no-trunc",
                    "--quiet",
                ],
                timeout=1,
                env={"PATH": "/usr/bin:/bin"},
                runner=self.fixture.runner,
            )


if __name__ == "__main__":
    unittest.main()
