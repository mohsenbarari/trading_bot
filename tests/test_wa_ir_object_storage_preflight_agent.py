from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import wa_ir_object_storage_preflight_agent as agent


class WaIrObjectStoragePreflightAgentTests(unittest.TestCase):
    def test_manifest_and_file_transfer_parsers_remain_offline(self):
        with tempfile.TemporaryDirectory() as raw:
            manifest = Path(raw) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": agent.SCHEMA,
                        "role": "webapp-ir",
                        "release_sha": "a" * 40,
                        "secure_materials_dir": "/root/secure-envs/trading-bot/three-site-staging-aaaaaaaa",
                        "release_bundle": {"url": "https://s3.ir-thr-at1.arvanstorage.ir/private/release", "sha256": "b" * 64, "bytes": 12},
                        "role_materials": {"url": "https://s3.ir-thr-at1.arvanstorage.ir/private/materials", "sha256": "c" * 64, "bytes": 12},
                        "preflight_output": "/root/secure-envs/trading-bot/three-site-staging-aaaaaaaa/webapp-ir-fresh-preflight.json",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(agent.load_manifest(manifest)["role"], "webapp-ir")
        encoded = base64.b64encode(
            json.dumps(
                {
                    "schema": agent.FILE_TRANSFER_SCHEMA,
                    "role": "webapp-ir",
                    "campaign_tag": "wwm_0123456789ab",
                    "destination": "/run/writer-witness-matrix/wwm_0123456789ab/client.env",
                    "mode": 0o600,
                    "artifact": {
                        "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/client.age?sig=x",
                        "sha256": "a" * 64,
                        "bytes": 100,
                        "encrypted": True,
                        "ciphertext_sha256": "b" * 64,
                        "ciphertext_bytes": 300,
                    },
                }
            ).encode()
        ).decode()
        self.assertEqual(agent.load_file_transfer_manifest(encoded)["role"], "webapp-ir")

    def test_effectful_helpers_block_before_network_subprocess_or_write(self):
        artifact = {"url": "https://s3.ir-thr-at1.arvanstorage.ir/private/item", "sha256": "a" * 64, "bytes": 1}
        with patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError):
            with self.assertRaisesRegex(agent.AgentError, "retired"):
                agent.download(artifact, label="item", output=Path("/unsafe/output"))
        with patch.object(agent.subprocess, "run", side_effect=AssertionError):
            with self.assertRaisesRegex(agent.AgentError, "retired"):
                agent.run_preflight(
                    release_dir=Path("/unsafe/release"),
                    secure_dir=Path("/unsafe/secure"),
                    output=Path("/unsafe/output"),
                )
        with self.assertRaisesRegex(agent.AgentError, "retired"):
            agent.receive_file_transfer({"destination": "/unsafe/output", "artifact": {}})

    def test_cli_blocks_before_parser(self):
        with patch.object(agent.argparse, "ArgumentParser", side_effect=AssertionError):
            self.assertEqual(agent.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
