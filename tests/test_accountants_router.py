import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from core.registration_contracts import InvitationSMSStatus
from api.routers.accountants import (
    cancel_my_pending_accountant,
    create_my_accountant,
    list_my_accountant_sessions,
    list_my_accountants,
    terminate_my_accountant_session,
    update_my_accountant,
)
from models.accountant_relation import AccountantRelationStatus


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeExecuteResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class AccountantsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_routes_reject_accountant_context(self):
        context = SimpleNamespace(is_accountant_context=True, owner_user=SimpleNamespace(id=7))
        payload = schemas.AccountantRelationCreate(
            account_name="acc1",
            relation_display_name="حسابدار",
            mobile_number="09120000000",
        )

        with self.assertRaises(HTTPException) as exc_info:
            await create_my_accountant(payload, context=context, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_owner_routes_reject_customer_context(self):
        context = SimpleNamespace(
            is_accountant_context=False,
            owner_user=SimpleNamespace(id=7),
            actor_user=SimpleNamespace(id=7, is_customer=True),
        )
        db = SimpleNamespace(execute=AsyncMock())

        with self.assertRaises(HTTPException) as exc_info:
            await list_my_accountants(context=context, db=db)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("Customers cannot manage owner accountants", exc_info.exception.detail)

    async def test_create_and_list_owner_accountants_serialize_registration_links(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            accountant_user_id=None,
            accountant_user=None,
            global_account_name="acc1",
            relation_display_name="حسابدار اول",
            duty_description="پیگیری",
            mobile_number="09120000000",
            status="pending",
            invitation_token="ACCT-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=None,
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        payload = schemas.AccountantRelationCreate(
            account_name="acc1",
            relation_display_name="حسابدار اول",
            mobile_number="09120000000",
            duty_description="پیگیری",
        )

        with patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new=AsyncMock(return_value=SimpleNamespace(relation=relation, invitation=SimpleNamespace(id=99), created=True)),
        ) as create_mock, patch(
            "api.routers.accountants.send_accountant_invitation_sms_result"
        ) as sms_mock, patch(
            "api.routers.accountants.deliver_invitation_sms_once",
            new=AsyncMock(side_effect=lambda *args, **kwargs: (
                InvitationSMSStatus.ACCEPTED
                if kwargs["sender"]()
                else InvitationSMSStatus.AMBIGUOUS
            )),
        ), patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example",
        ):
            created = await create_my_accountant(payload, context=context, db=FakeDB())

        self.assertEqual(created["registration_link"], "https://app.example/register?token=ACCT-token")
        create_mock.assert_awaited_once()
        sms_mock.assert_called_once()

        with patch(
            "api.routers.accountants.list_owner_accountant_relations",
            new=AsyncMock(return_value=[relation]),
        ), patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example",
        ):
            listed = await list_my_accountants(context=context, db=FakeDB())

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["registration_link"], "https://app.example/register?token=ACCT-token")

    async def test_create_owner_accountant_fails_closed_when_public_webapp_url_is_invalid(self):
        relation = SimpleNamespace(
            id=19,
            owner_user_id=7,
            accountant_user_id=None,
            accountant_user=None,
            global_account_name="acc2",
            relation_display_name="حسابدار دوم",
            duty_description=None,
            mobile_number="09120000001",
            status="pending",
            invitation_token="ACCT-no-link",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=None,
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        payload = schemas.AccountantRelationCreate(
            account_name="acc2",
            relation_display_name="حسابدار دوم",
            mobile_number="09120000001",
        )

        with patch(
            "api.routers.accountants.create_or_reuse_owner_accountant_relation",
            new=AsyncMock(return_value=SimpleNamespace(relation=relation, invitation=SimpleNamespace(id=100), created=True)),
        ) as create_mock, patch(
            "api.routers.accountants.send_accountant_invitation_sms_result"
        ) as sms_mock, patch(
            "api.routers.accountants.public_webapp_url_for_links",
            side_effect=ValueError("invalid public WebApp URL"),
        ), self.assertRaises(ValueError):
            await create_my_accountant(payload, context=context, db=FakeDB())

        create_mock.assert_not_awaited()
        sms_mock.assert_not_called()

    async def test_cancel_owner_pending_accountant_returns_serialized_relation(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            accountant_user_id=None,
            accountant_user=None,
            global_account_name="acc1",
            relation_display_name="حسابدار اول",
            duty_description=None,
            mobile_number="09120000000",
            status="revoked",
            invitation_token="ACCT-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=None,
            deleted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))

        with patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example",
        ):
            result = await cancel_my_pending_accountant(9, context=context, db=FakeDB())

        self.assertEqual(result["id"], 9)
        self.assertEqual(result["registration_link"], "https://app.example/register?token=ACCT-token")

    async def test_cancel_owner_active_accountant_uses_unlink_service(self):
        relation = SimpleNamespace(
            id=11,
            owner_user_id=7,
            accountant_user_id=12,
            accountant_user=SimpleNamespace(account_name="acc-active"),
            global_account_name="acc-active",
            relation_display_name="حسابدار فعال",
            duty_description="پیگیری",
            mobile_number="09120000000",
            status="deleted",
            invitation_token="ACCT-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=datetime.utcnow(),
            deleted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))

        with patch(
            "api.routers.accountants.unlink_owner_accountant_relation",
            new=AsyncMock(return_value=relation),
        ) as unlink_mock, patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example",
        ):
            result = await cancel_my_pending_accountant(11, context=context, db=FakeDB())

        unlink_mock.assert_awaited_once()
        self.assertEqual(result["status"], "deleted")

    async def test_update_owner_accountant_returns_serialized_relation(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            accountant_user_id=12,
            accountant_user=SimpleNamespace(account_name="acc1"),
            global_account_name="acc1",
            relation_display_name="حسابدار ارشد",
            duty_description="پیگیری معاملات",
            mobile_number="09120000000",
            status=AccountantRelationStatus.ACTIVE,
            invitation_token="ACCT-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=datetime.utcnow(),
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        payload = schemas.AccountantRelationUpdate(
            duty_description="پیگیری معاملات",
        )

        with patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new=AsyncMock(return_value=relation),
        ) as update_mock, patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://app.example",
        ):
            result = await update_my_accountant(9, payload, context=context, db=FakeDB())

        update_mock.assert_awaited_once()
        self.assertEqual(update_mock.await_args.kwargs["duty_description"], "پیگیری معاملات")
        self.assertNotIn("relation_display_name", update_mock.await_args.kwargs)
        self.assertEqual(result["id"], 9)
        self.assertEqual(result["relation_display_name"], "حسابدار ارشد")

    async def test_owner_can_list_and_terminate_active_accountant_sessions(self):
        session_id = "11111111-1111-1111-1111-111111111111"
        relation = SimpleNamespace(
            id=11,
            owner_user_id=7,
            accountant_user_id=12,
            accountant_user=SimpleNamespace(id=12, is_deleted=False),
            status="active",
            deleted_at=None,
        )
        session = SimpleNamespace(
            id=session_id,
            device_name="Chrome",
            device_ip="10.0.0.1",
            platform=SimpleNamespace(value="web"),
            home_server="foreign",
            is_primary=True,
            is_active=True,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))

        with patch("api.routers.accountants.get_active_sessions", new=AsyncMock(return_value=[session])) as sessions_mock:
            result = await list_my_accountant_sessions(
                11,
                context=context,
                db=FakeDB([FakeExecuteResult(relation)]),
            )

        sessions_mock.assert_awaited_once_with(unittest.mock.ANY, 12)
        self.assertEqual(result[0].id, session_id)
        self.assertEqual(result[0].platform, "web")

        with patch("api.routers.accountants.logout_session", new=AsyncMock(return_value=None)) as logout_mock:
            terminated = await terminate_my_accountant_session(
                11,
                session_id,
                context=context,
                db=FakeDB([FakeExecuteResult(relation), FakeExecuteResult(session)]),
            )

        logout_mock.assert_awaited_once_with(unittest.mock.ANY, session)
        self.assertEqual(terminated["terminated_session_id"], session_id)
        self.assertIn("حسابدار", terminated["detail"])

    async def test_accountant_session_management_requires_active_owner_relation(self):
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        deleted_relation = SimpleNamespace(
            id=11,
            owner_user_id=7,
            accountant_user_id=12,
            accountant_user=SimpleNamespace(id=12, is_deleted=False),
            status=AccountantRelationStatus.DELETED,
            deleted_at=datetime.utcnow(),
        )

        with self.assertRaises(HTTPException) as exc_info:
            await list_my_accountant_sessions(
                11,
                context=context,
                db=FakeDB([FakeExecuteResult(deleted_relation)]),
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("فعال", exc_info.exception.detail)

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_my_accountant_session(
                11,
                "not-a-uuid",
                context=context,
                db=FakeDB(),
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("شناسه نشست", exc_info.exception.detail)


if __name__ == "__main__":
    unittest.main()
