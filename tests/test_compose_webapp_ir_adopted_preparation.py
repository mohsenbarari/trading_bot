"""Focused tests for the local adopted-image preparation composition primitive."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "compose_webapp_ir_adopted_preparation.py"
SPEC = importlib.util.spec_from_file_location("compose_webapp_ir_adopted_preparation_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


RELEASE_SHA = MODULE.provenance.LEGACY_APPLICATION_RELEASE_SHA
RELEASE_TREE = "a" * 40
CAMPAIGN_ID = "adopted-preparation-campaign"
PREPARED_AT = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _digest(path: Path) -> tuple[str, int]:
    return MODULE.preparer.sha256_file(path)


def _image_id(config: bytes) -> str:
    return "sha256:" + hashlib.sha256(config).hexdigest()


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _write_docker_archive(path: Path, entries: list[tuple[bytes, list[str]]]) -> None:
    manifest: list[dict[str, object]] = []
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for config, tags in entries:
            identifier = _image_id(config)
            config_name = identifier.removeprefix("sha256:") + ".json"
            info = tarfile.TarInfo(config_name)
            info.mode = 0o600
            info.size = len(config)
            archive.addfile(info, io.BytesIO(config))
            manifest.append({"Config": config_name, "Layers": [], "RepoTags": tags})
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
        info = tarfile.TarInfo("manifest.json")
        info.mode = 0o600
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)


@unittest.skipUnless(os.geteuid() == 0, "composition contract is root-only")
class ComposeAdoptedPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="compose-adopted-preparation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.source_output = self.root / "source-preparation"
        self.adopted_input = self.root / "adopted-input"
        self.output_parent = self.root / "outputs"
        for directory in (self.source_output, self.adopted_input, self.output_parent):
            directory.mkdir(mode=0o700)
        self.source_receipt, self.source_release = self._make_source_preparation()
        self.adopted_bundle, self.adopted_manifest, self.adopted_images = self._make_adopted_images()

    def _tag(self, image_id: str) -> str:
        return MODULE.preparer.image_contract.canonical_archive_tag(
            campaign_id=CAMPAIGN_ID,
            release_sha=RELEASE_SHA,
            image_id=image_id,
        )

    def _artifact(self, name: str, path: Path, bindings: dict[str, str]) -> dict[str, object]:
        digest, bytes_value = _digest(path)
        return {
            "bindings": dict(sorted(bindings.items())),
            "bytes": bytes_value,
            "name": name,
            "path": str(path),
            "sha256": digest,
        }

    def _make_source_preparation(self) -> tuple[Path, Path]:
        release_bundle = _write_private(self.source_output / "release.bundle", b"immutable application release bundle\n")
        source_config = b'{"architecture":"amd64","config":"source-app"}'
        source_id = _image_id(source_config)
        source_tag = self._tag(source_id)
        image_bundle = self.source_output / "images.tar"
        _write_docker_archive(image_bundle, [(source_config, [source_tag])])
        image_sha, image_bytes = _digest(image_bundle)
        images = [
            {
                "archive_tag": source_tag,
                "image_id": source_id,
                "repo_digests": [],
                "repo_tags": ["trading_bot_base:rollback-2c08"],
                "size_bytes": 101,
                "source_ref": "trading_bot_base:rollback-2c08",
            }
        ]
        image_archive = {
            "bytes": image_bytes,
            "image_ids": [source_id],
            "repo_tags": [source_tag],
            "sha256": image_sha,
        }
        image_manifest = _write_private(
            self.source_output / "image-manifest.json",
            _canonical(
                {
                    "archive": image_archive,
                    "campaign_id": CAMPAIGN_ID,
                    "image_set_sha256": MODULE.preparer.sha256_bytes(MODULE.preparer.canonical_json_bytes(images)),
                    "images": images,
                    "release_sha": RELEASE_SHA,
                    "schema": MODULE.preparer.IMAGE_MANIFEST_SCHEMA,
                    "status": "prepared",
                }
            ),
        )
        release_sha, release_bytes = _digest(release_bundle)
        manifest_sha, _manifest_bytes = _digest(image_manifest)
        image_ids_sha = MODULE.preparer.sha256_bytes(MODULE.preparer.canonical_json_bytes([source_id]))
        image_set_sha = MODULE.preparer.sha256_bytes(MODULE.preparer.canonical_json_bytes(images))
        artifacts = [
            self._artifact(
                MODULE.provenance.IMAGE_BUNDLE_ARTIFACT,
                image_bundle,
                {
                    "artifact_sha256": image_sha,
                    "image_count": "1",
                    "image_ids_sha256": image_ids_sha,
                    "image_manifest_sha256": manifest_sha,
                    "image_set_sha256": image_set_sha,
                    "release_sha": RELEASE_SHA,
                },
            ),
            self._artifact(
                MODULE.provenance.IMAGE_MANIFEST_ARTIFACT,
                image_manifest,
                {
                    "artifact_sha256": manifest_sha,
                    "image_set_sha256": image_set_sha,
                    "release_sha": RELEASE_SHA,
                },
            ),
            self._artifact(
                MODULE.provenance.APPLICATION_BUNDLE_ARTIFACT,
                release_bundle,
                {
                    "artifact_sha256": release_sha,
                    "git_commit": RELEASE_SHA,
                    "git_tree": RELEASE_TREE,
                    "release_sha": RELEASE_SHA,
                },
            ),
        ]
        receipt = {
            "artifacts": artifacts,
            "campaign_id": CAMPAIGN_ID,
            "capacity_preflight": {
                "image_logical_bytes": 101,
                "output_free_bytes": 1_000_000,
                "output_required_bytes": 500_000,
                "workspace_free_bytes": 1_000_000,
                "workspace_required_bytes": 500_000,
            },
            "image_archive": image_archive,
            "images": images,
            "output_directory": str(self.source_output),
            "preparation_id": "source-preparation-0123456789abcdef",
            "release_bundle": {
                "bytes": release_bytes,
                "git_commit": RELEASE_SHA,
                "git_tree": RELEASE_TREE,
                "sha256": release_sha,
            },
            "release_sha": RELEASE_SHA,
            "schema": MODULE.preparer.PREPARATION_SCHEMA,
            "stage_publish": MODULE._stage_publish(artifacts),
            "status": "prepared",
            "prepared_at": "2026-07-30T12:00:00Z",
        }
        receipt["receipt_sha256"] = MODULE.preparer.sha256_bytes(MODULE.preparer.canonical_json_bytes(receipt))
        receipt_path = _write_private(self.source_output / "preparation-receipt.json", _canonical(receipt))
        MODULE.provenance._preparation_receipt(receipt_path)
        return receipt_path, release_bundle

    def _make_adopted_images(self) -> tuple[Path, Path, list[dict[str, object]]]:
        configs = {
            "app": b'{"architecture":"amd64","config":"adopted-app"}',
            "postgres": b'{"architecture":"amd64","config":"adopted-postgres"}',
            "redis": b'{"architecture":"amd64","config":"adopted-redis"}',
        }
        ids = {name: _image_id(config) for name, config in configs.items()}
        values = [
            {
                "archive_tag": self._tag(ids["postgres"]),
                "image_id": ids["postgres"],
                "repo_digests": [],
                "repo_tags": ["postgres:15-alpine"],
                "size_bytes": 202,
                "source_ref": "postgres:15-alpine",
            },
            {
                "archive_tag": self._tag(ids["redis"]),
                "image_id": ids["redis"],
                "repo_digests": [],
                "repo_tags": ["redis:7-alpine"],
                "size_bytes": 303,
                "source_ref": "redis:7-alpine",
            },
            {
                "archive_tag": self._tag(ids["app"]),
                "image_id": ids["app"],
                "repo_digests": [],
                "repo_tags": ["trading_bot_base:rollback-2c08"],
                "size_bytes": 404,
                "source_ref": "trading_bot_base:rollback-2c08",
            },
        ]
        bundle = self.adopted_input / "images.tar"
        _write_docker_archive(
            bundle,
            [
                (configs["app"], [self._tag(ids["app"])]),
                (configs["postgres"], [self._tag(ids["postgres"])]),
                (configs["redis"], [self._tag(ids["redis"])]),
            ],
        )
        bundle_sha, bundle_bytes = _digest(bundle)
        archive = {
            "bytes": bundle_bytes,
            "image_ids": sorted(ids.values()),
            "repo_tags": sorted(str(value["archive_tag"]) for value in values),
            "sha256": bundle_sha,
        }
        manifest = _write_private(
            self.adopted_input / "image-manifest.json",
            _canonical(
                {
                    "archive": archive,
                    "campaign_id": CAMPAIGN_ID,
                    "image_set_sha256": MODULE.preparer.sha256_bytes(MODULE.preparer.canonical_json_bytes(values)),
                    "images": values,
                    "release_sha": RELEASE_SHA,
                    "schema": MODULE.preparer.IMAGE_MANIFEST_SCHEMA,
                    "status": "prepared",
                }
            ),
        )
        return bundle, manifest, values

    def _compose(self, *, preparation_id: str = "adopted-preparation-0123456789abcdef", **kwargs: object) -> dict[str, object]:
        return MODULE.compose_adopted_preparation(
            application_preparation_receipt=self.source_receipt,
            adopted_image_bundle=self.adopted_bundle,
            adopted_image_manifest=self.adopted_manifest,
            output_parent=self.output_parent,
            preparation_id=preparation_id,
            now=PREPARED_AT,
            **kwargs,
        )

    def test_creates_exact_four_file_candidate_accepted_by_existing_verifier(self) -> None:
        result = self._compose()

        candidate = Path(str(result["output_directory"]))
        self.assertEqual("prepared", result["status"])
        self.assertEqual(
            {"release.bundle", "images.tar", "image-manifest.json", "preparation-receipt.json"},
            {path.name for path in candidate.iterdir()},
        )
        prepared = MODULE.provenance._preparation_receipt(candidate / "preparation-receipt.json")
        self.assertEqual(RELEASE_SHA, prepared.release_sha)
        self.assertEqual(3, len(prepared.images))
        self.assertEqual(_digest(self.source_release), _digest(candidate / "release.bundle"))
        self.assertEqual(_digest(self.adopted_bundle), _digest(candidate / "images.tar"))
        self.assertEqual(_digest(self.adopted_manifest), _digest(candidate / "image-manifest.json"))
        self.assertEqual(
            [
                f"image-bundle={candidate / 'images.tar'}",
                f"image-manifest={candidate / 'image-manifest.json'}",
                f"release-bundle={candidate / 'release.bundle'}",
            ],
            result["stage_publish"]["artifact"],
        )
        self.assertFalse(result["object_storage_action"])
        self.assertFalse(result["ssh_action"])
        self.assertFalse(result["docker_command_invoked"])

    def test_rejects_insufficient_capacity_before_creating_candidate(self) -> None:
        preparation_id = "adopted-low-capacity-0123456789abcdef"
        required = (
            self.source_release.stat().st_size
            + self.adopted_bundle.stat().st_size
            + self.adopted_manifest.stat().st_size
            + MODULE.RECEIPT_RESERVE_BYTES
            + MODULE.preparer.CAPACITY_MARGIN_BYTES
        )
        with self.assertRaisesRegex(MODULE.AdoptedPreparationCompositionError, "insufficient free space"):
            self._compose(
                preparation_id=preparation_id,
                # Source and destination may be on this same filesystem, so
                # the admission must reserve every copied output byte rather
                # than treating an existing source file as free capacity.
                disk_usage=lambda _path: SimpleNamespace(free=required - 1),
            )
        candidate = MODULE.preparer.candidate_directory(
            self.output_parent,
            release_sha=RELEASE_SHA,
            preparation_id=preparation_id,
        )
        self.assertFalse(candidate.exists())

    def test_rejects_writable_ancestor_and_symlinked_output_parent(self) -> None:
        unsafe_parent = self.root / "writable-nonsticky-parent"
        unsafe_parent.mkdir(mode=0o777)
        unsafe_parent.chmod(0o777)
        root_only_child = unsafe_parent / "root-only-child"
        root_only_child.mkdir(mode=0o700)
        with self.assertRaisesRegex(MODULE.AdoptedPreparationCompositionError, "writable non-sticky ancestor"):
            MODULE._require_root_only_directory(root_only_child, field="test root-only child")

        symlinked_parent = self.root / "output-parent-link"
        symlinked_parent.symlink_to(self.output_parent, target_is_directory=True)
        with self.assertRaisesRegex(MODULE.AdoptedPreparationCompositionError, "root-only non-symlink"):
            MODULE._require_root_only_directory(symlinked_parent, field="test output parent")

    def test_source_mutation_is_detected_and_no_receipt_is_written(self) -> None:
        preparation_id = "adopted-source-race-0123456789abcdef"
        original_copy = MODULE._copy_exact_file
        mutated = False

        def copy_then_mutate(**kwargs: object):
            nonlocal mutated
            result = original_copy(**kwargs)
            source = kwargs["source"]
            if not mutated and isinstance(source, MODULE.InputFile) and source.path == self.source_release:
                self.source_release.write_bytes(self.source_release.read_bytes() + b"source mutation")
                self.source_release.chmod(0o600)
                mutated = True
            return result

        with mock.patch.object(MODULE, "_copy_exact_file", side_effect=copy_then_mutate):
            with self.assertRaisesRegex(MODULE.AdoptedPreparationCompositionError, "changed from its verified input binding"):
                self._compose(preparation_id=preparation_id)
        self.assertTrue(mutated)
        candidate = MODULE.preparer.candidate_directory(
            self.output_parent,
            release_sha=RELEASE_SHA,
            preparation_id=preparation_id,
        )
        self.assertTrue(candidate.is_dir())
        self.assertFalse((candidate / "preparation-receipt.json").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
