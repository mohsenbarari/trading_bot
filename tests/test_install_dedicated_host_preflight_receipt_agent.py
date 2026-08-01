"""Local staged-install boundary tests using only temporary synthetic roots."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from core import dedicated_host_preflight_receipt_agent_boundary as boundary
from scripts import render_dedicated_host_preflight_receipt_agent as renderer


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_dedicated_host_preflight_receipt_agent.py"
SPEC = importlib.util.spec_from_file_location("install_dedicated_host_preflight_receipt_agent", SCRIPT)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def _public_key() -> str:
    kind = b"ssh-ed25519"
    public = hashlib.sha256(b"receipt-agent-installer-script-test").digest()
    wire = len(kind).to_bytes(4, "big") + kind + len(public).to_bytes(4, "big") + public
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


@unittest.skipUnless(os.geteuid() == 0, "root-only installer contract")
class ReceiptAgentInstallerScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="receipt-agent-installer-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.stage = self.root / "stage"
        self.stage.mkdir(mode=0o700)
        self.live = self.root / "live"
        self.live.mkdir(mode=0o700)
        self.rendered = boundary.render_receipt_agent_assets(
            boundary.ReceiptAgentInstallationConfig(
                enabled=False,
                site_role="witness",
                agent_release_sha="a" * 40,
                controller_public_key=_public_key(),
            )
        )
        renderer.materialize_fresh_render(self.rendered, root=self.stage)
        (self.live / "etc" / "ssh" / "sshd_config.d").mkdir(parents=True, mode=0o755)
        (self.live / "etc" / "sudoers.d").mkdir(parents=True, mode=0o755)
        self.patches = [
            mock.patch.object(installer, "DEFAULT_RENDER_ROOT", self.stage),
            mock.patch.object(installer, "LIVE_ROOT", self.live),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def test_default_verifies_stage_without_writing_live_assets(self) -> None:
        output = io.BytesIO()
        with mock.patch.object(installer.sys, "stdout", SimpleNamespace(buffer=output)):
            code = installer.main([])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "staged-verified-not-installed")
        self.assertFalse(result["host_change_applied"])
        self.assertFalse((self.live / "etc" / "trading-bot").exists())

    def test_apply_requires_double_confirmation_before_any_live_write(self) -> None:
        output = io.BytesIO()
        with mock.patch.object(installer.sys, "stdout", SimpleNamespace(buffer=output)):
            code = installer.main(["--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), b"")
        self.assertFalse((self.live / "etc" / "trading-bot").exists())

    def test_confirmed_apply_uses_temp_live_root_atomic_assets_and_attestation(self) -> None:
        output = io.BytesIO()
        with (
            mock.patch.object(installer, "_validate_assets_at") as validate,
            mock.patch.object(installer.sys, "stdout", SimpleNamespace(buffer=output)),
        ):
            code = installer.main(["--apply", "--confirm-staged-install"])
        self.assertEqual(code, 0)
        self.assertEqual(validate.call_count, 2)
        for item in self.rendered.files:
            installed = self.live / item.destination.relative_to("/")
            self.assertEqual(installed.read_bytes(), item.content)
            self.assertEqual(installed.stat().st_mode & 0o777, item.mode)
        attestation = self.live / installer.FIXED_INSTALLATION_ATTESTATION.relative_to("/")
        value = json.loads(attestation.read_bytes())
        self.assertEqual(value["status"], "installed-not-activated")
        self.assertFalse(value["service_reloaded"])
        self.assertFalse(value["writer_authorized"])
        self.assertEqual(json.loads(output.getvalue())["status"], "installed-not-activated")

