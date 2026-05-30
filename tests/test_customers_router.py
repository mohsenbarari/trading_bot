import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.customers import (
    build_customer_registration_link,
    create_my_customer,
    get_active_customer_session,
    get_active_owner_customer_relation,
    list_my_customers,
    list_my_customer_sessions,
    terminate_my_customer_session,
    unlink_my_customer,
    update_my_customer,
)
from models.customer_relation import CustomerRelationStatus, CustomerTier


class FakeDB:
    pass


class FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ExecuteDB:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, _stmt):
        if not self.values:
            raise AssertionError("Unexpected execute() call")
        return FakeExecuteResult(self.values.pop(0))


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

    async def test_owner_routes_reject_customer_context(self):
        context = SimpleNamespace(
            is_accountant_context=False,
            owner_user=SimpleNamespace(id=7),
            actor_user=SimpleNamespace(id=7),
        )
        db = SimpleNamespace(execute=AsyncMock())

        with patch("api.routers.customers.is_user_customer", new=AsyncMock(return_value=True)):
            with self.assertRaises(HTTPException) as exc_info:
                await list_my_customers(context=context, db=db)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("Customers cannot manage owner customers", exc_info.exception.detail)

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

    async def test_unlink_owner_customer_returns_serialized_relation(self):
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
            "api.routers.customers.unlink_owner_customer_relation",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.customers.load_customer_relation_invitation_map",
            new=AsyncMock(return_value={"CUST-token": invitation}),
        ), patch(
            "api.routers.customers.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            result = await unlink_my_customer(9, context=context, db=FakeDB())

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

    async def test_list_my_customer_sessions_returns_active_customer_sessions(self):
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        relation = SimpleNamespace(customer_user_id=12)
        sessions = [
            SimpleNamespace(
                id="session-1",
                device_name="Chrome on Android",
                device_ip="10.0.0.10",
                platform=SimpleNamespace(value="web"),
                home_server="foreign",
                is_primary=True,
                is_active=True,
                created_at=datetime.utcnow(),
                last_active_at=datetime.utcnow(),
            )
        ]

        with patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=AsyncMock(return_value=relation),
        ) as relation_mock, patch(
            "api.routers.customers.get_active_sessions",
            new=AsyncMock(return_value=sessions),
        ) as sessions_mock:
            result = await list_my_customer_sessions(9, context=context, db=FakeDB())

        relation_mock.assert_awaited_once()
        sessions_mock.assert_awaited_once_with(unittest.mock.ANY, 12)
        self.assertEqual(result[0].device_name, "Chrome on Android")
        self.assertTrue(result[0].is_primary)

    async def test_customer_session_routes_reject_accountant_context(self):
        context = SimpleNamespace(is_accountant_context=True, owner_user=SimpleNamespace(id=7))

        with self.assertRaises(HTTPException) as exc_info:
            await list_my_customer_sessions(9, context=context, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_my_customer_session(
                9,
                "11111111-1111-1111-1111-111111111111",
                context=context,
                db=FakeDB(),
            )
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_get_active_owner_customer_relation_rejects_inactive_or_deleted_relation(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_owner_customer_relation(ExecuteDB(None), owner_user_id=7, relation_id=9)
        self.assertEqual(exc_info.exception.status_code, 404)

        base_relation = SimpleNamespace(
            deleted_at=None,
            status=CustomerRelationStatus.PENDING,
            customer_user_id=12,
            customer_user=SimpleNamespace(is_deleted=False),
        )
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_owner_customer_relation(ExecuteDB(base_relation), owner_user_id=7, relation_id=9)
        self.assertEqual(exc_info.exception.status_code, 400)

        deleted_relation = SimpleNamespace(
            deleted_at=datetime.utcnow(),
            status=CustomerRelationStatus.ACTIVE,
            customer_user_id=12,
            customer_user=SimpleNamespace(is_deleted=False),
        )
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_owner_customer_relation(ExecuteDB(deleted_relation), owner_user_id=7, relation_id=9)
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_get_active_owner_customer_relation_rejects_missing_or_deleted_customer_user(self):
        no_customer_relation = SimpleNamespace(
            deleted_at=None,
            status=CustomerRelationStatus.ACTIVE,
            customer_user_id=None,
            customer_user=None,
        )
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_owner_customer_relation(ExecuteDB(no_customer_relation), owner_user_id=7, relation_id=9)
        self.assertEqual(exc_info.exception.status_code, 400)

        deleted_customer_relation = SimpleNamespace(
            deleted_at=None,
            status=CustomerRelationStatus.ACTIVE,
            customer_user_id=12,
            customer_user=SimpleNamespace(is_deleted=True),
        )
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_owner_customer_relation(ExecuteDB(deleted_customer_relation), owner_user_id=7, relation_id=9)
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_get_active_customer_session_requires_active_customer_session(self):
        session_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

        with self.assertRaises(HTTPException) as exc_info:
            await get_active_customer_session(ExecuteDB(None), customer_user_id=12, session_id=session_id)

        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_customer_router_helpers_cover_blank_frontend_and_successful_lookups(self):
        with patch("api.routers.customers.settings", SimpleNamespace(frontend_url="   ")):
            self.assertIsNone(build_customer_registration_link("token-1"))

        active_relation = SimpleNamespace(
            deleted_at=None,
            status=CustomerRelationStatus.ACTIVE,
            customer_user_id=12,
            customer_user=SimpleNamespace(is_deleted=False),
        )
        resolved_relation = await get_active_owner_customer_relation(
            ExecuteDB(active_relation),
            owner_user_id=7,
            relation_id=9,
        )
        self.assertIs(resolved_relation, active_relation)

        session_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        active_session = SimpleNamespace(id=session_id)
        resolved_session = await get_active_customer_session(
            ExecuteDB(active_session),
            customer_user_id=12,
            session_id=session_id,
        )
        self.assertIs(resolved_session, active_session)

    async def test_terminate_my_customer_session_logs_out_selected_customer_session(self):
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))
        relation = SimpleNamespace(customer_user_id=12)
        session = SimpleNamespace(id=uuid.UUID("11111111-1111-1111-1111-111111111111"))
        promoted = SimpleNamespace(id=uuid.UUID("22222222-2222-2222-2222-222222222222"))

        with patch(
            "api.routers.customers.get_active_owner_customer_relation",
            new=AsyncMock(return_value=relation),
        ) as relation_mock, patch(
            "api.routers.customers.get_active_customer_session",
            new=AsyncMock(return_value=session),
        ) as session_mock, patch(
            "api.routers.customers.logout_session",
            new=AsyncMock(return_value=promoted),
        ) as logout_mock:
            result = await terminate_my_customer_session(
                9,
                "11111111-1111-1111-1111-111111111111",
                context=context,
                db=FakeDB(),
            )

        relation_mock.assert_awaited_once()
        session_mock.assert_awaited_once()
        logout_mock.assert_awaited_once_with(unittest.mock.ANY, session)
        self.assertEqual(result["terminated_session_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result["promoted_primary_session_id"], "22222222-2222-2222-2222-222222222222")

    async def test_terminate_my_customer_session_rejects_invalid_session_id(self):
        context = SimpleNamespace(is_accountant_context=False, owner_user=SimpleNamespace(id=7))

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_my_customer_session(9, "bad-session-id", context=context, db=FakeDB())

        self.assertEqual(exc_info.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()