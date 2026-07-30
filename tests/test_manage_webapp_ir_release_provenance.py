"""Focused tests for separate immutable WA-IR application/control roots."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/manage_webapp_ir_release_provenance.py"
SPEC = importlib.util.spec_from_file_location("manage_webapp_ir_release_provenance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def digest(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout.strip()


def make_repository(root: Path, *, filename: str, content: str) -> tuple[Path, str, str]:
    repository = root / filename.replace(".", "-")
    repository.mkdir(mode=0o755)
    git(repository, "init", "--quiet")
    git(repository, "config", "user.name", "test")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / filename).write_text(content, encoding="utf-8")
    git(repository, "add", filename)
    git(repository, "commit", "--quiet", "-m", "fixture")
    commit = git(repository, "rev-parse", "HEAD")
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    return repository, commit, tree


class ReleaseProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="wa-ir-release-provenance-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.application_parent = self.root / "application-releases"
        self.control_parent = self.root / "control-releases"
        self.preparation_parent = self.root / "prepared-application"
        self.output_parent = self.root / "prepared-control"
        self.receipt_parent = self.root / "receipts"
        self.dispatcher_directory = self.root / "fixed-control-dispatcher"
        self.dispatcher_path = self.dispatcher_directory / "manage_webapp_ir_release_provenance.py"
        for directory, mode in (
            (self.application_parent, 0o755),
            (self.control_parent, 0o755),
            (self.preparation_parent, 0o700),
            (self.output_parent, 0o700),
            (self.receipt_parent, 0o700),
        ):
            directory.mkdir()
            directory.chmod(mode)
        self.application_repository, self.application_sha, self.application_tree = make_repository(
            self.root,
            filename="application.txt",
            content="legacy application exact source\n",
        )
        self.control_repository, self.control_sha, self.control_tree = make_repository(
            self.root,
            filename="control.txt",
            content="fenced control tooling source\n",
        )
        dispatcher_source = self.control_repository / MODULE.CONTROL_DISPATCHER_SOURCE
        dispatcher_source.parent.mkdir()
        dispatcher_source.write_text("# verified control dispatcher fixture\n", encoding="utf-8")
        git(self.control_repository, "add", str(MODULE.CONTROL_DISPATCHER_SOURCE))
        git(self.control_repository, "commit", "--quiet", "-m", "add dispatcher")
        self.control_sha = git(self.control_repository, "rev-parse", "HEAD")
        self.control_tree = git(self.control_repository, "rev-parse", "HEAD^{tree}")
        self.app_image_config = b'{"architecture":"amd64","config":"release"}'
        self.app_image_id = "sha256:" + hashlib.sha256(self.app_image_config).hexdigest()
        self.app_repo_digest = "trading_bot_base_iran@sha256:" + "b" * 64
        self.campaign_id = "current-2c08-standby-campaign"
        self.expected_alembic_revision = "f2c7d8e9a0b1"
        self.canonical_release_tree_sha256 = "e" * 64
        self.source_provenance_input = self.root / "source-provenance-input.json"
        write_private(
            self.source_provenance_input,
            MODULE.canonical_json_bytes(
                {
                    "schema": MODULE.WEBAPP_FI_SOURCE_PROVENANCE_INPUT_SCHEMA,
                    "campaign_id": self.campaign_id,
                    "proofs": {
                        name: {"fixture_proof": name}
                        for name in MODULE.WEBAPP_FI_SOURCE_PROOF_NAMES
                    },
                }
            )
            + b"\n",
        )

    @contextlib.contextmanager
    def patched_contract(self):
        with (
            mock.patch.multiple(
                MODULE,
                APPLICATION_RELEASE_PARENT=self.application_parent,
                CONTROL_RELEASE_PARENT=self.control_parent,
                CONTROL_DISPATCHER_DIRECTORY=self.dispatcher_directory,
                TRUSTED_DISPATCHER_PATH=self.dispatcher_path,
                LEGACY_APPLICATION_RELEASE_SHA=self.application_sha,
            ),
            mock.patch.object(
                MODULE,
                "_verify_webapp_fi_source_composite",
                return_value={"status": "verified"},
            ),
            mock.patch.object(
                MODULE,
                "_verification_anchor_from_adoption",
                return_value="2026-07-30T12:00:00Z",
            ),
        ):
            yield

    def _artifact_descriptor(self, name: str, path: Path, bindings: dict[str, str]) -> dict:
        sha256, size = digest(path)
        return {
            "bindings": dict(sorted(bindings.items())),
            "bytes": size,
            "name": name,
            "path": str(path),
            "sha256": sha256,
        }

    def make_application_preparation(self, *, app_repo_digests: list[str] | None = None) -> Path:
        """Make the exact root-only receipt contract emitted by the preparer."""

        # The preparer uses a new, private output directory.  Use the same file
        # names and receipt schema without invoking Docker in this unit test.
        output = self.preparation_parent / ("prepared-" + self.application_sha)
        output.mkdir(mode=0o700)
        output.chmod(0o700)
        release_bundle = output / "release.bundle"
        MODULE._create_git_bundle(self.application_repository, self.application_sha, release_bundle)
        images = [
            {
                "archive_tag": MODULE.image_contract.canonical_archive_tag(
                    campaign_id=self.campaign_id,
                    release_sha=self.application_sha,
                    image_id=self.app_image_id,
                ),
                "image_id": self.app_image_id,
                "repo_digests": [self.app_repo_digest] if app_repo_digests is None else app_repo_digests,
                "repo_tags": ["trading_bot_base_iran:rollback-2c08"],
                "size_bytes": 123,
                "source_ref": "trading_bot_base_iran:rollback-2c08",
            }
        ]
        image_bundle = output / "images.tar"
        config_name = self.app_image_id.removeprefix("sha256:") + ".json"
        docker_manifest = MODULE.canonical_json_bytes(
            [
                {
                    "Config": config_name,
                    "Layers": [],
                    "RepoTags": [images[0]["archive_tag"]],
                }
            ]
        )
        with tarfile.open(image_bundle, "w", format=tarfile.USTAR_FORMAT) as archive:
            config_info = tarfile.TarInfo(config_name)
            config_info.mode = 0o600
            config_info.size = len(self.app_image_config)
            archive.addfile(config_info, io.BytesIO(self.app_image_config))
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.mode = 0o600
            manifest_info.size = len(docker_manifest)
            archive.addfile(manifest_info, io.BytesIO(docker_manifest))
        image_bundle.chmod(0o600)
        archive_sha, archive_bytes = digest(image_bundle)
        archive = {
            "bytes": archive_bytes,
            "image_ids": [self.app_image_id],
            "repo_tags": [images[0]["archive_tag"]],
            "sha256": archive_sha,
        }
        image_set_sha = hashlib.sha256(MODULE.canonical_json_bytes(images)).hexdigest()
        image_ids_sha = hashlib.sha256(MODULE.canonical_json_bytes([self.app_image_id])).hexdigest()
        image_manifest = output / "image-manifest.json"
        write_private(
            image_manifest,
            MODULE.canonical_json_bytes(
                {
                    "archive": archive,
                    "campaign_id": self.campaign_id,
                    "image_set_sha256": image_set_sha,
                    "images": images,
                    "release_sha": self.application_sha,
                    "schema": MODULE.IMAGE_MANIFEST_SCHEMA,
                    "status": "prepared",
                }
            )
            + b"\n",
        )
        release_sha, release_bytes = digest(release_bundle)
        manifest_sha, manifest_bytes = digest(image_manifest)
        artifacts = [
            self._artifact_descriptor(
                MODULE.IMAGE_BUNDLE_ARTIFACT,
                image_bundle,
                {
                    "artifact_sha256": archive_sha,
                    "image_count": "1",
                    "image_ids_sha256": image_ids_sha,
                    "image_manifest_sha256": manifest_sha,
                    "image_set_sha256": image_set_sha,
                    "release_sha": self.application_sha,
                },
            ),
            self._artifact_descriptor(
                MODULE.IMAGE_MANIFEST_ARTIFACT,
                image_manifest,
                {
                    "artifact_sha256": manifest_sha,
                    "image_set_sha256": image_set_sha,
                    "release_sha": self.application_sha,
                },
            ),
            self._artifact_descriptor(
                MODULE.APPLICATION_BUNDLE_ARTIFACT,
                release_bundle,
                {
                    "artifact_sha256": release_sha,
                    "git_commit": self.application_sha,
                    "git_tree": self.application_tree,
                    "release_sha": self.application_sha,
                },
            ),
        ]
        stage_publish = {
            "artifact": [
                name + "=" + str(path)
                for name, path in sorted(
                    {
                        MODULE.IMAGE_BUNDLE_ARTIFACT: image_bundle,
                        MODULE.IMAGE_MANIFEST_ARTIFACT: image_manifest,
                        MODULE.APPLICATION_BUNDLE_ARTIFACT: release_bundle,
                    }.items()
                )
            ],
            "artifact_binding": [
                artifact["name"] + "=" + key + "=" + value
                for artifact in sorted(artifacts, key=lambda item: item["name"])
                for key, value in sorted(artifact["bindings"].items())
            ],
        }
        receipt = {
            "artifacts": artifacts,
            "campaign_id": self.campaign_id,
            "capacity_preflight": {
                "image_logical_bytes": 123,
                "output_free_bytes": 1_000_000,
                "output_required_bytes": 500_000,
                "workspace_free_bytes": 1_000_000,
                "workspace_required_bytes": 500_000,
            },
            "image_archive": archive,
            "images": images,
            "output_directory": str(output),
            "preparation_id": "20260730T120000Z-0123456789abcdef01234567",
            "release_bundle": {
                "bytes": release_bytes,
                "git_commit": self.application_sha,
                "git_tree": self.application_tree,
                "sha256": release_sha,
            },
            "release_sha": self.application_sha,
            "schema": MODULE.PREPARATION_SCHEMA,
            "stage_publish": stage_publish,
            "status": "prepared",
            "prepared_at": "2026-07-30T12:00:00Z",
        }
        receipt["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(receipt)).hexdigest()
        receipt_path = output / "preparation-receipt.json"
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")
        return receipt_path

    def build(self) -> dict:
        preparation = self.make_application_preparation()
        with self.patched_contract():
            return MODULE.build_control_artifacts(
                application_preparation_receipt=preparation,
                control_repository=self.control_repository,
                control_release_sha=self.control_sha,
                output_directory=self.output_parent / "control-bound",
                app_image_id=self.app_image_id,
                expected_alembic_revision=self.expected_alembic_revision,
                canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                webapp_fi_source_provenance_input=self.source_provenance_input,
                app_repo_digest=self.app_repo_digest,
            )

    @staticmethod
    def _stage_inputs(prepared: dict) -> tuple[dict[str, Path], dict[str, dict[str, str]]]:
        paths: dict[str, Path] = {}
        bindings: dict[str, dict[str, str]] = {}
        for value in prepared["stage_publish"]["artifact"]:
            name, raw_path = value.split("=", 1)
            paths[name] = Path(raw_path)
        for value in prepared["stage_publish"]["artifact_binding"]:
            name, key, item = value.split("=", 2)
            bindings.setdefault(name, {})[key] = item
        return paths, bindings

    def make_candidate(
        self,
        prepared: dict,
        *,
        invalid_application_bundle: bool = False,
        mismatched_image_binding: bool = False,
        source_site: str = "webapp_fi",
        destination_site: str = "webapp_ir",
    ) -> Path:
        paths, bindings = self._stage_inputs(prepared)
        candidate = self.root / "candidate"
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        for name, source in paths.items():
            destination = candidate / name
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
        if invalid_application_bundle:
            target = candidate / MODULE.APPLICATION_BUNDLE_ARTIFACT
            write_private(target, b"this is not a Git bundle\n")
            replacement_sha, _ = digest(target)
            bindings[MODULE.APPLICATION_BUNDLE_ARTIFACT]["artifact_sha256"] = replacement_sha
        if mismatched_image_binding:
            bindings[MODULE.IMAGE_BUNDLE_ARTIFACT]["image_set_sha256"] = "f" * 64
        descriptors: list[dict] = []
        for name in sorted(paths):
            artifact = candidate / name
            sha256, size = digest(artifact)
            descriptors.append(
                {
                    "name": name,
                    "sha256": sha256,
                    "bytes": size,
                    "object_key": "campaign/release/" + name + ".age",
                    "version_id": "version-" + name,
                    "ciphertext_sha256": "c" * 64,
                    "ciphertext_bytes": size + 32,
                    "bindings": dict(sorted(bindings[name].items())),
                }
            )
        receipt = {
            "schema": MODULE.STAGE_RECEIPT_SCHEMA,
            "status": "staged",
            "source_site": source_site,
            "destination_site": destination_site,
            "release_sha": self.application_sha,
            "bundle_id": "20260730T120000Z-0123456789abcdef01234567",
            "published_at": "2026-07-30T12:00:00Z",
            "staged_at": "2026-07-30T12:00:01Z",
            "candidate_directory": str(candidate),
            "manifest": {
                "object_key": "campaign/release/manifest.json.age",
                "version_id": "manifest-version",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 123,
            },
            "artifacts": descriptors,
        }
        receipt["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(receipt)).hexdigest()
        receipt_path = candidate / "stage-receipt.json"
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")
        return receipt_path

    def make_bootstrap_receipt(
        self,
        *,
        control_commit: str | None = None,
        control_tree: str | None = None,
    ) -> Path:
        """Create the URL-free receiver receipt consumed by provenance install."""

        receipt_control_commit = control_commit or self.control_sha
        receipt_control_tree = control_tree or self.control_tree
        bootstrap_id = "20260730T110000Z-0123456789abcdef01234567"
        candidate = self.root / f"received-{receipt_control_commit}-{bootstrap_id}"
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        consumer_config = {
            "schema": MODULE.CONSUMER_CONFIG_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "three-site-private",
            "prefix": "campaign-current/artifacts",
            "age_binary": "/usr/bin/age",
            "age_identity_file": "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey",
            "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
            "source_site": "webapp_fi",
            "source_signing_public_key_base64": base64.b64encode(b"\x00" * 32).decode("ascii"),
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(b"\x01" * 32).decode("ascii"),
            "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(b"\x02" * 32).decode("ascii"),
            "maximum_artifact_bytes": 1024 * 1024,
        }
        members = {
            "scripts/manage_webapp_ir_artifact_stage.py": b"# stage consumer fixture\n",
            "scripts/manage_webapp_ir_snapshot.py": b"# snapshot fixture\n",
            "scripts/manage_webapp_ir_release_provenance.py": b"# release provenance fixture\n",
            "scripts/prepare_webapp_ir_artifact_bundle.py": b"# image archive verifier fixture\n",
            "scripts/verify_webapp_fi_source_provenance.py": b"# pure source verifier fixture\n",
            "scripts/install_webapp_ir_static_assets.py": b"# detached static installer fixture\n",
            "core/standby_snapshot_capacity.py": b"# capacity fixture\n",
            "scripts/webapp_ir_image_archive_contract.py": b"# image archive fixture\n",
            "config/consumer.json": MODULE.canonical_json_bytes(consumer_config) + b"\n",
        }
        for name, raw in members.items():
            member_path = candidate / name
            member_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            member_path.parent.chmod(0o700)
            write_private(member_path, raw)
        member_hashes = {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in members.items()
        }
        payload = {
            "schema": MODULE.BOOTSTRAP_RECEIPT_SCHEMA,
            "status": "received",
            "received_at": "2026-07-30T12:00:00Z",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "control_commit": receipt_control_commit,
            "control_tree": receipt_control_tree,
            "bootstrap_id": bootstrap_id,
            "candidate_directory": str(candidate),
            "files": member_hashes,
            "bootstrap": {
                "object_key": "campaign/bootstrap/v1/webapp_fi/webapp_ir/stage-consumer-bootstrap.tar.age",
                "version_id": "version-bootstrap",
                "ciphertext_sha256": "f" * 64,
                "ciphertext_bytes": 2048,
                "plaintext_sha256": "0" * 64,
                "plaintext_bytes": 1024,
                "package_manifest_sha256": "1" * 64,
                "consumer_config_sha256": member_hashes["config/consumer.json"],
                "preparation_receipt_sha256": "3" * 64,
            },
        }
        payload["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(payload)).hexdigest()
        path = candidate / MODULE.BOOTSTRAP_RECEIPT_NAME
        write_private(path, MODULE.canonical_json_bytes(payload) + b"\n")
        return path

    def rewrite_bootstrap_consumer_config(self, receipt_path: Path, payload: bytes) -> None:
        """Model a self-consistent received receipt for a negative config case."""

        config_path = receipt_path.parent / MODULE.BOOTSTRAP_CONSUMER_CONFIG
        write_private(config_path, payload)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        config_sha256 = hashlib.sha256(payload).hexdigest()
        receipt["files"][MODULE.BOOTSTRAP_CONSUMER_CONFIG] = config_sha256
        receipt["bootstrap"]["consumer_config_sha256"] = config_sha256
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        receipt["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(unsigned)).hexdigest()
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")

    def test_build_and_install_bind_the_preparer_artifacts_to_a_distinct_control_root(self) -> None:
        prepared = self.build()
        paths, _ = self._stage_inputs(prepared)
        self.assertEqual(
            set(paths),
            {
                MODULE.APPLICATION_BUNDLE_ARTIFACT,
                MODULE.IMAGE_BUNDLE_ARTIFACT,
                MODULE.IMAGE_MANIFEST_ARTIFACT,
                MODULE.CONTROL_BUNDLE_ARTIFACT,
                MODULE.PROVENANCE_ARTIFACT,
            },
        )
        self.assertEqual(git(self.application_repository, "status", "--porcelain"), "")
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"

        with self.patched_contract():
            result = MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )

        application_root = self.application_parent / self.application_sha
        control_root = self.control_parent / self.control_sha
        self.assertEqual(git(application_root, "rev-parse", "HEAD"), self.application_sha)
        self.assertEqual(git(application_root, "rev-parse", "HEAD^{tree}"), self.application_tree)
        self.assertEqual(git(control_root, "rev-parse", "HEAD"), self.control_sha)
        self.assertEqual(git(control_root, "rev-parse", "HEAD^{tree}"), self.control_tree)
        self.assertNotEqual(application_root, control_root)
        self.assertEqual(result["application"]["release_root"], str(application_root))
        self.assertEqual(result["control"]["release_root"], str(control_root))
        self.assertEqual(result["dispatcher"]["path"], str(self.dispatcher_path))
        self.assertEqual(result["dispatcher"]["control_release_sha"], self.control_sha)
        self.assertTrue(self.dispatcher_path.is_file())
        self.assertEqual(
            self.dispatcher_path.read_bytes(),
            (control_root / MODULE.CONTROL_DISPATCHER_SOURCE).read_bytes(),
        )
        self.assertEqual(result["runtime_images"]["app_image_id"], self.app_image_id)
        self.assertEqual(result["runtime_images"]["app_repo_digest"], self.app_repo_digest)
        self.assertEqual(result["schema"], MODULE.INSTALL_RECEIPT_SCHEMA)
        self.assertEqual(
            result["bootstrap_provenance"]["webapp_fi_source_attestation_public_key_base64"],
            base64.b64encode(b"\x01" * 32).decode("ascii"),
        )
        self.assertEqual(
            result["bootstrap_provenance"]["webapp_fi_controller_authorization_public_key_base64"],
            base64.b64encode(b"\x02" * 32).decode("ascii"),
        )
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        with self.patched_contract():
            installed = MODULE.load_installed_release_receipt(receipt_path)
        self.assertEqual(installed["application"]["release_sha"], self.application_sha)
        self.assertEqual(installed["control"]["release_sha"], self.control_sha)
        self.assertEqual(installed["dispatcher"]["sha256"], result["dispatcher"]["sha256"])
        self.assertEqual(installed["bootstrap_provenance"], result["bootstrap_provenance"])

    def test_build_and_install_bind_a_verified_app_image_without_a_repo_digest(self) -> None:
        preparation = self.make_application_preparation(app_repo_digests=[])
        with self.patched_contract():
            prepared = MODULE.build_control_artifacts(
                application_preparation_receipt=preparation,
                control_repository=self.control_repository,
                control_release_sha=self.control_sha,
                output_directory=self.output_parent / "control-no-repo-digest",
                app_image_id=self.app_image_id,
                expected_alembic_revision=self.expected_alembic_revision,
                canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                webapp_fi_source_provenance_input=self.source_provenance_input,
            )
        self.assertEqual(prepared["runtime_images"]["app_image_id"], self.app_image_id)
        self.assertNotIn("app_repo_digest", prepared["runtime_images"])

        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots-no-repo-digest.json"
        with self.patched_contract():
            installed = MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
            loaded = MODULE.load_installed_release_receipt(receipt_path)

        self.assertEqual(installed["runtime_images"]["app_image_id"], self.app_image_id)
        self.assertNotIn("app_repo_digest", installed["runtime_images"])
        self.assertNotIn("app_repo_digest", loaded["runtime_images"])

    def test_build_rejects_an_unverified_app_image_id_without_a_repo_digest(self) -> None:
        preparation = self.make_application_preparation(app_repo_digests=[])
        output = self.output_parent / "unverified-app-image"
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "absent from the prepared image manifest"):
                MODULE.build_control_artifacts(
                    application_preparation_receipt=preparation,
                    control_repository=self.control_repository,
                    control_release_sha=self.control_sha,
                    output_directory=output,
                    app_image_id="sha256:" + "c" * 64,
                    expected_alembic_revision=self.expected_alembic_revision,
                    canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                    webapp_fi_source_provenance_input=self.source_provenance_input,
                )
        self.assertFalse(output.exists())

    def test_build_requires_a_present_repo_digest_to_be_bound(self) -> None:
        preparation = self.make_application_preparation()
        output = self.output_parent / "missing-app-repo-digest"
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "repo digest that must be bound"):
                MODULE.build_control_artifacts(
                    application_preparation_receipt=preparation,
                    control_repository=self.control_repository,
                    control_release_sha=self.control_sha,
                    output_directory=output,
                    app_image_id=self.app_image_id,
                    expected_alembic_revision=self.expected_alembic_revision,
                    canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                    webapp_fi_source_provenance_input=self.source_provenance_input,
                )
        self.assertFalse(output.exists())

    def test_build_parser_allows_an_absent_app_repo_digest(self) -> None:
        parsed = MODULE.parse_args(
            [
                "build-control",
                "--application-preparation-receipt",
                "/root/preparation-receipt.json",
                "--control-repository",
                "/root/control",
                "--control-release-sha",
                self.control_sha,
                "--output-directory",
                "/root/output",
                "--app-image-id",
                self.app_image_id,
                "--expected-alembic-revision",
                self.expected_alembic_revision,
                "--canonical-release-tree-sha256",
                self.canonical_release_tree_sha256,
                "--webapp-fi-source-provenance-input",
                str(self.source_provenance_input),
            ]
        )
        self.assertIsNone(parsed.app_repo_digest)

    def test_runtime_contract_rejects_a_null_repo_digest_field(self) -> None:
        with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "runtime app_repo_digest is invalid"):
            MODULE._runtime_contract(
                {
                    "app_image_id": self.app_image_id,
                    "app_repo_digest": None,
                    "image_bundle_sha256": "a" * 64,
                    "image_manifest_sha256": "b" * 64,
                    "image_set_sha256": "c" * 64,
                    "image_ids_sha256": "d" * 64,
                    "image_count": 1,
                }
            )

    def test_install_rejects_an_arbitrary_non_git_application_bundle_without_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared, invalid_application_bundle=True)

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "staged application bundle does not match"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_a_valid_stage_with_a_control_identity_other_than_bootstrap(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt(
            control_commit="d" * 40,
            control_tree="e" * 40,
        )

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "does not match the validated bootstrap control identity"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_receipt_with_an_invalid_self_hash_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        payload = json.loads(bootstrap_receipt.read_text(encoding="utf-8"))
        payload["control_tree"] = "f" * 40
        write_private(bootstrap_receipt, MODULE.canonical_json_bytes(payload) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "bootstrap receive receipt hash is invalid"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_manifest_misclassified_as_a_payload_file(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        payload = json.loads(bootstrap_receipt.read_text(encoding="utf-8"))
        payload["files"]["bootstrap-package.json"] = "e" * 64
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(unsigned)).hexdigest()
        write_private(bootstrap_receipt, MODULE.canonical_json_bytes(payload) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "bootstrap receive receipt files are invalid"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_receipt_moved_outside_its_candidate(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        copied_receipt = self.receipt_parent / MODULE.BOOTSTRAP_RECEIPT_NAME
        shutil.copyfile(bootstrap_receipt, copied_receipt)
        copied_receipt.chmod(0o600)

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "must remain at its candidate path"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=copied_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_candidate_not_named_for_its_control_and_id(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        invalid_candidate = self.root / "received-not-receipt-bound"
        bootstrap_receipt.parent.rename(invalid_candidate)
        bootstrap_receipt = invalid_candidate / MODULE.BOOTSTRAP_RECEIPT_NAME
        payload = json.loads(bootstrap_receipt.read_text(encoding="utf-8"))
        payload["candidate_directory"] = str(invalid_candidate)
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(unsigned)).hexdigest()
        write_private(bootstrap_receipt, MODULE.canonical_json_bytes(payload) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "candidate_directory is not receipt-bound"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_receipt_with_a_mismatched_consumer_config_hash(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        payload = json.loads(bootstrap_receipt.read_text(encoding="utf-8"))
        payload["bootstrap"]["consumer_config_sha256"] = "e" * 64
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(unsigned)).hexdigest()
        write_private(bootstrap_receipt, MODULE.canonical_json_bytes(payload) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "consumer config hash is inconsistent"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())

    def test_install_rejects_a_bootstrap_source_verifier_changed_after_receive(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        verifier = bootstrap_receipt.parent / "scripts/verify_webapp_fi_source_provenance.py"
        write_private(verifier, b"# substituted source verifier\n")

        with self.patched_contract():
            with self.assertRaisesRegex(
                MODULE.ReleaseProvenanceError,
                "file hashes no longer match the received candidate",
            ):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_a_missing_received_consumer_config_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        (bootstrap_receipt.parent / MODULE.BOOTSTRAP_CONSUMER_CONFIG).unlink()

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "bootstrap member config/consumer.json does not exist"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_a_malformed_received_consumer_config_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        self.rewrite_bootstrap_consumer_config(bootstrap_receipt, b"{not-json}\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "bootstrap received consumer config is not valid JSON"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_an_old_received_consumer_config_schema_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        config_path = bootstrap_receipt.parent / MODULE.BOOTSTRAP_CONSUMER_CONFIG
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["schema"] = "gold-trade-wa-ir-artifact-stage-config-v2"
        self.rewrite_bootstrap_consumer_config(
            bootstrap_receipt,
            MODULE.canonical_json_bytes(config) + b"\n",
        )

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "not the exact v3 schema"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_a_malformed_received_provenance_pin_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        bootstrap_receipt = self.make_bootstrap_receipt()
        config_path = bootstrap_receipt.parent / MODULE.BOOTSTRAP_CONSUMER_CONFIG
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["webapp_fi_source_attestation_public_key_base64"] = "AQ=="
        self.rewrite_bootstrap_consumer_config(
            bootstrap_receipt,
            MODULE.canonical_json_bytes(config) + b"\n",
        )

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "source attestation public key is invalid"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=bootstrap_receipt,
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_a_stage_for_any_site_pair_other_than_webapp_fi_to_webapp_ir(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared, source_site="bot_fi")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "fixed webapp_fi to webapp_ir transfer"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_mismatched_image_binding_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared, mismatched_image_binding=True)

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "bindings do not match"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_rejects_an_image_archive_that_fails_structural_verification_before_creating_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)

        with self.patched_contract(), mock.patch.object(
            MODULE.artifact_preparer,
            "verify_docker_image_archive",
            side_effect=RuntimeError("synthetic archive verifier failure"),
        ):
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "staged image archive"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())

    def test_install_refuses_a_preexisting_fixed_dispatcher_without_touching_roots(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        self.dispatcher_directory.mkdir()
        self.dispatcher_directory.chmod(0o755)
        self.dispatcher_path.write_text("untrusted preexisting dispatcher\n", encoding="utf-8")
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "dispatcher directory must not already exist"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=self.receipt_parent / "release-roots.json",
                )

        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertEqual(self.dispatcher_path.read_text(encoding="utf-8"), "untrusted preexisting dispatcher\n")

    def test_build_rejects_a_non_git_control_source_without_creating_an_output(self) -> None:
        preparation = self.make_application_preparation()
        non_repository = self.root / "mutable-current"
        non_repository.mkdir(mode=0o755)
        (non_repository / "main.py").write_text("untracked runtime\n", encoding="utf-8")
        output = self.output_parent / "invalid"

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "not a Git worktree"):
                MODULE.build_control_artifacts(
                    application_preparation_receipt=preparation,
                    control_repository=non_repository,
                    control_release_sha=self.control_sha,
                    output_directory=output,
                    app_image_id=self.app_image_id,
                    expected_alembic_revision=self.expected_alembic_revision,
                    canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                    webapp_fi_source_provenance_input=self.source_provenance_input,
                    app_repo_digest=self.app_repo_digest,
                )

        self.assertFalse(output.exists())

    def test_failed_control_candidate_is_retained_without_a_success_provenance_artifact(self) -> None:
        preparation = self.make_application_preparation()
        output = self.output_parent / "failed-control"
        with self.patched_contract(), mock.patch.object(
            MODULE,
            "_create_only_json",
            side_effect=MODULE.ReleaseProvenanceError("synthetic provenance write failure"),
        ):
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "synthetic provenance write failure"):
                MODULE.build_control_artifacts(
                    application_preparation_receipt=preparation,
                    control_repository=self.control_repository,
                    control_release_sha=self.control_sha,
                    output_directory=output,
                    app_image_id=self.app_image_id,
                    expected_alembic_revision=self.expected_alembic_revision,
                    canonical_release_tree_sha256=self.canonical_release_tree_sha256,
                    webapp_fi_source_provenance_input=self.source_provenance_input,
                    app_repo_digest=self.app_repo_digest,
                )
        self.assertTrue((output / MODULE.CONTROL_BUNDLE_ARTIFACT).is_file())
        self.assertFalse((output / MODULE.PROVENANCE_ARTIFACT).exists())

    def test_install_cleans_only_new_roots_when_receipt_creation_fails(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract(), mock.patch.object(
            MODULE,
            "_create_only_json",
            side_effect=MODULE.ReleaseProvenanceError("synthetic receipt write failure"),
        ):
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "synthetic receipt write failure"):
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=receipt_path,
                )
        self.assertFalse((self.application_parent / self.application_sha).exists())
        self.assertFalse((self.control_parent / self.control_sha).exists())
        self.assertFalse(self.dispatcher_directory.exists())
        self.assertFalse(receipt_path.exists())

    def test_receipt_load_rejects_a_mutated_fixed_dispatcher(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract():
            MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
        self.dispatcher_path.write_text("tampered dispatcher\n", encoding="utf-8")
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "dispatcher hash does not match"):
                MODULE.load_installed_release_receipt(receipt_path)

    def test_receipt_load_rejects_a_mutated_installed_git_root(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract():
            MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
        (self.application_parent / self.application_sha / "application.txt").write_text("tampered\n", encoding="utf-8")
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "installed application release root does not match"):
                MODULE.load_installed_release_receipt(receipt_path)

    def test_receipt_load_rejects_a_tampered_bootstrap_provenance_pin(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract():
            MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["bootstrap_provenance"]["webapp_fi_source_attestation_public_key_base64"] = (
            base64.b64encode(b"\x03" * 32).decode("ascii")
        )
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "receipt hash is invalid"):
                MODULE.load_installed_release_receipt(receipt_path)

    def test_receipt_load_fails_closed_for_the_legacy_v1_schema(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract():
            MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["schema"] = "gold-trade-wa-ir-release-provenance-install-receipt-v1"
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "schema is unsupported"):
                MODULE.load_installed_release_receipt(receipt_path)

    def test_receipt_load_rejects_a_v2_receipt_missing_bootstrap_provenance(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        with self.patched_contract():
            MODULE.install_release_roots(
                stage_receipt_path=stage_receipt,
                bootstrap_receipt_path=self.make_bootstrap_receipt(),
                receipt_path=receipt_path,
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("bootstrap_provenance")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        receipt["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(unsigned)).hexdigest()
        write_private(receipt_path, MODULE.canonical_json_bytes(receipt) + b"\n")

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "fields are invalid"):
                MODULE.load_installed_release_receipt(receipt_path)

    def test_install_keeps_git_code_readable_when_the_invoking_umask_is_private(self) -> None:
        prepared = self.build()
        stage_receipt = self.make_candidate(prepared)
        receipt_path = self.receipt_parent / "release-roots.json"
        prior_umask = os.umask(0o077)
        try:
            with self.patched_contract():
                MODULE.install_release_roots(
                    stage_receipt_path=stage_receipt,
                    bootstrap_receipt_path=self.make_bootstrap_receipt(),
                    receipt_path=receipt_path,
                )
        finally:
            os.umask(prior_umask)
        application_root = self.application_parent / self.application_sha
        self.assertEqual(stat.S_IMODE(application_root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((application_root / "application.txt").stat().st_mode), 0o644)

    def test_fixed_dispatcher_rejects_a_stale_env_root_before_opening_target_code(self) -> None:
        receipt = self.receipt_parent / "release-roots.json"
        config = self.receipt_parent / "writer.json"
        write_private(receipt, b"fixture\n")
        write_private(config, b"{}\n")
        bound_root = self.control_parent / self.control_sha
        stale_root = self.root / "stale-control" / self.control_sha
        for root in (bound_root, stale_root):
            (root / "scripts").mkdir(parents=True)
            root.chmod(0o755)
            (root / "scripts").chmod(0o755)
            (root / "scripts" / "production_writer_lease_agent.py").write_text(
                "# fixture\n", encoding="utf-8"
            )
        installed = {
            "application": {
                "release_sha": self.application_sha,
                "release_root": str(self.application_parent / self.application_sha),
            },
            "control": {
                "release_sha": self.control_sha,
                "release_root": str(bound_root),
            },
        }
        with (
            mock.patch.object(MODULE, "TRUSTED_DISPATCHER_PATH", SCRIPT.resolve()),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=installed),
            mock.patch.object(MODULE.os, "execve") as execve,
        ):
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "does not bind the expected control release root"):
                MODULE.exec_receipt_bound_control(
                    receipt_path=receipt,
                    control_release_root=stale_root,
                    control_release_sha=self.control_sha,
                    target="lease-guard",
                    config_path=config,
                )

        execve.assert_not_called()

    def test_fixed_dispatcher_execs_only_a_receipt_bound_fixed_target(self) -> None:
        receipt = self.receipt_parent / "release-roots.json"
        config = self.receipt_parent / "writer.json"
        write_private(receipt, b"fixture\n")
        write_private(config, b"{}\n")
        bound_root = self.control_parent / self.control_sha
        script = bound_root / "scripts" / "production_writer_lease_agent.py"
        script.parent.mkdir(parents=True)
        bound_root.chmod(0o755)
        script.parent.chmod(0o755)
        script.write_text("# fixture\n", encoding="utf-8")
        installed = {
            "application": {
                "release_sha": self.application_sha,
                "release_root": str(self.application_parent / self.application_sha),
            },
            "control": {
                "release_sha": self.control_sha,
                "release_root": str(bound_root),
            },
        }
        with (
            mock.patch.object(MODULE, "TRUSTED_DISPATCHER_PATH", SCRIPT.resolve()),
            mock.patch.object(MODULE, "load_installed_release_receipt", return_value=installed),
            mock.patch.object(MODULE.os, "execve", side_effect=OSError("fixture")) as execve,
        ):
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "cannot exec receipt-bound control target"):
                MODULE.exec_receipt_bound_control(
                    receipt_path=receipt,
                    control_release_root=bound_root,
                    control_release_sha=self.control_sha,
                    target="lease-guard",
                    config_path=config,
                )

        command = execve.call_args.args[1]
        self.assertEqual(
            command,
            [
                str(MODULE.PYTHON_BINARY.resolve()),
                "-I",
                "-B",
                str(script),
                "--config",
                str(config),
                "guard",
            ],
        )
        self.assertEqual(
            execve.call_args.args[2],
            {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
        )

    def test_implementation_has_no_current_s3_docker_or_remote_control_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("boto3", "docker API", "ssh ", "scp ", "urlopen(", "tarfile", "make_archive"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
