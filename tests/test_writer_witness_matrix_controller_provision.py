from __future__ import annotations

import base64
import importlib.util
import io
from pathlib import Path
import os
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_writer_witness_matrix_controller.py"
SPEC = importlib.util.spec_from_file_location("writer_witness_matrix_controller_provision", SCRIPT)
assert SPEC and SPEC.loader
provisioner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provisioner
SPEC.loader.exec_module(provisioner)


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def ed25519_public_key(seed: int, comment: str = "test-key") -> bytes:
    key_type = b"ssh-ed25519"
    blob = ssh_string(key_type) + ssh_string(bytes([seed]) * 32)
    encoded = base64.b64encode(blob)
    return key_type + b" " + encoded + b" " + comment.encode("ascii") + b"\n"


class ControllerProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.keys = self.root / "keys"
        self.keys.mkdir(mode=0o700)
        self.observer_key = self.keys / "observer.pub"
        self.commander_key = self.keys / "commander.pub"
        self.write_key(self.observer_key, ed25519_public_key(1, "observer-private-device"))
        self.write_key(self.commander_key, ed25519_public_key(2, "commander-private-device"))

    @staticmethod
    def write_key(path: Path, raw: bytes, mode: int = 0o600) -> None:
        path.write_bytes(raw)
        path.chmod(mode)

    def config(self, **overrides: object):
        values = {
            "observer_identity": "abort-observer",
            "observer_public_key_file": self.observer_key,
            "incident_commander_identity": "incident-commander",
            "incident_commander_public_key_file": self.commander_key,
            "config_root": self.root / "etc-controller",
            "controller_root": self.root / "state-controller",
            "runtime_root": self.root / "run-controller",
            "owner_uid": os.geteuid(),
            "owner_gid": os.getegid(),
            "test_mode": True,
        }
        values.update(overrides)
        return provisioner.ProvisionConfig(**values)

    def assert_owner_path(self, path: Path, mode: int) -> None:
        metadata = path.lstat()
        self.assertTrue(stat.S_ISDIR(metadata.st_mode) if mode == 0o700 else stat.S_ISREG(metadata.st_mode))
        self.assertEqual(stat.S_IMODE(metadata.st_mode), mode)
        self.assertEqual(metadata.st_uid, os.geteuid())
        self.assertEqual(metadata.st_gid, os.getegid())

    def test_provisions_distinct_policy_and_all_controller_directories(self) -> None:
        result = provisioner.provision(self.config())

        policy = self.root / "etc-controller" / "allowed_signers"
        self.assert_owner_path(policy, 0o600)
        lines = policy.read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].split()[0], "abort-observer")
        self.assertEqual(lines[1].split()[0], "incident-commander")
        self.assertEqual(len(lines[0].split()), 3)
        self.assertEqual(len(lines[1].split()), 3)
        self.assertNotIn("private-device", policy.read_text(encoding="ascii"))
        for path in (
            self.root / "etc-controller",
            self.root / "state-controller",
            self.root / "state-controller" / "campaigns",
            self.root / "state-controller" / "campaigns" / "consumed-approvals",
            self.root / "state-controller" / "campaigns" / "consumed-preflights",
            self.root / "state-controller" / "runs",
            self.root / "run-controller",
        ):
            self.assert_owner_path(path, 0o700)
        self.assertFalse(result["private_keys_copied"])
        self.assertRegex(result["allowed_signers_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            result["observer_key_fingerprint"], result["incident_commander_key_fingerprint"]
        )
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_is_idempotent_and_atomically_replaces_a_safe_policy(self) -> None:
        first = provisioner.provision(self.config())
        self.write_key(self.commander_key, ed25519_public_key(3))
        second = provisioner.provision(self.config())
        self.assertNotEqual(first["allowed_signers_sha256"], second["allowed_signers_sha256"])
        self.assert_owner_path(self.root / "etc-controller" / "allowed_signers", 0o600)

    def test_rejects_duplicate_identity_case_insensitively(self) -> None:
        with self.assertRaisesRegex(provisioner.ProvisionError, "identities must be distinct"):
            provisioner.provision(
                self.config(incident_commander_identity="ABORT-OBSERVER")
            )

    def test_rejects_unsafe_identity(self) -> None:
        for identity in ("observer,commander", "../observer", " observer", "observer role", ""):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(provisioner.ProvisionError, "safe OpenSSH"):
                    provisioner.provision(self.config(observer_identity=identity))

    def test_rejects_duplicate_key_blob_even_when_comments_differ(self) -> None:
        self.write_key(self.commander_key, ed25519_public_key(1, "different-comment"))
        with self.assertRaisesRegex(provisioner.ProvisionError, "different public keys"):
            provisioner.provision(self.config())

    def test_rejects_symbolic_link_public_key(self) -> None:
        alias = self.keys / "observer-link.pub"
        alias.symlink_to(self.observer_key)
        with self.assertRaisesRegex(provisioner.ProvisionError, "owner-controlled"):
            provisioner.provision(self.config(observer_public_key_file=alias))

    def test_rejects_hard_link_public_key(self) -> None:
        alias = self.keys / "observer-hardlink.pub"
        os.link(self.observer_key, alias)
        with self.assertRaisesRegex(provisioner.ProvisionError, "owner-controlled"):
            provisioner.provision(self.config())

    def test_rejects_fifo_without_blocking(self) -> None:
        fifo = self.keys / "observer.fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(provisioner.ProvisionError, "owner-controlled"):
            provisioner.provision(self.config(observer_public_key_file=fifo))

    def test_rejects_public_key_with_bad_mode(self) -> None:
        self.observer_key.chmod(0o644)
        with self.assertRaisesRegex(provisioner.ProvisionError, "mode-0600"):
            provisioner.provision(self.config())

    def test_rejects_malformed_and_mismatched_openssh_keys(self) -> None:
        cases = (
            b"not-a-key value\n",
            b"ssh-ed25519 !!!\n",
            b"ssh-rsa " + ed25519_public_key(9).split()[1] + b"\n",
            b"ssh-dss AAAAB3NzaC1kc3MAAACB\n",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.write_key(self.observer_key, raw)
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.provision(self.config())

    def test_rejects_existing_policy_symlink_or_hardlink(self) -> None:
        config_root = self.root / "etc-controller"
        config_root.mkdir(mode=0o700)
        unrelated = self.root / "unrelated"
        unrelated.write_text("do-not-replace\n", encoding="ascii")
        unrelated.chmod(0o600)
        policy = config_root / "allowed_signers"
        policy.symlink_to(unrelated)
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing allowed_signers"):
            provisioner.provision(self.config())
        self.assertEqual(unrelated.read_text(encoding="ascii"), "do-not-replace\n")

        policy.unlink()
        os.link(unrelated, policy)
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing allowed_signers"):
            provisioner.provision(self.config())
        self.assertEqual(unrelated.read_text(encoding="ascii"), "do-not-replace\n")

    def test_replace_failure_never_publishes_an_empty_final_file(self) -> None:
        real_replace = os.replace

        def fail_policy_replace(source: object, destination: object) -> None:
            if Path(destination).name == "allowed_signers":
                raise OSError("simulated power loss before rename")
            real_replace(source, destination)

        with mock.patch.object(provisioner.os, "replace", side_effect=fail_policy_replace):
            with self.assertRaisesRegex(OSError, "simulated power loss"):
                provisioner.provision(self.config())
        policy = self.root / "etc-controller" / "allowed_signers"
        self.assertFalse(policy.exists())
        self.assertEqual(list((self.root / "etc-controller").glob(".allowed_signers.*")), [])

    def test_fsyncs_policy_and_parent_directories(self) -> None:
        real_fsync = os.fsync
        descriptors: list[int] = []

        def recording_fsync(descriptor: int) -> None:
            descriptors.append(descriptor)
            real_fsync(descriptor)

        with mock.patch.object(provisioner.os, "fsync", side_effect=recording_fsync):
            provisioner.provision(self.config())
        self.assertGreaterEqual(len(descriptors), 15)

    def test_test_mode_rejects_production_roots_and_root_traversal(self) -> None:
        with self.assertRaisesRegex(provisioner.ProvisionError, "must not target"):
            provisioner.provision(
                self.config(config_root=provisioner.PRODUCTION_CONFIG_ROOT)
            )
        with self.assertRaisesRegex(provisioner.ProvisionError, "traversal"):
            provisioner.provision(
                self.config(config_root=self.root / "state-controller" / ".." / "bad")
            )

    def test_cli_test_mode_uses_override_owner_and_roots(self) -> None:
        stdout = io.StringIO()
        arguments = [
            "--observer-identity", "observer",
            "--observer-public-key-file", str(self.observer_key),
            "--incident-commander-identity", "commander",
            "--incident-commander-public-key-file", str(self.commander_key),
            "--test-mode",
            "--config-root", str(self.root / "cli-etc"),
            "--controller-root", str(self.root / "cli-state"),
            "--runtime-root", str(self.root / "cli-run"),
            "--owner-uid", str(os.geteuid()),
            "--owner-gid", str(os.getegid()),
        ]
        with mock.patch("sys.stdout", stdout):
            self.assertEqual(provisioner.main(arguments), 0)
        self.assertIn('"private_keys_copied": false', stdout.getvalue())

    def test_non_root_production_execution_is_rejected(self) -> None:
        with mock.patch.object(provisioner.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(provisioner.ProvisionError, "must run as root"):
                provisioner._validate_execution_context(
                    provisioner.ProvisionConfig(
                        observer_identity="observer",
                        observer_public_key_file=self.observer_key,
                        incident_commander_identity="commander",
                        incident_commander_public_key_file=self.commander_key,
                    )
                )


if __name__ == "__main__":
    unittest.main()
