"""Characterization tests for owner-customer internal authority validations.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-OWNER-CUSTOMER-INTERNAL-FROM-BOT-L480

Included Scenarios:
1. Non-Iran current server authority rejection: When verify_internal_signature
   succeeds but current_server() != SERVER_IRAN (e.g. SERVER_FOREIGN), the handler
   raises HTTPException 403 with detail 'Customer invite endpoint is Iran-authoritative'
   prior to public URL lookup, requester resolution, or any database/downstream work.
2. Source mismatch authority rejection: When current_server() == SERVER_IRAN but
   payload source_server and raw request header x-source-server do not match the required
   foreign origin (e.g. payload source SERVER_FOREIGN while header source SERVER_IRAN),
   the handler raises HTTPException 401 with detail 'Invalid internal customer invite source'
   prior to requester resolution, database work, or downstream services.

Explicit Exclusions:
This module explicitly excludes bad-signature behavior, owner customer/accountant
policy denials, payload validation/idempotency/account-name branches, requester-resolution
failures, relation lifecycle, SMS outcomes, inventory/docs updates, source changes,
network/provider/database access, blocker closure, reconciliation, and Stage closure.

Notice:
This partial evidence closes neither the seed/blocker/counts/reconciliation nor P1-00b.
Stage P1-00b depends on completed P1-00 and remains IN_PROGRESS.
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
from core.invitation_creation_contracts import InvitationRequesterIdentity
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from models.customer_relation import CustomerTier
import schemas


class OpaqueDatabaseSentinel:
    """Opaque database session sentinel that exposes no session methods."""

    __slots__ = ()


class CreateOwnerCustomerInternalAuthorityValidationsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
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

    def _build_valid_payload(self, *, source_server: str) -> schemas.InternalCustomerInviteRequest:
        owner_identity = InvitationRequesterIdentity(
            account_name="owner_user",
            mobile_number="09123456789",
            telegram_id=123456789,
        )
        return schemas.InternalCustomerInviteRequest(
            owner_identity=owner_identity,
            account_name=None,
            management_name="Owner Customer Org",
            mobile_number="09987654321",
            customer_tier=CustomerTier.TIER_1,
            idempotency_key="deterministic-test-idempotency-key-1234",
            source_server=source_server,
        )

    async def test_non_iran_current_server_is_rejected_before_requester_or_db_work(self) -> None:
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

        payload = self._build_valid_payload(source_server=SERVER_FOREIGN)

        with patch("api.routers.customers.verify_internal_signature", return_value=True) as mock_verify_sig, \
             patch("api.routers.customers.current_server", return_value=SERVER_FOREIGN) as mock_current_srv, \
             patch("api.routers.customers.public_webapp_url_for_links") as mock_public_url, \
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

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Customer invite endpoint is Iran-authoritative")

            mock_request.body.assert_awaited_once()
            mock_verify_sig.assert_called_once_with(raw_body, timestamp, signature, api_key)
            mock_current_srv.assert_called_once()

            mock_public_url.assert_not_called()
            mock_resolve_req.assert_not_called()
            mock_is_customer.assert_not_called()
            mock_is_accountant.assert_not_called()
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit.assert_not_called()
            self._assert_opaque_db_sentinel(db_sentinel)

    async def test_payload_header_source_mismatch_is_rejected_before_requester_or_db_work(self) -> None:
        db_sentinel = OpaqueDatabaseSentinel()
        self._assert_opaque_db_sentinel(db_sentinel)

        raw_body = b'{"action": "create_owner_customer", "deterministic": true}'
        timestamp = "1726490001"
        signature = "sig_valid_hex_string_for_test_mismatch"
        api_key = "test_api_key_deterministic_mismatch"

        mock_request = MagicMock(spec=Request)
        mock_request.body = AsyncMock(return_value=raw_body)
        mock_request.headers = Headers({
            "x-timestamp": timestamp,
            "x-signature": signature,
            "x-api-key": api_key,
            "x-source-server": SERVER_IRAN,
        })

        payload = self._build_valid_payload(source_server=SERVER_FOREIGN)

        with patch("api.routers.customers.verify_internal_signature", return_value=True) as mock_verify_sig, \
             patch("api.routers.customers.current_server", return_value=SERVER_IRAN) as mock_current_srv, \
             patch("api.routers.customers.public_webapp_url_for_links") as mock_public_url, \
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

            self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(ctx.exception.detail, "Invalid internal customer invite source")

            mock_request.body.assert_awaited_once()
            mock_verify_sig.assert_called_once_with(raw_body, timestamp, signature, api_key)
            mock_current_srv.assert_called_once()

            mock_public_url.assert_not_called()
            mock_resolve_req.assert_not_called()
            mock_is_customer.assert_not_called()
            mock_is_accountant.assert_not_called()
            mock_sweep.assert_not_called()
            mock_create_relation.assert_not_called()
            mock_deliver_sms.assert_not_called()
            mock_audit.assert_not_called()
            self._assert_opaque_db_sentinel(db_sentinel)
