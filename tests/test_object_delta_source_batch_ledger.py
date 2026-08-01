from __future__ import annotations

from pathlib import Path
import unittest

import models
from core.object_delta_source_batch_ledger import (
    GENESIS_PRIOR_CHAIN_SHA256,
    OUTBOUND_ACK_ACTION_ADVANCE,
    OUTBOUND_ACK_ACTION_REPLAY,
    SOURCE_BATCH_APPEND_ACTION_APPEND,
    SOURCE_BATCH_APPEND_ACTION_REPLAY,
    ObjectDeltaSourceLedgerError,
    OutboundAckCursor,
    SourceBatchAcknowledgement,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
    plan_outbound_ack_cursor,
    plan_source_batch_ledger_append,
)
from models.database import Base
from models.object_delta_source_batch import (
    ObjectDeltaOutboundAckCursor,
    ObjectDeltaSourceBatchLedger,
)


def _stream(**overrides) -> SourceStreamIdentity:
    options = {
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": "wa-ir-append-delta-20260730",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        "stream_generation_id": "fi-ir-stream-20260730-a",
    }
    options.update(overrides)
    return SourceStreamIdentity(**options)


def _entry(*, first=1, last=3, prior=GENESIS_PRIOR_CHAIN_SHA256, batch="a" * 64, **overrides) -> SourceBatchLedgerEntry:
    options = {
        "stream": _stream(),
        "first_sequence": first,
        "last_sequence": last,
        "writer_epoch": 9,
        "writer_lease_id": "lease-9",
        "prior_chain_sha256": prior,
        "batch_sha256": batch,
        "payload_sha256": "b" * 64,
        "payload_bytes": 512,
        "object_key": "campaign/delta-fi-ir-000001.age",
        "object_version_id": "version-001",
        "ciphertext_sha256": "c" * 64,
        "ciphertext_bytes": 768,
    }
    options.update(overrides)
    return SourceBatchLedgerEntry(**options)


def _ack(entry: SourceBatchLedgerEntry, **overrides) -> SourceBatchAcknowledgement:
    options = {
        "stream": entry.stream,
        "first_sequence": entry.first_sequence,
        "last_sequence": entry.last_sequence,
        "batch_sha256": entry.batch_sha256,
    }
    options.update(overrides)
    return SourceBatchAcknowledgement(**options)


