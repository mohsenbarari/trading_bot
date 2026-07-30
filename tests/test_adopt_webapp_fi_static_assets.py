"""Focused local-only tests for controller static-asset adoption."""

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
MODULE_PATH = ROOT / "scripts" / "adopt_webapp_fi_static_assets.py"
SPEC = importlib.util.spec_from_file_location("adopt_webapp_fi_static_assets_test", MODULE_PATH)
assert SPEC and SPEC.loader
adopt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adopt
SPEC.loader.exec_module(adopt)


CAMPAIGN = "campaign-12345678"
RELEASE = "a" * 40
REVISION = "f2c7d8e9a0b1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _static_archive(path: Path, entries: dict[str, bytes], *, mode: int = 0o644) -> Path:
    """Create a deliberately deterministic static tar archive without tar extensions."""

    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(entries):
            payload = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)
    return path


@unittest.skipUnless(os.geteuid() == 0, "controller static asset adoption is root-only")
class StaticAssetAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-static-adoption-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.inputs.mkdir(mode=0o700)
        self.outputs.mkdir(mode=0o700)
        self.application = {"release_sha": RELEASE, "expected_alembic_revision": REVISION}

        self.archive = _static_archive(
            self.inputs / "mini-app-dist.tar",
            {
                "assets/app.js": b"console.log('fixture');\n",
                "index.html": b"<!doctype html><title>fixture</title>\n",
            },
        )
        self.archive_sha256, self.archive_bytes = adopt.sha256_file(self.archive)
        self.readback = _private(
            self.inputs / "static-assets-readback.json",
            _canonical(
                {
                    "schema": adopt.STATIC_ASSET_READBACK_SCHEMA,
                    "status": "read_back",
                    "campaign_id": CAMPAIGN,
                    "source_site": "webapp_fi",
                    "consumer_site": "controller",
                    "object": {
                        "object_key": "campaigns/fixture/mini-app-dist.age",
                        "version_id": "static-version-1",
                        "ciphertext_sha256": hashlib.sha256(b"fixture ciphertext").hexdigest(),
                        "ciphertext_bytes": len(b"fixture ciphertext"),
                        "plaintext_sha256": self.archive_sha256,
                        "plaintext_bytes": self.archive_bytes,
                    },
                    "transport": adopt.STATIC_TRANSPORT,
                    "age_decryption": adopt.STATIC_ASSET_AGE_DECRYPTION,
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
        unsigned_binding = {
            "schema": "gold-trade-webapp-fi-source-campaign-binding-v1",
            "status": "bound",
            "campaign_id": CAMPAIGN,
            "application": {
                "release_sha": RELEASE,
                "release_tree": "b" * 40,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {"control_commit": "c" * 40, "control_tree": "d" * 40},
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
                application_release_tree="b" * 40,
                expected_alembic_revision=REVISION,
                control_commit="c" * 40,
                control_tree="d" * 40,
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

    def _arguments(self, *, output_name: str = "candidate") -> dict[str, object]:
        return {
            "static_archive": self.archive,
            "object_storage_readback": self.readback,
            "campaign_binding_path": self.campaign_binding,
            "output_directory": self.outputs / output_name,
        }

    def test_plan_is_local_only_and_apply_emits_existing_verifier_compatible_proof(self) -> None:
        arguments = self._arguments()
        with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess is permitted")):
            plan = adopt.adopt_static_assets(**arguments, apply=False)
            self.assertEqual("planned", plan["status"])
            self.assertFalse(Path(str(plan["output_directory"])).exists())
            result = adopt.adopt_static_assets(**arguments, apply=True)

        self.assertEqual("adopted", result["status"])
        self.assertEqual(2, result["file_count"])
        self.assertFalse(result["object_storage_action"])
        self.assertFalse(result["age_action"])
        self.assertFalse(result["ssh_action"])
        self.assertFalse(result["docker_action"])
        self.assertFalse(result["service_changed"])
        self.assertNotIn("subprocess", adopt.__dict__)
        self.assertNotIn("boto3", adopt.__dict__)

        provenance_path = Path(str(result["static_assets_provenance_path"]))
        verified = adopt.portable._static_assets_provenance(
            payload=provenance_path.read_bytes(),
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_id=CAMPAIGN,
            expected_application=self.application,
        )
        self.assertEqual(self.archive_sha256, verified["artifact"]["plaintext_sha256"])
        self.assertEqual(2, verified["file_count"])

        receipt_path = Path(str(result["adoption_receipt_path"]))
        receipt = json.loads(receipt_path.read_text(encoding="ascii"))
        receipt_unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        self.assertEqual(
            adopt.sha256_bytes(adopt.canonical_json_bytes(receipt_unsigned)),
            receipt["receipt_sha256"],
        )
        self.assertEqual("static-version-1", receipt["object"]["version_id"])
        self.assertEqual("webapp_fi", receipt["source_site"])
        self.assertEqual("controller", receipt["destination_site"])

        input_value = json.loads(Path(str(result["provenance_input_path"])).read_text(encoding="ascii"))
        self.assertEqual(adopt.STATIC_ASSET_PROVENANCE_INPUT_SCHEMA, input_value["schema"])
        self.assertEqual(json.loads(provenance_path.read_text(encoding="ascii")), input_value["static_assets_provenance"])
        serialized_input = json.dumps(input_value, sort_keys=True).lower()
        self.assertNotIn("://", serialized_input)
        self.assertNotIn('"url"', serialized_input)
        self.assertNotIn("presigned", serialized_input)
        self.assertEqual(0o700, stat_mode(Path(str(result["output_directory"]))))
        for emitted in (provenance_path, receipt_path, Path(str(result["provenance_input_path"]))):
            self.assertEqual(0o600, stat_mode(emitted))
            self.assertEqual(0, emitted.stat().st_uid)

    def test_rejects_plaintext_archive_changed_after_exact_version_readback(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"tampered")
        self.archive.chmod(0o600)
        arguments = self._arguments(output_name="tampered")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "differs from the Object Storage read-back"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "tampered").exists())

    def test_rejects_readback_with_wrong_source_direction_before_creating_candidate(self) -> None:
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["source_site"] = "controller"
        readback["consumer_site"] = "webapp_fi"
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="wrong-direction")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "read-back record is unsupported"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "wrong-direction").exists())

    def test_rejects_readback_without_controller_root_only_age_scope_before_creating_candidate(self) -> None:
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["age_decryption"]["controller_identity_scope"] = "user"
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="wrong-age-scope")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "read-back record is unsupported"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "wrong-age-scope").exists())

    def test_rejects_unsafe_archive_member_before_creating_candidate(self) -> None:
        with tarfile.open(self.archive, "w", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo("../outside.js")
            info.size = 2
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.mtime = 0
            archive.addfile(info, io.BytesIO(b"//"))
        self.archive.chmod(0o600)
        archive_sha, archive_bytes = adopt.sha256_file(self.archive)
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["object"]["plaintext_sha256"] = archive_sha
        readback["object"]["plaintext_bytes"] = archive_bytes
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="unsafe-member")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "archive path is invalid"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "unsafe-member").exists())

    def test_rejects_static_archive_symlink_before_creating_candidate(self) -> None:
        with tarfile.open(self.archive, "w", format=tarfile.USTAR_FORMAT) as archive:
            info = tarfile.TarInfo("assets/escape")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.mtime = 0
            archive.addfile(info)
        self.archive.chmod(0o600)
        archive_sha, archive_bytes = adopt.sha256_file(self.archive)
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["object"]["plaintext_sha256"] = archive_sha
        readback["object"]["plaintext_bytes"] = archive_bytes
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="unsafe-symlink")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "member is not deterministic"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "unsafe-symlink").exists())

    def test_rejects_non_deterministic_member_metadata_before_creating_candidate(self) -> None:
        _static_archive(self.archive, {"index.html": b"fixture"}, mode=0o600)
        archive_sha, archive_bytes = adopt.sha256_file(self.archive)
        readback = json.loads(self.readback.read_text(encoding="ascii"))
        readback["object"]["plaintext_sha256"] = archive_sha
        readback["object"]["plaintext_bytes"] = archive_bytes
        _private(self.readback, _canonical(readback))
        arguments = self._arguments(output_name="non-deterministic")
        with self.assertRaisesRegex(adopt.StaticAssetAdoptionError, "member is not deterministic"):
            adopt.adopt_static_assets(**arguments, apply=True)
        self.assertFalse((self.outputs / "non-deterministic").exists())


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
