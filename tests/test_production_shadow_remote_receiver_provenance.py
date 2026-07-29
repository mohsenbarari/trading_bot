from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from scripts import production_shadow_remote_receiver_attestation as attestation
from scripts import production_shadow_remote_receiver_provenance as module
from scripts import production_shadow_remote_receiver_signing_policy as policy_module


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "2ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"


def policy_payload() -> tuple[bytes, policy_module.SigningPolicy]:
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
    payload = policy_module.canonical_json_bytes(document) + b"\n"
    return payload, policy_module.parse_policy_payload(payload)


def attestation_arguments(policy: policy_module.SigningPolicy) -> dict[str, object]:
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


def expected(arguments: dict[str, object]) -> module.ExpectedRemoteReceiverProvenance:
    return module.ExpectedRemoteReceiverProvenance(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=TREE_SHA,
        role="webapp_ir",
        manifest_sha256=str(arguments["manifest_sha256"]),
        plan_sha256=str(arguments["plan_sha256"]),
        approval_sha256=str(arguments["approval_sha256"]),
        phase=str(arguments["phase"]),
        operation=str(arguments["operation"]),
        expected_host=str(arguments["expected_host"]),
        phase_started_at=str(arguments["phase_started_at"]),
        request_sha256=str(arguments["request_sha256"]),
        worker_attestation_sha256=str(arguments["worker_attestation_sha256"]),
        worker_attestation_file_sha256=str(arguments["worker_attestation_file_sha256"]),
    )


class RemoteReceiverProvenanceTests(unittest.TestCase):
    def _built(self):
        payload, policy = policy_payload()
        arguments = attestation_arguments(policy)
        built = attestation.build_attestation(**arguments, sign_ed25519=lambda _payload: b"s" * 64)
        return payload, policy, arguments, built

    def test_verified_record_is_redacted_and_source_set_shaped(self):
        policy_bytes, policy, arguments, built = self._built()
        verifier = mock.Mock(return_value=True)
        record = module.verify_remote_receiver_provenance(
            policy_payload=policy_bytes,
            attestation_payload=built.payload,
            expected=expected(arguments),
            now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
            verify_ed25519=verifier,
        )
        self.assertEqual(set(record), module.PROVENANCE_FIELDS)
        self.assertEqual(record["worker_request_sha256"], arguments["request_sha256"])
        self.assertEqual(record["object_storage"]["readback_version_id"], "version-1")
        self.assertFalse(record["presigned_url_persisted"])
        self.assertNotIn("signature_base64", record)
        verifier.assert_called_once_with(policy.public_key, b"s" * 64, built.signature_payload)

    def test_proof_substitution_replay_and_role_drift_fail_before_verifier(self):
        policy_bytes, _policy, arguments, built = self._built()
        verifier = mock.Mock(return_value=True)
        substituted = expected(arguments)
        substituted = module.ExpectedRemoteReceiverProvenance(
            **{**substituted.__dict__, "request_sha256": "b" * 64}
        )
        for expected_value in (
            substituted,
            module.ExpectedRemoteReceiverProvenance(
                **{**expected(arguments).__dict__, "operation_id": "7fb08095-7a9e-4a92-9fa9-3f9a301b2999"}
            ),
            module.ExpectedRemoteReceiverProvenance(
                **{**expected(arguments).__dict__, "role": "witness"}
            ),
        ):
            with self.subTest(expected=expected_value):
                with self.assertRaises(module.RemoteReceiverProvenanceError):
                    module.verify_remote_receiver_provenance(
                        policy_payload=policy_bytes,
                        attestation_payload=built.payload,
                        expected=expected_value,
                        now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
                        verify_ed25519=verifier,
                    )
        verifier.assert_not_called()

    def test_version_readback_drift_fails_before_verifier(self):
        policy_bytes, _policy, arguments, built = self._built()
        tampered = json.loads(built.payload)
        tampered["object_storage"]["readback_version_id"] = "version-2"
        tampered["signed_payload_sha256"] = hashlib.sha256(
            policy_module.receipt_signing_payload(tampered)
        ).hexdigest()
        tampered["receipt_sha256"] = hashlib.sha256(policy_module.receipt_payload(tampered)).hexdigest()
        verifier = mock.Mock(return_value=True)
        with self.assertRaises(module.RemoteReceiverProvenanceError):
            module.verify_remote_receiver_provenance(
                policy_payload=policy_bytes,
                attestation_payload=policy_module.canonical_json_bytes(tampered) + b"\n",
                expected=expected(arguments),
                now=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
                verify_ed25519=verifier,
            )
        verifier.assert_not_called()

    def test_module_has_no_live_operation_dependency(self):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket", "urllib", "boto3", "open(", "Ed25519PrivateKey"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
