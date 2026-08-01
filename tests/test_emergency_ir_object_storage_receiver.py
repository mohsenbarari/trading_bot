from __future__ import annotations

import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import emergency_ir_object_storage_manifest as manifest


MODULE_PATH = REPO_ROOT / "scripts" / "emergency_ir_object_storage_receiver.py"
SPEC = importlib.util.spec_from_file_location("emergency_ir_object_storage_receiver", MODULE_PATH)
assert SPEC and SPEC.loader
receiver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = receiver
SPEC.loader.exec_module(receiver)


CAMPAIGN_ID = "20260801T203000Z-emergency-ir-02"
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
                    prefix=prefix, campaign_id=CAMPAIGN_ID, kind=kind
                ),
                "version_id": f"Version-{index + 1}_immutable",
                "plaintext_sha256": f"{index + 1:x}" * 64,
                "plaintext_bytes": 1024 + index,
                "ciphertext_sha256": f"{index + 5:x}" * 64,
                "ciphertext_bytes": 1152 + index,
                "encryption": {"algorithm": "age-v1", "recipient_key_id": RECIPIENT_KEY_ID},
                "target_path": manifest.expected_target_path(campaign_id=CAMPAIGN_ID, kind=kind),
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
        "created_at": datetime(2026, 8, 1, 20, 30, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "destination_age_recipient_key_id": RECIPIENT_KEY_ID,
        "artifacts": artifacts,
    }


def presigned_url(*, bucket: str, artifact: dict[str, object], version_id: str | None = None) -> str:
    query = urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": "access/20260801/ir-thr-at1/s3/aws4_request",
            "X-Amz-Date": "20260801T203000Z",
            "X-Amz-Expires": "300",
            "X-Amz-SignedHeaders": "host",
            "X-Amz-Signature": "a" * 64,
            "versionId": version_id if version_id is not None else artifact["version_id"],
        }
    )
    return f"{manifest.APPROVED_ARVAN_ENDPOINT}/{bucket}/{artifact['object_key']}?{query}"


class EmergencyIrObjectStorageReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        signed = manifest.sign_manifest(unsigned_manifest(), private_key=private_key)
        self.plan = manifest.verify_manifest_bytes(
            manifest.canonical_json_bytes(signed), public_key=private_key.public_key()
        ).as_receive_plan()

    def url_map(self) -> dict[str, object]:
        return {
            "schema": receiver.URL_MAP_SCHEMA,
            "manifest_sha256": self.plan["manifest_sha256"],
            "artifacts": [
                {
                    "kind": artifact["kind"],
                    "url": presigned_url(bucket=self.plan["bucket"], artifact=artifact),
                }
                for artifact in self.plan["artifacts"]
            ],
        }

    def test_url_map_and_presigned_urls_bind_all_fixed_artifacts(self) -> None:
        urls = receiver._parse_url_map(
            json.dumps(self.url_map()).encode("utf-8"), manifest_sha256=self.plan["manifest_sha256"]
        )
        self.assertEqual(list(urls), list(manifest.ARTIFACT_ORDER))
        for artifact in self.plan["artifacts"]:
            receiver._validate_presigned_url(
                url=urls[artifact["kind"]], plan=self.plan, artifact=artifact
            )

    def test_wrong_manifest_binding_or_artifact_order_fails_closed(self) -> None:
        wrong_digest = self.url_map()
        wrong_digest["manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(receiver.EmergencyReceiverError, "bound"):
            receiver._parse_url_map(
                json.dumps(wrong_digest).encode("utf-8"), manifest_sha256=self.plan["manifest_sha256"]
            )

        reordered = self.url_map()
        reordered["artifacts"][0], reordered["artifacts"][1] = (
            reordered["artifacts"][1],
            reordered["artifacts"][0],
        )
        with self.assertRaisesRegex(receiver.EmergencyReceiverError, "fixed order"):
            receiver._parse_url_map(
                json.dumps(reordered).encode("utf-8"), manifest_sha256=self.plan["manifest_sha256"]
            )

    def test_url_cannot_drift_endpoint_path_version_or_expiry(self) -> None:
        artifact = self.plan["artifacts"][0]
        valid = presigned_url(bucket=self.plan["bucket"], artifact=artifact)
        receiver._validate_presigned_url(url=valid, plan=self.plan, artifact=artifact)

        for bad in (
            valid.replace("s3.ir-thr-at1.arvanstorage.ir", "example.invalid"),
            valid.replace(str(artifact["object_key"]), "another-object.age"),
            presigned_url(bucket=self.plan["bucket"], artifact=artifact, version_id="wrong-version"),
            valid.replace("X-Amz-Expires=300", "X-Amz-Expires=3600"),
        ):
            with self.assertRaises(receiver.EmergencyReceiverError):
                receiver._validate_presigned_url(url=bad, plan=self.plan, artifact=artifact)

    def test_url_map_rejects_duplicate_or_unknown_fields(self) -> None:
        duplicate = (
            b'{"schema":"gold-trade-emergency-ir-presigned-url-map-v1",'
            b'"schema":"duplicate","manifest_sha256":"' + self.plan["manifest_sha256"].encode() + b'","artifacts":[]}'
        )
        with self.assertRaisesRegex(receiver.EmergencyReceiverError, "duplicate"):
            receiver._parse_url_map(duplicate, manifest_sha256=self.plan["manifest_sha256"])

        unknown = copy.deepcopy(self.url_map())
        unknown["unexpected"] = "value"
        with self.assertRaisesRegex(receiver.EmergencyReceiverError, "unsupported"):
            receiver._parse_url_map(
                json.dumps(unknown).encode("utf-8"), manifest_sha256=self.plan["manifest_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
