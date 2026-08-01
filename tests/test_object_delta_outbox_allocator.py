from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from models.change_log import ChangeLog
from models.object_delta import (
    ObjectDeltaOutboxEntry,
    ObjectDeltaSourceCutover,
    ObjectDeltaStream,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _AllocatorSession:
    """Small async-session double; it never opens a database connection."""

    def __init__(self, *, stream, change_log, cutover=None, outbox=None, active=True):
        self.stream = stream
        self.change_log = change_log
        self.cutover = cutover
        self.outbox = outbox
        self.active = active
        self.added = []
        self.statements = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

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
        if ChangeLog in entities:
            return _ScalarResult(self.change_log)
        if ObjectDeltaOutboxEntry in entities:
            return _ScalarResult(self.outbox)
        if "pg_advisory_xact_lock" in str(statement):
            return _ScalarResult(None)
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.added.append(value)
        if isinstance(value, ObjectDeltaStream):
            self.stream = value

    async def flush(self):
        self.flush_count += 1
        for value in self.added:
            if isinstance(value, ObjectDeltaStream) and value.id is None:
                value.id = 701
            if isinstance(value, ObjectDeltaOutboxEntry) and value.id is None:
                value.id = 801

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


class _MappingResult:
    """Small Core Connection result double for the synchronous allocator."""

    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


def _change_log_row(change_log):
    if change_log is None:
        return None
    return {
        "id": change_log.id,
        "operation": change_log.operation,
        "table_name": change_log.table_name,
        "record_id": change_log.record_id,
        "data": change_log.data,
        "timestamp": change_log.timestamp,
        "hash": change_log.hash,
    }


def _stream_row(stream):
    if stream is None:
        return None
    return {
        "id": stream.id,
        "source_site": stream.source_site,
        "destination_site": stream.destination_site,
        "campaign_id": stream.campaign_id,
        "release_sha": stream.release_sha,
        "stream_generation_id": stream.stream_generation_id,
        "next_sequence": stream.next_sequence,
    }


def _outbox_row(outbox):
    if outbox is None:
        return None
    return {
        "id": outbox.id,
        "stream_id": outbox.stream_id,
        "logical_sequence": outbox.logical_sequence,
        "change_log_id": outbox.change_log_id,
        "writer_epoch": outbox.writer_epoch,
        "writer_lease_id": outbox.writer_lease_id,
        "canonical_sync_item": outbox.canonical_sync_item,
        "sync_item_sha256": outbox.sync_item_sha256,
    }


def _cutover_row(cutover):
    if cutover is None:
        return None
    return {
        "id": cutover.id,
        "stream_id": cutover.stream_id,
        "source_site": cutover.source_site,
        "destination_site": cutover.destination_site,
        "campaign_id": cutover.campaign_id,
        "release_sha": cutover.release_sha,
        "stream_generation_id": cutover.stream_generation_id,
        "write_gate_id": cutover.write_gate_id,
        "registry_fingerprint": cutover.registry_fingerprint,
        "writer_epoch": cutover.writer_epoch,
        "writer_lease_id": cutover.writer_lease_id,
        "source_generation": cutover.source_generation,
        "snapshot_id": cutover.snapshot_id,
        "alembic_revision": cutover.alembic_revision,
        "snapshot_manifest_object_key": cutover.snapshot_manifest_object_key,
        "snapshot_manifest_object_version_id": cutover.snapshot_manifest_object_version_id,
        "snapshot_manifest_ciphertext_sha256": cutover.snapshot_manifest_ciphertext_sha256,
        "snapshot_manifest_ciphertext_bytes": cutover.snapshot_manifest_ciphertext_bytes,
        "baseline_manifest_object_key": cutover.baseline_manifest_object_key,
        "baseline_manifest_object_version_id": cutover.baseline_manifest_object_version_id,
        "baseline_manifest_ciphertext_sha256": cutover.baseline_manifest_ciphertext_sha256,
        "baseline_manifest_ciphertext_bytes": cutover.baseline_manifest_ciphertext_bytes,
        "database_sha256": cutover.database_sha256,
        "uploads_sha256": cutover.uploads_sha256,
        "state": cutover.state,
    }


class _AllocatorConnection:
    """Connection-level double; it rejects any implicit transaction ownership."""

    def __init__(self, *, stream, change_log, request, cutover=None, outbox=None, active=True):
        self.stream = stream
        self.change_log = change_log
        self.request = request
        self.cutover = cutover
        self.outbox = outbox
        self.active = active
        self.statements = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def in_transaction(self):
        return self.active

    def begin(self):
        self.begin_count += 1
        raise AssertionError("allocator must not begin a transaction")

    def commit(self):
        self.commit_count += 1
        raise AssertionError("allocator must not commit")

    def rollback(self):
        self.rollback_count += 1
        raise AssertionError("allocator must not roll back")

    def execute(self, statement):
        self.statements.append(statement)
        rendered = str(statement)
        normalized = rendered.lstrip().upper()
        if "pg_advisory_xact_lock" in rendered:
            return _MappingResult(None)
        if "FROM change_log" in rendered:
            return _MappingResult(_change_log_row(self.change_log))
        if "object_delta_source_cutovers" in rendered:
            if normalized.startswith("SELECT"):
                return _MappingResult(_cutover_row(self.cutover))
        if "object_delta_streams" in rendered:
            if normalized.startswith("SELECT"):
                return _MappingResult(_stream_row(self.stream))
            if normalized.startswith("UPDATE"):
                if self.stream is None:
                    return _MappingResult(None)
                self.stream.next_sequence += 1
                return _MappingResult(_stream_row(self.stream))
        if "object_delta_outbox" in rendered:
            if normalized.startswith("SELECT"):
                return _MappingResult(_outbox_row(self.outbox))
            if normalized.startswith("INSERT"):
                self.outbox = ObjectDeltaOutboxEntry(
                    id=801,
                    stream_id=self.stream.id,
                    logical_sequence=self.stream.next_sequence,
                    change_log_id=self.request.change_log_id,
                    writer_epoch=self.request.writer_epoch,
                    writer_lease_id=self.request.writer_lease_id,
                    canonical_sync_item=dict(self.request.canonical_sync_item),
                    sync_item_sha256="uninitialized",
                )
                # The adapter validates the canonical fingerprint before this
                # statement; derive it lazily to keep the double connection-only.
                from core.object_delta_outbox_allocator import canonical_sync_item_sha256

                self.outbox.sync_item_sha256 = canonical_sync_item_sha256(
                    self.request.canonical_sync_item
                )
                return _MappingResult(_outbox_row(self.outbox))
        raise AssertionError(f"unexpected statement: {rendered}")


def _change_log(*, change_log_id=41, record_id=101, item_hash="a" * 64):
    return ChangeLog(
        id=change_log_id,
        operation="UPDATE",
        table_name="users",
        record_id=record_id,
        data={"id": record_id, "full_name": f"User {record_id}"},
        timestamp=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        hash=item_hash,
    )


def _sync_protocol():
    from core.sync_protocol import (
        SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
        SYNC_PAYLOAD_SCHEMA_VERSION,
        SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
        SYNC_PROTOCOL_VERSION,
        SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
        SYNC_REGISTRY_VERSION,
    )

    return {
        "protocol_version": SYNC_PROTOCOL_VERSION,
        "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
        "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
        "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
        "registry_version": SYNC_REGISTRY_VERSION,
        "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
        "registry_fingerprint": "0123456789abcdef",
        "producer": {"server_mode": "foreign"},
    }


def _canonical_item(change_log):
    from core.sync_metadata import build_sync_metadata

    data = dict(change_log.data)
    return {
        "type": "db_change",
        "operation": change_log.operation,
        "table": change_log.table_name,
        "id": change_log.record_id,
        "data": data,
        "hash": change_log.hash,
        "timestamp": change_log.timestamp.timestamp(),
        "change_log_id": change_log.id,
        "sync_protocol": _sync_protocol(),
        "sync_meta": build_sync_metadata(
            change_log.table_name,
            change_log.record_id,
            change_log.operation,
            data,
            change_log_id=change_log.id,
            source_server="foreign",
        ),
    }


def _request(change_log, **overrides):
    from core.object_delta_outbox_allocator import ObjectDeltaOutboxRequest

    options = {
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": "wa-ir-append-delta-20260730",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        # This represents the trusted, release-bound control-plane value, not
        # a value learned from the untrusted sync item.
        "expected_registry_fingerprint": "0123456789abcdef",
        "stream_generation_id": "fi-ir-stream-20260730-a",
        "writer_epoch": 9,
        "writer_lease_id": "lease-9",
        "change_log_id": change_log.id,
        "canonical_sync_item": _canonical_item(change_log),
    }
    options.update(overrides)
    return ObjectDeltaOutboxRequest(**options)


def _published_cutover(stream, request, **overrides):
    options = {
        "id": 901,
        "stream_id": stream.id,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "stream_generation_id": request.stream_generation_id,
        "write_gate_id": uuid4(),
        "registry_fingerprint": request.expected_registry_fingerprint,
        "writer_epoch": request.writer_epoch,
        "writer_lease_id": request.writer_lease_id,
        "source_generation": "fi-generation-20260731-a",
        "snapshot_id": "20260731T043000Z-0123456789abcdef",
        "alembic_revision": "0deltacutover01",
        "snapshot_manifest_object_key": "campaigns/three-site/snapshot-manifest.age",
        "snapshot_manifest_object_version_id": "snapshot-version-20260731-a",
        "snapshot_manifest_ciphertext_sha256": "b" * 64,
        "snapshot_manifest_ciphertext_bytes": 1024,
        "baseline_manifest_object_key": "campaigns/three-site/baseline-manifest.age",
        "baseline_manifest_object_version_id": "baseline-version-20260731-a",
        "baseline_manifest_ciphertext_sha256": "c" * 64,
        "baseline_manifest_ciphertext_bytes": 1024,
        "database_sha256": "d" * 64,
        "uploads_sha256": "e" * 64,
        "state": "baseline_published",
    }
    options.update(overrides)
    return ObjectDeltaSourceCutover(**options)


def _stream(request, *, stream_id=701, next_sequence=1):
    return ObjectDeltaStream(
        id=stream_id,
        source_site=request.source_site,
        destination_site=request.destination_site,
        campaign_id=request.campaign_id,
        release_sha=request.release_sha,
        stream_generation_id=request.stream_generation_id,
        next_sequence=next_sequence,
    )


def _assert_strict_allocator_lock_order(test_case, statements):
    """Assert the deadlock-safe read/lock sequence before any outbox access."""

    rendered = [str(statement) for statement in statements]
    change_log_index = next(index for index, value in enumerate(rendered) if "change_log" in value)
    advisory_index = next(
        index for index, value in enumerate(rendered) if "pg_advisory_xact_lock" in value
    )
    stream_index = next(
        index for index, value in enumerate(rendered) if "object_delta_streams" in value
    )
    cutover_index = next(
        index for index, value in enumerate(rendered) if "object_delta_source_cutovers" in value
    )
    outbox_index = next(
        index for index, value in enumerate(rendered) if "object_delta_outbox" in value
    )
    test_case.assertIn("FOR UPDATE", rendered[change_log_index])
    test_case.assertIn("FOR UPDATE", rendered[stream_index])
    test_case.assertIn("FOR UPDATE", rendered[cutover_index])
    test_case.assertLess(change_log_index, advisory_index)
    test_case.assertLess(advisory_index, stream_index)
    test_case.assertLess(stream_index, cutover_index)
    test_case.assertLess(cutover_index, outbox_index)


class ObjectDeltaOutboxAllocatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_allocation_uses_a_precreated_published_stream_and_reserves_its_next_sequence(self):
        from core.object_delta_outbox_allocator import (
            ALLOCATION_ACTION_ALLOCATED,
            allocate_object_delta_outbox_entry,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request)
        session = _AllocatorSession(
            stream=stream,
            change_log=change_log,
            cutover=_published_cutover(stream, request),
        )

        allocation = await allocate_object_delta_outbox_entry(session, request)

        self.assertEqual(ALLOCATION_ACTION_ALLOCATED, allocation.action)
        self.assertEqual(1, allocation.logical_sequence)
        self.assertEqual(2, allocation.stream.next_sequence)
        self.assertEqual(701, allocation.stream.id)
        self.assertEqual(801, allocation.outbox_entry.id)
        self.assertEqual(1, allocation.outbox_entry.logical_sequence)
        self.assertEqual(change_log.id, allocation.outbox_entry.change_log_id)
        self.assertEqual(
            canonical_sync_item_sha256(request.canonical_sync_item),
            allocation.outbox_entry.sync_item_sha256,
        )
        self.assertEqual([ObjectDeltaOutboxEntry], [type(value) for value in session.added])
        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("object_delta_source_cutovers", statements)
        self.assertGreaterEqual(statements.count("FOR UPDATE"), 4)
        _assert_strict_allocator_lock_order(self, session.statements)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_allocator_refuses_lazy_stream_creation_before_any_outbox_mutation(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        request = _request(change_log)
        session = _AllocatorSession(stream=None, change_log=change_log)

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "pre-created"):
            await allocate_object_delta_outbox_entry(session, request)

        statements = "\n".join(str(statement) for statement in session.statements)
        self.assertIn("object_delta_streams", statements)
        self.assertNotIn("object_delta_source_cutovers", statements)
        self.assertNotIn("object_delta_outbox", statements)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_allocator_rejects_pending_or_incomplete_published_cutover_before_outbox_mutation(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request)
        cases = (
            (
                "pending",
                {"state": "outbox_active_baseline_pending"},
                "not baseline published",
            ),
            (
                "incomplete-evidence",
                {"baseline_manifest_object_key": None},
                "baseline manifest key",
            ),
        )
        for label, overrides, error in cases:
            with self.subTest(label=label):
                session = _AllocatorSession(
                    stream=stream,
                    change_log=change_log,
                    cutover=_published_cutover(stream, request, **overrides),
                )
                with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, error):
                    await allocate_object_delta_outbox_entry(session, request)

                statements = "\n".join(str(statement) for statement in session.statements)
                self.assertIn("object_delta_source_cutovers", statements)
                self.assertNotIn("object_delta_outbox", statements)
                self.assertEqual([], session.added)

    async def test_allocator_requires_locked_cutover_registry_and_writer_term_to_match_the_request(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request)
        cases = (
            ("registry", {"registry_fingerprint": "f" * 16}, "registry fingerprint"),
            ("epoch", {"writer_epoch": request.writer_epoch + 1}, "Writer Witness term"),
            ("lease", {"writer_lease_id": "lease-10"}, "Writer Witness term"),
        )
        for label, overrides, error in cases:
            with self.subTest(label=label):
                session = _AllocatorSession(
                    stream=stream,
                    change_log=change_log,
                    cutover=_published_cutover(stream, request, **overrides),
                )
                with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, error):
                    await allocate_object_delta_outbox_entry(session, request)

                self.assertEqual([], session.added)

    async def test_exact_retry_replays_existing_entry_without_consuming_a_new_sequence(self):
        from core.object_delta_outbox_allocator import (
            ALLOCATION_ACTION_REPLAY,
            allocate_object_delta_outbox_entry,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = ObjectDeltaStream(
            id=77,
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
            next_sequence=12,
        )
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        session = _AllocatorSession(
            stream=stream,
            change_log=change_log,
            cutover=_published_cutover(stream, request),
            outbox=existing,
        )

        allocation = await allocate_object_delta_outbox_entry(session, request)

        self.assertEqual(ALLOCATION_ACTION_REPLAY, allocation.action)
        self.assertIs(existing, allocation.outbox_entry)
        self.assertEqual(11, allocation.logical_sequence)
        self.assertEqual(12, stream.next_sequence)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.commit_count)

    async def test_replay_still_requires_a_preexisting_published_cutover(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request, next_sequence=12)
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        cases = (
            ("missing", None, "no durable source cutover"),
            (
                "pending",
                _published_cutover(
                    stream,
                    request,
                    state="outbox_active_baseline_pending",
                ),
                "not baseline published",
            ),
        )
        for label, cutover, error in cases:
            with self.subTest(label=label):
                session = _AllocatorSession(
                    stream=stream,
                    change_log=change_log,
                    cutover=cutover,
                    outbox=existing,
                )

                with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, error):
                    await allocate_object_delta_outbox_entry(session, request)

                self.assertFalse(
                    any("object_delta_outbox" in str(statement) for statement in session.statements)
                )
                self.assertEqual(12, stream.next_sequence)
                self.assertEqual([], session.added)

    async def test_conflicting_retry_fails_without_advancing_the_stream(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = ObjectDeltaStream(
            id=77,
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
            next_sequence=12,
        )
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        existing.writer_epoch = 10
        session = _AllocatorSession(
            stream=stream,
            change_log=change_log,
            cutover=_published_cutover(stream, request),
            outbox=existing,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "conflicts"):
            await allocate_object_delta_outbox_entry(session, request)

        self.assertEqual(12, stream.next_sequence)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.commit_count)

    async def test_retry_rejects_a_corrupt_counter_that_would_overlap_its_existing_sequence(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = ObjectDeltaStream(
            id=77,
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
            next_sequence=11,
        )
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        session = _AllocatorSession(
            stream=stream,
            change_log=change_log,
            cutover=_published_cutover(stream, request),
            outbox=existing,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "conflicts"):
            await allocate_object_delta_outbox_entry(session, request)

        self.assertEqual(11, stream.next_sequence)
        self.assertEqual([], session.added)

    async def test_change_log_evidence_must_match_the_canonical_item_before_mutation(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        bad_item = _canonical_item(change_log)
        bad_item["hash"] = "b" * 64
        session = _AllocatorSession(stream=None, change_log=change_log)

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "does not match"):
            await allocate_object_delta_outbox_entry(
                session,
                _request(change_log, canonical_sync_item=bad_item),
            )

        self.assertEqual([], session.added)

    async def test_change_log_evidence_rejects_json_numeric_type_drift_before_mutation(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        bad_item = _canonical_item(change_log)
        # ``int == float`` in Python, but the two values produce different
        # canonical JSON and therefore different durable retry hashes.
        bad_item["timestamp"] = int(bad_item["timestamp"])
        self.assertEqual(change_log.timestamp.timestamp(), bad_item["timestamp"])
        session = _AllocatorSession(stream=None, change_log=change_log)

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "does not match"):
            await allocate_object_delta_outbox_entry(
                session,
                _request(change_log, canonical_sync_item=bad_item),
            )

        self.assertEqual([], session.added)

    async def test_allocator_requires_a_valid_trusted_release_registry_binding_before_database_work(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        session = _AllocatorSession(stream=None, change_log=change_log)

        with self.assertRaisesRegex(
            ObjectDeltaOutboxAllocationError,
            "expected object-delta registry fingerprint is invalid",
        ):
            await allocate_object_delta_outbox_entry(
                session,
                _request(change_log, expected_registry_fingerprint="not-a-registry-fingerprint"),
            )

        self.assertEqual([], session.statements)
        self.assertEqual([], session.added)

    async def test_allocator_rejects_item_registry_fingerprint_that_disagrees_with_trusted_release_binding(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        session = _AllocatorSession(stream=None, change_log=change_log)

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "canonical sync item is invalid"):
            await allocate_object_delta_outbox_entry(
                session,
                _request(change_log, expected_registry_fingerprint="f" * 16),
            )

        self.assertEqual([], session.statements)
        self.assertEqual([], session.added)

    async def test_allocator_requires_a_caller_owned_transaction_and_never_starts_or_commits_one(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry,
        )

        change_log = _change_log()
        session = _AllocatorSession(stream=None, change_log=change_log, active=False)

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "active caller-owned transaction"):
            await allocate_object_delta_outbox_entry(session, _request(change_log))

        self.assertEqual([], session.statements)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)


