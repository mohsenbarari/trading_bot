from __future__ import annotations

import unittest

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    DELTA_OBJECT_KIND,
)
from core.object_delta_batch_assembler import (
    SourceOutboxDeltaItem,
    assemble_object_delta_payload,
)
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_ledger import SourceStreamIdentity
from core.object_delta_source_batch_publication import (
    ObjectDeltaSourceBatchPublicationError,
    prepare_object_delta_source_batch,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    derive_object_delta_object_key,
)
from tests.test_object_delta_batch_assembler import FINGERPRINT, outbox_item, stream


CAMPAIGN = "wa-ir-standby-97265988-4b12-444e"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-delta-97265988-a"


def binding(**overrides) -> ObjectDeltaSourceRuntimeBinding:
    values = {
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": CAMPAIGN,
        "release_sha": RELEASE,
        "stream_generation_id": GENERATION,
        "expected_registry_fingerprint": FINGERPRINT,
    }
    values.update(overrides)
    return ObjectDeltaSourceRuntimeBinding(**values)


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


def prepared_payload():
    return assemble_object_delta_payload(
        stream=stream(),
        outbox_items=(outbox_item(sequence=4), outbox_item(sequence=5)),
        expected_registry_fingerprint=FINGERPRINT,
    )


def receipt(prepared):
    return {
        "schema": IMMUTABLE_RECEIPT_SCHEMA,
        "status": IMMUTABLE_RECEIPT_STATUS,
        "object_kind": DELTA_OBJECT_KIND,
        "object_key": derive_object_delta_object_key(
            policy(),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=GENERATION,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            payload_sha256=prepared.payload_sha256,
        ),
        "version_id": "version-20260730-04",
        "ciphertext_sha256": "d" * 64,
        "ciphertext_bytes": 1024,
    }


class ObjectDeltaSourceBatchPublicationTests(unittest.TestCase):
    def test_verified_ciphertext_becomes_a_transport_bound_batch_and_ledger_candidate(self):
        prepared = prepared_payload()

        result = prepare_object_delta_source_batch(
            binding=binding(),
            policy=policy(),
            prepared_payload=prepared,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            verified_ciphertext_receipt=receipt(prepared),
        )

        self.assertEqual((4, 5), result.batch.stream.sequence_ids)
        self.assertEqual("webapp_ir", result.transport_binding.destination_site)
        self.assertEqual(receipt(prepared)["object_key"], result.ledger_entry.object_key)
        self.assertEqual(result.batch.batch_sha256, result.ledger_entry.batch_sha256)
        self.assertEqual(result.batch.payload_sha256, result.ledger_entry.payload_sha256)
        self.assertEqual((7, "lease-7"), (
            result.ledger_entry.writer_epoch,
            result.ledger_entry.writer_lease_id,
        ))

    def test_receipt_cannot_name_a_different_object_key_or_object_kind(self):
        prepared = prepared_payload()
        wrong_key = receipt(prepared)
        wrong_key["object_key"] = "campaigns/three-site/object-delta/v1/other.age"
        wrong_kind = receipt(prepared)
        wrong_kind["object_kind"] = "release-bundle"

        for candidate, pattern in ((wrong_key, "key"), (wrong_kind, "receipt")):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ObjectDeltaSourceBatchPublicationError, pattern):
                    prepare_object_delta_source_batch(
                        binding=binding(),
                        policy=policy(),
                        prepared_payload=prepared,
                        prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
                        verified_ciphertext_receipt=candidate,
                    )

    def test_payload_stream_and_canonical_content_are_revalidated_before_batch_sealing(self):
        prepared = prepared_payload()
        altered_stream = prepared.__class__(
            stream=SourceStreamIdentity(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="fi-ir-delta-97265988-b",
            ),
            writer_term=prepared.writer_term,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            sequence_ids=prepared.sequence_ids,
            payload=prepared.payload,
            payload_sha256=prepared.payload_sha256,
        )
        altered_bytes = prepared.__class__(
            stream=prepared.stream,
            writer_term=prepared.writer_term,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            sequence_ids=prepared.sequence_ids,
            payload=b"{}",
            payload_sha256=prepared.payload_sha256,
        )
        altered_hash = prepared.__class__(
            stream=prepared.stream,
            writer_term=prepared.writer_term,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            sequence_ids=prepared.sequence_ids,
            payload=prepared.payload,
            payload_sha256="f" * 64,
        )

        for candidate in (altered_stream, altered_bytes, altered_hash):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ObjectDeltaSourceBatchPublicationError):
                    prepare_object_delta_source_batch(
                        binding=binding(),
                        policy=policy(),
                        prepared_payload=candidate,
                        prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
                        verified_ciphertext_receipt=receipt(prepared),
                    )

    def test_changed_ciphertext_version_creates_a_distinct_candidate_for_ledger_conflict_detection(self):
        prepared = prepared_payload()
        first = prepare_object_delta_source_batch(
            binding=binding(),
            policy=policy(),
            prepared_payload=prepared,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            verified_ciphertext_receipt=receipt(prepared),
        )
        changed_receipt = receipt(prepared)
        changed_receipt["version_id"] = "version-20260730-05"
        second = prepare_object_delta_source_batch(
            binding=binding(),
            policy=policy(),
            prepared_payload=prepared,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            verified_ciphertext_receipt=changed_receipt,
        )

        self.assertNotEqual(first.ledger_entry, second.ledger_entry)
        self.assertNotEqual(first.ledger_entry.batch_sha256, second.ledger_entry.batch_sha256)


if __name__ == "__main__":
    unittest.main()
