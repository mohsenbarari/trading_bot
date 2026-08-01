"""Focused tests for separate immutable WA-IR application/control roots."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


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
        self.app_image_id = "sha256:" + "a" * 64
        self.app_repo_digest = "trading_bot_base_iran@sha256:" + "b" * 64
        self.campaign_id = "current-2c08-standby-campaign"
        self.fi_attestation_private_key = Ed25519PrivateKey.generate()
        self.fi_attestation_public_key = self.fi_attestation_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.fi_attestation_public_key_path = self.root / "fi-attestation-public.key"
        write_private(self.fi_attestation_public_key_path, self.fi_attestation_public_key)
        self._attestation_serial = 0

    def patched_contract(self):
        return mock.patch.multiple(
            MODULE,
            APPLICATION_RELEASE_PARENT=self.application_parent,
            CONTROL_RELEASE_PARENT=self.control_parent,
            CONTROL_DISPATCHER_DIRECTORY=self.dispatcher_directory,
            TRUSTED_DISPATCHER_PATH=self.dispatcher_path,
            LEGACY_APPLICATION_RELEASE_SHA=self.application_sha,
        )

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
        image_bundle = output / "images.tar"
        write_private(image_bundle, b"verified-image-archive-fixture\n")
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

    def make_source_proof(
        self,
        *,
        image_id: str | None = None,
        repo_digest: str | None | object = ...,
        release_sha: str | None = None,
        release_tree: str | None = None,
    ) -> tuple[Path, Path, Path]:
        """Make the exact rich FI proof consumed by provenance v2."""

        self._attestation_serial += 1
        serial = self._attestation_serial
        selected_image_id = image_id or self.app_image_id
        selected_repo_digest = self.app_repo_digest if repo_digest is ... else repo_digest
        selected_release = release_sha or self.application_sha
        selected_tree = release_tree or self.application_tree
        image_reference = "trading_bot_base_iran:rollback-2c08"
        public_key_base64 = base64.b64encode(self.fi_attestation_public_key).decode("ascii")
        controller_key_base64 = base64.b64encode(b"c" * 32).decode("ascii")
        key_id = "ed25519-sha256:" + hashlib.sha256(self.fi_attestation_public_key).hexdigest()
        archive_sha256 = "e" * 64
        archive_bytes = 123
        descriptor_sha256 = "d" * 64
        projection_relatives = (
            "api",
            "bot",
            "core",
            "src",
            "models",
            "migrations",
            "scripts",
            "main.py",
            "schemas.py",
            "trading_settings.json",
        )
        projections = {
            relative: [
                {
                    "path": relative if "." in relative else relative + "/fixture.py",
                    "sha256": hashlib.sha256(relative.encode("ascii")).hexdigest(),
                    "bytes": 1,
                    "mode": "100644",
                }
            ]
            for relative in projection_relatives
        }
        projection = {
            "runtime_source_root": "/srv/trading-bot/current",
            "release_sha": selected_release,
            "git_tree": selected_tree,
            "descriptor_sha256": descriptor_sha256,
            "projections": projections,
            "projection_sha256": hashlib.sha256(MODULE.canonical_json_bytes(projections)).hexdigest(),
        }
        static_record = {
            "descriptor_sha256": "7" * 64,
            "artifact": {
                "object_key": "campaign/source/static-assets.age",
                "version_id": "static-version-1",
                "ciphertext_sha256": "8" * 64,
                "ciphertext_bytes": 101,
                "plaintext_sha256": "9" * 64,
                "plaintext_bytes": 99,
            },
            "files_sha256": "a" * 64,
            "file_count": 1,
            "source_kind": "deterministic_2c08_dist_manifest",
        }
        def projection_mounts(*, include_static_assets: bool) -> list[dict[str, object]]:
            relatives = list(projection_relatives)
            if include_static_assets:
                relatives.append("mini_app_dist")
            return sorted(
                [
                    {
                        "type": "bind",
                        "source": "/srv/trading-bot/current/" + relative,
                        "destination": "/app/" + relative,
                        "read_only": True,
                    }
                    for relative in relatives
                ],
                key=lambda item: (str(item["destination"]), str(item["type"]), str(item["source"])),
            )

        def container(*, name: str, image_id: str, image_ref: str, mounts: list[dict[str, object]]) -> dict[str, object]:
            return {
                "name": name,
                "container_id": hashlib.sha256(name.encode("ascii")).hexdigest(),
                "image_id": image_id,
                "image_reference": image_ref,
                "mounts": mounts,
            }

        application_container = container(
            name="webapp-fi-app",
            image_id=selected_image_id,
            image_ref=image_reference,
            mounts=projection_mounts(include_static_assets=True),
        )
        sync_container = container(
            name="webapp-fi-sync",
            image_id=selected_image_id,
            image_ref=image_reference,
            mounts=projection_mounts(include_static_assets=False),
        )
        active_image = {
            "image_id": selected_image_id,
            "image_reference": image_reference,
            "repo_tags": [image_reference],
            "repo_digests": [] if selected_repo_digest is None else [selected_repo_digest],
        }
        role_unsigned: dict[str, object] = {
            "schema": MODULE.WEBAPP_FI_SOURCE_ROLE_ATTESTATION_SCHEMA,
            "status": "attested",
            "attested_at": "2026-07-30T12:00:00Z",
            "campaign_id": self.campaign_id,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "package_id": f"source-package-{serial}",
            "application": {
                "release_sha": selected_release,
                "expected_alembic_revision": "a1b2c3d4e5f6",
            },
            "tooling": {"control_commit": self.control_sha, "control_tree": self.control_tree},
            "source_adoption_install_receipt_sha256": "1" * 64,
            "source_adoption_delivery": {
                "object_key": "campaign/source/adoption.age",
                "version_id": "source-adoption-version-1",
                "ciphertext_sha256": "2" * 64,
                "ciphertext_bytes": 102,
                "plaintext_sha256": "3" * 64,
                "plaintext_bytes": 100,
                "delivery_envelope_sha256": "4" * 64,
                "controller_public_key_base64": controller_key_base64,
            },
            "canonical_release_tree_sha256": descriptor_sha256,
            "application_release_tree": selected_tree,
            "source_signer_enrollment": {
                "receipt_sha256": "5" * 64,
                "certificate_sha256": "6" * 64,
                "fi_ssh_host_public_key_sha256": "7" * 64,
                "controller_public_key_base64": controller_key_base64,
                "source_signing_public_key_base64": public_key_base64,
            },
            "runtime_projection": {"before": projection, "after": dict(projection)},
            "static_assets_proof": {
                "before": static_record,
                "after": dict(static_record),
                "proof_is_not_static_payload": True,
                "promotion_requires_verified_immutable_age_object": True,
            },
            "containers": {
                "database": container(
                    name="webapp-fi-db",
                    image_id="sha256:" + "0" * 64,
                    image_ref="postgres:16",
                    mounts=[],
                ),
                "application": application_container,
                "sync_worker": sync_container,
            },
            "active_application_image": active_image,
            "schema_observation": {"observed_alembic_revision": "a1b2c3d4e5f6", "capture_role_verified_read_only": True},
            "race_check": {
                "runtime_projection_unchanged": True,
                "static_assets_unchanged": True,
                "application_container_unchanged": True,
                "sync_worker_container_unchanged": True,
                "database_container_unchanged": True,
                "active_image_unchanged": True,
                "schema_unchanged": True,
            },
            "snapshot_transport": {
                "payload_path": "private_versioned_object_storage_age_only",
                "one_off_publication_only": True,
                "direct_webapp_fi_to_webapp_ir_transfer": False,
                "automatic_deletion": False,
            },
            "source_signing_public_key_base64": public_key_base64,
            "source_signing_key_id": key_id,
        }
        role_signature = self.fi_attestation_private_key.sign(
            MODULE.WEBAPP_FI_SOURCE_ROLE_ATTESTATION_DOMAIN + MODULE.canonical_json_bytes(role_unsigned)
        )
        role = {
            **role_unsigned,
            "source_signature": {
                "algorithm": MODULE.WEBAPP_FI_SOURCE_SIGNING_ALGORITHM,
                "signature_base64": base64.b64encode(role_signature).decode("ascii"),
            },
        }
        role_path = self.root / f"fi-source-role-attestation-{serial}.json"
        write_private(role_path, MODULE.canonical_json_bytes(role) + b"\n")
        role_sha256 = hashlib.sha256(role_path.read_bytes()).hexdigest()
        export_unsigned: dict[str, object] = {
            "schema": MODULE.WEBAPP_FI_IMAGE_EXPORT_RECEIPT_SCHEMA,
            "status": "exported",
            "exported_at": "2026-07-30T12:00:01Z",
            "export_id": f"image-export-{serial}",
            "campaign_id": self.campaign_id,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": role_unsigned["application"],
            "application_release_tree": selected_tree,
            "tooling": role_unsigned["tooling"],
            "canonical_release_tree_sha256": descriptor_sha256,
            "source_role_attestation_sha256": role_sha256,
            "image": {
                "image_id": selected_image_id,
                "image_reference": image_reference,
                "archive_sha256": archive_sha256,
                "archive_bytes": archive_bytes,
                "docker_manifest_sha256": "b" * 64,
                "docker_config_sha256": selected_image_id.removeprefix("sha256:"),
                "layer_count": 1,
                "repo_tags": [image_reference],
            },
            "pre_export_runtime": {
                "application": application_container,
                "sync_worker": sync_container,
                "active_image": active_image,
            },
            "post_export_runtime": {
                "application": application_container,
                "sync_worker": sync_container,
                "active_image": active_image,
            },
            "image_archive_does_not_prove_bind_mounted_runtime": True,
            "archive_consumption": {
                "docker_load_prohibited": True,
                "fi_local_archive_verification_before_age_encryption": True,
                "controller_read_back_verification_after_age_encryption": True,
                "raw_repo_tags_are_not_authorization": True,
            },
            "object_storage_export_required": {
                "transport": "private_versioned_age_only",
                "create_only": True,
                "read_back_same_version_id": True,
                "direct_webapp_fi_to_webapp_ir_transfer": False,
            },
            "source_signing_public_key_base64": public_key_base64,
            "source_signing_key_id": key_id,
        }
        export_signature = self.fi_attestation_private_key.sign(
            MODULE.WEBAPP_FI_IMAGE_EXPORT_RECEIPT_DOMAIN + MODULE.canonical_json_bytes(export_unsigned)
        )
        image_export = {
            **export_unsigned,
            "source_signature": {
                "algorithm": MODULE.WEBAPP_FI_SOURCE_SIGNING_ALGORITHM,
                "signature_base64": base64.b64encode(export_signature).decode("ascii"),
            },
        }
        export_path = self.root / f"fi-image-export-receipt-{serial}.json"
        write_private(export_path, MODULE.canonical_json_bytes(image_export) + b"\n")
        export_sha256 = hashlib.sha256(export_path.read_bytes()).hexdigest()
        adoption = {
            "schema": MODULE.WEBAPP_FI_CONTROLLER_IMAGE_ADOPTION_RECEIPT_SCHEMA,
            "status": "adopted",
            "adopted_at": "2026-07-30T12:00:02Z",
            "campaign_id": self.campaign_id,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {"release_sha": selected_release, "release_tree": selected_tree},
            "source_role_attestation_sha256": role_sha256,
            "image_export_receipt_sha256": export_sha256,
            "source_signing_key_id": key_id,
            "image": {"image_id": selected_image_id, "image_reference": image_reference},
            "object": {
                "object_key": "campaign/returned/image-export.age",
                "version_id": f"returned-image-version-{serial}",
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": 145,
                "plaintext_sha256": archive_sha256,
                "plaintext_bytes": archive_bytes,
            },
            "read_back_verified": True,
            "decrypted_verified": True,
        }
        adoption_path = self.root / f"fi-controller-image-adoption-{serial}.json"
        write_private(adoption_path, MODULE.canonical_json_bytes(adoption) + b"\n")
        return role_path, export_path, adoption_path

    def source_proof_arguments(self, **kwargs: object) -> dict[str, Path]:
        role, image_export, adoption = self.make_source_proof(**kwargs)
        return {
            "webapp_fi_source_role_attestation": role,
            "webapp_fi_image_export_receipt": image_export,
            "webapp_fi_controller_image_adoption_receipt": adoption,
            "webapp_fi_source_attestation_public_key_file": self.fi_attestation_public_key_path,
        }

    def resign_fi_record(self, path: Path, *, domain: bytes, mutate) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("source_signature")
        mutate(payload)
        signature = self.fi_attestation_private_key.sign(
            domain + MODULE.canonical_json_bytes(payload)
        )
        payload["source_signature"] = {
            "algorithm": MODULE.WEBAPP_FI_SOURCE_SIGNING_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
        write_private(path, MODULE.canonical_json_bytes(payload) + b"\n")

    def build_with_source_proof(
        self,
        *,
        preparation: Path,
        output_directory: Path,
        source_proof: dict[str, Path],
    ) -> dict:
        return MODULE.build_control_artifacts(
            application_preparation_receipt=preparation,
            control_repository=self.control_repository,
            control_release_sha=self.control_sha,
            output_directory=output_directory,
            **source_proof,
        )

    def build(self) -> dict:
        preparation = self.make_application_preparation()
        with self.patched_contract():
            return self.build_with_source_proof(
                preparation=preparation,
                output_directory=self.output_parent / "control-bound",
                source_proof=self.source_proof_arguments(),
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
        config_directory = candidate / "config"
        config_directory.mkdir(mode=0o700)
        config_directory.chmod(0o700)
        consumer_config = {
            "schema": MODULE.ARTIFACT_STAGE_CONSUMER_CONFIG_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "three-site-private",
            "prefix": "campaign-current/artifacts",
            "age_binary": "/usr/bin/age",
            "age_identity_file": "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey",
            "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
            "source_site": "webapp_fi",
            "source_signing_public_key_base64": base64.b64encode(b"p" * 32).decode("ascii"),
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
                self.fi_attestation_public_key
            ).decode("ascii"),
            "maximum_artifact_bytes": 1024 * 1024,
        }
        consumer_config_raw = json.dumps(consumer_config, sort_keys=True).encode("utf-8")
        write_private(config_directory / "consumer.json", consumer_config_raw)
        consumer_config_sha256 = hashlib.sha256(consumer_config_raw).hexdigest()
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
            "files": {
                "scripts/manage_webapp_ir_artifact_stage.py": "a" * 64,
                "scripts/manage_webapp_ir_snapshot.py": "b" * 64,
                "scripts/manage_webapp_ir_release_provenance.py": "c" * 64,
                "core/standby_snapshot_capacity.py": "d" * 64,
                "scripts/webapp_ir_image_archive_contract.py": "e" * 64,
                "config/consumer.json": consumer_config_sha256,
            },
            "bootstrap": {
                "object_key": "campaign/bootstrap/v1/webapp_fi/webapp_ir/stage-consumer-bootstrap.tar.age",
                "version_id": "version-bootstrap",
                "ciphertext_sha256": "f" * 64,
                "ciphertext_bytes": 2048,
                "plaintext_sha256": "0" * 64,
                "plaintext_bytes": 1024,
                "package_manifest_sha256": "1" * 64,
                "consumer_config_sha256": consumer_config_sha256,
                "preparation_receipt_sha256": "3" * 64,
            },
        }
        payload["receipt_sha256"] = hashlib.sha256(MODULE.canonical_json_bytes(payload)).hexdigest()
        path = candidate / MODULE.BOOTSTRAP_RECEIPT_NAME
        write_private(path, MODULE.canonical_json_bytes(payload) + b"\n")
        return path

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
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        with self.patched_contract():
            installed = MODULE.load_installed_release_receipt(receipt_path)
        self.assertEqual(installed["application"]["release_sha"], self.application_sha)
        self.assertEqual(installed["control"]["release_sha"], self.control_sha)
        self.assertEqual(installed["dispatcher"]["sha256"], result["dispatcher"]["sha256"])

    def test_build_and_install_bind_a_verified_app_image_without_a_repo_digest(self) -> None:
        preparation = self.make_application_preparation(app_repo_digests=[])
        with self.patched_contract():
            prepared = MODULE.build_control_artifacts(
                application_preparation_receipt=preparation,
                control_repository=self.control_repository,
                control_release_sha=self.control_sha,
                output_directory=self.output_parent / "control-no-repo-digest",
                **self.source_proof_arguments(repo_digest=None),
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
                    **self.source_proof_arguments(image_id="sha256:" + "c" * 64, repo_digest=None),
                )
        self.assertFalse(output.exists())

    def test_build_requires_a_present_repo_digest_to_be_bound(self) -> None:
        preparation = self.make_application_preparation()
        output = self.output_parent / "missing-app-repo-digest"
        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "unbound repo digest"):
                MODULE.build_control_artifacts(
                    application_preparation_receipt=preparation,
                    control_repository=self.control_repository,
                    control_release_sha=self.control_sha,
                    output_directory=output,
                    **self.source_proof_arguments(repo_digest=None),
                )
        self.assertFalse(output.exists())

    def test_build_embeds_the_complete_signed_fi_proof_and_controller_adoption_receipt(self) -> None:
        prepared = self.build()
        provenance_path = dict(
            item.split("=", 1)
            for item in prepared["stage_publish"]["artifact"]
        )[MODULE.PROVENANCE_ARTIFACT]
        provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
        proof = provenance["webapp_fi_source_proof"]
        self.assertEqual(
            set(proof),
            {
                "source_role_attestation",
                "image_export_receipt",
                "controller_image_adoption_receipt",
            },
        )
        self.assertEqual(
            proof["source_role_attestation"]["application_release_tree"],
            self.application_tree,
        )
        self.assertEqual(
            proof["image_export_receipt"]["application_release_tree"],
            self.application_tree,
        )
        self.assertTrue(proof["controller_image_adoption_receipt"]["read_back_verified"])
        self.assertTrue(proof["controller_image_adoption_receipt"]["decrypted_verified"])

    def test_build_rejects_a_tampered_fi_role_signature_before_creating_output(self) -> None:
        preparation = self.make_application_preparation()
        source_proof = self.source_proof_arguments()
        role = source_proof["webapp_fi_source_role_attestation"]
        payload = json.loads(role.read_text(encoding="utf-8"))
        payload["source_signature"]["signature_base64"] = base64.b64encode(b"x" * 64).decode("ascii")
        write_private(role, MODULE.canonical_json_bytes(payload) + b"\n")
        output = self.output_parent / "tampered-fi-signature"

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "signature verification failed"):
                self.build_with_source_proof(
                    preparation=preparation,
                    output_directory=output,
                    source_proof=source_proof,
                )
        self.assertFalse(output.exists())

    def test_build_rejects_a_validly_signed_export_with_the_wrong_attestation_hash(self) -> None:
        preparation = self.make_application_preparation()
        source_proof = self.source_proof_arguments()
        self.resign_fi_record(
            source_proof["webapp_fi_image_export_receipt"],
            domain=MODULE.WEBAPP_FI_IMAGE_EXPORT_RECEIPT_DOMAIN,
            mutate=lambda payload: payload.__setitem__("source_role_attestation_sha256", "0" * 64),
        )
        output = self.output_parent / "wrong-export-attestation-hash"

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "does not bind the source role attestation"):
                self.build_with_source_proof(
                    preparation=preparation,
                    output_directory=output,
                    source_proof=source_proof,
                )
        self.assertFalse(output.exists())

    def test_build_rejects_a_controller_adoption_receipt_that_persists_a_url(self) -> None:
        preparation = self.make_application_preparation()
        source_proof = self.source_proof_arguments()
        adoption = source_proof["webapp_fi_controller_image_adoption_receipt"]
        payload = json.loads(adoption.read_text(encoding="utf-8"))
        payload["object"]["version_id"] = "https://example.invalid/presigned-object"
        write_private(adoption, MODULE.canonical_json_bytes(payload) + b"\n")
        output = self.output_parent / "adoption-url"

        with self.patched_contract():
            with self.assertRaisesRegex(MODULE.ReleaseProvenanceError, "persists a forbidden URL"):
                self.build_with_source_proof(
                    preparation=preparation,
                    output_directory=output,
                    source_proof=source_proof,
                )
        self.assertFalse(output.exists())

    def test_build_parser_requires_the_independent_fi_source_proof_inputs(self) -> None:
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
                "--webapp-fi-source-role-attestation",
                "/root/fi-source-role-attestation.json",
                "--webapp-fi-image-export-receipt",
                "/root/fi-image-export-receipt.json",
                "--webapp-fi-controller-image-adoption-receipt",
                "/root/fi-controller-image-adoption.json",
                "--webapp-fi-source-attestation-public-key-file",
                "/root/fi-attestation.key",
            ]
        )
        self.assertEqual(
            parsed.webapp_fi_source_role_attestation,
            Path("/root/fi-source-role-attestation.json"),
        )
        self.assertEqual(parsed.webapp_fi_image_export_receipt, Path("/root/fi-image-export-receipt.json"))
        self.assertEqual(
            parsed.webapp_fi_controller_image_adoption_receipt,
            Path("/root/fi-controller-image-adoption.json"),
        )
        self.assertEqual(
            parsed.webapp_fi_source_attestation_public_key_file,
            Path("/root/fi-attestation.key"),
        )

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
                    **self.source_proof_arguments(),
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
                    **self.source_proof_arguments(),
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
