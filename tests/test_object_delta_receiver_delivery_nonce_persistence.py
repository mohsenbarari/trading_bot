from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from core.object_delta_receiver_delivery_nonce import (
    RECEIVER_DELIVERY_NONCE_ACTION_CONSUME,
    RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
    ObjectDeltaReceiverDeliveryNonceReceipt as NonceReceipt,
)
from core.object_delta_receiver_delivery_nonce_persistence import (
    ObjectDeltaReceiverDeliveryNoncePersistenceError,
    persist_object_delta_receiver_delivery_nonce,
    receiver_delivery_nonce_advisory_lock_key,
)
from models.object_delta_receiver_delivery import (
    ObjectDeltaReceiverDeliveryNonceReceipt,
)


EXPIRES_AT = datetime(2026, 7, 31, 12, 4, 0, tzinfo=timezone.utc)


def receipt() -> NonceReceipt:
    return NonceReceipt(
        controller_key_id="ed25519-sha256:" + "a" * 64,
        nonce="b" * 64,
        packet_claim_sha256="c" * 64,
        bucket="private-delta-bucket",
        source_site="webapp_fi",
        destination_site="webapp_ir",
        destination_age_recipient="age1" + "c" * 30,
        campaign_id="wa-ir-delta-nonce-persist-20260731",
        release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        stream_generation_id="fi-ir-delta-nonce-persist-20260731",
        writer_epoch=7,
        writer_lease_id="writer-lease-7",
        first_sequence=1,
        last_sequence=2,
        batch_sha256="d" * 64,
        object_key="campaigns/three-site/delta-object-20260731",
        object_version_id="version-20260731-01",
        expires_at=EXPIRES_AT,
    )


def row(value: NonceReceipt) -> ObjectDeltaReceiverDeliveryNonceReceipt:
    return ObjectDeltaReceiverDeliveryNonceReceipt(
        id=901,
        controller_key_id=value.controller_key_id,
        nonce=value.nonce,
        packet_claim_sha256=value.packet_claim_sha256,
        bucket=value.bucket,
        source_site=value.source_site,
        destination_site=value.destination_site,
        destination_age_recipient=value.destination_age_recipient,
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        stream_generation_id=value.stream_generation_id,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        first_sequence=value.first_sequence,
        last_sequence=value.last_sequence,
        batch_sha256=value.batch_sha256,
        object_key=value.object_key,
        object_version_id=value.object_version_id,
        expires_at=value.expires_at,
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _NonceSession:
    def __init__(self, *, existing=None, active=True):
        self.existing = existing
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
        if ObjectDeltaReceiverDeliveryNonceReceipt in entities:
            return _ScalarResult(self.existing)
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        for value in self.added:
            if isinstance(value, ObjectDeltaReceiverDeliveryNonceReceipt) and value.id is None:
                value.id = 902

    async def begin(self):
        self.begin_count += 1
        raise AssertionError("adapter must not begin a transaction")

    async def commit(self):
        self.commit_count += 1
        raise AssertionError("adapter must not commit")

    async def rollback(self):
        self.rollback_count += 1
        raise AssertionError("adapter must not roll back")


class ObjectDeltaReceiverDeliveryNoncePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_locks_nonce_and_inserts_only_the_expected_row(self):
        value = receipt()
        session = _NonceSession()

        result = await persist_object_delta_receiver_delivery_nonce(session, value)

        self.assertEqual(RECEIVER_DELIVERY_NONCE_ACTION_CONSUME, result.action)
        self.assertEqual(value, result.receipt)
        self.assertEqual(902, result.row.id)
        self.assertEqual([ObjectDeltaReceiverDeliveryNonceReceipt], [type(item) for item in session.added])
        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("FOR UPDATE", statements)
        self.assertEqual(1, session.flush_count)
        self.assertEqual(0, session.begin_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_exact_retry_replays_without_insert_or_flush(self):
        value = receipt()
        existing = row(value)
        session = _NonceSession(existing=existing)

        result = await persist_object_delta_receiver_delivery_nonce(session, value)

        self.assertEqual(RECEIVER_DELIVERY_NONCE_ACTION_REPLAY, result.action)
        self.assertIs(existing, result.row)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_conflicting_nonce_row_fails_without_writes(self):
        value = receipt()
        session = _NonceSession(existing=row(replace(value, batch_sha256="e" * 64)))

        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNoncePersistenceError, "conflicts"):
            await persist_object_delta_receiver_delivery_nonce(session, value)

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_inactive_transaction_and_invalid_receipt_issue_no_sql(self):
        inactive = _NonceSession(active=False)
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNoncePersistenceError, "active caller-owned"):
            await persist_object_delta_receiver_delivery_nonce(inactive, receipt())
        self.assertEqual([], inactive.statements)

        invalid = _NonceSession()
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNoncePersistenceError, "expected"):
            await persist_object_delta_receiver_delivery_nonce(
                invalid,
                replace(receipt(), nonce="not-hex"),
            )
        self.assertEqual([], invalid.statements)

    def test_nonce_advisory_lock_key_is_stable_and_nonce_scoped(self):
        self.assertEqual(
            receiver_delivery_nonce_advisory_lock_key(receipt()),
            receiver_delivery_nonce_advisory_lock_key(receipt()),
        )
        self.assertNotEqual(
            receiver_delivery_nonce_advisory_lock_key(receipt()),
            receiver_delivery_nonce_advisory_lock_key(replace(receipt(), nonce="e" * 64)),
        )
