"""Focused fake-session tests for the explicit V1 writer transaction envelope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from sqlalchemy import select, text
from sqlalchemy.orm import aliased
from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as adapter_module
from core import physical_operational_failover_v1_writer_transaction_envelope as subject
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)
from uuid import uuid4


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
POLICY_SHA256 = "c" * 64


@dataclass(frozen=True)
class _Evidence:
    cluster_id: str = "gold-trade-three-site"
    holder_site: str = "webapp_fi"
    writer_epoch: int = 7
    writer_lease_id: str = "writer-lease-0007"
    release_sha: str = RELEASE_SHA
    generation_id: str = "physical-generation-0007"
    evidence_id: str = "witness-evidence-0001"
    revalidation_id: str = "revalidation-id-0001"
    issued_at: datetime = NOW - timedelta(seconds=10)
    expires_at: datetime = NOW + timedelta(seconds=60)


class _Revalidator:
    def __init__(self, evidence: _Evidence) -> None:
        self.evidence = evidence

    def revalidate_writer_term(self, *, request):
        return self.evidence


class _ScalarResult:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _UpdateResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _TransactionDouble:
    def __init__(self, session: "_SessionDouble") -> None:
        self.session = session
        self.started = False
        self.nested = False

    def __await__(self):
        async def _start():
            if self.started or self.session.active:
                raise RuntimeError("transaction already started")
            self.started = True
            self.session.active = True
            self.session.events.append("begin")
            return self

        return _start().__await__()

    @property
    def is_active(self) -> bool:
        # SQLAlchemy's real AsyncSessionTransaction raises until its awaitable
        # begin object has been started.  The envelope must not inspect this
        # state before awaiting the transaction scope.
        if not self.started:
            raise RuntimeError("transaction has not started")
        return self.session.active

    async def commit(self) -> None:
        if not self.session.active:
            raise RuntimeError("transaction is inactive")
        self.session.transaction_commit_count += 1
        self.session.active = False
        self.session.events.append("transaction_commit")

    async def rollback(self) -> None:
        if not self.session.active:
            raise RuntimeError("transaction is inactive")
        self.session.transaction_rollback_count += 1
        self.session.active = False
        self.session.events.append("transaction_rollback")


class _SessionDouble:
    """A fresh caller-local AsyncSession-shaped double for envelope tests."""

    def __init__(
        self,
        *,
        head,
        dialect_name: str = "postgresql",
        admission_head_update_rowcount: int = 1,
    ) -> None:
        self.head = head
        self.dialect_name = dialect_name
        self.admission_head_update_rowcount = admission_head_update_rowcount
        self.active = False
        self.info: dict[object, object] = {}
        self.new: set[object] = set()
        self.dirty: set[object] = set()
        self.deleted: set[object] = set()
        self.identity_map: dict[object, object] = {}
        self.events: list[str] = []
        self.transaction_begin_count = 0
        self.transaction_commit_count = 0
        self.transaction_rollback_count = 0
        self.manual_commit_count = 0
        self.manual_rollback_count = 0
        self.flush_count = 0
        self.added: list[object] = []
        self.application_guard_connection_calls = 0
        self.application_guard_sync_connection = object()

    @property
    def is_active(self) -> bool:
        return True

    def in_transaction(self) -> bool:
        return self.active

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect_name))

    def begin(self):
        self.transaction_begin_count += 1
        return _TransactionDouble(self)

    async def connection(self):
        self.application_guard_connection_calls += 1
        self.events.append("application_guard_connection")
        return SimpleNamespace(sync_connection=self.application_guard_sync_connection)

    async def execute(self, statement, parameters=None):
        rendered = str(statement)
        if "pg_advisory_xact_lock" in rendered:
            self.events.append("admission_advisory_lock")
            return _ScalarResult()
        if "FROM operational_writer_admission_heads" in rendered:
            self.events.append("admission_head_lock")
            return _ScalarResult(self.head)
        if "UPDATE operational_writer_admission_heads" in rendered:
            self.events.append("admission_head_cas")
            return _UpdateResult(self.admission_head_update_rowcount)
        self.events.append("business_execute")
        return _ScalarResult()

    def add(self, value) -> None:
        self.added.append(value)
        if isinstance(value, OperationalWriterAdmissionCommit):
            self.events.append("admission_append")
            return
        self.new.add(value)
        self.events.append("business_add")

    def add_all(self, values) -> None:
        for value in values:
            self.add(value)

    async def delete(self, value) -> None:
        self.deleted.add(value)
        self.events.append("business_delete")

    async def flush(self) -> None:
        self.flush_count += 1
        if self.flush_count <= 2:
            self.events.append("admission_flush")
            return
        self.events.append("business_flush")
        self.new.clear()
        self.dirty.clear()
        self.deleted.clear()

    async def scalar(self, statement, parameters=None):
        self.events.append("business_scalar")
        return None

    async def scalars(self, statement, parameters=None):
        self.events.append("business_scalars")
        return []

    async def get(self, entity, ident):
        self.events.append("business_get")
        return None

    async def refresh(self, instance) -> None:
        self.events.append("business_refresh")

    async def commit(self) -> None:
        self.manual_commit_count += 1
        self.active = False
        self.events.append("manual_session_commit")

    async def rollback(self) -> None:
        self.manual_rollback_count += 1
        self.active = False
        self.events.append("manual_session_rollback")


class _Issuer:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0
        self.events: list[str] | None = None
        self.session_to_observe: _SessionDouble | None = None
        self.observed_transaction_states: list[bool] = []

    async def issue_transaction_commit_admission(self):
        self.calls += 1
        if self.events is not None:
            self.events.append("issue_admission")
        if self.session_to_observe is not None:
            self.observed_transaction_states.append(self.session_to_observe.in_transaction())
        return self.value


class _SessionTouchingIssuer(_Issuer):
    """Adversarial test double: a real issuer must never receive this capability."""

    def __init__(self, value, *, session: _SessionDouble) -> None:
        super().__init__(value)
        self._session = session

    async def issue_transaction_commit_admission(self):
        value = await super().issue_transaction_commit_admission()
        self._session.new.add(object())
        return value


class PhysicalOperationalFailoverV1WriterTransactionEnvelopeTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id="physical-generation-0007",
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id="root-runtime-instance-0001",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.adapter_config = adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig(
            enabled=True,
            writer_admission_config=self.writer_config,
            control_role_label="webapp-fi-writer-control",
            control_policy_sha256=POLICY_SHA256,
        )
        self.adapter = adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter(
            self.adapter_config
        )

    def _admission(self, *, operation_kind: str | None = None):
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        transition = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=_Revalidator(_Evidence()),
            revalidation_id="revalidation-id-0001",
            now=NOW,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=startup,
            transition=transition,
        )
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=active,
            operation_kind=(
                operation_kind
                or admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT
            ),
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        writer_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=active,
            operation=operation,
            now=NOW + timedelta(seconds=2),
        )
        self.assertIsNotNone(writer_admission)
        assert writer_admission is not None
        return active, writer_admission

    def _head(self, state) -> OperationalWriterAdmissionHead:
        facts = adapter_module._facts(self.adapter_config)
        self.assertIsNotNone(facts)
        assert facts is not None
        values = adapter_module._state_values(
            state,
            facts=facts,
            code="test-state-invalid",
        )
        return OperationalWriterAdmissionHead(
            id=uuid4(),
            **values,
            state_sha256=adapter_module._state_sha256(
                state,
                facts=facts,
                code="test-state-invalid",
            ),
            receipt_sha256="b" * 64,
            current_commit_id=uuid4(),
            current_commit_sha256="d" * 64,
            control_boundary=OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            control_role_label=self.adapter_config.control_role_label,
            control_policy_sha256=self.adapter_config.control_policy_sha256,
            committed_at=NOW,
        )

    def _envelope(self, writer_admission):
        return subject.PhysicalOperationalFailoverV1WriterTransactionEnvelope(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig(enabled=True),
            admission_issuer=_Issuer(writer_admission),
            admission_adapter=self.adapter,
        )

    async def test_default_off_rejects_before_session_or_runtime_dependencies_are_used(self) -> None:
        state, _writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))
        envelope = subject.PhysicalOperationalFailoverV1WriterTransactionEnvelope(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig()
        )

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "ENVELOPE_DISABLED",
        ):
            async with envelope.transaction(session=session):
                self.fail("default-off envelope must not yield")

        self.assertEqual(0, session.transaction_begin_count)
        self.assertEqual([], session.events)

    async def test_admission_precedes_every_exposed_business_dml_and_commits_once(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))
        envelope = self._envelope(writer_admission)
        issuer = envelope._admission_issuer
        assert isinstance(issuer, _Issuer)
        issuer.events = session.events
        issuer.session_to_observe = session

        async with envelope.transaction(session=session) as business:
            self.assertFalse(hasattr(business, "commit"))
            self.assertFalse(hasattr(business, "rollback"))
            self.assertFalse(hasattr(business, "begin"))
            business.add(object())
            await business.flush()
            await business.execute(select(1))

        self.assertEqual(1, session.transaction_begin_count)
        self.assertEqual(1, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)
        self.assertEqual(0, session.manual_commit_count)
        self.assertEqual(1, issuer.calls)
        self.assertEqual([False], issuer.observed_transaction_states)
        self.assertLess(session.events.index("issue_admission"), session.events.index("begin"))
        self.assertLess(
            session.events.index("begin"),
            session.events.index("admission_advisory_lock"),
        )
        self.assertLess(
            session.events.index("begin"),
            session.events.index("application_guard_connection"),
        )
        self.assertLess(
            session.events.index("application_guard_connection"),
            session.events.index("admission_advisory_lock"),
        )
        self.assertLess(
            session.events.index("admission_head_cas"),
            session.events.index("business_add"),
        )
        self.assertLess(
            session.events.index("admission_head_cas"),
            session.events.index("business_execute"),
        )
        self.assertEqual("transaction_commit", writer_admission.operation.operation_kind)

    async def test_failure_in_business_block_rolls_back_exactly_once(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))

        with self.assertRaisesRegex(RuntimeError, "business failed"):
            async with self._envelope(writer_admission).transaction(session=session) as business:
                business.add(object())
                raise RuntimeError("business failed")

        self.assertEqual(1, session.transaction_begin_count)
        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertEqual(0, session.manual_rollback_count)

    async def test_external_effect_admission_is_rejected_before_adapter_or_business_facade(self) -> None:
        state, writer_admission = self._admission(
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT
        )
        session = _SessionDouble(head=self._head(state))

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "EXTERNAL_EFFECT_FORBIDDEN",
        ):
            async with self._envelope(writer_admission).transaction(session=session):
                self.fail("external-effect admission must not yield business DML")

        self.assertEqual(0, session.transaction_begin_count)
        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)
        self.assertNotIn("admission_advisory_lock", session.events)

    async def test_prior_pending_or_non_postgresql_session_is_rejected_before_begin(self) -> None:
        state, writer_admission = self._admission()
        pending = _SessionDouble(head=self._head(state))
        pending.new.add(object())

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "PENDING_MUTATION",
        ):
            async with self._envelope(writer_admission).transaction(session=pending):
                self.fail("pending session must not yield")
        self.assertEqual(0, pending.transaction_begin_count)

        prior_transaction = _SessionDouble(head=self._head(state))
        prior_transaction.active = True
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "SESSION_NOT_FRESH",
        ):
            async with self._envelope(writer_admission).transaction(session=prior_transaction):
                self.fail("session with a prior transaction must not yield")
        self.assertEqual(0, prior_transaction.transaction_begin_count)

        previously_read = _SessionDouble(head=self._head(state))
        previously_read.identity_map["prior"] = object()
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "PENDING_MUTATION",
        ):
            async with self._envelope(writer_admission).transaction(session=previously_read):
                self.fail("session with prior ORM identity state must not yield")
        self.assertEqual(0, previously_read.transaction_begin_count)

        non_postgres = _SessionDouble(head=self._head(state), dialect_name="sqlite")
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "POSTGRES_REQUIRED",
        ):
            async with self._envelope(writer_admission).transaction(session=non_postgres):
                self.fail("non-PostgreSQL session must not yield")
        self.assertEqual(0, non_postgres.transaction_begin_count)

    async def test_session_is_rechecked_after_issuer_before_database_transaction_begins(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))
        envelope = subject.PhysicalOperationalFailoverV1WriterTransactionEnvelope(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig(enabled=True),
            admission_issuer=_SessionTouchingIssuer(writer_admission, session=session),
            admission_adapter=self.adapter,
        )

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "PENDING_MUTATION",
        ):
            async with envelope.transaction(session=session):
                self.fail("issuer-side session touch must prevent begin")

        self.assertEqual(0, session.transaction_begin_count)
        self.assertNotIn("admission_advisory_lock", session.events)

    async def test_same_session_cannot_be_reused_after_a_completed_attempt(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))
        envelope = self._envelope(writer_admission)

        async with envelope.transaction(session=session):
            pass

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "SESSION_REUSED",
        ):
            async with envelope.transaction(session=session):
                self.fail("consumed session must not yield")
        self.assertEqual(1, session.transaction_begin_count)

    async def test_manual_terminal_state_is_detected_and_not_repaired_with_second_rollback(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "SESSION_TERMINAL_STATE",
        ):
            async with self._envelope(writer_admission).transaction(session=session):
                await session.commit()

        self.assertEqual(1, session.manual_commit_count)
        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(0, session.transaction_rollback_count)

    async def test_textual_transaction_control_is_not_available_through_business_facade(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(head=self._head(state))

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "BUSINESS_STATEMENT_FORBIDDEN",
        ):
            async with self._envelope(writer_admission).transaction(session=session) as business:
                await business.execute(text("COMMIT"))

        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertNotIn("business_execute", session.events)

    async def test_control_plane_rows_cannot_be_read_or_locked_through_business_facade(self) -> None:
        state, writer_admission = self._admission()
        select_session = _SessionDouble(head=self._head(state))

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "BUSINESS_STATEMENT_FORBIDDEN",
        ):
            async with self._envelope(writer_admission).transaction(session=select_session) as business:
                await business.execute(select(OperationalWriterAdmissionHead))

        self.assertNotIn("business_execute", select_session.events)
        self.assertEqual(1, select_session.transaction_rollback_count)

        # An ORM alias must not turn a control-plane select into a business
        # query.  This exercises the source/alias traversal rather than only
        # the direct DML ``statement.table`` check.
        state, writer_admission = self._admission()
        alias_session = _SessionDouble(head=self._head(state))
        head_alias = aliased(OperationalWriterAdmissionHead)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "BUSINESS_STATEMENT_FORBIDDEN",
        ):
            async with self._envelope(writer_admission).transaction(session=alias_session) as business:
                await business.execute(select(head_alias))
        self.assertNotIn("business_execute", alias_session.events)

        state, writer_admission = self._admission()
        get_session = _SessionDouble(head=self._head(state))
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "BUSINESS_CONTROL_PLANE_FORBIDDEN",
        ):
            async with self._envelope(writer_admission).transaction(session=get_session) as business:
                await business.get(OperationalWriterAdmissionHead, uuid4())
        self.assertNotIn("business_get", get_session.events)

    async def test_admission_adapter_failure_rolls_back_once_before_business_dml_is_exposed(self) -> None:
        state, writer_admission = self._admission()
        session = _SessionDouble(
            head=self._head(state),
            admission_head_update_rowcount=0,
        )

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "ADMISSION_PERSIST_FAILED",
        ):
            async with self._envelope(writer_admission).transaction(session=session):
                self.fail("failed admission persistence must not yield business DML")

        self.assertEqual(1, session.transaction_begin_count)
        self.assertEqual(0, session.transaction_commit_count)
        self.assertEqual(1, session.transaction_rollback_count)
        self.assertNotIn("business_add", session.events)

    async def test_reusing_one_issued_admission_is_rejected_on_a_fresh_session(self) -> None:
        state, writer_admission = self._admission()
        first = _SessionDouble(head=self._head(state))
        second = _SessionDouble(head=self._head(state))
        envelope = self._envelope(writer_admission)

        async with envelope.transaction(session=first):
            pass

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterTransactionEnvelopeError,
            "ADMISSION_REUSED",
        ):
            async with envelope.transaction(session=second):
                self.fail("reused V1 admission must not yield")
        self.assertEqual(0, second.transaction_begin_count)
        self.assertEqual(0, second.transaction_rollback_count)
        self.assertNotIn("admission_advisory_lock", second.events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
