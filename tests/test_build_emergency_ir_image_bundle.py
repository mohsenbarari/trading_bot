from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_emergency_ir_image_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_image_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


PATCH_SHA = "a" * 40
PYTHON_ID = "sha256:" + "1" * 64
POSTGRES_ID = "sha256:" + "2" * 64
REDIS_ID = "sha256:" + "3" * 64
APP_ID = "sha256:" + "4" * 64


def completed(command: object, *, returncode: int = 0, stdout: str | bytes = "", stderr: str | bytes = "") -> subprocess.CompletedProcess[object]:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def app_payload(*, tag: str, labels: dict[str, str] | None = None, tags: list[str] | None = None) -> dict[str, object]:
    return {
        "Id": APP_ID,
        "RepoTags": tags if tags is not None else [tag],
        "Config": {
            "Labels": labels
            if labels is not None
            else {
                "org.opencontainers.image.revision": PATCH_SHA,
                "org.goldtrade.emergency.base-revision": BUILDER.SOURCE_RELEASE_SHA,
                "org.goldtrade.emergency.scope": "ir-standalone",
                "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
            },
            "Env": ["PATH=/usr/local/bin"],
        },
    }


class BuildEmergencyIrImageBundleTests(unittest.TestCase):
    def checkout_runner(self, repo: Path, *, head: str = PATCH_SHA, ancestor_code: int = 0, dirty: str = ""):
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
            if "--show-toplevel" in command:
                return completed(command, stdout=str(repo) + "\n")
            if "status" in command:
                return completed(command, stdout=dirty)
            if command[-1:] == ["HEAD"]:
                return completed(command, stdout=head + "\n")
            if "merge-base" in command:
                return completed(command, returncode=ancestor_code)
            self.fail(f"unexpected command: {command}")

        return runner

    def test_clean_checkout_rejects_dirty_wrong_head_and_wrong_base(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-builder-") as raw:
            repo = Path(raw)
            with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "dirty"):
                BUILDER._assert_clean_checkout(repo=repo, runner=self.checkout_runner(repo, dirty="?? accidental.py\n"))
            with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "HEAD"):
                BUILDER._assert_clean_checkout(repo=repo, runner=self.checkout_runner(repo, head="not-a-sha"))
            with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "attested production base"):
                BUILDER._assert_clean_checkout(repo=repo, runner=self.checkout_runner(repo, ancestor_code=1))

    def test_application_provenance_rejects_label_and_tag_mismatch(self) -> None:
        tag = f"{BUILDER.APP_REPOSITORY}:{PATCH_SHA}"
        for name, payload in (
            (
                "labels",
                app_payload(tag=tag, labels={"org.opencontainers.image.revision": "wrong"}),
            ),
            ("tag", app_payload(tag=tag, tags=["trading_bot_emergency_ir_app:" + "b" * 40])),
        ):
            with self.subTest(name=name):
                def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
                    self.assertEqual(command[:3], ["docker", "image", "inspect"])
                    return completed(command, stdout=json.dumps(payload))

                with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "provenance/tag"):
                    BUILDER._verify_application_image(tag=tag, patch_sha=PATCH_SHA, runner=runner)

    def test_missing_local_source_image_fails_before_any_pull(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
            calls.append(command)
            return completed(command, returncode=1, stderr="No such image")

        with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "unavailable"):
            BUILDER._inspect_local_image(image="postgres:15-alpine", label="PostgreSQL source", runner=runner)
        self.assertEqual(calls[0][:3], ["docker", "image", "inspect"])
        self.assertFalse(any(command[:2] == ["docker", "pull"] for command in calls))

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-builder-") as raw:
            output = Path(raw) / "images.tar"
            output.write_bytes(b"prior-forensic-artifact")

            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
                self.fail(f"save runner must not be called for existing output: {command}")

            with self.assertRaisesRegex(BUILDER.EmergencyImageBundleError, "overwrite"):
                BUILDER._save_images_create_only(output=output, tags=("one", "two", "three"), runner=runner)
            self.assertEqual(output.read_bytes(), b"prior-forensic-artifact")

    def test_builds_receipted_bundle_from_clean_git_and_immutable_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-image-builder-") as raw:
            root = Path(raw)
            repository = root / "repository"
            subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(repository)], check=True)
            frontend = root / "frontend"
            frontend.mkdir(mode=0o700)
            (frontend / "index.html").write_text("<!doctype html><title>Emergency</title>\n", encoding="utf-8")
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir(mode=0o700)
            (wheelhouse / "fixture-1.0-py3-none-any.whl").write_bytes(b"not-a-real-wheel-but-local")
            output = root / "images.tar"
            receipt = root / "images.receipt.json"
            context = root / "immutable-context"
            app_tag = f"{BUILDER.APP_REPOSITORY}:{PATCH_SHA}"
            postgres_tag = BUILDER._base_target_tag(repository=BUILDER.POSTGRES_REPOSITORY, image_id=POSTGRES_ID)
            redis_tag = BUILDER._base_target_tag(repository=BUILDER.REDIS_REPOSITORY, image_id=REDIS_ID)
            command_log: list[list[str]] = []

            source_images = {
                BUILDER.PYTHON_BASE_IMAGE: {"Id": PYTHON_ID, "RepoTags": [BUILDER.PYTHON_BASE_IMAGE], "Config": {"Env": []}},
                "postgres:15-alpine": {"Id": POSTGRES_ID, "RepoTags": ["postgres:15-alpine"], "Config": {"Env": []}},
                "redis:7-alpine": {"Id": REDIS_ID, "RepoTags": ["redis:7-alpine"], "Config": {"Env": []}},
                app_tag: app_payload(tag=app_tag),
                postgres_tag: {"Id": POSTGRES_ID, "RepoTags": [postgres_tag], "Config": {"Env": []}},
                redis_tag: {"Id": REDIS_ID, "RepoTags": [redis_tag], "Config": {"Env": []}},
            }

            def add_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
                item = tarfile.TarInfo(name)
                item.mode = 0o600
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))

            def write_save(fd: int) -> None:
                app_config = source_images[app_tag]["Config"]
                rows = (
                    (APP_ID, app_tag, app_config),
                    (POSTGRES_ID, postgres_tag, source_images[postgres_tag]["Config"]),
                    (REDIS_ID, redis_tag, source_images[redis_tag]["Config"]),
                )
                with os.fdopen(os.dup(fd), "wb") as stream, tarfile.open(fileobj=stream, mode="w") as archive:
                    manifest = []
                    for image_id, tag, config in rows:
                        config_name = image_id.partition(":")[2] + ".json"
                        add_member(archive, config_name, json.dumps({"config": config}).encode("utf-8"))
                        manifest.append({"Config": config_name, "RepoTags": [tag], "Layers": []})
                    add_member(archive, "manifest.json", json.dumps(manifest).encode("utf-8"))

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object] | SimpleNamespace:
                command_log.append(command)
                if command[0] == "git":
                    return subprocess.run(command, **kwargs)
                if command[:3] == ["docker", "image", "inspect"]:
                    payload = source_images.get(command[3])
                    if payload is None:
                        return completed(command, returncode=1)
                    return completed(command, stdout=json.dumps(payload))
                if command[:3] == ["docker", "image", "ls"]:
                    return completed(command, stdout="")
                if command[:2] == ["docker", "build"]:
                    self.assertIn("--pull=false", command)
                    return completed(command)
                if command[:3] == ["docker", "image", "tag"]:
                    return completed(command)
                if command[:3] == ["docker", "image", "save"]:
                    stdout = kwargs.get("stdout")
                    self.assertIsInstance(stdout, int)
                    write_save(stdout)
                    return completed(command)
                self.fail(f"unexpected command: {command}")

            # The cloned checkout has its own HEAD, so rewrite only the fake
            # image's revision/tag to that exact committed identity.
            clone_head = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            source_images.pop(app_tag)
            app_tag = f"{BUILDER.APP_REPOSITORY}:{clone_head}"
            source_images[app_tag] = app_payload(
                tag=app_tag,
                labels={
                    "org.opencontainers.image.revision": clone_head,
                    "org.goldtrade.emergency.base-revision": BUILDER.SOURCE_RELEASE_SHA,
                    "org.goldtrade.emergency.scope": "ir-standalone",
                    "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
                },
            )

            result = BUILDER.build_bundle(
                repo=repository,
                frontend_dist=frontend,
                wheelhouse=wheelhouse,
                postgres_image="postgres:15-alpine",
                redis_image="redis:7-alpine",
                output=output,
                receipt_output=receipt,
                context_output=context,
                runner=runner,
            )
            self.assertTrue(output.is_file())
            self.assertTrue(receipt.is_file())
            self.assertTrue((context / "mini_app_dist" / "index.html").is_file())
            self.assertTrue((context / "pip_packages" / "fixture-1.0-py3-none-any.whl").is_file())
            self.assertEqual(result.receipt["emergency_patch_sha"], clone_head)
            self.assertEqual(result.receipt["images"]["postgres"]["source_image_id"], POSTGRES_ID)
            self.assertEqual(result.receipt["images"]["redis"]["source_image_id"], REDIS_ID)
            self.assertFalse(any(command[:2] == ["docker", "pull"] for command in command_log))


if __name__ == "__main__":
    unittest.main()
