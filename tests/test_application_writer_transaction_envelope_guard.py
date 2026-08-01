"""Focused default-off tests for the application V1/Gen2 envelope seam."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, text
from sqlalchemy.orm import Session

from core import application_writer_transaction_envelope_guard as guard
from core import db
from core.application_writer_term import ValidatedWriterTerm


class _EnvelopeSession:
    """AsyncSession-shaped identity double for the pure guard boundary."""

    def __init__(self, *, active_transaction: bool = True) -> None:
        self.info: dict[object, object] = {}
        # SQLAlchemy event handlers receive the paired synchronous Session,
        # whereas the envelopes open the asynchronous Session.  Keep their
        # ``info`` identity shared exactly as SQLAlchemy does.
        self.sync_session = SimpleNamespace(info=self.info)
        self.active_transaction = active_transaction
        self.sync_connection = object()
        self.connection_calls = 0

    def in_transaction(self) -> bool:
        return self.active_transaction

    async def connection(self) -> object:
        self.connection_calls += 1
        return SimpleNamespace(sync_connection=self.sync_connection)


def _enabled_policy() -> guard.ApplicationWriterTransactionEnvelopeGuardPolicy:
    return guard.ApplicationWriterTransactionEnvelopeGuardPolicy(enabled=True)


def _enabled_runtime_settings(**overrides: object) -> SimpleNamespace:
    """A complete static activation shape; it never opens the lease file."""

    values: dict[str, object] = {
        "application_writer_transaction_envelope_guard_enforced": True,
        "single_writer_runtime_enabled": True,
        "application_writer_term_enforced": True,
        "application_writer_term_local_site": "webapp_fi",
        "application_writer_term_lease_file": "/run/trading-bot/writer-term.json",
        "application_writer_term_safety_margin_seconds": 5,
        "application_writer_term_max_lease_duration_seconds": 90,
        # Application schema creation is intentionally not part of a guarded
        # writer runtime. Alembic/manual control planes stay separate.
        "database_schema_bootstrap_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _active_writer_term() -> ValidatedWriterTerm:
    issued_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    return ValidatedWriterTerm(
        holder_site="webapp_fi",
        writer_epoch=11,
        lease_id="writer-lease-73",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=60),
        witness_transition_id="witness-transition-11",
    )


class _SyncSessionBackedAsyncEnvelope:
    """Small test-only AsyncSession bridge around an exact sync Session."""

    def __init__(self, sync_session: Session) -> None:
        self.sync_session = sync_session
        self.info = sync_session.info

    def in_transaction(self) -> bool:
        return self.sync_session.in_transaction()

    async def connection(self) -> object:
        return SimpleNamespace(sync_connection=self.sync_session.connection())


class ApplicationWriterTransactionEnvelopeGuardSettingsTests(unittest.TestCase):
    def test_default_off_projection_reads_only_its_dedicated_flag(self) -> None:
        class DisabledSettings:
            application_writer_transaction_envelope_guard_enforced = False

            def __getattr__(self, name: str) -> object:
                raise AssertionError(f"disabled projection unexpectedly read {name}")

        self.assertEqual(
            guard.policy_from_settings(DisabledSettings()),
            guard.ApplicationWriterTransactionEnvelopeGuardPolicy(),
        )

    def test_enabled_projection_rejects_malformed_or_partial_runtime_settings(self) -> None:
        cases = (
            (
                SimpleNamespace(
                    application_writer_transaction_envelope_guard_enforced="true"
                ),
                "SETTINGS_ENABLED_INVALID",
            ),
            (
                SimpleNamespace(application_writer_transaction_envelope_guard_enforced=True),
                "SETTINGS_SINGLE_WRITER_RUNTIME_ENABLED_REQUIRED",
            ),
            (
                _enabled_runtime_settings(application_writer_term_enforced=False),
                "WRITER_TERM_ENFORCEMENT_REQUIRED",
            ),
            (
                _enabled_runtime_settings(application_writer_term_local_site="iran"),
                "WRITER_TERM_LOCAL_SITE_INVALID",
            ),
            (
                _enabled_runtime_settings(application_writer_term_lease_file=None),
                "WRITER_TERM_LEASE_FILE_REQUIRED",
            ),
            (
                _enabled_runtime_settings(application_writer_term_lease_file="relative-term.json"),
                "WRITER_TERM_LEASE_FILE_INVALID",
            ),
            (
                _enabled_runtime_settings(application_writer_term_safety_margin_seconds=True),
                "WRITER_TERM_SAFETY_MARGIN_INVALID",
            ),
            (
                _enabled_runtime_settings(application_writer_term_max_lease_duration_seconds=5),
                "WRITER_TERM_MAX_LEASE_DURATION_INVALID",
            ),
            (
                _enabled_runtime_settings(database_schema_bootstrap_enabled=True),
                "DATABASE_SCHEMA_BOOTSTRAP_MUST_BE_DISABLED",
            ),
        )
        for settings, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                code,
            ):
                guard.policy_from_settings(settings)

    def test_enabled_projection_requires_the_complete_three_site_writer_shape(self) -> None:
        self.assertEqual(
            guard.policy_from_settings(_enabled_runtime_settings()),
            guard.ApplicationWriterTransactionEnvelopeGuardPolicy(enabled=True),
        )

    def test_canonical_init_db_preflights_enabled_guard_before_lease_or_engine(self) -> None:
        with patch.object(
            db.settings,
            "application_writer_transaction_envelope_guard_enforced",
            True,
        ), patch.object(db, "require_application_writer_term") as require_term, self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "SINGLE_WRITER_RUNTIME_REQUIRED",
        ):
            # ``init_db`` is async; this focused regression calls its coroutine
            # in a fresh loop and proves malformed activation is rejected
            # before the lease or application engine are touched.
            asyncio.run(db.init_db())

        require_term.assert_not_called()


class ApplicationWriterTransactionEnvelopeGuardConfiguredRuntimeTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_explicit_config_allows_only_exact_term_and_envelope_bound_app_dml(self) -> None:
        """Exercise the actual canonical session + engine event lifecycle.

        The SQLite engine is a disposable local test double.  The configured
        policy is not monkeypatched: the same Settings projection used by the
        production ``core.db`` hooks is enabled, while only the term reader is
        replaced with a non-secret validated test term.
        """

        engine = create_engine("sqlite://")
        table = Table(
            "configured_envelope_guard",
            MetaData(),
            Column("id", Integer, primary_key=True),
        )
        table.create(engine)
        db.register_application_writer_term_engine_guard(engine)
        configured = _enabled_runtime_settings()
        setting_names = tuple(vars(configured))
        try:
            with ExitStack() as stack:
                for name in setting_names:
                    stack.enter_context(
                        patch.object(db.settings, name, getattr(configured, name))
                    )
                with patch.object(
                    db,
                    "require_application_writer_term",
                    return_value=_active_writer_term(),
                ) as require_term:
                    # A raw canonical Session sees the registered Core-DML
                    # hook and is refused before SQLite observes the insert.
                    raw_session = Session(engine)
                    try:
                        with self.assertRaisesRegex(
                            guard.ApplicationWriterTransactionEnvelopeGuardError,
                            "ENVELOPE_REQUIRED",
                        ):
                            raw_session.execute(insert(table).values(id=1))
                    finally:
                        raw_session.rollback()
                        raw_session.close()

                    # A reviewed envelope marker must bind both the exact
                    # synchronous Session and the exact connection.  It can
                    # now pass the Session and engine hooks and commit DML.
                    guarded_session = Session(engine)
                    guarded_session.begin()
                    envelope_session = _SyncSessionBackedAsyncEnvelope(guarded_session)
                    lease = await guard.open_application_writer_transaction_envelope_guard(
                        envelope_session,
                        envelope_kind=guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1,
                    )
                    try:
                        guarded_session.execute(insert(table).values(id=1))
                        guarded_session.commit()
                    finally:
                        if guarded_session.in_transaction():
                            guarded_session.rollback()
                        await guard.close_application_writer_transaction_envelope_guard(lease)
                        guarded_session.close()

                    # A direct connection is neither the marked Session nor
                    # its exact captured connection, even inside the same
                    # configured process.
                    with self.assertRaisesRegex(
                        guard.ApplicationWriterTransactionEnvelopeGuardError,
                        "ENVELOPE_REQUIRED",
                    ):
                        with engine.begin() as raw_connection:
                            raw_connection.execute(insert(table).values(id=2))

                    self.assertGreaterEqual(require_term.call_count, 4)

            with engine.connect() as connection:
                self.assertEqual(
                    [(1,)],
                    connection.execute(text("SELECT id FROM configured_envelope_guard")).all(),
                )
        finally:
            engine.dispose()


class ApplicationWriterTransactionEnvelopeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_owner_task_session_and_connection_are_required(self) -> None:
        session = _EnvelopeSession()
        policy = _enabled_policy()
        lease = await guard.open_application_writer_transaction_envelope_guard(
            session,
            envelope_kind=guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1,
        )
        try:
            guard.require_application_writer_transaction_envelope_session(policy, session)
            guard.require_application_writer_transaction_envelope_session(
                policy,
                session.sync_session,
            )
            guard.require_application_writer_transaction_envelope_connection(
                policy,
                session.sync_connection,
            )

            with self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                "ENVELOPE_REQUIRED",
            ):
                guard.require_application_writer_transaction_envelope_session(
                    policy,
                    _EnvelopeSession(),
                )
            with self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                "ENVELOPE_REQUIRED",
            ):
                guard.require_application_writer_transaction_envelope_connection(policy, object())

            async def spawned_same_session_write_check() -> None:
                # ContextVars are copied into child tasks.  The opener task
                # identity is the second required pin, so this cannot reuse
                # the parent's active Session proof.
                guard.require_application_writer_transaction_envelope_session(policy, session)

            with self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                "ENVELOPE_REQUIRED",
            ):
                await asyncio.create_task(spawned_same_session_write_check())

            async def spawned_same_session_close_attempt() -> None:
                await guard.close_application_writer_transaction_envelope_guard(lease)

            with self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                "LEASE_INVALID",
            ):
                await asyncio.create_task(spawned_same_session_close_attempt())
            # The rejected child cannot clear its parent's session marker.
            guard.require_application_writer_transaction_envelope_session(policy, session)
        finally:
            await guard.close_application_writer_transaction_envelope_guard(lease)

        self.assertEqual(1, session.connection_calls)
        with self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "ENVELOPE_REQUIRED",
        ):
            guard.require_application_writer_transaction_envelope_session(policy, session)

    async def test_closed_or_forged_marker_never_authorizes_a_raw_session(self) -> None:
        policy = _enabled_policy()
        raw = _EnvelopeSession()
        raw.info["application_writer_transaction_envelope_guard"] = object()
        with self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "ENVELOPE_REQUIRED",
        ):
            guard.require_application_writer_transaction_envelope_session(policy, raw)

        with self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "ROOT_TRANSACTION_REQUIRED",
        ):
            await guard.open_application_writer_transaction_envelope_guard(
                _EnvelopeSession(active_transaction=False),
                envelope_kind=guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2,
            )

    async def test_db_api_and_worker_guards_allow_only_the_active_exact_registration(self) -> None:
        session = _EnvelopeSession()
        policy = _enabled_policy()
        table = Table("application_envelope_guard", MetaData(), Column("id", Integer))
        # Core/ORM event hooks receive this exact paired sync Session, not a
        # look-alike.  This verifies the intended AsyncSession event bridge.
        state = SimpleNamespace(
            session=session.sync_session,
            statement=insert(table).values(id=1),
        )
        context = SimpleNamespace(execution_options={})
        lease = await guard.open_application_writer_transaction_envelope_guard(
            session,
            envelope_kind=guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2,
        )
        try:
            with patch.object(
                db,
                "application_writer_transaction_envelope_guard_policy",
                return_value=policy,
            ), patch.object(db, "require_application_writer_term") as require_term, patch.object(
                db.settings,
                "application_writer_term_enforced",
                True,
            ):
                # API dependency/session Core DML and a worker-style direct
                # SQL callback both observe the same exact root connection.
                db._enforce_application_writer_term_for_core_dml(state)
                db._enforce_application_writer_term_before_cursor_execute(
                    session.sync_connection,
                    None,
                    "UPDATE application_envelope_guard SET id = 2",
                    None,
                    context,
                    False,
                )
            self.assertEqual(2, require_term.call_count)
        finally:
            await guard.close_application_writer_transaction_envelope_guard(lease)


class ApplicationWriterTransactionEnvelopeGuardOrderingTests(unittest.TestCase):
    def test_api_session_core_dml_checks_term_before_rejecting_raw_envelope_bypass(self) -> None:
        table = Table("application_envelope_api_order", MetaData(), Column("id", Integer))
        session = SimpleNamespace(info={})
        calls: list[str] = []

        def require_term() -> None:
            calls.append("term")

        def reject_raw_envelope(policy: object, target: object) -> None:
            self.assertIsInstance(policy, guard.ApplicationWriterTransactionEnvelopeGuardPolicy)
            self.assertIs(target, session)
            calls.append("envelope")
            raise guard.ApplicationWriterTransactionEnvelopeGuardError(
                "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ENVELOPE_REQUIRED"
            )

        with patch.object(
            db,
            "application_writer_transaction_envelope_guard_policy",
            return_value=_enabled_policy(),
        ), patch.object(db, "require_application_writer_term", side_effect=require_term), patch.object(
            db,
            "require_application_writer_transaction_envelope_session",
            side_effect=reject_raw_envelope,
        ), self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "ENVELOPE_REQUIRED",
        ):
            db._enforce_application_writer_term_for_core_dml(
                SimpleNamespace(session=session, statement=insert(table).values(id=1))
            )

        self.assertEqual(["term", "envelope"], calls)

    def test_worker_direct_sql_checks_term_before_rejecting_cross_connection_bypass(self) -> None:
        calls: list[str] = []
        connection = object()

        def require_term() -> None:
            calls.append("term")

        def reject_cross_connection(policy: object, target: object) -> None:
            self.assertIsInstance(policy, guard.ApplicationWriterTransactionEnvelopeGuardPolicy)
            self.assertIs(target, connection)
            calls.append("envelope")
            raise guard.ApplicationWriterTransactionEnvelopeGuardError(
                "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ENVELOPE_REQUIRED"
            )

        with patch.object(
            db.settings,
            "application_writer_term_enforced",
            True,
        ), patch.object(
            db,
            "application_writer_transaction_envelope_guard_policy",
            return_value=_enabled_policy(),
        ), patch.object(db, "require_application_writer_term", side_effect=require_term), patch.object(
            db,
            "require_application_writer_transaction_envelope_connection",
            side_effect=reject_cross_connection,
        ), self.assertRaisesRegex(
            guard.ApplicationWriterTransactionEnvelopeGuardError,
            "ENVELOPE_REQUIRED",
        ):
            db._enforce_application_writer_term_before_cursor_execute(
                connection,
                None,
                "DELETE FROM application_envelope_worker_order",
                None,
                SimpleNamespace(execution_options={}),
                False,
            )

        self.assertEqual(["term", "envelope"], calls)

    def test_real_worker_engine_dml_cannot_bypass_the_enabled_envelope_gate(self) -> None:
        """A worker-style direct application-engine write is refused pre-SQL.

        The broad application migration deliberately does not happen here,
        but this verifies the actual ``before_cursor_execute`` registration
        rather than only invoking its callback as a unit helper.
        """

        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE application_envelope_real_worker "
                    "(id INTEGER PRIMARY KEY)"
                )
            )
        db.register_application_writer_term_engine_guard(engine)
        calls: list[str] = []
        original_require_envelope = (
            db.require_application_writer_transaction_envelope_connection
        )

        def require_term() -> None:
            calls.append("term")

        def require_envelope(policy: object, connection: object) -> None:
            calls.append("envelope")
            original_require_envelope(policy, connection)

        try:
            with patch.object(
                db.settings,
                "application_writer_term_enforced",
                True,
            ), patch.object(
                db,
                "application_writer_transaction_envelope_guard_policy",
                return_value=_enabled_policy(),
            ), patch.object(
                db,
                "require_application_writer_term",
                side_effect=require_term,
            ), patch.object(
                db,
                "require_application_writer_transaction_envelope_connection",
                side_effect=require_envelope,
            ), self.assertRaisesRegex(
                guard.ApplicationWriterTransactionEnvelopeGuardError,
                "ENVELOPE_REQUIRED",
            ):
                with engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO application_envelope_real_worker (id) VALUES (1)")
                    )

            self.assertEqual(["term", "envelope"], calls)
            with engine.connect() as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        text("SELECT COUNT(*) FROM application_envelope_real_worker")
                    ).scalar_one(),
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
