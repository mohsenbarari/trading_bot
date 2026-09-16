"""Stage P1-00b Characterization Test.

Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides hermetic handler-level characterization for the
create_my_customer accountant-context and customer-actor guards, the
reused-relation invalid-short-code replay boundary, and the newly-created
relation valid-short-code success boundary.

These characterization outcomes remain partial evidence and close neither the
seed, blocker, inventory reconciliation, nor Stage.
Single created-success outcome: newly-created relation with valid canonical
short code delivers through sender boundary once, emits exact customer.link
audit, and returns serialization result without replay commit or refresh.
Exclusions: no product/source change, external database/session/table,
Redis/SMS/HTTP/network/provider call, reused-relation behavior,
list/update/delete/session handlers, generic test edits,
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
from core.enums import UserRole
from core.registration_contracts import InvitationSMSStatus
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.customer_relation_service import CustomerRelationCreationResult
from core.sms import SMSDeliveryOutcome
from models.customer_relation import CustomerRelationStatus, CustomerTier


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


class CreateMyCustomerCustomerActorGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test proving the customer-actor guard precedes persistence and side effects.

    This guard outcome remains partial evidence and closes neither the seed, blocker, nor Stage.
    """

    async def test_create_my_customer_customer_actor_403_uses_actor_id_before_mutation_and_side_effects(self) -> None:
        payload = schemas.CustomerRelationCreate(
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
        )
        owner_user = SimpleNamespace(id=101)
        actor_user = SimpleNamespace(id=202)
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
            mock_is_user_customer.return_value = True
            with self.assertRaises(HTTPException) as exc_info:
                await create_my_customer(
                    payload=payload,
                    context=context,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Customers cannot manage owner customers")

        mock_is_user_customer.assert_awaited_once_with(db, 202)
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

        db.commit.assert_not_awaited()
        db.commit.assert_not_called()
        db.refresh.assert_not_awaited()
        db.refresh.assert_not_called()
        db.execute.assert_not_awaited()
        db.execute.assert_not_called()


class CreateMyCustomerReusedInvalidShortCodeCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test proving reused customer relations replay invitation sync before 409 on invalid short code.

    This outcome remains partial evidence and closes neither the seed, blocker, inventory reconciliation, nor Stage.
    """

    async def test_create_my_customer_reused_relation_invalid_short_code_replays_then_409(self) -> None:
        payload = schemas.CustomerRelationCreate(
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
        )
        owner_user = SimpleNamespace(id=101)
        actor_user = SimpleNamespace(id=101)
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
        relation_sentinel = SimpleNamespace(id=303)
        invitation_sentinel = SimpleNamespace(id=404, short_code="bad")
        creation_result = SimpleNamespace(
            relation=relation_sentinel,
            invitation=invitation_sentinel,
            created=False,
        )

        call_order: list[tuple[str, object, ...]] = []

        def record_mark_replay(rel: object, inv: object) -> None:
            call_order.append(("mark_replay", rel, inv))

        async def record_commit() -> None:
            call_order.append(("commit",))

        async def record_refresh(target: object) -> None:
            call_order.append(("refresh", target))

        db.commit.side_effect = record_commit
        db.refresh.side_effect = record_refresh

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
            mock_create_or_reuse.return_value = creation_result
            mock_mark_replay.side_effect = record_mark_replay

            with self.assertRaises(HTTPException) as exc_info:
                await create_my_customer(
                    payload=payload,
                    context=context,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "کد کوتاه دعوت مشتری معتبر نیست")

        mock_is_user_customer.assert_awaited_once_with(db, 101)
        mock_public_webapp_url.assert_called_once_with()

        mock_create_or_reuse.assert_awaited_once_with(
            db,
            owner_user=owner_user,
            account_name="customer_alpha",
            management_name="مدیریت مشتری تست",
            mobile_number="09123456789",
            customer_tier=payload.customer_tier,
            commission_rate=None,
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
        )
        self.assertIs(mock_create_or_reuse.await_args.args[0], db)
        self.assertIs(mock_create_or_reuse.await_args.kwargs["owner_user"], owner_user)

        self.assertEqual(
            call_order,
            [
                ("mark_replay", relation_sentinel, invitation_sentinel),
                ("commit",),
                ("refresh", invitation_sentinel),
                ("refresh", relation_sentinel),
            ],
        )
        self.assertIs(call_order[0][1], relation_sentinel)
        self.assertIs(call_order[0][2], invitation_sentinel)
        self.assertIs(call_order[2][1], invitation_sentinel)
        self.assertIs(call_order[3][1], relation_sentinel)

        mock_mark_replay.assert_called_once_with(relation_sentinel, invitation_sentinel)
        db.commit.assert_awaited_once()
        self.assertEqual(db.refresh.await_count, 2)
        db.refresh.assert_has_awaits([
            unittest.mock.call(invitation_sentinel),
            unittest.mock.call(relation_sentinel),
        ])

        mock_deliver_sms.assert_not_awaited()
        mock_deliver_sms.assert_not_called()
        mock_send_sms_result.assert_not_called()
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()


class CreateMyCustomerNewSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test for create_my_customer newly-created relation with valid canonical short code.

    Stage: P1-00b
    Assignment: WA-P1-00B-FREEZE-20260904-001
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Seed: API-IDENTITY-POST-CUSTOMERS-CREATE-MY-CUSTOMER-L415

    Characterizes the single created-success outcome: create_my_customer receives
    a newly-created relation with a valid canonical short code, delivers through
    the sender boundary once, emits the exact audit payload, hands the accepted
    status to serialization, and returns that serialization result without replay
    commit or refresh.

    This outcome remains partial evidence and closes neither the seed, blocker,
    inventory reconciliation, nor Stage.
    Exclusions: no product/source change, external database/session/table,
    Redis/SMS/HTTP/network/provider call, reused-relation behavior,
    list/update/delete/session handlers, generic test edits,
    inventory/reconciliation edits, blocker-count change, blocker closure, or
    Stage closure.
    """

    async def test_create_my_customer_new_valid_short_code_delivers_audits_and_returns_serialization(self) -> None:
        payload = schemas.CustomerRelationCreate(
            account_name="cust1",
            management_name="مشتری اول",
            mobile_number="09120000000",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=0.5,
            min_trade_quantity=1,
            max_trade_quantity=5,
            max_daily_trades=3,
            max_daily_commodity_volume=10,
        )
        owner_user = SimpleNamespace(id=7, role=UserRole.STANDARD)
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=owner_user,
            relation=None,
            is_accountant_context=False,
        )
        db = SimpleNamespace(
            execute=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            customer_user_id=None,
            customer_tier=CustomerTier.TIER_2,
            status=CustomerRelationStatus.PENDING,
            management_name="مشتری اول",
        )
        invitation = SimpleNamespace(
            id=21,
            mobile_number="09120000000",
            short_code="SHORTC01",
        )
        creation_result = CustomerRelationCreationResult(
            relation=relation,
            invitation=invitation,
            created=True,
        )
        sentinel_serialized_response = {
            "id": 9,
            "owner_user_id": 7,
            "status": "pending",
            "sentinel": "created_success_serialization_result",
        }

        async def fake_deliver_sms(
            db_session: object,
            *,
            invitation_id: int,
            newly_created: bool,
            sender: object,
        ) -> InvitationSMSStatus:
            sender()
            return InvitationSMSStatus.ACCEPTED

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
            mock_deliver_sms.side_effect = fake_deliver_sms
            mock_send_sms_result.return_value = SMSDeliveryOutcome.ACCEPTED
            mock_serialize_customer_relation.return_value = sentinel_serialized_response

            result = await create_my_customer(
                payload=payload,
                context=context,
                db=db,
            )

        mock_is_user_customer.assert_awaited_once_with(db, 7)
        self.assertIs(mock_is_user_customer.await_args.args[0], db)
        self.assertEqual(mock_is_user_customer.await_args.args[1], 7)

        self.assertEqual(mock_public_webapp_url.call_count, 2)

        mock_create_or_reuse.assert_awaited_once_with(
            db,
            owner_user=owner_user,
            account_name="cust1",
            management_name="مشتری اول",
            mobile_number="09120000000",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=0.5,
            min_trade_quantity=1,
            max_trade_quantity=5,
            max_daily_trades=3,
            max_daily_commodity_volume=10,
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

        mock_deliver_sms.assert_awaited_once()
        self.assertIs(mock_deliver_sms.await_args.args[0], db)
        self.assertEqual(mock_deliver_sms.await_args.kwargs["invitation_id"], 21)
        self.assertIs(mock_deliver_sms.await_args.kwargs["newly_created"], True)

        mock_send_sms_result.assert_called_once_with(
            mobile="09120000000",
            management_name="مشتری اول",
            web_link="https://app.example/i/SHORTC01",
        )

        mock_audit_log.assert_called_once_with(
            "customer.link",
            target_type="customer_relation",
            target_id=9,
            after_summary={
                "owner_user_id": 7,
                "customer_user_id": None,
                "customer_tier": CustomerTier.TIER_2,
                "status": CustomerRelationStatus.PENDING,
            },
            actor_id=7,
            actor_role=UserRole.STANDARD.value,
        )

        mock_serialize_customer_relation.assert_called_once_with(
            relation,
            invitation=invitation,
            sms_status=InvitationSMSStatus.ACCEPTED,
        )
        self.assertIs(mock_serialize_customer_relation.call_args.args[0], relation)
        self.assertIs(mock_serialize_customer_relation.call_args.kwargs["invitation"], invitation)
        self.assertEqual(mock_serialize_customer_relation.call_args.kwargs["sms_status"], InvitationSMSStatus.ACCEPTED)

        self.assertIs(result, sentinel_serialized_response)
