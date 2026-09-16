"""Stage P1-00b Characterization Test.

Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides hermetic handler-level characterization for the
create_my_customer accountant-context guard. This one guard outcome is partial
evidence and closes neither the seed, blocker, nor Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import schemas
from api.routers.customers import create_my_customer
from core.services.accountant_relation_service import EffectiveOwnerActor


class CreateMyCustomerAccountantGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test proving the accountant-context guard precedes persistence and side effects."""

    async def test_create_my_customer_accountant_context_403_precedes_persistence_and_side_effects(self) -> None:
        payload = schemas.CustomerRelationCreate(
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
        )
        owner_user = SimpleNamespace(id=101)
        actor_user = SimpleNamespace(id=202)
        relation = SimpleNamespace(
            id=303,
            owner_user_id=101,
            accountant_user_id=202,
            status="active",
        )
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=relation,
            is_accountant_context=True,
        )
        db = object()

        with (
            patch("api.routers.customers.is_user_customer", new_callable=AsyncMock) as mock_is_user_customer,
            patch("api.routers.customers.create_or_reuse_owner_customer_relation", new_callable=AsyncMock) as mock_create_or_reuse,
            patch("api.routers.customers.deliver_invitation_sms_once", new_callable=AsyncMock) as mock_deliver_sms,
            patch("api.routers.customers.public_webapp_url_for_links") as mock_public_webapp_url,
            patch("api.routers.customers.mark_customer_invitation_for_authoritative_replay") as mock_mark_replay,
            patch("api.routers.customers.send_customer_invitation_sms_result") as mock_send_sms_result,
            patch("api.routers.customers.build_customer_registration_link") as mock_build_link,
            patch("api.routers.customers.audit_log") as mock_audit_log,
            patch("api.routers.customers.serialize_customer_relation") as mock_serialize_customer_relation,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_my_customer(
                    payload=payload,
                    context=context,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Accountants cannot manage owner customers")

        mock_is_user_customer.assert_not_awaited()
        mock_is_user_customer.assert_not_called()
        mock_create_or_reuse.assert_not_awaited()
        mock_create_or_reuse.assert_not_called()
        mock_deliver_sms.assert_not_awaited()
        mock_deliver_sms.assert_not_called()

        mock_public_webapp_url.assert_not_called()
        mock_mark_replay.assert_not_called()
        mock_send_sms_result.assert_not_called()
        mock_build_link.assert_not_called()
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()
        self.assertFalse(hasattr(db, "execute"))
