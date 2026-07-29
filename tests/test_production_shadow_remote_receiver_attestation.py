from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from scripts import production_shadow_remote_receiver_attestation as module
from scripts import production_shadow_remote_receiver_signing_policy as policy_module


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "2ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"


def signing_policy() -> policy_module.SigningPolicy:
    public_key = bytes(range(32))
    document: dict[str, object] = {
        "schema": policy_module.POLICY_SCHEMA,
        "algorithm": policy_module.ALGORITHM,
        "key_id": "webapp-ir-convergence-01",
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "role": "webapp_ir",
        "not_before": "2026-07-29T11:00:00Z",
        "expires_at": "2026-07-29T13:00:00Z",
        "receiver_sha256": "1" * 64,
        "worker_sha256": "2" * 64,
        "policy_sha256": "0" * 64,
    }
    document["policy_sha256"] = hashlib.sha256(policy_module.policy_payload(document)).hexdigest()
    return policy_module.validate_policy_document(document)


def arguments(policy: policy_module.SigningPolicy) -> dict[str, object]:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    return {
        "policy": policy,
        "manifest_sha256": "3" * 64,
        "plan_sha256": "4" * 64,
        "approval_sha256": "5" * 64,
        "phase": "convergence-gate",
        "operation": "observe-production-convergence",
        "expected_host": "95.38.164.29",
        "phase_started_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "request_sha256": "6" * 64,
        "worker_attestation_sha256": "7" * 64,
        "worker_attestation_file_sha256": "8" * 64,
        "object_storage": {
            "provider": "arvan",
            "bucket": "production-sync-coin",
            "artifact_kind": "convergence-attestation",
            "object_key": "dark-standby/convergence/object.age",
            "version_id": "version-1",
            "readback_version_id": "version-1",
            "ciphertext_sha256": "9" * 64,
            "ciphertext_bytes": 128,
            "age_recipient_sha256": "a" * 64,
            "private": True,
            "versioned": True,
        },
        "observed_at": now.isoformat().replace("+00:00", "Z"),
    }


class RemoteReceiverAttestationTests(unittest.TestCase):
    def test_build_parse_and_verify_bind_the_exact_remote_context(self):
        policy = signing_policy()
        signer = mock.Mock(return_value=b"s" * 64)
        built = module.build_attestation(**arguments(policy), sign_ed25519=signer)
        signer.assert_called_once_with(built.signature_payload)
        parsed = module.parse_attestation_payload(built.payload, policy=policy)
        verifier = mock.Mock(return_value=True)
        verified = module.verify_attestation_payload(
            built.payload,
            policy=policy,
            expected_request_sha256="6" * 64,
            now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
            verify_ed25519=verifier,
        )
        self.assertEqual(parsed.document, built.document)
        self.assertEqual(
            {key: verified.document[key] for key in ("role", "campaign_id", "operation_id", "release_sha", "release_tree_sha", "policy_sha256")},
            {
                "role": policy.role,
                "campaign_id": policy.campaign_id,
                "operation_id": policy.operation_id,
                "release_sha": policy.release_sha,
                "release_tree_sha": policy.release_tree_sha,
                "policy_sha256": policy.policy_sha256,
            },
        )
        self.assertEqual(verified.document["object_storage"]["readback_version_id"], "version-1")
        verifier.assert_called_once_with(policy.public_key, b"s" * 64, built.signature_payload)

    def test_invalid_binding_or_signer_fails_before_sign_or_verify(self):
        policy = signing_policy()
        invalid = arguments(policy)
        invalid["object_storage"] = {**invalid["object_storage"], "readback_version_id": "version-2"}
        signer = mock.Mock(return_value=b"s" * 64)
        with self.assertRaises(module.RemoteReceiverAttestationError):
            module.build_attestation(**invalid, sign_ed25519=signer)
        signer.assert_not_called()
        built = module.build_attestation(**arguments(policy), sign_ed25519=lambda _payload: b"s" * 64)
        tampered = json.loads(built.payload)
        tampered["role"] = "witness"
        tampered["signed_payload_sha256"] = hashlib.sha256(
            policy_module.receipt_signing_payload(tampered)
        ).hexdigest()
        tampered["receipt_sha256"] = hashlib.sha256(policy_module.receipt_payload(tampered)).hexdigest()
        verifier = mock.Mock(return_value=True)
        with self.assertRaises(module.RemoteReceiverAttestationError):
            module.verify_attestation_payload(
                policy_module.canonical_json_bytes(tampered) + b"\n",
                policy=policy,
                expected_request_sha256="6" * 64,
                now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
                verify_ed25519=verifier,
            )
        verifier.assert_not_called()

    def test_noncanonical_payload_and_bad_signer_are_rejected(self):
        policy = signing_policy()
        with self.assertRaises(module.RemoteReceiverAttestationError):
            module.build_attestation(**arguments(policy), sign_ed25519=lambda _payload: b"short")
        built = module.build_attestation(**arguments(policy), sign_ed25519=lambda _payload: b"s" * 64)
        with self.assertRaises(module.RemoteReceiverAttestationError):
            module.parse_attestation_payload(built.payload.replace(b"\"role\":\"webapp_ir\"", b"\"role\" : \"webapp_ir\""), policy=policy)

    def test_module_has_no_live_operation_dependency(self):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket", "urllib", "boto3", "open(", "Ed25519PrivateKey"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
