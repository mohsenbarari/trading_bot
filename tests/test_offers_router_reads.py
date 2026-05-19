import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.deps import EffectiveOwnerActor
from api.routers.offers import ParseOfferRequest, get_active_offers, get_my_offers, parse_offer_text


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
        current_user = SimpleNamespace(id=5)

        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new=AsyncMock(return_value=(None, SimpleNamespace(message="bad format"))),
        ):
            result = await parse_offer_text(ParseOfferRequest(text="foo"), current_user=current_user)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "bad format")

        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new=AsyncMock(return_value=(None, None)),
        ):
            result = await parse_offer_text(ParseOfferRequest(text="foo"), current_user=current_user)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "متن قابل تشخیص نیست.")

        parsed = SimpleNamespace(
            trade_type="buy",
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
            result = await parse_offer_text(ParseOfferRequest(text="foo"), current_user=current_user)
        self.assertTrue(result.success)
        self.assertEqual(
            result.data,
            {
                "trade_type": "buy",
                "commodity_id": 3,
                "commodity_name": "Gold",
                "quantity": 10,
                "price": 123456,
                "is_wholesale": False,
                "lot_sizes": [4, 3, 3],
                "notes": "urgent",
            },
        )

    async def test_get_active_offers_serializes_rows_for_viewer(self):
        offers = [SimpleNamespace(id=1, user_id=501), SimpleNamespace(id=2, user_id=502)]
        db = FakeDB([FakeExecuteResult(offers)])
        context = self.make_context(owner_id=77, actor_id=99)
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

    async def test_get_my_offers_serializes_rows_with_owner_identity(self):
        offers = [SimpleNamespace(id=5, user_id=88)]
        db = FakeDB([FakeExecuteResult(offers)])
        context = self.make_context(owner_id=88, actor_id=144)
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

    async def test_get_my_offers_uses_effective_owner_view_when_actor_differs(self):
        offers = [SimpleNamespace(id=9, user_id=61)]
        db = FakeDB([FakeExecuteResult(offers)])
        context = self.make_context(owner_id=61, actor_id=62)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=30)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({}, None)),
        ), patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 9, "user_id": 61},
        ) as response_mock:
            await get_my_offers(
                status_filter=None,
                since_hours=None,
                skip=0,
                limit=50,
                db=db,
                context=context,
            )

        self.assertEqual(response_mock.call_args.kwargs["viewer_user_id"], 61)


if __name__ == "__main__":
    unittest.main()