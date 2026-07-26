from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.object_storage_controller import (
    ObjectStorageControllerError,
    _external_anchor_head,
    store_external_anchor,
)


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise AssertionError("the test anchor must use a distinct object key")
        version = f"version-{len(self.objects) + 1}"
        self.objects[key] = (bytes(kwargs["Body"]), dict(kwargs["Metadata"]), version)
        return {"VersionId": version}

    def get_object(self, *, Bucket: str, Key: str):
        body, metadata, version = self.objects[(Bucket, Key)]
        return {"Body": BytesIO(body), "Metadata": metadata, "VersionId": version}


class FullMatrixExternalAnchorTests(unittest.TestCase):
    def test_versioned_external_anchor_binds_sorted_artifacts_and_readback(self):
        artifacts = [
            {"path": "a.json", "sha256": "a" * 64, "size": 2},
            {"path": "b.json", "sha256": "b" * 64, "size": 3},
        ]
        fake = _FakeS3()
        config = {
            "campaign_id": "12345678-1234-4234-9234-123456789abc",
            "release_sha": "c" * 40,
            "bucket": "private-versioned-bucket",
            "request_key": "full-matrix/campaign/control/request.age",
            "credentials_file": "/root/secure/credentials.json",
        }
        with patch(
            "scripts.full_matrix_live.object_storage_controller.load_controller_config",
            return_value=config,
        ), patch(
            "scripts.full_matrix_live.object_storage_controller.require_private_versioned_bucket"
        ) as require_private:
            result = store_external_anchor(
                Path("/root/secure/controller.json"),
                campaign_id=config["campaign_id"],
                release_sha=config["release_sha"],
                execution_class="shared-host-safe",
                operation_id="22345678-1234-4234-9234-123456789abc",
                artifacts=artifacts,
                client=fake,
            )
        expected = hashlib.sha256(
            f":{'a' * 64}".encode("ascii")
        ).hexdigest()
        expected = hashlib.sha256(f"{expected}:{'b' * 64}".encode("ascii")).hexdigest()
        self.assertEqual(result["status"], "anchored")
        self.assertEqual(result["chain_head"], expected)
        self.assertTrue(result["object_version_id"])
        self.assertIn("/external-anchors/", result["object_key"])
        require_private.assert_called_once_with(fake, bucket=config["bucket"])

    def test_anchor_refuses_unsorted_or_duplicate_artifact_paths(self):
        with self.assertRaises(ObjectStorageControllerError):
            _external_anchor_head(
                [
                    {"path": "b.json", "sha256": "b" * 64, "size": 2},
                    {"path": "a.json", "sha256": "a" * 64, "size": 2},
                ]
            )


if __name__ == "__main__":
    unittest.main()
