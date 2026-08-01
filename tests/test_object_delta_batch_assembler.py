from __future__ import annotations

import json
import unittest

from core.object_delta_batch_assembler import (
    ObjectDeltaBatchAssemblyError,
    SourceOutboxDeltaItem,
    assemble_object_delta_payload,
)
from core.object_delta_source_batch_ledger import SourceStreamIdentity
from core.sync_metadata import build_sync_metadata
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)


FINGERPRINT = "0123456789abcdef"


def stream() -> SourceStreamIdentity:
    return SourceStreamIdentity(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id="wa-ir-standby-97265988-4b12-444e",
        release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        stream_generation_id="fi-ir-delta-97265988-a",
    )


def sync_item(*, sequence: int, change_log_id: int) -> dict:
    data = {"id": change_log_id, "full_name": f"User {change_log_id}"}
    return {
        "type": "db_change",
        "operation": "UPDATE",
        "table": "users",
        "id": change_log_id,
        "data": data,
        "hash": "a" * 64,
        "timestamp": 1785412800.0 + sequence,
        "change_log_id": change_log_id,
        "sync_protocol": {
            "protocol_version": SYNC_PROTOCOL_VERSION,
            "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
            "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
            "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
            "registry_version": SYNC_REGISTRY_VERSION,
            "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
            "registry_fingerprint": FINGERPRINT,
            "producer": {"server_mode": "foreign"},
        },
        "sync_meta": build_sync_metadata(
            "users",
            change_log_id,
            "UPDATE",
            data,
            change_log_id=change_log_id,
            source_server="foreign",
        ),
    }


def outbox_item(*, sequence: int, change_log_id: int | None = None, epoch: int = 7, lease="lease-7"):
    change_log_id = sequence + 100 if change_log_id is None else change_log_id
    return SourceOutboxDeltaItem(
        logical_sequence=sequence,
        change_log_id=change_log_id,
        writer_epoch=epoch,
        writer_lease_id=lease,
        canonical_sync_item=sync_item(sequence=sequence, change_log_id=change_log_id),
    )


class ObjectDeltaBatchAssemblerTests(unittest.TestCase):
    def test_assembles_canonical_contiguous_same_term_payload(self):
        prepared = assemble_object_delta_payload(
            stream=stream(),
            outbox_items=(outbox_item(sequence=4), outbox_item(sequence=5)),
            expected_registry_fingerprint=FINGERPRINT,
        )

        payload = json.loads(prepared.payload)
        self.assertEqual((4, 5), prepared.sequence_ids)
        self.assertEqual(4, prepared.first_sequence)
        self.assertEqual(5, prepared.last_sequence)
        self.assertEqual(7, prepared.writer_term.epoch)
        self.assertEqual([4, 5], [item["logical_sequence"] for item in payload["items"]])
        self.assertEqual(64, len(prepared.payload_sha256))

    def test_gap_or_reordering_is_not_normalized_or_sorted(self):
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "not contiguous"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(outbox_item(sequence=4), outbox_item(sequence=6)),
                expected_registry_fingerprint=FINGERPRINT,
            )
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "not contiguous"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(outbox_item(sequence=5), outbox_item(sequence=4)),
                expected_registry_fingerprint=FINGERPRINT,
            )

    def test_term_change_is_a_required_batch_boundary(self):
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "mixes Writer Witness"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(
                    outbox_item(sequence=4, epoch=7, lease="lease-7"),
                    outbox_item(sequence=5, epoch=8, lease="lease-8"),
                ),
                expected_registry_fingerprint=FINGERPRINT,
            )

    def test_item_fingerprint_and_duplicate_change_log_evidence_fail_closed(self):
        wrong_fingerprint = outbox_item(sequence=4)
        wrong_item = dict(wrong_fingerprint.canonical_sync_item)
        wrong_protocol = dict(wrong_item["sync_protocol"])
        wrong_protocol["registry_fingerprint"] = "f" * 16
        wrong_item["sync_protocol"] = wrong_protocol
        wrong_fingerprint = SourceOutboxDeltaItem(
            logical_sequence=wrong_fingerprint.logical_sequence,
            change_log_id=wrong_fingerprint.change_log_id,
            writer_epoch=wrong_fingerprint.writer_epoch,
            writer_lease_id=wrong_fingerprint.writer_lease_id,
            canonical_sync_item=wrong_item,
        )
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "payload is invalid"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(wrong_fingerprint,),
                expected_registry_fingerprint=FINGERPRINT,
            )
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "repeats ChangeLog"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(outbox_item(sequence=4, change_log_id=101), outbox_item(sequence=5, change_log_id=101)),
                expected_registry_fingerprint=FINGERPRINT,
            )

    def test_payload_bound_rejects_oversize_pre_encryption(self):
        with self.assertRaisesRegex(ObjectDeltaBatchAssemblyError, "exceeds"):
            assemble_object_delta_payload(
                stream=stream(),
                outbox_items=(outbox_item(sequence=4),),
                expected_registry_fingerprint=FINGERPRINT,
                maximum_payload_bytes=1,
            )


if __name__ == "__main__":
    unittest.main()
