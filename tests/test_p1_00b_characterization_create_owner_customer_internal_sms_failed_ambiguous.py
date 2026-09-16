"""Stage P1-00b Characterization Test.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

This module provides hermetic handler-level characterization for exactly two
related newly-created handler outcomes in
api.routers.customers.create_owner_customer_internal_from_bot:
a valid foreign Bot request on Iran resolves an eligible owner, finds no expired
pending relations, creates a Tier-1 relation, supplies a callable SMS sender to
deliver_invitation_sms_once, and receives either InvitationSMSStatus.FAILED or
InvitationSMSStatus.AMBIGUOUS without the patched delivery boundary invoking that sender.
For each outcome, the handler records sms_sent=False in the customer.link audit and returns
InternalCustomerInviteResponse with sms_sent=False and the corresponding sms_status.

Status: partial-evidence only.
Reconciliation is not due; units_since_reconciliation=2 before this unit.
This partial test closes neither the seed, blocker/counts, reconciliation, nor Stage.

Exclusions: all already-covered handler outcomes including SMS ACCEPTED/DISABLED,
reused-relation replay, expired-relation commit, invalid signature/source/idempotency/short-code/requester,
account or idempotency validation, real database/session/table, Redis/SMS/HTTP/network/provider calls,
product/source changes, other handlers, generic test edits, inventory/reconciliation edits,
canonical count changes, blocker or seed closure, and Stage closure.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import schemas
from api.routers.customers import create_owner_customer_internal_from_bot
from core.customer_invite import (
    build_customer_invite_account_name,
    build_customer_invite_idempotency_key,
    normalize_customer_invite_mobile,
    normalize_customer_invite_tier,
)
from core.invitation_creation_contracts import InvitationRequesterIdentity
from core.registration_contracts import InvitationSMSStatus
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from core.services.customer_relation_service import CustomerRelationCreationResult
from models.customer_relation import CustomerRelationStatus, CustomerTier
from models.user import UserRole


class CreateOwnerCustomerInternalSMSFailedAmbiguousCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for create_owner_customer_internal_from_bot SMS failed and ambiguous outcomes.

    Stage: P1-00b
    Assignment: WA-P1-00B-FREEZE-20260904-001
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

    Characterizes exactly two related newly-created handler outcomes through one shared flow:
    api.routers.customers.create_owner_customer_internal_from_bot receives valid foreign-source
    headers and payload on Iran, resolves an eligible owner, finds no expired pending relations,
    creates a Tier-1 relation, supplies a callable SMS sender to deliver_invitation_sms_once,
    and receives either InvitationSMSStatus.FAILED or InvitationSMSStatus.AMBIGUOUS without
    invoking that sender.

    Status: partial-evidence only.
    Reconciliation is not due; units_since_reconciliation=2 before this unit.
    This partial test closes neither the seed, blocker/counts, reconciliation, nor Stage.

    Exclusions: all already-covered handler outcomes including SMS ACCEPTED/DISABLED,
    reused-relation replay, expired-relation commit, invalid signature/source/idempotency/short-code/requester,
    account or idempotency validation, real database/session/table, Redis/SMS/HTTP/network/provider calls,
    product/source changes, other handlers, generic test edits, inventory/reconciliation edits,
    canonical count changes, blocker or seed closure, and Stage closure.
    """

    async def _exercise_new_owner_customer_sms_outcome(
        self,
        expected_sms_status: InvitationSMSStatus,
    ) -> None:
        owner_identity = InvitationRequesterIdentity(
            account_name="owner7",
            mobile_number="09120000007",
            telegram_id=700,
        )
        mobile_number = "09123456789"
        management_name = "مشتری"
        short_code = "SHORT032"

        normalized_mobile = normalize_customer_invite_mobile(mobile_number)
        tier = normalize_customer_invite_tier(CustomerTier.TIER_1)
        expected_account_name = build_customer_invite_account_name(normalized_mobile)
        expected_idempotency_key = build_customer_invite_idempotency_key(
            source_server=SERVER_FOREIGN,
            owner_identity=owner_identity,
            mobile_number=normalized_mobile,
            customer_tier=tier,
        )

        payload = schemas.InternalCustomerInviteRequest(
            owner_identity=owner_identity,
            account_name=expected_account_name,
            management_name=management_name,
            mobile_number=mobile_number,
            customer_tier=CustomerTier.TIER_1,
            idempotency_key=expected_idempotency_key,
            source_server=SERVER_FOREIGN,
        )

        request_body = b"{}"
        raw_request = SimpleNamespace(
            body=AsyncMock(return_value=request_body),
            headers={
                "x-timestamp": "1",
                "x-signature": "sig",
                "x-api-key": "key",
                "x-source-server": SERVER_FOREIGN,
            },
        )

        db = SimpleNamespace(
            execute=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        owner_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
        )
        relation_sentinel = SimpleNamespace(
            id=12,
            owner_user_id=7,
            customer_user_id=None,
            customer_tier=CustomerTier.TIER_1,
            status=CustomerRelationStatus.PENDING,
            management_name=management_name,
        )
        invitation_sentinel = SimpleNamespace(
            id=32,
            mobile_number=mobile_number,
            short_code=short_code,
        )
        creation_result = CustomerRelationCreationResult(
            relation=relation_sentinel,
            invitation=invitation_sentinel,
            created=True,
        )

        contract_expiry = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        contract = SimpleNamespace(
            bot_link="https://t.me/fixture_bot?start=test_token",
            web_link=f"https://app.example/i/{short_code}",
            web_short_link=f"https://app.example/i/{short_code}",
            expires_at=contract_expiry,
        )

        with (
            patch("api.routers.customers.verify_internal_signature") as mock_verify_signature,
            patch("api.routers.customers.current_server") as mock_current_server,
            patch("api.routers.customers.public_webapp_url_for_links") as mock_public_webapp_url,
            patch("api.routers.customers.resolve_current_invitation_requester", new_callable=AsyncMock) as mock_resolve_requester,
            patch("api.routers.customers.is_user_customer", new_callable=AsyncMock) as mock_is_user_customer,
            patch("api.routers.customers.is_user_accountant", new_callable=AsyncMock) as mock_is_user_accountant,
            patch("api.routers.customers.sweep_expired_pending_customer_relations", new_callable=AsyncMock) as mock_sweep_expired,
            patch("api.routers.customers.create_or_reuse_owner_customer_relation", new_callable=AsyncMock) as mock_create_or_reuse,
            patch("api.routers.customers.mark_customer_invitation_for_authoritative_replay") as mock_mark_replay,
            patch("api.routers.customers.deliver_invitation_sms_once", new_callable=AsyncMock) as mock_deliver_sms,
            patch("api.routers.customers.send_customer_invitation_sms_result") as mock_send_sms_result,
            patch("api.routers.customers.build_invitation_contract_v2") as mock_build_contract,
            patch("api.routers.customers.settings") as mock_settings,
            patch("api.routers.customers.audit_log") as mock_audit_log,
        ):
            mock_verify_signature.return_value = True
            mock_current_server.return_value = SERVER_IRAN
            mock_public_webapp_url.return_value = "https://app.example"
            mock_resolve_requester.return_value = owner_user
            mock_is_user_customer.return_value = False
            mock_is_user_accountant.return_value = False
            mock_sweep_expired.return_value = []
            mock_create_or_reuse.return_value = creation_result
            mock_deliver_sms.return_value = expected_sms_status
            mock_build_contract.return_value = contract
            mock_settings.bot_username = "fixture_bot"

            response = await create_owner_customer_internal_from_bot(
                payload=payload,
                raw_request=raw_request,
                db=db,
            )

        mock_verify_signature.assert_called_once_with(
            request_body,
            "1",
            "sig",
            "key",
        )
        mock_current_server.assert_called_once_with()
        self.assertEqual(mock_public_webapp_url.call_count, 2)

        mock_resolve_requester.assert_awaited_once_with(db, identity=owner_identity)
        self.assertIs(mock_resolve_requester.await_args.args[0], db)

        mock_is_user_customer.assert_awaited_once_with(db, 7)
        self.assertIs(mock_is_user_customer.await_args.args[0], db)
        self.assertEqual(mock_is_user_customer.await_args.args[1], 7)

        mock_is_user_accountant.assert_awaited_once_with(db, 7)
        self.assertIs(mock_is_user_accountant.await_args.args[0], db)
        self.assertEqual(mock_is_user_accountant.await_args.args[1], 7)

        mock_sweep_expired.assert_awaited_once_with(db)
        self.assertIs(mock_sweep_expired.await_args.args[0], db)

        mock_create_or_reuse.assert_awaited_once_with(
            db,
            owner_user=owner_user,
            account_name="customer_09123456789",
            management_name="مشتری",
            mobile_number="09123456789",
            customer_tier=CustomerTier.TIER_1,
        )
        self.assertIs(mock_create_or_reuse.await_args.args[0], db)
        self.assertIs(mock_create_or_reuse.await_args.kwargs["owner_user"], owner_user)

        mock_deliver_sms.assert_awaited_once()
        delivery_args, delivery_kwargs = mock_deliver_sms.await_args
        self.assertIs(delivery_args[0], db)
        self.assertEqual(delivery_kwargs["invitation_id"], 32)
        self.assertIs(delivery_kwargs["newly_created"], True)
        self.assertTrue(callable(delivery_kwargs["sender"]))

        mock_send_sms_result.assert_not_called()

        mock_build_contract.assert_called_once_with(
            invitation_sentinel,
            bot_username="fixture_bot",
            sms_status=InvitationSMSStatus.DISABLED,
            customer_tier=CustomerTier.TIER_1,
        )
        self.assertIs(mock_build_contract.call_args.args[0], invitation_sentinel)

        mock_mark_replay.assert_not_called()

        mock_audit_log.assert_called_once_with(
            "customer.link",
            target_type="customer_relation",
            target_id=12,
            actor_id=7,
            actor_role=UserRole.STANDARD.value,
            after_summary={
                "owner_user_id": 7,
                "customer_user_id": None,
                "customer_tier": CustomerTier.TIER_1,
                "status": CustomerRelationStatus.PENDING,
                "source": "telegram_bot_foreign",
                "sms_sent": False,
            },
        )

        self.assertIsInstance(response, schemas.InternalCustomerInviteResponse)
        self.assertIs(response.created, True)
        self.assertIs(response.already_pending, False)
        self.assertEqual(response.relation_id, 12)
        self.assertEqual(response.short_code, "SHORT032")
        self.assertEqual(response.idempotency_key, expected_idempotency_key)
        self.assertIs(response.sms_sent, False)
        self.assertEqual(response.sms_status, expected_sms_status)
        self.assertIsNone(response.reason)
        self.assertEqual(response.bot_link, "https://t.me/fixture_bot?start=test_token")
        self.assertEqual(response.web_link, "https://app.example/i/SHORT032")
        self.assertEqual(response.web_short_link, "https://app.example/i/SHORT032")
        self.assertEqual(response.expires_at, contract_expiry)

        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.refresh.assert_not_awaited()

    async def test_new_owner_customer_sms_failed_returns_false_and_audits_failure(self) -> None:
        await self._exercise_new_owner_customer_sms_outcome(InvitationSMSStatus.FAILED)

    async def test_new_owner_customer_sms_ambiguous_returns_false_and_audits_ambiguity(self) -> None:
        await self._exercise_new_owner_customer_sms_outcome(InvitationSMSStatus.AMBIGUOUS)
