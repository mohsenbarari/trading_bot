from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest
from uuid import UUID

from core.object_delta_outbox_allocator import canonical_sync_item_sha256
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_publication_snapshot import (
    ObjectDeltaLockedSourcePublicationSnapshot,
    ObjectDeltaLockedSourcePublicationSnapshotError,
    require_locked_object_delta_source_publication_snapshot,
    snapshot_locked_object_delta_source_publication,
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
from models.object_delta import ObjectDeltaOutboxEntry, ObjectDeltaSourceCutover, ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger


CAMPAIGN = "wa-ir-locked-publication-snapshot-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-locked-publication-snapshot-20260731"
FINGERPRINT = "0123456789abcdef"
MAXIMUM_PAYLOAD_BYTES = 1024 * 1024


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _RowsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


_DEFAULT_CUTOVER = object()


class _LockedSnapshotSession:
    """Async-session double exposing the required lock/query sequence."""

    def __init__(self, *, stream, cutover=_DEFAULT_CUTOVER, terminal=None, outbox=(), active=True):
        self.stream = stream
        self.cutover = (
            published_cutover(stream) if cutover is _DEFAULT_CUTOVER and stream is not None else cutover
        )
        self.terminal = terminal
        self.outbox = tuple(outbox)
        self.active = active
        self.statements = []
        self.mutations = []

    def in_transaction(self):
        return self.active

    async def execute(self, statement):
        self.statements.append(statement)
        rendered = str(statement)
        if "pg_advisory_xact_lock" in rendered:
            return _ScalarResult(None)
        entities = {
            description.get("entity")
            for description in getattr(statement, "column_descriptions", ())
            if isinstance(description, dict)
        }
        if ObjectDeltaStream in entities:
            return _ScalarResult(self.stream)
        if ObjectDeltaSourceCutover in entities:
            return _ScalarResult(self.cutover)
        if ObjectDeltaSourceBatchLedger in entities:
            return _ScalarResult(self.terminal)
        if ObjectDeltaOutboxEntry in entities:
            limit = getattr(getattr(statement, "_limit_clause", None), "value", None)
            if type(limit) is not int:
                raise AssertionError("locked outbox query must use a concrete bounded limit")
            return _RowsResult(self.outbox[:limit])
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.mutations.append("add")
        raise AssertionError("snapshot adapter must not add rows")

    async def flush(self):
        self.mutations.append("flush")
        raise AssertionError("snapshot adapter must not flush")

    async def begin(self):
        self.mutations.append("begin")
        raise AssertionError("snapshot adapter must not begin")

    async def commit(self):
        self.mutations.append("commit")
        raise AssertionError("snapshot adapter must not commit")

    async def rollback(self):
        self.mutations.append("rollback")
        raise AssertionError("snapshot adapter must not roll back")


def binding() -> ObjectDeltaSourceRuntimeBinding:
    return ObjectDeltaSourceRuntimeBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        expected_registry_fingerprint=FINGERPRINT,
    )


def source_stream(*, next_sequence: int) -> ObjectDeltaStream:
    return ObjectDeltaStream(
        id=701,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        next_sequence=next_sequence,
    )


def published_cutover(stream: ObjectDeltaStream, **overrides: object) -> ObjectDeltaSourceCutover:
    values: dict[str, object] = {
        "id": 801,
        "stream_id": stream.id,
        "source_site": stream.source_site,
        "destination_site": stream.destination_site,
        "campaign_id": stream.campaign_id,
        "release_sha": stream.release_sha,
        "stream_generation_id": stream.stream_generation_id,
        "write_gate_id": UUID("11111111-2222-3333-4444-555555555555"),
        "registry_fingerprint": FINGERPRINT,
        "writer_epoch": 7,
        "writer_lease_id": "lease-7",
        "source_generation": "fi-generation-20260731",
        "snapshot_id": "20260731T000000Z-" + "a" * 16,
        "alembic_revision": "0deltacutover01",
        "snapshot_manifest_object_key": "campaigns/three-site/snapshot-manifest.age",
        "snapshot_manifest_object_version_id": "version-snapshot-20260731",
        "snapshot_manifest_ciphertext_sha256": "a" * 64,
        "snapshot_manifest_ciphertext_bytes": 1024,
        "baseline_manifest_object_key": "campaigns/three-site/baseline-manifest.age",
        "baseline_manifest_object_version_id": "version-baseline-20260731",
        "baseline_manifest_ciphertext_sha256": "b" * 64,
        "baseline_manifest_ciphertext_bytes": 2048,
        "database_sha256": "c" * 64,
        "uploads_sha256": "d" * 64,
        "state": "baseline_published",
    }
    values.update(overrides)
    return ObjectDeltaSourceCutover(**values)


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


