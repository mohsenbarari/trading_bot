"""Stage P1-00b Characterization Test.

Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides hermetic handler-level characterization for the single
still-missing API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415 outcome:
create_my_customer receives a newly-created relation (created=True) whose
invitation has a malformed short code, calls the imported handler through mocked
service boundaries, and raises the exact HTTP 409 before replay persistence,
invitation delivery, SMS sending, audit, or serialization.

This partial characterization outcome closes neither the seed, canonical
blocker/counts, inventory reconciliation, nor Stage.
Exclusions: no product/source change, external database/session/table,
Redis/SMS/HTTP/network/provider call, reused-relation behavior, valid-short-code
behavior, list/update/delete/session handlers, generic test edits,
inventory/reconciliation edits, blocker-count change, blocker closure, or
Stage closure.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import schemas
from api.routers.customers import create_my_customer
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.customer_relation_service import CustomerRelationCreationResult
from models.customer_relation import CustomerTier


class CreateMyCustomerNewInvalidShortCodeCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test for create_my_customer newly-created relation with malformed short code.

    Stage: P1-00b
    Assignment: WA-P1-00B-FREEZE-20260904-001
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415

    Characterizes the single outcome: create_my_customer receives a newly-created
    relation (created=True) whose invitation has a malformed short code, calls the
    imported handler through mocked service boundaries, and raises the exact HTTP 409
    before replay persistence, invitation delivery, SMS sending, audit, or serialization.

    This outcome remains partial evidence and closes neither the seed, blocker,
    inventory reconciliation, nor Stage.
    Exclusions: no product/source change, external database/session/table,
    Redis/SMS/HTTP/network/provider call, reused-relation behavior,
    valid-short-code behavior, list/update/delete/session handlers, generic test edits,
    inventory/reconciliation edits, blocker-count change, blocker closure, or
    Stage closure.
    """

    async def test_create_my_customer_new_invalid_short_code_raises_409_without_replay_or_side_effects(self) -> None:
        payload = schemas.CustomerRelationCreate(
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
        )
        owner_user = SimpleNamespace(id=101)
        actor_user = owner_user
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=False,
        )
        db = SimpleNamespace(
            execute=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        relation_sentinel = SimpleNamespace(id=303, management_name="مدیریت مشتری تست")
        invitation_sentinel = SimpleNamespace(
            id=404,
            mobile_number="09123456789",
            short_code="malformed_code!",
        )
        creation_result = CustomerRelationCreationResult(
            relation=relation_sentinel,
            invitation=invitation_sentinel,
            created=True,
        )

        with (
            patch("api.routers.customers.is_user_customer", new_callable=AsyncMock) as mock_is_user_customer,
            patch("api.routers.customers.public_webapp_url_for_links") as mock_public_webapp_url,
            patch("api.routers.customers.create_or_reuse_owner_customer_relation", new_callable=AsyncMock) as mock_create_or_reuse,
            patch("api.routers.customers.mark_customer_invitation_for_authoritative_replay") as mock_mark_replay,
            patch("api.routers.customers.deliver_invitation_sms_once", new_callable=AsyncMock) as mock_deliver_sms,
            patch("api.routers.customers.send_customer_invitation_sms_result") as mock_send_sms_result,
            patch("api.routers.customers.audit_log") as mock_audit_log,
            patch("api.routers.customers.serialize_customer_relation") as mock_serialize_customer_relation,
        ):
            mock_is_user_customer.return_value = False
            mock_public_webapp_url.return_value = "https://app.example/"
            mock_create_or_reuse.return_value = creation_result

            with self.assertRaises(HTTPException) as exc_info:
                await create_my_customer(
                    payload=payload,
                    context=context,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "کد کوتاه دعوت مشتری معتبر نیست")

        mock_is_user_customer.assert_awaited_once_with(db, 101)
        self.assertIs(mock_is_user_customer.await_args.args[0], db)
        self.assertEqual(mock_is_user_customer.await_args.args[1], 101)

        mock_public_webapp_url.assert_called_once_with()

        mock_create_or_reuse.assert_awaited_once_with(
            db,
            owner_user=owner_user,
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
            customer_tier=CustomerTier.TIER_1,
            commission_rate=None,
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
        )
        self.assertIs(mock_create_or_reuse.await_args.args[0], db)
        self.assertIs(mock_create_or_reuse.await_args.kwargs["owner_user"], owner_user)

        mock_mark_replay.assert_not_called()
        db.execute.assert_not_awaited()
        db.execute.assert_not_called()
        db.commit.assert_not_awaited()
        db.commit.assert_not_called()
        db.refresh.assert_not_awaited()
        db.refresh.assert_not_called()

        mock_deliver_sms.assert_not_awaited()
        mock_deliver_sms.assert_not_called()
        mock_send_sms_result.assert_not_called()
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()
