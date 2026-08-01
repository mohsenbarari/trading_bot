from __future__ import annotations

import unittest

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    canonical_json_bytes,
    parse_delta_batch,
    sha256_bytes,
)
from core.append_only_sync_delta_payload import (
    build_object_delta_payload,
    parse_object_delta_payload,
)
from core.object_delta_import_plan import (
    IMPORT_ACTION_APPLY,
    IMPORT_ACTION_REPLAY,
    REQUIRED_ATOMIC_TRANSACTION_STEPS,
    ObjectDeltaImportPlanError,
    ReceiverStreamCursor,
    expected_import_receipt,
    plan_atomic_object_delta_import,
)
from core.sync_metadata import build_sync_metadata, build_sync_public_identity
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)


CAMPAIGN = "wa-ir-append-delta-20260730"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "ir-fi-stream-20260730-a"
REGISTRY_FINGERPRINT = "0123456789abcdef"


def immutable_receipt() -> dict[str, object]:
    return {
        "schema": IMMUTABLE_RECEIPT_SCHEMA,
        "status": "read_back_verified",
        "object_kind": "sync_delta_batch",
        "object_key": "campaigns/wa-ir-append-delta/delta-000001.age",
        "version_id": "3/L4kqtJlcpXroDTDmJ+3DcJKZBjjfM7m1E7S=",
        "ciphertext_sha256": "a" * 64,
        "ciphertext_bytes": 512,
    }


def sync_protocol() -> dict[str, object]:
    return {
        "protocol_version": SYNC_PROTOCOL_VERSION,
        "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
        "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
        "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
        "registry_version": SYNC_REGISTRY_VERSION,
        "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
        "registry_fingerprint": REGISTRY_FINGERPRINT,
        "producer": {"server_mode": "iran"},
    }


