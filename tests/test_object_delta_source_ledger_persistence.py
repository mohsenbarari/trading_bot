from __future__ import annotations

from dataclasses import replace
import unittest

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    sha256_bytes,
    validate_delta_batch,
)
from core.object_delta_source_batch_ledger import (
    SOURCE_BATCH_APPEND_ACTION_APPEND,
    SOURCE_BATCH_APPEND_ACTION_REPLAY,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_source_batch_publication import PreparedObjectDeltaSourceBatch
from core.object_delta_source_ledger_persistence import (
    _legacy_test_only_persist_prepared_object_delta_source_batch_ledger as persist_prepared_object_delta_source_batch_ledger,
    ObjectDeltaSourceLedgerPersistenceError,
    persist_prepared_object_delta_source_batch_ledger as disabled_persist_prepared_object_delta_source_batch_ledger,
)
from core.legacy_source_publication_fence import (
    LegacyObjectDeltaSourcePublicationDisabledError,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)
from models.object_delta import ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger


CAMPAIGN = "wa-ir-source-ledger-persist-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-ledger-persist-20260731"
PAYLOAD = b'{"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1","items":[]}'
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _LedgerSession:
    """Async-session double that exposes the adapter's fixed lock order."""

    def __init__(self, *, stream, terminal=None, same_range=None, same_batch=None, same_object=None, active=True):
        self.stream = stream
        self._ledger_rows = iter((terminal, same_range, same_batch, same_object))
        self.active = active
        self.statements = []
        self.added = []
        self.flush_count = 0
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def in_transaction(self):
        return self.active

    async def execute(self, statement):
        self.statements.append(statement)
        if "pg_advisory_xact_lock" in str(statement):
            return _ScalarResult(None)
        entities = {
            description.get("entity")
            for description in getattr(statement, "column_descriptions", ())
            if isinstance(description, dict)
        }
        if ObjectDeltaStream in entities:
            return _ScalarResult(self.stream)
        if ObjectDeltaSourceBatchLedger in entities:
            return _ScalarResult(next(self._ledger_rows))
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        for value in self.added:
            if isinstance(value, ObjectDeltaSourceBatchLedger) and value.id is None:
                value.id = 901

    async def begin(self):
        self.begin_count += 1
        raise AssertionError("adapter must not begin a transaction")

    async def commit(self):
        self.commit_count += 1
        raise AssertionError("adapter must not commit")

    async def rollback(self):
        self.rollback_count += 1
        raise AssertionError("adapter must not roll back")


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient=FI_RECIPIENT,
        webapp_ir_age_recipient=IR_RECIPIENT,
    )


def stream() -> ObjectDeltaStream:
    return ObjectDeltaStream(
        id=701,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        next_sequence=3,
    )


