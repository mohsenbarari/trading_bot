from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_emergency_ir_object_storage_receive.py"
SPEC = importlib.util.spec_from_file_location("run_emergency_ir_object_storage_receive", MODULE_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def artifact(name: str, size: int) -> dict[str, object]:
    return {
        "url": f"https://s3.ir-thr-at1.arvanstorage.ir/emergency-ir/{name}?X-Amz-Signature=abc",
        "sha256": (name[0] * 64) if name[0] in "abcdef" else "a" * 64,
        "bytes": size,
    }


class RunEmergencyIrObjectStorageReceiveTests(unittest.TestCase):
    def descriptor(self) -> dict[str, object]:
        return {
            "schema": runner.SCHEMA,
            "campaign_id": "20260801T210000Z-emergency-ir-03",
            "expires_in_seconds": 300,
            "bootstrap_provenance": {
                "schema": runner.BOOTSTRAP_PROVENANCE_SCHEMA,
                "publisher_source_revision": "a" * 40,
                "receiver_bundle_sha256": "a" * 64,
                "receiver_bundle_bytes": 4096,
                "signer_key_id": "ed25519-sha256:" + "b" * 64,
            },
            "receiver_bundle": artifact("agent", 4096),
            "manifest": artifact("manifest", 4096),
            "url_map": artifact("urls", 4096),
        }

    def write_descriptor(self, directory: Path, payload: dict[str, object], mode: int = 0o600) -> Path:
        path = directory / "descriptor.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)
        return path

    def test_loads_only_root_style_private_fixed_descriptor_and_builds_bounded_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-bootstrap-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            descriptor_path = self.write_descriptor(directory, self.descriptor())
            payload = runner.load_descriptor(descriptor_path)
            self.assertEqual(payload["campaign_id"], "20260801T210000Z-emergency-ir-03")
            self.assertIn("receive-emergency-ir:20260801T210000Z-emergency-ir-03:", runner.confirmation_phrase(payload))
            self.assertTrue(runner.confirmation_phrase(payload).endswith(":" + "a" * 64))
            command = runner.remote_command(payload)
            self.assertIn("sudo -n -- /usr/bin/python3", command)
            self.assertIn("trading-bot-emergency-bootstrap", command)
            self.assertIn("--expected-publisher-source-revision", runner.REMOTE_BOOTSTRAP)
            self.assertIn(payload["bootstrap_provenance"]["signer_key_id"], command)
            self.assertLess(len(command.encode("utf-8")), 65_536)

    def test_rejects_insecure_descriptor_and_unpinned_target_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-bootstrap-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            insecure = self.write_descriptor(directory, self.descriptor(), mode=0o644)
            with self.assertRaisesRegex(runner.EmergencyBootstrapError, "0600"):
                runner.load_descriptor(insecure)

            secure = self.write_descriptor(directory, self.descriptor())
            bad = self.descriptor()
            bad["receiver_bundle"]["url"] = "https://example.invalid/bad"
            secure.write_text(json.dumps(bad), encoding="utf-8")
            secure.chmod(0o600)
            with self.assertRaisesRegex(runner.EmergencyBootstrapError, "approved Arvan"):
                runner.load_descriptor(secure)

            bad_provenance = self.descriptor()
            bad_provenance["bootstrap_provenance"]["receiver_bundle_sha256"] = "b" * 64
            secure.write_text(json.dumps(bad_provenance), encoding="utf-8")
            secure.chmod(0o600)
            with self.assertRaisesRegex(runner.EmergencyBootstrapError, "differs from its provenance"):
                runner.load_descriptor(secure)

    def test_execute_rejects_a_remote_success_for_a_different_manifest(self) -> None:
        """The controller must bind the remote receipt to its descriptor hash."""

        with tempfile.TemporaryDirectory(prefix="emergency-ir-bootstrap-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            descriptor = self.write_descriptor(directory, self.descriptor())
            identity = directory / "id_ed25519"
            identity.write_text("not-a-real-key\n", encoding="ascii")
            identity.chmod(0o600)
            args = argparse.Namespace(
                host=runner.WA_IR_HOST,
                port=22,
                user=runner.WA_IR_USER,
                identity=identity,
                descriptor=descriptor,
                apply=True,
                confirm=runner.confirmation_phrase(runner.load_descriptor(descriptor)),
            )
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "status": "received-non-authorizing",
                        "campaign_id": self.descriptor()["campaign_id"],
                        "manifest_sha256": "f" * 64,
                        "artifacts": [],
                    }
                ),
                stderr="",
            )
            with patch.object(runner.subprocess, "run", return_value=completed) as remote:
                with self.assertRaisesRegex(runner.EmergencyBootstrapError, "pinned success"):
                    runner.execute(args)
            remote.assert_called_once()

    def test_remote_bootstrap_never_carries_a_payload_file_over_ssh(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("payload_transport", source)
        self.assertIn("ssh_payload_transfer", source)
        self.assertNotIn("core.secure_file_io", source)
        self.assertNotIn("wa_ir_object_storage_preflight_agent", source)
        self.assertNotIn("scp", source)
        self.assertNotIn("rsync", source)

    def test_remote_bootstrap_is_syntactically_valid_and_can_resume_a_sealed_campaign(self) -> None:
        compile(runner.REMOTE_BOOTSTRAP, "<emergency-bootstrap>", "exec")
        self.assertIn("def existing(target,digest,size):", runner.REMOTE_BOOTSTRAP)
        self.assertIn("def bundle_ready(target):", runner.REMOTE_BOOTSTRAP)
        self.assertIn("already-received", (Path(__file__).resolve().parents[1] / "scripts" / "emergency_ir_object_storage_receiver.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