class ObjectDeltaSourceBatchLedgerTests(unittest.TestCase):
    def test_first_batch_appends_only_at_genesis(self):
        candidate = _entry()

        plan = plan_source_batch_ledger_append(
            candidate=candidate,
            previous_entry=None,
            existing_by_first_sequence=None,
            existing_by_batch_sha256=None,
            existing_by_object_version=None,
        )

        self.assertEqual(SOURCE_BATCH_APPEND_ACTION_APPEND, plan.action)
        self.assertIs(candidate, plan.entry_to_insert)

    def test_exact_retry_replays_without_new_source_ledger_row(self):
        candidate = _entry()

        plan = plan_source_batch_ledger_append(
            candidate=candidate,
            previous_entry=None,
            existing_by_first_sequence=candidate,
            existing_by_batch_sha256=candidate,
            existing_by_object_version=candidate,
        )

        self.assertEqual(SOURCE_BATCH_APPEND_ACTION_REPLAY, plan.action)
        self.assertIsNone(plan.entry_to_insert)

    def test_conflicting_retry_cannot_replace_immutable_object_receipt(self):
        existing = _entry()
        candidate = _entry(object_version_id="version-002")

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerError, "conflicts"):
            plan_source_batch_ledger_append(
                candidate=candidate,
                previous_entry=None,
                existing_by_first_sequence=existing,
                existing_by_batch_sha256=None,
                existing_by_object_version=None,
            )

    def test_successor_requires_the_terminal_batch_and_its_chain_hash(self):
        first = _entry()
        successor = _entry(
            first=4,
            last=6,
            prior=first.batch_sha256,
            batch="d" * 64,
            payload_sha256="e" * 64,
            object_key="campaign/delta-fi-ir-000004.age",
            object_version_id="version-004",
            ciphertext_sha256="f" * 64,
        )

        plan = plan_source_batch_ledger_append(
            candidate=successor,
            previous_entry=first,
            existing_by_first_sequence=None,
            existing_by_batch_sha256=None,
            existing_by_object_version=None,
        )

        self.assertEqual(SOURCE_BATCH_APPEND_ACTION_APPEND, plan.action)
        with self.assertRaisesRegex(ObjectDeltaSourceLedgerError, "predecessor does not match"):
            plan_source_batch_ledger_append(
                candidate=_entry(
                    first=4,
                    last=6,
                    prior="0" * 64,
                    batch="d" * 64,
                    payload_sha256="e" * 64,
                    object_key="campaign/delta-fi-ir-000004.age",
                    object_version_id="version-004",
                    ciphertext_sha256="f" * 64,
                ),
                previous_entry=first,
                existing_by_first_sequence=None,
                existing_by_batch_sha256=None,
                existing_by_object_version=None,
            )

    def test_acknowledgement_advances_only_the_next_contiguous_batch(self):
        first = _entry()

        plan = plan_outbound_ack_cursor(
            cursor=None,
            acknowledgement=_ack(first),
            ledger_entry=first,
        )

        self.assertEqual(OUTBOUND_ACK_ACTION_ADVANCE, plan.action)
        self.assertEqual(3, plan.cursor_to_write.last_acknowledged_sequence)
        self.assertEqual(first.batch_sha256, plan.cursor_to_write.last_acknowledged_batch_sha256)

    def test_acknowledgement_retry_is_a_noop_after_restart(self):
        first = _entry()
        cursor = OutboundAckCursor(
            stream=first.stream,
            last_acknowledged_sequence=first.last_sequence,
            last_acknowledged_batch_sha256=first.batch_sha256,
        )

        plan = plan_outbound_ack_cursor(
            cursor=cursor,
            acknowledgement=_ack(first),
            ledger_entry=first,
        )

        self.assertEqual(OUTBOUND_ACK_ACTION_REPLAY, plan.action)
        self.assertIsNone(plan.cursor_to_write)

    def test_acknowledgement_gap_or_tampering_fails_closed(self):
        first = _entry()
        later = _entry(
            first=4,
            last=6,
            prior=first.batch_sha256,
            batch="d" * 64,
            payload_sha256="e" * 64,
            object_key="campaign/delta-fi-ir-000004.age",
            object_version_id="version-004",
            ciphertext_sha256="f" * 64,
        )

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerError, "next logical sequence"):
            plan_outbound_ack_cursor(
                cursor=None,
                acknowledgement=_ack(later),
                ledger_entry=later,
            )
        with self.assertRaisesRegex(ObjectDeltaSourceLedgerError, "does not match immutable"):
            plan_outbound_ack_cursor(
                cursor=None,
                acknowledgement=_ack(first, batch_sha256="d" * 64),
                ledger_entry=first,
            )

    def test_genesis_cursor_cannot_have_a_non_genesis_hash(self):
        with self.assertRaisesRegex(ObjectDeltaSourceLedgerError, "genesis acknowledgement"):
            OutboundAckCursor(
                stream=_stream(),
                last_acknowledged_sequence=0,
                last_acknowledged_batch_sha256="a" * 64,
            )


class ObjectDeltaSourceBatchLedgerSchemaTests(unittest.TestCase):
    def test_package_exports_and_migration_environment_register_the_source_schema(self):
        self.assertIs(models.ObjectDeltaSourceBatchLedger, ObjectDeltaSourceBatchLedger)
        self.assertIs(models.ObjectDeltaOutboundAckCursor, ObjectDeltaOutboundAckCursor)

        migration_env = Path(__file__).parents[1] / "migrations/env.py"
        self.assertIn(
            "import models.object_delta_source_batch",
            migration_env.read_text(encoding="utf-8"),
        )

    def test_models_keep_ledger_immutable_and_cursor_separate(self):
        ledger = Base.metadata.tables[ObjectDeltaSourceBatchLedger.__tablename__]
        cursor = Base.metadata.tables[ObjectDeltaOutboundAckCursor.__tablename__]

        self.assertEqual({"object_delta_streams.id"}, {str(fk.target_fullname) for fk in ledger.foreign_keys})
        unique_names = {constraint.name for constraint in ledger.constraints if constraint.name}
        self.assertIn("ux_object_delta_source_batch_ledger_stream_first_sequence", unique_names)
        self.assertIn("ux_object_delta_source_batch_ledger_stream_batch_hash", unique_names)
        self.assertIn("ux_object_delta_source_batch_ledger_object_version", unique_names)
        self.assertEqual({"object_delta_streams.id"}, {str(fk.target_fullname) for fk in cursor.foreign_keys})
        self.assertIn(
            "ux_object_delta_outbound_ack_cursors_stream",
            {constraint.name for constraint in cursor.constraints if constraint.name},
        )

    def test_sibling_migration_depends_on_the_object_delta_foundation(self):
        path = Path(__file__).parents[1] / "migrations/versions/b2c3d4e5f6a7_add_object_delta_source_batch_ledger.py"
        migration = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "0deltasource01"', migration)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "0deltadelta01"', migration)
        self.assertEqual(2, migration.count("op.create_table("))
        self.assertNotIn("boto3", migration)
        self.assertNotIn("subprocess", migration)
        self.assertIn("refusing destructive object-delta source ledger downgrade", migration)


if __name__ == "__main__":
    unittest.main()
