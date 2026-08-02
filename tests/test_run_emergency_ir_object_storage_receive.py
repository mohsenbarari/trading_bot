from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_emergency_ir_object_storage_receive.py"
REPO_ROOT = MODULE_PATH.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SPEC = importlib.util.spec_from_file_location("run_emergency_ir_object_storage_receive", MODULE_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

from scripts import build_emergency_ir_receiver_bundle as receiver_bundle


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
        self.assertIn("scripts/emergency_ir_standalone_activate.py", runner.REMOTE_BOOTSTRAP)
        self.assertIn("def bundled_key_id(path):", runner.REMOTE_BOOTSTRAP)
        self.assertIn("receiver bundle signing public key does not match descriptor", runner.REMOTE_BOOTSTRAP)
        self.assertIn('receiver=campaign_root/"receiver"', runner.REMOTE_BOOTSTRAP)
        self.assertNotIn('(\"receiver-\"+bundle_hash)', runner.REMOTE_BOOTSTRAP)
        self.assertNotIn("bundle_hash[:16]", runner.REMOTE_BOOTSTRAP)
        self.assertNotIn("os.rename(temporary,target)", runner.REMOTE_BOOTSTRAP)
        self.assertIn("try: target.mkdir(mode=0o700)", runner.REMOTE_BOOTSTRAP)
        self.assertIn("refusing to overwrite receiver bundle directory", runner.REMOTE_BOOTSTRAP)
        raw_allowed = [
            ast.literal_eval(value)
            for value in re.findall(r"(?m)^ allowed=(\{[^\n]+\})$", runner.REMOTE_BOOTSTRAP)
        ]
        expected_allowed = {target for _source, target in receiver_bundle.BUNDLE_MEMBERS} | {
            "signing-public.key"
        }
        self.assertEqual(raw_allowed, [expected_allowed, expected_allowed])
        self.assertNotIn("scripts/__init__.py", expected_allowed)
        self.assertIn("already-received", (Path(__file__).resolve().parents[1] / "scripts" / "emergency_ir_object_storage_receiver.py").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.geteuid() == 0, "raw Emergency bootstrap is intentionally root-only")
    def test_raw_bootstrap_rejects_a_bundle_with_the_wrong_pinned_public_key_before_execution(self) -> None:
        """A hash-valid archive cannot swap the public key used by the receiver."""

        def root_controlled_directory(path: Path) -> bool:
            current = Path("/")
            for component in path.resolve().parts[1:]:
                current /= component
                try:
                    state = current.lstat()
                except OSError:
                    return False
                if (
                    state.st_uid != 0
                    or not stat.S_ISDIR(state.st_mode)
                    or stat.S_ISLNK(state.st_mode)
                    or stat.S_IMODE(state.st_mode) & 0o022
                ):
                    return False
            return True

        with tempfile.TemporaryDirectory(prefix="emergency-ir-raw-source-") as raw:
            source = Path(raw)
            source.chmod(0o700)
            runtime_directory: tempfile.TemporaryDirectory[str] | None = None
            for candidate in (Path("/run"), Path.cwd(), *Path.cwd().parents):
                if not root_controlled_directory(candidate):
                    continue
                try:
                    runtime_directory = tempfile.TemporaryDirectory(
                        prefix="emergency-ir-raw-runtime-", dir=candidate
                    )
                except OSError:
                    continue
                break
            if runtime_directory is None:
                self.skipTest("a root-owned test namespace is unavailable")
            with runtime_directory as runtime_raw:
                runtime = Path(runtime_raw)
                marker = runtime / "receiver-was-executed"
                expected_key = b"x" * 32
                bundled_key = b"y" * 32
                members = {
                    "run_receiver.py": (
                        "from pathlib import Path\n"
                        f"Path({json.dumps(str(marker))}).write_text('unexpected', encoding='utf-8')\n"
                    ).encode("utf-8"),
                    "signing-public.key": base64.b64encode(bundled_key) + b"\n",
                    "scripts/emergency_ir_object_storage_manifest.py": b"placeholder-manifest\n",
                    "scripts/emergency_ir_object_storage_receiver.py": b"placeholder-receiver\n",
                    "scripts/emergency_ir_standalone_activate.py": b"placeholder-activator\n",
                }
                bundle = source / "receiver.tar.gz"
                with tarfile.open(bundle, "w:gz") as archive:
                    for name, payload in members.items():
                        entry = tarfile.TarInfo(name)
                        entry.size = len(payload)
                        entry.mode = 0o600
                        archive.addfile(entry, io.BytesIO(payload))
                sealed_manifest = source / "sealed-manifest.json"
                sealed_manifest.write_bytes(b"sealed-manifest")
                url_map = source / "presigned-urls.json"
                url_map.write_bytes(b"url-map")
                for path in (bundle, sealed_manifest, url_map):
                    path.chmod(0o600)

                def args_for(path: Path) -> tuple[str, str, str]:
                    payload = path.read_bytes()
                    return path.as_uri(), hashlib.sha256(payload).hexdigest(), str(len(payload))

                bundle_url, bundle_hash, bundle_bytes = args_for(bundle)
                manifest_url, manifest_hash, manifest_bytes = args_for(sealed_manifest)
                url_map_url, url_map_hash, url_map_bytes = args_for(url_map)
                # ``file://`` is only a no-network test transport. Its response
                # exposes ``status=None`` while the production HTTPS handler
                # returns 200, so relax that one transport assertion without
                # changing the bootstrap's signer/layout code under test.
                test_bootstrap = runner.REMOTE_BOOTSTRAP.replace(
                    'getattr(response,"status",200)!=200',
                    'getattr(response,"status",200) not in (None,200)',
                )
                command = [
                    sys.executable,
                    "-c",
                    test_bootstrap,
                    str(runtime),
                    "20260801T210000Z-emergency-ir-03",
                    bundle_url,
                    bundle_hash,
                    bundle_bytes,
                    manifest_url,
                    manifest_hash,
                    manifest_bytes,
                    url_map_url,
                    url_map_hash,
                    url_map_bytes,
                    "a" * 40,
                    bundle_hash,
                    bundle_bytes,
                    "ed25519-sha256:" + hashlib.sha256(expected_key).hexdigest(),
                ]
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("signing public key does not match descriptor", completed.stderr)
                self.assertFalse(marker.exists())

                receiver_directory = runtime / "20260801T210000Z-emergency-ir-03" / "receiver"
                sentinel = receiver_directory / "forensic-sentinel"
                sentinel.write_text("must-not-be-replaced\n", encoding="utf-8")
                sentinel.chmod(0o600)
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("existing receiver bundle is incomplete", completed.stderr)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "must-not-be-replaced\n")


if __name__ == "__main__":
    unittest.main()
