"""P1-00b characterization tests for delete_pending_invitation with real AsyncSession.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294
Scenario: P1-00B.API.IDENTITY.DELETE_INVITATIONS.DELETE_PENDING_INVITATION.OWNED_ACTIVE_STANDARD_REVOKE_RELEASE_REAL_ASYNCSESSION

One live outcome:
- Characterize successful owned-active STANDARD invitation path through actual
  api.routers.invitations.delete_pending_invitation handler with genuine AsyncSession.

Exclusions:
- No inventory, evidence, reconciliation, blocker, or Stage closure claim.
- Exclude list_pending_invitations, mocked success/failure cases, non-owner/invalid/used/revoked/expired
  failures, concurrency/retry/reload, origin-matrix cells, inventory/ledger/reconciliation edits,
  production/operational data, network access, deploy/DNS/Writer work, and Stage closure.

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from datetime import datetime, timezone
import unittest
from unittest import mock

from sqlalchemy import create_engine, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from api.routers.invitations import delete_pending_invitation
from core.enums import UserRole
from core.registration_contracts import InvitationDerivedState
from core.services.invitation_identity_reservation_service import (
    NormalizedInvitationIdentity,
)
from core.services.invitation_lifecycle_service import derive_invitation_state
from models.invitation import Invitation, InvitationKind
from models.invitation_identity_reservation import InvitationIdentityReservation
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


class DeletePendingInvitationRealAsyncSessionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterize delete_pending_invitation with genuine AsyncSession persistence."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _register_sqlite_functions(dbapi_connection, _):
            def _btrim(val, chars=None):
                if val is None:
                    return None
                if chars is None:
                    return val.strip()
                return val.strip(chars)

            dbapi_connection.create_function("btrim", -1, _btrim)

        Invitation.__table__.create(bind=self.engine, checkfirst=True)
        InvitationIdentityReservation.__table__.create(
            bind=self.engine, checkfirst=True
        )
        self.addCleanup(self._dispose_engine)

    def _dispose_engine(self) -> None:
        try:
            InvitationIdentityReservation.__table__.drop(
                bind=self.engine, checkfirst=True
            )
            Invitation.__table__.drop(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()

    async def test_owned_active_standard_invitation_revokes_releases_and_commits_with_real_async_session(
        self,
    ) -> None:
        """Exercise delete_pending_invitation revoking, releasing identity, and committing."""
        fixed_created_at = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
        fixed_expires_at = datetime(2028, 1, 1, 0, 0, 0)

        # 1. Persist one active STANDARD Invitation and matching reservation in session 1.
        async with real_async_session(self.engine) as session_1:
            self.assertIsInstance(session_1, AsyncSession)
            invitation = Invitation(
                id=501,
                account_name="standard_invitee",
                mobile_number="09120000501",
                token="INV-501-STANDARD",
                short_code="PEND0501",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
                created_by_id=42,
                is_used=False,
                expires_at=fixed_expires_at,
                created_at=fixed_created_at,
                sync_version=1,
            )
            reservation = InvitationIdentityReservation(
                id=1,
                invitation_id=501,
                normalized_mobile="09120000501",
                normalized_account_name="standard_invitee",
                created_at=fixed_created_at,
            )
            session_1.add(invitation)
            session_1.add(reservation)
            await session_1.commit()

        # 2. Invoke actual delete_pending_invitation in session 2 with middle-manager admin.
        admin = User(
            id=42,
            role=UserRole.MIDDLE_MANAGER,
            account_name="admin_mm",
            mobile_number="09120000042",
            full_name="Middle Manager",
            address="Tehran",
        )

        with mock.patch(
            "core.services.invitation_transition_lock_service.acquire_invitation_transition_locks",
            new_callable=mock.AsyncMock,
            return_value=None,
        ) as mock_acquire_locks:
            async with real_async_session(self.engine) as session_2:
                self.assertIsInstance(session_2, AsyncSession)
                result = await delete_pending_invitation(
                    invitation_id=501,
                    db=session_2,
                    admin=admin,
                )

        self.assertIsNone(result)
        mock_acquire_locks.assert_awaited_once_with(
            session_2,
            invitation_token="INV-501-STANDARD",
            identity=NormalizedInvitationIdentity(
                mobile_number="09120000501",
                account_name="standard_invitee",
            ),
            telegram_id=None,
        )

        # 3. Verify in independent session 3 that revoked_at persisted, state is REVOKED, and reservation was deleted.
        async with real_async_session(self.engine) as session_3:
            self.assertIsInstance(session_3, AsyncSession)
            reloaded_invitation = (
                await session_3.execute(
                    select(Invitation).where(Invitation.id == 501)
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(reloaded_invitation)
            self.assertIsNotNone(reloaded_invitation.revoked_at)
            self.assertEqual(
                derive_invitation_state(reloaded_invitation),
                InvitationDerivedState.REVOKED,
            )
            self.assertFalse(reloaded_invitation.is_used)
            self.assertEqual(reloaded_invitation.kind, InvitationKind.STANDARD)
            self.assertEqual(reloaded_invitation.role, UserRole.STANDARD)

            reloaded_reservation = (
                await session_3.execute(
                    select(InvitationIdentityReservation).where(
                        InvitationIdentityReservation.invitation_id == 501
                    )
                )
            ).scalar_one_or_none()
            self.assertIsNone(reloaded_reservation)


if __name__ == "__main__":
    unittest.main()
