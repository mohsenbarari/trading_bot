"""Focused tests for the fixed controller campaign Ed25519 signing key."""

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


signing = _load(
    "test_manage_controller_campaign_signing_key",
    "manage_controller_campaign_signing_key.py",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class ControllerCampaignSigningKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-campaign-signing-key-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.campaign_root = self.root / "campaigns"
        self.campaign_root.mkdir(mode=0o700)
        self.campaign_root.chmod(0o700)
        self.campaign_id = "controller-signing-key-20260730"
        self.campaign_directory = self.campaign_root / self.campaign_id
        self.campaign_directory.mkdir(mode=0o700)
        self.campaign_directory.chmod(0o700)
        self.binding_path = self._write_binding(self.campaign_directory, self.campaign_id)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_binding(self, campaign_directory: Path, campaign_id: str) -> Path:
        phase = campaign_directory / signing.binding.SOURCE_PHASE_DIRECTORY
        phase.mkdir(mode=0o700)
        phase.chmod(0o700)
        value = signing.binding.build_campaign_binding(
            campaign_id=campaign_id,
            application_release_sha="a" * 40,
            application_release_tree="b" * 40,
            expected_alembic_revision="c" * 12,
            control_commit="d" * 40,
            control_tree="e" * 40,
        )
        return _private(phase / signing.binding.CAMPAIGN_BINDING_FILENAME, _canonical(value))

    def _enroll(self) -> dict[str, object]:
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            return signing.enroll_campaign_signing_key(
                campaign_binding_path=self.binding_path,
                apply=True,
            )

    def _layout(self):
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            return signing.signing_key_layout_for_campaign_binding(self.binding_path)

    def _verify(self):
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            return signing.load_verified_campaign_signing_key(campaign_binding_path=self.binding_path)

    def test_default_is_a_plan_and_does_not_create_a_controller_directory(self) -> None:
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            result = signing.enroll_campaign_signing_key(campaign_binding_path=self.binding_path)
        self.assertEqual("planned", result["status"])
        self.assertEqual(self.campaign_id, result["campaign_id"])
        self.assertEqual("ed25519", result["algorithm"])
        self.assertFalse(result["private_key_created"])
        self.assertNotIn("private_key_path", result)
        self.assertNotIn("receipt_path", result)
        self.assertFalse((self.campaign_directory / signing.CONTROLLER_DIRECTORY_NAME).exists())

    def test_apply_creates_exactly_one_raw_private_key_and_url_free_public_receipt(self) -> None:
        result = self._enroll()
        layout = self._layout()
        verified = self._verify()

        self.assertEqual("created", result["status"])
        self.assertEqual(0o700, stat.S_IMODE(layout.controller_directory.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(layout.signing_directory.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(layout.private_key_path.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(layout.receipt_path.stat().st_mode))
        self.assertEqual(signing.ED25519_PRIVATE_KEY_BYTES, len(layout.private_key_path.read_bytes()))
        self.assertEqual(verified.public_key_base64, result["controller_signing_public_key_base64"])
        self.assertEqual(verified.key_id, result["controller_signing_key_id"])
        self.assertEqual(verified.receipt, result["signing_key_receipt"])
        receipt_payload = layout.receipt_path.read_bytes()
        receipt = json.loads(receipt_payload)
        self.assertEqual(receipt_payload, _canonical(receipt))
        self.assertEqual(
            {
                "schema",
                "status",
                "campaign_id",
                "campaign_binding_sha256",
                "algorithm",
                "public_key_base64",
                "key_id",
            },
            set(receipt),
        )
        self.assertNotIn(b"://", receipt_payload)
        self.assertNotIn(b"private", receipt_payload.lower())
        self.assertNotIn(layout.private_key_path.read_bytes(), receipt_payload)
        self.assertNotIn("private_key_path", result)
        self.assertNotIn("receipt_path", result)

    def test_existing_key_directory_is_retained_and_never_reused_or_overwritten(self) -> None:
        layout = self._layout()
        layout.controller_directory.mkdir(mode=0o700)
        layout.controller_directory.chmod(0o700)
        layout.signing_directory.mkdir(mode=0o700)
        layout.signing_directory.chmod(0o700)
        legacy_private = _private(layout.private_key_path, b"x" * signing.ED25519_PRIVATE_KEY_BYTES)
        before = legacy_private.read_bytes()
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            with self.assertRaisesRegex(
                signing.ControllerCampaignSigningKeyError,
                "already exists and will not be reused",
            ):
                signing.enroll_campaign_signing_key(campaign_binding_path=self.binding_path, apply=True)
        self.assertEqual(before, legacy_private.read_bytes())
        self.assertFalse(layout.receipt_path.exists())

    def test_plan_rejects_an_unsafe_preexisting_controller_directory(self) -> None:
        layout = self._layout()
        layout.controller_directory.mkdir(mode=0o755)
        layout.controller_directory.chmod(0o755)
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            with self.assertRaisesRegex(signing.ControllerCampaignSigningKeyError, "controller campaign directory is unsafe"):
                signing.enroll_campaign_signing_key(campaign_binding_path=self.binding_path)

    def test_verify_rejects_tampered_public_receipt_and_unsafe_private_file(self) -> None:
        self._enroll()
        layout = self._layout()
        receipt = json.loads(layout.receipt_path.read_bytes())
        receipt["public_key_base64"] = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
        receipt["key_id"] = signing.key_id_for_public_key(receipt["public_key_base64"])
        _private(layout.receipt_path, _canonical(receipt))
        with self.assertRaisesRegex(
            signing.ControllerCampaignSigningKeyError,
            "does not match its receipt",
        ):
            self._verify()

        layout.private_key_path.chmod(0o640)
        with self.assertRaisesRegex(signing.ControllerCampaignSigningKeyError, "private key is unsafe"):
            self._verify()

    def test_signer_loader_exposes_no_raw_key_and_rechecks_the_fixed_receipt(self) -> None:
        self._enroll()
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", self.campaign_root):
            loaded = signing.load_verified_campaign_signer(campaign_binding_path=self.binding_path)
        self.assertEqual(self.campaign_id, loaded.campaign_binding.campaign_id)
        self.assertEqual(loaded.signing_key.public_key_base64, loaded.signing_key.receipt["public_key_base64"])
        self.assertFalse(hasattr(loaded, "private_key"))
        signature = loaded.signer.sign(b"campaign-bound-test")
        self.assertTrue(signature)

    def test_rejects_wrong_campaign_binding_path_and_parser_has_no_key_override(self) -> None:
        wrong_root = self.root / "wrong-campaign-root"
        wrong_root.mkdir(mode=0o700)
        wrong_root.chmod(0o700)
        with mock.patch.object(signing, "CAMPAIGNS_ROOT", wrong_root):
            with self.assertRaisesRegex(
                signing.ControllerCampaignSigningKeyError,
                "not installed at its fixed campaign path",
            ):
                signing.enroll_campaign_signing_key(campaign_binding_path=self.binding_path)

        parsed = signing._parser().parse_args(["enroll", "--campaign-binding", str(self.binding_path)])
        self.assertFalse(hasattr(parsed, "private_key_path"))
        self.assertFalse(hasattr(parsed, "output"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            signing._parser().parse_args(
                ["enroll", "--campaign-binding", str(self.binding_path), "--private-key", "/tmp/legacy.raw"]
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
