from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.fetch_three_site_staging_seed import SeedFetchError, _fetch_one, build_plan


class _Client:
    def __init__(self, payload: bytes, item: dict):
        self.payload = payload
        self.item = item

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        return {
            "Body": io.BytesIO(self.payload),
            "ContentLength": len(self.payload),
            "VersionId": VersionId,
            "Metadata": {
                "plaintext-sha256": self.item["plaintext_sha256"],
                "ciphertext-sha256": self.item["ciphertext_sha256"],
                "artifact-kind": self.item["kind"],
            },
        }


def _decrypt(arguments, *, timeout=1800):  # noqa: ANN001, ARG001
    output = Path(arguments[arguments.index("--output") + 1])
    payload = Path(arguments[-1]).read_bytes()
    output.write_bytes(payload[3:])


class FetchThreeSiteStagingSeedTests(unittest.TestCase):
    def test_exact_version_ciphertext_and_plaintext_are_verified(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed/webapp_fi/object.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher),
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.fetch_three_site_staging_seed._run_age", side_effect=_decrypt
        ):
            output = Path(directory) / "postgres.custom"
            result = _fetch_one(
                _Client(cipher, item),
                bucket="staging-bucket",
                item=item,
                identity_path=Path(directory) / "identity",
                output=output,
            )
            self.assertEqual(output.read_bytes(), plain)
            self.assertEqual(result["plaintext_sha256"], item["plaintext_sha256"])
            self.assertFalse((Path(directory) / ".postgres.custom.ciphertext").exists())

    def test_provider_size_mismatch_fails_before_decryption(self):
        plain = b"database-seed"
        cipher = b"AGE" + plain
        item = {
            "kind": "postgres",
            "object_key": "staging/campaign/seed/bot_fi/object.age",
            "version_id": "v1",
            "plaintext_sha256": hashlib.sha256(plain).hexdigest(),
            "plaintext_bytes": len(plain),
            "ciphertext_sha256": hashlib.sha256(cipher).hexdigest(),
            "ciphertext_bytes": len(cipher) + 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SeedFetchError, "provider identity"):
                _fetch_one(
                    _Client(cipher, item),
                    bucket="staging-bucket",
                    item=item,
                    identity_path=Path(directory) / "identity",
                    output=Path(directory) / "postgres.custom",
                )

    def test_witness_plan_has_no_seed_objects(self):
        plan = build_plan(
            campaign_id="11111111-1111-4111-8111-111111111111",
            target_role="witness",
            plan_hash="a" * 64,
            source_role=None,
        )
        self.assertEqual(plan["object_count"], 0)


if __name__ == "__main__":
    unittest.main()
