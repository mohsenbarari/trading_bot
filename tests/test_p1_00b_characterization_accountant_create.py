"""Hermetic direct characterization for create_my_accountant endpoint.

Target: api.routers.accountants.create_my_accountant

Note: This module provides direct characterization only and does not update
an executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete; evidence promotion is a separate unit.
"""

from __future__ import annotations

import enum
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

import schemas
from api.routers.accountants import create_my_accountant
from models.accountant_relation import AccountantRelationStatus


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAccountantCreateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for create_my_accountant."""

    async def test_create_my_accountant_newly_created_success_path_call_order_and_boundaries(
        self,
    ) -> None:
        """Characterize newly-created success path: call ordering, boundaries, SMS sender callback, and audit/serialization."""
        owner_id = 42
        actor_id = 99
        relation_id = 7
        invitation_id = 11

        class RoleEnum(enum.Enum):
            OWNER = "owner"

        actor = mock.Mock(id=actor_id, role=RoleEnum.OWNER)
        context = mock.Mock(
            is_accountant_context=False,
            actor_user=actor,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )
        payload = schemas.AccountantRelationCreate(
            account_name="  acct_test  ",
            relation_display_name="  Reza Accountant  ",
            mobile_number="09123456789",
            duty_description="  Bookkeeping and taxes  ",
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=101,
            relation_display_name="Reza Accountant",
            mobile_number="09123456789",
            status=AccountantRelationStatus.PENDING,
        )
        invitation = mock.Mock(id=invitation_id, short_code="inv12345")
        creation = mock.Mock(
            relation=relation,
            invitation=invitation,
            created=True,
        )
        built_link = "https://app.example.com/register/inv12345"
        expected_sms_delivery_result = {"status": "queued"}
        expected_sms_sender_result = {"delivered": True}
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "status": AccountantRelationStatus.PENDING,
            "registration_link": built_link,
            "sms_status": expected_sms_delivery_result,
        }
        fake_db = FakeAsyncSession()

        call_order: list[str] = []

        async def _fake_guard(*args, **kwargs):
            call_order.append("ensure_owner_context")

        def _fake_public_url(*args, **kwargs):
            call_order.append("public_webapp_url_for_links")
            return "https://app.example.com"

        async def _fake_create_relation(*args, **kwargs):
            call_order.append("create_or_reuse_owner_accountant_relation")
            return creation

        def _fake_build_link(*args, **kwargs):
            call_order.append("build_accountant_registration_link")
            return built_link

        async def _fake_deliver(*args, **kwargs):
            call_order.append("deliver_invitation_sms_once")
            return expected_sms_delivery_result

        def _fake_audit(*args, **kwargs):
            call_order.append("audit_log")

        def _fake_serialize(*args, **kwargs):
            call_order.append("serialize_accountant_relation")
            return serialized_payload

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=_fake_guard,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
            side_effect=_fake_public_url,
        ) as mock_public_url, mock.patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            side_effect=_fake_create_relation,
        ) as mock_create_relation, mock.patch(
            "api.routers.accountants.build_accountant_registration_link",
            side_effect=_fake_build_link,
        ) as mock_build_link, mock.patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new_callable=mock.AsyncMock,
            side_effect=_fake_deliver,
        ) as mock_deliver, mock.patch(
            "api.routers.accountants.send_accountant_invitation_sms_result",
            return_value=expected_sms_sender_result,
        ) as mock_send_sms, mock.patch(
            "api.routers.accountants.audit_log",
            side_effect=_fake_audit,
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await create_my_accountant(
                payload=payload,
                context=context,
                db=fake_db,
            )

            self.assertEqual(
                call_order,
                [
                    "ensure_owner_context",
                    "public_webapp_url_for_links",
                    "create_or_reuse_owner_accountant_relation",
                    "build_accountant_registration_link",
                    "deliver_invitation_sms_once",
                    "audit_log",
                    "serialize_accountant_relation",
                ],
            )
            mock_guard.assert_awaited_once_with(context, fake_db)
            mock_public_url.assert_called_once_with()
            mock_create_relation.assert_awaited_once_with(
                fake_db,
                owner_user=context.owner_user,
                global_account_name="acct_test",
                relation_display_name="Reza Accountant",
                mobile_number="09123456789",
                duty_description="Bookkeeping and taxes",
            )
            mock_build_link.assert_called_once_with("inv12345")
            mock_deliver.assert_awaited_once_with(
                fake_db,
                invitation_id=invitation_id,
                newly_created=True,
                sender=mock.ANY,
            )

            sender_fn = mock_deliver.await_args.kwargs["sender"]
            sms_sender_result = sender_fn()
            mock_send_sms.assert_called_once_with(
                mobile="09123456789",
                relation_display_name="Reza Accountant",
                web_link=built_link,
            )
            self.assertEqual(sms_sender_result, expected_sms_sender_result)

            mock_audit.assert_called_once_with(
                "accountant.link",
                target_type="accountant_relation",
                target_id=relation_id,
                after_summary={
                    "owner_user_id": owner_id,
                    "accountant_user_id": 101,
                    "status": AccountantRelationStatus.PENDING,
                },
                actor_id=actor_id,
                actor_role="owner",
            )
            self.assertNotIn("before_summary", mock_audit.call_args.kwargs)

            mock_serialize.assert_called_once_with(
                relation,
                invitation=invitation,
                sms_status=expected_sms_delivery_result,
            )
            self.assertEqual(result, serialized_payload)

    async def test_create_my_accountant_reused_relation_success_fallback_owner_user(
        self,
    ) -> None:
        """Characterize reused relation success path: created=False, fallback to owner_user when actor_user is absent, and serialization."""
        owner_id = 42
        relation_id = 8
        invitation_id = 12

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationCreate(
            account_name="acct_reused",
            relation_display_name="Reused Accountant",
            mobile_number="09129999999",
            duty_description=None,
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=None,
            relation_display_name="Reused Accountant",
            mobile_number="09129999999",
            status=AccountantRelationStatus.PENDING,
        )
        invitation = mock.Mock(id=invitation_id, short_code="invreused1")
        creation = mock.Mock(
            relation=relation,
            invitation=invitation,
            created=False,
        )
        built_link = "https://app.example.com/register/invreused1"
        expected_sms_delivery_result = {"status": "already_sent"}
        serialized_payload = {
            "id": relation_id,
            "owner_user_id": owner_id,
            "status": AccountantRelationStatus.PENDING,
            "registration_link": built_link,
            "sms_status": expected_sms_delivery_result,
        }
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example.com",
        ) as mock_public_url, mock.patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=creation,
        ) as mock_create_relation, mock.patch(
            "api.routers.accountants.build_accountant_registration_link",
            return_value=built_link,
        ) as mock_build_link, mock.patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new_callable=mock.AsyncMock,
            return_value=expected_sms_delivery_result,
        ) as mock_deliver, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            return_value=serialized_payload,
        ) as mock_serialize:
            result = await create_my_accountant(
                payload=payload,
                context=context,
                db=fake_db,
            )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_public_url.assert_called_once_with()
        mock_create_relation.assert_awaited_once_with(
            fake_db,
            owner_user=context.owner_user,
            global_account_name="acct_reused",
            relation_display_name="Reused Accountant",
            mobile_number="09129999999",
            duty_description=None,
        )
        mock_build_link.assert_called_once_with("invreused1")
        mock_deliver.assert_awaited_once_with(
            fake_db,
            invitation_id=invitation_id,
            newly_created=False,
            sender=mock.ANY,
        )

        mock_audit.assert_called_once_with(
            "accountant.link",
            target_type="accountant_relation",
            target_id=relation_id,
            after_summary={
                "owner_user_id": owner_id,
                "accountant_user_id": None,
                "status": AccountantRelationStatus.PENDING,
            },
            actor_id=owner_id,
            actor_role="owner",
        )
        self.assertNotIn("before_summary", mock_audit.call_args.kwargs)

        mock_serialize.assert_called_once_with(
            relation,
            invitation=invitation,
            sms_status=expected_sms_delivery_result,
        )
        self.assertEqual(result, serialized_payload)

    async def test_create_my_accountant_invalid_registration_link_raises_409(
        self,
    ) -> None:
        """Characterize invalid registration link branch: raises HTTPException 409 and halts downstream delivery, audit, serialize."""
        owner_id = 42
        relation_id = 9
        invitation_id = 13

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationCreate(
            account_name="acct_invalid",
            relation_display_name="Invalid Link Accountant",
            mobile_number="09120000000",
            duty_description=None,
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=None,
            relation_display_name="Invalid Link Accountant",
            mobile_number="09120000000",
            status=AccountantRelationStatus.PENDING,
        )
        invitation = mock.Mock(id=invitation_id, short_code="bad_code")
        creation = mock.Mock(
            relation=relation,
            invitation=invitation,
            created=True,
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example.com",
        ) as mock_public_url, mock.patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=creation,
        ) as mock_create_relation, mock.patch(
            "api.routers.accountants.build_accountant_registration_link",
            return_value=None,
        ) as mock_build_link, mock.patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new_callable=mock.AsyncMock,
        ) as mock_deliver, mock.patch(
            "api.routers.accountants.send_accountant_invitation_sms_result",
        ) as mock_send_sms, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await create_my_accountant(
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "کد کوتاه دعوت حسابدار معتبر نیست")

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_public_url.assert_called_once_with()
        mock_create_relation.assert_awaited_once_with(
            fake_db,
            owner_user=context.owner_user,
            global_account_name="acct_invalid",
            relation_display_name="Invalid Link Accountant",
            mobile_number="09120000000",
            duty_description=None,
        )
        mock_build_link.assert_called_once_with("bad_code")
        mock_deliver.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_create_my_accountant_owner_context_denial_fail_closed(
        self,
    ) -> None:
        """Characterize owner-context denial fail-closed behavior: halts execution before any collaborator call."""
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationCreate(
            account_name="acct_denied",
            relation_display_name="Denied Accountant",
            mobile_number="09121111111",
            duty_description=None,
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Accountants cannot manage owner accountants",
            ),
        ) as mock_guard, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
        ) as mock_public_url, mock.patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_create_relation, mock.patch(
            "api.routers.accountants.build_accountant_registration_link",
        ) as mock_build_link, mock.patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new_callable=mock.AsyncMock,
        ) as mock_deliver, mock.patch(
            "api.routers.accountants.send_accountant_invitation_sms_result",
        ) as mock_send_sms, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await create_my_accountant(
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_public_url.assert_not_called()
        mock_create_relation.assert_not_called()
        mock_build_link.assert_not_called()
        mock_deliver.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()

    async def test_create_my_accountant_invitation_delivery_failure_propagation(
        self,
    ) -> None:
        """Characterize invitation delivery failure: exception propagates while preventing audit and serialization."""
        owner_id = 42
        relation_id = 10
        invitation_id = 14

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, role="owner", is_customer=False),
        )
        payload = schemas.AccountantRelationCreate(
            account_name="acct_delivery_fail",
            relation_display_name="Delivery Fail Accountant",
            mobile_number="09122222222",
            duty_description=None,
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=None,
            relation_display_name="Delivery Fail Accountant",
            mobile_number="09122222222",
            status=AccountantRelationStatus.PENDING,
        )
        invitation = mock.Mock(id=invitation_id, short_code="invdelivery1")
        creation = mock.Mock(
            relation=relation,
            invitation=invitation,
            created=True,
        )
        built_link = "https://app.example.com/register/invdelivery1"
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example.com",
        ) as mock_public_url, mock.patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=creation,
        ) as mock_create_relation, mock.patch(
            "api.routers.accountants.build_accountant_registration_link",
            return_value=built_link,
        ) as mock_build_link, mock.patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new_callable=mock.AsyncMock,
            side_effect=RuntimeError("SMS delivery provider unreachable"),
        ) as mock_deliver, mock.patch(
            "api.routers.accountants.audit_log",
        ) as mock_audit, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(RuntimeError) as ctx:
                await create_my_accountant(
                    payload=payload,
                    context=context,
                    db=fake_db,
                )

        self.assertEqual(str(ctx.exception), "SMS delivery provider unreachable")

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_public_url.assert_called_once_with()
        mock_create_relation.assert_awaited_once_with(
            fake_db,
            owner_user=context.owner_user,
            global_account_name="acct_delivery_fail",
            relation_display_name="Delivery Fail Accountant",
            mobile_number="09122222222",
            duty_description=None,
        )
        mock_build_link.assert_called_once_with("invdelivery1")
        mock_deliver.assert_awaited_once_with(
            fake_db,
            invitation_id=invitation_id,
            newly_created=True,
            sender=mock.ANY,
        )
        mock_audit.assert_not_called()
        mock_serialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
