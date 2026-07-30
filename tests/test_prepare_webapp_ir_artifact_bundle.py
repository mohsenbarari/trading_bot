"""Focused tests for local-only WA-IR release and image artifact preparation."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "prepare_webapp_ir_artifact_bundle.py"
SPEC = importlib.util.spec_from_file_location("prepare_webapp_ir_artifact_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)

STAGE_MODULE_PATH = ROOT / "scripts" / "manage_webapp_ir_artifact_stage.py"
STAGE_SPEC = importlib.util.spec_from_file_location("manage_webapp_ir_artifact_stage_for_prepare_test", STAGE_MODULE_PATH)
assert STAGE_SPEC and STAGE_SPEC.loader
stage = importlib.util.module_from_spec(STAGE_SPEC)
sys.modules[STAGE_SPEC.name] = stage
STAGE_SPEC.loader.exec_module(stage)


RELEASE_SHA = "a" * 40
TREE_SHA = "b" * 40
PREPARATION_ID = "20260730T120000Z-0123456789abcdef01234567"


def image_id(config: bytes) -> str:
    return "sha256:" + hashlib.sha256(config).hexdigest()


class FakeRunner:
    def __init__(self, source_repo: Path, image_values: dict[str, dict[str, Any]]) -> None:
        self.source_repo = source_repo
        self.image_values = image_values
        self.calls: list[list[str]] = []
        self.missing_git = False
        self.archive_drop_tags = False
        self.archive_config_overrides: dict[str, bytes] = {}

    def __call__(
        self,
        arguments: Sequence[str],
        _cwd: Path | None,
        _timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        args = [str(item) for item in arguments]
        self.calls.append(args)
        if args[0] == "/usr/bin/git":
            return self._git(args)
        if args[0] == "/usr/bin/docker":
            return self._docker(args)
        raise AssertionError(f"unexpected executable: {args}")

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[1:4] == ["-C", str(self.source_repo), "rev-parse"] and args[4:] == ["--absolute-git-dir"]:
            if self.missing_git:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="not a git repository")
            return subprocess.CompletedProcess(args, 0, stdout=str(self.source_repo / ".git") + "\n", stderr="")
        if args[1:5] == ["clone", "--bare", "--shared", "--no-tags"]:
            cloned = Path(args[-1])
            cloned.mkdir(mode=0o700)
            cloned.chmod(0o700)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "update-ref" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "bundle" in args and "create" in args:
            output_path = Path(args[-2])
            self._write_private(output_path, b"# v2 git bundle\nexact release bytes\n")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[1:3] == ["bundle", "list-heads"]:
            return subprocess.CompletedProcess(args, 0, stdout=RELEASE_SHA + " HEAD\n", stderr="")
        if "bundle" in args and "verify" in args:
            return subprocess.CompletedProcess(args, 0, stdout="The bundle is okay\n", stderr="")
        if args[1:3] == ["init", "--bare"]:
            verifier = Path(args[3])
            verifier.mkdir(mode=0o700)
            verifier.chmod(0o700)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "bundle" in args and "unbundle" in args:
            return subprocess.CompletedProcess(args, 0, stdout=RELEASE_SHA + " HEAD\n", stderr="")
        if "rev-parse" in args and "--verify" in args:
            target = args[-1]
            if target == RELEASE_SHA + "^{commit}":
                return subprocess.CompletedProcess(args, 0, stdout=RELEASE_SHA + "\n", stderr="")
            if target == RELEASE_SHA + "^{tree}":
                return subprocess.CompletedProcess(args, 0, stdout=TREE_SHA + "\n", stderr="")
        raise AssertionError(f"unexpected git command: {args}")

    def _docker(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[1:5] == ["image", "inspect", "--format", "{{json .}}"]:
            reference = args[5]
            inspected = {key: value for key, value in self.image_values[reference].items() if key != "_config"}
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(inspected) + "\n", stderr="")
        if args[1:3] == ["image", "save"]:
            output = Path(args[args.index("--output") + 1])
            image_references = args[args.index("--output") + 2 :]
            values = [
                value
                for value in self.image_values.values()
                if value["Id"] in image_references or any(tag in image_references for tag in value["RepoTags"])
            ]
            if len(values) != len(image_references):
                raise AssertionError("docker save must receive only previously inspected image references")
            self._write_docker_archive(output, values)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker command: {args}")

    def _write_docker_archive(self, output: Path, values: list[dict[str, Any]]) -> None:
        with tarfile.open(output, "w") as archive:
            manifest: list[dict[str, Any]] = []
            for value in values:
                config = self.archive_config_overrides.get(value["Id"], value["_config"])
                config_name = value["Id"].removeprefix("sha256:") + ".json"
                config_info = tarfile.TarInfo(config_name)
                config_info.size = len(config)
                archive.addfile(config_info, io.BytesIO(config))
                manifest.append(
                    {
                        "Config": config_name,
                        "Layers": [],
                        "RepoTags": [] if self.archive_drop_tags else list(value["RepoTags"]),
                    }
                )
            payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(payload)
            archive.addfile(manifest_info, io.BytesIO(payload))
        output.chmod(0o600)

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        path.write_bytes(content)
        path.chmod(0o600)


class ArtifactPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="prepare-wa-ir-artifact-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.source_repo = self.root / "source"
        self.workspace = self.root / "workspace"
        self.output_root = self.root / "output"
        for path in (self.source_repo, self.workspace, self.output_root):
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        (self.source_repo / ".git").mkdir(mode=0o700)
        (self.source_repo / ".git").chmod(0o700)
        self.config_one = b'{"architecture":"amd64","config":"one"}'
        self.config_two = b'{"architecture":"amd64","config":"two"}'
        self.first_id = image_id(self.config_one)
        self.second_id = image_id(self.config_two)
        self.first_ref = "registry.example/trading-bot:release-one"
        self.second_ref = "registry.example/postgres:release-two"
        self.images = {
            self.first_ref: {
                "Id": self.first_id,
                "RepoDigests": ["registry.example/trading-bot@" + self.first_id],
                "RepoTags": [self.first_ref],
                "Size": 1024,
                "_config": self.config_one,
            },
            self.second_ref: {
                "Id": self.second_id,
                "RepoDigests": ["registry.example/postgres@" + self.second_id],
                "RepoTags": [self.second_ref],
                "Size": 2048,
                "_config": self.config_two,
            },
        }
        self.runner = FakeRunner(self.source_repo, self.images)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self, **overrides: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "source_repo": self.source_repo,
            "release_sha": RELEASE_SHA,
            "workspace": self.workspace,
            "output_root": self.output_root,
            "image_specifications": prepare.parse_image_specifications(
                [self.first_ref + "=" + self.first_id, self.second_ref + "=" + self.second_id]
            ),
            "git_binary": Path("/usr/bin/git"),
            "docker_binary": Path("/usr/bin/docker"),
            "preparation_id": PREPARATION_ID,
            "maximum_artifact_bytes": 1024 * 1024,
            "runner": self.runner,
        }
        arguments.update(overrides)
        return prepare.prepare_artifacts(**arguments)

    def test_prepares_exact_git_and_immutable_image_archive_with_stage_compatible_bindings(self) -> None:
        receipt = self.prepare()
        target = prepare.candidate_directory(
            self.output_root,
            release_sha=RELEASE_SHA,
            preparation_id=PREPARATION_ID,
        )

        self.assertEqual(prepare.PREPARATION_SCHEMA, receipt["schema"])
        self.assertEqual("prepared", receipt["status"])
        self.assertEqual(str(target), receipt["output_directory"])
        self.assertEqual(RELEASE_SHA, receipt["release_bundle"]["git_commit"])
        self.assertEqual(TREE_SHA, receipt["release_bundle"]["git_tree"])
        self.assertEqual(
            prepare.sha256_bytes(
                prepare.canonical_json_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            ),
            receipt["receipt_sha256"],
        )
        self.assertEqual(0o700, stat.S_IMODE(target.stat().st_mode))
        for name in ("release.bundle", "images.tar", "image-manifest.json", "preparation-receipt.json"):
            self.assertEqual(0o600, stat.S_IMODE((target / name).stat().st_mode), name)

        staged_artifacts = stage.apply_artifact_bindings(
            stage.parse_artifact_specifications(receipt["stage_publish"]["artifact"]),
            receipt["stage_publish"]["artifact_binding"],
        )
        self.assertEqual(["image-bundle", "image-manifest", "release-bundle"], [item.name for item in staged_artifacts])
        release_bindings = next(item.bindings for item in staged_artifacts if item.name == "release-bundle")
        self.assertEqual(RELEASE_SHA, release_bindings["git_commit"])
        self.assertEqual(TREE_SHA, release_bindings["git_tree"])
        image_bindings = next(item.bindings for item in staged_artifacts if item.name == "image-bundle")
        self.assertEqual("2", image_bindings["image_count"])
        self.assertIn("image_manifest_sha256", image_bindings)
        manifest = json.loads((target / "image-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(PREPARATION_ID, receipt["campaign_id"])
        self.assertEqual(PREPARATION_ID, manifest["campaign_id"])
        self.assertEqual(sorted([self.first_ref, self.second_ref]), [item["source_ref"] for item in manifest["images"]])
        self.assertEqual(sorted([self.first_id, self.second_id]), manifest["archive"]["image_ids"])
        expected_archive_tags = {
            prepare.image_contract.canonical_archive_tag(
                campaign_id=PREPARATION_ID,
                release_sha=RELEASE_SHA,
                image_id=self.first_id,
            ),
            prepare.image_contract.canonical_archive_tag(
                campaign_id=PREPARATION_ID,
                release_sha=RELEASE_SHA,
                image_id=self.second_id,
            ),
        }
        self.assertEqual(expected_archive_tags, set(manifest["archive"]["repo_tags"]))
        self.assertEqual(expected_archive_tags, {item["archive_tag"] for item in manifest["images"]})
        with tarfile.open(target / "images.tar", "r") as archive:
            self.assertNotIn("repositories", archive.getnames())
            archive_manifest = json.loads(archive.extractfile("manifest.json").read().decode("utf-8"))
        self.assertEqual(expected_archive_tags, {tag for entry in archive_manifest for tag in entry["RepoTags"]})
        self.assertTrue(all(self.first_ref not in entry["RepoTags"] for entry in archive_manifest))
        self.assertTrue(all(self.second_ref not in entry["RepoTags"] for entry in archive_manifest))

        save_call = next(call for call in self.runner.calls if call[:3] == ["/usr/bin/docker", "image", "save"])
        self.assertEqual(sorted(self.images), save_call[save_call.index("--output") + 2 :])
        forbidden = {"build", "pull", "load", "run", "start", "stop", "compose"}
        self.assertTrue(all(not (set(call) & forbidden) for call in self.runner.calls))

    def test_rejects_image_tag_that_resolves_to_a_different_immutable_id_before_save(self) -> None:
        self.images[self.first_ref]["Id"] = self.second_id

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "expected immutable image ID"):
            self.prepare()

        self.assertFalse(any(call[:3] == ["/usr/bin/docker", "image", "save"] for call in self.runner.calls))
        self.assertEqual([], list(self.output_root.iterdir()))

    def test_rejects_raw_archive_when_the_verified_source_tag_is_missing_before_a_final_archive_exists(self) -> None:
        self.runner.archive_drop_tags = True

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "does not retain every verified source image tag"):
            self.prepare()

        target = prepare.candidate_directory(
            self.output_root,
            release_sha=RELEASE_SHA,
            preparation_id=PREPARATION_ID,
        )
        self.assertTrue(target.is_dir())
        self.assertTrue((target / "release.bundle").is_file())
        self.assertFalse((target / "images.tar").exists())
        self.assertFalse((target / "preparation-receipt.json").exists())

    def test_rejects_raw_archive_when_an_image_changes_between_inspection_and_save_before_a_final_archive_exists(self) -> None:
        self.runner.archive_config_overrides[self.first_id] = b'{"architecture":"amd64","config":"repointed"}'

        with self.assertRaisesRegex(
            prepare.ArtifactPreparationError,
            "config path is not bound to its image ID",
        ):
            self.prepare()

        target = prepare.candidate_directory(
            self.output_root,
            release_sha=RELEASE_SHA,
            preparation_id=PREPARATION_ID,
        )
        self.assertTrue(target.is_dir())
        self.assertTrue((target / "release.bundle").is_file())
        self.assertFalse((target / "images.tar").exists())
        self.assertFalse((target / "preparation-receipt.json").exists())

    def test_inspection_rejects_a_config_member_name_not_bound_to_its_payload_hash(self) -> None:
        archive_path = self.root / "wrong-config-name.tar"
        wrong_name = "f" * 64 + ".json"
        manifest = [{"Config": wrong_name, "RepoTags": [self.first_ref], "Layers": []}]
        with tarfile.open(archive_path, "w") as archive:
            config_info = tarfile.TarInfo(wrong_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        archive_path.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "config path is not bound"):
            prepare.inspect_docker_image_archive(path=archive_path)

    def test_inspection_rejects_unlisted_or_nonregular_layer_paths(self) -> None:
        archive_path = self.root / "missing-layer.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        manifest = [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": ["missing/layer.tar"]}]
        with tarfile.open(archive_path, "w") as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        archive_path.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "layer path is malformed"):
            prepare.inspect_docker_image_archive(path=archive_path)

    def test_inspection_rejects_unreferenced_config_members(self) -> None:
        archive_path = self.root / "unreferenced-config.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        extra_config = b'{"architecture":"amd64","config":"extra"}'
        extra_name = image_id(extra_config).removeprefix("sha256:") + ".json"
        manifest = [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": []}]
        with tarfile.open(archive_path, "w") as archive:
            for name, payload in ((config_name, self.config_one), (extra_name, extra_config)):
                config_info = tarfile.TarInfo(name)
                config_info.size = len(payload)
                archive.addfile(config_info, io.BytesIO(payload))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        archive_path.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "unreferenced config"):
            prepare.inspect_docker_image_archive(path=archive_path)

    def test_inspection_rejects_control_metadata_as_a_layer(self) -> None:
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        second_name = self.second_id.removeprefix("sha256:") + ".json"
        for layer_name in ("manifest.json", second_name):
            with self.subTest(layer_name=layer_name), tempfile.TemporaryDirectory(prefix="reserved-layer-") as raw:
                archive_path = Path(raw) / "reserved-layer.tar"
                manifest = [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": [layer_name]}]
                with tarfile.open(archive_path, "w") as archive:
                    config_info = tarfile.TarInfo(config_name)
                    config_info.size = len(self.config_one)
                    archive.addfile(config_info, io.BytesIO(self.config_one))
                    if layer_name == second_name:
                        second_info = tarfile.TarInfo(second_name)
                        second_info.size = len(self.config_two)
                        archive.addfile(second_info, io.BytesIO(self.config_two))
                    manifest_bytes = json.dumps(manifest).encode("utf-8")
                    manifest_info = tarfile.TarInfo("manifest.json")
                    manifest_info.size = len(manifest_bytes)
                    archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
                archive_path.chmod(0o600)

                with self.assertRaisesRegex(prepare.ArtifactPreparationError, "layer path is malformed"):
                    prepare.inspect_docker_image_archive(path=archive_path)

    def test_inspection_rejects_pax_extended_headers_before_their_payload_is_processed(self) -> None:
        archive_path = self.root / "pax-header.tar"
        with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
            payload = b"x"
            info = tarfile.TarInfo("p" * 101)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        archive_path.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "cannot be validated"):
            prepare.inspect_docker_image_archive(path=archive_path)

    def test_rewrite_preserves_a_standard_layer_payload_and_drops_repositories(self) -> None:
        raw_path = self.root / "raw-layered.tar"
        output_path = self.root / "isolated-layered.tar"
        layer_id = "d" * 64
        layer_name = layer_id + "/layer.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        manifest = [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": [layer_name]}]
        with tarfile.open(raw_path, "w") as archive:
            for name, payload in (
                (layer_name, b"layer payload"),
                (layer_id + "/VERSION", b"1.0"),
                (layer_id + "/json", b"{}"),
                (config_name, self.config_one),
                ("repositories", b"{}"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        raw_path.chmod(0o600)
        image = prepare.bind_isolated_archive_tags(
            campaign_id="layered-image-proof",
            release_sha=RELEASE_SHA,
            images=[
                prepare.PreparedImage(
                    source_ref=self.first_ref,
                    image_id=self.first_id,
                    repo_digests=(),
                    repo_tags=(self.first_ref,),
                    size_bytes=1,
                )
            ],
        )
        raw_sha256, raw_bytes = prepare.sha256_file(raw_path)

        prepare.rewrite_docker_image_archive_tags(
            raw_path=raw_path,
            output_path=output_path,
            images=image,
            expected_raw_sha256=raw_sha256,
            expected_raw_bytes=raw_bytes,
        )

        prepare.verify_docker_image_archive(path=output_path, images=image, require_isolated_tags=True)
        with tarfile.open(output_path, "r") as archive:
            self.assertNotIn("repositories", archive.getnames())
            self.assertEqual(b"layer payload", archive.extractfile(layer_name).read())
            self.assertIn(layer_id + "/VERSION", archive.getnames())
            self.assertIn(layer_id + "/json", archive.getnames())

    def test_rewrite_requires_the_exact_signed_raw_archive_digest(self) -> None:
        raw_path = self.root / "raw-digest.tar"
        output_path = self.root / "raw-digest-output.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        manifest = [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": []}]
        with tarfile.open(raw_path, "w") as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        raw_path.chmod(0o600)
        image = prepare.bind_isolated_archive_tags(
            campaign_id="digest-image-proof",
            release_sha=RELEASE_SHA,
            images=[
                prepare.PreparedImage(
                    source_ref=self.first_ref,
                    image_id=self.first_id,
                    repo_digests=(),
                    repo_tags=(self.first_ref,),
                    size_bytes=1,
                )
            ],
        )

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "signed raw archive binding"):
            prepare.rewrite_docker_image_archive_tags(
                raw_path=raw_path,
                output_path=output_path,
                images=image,
                expected_raw_sha256="0" * 64,
                expected_raw_bytes=raw_path.stat().st_size,
            )
        self.assertFalse(output_path.exists())

    def test_signed_image_id_export_may_be_untagged_but_only_in_explicit_mode(self) -> None:
        raw_path = self.root / "raw-untagged.tar"
        output_path = self.root / "isolated-untagged.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        manifest = [{"Config": config_name, "RepoTags": None, "Layers": []}]
        with tarfile.open(raw_path, "w") as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        raw_path.chmod(0o600)
        image = prepare.bind_isolated_archive_tags(
            campaign_id="untagged-image-proof",
            release_sha=RELEASE_SHA,
            images=[
                prepare.PreparedImage(
                    source_ref=self.first_ref,
                    image_id=self.first_id,
                    repo_digests=(),
                    repo_tags=(self.first_ref,),
                    size_bytes=1,
                )
            ],
        )
        raw_sha256, raw_bytes = prepare.sha256_file(raw_path)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "does not retain every verified source image tag"):
            prepare.verify_docker_image_archive(path=raw_path, images=image)
        digest_reference_image = [
            prepare.dataclasses.replace(
                image[0],
                source_ref="registry.example/trading-bot@" + self.first_id,
            )
        ]
        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "does not retain every verified source image tag"):
            prepare.verify_docker_image_archive(path=raw_path, images=digest_reference_image)
        prepare.rewrite_docker_image_archive_tags(
            raw_path=raw_path,
            output_path=output_path,
            images=image,
            expected_raw_sha256=raw_sha256,
            expected_raw_bytes=raw_bytes,
            require_source_tags=False,
        )
        isolated = prepare.verify_docker_image_archive(
            path=output_path,
            images=image,
            require_isolated_tags=True,
        )
        self.assertEqual([image[0].archive_tag], isolated["repo_tags"])

    def test_inspection_rejects_oci_control_files_before_tag_rewrite(self) -> None:
        archive_path = self.root / "oci-layout.tar"
        with tarfile.open(archive_path, "w") as archive:
            for name, payload in (
                ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
                ("index.json", b'{"schemaVersion":2,"manifests":[]}'),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        archive_path.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "unsupported OCI layout"):
            prepare.inspect_docker_image_archive(path=archive_path)

    def test_inspection_rejects_duplicate_manifest_keys_and_member_count_exhaustion(self) -> None:
        duplicate_path = self.root / "duplicate-manifest-key.tar"
        config_name = self.first_id.removeprefix("sha256:") + ".json"
        duplicate_manifest = (
            b'[{"Config":"' + config_name.encode("ascii") + b'","Config":"' + config_name.encode("ascii")
            + b'","RepoTags":["' + self.first_ref.encode("ascii") + b'"],"Layers":[]}]'
        )
        with tarfile.open(duplicate_path, "w") as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(duplicate_manifest)
            archive.addfile(manifest_info, io.BytesIO(duplicate_manifest))
        duplicate_path.chmod(0o600)
        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "manifest is invalid"):
            prepare.inspect_docker_image_archive(path=duplicate_path)

        crowded_path = self.root / "crowded.tar"
        valid_manifest = json.dumps(
            [{"Config": config_name, "RepoTags": [self.first_ref], "Layers": []}]
        ).encode("utf-8")
        with tarfile.open(crowded_path, "w") as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.size = len(self.config_one)
            archive.addfile(config_info, io.BytesIO(self.config_one))
            archive.addfile(tarfile.TarInfo("extra"), io.BytesIO())
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(valid_manifest)
            archive.addfile(manifest_info, io.BytesIO(valid_manifest))
        crowded_path.chmod(0o600)
        with mock.patch.object(prepare, "MAX_DOCKER_ARCHIVE_MEMBERS", 2):
            with self.assertRaisesRegex(prepare.ArtifactPreparationError, "too many members"):
                prepare.inspect_docker_image_archive(path=crowded_path)

    def test_final_archive_remaps_every_shared_source_tag_to_the_isolated_namespace(self) -> None:
        shared_tag = "postgres:15-alpine"
        self.images[self.second_ref]["RepoTags"].append(shared_tag)

        receipt = self.prepare(campaign_id="current-2c08-standby-campaign")
        target = Path(receipt["output_directory"])
        manifest = json.loads((target / "image-manifest.json").read_text(encoding="utf-8"))
        self.assertIn(shared_tag, next(item for item in manifest["images"] if item["image_id"] == self.second_id)["repo_tags"])
        self.assertNotIn(shared_tag, manifest["archive"]["repo_tags"])
        with tarfile.open(target / "images.tar", "r") as archive:
            archive_manifest = json.loads(archive.extractfile("manifest.json").read().decode("utf-8"))
        self.assertNotIn(shared_tag, {tag for entry in archive_manifest for tag in entry["RepoTags"]})

    def test_archive_tag_is_deterministically_bound_to_campaign_release_and_image_id(self) -> None:
        tag = prepare.image_contract.canonical_archive_tag(
            campaign_id="campaign-a",
            release_sha=RELEASE_SHA,
            image_id=self.first_id,
        )
        self.assertEqual(
            tag,
            prepare.image_contract.canonical_archive_tag(
                campaign_id="campaign-a",
                release_sha=RELEASE_SHA,
                image_id=self.first_id,
            ),
        )
        self.assertNotEqual(
            tag,
            prepare.image_contract.canonical_archive_tag(
                campaign_id="campaign-b",
                release_sha=RELEASE_SHA,
                image_id=self.first_id,
            ),
        )
        self.assertNotEqual(
            tag,
            prepare.image_contract.canonical_archive_tag(
                campaign_id="campaign-a",
                release_sha="c" * 40,
                image_id=self.first_id,
            ),
        )
        self.assertNotEqual(
            tag,
            prepare.image_contract.canonical_archive_tag(
                campaign_id="campaign-a",
                release_sha=RELEASE_SHA,
                image_id=self.second_id,
            ),
        )

    def test_rejects_non_git_runtime_before_docker_inspection(self) -> None:
        self.runner.missing_git = True

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "Git source repository inspection failed"):
            self.prepare()

        self.assertFalse(any(call[:3] == ["/usr/bin/docker", "image", "inspect"] for call in self.runner.calls))
        self.assertEqual([], list(self.output_root.iterdir()))

    def test_refuses_existing_detached_output_without_touching_it(self) -> None:
        target = prepare.candidate_directory(
            self.output_root,
            release_sha=RELEASE_SHA,
            preparation_id=PREPARATION_ID,
        )
        target.mkdir(mode=0o700)
        marker = target / "keep"
        marker.write_text("keep", encoding="utf-8")
        marker.chmod(0o600)

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "overwrite an existing detached"):
            self.prepare()

        self.assertEqual("keep", marker.read_text(encoding="utf-8"))

    def test_rejects_untagged_and_unsafe_image_inputs(self) -> None:
        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "explicit image tag"):
            prepare.parse_image_specifications(["registry.example/trading-bot=" + self.first_id])
        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "unsafe image reference"):
            prepare.parse_image_specifications(["-bad:tag=" + self.first_id])

    def test_capacity_preflight_rejects_shared_small_filesystem_before_artifact_writes(self) -> None:
        image = prepare.PreparedImage(
            source_ref=self.first_ref,
            image_id=self.first_id,
            repo_digests=(),
            repo_tags=(self.first_ref,),
            size_bytes=1024,
        )

        with self.assertRaisesRegex(prepare.ArtifactPreparationError, "insufficient free space"):
            prepare.preflight_artifact_capacity(
                workspace=self.workspace,
                output_root=self.output_root,
                maximum_artifact_bytes=1024,
                images=[image],
                disk_usage=lambda _path: SimpleNamespace(free=1024),
                stat_path=lambda _path: SimpleNamespace(st_dev=1),
            )

    def test_default_runner_scrubs_git_and_remote_docker_context_environment(self) -> None:
        completed = subprocess.CompletedProcess(["ignored"], 0, stdout="", stderr="")
        with mock.patch.dict(
            prepare.os.environ,
            {"GIT_DIR": "/attacker", "DOCKER_HOST": "tcp://remote.example:2376", "DOCKER_CONTEXT": "remote"},
            clear=False,
        ), mock.patch.object(prepare.subprocess, "run", return_value=completed) as run:
            prepare.default_command_runner(["/usr/bin/git", "--version"], None, 1)
            git_environment = run.call_args.kwargs["env"]
            self.assertNotIn("GIT_DIR", git_environment)
            self.assertEqual("/nonexistent", git_environment["HOME"])
            prepare.default_command_runner(["/usr/bin/docker", "version"], None, 1)
            docker_environment = run.call_args.kwargs["env"]
            self.assertNotIn("DOCKER_HOST", docker_environment)
            self.assertEqual("default", docker_environment["DOCKER_CONTEXT"])

    def test_gitless_contract_remains_explicitly_unimplemented(self) -> None:
        contract = prepare.gitless_release_tree_contract()
        self.assertEqual("not_implemented_fail_closed", contract["status"])
        self.assertIn("source-signed-release-tree-descriptor.json", contract["required_root_only_inputs"])


if __name__ == "__main__":
    unittest.main()
