"""Tests for the root-only central public dual-signature verifier policy."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_ir_witness_attestation as attestation
from core import dedicated_host_preflight_witness_evidence_runtime as runtime
from core.dedicated_host_preflight_receipt import canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import READONLY_REQUEST_SCHEMA


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _request(public_key: bytes) -> dict[str, object]:
    readonly = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": "witness-verifier-runtime-20260731",
        "operation_id": "11111111-2222-4333-8444-555555555555",
        "release_sha": "a" * 40,
        "role": "webapp_ir",
        "manifest_sha256": "b" * 64,
    }
    return {
        "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
        "version": 1,
        "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
        "readonly_request": readonly,
        "readonly_request_sha256": hashlib.sha256(canonical_json_bytes(readonly) + b"\n").hexdigest(),
        "attestation_id": "66666666-7777-4888-8999-aaaaaaaaaaaa",
        "nonce": "A" * 22,
        "maximum_validity_seconds": 120,
        "wa_ir_attestation_key_id": "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest(),
    }


@unittest.skipUnless(os.geteuid() == 0, "root-only verifier runtime contract")
class WitnessEvidenceVerifierRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="witness-verifier-runtime-")
        root = Path(self.temporary.name)
        root.chmod(0o700)
        self.config_path = root / "verifier.json"
        self.wa_ir = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir)
        self.witness_public = _public(self.witness)
        self.config = {
            "schema": runtime.DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_RUNTIME_CONFIG_SCHEMA,
            "version": 1,
            "enabled": True,
            "mode": "read-only",
            "transport": "pinned-ssh-witness-evidence-agent",
            "direct_finland_to_iran": "forbidden",
            "attestation_request": _request(self.wa_ir_public),
            "wa_ir_public_key_base64": base64.b64encode(self.wa_ir_public).decode("ascii"),
            "witness_public_key_base64": base64.b64encode(self.witness_public).decode("ascii"),
        }
        self.path_patch = patch.object(
            runtime,
            "FIXED_WITNESS_EVIDENCE_VERIFIER_CONFIG_FILE",
            self.config_path,
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def test_parser_and_loader_admit_only_the_fixed_public_verifier_policy(self) -> None:
        parsed = runtime.parse_root_owned_witness_evidence_verifier_runtime_config(self.config)
        self.assertTrue(parsed.enabled)
        self.assertEqual(parsed.expected_wa_ir_public_key, self.wa_ir_public)
        self.assertEqual(parsed.expected_witness_public_key, self.witness_public)
        self.assertEqual(parsed.expected_request.readonly_request["role"], "webapp_ir")
        self.config_path.write_bytes(canonical_json_bytes(self.config) + b"\n")
        self.config_path.chmod(0o600)
        loaded = runtime.load_root_owned_witness_evidence_delivery_config(
            config=runtime.RootOwnedWitnessEvidenceVerifierRuntimeConfig(enabled=True)
        )
        self.assertEqual(loaded, parsed)

    def test_disabled_or_mutable_config_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightWitnessEvidenceRuntimeError,
            "WITNESS_EVIDENCE_VERIFIER_RUNTIME_DISABLED",
        ):
            runtime.load_root_owned_witness_evidence_delivery_config()
        bad = {**self.config, "direct_finland_to_iran": "allowed"}
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightWitnessEvidenceRuntimeError,
            "WITNESS_EVIDENCE_VERIFIER_CONFIG_INVALID",
        ):
            runtime.parse_root_owned_witness_evidence_verifier_runtime_config(bad)

