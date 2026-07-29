from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import provision_writer_witness_object_storage as provisioner


class ProvisionWriterWitnessObjectStorageTests(unittest.TestCase):
    def test_pure_serializers_remain_offline(self):
        self.assertEqual(
            provisioner.content_headers(b"payload", "application/octet-stream")["content-type"],
            "application/octet-stream",
        )
        root = provisioner.ET.fromstring("<Root><Value>ok</Value></Root>")
        self.assertEqual(provisioner.xml_text(root, "Value"), "ok")

    def test_credential_provider_and_output_boundaries_block_before_io(self):
        with self.assertRaisesRegex(provisioner.ProvisioningError, "retired"):
            provisioner.read_env(Path("/unsafe/credentials.env"), required=())
        client = provisioner.SignedS3Client(
            endpoint="https://object-storage.invalid",
            region="test",
            credential=provisioner.Credential("access", "secret"),
        )
        with patch.object(provisioner, "urlopen", side_effect=AssertionError):
            with self.assertRaisesRegex(provisioner.ProvisioningError, "retired"):
                client.request("GET", "/")
        with self.assertRaisesRegex(provisioner.ProvisioningError, "retired"):
            provisioner.create_bucket(client, "unsafe-bucket", "test")
        with self.assertRaisesRegex(provisioner.ProvisioningError, "retired"):
            provisioner.write_bucket_env(
                Path("/unsafe/writer-witness-bucket.env"),
                bucket="unsafe-bucket",
                endpoint="https://object-storage.invalid",
                region="test",
            )

    def test_cli_blocks_before_parser_or_credential_read(self):
        with (
            patch.object(provisioner, "parse_args", side_effect=AssertionError),
            patch.object(provisioner, "read_env", side_effect=AssertionError),
        ):
            self.assertEqual(provisioner.main(), 2)


if __name__ == "__main__":
    unittest.main()
