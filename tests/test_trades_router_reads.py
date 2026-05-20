import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.deps import EffectiveOwnerActor
from api.routers.trades import get_my_trades, get_trade, get_trades_with_user


class FakeScalarRows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values=None, single=None):
        self._values = values or []
        self._single = single

    def scalars(self):
        return FakeScalarRows(self._values)

    def scalar_one_or_none(self):
        return self._single


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class TradesRouterReadTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_context(owner_id=5, actor_id=None):
        owner_user = SimpleNamespace(id=owner_id)
        actor_user = SimpleNamespace(id=actor_id if actor_id is not None else owner_id)
        return EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=owner_user.id != actor_user.id,
        )

    async def test_get_my_trades_serializes_matching_rows(self):
        trades = [
            SimpleNamespace(id=1, offer_user_id=5, responder_user_id=11),
            SimpleNamespace(id=2, offer_user_id=12, responder_user_id=5),
        ]
        db = FakeDB([FakeExecuteResult(values=trades)])
        context = self.make_context(owner_id=5, actor_id=11)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=[{"id": 1}, {"id": 2}],
        ) as response_mock:
            result = await get_my_trades(skip=0, limit=50, db=db, context=context)

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        identity_mock.assert_awaited_once()
        self.assertEqual(response_mock.call_count, 2)
        self.assertEqual(response_mock.call_args_list[0].args[0], trades[0])
        self.assertEqual(response_mock.call_args_list[0].kwargs["identity_map"], {})

    async def test_get_trade_enforces_not_found_and_access_control(self):
        context = self.make_context(owner_id=5, actor_id=12)

        with self.assertRaises(HTTPException) as exc_info:
            await get_trade(trade_id=7, db=FakeDB([FakeExecuteResult(single=None)]), context=context)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "معامله یافت نشد.")

        foreign_trade = SimpleNamespace(offer_user_id=9, responder_user_id=10)
        with self.assertRaises(HTTPException) as exc_info:
            await get_trade(
                trade_id=7,
                db=FakeDB([FakeExecuteResult(single=foreign_trade)]),
                context=context,
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما به این معامله دسترسی ندارید.")

    async def test_get_trade_returns_trade_for_participant(self):
        context = self.make_context(owner_id=5, actor_id=12)
        trade = SimpleNamespace(id=7, offer_user_id=5, responder_user_id=10)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch("api.routers.trades.trade_to_response", return_value={"id": 7}) as response_mock:
            result = await get_trade(
                trade_id=7,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        response_mock.assert_called_once_with(trade, identity_map={})
        self.assertEqual(result, {"id": 7})

    async def test_get_trades_with_user_short_circuits_self_and_serializes_history(self):
        context = self.make_context(owner_id=5, actor_id=12)
        self.assertEqual(
            await get_trades_with_user(other_user_id=5, skip=0, limit=20, db=FakeDB(), context=context),
            [],
        )

        trades = [SimpleNamespace(id=11, offer_user_id=5, responder_user_id=7)]
        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch("api.routers.trades.trade_to_response", return_value={"id": 11}) as response_mock:
            result = await get_trades_with_user(
                other_user_id=7,
                skip=0,
                limit=20,
                db=FakeDB([FakeExecuteResult(values=trades)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        response_mock.assert_called_once_with(trades[0], identity_map={})
        self.assertEqual(result, [{"id": 11}])

    async def test_get_trade_allows_effective_owner_when_actor_differs(self):
        context = self.make_context(owner_id=51, actor_id=52)
        trade = SimpleNamespace(id=17, offer_user_id=9, responder_user_id=51)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch("api.routers.trades.trade_to_response", return_value={"id": 17}) as response_mock:
            result = await get_trade(
                trade_id=17,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        response_mock.assert_called_once_with(trade, identity_map={})
        self.assertEqual(result, {"id": 17})


if __name__ == "__main__":
    unittest.main()