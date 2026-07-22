from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_wa_ir_object_storage_preflight import (
    BootstrapError,
    SCHEMA,
    execute,
    load_descriptor,
    remote_command,
)


class RunWaIrObjectStoragePreflightTests(unittest.TestCase):
    def test_direct_script_help_bootstraps_repository_imports(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_wa_ir_object_storage_preflight.py"
        )
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd="/tmp",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def _descriptor(self, root: Path) -> Path:
        payload = {
            "schema": SCHEMA,
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
        path = root / "bootstrap.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_descriptor_and_remote_command_are_object_storage_only(self):
        with tempfile.TemporaryDirectory() as raw:
            payload = load_descriptor(self._descriptor(Path(raw)))
            command = remote_command(payload)
            self.assertIn("urllib.request", command)
            self.assertIn("s3.ir-thr-at1.arvanstorage.ir", command)
            self.assertNotIn("scp", command)
            self.assertLess(len(command.encode()), 65_536)

    def test_descriptor_rejects_non_arvan_url_and_open_permissions(self):
        with tempfile.TemporaryDirectory() as raw:
            path = self._descriptor(Path(raw))
            payload = json.loads(path.read_text())
            payload["manifest"]["url"] = "https://example.com/manifest"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "approved Arvan"):
                load_descriptor(path)
            path.chmod(0o644)
            with self.assertRaisesRegex(BootstrapError, "root-only"):
                load_descriptor(path)

    def test_descriptor_rejects_an_invalid_presigned_lifetime(self):
        with tempfile.TemporaryDirectory() as raw:
            path = self._descriptor(Path(raw))
            payload = json.loads(path.read_text())
            payload["expires_in_seconds"] = 3600
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(BootstrapError, "lifetime"):
                load_descriptor(path)

    def test_execute_uses_ssh_only_for_command_and_redacts_remote_details(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            descriptor = self._descriptor(root)
            identity = root / "id"
            identity.write_text("private-key", encoding="utf-8")
            identity.chmod(0o600)
            completed = subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "status": "wa-ir-preflight-complete",
                        "release_sha": "a" * 40,
                    }
                ),
                "",
            )
            args = argparse.Namespace(
                descriptor=descriptor,
                identity=identity,
                host="95.38.164.29",
                port=22,
                user="ubuntu",
            )
            with patch(
                "scripts.run_wa_ir_object_storage_preflight.subprocess.run",
                return_value=completed,
            ) as run:
                result = execute(args)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "ssh")
            self.assertNotIn("scp", command)
            self.assertIn("ubuntu@95.38.164.29", command)
            self.assertIn("sudo -n", command[-1])
            self.assertNotIn("url", json.dumps(result).lower())
            self.assertEqual(result["payload_transport"], "private-arvan-object-storage")


if __name__ == "__main__":
    unittest.main()
