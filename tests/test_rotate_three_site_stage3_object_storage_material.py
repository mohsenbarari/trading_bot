from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.rotate_three_site_stage3_object_storage_material import (
    CAMPAIGN_ID,
    CONFIRMATION,
    DEPLOYMENT_ID,
    EXPECTED_RELEASE_SHA,
    MaterialRotationError,
    READINESS_STATUS,
    STAGING_BUCKET,
    STAGING_PREFIX,
    rotate,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RotateStage3ObjectStorageMaterialTests(unittest.TestCase):
    def _fixture(self, root: Path) -> argparse.Namespace:
        material = root / "material"
        secrets = material / "secrets"
        secrets.mkdir(parents=True, mode=0o700)
        material.chmod(0o700)
        old_access = "stage3-access"
        old_secret = "o" * 64
        new_access = "stage3-access"
        new_secret = "n" * 64
        credential = secrets / "staging-dr-blob-s3.json"
        credential.write_text(
            json.dumps({"access_key": old_access, "secret_key": old_secret}) + "\n"
        )
        credential.chmod(0o600)
        manifest = material / "bootstrap-material-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "three-site-stage3-bootstrap-material-v1",
                    "campaign_id": CAMPAIGN_ID,
                    "deployment_id": DEPLOYMENT_ID,
                    "release_sha": EXPECTED_RELEASE_SHA,
                    "object_storage_access_key_sha256": _sha256(old_access),
                    "unchanged": "sentinel",
                }
            )
            + "\n"
        )
        manifest.chmod(0o600)
        credentials = root / "new.env"
        credentials.write_text(
            "\n".join(
                (
                    f"ARVAN_S3_ACCESS_KEY={new_access}",
                    f"ARVAN_S3_SECRET_KEY={new_secret}",
                    "ARVAN_S3_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir",
                    "ARVAN_S3_REGION=ir-thr-at1",
                    "",
                )
            )
        )
        credentials.chmod(0o600)
        readiness = root / "readiness.json"
        readiness.write_text(
            json.dumps(
                {
                    "status": READINESS_STATUS,
                    "bucket": STAGING_BUCKET,
                    "prefix": STAGING_PREFIX,
                    "campaign_id": CAMPAIGN_ID,
                    "credential_fingerprints_sha256": {
                        "access_key_sha256": _sha256(new_access),
                        "secret_key_sha256": _sha256(new_secret),
                    },
                    "probe": {"readback_verified": True, "version_id": "version-2"},
                }
            )
            + "\n"
        )
        readiness.chmod(0o600)
        return argparse.Namespace(
            material_root=material,
            new_credentials=credentials,
            readiness_evidence=readiness,
            apply=False,
            confirm=None,
        )

    def test_dry_run_does_not_change_material(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            before = (args.material_root / "secrets/staging-dr-blob-s3.json").read_bytes()
            result = rotate(args)
            self.assertEqual(result["status"], "planned")
            self.assertTrue(result["secret_key_changed"])
            self.assertEqual(
                (args.material_root / "secrets/staging-dr-blob-s3.json").read_bytes(),
                before,
            )

    def test_apply_rotates_only_credential_and_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            sentinel = args.material_root / "unchanged.bin"
            sentinel.write_bytes(b"unchanged")
            sentinel.chmod(0o600)
            args.apply = True
            args.confirm = CONFIRMATION
            result = rotate(args)
            self.assertEqual(result["status"], "rotated")
            credential = json.loads(
                (args.material_root / "secrets/staging-dr-blob-s3.json").read_text()
            )
            manifest = json.loads(
                (args.material_root / "bootstrap-material-manifest.json").read_text()
            )
            self.assertEqual(credential["secret_key"], "n" * 64)
            self.assertEqual(manifest["schema"], "three-site-stage3-bootstrap-material-v2")
            self.assertEqual(manifest["unchanged"], "sentinel")
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertEqual(sentinel.stat().st_mode & 0o777, 0o600)

    def test_same_secret_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            text = args.new_credentials.read_text().replace("n" * 64, "o" * 64)
            args.new_credentials.write_text(text)
            args.new_credentials.chmod(0o600)
            with self.assertRaisesRegex(MaterialRotationError, "did not rotate"):
                rotate(args)

    def test_mismatched_readiness_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            evidence = json.loads(args.readiness_evidence.read_text())
            evidence["credential_fingerprints_sha256"]["secret_key_sha256"] = "0" * 64
            args.readiness_evidence.write_text(json.dumps(evidence) + "\n")
            args.readiness_evidence.chmod(0o600)
            with self.assertRaisesRegex(MaterialRotationError, "not bound"):
                rotate(args)

    def test_confirmation_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            args.apply = True
            args.confirm = "wrong"
            with self.assertRaisesRegex(MaterialRotationError, "confirmation mismatch"):
                rotate(args)


if __name__ == "__main__":
    unittest.main()
