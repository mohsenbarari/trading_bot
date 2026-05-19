import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_history import export_excel


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}

    async def get_data(self):
        return dict(self.data)


def make_callback():
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=44), answer=AsyncMock()),
    )


class BotTradeHistoryExportExcelTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_excel_returns_early_without_user_and_uses_self_display_name(self):
        callback = make_callback()
        await export_excel(callback, SimpleNamespace(target_user_id=5), FakeState(), user=None, bot=SimpleNamespace())
        callback.answer.assert_not_awaited()

        callback = make_callback()
        bot = SimpleNamespace(send_document=AsyncMock())
        trades = [SimpleNamespace(id=1)]
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(None, trades))), patch(
            "bot.handlers.trade_history.generate_excel", new=AsyncMock(return_value="/tmp/out.xlsx")
        ), patch("bot.handlers.trade_history.FSInputFile", return_value="FILE") as file_mock, patch(
            "bot.handlers.trade_history.os.remove"
        ):
            await export_excel(callback, SimpleNamespace(target_user_id=2), FakeState(), user=SimpleNamespace(id=2), bot=bot)
        self.assertIn("trade_history_پروفایل من.xlsx", file_mock.call_args.kwargs["filename"])

    async def test_export_excel_warns_when_no_trades_exist(self):
        callback = make_callback()
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(SimpleNamespace(account_name="t"), []))):
            await export_excel(callback, SimpleNamespace(target_user_id=5), FakeState(), user=SimpleNamespace(id=2), bot=SimpleNamespace())

        callback.message.answer.assert_awaited_once()
        self.assertIn("برای دانلود وجود ندارد", callback.message.answer.await_args.args[0])

    async def test_export_excel_sends_document_and_handles_generation_error(self):
        callback = make_callback()
        bot = SimpleNamespace(send_document=AsyncMock())
        target_user = SimpleNamespace(account_name="target")
        trades = [SimpleNamespace(id=1)]

        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(target_user, trades))), patch(
            "bot.handlers.trade_history.generate_excel", new=AsyncMock(return_value="/tmp/out.xlsx")
        ), patch("bot.handlers.trade_history.FSInputFile", return_value="FILE"), patch(
            "bot.handlers.trade_history.os.remove"
        ) as remove_mock:
            await export_excel(callback, SimpleNamespace(target_user_id=5), FakeState({"history_months": 6}), user=SimpleNamespace(id=2), bot=bot)

        bot.send_document.assert_awaited_once()
        remove_mock.assert_called_once_with("/tmp/out.xlsx")

        callback = make_callback()
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(target_user, trades))), patch(
            "bot.handlers.trade_history.generate_excel", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await export_excel(callback, SimpleNamespace(target_user_id=5), FakeState(), user=SimpleNamespace(id=2), bot=bot)

        self.assertIn("خطا در ایجاد فایل", callback.message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()