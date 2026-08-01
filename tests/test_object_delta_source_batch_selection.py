from __future__ import annotations

import ast
from pathlib import Path
import unittest
from uuid import UUID

from core.object_delta_outbox_allocator import canonical_sync_item_sha256
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_selection import (
    ObjectDeltaSourceBatchSelectionError,
    select_object_delta_source_batch,
)
from models.object_delta import ObjectDeltaOutboxEntry, ObjectDeltaSourceCutover, ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger
from core.sync_metadata import build_sync_metadata
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)


CAMPAIGN = "wa-ir-source-selection-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-selection-20260731"
FINGERPRINT = "0123456789abcdef"
MAXIMUM_PAYLOAD_BYTES = 1024 * 1024


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ScalarRows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _RowsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _ScalarRows(self.values)


_DEFAULT_CUTOVER = object()


class _ReadOnlySession:
    def __init__(self, *, stream, terminal=None, outbox=(), cutover=_DEFAULT_CUTOVER, active=True):
        self.stream = stream
        self.terminal = terminal
        self.outbox = tuple(outbox)
        self.cutover = (
            published_cutover(stream) if cutover is _DEFAULT_CUTOVER and stream is not None else cutover
        )
        self.active = active
        self.statements = []
        self.mutation_calls = []

    def in_transaction(self):
        return self.active

    async def execute(self, statement):
        self.statements.append(statement)
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
            limit_clause = getattr(statement, "_limit_clause", None)
            limit = getattr(limit_clause, "value", None)
            if type(limit) is not int:
                raise AssertionError("outbox read must use a concrete query limit")
            return _RowsResult(self.outbox[:limit])
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.mutation_calls.append("add")
        raise AssertionError("selection adapter must not add rows")

    async def flush(self):
        self.mutation_calls.append("flush")
        raise AssertionError("selection adapter must not flush")

    async def begin(self):
        self.mutation_calls.append("begin")
        raise AssertionError("selection adapter must not begin transactions")

    async def commit(self):
        self.mutation_calls.append("commit")
        raise AssertionError("selection adapter must not commit")

    async def rollback(self):
        self.mutation_calls.append("rollback")
        raise AssertionError("selection adapter must not roll back")

    async def delete(self, value):
        self.mutation_calls.append("delete")
        raise AssertionError("selection adapter must not delete rows")


def binding() -> ObjectDeltaSourceRuntimeBinding:
    return ObjectDeltaSourceRuntimeBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        expected_registry_fingerprint=FINGERPRINT,
    )


def stream(*, next_sequence: int) -> ObjectDeltaStream:
    return ObjectDeltaStream(
        id=701,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        next_sequence=next_sequence,
    )


