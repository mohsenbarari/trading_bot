"""Adversarial lifecycle tests for the Gen2 bound writer transaction envelope."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.orm import aliased

from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction as adapter_module
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_transaction_envelope as subject
from models.operational_writer_admission import (
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer import (
    PhysicalWalV2WitnessRoundtripStrictWriterCommit,
)
from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


class _Pending:
    def __init__(self, reconciliation_identity: object) -> None:
        self.reconciliation_identity = reconciliation_identity


class _DurableReconciliationRequired:
    def __init__(self, reconciliation_identity: object) -> None:
        self.reconciliation_identity = reconciliation_identity


class _TransactionDouble:
    def __init__(self, session: "_SessionDouble") -> None:
        self.session = session
        self.started = False
        self.nested = False

    def __await__(self):
        async def _start():
            if self.started or self.session.active:
                raise RuntimeError("already started")
            self.started = True
            self.session.active = True
            self.session.events.append("begin")
            return self

        return _start().__await__()

    @property
    def is_active(self) -> bool:
        if not self.started:
            raise RuntimeError("not started")
        return self.session.active

    async def commit(self) -> None:
        self.session.transaction_commit_count += 1
        self.session.events.append("transaction_commit")
        if self.session.commit_error is not None:
            raise self.session.commit_error
        if not self.session.active:
            raise RuntimeError("inactive commit")
        self.session.active = False

    async def rollback(self) -> None:
        self.session.transaction_rollback_count += 1
        self.session.events.append("transaction_rollback")
        if self.session.rollback_error is not None:
            raise self.session.rollback_error
        if not self.session.active:
            raise RuntimeError("inactive rollback")
        self.session.active = False


class _SessionDouble:
    def __init__(
        self,
        *,
        dirty: bool = False,
        dialect_name: str = "postgresql",
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.active = False
        self.dialect_name = dialect_name
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.info: dict[object, object] = {}
        self.new = set()
        self.dirty = {object()} if dirty else set()
        self.deleted = set()
        self.identity_map: dict[object, object] = {}
        self.events: list[str] = []
        self.transaction_begin_count = 0
        self.transaction_commit_count = 0
        self.transaction_rollback_count = 0
        self.manual_commit_count = 0
        self.manual_rollback_count = 0
        self.business_add_count = 0
        self.business_execute_count = 0
        self.application_guard_connection_calls = 0
        self.application_guard_sync_connection = object()

    @property
    def is_active(self) -> bool:
        return True

    def in_transaction(self) -> bool:
        return self.active

    def get_bind(self) -> object:
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect_name))

    def begin(self) -> _TransactionDouble:
        self.transaction_begin_count += 1
        return _TransactionDouble(self)

    async def connection(self) -> object:
        self.application_guard_connection_calls += 1
        self.events.append("application_guard_connection")
        return SimpleNamespace(sync_connection=self.application_guard_sync_connection)

    def add(self, value: object) -> None:
        del value
        self.business_add_count += 1
        self.events.append("business_add")

    def add_all(self, values: object) -> None:
        del values
        self.business_add_count += 1
        self.events.append("business_add_all")

    async def delete(self, value: object) -> None:
        del value
        self.events.append("business_delete")

    async def flush(self) -> None:
        self.events.append("business_flush")

    async def execute(self, statement: object, *args: object) -> object:
        del statement, args
        self.business_execute_count += 1
        self.events.append("business_execute")
        return "execute-result"

    async def scalar(self, statement: object, *args: object) -> object:
        del statement, args
        self.events.append("business_scalar")
        return "scalar-result"

    async def scalars(self, statement: object, *args: object) -> object:
        del statement, args
        self.events.append("business_scalars")
        return "scalars-result"

    async def get(self, entity: object, ident: object) -> object:
        del entity, ident
        self.events.append("business_get")
        return "get-result"

    async def refresh(self, instance: object) -> None:
        del instance
        self.events.append("business_refresh")

    async def commit(self) -> None:
        self.manual_commit_count += 1
        self.events.append("manual_commit")
        self.active = False

    async def rollback(self) -> None:
        self.manual_rollback_count += 1
        self.events.append("manual_rollback")
        self.active = False


class _AdapterDouble:
    def __init__(self, events: list[str], result: object) -> None:
        self.events = events
        self.result = result
        self.calls: list[tuple[object, object, object]] = []
        self.reconcile_calls = 0

    async def persist_bound_writer_response(
        self,
        *,
        session: object,
        issued_bridge: object,
        issuer: object,
    ) -> object:
        self.calls.append((session, issued_bridge, issuer))
        self.events.append("adapter_persist")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def reconcile_after_unknown_outcome(self, **kwargs: object) -> None:
        del kwargs
        self.reconcile_calls += 1
        raise AssertionError("envelope must never auto-reconcile")


def _identity() -> adapter_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
    sha = lambda char: char * 64
    return adapter_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity(
        schema="gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2",
        configuration_sha256=sha("a"),
        v2_base_configuration_sha256=sha("b"),
        atomic_commit_boundary="root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2",
        commit_id="v2-witness-strict-writer-g2-" + sha("c"),
        v2_base_commit_id="v2-witness-strict-writer-" + sha("d"),
        attestation_sha256=sha("e"),
        ir_durable_assertion_sha256=sha("f"),
        context_certificate_sha256=sha("1"),
        context_sha256=sha("2"),
        source_envelope_sha256=sha("3"),
        source_request_sha256=sha("4"),
        destination_receipt_sha256=sha("5"),
        durable_ledger_entry_sha256=sha("6"),
        target_recovery_evidence_sha256=sha("7"),
        readback_attestation_sha256=sha("8"),
        stage_receipt_sha256=sha("9"),
        witness_sequence=1,
        witness_ledger_entry_sha256=sha("a"),
        witness_ledger_previous_head_sha256=sha("0"),
        witness_ledger_binding_sha256=sha("b"),
        writer_holder_site="webapp_fi",
        writer_epoch=1,
        writer_lease_id="writer-lease-0001",
        witnessed_term_proof_sha256=sha("c"),
        witness_transition_id="witness-transition-0001",
        activation_mode="normal_fi_writer",
        activation_stream_generation_id="stream-gen-0001",
        activation_route_artifact_sha256=sha("d"),
        activation_source_cutover_attestation_sha256=sha("e"),
        activation_receiver_permit_sha256=sha("f"),
    )


class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.identity = _identity()
        self.pending = _Pending(self.identity)
        self.events: list[str] = []
        self.adapter = _AdapterDouble(self.events, self.pending)
        self.observation = object()
        self.issued_bridge = object()
        self.issuer = object()
        self.adapter_config = adapter_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig(
            enabled=True
        )
        self.config = subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig(
            enabled=True,
            sqlalchemy_transaction_config=self.adapter_config,
        )
        self.constructor_patch = patch.object(
            subject.transaction_adapter,
            "PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter",
            return_value=self.adapter,
        )
        self.pending_patch = patch.object(
            subject.transaction_adapter,
            "PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit",
            _Pending,
        )
        self.durable_patch = patch.object(
            subject.transaction_adapter,
            "DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired",
            _DurableReconciliationRequired,
        )
        self.finalize_patch = patch.object(
            subject.transaction_adapter,
            "finalize_pending_physical_wal_v2_witness_roundtrip_strict_writer_bound_commit",
            side_effect=self._finalize,
        )
        self.constructor_patch.start()
        self.pending_patch.start()
        self.durable_patch.start()
        self.finalize = self.finalize_patch.start()

    def tearDown(self) -> None:
        self.finalize_patch.stop()
        self.durable_patch.stop()
        self.pending_patch.stop()
        self.constructor_patch.stop()

    def _finalize(self, pending: object, *, config: object) -> object:
        if pending is not self.pending or config is not self.adapter_config:
            raise AssertionError("unexpected finalization input")
        self.events.append("finalize_after_commit")
        return self.observation

    def _envelope(self) -> subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope:
        return subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope(self.config)

    async def test_default_off_rejects_before_session_or_adapter_use(self) -> None:
        session = _SessionDouble()
        envelope = subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig()
        )

        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "ENVELOPE_DISABLED",
        ):
            async with envelope.transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                self.fail("disabled envelope must not yield")

        self.assertEqual(0, session.transaction_begin_count)
        self.assertEqual([], self.events)

    async def test_adapter_precedes_business_dml_commit_once_and_finalize_only_after_commit(self) -> None:
        session = _SessionDouble()
        envelope = self._envelope()

        async with envelope.transaction(
            session=session,
            issued_bridge=self.issued_bridge,
            issuer=self.issuer,
        ) as business:
            self.assertFalse(hasattr(business, "commit"))
            self.assertFalse(hasattr(business, "rollback"))
            self.assertFalse(hasattr(business, "close"))
            self.assertFalse(hasattr(business, "begin"))
            self.assertFalse(hasattr(business, "connection"))
            with self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
                "OBSERVATION_UNAVAILABLE",
            ):
                business.verified_observation_after_known_commit()
            business.add(object())
            await business.flush()
            self.assertEqual("execute-result", await business.execute(select(1)))

        self.assertEqual([(session, self.issued_bridge, self.issuer)], self.adapter.calls)
        self.assertEqual(1, session.transaction_begin_count)
        self.assertEqual(1, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)
        self.assertEqual(
            ["begin", "application_guard_connection", "adapter_persist", "business_add", "business_flush", "business_execute", "transaction_commit", "finalize_after_commit"],
            session.events[:2] + self.events[:1] + session.events[2:] + self.events[1:],
        )
        self.assertIs(self.observation, business.verified_observation_after_known_commit())

    async def test_business_failure_rolls_back_once_and_never_finalizes(self) -> None:
        session = _SessionDouble()
        with self.assertRaisesRegex(RuntimeError, "business exploded"):
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ) as business:
                business.add(object())
                raise RuntimeError("business exploded")

        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(1, session.transaction_rollback_count)
        self.finalize.assert_not_called()

    async def test_existing_durable_row_is_reconciliation_required_hard_fence_before_business_dml(self) -> None:
        self.adapter.result = _DurableReconciliationRequired(self.identity)
        session = _SessionDouble()

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError) as raised:
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                self.fail("existing durable row cannot yield business facade")

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_RECONCILIATION_REQUIRED_HARD_FENCE",
            raised.exception.code,
        )
        self.assertEqual("known_durable", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIs(self.identity, raised.exception.reconciliation_identity)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertEqual(0, session.business_add_count)
        self.finalize.assert_not_called()

    async def test_adapter_flush_uncertainty_exposes_only_identity_and_hard_fence(self) -> None:
        self.adapter.result = adapter_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_FLUSH_OUTCOME_UNKNOWN_HARD_FENCE",
            outcome="unknown",
            requires_hard_fence=True,
            reconciliation_identity=self.identity,
        )
        session = _SessionDouble()

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError) as raised:
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                self.fail("uncertain flush cannot yield business facade")

        self.assertEqual("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_PERSIST_FAILED", raised.exception.code)
        self.assertEqual("unknown", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIs(self.identity, raised.exception.reconciliation_identity)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertEqual(0, self.adapter.reconcile_calls)
        self.finalize.assert_not_called()

    async def test_commit_response_loss_is_unknown_hard_fence_without_finalize_or_auto_reconcile(self) -> None:
        session = _SessionDouble(commit_error=ConnectionError("commit reply lost"))
        business = None

        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError) as raised:
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ) as business:
                business.add(object())

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_COMMIT_OUTCOME_UNKNOWN_HARD_FENCE",
            raised.exception.code,
        )
        self.assertEqual("unknown", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIs(self.identity, raised.exception.reconciliation_identity)
        self.assertEqual(1, session.transaction_commit_count)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertEqual(0, self.adapter.reconcile_calls)
        self.finalize.assert_not_called()
        assert business is not None
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "OBSERVATION_UNAVAILABLE",
        ):
            business.verified_observation_after_known_commit()

    async def test_text_and_all_v1_gen2_control_plane_paths_are_forbidden_through_business_facade(self) -> None:
        session = _SessionDouble()
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "BUSINESS_STATEMENT_FORBIDDEN",
        ):
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ) as business:
                await business.execute(text("COMMIT"))
        self.assertEqual(0, session.business_execute_count)
        self.assertEqual(1, session.transaction_rollback_count)

        for entity in (
            OperationalWriterAdmissionHead,
            OperationalWriterAdmissionCommit,
            PhysicalWalV2WitnessRoundtripStrictWriterCommit,
            PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
            PhysicalWalV2WitnessRoundtripAttestationConsumption,
        ):
            with self.subTest(entity=entity.__name__):
                session = _SessionDouble()
                with self.assertRaisesRegex(
                    subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
                    "BUSINESS_STATEMENT_FORBIDDEN",
                ):
                    async with self._envelope().transaction(
                        session=session,
                        issued_bridge=self.issued_bridge,
                        issuer=self.issuer,
                    ) as business:
                        await business.execute(select(entity))
                self.assertEqual(0, session.business_execute_count)

        alias_session = _SessionDouble()
        control_alias = aliased(PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "BUSINESS_STATEMENT_FORBIDDEN",
        ):
            async with self._envelope().transaction(
                session=alias_session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ) as business:
                await business.execute(select(control_alias))
        self.assertEqual(0, alias_session.business_execute_count)

    async def test_direct_terminal_session_misuse_is_detected_without_second_rollback(self) -> None:
        session = _SessionDouble()
        with self.assertRaises(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError
        ) as raised:
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                await session.commit()

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_EXTERNAL_TRANSACTION_OUTCOME_UNKNOWN_HARD_FENCE",
            raised.exception.code,
        )
        self.assertEqual("unknown", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIs(self.identity, raised.exception.reconciliation_identity)
        self.assertEqual(1, session.manual_commit_count)
        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)
        self.finalize.assert_not_called()

    async def test_not_fresh_session_is_rejected_before_begin_or_adapter(self) -> None:
        session = _SessionDouble(dirty=True)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "PENDING_MUTATION",
        ):
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                self.fail("dirty session must not yield")
        self.assertEqual(0, session.transaction_begin_count)
        self.assertEqual([], self.adapter.calls)

    async def test_post_commit_finalization_failure_hard_fences_without_rollback(self) -> None:
        self.finalize.side_effect = adapter_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
            outcome="unknown",
            requires_hard_fence=True,
        )
        session = _SessionDouble()
        with self.assertRaises(subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError) as raised:
            async with self._envelope().transaction(
                session=session,
                issued_bridge=self.issued_bridge,
                issuer=self.issuer,
            ):
                pass

        self.assertEqual(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_POST_COMMIT_FINALIZATION_FAILED_HARD_FENCE",
            raised.exception.code,
        )
        self.assertEqual("known_durable", raised.exception.outcome)
        self.assertTrue(raised.exception.requires_hard_fence)
        self.assertIs(self.identity, raised.exception.reconciliation_identity)
        self.assertEqual(1, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)
