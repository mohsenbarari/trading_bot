"""Opt-in, loopback-only PostgreSQL integration tests for the Gen2 envelope.

This verifies the transaction *envelope*, not the separately tested opaque
Witness/bridge issuance path.  It intentionally uses a non-authorizing local
recording adapter: fabricating an issued bridge, V1 admission, or signed Gen2
response in a PostgreSQL harness would weaken the production capability
boundary.  The test still uses a real ``AsyncSession``, a real PostgreSQL root
transaction, real flushes, and a private disposable probe table.

The target and Alembic runner are deliberately reused from the strict-writer
scratch harness.  There is no fallback to ``DATABASE_URL``,
``SYNC_DATABASE_URL``, a project volume, DNS, a provider, or a remote host.
It runs only when that harness's exact loopback-only scratch confirmation is
present.  For example::

    export V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_URL='postgresql://postgres@127.0.0.1:55433/v2wsrc_scratch_ci_20260801'
    export V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_CONFIRM='I_CONFIRM_V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_ONLY'
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \\
      tests.test_physical_wal_v2_witness_roundtrip_strict_writer_bound_transaction_envelope_postgres_integration

Do not point this at a project, staging, or production database.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import Integer, String, delete, event, insert, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncSessionTransaction,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction as transaction_adapter
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_transaction_envelope as envelope_module
from core import application_writer_transaction_envelope_guard as application_guard
from tests.test_physical_wal_v2_witness_roundtrip_strict_writer_postgres_integration import (
    MIGRATION_HEAD,
    SCRATCH_CONFIRM_ENV,
    SCRATCH_CONFIRM_VALUE,
    SCRATCH_TARGET,
    SCRATCH_URL_ENV,
    _configured_scratch_target,
    _run_alembic,
    _verify_connected_target,
)


class _EnvelopeProbeBase(DeclarativeBase):
    """Private harness metadata; it is never part of project metadata."""


class _EnvelopeProbe(_EnvelopeProbeBase):
    __tablename__ = "v2wsrc_envelope_transaction_probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)


@dataclass(frozen=True)
class _Pending:
    """Non-authorizing stand-in solely for the envelope's type gate."""

    reconciliation_identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity


class _RecordingAdapter:
    """Local test double that does actual PostgreSQL work before yield.

    It holds no bridge, V1 admission, response receipt, or signer.  Its only
    purpose is to prove the envelope starts a real root transaction and
    completes adapter flush work before the restricted business facade exists.
    """

    def __init__(
        self,
        *,
        owner: "PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopePostgresIntegrationTests",
        run_id: str,
        identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
        events: list[str],
    ) -> None:
        self._owner = owner
        self._run_id = run_id
        self._identity = identity
        self._events = events
        self.calls = 0

    async def persist_bound_writer_response(
        self,
        *,
        session: object,
        issued_bridge: object,
        issuer: object,
    ) -> _Pending:
        # The envelope must not enter its business portion until a fresh
        # actual AsyncSession root transaction exists.
        self._owner.assertIsInstance(session, AsyncSession)
        assert isinstance(session, AsyncSession)
        self._owner.assertTrue(session.in_transaction())
        self._owner.assertIsNotNone(issued_bridge)
        self._owner.assertIsNotNone(issuer)
        self.calls += 1
        self._events.append("adapter_enter")
        session.add(_EnvelopeProbe(run_id=self._run_id, stage="adapter"))
        await session.flush()
        self._events.append("adapter_flushed")
        return _Pending(reconciliation_identity=self._identity)


