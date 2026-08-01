"""Fake-session tests for the V1 PostgreSQL writer-admission transaction seam."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as subject
from core.physical_operational_failover_v1_writer_admission_postgres_contract import (
    operational_writer_admission_postgres_commit_sha256_v1,
    operational_writer_admission_postgres_receipt_sha256_v1,
)
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
POLICY_SHA256 = "c" * 64


@dataclass(frozen=True)
class _Evidence:
    cluster_id: str = "gold-trade-three-site"
    holder_site: str = "webapp_fi"
    writer_epoch: int = 7
    writer_lease_id: str = "writer-lease-73"
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
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _UpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _AsyncSessionDouble:
    """Captures statement order without opening a real database."""

    def __init__(self, *, head, update_rowcount: int = 1, duplicate_receipt: bool = False) -> None:
        self.head = head
        self.update_rowcount = update_rowcount
        self.duplicate_receipt = duplicate_receipt
        self.events: list[str] = []
        self.statements = []
        self.added = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.new = set()
        self.dirty = set()
        self.deleted = set()

    def in_transaction(self):
        return True

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, statement):
        self.statements.append(statement)
        rendered = str(statement)
        if "pg_advisory_xact_lock" in rendered:
            self.events.append("advisory_lock")
            return _ScalarResult(None)
        if "FROM operational_writer_admission_heads" in rendered:
            self.events.append("head_for_update")
            return _ScalarResult(self.head)
        if "UPDATE operational_writer_admission_heads" in rendered:
            self.events.append("head_cas")
            return _UpdateResult(self.update_rowcount)
        raise AssertionError(f"unexpected statement: {rendered}")

    def add(self, value) -> None:
        self.events.append("append_commit")
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1
        self.events.append("flush")
        if self.duplicate_receipt and self.flush_count == 1:
            raise IntegrityError("insert", {}, RuntimeError("duplicate receipt"))

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def close(self) -> None:
        self.close_count += 1


class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionTests(
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
        self.config = subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig(
            enabled=True,
            writer_admission_config=self.writer_config,
            control_role_label="webapp-fi-writer-control",
            control_policy_sha256=POLICY_SHA256,
        )
        self.adapter = subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter(
            self.config
        )

    def _active_and_admission(self):
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        revalidation = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=_Revalidator(_Evidence()),
            revalidation_id="revalidation-id-0001",
            now=NOW,
        )
        self.assertIsNotNone(revalidation)
        assert revalidation is not None
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=startup,
            transition=revalidation,
        )
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
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
        facts = subject._facts(self.config)
        self.assertIsNotNone(facts)
        assert facts is not None
        values = subject._state_values(
            state,
            facts=facts,
            code="test-state-invalid",
        )
        return OperationalWriterAdmissionHead(
            id=uuid4(),
            **values,
            state_sha256=subject._state_sha256(state, facts=facts, code="test-state-invalid"),
            receipt_sha256="b" * 64,
            current_commit_id=uuid4(),
            current_commit_sha256="d" * 64,
            control_boundary=OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            control_role_label=self.config.control_role_label,
            control_policy_sha256=self.config.control_policy_sha256,
            committed_at=NOW,
        )

    async def _fresh_parent_receipt(self):
        active, writer_admission = self._active_and_admission()
        receipt = await self.adapter.persist_writer_admission(
            session=_AsyncSessionDouble(head=self._head(active)),
            writer_admission=writer_admission,
        )
        self.assertIsNotNone(receipt)
        assert receipt is not None
        return receipt

    async def test_active_transaction_locks_head_appends_receipt_and_cas_advances_it(self) -> None:
        active, writer_admission = self._active_and_admission()
        head = self._head(active)
        session = _AsyncSessionDouble(head=head)

        result = await self.adapter.persist_writer_admission(
            session=session,
            writer_admission=writer_admission,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(active.revision, result.prior_revision)
        self.assertEqual(active.revision + 1, result.next_revision)
        self.assertEqual(
            ["advisory_lock", "head_for_update", "append_commit", "flush", "head_cas", "flush"],
            session.events,
        )
        self.assertEqual(2, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        self.assertEqual(0, session.close_count)
        self.assertEqual([OperationalWriterAdmissionCommit], [type(item) for item in session.added])
        commit = session.added[0]
        self.assertEqual("writer_admission", commit.transition_kind)
        self.assertEqual(writer_admission.term.evidence_id, commit.evidence_id)
        self.assertEqual(writer_admission.term.revalidation_id, commit.revalidation_id)
        self.assertEqual(writer_admission.admitted_at, commit.clock_floor)
        expected_receipt_sha256 = operational_writer_admission_postgres_receipt_sha256_v1(
            binding={
                "cluster_id": self.binding.cluster_id,
                "local_site": self.binding.local_site,
                "release_sha": self.binding.release_sha,
                "generation_id": self.binding.generation_id,
            },
            transition_kind="writer_admission",
            prior_revision=active.revision,
            prior_fence_generation=active.fence_generation,
            prior_state_sha256=head.state_sha256,
            previous_commit_sha256=head.current_commit_sha256,
            next_state_sha256=commit.state_sha256,
            next_fence_generation=commit.next_fence_generation,
            operation={
                "operation_kind": writer_admission.operation.operation_kind,
                "opened_state_revision": writer_admission.operation.opened_state_revision,
                "fence_generation": writer_admission.operation.fence_generation,
                "evidence_id": writer_admission.operation.evidence_id,
                "writer_epoch": writer_admission.operation.writer_epoch,
                "writer_lease_id": writer_admission.operation.writer_lease_id,
                "opened_at": writer_admission.operation.opened_at,
                "admitted_at": writer_admission.admitted_at,
            },
            control={
                "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
                "control_role_label": self.config.control_role_label,
                "control_policy_sha256": self.config.control_policy_sha256,
            },
            committed_at=writer_admission.admitted_at,
        )
        self.assertEqual(expected_receipt_sha256, commit.receipt_sha256)
        self.assertEqual(
            operational_writer_admission_postgres_commit_sha256_v1(
                commit_id=commit.id,
                head_id=head.id,
                receipt_sha256=commit.receipt_sha256,
                previous_commit_sha256=head.current_commit_sha256,
                state_sha256=commit.state_sha256,
                committed_at=writer_admission.admitted_at,
            ),
            commit.commit_sha256,
        )

        rendered = [str(statement) for statement in session.statements]
        self.assertIn("pg_advisory_xact_lock", rendered[0])
        self.assertIn("FOR UPDATE", rendered[1])
        self.assertIn("operational_writer_admission_heads", rendered[1])
        self.assertIn("UPDATE operational_writer_admission_heads", rendered[2])
        compiled = str(
            session.statements[2].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        self.assertIn("revision", compiled)
        self.assertIn("fence_generation", compiled)
        self.assertIn("state_sha256", compiled)
        self.assertIn("current_commit_sha256", compiled)

    async def test_parent_commit_receipt_is_one_time_verified_capability_with_exact_projection(self) -> None:
        receipt = await self._fresh_parent_receipt()

        projection = (
            subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                receipt,
                config=self.config,
            )
        )

        self.assertIsInstance(
            projection,
            subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection,
        )
        self.assertEqual(receipt.commit_id, projection.commit_id)
        self.assertEqual(receipt.commit_sha256, projection.commit_sha256)
        self.assertEqual(receipt.receipt_sha256, projection.receipt_sha256)
        self.assertEqual(self.binding.cluster_id, projection.cluster_id)
        self.assertEqual(self.binding.local_site, projection.local_site)
        self.assertEqual(self.binding.release_sha, projection.release_sha)
        self.assertEqual(self.binding.generation_id, projection.generation_id)
        self.assertEqual(receipt.writer_epoch, projection.writer_epoch)
        self.assertEqual(receipt.writer_lease_id, projection.writer_lease_id)
        self.assertEqual(receipt.evidence_id, projection.evidence_id)
        self.assertEqual(receipt.revalidation_id, projection.revalidation_id)
        self.assertEqual(receipt.admitted_at, projection.admitted_at)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
            "COMMIT_RECEIPT_REPLAYED",
        ):
            subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                receipt,
                config=self.config,
            )

    async def test_parent_commit_receipt_rejects_serialization_forgery_tampering_and_config_mismatch(self) -> None:
        with self.subTest("serialization-and-manual-construction"):
            receipt = await self._fresh_parent_receipt()
            with self.assertRaisesRegex(TypeError, "COMMIT_RECEIPT_SERIALIZATION_FORBIDDEN"):
                pickle.dumps(receipt)
            with self.assertRaisesRegex(TypeError, "COMMIT_RECEIPT_CONSTRUCTION_FORBIDDEN"):
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt(
                    commit_id=receipt.commit_id,
                    commit_sha256=receipt.commit_sha256,
                    receipt_sha256=receipt.receipt_sha256,
                    cluster_id=receipt.cluster_id,
                    local_site=receipt.local_site,
                    release_sha=receipt.release_sha,
                    generation_id=receipt.generation_id,
                    prior_revision=receipt.prior_revision,
                    next_revision=receipt.next_revision,
                    fence_generation=receipt.fence_generation,
                    writer_epoch=receipt.writer_epoch,
                    writer_lease_id=receipt.writer_lease_id,
                    evidence_id=receipt.evidence_id,
                    revalidation_id=receipt.revalidation_id,
                    admitted_at=receipt.admitted_at,
                    capability=object(),
                )
            forged = object.__new__(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt
            )
            projection_type = (
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection
            )
            for field_name in projection_type.__dataclass_fields__:
                object.__setattr__(forged, field_name, getattr(receipt, field_name))
            object.__setattr__(forged, "_capability", subject._COMMIT_RECEIPT_CAPABILITY)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "COMMIT_RECEIPT_CAPABILITY_REQUIRED",
            ):
                subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                    forged,
                    config=self.config,
                )

        with self.subTest("config-mismatch-does-not-consume"):
            receipt = await self._fresh_parent_receipt()
            wrong_writer_config = replace(
                self.writer_config,
                maximum_evidence_age_seconds=self.writer_config.maximum_evidence_age_seconds - 1,
            )
            wrong_config = replace(self.config, writer_admission_config=wrong_writer_config)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "COMMIT_RECEIPT_CONFIG_MISMATCH",
            ):
                subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                    receipt,
                    config=wrong_config,
                )
            self.assertEqual(
                receipt.commit_sha256,
                subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                    receipt,
                    config=self.config,
                ).commit_sha256,
            )

        with self.subTest("tampering"):
            receipt = await self._fresh_parent_receipt()
            object.__setattr__(receipt, "writer_epoch", receipt.writer_epoch + 1)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "COMMIT_RECEIPT_TAMPERED",
            ):
                subject.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                    receipt,
                    config=self.config,
                )

    async def test_stale_head_evidence_fails_before_receipt_insert(self) -> None:
        active, writer_admission = self._active_and_admission()
        head = self._head(active)
        head.evidence_id = "wrong-evidence-0001"
        session = _AsyncSessionDouble(head=head)

        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
            "HEAD_STALE_OR_MISMATCH",
        ):
            await self.adapter.persist_writer_admission(
                session=session,
                writer_admission=writer_admission,
            )
        self.assertEqual(["advisory_lock", "head_for_update"], session.events)
        self.assertEqual([], session.added)

    async def test_receipt_replay_and_cas_race_fail_closed_without_transaction_ownership(self) -> None:
        active, writer_admission = self._active_and_admission()
        with self.subTest("receipt-conflict"):
            duplicate = _AsyncSessionDouble(head=self._head(active), duplicate_receipt=True)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "REPLAY_OR_RECEIPT_CONFLICT",
            ):
                await self.adapter.persist_writer_admission(
                    session=duplicate,
                    writer_admission=writer_admission,
                )
            self.assertEqual(0, duplicate.commit_count)
            self.assertEqual(0, duplicate.rollback_count)
            self.assertEqual(0, duplicate.close_count)

        with self.subTest("cas-race"):
            raced = _AsyncSessionDouble(head=self._head(active), update_rowcount=0)
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "HEAD_CAS_RACED",
            ):
                await self.adapter.persist_writer_admission(
                    session=raced,
                    writer_admission=writer_admission,
                )
            self.assertEqual(0, raced.commit_count)
            self.assertEqual(0, raced.rollback_count)
            self.assertEqual(0, raced.close_count)

    async def test_default_off_never_inspects_a_session_or_capability(self) -> None:
        adapter = subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter(
            subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig()
        )
        self.assertIsNone(
            await adapter.persist_writer_admission(
                session=object(),  # type: ignore[arg-type]
                writer_admission=object(),  # type: ignore[arg-type]
            )
        )

    async def test_nonpostgres_or_pending_unguarded_mutation_fails_before_lock(self) -> None:
        active, writer_admission = self._active_and_admission()
        with self.subTest("non-postgresql"):
            session = _AsyncSessionDouble(head=self._head(active))
            session.get_bind = lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "POSTGRES_REQUIRED",
            ):
                await self.adapter.persist_writer_admission(
                    session=session,
                    writer_admission=writer_admission,
                )
            self.assertEqual([], session.events)

        with self.subTest("pending-mutation"):
            session = _AsyncSessionDouble(head=self._head(active))
            session.dirty.add(object())
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "UNGUARDED_PENDING_MUTATION",
            ):
                await self.adapter.persist_writer_admission(
                    session=session,
                    writer_admission=writer_admission,
                )
            self.assertEqual([], session.events)

    def test_adapter_has_no_engine_worker_provider_or_peer_transport_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "physical_operational_failover_v1_writer_admission_sqlalchemy_transaction.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "create_async_engine",
            "async_sessionmaker",
            "core.db",
            "get_db",
            "boto",
            "httpx",
            "requests",
            "aiohttp",
            "socket",
            "urllib",
            "subprocess",
            "ssh",
            "scp",
            "rsync",
            "physical_wal_v2",
            "physical_full_matrix_v4",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
