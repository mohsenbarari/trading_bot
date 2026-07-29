from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)
from scripts import run_wa_ir_object_storage_preflight as bootstrap


class RunWaIrObjectStoragePreflightTests(unittest.TestCase):
    def test_descriptor_parser_and_command_serializer_remain_offline(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bootstrap.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": bootstrap.SCHEMA,
                        "release_sha": "a" * 40,
                        "expires_in_seconds": 900,
                        "agent": {
                            "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/agent?sig=x",
                            "sha256": "b" * 64,
                            "bytes": 123,
                        },
                        "manifest": {
                            "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/manifest?sig=x",
                            "sha256": "c" * 64,
                            "bytes": 456,
                        },
                    }
                ),
                encoding="utf-8",
            )
            path.chmod(0o600)
            payload = bootstrap.load_descriptor(path)
            self.assertIn("urllib.request", bootstrap.remote_command(payload))

    def test_execute_blocks_before_descriptor_identity_or_ssh(self):
        args = argparse.Namespace(
            descriptor=Path("/unsafe/descriptor"),
            identity=Path("/unsafe/identity"),
            host="not-read",
            port=0,
            user="not-read",
        )
        with (
            patch.object(bootstrap, "load_descriptor", side_effect=AssertionError),
            patch.object(bootstrap.subprocess, "run", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                bootstrap.execute(args)

    def test_cli_blocks_before_parser(self):
        with patch.object(bootstrap.argparse, "ArgumentParser", side_effect=AssertionError):
            self.assertEqual(bootstrap.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
