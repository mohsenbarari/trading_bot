from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from scripts import produce_production_shadow_dr_ca as MODULE


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
NOW = datetime(2026, 7, 27, 22, 30, 15, tzinfo=timezone.utc)


class ProductionShadowDrCaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "secret-root"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.prefix = mock.patch.object(
            MODULE,
            "SECRET_ROOT_PREFIX",
            self.root,
        )
        self.prefix.start()
        self.owner_uid = os.geteuid()

    def tearDown(self) -> None:
        self.prefix.stop()
        self.temporary.cleanup()

    def generate(self, **overrides):
        values = {
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "owner_uid": self.owner_uid,
            "now": NOW,
        }
        values.update(overrides)
        return MODULE.generate_dr_ca(**values)

    def paths(self) -> dict[str, Path]:
        return MODULE._canonical_paths(OPERATION_ID)

    def test_plan_is_nonmutating_and_binds_exact_confirmation(self) -> None:
        result = MODULE.build_plan(OPERATION_ID, RELEASE_SHA)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(
            result["required_confirmation"],
            (
                "CREATE-FRESH-PRODUCTION-SHADOW-DR-CA:"
                f"{OPERATION_ID}:{RELEASE_SHA}"
            ),
        )
        self.assertFalse(result["live_io_performed"])
        self.assertFalse(result["private_key_exported"])
        self.assertFalse(result["old_tls_material_reused"])
        self.assertFalse((self.root / OPERATION_ID).exists())

    def test_generates_exact_root_only_operation_bound_ca_and_attestation(
        self,
    ) -> None:
        result = self.generate()
        paths = self.paths()
        self.assertEqual(result["status"], "fresh-operation-dr-ca-ready")
        self.assertFalse(result["live_io_performed"])
        self.assertFalse(result["private_key_exported"])
        self.assertTrue(result["private_key_retained_on_controller"])
        self.assertFalse(result["old_tls_material_reused"])

        for directory in (paths["operation_root"], paths["tls_root"]):
            metadata = directory.stat(follow_symlinks=False)
            self.assertTrue(stat.S_ISDIR(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        for name in ("state", "key", "certificate", "attestation", "lock"):
            metadata = paths[name].stat(follow_symlinks=False)
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)

        key_bytes = paths["key"].read_bytes()
        certificate_bytes = paths["certificate"].read_bytes()
        attestation_bytes = paths["attestation"].read_bytes()
        self.assertIn(b"BEGIN PRIVATE KEY", key_bytes)
        self.assertNotIn(b"PRIVATE KEY", certificate_bytes)
        self.assertNotIn(key_bytes, attestation_bytes)
        self.assertFalse(attestation_bytes.endswith(b"\n"))

        key = serialization.load_pem_private_key(key_bytes, password=None)
        self.assertIsInstance(key, ec.EllipticCurvePrivateKey)
        self.assertIsInstance(key.curve, ec.SECP256R1)
        certificate = x509.load_pem_x509_certificate(certificate_bytes)
        certificate.verify_directly_issued_by(certificate)
        basic = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        )
        usage = certificate.extensions.get_extension_for_class(
            x509.KeyUsage
        )
        self.assertTrue(basic.critical)
        self.assertTrue(basic.value.ca)
        self.assertEqual(basic.value.path_length, 1)
        self.assertTrue(usage.critical)
        self.assertTrue(usage.value.key_cert_sign)
        self.assertTrue(usage.value.crl_sign)

        attestation = json.loads(attestation_bytes)
        self.assertEqual(set(attestation), MODULE.ATTESTATION_FIELDS)
        self.assertEqual(attestation["operation_id"], OPERATION_ID)
        self.assertEqual(attestation["release_sha"], RELEASE_SHA)
        self.assertEqual(
            attestation["ca_sha256"],
            hashlib.sha256(certificate_bytes).hexdigest(),
        )
        self.assertEqual(
            attestation["ca_subject"],
            certificate.subject.rfc4514_string(),
        )
        self.assertEqual(
            attestation["ca_serial_hex"],
            format(certificate.serial_number, "x"),
        )
        self.assertEqual(attestation["private_key_mode"], "0600")
        self.assertTrue(
            attestation["private_key_retained_on_controller"]
        )
        self.assertFalse(attestation["old_tls_material_reused"])
        self.assertEqual(
            result["attestation_sha256"],
            hashlib.sha256(attestation_bytes).hexdigest(),
        )

    def test_exact_rerun_reuses_all_material_without_rotation(self) -> None:
        first = self.generate()
        paths = self.paths()
        bytes_before = {
            name: paths[name].read_bytes()
            for name in ("state", "key", "certificate", "attestation")
        }
        second = self.generate(now=NOW.replace(hour=23))
        self.assertEqual(
            second["publication"],
            {
                "state": "reused",
                "private_key": "reused",
                "certificate": "reused",
                "attestation": "reused",
            },
        )
        self.assertEqual(first["certificate_sha256"], second["certificate_sha256"])
        for name, expected in bytes_before.items():
            self.assertEqual(paths[name].read_bytes(), expected)

    def test_crash_after_key_resumes_from_bound_state_without_old_material(
        self,
    ) -> None:
        def fail_after_key(phase: str) -> None:
            if phase == "after-key":
                raise RuntimeError("simulated crash")

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            self.generate(checkpoint=fail_after_key)
        paths = self.paths()
        self.assertTrue(paths["state"].exists())
        self.assertTrue(paths["key"].exists())
        self.assertFalse(paths["certificate"].exists())
        key_before = paths["key"].read_bytes()

        result = self.generate(now=NOW.replace(hour=23))
        self.assertEqual(paths["key"].read_bytes(), key_before)
        self.assertEqual(result["publication"]["state"], "reused")
        self.assertEqual(result["publication"]["private_key"], "reused")
        self.assertEqual(result["publication"]["certificate"], "created")
        self.assertFalse(result["old_tls_material_reused"])

    def test_existing_material_for_different_release_is_never_adopted(
        self,
    ) -> None:
        self.generate()
        key_before = self.paths()["key"].read_bytes()
        with self.assertRaisesRegex(MODULE.DrCaError, "state identity"):
            MODULE.generate_dr_ca(
                operation_id=OPERATION_ID,
                release_sha="b" * 40,
                owner_uid=self.owner_uid,
                now=NOW,
            )
        self.assertEqual(self.paths()["key"].read_bytes(), key_before)

    def test_stale_operation_ca_is_not_reported_ready_or_rotated(self) -> None:
        self.generate()
        key_before = self.paths()["key"].read_bytes()
        with self.assertRaisesRegex(MODULE.DrCaError, "stale"):
            self.generate(now=NOW + timedelta(hours=25))
        self.assertEqual(self.paths()["key"].read_bytes(), key_before)

    def test_tampered_certificate_is_rejected_without_overwrite(self) -> None:
        self.generate()
        path = self.paths()["certificate"]
        path.write_bytes(b"not a certificate")
        path.chmod(0o600)
        before = path.read_bytes()
        with self.assertRaisesRegex(MODULE.DrCaError, "encoding"):
            self.generate()
        self.assertEqual(path.read_bytes(), before)

    def test_unsafe_key_mode_and_attestation_symlink_fail_closed(self) -> None:
        self.generate()
        paths = self.paths()
        paths["key"].chmod(0o640)
        with self.assertRaises(MODULE.SecureFileError):
            self.generate()
        paths["key"].chmod(0o600)

        attestation_bytes = paths["attestation"].read_bytes()
        paths["attestation"].unlink()
        alternate = paths["tls_root"] / "alternate.json"
        alternate.write_bytes(attestation_bytes)
        alternate.chmod(0o600)
        paths["attestation"].symlink_to(alternate)
        with self.assertRaises(MODULE.SecureFileError):
            self.generate()

    def test_cli_apply_requires_exact_confirmation_before_mutation(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = MODULE.main(
                [
                    "--operation-id",
                    OPERATION_ID,
                    "--release-sha",
                    RELEASE_SHA,
                    "--apply",
                    "--confirm",
                    "wrong",
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertFalse((self.root / OPERATION_ID).exists())

        output = io.StringIO()
        with mock.patch.object(
            MODULE,
            "generate_dr_ca",
            return_value={"status": "fresh-operation-dr-ca-ready"},
        ) as generate, redirect_stdout(output):
            status = MODULE.main(
                [
                    "--operation-id",
                    OPERATION_ID,
                    "--release-sha",
                    RELEASE_SHA,
                    "--apply",
                    "--confirm",
                    MODULE.confirmation_phrase(OPERATION_ID, RELEASE_SHA),
                ]
            )
        self.assertEqual(status, 0)
        generate.assert_called_once_with(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            owner_uid=0,
        )
        self.assertEqual(
            json.loads(output.getvalue())["status"],
            "fresh-operation-dr-ca-ready",
        )


if __name__ == "__main__":
    unittest.main()
