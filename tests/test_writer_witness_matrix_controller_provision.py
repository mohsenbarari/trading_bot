from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import os
import stat
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


def public_policy() -> dict:
    return {
        "schema": "three-site-human-approval-policy-v1",
        "policy_id": "11111111-1111-4111-8111-111111111111",
        "issuer": {
            "issuer_id": "three-site-witness-approval-service",
            "key_id": "witness-approval-20260722",
            "operator": "mohsen",
            "authenticator_id": "22222222-2222-4222-8222-222222222222",
            "public_key": base64.b64encode(b"\x01" * 32).decode(),
        },
        "actions": [
            {
                "action": "run_writer_witness_matrix",
                "environments": ["staging"],
                "max_ttl_seconds": 600,
            }
        ],
    }


class ControllerProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source_root = self.root / "source"
        self.source_root.mkdir(mode=0o700)
        self.policy = self.source_root / "human-approval-policy.json"
        self.write_policy(public_policy())

    def write_policy(self, payload: dict, *, mode: int = 0o600) -> None:
        self.policy.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.policy.chmod(mode)

    def config(self, **overrides: object):
        values = {
            "human_approval_policy_file": self.policy,
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
        expected = stat.S_ISDIR if mode == 0o700 else stat.S_ISREG
        self.assertTrue(expected(metadata.st_mode))
        self.assertEqual(stat.S_IMODE(metadata.st_mode), mode)
        self.assertEqual(metadata.st_uid, os.geteuid())
        self.assertEqual(metadata.st_gid, os.getegid())

    def test_provisions_only_public_policy_and_controller_directories(self) -> None:
        result = provisioner.provision(self.config())
        installed = self.root / "etc-controller" / "human-approval-policy.json"
        self.assert_owner_path(installed, 0o600)
        self.assertEqual(json.loads(installed.read_text()), public_policy())
        self.assertEqual(result["operator"], "mohsen")
        self.assertFalse(result["issuer_secrets_copied"])
        self.assertRegex(result["human_approval_policy_sha256"], r"^[0-9a-f]{64}$")
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
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_is_idempotent_and_atomically_replaces_a_safe_public_policy(self) -> None:
        first = provisioner.provision(self.config())
        changed = public_policy()
        changed["issuer"]["key_id"] = "witness-approval-20260723"
        self.write_policy(changed)
        second = provisioner.provision(self.config())
        self.assertNotEqual(
            first["human_approval_policy_sha256"], second["human_approval_policy_sha256"]
        )

    def test_rejects_legacy_two_signer_or_secret_bearing_policy(self) -> None:
        legacy = {
            "schema": "three-site-staging-inventory-signers-v1",
            "policy_id": "11111111-1111-4111-8111-111111111111",
            "signers": [],
        }
        self.write_policy(legacy)
        with self.assertRaisesRegex(provisioner.ProvisionError, "policy is invalid"):
            provisioner.provision(self.config())
        secret_bearing = public_policy()
        secret_bearing["totp_secret"] = "MUST-NOT-INSTALL"
        self.write_policy(secret_bearing)
        with self.assertRaisesRegex(provisioner.ProvisionError, "policy is invalid"):
            provisioner.provision(self.config())

    def test_rejects_symlink_hardlink_fifo_and_bad_mode_source(self) -> None:
        alias = self.source_root / "policy-link.json"
        alias.symlink_to(self.policy)
        with self.assertRaisesRegex(provisioner.ProvisionError, "securely open"):
            provisioner.provision(self.config(human_approval_policy_file=alias))
        alias.unlink()
        os.link(self.policy, alias)
        with self.assertRaisesRegex(provisioner.ProvisionError, "owner-controlled"):
            provisioner.provision(self.config())
        alias.unlink()
        self.policy.unlink()
        os.mkfifo(self.policy, 0o600)
        with self.assertRaisesRegex(provisioner.ProvisionError, "owner-controlled"):
            provisioner.provision(self.config())
        self.policy.unlink()
        self.write_policy(public_policy(), mode=0o644)
        with self.assertRaisesRegex(provisioner.ProvisionError, "mode-0600"):
            provisioner.provision(self.config())

    def test_rejects_existing_policy_symlink_or_hardlink(self) -> None:
        config_root = self.root / "etc-controller"
        config_root.mkdir(mode=0o700)
        unrelated = self.root / "unrelated"
        unrelated.write_text("do-not-replace\n")
        unrelated.chmod(0o600)
        target = config_root / "human-approval-policy.json"
        target.symlink_to(unrelated)
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing human approval"):
            provisioner.provision(self.config())
        self.assertEqual(unrelated.read_text(), "do-not-replace\n")
        target.unlink()
        os.link(unrelated, target)
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing human approval"):
            provisioner.provision(self.config())

    def test_replace_failure_never_publishes_an_empty_final_file(self) -> None:
        real_replace = os.replace

        def fail_policy_replace(source: object, destination: object) -> None:
            if Path(destination).name == "human-approval-policy.json":
                raise OSError("simulated power loss before rename")
            real_replace(source, destination)

        with mock.patch.object(provisioner.os, "replace", side_effect=fail_policy_replace):
            with self.assertRaisesRegex(OSError, "simulated power loss"):
                provisioner.provision(self.config())
        target = self.root / "etc-controller" / "human-approval-policy.json"
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(".human-approval-policy.json.*")), [])

    def test_test_mode_rejects_production_roots_and_root_traversal(self) -> None:
        with self.assertRaisesRegex(provisioner.ProvisionError, "must not target"):
            provisioner.provision(
                self.config(config_root=provisioner.PRODUCTION_CONFIG_ROOT)
            )
        with self.assertRaisesRegex(provisioner.ProvisionError, "traversal"):
            provisioner.provision(
                self.config(config_root=self.root / "state-controller" / ".." / "bad")
            )

    def test_cli_test_mode_uses_public_policy_and_override_roots(self) -> None:
        stdout = io.StringIO()
        arguments = [
            "--human-approval-policy-file", str(self.policy),
            "--test-mode",
            "--config-root", str(self.root / "cli-etc"),
            "--controller-root", str(self.root / "cli-state"),
            "--runtime-root", str(self.root / "cli-run"),
            "--owner-uid", str(os.geteuid()),
            "--owner-gid", str(os.getegid()),
        ]
        with mock.patch("sys.stdout", stdout):
            self.assertEqual(provisioner.main(arguments), 0)
        self.assertIn('"issuer_secrets_copied": false', stdout.getvalue())

    def test_non_root_production_execution_is_rejected(self) -> None:
        with mock.patch.object(provisioner.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(provisioner.ProvisionError, "must run as root"):
                provisioner._validate_execution_context(
                    provisioner.ProvisionConfig(human_approval_policy_file=self.policy)
                )


if __name__ == "__main__":
    unittest.main()
