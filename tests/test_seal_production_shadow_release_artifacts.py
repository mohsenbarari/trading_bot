from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from scripts import seal_production_shadow_release_artifacts as MODULE


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"


def _run_git(*arguments: str) -> str:
    result = subprocess.run(
        [MODULE.GIT, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=MODULE.SAFE_GIT_ENV,
        text=True,
    )
    return result.stdout.strip()


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _image_fixture(
    role: str,
    release_sha: str,
    *,
    tags: list[str] | None = None,
    config_role: str | None = None,
    postgres_runtime_labels: bool = True,
) -> tuple[str, bytes, dict]:
    labels: dict[str, str] = {}
    if role in MODULE.RELEASE_BOUND_IMAGE_ROLES:
        labels["org.opencontainers.image.revision"] = release_sha
    if role == "postgres" and postgres_runtime_labels:
        labels[MODULE.POSTGRES_RUNTIME_UID_LABEL] = str(
            MODULE.EXPECTED_POSTGRES_RUNTIME_UID
        )
        labels[MODULE.POSTGRES_RUNTIME_GID_LABEL] = str(
            MODULE.EXPECTED_POSTGRES_RUNTIME_GID
        )
    layer = f"layer:{config_role or role}".encode("ascii")
    diff_id = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = {
        "architecture": "amd64",
        "os": "linux",
        "created": "2026-07-27T00:00:00Z",
        "rootfs": {"type": "layers", "diff_ids": [diff_id]},
        "config": {
            "Labels": labels,
            "fixture_role": config_role or role,
        },
    }
    config_payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    image_id = "sha256:" + hashlib.sha256(config_payload).hexdigest()
    config_name = f"{image_id.removeprefix('sha256:')}.json"
    manifest = json.dumps(
        [
            {
                "Config": config_name,
                "RepoTags": [] if tags is None else tags,
                "Layers": ["layer/layer.tar"],
            }
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    archive = _tar_bytes(
        {
            "manifest.json": manifest,
            config_name: config_payload,
            "layer/layer.tar": layer,
        }
    )
    inspect = {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "Created": config["created"],
        "Config": config["config"],
        "RootFS": {
            "Type": "layers",
            "Layers": [diff_id],
        },
        "RepoTags": [],
        "RepoDigests": [],
    }
    return image_id, archive, inspect


class FakeLocalCommands:
    def __init__(
        self,
        images: dict[str, tuple[bytes, dict]],
        real_execute,
    ) -> None:
        self.images = images
        self.real_execute = real_execute
        self.calls: list[list[str]] = []

    def __call__(
        self,
        arguments: list[str],
        *,
        timeout: int,
        env,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(arguments))
        if arguments[0] == MODULE.GIT:
            return self.real_execute(arguments, timeout=timeout, env=env)
        if arguments[:3] == [MODULE.DOCKER, "image", "inspect"]:
            image_id = arguments[3]
            if image_id not in self.images:
                return subprocess.CompletedProcess(arguments, 1, b"", b"not found")
            inspect = self.images[image_id][1]
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps([inspect], sort_keys=True).encode("utf-8"),
                b"",
            )
        if arguments[:4] == [
            MODULE.DOCKER,
            "image",
            "save",
            "--output",
        ]:
            destination = Path(arguments[4])
            image_id = arguments[5]
            if image_id not in self.images:
                return subprocess.CompletedProcess(arguments, 1, b"", b"not found")
            destination.write_bytes(self.images[image_id][0])
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        return subprocess.CompletedProcess(arguments, 97, b"", b"forbidden command")


class SealFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.release_root = root / "release"
        self.release_root.mkdir(mode=0o700)
        _run_git("init", "--quiet", str(self.release_root))
        _run_git("-C", str(self.release_root), "config", "user.name", "Test")
        _run_git(
            "-C",
            str(self.release_root),
            "config",
            "user.email",
            "test@example.invalid",
        )
        (self.release_root / "README").write_text(
            "exact production shadow release\n",
            encoding="ascii",
        )
        _run_git("-C", str(self.release_root), "add", "README")
        _run_git(
            "-C",
            str(self.release_root),
            "commit",
            "--quiet",
            "-m",
            "fixture",
        )
        self.release_sha = _run_git(
            "-C",
            str(self.release_root),
            "rev-parse",
            "HEAD",
        )
        self.release_tree_sha = _run_git(
            "-C",
            str(self.release_root),
            "rev-parse",
            "HEAD^{tree}",
        )
        _run_git(
            "-C",
            str(self.release_root),
            "checkout",
            "--quiet",
            "--detach",
            self.release_sha,
        )
        self.image_ids: dict[str, str] = {}
        self.images: dict[str, tuple[bytes, dict]] = {}
        for role in MODULE.IMAGE_ROLES:
            image_id, archive, inspect = _image_fixture(
                role,
                self.release_sha,
            )
            self.image_ids[role] = image_id
            self.images[image_id] = (archive, inspect)

    def request(
        self,
        *,
        operation_id: str = OPERATION_ID,
        release_root: Path | None = None,
        release_sha: str | None = None,
        release_tree_sha: str | None = None,
        image_ids: dict[str, str] | None = None,
        checkpoint=None,
    ) -> dict:
        return MODULE.seal_release_artifacts(
            operation_id=operation_id,
            release_root=self.release_root if release_root is None else release_root,
            release_sha=self.release_sha if release_sha is None else release_sha,
            release_tree_sha=(
                self.release_tree_sha
                if release_tree_sha is None
                else release_tree_sha
            ),
            image_ids=self.image_ids if image_ids is None else image_ids,
            checkpoint=checkpoint,
        )


class ReleaseArtifactProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = SealFixture(self.root)
        self.artifact_root = self.root / "sealed"
        self.artifact_root_patch = mock.patch.object(
            MODULE,
            "ARTIFACT_ROOT",
            self.artifact_root,
        )
        self.artifact_root_patch.start()
        self.real_execute = MODULE._execute_command
        self.fake = FakeLocalCommands(self.fixture.images, self.real_execute)
        self.execute_patch = mock.patch.object(
            MODULE,
            "_execute_command",
            side_effect=self.fake,
        )
        self.execute_patch.start()

    def tearDown(self) -> None:
        self.execute_patch.stop()
        self.artifact_root_patch.stop()
        self.temporary.cleanup()

    def _operation_root(self, operation_id: str = OPERATION_ID) -> Path:
        return self.artifact_root / operation_id

    def test_seals_exact_closure_and_idempotently_reuses_every_artifact(self):
        first = self.fixture.request()
        first_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self._operation_root() / "artifacts").iterdir()
        }
        second = self.fixture.request()
        second_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self._operation_root() / "artifacts").iterdir()
        }

        self.assertEqual(first, second)
        self.assertEqual(first_hashes, second_hashes)
        self.assertEqual(first["completed_phases"], list(MODULE.PHASES))
        docker_calls = [
            call for call in self.fake.calls if call[0] == MODULE.DOCKER
        ]
        save_calls = [call for call in docker_calls if call[2] == "save"]
        inspect_calls = [call for call in docker_calls if call[2] == "inspect"]
        self.assertEqual(len(save_calls), 4)
        self.assertEqual(len(inspect_calls), 4)
        self.assertTrue(
            all(
                call[:4]
                in (
                    [MODULE.DOCKER, "image", "inspect", call[3]],
                    [MODULE.DOCKER, "image", "save", "--output"],
                )
                for call in docker_calls
            )
        )
        self.assertFalse(
            any(
                forbidden in call
                for call in docker_calls
                for forbidden in (
                    "build",
                    "pull",
                    "load",
                    "tag",
                    "run",
                    "create",
                    "start",
                    "stop",
                    "compose",
                )
            )
        )
        bundle_create_calls = [
            call
            for call in self.fake.calls
            if call[0] == MODULE.GIT
            and "bundle" in call
            and "create" in call
        ]
        self.assertEqual(len(bundle_create_calls), 1)
        artifacts = self._operation_root() / "artifacts"
        self.assertEqual(
            {path.name for path in artifacts.iterdir()},
            {
                "release.bundle",
                "app-image.tar",
                "postgres-image.tar",
                "redis-image.tar",
                "nginx-image.tar",
                "closure-manifest.json",
            },
        )
        for path in artifacts.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        closure = json.loads((artifacts / "closure-manifest.json").read_bytes())
        self.assertNotIn(str(self.fixture.release_root), json.dumps(closure))
        self.assertEqual(closure["release"]["commit_sha"], self.fixture.release_sha)
        self.assertEqual(set(closure["images"]), set(MODULE.IMAGE_ROLES))
        for role in MODULE.IMAGE_ROLES:
            image_id = self.fixture.image_ids[role]
            archive, inspected = self.fixture.images[image_id]
            descriptor, content_identity = MODULE.image_content_descriptor(
                inspected
            )
            self.assertEqual(
                closure["images"][role],
                {
                    "archive_sha256": hashlib.sha256(archive).hexdigest(),
                    "archive_bytes": len(archive),
                    "config_digest": image_id,
                    "content_descriptor": descriptor,
                    "content_identity": content_identity,
                },
            )
            self.assertEqual(
                closure["source_engine_observations"][role],
                {
                    "image_id": image_id,
                    "informational_only": True,
                },
            )
            self.assertEqual(
                closure["verified_image_contracts"][role]["repo_tags"],
                [],
            )
        self.assertEqual(
            closure["verified_image_contracts"]["postgres"]["runtime_user"],
            {
                "uid": MODULE.EXPECTED_POSTGRES_RUNTIME_UID,
                "gid": MODULE.EXPECTED_POSTGRES_RUNTIME_GID,
                "uid_label": MODULE.POSTGRES_RUNTIME_UID_LABEL,
                "gid_label": MODULE.POSTGRES_RUNTIME_GID_LABEL,
            },
        )
        self.assertEqual(
            closure["verified_image_contracts"]["app"]["oci_revision"],
            self.fixture.release_sha,
        )
        self.assertEqual(
            closure["verified_image_contracts"]["postgres"]["oci_revision"],
            self.fixture.release_sha,
        )
        self.assertIsNone(
            closure["verified_image_contracts"]["redis"]["oci_revision"]
        )
        self.assertIsNone(
            closure["verified_image_contracts"]["nginx"]["oci_revision"]
        )
        self.assertEqual(closure["constraints"]["network_transfer_performed"], False)
        self.assertEqual(closure["constraints"]["container_runtime_changed"], False)

    def test_crash_after_publication_resumes_without_resaving_published_image(self):
        crashed = False

        def checkpoint(name: str) -> None:
            nonlocal crashed
            if name == "after-publish:seal-app-image" and not crashed:
                crashed = True
                raise RuntimeError("simulated process interruption")

        with self.assertRaisesRegex(RuntimeError, "simulated"):
            self.fixture.request(checkpoint=checkpoint)
        app_path = self._operation_root() / "artifacts" / "app-image.tar"
        self.assertTrue(app_path.is_file())
        journal = json.loads(
            (self._operation_root() / "operation-journal.json").read_bytes()
        )
        self.assertEqual(journal["current_phase"], "seal-app-image")
        self.assertNotIn("seal-app-image", journal["completed_phases"])

        result = self.fixture.request()

        self.assertEqual(result["status"], "sealed")
        app_save_calls = [
            call
            for call in self.fake.calls
            if call[:3] == [MODULE.DOCKER, "image", "save"]
            and call[-1] == self.fixture.image_ids["app"]
        ]
        self.assertEqual(len(app_save_calls), 1)

    def test_safe_partial_archive_temporary_is_removed_and_regenerated(self):
        operation_root = self._operation_root()
        artifacts_root = operation_root / "artifacts"
        artifacts_root.mkdir(parents=True, mode=0o700)
        self.artifact_root.chmod(0o700)
        operation_root.chmod(0o700)
        artifacts_root.chmod(0o700)
        temporary = artifacts_root / ".app-image.tar.materializing"
        temporary.write_bytes(b"partial")
        temporary.chmod(0o600)

        result = self.fixture.request()

        self.assertEqual(result["status"], "sealed")
        self.assertFalse(temporary.exists())
        self.assertTrue((artifacts_root / "app-image.tar").is_file())

    def test_tagged_archive_is_rejected_without_publication(self):
        app_id = self.fixture.image_ids["app"]
        tagged_id, tagged_archive, tagged_inspect = _image_fixture(
            "app",
            self.fixture.release_sha,
            tags=["forbidden:test"],
        )
        self.assertEqual(tagged_id, app_id)
        self.fake.images[app_id] = (tagged_archive, tagged_inspect)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "tagless",
        ):
            self.fixture.request()

        self.assertFalse(
            (self._operation_root() / "artifacts" / "app-image.tar").exists()
        )

    def test_preexisting_different_archive_is_never_overwritten(self):
        operation_root = self._operation_root()
        artifacts_root = operation_root / "artifacts"
        artifacts_root.mkdir(parents=True, mode=0o700)
        self.artifact_root.chmod(0o700)
        operation_root.chmod(0o700)
        artifacts_root.chmod(0o700)
        destination = artifacts_root / "app-image.tar"
        original = b"existing-create-only-destination"
        destination.write_bytes(original)
        destination.chmod(0o600)

        with self.assertRaises(MODULE.ReleaseArtifactError):
            self.fixture.request()

        self.assertEqual(destination.read_bytes(), original)

    def test_archive_with_wrong_config_image_id_is_rejected(self):
        app_id = self.fixture.image_ids["app"]
        _wrong_id, wrong_archive, _wrong_inspect = _image_fixture(
            "app",
            self.fixture.release_sha,
            config_role="different-app-config",
        )
        expected_inspect = self.fixture.images[app_id][1]
        self.fake.images[app_id] = (wrong_archive, expected_inspect)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "semantic",
        ):
            self.fixture.request()

        self.assertFalse(
            (self._operation_root() / "artifacts" / "app-image.tar").exists()
        )

    def test_containerd_style_engine_id_is_only_an_informational_observation(self):
        config_digest_id = self.fixture.image_ids["app"]
        archive, inspect = self.fake.images.pop(config_digest_id)
        engine_image_id = "sha256:" + "e" * 64
        inspect = dict(inspect)
        inspect["Id"] = engine_image_id
        self.fake.images[engine_image_id] = (archive, inspect)
        image_ids = dict(self.fixture.image_ids)
        image_ids["app"] = engine_image_id

        result = self.fixture.request(image_ids=image_ids)

        closure = json.loads(Path(result["closure_manifest"]).read_bytes())
        self.assertEqual(
            closure["source_engine_observations"]["app"]["image_id"],
            engine_image_id,
        )
        self.assertTrue(
            closure["source_engine_observations"]["app"][
                "informational_only"
            ]
        )
        self.assertNotIn("image_id", closure["images"]["app"])
        self.assertEqual(
            closure["images"]["app"]["config_digest"],
            config_digest_id,
        )
        self.assertRegex(
            closure["images"]["app"]["content_identity"],
            r"^sha256:[0-9a-f]{64}$",
        )
        app_save = next(
            call
            for call in self.fake.calls
            if call[:3] == [MODULE.DOCKER, "image", "save"]
            and call[-1] == engine_image_id
        )
        self.assertNotIn(config_digest_id, app_save)

    def test_postgres_without_runtime_uid_gid_labels_fails_closed(self):
        postgres_id = self.fixture.image_ids["postgres"]
        unlabeled_id, archive, inspect = _image_fixture(
            "postgres",
            self.fixture.release_sha,
            postgres_runtime_labels=False,
        )
        image_ids = dict(self.fixture.image_ids)
        image_ids["postgres"] = unlabeled_id
        self.fake.images[unlabeled_id] = (archive, inspect)
        if postgres_id != unlabeled_id:
            self.fake.images.pop(postgres_id)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "runtime UID/GID",
        ):
            self.fixture.request(image_ids=image_ids)

        self.assertFalse(
            (self._operation_root() / "artifacts" / "postgres-image.tar").exists()
        )

    def test_postgres_archive_without_runtime_uid_gid_labels_fails_closed(self):
        postgres_id = self.fixture.image_ids["postgres"]
        _unlabeled_id, archive, _unlabeled_inspect = _image_fixture(
            "postgres",
            self.fixture.release_sha,
            postgres_runtime_labels=False,
        )
        expected_inspect = self.fixture.images[postgres_id][1]
        self.fake.images[postgres_id] = (archive, expected_inspect)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "runtime UID/GID",
        ):
            self.fixture.request()

        self.assertFalse(
            (self._operation_root() / "artifacts" / "postgres-image.tar").exists()
        )

    def test_postgres_archive_with_wrong_release_label_fails_closed(self):
        postgres_id = self.fixture.image_ids["postgres"]
        _wrong_id, archive, _wrong_inspect = _image_fixture(
            "postgres",
            "0" * 40,
        )
        expected_inspect = self.fixture.images[postgres_id][1]
        self.fake.images[postgres_id] = (archive, expected_inspect)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "exact OCI release revision",
        ):
            self.fixture.request()

        self.assertFalse(
            (self._operation_root() / "artifacts" / "postgres-image.tar").exists()
        )

    def test_postgres_inspect_with_wrong_release_label_fails_closed(self):
        original_id = self.fixture.image_ids["postgres"]
        wrong_id, archive, inspect = _image_fixture(
            "postgres",
            "0" * 40,
        )
        image_ids = dict(self.fixture.image_ids)
        image_ids["postgres"] = wrong_id
        self.fake.images[wrong_id] = (archive, inspect)
        if original_id != wrong_id:
            self.fake.images.pop(original_id)

        with self.assertRaisesRegex(
            MODULE.ReleaseArtifactError,
            "exact OCI release revision",
        ):
            self.fixture.request(image_ids=image_ids)

        self.assertFalse(
            (self._operation_root() / "artifacts" / "postgres-image.tar").exists()
        )

    def test_dirty_symbolic_and_remote_release_sources_are_rejected(self):
        cases: list[tuple[str, Path, str]] = []

        symbolic = self.root / "symbolic"
        symbolic.mkdir()
        _run_git("clone", "--quiet", str(self.fixture.release_root), str(symbolic))
        _run_git("-C", str(symbolic), "remote", "remove", "origin")
        cases.append(("symbolic", symbolic, "12345678-1234-4234-8234-123456789ab1"))

        dirty = self.root / "dirty"
        _run_git("clone", "--quiet", str(self.fixture.release_root), str(dirty))
        _run_git("-C", str(dirty), "remote", "remove", "origin")
        _run_git("-C", str(dirty), "checkout", "--quiet", "--detach")
        (dirty / "DIRTY").write_text("dirty\n", encoding="ascii")
        cases.append(("dirty", dirty, "12345678-1234-4234-8234-123456789ab2"))

        remote = self.root / "remote"
        _run_git("clone", "--quiet", str(self.fixture.release_root), str(remote))
        _run_git("-C", str(remote), "checkout", "--quiet", "--detach")
        cases.append(("remote", remote, "12345678-1234-4234-8234-123456789ab3"))

        for label, release_root, operation_id in cases:
            with self.subTest(label=label):
                release_sha = _run_git(
                    "-C",
                    str(release_root),
                    "rev-parse",
                    "HEAD",
                )
                release_tree_sha = _run_git(
                    "-C",
                    str(release_root),
                    "rev-parse",
                    "HEAD^{tree}",
                )
                with self.assertRaisesRegex(
                    MODULE.ReleaseArtifactError,
                    "detached, clean, and remote-free",
                ):
                    self.fixture.request(
                        operation_id=operation_id,
                        release_root=release_root.resolve(),
                        release_sha=release_sha,
                        release_tree_sha=release_tree_sha,
                    )
                self.assertFalse((self.artifact_root / operation_id).exists())

    def test_root_only_gate_precedes_any_artifact_mutation(self):
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                MODULE.ReleaseArtifactError,
                "must run as root",
            ):
                self.fixture.request()
        self.assertFalse(self.artifact_root.exists())

    def test_plan_is_nonmutating_and_binds_exact_confirmation(self):
        plan = MODULE._plan(
            operation_id=OPERATION_ID,
            release_root=self.fixture.release_root,
            release_sha=self.fixture.release_sha,
            release_tree_sha=self.fixture.release_tree_sha,
            image_ids=self.fixture.image_ids,
        )

        self.assertEqual(plan["status"], "planned")
        self.assertEqual(
            plan["required_confirmation"],
            MODULE._confirmation(OPERATION_ID, self.fixture.release_sha),
        )
        self.assertEqual(UUID(plan["operation_id"]), UUID(OPERATION_ID))
        self.assertFalse(self.artifact_root.exists())
        self.assertIn("network", plan["forbidden"])
        self.assertIn("pull", plan["forbidden"])
        self.assertIn("push", plan["forbidden"])

    def test_default_executor_delegates_to_identity_bounded_execution(self):
        arguments = [MODULE.GIT, "--version"]
        bounded_result = MODULE.BoundedCommandResult(
            returncode=0,
            stdout=b"git version\n",
            stderr=b"",
        )
        with mock.patch.object(
            MODULE,
            "_bounded_command",
            return_value=bounded_result,
        ) as bounded:
            completed = self.real_execute(
                arguments,
                timeout=17,
                env=MODULE.SAFE_GIT_ENV,
            )

        self.assertEqual(completed.args, arguments)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"git version\n")
        self.assertEqual(completed.stderr, b"")
        bounded.assert_called_once_with(
            arguments,
            timeout=17,
            env=dict(MODULE.SAFE_GIT_ENV),
            stdout_limit=MODULE.MAX_COMMAND_OUTPUT_BYTES,
            stderr_limit=MODULE.MAX_COMMAND_OUTPUT_BYTES,
        )

    def test_default_executor_maps_bounded_failure_but_not_baseexception(self):
        arguments = [MODULE.GIT, "--version"]
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=MODULE.BoundedCommandError("timed out"),
            ),
            self.assertRaisesRegex(
                MODULE.ReleaseArtifactError,
                "required local command is unavailable",
            ),
        ):
            self.real_execute(
                arguments,
                timeout=1,
                env=MODULE.SAFE_GIT_ENV,
            )
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.real_execute(
                arguments,
                timeout=1,
                env=MODULE.SAFE_GIT_ENV,
            )


if __name__ == "__main__":
    unittest.main()
