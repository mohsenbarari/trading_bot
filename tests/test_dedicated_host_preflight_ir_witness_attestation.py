"""Focused cryptographic tests for the WA-IR -> Witness evidence contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.dedicated_host_preflight_ir_witness_attestation as attestation
from core.dedicated_host_preflight_receipt import PREFLIGHT_RECEIPT_SCHEMA, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "preflight-wa-ir-witness-20260731"
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


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _request_payload(key_id: str) -> bytes:
    readonly_request = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    readonly_raw = canonical_json_bytes(readonly_request) + b"\n"
    value = {
        "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
        "version": 1,
        "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
        "readonly_request": readonly_request,
        "readonly_request_sha256": hashlib.sha256(readonly_raw).hexdigest(),
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
                "container_count": 2,
                "matrix_process_count": 0,
                "current_link_present": True,
            },
            "staging_mount": {
                "present": True,
                "filesystem": "ext4",
                "available_bytes": 1024,
                "options": ["nodev", "noexec", "nosuid", "rw"],
            },
        },
    }
    return canonical_json_bytes(value) + b"\n"


def _key_record(signer: Ed25519PrivateKey, *, purpose: str | None = None) -> bytes:
    private_raw = signer.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = _public(signer)
    value = {
        "schema": attestation.WA_IR_WITNESS_ATTESTATION_KEY_SCHEMA,
        "version": 1,
        "purpose": purpose or attestation.WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE,
        "algorithm": "ed25519",
        "private_key_base64": base64.b64encode(private_raw).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public).hexdigest(),
        "key_id": _key_id(public),
    }
    return canonical_json_bytes(value) + b"\n"


class WaIrWitnessAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir_signer)
        self.witness_public = _public(self.witness_signer)
        self.request = attestation.parse_wa_ir_witness_attestation_request(
            _request_payload(_key_id(self.wa_ir_public))
        )
        self.receipt = _receipt_payload()

    def envelope(self) -> bytes:
        return attestation.build_wa_ir_witness_attestation_envelope(
            request=self.request,
            canonical_receipt=self.receipt,
            signer=self.wa_ir_signer,
            issued_at=NOW,
        )

    def test_dual_signed_evidence_extracts_the_exact_existing_v2_receipt(self) -> None:
        envelope = self.envelope()
        verified_attestation = attestation.verify_wa_ir_witness_attestation_envelope(
            canonical_envelope=envelope,
            expected_request=self.request,
            expected_wa_ir_public_key=self.wa_ir_public,
            now=NOW,
        )
        evidence = attestation.build_witness_preflight_evidence(
            wa_ir_attestation=verified_attestation,
            witness_signer=self.witness_signer,
            accepted_at=NOW,
        )
        verified = attestation.verify_witness_preflight_evidence(
            canonical_evidence=evidence,
            expected_request=self.request,
            expected_wa_ir_public_key=self.wa_ir_public,
            expected_witness_public_key=self.witness_public,
            now=NOW,
        )
        self.assertEqual(self.receipt, verified.canonical_receipt)
        self.assertEqual("webapp_ir", verified.receipt["role"])
        self.assertFalse(json.loads(evidence)["writer_authorized"])
        self.assertFalse(json.loads(evidence)["promotion_authorized"])
        self.assertFalse(json.loads(evidence)["execution_authorized"])

    def test_tampered_wa_ir_envelope_or_witness_evidence_is_rejected(self) -> None:
        envelope = self.envelope()
        altered = json.loads(envelope)
        altered["nonce"] = "B" * 22
        tampered_envelope = canonical_json_bytes(altered) + b"\n"
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_ENVELOPE_INVALID",
        ):
            attestation.verify_wa_ir_witness_attestation_envelope(
                canonical_envelope=tampered_envelope,
                expected_request=self.request,
                expected_wa_ir_public_key=self.wa_ir_public,
                now=NOW,
            )

        verified_attestation = attestation.verify_wa_ir_witness_attestation_envelope(
            canonical_envelope=envelope,
            expected_request=self.request,
            expected_wa_ir_public_key=self.wa_ir_public,
            now=NOW,
        )
        evidence = attestation.build_witness_preflight_evidence(
            wa_ir_attestation=verified_attestation,
            witness_signer=self.witness_signer,
            accepted_at=NOW,
        )
        altered_evidence = json.loads(evidence)
        altered_evidence["writer_authorized"] = True
        tampered_evidence = canonical_json_bytes(altered_evidence) + b"\n"
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WITNESS_PREFLIGHT_EVIDENCE_INVALID",
        ):
            attestation.verify_witness_preflight_evidence(
                canonical_evidence=tampered_evidence,
                expected_request=self.request,
                expected_wa_ir_public_key=self.wa_ir_public,
                expected_witness_public_key=self.witness_public,
                now=NOW,
            )

    def test_expired_attestation_is_rejected_before_witness_evidence_is_accepted(self) -> None:
        envelope = self.envelope()
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_ENVELOPE_INVALID",
        ):
            attestation.verify_wa_ir_witness_attestation_envelope(
                canonical_envelope=envelope,
                expected_request=self.request,
                expected_wa_ir_public_key=self.wa_ir_public,
                now=NOW + timedelta(seconds=121),
            )

    def test_wrong_key_and_wrong_key_purpose_are_rejected(self) -> None:
        envelope = self.envelope()
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_ENVELOPE_INVALID",
        ):
            attestation.verify_wa_ir_witness_attestation_envelope(
                canonical_envelope=envelope,
                expected_request=self.request,
                expected_wa_ir_public_key=_public(Ed25519PrivateKey.generate()),
                now=NOW,
            )
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_KEY_INVALID",
        ):
            attestation.parse_wa_ir_witness_attestation_key_record(
                _key_record(self.wa_ir_signer, purpose="writer-witness-key")
            )
        parsed = attestation.parse_wa_ir_witness_attestation_key_record(
            _key_record(self.wa_ir_signer)
        )
        self.assertEqual(self.wa_ir_public, _public(parsed))

    def test_request_key_pin_must_match_the_signer(self) -> None:
        wrong_request = attestation.parse_wa_ir_witness_attestation_request(
            _request_payload(_key_id(_public(Ed25519PrivateKey.generate())))
        )
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_KEY_NOT_PINNED",
        ):
            attestation.build_wa_ir_witness_attestation_envelope(
                request=wrong_request,
                canonical_receipt=self.receipt,
                signer=self.wa_ir_signer,
                issued_at=NOW,
            )

    def test_receipt_provider_identity_must_match_the_pinned_wa_ir_host(self) -> None:
        altered = json.loads(self.receipt)
        altered["instance"]["public_ipv4"] = "8.8.8.8"
        with self.assertRaisesRegex(
            attestation.DedicatedHostPreflightIrWitnessAttestationError,
            "WA_IR_WITNESS_ATTESTATION_RECEIPT_INVALID",
        ):
            attestation.build_wa_ir_witness_attestation_envelope(
                request=self.request,
                canonical_receipt=canonical_json_bytes(altered) + b"\n",
                signer=self.wa_ir_signer,
                issued_at=NOW,
            )