def _identity(label: str) -> transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
    """Make a serializable, non-secret identity for hard-fence assertions."""

    def digest(field: str) -> str:
        return hashlib.sha256(f"envelope-postgres:{label}:{field}".encode("ascii")).hexdigest()

    return transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity(
        schema="gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2",
        configuration_sha256=digest("configuration"),
        v2_base_configuration_sha256=digest("base-configuration"),
        atomic_commit_boundary="root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2",
        commit_id="v2-witness-strict-writer-g2-" + digest("commit"),
        v2_base_commit_id="v2-witness-strict-writer-" + digest("base-commit"),
        attestation_sha256=digest("attestation"),
        ir_durable_assertion_sha256=digest("ir-durable"),
        context_certificate_sha256=digest("certificate"),
        context_sha256=digest("context"),
        source_envelope_sha256=digest("source-envelope"),
        source_request_sha256=digest("source-request"),
        destination_receipt_sha256=digest("destination-receipt"),
        durable_ledger_entry_sha256=digest("durable-ledger"),
        target_recovery_evidence_sha256=digest("recovery"),
        readback_attestation_sha256=digest("readback"),
        stage_receipt_sha256=digest("stage"),
        witness_sequence=1,
        witness_ledger_entry_sha256=digest("witness-ledger"),
        witness_ledger_previous_head_sha256="0" * 64,
        witness_ledger_binding_sha256=digest("witness-binding"),
        writer_holder_site="webapp_fi",
        writer_epoch=1,
        writer_lease_id="envelope-postgres-lease-0001",
        witnessed_term_proof_sha256=digest("term-proof"),
        witness_transition_id="envelope-postgres-transition-0001",
        activation_mode="normal_fi_writer",
        activation_stream_generation_id="envelope-postgres-generation-0001",
        activation_route_artifact_sha256=digest("route"),
        activation_source_cutover_attestation_sha256=digest("cutover"),
        activation_receiver_permit_sha256=digest("receiver"),
    )


