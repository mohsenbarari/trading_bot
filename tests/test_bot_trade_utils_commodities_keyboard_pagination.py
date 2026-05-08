import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.callbacks import PageCallback
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


class BotTradeUtilsCommoditiesKeyboardPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_commodities_keyboard_adds_previous_and_next_page_buttons(self):
        all_commodities = [SimpleNamespace(id=index, name=f"کالا {index}") for index in range(1, 13)]
        page_two = [SimpleNamespace(id=index, name=f"کالا {index}") for index in range(10, 13)]
        session = FakeSession([all_commodities, page_two])

        with patch("bot.handlers.trade_utils.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            keyboard = await get_commodities_keyboard("sell", page=2, limit=9)

        pagination_row = keyboard.inline_keyboard[-2]
        self.assertEqual(pagination_row[0].text, "➡️ قبلی")
        self.assertEqual(pagination_row[0].callback_data, PageCallback(trade_type="sell", page=1).pack())
        self.assertEqual(pagination_row[1].text, "📄 2/2")
        self.assertEqual(pagination_row[1].callback_data, ACTION_NOOP)

        session = FakeSession([all_commodities, all_commodities[:9]])
        with patch("bot.handlers.trade_utils.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            keyboard = await get_commodities_keyboard("buy", page=1, limit=9)

        pagination_row = keyboard.inline_keyboard[-2]
        self.assertEqual(pagination_row[0].text, "📄 1/2")
        self.assertEqual(pagination_row[0].callback_data, ACTION_NOOP)
        self.assertEqual(pagination_row[1].text, "⬅️ بعدی")
        self.assertEqual(pagination_row[1].callback_data, PageCallback(trade_type="buy", page=2).pack())


if __name__ == "__main__":
    unittest.main()