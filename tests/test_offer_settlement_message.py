import unittest

from core.enums import SettlementType
from core.offer_settlement import trade_settlement_emoji, trade_settlement_message_line


class TradeSettlementMessageTests(unittest.TestCase):
    def test_cash_trade_uses_sun_marker(self):
        self.assertEqual(trade_settlement_emoji(SettlementType.CASH), "☀️")
        self.assertEqual(trade_settlement_message_line("cash"), "☀️ تسویه: نقد حاضر")

    def test_tomorrow_trade_uses_calendar_marker(self):
        self.assertEqual(trade_settlement_emoji(SettlementType.TOMORROW), "📆")
        self.assertEqual(trade_settlement_message_line("tomorrow"), "📆 تسویه: فردایی")


if __name__ == "__main__":
    unittest.main()
