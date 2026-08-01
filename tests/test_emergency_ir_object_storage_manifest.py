from __future__ import annotations

import base64
import contextlib
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "emergency_ir_object_storage_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("emergency_ir_object_storage_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
manifest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest
SPEC.loader.exec_module(manifest)


CAMPAIGN_ID = "20260801T201500Z-emergency-ir-01"
RECIPIENT_KEY_ID = "age-recipient-sha256:" + "d" * 64


def unsigned_manifest() -> dict[str, object]:
    prefix = "emergency-ir"
    artifacts: list[dict[str, object]] = []
    for index, kind in enumerate(manifest.ARTIFACT_ORDER):
        artifacts.append(
            {
                "kind": kind,
                "format": manifest.ARTIFACT_CONTRACTS[kind]["format"],
                "object_key": manifest.expected_object_key(
                    prefix=prefix,
                    campaign_id=CAMPAIGN_ID,
                    kind=kind,
                ),
                "version_id": f"Version-{index + 1}_immutable",
                "plaintext_sha256": f"{index + 1:x}" * 64,
                "plaintext_bytes": 1024 + index,
                "ciphertext_sha256": f"{index + 5:x}" * 64,
                "ciphertext_bytes": 1152 + index,
                "encryption": {
                    "algorithm": "age-v1",
                    "recipient_key_id": RECIPIENT_KEY_ID,
                },
                "target_path": manifest.expected_target_path(
                    campaign_id=CAMPAIGN_ID,
                    kind=kind,
                ),
            }
        )
    return {
        "schema": manifest.MANIFEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "source_site": manifest.SOURCE_SITE,
        "destination_site": manifest.DESTINATION_SITE,
        "endpoint": manifest.APPROVED_ARVAN_ENDPOINT,
        "region": manifest.APPROVED_ARVAN_REGION,
        "bucket": "emergency-ir-artifacts",
        "prefix": prefix,
        "created_at": "2026-08-01T20:15:00Z",
        "destination_age_recipient_key_id": RECIPIENT_KEY_ID,
        "artifacts": artifacts,
    }


class EmergencyIrObjectStorageManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()

    def test_sign_and_verify_emit_complete_allowlisted_receive_plan(self) -> None:
        signed = manifest.sign_manifest(unsigned_manifest(), private_key=self.private_key)
        verified = manifest.verify_manifest_bytes(
            manifest.canonical_json_bytes(signed), public_key=self.public_key
        )
        plan = verified.as_receive_plan()

        self.assertEqual(plan["status"], "verified-non-authorizing")
        self.assertEqual(plan["campaign_id"], CAMPAIGN_ID)
        self.assertEqual(plan["endpoint"], manifest.APPROVED_ARVAN_ENDPOINT)
        self.assertEqual(plan["bucket"], "emergency-ir-artifacts")
        self.assertEqual([item["kind"] for item in plan["artifacts"]], list(manifest.ARTIFACT_ORDER))
        self.assertEqual(
            [item["target_path"] for item in plan["artifacts"]],
            [
                manifest.expected_target_path(campaign_id=CAMPAIGN_ID, kind=kind)
                for kind in manifest.ARTIFACT_ORDER
            ],
        )
        self.assertNotIn("signature_base64", plan)
        self.assertNotIn("deploy", json.dumps(plan).lower())

    def test_signature_tampering_and_noncanonical_signed_bytes_are_rejected(self) -> None:
        signed = manifest.sign_manifest(unsigned_manifest(), private_key=self.private_key)
        tampered = copy.deepcopy(signed)
        tampered["artifacts"][0]["ciphertext_sha256"] = "0" * 64
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "signature"):
            manifest.verify_manifest(tampered, public_key=self.public_key)

        with self.assertRaisesRegex(manifest.EmergencyManifestError, "pinned public key"):
            manifest.verify_manifest(
                signed,
                public_key=Ed25519PrivateKey.generate().public_key(),
            )

        noncanonical = json.dumps(signed, sort_keys=True).encode("utf-8")
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "canonical JSON"):
            manifest.verify_manifest_bytes(noncanonical, public_key=self.public_key)

    def test_duplicate_json_unknown_fields_and_non_arvan_endpoint_fail_closed(self) -> None:
        duplicate = b'{"schema":"one","schema":"two"}'
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "duplicate"):
            manifest.load_strict_json_bytes(duplicate, require_canonical=False)

        invalid_endpoint = unsigned_manifest()
        invalid_endpoint["endpoint"] = "https://s3.us-east-1.amazonaws.com"
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "approved Arvan"):
            manifest.validate_unsigned_manifest(invalid_endpoint)

        unknown = unsigned_manifest()
        unknown["unexpected"] = "value"
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "unsupported"):
            manifest.validate_unsigned_manifest(unknown)

    def test_arbitrary_object_or_target_path_and_unencrypted_artifact_are_rejected(self) -> None:
        wrong_object = unsigned_manifest()
        wrong_object["artifacts"][0]["object_key"] = "other-campaign/images.tar.age"
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "object_key"):
            manifest.validate_unsigned_manifest(wrong_object)

        wrong_target = unsigned_manifest()
        wrong_target["artifacts"][1]["target_path"] = "/srv/trading-bot-three-site/releases/evil"
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "allowlisted"):
            manifest.validate_unsigned_manifest(wrong_target)

        unencrypted = unsigned_manifest()
        unencrypted["artifacts"][2]["encryption"]["algorithm"] = "none"
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "age-v1"):
            manifest.validate_unsigned_manifest(unencrypted)

    def test_complete_fixed_artifact_set_is_required(self) -> None:
        missing = unsigned_manifest()
        missing["artifacts"].pop()
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "complete"):
            manifest.validate_unsigned_manifest(missing)

        reordered = unsigned_manifest()
        reordered["artifacts"][0], reordered["artifacts"][1] = (
            reordered["artifacts"][1],
            reordered["artifacts"][0],
        )
        with self.assertRaisesRegex(manifest.EmergencyManifestError, "fixed order"):
            manifest.validate_unsigned_manifest(reordered)

    def test_cli_build_and_verify_are_local_create_only_operations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-manifest-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(unsigned_manifest(), indent=2), encoding="utf-8")
            spec_path.chmod(0o600)

            private_path = root / "signing-private.key"
            private_path.write_text(
                base64.b64encode(
                    self.private_key.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                ).decode("ascii"),
                encoding="ascii",
            )
            private_path.chmod(0o600)
            public_path = root / "signing-public.key"
            public_path.write_text(
                base64.b64encode(
                    self.public_key.public_bytes(
                        serialization.Encoding.Raw,
                        serialization.PublicFormat.Raw,
                    )
                ).decode("ascii"),
                encoding="ascii",
            )
            public_path.chmod(0o644)
            output_path = root / "sealed-manifest.json"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                build_rc = manifest._main(
                    [
                        "build",
                        "--spec",
                        str(spec_path),
                        "--signing-private-key",
                        str(private_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(build_rc, 0)
            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "built-non-authorizing")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                verify_rc = manifest._main(
                    [
                        "verify",
                        "--manifest",
                        str(output_path),
                        "--signing-public-key",
                        str(public_path),
                    ]
                )
            self.assertEqual(verify_rc, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "verified-non-authorizing")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                repeat_rc = manifest._main(
                    [
                        "build",
                        "--spec",
                        str(spec_path),
                        "--signing-private-key",
                        str(private_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(repeat_rc, 2)
            self.assertIn("refusing to overwrite", stdout.getvalue())

    def test_module_has_no_network_or_host_execution_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("boto3", "urllib", "requests", "socket", "subprocess", "ssh"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
