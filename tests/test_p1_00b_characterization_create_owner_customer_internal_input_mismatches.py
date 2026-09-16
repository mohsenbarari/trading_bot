"""Characterization tests for owner-customer internal input mismatches.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

Included Scenarios:
1. Non-empty account_name mismatch rejection: When signature verification, Iran current
   server authority, foreign source matching, and public WebApp URL validation succeed,
   but payload.account_name is non-empty and differs from the deterministic account name
   derived from mobile_number, the handler raises HTTPException 400 with detail
   'نام کاربری مشتری با شماره موبایل تطابق ندارد' prior to requester resolution or any
   database/downstream work.
2. Idempotency key mismatch rejection: When signature verification, Iran current server
   authority, foreign source matching, public WebApp URL validation succeed, and
   payload.account_name matches the deterministic account name, but payload.idempotency_key
   differs from the deterministic idempotency key, the handler raises HTTPException 400
   with detail 'کلید تکرار دعوت مشتری نامعتبر است' prior to requester resolution or any
   database/downstream work.

Explicit Exclusions:
This module explicitly excludes bad-signature rejection, non-Iran server authority rejection,
source mismatch authority rejection, owner customer/accountant policy denials,
requester-resolution failures, relation lifecycle, SMS delivery outcomes, inventory/docs updates,
source changes, network/provider/database access, blocker closure, seed closure,
reconciliation claiming, and Stage closure.

Notice:
This characterization test provides partial evidence under Stage P1-00b and does not edit or
claim closure of tracked docs, inventory, blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE,
seed API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480, or Stage P1-00b.
Prior to this unit, units_since_reconciliation=4; if Codex accepts this unit,
units_since_reconciliation becomes 5 and reconciliation is immediately due next before
further characterization.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import HTTPException, Request
from starlette.datastructures import Headers

from api.routers.customers import create_owner_customer_internal_from_bot
from core.customer_invite import (
    build_customer_invite_account_name,
    build_customer_invite_idempotency_key,
)
from core.invitation_creation_contracts import InvitationRequesterIdentity
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from models.customer_relation import CustomerTier
import schemas


class OpaqueDatabaseSentinel:
    """Opaque database session sentinel that exposes no session methods."""

    __slots__ = ()


class CreateOwnerCustomerInternalInputMismatchesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for pre-requester input-mismatch rejection.

    Stage: P1-00b
    Assignment: WA-P1-00B-FREEZE-20260904-001
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

    Included Scenarios:
    - Mismatching non-empty account_name raises HTTPException 400 ("نام کاربری مشتری با شماره موبایل تطابق ندارد")
    - Mismatching idempotency_key with matching account_name raises HTTPException 400 ("کلید تکرار دعوت مشتری نامعتبر است")

    Explicit Exclusions:
    Excludes authority checks, signature verification denials, requester resolution failures,
    downstream relation creation, SMS delivery, audit log recording, database mutations,
    and claiming closure of blocker, seed, inventory, reconciliation, or Stage P1-00b.

    Notice:
    Partial evidence only. Upon acceptance, units_since_reconciliation becomes 5 and
    reconciliation is due next.
    """

    PROHIBITED_SESSION_METHODS = (
        "execute",
        "commit",
        "refresh",
        "rollback",
        "get",
        "add",
        "flush",
        "delete",
        "close",
    )

    def _assert_opaque_db_sentinel(self, db: object) -> None:
        for method_name in self.PROHIBITED_SESSION_METHODS:
            self.assertFalse(
                hasattr(db, method_name),
                f"Opaque database sentinel must not expose session method {method_name}",
            )

    def _build_test_identity(self) -> InvitationRequesterIdentity:
        return InvitationRequesterIdentity(
            account_name="owner_user",
            mobile_number="09123456789",
            telegram_id=123456789,
        )

    async def test_mismatching_non_empty_account_name_is_rejected_before_requester_or_db_work(self) -> None:
        db_sentinel = OpaqueDatabaseSentinel()
        self._assert_opaque_db_sentinel(db_sentinel)

        raw_body = b'{"action": "create_owner_customer", "deterministic": true}'
        timestamp = "1726490000"
        signature = "sig_valid_hex_string_for_test"
        api_key = "test_api_key_deterministic"

        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=raw_body)
        mock_request.headers = Headers({
            "x-timestamp": timestamp,
            "x-signature": signature,
            "x-api-key": api_key,
            "x-source-server": SERVER_FOREIGN,
        })

        owner_identity = self._build_test_identity()
        target_mobile = "09987654321"
        expected_idempotency_key = build_customer_invite_idempotency_key(
            source_server=SERVER_FOREIGN,
            owner_identity=owner_identity,
            mobile_number=target_mobile,
            customer_tier=CustomerTier.TIER_1,
        )

        payload = schemas.InternalCustomerInviteRequest(
            owner_identity=owner_identity,
            account_name="customer_mismatched_account_name",
            management_name="Owner Customer Org",
            mobile_number=target_mobile,
            customer_tier=CustomerTier.TIER_1,
            idempotency_key=expected_idempotency_key,
            source_server=SERVER_FOREIGN,
        )

        with patch("api.routers.customers.verify_internal_signature", return_value=True) as mock_verify_sig, \
             patch("api.routers.customers.current_server", return_value=SERVER_IRAN) as mock_current_srv, \
             patch("api.routers.customers.public_webapp_url_for_links", return_value="https://app.example.com") as mock_public_url, \
             patch("api.routers.customers.resolve_current_invitation_requester") as mock_resolve_req, \
             patch("api.routers.customers.is_user_customer") as mock_is_customer, \
             patch("api.routers.customers.is_user_accountant") as mock_is_accountant, \
             patch("api.routers.customers.sweep_expired_pending_customer_relations") as mock_sweep, \
             patch("api.routers.customers.create_or_reuse_owner_customer_relation") as mock_create_relation, \
             patch("api.routers.customers.deliver_invitation_sms_once") as mock_deliver_sms, \
             patch("api.routers.customers.audit_log") as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await create_owner_customer_internal_from_bot(
                    payload=payload,
                    raw_request=mock_request,
                    db=db_sentinel,  # type: ignore[arg-type]
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "نام کاربری مشتری با شماره موبایل تطابق ندارد")

            mock_request.body.assert_awaited_once()
            mock_verify_sig.assert_called_once_with(raw_body, timestamp, signature, api_key)
            mock_current_srv.assert_called_once()
            mock_public_url.assert_called_once_with()

            mock_resolve_req.assert_not_called()
            mock_is_customer.assert_not_called()
            mock_is_accountant.assert_not_called()
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit.assert_not_called()
            self._assert_opaque_db_sentinel(db_sentinel)

    async def test_mismatching_idempotency_key_with_matching_account_name_is_rejected_before_requester_or_db_work(self) -> None:
        db_sentinel = OpaqueDatabaseSentinel()
        self._assert_opaque_db_sentinel(db_sentinel)

        raw_body = b'{"action": "create_owner_customer", "deterministic": true}'
        timestamp = "1726490001"
        signature = "sig_valid_hex_string_for_test_idempotency"
        api_key = "test_api_key_deterministic_idempotency"

        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=raw_body)
        mock_request.headers = Headers({
            "x-timestamp": timestamp,
            "x-signature": signature,
            "x-api-key": api_key,
            "x-source-server": SERVER_FOREIGN,
        })

        owner_identity = self._build_test_identity()
        target_mobile = "09987654321"
        matching_account_name = build_customer_invite_account_name(target_mobile)
        wrong_idempotency_key = "customer-invite:schema_valid_wrong_key_999999999999"

        payload = schemas.InternalCustomerInviteRequest(
            owner_identity=owner_identity,
            account_name=matching_account_name,
            management_name="Owner Customer Org",
            mobile_number=target_mobile,
            customer_tier=CustomerTier.TIER_1,
            idempotency_key=wrong_idempotency_key,
            source_server=SERVER_FOREIGN,
        )

        with patch("api.routers.customers.verify_internal_signature", return_value=True) as mock_verify_sig, \
             patch("api.routers.customers.current_server", return_value=SERVER_IRAN) as mock_current_srv, \
             patch("api.routers.customers.public_webapp_url_for_links", return_value="https://app.example.com") as mock_public_url, \
             patch("api.routers.customers.resolve_current_invitation_requester") as mock_resolve_req, \
             patch("api.routers.customers.is_user_customer") as mock_is_customer, \
             patch("api.routers.customers.is_user_accountant") as mock_is_accountant, \
             patch("api.routers.customers.sweep_expired_pending_customer_relations") as mock_sweep, \
             patch("api.routers.customers.create_or_reuse_owner_customer_relation") as mock_create_relation, \
             patch("api.routers.customers.deliver_invitation_sms_once") as mock_deliver_sms, \
             patch("api.routers.customers.audit_log") as mock_audit:
            with self.assertRaises(HTTPException) as ctx:
                await create_owner_customer_internal_from_bot(
                    payload=payload,
                    raw_request=mock_request,
                    db=db_sentinel,  # type: ignore[arg-type]
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "کلید تکرار دعوت مشتری نامعتبر است")

            mock_request.body.assert_awaited_once()
            mock_verify_sig.assert_called_once_with(raw_body, timestamp, signature, api_key)
            mock_current_srv.assert_called_once()
            mock_public_url.assert_called_once_with()

            mock_resolve_req.assert_not_called()
            mock_is_customer.assert_not_called()
            mock_is_accountant.assert_not_called()
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit.assert_not_called()
            self._assert_opaque_db_sentinel(db_sentinel)
