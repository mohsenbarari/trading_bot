from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import ast
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from core.object_delta_source_batch_ledger import SourceBatchLedgerEntry, SourceStreamIdentity
from core.object_delta_source_batch_publication import PreparedObjectDeltaSourceBatch
from core.object_delta_source_ledger_persistence import (
    ObjectDeltaSourceLedgerPersistenceError,
    ObjectDeltaSourceLedgerPersistenceResult,
)
from core.object_delta_source_publication_attempt import (
    SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD,
    SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY,
    SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE,
    SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL,
    SOURCE_PUBLICATION_LEDGER_ACTION_APPEND,
    SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD,
    SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY,
    ObjectDeltaSourcePublicationAttempt,
    ObjectDeltaSourcePublicationAttestationArtifact,
    ObjectDeltaSourcePublicationCiphertextSpool,
    ObjectDeltaSourcePublicationExactReceipt,
    ObjectDeltaSourcePublicationIntent,
    build_object_delta_source_publication_attempt,
    derive_object_delta_source_transport_policy_sha256,
)
from core.object_delta_source_publication_attempt_persistence import (
    ObjectDeltaSourcePublicationAttemptPersistenceError,
    REQUIRED_OBJECT_DELTA_SOURCE_PREUPLOAD_RESERVATION_AUTHORIZATION,
    _AuthorizedSourcePublicationFacts,
    _legacy_test_only_record_authorized_object_delta_source_publication_attestation as record_authorized_object_delta_source_publication_attestation,
    _legacy_test_only_record_object_delta_source_publication_exact_receipt as _record_object_delta_source_publication_exact_receipt,
    _legacy_test_only_reserve_object_delta_source_publication_attempt as _reserve_object_delta_source_publication_attempt,
    _legacy_test_only_seal_object_delta_source_publication_attempt as _seal_object_delta_source_publication_attempt,
    _legacy_test_only_bind_authorized_object_delta_source_publication_ledger as bind_authorized_object_delta_source_publication_ledger,
    bind_authorized_object_delta_source_publication_ledger as disabled_bind_authorized_object_delta_source_publication_ledger,
    record_authorized_object_delta_source_publication_attestation as disabled_record_authorized_object_delta_source_publication_attestation,
    source_publication_attempt_advisory_lock_keys,
)
from core.legacy_source_publication_fence import (
    LegacyObjectDeltaSourcePublicationDisabledError,
)
from core.object_delta_transport_binding import ObjectDeltaTransportPolicy
from models.object_delta import ObjectDeltaSourceCutover, ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger
from models.object_delta_source_publication_attempt import (
    ObjectDeltaSourcePublicationAttempt as PublicationAttemptRow,
    ObjectDeltaSourcePublicationAttestation as PublicationAttestationRow,
    ObjectDeltaSourcePublicationLedgerBinding as PublicationLedgerBindingRow,
    ObjectDeltaSourcePublicationReceipt as PublicationReceiptRow,
    ObjectDeltaSourcePublicationSeal as PublicationSealRow,
)


CAMPAIGN = "wa-ir-source-attempt-persist-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-attempt-persist-20260731"
OBJECT_KEY = (
    "campaigns/three-site/object-delta/v1/wa-ir-source-attempt-persist-20260731/"
    "00000000000000000001-00000000000000000002-" + "a" * 64 + ".age"
)
RECIPIENT = "age1" + "a" * 30


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "c" * 30,
        webapp_ir_age_recipient=RECIPIENT,
    )


def stream_identity() -> SourceStreamIdentity:
    return SourceStreamIdentity(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
    )


