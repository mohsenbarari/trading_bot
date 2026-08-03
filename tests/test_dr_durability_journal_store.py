from __future__ import annotations

import unittest

from core.dr_durability_journal import (
    DurabilityJournalError,
    build_prepare,
    parse_resolution,
)
from core.dr_durability_journal_store import (
    commit_record,
    prepare_record,
    rollback_record,
)
from core.dr_event_protocol import destination_transaction_hash, sha256_json, transaction_hash_from_envelopes


_TRANSACTION_ID = "12345678-1234-4234-8234-123456789abc"


def _prepare():
    payload = {"id": 1, "normalized_mobile": "09120000001"}
    event = {
        "protocol_version": 2,
        "event_id": "12345678-1234-4234-8234-123456789ab1",
        "origin_authority": "webapp",
        "origin_physical_site": "webapp_fi",
        "producer_epoch": 7,
        "producer_sequence": 1,
        "aggregate_type": "messages",
        "aggregate_id": "message-1",
        "aggregate_db_id": "1",
        "aggregate_version": 1,
        "operation": "INSERT",
        "canonical_payload": payload,
        "canonical_payload_hash": sha256_json(payload),
        "schema_version": 1,
        "causation_id": None,
        "idempotency_key": "message-1",
        "writer_epoch": 7,
        "tombstone": False,
        "created_at": "2026-08-03T12:00:00+00:00",
        "transaction_id": _TRANSACTION_ID,
        "transaction_position": 1,
        "transaction_size": 1,
        "transaction_hash": "0" * 64,
        "destination_streams": {
            "webapp_ir": {
                "sequence": 1,
                "transaction_id": _TRANSACTION_ID,
                "transaction_position": 1,
                "transaction_size": 1,
                "transaction_hash": "0" * 64,
            }
        },
    }
    event["transaction_hash"] = transaction_hash_from_envelopes([event])
    event["destination_streams"]["webapp_ir"]["transaction_hash"] = destination_transaction_hash(
        [event], destination_site="webapp_ir"
    )
    return build_prepare(
        envelopes=[event],
        origin_physical_site="webapp_fi",
        writer_epoch=7,
        transaction_id=_TRANSACTION_ID,
        transaction_hash=event["transaction_hash"],
        local_transaction_gid="_sa_1234567890abcdef1234567890abcdef",
        release_sha="e00283c037ec5ca63340b9827768256b1c5ef144",
        encryption_key_id="staging-fi-journal-v1",
        encryption_secret="journal-encryption-secret-is-at-least-32-bytes",
    )


class _MemorySession:
    def __init__(self):
        self.rows = {}

    async def get(self, _model, key, **_kwargs):
        return self.rows.get(tuple(key))

    def add(self, row):
        self.rows[(row.origin_physical_site, row.writer_epoch, row.transaction_id)] = row

    async def flush(self):
        return None


class DurabilityJournalStoreTests(unittest.IsolatedAsyncioTestCase):
    def _resolution(self, *, gid: str | None):
        record = _prepare()
        payload = {
            "schema": "three-site-same-region-journal-v1",
            "origin_physical_site": record.origin_physical_site,
            "writer_epoch": record.writer_epoch,
            "transaction_id": record.transaction_id,
            "transaction_hash": record.transaction_hash,
        }
        if gid is not None:
            payload["prepared_transaction_gid"] = gid
        return parse_resolution(payload, require_prepared_gid=gid is not None)

    async def test_prepare_is_idempotent_but_conflicting_ciphertext_is_rejected(self):
        session = _MemorySession()
        record = _prepare()
        first = await prepare_record(session, prepare=record, request_hash="a" * 64)
        repeated = await prepare_record(session, prepare=record, request_hash="b" * 64)
        self.assertIs(first, repeated)
        # A second encryption of the same transaction has a new nonce and is
        # therefore a distinct immutable payload, not a retry.
        conflict = _prepare()
        with self.assertRaisesRegex(DurabilityJournalError, "immutable existing"):
            await prepare_record(session, prepare=conflict, request_hash="c" * 64)

    async def test_commit_is_idempotent_and_rollback_cannot_erase_committed_record(self):
        session = _MemorySession()
        record = _prepare()
        await prepare_record(session, prepare=record, request_hash="a" * 64)
        gid = "_sa_1234567890abcdef1234567890abcdef"
        committed = await commit_record(session, resolution=self._resolution(gid=gid))
        self.assertEqual((committed.state, committed.prepared_transaction_gid), ("committed", gid))
        repeated = await commit_record(session, resolution=self._resolution(gid=gid))
        self.assertIs(repeated, committed)
        with self.assertRaisesRegex(DurabilityJournalError, "committed"):
            await rollback_record(session, resolution=self._resolution(gid=None))

    async def test_commit_gid_must_match_the_prepare_binding(self):
        session = _MemorySession()
        record = _prepare()
        await prepare_record(session, prepare=record, request_hash="a" * 64)
        with self.assertRaisesRegex(DurabilityJournalError, "does not match"):
            await commit_record(
                session,
                resolution=self._resolution(gid="_sa_ffffffffffffffffffffffffffffffff"),
            )

    async def test_rollback_only_resolves_unprepared_record(self):
        session = _MemorySession()
        record = _prepare()
        await prepare_record(session, prepare=record, request_hash="a" * 64)
        rolled_back = await rollback_record(session, resolution=self._resolution(gid=None))
        self.assertEqual(rolled_back.state, "rolled_back")
        with self.assertRaisesRegex(DurabilityJournalError, "rolled back"):
            await prepare_record(session, prepare=record, request_hash="b" * 64)
