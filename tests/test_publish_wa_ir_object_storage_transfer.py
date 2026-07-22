from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.publish_wa_ir_object_storage_transfer import (
    TransferPublicationError,
    publish_file,
)


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def get_bucket_versioning(self, *, Bucket):  # noqa: N803, ARG002
        return {"Status": "Enabled"}

    def get_bucket_acl(self, *, Bucket):  # noqa: N803, ARG002
        return {"Grants": [{"Grantee": {"Type": "CanonicalUser"}, "Permission": "FULL_CONTROL"}]}

    def put_object(self, *, Bucket, Key, Body, ContentLength, ContentType, Metadata):  # noqa: N803, ARG002
        payload = Body.read()
        self.objects[(Bucket, Key)] = (payload, dict(Metadata), "version-1")
        self.asserted_length = ContentLength
        return {"VersionId": "version-1"}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        payload, metadata, version = self.objects[(Bucket, Key)]
        return {"ContentLength": len(payload), "Metadata": metadata, "VersionId": version}

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        payload, _metadata, version = self.objects[(Bucket, Key)]
        assert version == VersionId
        return {"Body": io.BytesIO(payload)}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):  # noqa: N803
        return f"https://s3.ir-thr-at1.arvanstorage.ir/{Params['Bucket']}/{Params['Key']}?op={operation}&ttl={ExpiresIn}"


def _fake_encrypt(source: Path, output: Path, recipient: str):  # noqa: ANN001, ARG001
    output.write_bytes(b"AGE" + source.read_bytes())
    output.chmod(0o600)
    payload = output.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


class PublishWaIrObjectStorageTransferTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        recipient = root / "recipient.txt"
        recipient.write_text("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n")
        recipient.chmod(0o600)
        config = root / "transport.env"
        config.write_text(
            "ARVAN_S3_ACCESS_KEY=access-key\n"
            f"ARVAN_S3_SECRET_KEY={'s' * 40}\n"
            "ARVAN_S3_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir\n"
            "ARVAN_S3_REGION=ir-thr-at1\n"
            "WA_IR_OBJECT_STORAGE_BUCKET=production-sync-coin\n"
            "WA_IR_OBJECT_STORAGE_PREFIX=staging/matrix-transport\n"
            f"WA_IR_AGE_RECIPIENT_FILE={recipient}\n"
            "WA_IR_REMOTE_AGE_IDENTITY=/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt\n"
        )
        config.chmod(0o600)
        return config

    def test_file_is_encrypted_versioned_and_url_is_not_in_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "client.env"
            source.write_text("SECRET=value\n")
            source.chmod(0o600)
            with patch(
                "scripts.publish_wa_ir_object_storage_transfer.encrypt",
                side_effect=_fake_encrypt,
            ):
                descriptor, evidence = publish_file(
                    source,
                    campaign_tag="wwm_0123456789ab",
                    destination="/run/writer-witness-matrix/wwm_0123456789ab/client.env",
                    mode=0o600,
                    config_path=self._config(root),
                    client=_FakeS3(),
                )
            self.assertTrue(descriptor["artifact"]["encrypted"])
            self.assertIn("s3.ir-thr-at1.arvanstorage.ir", descriptor["artifact"]["url"])
            self.assertNotIn("url", evidence)
            self.assertFalse(evidence["presigned_url_persisted"])
            self.assertEqual(evidence["destination_name"], "client.env")

    def test_destination_outside_campaign_is_rejected_before_s3(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "client.env"
            source.write_text("SECRET=value\n")
            source.chmod(0o600)
            with self.assertRaisesRegex(TransferPublicationError, "allowlist"):
                publish_file(
                    source,
                    campaign_tag="wwm_0123456789ab",
                    destination="/root/.ssh/authorized_keys",
                    mode=0o600,
                    config_path=self._config(root),
                    client=_FakeS3(),
                )


if __name__ == "__main__":
    unittest.main()
