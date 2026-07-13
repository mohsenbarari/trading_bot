import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.callbacks import CommodityCallback, TradeActionCallback
from bot.handlers.trade_utils import ACTION_NOOP, get_commodities_keyboard


class FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalars(self._values)


class FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return FakeExecuteResult(self._results.pop(0))


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeUtilsCommoditiesKeyboardSinglePageTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_commodities_keyboard_builds_rows_without_pagination(self):
        commodities = [
            SimpleNamespace(id=1, name="سکه"),
            SimpleNamespace(id=2, name="ربع"),
            SimpleNamespace(id=3, name="نیم"),
            SimpleNamespace(id=4, name="تمام"),
        ]
        session = FakeSession([commodities, commodities])

        with patch("bot.handlers.trade_utils.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            keyboard = await get_commodities_keyboard("buy", page=1, limit=9)

        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["سکه", "ربع", "نیم"])
        self.assertEqual([button.text for button in keyboard.inline_keyboard[1]], ["تمام"])
        self.assertNotIn(ACTION_NOOP, [button.callback_data for row in keyboard.inline_keyboard for button in row])
        self.assertEqual(
            keyboard.inline_keyboard[-1][0].callback_data,
            TradeActionCallback(action="back_to_settlement").pack(),
        )
        self.assertEqual(keyboard.inline_keyboard[-1][1].callback_data, TradeActionCallback(action="cancel").pack())
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, CommodityCallback(id=1).pack())


if __name__ == "__main__":
    unittest.main()
