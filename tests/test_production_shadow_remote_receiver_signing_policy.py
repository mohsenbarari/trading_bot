from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from scripts import production_shadow_remote_receiver_signing_policy as module


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "2ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"


def policy_document() -> dict[str, object]:
    public_key = bytes(range(32))
    document: dict[str, object] = {
        "schema": module.POLICY_SCHEMA,
        "algorithm": module.ALGORITHM,
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
    document["policy_sha256"] = hashlib.sha256(module.policy_payload(document)).hexdigest()
    return document


def receipt_document(policy: module.SigningPolicy) -> dict[str, object]:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    signature = bytes(range(64))
    document: dict[str, object] = {
        "schema": module.RECEIPT_SCHEMA,
        "algorithm": module.ALGORITHM,
        "key_id": policy.key_id,
        "policy_sha256": policy.policy_sha256,
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
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
        "signed_payload_sha256": "0" * 64,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
        "signature_sha256": hashlib.sha256(signature).hexdigest(),
        "receipt_sha256": "0" * 64,
    }
    document["signed_payload_sha256"] = hashlib.sha256(module.receipt_signing_payload(document)).hexdigest()
    document["receipt_sha256"] = hashlib.sha256(module.receipt_payload(document)).hexdigest()
    return document


class RemoteReceiverSigningPolicyTests(unittest.TestCase):
    def test_policy_and_receipt_are_canonical_and_context_bound(self):
        policy_bytes = module.canonical_json_bytes(policy_document()) + b"\n"
        policy = module.parse_policy_payload(policy_bytes)
        self.assertEqual(policy.role, "webapp_ir")
        receipt_bytes = module.canonical_json_bytes(receipt_document(policy)) + b"\n"
        receipt = module.parse_receipt_payload(receipt_bytes, policy=policy)
        self.assertEqual(receipt.document["object_storage"]["readback_version_id"], "version-1")
        self.assertEqual(len(receipt.signature_payload), len(module.receipt_signing_payload(receipt.document)))

    def test_policy_rejects_wrong_public_key_digest_and_noncanonical_payload(self):
        document = policy_document()
        document["public_key_sha256"] = "b" * 64
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.validate_policy_document(document)
        duplicate = b'{"schema":"x","schema":"x"}\n'
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.parse_policy_payload(duplicate)

    def test_receipt_rejects_policy_replay_signature_and_version_binding_drift(self):
        policy = module.validate_policy_document(policy_document())
        document = receipt_document(policy)
        document["role"] = "witness"
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.validate_receipt_document(document, policy=policy)
        document = receipt_document(policy)
        document["signature_sha256"] = "b" * 64
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.validate_receipt_document(document, policy=policy)
        document = receipt_document(policy)
        document["object_storage"]["readback_version_id"] = "version-2"
        document["signed_payload_sha256"] = hashlib.sha256(module.receipt_signing_payload(document)).hexdigest()
        document["receipt_sha256"] = hashlib.sha256(module.receipt_payload(document)).hexdigest()
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.validate_receipt_document(document, policy=policy)

    def test_foundation_has_no_crypto_or_transport_dependency(self):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "import cryptography",
            "from cryptography",
            "import boto3",
            "urllib.request",
            "import subprocess",
            "import socket",
            "open(",
        ):
            self.assertNotIn(forbidden, source)

    def test_injected_verifier_receives_only_the_canonical_bound_payload(self):
        policy = module.validate_policy_document(policy_document())
        payload = module.canonical_json_bytes(receipt_document(policy)) + b"\n"
        calls: list[tuple[bytes, bytes, bytes]] = []

        def verifier(public_key: bytes, signature: bytes, signed_payload: bytes) -> bool:
            calls.append((public_key, signature, signed_payload))
            return True

        verified = module.verify_receipt_payload(
            payload,
            policy=policy,
            expected_request_sha256="6" * 64,
            verify_ed25519=verifier,
            now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], policy.public_key)
        self.assertEqual(calls[0][1], verified.signature)
        self.assertEqual(calls[0][2], verified.signature_payload)

    def test_malformed_replayed_or_expired_receipts_fail_before_verifier(self):
        policy = module.validate_policy_document(policy_document())
        callback = mock.Mock(return_value=True)
        malformed = receipt_document(policy)
        malformed["signature_base64"] = "not-base64"
        malformed["signature_sha256"] = "b" * 64
        malformed["receipt_sha256"] = hashlib.sha256(module.receipt_payload(malformed)).hexdigest()
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.verify_receipt_payload(
                module.canonical_json_bytes(malformed) + b"\n",
                policy=policy,
                expected_request_sha256="6" * 64,
                verify_ed25519=callback,
                now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
            )
        replayed_policy = policy_document()
        replayed_policy["role"] = "witness"
        replayed_policy["key_id"] = "witness-convergence-01"
        replayed_policy["policy_sha256"] = hashlib.sha256(module.policy_payload(replayed_policy)).hexdigest()
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.verify_receipt_payload(
                module.canonical_json_bytes(receipt_document(policy)) + b"\n",
                policy=module.validate_policy_document(replayed_policy),
                expected_request_sha256="6" * 64,
                verify_ed25519=callback,
                now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
            )
        with self.assertRaises(module.RemoteReceiverSigningPolicyError):
            module.verify_receipt_payload(
                module.canonical_json_bytes(receipt_document(policy)) + b"\n",
                policy=policy,
                expected_request_sha256="6" * 64,
                verify_ed25519=callback,
                now=datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc),
            )
        callback.assert_not_called()

    def test_receipt_for_another_request_cannot_replay_within_policy_window(self):
        policy = module.validate_policy_document(policy_document())
        callback = mock.Mock(return_value=True)
        with self.assertRaisesRegex(
            module.RemoteReceiverSigningPolicyError,
            "request binding differs",
        ):
            module.verify_receipt_payload(
                module.canonical_json_bytes(receipt_document(policy)) + b"\n",
                policy=policy,
                expected_request_sha256="f" * 64,
                verify_ed25519=callback,
                now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
            )
        callback.assert_not_called()

    def test_false_or_raising_verifier_fails_closed(self):
        policy = module.validate_policy_document(policy_document())
        payload = module.canonical_json_bytes(receipt_document(policy)) + b"\n"
        for verifier in (
            lambda *_args: False,
            lambda *_args: 1,
            lambda *_args: (_ for _ in ()).throw(ValueError("bad")),
        ):
            with self.subTest(verifier=verifier):
                with self.assertRaises(module.RemoteReceiverSigningPolicyError):
                    module.verify_receipt_payload(
                        payload,
                        policy=policy,
                        expected_request_sha256="6" * 64,
                        verify_ed25519=verifier,
                        now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
                    )


if __name__ == "__main__":
    unittest.main()
