from __future__ import annotations

import unittest

from core.append_only_sync_delta_batch import (
    DELTA_BATCH_SCHEMA,
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    AppendOnlySyncDeltaBatchError,
    build_delta_batch,
    canonical_json_bytes,
    parse_delta_batch,
    sha256_bytes,
    validate_delta_batch,
    verify_delta_payload,
)


CAMPAIGN = "wa-ir-append-delta-20260730"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
STREAM_GENERATION = "fi-ir-stream-20260730-a"
# ChangeLog IDs are deliberately opaque payload evidence.  They may have
# normal PostgreSQL sequence gaps and are not the append-only stream cursor.
PAYLOAD = b'{"change_log_ids":[41,47,102],"records":[]}'


def immutable_receipt() -> dict[str, object]:
    return {
        "schema": IMMUTABLE_RECEIPT_SCHEMA,
        "status": "read_back_verified",
        "object_kind": "sync_delta_batch",
        "object_key": "campaigns/wa-ir-append-delta/delta-000041.age",
        "version_id": "3/L4kqtJlcpXroDTDmJ+3DcJKZBjjfM7m1E7S=",
        "ciphertext_sha256": "a" * 64,
        "ciphertext_bytes": 512,
    }


def batch_value(
    *,
    sequence_ids: list[int] | None = None,
    generation_id: str = STREAM_GENERATION,
    payload: bytes = PAYLOAD,
    prior: str = GENESIS_PRIOR_CHAIN_SHA256,
) -> dict[str, object]:
    return build_delta_batch(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        writer_epoch=9,
        writer_lease_id="lease-9",
        stream_generation_id=generation_id,
        stream_sequence_ids=[1, 2, 3] if sequence_ids is None else sequence_ids,
        payload=payload,
        prior_chain_sha256=prior,
        immutable_receipt=immutable_receipt(),
    )


def raw_batch(value: dict[str, object]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def rehash(value: dict[str, object]) -> dict[str, object]:
    unsigned = {key: item for key, item in value.items() if key != "batch_sha256"}
    return {**unsigned, "batch_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


class AppendOnlySyncDeltaBatchTests(unittest.TestCase):
    def test_valid_canonical_batch_binds_all_future_import_prerequisites(self) -> None:
        value = batch_value()
        batch = parse_delta_batch(
            raw_batch(value),
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=9,
            expected_writer_lease_id="lease-9",
            expected_prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            expected_stream_generation_id=STREAM_GENERATION,
            expected_first_stream_sequence=1,
        )

        self.assertEqual(DELTA_BATCH_SCHEMA, value["schema"])
        self.assertEqual(STREAM_GENERATION, batch.stream.generation_id)
        self.assertEqual((1, 2, 3), batch.stream.sequence_ids)
        self.assertEqual(3, batch.stream.last_sequence)
        self.assertTrue(value["import_intent"]["side_effects_disabled"])
        self.assertEqual("read_back_verified", value["immutable_receipt"]["status"])
        verify_delta_payload(batch, PAYLOAD)

    def test_logical_sequence_gap_is_rejected_even_when_the_batch_hash_is_recomputed(self) -> None:
        value = batch_value()
        value["stream"] = {
            "generation_id": STREAM_GENERATION,
            "first_sequence": 1,
            "last_sequence": 3,
            "sequence_ids": [1, 3],
        }

        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "range is not contiguous"):
            validate_delta_batch(rehash(value))

    def test_reordered_logical_sequence_is_rejected_even_when_the_batch_hash_is_recomputed(self) -> None:
        value = batch_value()
        value["stream"] = {
            "generation_id": STREAM_GENERATION,
            "first_sequence": 1,
            "last_sequence": 3,
            "sequence_ids": [2, 1, 3],
        }

        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "not contiguous and ordered"):
            validate_delta_batch(rehash(value))

    def test_opaque_changelog_evidence_may_have_normal_sequence_gaps(self) -> None:
        payload = b'{"change_log_ids":[7,13,29],"records":[]}'
        batch = parse_delta_batch(
            raw_batch(batch_value(payload=payload)),
            expected_stream_generation_id=STREAM_GENERATION,
            expected_first_stream_sequence=1,
        )

        self.assertEqual((1, 2, 3), batch.stream.sequence_ids)
        verify_delta_payload(batch, payload)

    def test_cross_generation_is_rejected_when_following_a_known_chain(self) -> None:
        first = batch_value(generation_id="fi-ir-stream-20260730-a")
        cross_generation = batch_value(
            generation_id="fi-ir-stream-20260730-b",
            prior=str(first["batch_sha256"]),
        )

        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "generation does not match"):
            parse_delta_batch(
                raw_batch(cross_generation),
                expected_stream_generation_id="fi-ir-stream-20260730-a",
                expected_prior_chain_sha256=str(first["batch_sha256"]),
            )

    def test_chain_continuation_requires_an_expected_generation(self) -> None:
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "generation is required"):
            parse_delta_batch(
                raw_batch(batch_value()),
                expected_prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            )

    def test_legacy_raw_changelog_header_is_rejected(self) -> None:
        value = batch_value()
        del value["stream"]
        value["change_log"] = {"first_id": 41, "last_id": 43, "ids": [41, 42, 43]}

        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "fields are invalid"):
            validate_delta_batch(rehash(value))

    def test_manifest_tamper_and_payload_tamper_both_fail_closed(self) -> None:
        value = batch_value()
        value["payload"] = {"sha256": "b" * 64, "bytes": len(PAYLOAD)}
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "batch hash is invalid"):
            parse_delta_batch(raw_batch(value))

        batch = parse_delta_batch(raw_batch(batch_value()))
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "does not match its batch descriptor"):
            verify_delta_payload(batch, PAYLOAD + b"!")

    def test_cross_term_is_rejected_before_any_future_importer_can_use_the_batch(self) -> None:
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "writer term does not match"):
            parse_delta_batch(
                raw_batch(batch_value()),
                expected_writer_epoch=10,
                expected_writer_lease_id="lease-10",
            )

    def test_cross_campaign_is_rejected_before_any_future_importer_can_use_the_batch(self) -> None:
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "campaign does not match"):
            parse_delta_batch(
                raw_batch(batch_value()),
                expected_campaign_id="other-append-delta-20260730",
            )

    def test_version_bound_receipt_rejects_an_unversioned_object(self) -> None:
        value = batch_value()
        receipt = dict(value["immutable_receipt"])
        receipt["version_id"] = "null"
        value["immutable_receipt"] = receipt

        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "version_id is invalid"):
            validate_delta_batch(rehash(value))

    def test_noncanonical_bytes_are_rejected(self) -> None:
        with self.assertRaisesRegex(AppendOnlySyncDeltaBatchError, "not canonical"):
            parse_delta_batch(raw_batch(batch_value()) + b" ")


if __name__ == "__main__":
    unittest.main()