class ObjectDeltaOutboxConnectionAllocatorTests(unittest.TestCase):
    def test_sync_connection_allocation_locks_and_reserves_in_the_outer_transaction(self):
        from core.object_delta_outbox_allocator import (
            ALLOCATION_ACTION_ALLOCATED,
            allocate_object_delta_outbox_entry_sync,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request)
        connection = _AllocatorConnection(
            stream=stream,
            change_log=change_log,
            request=request,
            cutover=_published_cutover(stream, request),
        )

        allocation = allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertEqual(ALLOCATION_ACTION_ALLOCATED, allocation.action)
        self.assertEqual(1, allocation.logical_sequence)
        self.assertEqual(701, allocation.stream.id)
        self.assertEqual(2, allocation.stream.next_sequence)
        self.assertEqual(request.source_site, allocation.stream.source_site)
        self.assertEqual(request.destination_site, allocation.stream.destination_site)
        self.assertEqual(801, allocation.outbox_entry.id)
        self.assertEqual(change_log.id, allocation.outbox_entry.change_log_id)
        self.assertEqual(request.writer_epoch, allocation.outbox_entry.writer_epoch)
        self.assertEqual(request.writer_lease_id, allocation.outbox_entry.writer_lease_id)
        self.assertEqual(
            canonical_sync_item_sha256(request.canonical_sync_item),
            allocation.outbox_entry.sync_item_sha256,
        )
        statements = "\n".join(str(statement) for statement in connection.statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("object_delta_source_cutovers", statements)
        self.assertGreaterEqual(statements.count("FOR UPDATE"), 4)
        _assert_strict_allocator_lock_order(self, connection.statements)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)

    def test_sync_connection_refuses_lazy_stream_creation_before_any_outbox_mutation(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry_sync,
        )

        change_log = _change_log()
        request = _request(change_log)
        connection = _AllocatorConnection(
            stream=None,
            change_log=change_log,
            request=request,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "pre-created"):
            allocate_object_delta_outbox_entry_sync(connection, request)

        statements = "\n".join(str(statement) for statement in connection.statements)
        self.assertIn("object_delta_streams", statements)
        self.assertNotIn("object_delta_source_cutovers", statements)
        self.assertNotIn("object_delta_outbox", statements)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)

    def test_sync_connection_reuses_validation_before_it_issues_sql(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry_sync,
        )

        change_log = _change_log()
        request = _request(change_log, expected_registry_fingerprint="not-a-registry-fingerprint")
        connection = _AllocatorConnection(
            stream=None,
            change_log=change_log,
            request=request,
        )

        with self.assertRaisesRegex(
            ObjectDeltaOutboxAllocationError,
            "expected object-delta registry fingerprint is invalid",
        ):
            allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertEqual([], connection.statements)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)

    def test_sync_connection_exact_retry_reuses_the_existing_logical_sequence(self):
        from core.object_delta_outbox_allocator import (
            ALLOCATION_ACTION_REPLAY,
            allocate_object_delta_outbox_entry_sync,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = ObjectDeltaStream(
            id=77,
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
            next_sequence=12,
        )
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        connection = _AllocatorConnection(
            stream=stream,
            change_log=change_log,
            request=request,
            cutover=_published_cutover(stream, request),
            outbox=existing,
        )

        allocation = allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertEqual(ALLOCATION_ACTION_REPLAY, allocation.action)
        self.assertEqual(11, allocation.logical_sequence)
        self.assertEqual(12, allocation.stream.next_sequence)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)
        self.assertFalse(any(str(statement).lstrip().upper().startswith("INSERT") for statement in connection.statements))

    def test_sync_connection_replay_still_requires_a_preexisting_published_cutover(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry_sync,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = _stream(request, next_sequence=12)
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        connection = _AllocatorConnection(
            stream=stream,
            change_log=change_log,
            request=request,
            cutover=None,
            outbox=existing,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "no durable source cutover"):
            allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertFalse(
            any("object_delta_outbox" in str(statement) for statement in connection.statements)
        )
        self.assertEqual(12, stream.next_sequence)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)

    def test_sync_connection_conflicting_retry_fails_without_advancing_the_stream(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry_sync,
            canonical_sync_item_sha256,
        )

        change_log = _change_log()
        request = _request(change_log)
        stream = ObjectDeltaStream(
            id=77,
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
            next_sequence=12,
        )
        existing = ObjectDeltaOutboxEntry(
            id=88,
            stream_id=stream.id,
            logical_sequence=11,
            change_log_id=change_log.id,
            writer_epoch=request.writer_epoch + 1,
            writer_lease_id=request.writer_lease_id,
            canonical_sync_item=_canonical_item(change_log),
            sync_item_sha256=canonical_sync_item_sha256(request.canonical_sync_item),
        )
        connection = _AllocatorConnection(
            stream=stream,
            change_log=change_log,
            request=request,
            cutover=_published_cutover(stream, request),
            outbox=existing,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "conflicts"):
            allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertEqual(12, stream.next_sequence)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)

    def test_sync_connection_requires_an_existing_outer_transaction_before_sql(self):
        from core.object_delta_outbox_allocator import (
            ObjectDeltaOutboxAllocationError,
            allocate_object_delta_outbox_entry_sync,
        )

        change_log = _change_log()
        request = _request(change_log)
        connection = _AllocatorConnection(
            stream=None,
            change_log=change_log,
            request=request,
            active=False,
        )

        with self.assertRaisesRegex(ObjectDeltaOutboxAllocationError, "active caller-owned transaction"):
            allocate_object_delta_outbox_entry_sync(connection, request)

        self.assertEqual([], connection.statements)
        self.assertEqual(0, connection.begin_count)
        self.assertEqual(0, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)


if __name__ == "__main__":
    unittest.main()