def intent(**overrides: object) -> ObjectDeltaSourcePublicationIntent:
    values: dict[str, object] = {
        "stream": stream_identity(),
        "writer_epoch": 7,
        "writer_lease_id": "lease-7",
        "first_sequence": 1,
        "last_sequence": 2,
        "prior_chain_sha256": "0" * 64,
        "payload_sha256": "a" * 64,
        "payload_bytes": 512,
        "object_key": OBJECT_KEY,
        "destination_age_recipient": RECIPIENT,
        "transport_policy_sha256": derive_object_delta_source_transport_policy_sha256(policy()),
        "source_cutover_artifact_sha256": "d" * 64,
        "source_cutover_artifact_bytes": 2048,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationIntent(**values)


def attempt(**overrides: object) -> ObjectDeltaSourcePublicationAttempt:
    return build_object_delta_source_publication_attempt(intent(**overrides))


def ciphertext(value: ObjectDeltaSourcePublicationAttempt, **overrides: object) -> ObjectDeltaSourcePublicationCiphertextSpool:
    values: dict[str, object] = {
        "attempt_id": value.attempt_id,
        "ciphertext_sha256": "b" * 64,
        "ciphertext_bytes": 768,
        "spool_sha256": "b" * 64,
        "spool_bytes": 768,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationCiphertextSpool(**values)


def receipt(value: ObjectDeltaSourcePublicationAttempt, **overrides: object) -> ObjectDeltaSourcePublicationExactReceipt:
    values: dict[str, object] = {
        "attempt_id": value.attempt_id,
        "object_key": value.intent.object_key,
        "object_version_id": "version-20260731-01",
        "ciphertext_sha256": "b" * 64,
        "ciphertext_bytes": 768,
        "transport_receipt_artifact_sha256": "c" * 64,
        "transport_receipt_artifact_bytes": 512,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationExactReceipt(**values)


def source_stream() -> ObjectDeltaStream:
    return ObjectDeltaStream(
        id=701,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        next_sequence=3,
    )


def reservation_row(value: ObjectDeltaSourcePublicationAttempt) -> PublicationAttemptRow:
    source_intent = value.intent
    return PublicationAttemptRow(
        id=801,
        attempt_id=value.attempt_id,
        stream_id=701,
        source_site=source_intent.stream.source_site,
        destination_site=source_intent.stream.destination_site,
        campaign_id=source_intent.stream.campaign_id,
        release_sha=source_intent.stream.release_sha,
        stream_generation_id=source_intent.stream.stream_generation_id,
        writer_epoch=source_intent.writer_epoch,
        writer_lease_id=source_intent.writer_lease_id,
        first_sequence=source_intent.first_sequence,
        last_sequence=source_intent.last_sequence,
        prior_chain_sha256=source_intent.prior_chain_sha256,
        payload_sha256=source_intent.payload_sha256,
        payload_bytes=source_intent.payload_bytes,
        object_key=source_intent.object_key,
        destination_age_recipient=source_intent.destination_age_recipient,
        transport_policy_sha256=source_intent.transport_policy_sha256,
        source_cutover_artifact_sha256=source_intent.source_cutover_artifact_sha256,
        source_cutover_artifact_bytes=source_intent.source_cutover_artifact_bytes,
    )


def seal_row(value: ObjectDeltaSourcePublicationAttempt) -> PublicationSealRow:
    return PublicationSealRow(
        id=802,
        attempt_id=value.attempt_id,
        ciphertext_sha256="b" * 64,
        ciphertext_bytes=768,
        spool_sha256="b" * 64,
        spool_bytes=768,
    )


def receipt_row(value: ObjectDeltaSourcePublicationAttempt) -> PublicationReceiptRow:
    return PublicationReceiptRow(
        id=803,
        attempt_id=value.attempt_id,
        object_key=value.intent.object_key,
        object_version_id="version-20260731-01",
        ciphertext_sha256="b" * 64,
        ciphertext_bytes=768,
        transport_receipt_artifact_sha256="c" * 64,
        transport_receipt_artifact_bytes=512,
    )


def attestation_artifact(value: ObjectDeltaSourcePublicationAttempt) -> ObjectDeltaSourcePublicationAttestationArtifact:
    return ObjectDeltaSourcePublicationAttestationArtifact(
        attempt_id=value.attempt_id,
        source_key_id="ed25519-sha256:" + "d" * 64,
        batch_sha256="e" * 64,
        source_attestation_artifact_sha256="f" * 64,
        source_attestation_artifact_bytes=1024,
    )


def attestation_row(value: ObjectDeltaSourcePublicationAttempt) -> PublicationAttestationRow:
    artifact = attestation_artifact(value)
    return PublicationAttestationRow(
        id=804,
        attempt_id=value.attempt_id,
        source_key_id=artifact.source_key_id,
        batch_sha256=artifact.batch_sha256,
        source_attestation_artifact_sha256=artifact.source_attestation_artifact_sha256,
        source_attestation_artifact_bytes=artifact.source_attestation_artifact_bytes,
    )


def ledger_entry(value: ObjectDeltaSourcePublicationAttempt) -> SourceBatchLedgerEntry:
    source_receipt = receipt(value)
    source_artifact = attestation_artifact(value)
    source_intent = value.intent
    return SourceBatchLedgerEntry(
        stream=source_intent.stream,
        first_sequence=source_intent.first_sequence,
        last_sequence=source_intent.last_sequence,
        writer_epoch=source_intent.writer_epoch,
        writer_lease_id=source_intent.writer_lease_id,
        prior_chain_sha256=source_intent.prior_chain_sha256,
        batch_sha256=source_artifact.batch_sha256,
        payload_sha256=source_intent.payload_sha256,
        payload_bytes=source_intent.payload_bytes,
        object_key=source_intent.object_key,
        object_version_id=source_receipt.object_version_id,
        ciphertext_sha256=source_receipt.ciphertext_sha256,
        ciphertext_bytes=source_receipt.ciphertext_bytes,
    )


def ledger_row(value: ObjectDeltaSourcePublicationAttempt) -> ObjectDeltaSourceBatchLedger:
    entry = ledger_entry(value)
    return ObjectDeltaSourceBatchLedger(
        id=901,
        stream_id=701,
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


def cutover_row(value: ObjectDeltaSourcePublicationAttempt) -> ObjectDeltaSourceCutover:
    source_intent = value.intent
    return ObjectDeltaSourceCutover(
        id=711,
        stream_id=701,
        source_site=source_intent.stream.source_site,
        destination_site=source_intent.stream.destination_site,
        campaign_id=source_intent.stream.campaign_id,
        release_sha=source_intent.stream.release_sha,
        stream_generation_id=source_intent.stream.stream_generation_id,
        write_gate_id=UUID("12345678-1234-4234-9234-123456789abc"),
        registry_fingerprint="abcd1234efgh5678",
        writer_epoch=source_intent.writer_epoch,
        writer_lease_id=source_intent.writer_lease_id,
        source_generation="source-generation-1",
        snapshot_id="20260731T000000Z-" + "a" * 16,
        alembic_revision="0deltaguard01",
        snapshot_manifest_object_key="campaigns/three-site/snapshot.age",
        snapshot_manifest_object_version_id="snapshot-v1",
        snapshot_manifest_ciphertext_sha256="1" * 64,
        snapshot_manifest_ciphertext_bytes=1024,
        baseline_manifest_object_key="campaigns/three-site/baseline.age",
        baseline_manifest_object_version_id="baseline-v1",
        baseline_manifest_ciphertext_sha256="2" * 64,
        baseline_manifest_ciphertext_bytes=1024,
        database_sha256="3" * 64,
        uploads_sha256="4" * 64,
        state="baseline_published",
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _AttemptSession:
    """Async session double that makes the adapter's lock order observable."""

    def __init__(
        self,
        *,
        stream=None,
        by_attempt=None,
        by_object_key=None,
        seal=None,
        receipt=None,
        attestation=None,
        binding=None,
        ledger=None,
        cutover=None,
        active=True,
    ):
        self.stream = stream
        self.by_attempt = by_attempt
        self.by_object_key = by_object_key
        self.seal = seal
        self.receipt = receipt
        self.attestation = attestation
        self.binding = binding
        self.ledger = ledger
        self.cutover = cutover
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
        if PublicationAttemptRow in entities:
            return _ScalarResult(
                self.by_attempt
                if "WHERE object_delta_source_publication_attempts.attempt_id =" in rendered
                else self.by_object_key
            )
        if PublicationSealRow in entities:
            return _ScalarResult(self.seal)
        if PublicationReceiptRow in entities:
            return _ScalarResult(self.receipt)
        if PublicationAttestationRow in entities:
            return _ScalarResult(self.attestation)
        if PublicationLedgerBindingRow in entities:
            return _ScalarResult(self.binding)
        if ObjectDeltaSourceBatchLedger in entities:
            return _ScalarResult(self.ledger)
        if ObjectDeltaSourceCutover in entities:
            return _ScalarResult(self.cutover)
        raise AssertionError(f"unexpected statement: {statement}")

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = 1000 + self.flush_count

    async def begin(self):
        self.begin_count += 1
        raise AssertionError("adapter must not begin a transaction")

    async def commit(self):
        self.commit_count += 1
        raise AssertionError("adapter must not commit")

    async def rollback(self):
        self.rollback_count += 1
        raise AssertionError("adapter must not roll back")


class ObjectDeltaSourcePublicationAttemptPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reservation_locks_stream_and_both_unique_identities_then_inserts(self):
        candidate = attempt()
        session = _AttemptSession(stream=source_stream())

        result = await _reserve_object_delta_source_publication_attempt(session, candidate.intent)

        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE, result.action)
        self.assertEqual(candidate, result.state)
        self.assertEqual([PublicationAttemptRow], [type(value) for value in session.added])
        self.assertEqual(candidate.attempt_id, result.attempt_row.attempt_id)
        self.assertEqual(1, session.flush_count)
        rendered = "\n".join(str(statement) for statement in session.statements)
        self.assertGreaterEqual(rendered.count("pg_advisory_xact_lock"), 3)
        self.assertGreaterEqual(rendered.count("FOR UPDATE"), 3)
        self.assertEqual(0, session.begin_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)

    async def test_reservation_exact_replay_uses_both_row_lookups_without_insert(self):
        candidate = attempt()
        existing = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=existing,
            by_object_key=existing,
        )

        result = await _reserve_object_delta_source_publication_attempt(session, candidate.intent)

        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY, result.action)
        self.assertIs(existing, result.attempt_row)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_reservation_rejects_foreign_object_key_before_writing(self):
        candidate = attempt()
        foreign = reservation_row(attempt(writer_epoch=8))
        foreign.object_key = candidate.intent.object_key
        session = _AttemptSession(stream=source_stream(), by_object_key=foreign)

        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "reservation",
        ):
            await _reserve_object_delta_source_publication_attempt(session, candidate.intent)

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_seal_and_receipt_follow_immutable_phase_order_and_replay_exactly(self):
        candidate = attempt()
        reserved = reservation_row(candidate)
        seal_session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
        )
        sealed = await _seal_object_delta_source_publication_attempt(
            seal_session,
            attempt=candidate,
            ciphertext=ciphertext(candidate),
        )
        self.assertEqual(SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL, sealed.action)
        self.assertEqual([PublicationSealRow], [type(value) for value in seal_session.added])

        durable_seal = seal_row(candidate)
        receipt_session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=durable_seal,
        )
        uploaded = await _record_object_delta_source_publication_exact_receipt(
            receipt_session,
            attempt=candidate,
            receipt=receipt(candidate),
        )
        self.assertEqual(SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD, uploaded.action)
        self.assertEqual([PublicationReceiptRow], [type(value) for value in receipt_session.added])

        replay_session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=durable_seal,
            receipt=receipt_row(candidate),
        )
        replay = await _record_object_delta_source_publication_exact_receipt(
            replay_session,
            attempt=candidate,
            receipt=receipt(candidate),
        )
        self.assertEqual(SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY, replay.action)
        self.assertEqual([], replay_session.added)

    async def test_reseal_with_different_ciphertext_fails_without_write(self):
        candidate = attempt()
        reserved = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=seal_row(candidate),
        )

        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "ciphertext seal conflicts",
        ):
            await _seal_object_delta_source_publication_attempt(
                session,
                attempt=candidate,
                ciphertext=ciphertext(
                    candidate,
                    ciphertext_sha256="c" * 64,
                    spool_sha256="c" * 64,
                ),
            )

        self.assertEqual([], session.added)

    async def test_receipt_before_seal_fails_closed_without_write(self):
        candidate = attempt()
        reserved = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
        )

        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "requires a durable ciphertext seal",
        ):
            await _record_object_delta_source_publication_exact_receipt(
                session,
                attempt=candidate,
                receipt=receipt(candidate),
            )

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_authorized_boundary_rejects_raw_prepared_batch_before_database_io(self):
        session = _AttemptSession(stream=source_stream())
        raw_prepared = PreparedObjectDeltaSourceBatch(
            batch=object(),
            transport_binding=object(),
            ledger_entry=object(),
        )

        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "authorized source attestation capability",
        ):
            await record_authorized_object_delta_source_publication_attestation(
                session,
                authorization=raw_prepared,
            )

        self.assertEqual([], session.statements)
        self.assertEqual([], session.added)

    async def test_former_public_attestation_and_ledger_entrypoints_are_hard_disabled_before_sql(self):
        attestation_session = _AttemptSession(stream=source_stream())
        with self.assertRaisesRegex(
            LegacyObjectDeltaSourcePublicationDisabledError,
            "hard-disabled.*locked source snapshot.*live Writer Witness",
        ):
            await disabled_record_authorized_object_delta_source_publication_attestation(
                attestation_session,
                authorization=object(),
            )
        self.assertEqual([], attestation_session.statements)
        self.assertEqual([], attestation_session.added)

        ledger_session = _AttemptSession(stream=source_stream())
        with self.assertRaisesRegex(LegacyObjectDeltaSourcePublicationDisabledError, "hard-disabled"):
            await disabled_bind_authorized_object_delta_source_publication_ledger(
                ledger_session,
                authorization=object(),
            )
        self.assertEqual([], ledger_session.statements)
        self.assertEqual([], ledger_session.added)

    async def test_terminal_binding_uses_gated_facts_and_ledger_append_in_same_transaction(self):
        candidate = attempt()
        durable_ledger = ledger_row(candidate)
        facts = _AuthorizedSourcePublicationFacts(
            attempt=candidate,
            attestation=attestation_artifact(candidate),
            ledger_entry=ledger_entry(candidate),
            prepared=object(),
            expected_registry_fingerprint="abcd1234efgh5678",
        )
        reserved = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=seal_row(candidate),
            receipt=receipt_row(candidate),
            attestation=attestation_row(candidate),
            cutover=cutover_row(candidate),
        )
        ledger_result = ObjectDeltaSourceLedgerPersistenceResult(
            action="append",
            ledger_entry=facts.ledger_entry,
            ledger_row=durable_ledger,
        )

        with (
            patch(
                "core.object_delta_source_publication_attempt_persistence._authorized_facts",
                return_value=facts,
            ),
            patch(
                "core.object_delta_source_publication_attempt_persistence._legacy_test_only_persist_prepared_object_delta_source_batch_ledger",
                new=AsyncMock(return_value=ledger_result),
            ) as persist_ledger,
        ):
            result = await bind_authorized_object_delta_source_publication_ledger(
                session,
                authorization=object(),
            )

        self.assertEqual(SOURCE_PUBLICATION_LEDGER_ACTION_APPEND, result.action)
        self.assertEqual([PublicationLedgerBindingRow], [type(value) for value in session.added])
        self.assertEqual(candidate.attempt_id, result.ledger_binding_row.attempt_id)
        self.assertEqual(durable_ledger.id, result.ledger_binding_row.source_batch_ledger_id)
        persist_ledger.assert_awaited_once_with(session, facts.prepared)
        self.assertEqual(1, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        rendered = [str(statement) for statement in session.statements]
        cutover_index = next(
            index
            for index, statement in enumerate(rendered)
            if "object_delta_source_cutovers" in statement
        )
        attempt_index = next(
            index
            for index, statement in enumerate(rendered)
            if "object_delta_source_publication_attempts" in statement
        )
        self.assertLess(cutover_index, attempt_index)

    async def test_terminal_rejects_orphan_ledger_replay_without_creating_binding(self):
        candidate = attempt()
        facts = _AuthorizedSourcePublicationFacts(
            attempt=candidate,
            attestation=attestation_artifact(candidate),
            ledger_entry=ledger_entry(candidate),
            prepared=object(),
            expected_registry_fingerprint="abcd1234efgh5678",
        )
        reserved = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=seal_row(candidate),
            receipt=receipt_row(candidate),
            attestation=attestation_row(candidate),
            cutover=cutover_row(candidate),
        )
        replay = ObjectDeltaSourceLedgerPersistenceResult(
            action="replay",
            ledger_entry=facts.ledger_entry,
            ledger_row=ledger_row(candidate),
        )
        with (
            patch(
                "core.object_delta_source_publication_attempt_persistence._authorized_facts",
                return_value=facts,
            ),
            patch(
                "core.object_delta_source_publication_attempt_persistence._legacy_test_only_persist_prepared_object_delta_source_batch_ledger",
                new=AsyncMock(return_value=replay),
            ),
            self.assertRaisesRegex(
                ObjectDeltaSourcePublicationAttemptPersistenceError,
                "exists without a terminal publication binding",
            ),
        ):
            await bind_authorized_object_delta_source_publication_ledger(
                session,
                authorization=object(),
            )
        self.assertEqual([], session.added)

    async def test_terminal_ledger_adapter_failure_never_writes_binding(self):
        candidate = attempt()
        facts = _AuthorizedSourcePublicationFacts(
            attempt=candidate,
            attestation=attestation_artifact(candidate),
            ledger_entry=ledger_entry(candidate),
            prepared=object(),
            expected_registry_fingerprint="abcd1234efgh5678",
        )
        reserved = reservation_row(candidate)
        session = _AttemptSession(
            stream=source_stream(),
            by_attempt=reserved,
            by_object_key=reserved,
            seal=seal_row(candidate),
            receipt=receipt_row(candidate),
            attestation=attestation_row(candidate),
            cutover=cutover_row(candidate),
        )

        with (
            patch(
                "core.object_delta_source_publication_attempt_persistence._authorized_facts",
                return_value=facts,
            ),
            patch(
                "core.object_delta_source_publication_attempt_persistence._legacy_test_only_persist_prepared_object_delta_source_batch_ledger",
                new=AsyncMock(
                    side_effect=ObjectDeltaSourceLedgerPersistenceError("database unavailable")
                ),
            ),
            self.assertRaisesRegex(
                ObjectDeltaSourcePublicationAttemptPersistenceError,
                "ledger persistence failed",
            ),
        ):
            await bind_authorized_object_delta_source_publication_ledger(
                session,
                authorization=object(),
            )

        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)

    async def test_inactive_transaction_fails_before_sql(self):
        session = _AttemptSession(stream=source_stream(), active=False)

        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "active caller-owned transaction",
        ):
            await _reserve_object_delta_source_publication_attempt(session, intent())

        self.assertEqual([], session.statements)

    def test_attempt_and_object_key_advisory_locks_are_stable_and_ordered(self):
        candidate = attempt()
        first = source_publication_attempt_advisory_lock_keys(candidate)
        second = source_publication_attempt_advisory_lock_keys(candidate)
        self.assertEqual(first, second)
        self.assertEqual(tuple(sorted(first)), first)
        self.assertEqual(2, len(first))
        self.assertNotEqual(
            first,
            source_publication_attempt_advisory_lock_keys(
                attempt(object_key=OBJECT_KEY + ".other")
            ),
        )

    def test_public_preupload_seam_requires_a_separate_opaque_coordinator_capability(self):
        import core.object_delta_source_publication_attempt_persistence as adapter

        self.assertTrue(
            hasattr(adapter, "reserve_authorized_object_delta_source_preupload_attempt")
        )
        self.assertIn(
            "AuthorizedObjectDeltaSourcePreuploadReservation",
            adapter.__all__,
        )
        self.assertFalse(
            hasattr(adapter, "reserve_object_delta_source_publication_attempt")
        )
        self.assertFalse(
            hasattr(adapter, "seal_object_delta_source_publication_attempt")
        )
        self.assertFalse(
            hasattr(adapter, "record_object_delta_source_publication_exact_receipt")
        )
        self.assertFalse(
            {
                "_legacy_test_only_reserve_object_delta_source_publication_attempt",
                "_legacy_test_only_seal_object_delta_source_publication_attempt",
                "_legacy_test_only_record_object_delta_source_publication_exact_receipt",
                "_legacy_test_only_record_authorized_object_delta_source_publication_attestation",
                "_legacy_test_only_bind_authorized_object_delta_source_publication_ledger",
                "record_authorized_object_delta_source_publication_attestation",
                "bind_authorized_object_delta_source_publication_ledger",
            }
            & set(adapter.__all__)
        )
        obligations = "\n".join(
            REQUIRED_OBJECT_DELTA_SOURCE_PREUPLOAD_RESERVATION_AUTHORIZATION
        )
        self.assertIn("non-public root-only coordinator authority", obligations)
        self.assertIn("locked contiguous outbox selection", obligations)
        self.assertIn("fresh live Writer Witness", obligations)


class ObjectDeltaSourcePublicationAttemptPersistenceStaticTests(unittest.TestCase):
    def test_adapter_has_no_storage_crypto_or_runtime_activation_import(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "object_delta_source_publication_attempt_persistence.py"
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
        banned_calls = {"begin", "commit", "rollback", "put_object", "open", "write_bytes"}
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
