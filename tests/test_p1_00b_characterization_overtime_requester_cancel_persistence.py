"""P1-00b characterization: durable persistence for overtime requester cancellation.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: overtime_requester_cancel_legal_durable_persistence

Explicit exclusions:
- No handler commit, callback, or UI proof.
- No owner notification proof.
- No queue promotion proof.
- No remote forwarding proof.
- No origin SUCCESS, policy, notification, or retry cell evidence.
- No seed or blocker closure.
- No recent-characterization reconciliation.
- No Stage closure.

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""
from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator
import unittest

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from core.server_routing import SERVER_FOREIGN
from core.services.offer_overtime_request_service import (
    cancel_by_requester,
    load_overtime_request_by_public_id,
)
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)


class _IsolatedAsyncDatabase:
    """Bounded test-local helper managing an isolated SQLite table and AsyncSession instances.

    Adapts an in-memory SQLite engine via AsyncSession.sync_session_class because
    aiosqlite is not installed. Yields actual sqlalchemy.ext.asyncio.AsyncSession
    instances bound to the isolated OfferRequest table without fake result queues
    or owner/None fallbacks.
    """

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        OfferRequest.__table__.create(bind=self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        class _BoundSyncSession(Session):
            def __init__(sub_self, *args: Any, **kwargs: Any) -> None:
                kwargs.pop("bind", None)
                kwargs.pop("binds", None)
                super().__init__(bind=self.engine, **kwargs)

        session = AsyncSession(sync_session_class=_BoundSyncSession)
        try:
            yield session
        finally:
            await session.close()

    def dispose(self) -> None:
        try:
            OfferRequest.__table__.drop(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()


class OvertimeRequesterCancelPersistenceCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize durable persistence for overtime requester cancellation across sessions."""

    def setUp(self) -> None:
        super().setUp()
        self.db = _IsolatedAsyncDatabase()
        self.addCleanup(self.db.dispose)

    async def test_legal_requester_cancel_persists_terminal_fields_across_sessions(self) -> None:
        """Verify requester cancellation persists terminal fields into SQLite across independent sessions.

        Stage: P1-00b
        Assignment: WA-P1-00B-FREEZE-20260904-001
        Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
        Scenario: overtime_requester_cancel_legal_durable_persistence
        """
        public_id = "req-overtime-cancel-71-durable"
        initial_received_at = datetime(2026, 9, 15, 10, 0, 0)
        fixed_cancellation_stamp = datetime(2026, 9, 15, 11, 22, 33)

        # 1. Create and commit minimal valid OfferRequest in initial AsyncSession, then close it.
        async with self.db.session() as session_create:
            initial_request = OfferRequest(
                request_public_id=public_id,
                request_home_server=SERVER_FOREIGN,
                request_source_server=SERVER_FOREIGN,
                request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
                offer_public_id="off-overtime-test-001",
                offer_owner_user_id=42,
                requester_user_id=71,
                requested_quantity=1,
                workflow_kind=OfferRequestWorkflow.OVERTIME,
                result_status=OfferRequestStatus.OVERTIME_QUEUED,
                received_at=initial_received_at,
                created_at=initial_received_at,
                version_id=1,
                archived=False,
            )
            session_create.add(initial_request)
            await session_create.commit()

        # 2. In a second AsyncSession, reload row by public ID before invoking cancellation,
        #    proving input is database-backed. Call unmocked cancel_by_requester, commit, and close.
        async with self.db.session() as session_cancel:
            reloaded_request = await load_overtime_request_by_public_id(
                session_cancel,
                public_id,
            )
            self.assertIsNotNone(reloaded_request)
            self.assertEqual(
                reloaded_request.result_status,
                OfferRequestStatus.OVERTIME_QUEUED,
            )
            self.assertEqual(reloaded_request.requester_user_id, 71)

            cancelled_request = await cancel_by_requester(
                session_cancel,
                reloaded_request,
                requester_user_id=71,
                now=fixed_cancellation_stamp,
                flush=True,
                promote_next=False,
            )
            self.assertIs(cancelled_request, reloaded_request)
            await session_cancel.commit()

        # 3. In a third AsyncSession, reload row by public ID and prove durable terminal fields.
        async with self.db.session() as session_verify:
            persisted_request = await load_overtime_request_by_public_id(
                session_verify,
                public_id,
            )
            self.assertIsNotNone(persisted_request)
            self.assertEqual(
                persisted_request.result_status,
                OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
            )
            self.assertEqual(
                persisted_request.decided_at,
                fixed_cancellation_stamp,
            )
            self.assertEqual(
                persisted_request.terminal_reason,
                "requester_cancelled",
            )
