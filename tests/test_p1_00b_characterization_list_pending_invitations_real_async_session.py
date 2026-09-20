"""P1-00b characterization tests for list_pending_invitations with real AsyncSession.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266
Scenario: P1-00B.API.IDENTITY.GET_INVITATIONS.LIST_PENDING_INVITATIONS.ACTIVE_STANDARD_SMS_REAL_ASYNCSESSION

One live outcome:
- Characterize successful non-super-admin owner-scoped outcome for active STANDARD invitation
  with ACCEPTED persisted SMS status through genuine AsyncSession.

Exclusions:
- delete_pending_invitation lock/revoke/release/commit lifecycle is explicitly excluded.
- No inventory, evidence, reconciliation, blocker, or Stage closure claim.
- Exclude failure paths, super-admin visibility, multiple-row ordering or limit scenarios,
  missing-SMS fallback, broader persistence/retry, subprocess/reload, origin-matrix claims,
  documentation/inventory/ledger edits, production/operational data, network access,
  deploy/DNS/Writer work, and Stage closure.

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

from datetime import datetime
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from api.routers.invitations import list_pending_invitations
from core.enums import UserRole
from core.registration_contracts import (
    InvitationDerivedState,
    InvitationSMSStatus,
)
from models.invitation import Invitation, InvitationKind
from models.invitation_sms_delivery import InvitationSMSDelivery
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


class ListPendingInvitationsRealAsyncSessionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterize list_pending_invitations with genuine AsyncSession persistence."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Invitation.__table__.create(bind=self.engine, checkfirst=True)
        InvitationSMSDelivery.__table__.create(bind=self.engine, checkfirst=True)
        self.addCleanup(self._dispose_engine)

    def _dispose_engine(self) -> None:
        try:
            InvitationSMSDelivery.__table__.drop(bind=self.engine, checkfirst=True)
            Invitation.__table__.drop(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()

    async def test_owned_active_standard_invitation_returns_sanitized_sms_projection_with_real_async_session(
        self,
    ) -> None:
        """Exercise owner-scoped list_pending_invitations returning sanitized response with real AsyncSession."""
        fixed_now = datetime(2026, 9, 20, 10, 0, 0)
        fixed_created_at = datetime(2026, 9, 20, 9, 0, 0)
        fixed_expires_at = datetime(2026, 9, 22, 10, 0, 0)

        # 1. Persist active STANDARD invitation and ACCEPTED SMS delivery in session 1, then commit and close.
        async with real_async_session(self.engine) as session_1:
            self.assertIsInstance(session_1, AsyncSession)
            invitation = Invitation(
                id=501,
                account_name="pending_standard",
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
            sms_delivery = InvitationSMSDelivery(
                id=1,
                invitation_id=501,
                status=InvitationSMSStatus.ACCEPTED.value,
                attempt_count=1,
                created_at=fixed_created_at,
            )
            session_1.add(invitation)
            session_1.add(sms_delivery)
            await session_1.commit()

        # 2. Call list_pending_invitations with middle-manager admin in second independent AsyncSession.
        admin = User(
            id=42,
            role=UserRole.MIDDLE_MANAGER,
            account_name="admin_mm",
            mobile_number="09120000042",
            full_name="Middle Manager",
            address="Tehran",
        )

        with (
            mock.patch(
                "api.routers.invitations.utc_now_naive", return_value=fixed_now
            ),
            mock.patch(
                "core.services.invitation_lifecycle_service.utc_now_naive",
                return_value=fixed_now,
            ),
            mock.patch(
                "api.routers.invitations.settings.bot_username", "test_bot"
            ),
            mock.patch(
                "core.invitation_contract_service.public_webapp_url_for_links",
                return_value="https://frontend.test",
            ),
        ):
            async with real_async_session(self.engine) as session_2:
                self.assertIsInstance(session_2, AsyncSession)
                items = await list_pending_invitations(db=session_2, admin=admin)

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsInstance(item, dict)

        expected_keys = {
            "id",
            "account_name",
            "mobile_number",
            "role",
            "short_code",
            "bot_link",
            "web_link",
            "web_short_link",
            "short_link",
            "bot_available",
            "web_available",
            "state",
            "kind",
            "sms_status",
            "expires_at",
            "created_at",
            "created_by_id",
        }
        self.assertEqual(set(item.keys()), expected_keys)

        excluded_keys = {
            "token",
            "is_used",
            "revoked_at",
            "registered_user_id",
            "completed_at",
            "completed_via",
            "sync_version",
            "attempt_count",
            "claimed_at",
        }
        for key in excluded_keys:
            self.assertNotIn(key, item)

        self.assertEqual(item["id"], 501)
        self.assertEqual(item["account_name"], "pending_standard")
        self.assertEqual(item["mobile_number"], "09120000501")
        self.assertEqual(item["role"], UserRole.STANDARD)
        self.assertEqual(item["short_code"], "PEND0501")
        self.assertEqual(
            item["bot_link"],
            "https://t.me/test_bot?start=INV-501-STANDARD",
        )
        self.assertEqual(item["web_link"], "https://frontend.test/i/PEND0501")
        self.assertEqual(item["web_short_link"], "https://frontend.test/i/PEND0501")
        self.assertEqual(item["short_link"], "https://frontend.test/i/PEND0501")
        self.assertIs(item["bot_available"], True)
        self.assertIs(item["web_available"], True)
        self.assertEqual(item["state"], InvitationDerivedState.PENDING)
        self.assertEqual(item["kind"], "standard")
        self.assertEqual(item["sms_status"], InvitationSMSStatus.ACCEPTED)
        self.assertEqual(item["expires_at"], fixed_expires_at)
        self.assertEqual(item["created_at"], fixed_created_at)
        self.assertEqual(item["created_by_id"], 42)


if __name__ == "__main__":
    unittest.main()
