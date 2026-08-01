"""Focused durable/replay tests for the Witness-side WA-IR attestation ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.dedicated_host_preflight_ir_witness_attestation as attestation
import core.dedicated_host_preflight_witness_attestation_ledger as ledger_module
from core.dedicated_host_preflight_receipt import PREFLIGHT_RECEIPT_SCHEMA, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "preflight-wa-ir-ledger-20260731"
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
    readonly = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    value = {
        "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
        "version": 1,
        "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
        "readonly_request": readonly,
        "readonly_request_sha256": hashlib.sha256(canonical_json_bytes(readonly) + b"\n").hexdigest(),
        "attestation_id": ATTESTATION_ID,
        "nonce": NONCE,
        "maximum_validity_seconds": 120,
        "wa_ir_attestation_key_id": key_id,
    }
    return canonical_json_bytes(value) + b"\n"


def _receipt_payload() -> bytes:
    binding = EXPECTED_HOSTS["webapp_ir"]
    value = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "instance": {
            "provider": "arvan_ecc",
            "server_id": binding["instance_id"],
            "public_ipv4": binding["public_ip"],
        },
        "manifest_sha256": MANIFEST_SHA256,
        "observed_at": "2026-07-31T14:00:00Z",
        "observation": {
            "role_marker": "webapp_ir",
            "release": {"state": "present", "release_sha": RELEASE_SHA, "clean": True},
            "runtime": {
                "docker_state": "active",
                "container_count": 1,
                "matrix_process_count": 0,
                "current_link_present": True,
            },
            "staging_mount": {"present": False, "filesystem": None, "available_bytes": None, "options": []},
        },
    }
    return canonical_json_bytes(value) + b"\n"


class WitnessPreflightAttestationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir_signer)
        self.witness_public = _public(self.witness_signer)
        self.request = attestation.parse_wa_ir_witness_attestation_request(
            _request_payload(_key_id(self.wa_ir_public))
        )
        self.path_patch = patch.object(
            ledger_module,
            "FIXED_WITNESS_PREFLIGHT_ATTESTATION_LEDGER_STATE_ROOT",
            self.root,
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def config(self, **changes: object) -> ledger_module.RootOwnedWitnessPreflightAttestationLedgerConfig:
        values: dict[str, object] = {
            "expected_request": self.request,
            "expected_wa_ir_public_key": self.wa_ir_public,
            "expected_witness_public_key": self.witness_public,
            "enabled": True,
        }
        values.update(changes)
        return ledger_module.RootOwnedWitnessPreflightAttestationLedgerConfig(**values)

    def envelope(self) -> bytes:
        return attestation.build_wa_ir_witness_attestation_envelope(
            request=self.request,
            canonical_receipt=_receipt_payload(),
            signer=self.wa_ir_signer,
            issued_at=NOW,
        )

    def ledger(self, **changes: object) -> ledger_module.RootOwnedWitnessPreflightAttestationLedger:
        return ledger_module.RootOwnedWitnessPreflightAttestationLedger(
            config=self.config(**changes),
            witness_signer=self.witness_signer,
        )

    def test_accepts_once_persists_and_returns_only_pinned_evidence(self) -> None:
        ledger = self.ledger()
        evidence = ledger.accept_wa_ir_attestation(canonical_envelope=self.envelope(), now=NOW)
        self.assertEqual(evidence, ledger.collect_pinned_evidence())
        verified = attestation.verify_witness_preflight_evidence(
            canonical_evidence=evidence,
            expected_request=self.request,
            expected_wa_ir_public_key=self.wa_ir_public,
            expected_witness_public_key=self.witness_public,
            now=NOW,
        )
        self.assertEqual(_receipt_payload(), verified.canonical_receipt)
        state = json.loads((self.root / "witness-wa-ir-attestation-ledger.json").read_bytes())
        self.assertFalse(state["writer_authorized"])
        self.assertFalse(state["promotion_authorized"])
        self.assertFalse(state["execution_authorized"])

    def test_replay_and_expiry_fail_closed(self) -> None:
        ledger = self.ledger()
        envelope = self.envelope()
        ledger.accept_wa_ir_attestation(canonical_envelope=envelope, now=NOW)
        with self.assertRaisesRegex(
            ledger_module.DedicatedHostPreflightWitnessAttestationLedgerError,
            "WITNESS_PREFLIGHT_LEDGER_REPLAYED",
        ):
            ledger.accept_wa_ir_attestation(canonical_envelope=envelope, now=NOW)

        fresh_ledger = self.ledger()
        with self.assertRaisesRegex(
            ledger_module.DedicatedHostPreflightWitnessAttestationLedgerError,
            "WITNESS_PREFLIGHT_LEDGER_ENVELOPE_INVALID",
        ):
            fresh_ledger.accept_wa_ir_attestation(
                canonical_envelope=envelope,
                now=NOW + timedelta(seconds=121),
            )

    def test_tampered_persisted_evidence_and_wrong_witness_key_are_rejected(self) -> None:
        ledger = self.ledger()
        ledger.accept_wa_ir_attestation(canonical_envelope=self.envelope(), now=NOW)
        state_path = self.root / "witness-wa-ir-attestation-ledger.json"
        state = json.loads(state_path.read_bytes())
        state["entries"][0]["nonce"] = "B" * 22
        state_path.write_bytes(canonical_json_bytes(state) + b"\n")
        state_path.chmod(0o600)
        with self.assertRaisesRegex(
            ledger_module.DedicatedHostPreflightWitnessAttestationLedgerError,
            "WITNESS_PREFLIGHT_LEDGER_STATE_INVALID",
        ):
            ledger.collect_pinned_evidence()

        with self.assertRaisesRegex(
            ledger_module.DedicatedHostPreflightWitnessAttestationLedgerError,
            "WITNESS_PREFLIGHT_LEDGER_SIGNER_INVALID",
        ):
            ledger_module.RootOwnedWitnessPreflightAttestationLedger(
                config=self.config(),
                witness_signer=Ed25519PrivateKey.generate(),
            )
