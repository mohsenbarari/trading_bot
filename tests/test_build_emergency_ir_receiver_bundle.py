from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "build_emergency_ir_receiver_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_receiver_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bundle
SPEC.loader.exec_module(bundle)


class BuildEmergencyIrReceiverBundleTests(unittest.TestCase):
    def public_key(self, directory: Path) -> Path:
        key = Ed25519PrivateKey.generate().public_key()
        path = directory / "signing-public.key"
        path.write_text(
            base64.b64encode(
                key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            ).decode("ascii") + "\n",
            encoding="ascii",
        )
        path.chmod(0o600)
        return path

    def test_builds_exact_small_bundle_with_only_allowlisted_members(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-receiver-bundle-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "receiver.tar.gz"
            digest, size = bundle.build_bundle(
                repo=REPO_ROOT, signing_public_key=self.public_key(directory), output=output
            )
            self.assertEqual(len(digest), 64)
            self.assertEqual(size, output.stat().st_size)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with tarfile.open(output, "r:gz") as archive:
                self.assertEqual(
                    {member.name for member in archive.getmembers()},
                    {target for _, target in bundle.BUNDLE_MEMBERS} | {"signing-public.key"},
                )
                self.assertTrue(all(member.isreg() for member in archive.getmembers()))

    def test_refuses_to_overwrite_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-receiver-bundle-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "receiver.tar.gz"
            public_key = self.public_key(directory)
            bundle.build_bundle(repo=REPO_ROOT, signing_public_key=public_key, output=output)
            with self.assertRaisesRegex(bundle.ReceiverBundleError, "overwrite"):
                bundle.build_bundle(repo=REPO_ROOT, signing_public_key=public_key, output=output)


if __name__ == "__main__":
    unittest.main()
