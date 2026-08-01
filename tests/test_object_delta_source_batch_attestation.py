from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    build_delta_batch,
    canonical_json_bytes,
    sha256_bytes,
    validate_delta_batch,
)
from core.object_delta_source_batch_attestation import (
    OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA,
    ObjectDeltaSourceBatchAttestationError,
    build_object_delta_source_batch_attestation,
    canonical_object_delta_source_batch_attestation_bytes,
    parse_object_delta_source_batch_attestation_json,
    source_key_id_from_public_key,
    verify_object_delta_source_batch_attestation,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-source-attestation-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-attestation-stream-20260731"
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30
PAYLOAD = b'{"items":[],"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1"}'


def policy(**overrides) -> ObjectDeltaTransportPolicy:
    value = {
        "bucket": "private-delta-bucket",
        "prefix": "campaigns/three-site",
        "webapp_fi_age_recipient": FI_RECIPIENT,
        "webapp_ir_age_recipient": IR_RECIPIENT,
    }
    value.update(overrides)
    return ObjectDeltaTransportPolicy(**value)


def batch(*, writer_epoch: int = 7):
    payload_hash = sha256_bytes(PAYLOAD)
    object_key = derive_object_delta_object_key(
        policy(),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=1,
        last_sequence=2,
        payload_sha256=payload_hash,
    )
    return validate_delta_batch(
        build_delta_batch(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=writer_epoch,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=(1, 2),
            payload=PAYLOAD,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": IMMUTABLE_RECEIPT_STATUS,
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": "version-20260731-01",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class ObjectDeltaSourceBatchAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.source_public_key = public_key(self.signer)
        self.batch = batch()
        self.binding = bind_object_delta_batch(policy(), self.batch)

    def attest(self):
        return build_object_delta_source_batch_attestation(
            batch=self.batch,
            transport_policy=policy(),
            transport_binding=self.binding,
            source_signer=self.signer,
        )

    def test_real_ed25519_signature_verifies_the_exact_batch_policy_and_binding(self) -> None:
        attestation = self.attest()

        verified = verify_object_delta_source_batch_attestation(
            attestation,
            expected_source_public_key=self.source_public_key,
            expected_transport_policy=policy(),
        )

        self.assertEqual(OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA, attestation["schema"])
        self.assertEqual(self.batch, verified.batch)
        self.assertEqual(self.binding, verified.transport_binding)
        self.assertEqual(policy(), verified.transport_policy)
        self.assertEqual(source_key_id_from_public_key(self.source_public_key), verified.source_key_id)
        self.assertEqual(64, len(verified.attestation_sha256))

    def test_canonical_json_round_trip_rejects_duplicate_and_noncanonical_wire_forms(self) -> None:
        attestation = self.attest()
        raw = canonical_object_delta_source_batch_attestation_bytes(attestation)

        parsed = parse_object_delta_source_batch_attestation_json(raw)

        self.assertEqual(attestation, parsed)
        duplicate = raw[:-2] + b',"schema":"duplicate"}\n'
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "duplicate"):
            parse_object_delta_source_batch_attestation_json(duplicate)
        noncanonical = json.dumps(attestation, sort_keys=True, indent=2).encode("utf-8")
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "canonical"):
            parse_object_delta_source_batch_attestation_json(noncanonical)

    def test_parser_fails_closed_when_json_recursion_is_exhausted(self) -> None:
        with patch(
            "core.object_delta_source_batch_attestation.json.loads",
            side_effect=RecursionError("nested JSON"),
        ):
            with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "JSON is invalid"):
                parse_object_delta_source_batch_attestation_json(b"{}\n")

    def test_tampered_signed_content_or_signature_fails_closed(self) -> None:
        changed_batch = batch(writer_epoch=8)
        tampered = self.attest()
        tampered["batch"] = {
            **tampered["batch"],
            "writer_term": {"epoch": changed_batch.writer_term.epoch, "lease_id": changed_batch.writer_term.lease_id},
            "batch_sha256": changed_batch.batch_sha256,
        }
        tampered["transport_binding"] = {
            **tampered["transport_binding"],
            "ciphertext_sha256": changed_batch.immutable_receipt.ciphertext_sha256,
        }
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "signature"):
            verify_object_delta_source_batch_attestation(
                tampered,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

        bad_signature = self.attest()
        bad_signature["source_signature"]["signature_base64"] = "A" * 88
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "signature"):
            verify_object_delta_source_batch_attestation(
                bad_signature,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

    def test_pinned_key_and_key_identifier_confusion_are_rejected(self) -> None:
        other_signer = Ed25519PrivateKey.generate()
        other_attestation = build_object_delta_source_batch_attestation(
            batch=self.batch,
            transport_policy=policy(),
            transport_binding=self.binding,
            source_signer=other_signer,
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "not pinned"):
            verify_object_delta_source_batch_attestation(
                other_attestation,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

        confused = self.attest()
        confused["source_signer"]["key_id"] = source_key_id_from_public_key(public_key(other_signer))
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "key ID"):
            verify_object_delta_source_batch_attestation(
                confused,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

    def test_policy_and_binding_mismatches_are_rejected_before_signing_or_verification(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "binding"):
            build_object_delta_source_batch_attestation(
                batch=self.batch,
                transport_policy=policy(prefix="campaigns/other-site"),
                transport_binding=self.binding,
                source_signer=self.signer,
            )

        other_policy = policy(bucket="other-private-delta-bucket")
        other_attestation = build_object_delta_source_batch_attestation(
            batch=self.batch,
            transport_policy=other_policy,
            transport_binding=bind_object_delta_batch(other_policy, self.batch),
            source_signer=self.signer,
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "policy does not match"):
            verify_object_delta_source_batch_attestation(
                other_attestation,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

        wrong_schema = self.attest()
        wrong_schema["transport_policy"]["transport_schema"] = "other-transport-schema"
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "protocol"):
            verify_object_delta_source_batch_attestation(
                wrong_schema,
                expected_source_public_key=self.source_public_key,
                expected_transport_policy=policy(),
            )

        bad_binding = replace(self.binding, object_version_id="version-20260731-other")
        with self.assertRaisesRegex(ObjectDeltaSourceBatchAttestationError, "does not match"):
            build_object_delta_source_batch_attestation(
                batch=self.batch,
                transport_policy=policy(),
                transport_binding=bad_binding,
                source_signer=self.signer,
            )


if __name__ == "__main__":
    unittest.main()