def change_item(*, logical_sequence: int, change_log_id: int, record_id: int) -> dict[str, object]:
    data: dict[str, object] = {"id": record_id, "name": f"Commodity {record_id}"}
    item: dict[str, object] = {
        "logical_sequence": logical_sequence,
        "type": "db_change",
        "operation": "INSERT",
        "table": "commodities",
        "id": record_id,
        "data": data,
        "hash": sha256_bytes(canonical_json_bytes({"change_log_id": change_log_id})),
        "timestamp": 1_785_000_000.25,
        "change_log_id": change_log_id,
        "sync_protocol": sync_protocol(),
        "sync_meta": build_sync_metadata(
            "commodities",
            record_id,
            "INSERT",
            data,
            change_log_id=change_log_id,
            source_server="iran",
        ),
    }
    public_identity = build_sync_public_identity("commodities", record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return item


def build_valid_batch_and_payload(
    *,
    sequences: tuple[int, ...] = (1, 2),
    prior: str = GENESIS_PRIOR_CHAIN_SHA256,
) -> tuple[object, object]:
    payload_value = build_object_delta_payload(
        stream_generation_id=GENERATION,
        items=[
            change_item(
                logical_sequence=sequence,
                change_log_id=40 + sequence * 7,
                record_id=100 + sequence,
            )
            for sequence in sequences
        ],
    )
    payload_raw = canonical_json_bytes(payload_value) + b"\n"
    batch_value = build_delta_batch(
        source_site="webapp_ir",
        destination_site="webapp_fi",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        writer_epoch=9,
        writer_lease_id="lease-9",
        stream_generation_id=GENERATION,
        stream_sequence_ids=sequences,
        payload=payload_raw,
        prior_chain_sha256=prior,
        immutable_receipt=immutable_receipt(),
    )
    batch = parse_delta_batch(canonical_json_bytes(batch_value) + b"\n")
    payload = parse_object_delta_payload(
        payload_raw,
        expected_stream_generation_id=GENERATION,
        expected_stream_sequence_ids=sequences,
        expected_source_server="iran",
        expected_registry_fingerprint=REGISTRY_FINGERPRINT,
    )
    return batch, payload


def build_broad_sync_batch_and_payload(
    *,
    table: str,
    operation: str,
    data: dict[str, object],
) -> tuple[object, object]:
    """Build a valid broad-Sync payload that lacks an executable receiver handler."""

    record_id = data["id"]
    assert type(record_id) is int
    item: dict[str, object] = {
        "logical_sequence": 1,
        "type": "db_change",
        "operation": operation,
        "table": table,
        "id": record_id,
        "data": data,
        "hash": sha256_bytes(canonical_json_bytes({"change_log_id": 91})),
        "timestamp": 1_785_000_000.5,
        "change_log_id": 91,
        "sync_protocol": sync_protocol(),
        "sync_meta": build_sync_metadata(
            table,
            record_id,
            operation,
            data,
            change_log_id=91,
            source_server="iran",
        ),
    }
    public_identity = build_sync_public_identity(table, record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    payload_value = build_object_delta_payload(
        stream_generation_id=GENERATION,
        items=[item],
    )
    payload_raw = canonical_json_bytes(payload_value) + b"\n"
    batch_value = build_delta_batch(
        source_site="webapp_ir",
        destination_site="webapp_fi",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        writer_epoch=9,
        writer_lease_id="lease-9",
        stream_generation_id=GENERATION,
        stream_sequence_ids=(1,),
        payload=payload_raw,
        prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
        immutable_receipt=immutable_receipt(),
    )
    return (
        parse_delta_batch(canonical_json_bytes(batch_value) + b"\n"),
        parse_object_delta_payload(
            payload_raw,
            expected_stream_generation_id=GENERATION,
            expected_stream_sequence_ids=(1,),
            expected_source_server="iran",
            expected_registry_fingerprint=REGISTRY_FINGERPRINT,
        ),
    )


def plan_for(batch, payload, **overrides):
    options = {
        "batch": batch,
        "payload": payload,
        "local_site": "webapp_fi",
        "expected_source_site": "webapp_ir",
        "expected_campaign_id": CAMPAIGN,
        "expected_release_sha": RELEASE,
        "expected_stream_generation_id": GENERATION,
        "expected_writer_epoch": 9,
        "expected_writer_lease_id": "lease-9",
        "expected_registry_fingerprint": REGISTRY_FINGERPRINT,
        "receiver_cursor": None,
        "receipt_by_object": None,
        "receipt_by_stream": None,
    }
    options.update(overrides)
    return plan_atomic_object_delta_import(**options)


class ObjectDeltaImportPlanTests(unittest.TestCase):
    def test_genesis_batch_returns_one_atomic_apply_plan_without_runtime_side_effects(self) -> None:
        batch, payload = build_valid_batch_and_payload()

        plan = plan_for(batch, payload)

        self.assertEqual(IMPORT_ACTION_APPLY, plan.action)
        self.assertEqual((1, 2), tuple(change.logical_sequence for change in plan.changes_to_apply))
        self.assertEqual((47, 54), tuple(change.change_log_id for change in plan.changes_to_apply))
        self.assertEqual(batch.batch_sha256, plan.cursor_to_write.last_batch_sha256)
        self.assertEqual(batch.immutable_receipt.version_id, plan.receipt_to_insert.object_version_id)
        self.assertFalse(hasattr(plan.changes_to_apply[0], "sync_item"))
        self.assertEqual("commodities", plan.changes_to_apply[0].intent.table)
        self.assertEqual("INSERT", plan.changes_to_apply[0].intent.operation)
        self.assertEqual("Commodity 101", plan.changes_to_apply[0].intent.name)
        self.assertIn("source signature", REQUIRED_ATOMIC_TRANSACTION_STEPS[0])
        self.assertIn("dedicated no-side-effect adapter", REQUIRED_ATOMIC_TRANSACTION_STEPS[6])

    def test_next_batch_requires_exact_cursor_sequence_and_chain(self) -> None:
        first, first_payload = build_valid_batch_and_payload()
        first_plan = plan_for(first, first_payload)
        second, second_payload = build_valid_batch_and_payload(
            sequences=(3, 4), prior=first.batch_sha256
        )

        plan = plan_for(second, second_payload, receiver_cursor=first_plan.cursor_to_write)

        self.assertEqual(IMPORT_ACTION_APPLY, plan.action)
        self.assertEqual(4, plan.cursor_to_write.last_sequence)

    def test_gap_or_wrong_predecessor_is_rejected_before_any_future_write(self) -> None:
        first, first_payload = build_valid_batch_and_payload()
        cursor = plan_for(first, first_payload).cursor_to_write
        gapped, gapped_payload = build_valid_batch_and_payload(
            sequences=(4, 5), prior=first.batch_sha256
        )
        wrong_prior, wrong_prior_payload = build_valid_batch_and_payload(
            sequences=(3, 4), prior="b" * 64
        )

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "next logical"):
            plan_for(gapped, gapped_payload, receiver_cursor=cursor)
        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "predecessor"):
            plan_for(wrong_prior, wrong_prior_payload, receiver_cursor=cursor)

    def test_exact_existing_receipt_is_a_zero_mutation_idempotent_replay(self) -> None:
        batch, payload = build_valid_batch_and_payload()
        receipt = expected_import_receipt(batch)
        cursor = ReceiverStreamCursor(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=GENERATION,
            last_sequence=batch.stream.last_sequence,
            last_batch_sha256=batch.batch_sha256,
        )

        plan = plan_for(
            batch,
            payload,
            receiver_cursor=cursor,
            receipt_by_object=receipt,
            receipt_by_stream=receipt,
        )

        self.assertEqual(IMPORT_ACTION_REPLAY, plan.action)
        self.assertEqual((), plan.changes_to_apply)
        self.assertIsNone(plan.receipt_to_insert)
        self.assertIsNone(plan.cursor_to_write)

    def test_conflicting_object_or_stream_receipt_fails_closed(self) -> None:
        batch, payload = build_valid_batch_and_payload()
        receipt = expected_import_receipt(batch)
        conflicting = receipt.__class__(
            **{**receipt.__dict__, "payload_sha256": "b" * 64}
        )

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "lookups disagree"):
            plan_for(
                batch,
                payload,
                receipt_by_object=receipt,
                receipt_by_stream=conflicting,
            )

    def test_existing_receipt_without_durable_cursor_is_rejected(self) -> None:
        batch, payload = build_valid_batch_and_payload()

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "has no receiver cursor"):
            plan_for(batch, payload, receipt_by_object=expected_import_receipt(batch))

    def test_receiver_binding_and_payload_source_are_not_inferred_from_transport(self) -> None:
        batch, payload = build_valid_batch_and_payload()

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "destination site"):
            plan_for(batch, payload, local_site="webapp_ir")
        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "receiver registry"):
            plan_for(batch, payload, expected_registry_fingerprint="f" * 16)

    def test_unapproved_stream_generation_cannot_restart_at_genesis(self) -> None:
        batch, payload = build_valid_batch_and_payload()

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "stream generation"):
            plan_for(batch, payload, expected_stream_generation_id="fi-ir-stream-20260730-b")

    def test_broad_sync_registry_tables_and_non_insert_operations_cannot_form_receiver_plans(self) -> None:
        cases = (
            (
                "users",
                "UPDATE",
                {"id": 101, "full_name": "Still broad-sync only"},
                "table has no release-pinned receiver handler",
            ),
            (
                "commodities",
                "UPDATE",
                {"id": 102, "name": "A commodity update"},
                "operation has no release-pinned receiver handler",
            ),
        )
        for table, operation, data, error in cases:
            with self.subTest(table=table, operation=operation):
                batch, payload = build_broad_sync_batch_and_payload(
                    table=table,
                    operation=operation,
                    data=data,
                )
                with self.assertRaisesRegex(ObjectDeltaImportPlanError, error):
                    plan_for(batch, payload)

    def test_commodity_payload_cannot_smuggle_fields_outside_the_exact_handler_contract(self) -> None:
        batch, payload = build_broad_sync_batch_and_payload(
            table="commodities",
            operation="INSERT",
            data={"id": 103, "name": "Only natural key", "unexpected": "not applied"},
        )

        with self.assertRaisesRegex(ObjectDeltaImportPlanError, "exact receiver handler contract"):
            plan_for(batch, payload)


if __name__ == "__main__":
    unittest.main()
