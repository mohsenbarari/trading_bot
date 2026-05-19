import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from bot.handlers.trade_history import get_trade_history


class FakeExecuteResult:
    def __init__(self, value=None, trades=None):
        self._value = value
        self._trades = trades

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._trades or []))


class FakeSession:
    def __init__(self, target_user, trades):
        self.target_user = target_user
        self.trades = trades
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        if self.calls == 1:
            return FakeExecuteResult(value=self.target_user)
        return FakeExecuteResult(trades=self.trades)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeHistoryQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_trade_history_returns_none_when_target_user_missing(self):
        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None, []))):
            target_user, trades = await get_trade_history(1, 2, months=3)

        self.assertIsNone(target_user)
        self.assertEqual(trades, [])

    async def test_get_trade_history_returns_target_user_and_trades(self):
        target_user = SimpleNamespace(id=2, account_name="other")
        trades = [SimpleNamespace(created_at=datetime(2026, 1, 1, 12, 0, 0))]
        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(target_user, trades))):
            result_user, result_trades = await get_trade_history(1, 2, months=1)

        self.assertEqual(result_user, target_user)
        self.assertEqual(result_trades, trades)

    async def test_get_trade_history_self_history_skips_target_lookup(self):
        trades = [SimpleNamespace(created_at=datetime(2026, 1, 1, 12, 0, 0))]

        class SelfHistorySession(FakeSession):
            async def execute(self, stmt):
                self.calls += 1
                return FakeExecuteResult(trades=self.trades)

        session = SelfHistorySession(None, trades)
        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            result_user, result_trades = await get_trade_history(1, 0, months=1)

        self.assertIsNone(result_user)
        self.assertEqual(result_trades, trades)


if __name__ == "__main__":
    unittest.main()