"""Focused shared-host safety tests for the future WA-IR image loader."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import contextlib
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/load_webapp_ir_staged_images.py"
SPEC = importlib.util.spec_from_file_location("load_webapp_ir_staged_images", SCRIPT)
assert SPEC and SPEC.loader
loader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = loader
SPEC.loader.exec_module(loader)


RELEASE_SHA = "a" * 40
CAMPAIGN_ID = "current-2c08-standby-campaign"


def image_id(config: bytes) -> str:
    return "sha256:" + hashlib.sha256(config).hexdigest()


class FakeDockerRunner:
    def __init__(self, expected: dict[str, str]) -> None:
        self.expected = expected
        self.calls: list[list[str]] = []
        self.loaded = False
        self.preexisting: dict[str, str] = {}
        self.loaded_overrides: dict[str, str] = {}

    def __call__(self, arguments, _cwd, _timeout):
        args = [str(item) for item in arguments]
        self.calls.append(args)
        if args[1:5] == ["image", "inspect", "--format", "{{.Id}}"]:
            tag = args[5]
            if tag in self.preexisting:
                return subprocess.CompletedProcess(args, 0, stdout=self.preexisting[tag] + "\n", stderr="")
            if self.loaded:
                actual = self.loaded_overrides.get(tag, self.expected[tag])
                return subprocess.CompletedProcess(args, 0, stdout=actual + "\n", stderr="")
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Error response from daemon: No such image: " + tag)
        if args[1:4] == ["image", "load", "--input"]:
            self.loaded = True
            return subprocess.CompletedProcess(args, 0, stdout="Loaded image\n", stderr="")
        raise AssertionError(f"unexpected Docker command: {args}")


class StagedImageLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="wa-ir-image-loader-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.bootstrap_receipt_path = self.root / "bootstrap-receipt.json"
        self.config_one = b'{"architecture":"amd64","config":"one"}'
        self.config_two = b'{"architecture":"amd64","config":"two"}'
        self.first_id = image_id(self.config_one)
        self.second_id = image_id(self.config_two)
        self.tags = {
            self.first_id: loader.image_contract.canonical_archive_tag(
                campaign_id=CAMPAIGN_ID, release_sha=RELEASE_SHA, image_id=self.first_id
            ),
            self.second_id: loader.image_contract.canonical_archive_tag(
                campaign_id=CAMPAIGN_ID, release_sha=RELEASE_SHA, image_id=self.second_id
            ),
        }

    def _write_archive(self, *, tags: dict[str, list[str]] | None = None, repositories: bool = False) -> Path:
        path = self.root / "images.tar"
        tags = tags or {image: [tag] for image, tag in self.tags.items()}
        configs = {self.first_id: self.config_one, self.second_id: self.config_two}
        with tarfile.open(path, "w") as archive:
            manifest = []
            for identifier in sorted(configs):
                config = configs[identifier]
                config_name = identifier.removeprefix("sha256:") + ".json"
                info = tarfile.TarInfo(config_name)
                info.size = len(config)
                archive.addfile(info, io.BytesIO(config))
                manifest.append({"Config": config_name, "Layers": [], "RepoTags": tags[identifier]})
            payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
            if repositories:
                payload = b'{"postgres":{"15-alpine":"legacy"}}'
                info = tarfile.TarInfo("repositories")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        path.chmod(0o600)
        return path

    def _stage(self, archive: Path) -> tuple[Path, object]:
        sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = self.root / "image-manifest.json"
        images = [
            {
                "archive_tag": self.tags[self.second_id],
                "image_id": self.second_id,
                "repo_digests": [],
                "repo_tags": ["postgres:15-alpine"],
                "size_bytes": 1,
                "source_ref": "postgres:15-alpine",
            },
            {
                "archive_tag": self.tags[self.first_id],
                "image_id": self.first_id,
                "repo_digests": [],
                "repo_tags": ["trading_bot_base:latest"],
                "size_bytes": 1,
                "source_ref": "trading_bot_base:latest",
            },
        ]
        manifest.write_text(
            json.dumps(
                {
                    "archive": {"sha256": sha256},
                    "campaign_id": CAMPAIGN_ID,
                    "image_set_sha256": "0" * 64,
                    "images": images,
                    "release_sha": RELEASE_SHA,
                    "schema": loader.provenance.IMAGE_MANIFEST_SCHEMA,
                    "status": "prepared",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        bundle = SimpleNamespace(path=archive, sha256=sha256, bytes=archive.stat().st_size)
        manifest_artifact = SimpleNamespace(path=manifest)
        stage = SimpleNamespace(
            artifacts={
                loader.provenance.IMAGE_BUNDLE_ARTIFACT: bundle,
                loader.provenance.IMAGE_MANIFEST_ARTIFACT: manifest_artifact,
            },
            bundle_id="20260730T120000Z-0123456789abcdef01234567",
            release_sha=RELEASE_SHA,
        )
        return self.root / "stage-receipt.json", stage

    @contextlib.contextmanager
    def _verify_context(self, stage: object):
        with (
            mock.patch.object(loader.provenance, "load_bootstrap_receive_receipt", return_value=object()),
            mock.patch.object(loader.provenance, "verify_staged_provenance", return_value=(stage, object())),
        ):
            yield

    def test_load_refuses_preexisting_isolated_tag_without_calling_docker_load(self) -> None:
        receipt, stage = self._stage(self._write_archive())
        runner = FakeDockerRunner({tag: identifier for identifier, tag in self.tags.items()})
        runner.preexisting[self.tags[self.first_id]] = self.first_id

        with self._verify_context(stage):
            with self.assertRaisesRegex(loader.StagedImageLoadError, "refusing to overwrite"):
                loader.load_verified_staged_images(
                    stage_receipt_path=receipt,
                    bootstrap_receipt_path=self.bootstrap_receipt_path,
                    runner=runner,
                )

        self.assertFalse(any(call[1:4] == ["image", "load", "--input"] for call in runner.calls))

    def test_load_rejects_shared_archive_tag_before_any_docker_command(self) -> None:
        unsafe = {image: list(values) for image, values in {image: [tag] for image, tag in self.tags.items()}.items()}
        unsafe[self.second_id] = ["postgres:15-alpine"]
        receipt, stage = self._stage(self._write_archive(tags=unsafe))
        runner = FakeDockerRunner({tag: identifier for identifier, tag in self.tags.items()})

        with self._verify_context(stage):
            with self.assertRaisesRegex(loader.StagedImageLoadError, "not safe for a shared host"):
                loader.load_verified_staged_images(
                    stage_receipt_path=receipt,
                    bootstrap_receipt_path=self.bootstrap_receipt_path,
                    runner=runner,
                )

        self.assertEqual([], runner.calls)

    def test_load_rejects_legacy_repositories_metadata_before_any_docker_command(self) -> None:
        receipt, stage = self._stage(self._write_archive(repositories=True))
        runner = FakeDockerRunner({tag: identifier for identifier, tag in self.tags.items()})

        with self._verify_context(stage):
            with self.assertRaisesRegex(loader.StagedImageLoadError, "not safe for a shared host"):
                loader.load_verified_staged_images(
                    stage_receipt_path=receipt,
                    bootstrap_receipt_path=self.bootstrap_receipt_path,
                    runner=runner,
                )

        self.assertEqual([], runner.calls)

    def test_load_requires_every_loaded_tag_to_resolve_to_the_signed_immutable_id(self) -> None:
        receipt, stage = self._stage(self._write_archive())
        runner = FakeDockerRunner({tag: identifier for identifier, tag in self.tags.items()})
        runner.loaded_overrides[self.tags[self.second_id]] = self.first_id

        with self._verify_context(stage):
            with self.assertRaisesRegex(loader.StagedImageLoadError, "does not match its immutable"):
                loader.load_verified_staged_images(
                    stage_receipt_path=receipt,
                    bootstrap_receipt_path=self.bootstrap_receipt_path,
                    runner=runner,
                )

        self.assertTrue(any(call[1:4] == ["image", "load", "--input"] for call in runner.calls))

    def test_load_uses_only_isolated_tags_and_verifies_all_loaded_ids(self) -> None:
        receipt, stage = self._stage(self._write_archive())
        runner = FakeDockerRunner({tag: identifier for identifier, tag in self.tags.items()})

        with self._verify_context(stage):
            result = loader.load_verified_staged_images(
                stage_receipt_path=receipt,
                bootstrap_receipt_path=self.bootstrap_receipt_path,
                runner=runner,
            )

        self.assertEqual("loaded", result["status"])
        self.assertEqual(set(self.tags.values()), {item["archive_tag"] for item in result["images"]})
        self.assertTrue(any(call[1:4] == ["image", "load", "--input"] for call in runner.calls))
        self.assertFalse(any("postgres:15-alpine" in call for call in runner.calls))


if __name__ == "__main__":
    unittest.main()
