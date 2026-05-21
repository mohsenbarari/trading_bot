import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.customers import (
    cancel_my_pending_customer,
    create_my_customer,
    list_my_customers,
    update_my_customer,
)
from models.customer_relation import CustomerTier


class FakeDB:
    pass


class CustomersRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_routes_reject_accountant_context(self):
        context = SimpleNamespace(is_accountant_context=True, owner_user=SimpleNamespace(id=7))
        payload = schemas.CustomerRelationCreate(
            account_name="cust1",
            management_name="مشتری",
            mobile_number="09120000000",
        )

        with self.assertRaises(HTTPException) as exc_info:
            await create_my_customer(payload, context=context, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_create_and_list_owner_customers_serialize_registration_links(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            customer_user_id=None,
            customer_user=None,
            management_name="مشتری اول",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=0.5,
            min_trade_quantity=1,
            max_trade_quantity=5,
            max_daily_trades=3,
            max_daily_commodity_volume=10,
            status="pending",
            invitation_token="CUST-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=None,
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        invitation = SimpleNamespace(account_name="cust1", mobile_number="09120000000")
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
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

        with patch(
            "api.routers.customers.create_owner_customer_relation",
            new=AsyncMock(return_value=(relation, invitation)),
        ) as create_mock, patch(
            "api.routers.customers.send_customer_invitation_sms"
        ) as sms_mock, patch(
            "api.routers.customers.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            created = await create_my_customer(payload, context=context, db=FakeDB())

        self.assertEqual(created["registration_link"], "https://app.example/register?token=CUST-token")
        self.assertEqual(created["invitation_account_name"], "cust1")
        create_mock.assert_awaited_once()
        sms_mock.assert_called_once()

        with patch(
            "api.routers.customers.list_owner_customer_relations",
            new=AsyncMock(return_value=[relation]),
        ), patch(
            "api.routers.customers.load_customer_relation_invitation_map",
            new=AsyncMock(return_value={"CUST-token": invitation}),
        ), patch(
            "api.routers.customers.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            listed = await list_my_customers(context=context, db=FakeDB())

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["registration_link"], "https://app.example/register?token=CUST-token")
        self.assertEqual(listed[0]["mobile_number"], "09120000000")

    async def test_cancel_owner_pending_customer_returns_serialized_relation(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            customer_user_id=None,
            customer_user=None,
            management_name="مشتری اول",
            customer_tier=CustomerTier.TIER_1,
            commission_rate=None,
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
            status="revoked",
            invitation_token="CUST-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=None,
            deleted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        invitation = SimpleNamespace(account_name="cust1", mobile_number="09120000000")
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))

        with patch(
            "api.routers.customers.cancel_pending_customer_relation",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.customers.load_customer_relation_invitation_map",
            new=AsyncMock(return_value={"CUST-token": invitation}),
        ), patch(
            "api.routers.customers.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            result = await cancel_my_pending_customer(9, context=context, db=FakeDB())

        self.assertEqual(result["id"], 9)
        self.assertEqual(result["registration_link"], "https://app.example/register?token=CUST-token")

    async def test_update_owner_customer_returns_serialized_relation(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            customer_user_id=12,
            customer_user=SimpleNamespace(account_name="cust1"),
            management_name="مشتری ارشد",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=0.8,
            min_trade_quantity=2,
            max_trade_quantity=8,
            max_daily_trades=4,
            max_daily_commodity_volume=25,
            status="active",
            invitation_token="CUST-token",
            expires_at=datetime.utcnow() + timedelta(days=2),
            activated_at=datetime.utcnow(),
            deleted_at=None,
            created_at=datetime.utcnow(),
        )
        invitation = SimpleNamespace(account_name="cust1", mobile_number="09120000000")
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        payload = schemas.CustomerRelationUpdate(
            customer_tier=CustomerTier.TIER_2,
            commission_rate=0.8,
            min_trade_quantity=2,
            max_trade_quantity=8,
        )

        with patch(
            "api.routers.customers.update_owner_customer_relation",
            new=AsyncMock(return_value=relation),
        ) as update_mock, patch(
            "api.routers.customers.load_customer_relation_invitation_map",
            new=AsyncMock(return_value={"CUST-token": invitation}),
        ), patch(
            "api.routers.customers.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            result = await update_my_customer(9, payload, context=context, db=FakeDB())

        update_mock.assert_awaited_once()
        self.assertEqual(update_mock.await_args.kwargs["update_data"]["commission_rate"], 0.8)
        self.assertEqual(result["management_name"], "مشتری ارشد")


if __name__ == "__main__":
    unittest.main()