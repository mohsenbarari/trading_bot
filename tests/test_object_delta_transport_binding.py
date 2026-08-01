from __future__ import annotations

import unittest

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    validate_delta_batch,
)
from core.object_delta_import_plan import expected_import_receipt
from core.object_delta_source_batch_ledger import SourceBatchLedgerEntry, SourceStreamIdentity
from core.object_delta_transport_binding import (
    CONTROLLER_CREDENTIAL_HOLDER,
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
    required_object_metadata,
    validate_object_delta_transport_policy,
)


CAMPAIGN = "wa-ir-object-delta-transport-20260730"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-object-delta-stream-20260730"
PAYLOAD = b'{"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1","items":[]}'
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient=FI_RECIPIENT,
        webapp_ir_age_recipient=IR_RECIPIENT,
    )


def object_key(
    *,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    payload: bytes = PAYLOAD,
) -> str:
    from core.append_only_sync_delta_batch import sha256_bytes

    return derive_object_delta_object_key(
        policy(),
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=1,
        last_sequence=2,
        payload_sha256=sha256_bytes(payload),
    )


def batch(
    *,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    payload: bytes = PAYLOAD,
    receipt_key: str | None = None,
) -> object:
    raw = build_delta_batch(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        writer_epoch=7,
        writer_lease_id="writer-lease-7",
        stream_generation_id=GENERATION,
        stream_sequence_ids=(1, 2),
        payload=payload,
        prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
        immutable_receipt={
            "schema": IMMUTABLE_RECEIPT_SCHEMA,
            "status": "read_back_verified",
            "object_kind": "sync_delta_batch",
            "object_key": receipt_key
            or object_key(
                source_site=source_site,
                destination_site=destination_site,
                payload=payload,
            ),
            "version_id": "version-20260730-01",
            "ciphertext_sha256": "d" * 64,
            "ciphertext_bytes": 1024,
        },
    )
    return validate_delta_batch(raw)


class ObjectDeltaTransportBindingTests(unittest.TestCase):
    def test_fi_to_ir_batch_has_one_ir_recipient_and_deterministic_key(self) -> None:
        binding = bind_object_delta_batch(policy(), batch())

        self.assertEqual("webapp_fi", binding.source_site)
        self.assertEqual("webapp_ir", binding.destination_site)
        self.assertEqual(IR_RECIPIENT, binding.destination_age_recipient)
        self.assertEqual(object_key(), binding.object_key)
        self.assertNotIn("controller", binding.destination_age_recipient)
        self.assertNotIn("://", binding.object_key)
        self.assertIn("/object-delta/v1/", binding.object_key)

        metadata = required_object_metadata(binding)
        self.assertEqual(OBJECT_DELTA_TRANSPORT_SCHEMA, metadata["transport-schema"])
        self.assertEqual(OBJECT_DELTA_ENCRYPTION, metadata["encryption"])
        self.assertEqual("webapp_fi", metadata["source-site"])
        self.assertEqual("webapp_ir", metadata["destination-site"])

    def test_ir_to_fi_reverse_route_selects_only_fi_recipient(self) -> None:
        binding = bind_object_delta_batch(
            policy(),
            batch(source_site="webapp_ir", destination_site="webapp_fi"),
        )

        self.assertEqual(FI_RECIPIENT, binding.destination_age_recipient)
        self.assertIn("/webapp_ir/webapp_fi/", binding.object_key)

    def test_binding_composes_with_import_receipt_and_source_ledger(self) -> None:
        value = batch()
        binding = bind_object_delta_batch(policy(), value)
        receipt = expected_import_receipt(value)
        ledger = SourceBatchLedgerEntry(
            stream=SourceStreamIdentity(
                source_site=value.source_site,
                destination_site=value.destination_site,
                campaign_id=value.campaign_id,
                release_sha=value.release_sha,
                stream_generation_id=value.stream.generation_id,
            ),
            first_sequence=value.stream.first_sequence,
            last_sequence=value.stream.last_sequence,
            writer_epoch=value.writer_term.epoch,
            writer_lease_id=value.writer_term.lease_id,
            prior_chain_sha256=value.prior_chain_sha256,
            batch_sha256=value.batch_sha256,
            payload_sha256=value.payload_sha256,
            payload_bytes=value.payload_bytes,
            object_key=binding.object_key,
            object_version_id=binding.object_version_id,
            ciphertext_sha256=binding.ciphertext_sha256,
            ciphertext_bytes=binding.ciphertext_bytes,
        )

        self.assertEqual(binding.object_key, receipt.object_key)
        self.assertEqual(binding.object_version_id, receipt.object_version_id)
        self.assertEqual(binding.object_key, ledger.object_key)

    def test_changed_payload_gets_a_new_key_but_tampered_receipt_is_rejected(self) -> None:
        changed_payload = PAYLOAD + b"!"
        self.assertNotEqual(object_key(), object_key(payload=changed_payload))

        with self.assertRaisesRegex(ObjectDeltaTransportBindingError, "deterministic Object key"):
            bind_object_delta_batch(
                policy(),
                batch(receipt_key="campaigns/three-site/object-delta/unbound.age"),
            )

    def test_policy_refuses_non_controller_credential_holder_and_duplicate_recipients(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaTransportBindingError, "only by the controller"):
            validate_object_delta_transport_policy(
                ObjectDeltaTransportPolicy(
                    bucket="private-delta-bucket",
                    prefix="campaigns/three-site",
                    webapp_fi_age_recipient=FI_RECIPIENT,
                    webapp_ir_age_recipient=IR_RECIPIENT,
                    credential_holder="webapp_fi",
                )
            )
        with self.assertRaisesRegex(ObjectDeltaTransportBindingError, "must be distinct"):
            validate_object_delta_transport_policy(
                ObjectDeltaTransportPolicy(
                    bucket="private-delta-bucket",
                    prefix="campaigns/three-site",
                    webapp_fi_age_recipient=FI_RECIPIENT,
                    webapp_ir_age_recipient=FI_RECIPIENT,
                )
            )
        self.assertEqual(
            CONTROLLER_CREDENTIAL_HOLDER,
            validate_object_delta_transport_policy(policy()).credential_holder,
        )


if __name__ == "__main__":
    unittest.main()