def outbox_row(
    *,
    sequence: int,
    epoch: int = 7,
    lease_id: str = "lease-7",
    stream_id: int = 701,
) -> ObjectDeltaOutboxEntry:
    item = sync_item(sequence=sequence, change_log_id=sequence + 100)
    return ObjectDeltaOutboxEntry(
        id=900 + sequence,
        stream_id=stream_id,
        logical_sequence=sequence,
        change_log_id=sequence + 100,
        writer_epoch=epoch,
        writer_lease_id=lease_id,
        canonical_sync_item=item,
        sync_item_sha256=canonical_sync_item_sha256(item),
    )


def terminal_row(*, last_sequence: int = 2, epoch: int = 7, lease_id: str = "lease-7") -> ObjectDeltaSourceBatchLedger:
    return ObjectDeltaSourceBatchLedger(
        id=801,
        stream_id=701,
        first_sequence=1,
        last_sequence=last_sequence,
        writer_epoch=epoch,
        writer_lease_id=lease_id,
        prior_chain_sha256="0" * 64,
        batch_sha256="b" * 64,
        payload_sha256="c" * 64,
        payload_bytes=512,
        object_key="campaigns/three-site/terminal.age",
        object_version_id="version-terminal",
        ciphertext_sha256="d" * 64,
        ciphertext_bytes=1024,
    )


class ObjectDeltaLockedSourcePublicationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_locks_fixed_order_and_binds_prefix_to_terminal_chain(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=5),
            terminal=terminal_row(),
            outbox=(outbox_row(sequence=3), outbox_row(sequence=4)),
        )

        result = await snapshot_locked_object_delta_source_publication(
            session,
            binding(),
            max_items=4,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertFalse(result.no_work)
        self.assertEqual(701, result.source_stream_id)
        self.assertEqual("b" * 64, result.prior_chain_sha256)
        self.assertEqual((3, 4), result.prepared_payload.sequence_ids)
        self.assertEqual((7, "lease-7"), (
            result.cutover_writer_term.epoch,
            result.cutover_writer_term.lease_id,
        ))
        self.assertEqual(5, len(session.statements))
        rendered = [str(statement) for statement in session.statements]
        self.assertIn("pg_advisory_xact_lock", rendered[0])
        self.assertIn("object_delta_streams", rendered[1])
        self.assertIn("object_delta_source_cutovers", rendered[2])
        self.assertIn("object_delta_source_batch_ledger", rendered[3])
        self.assertIn("object_delta_outbox", rendered[4])
        self.assertTrue(all("FOR UPDATE" in statement for statement in rendered[1:]))
        self.assertEqual([], session.mutations)

    async def test_missing_stream_is_a_readiness_failure_after_only_stream_lock(self):
        session = _LockedSnapshotSession(stream=None)

        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "stream does not exist for the active runtime binding",
        ):
            await snapshot_locked_object_delta_source_publication(
                session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual(2, len(session.statements))
        self.assertEqual([], session.mutations)

    async def test_idle_stream_is_explicit_no_work_and_preserves_terminal_frontier(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=3),
            terminal=terminal_row(),
            outbox=(),
        )

        result = await snapshot_locked_object_delta_source_publication(
            session,
            binding(),
            max_items=4,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertTrue(result.no_work)
        self.assertEqual(2, result.terminal_ledger_entry.last_sequence)
        self.assertEqual("b" * 64, result.prior_chain_sha256)
        self.assertEqual(5, len(session.statements))

    async def test_gap_from_ledger_frontier_fails_closed(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=6),
            terminal=terminal_row(),
            outbox=(outbox_row(sequence=3), outbox_row(sequence=5)),
        )

        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "not contiguous from the ledger frontier",
        ):
            await snapshot_locked_object_delta_source_publication(
                session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual([], session.mutations)

    async def test_empty_or_short_suffix_cannot_hide_an_allocated_missing_sequence(self):
        empty = _LockedSnapshotSession(
            stream=source_stream(next_sequence=4),
            terminal=terminal_row(),
            outbox=(),
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "missing the next ledger sequence",
        ):
            await snapshot_locked_object_delta_source_publication(
                empty,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        short = _LockedSnapshotSession(
            stream=source_stream(next_sequence=5),
            terminal=terminal_row(),
            outbox=(outbox_row(sequence=3),),
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "missing a sequence before the allocator frontier",
        ):
            await snapshot_locked_object_delta_source_publication(
                short,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

    async def test_outbox_row_before_frontier_fails_closed(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=4),
            terminal=terminal_row(),
            # A real relational filter excludes this, but the adapter still
            # rejects a malformed/mocked locked result rather than treating it
            # as a harmless historical row.
            outbox=(outbox_row(sequence=2),),
        )

        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "precedes the ledger frontier",
        ):
            await snapshot_locked_object_delta_source_publication(
                session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

    async def test_mixed_or_foreign_writer_term_fails_closed(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=5),
            terminal=terminal_row(),
            outbox=(outbox_row(sequence=3), outbox_row(sequence=4, epoch=8, lease_id="lease-8")),
        )

        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "Writer Witness term does not match the published cutover",
        ):
            await snapshot_locked_object_delta_source_publication(
                session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

    async def test_terminal_term_and_cutover_registry_mismatch_fail_closed(self):
        term_session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=3),
            terminal=terminal_row(epoch=8, lease_id="lease-8"),
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "terminal ledger Writer Witness term",
        ):
            await snapshot_locked_object_delta_source_publication(
                term_session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        source = source_stream(next_sequence=3)
        registry_session = _LockedSnapshotSession(
            stream=source,
            cutover=published_cutover(source, registry_fingerprint="fedcba9876543210"),
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "registry fingerprint does not match",
        ):
            await snapshot_locked_object_delta_source_publication(
                registry_session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        state_session = _LockedSnapshotSession(
            stream=source,
            cutover=published_cutover(source, state="outbox_active_baseline_pending"),
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "does not match the source stream",
        ):
            await snapshot_locked_object_delta_source_publication(
                state_session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

    async def test_inactive_transaction_fails_before_sql(self):
        session = _LockedSnapshotSession(stream=source_stream(next_sequence=1), active=False)

        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "active caller-owned transaction",
        ):
            await snapshot_locked_object_delta_source_publication(
                session,
                binding(),
                max_items=4,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual([], session.statements)

    async def test_opaque_snapshot_provenance_accepts_only_minted_consistent_result(self):
        session = _LockedSnapshotSession(
            stream=source_stream(next_sequence=4),
            terminal=terminal_row(),
            outbox=(outbox_row(sequence=3),),
        )
        value = await snapshot_locked_object_delta_source_publication(
            session,
            binding(),
            max_items=4,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertIs(value, require_locked_object_delta_source_publication_snapshot(value))
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "was not minted",
        ):
            require_locked_object_delta_source_publication_snapshot(replace(value))
        forged = ObjectDeltaLockedSourcePublicationSnapshot(
            binding=value.binding,
            stream=value.stream,
            source_stream_id=value.source_stream_id,
            cutover_writer_term=value.cutover_writer_term,
            terminal_ledger_entry=value.terminal_ledger_entry,
            prior_chain_sha256=value.prior_chain_sha256,
            prepared_payload=value.prepared_payload,
        )
        with self.assertRaisesRegex(
            ObjectDeltaLockedSourcePublicationSnapshotError,
            "was not minted",
        ):
            require_locked_object_delta_source_publication_snapshot(forged)


class ObjectDeltaLockedSourcePublicationSnapshotStaticTests(unittest.TestCase):
    def test_snapshot_has_no_storage_crypto_runtime_or_mutation_capability(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "object_delta_source_publication_snapshot.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        forbidden = {
            "age",
            "aiohttp",
            "boto3",
            "botocore",
            "http",
            "httpx",
            "os",
            "pathlib",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(
            [
                value
                for value in imports
                if value in forbidden or value.startswith(("boto.", "urllib."))
            ]
        )
        banned_calls = {"add", "begin", "commit", "delete", "flush", "rollback", "put_object"}
        self.assertFalse(
            [
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in banned_calls
            ]
        )


if __name__ == "__main__":
    unittest.main()
