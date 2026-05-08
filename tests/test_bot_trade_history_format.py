import unittest
from datetime import datetime
from types import SimpleNamespace

from bot.handlers.trade_history import format_trade_history
from models.trade import TradeType


def make_trade(index, responder_user_id=2, current_user_id=2):
    return SimpleNamespace(
        responder_user_id=responder_user_id,
        trade_type=TradeType.BUY,
        commodity=SimpleNamespace(name=f"کالای {index}"),
        quantity=index,
        price=index * 1000,
        created_at=datetime(2026, 1, min(index, 28), 12, 0, 0),
    )


class BotTradeHistoryFormatTests(unittest.TestCase):
    def test_format_trade_history_handles_empty_result(self):
        text = format_trade_history([], SimpleNamespace(account_name="target"), current_user_id=2)

        self.assertIn("معامله", text)
        self.assertIn("یافت نشد", text)

    def test_format_trade_history_formats_entries_and_truncates_after_twenty(self):
        trades = [make_trade(index) for index in range(1, 22)]

        text = format_trade_history(trades, SimpleNamespace(account_name="target"), current_user_id=2)

        self.assertIn("📊 تاریخچه معاملات با target", text)
        self.assertIn("🟢 خرید کالای 1", text)
        self.assertIn("... و 1 معامله دیگر", text)


if __name__ == "__main__":
    unittest.main()