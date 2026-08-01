from __future__ import annotations

import unittest

from core.append_only_sync_delta_batch import canonical_json_bytes, sha256_bytes
from core.append_only_sync_delta_payload import (
    OBJECT_DELTA_PAYLOAD_SCHEMA,
    OBJECT_DELTA_SYNC_TABLES,
    ObjectDeltaPayloadError,
    build_object_delta_payload,
    normalize_object_delta_payload,
    parse_object_delta_payload,
)
from core.sync_metadata import build_sync_metadata
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)
from core.sync_registry import SyncPolicy, sync_registry_entries


GENERATION = "fi-ir-stream-20260730-a"
SOURCE_SERVER = "foreign"
REGISTRY_FINGERPRINT = "0123456789abcdef"


def sync_protocol() -> dict[str, object]:
    return {
        "protocol_version": SYNC_PROTOCOL_VERSION,
        "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
        "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
        "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
        "registry_version": SYNC_REGISTRY_VERSION,
        "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
        "registry_fingerprint": REGISTRY_FINGERPRINT,
        "producer": {"server_mode": SOURCE_SERVER},
    }


def change_item(
    *,
    logical_sequence: int = 1,
    change_log_id: int = 41,
    record_id: int = 101,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": record_id,
        "full_name": f"User {record_id}",
    }
    return {
        "logical_sequence": logical_sequence,
        "type": "db_change",
        "operation": "UPDATE",
        "table": "users",
        "id": record_id,
        "data": data,
        "hash": sha256_bytes(canonical_json_bytes({"change_log_id": change_log_id})),
        "timestamp": 1_785_000_000.25,
        "change_log_id": change_log_id,
        "sync_protocol": sync_protocol(),
        "sync_meta": build_sync_metadata(
            "users",
            record_id,
            "UPDATE",
            data,
            change_log_id=change_log_id,
            source_server=SOURCE_SERVER,
        ),
    }


def raw_payload(value: dict[str, object]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


class ObjectDeltaPayloadTests(unittest.TestCase):
    def test_pure_table_snapshot_matches_the_enabled_registry(self) -> None:
        # The pure validator deliberately avoids importing sync_registry at
        # runtime, so this test makes snapshot drift a release-time failure.
        enabled = {
            table_name
            for table_name, entry in sync_registry_entries().items()
            if entry.policy == SyncPolicy.SYNC
        }
        self.assertEqual(OBJECT_DELTA_SYNC_TABLES, enabled)

    def test_valid_payload_normalizes_only_canonical_db_changes(self) -> None:
        value = build_object_delta_payload(
            stream_generation_id=GENERATION,
            items=[
                change_item(logical_sequence=1, change_log_id=41, record_id=101),
                change_item(logical_sequence=2, change_log_id=47, record_id=102),
            ],
        )
        payload = parse_object_delta_payload(
            raw_payload(value),
            expected_stream_generation_id=GENERATION,
            expected_stream_sequence_ids=(1, 2),
            expected_source_server=SOURCE_SERVER,
            expected_registry_fingerprint=REGISTRY_FINGERPRINT,
        )

        self.assertEqual(OBJECT_DELTA_PAYLOAD_SCHEMA, value["schema"])
        self.assertEqual(GENERATION, payload.stream_generation_id)
        self.assertEqual((1, 2), tuple(item.logical_sequence for item in payload.items))
        self.assertEqual((41, 47), tuple(item.change_log_id for item in payload.items))
        self.assertNotIn("logical_sequence", payload.items[0].as_sync_item())

    def test_notification_side_effect_item_is_rejected(self) -> None:
        item = change_item()
        item["type"] = "notification"
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [item],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "type must be db_change"):
            normalize_object_delta_payload(value)

    def test_external_parse_requires_all_outer_batch_bindings(self) -> None:
        value = build_object_delta_payload(
            stream_generation_id=GENERATION,
            items=[change_item()],
        )

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "requires all outer-batch bindings"):
            parse_object_delta_payload(raw_payload(value))

    def test_no_sync_table_is_rejected_before_any_future_import(self) -> None:
        item = change_item()
        item["table"] = "messages"
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [item],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "not enabled for sync"):
            normalize_object_delta_payload(value)

    def test_reordered_or_gapped_logical_items_are_rejected(self) -> None:
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [
                change_item(logical_sequence=2, change_log_id=41, record_id=101),
                change_item(logical_sequence=1, change_log_id=47, record_id=102),
            ],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "not contiguous and ordered"):
            normalize_object_delta_payload(value)

    def test_malformed_logical_sequence_is_rejected(self) -> None:
        item = change_item()
        item["logical_sequence"] = True
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [item],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "logical sequence is invalid"):
            normalize_object_delta_payload(value)

    def test_noncontiguous_expected_batch_binding_is_rejected(self) -> None:
        value = build_object_delta_payload(
            stream_generation_id=GENERATION,
            items=[change_item(logical_sequence=1, change_log_id=41)],
        )

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "does not match the batch manifest"):
            normalize_object_delta_payload(value, expected_stream_sequence_ids=(1, 2))

    def test_duplicate_changelog_evidence_is_rejected_even_with_distinct_logical_positions(self) -> None:
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [
                change_item(logical_sequence=1, change_log_id=41, record_id=101),
                change_item(logical_sequence=2, change_log_id=41, record_id=102),
            ],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "evidence is not unique"):
            normalize_object_delta_payload(value)

    def test_unsanitized_local_only_field_is_rejected(self) -> None:
        item = change_item()
        data = dict(item["data"])
        data["admin_password_hash"] = "local-only-secret"
        item["data"] = data
        item["sync_meta"] = build_sync_metadata(
            "users",
            101,
            "UPDATE",
            data,
            change_log_id=41,
            source_server=SOURCE_SERVER,
        )
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [item],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "violates the sync field policy"):
            normalize_object_delta_payload(value)

    def test_foreign_source_cannot_emit_iran_authoritative_table(self) -> None:
        item = change_item()
        item["table"] = "commodities"
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": GENERATION,
            "items": [item],
        }

        with self.assertRaisesRegex(ObjectDeltaPayloadError, "lacks table authority"):
            normalize_object_delta_payload(value)


if __name__ == "__main__":
    unittest.main()