def published_cutover(
    source_stream: ObjectDeltaStream,
    **overrides: object,
) -> ObjectDeltaSourceCutover:
    values: dict[str, object] = {
        "id": 801,
        "stream_id": source_stream.id,
        "source_site": source_stream.source_site,
        "destination_site": source_stream.destination_site,
        "campaign_id": source_stream.campaign_id,
        "release_sha": source_stream.release_sha,
        "stream_generation_id": source_stream.stream_generation_id,
        "write_gate_id": UUID("11111111-2222-3333-4444-555555555555"),
        "registry_fingerprint": FINGERPRINT,
        "writer_epoch": 7,
        "writer_lease_id": "lease-7",
        "source_generation": "fi-generation-20260731",
        "snapshot_id": "20260731T000000Z-aaaaaaaaaaaaaaaa",
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


def terminal_row(*, last_sequence: int = 2, stream_id: int = 701) -> ObjectDeltaSourceBatchLedger:
    return ObjectDeltaSourceBatchLedger(
        id=801,
        stream_id=stream_id,
        first_sequence=1,
        last_sequence=last_sequence,
        writer_epoch=7,
        writer_lease_id="lease-7",
        prior_chain_sha256="0" * 64,
        batch_sha256="b" * 64,
        payload_sha256="c" * 64,
        payload_bytes=512,
        object_key="campaigns/three-site/terminal.age",
        object_version_id="version-terminal",
        ciphertext_sha256="d" * 64,
        ciphertext_bytes=1024,
    )


class ObjectDeltaSourceBatchSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_genesis_selection_uses_plain_reads_and_assembles_payload(self):
        session = _ReadOnlySession(
            stream=stream(next_sequence=3),
            outbox=(outbox_row(sequence=1), outbox_row(sequence=2)),
        )

        result = await select_object_delta_source_batch(
            session,
            binding(),
            max_items=4,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertFalse(result.no_work)
        self.assertIsNone(result.terminal_ledger_entry)
        self.assertEqual((1, 2), result.prepared_payload.sequence_ids)
        self.assertEqual((7, "lease-7"), (
            result.prepared_payload.writer_term.epoch,
            result.prepared_payload.writer_term.lease_id,
        ))
        self.assertEqual(4, len(session.statements))
        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("object_delta_source_cutovers", statements)
        self.assertIn("ORDER BY object_delta_source_batch_ledger.last_sequence DESC", statements)
        self.assertIn("ORDER BY object_delta_outbox.logical_sequence ASC", statements)
        self.assertIn("LIMIT", statements)
        self.assertNotIn("FOR UPDATE", statements)
        self.assertNotIn("pg_advisory", statements)
        self.assertEqual([], session.mutation_calls)

    async def test_terminal_selection_starts_at_terminal_plus_one_and_maps_terminal(self):
        terminal = terminal_row(last_sequence=2)
        session = _ReadOnlySession(
            stream=stream(next_sequence=5),
            terminal=terminal,
            outbox=(outbox_row(sequence=3), outbox_row(sequence=4)),
        )

        result = await select_object_delta_source_batch(
            session,
            binding(),
            max_items=4,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertEqual(terminal.last_sequence, result.terminal_ledger_entry.last_sequence)
        self.assertEqual((3, 4), result.prepared_payload.sequence_ids)
        self.assertEqual([], session.mutation_calls)

    async def test_missing_stream_and_true_empty_frontier_are_no_work(self):
        missing_session = _ReadOnlySession(stream=None)

        missing = await select_object_delta_source_batch(
            missing_session,
            binding(),
            max_items=2,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertTrue(missing.no_work)
        self.assertEqual(1, len(missing_session.statements))
        empty_session = _ReadOnlySession(
            stream=stream(next_sequence=3),
            terminal=terminal_row(last_sequence=2),
        )
        empty = await select_object_delta_source_batch(
            empty_session,
            binding(),
            max_items=2,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )
        self.assertTrue(empty.no_work)
        self.assertEqual(4, len(empty_session.statements))
        self.assertEqual([], empty_session.mutation_calls)

    async def test_existing_stream_requires_complete_matching_published_cutover_before_reading_ledger_or_outbox(self):
        source_stream = stream(next_sequence=2)
        cases = (
            ("missing", None, "has no durable source cutover"),
            (
                "pending",
                published_cutover(source_stream, state="outbox_active_baseline_pending"),
                "not baseline published",
            ),
            (
                "wrong-registry",
                published_cutover(source_stream, registry_fingerprint="f" * 16),
                "registry fingerprint",
            ),
            (
                "missing-baseline-receipt",
                published_cutover(source_stream, baseline_manifest_object_version_id=None),
                "baseline manifest version",
            ),
            (
                "wrong-identity",
                published_cutover(source_stream, campaign_id="wa-ir-source-selection-wrong-20260731"),
                "does not match the runtime binding",
            ),
        )
        for label, cutover, error in cases:
            with self.subTest(label=label):
                session = _ReadOnlySession(
                    stream=source_stream,
                    outbox=(outbox_row(sequence=1),),
                    cutover=cutover,
                )
                with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, error):
                    await select_object_delta_source_batch(
                        session,
                        binding(),
                        max_items=1,
                        maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
                    )
                statements = "\n".join(str(statement) for statement in session.statements)
                self.assertIn("object_delta_streams", statements)
                self.assertIn("object_delta_source_cutovers", statements)
                self.assertNotIn("object_delta_source_batch_ledger", statements)
                self.assertNotIn("object_delta_outbox", statements)
                self.assertEqual([], session.mutation_calls)

    async def test_selected_payload_writer_term_must_match_published_cutover(self):
        source_stream = stream(next_sequence=2)
        session = _ReadOnlySession(
            stream=source_stream,
            outbox=(outbox_row(sequence=1, epoch=8, lease_id="lease-8"),),
            cutover=published_cutover(source_stream, writer_epoch=7, writer_lease_id="lease-7"),
        )

        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "selected source outbox Writer Witness term"):
            await select_object_delta_source_batch(
                session,
                binding(),
                max_items=1,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("object_delta_source_cutovers", statements)
        self.assertIn("object_delta_source_batch_ledger", statements)
        self.assertIn("object_delta_outbox", statements)
        self.assertEqual([], session.mutation_calls)

    async def test_empty_query_cannot_hide_an_allocated_missing_sequence(self):
        session = _ReadOnlySession(
            stream=stream(next_sequence=4),
            terminal=terminal_row(last_sequence=2),
        )

        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "missing the next ledger"):
            await select_object_delta_source_batch(
                session,
                binding(),
                max_items=2,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual([], session.mutation_calls)

    async def test_sequence_gap_and_foreign_terminal_fail_closed(self):
        gap_session = _ReadOnlySession(
            stream=stream(next_sequence=5),
            terminal=terminal_row(last_sequence=2),
            outbox=(outbox_row(sequence=4),),
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "not contiguous"):
            await select_object_delta_source_batch(
                gap_session,
                binding(),
                max_items=3,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        foreign_terminal_session = _ReadOnlySession(
            stream=stream(next_sequence=3),
            terminal=terminal_row(last_sequence=2, stream_id=702),
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "different source stream"):
            await select_object_delta_source_batch(
                foreign_terminal_session,
                binding(),
                max_items=3,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual([], gap_session.mutation_calls)
        self.assertEqual([], foreign_terminal_session.mutation_calls)

    async def test_term_boundary_truncates_only_after_its_contiguous_first_row(self):
        session = _ReadOnlySession(
            stream=stream(next_sequence=4),
            outbox=(
                outbox_row(sequence=1, epoch=7, lease_id="lease-7"),
                outbox_row(sequence=2, epoch=8, lease_id="lease-8"),
                outbox_row(sequence=3, epoch=8, lease_id="lease-8"),
            ),
        )

        result = await select_object_delta_source_batch(
            session,
            binding(),
            max_items=3,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertEqual((1,), result.prepared_payload.sequence_ids)
        self.assertEqual(7, result.prepared_payload.writer_term.epoch)
        self.assertEqual([], session.mutation_calls)

    async def test_term_boundary_cannot_hide_a_gap_and_invalid_outbox_digest_fails_closed(self):
        boundary_gap_session = _ReadOnlySession(
            stream=stream(next_sequence=4),
            outbox=(
                outbox_row(sequence=1, epoch=7, lease_id="lease-7"),
                outbox_row(sequence=3, epoch=8, lease_id="lease-8"),
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "not contiguous"):
            await select_object_delta_source_batch(
                boundary_gap_session,
                binding(),
                max_items=3,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        invalid = outbox_row(sequence=1)
        invalid.sync_item_sha256 = "f" * 64
        invalid_row_session = _ReadOnlySession(
            stream=stream(next_sequence=2),
            outbox=(invalid,),
        )
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "digest does not match"):
            await select_object_delta_source_batch(
                invalid_row_session,
                binding(),
                max_items=1,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )

        self.assertEqual([], boundary_gap_session.mutation_calls)
        self.assertEqual([], invalid_row_session.mutation_calls)

    async def test_max_count_is_query_bound_and_binding_mismatch_rejects(self):
        session = _ReadOnlySession(
            stream=stream(next_sequence=4),
            outbox=(outbox_row(sequence=1), outbox_row(sequence=2), outbox_row(sequence=3)),
        )

        result = await select_object_delta_source_batch(
            session,
            binding(),
            max_items=2,
            maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
        )

        self.assertEqual((1, 2), result.prepared_payload.sequence_ids)
        wrong_stream = stream(next_sequence=2)
        wrong_stream.campaign_id = "wa-ir-source-selection-mismatch"
        mismatch_session = _ReadOnlySession(stream=wrong_stream)
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "does not match"):
            await select_object_delta_source_batch(
                mismatch_session,
                binding(),
                max_items=2,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )
        self.assertEqual(1, len(mismatch_session.statements))
        self.assertEqual([], session.mutation_calls)
        self.assertEqual([], mismatch_session.mutation_calls)

    async def test_inactive_or_invalid_input_issues_no_sql(self):
        inactive = _ReadOnlySession(stream=stream(next_sequence=1), active=False)
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "active caller-owned"):
            await select_object_delta_source_batch(
                inactive,
                binding(),
                max_items=1,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )
        self.assertEqual([], inactive.statements)

        invalid_limit = _ReadOnlySession(stream=stream(next_sequence=1))
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "max item count"):
            await select_object_delta_source_batch(
                invalid_limit,
                binding(),
                max_items=0,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )
        self.assertEqual([], invalid_limit.statements)

        invalid_payload_limit = _ReadOnlySession(stream=stream(next_sequence=1))
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "maximum payload bytes"):
            await select_object_delta_source_batch(
                invalid_payload_limit,
                binding(),
                max_items=1,
                maximum_payload_bytes=0,
            )
        self.assertEqual([], invalid_payload_limit.statements)

        invalid_binding = _ReadOnlySession(stream=stream(next_sequence=1))
        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "source binding"):
            await select_object_delta_source_batch(
                invalid_binding,
                object(),
                max_items=1,
                maximum_payload_bytes=MAXIMUM_PAYLOAD_BYTES,
            )
        self.assertEqual([], invalid_binding.statements)

    async def test_plaintext_payload_limit_is_enforced_before_any_publisher_exists(self):
        session = _ReadOnlySession(
            stream=stream(next_sequence=2),
            outbox=(outbox_row(sequence=1),),
        )

        with self.assertRaisesRegex(ObjectDeltaSourceBatchSelectionError, "selected source outbox rows"):
            await select_object_delta_source_batch(
                session,
                binding(),
                max_items=1,
                maximum_payload_bytes=1,
            )

        self.assertEqual([], session.mutation_calls)


class ObjectDeltaSourceBatchSelectionStaticTests(unittest.TestCase):
    def test_adapter_has_no_external_or_mutating_imports_or_calls(self):
        path = Path(__file__).resolve().parents[1] / "core" / "object_delta_source_batch_selection.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        forbidden_import_modules = {
            "age",
            "aiohttp",
            "boto3",
            "botocore",
            "http",
            "httpx",
            "os",
            "pathlib",
            "requests",
            "shutil",
            "socket",
            "subprocess",
            "urllib",
            "core.object_delta_source_batch_publication",
        }
        self.assertFalse(
            [
                module
                for module in imported_modules
                if module in forbidden_import_modules or module.startswith(("boto.", "urllib."))
            ]
        )
        forbidden_calls = {
            "add",
            "begin",
            "commit",
            "rollback",
            "flush",
            "delete",
            "update",
            "insert",
            "with_for_update",
        }
        self.assertFalse(
            [
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
            ]
        )


if __name__ == "__main__":
    unittest.main()
