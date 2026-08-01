"""Pure protocol tests for FI-signed WA-IR request provisioning records.

These tests create in-memory Ed25519 records only.  They do not use age,
Object Storage, a host file, a socket, or a service.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_ir_request_provisioning as provisioning
from core import dedicated_host_preflight_ir_witness_attestation as attestation
from core.dedicated_host_preflight_receipt import canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import READONLY_REQUEST_SCHEMA


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "preflight-wa-ir-request-20260731"
OPERATION_ID = "11111111-2222-4333-8444-555555555555"
ATTESTATION_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64
NONCE = "A" * 22
RECIPIENT = "age1" + "a" * 30


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _request(wa_ir_public: bytes) -> attestation.ParsedWaIrWitnessAttestationRequest:
    readonly = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    readonly_raw = canonical_json_bytes(readonly) + b"\n"
    return attestation.parse_wa_ir_witness_attestation_request(
        canonical_json_bytes(
            {
                "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
                "version": 1,
                "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
                "readonly_request": readonly,
                "readonly_request_sha256": hashlib.sha256(readonly_raw).hexdigest(),
                "attestation_id": ATTESTATION_ID,
                "nonce": NONCE,
                "maximum_validity_seconds": 120,
                "wa_ir_attestation_key_id": _key_id(wa_ir_public),
            }
        )
        + b"\n"
    )


def _key_record(signer: Ed25519PrivateKey, *, purpose: str) -> bytes:
    private_raw = signer.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = _public(signer)
    return canonical_json_bytes(
        {
            "schema": provisioning.FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_SCHEMA,
            "version": 1,
            "purpose": purpose,
            "algorithm": "ed25519",
            "private_key_base64": base64.b64encode(private_raw).decode("ascii"),
            "public_key_sha256": hashlib.sha256(public).hexdigest(),
            "key_id": _key_id(public),
        }
    ) + b"\n"


class FiWaIrRequestProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.fi_signer = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir_signer)
        self.fi_public = _public(self.fi_signer)
        self.request = _request(self.wa_ir_public)
        self.binding = provisioning.FiWaIrPreflightRequestProvisioningBinding(
            route_binding_sha256="c" * 64,
            fi_publisher_identity_sha256="d" * 64,
            ir_receiver_identity_sha256="e" * 64,
            age_recipient=RECIPIENT,
            issued_at=NOW,
            maximum_validity_seconds=120,
        )

    def payload(self) -> bytes:
        return provisioning.build_fi_wa_ir_preflight_request_payload(
            request=self.request,
            binding=self.binding,
            signer=self.fi_signer,
        )

    def locator(self, payload: bytes) -> bytes:
        payload_sha = hashlib.sha256(payload).hexdigest()
        return provisioning.build_fi_wa_ir_preflight_request_locator(
            canonical_payload=payload,
            expected_fi_public_key=self.fi_public,
            object=provisioning.FiWaIrPreflightRequestLocator(
                object_key=(
                    "dedicated-host-preflight/v1/"
                    + CAMPAIGN_ID
                    + "/"
                    + OPERATION_ID
                    + "/wa-ir-witness-request/request-"
                    + payload_sha
                    + ".age"
                ),
                version_id="version-20260731-A",
                ciphertext_sha256="f" * 64,
                ciphertext_bytes=256,
                metadata={
                    "encryption": "age-v1",
                    "ciphertext-sha256": "f" * 64,
                    "ciphertext-bytes": "256",
                    "payload-sha256": payload_sha,
                    "request-sha256": self.request.attestation_request_sha256,
                },
            ),
            signer=self.fi_signer,
            now=NOW,
        )

    def test_payload_and_redacted_exact_locator_round_trip(self) -> None:
        payload = self.payload()
        verified_payload = provisioning.verify_fi_wa_ir_preflight_request_payload(
            canonical_payload=payload,
            expected_fi_public_key=self.fi_public,
            now=NOW,
        )
        self.assertEqual(self.request.canonical_request, verified_payload.request.canonical_request)
        self.assertEqual(RECIPIENT, verified_payload.age_recipient)

        locator = self.locator(payload)
        verified_locator = provisioning.verify_fi_wa_ir_preflight_request_locator(
            canonical_locator=locator,
            expected_fi_public_key=self.fi_public,
            now=NOW,
        )
        self.assertEqual(hashlib.sha256(payload).hexdigest(), verified_locator.payload_sha256)
        self.assertEqual(self.request.attestation_request_sha256, verified_locator.request_sha256)
        self.assertEqual("version-20260731-A", verified_locator.object.version_id)
        rendered = locator.decode("ascii")
        self.assertNotIn("private-physical", rendered)
        self.assertNotIn("https://", rendered)

    def test_tamper_wrong_key_and_expiry_fail_closed(self) -> None:
        payload = self.payload()
        altered = json.loads(payload)
        altered["ir_receiver_identity_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            provisioning.DedicatedHostPreflightIrRequestProvisioningError,
            "FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID",
        ):
            provisioning.verify_fi_wa_ir_preflight_request_payload(
                canonical_payload=canonical_json_bytes(altered) + b"\n",
                expected_fi_public_key=self.fi_public,
                now=NOW,
            )
        with self.assertRaisesRegex(
            provisioning.DedicatedHostPreflightIrRequestProvisioningError,
            "FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID",
        ):
            provisioning.verify_fi_wa_ir_preflight_request_payload(
                canonical_payload=payload,
                expected_fi_public_key=_public(Ed25519PrivateKey.generate()),
                now=NOW,
            )
        with self.assertRaisesRegex(
            provisioning.DedicatedHostPreflightIrRequestProvisioningError,
            "FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID",
        ):
            provisioning.verify_fi_wa_ir_preflight_request_payload(
                canonical_payload=payload,
                expected_fi_public_key=self.fi_public,
                now=NOW + timedelta(seconds=121),
            )

        locator = self.locator(payload)
        altered_locator = json.loads(locator)
        altered_locator["object"]["version_id"] = "latest"
        with self.assertRaisesRegex(
            provisioning.DedicatedHostPreflightIrRequestProvisioningError,
            "FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID",
        ):
            provisioning.verify_fi_wa_ir_preflight_request_locator(
                canonical_locator=canonical_json_bytes(altered_locator) + b"\n",
                expected_fi_public_key=self.fi_public,
                now=NOW,
            )

    def test_provisioning_key_has_an_exclusive_purpose(self) -> None:
        parsed = provisioning.parse_fi_wa_ir_preflight_request_provisioning_key_record(
            _key_record(
                self.fi_signer,
                purpose=provisioning.FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE,
            )
        )
        self.assertEqual(self.fi_public, _public(parsed))
        with self.assertRaisesRegex(
            provisioning.DedicatedHostPreflightIrRequestProvisioningError,
            "FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID",
        ):
            provisioning.parse_fi_wa_ir_preflight_request_provisioning_key_record(
                _key_record(self.fi_signer, purpose="writer-witness-key")
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
