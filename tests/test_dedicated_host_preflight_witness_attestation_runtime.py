"""Tests for the default-off local Witness evidence retrieval runtime."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
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
from core import dedicated_host_preflight_witness_attestation_ledger as ledger
from core import dedicated_host_preflight_witness_attestation_runtime as runtime
from core.dedicated_host_preflight_receipt import PREFLIGHT_RECEIPT_SCHEMA, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


CAMPAIGN_ID = "witness-runtime-20260731"
OPERATION_ID = "11111111-2222-4333-8444-555555555555"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64
ATTESTATION_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
NONCE = "A" * 22


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _key_id(public: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public).hexdigest()


def _request_payload(key_id: str) -> bytes:
    request = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    return canonical_json_bytes(
        {
            "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
            "version": 1,
            "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
            "readonly_request": request,
            "readonly_request_sha256": hashlib.sha256(
                canonical_json_bytes(request) + b"\n"
            ).hexdigest(),
            "attestation_id": ATTESTATION_ID,
            "nonce": NONCE,
            "maximum_validity_seconds": 120,
            "wa_ir_attestation_key_id": key_id,
        }
    ) + b"\n"


def _receipt() -> bytes:
    host = EXPECTED_HOSTS["webapp_ir"]
    return canonical_json_bytes(
        {
            "schema": PREFLIGHT_RECEIPT_SCHEMA,
            "status": "observed",
            "observation_mode": "read-only",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "role": "webapp_ir",
            "instance": {
                "provider": "arvan_ecc",
                "server_id": host["instance_id"],
                "public_ipv4": host["public_ip"],
            },
            "manifest_sha256": MANIFEST_SHA256,
            "observed_at": "2026-07-31T00:00:00Z",
            "observation": {
                "role_marker": "webapp_ir",
                "release": {"state": "present", "release_sha": RELEASE_SHA, "clean": True},
                "runtime": {
                    "docker_state": "active",
                    "container_count": 0,
                    "matrix_process_count": 0,
                    "current_link_present": False,
                },
                "staging_mount": {
                    "present": False,
                    "filesystem": None,
                    "available_bytes": None,
                    "options": [],
                },
            },
        }
    ) + b"\n"


@unittest.skipUnless(os.geteuid() == 0, "root-only Witness runtime contract")
class WitnessAttestationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="witness-runtime-")
        root = Path(self.temporary.name)
        root.chmod(0o700)
        self.config_path = root / "runtime.json"
        self.key_path = root / "key.json"
        self.ledger_root = root / "ledger"
        self.ledger_root.mkdir(mode=0o700)
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir_signer)
        self.witness_public = _public(self.witness_signer)
        self.request = attestation.parse_wa_ir_witness_attestation_request(
            _request_payload(_key_id(self.wa_ir_public))
        )
        self.patches = [
            patch.object(runtime, "FIXED_WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_CONFIG_FILE", self.config_path),
            patch.object(runtime, "FIXED_WITNESS_PREFLIGHT_ATTESTATION_KEY_FILE", self.key_path),
            patch.object(ledger, "FIXED_WITNESS_PREFLIGHT_ATTESTATION_LEDGER_STATE_ROOT", self.ledger_root),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def _write_runtime_files(self) -> None:
        config = {
            "schema": runtime.DEDICATED_HOST_PREFLIGHT_WITNESS_ATTESTATION_RUNTIME_CONFIG_SCHEMA,
            "version": 1,
            "enabled": True,
            "mode": "read-only",
            "transport": "local-selector-free-witness-ledger",
            "direct_finland_to_iran": "forbidden",
            "attestation_request": json.loads(self.request.canonical_request),
            "wa_ir_public_key_base64": base64.b64encode(self.wa_ir_public).decode("ascii"),
            "witness_public_key_base64": base64.b64encode(self.witness_public).decode("ascii"),
            "maximum_entries": 4,
        }
        self.config_path.write_bytes(canonical_json_bytes(config) + b"\n")
        self.config_path.chmod(0o600)
        private = self.witness_signer.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key = {
            "schema": runtime.WITNESS_PREFLIGHT_ATTESTATION_KEY_SCHEMA,
            "version": 1,
            "purpose": runtime.WITNESS_PREFLIGHT_ATTESTATION_KEY_PURPOSE,
            "algorithm": "ed25519",
            "private_key_base64": base64.b64encode(private).decode("ascii"),
            "public_key_sha256": hashlib.sha256(self.witness_public).hexdigest(),
        }
        self.key_path.write_bytes(canonical_json_bytes(key) + b"\n")
        self.key_path.chmod(0o400)

    def _persist_evidence(self) -> bytes:
        now = datetime.now(timezone.utc)
        envelope = attestation.build_wa_ir_witness_attestation_envelope(
            request=self.request,
            canonical_receipt=_receipt(),
            signer=self.wa_ir_signer,
            issued_at=now,
        )
        item = ledger.RootOwnedWitnessPreflightAttestationLedger(
            config=ledger.RootOwnedWitnessPreflightAttestationLedgerConfig(
                expected_request=self.request,
                expected_wa_ir_public_key=self.wa_ir_public,
                expected_witness_public_key=self.witness_public,
                enabled=True,
                maximum_entries=4,
            ),
            witness_signer=self.witness_signer,
        )
        return item.accept_wa_ir_attestation(canonical_envelope=envelope, now=now)

    def test_retrieves_only_previously_persisted_selector_free_evidence(self) -> None:
        self._write_runtime_files()
        expected = self._persist_evidence()
        result = runtime.collect_root_owned_witness_pinned_preflight_evidence(
            config=runtime.RootOwnedWitnessPreflightAttestationRuntimeConfig(enabled=True)
        )
        self.assertEqual(result, expected)
        self.assertEqual(
            runtime.load_root_owned_witness_preflight_attestation_ledger().collect_pinned_evidence(),
            expected,
        )

    def test_default_off_does_not_open_or_accept_an_alternate_runtime(self) -> None:
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightWitnessAttestationRuntimeError,
            "WITNESS_PREFLIGHT_ATTESTATION_RUNTIME_DISABLED",
        ):
            runtime.collect_root_owned_witness_pinned_preflight_evidence()
        self._write_runtime_files()
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightWitnessAttestationRuntimeError,
            "WITNESS_PREFLIGHT_ATTESTATION_EVIDENCE_UNAVAILABLE",
        ):
            runtime.collect_root_owned_witness_pinned_preflight_evidence(
                config=runtime.RootOwnedWitnessPreflightAttestationRuntimeConfig(enabled=True)
            )

