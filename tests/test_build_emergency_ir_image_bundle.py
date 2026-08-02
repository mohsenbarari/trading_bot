from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_emergency_ir_image_bundle",
    ROOT / "scripts" / "build_emergency_ir_image_bundle.py",
)
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILD
SPEC.loader.exec_module(BUILD)


PATCH_SHA = "a" * 40
APP_TAG = f"trading_bot_emergency_ir_app:{PATCH_SHA}"
POSTGRES_TAG = BUILD.expected_postgres_image(PATCH_SHA)
REDIS_TAG = BUILD.expected_redis_image(PATCH_SHA)
IMAGE_IDS = {
    APP_TAG: "sha256:" + "1" * 64,
    BUILD.POSTGRES_SOURCE_IMAGE: "sha256:" + "2" * 64,
    BUILD.REDIS_SOURCE_IMAGE: "sha256:" + "3" * 64,
}


def completed(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class LocalDockerRunner:
    """Small command recorder; no Docker or Git process is ever invoked."""

    def __init__(
        self,
        *,
        repo: Path,
        dirty: bool = False,
        head: str = PATCH_SHA,
        source_is_ancestor: bool = True,
        existing: set[str] | None = None,
    ) -> None:
        self.repo = repo
        self.dirty = dirty
        self.head = head
        self.source_is_ancestor = source_is_ancestor
        self.existing = set(existing or ())
        self.tags: dict[str, str] = {}
        self.app_built = False
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        command = list(command)
        self.commands.append(command)
        if command[0] == BUILD.GIT_BINARY:
            arguments = command[3:]
            if arguments == ["rev-parse", "--show-toplevel"]:
                return completed(stdout=str(self.repo) + "\n")
            if arguments == ["rev-parse", "--verify", "HEAD^{commit}"]:
                return completed(stdout=self.head + "\n")
            if arguments == ["merge-base", "--is-ancestor", BUILD.SOURCE_RELEASE_SHA, PATCH_SHA]:
                return completed(returncode=0 if self.source_is_ancestor else 1)
            if arguments == ["status", "--porcelain=v1", "--untracked-files=all"]:
                return completed(stdout=" M main.py\n" if self.dirty else "")
            raise AssertionError(f"unexpected Git command: {command!r}")

        if command[0] != BUILD.DOCKER_BINARY:
            raise AssertionError(f"unexpected local command: {command!r}")
        if command[1:3] == ["image", "inspect"]:
            reference = command[3]
            image_id = self.tags.get(reference)
            if image_id is None:
                if reference in self.existing:
                    image_id = "sha256:" + "9" * 64
                elif reference in IMAGE_IDS and reference != APP_TAG:
                    image_id = IMAGE_IDS[reference]
                elif reference == APP_TAG and self.app_built:
                    image_id = IMAGE_IDS[APP_TAG]
            if image_id is None:
                return completed(returncode=1, stderr=f"Error response from daemon: No such image: {reference}\n")
            if command[-1] == "{{json .}}":
                labels = {
                    "org.opencontainers.image.revision": PATCH_SHA,
                    "org.goldtrade.emergency.base-revision": BUILD.SOURCE_RELEASE_SHA,
                    "org.goldtrade.emergency.scope": "ir-standalone",
                    "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
                }
                return completed(
                    stdout=json.dumps(
                        {
                            "Id": image_id,
                            "RepoTags": [reference],
                            "Config": {"Labels": labels, "Env": ["PATH=/usr/bin"]},
                        }
                    )
                )
            return completed(stdout=image_id + "\n")
        if command[1] == "build":
            self.app_built = True
            return completed()
        if command[1:3] == ["image", "tag"]:
            source, target = command[3:5]
            if source not in IMAGE_IDS:
                raise AssertionError(f"unexpected tag source: {source}")
            self.tags[target] = IMAGE_IDS[source]
            return completed()
        if command[1] == "save":
            stream = kwargs.get("stdout")
            assert hasattr(stream, "write")
            stream.write(b"mock-docker-archive")
            return completed()
        raise AssertionError(f"unexpected Docker command: {command!r}")


def expected_entries() -> list[BUILD.activator.ImageEntry]:
    return [
        BUILD.activator.ImageEntry(kind="app", tag=APP_TAG, config_id=IMAGE_IDS[APP_TAG]),
        BUILD.activator.ImageEntry(
            kind="postgres",
            tag=POSTGRES_TAG,
            config_id=IMAGE_IDS[BUILD.POSTGRES_SOURCE_IMAGE],
        ),
        BUILD.activator.ImageEntry(
            kind="redis",
            tag=REDIS_TAG,
            config_id=IMAGE_IDS[BUILD.REDIS_SOURCE_IMAGE],
        ),
    ]


class EmergencyImageBundleTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir(mode=0o700)
        (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (repo / "Dockerfile").chmod(0o600)
        return repo

    def test_builds_exact_three_tag_archive_after_local_source_and_provenance_gates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-bundle-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repo = self._repo(root)
            output = root / "images.tar"
            runner = LocalDockerRunner(repo=repo)
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), mock.patch.object(
                BUILD.activator, "inspect_image_bundle", return_value=expected_entries()
            ) as archive_verify:
                built = BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=output,
                    runner=runner,
                )

            self.assertEqual(output, built.output)
            self.assertEqual(hashlib.sha256(b"mock-docker-archive").hexdigest(), built.sha256)
            self.assertEqual(len(b"mock-docker-archive"), built.bytes)
            self.assertEqual(expected_entries(), list(built.images))
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            archive_verify.assert_called_once_with(
                image_tar=mock.ANY,
                source_sha=BUILD.SOURCE_RELEASE_SHA,
                patch_sha=PATCH_SHA,
                profile="telegram-only",
            )
            build_command = next(command for command in runner.commands if command[1] == "build")
            self.assertIn("--pull=false", build_command)
            self.assertEqual(BUILD.DOCKER_BINARY, build_command[0])
            expected_labels = {
                "org.opencontainers.image.revision=" + PATCH_SHA,
                "org.goldtrade.emergency.base-revision=" + BUILD.SOURCE_RELEASE_SHA,
                "org.goldtrade.emergency.scope=ir-standalone",
                "org.goldtrade.emergency.auth=webapp-initdata-and-local-sms-otp",
            }
            self.assertTrue(expected_labels.issubset(set(build_command)))
            self.assertEqual(
                [
                    [BUILD.DOCKER_BINARY, "image", "tag", BUILD.POSTGRES_SOURCE_IMAGE, POSTGRES_TAG],
                    [BUILD.DOCKER_BINARY, "image", "tag", BUILD.REDIS_SOURCE_IMAGE, REDIS_TAG],
                ],
                [command for command in runner.commands if command[1:3] == ["image", "tag"]],
            )
            save_command = next(command for command in runner.commands if command[1] == "save")
            self.assertEqual(
                [BUILD.DOCKER_BINARY, "save", APP_TAG, POSTGRES_TAG, REDIS_TAG],
                save_command,
            )
            self.assertFalse(any(command[1] == "load" for command in runner.commands))

    def test_dirty_or_wrong_head_source_fails_before_any_docker_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-bundle-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repo = self._repo(root)
            runner = LocalDockerRunner(repo=repo, dirty=True)
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), self.assertRaisesRegex(
                BUILD.EmergencyImageBundleError, "not clean"
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=root / "images.tar",
                    runner=runner,
                )
            self.assertFalse(any(command[0] == BUILD.DOCKER_BINARY for command in runner.commands))

            runner = LocalDockerRunner(repo=repo, source_is_ancestor=False)
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), self.assertRaisesRegex(
                BUILD.EmergencyImageBundleError, "does not descend"
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=root / "wrong-base.tar",
                    runner=runner,
                )
            self.assertFalse(any(command[0] == BUILD.DOCKER_BINARY for command in runner.commands))

            wrong_head = "b" * 40
            runner = LocalDockerRunner(repo=repo, head=wrong_head)
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), self.assertRaisesRegex(
                BUILD.EmergencyImageBundleError, "does not equal"
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=root / "wrong-head.tar",
                    runner=runner,
                )
            self.assertFalse(any(command[0] == BUILD.DOCKER_BINARY for command in runner.commands))

    def test_preexisting_output_or_target_tag_fails_before_build_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-bundle-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repo = self._repo(root)
            output = root / "images.tar"
            output.write_bytes(b"existing")
            output.chmod(0o600)
            runner = LocalDockerRunner(repo=repo)
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), self.assertRaisesRegex(
                BUILD.EmergencyImageBundleError, "overwrite an existing Emergency image archive"
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo, emergency_patch_sha=PATCH_SHA, output=output, runner=runner
                )
            self.assertFalse(runner.commands)

            runner = LocalDockerRunner(repo=repo, existing={POSTGRES_TAG})
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), self.assertRaisesRegex(
                BUILD.EmergencyImageBundleError, "overwrite existing Emergency target tag"
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo, emergency_patch_sha=PATCH_SHA, output=root / "fresh.tar", runner=runner
                )
            self.assertFalse(any(command[1] == "build" for command in runner.commands if command[0] == BUILD.DOCKER_BINARY))

    def test_dependency_tags_are_patch_bound_and_legacy_tags_do_not_block_a_fresh_build(self) -> None:
        other_patch = "b" * 40
        self.assertEqual(
            f"trading_bot_emergency_ir_postgres:15-alpine-{PATCH_SHA}",
            POSTGRES_TAG,
        )
        self.assertEqual(
            f"trading_bot_emergency_ir_redis:7-alpine-{PATCH_SHA}",
            REDIS_TAG,
        )
        self.assertNotEqual(POSTGRES_TAG, BUILD.expected_postgres_image(other_patch))
        self.assertNotEqual(REDIS_TAG, BUILD.expected_redis_image(other_patch))

        with tempfile.TemporaryDirectory(prefix="emergency-image-bundle-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repo = self._repo(root)
            runner = LocalDockerRunner(
                repo=repo,
                existing={
                    "trading_bot_emergency_ir_postgres:15-alpine",
                    "trading_bot_emergency_ir_redis:7-alpine",
                },
            )
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), mock.patch.object(
                BUILD.activator, "inspect_image_bundle", return_value=expected_entries()
            ):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=root / "fresh.tar",
                    runner=runner,
                )

            self.assertEqual(
                [
                    [BUILD.DOCKER_BINARY, "image", "tag", BUILD.POSTGRES_SOURCE_IMAGE, POSTGRES_TAG],
                    [BUILD.DOCKER_BINARY, "image", "tag", BUILD.REDIS_SOURCE_IMAGE, REDIS_TAG],
                ],
                [command for command in runner.commands if command[1:3] == ["image", "tag"]],
            )

    def test_docker_inspect_error_is_not_treated_as_an_absent_target_tag(self) -> None:
        def denied(_command: list[str], **_kwargs: object) -> SimpleNamespace:
            return completed(returncode=1, stderr="permission denied while connecting to the Docker daemon")

        with self.assertRaisesRegex(BUILD.EmergencyImageBundleError, "failed"):
            BUILD._image_id(
                POSTGRES_TAG,
                label="target probe",
                runner=denied,
                allow_missing=True,
            )

    def test_archive_id_mismatch_is_not_finalized_or_sealed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-bundle-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repo = self._repo(root)
            output = root / "images.tar"
            runner = LocalDockerRunner(repo=repo)
            mismatched = expected_entries()
            mismatched[0] = BUILD.activator.ImageEntry(
                kind="app", tag=APP_TAG, config_id="sha256:" + "f" * 64
            )
            with mock.patch.object(BUILD.os, "geteuid", return_value=0), mock.patch.object(
                BUILD.activator, "inspect_image_bundle", return_value=mismatched
            ), self.assertRaisesRegex(BUILD.EmergencyImageBundleError, "IDs differ"):
                BUILD.build_emergency_ir_image_bundle(
                    repo=repo,
                    emergency_patch_sha=PATCH_SHA,
                    output=output,
                    runner=runner,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".emergency-ir-images-*.tar")))
            self.assertTrue(any(command[1] == "save" for command in runner.commands))

    def test_module_has_no_object_storage_remote_or_activation_surface(self) -> None:
        source = Path(BUILD.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(BUILD.__file__))
        modules = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue({"boto3", "botocore", "socket", "urllib", "requests", "paramiko"}.isdisjoint(modules))
        strings = {
            value.value
            for value in ast.walk(tree)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        for forbidden in ("s3://", "ssh", "scp", "rsync", "docker load", "systemctl", "nginx"):
            self.assertNotIn(forbidden, strings)


if __name__ == "__main__":
    unittest.main()
