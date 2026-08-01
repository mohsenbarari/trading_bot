"""Pure staged renderer/install-attestation checks; no host writes occur."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest

from core import dedicated_host_preflight_receipt_agent_boundary as boundary
from core import dedicated_host_preflight_receipt_agent_installation as installation


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    public = hashlib.sha256(b"receipt-agent-installation-test").digest()
    wire = (
        len(algorithm).to_bytes(4, "big")
        + algorithm
        + len(public).to_bytes(4, "big")
        + public
    )
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


def _config(role: str) -> boundary.ReceiptAgentInstallationConfig:
    return boundary.ReceiptAgentInstallationConfig(
        enabled=False,
        site_role=role,
        agent_release_sha="a" * 40,
        controller_public_key=_public_key(),
    )


class ReceiptAgentInstallationTests(unittest.TestCase):
    def _stage(self, role: str) -> tuple[boundary.RenderedReceiptAgentAssets, dict[Path, tuple[bytes, int]]]:
        rendered = boundary.render_receipt_agent_assets(_config(role))
        return rendered, {item.destination: (item.content, item.mode) for item in rendered.files}

    def test_only_exact_full_renderer_stage_is_admitted_including_witness_assets(self) -> None:
        rendered, stage_files = self._stage("witness")
        verified = installation.verify_staged_receipt_agent_assets(stage_files)
        self.assertEqual(verified.config, rendered.config)
        self.assertEqual(verified.installation_sha256, rendered.installation_sha256)
        self.assertIn(
            boundary.FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG,
            [item.destination for item in verified.files],
        )

        tampered = dict(stage_files)
        content, mode = tampered[boundary.FIXED_PREFLIGHT_SUDOERS]
        tampered[boundary.FIXED_PREFLIGHT_SUDOERS] = (content + b"# altered\n", mode)
        with self.assertRaisesRegex(
            installation.DedicatedHostPreflightReceiptAgentInstallationError,
            "PREFLIGHT_RECEIPT_AGENT_STAGE_MISMATCH",
        ):
            installation.verify_staged_receipt_agent_assets(tampered)

        extra = dict(stage_files)
        extra[Path("/etc/trading-bot/security/dedicated-host-preflight/extra")] = (b"x\n", 0o600)
        with self.assertRaisesRegex(
            installation.DedicatedHostPreflightReceiptAgentInstallationError,
            "PREFLIGHT_RECEIPT_AGENT_STAGE_MISMATCH",
        ):
            installation.verify_staged_receipt_agent_assets(extra)

    def test_final_attestation_is_not_an_activation_or_authorization(self) -> None:
        _rendered, stage_files = self._stage("bot_fi")
        verified = installation.verify_staged_receipt_agent_assets(stage_files)
        raw = installation.canonical_installation_attestation_bytes(
            stage=verified,
            installed_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        value = json.loads(raw)
        self.assertEqual(value["status"], "installed-not-activated")
        self.assertFalse(value["service_reloaded"])
        self.assertFalse(value["writer_authorized"])
        self.assertFalse(value["promotion_authorized"])
        self.assertFalse(value["execution_authorized"])