def prepared(*, writer_epoch: int = 7, writer_lease_id: str = "writer-lease-7") -> PreparedObjectDeltaSourceBatch:
    payload_sha256 = sha256_bytes(PAYLOAD)
    object_key = derive_object_delta_object_key(
        policy(),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=1,
        last_sequence=2,
        payload_sha256=payload_sha256,
    )
    batch = validate_delta_batch(
        build_delta_batch(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            stream_generation_id=GENERATION,
            stream_sequence_ids=(1, 2),
            payload=PAYLOAD,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": "version-20260731-01",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )
    transport = bind_object_delta_batch(policy(), batch)
    ledger = SourceBatchLedgerEntry(
        stream=SourceStreamIdentity(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
        ),
        first_sequence=batch.stream.first_sequence,
        last_sequence=batch.stream.last_sequence,
        writer_epoch=batch.writer_term.epoch,
        writer_lease_id=batch.writer_term.lease_id,
        prior_chain_sha256=batch.prior_chain_sha256,
        batch_sha256=batch.batch_sha256,
        payload_sha256=batch.payload_sha256,
        payload_bytes=batch.payload_bytes,
        object_key=batch.immutable_receipt.object_key,
        object_version_id=batch.immutable_receipt.version_id,
        ciphertext_sha256=batch.immutable_receipt.ciphertext_sha256,
        ciphertext_bytes=batch.immutable_receipt.ciphertext_bytes,
    )
    return PreparedObjectDeltaSourceBatch(
        batch=batch,
        transport_binding=transport,
        ledger_entry=ledger,
    )


def ledger_row(entry: SourceBatchLedgerEntry, *, stream_id: int = 701) -> ObjectDeltaSourceBatchLedger:
    return ObjectDeltaSourceBatchLedger(
        id=801,
        stream_id=stream_id,
        first_sequence=entry.first_sequence,
        last_sequence=entry.last_sequence,
        writer_epoch=entry.writer_epoch,
        writer_lease_id=entry.writer_lease_id,
        prior_chain_sha256=entry.prior_chain_sha256,
        batch_sha256=entry.batch_sha256,
        payload_sha256=entry.payload_sha256,
        payload_bytes=entry.payload_bytes,
        object_key=entry.object_key,
        object_version_id=entry.object_version_id,
        ciphertext_sha256=entry.ciphertext_sha256,
        ciphertext_bytes=entry.ciphertext_bytes,
    )


class ObjectDeltaSourceLedgerPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_former_public_persistence_entrypoint_is_hard_disabled_before_sql(self):
        session = _LedgerSession(stream=stream())

        with self.assertRaisesRegex(
            LegacyObjectDeltaSourcePublicationDisabledError,
            "hard-disabled.*locked source snapshot.*live Writer Witness",
        ):
            await disabled_persist_prepared_object_delta_source_batch_ledger(session, prepared())

        self.assertEqual([], session.statements)
        self.assertEqual([], session.added)

    def test_legacy_persistence_mechanics_are_not_star_exported(self):
        import core.object_delta_source_ledger_persistence as adapter

        self.assertNotIn(
            "_legacy_test_only_persist_prepared_object_delta_source_batch_ledger",
            adapter.__all__,
        )
        self.assertNotIn(
            "persist_prepared_object_delta_source_batch_ledger",
            adapter.__all__,
        )

    async def test_append_locks_every_identity_and_inserts_only_the_planned_immutable_row(self):
        value = prepared()
        session = _LedgerSession(stream=stream())

        result = await persist_prepared_object_delta_source_batch_ledger(session, value)

        self.assertEqual(SOURCE_BATCH_APPEND_ACTION_APPEND, result.action)
        self.assertEqual(value.ledger_entry, result.ledger_entry)
        self.assertEqual(901, result.ledger_row.id)
        self.assertEqual([ObjectDeltaSourceBatchLedger], [type(row) for row in session.added])
        self.assertEqual(value.ledger_entry.object_version_id, result.ledger_row.object_version_id)
        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertGreaterEqual(statements.count("FOR UPDATE"), 5)
        self.assertEqual(1, session.flush_count)
        self.assertEqual(0, session.begin_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_exact_retry_locks_all_rows_and_replays_without_insert_or_flush(self):
        value = prepared()
        existing = ledger_row(value.ledger_entry)
        session = _LedgerSession(
            stream=stream(),
            terminal=existing,
            same_range=existing,
            same_batch=existing,
            same_object=existing,
        )

        result = await persist_prepared_object_delta_source_batch_ledger(session, value)

        self.assertEqual(SOURCE_BATCH_APPEND_ACTION_REPLAY, result.action)
        self.assertIs(existing, result.ledger_row)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_conflicting_same_range_fails_without_any_insert(self):
        value = prepared()
        conflict = ledger_row(value.ledger_entry)
        conflict.object_version_id = "version-20260731-conflict"
        session = _LedgerSession(stream=stream(), same_range=conflict)

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerPersistenceError, "conflicts"):
            await persist_prepared_object_delta_source_batch_ledger(session, value)

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_foreign_object_version_row_fails_closed(self):
        value = prepared()
        foreign = ledger_row(value.ledger_entry, stream_id=702)
        session = _LedgerSession(stream=stream(), same_object=foreign)

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerPersistenceError, "different source stream"):
            await persist_prepared_object_delta_source_batch_ledger(session, value)

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_mismatched_locked_stream_fails_before_ledger_writes(self):
        value = prepared()
        mismatched_stream = stream()
        mismatched_stream.campaign_id = "wa-ir-source-ledger-persist-mismatch-20260731"
        session = _LedgerSession(stream=mismatched_stream)

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerPersistenceError, "does not match"):
            await persist_prepared_object_delta_source_batch_ledger(session, value)

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_inactive_transaction_or_mixed_prepared_result_issues_no_sql(self):
        value = prepared()
        inactive = _LedgerSession(stream=stream(), active=False)

        with self.assertRaisesRegex(ObjectDeltaSourceLedgerPersistenceError, "active caller-owned transaction"):
            await persist_prepared_object_delta_source_batch_ledger(inactive, value)
        self.assertEqual([], inactive.statements)

        mixed = replace(
            value,
            ledger_entry=replace(value.ledger_entry, payload_bytes=value.ledger_entry.payload_bytes + 1),
        )
        session = _LedgerSession(stream=stream())
        with self.assertRaisesRegex(ObjectDeltaSourceLedgerPersistenceError, "does not match its batch"):
            await persist_prepared_object_delta_source_batch_ledger(session, mixed)
        self.assertEqual([], session.statements)


if __name__ == "__main__":
    unittest.main()
