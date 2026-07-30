"""Focused tests for the fixed controller source-receive age identity."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


identity = _load(
    "test_manage_controller_source_receive_identity",
    "manage_controller_source_receive_identity.py",
)


RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
OTHER_RECIPIENT = "age1pppppppppppppppppppppppppppppppppppppppp"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class ControllerSourceReceiveIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-source-receive-identity-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.campaign_root = self.root / "campaigns"
        self.campaign_root.mkdir(mode=0o700)
        self.campaign_root.chmod(0o700)
        self.campaign_id = "controller-source-identity-20260730"
        self.campaign_directory = self.campaign_root / self.campaign_id
        self.campaign_directory.mkdir(mode=0o700)
        self.campaign_directory.chmod(0o700)
        self.binding_path = self._write_binding(self.campaign_directory, self.campaign_id)
        self.test_age_keygen = self.root / "age-keygen-test-only"
        self.test_age_keygen.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.test_age_keygen.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_binding(self, campaign_directory: Path, campaign_id: str) -> Path:
        phase = campaign_directory / identity.binding.SOURCE_PHASE_DIRECTORY
        phase.mkdir(mode=0o700)
        phase.chmod(0o700)
        value = identity.binding.build_campaign_binding(
            campaign_id=campaign_id,
            application_release_sha="a" * 40,
            application_release_tree="b" * 40,
            expected_alembic_revision="c" * 12,
            control_commit="d" * 40,
            control_tree="e" * 40,
        )
        return _private(phase / identity.binding.CAMPAIGN_BINDING_FILENAME, _canonical(value))

    def _bootstrap(self, *, recipient: str = RECIPIENT) -> dict[str, str]:
        def fake_generator(_binary: Path, descriptor: int) -> None:
            os.write(
                descriptor,
                b"# created: test-only\n# public key: " + recipient.encode("ascii") + b"\nAGE-SECRET-KEY-1TESTONLY\n",
            )

        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            mock.patch.object(identity, "_run_age_keygen_to_descriptor", side_effect=fake_generator) as generated,
            mock.patch.object(identity, "derive_recipient", return_value=recipient) as derived,
        ):
            result = identity.plan_or_apply_identity_bootstrap(
                campaign_binding_path=self.binding_path,
                apply=True,
            )
        self.assertEqual(1, generated.call_count)
        self.assertGreaterEqual(2, derived.call_count)
        return result

    def _verified(self, *, recipient: str = RECIPIENT):
        return mock.patch.object(identity, "derive_recipient", return_value=recipient)

    def _layout(self):
        with mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root):
            return identity.identity_layout_for_campaign_binding(self.binding_path)

    def test_default_is_a_plan_and_never_generates_or_creates_the_identity_directory(self) -> None:
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            mock.patch.object(identity, "_run_age_keygen_to_descriptor") as generated,
        ):
            result = identity.plan_or_apply_identity_bootstrap(
                campaign_binding_path=self.binding_path,
            )
        self.assertEqual("planned", result["status"])
        self.assertEqual(self.campaign_id, result["campaign_id"])
        self.assertNotIn("recipient", result)
        self.assertFalse((self.campaign_directory / identity.CONTROLLER_DIRECTORY_NAME).exists())
        generated.assert_not_called()

    def test_apply_creates_only_fixed_root_private_paths_and_nonsecret_receipt(self) -> None:
        result = self._bootstrap()
        layout = self._layout()

        self.assertEqual("created", result["status"])
        self.assertEqual(RECIPIENT, result["recipient"])
        self.assertEqual(identity.key_id_for_recipient(RECIPIENT), result["key_id"])
        self.assertEqual(0o700, stat.S_IMODE(layout.controller_directory.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(layout.identity_directory.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(layout.identity_path.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(layout.receipt_path.stat().st_mode))
        self.assertIn(b"AGE-SECRET-KEY", layout.identity_path.read_bytes())
        receipt_payload = layout.receipt_path.read_bytes()
        receipt = json.loads(receipt_payload)
        self.assertEqual(receipt_payload, _canonical(receipt))
        self.assertEqual(
            {
                "schema",
                "status",
                "campaign_id",
                "campaign_binding_sha256",
                "recipient",
                "key_id",
            },
            set(receipt),
        )
        self.assertEqual(identity.IDENTITY_RECEIPT_SCHEMA, receipt["schema"])
        self.assertEqual(RECIPIENT, receipt["recipient"])
        self.assertNotIn(b"AGE-SECRET-KEY", receipt_payload)
        self.assertNotIn("identity_path", receipt)

    def test_apply_is_create_only_and_preserves_the_original_identity_and_receipt(self) -> None:
        self._bootstrap()
        layout = self._layout()
        original_identity = layout.identity_path.read_bytes()
        original_receipt = layout.receipt_path.read_bytes()
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            mock.patch.object(identity, "_run_age_keygen_to_descriptor") as generated,
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "already exists and will not be reused"):
                identity.plan_or_apply_identity_bootstrap(
                    campaign_binding_path=self.binding_path,
                    apply=True,
                )
        generated.assert_not_called()
        self.assertEqual(original_identity, layout.identity_path.read_bytes())
        self.assertEqual(original_receipt, layout.receipt_path.read_bytes())

        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "already exists and will not be reused"):
                identity.plan_or_apply_identity_bootstrap(campaign_binding_path=self.binding_path)

    def test_test_only_oversized_generator_and_recipient_output_are_bounded(self) -> None:
        self._bootstrap()
        layout = self._layout()
        self.test_age_keygen.write_text("#!/bin/sh\nhead -c 270336 /dev/zero\n", encoding="ascii")
        self.test_age_keygen.chmod(0o700)
        partial_identity = layout.identity_directory / "oversized-test-only.agekey"
        with mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "generated controller source receive identity is unsafe"):
                identity._create_identity_file(partial_identity, binary=self.test_age_keygen)
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "recipient output is unsafe"):
                identity.derive_recipient(layout.identity_path)
        self.assertTrue(partial_identity.is_file())
        self.assertLessEqual(partial_identity.stat().st_size, identity.MAXIMUM_IDENTITY_BYTES)
        self.assertGreater(partial_identity.stat().st_size, 0)

    def test_verify_re_reads_the_receipt_and_rejects_recipient_binding_and_key_id_mismatches(self) -> None:
        self._bootstrap()
        layout = self._layout()
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            verified = identity.load_verified_identity(campaign_binding_path=self.binding_path)
        self.assertEqual(RECIPIENT, verified.recipient)
        self.assertEqual(identity.key_id_for_recipient(RECIPIENT), verified.key_id)

        value = json.loads(layout.receipt_path.read_bytes())
        value["recipient"] = OTHER_RECIPIENT
        value["key_id"] = identity.key_id_for_recipient(OTHER_RECIPIENT)
        _private(layout.receipt_path, _canonical(value))
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "does not match its receipt"):
                identity.load_verified_identity(campaign_binding_path=self.binding_path)

        value["recipient"] = RECIPIENT
        value["key_id"] = "0" * 64
        _private(layout.receipt_path, _canonical(value))
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "key identifier"):
                identity.load_verified_identity(campaign_binding_path=self.binding_path)

    def test_verify_rejects_a_receipt_bound_to_a_different_campaign_binding(self) -> None:
        self._bootstrap()
        layout = self._layout()
        value = json.loads(layout.receipt_path.read_bytes())
        value["campaign_binding_sha256"] = "0" * 64
        _private(layout.receipt_path, _canonical(value))
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "binding does not match"):
                identity.load_verified_identity(campaign_binding_path=self.binding_path)

    def test_rejects_unsafe_binary_identity_and_receipt_without_an_external_generator(self) -> None:
        self.test_age_keygen.chmod(0o777)
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            mock.patch.object(identity, "_run_age_keygen_to_descriptor") as generated,
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "age-keygen binary is unsafe"):
                identity.plan_or_apply_identity_bootstrap(campaign_binding_path=self.binding_path)
        generated.assert_not_called()
        self.test_age_keygen.chmod(0o700)
        self._bootstrap()
        layout = self._layout()
        layout.identity_path.chmod(0o640)
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "identity is unsafe"):
                identity.load_verified_identity(campaign_binding_path=self.binding_path)
        layout.identity_path.chmod(0o600)
        layout.receipt_path.chmod(0o644)
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", self.campaign_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
            self._verified(),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "receipt is unsafe"):
                identity.load_verified_identity(campaign_binding_path=self.binding_path)

    def test_rejects_old_or_wrong_campaign_binding_paths_and_parser_has_no_identity_override(self) -> None:
        wrong_root = self.root / "wrong-campaign-root"
        wrong_root.mkdir(mode=0o700)
        wrong_root.chmod(0o700)
        with (
            mock.patch.object(identity, "CAMPAIGNS_ROOT", wrong_root),
            mock.patch.object(identity, "AGE_KEYGEN_BINARY", self.test_age_keygen),
        ):
            with self.assertRaisesRegex(identity.ControllerSourceReceiveIdentityError, "not installed at its fixed campaign path"):
                identity.plan_or_apply_identity_bootstrap(campaign_binding_path=self.binding_path)

        argv = ["bootstrap", "--campaign-binding", str(self.binding_path)]
        parsed = identity._parser().parse_args(argv)
        self.assertFalse(hasattr(parsed, "identity_path"))
        self.assertFalse(hasattr(parsed, "age_keygen_binary"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            identity._parser().parse_args(argv + ["--identity-path", "/tmp/old.agekey"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
