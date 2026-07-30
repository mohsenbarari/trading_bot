"""Focused tests for deterministic local WebApp-FI static-asset preparation."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PREPARE = _load_module(
    "prepare_webapp_fi_static_assets_test",
    ROOT / "scripts" / "prepare_webapp_fi_static_assets.py",
)
ADOPT = _load_module(
    "adopt_webapp_fi_static_assets_for_preparation_test",
    ROOT / "scripts" / "adopt_webapp_fi_static_assets.py",
)


CAMPAIGN = "static-assets-12345678"
RELEASE = "a" * 40
REVISION = "f2c7d8e9a0b1"


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@unittest.skipUnless(os.geteuid() == 0, "static asset preparation is root-only")
class StaticAssetPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="prepare-static-assets-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.runtime = self.root / "runtime"
        self.static_root = self.runtime / "mini_app_dist"
        self.output_parent = self.root / "outputs"
        self.runtime.mkdir(mode=0o700)
        self._git("init")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "Static Asset Fixture")
        self.static_root.mkdir(mode=0o755)
        self.output_parent.mkdir(mode=0o700)
        self._write_source("index.html", b"<!doctype html><title>fixture</title>\n")
        self._write_source("assets/app.js", b"console.log('fixture');\n")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")
        self.release = self._git("rev-parse", "HEAD", capture=True)
        self.application = {"release_sha": self.release, "expected_alembic_revision": REVISION}

    def _git(self, *arguments: str, capture: bool = False) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.runtime), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=capture,
        )
        return result.stdout.strip() if capture else ""

    def _write_source(self, relative: str, payload: bytes) -> Path:
        path = self.static_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in (path.parent,):
            parent.chmod(0o755)
        path.write_bytes(payload)
        path.chmod(0o644)
        return path

    def _prepare(self, *, candidate: str = "candidate", **kwargs: object) -> dict[str, object]:
        return PREPARE.prepare_static_assets(
            runtime_source_root=self.runtime,
            output_directory=self.output_parent / candidate,
            expected_campaign_id=CAMPAIGN,
            expected_application=self.application,
            **kwargs,
        )

    def test_plan_and_apply_make_exact_consumer_compatible_ustar_candidate(self) -> None:
        plan = self._prepare(apply=False)
        result = self._prepare(apply=True)

        candidate = self.output_parent / "candidate"
        self.assertEqual("planned", plan["status"])
        self.assertEqual("prepared", result["status"])
        self.assertFalse(plan["object_storage_action"])
        self.assertFalse(plan["age_action"])
        self.assertFalse(plan["ssh_action"])
        self.assertFalse(plan["docker_action"])
        self.assertFalse(plan["service_changed"])
        self.assertEqual(
            {
                PREPARE.STATIC_ARCHIVE_NAME,
                PREPARE.STATIC_FILE_MANIFEST_NAME,
                PREPARE.STATIC_PREPARATION_RECEIPT_NAME,
            },
            {entry.name for entry in candidate.iterdir()},
        )
        self.assertEqual(0o700, _mode(candidate))
        for name in (
            PREPARE.STATIC_ARCHIVE_NAME,
            PREPARE.STATIC_FILE_MANIFEST_NAME,
            PREPARE.STATIC_PREPARATION_RECEIPT_NAME,
        ):
            self.assertEqual(0o600, _mode(candidate / name))
            self.assertEqual(0, (candidate / name).stat().st_uid)

        archive = candidate / PREPARE.STATIC_ARCHIVE_NAME
        archive_sha256, archive_bytes = PREPARE.sha256_file(archive)
        adopter_files = ADOPT._inspect_static_archive(
            archive_path=archive,
            object_descriptor={"plaintext_sha256": archive_sha256, "plaintext_bytes": archive_bytes},
        )
        manifest = json.loads((candidate / PREPARE.STATIC_FILE_MANIFEST_NAME).read_text(encoding="ascii"))
        self.assertEqual(adopter_files, manifest["files"])
        self.assertEqual(PREPARE._files_sha256(adopter_files), manifest["files_sha256"])
        self.assertEqual(["assets/app.js", "index.html"], [entry["path"] for entry in adopter_files])
        with tarfile.open(archive, "r:") as tar:
            members = tar.getmembers()
        self.assertEqual(["assets/app.js", "index.html"], [member.name for member in members])
        self.assertTrue(all(member.type == tarfile.REGTYPE for member in members))
        self.assertTrue(all(member.mode == 0o644 and member.mtime == 0 for member in members))
        self.assertTrue(all(not member.pax_headers and not member.linkname for member in members))

        receipt = json.loads((candidate / PREPARE.STATIC_PREPARATION_RECEIPT_NAME).read_text(encoding="ascii"))
        encoded = json.dumps(receipt, sort_keys=True).lower()
        self.assertNotIn("://", encoded)
        self.assertNotIn('"url"', encoded)
        self.assertNotIn('"presigned"', encoded)
        receipt_unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        self.assertEqual(PREPARE.sha256_bytes(PREPARE.canonical_json_bytes(receipt_unsigned)), receipt["receipt_sha256"])
        self.assertEqual("verified", result["verification"]["status"])
        self.assertNotIn("subprocess", PREPARE.__dict__)
        self.assertNotIn("boto3", PREPARE.__dict__)

    def test_checkout_commit_mismatch_blocks_before_candidate_creation(self) -> None:
        candidate = self.output_parent / "wrong-checkout"
        wrong_application = {"release_sha": "0" * 40, "expected_alembic_revision": REVISION}
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "does not match expected release"):
            PREPARE.prepare_static_assets(
                runtime_source_root=self.runtime,
                output_directory=candidate,
                expected_campaign_id=CAMPAIGN,
                expected_application=wrong_application,
                apply=True,
            )
        self.assertFalse(candidate.exists())

    def test_capacity_preflight_blocks_before_creating_candidate(self) -> None:
        candidate = self.output_parent / "insufficient-capacity"
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "insufficient free space"):
            self._prepare(
                candidate=candidate.name,
                apply=True,
                disk_usage=lambda _path: SimpleNamespace(free=0),
            )
        self.assertFalse(candidate.exists())

    def test_source_drift_after_archive_preserves_failed_candidate_without_success_receipt(self) -> None:
        candidate = self.output_parent / "source-drift"
        original = PREPARE._write_ustar_archive
        mutated = False

        def write_then_mutate(**kwargs: object):
            nonlocal mutated
            result = original(**kwargs)
            self._write_source("index.html", b"changed after archive\n")
            mutated = True
            return result

        with mock.patch.object(PREPARE, "_write_ustar_archive", side_effect=write_then_mutate):
            with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "source drifted"):
                self._prepare(candidate=candidate.name, apply=True)
        self.assertTrue(mutated)
        self.assertTrue(candidate.is_dir())
        self.assertTrue((candidate / PREPARE.STATIC_ARCHIVE_NAME).is_file())
        self.assertFalse((candidate / PREPARE.STATIC_FILE_MANIFEST_NAME).exists())
        self.assertFalse((candidate / PREPARE.STATIC_PREPARATION_RECEIPT_NAME).exists())

    def test_rejects_non_private_runtime_source_before_candidate_creation(self) -> None:
        self.runtime.chmod(0o755)
        candidate = self.output_parent / "unsafe-runtime"
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "runtime source root must be one root-only"):
            self._prepare(candidate=candidate.name, apply=True)
        self.assertFalse(candidate.exists())

    def test_rejects_symlinked_static_source_entry_before_candidate_creation(self) -> None:
        (self.static_root / "escape.js").symlink_to(self.root / "outside.js")
        candidate = self.output_parent / "symlink-entry"
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "only directories and regular files"):
            self._prepare(candidate=candidate.name, apply=True)
        self.assertFalse(candidate.exists())

    def test_rejects_path_that_cannot_be_represented_as_ustar_before_candidate_creation(self) -> None:
        first = "a" * 100
        second = "b" * 100
        third = "c" * 100
        self._write_source(first + "/" + second + "/" + third + "/entry.js", b"fixture\n")
        candidate = self.output_parent / "long-ustar-path"
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "cannot be represented in USTAR"):
            self._prepare(candidate=candidate.name, apply=True)
        self.assertFalse(candidate.exists())

    def test_create_only_refuses_existing_candidate(self) -> None:
        candidate = self.output_parent / "already-exists"
        candidate.mkdir(mode=0o700)
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "must be a new child"):
            self._prepare(candidate=candidate.name, apply=True)
        self.assertEqual(set(), {entry.name for entry in candidate.iterdir()})

    def test_verifier_rejects_changed_manifest_and_preserves_candidate(self) -> None:
        result = self._prepare(apply=True)
        candidate = Path(str(result["output_directory"]))
        manifest_path = candidate / PREPARE.STATIC_FILE_MANIFEST_NAME
        value = json.loads(manifest_path.read_text(encoding="ascii"))
        value["files"][0]["sha256"] = "0" * 64
        manifest_path.write_bytes(PREPARE.canonical_json_bytes(value) + b"\n")
        manifest_path.chmod(0o600)
        with self.assertRaisesRegex(PREPARE.StaticAssetPreparationError, "does not bind the prepared archive"):
            PREPARE.verify_prepared_static_assets(
                output_directory=candidate,
                expected_campaign_id=CAMPAIGN,
                expected_application=self.application,
                expected_static_source=self.static_root,
            )
        self.assertTrue(candidate.is_dir())
        self.assertTrue((candidate / PREPARE.STATIC_ARCHIVE_NAME).is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
