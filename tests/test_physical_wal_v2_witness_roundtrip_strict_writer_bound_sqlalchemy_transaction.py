"""Adversarial fake-session tests for the Gen2 bound SQL transaction seam."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.exc import IntegrityError

from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction as subject
from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)


NOW = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _Scalars:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return list(self.values)


class _RowsResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self.values)


class _AsyncSessionDouble:
    """Records SQL order and deliberately exposes lifecycle methods unused."""

    def __init__(
        self,
        *,
        registry: object | None = None,
        rows: list[object] | None = None,
        flush_error: Exception | None = None,
        dirty: bool = False,
        loaded_identity: bool = False,
        timeline: list[str] | None = None,
    ) -> None:
        self.registry = registry
        self.rows = [] if rows is None else rows
        self.flush_error = flush_error
        self.events: list[str] = []
        self.timeline = timeline
        self.added: list[object] = []
        self.new = set()
        self.dirty = {object()} if dirty else set()
        self.deleted = set()
        self.identity_map = {object(): object()} if loaded_identity else {}
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.begin_count = 0

    def _record(self, event: str) -> None:
        self.events.append(event)
        if self.timeline is not None:
            self.timeline.append(event)

    def in_transaction(self) -> bool:
        return True

    def get_transaction(self) -> object:
        return SimpleNamespace(nested=False)

    def get_nested_transaction(self) -> None:
        return None

    def get_bind(self) -> object:
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, statement: object) -> object:
        rendered = str(statement)
        if "pg_advisory_xact_lock" in rendered:
            self._record("advisory_lock")
            return _ScalarResult(None)
        if "physical_wal_v2_witness_roundtrip_attestation_consumptions" in rendered:
            self._record("registry_lookup")
            return _ScalarResult(self.registry)
        if "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits" in rendered:
            self._record("gen2_lookup")
            return _RowsResult(self.rows)
        raise AssertionError(f"unexpected statement: {rendered}")

    def add(self, value: object) -> None:
        self._record("gen2_add")
        self.added.append(value)

    async def flush(self) -> None:
        self._record("gen2_flush")
        if self.flush_error is not None:
            raise self.flush_error

    async def begin(self) -> None:
        self.begin_count += 1

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def close(self) -> None:
        self.close_count += 1


class _V1Adapter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def persist_writer_admission(self, *, session: object, writer_admission: object) -> object:
        del session
        self.calls += 1
        self.events.append("v1_parent_flush")
        if writer_admission != "opaque-v1-admission":
            raise AssertionError("unexpected writer admission")
        return "opaque-v1-sql-receipt"


class _Issuer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.consumed = False
        self.v2_calls = 0
        self.v1_calls = 0
        self.bind_calls = 0

    def require_v2_prepared_for_transaction(self, *, issued: object) -> object:
        if issued != "opaque-issued" or self.consumed:
            raise AssertionError("issued bridge cannot be reused after post-flush bind")
        self.v2_calls += 1
        self.events.append("issuer_v2_prepare")
        return "opaque-legacy-v2-prepared"

    def require_writer_admission_for_transaction(self, *, issued: object) -> object:
        if issued != "opaque-issued" or self.consumed:
            raise AssertionError("unexpected issued bridge")
        self.v1_calls += 1
        self.events.append("issuer_v1_admission")
        return "opaque-v1-admission"

    def bind_post_flush(self, *, issued: object, v1_sql_commit_receipt: object) -> object:
        if issued != "opaque-issued" or v1_sql_commit_receipt != "opaque-v1-sql-receipt":
            raise AssertionError("post-flush bind did not receive exact receipt")
        self.bind_calls += 1
        self.consumed = True
        self.events.append("issuer_bind_post_flush")
        return "opaque-bridge-bound"


class _AwaitableValue:
    def __await__(self):
        yield None
        return None


def _sha(char: str) -> str:
    return char * 64


def _base_instruction() -> SimpleNamespace:
    return SimpleNamespace(
        schema="gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2",
        configuration_sha256=_sha("a"),
        v2_base_configuration_sha256=_sha("b"),
        atomic_commit_boundary="root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2",
        commit_id="v2-witness-strict-writer-g2-" + _sha("c"),
        v2_base_commit_id="v2-witness-strict-writer-" + _sha("d"),
        attestation_sha256=_sha("e"),
        ir_durable_assertion_sha256=_sha("f"),
        context_certificate_sha256=_sha("1"),
        context_sha256=_sha("2"),
        source_envelope_sha256=_sha("3"),
        source_request_sha256=_sha("4"),
        destination_receipt_sha256=_sha("5"),
        durable_ledger_entry_sha256=_sha("6"),
        target_recovery_evidence_sha256=_sha("7"),
        readback_attestation_sha256=_sha("8"),
        stage_receipt_sha256=_sha("9"),
        witness_sequence=13,
        witness_ledger_entry_sha256=_sha("a"),
        witness_ledger_previous_head_sha256=_sha("0"),
        witness_ledger_binding_sha256=_sha("b"),
        writer_holder_site="webapp_fi",
        writer_epoch=9,
        writer_lease_id="writer-lease-0009",
        witnessed_term_proof_sha256=_sha("c"),
        witness_transition_id="witness-transition-0009",
        activation_mode="normal_fi_writer",
        activation_stream_generation_id="stream-gen-0009",
        activation_route_artifact_sha256=_sha("d"),
        activation_source_cutover_attestation_sha256=_sha("e"),
        activation_receiver_permit_sha256=_sha("f"),
    )


def _bound_instruction(base: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        **vars(base),
        v1_parent_cluster_id="gold-trade-three-site",
        v1_parent_local_site="webapp_fi",
        v1_parent_release_sha="a" * 40,
        v1_parent_generation_id="physical-generation-0009",
        v1_writer_admission_commit_id=str(uuid4()),
        v1_writer_admission_commit_sha256=_sha("a"),
        v1_writer_admission_receipt_sha256=_sha("b"),
        v1_parent_prior_revision=41,
        v1_parent_next_revision=42,
        v1_parent_fence_generation=9,
        v1_parent_holder_site="webapp_fi",
        v1_parent_evidence_id="witness-evidence-0009",
        v1_parent_revalidation_id="revalidation-id-0009",
        v1_parent_writer_epoch=9,
        v1_parent_writer_lease_id="writer-lease-0009",
        v1_parent_term_issued_at=NOW - timedelta(seconds=10),
        v1_parent_term_expires_at=NOW + timedelta(seconds=50),
        v1_parent_admitted_at=NOW,
        v1_v2_writer_term_bridge_certificate_id="bridge-certificate-0009",
        v1_v2_writer_term_bridge_intent_sha256=_sha("c"),
        v1_v2_writer_term_bridge_certificate_sha256=_sha("d"),
        v1_v2_writer_term_bridge_parent_binding_sha256=_sha("e"),
        canonical_v1_v2_writer_term_bridge_certificate=b"canonical-bridge-certificate",
        issued_at=NOW,
    )


class PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.timeline: list[str] = []
        self.events = self.timeline
        self.base = _base_instruction()
        self.instruction = _bound_instruction(self.base)
        self.prepared = object()
        self.bound = object()
        self.issuer = _Issuer(self.events)
        self.v1_adapter = _V1Adapter(self.events)
        self.config = subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig(
            enabled=True,
        )
        self.facts = SimpleNamespace(
            v1_transaction_config=object(),
            bound_response_config=object(),
            local_commit_private_key=Ed25519PrivateKey.generate(),
            v1_binding=object(),
        )
        self.adapter = subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter(
            self.config
        )
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(subject, "_facts", return_value=self.facts))
        self.stack.enter_context(
            patch.object(
                subject.v1_sql,
                "physical_operational_failover_v1_writer_admission_head_advisory_lock_key",
                return_value=19,
            )
        )
        self.stack.enter_context(
            patch.object(
                subject.v1_sql,
                "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter",
                return_value=self.v1_adapter,
            )
        )
        self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
                side_effect=self._prepare,
            )
        )
        self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
                side_effect=self._require_prepared,
            )
        )
        self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
                side_effect=self._bind,
            )
        )
        self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                side_effect=self._require_bound,
            )
        )
        self.sign = self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt",
                side_effect=self._sign,
            )
        )
        self.finalize = self.stack.enter_context(
            patch.object(
                subject.bound_response,
                "finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response",
            )
        )
        self.stack.enter_context(patch.object(subject, "_utc_now", return_value=NOW))

    def tearDown(self) -> None:
        self.stack.close()

    def _prepare(self, *, config: object, v2_prepared: object) -> object:
        if config is not self.facts.bound_response_config or v2_prepared != "opaque-legacy-v2-prepared":
            raise AssertionError("bad opaque V2 prepare handoff")
        self.events.append("gen2_prepare")
        return self.prepared

    def _require_prepared(self, value: object, *, config: object) -> object:
        if value is not self.prepared or config is not self.facts.bound_response_config:
            raise AssertionError("bad prepared capability")
        self.events.append("gen2_require_prepared")
        return self.base

    def _bind(self, prepared: object, *, bridge_bound: object, config: object) -> object:
        if prepared is not self.prepared or bridge_bound != "opaque-bridge-bound":
            raise AssertionError("bad post-flush bridge bind")
        self.events.append("gen2_bind")
        return self.bound

    def _require_bound(self, value: object, *, config: object) -> object:
        if value is not self.bound or config is not self.facts.bound_response_config:
            raise AssertionError("bad bound capability")
        self.events.append("gen2_require_bound")
        return self.instruction

    def _sign(self, bound: object, **kwargs: object) -> bytes:
        if bound is not self.bound:
            raise AssertionError("signing did not follow opaque binding")
        if kwargs["committed_at"] != NOW:
            raise AssertionError("unexpected local signing time")
        # A sign call must happen before add/flush and never release response.
        if "gen2_add" in self.events or "gen2_flush" in self.events:
            raise AssertionError("Gen2 signing reversed after row persistence")
        self.events.append("gen2_sign")
        return b"canonical-runtime-receipt"

    def _registry(self, *, source: str, commit_id: str) -> PhysicalWalV2WitnessRoundtripAttestationConsumption:
        return PhysicalWalV2WitnessRoundtripAttestationConsumption(
            attestation_sha256=self.base.attestation_sha256,
            source_generation=source,
            source_commit_id=commit_id,
            consumed_at=NOW,
        )

    async def test_absent_orders_lookup_v1_bind_sign_insert_and_never_finalizes_pre_commit(self) -> None:
        session = _AsyncSessionDouble(timeline=self.timeline)

        result = await self.adapter.persist_bound_writer_response(
            session=session,
            issued_bridge="opaque-issued",
            issuer=self.issuer,
        )

        self.assertIsInstance(
            result,
            subject.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
        )
        assert isinstance(result, subject.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit)
        self.assertEqual("pending_external_commit", result.outcome)
        self.assertEqual(
            [
                "issuer_v2_prepare",
                "gen2_prepare",
                "gen2_require_prepared",
                "advisory_lock",
                "registry_lookup",
                "gen2_lookup",
                "issuer_v1_admission",
                "v1_parent_flush",
                "issuer_bind_post_flush",
                "gen2_bind",
                "gen2_require_bound",
                "gen2_sign",
                "gen2_add",
                "gen2_flush",
            ],
            self.timeline,
        )
        self.assertLess(self.timeline.index("gen2_sign"), self.timeline.index("gen2_add"))
        self.assertEqual(["advisory_lock", "registry_lookup", "gen2_lookup", "gen2_add", "gen2_flush"], session.events)
        self.assertEqual(0, session.begin_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        self.assertEqual(0, session.close_count)
        self.finalize.assert_not_called()
        self.assertEqual(1, len(session.added))
        row = session.added[0]
        self.assertEqual(self.base.v2_base_configuration_sha256, row.v2_base_configuration_sha256)
        self.assertEqual(self.base.v2_base_commit_id, row.v2_base_commit_id)
        self.assertEqual(result.reconciliation_identity.commit_id, row.commit_id)

    async def test_restart_reconciles_with_serializable_identity_not_consumed_issuer(self) -> None:
        first = _AsyncSessionDouble(timeline=self.timeline)
        pending = await self.adapter.persist_bound_writer_response(
            session=first,
            issued_bridge="opaque-issued",
            issuer=self.issuer,
        )
        self.assertIsInstance(
            pending,
            subject.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
        )
        assert isinstance(pending, subject.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit)
        self.assertTrue(self.issuer.consumed)
        # Simulate the database committing while its response is lost, then a
        # process restart: only non-authorizing identity survives.
        row = subject._model_row(
            self.instruction,
            runtime_receipt=pending.runtime_receipt,
            committed_at=NOW,
        )
        registry = self._registry(
            source=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
            commit_id=row.commit_id,
        )
        restart = _AsyncSessionDouble(registry=registry, rows=[row], timeline=self.timeline)
        before = (self.issuer.v2_calls, self.issuer.v1_calls, self.issuer.bind_calls)

        result = await self.adapter.reconcile_after_unknown_outcome(
            session=restart,
            reconciliation_identity=pending.reconciliation_identity,
        )

        self.assertEqual("known_durable", result.outcome)
        self.assertTrue(result.requires_hard_fence)
        self.assertIsNotNone(result.durable_row)
        assert result.durable_row is not None
        self.assertTrue(result.durable_row.reconciliation_required)
        self.assertTrue(result.durable_row.requires_hard_fence)
        self.assertEqual(before, (self.issuer.v2_calls, self.issuer.v1_calls, self.issuer.bind_calls))
        self.assertEqual(["advisory_lock", "registry_lookup", "gen2_lookup"], restart.events)
        self.finalize.assert_not_called()

    async def test_absent_restart_outcome_is_unknown_and_hard_fenced(self) -> None:
        identity = subject._reconciliation_identity_from_base(self.base)
        session = _AsyncSessionDouble(timeline=self.timeline)

        result = await self.adapter.reconcile_after_unknown_outcome(
            session=session,
            reconciliation_identity=identity,
        )

        self.assertEqual("unknown", result.outcome)
        self.assertIsNone(result.durable_row)
        self.assertTrue(result.requires_hard_fence)
        self.assertEqual(0, self.issuer.v2_calls)
        self.assertEqual(0, self.v1_adapter.calls)

    async def test_existing_gen2_returns_reconciliation_required_without_v1_or_response_release(self) -> None:
        row = subject._model_row(
            self.instruction,
            runtime_receipt=b"canonical-runtime-receipt",
            committed_at=NOW,
        )
        session = _AsyncSessionDouble(
            registry=self._registry(
                source=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
                commit_id=row.commit_id,
            ),
            rows=[row],
            timeline=self.timeline,
        )

        result = await self.adapter.persist_bound_writer_response(
            session=session,
            issued_bridge="opaque-issued",
            issuer=self.issuer,
        )

        self.assertIsInstance(
            result,
            subject.DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired,
        )
        assert isinstance(result, subject.DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired)
        self.assertTrue(result.requires_hard_fence)
        self.assertTrue(result.reconciliation_required)
        self.assertEqual(0, self.issuer.v1_calls)
        self.assertEqual(0, self.issuer.bind_calls)
        self.assertEqual(0, self.v1_adapter.calls)
        self.assertEqual([], session.added)
        self.finalize.assert_not_called()

    async def test_cross_generation_registry_claim_is_hard_fence_before_v1(self) -> None:
        session = _AsyncSessionDouble(
            registry=self._registry(
                source=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
                commit_id="v2-witness-strict-writer-" + _sha("a"),
            ),
            timeline=self.timeline,
        )

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=self.issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CROSS_GENERATION_ATTESTATION_CONFLICT_HARD_FENCE",
            raised.exception.code,
        )
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertEqual(0, self.issuer.v1_calls)
        self.assertEqual(0, self.v1_adapter.calls)
        self.finalize.assert_not_called()

    async def test_registry_gen2_row_mismatch_is_hard_fence_before_v1(self) -> None:
        changed = _bound_instruction(self.base)
        changed.commit_id = "v2-witness-strict-writer-g2-" + _sha("f")
        row = subject._model_row(
            changed,
            runtime_receipt=b"canonical-runtime-receipt",
            committed_at=NOW,
        )
        session = _AsyncSessionDouble(
            registry=self._registry(
                source=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
                commit_id=row.commit_id,
            ),
            rows=[row],
            timeline=self.timeline,
        )

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=self.issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_OR_GEN2_INCONSISTENT_HARD_FENCE",
            raised.exception.code,
        )
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertEqual(0, self.v1_adapter.calls)

    async def test_registry_trigger_conflict_becomes_unknown_hard_fence_without_lifecycle(self) -> None:
        session = _AsyncSessionDouble(
            flush_error=IntegrityError(
                "insert physical_wal_v2_witness_roundtrip_attestation_consumptions",
                {},
                RuntimeError("v2wsrc_registry duplicate"),
            ),
            timeline=self.timeline,
        )

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=self.issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GLOBAL_ATTESTATION_CONSUMPTION_CONFLICT_HARD_FENCE",
            raised.exception.code,
        )
        self.assertEqual("unknown", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIsNotNone(raised.exception.reconciliation_identity)
        assert raised.exception.reconciliation_identity is not None
        self.assertEqual(self.base.commit_id, raised.exception.reconciliation_identity.commit_id)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        self.assertEqual(0, session.close_count)

    async def test_async_issuer_handoff_is_forbidden_inside_root_transaction(self) -> None:
        class _AsyncIssuer(_Issuer):
            def require_writer_admission_for_transaction(self, *, issued: object) -> object:
                del issued
                return _AwaitableValue()

        issuer = _AsyncIssuer(self.events)
        session = _AsyncSessionDouble(timeline=self.timeline)

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_OPAQUE_ISSUER_ASYNC_FORBIDDEN",
            raised.exception.code,
        )
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertEqual(0, self.v1_adapter.calls)

    async def test_dirty_external_transaction_is_rejected_before_opaque_or_database_work(self) -> None:
        session = _AsyncSessionDouble(dirty=True, timeline=self.timeline)

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=self.issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_UNGUARDED_PENDING_MUTATION",
            raised.exception.code,
        )
        self.assertEqual([], self.events)
        self.assertEqual([], session.events)

    async def test_preloaded_identity_map_is_not_a_fresh_clean_root_transaction(self) -> None:
        session = _AsyncSessionDouble(loaded_identity=True, timeline=self.timeline)

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError) as raised:
            await self.adapter.persist_bound_writer_response(
                session=session,
                issued_bridge="opaque-issued",
                issuer=self.issuer,
            )

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_UNGUARDED_PENDING_MUTATION",
            raised.exception.code,
        )
        self.assertEqual([], self.timeline)
