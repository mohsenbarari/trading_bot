import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.accountants import cancel_my_pending_accountant, create_my_accountant, list_my_accountants, update_my_accountant


class FakeDB:
    pass


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
            "api.routers.accountants.create_owner_accountant_relation",
            new=AsyncMock(return_value=(relation, SimpleNamespace())),
        ) as create_mock, patch(
            "api.routers.accountants.send_accountant_invitation_sms"
        ) as sms_mock, patch(
            "api.routers.accountants.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            created = await create_my_accountant(payload, context=context, db=FakeDB())

        self.assertEqual(created["registration_link"], "https://app.example/register?token=ACCT-token")
        create_mock.assert_awaited_once()
        sms_mock.assert_called_once()

        with patch(
            "api.routers.accountants.list_owner_accountant_relations",
            new=AsyncMock(return_value=[relation]),
        ), patch(
            "api.routers.accountants.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            listed = await list_my_accountants(context=context, db=FakeDB())

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["registration_link"], "https://app.example/register?token=ACCT-token")

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
            "api.routers.accountants.cancel_pending_accountant_relation",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.accountants.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            result = await cancel_my_pending_accountant(9, context=context, db=FakeDB())

        self.assertEqual(result["id"], 9)
        self.assertEqual(result["registration_link"], "https://app.example/register?token=ACCT-token")

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
            status="active",
            invitation_token="ACCT-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=datetime.utcnow(),
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        payload = schemas.AccountantRelationUpdate(
            relation_display_name="حسابدار ارشد",
            duty_description="پیگیری معاملات",
        )

        with patch(
            "api.routers.accountants.update_owner_accountant_relation",
            new=AsyncMock(return_value=relation),
        ) as update_mock, patch(
            "api.routers.accountants.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            result = await update_my_accountant(9, payload, context=context, db=FakeDB())

        update_mock.assert_awaited_once()
        self.assertEqual(result["id"], 9)
        self.assertEqual(result["relation_display_name"], "حسابدار ارشد")


if __name__ == "__main__":
    unittest.main()