class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeScratchSafetyTests(
    unittest.TestCase
):
    """Safety checks execute in ordinary CI without opening a connection."""

    def test_project_database_url_is_never_a_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://postgres@127.0.0.1/trading_bot",
                "SYNC_DATABASE_URL": "postgresql://postgres@127.0.0.1/trading_bot",
            },
            clear=True,
        ):
            self.assertIsNone(_configured_scratch_target())

    def test_explicit_non_scratch_target_is_rejected_before_connection(self) -> None:
        with patch.dict(
            os.environ,
            {
                SCRATCH_URL_ENV: "postgresql://postgres@127.0.0.1/trading_bot",
                SCRATCH_CONFIRM_ENV: SCRATCH_CONFIRM_VALUE,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "v2wsrc_scratch"):
                _configured_scratch_target()


@unittest.skipUnless(
    SCRATCH_TARGET is not None,
    "set dedicated V2 strict-writer PostgreSQL scratch URL and exact scratch-only confirmation to run",
)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopePostgresIntegrationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Exercise envelope lifecycle against one explicitly disposable DB."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        assert SCRATCH_TARGET is not None
        cls.target = SCRATCH_TARGET
        _verify_connected_target(cls.target)
        result = _run_alembic(cls.target, "upgrade", MIGRATION_HEAD)
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            self.target.async_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": "public"}},
        )
        self.Session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self.run_id = uuid4().hex
        self.adapter_config = (
            transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig(
                enabled=True
            )
        )
        self.envelope_config = (
            envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig(
                enabled=True,
                sqlalchemy_transaction_config=self.adapter_config,
            )
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(_EnvelopeProbeBase.metadata.create_all)
            await connection.execute(delete(_EnvelopeProbe))

    async def asyncTearDown(self) -> None:
        try:
            async with self.engine.begin() as connection:
                # This private table is the sole destructive target.  The
                # migration-owned append-only evidence tables are untouched.
                await connection.run_sync(_EnvelopeProbeBase.metadata.drop_all)
        finally:
            await self.engine.dispose()

    def _envelope(self) -> envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope:
        return envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope(
            self.envelope_config
        )

    def _patched_recording_runtime(
        self,
        *,
        adapter: _RecordingAdapter,
        finalizer: object,
    ) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(
                envelope_module.transaction_adapter,
                "PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter",
                return_value=adapter,
            )
        )
        stack.enter_context(
            patch.object(
                envelope_module.transaction_adapter,
                "PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit",
                _Pending,
            )
        )
        stack.enter_context(
            patch.object(
                envelope_module.transaction_adapter,
                "finalize_pending_physical_wal_v2_witness_roundtrip_strict_writer_bound_commit",
                finalizer,
            )
        )
        return stack

    async def _probe_stages(self, run_id: str) -> list[str]:
        async with self.Session() as session:
            result = await session.scalars(
                select(_EnvelopeProbe.stage)
                .where(_EnvelopeProbe.run_id == run_id)
                .order_by(_EnvelopeProbe.id)
            )
            return list(result)

    async def test_real_async_session_engine_event_pins_sync_connection_and_owner_task(self) -> None:
        """The DB-event seam accepts only the envelope's exact AsyncSession bind.

        This deliberately uses the disposable loopback scratch database rather
        than a fake execution context: SQLAlchemy's ``before_cursor_execute``
        receives the synchronous connection underneath ``AsyncSession``.  A
        child task inherits ContextVar values, so the explicit opener-task pin
        must reject it before its SQL reaches PostgreSQL.
        """

        policy = application_guard.ApplicationWriterTransactionEnvelopeGuardPolicy(enabled=True)
        accepted_connections: list[object] = []

        def require_exact_envelope_connection(
            connection: object,
            _cursor: object,
            _statement: object,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            application_guard.require_application_writer_transaction_envelope_connection(
                policy,
                connection,
            )
            accepted_connections.append(connection)

        event.listen(
            self.engine.sync_engine,
            "before_cursor_execute",
            require_exact_envelope_connection,
        )
        try:
            async with self.Session() as session:
                transaction = await session.begin()
                lease = await application_guard.open_application_writer_transaction_envelope_guard(
                    session,
                    envelope_kind=(
                        application_guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2
                    ),
                )
                try:
                    expected_sync_connection = lease._active.sync_connection
                    self.assertIsNotNone(expected_sync_connection)
                    application_guard.require_application_writer_transaction_envelope_session(
                        policy,
                        session.sync_session,
                    )
                    await session.execute(
                        insert(_EnvelopeProbe).values(
                            run_id=f"{self.run_id}-guard-owner",
                            stage="owner",
                        )
                    )
                    self.assertIs(expected_sync_connection, accepted_connections[-1])

                    async def child_task_same_session_check() -> None:
                        application_guard.require_application_writer_transaction_envelope_session(
                            policy,
                            session.sync_session,
                        )

                    with self.assertRaisesRegex(
                        application_guard.ApplicationWriterTransactionEnvelopeGuardError,
                        "ENVELOPE_REQUIRED",
                    ):
                        await asyncio.create_task(child_task_same_session_check())

                    async def child_task_same_session_write() -> None:
                        await session.execute(
                            insert(_EnvelopeProbe).values(
                                run_id=f"{self.run_id}-guard-child",
                                stage="child",
                            )
                        )

                    with self.assertRaisesRegex(
                        application_guard.ApplicationWriterTransactionEnvelopeGuardError,
                        "ENVELOPE_REQUIRED",
                    ):
                        await asyncio.create_task(child_task_same_session_write())

                    async with self.Session() as different_session:
                        with self.assertRaisesRegex(
                            application_guard.ApplicationWriterTransactionEnvelopeGuardError,
                            "ENVELOPE_REQUIRED",
                        ):
                            await different_session.execute(select(1))

                    async with self.engine.connect() as direct_connection:
                        with self.assertRaisesRegex(
                            application_guard.ApplicationWriterTransactionEnvelopeGuardError,
                            "ENVELOPE_REQUIRED",
                        ):
                            await direct_connection.execute(select(1))
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
                    await application_guard.close_application_writer_transaction_envelope_guard(
                        lease
                    )
        finally:
            event.remove(
                self.engine.sync_engine,
                "before_cursor_execute",
                require_exact_envelope_connection,
            )

    async def test_real_fresh_root_orders_adapter_business_one_commit_then_finalizes(self) -> None:
        events: list[str] = []
        commits: list[str] = []
        identity = _identity(f"normal-{self.run_id}")
        adapter_run_id = f"{self.run_id}-normal"
        adapter = _RecordingAdapter(
            owner=self,
            run_id=adapter_run_id,
            identity=identity,
            events=events,
        )
        observation = object()
        session: AsyncSession | None = None

        def on_commit(connection: object) -> None:
            del connection
            commits.append("db_commit")
            events.append("db_commit")

        def finalize(pending: object, *, config: object) -> object:
            self.assertIsInstance(pending, _Pending)
            self.assertIs(config, self.adapter_config)
            assert session is not None
            # The commit event has already fired and the real root is closed.
            self.assertEqual(["db_commit"], commits)
            self.assertFalse(session.in_transaction())
            events.append("finalize_after_known_commit")
            return observation

        event.listen(self.engine.sync_engine, "commit", on_commit)
        try:
            finalizer = Mock(side_effect=finalize)
            with self._patched_recording_runtime(adapter=adapter, finalizer=finalizer):
                async with self.Session() as session:
                    self.assertFalse(session.in_transaction())
                    async with self._envelope().transaction(
                        session=session,
                        issued_bridge=object(),
                        issuer=object(),
                    ) as business:
                        with self.assertRaisesRegex(
                            envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
                            "OBSERVATION_UNAVAILABLE",
                        ):
                            business.verified_observation_after_known_commit()
                        events.append("business_enter")
                        business.add(_EnvelopeProbe(run_id=adapter_run_id, stage="business"))
                        await business.flush()
                        events.append("business_flushed")

                    self.assertFalse(session.in_transaction())
                    self.assertIs(observation, business.verified_observation_after_known_commit())
        finally:
            event.remove(self.engine.sync_engine, "commit", on_commit)

        self.assertEqual(1, adapter.calls)
        self.assertEqual(["db_commit"], commits)
        self.assertEqual(
            [
                "adapter_enter",
                "adapter_flushed",
                "business_enter",
                "business_flushed",
                "db_commit",
                "finalize_after_known_commit",
            ],
            events,
        )
        self.assertEqual(["adapter", "business"], await self._probe_stages(adapter_run_id))
        finalizer.assert_called_once()

    async def test_post_commit_reply_loss_hard_fences_and_never_finalizes_or_releases_observation(self) -> None:
        events: list[str] = []
        commits: list[str] = []
        identity = _identity(f"uncertain-{self.run_id}")
        adapter_run_id = f"{self.run_id}-uncertain"
        adapter = _RecordingAdapter(
            owner=self,
            run_id=adapter_run_id,
            identity=identity,
            events=events,
        )
        finalizer = Mock(return_value=object())
        business = None

        def on_commit(connection: object) -> None:
            del connection
            commits.append("db_commit")
            events.append("db_commit")

        original_commit = AsyncSessionTransaction.commit

        async def commit_then_lose_reply(transaction: AsyncSessionTransaction) -> None:
            # Deliberately model the dangerous real-world case: PostgreSQL did
            # commit, but the caller lost the return path.  The envelope must
            # not use that locally observed commit as authorization to emit an
            # observation or retry/reconcile automatically.
            await original_commit(transaction)
            raise ConnectionError("synthetic local PostgreSQL commit reply loss")

        event.listen(self.engine.sync_engine, "commit", on_commit)
        try:
            with self._patched_recording_runtime(adapter=adapter, finalizer=finalizer), patch.object(
                AsyncSessionTransaction,
                "commit",
                new=commit_then_lose_reply,
            ):
                async with self.Session() as session:
                    self.assertFalse(session.in_transaction())
                    with self.assertRaises(
                        envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError
                    ) as raised:
                        async with self._envelope().transaction(
                            session=session,
                            issued_bridge=object(),
                            issuer=object(),
                        ) as business:
                            business.add(
                                _EnvelopeProbe(run_id=adapter_run_id, stage="business")
                            )
                            await business.flush()
                    self.assertEqual(
                        "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_COMMIT_OUTCOME_UNKNOWN_HARD_FENCE",
                        raised.exception.code,
                    )
                    self.assertEqual("unknown", raised.exception.outcome)
                    self.assertTrue(raised.exception.requires_hard_fence)
                    self.assertIs(identity, raised.exception.reconciliation_identity)
                    self.assertFalse(session.in_transaction())
        finally:
            event.remove(self.engine.sync_engine, "commit", on_commit)

        self.assertEqual(1, adapter.calls)
        self.assertEqual(["db_commit"], commits)
        self.assertEqual(["adapter_enter", "adapter_flushed", "db_commit"], events)
        finalizer.assert_not_called()
        self.assertIsNotNone(business)
        assert business is not None
        with self.assertRaisesRegex(
            envelope_module.PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError,
            "OBSERVATION_UNAVAILABLE",
        ):
            business.verified_observation_after_known_commit()
        # The local server may have committed even though the caller cannot
        # know that safely.  It is forensic state only, not an observation.
        self.assertEqual(["adapter", "business"], await self._probe_stages(adapter_run_id))


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
