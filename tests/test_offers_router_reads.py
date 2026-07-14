import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.deps import EffectiveOwnerActor
from api.routers.offers import (
    ParseOfferRequest,
    build_offer_read_options,
    get_active_offers,
    get_my_offers,
    get_my_repeatable_offers,
    parse_offer_text,
)
from models.customer_relation import CustomerTier


class FakeScalarRows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalarRows(self._values)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class OffersRouterReadTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_context(owner_id=77, actor_id=None):
        owner_user = SimpleNamespace(id=owner_id)
        actor_user = SimpleNamespace(id=actor_id if actor_id is not None else owner_id)
        return EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=owner_user.id != actor_user.id,
        )

    async def test_parse_offer_text_handles_error_unrecognized_and_success(self):
        context = self.make_context(owner_id=5)

        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new=AsyncMock(return_value=(None, SimpleNamespace(message="bad format"))),
        ):
            result = await parse_offer_text(ParseOfferRequest(text="foo"), context=context)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "bad format")

        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new=AsyncMock(return_value=(None, None)),
        ):
            result = await parse_offer_text(ParseOfferRequest(text="foo"), context=context)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "متن قابل تشخیص نیست.")

        parsed = SimpleNamespace(
            trade_type="buy",
            settlement_type="tomorrow",
            commodity_id=3,
            commodity_name="Gold",
            quantity=10,
            price=123456,
            is_wholesale=False,
            lot_sizes=[4, 3, 3],
            notes="urgent",
        )
        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new=AsyncMock(return_value=(parsed, None)),
        ):
            result = await parse_offer_text(ParseOfferRequest(text="foo"), context=context)
        self.assertTrue(result.success)
        self.assertEqual(
            result.data,
            {
                "trade_type": "buy",
                "settlement_type": "tomorrow",
                "commodity_id": 3,
                "commodity_name": "Gold",
                "quantity": 10,
                "price": 123456,
                "is_wholesale": False,
                "lot_sizes": [4, 3, 3],
                "notes": "urgent",
            },
        )

    async def test_parse_offer_text_rejects_accountant_context(self):
        with self.assertRaises(HTTPException) as exc_info:
            await parse_offer_text(ParseOfferRequest(text="foo"), context=self.make_context(owner_id=5, actor_id=9))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")

    async def test_offer_read_options_only_load_owner_when_identity_is_required(self):
        self.assertEqual(len(build_offer_read_options(include_owner_identity=False)), 1)
        self.assertEqual(len(build_offer_read_options(include_owner_identity=True)), 2)

    async def test_get_active_offers_serializes_rows_for_viewer(self):
        offers = [SimpleNamespace(id=1, user_id=501), SimpleNamespace(id=2, user_id=502)]
        db = FakeDB([FakeExecuteResult(offers)])
        context = self.make_context(owner_id=77)
        owner_relation = SimpleNamespace(customer_user_id=501, owner_user_id=77)
        viewer_relation = SimpleNamespace(customer_user_id=77, owner_user_id=900)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=15)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({501: owner_relation}, viewer_relation)),
        ), patch(
            "api.routers.offers.offer_to_response",
            side_effect=[{"id": 1}, {"id": 2}],
        ) as response_mock:
            result = await get_active_offers(
                offer_type=None,
                commodity_id=None,
                skip=0,
                limit=50,
                db=db,
                context=context,
            )

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(response_mock.call_count, 2)
        first_call = response_mock.call_args_list[0]
        self.assertEqual(first_call.args[0], offers[0])
        self.assertEqual(first_call.args[1].offer_expiry_minutes, 15)
        self.assertEqual(first_call.kwargs["viewer_user_id"], 77)
        self.assertFalse(first_call.kwargs["include_owner_identity"])
        self.assertIs(first_call.kwargs["offer_owner_relation"], owner_relation)
        self.assertIs(first_call.kwargs["viewer_customer_relation"], viewer_relation)

    async def test_get_active_offers_returns_empty_without_extra_read_context(self):
        db = FakeDB([FakeExecuteResult([])])
        context = self.make_context(owner_id=77)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(side_effect=AssertionError("settings should not be loaded for empty offers")),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(side_effect=AssertionError("read context should not be loaded for empty offers")),
        ):
            result = await get_active_offers(
                offer_type=None,
                commodity_id=None,
                skip=0,
                limit=50,
                db=db,
                context=context,
            )

        self.assertEqual(result, [])

    async def test_get_active_offers_rejects_accountant_context(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_active_offers(
                offer_type=None,
                commodity_id=None,
                skip=0,
                limit=50,
                db=FakeDB(),
                context=self.make_context(owner_id=77, actor_id=99),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")

    async def test_get_my_offers_serializes_rows_with_owner_identity(self):
        offers = [SimpleNamespace(id=5, user_id=88)]
        db = FakeDB([FakeExecuteResult(offers)])
        context = self.make_context(owner_id=88)
        owner_relation = SimpleNamespace(customer_user_id=88, owner_user_id=500)
        viewer_relation = SimpleNamespace(customer_user_id=88, owner_user_id=500)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=30)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({88: owner_relation}, viewer_relation)),
        ), patch(
            "api.routers.offers.offer_to_response",
            side_effect=[{"id": 5, "user_id": 88}],
        ) as response_mock:
            result = await get_my_offers(
                status_filter=None,
                since_hours=None,
                skip=0,
                limit=50,
                db=db,
                context=context,
            )

        self.assertEqual(result, [{"id": 5, "user_id": 88}])
        self.assertEqual(response_mock.call_count, 1)
        call = response_mock.call_args_list[0]
        self.assertEqual(call.args[0], offers[0])
        self.assertEqual(call.kwargs["viewer_user_id"], 88)
        self.assertTrue(call.kwargs["include_owner_identity"])
        self.assertIs(call.kwargs["offer_owner_relation"], owner_relation)
        self.assertIs(call.kwargs["viewer_customer_relation"], viewer_relation)

    async def test_get_my_offers_returns_empty_without_extra_read_context(self):
        db = FakeDB([FakeExecuteResult([])])
        context = self.make_context(owner_id=88)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(side_effect=AssertionError("settings should not be loaded for empty offers")),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(side_effect=AssertionError("read context should not be loaded for empty offers")),
        ):
            result = await get_my_offers(
                status_filter=None,
                since_hours=None,
                skip=0,
                limit=50,
                db=db,
                context=context,
            )

        self.assertEqual(result, [])

    async def test_get_my_offers_rejects_accountant_context(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_my_offers(
                status_filter=None,
                since_hours=None,
                skip=0,
                limit=50,
                db=FakeDB(),
                context=self.make_context(owner_id=61, actor_id=62),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")

    async def test_get_my_repeatable_offers_uses_current_session_service(self):
        offers = [SimpleNamespace(id=15, user_id=88)]
        context = self.make_context(owner_id=88)
        serialized = [{"id": 15, "user_id": 88}]

        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.list_repeatable_offers",
            new=AsyncMock(return_value=offers),
        ) as list_mock, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=30)),
        ), patch(
            "api.routers.offers._serialize_offer_responses",
            new=AsyncMock(return_value=serialized),
        ) as serialize_mock:
            result = await get_my_repeatable_offers(
                limit=3,
                db=SimpleNamespace(),
                context=context,
            )

        self.assertEqual(result, serialized)
        list_mock.assert_awaited_once()
        self.assertEqual(list_mock.await_args.kwargs["owner_user_id"], 88)
        self.assertEqual(list_mock.await_args.kwargs["limit"], 3)
        serialize_mock.assert_awaited_once()

    async def test_get_my_repeatable_offers_hides_tier2_customer(self):
        context = self.make_context(owner_id=88)
        relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2)

        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.offers.list_repeatable_offers",
            new=AsyncMock(),
        ) as list_mock:
            result = await get_my_repeatable_offers(
                limit=3,
                db=SimpleNamespace(),
                context=context,
            )

        self.assertEqual(result, [])
        list_mock.assert_not_awaited()

    async def test_get_my_repeatable_offers_rejects_accountant_context(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_my_repeatable_offers(
                limit=3,
                db=SimpleNamespace(),
                context=self.make_context(owner_id=61, actor_id=62),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")


if __name__ == "__main__":
    unittest.main()
