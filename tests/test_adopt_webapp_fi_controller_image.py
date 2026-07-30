"""Focused local-only tests for the controller FI image adoption boundary."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "adopt_webapp_fi_controller_image.py"
SPEC = importlib.util.spec_from_file_location("adopt_webapp_fi_controller_image_test", MODULE_PATH)
assert SPEC and SPEC.loader
adopt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adopt
SPEC.loader.exec_module(adopt)


CAMPAIGN = "campaign-12345678"
RELEASE = "a" * 40
REVISION = "f2c7d8e9a0b1"
CONTROL_COMMIT = "b" * 40
CONTROL_TREE = "c" * 40
CANONICAL_TREE = "d" * 64
VERIFICATION_TIME = "2026-07-30T12:00:00Z"
APP_REFERENCE = "registry.example/trading-bot:rollback-2c08"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _image_id(config: bytes) -> str:
    return "sha256:" + hashlib.sha256(config).hexdigest()


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _docker_archive(path: Path, entries: list[tuple[bytes, list[str]]], *, repositories: bool = False) -> None:
    """Build a minimal valid legacy docker-save archive without Docker."""

    manifest: list[dict[str, object]] = []
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for config, tags in entries:
            image_id = _image_id(config)
            name = image_id.removeprefix("sha256:") + ".json"
            info = tarfile.TarInfo(name)
            info.size = len(config)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(config))
            manifest.append({"Config": name, "Layers": [], "RepoTags": tags})
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(encoded)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(encoded))
        if repositories:
            payload = b"{}"
            info = tarfile.TarInfo("repositories")
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)


@unittest.skipUnless(os.geteuid() == 0, "controller adoption contract is root-only")
class ControllerImageAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-image-adoption-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.inputs.mkdir(mode=0o700)
        self.outputs.mkdir(mode=0o700)
        self.application = {"release_sha": RELEASE, "expected_alembic_revision": REVISION}

        self.app_config = b'{"architecture":"amd64","config":"app"}'
        self.app_image_id = _image_id(self.app_config)
        self.raw_archive = self.inputs / "raw-app.tar"
        # The source tag deliberately does not match APP_REFERENCE. The signed
        # FI byte hash, not a raw Docker tag, is authoritative here.
        _docker_archive(self.raw_archive, [(self.app_config, ["untrusted-local-tag:old"])], repositories=True)
        self.raw_sha256, self.raw_bytes = adopt.sha256_file(self.raw_archive)

        self.postgres_config = b'{"architecture":"amd64","config":"postgres"}'
        self.redis_config = b'{"architecture":"amd64","config":"redis"}'
        self.postgres_id = _image_id(self.postgres_config)
        self.redis_id = _image_id(self.redis_config)
        self.postgres_ref = "postgres:15-alpine"
        self.redis_ref = "redis:7-alpine"
        self.supplemental_bundle = self.inputs / "supplemental.tar"
        postgres_tag = adopt.preparer.image_contract.canonical_archive_tag(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            image_id=self.postgres_id,
        )
        redis_tag = adopt.preparer.image_contract.canonical_archive_tag(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            image_id=self.redis_id,
        )
        _docker_archive(
            self.supplemental_bundle,
            [(self.postgres_config, [postgres_tag]), (self.redis_config, [redis_tag])],
        )
        self.supplemental_images = [
            adopt.preparer.PreparedImage(
                source_ref=self.postgres_ref,
                image_id=self.postgres_id,
                repo_digests=(),
                repo_tags=(self.postgres_ref,),
                size_bytes=123,
                archive_tag=postgres_tag,
            ),
            adopt.preparer.PreparedImage(
                source_ref=self.redis_ref,
                image_id=self.redis_id,
                repo_digests=(),
                repo_tags=(self.redis_ref,),
                size_bytes=456,
                archive_tag=redis_tag,
            ),
        ]
        supplemental_values = [item.as_manifest_value() for item in self.supplemental_images]
        bundle_sha, bundle_bytes = adopt.sha256_file(self.supplemental_bundle)
        self.supplemental_manifest = _private(
            self.inputs / "supplemental-manifest.json",
            _canonical(
                {
                    "schema": adopt.preparer.IMAGE_MANIFEST_SCHEMA,
                    "status": "prepared",
                    "campaign_id": CAMPAIGN,
                    "release_sha": RELEASE,
                    "archive": {
                        "sha256": bundle_sha,
                        "bytes": bundle_bytes,
                        "image_ids": sorted(item.image_id for item in self.supplemental_images),
                        "repo_tags": sorted(str(item.archive_tag) for item in self.supplemental_images),
                    },
                    "image_set_sha256": adopt.sha256_bytes(adopt.canonical_json_bytes(supplemental_values)),
                    "images": supplemental_values,
                }
            ),
        )

        self.proof_paths: dict[str, Path] = {}
        for name in adopt.PROOF_NAMES:
            self.proof_paths[name] = _private(self.inputs / (name + ".json"), _canonical({"fixture": name}))
        self.proof_hashes = {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in self.proof_paths.items()
        }
        self.readback = _private(
            self.inputs / "source-image-readback.json",
            _canonical(
                {
                    "schema": adopt.SOURCE_IMAGE_READBACK_SCHEMA,
                    "status": "read_back",
                    "campaign_id": CAMPAIGN,
                    "source_site": "webapp_fi",
                    "consumer_site": "controller",
                    "object": {
                        "object_key": "campaigns/fixture/raw-app.age",
                        "version_id": "version-raw-app-1",
                        "ciphertext_sha256": hashlib.sha256(b"fixture ciphertext").hexdigest(),
                        "ciphertext_bytes": len(b"fixture ciphertext"),
                        "plaintext_sha256": self.raw_sha256,
                        "plaintext_bytes": self.raw_bytes,
                    },
                    "transport": adopt.SOURCE_IMAGE_TRANSPORT,
                    "age_decryption": adopt.SOURCE_IMAGE_AGE_DECRYPTION,
                }
            ),
        )
        self.controller_key = Ed25519PrivateKey.generate()
        self.controller_private = _private(
            self.inputs / "controller.key",
            self.controller_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        )
        self.controller_public = base64.b64encode(
            self.controller_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")
        self.source_public = base64.b64encode(b"f" * 32).decode("ascii")
        unsigned_binding = {
            "schema": "gold-trade-webapp-fi-source-campaign-binding-v1",
            "status": "bound",
            "campaign_id": CAMPAIGN,
            "application": {
                "release_sha": RELEASE,
                "release_tree": "e" * 40,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {"control_commit": CONTROL_COMMIT, "control_tree": CONTROL_TREE},
        }
        binding = {
            **unsigned_binding,
            "binding_sha256": hashlib.sha256(
                json.dumps(unsigned_binding, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest(),
        }
        self.campaign_binding = _private(
            self.inputs / "campaigns" / CAMPAIGN / "webapp-fi-source" / "campaign-binding.json",
            _canonical(binding),
        )
        self.controller_signing_authority = SimpleNamespace(
            signer=self.controller_key,
            signing_key=SimpleNamespace(
                public_key_base64=self.controller_public,
                key_id="ed25519-sha256:" + hashlib.sha256(base64.b64decode(self.controller_public)).hexdigest(),
                receipt_sha256=hashlib.sha256(b"fixture-controller-signing-receipt").hexdigest(),
            ),
            campaign_binding=SimpleNamespace(
                campaign_id=CAMPAIGN,
                application_release_sha=RELEASE,
                application_release_tree="e" * 40,
                expected_alembic_revision=REVISION,
                control_commit=CONTROL_COMMIT,
                control_tree=CONTROL_TREE,
                binding_sha256=binding["binding_sha256"],
            ),
        )
        signer_loader = mock.patch.object(
            adopt,
            "_load_campaign_bound_controller_signer",
            return_value=self.controller_signing_authority,
        )
        signer_loader.start()
        self.addCleanup(signer_loader.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _authority(self, **_kwargs: object) -> dict[str, object]:
        return {
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": self.application,
            "tooling": {"control_commit": CONTROL_COMMIT, "control_tree": CONTROL_TREE},
            "canonical_release_tree_sha256": CANONICAL_TREE,
            "proof_sha256": self.proof_hashes,
            "image_export": {
                "exported_at": VERIFICATION_TIME,
                "image_claim": {
                    "image_id": self.app_image_id,
                    "image_reference": APP_REFERENCE,
                    "docker_save_archive_sha256": self.raw_sha256,
                    "docker_save_archive_bytes": self.raw_bytes,
                },
            },
        }

    def _arguments(self, *, output_name: str = "candidate") -> dict[str, object]:
        return {
            "source_role_attestation": self.proof_paths["source_role_attestation"],
            "image_export_receipt": self.proof_paths["image_export_receipt"],
            "controller_delivery_envelope": self.proof_paths["controller_delivery_envelope"],
            "signer_enrollment_certificate": self.proof_paths["signer_enrollment_certificate"],
            "static_assets_provenance": self.proof_paths["static_assets_provenance"],
            "source_image_readback": self.readback,
            "raw_image_archive": self.raw_archive,
            "supplemental_image_bundle": self.supplemental_bundle,
            "supplemental_image_manifest": self.supplemental_manifest,
            "expected_postgres_image": adopt.preparer.ImageSpecification(
                reference=self.postgres_ref,
                expected_id=self.postgres_id,
            ),
            "expected_redis_image": adopt.preparer.ImageSpecification(
                reference=self.redis_ref,
                expected_id=self.redis_id,
            ),
            "pinned_source_signing_public_key_base64": self.source_public,
            "campaign_binding_path": self.campaign_binding,
            "expected_canonical_release_tree_sha256": CANONICAL_TREE,
            "expected_app_image_id": self.app_image_id,
            "expected_app_image_reference": APP_REFERENCE,
            "output_directory": self.outputs / output_name,
            "verification_time": VERIFICATION_TIME,
        }

    def test_plan_is_local_only_and_apply_merges_operational_images_with_v2_receipt(self) -> None:
        arguments = self._arguments()
        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority), mock.patch(
            "subprocess.run", side_effect=AssertionError("no subprocess is permitted")
        ):
            plan = adopt.adopt_webapp_fi_image(**arguments, apply=False)
            self.assertEqual("planned", plan["status"])
            self.assertFalse(Path(str(plan["output_directory"])).exists())
            result = adopt.adopt_webapp_fi_image(**arguments, apply=True)

        self.assertEqual("adopted", result["status"])
        self.assertEqual(3, result["image_count"])
        self.assertFalse(result["object_storage_action"])
        self.assertFalse(result["age_action"])
        self.assertFalse(result["docker_command_invoked"])
        self.assertFalse(result["docker_load_invoked"])
        self.assertNotIn("subprocess", adopt.__dict__)
        self.assertNotIn("boto3", adopt.__dict__)

        image_manifest_path = Path(str(result["image_manifest_path"]))
        image_bundle_path = Path(str(result["image_bundle_path"]))
        image_manifest = json.loads(image_manifest_path.read_text(encoding="ascii"))
        images = tuple(
            adopt._image_value(item, campaign_id=CAMPAIGN, release_sha=RELEASE, field="final image manifest")
            for item in image_manifest["images"]
        )
        self.assertEqual([self.postgres_ref, self.redis_ref, APP_REFERENCE], [item.source_ref for item in images])
        self.assertNotIn("untrusted-local-tag:old", image_manifest["archive"]["repo_tags"])
        verified_archive = adopt.preparer.verify_docker_image_archive(
            path=image_bundle_path,
            images=images,
            require_isolated_tags=True,
        )
        self.assertEqual(sorted(item.archive_tag for item in images), verified_archive["repo_tags"])

        receipt_payload = Path(str(result["image_adoption_receipt_path"])).read_bytes()
        verified_receipt = adopt.portable.verify_controller_image_adoption_receipt_payload(
            payload=receipt_payload,
            authority=self._authority(),
            pinned_controller_public_key_base64=self.controller_public,
            expected_image_bundle_sha256=str(result["image_bundle_sha256"]),
            expected_image_bundle_bytes=int(result["image_bundle_bytes"]),
            expected_image_manifest_sha256=str(result["image_manifest_sha256"]),
            expected_image_manifest_bytes=int(result["image_manifest_bytes"]),
            verification_time=VERIFICATION_TIME,
        )
        self.assertEqual(self.app_image_id, verified_receipt["controller_image_artifacts"]["app_image_id"])
        provenance_input = json.loads(Path(str(result["source_provenance_input_path"])).read_text(encoding="ascii"))
        self.assertEqual(adopt.SOURCE_PROVENANCE_INPUT_SCHEMA, provenance_input["schema"])
        self.assertEqual(
            set((*adopt.PROOF_NAMES, "controller_image_adoption_receipt")),
            set(provenance_input["proofs"]),
        )

    def test_rejects_tampered_age_plaintext_before_creating_candidate(self) -> None:
        self.raw_archive.write_bytes(self.raw_archive.read_bytes() + b"tampered")
        self.raw_archive.chmod(0o600)
        arguments = self._arguments(output_name="tampered")
        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority):
            with self.assertRaisesRegex(adopt.ControllerImageAdoptionError, "differs from the signed raw archive"):
                adopt.adopt_webapp_fi_image(**arguments, apply=True)
        self.assertFalse((self.outputs / "tampered").exists())

    def test_rejects_nonoperational_supplemental_manifest_before_creating_candidate(self) -> None:
        value = json.loads(self.supplemental_manifest.read_text(encoding="ascii"))
        value["images"] = value["images"][:1]
        value["image_set_sha256"] = adopt.sha256_bytes(adopt.canonical_json_bytes(value["images"]))
        _private(self.supplemental_manifest, _canonical(value))
        arguments = self._arguments(output_name="missing-runtime")
        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority):
            with self.assertRaisesRegex(adopt.ControllerImageAdoptionError, "exactly the required runtime images"):
                adopt.adopt_webapp_fi_image(**arguments, apply=True)
        self.assertFalse((self.outputs / "missing-runtime").exists())

    def test_rejects_supplemental_images_not_bound_to_explicit_inventory(self) -> None:
        arguments = self._arguments(output_name="wrong-inventory")
        arguments["expected_redis_image"] = adopt.preparer.ImageSpecification(
            reference="redis:7-alpine",
            expected_id="sha256:" + "0" * 64,
        )
        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority):
            with self.assertRaisesRegex(adopt.ControllerImageAdoptionError, "explicitly pinned controller inventory"):
                adopt.adopt_webapp_fi_image(**arguments, apply=True)
        self.assertFalse((self.outputs / "wrong-inventory").exists())

    def test_rechecks_supplemental_archive_after_copy_before_accepting_merge(self) -> None:
        arguments = self._arguments(output_name="supplemental-race")
        original_copy = adopt._copy_archive_members
        mutated = False

        def mutate_after_application_copy(**kwargs: object) -> None:
            nonlocal mutated
            original_copy(**kwargs)
            source_path = kwargs["source_path"]
            if isinstance(source_path, Path) and source_path.name == "app-isolated.tar" and not mutated:
                mutated = True
                self.supplemental_bundle.write_bytes(self.supplemental_bundle.read_bytes() + b"changed")
                self.supplemental_bundle.chmod(0o600)

        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority), mock.patch.object(
            adopt, "_copy_archive_members", side_effect=mutate_after_application_copy
        ):
            with self.assertRaisesRegex(adopt.ControllerImageAdoptionError, "changed while being merged"):
                adopt.adopt_webapp_fi_image(**arguments, apply=True)
        self.assertTrue(mutated)

    def test_accepts_a_tagless_exact_docker_save_export(self) -> None:
        _docker_archive(self.raw_archive, [(self.app_config, [])])
        self.raw_sha256, self.raw_bytes = adopt.sha256_file(self.raw_archive)
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["object"]["plaintext_sha256"] = self.raw_sha256
        readback["object"]["plaintext_bytes"] = self.raw_bytes
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="tagless-source")
        with mock.patch.object(adopt.portable, "verify_webapp_fi_source_authority_payloads", side_effect=self._authority):
            result = adopt.adopt_webapp_fi_image(**arguments, apply=True)
        manifest = json.loads(Path(str(result["image_manifest_path"])).read_text(encoding="ascii"))
        app = next(image for image in manifest["images"] if image["image_id"] == self.app_image_id)
        self.assertEqual([], app["repo_tags"])

    def test_rejects_root_private_workspace_below_a_writable_nonsticky_ancestor(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o777)
        nested = unsafe / "nested"
        nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(adopt.ControllerImageAdoptionError, "writable non-sticky ancestor"):
            adopt._require_private_directory(nested, field="test workspace")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
