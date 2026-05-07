import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

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
    async def test_get_my_trades_serializes_matching_rows(self):
        trades = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        db = FakeDB([FakeExecuteResult(values=trades)])
        current_user = SimpleNamespace(id=5)

        with patch(
            "api.routers.trades.trade_to_response",
            side_effect=[{"id": 1}, {"id": 2}],
        ) as response_mock:
            result = await get_my_trades(skip=0, limit=50, db=db, current_user=current_user)

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(response_mock.call_count, 2)
        self.assertEqual(response_mock.call_args_list[0].args[0], trades[0])

    async def test_get_trade_enforces_not_found_and_access_control(self):
        current_user = SimpleNamespace(id=5)

        with self.assertRaises(HTTPException) as exc_info:
            await get_trade(trade_id=7, db=FakeDB([FakeExecuteResult(single=None)]), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "معامله یافت نشد.")

        foreign_trade = SimpleNamespace(offer_user_id=9, responder_user_id=10)
        with self.assertRaises(HTTPException) as exc_info:
            await get_trade(
                trade_id=7,
                db=FakeDB([FakeExecuteResult(single=foreign_trade)]),
                current_user=current_user,
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما به این معامله دسترسی ندارید.")

    async def test_get_trade_returns_trade_for_participant(self):
        current_user = SimpleNamespace(id=5)
        trade = SimpleNamespace(id=7, offer_user_id=5, responder_user_id=10)

        with patch("api.routers.trades.trade_to_response", return_value={"id": 7}) as response_mock:
            result = await get_trade(
                trade_id=7,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                current_user=current_user,
            )

        response_mock.assert_called_once_with(trade)
        self.assertEqual(result, {"id": 7})

    async def test_get_trades_with_user_short_circuits_self_and_serializes_history(self):
        current_user = SimpleNamespace(id=5)
        self.assertEqual(
            await get_trades_with_user(other_user_id=5, skip=0, limit=20, db=FakeDB(), current_user=current_user),
            [],
        )

        trades = [SimpleNamespace(id=11)]
        with patch("api.routers.trades.trade_to_response", return_value={"id": 11}) as response_mock:
            result = await get_trades_with_user(
                other_user_id=7,
                skip=0,
                limit=20,
                db=FakeDB([FakeExecuteResult(values=trades)]),
                current_user=current_user,
            )

        response_mock.assert_called_once_with(trades[0])
        self.assertEqual(result, [{"id": 11}])


if __name__ == "__main__":
    unittest.main()