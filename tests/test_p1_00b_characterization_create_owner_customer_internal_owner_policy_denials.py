"""Stage P1-00b characterization tests for owner policy denials in create_owner_customer_internal_from_bot.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

Characterizes two policy-denial scenarios in
api.routers.customers.create_owner_customer_internal_from_bot:
1. When a valid foreign Bot request resolves an owner who is already a customer,
   the handler rejects with HTTP 403 and detail "مشتریان اجازه دعوت مشتری ندارند"
   before checking accountant eligibility.
2. When the resolved owner is not a customer but has an active accountant relation,
   the handler rejects with HTTP 403 and detail "حسابداران اجازه دعوت مشتری ندارند".

Explicit exclusions:
- Deferred SMS FAILED outcome
- SMS delivery and status tracking behavior
- Expired-relation commit and sweep side-effects
- Invitation replay and existing-pending lifecycle branches
- Invalid signature, invalid source, invalid idempotency, invalid short-code,
  and invalid requester resolution failure branches
- Production, source, documentation, or inventory edits
- Real network, provider, or database access
- Generic test changes

Note: This partial characterization test provides hermetic evidence only and
closes neither the seed, blocker/counts, reconciliation, nor Stage state.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, Request

from api.routers.customers import create_owner_customer_internal_from_bot
from core.customer_invite import (
    build_customer_invite_account_name,
    build_customer_invite_idempotency_key,
)
from core.invitation_creation_contracts import InvitationRequesterIdentity
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from models.customer_relation import CustomerTier
from models.user import User, UserRole
from schemas import InternalCustomerInviteRequest


class OpaqueDBSentinel:
    """Opaque sentinel representing AsyncSession without exposing any db methods."""


class CreateOwnerCustomerInternalOwnerPolicyDenialsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic tests characterizing owner customer/accountant policy denials."""

    def _build_test_fixtures(self) -> tuple[
        InternalCustomerInviteRequest,
        MagicMock,
        bytes,
        dict[str, str],
        MagicMock,
        OpaqueDBSentinel,
    ]:
        owner_identity = InvitationRequesterIdentity(
            account_name="owner_user",
            mobile_number="09121112233",
            telegram_id=987654321,
        )
        mobile_number = "09123456789"
        expected_account_name = build_customer_invite_account_name(mobile_number)
        expected_idempotency_key = build_customer_invite_idempotency_key(
            source_server=SERVER_FOREIGN,
            owner_identity=owner_identity,
            mobile_number=mobile_number,
            customer_tier=CustomerTier.TIER_1,
        )
        payload = InternalCustomerInviteRequest(
            owner_identity=owner_identity,
            account_name=expected_account_name,
            management_name="Company Management Name",
            mobile_number=mobile_number,
            customer_tier=CustomerTier.TIER_1,
            idempotency_key=expected_idempotency_key,
            source_server=SERVER_FOREIGN,
        )

        raw_body = b'{"mock": "payload_body"}'
        headers = {
            "x-source-server": SERVER_FOREIGN,
            "x-timestamp": "1700000000",
            "x-signature": "test-signature-sha256",
            "x-api-key": "test-internal-api-key",
        }
        raw_request = MagicMock(spec=Request)
        raw_request.body = AsyncMock(return_value=raw_body)
        raw_request.headers = headers

        owner_user = MagicMock(spec=User)
        owner_user.id = 101
        owner_user.role = UserRole.STANDARD

        db_sentinel = OpaqueDBSentinel()

        return payload, raw_request, raw_body, headers, owner_user, db_sentinel

    async def test_resolved_customer_owner_is_rejected_before_accountant_check(self) -> None:
        (
            payload,
            raw_request,
            raw_body,
            headers,
            owner_user,
            db_sentinel,
        ) = self._build_test_fixtures()

        with (
            patch(
                "api.routers.customers.verify_internal_signature",
                return_value=True,
            ) as mock_verify_signature,
            patch(
                "api.routers.customers.current_server",
                return_value=SERVER_IRAN,
            ),
            patch(
                "api.routers.customers.public_webapp_url_for_links",
                return_value="https://iran.example.com",
            ),
            patch(
                "api.routers.customers.resolve_current_invitation_requester",
                new_callable=AsyncMock,
            ) as mock_resolve_requester,
            patch(
                "api.routers.customers.is_user_customer",
                new_callable=AsyncMock,
            ) as mock_is_customer,
            patch(
                "api.routers.customers.is_user_accountant",
                new_callable=AsyncMock,
            ) as mock_is_accountant,
            patch(
                "api.routers.customers.sweep_expired_pending_customer_relations",
                new_callable=AsyncMock,
            ) as mock_sweep,
            patch(
                "api.routers.customers.create_or_reuse_owner_customer_relation",
                new_callable=AsyncMock,
            ) as mock_create_relation,
            patch(
                "api.routers.customers.deliver_invitation_sms_once",
                new_callable=AsyncMock,
            ) as mock_deliver_sms,
            patch(
                "api.routers.customers.audit_log",
            ) as mock_audit_log,
        ):
            mock_resolve_requester.return_value = owner_user
            mock_is_customer.return_value = True

            with self.assertRaises(HTTPException) as ctx:
                await create_owner_customer_internal_from_bot(
                    payload=payload,
                    raw_request=raw_request,
                    db=db_sentinel,
                )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "مشتریان اجازه دعوت مشتری ندارند")

            mock_verify_signature.assert_called_once_with(
                raw_body,
                headers["x-timestamp"],
                headers["x-signature"],
                headers["x-api-key"],
            )
            mock_resolve_requester.assert_awaited_once_with(
                db_sentinel,
                identity=payload.owner_identity,
            )
            mock_is_customer.assert_awaited_once_with(
                db_sentinel,
                owner_user.id,
            )
            mock_is_accountant.assert_not_called()
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit_log.assert_not_called()

            for method_name in (
                "execute",
                "commit",
                "refresh",
                "rollback",
                "get",
                "add",
                "flush",
                "delete",
                "close",
            ):
                self.assertFalse(hasattr(db_sentinel, method_name))

    async def test_resolved_accountant_owner_is_rejected_after_customer_check(self) -> None:
        (
            payload,
            raw_request,
            raw_body,
            headers,
            owner_user,
            db_sentinel,
        ) = self._build_test_fixtures()

        with (
            patch(
                "api.routers.customers.verify_internal_signature",
                return_value=True,
            ) as mock_verify_signature,
            patch(
                "api.routers.customers.current_server",
                return_value=SERVER_IRAN,
            ),
            patch(
                "api.routers.customers.public_webapp_url_for_links",
                return_value="https://iran.example.com",
            ),
            patch(
                "api.routers.customers.resolve_current_invitation_requester",
                new_callable=AsyncMock,
            ) as mock_resolve_requester,
            patch(
                "api.routers.customers.is_user_customer",
                new_callable=AsyncMock,
            ) as mock_is_customer,
            patch(
                "api.routers.customers.is_user_accountant",
                new_callable=AsyncMock,
            ) as mock_is_accountant,
            patch(
                "api.routers.customers.sweep_expired_pending_customer_relations",
                new_callable=AsyncMock,
            ) as mock_sweep,
            patch(
                "api.routers.customers.create_or_reuse_owner_customer_relation",
                new_callable=AsyncMock,
            ) as mock_create_relation,
            patch(
                "api.routers.customers.deliver_invitation_sms_once",
                new_callable=AsyncMock,
            ) as mock_deliver_sms,
            patch(
                "api.routers.customers.audit_log",
            ) as mock_audit_log,
        ):
            mock_resolve_requester.return_value = owner_user
            mock_is_customer.return_value = False
            mock_is_accountant.return_value = True

            with self.assertRaises(HTTPException) as ctx:
                await create_owner_customer_internal_from_bot(
                    payload=payload,
                    raw_request=raw_request,
                    db=db_sentinel,
                )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "حسابداران اجازه دعوت مشتری ندارند")

            mock_verify_signature.assert_called_once_with(
                raw_body,
                headers["x-timestamp"],
                headers["x-signature"],
                headers["x-api-key"],
            )
            mock_resolve_requester.assert_awaited_once_with(
                db_sentinel,
                identity=payload.owner_identity,
            )
            mock_is_customer.assert_awaited_once_with(
                db_sentinel,
                owner_user.id,
            )
            mock_is_accountant.assert_awaited_once_with(
                db_sentinel,
                owner_user.id,
            )
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit_log.assert_not_called()

            for method_name in (
                "execute",
                "commit",
                "refresh",
                "rollback",
                "get",
                "add",
                "flush",
                "delete",
                "close",
            ):
                self.assertFalse(hasattr(db_sentinel, method_name))
