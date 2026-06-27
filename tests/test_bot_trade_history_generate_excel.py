import os
import sys
import unittest
from collections import defaultdict
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from bot.handlers.trade_history import generate_excel
from models.trade import TradeType


def make_trade():
    return SimpleNamespace(
        id=1,
        trade_number=10001,
        responder_user_id=2,
        trade_type=TradeType.BUY,
        offer_user=SimpleNamespace(account_name="offer-owner"),
        responder_user=SimpleNamespace(account_name="responder"),
        commodity=SimpleNamespace(name="سکه"),
        quantity=3,
        price=150000,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )


class BotTradeHistoryGenerateExcelTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_excel_creates_xlsx_file(self):
        class DummyCell:
            def __init__(self, value=None):
                self.value = value
                self.fill = None
                self.font = None
                self.alignment = None

        class DummySheet:
            def __init__(self):
                self.title = ""
                self.sheet_view = SimpleNamespace(rightToLeft=False)
                self._cells = {}
                self.column_dimensions = defaultdict(lambda: SimpleNamespace(width=None))

            def cell(self, row, column, value=None):
                key = (row, column)
                cell = self._cells.setdefault(key, DummyCell())
                if value is not None:
                    cell.value = value
                return cell

        class DummyWorkbook:
            def __init__(self):
                self.active = DummySheet()

            def save(self, filename):
                with open(filename, "wb") as handle:
                    handle.write(b"xlsx")

        class DummyStyle:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        openpyxl_mod = ModuleType("openpyxl")
        openpyxl_mod.Workbook = DummyWorkbook
        styles_mod = ModuleType("openpyxl.styles")
        styles_mod.Font = DummyStyle
        styles_mod.Alignment = DummyStyle
        styles_mod.PatternFill = DummyStyle

        with patch.dict(sys.modules, {"openpyxl": openpyxl_mod, "openpyxl.styles": styles_mod}):
            filename = await generate_excel([make_trade()], SimpleNamespace(account_name="target"), SimpleNamespace(id=2))

        try:
            self.assertTrue(filename.endswith(".xlsx"))
            self.assertTrue(os.path.exists(filename))
            self.assertGreater(os.path.getsize(filename), 0)
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    async def test_generate_excel_self_history_records_counterparty_for_both_trade_sides(self):
        class DummyCell:
            def __init__(self, value=None):
                self.value = value
                self.fill = None
                self.font = None
                self.alignment = None

        class DummySheet:
            def __init__(self):
                self.title = ""
                self.sheet_view = SimpleNamespace(rightToLeft=False)
                self._cells = {}

            def cell(self, row, column, value=None):
                key = (row, column)
                cell = self._cells.setdefault(key, DummyCell())
                if value is not None:
                    cell.value = value
                return cell

        class DummyWorkbook:
            last_instance = None

            def __init__(self):
                self.active = DummySheet()
                DummyWorkbook.last_instance = self

            def save(self, filename):
                with open(filename, "wb") as handle:
                    handle.write(b"xlsx")

        class DummyStyle:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        openpyxl_mod = ModuleType("openpyxl")
        openpyxl_mod.Workbook = DummyWorkbook
        styles_mod = ModuleType("openpyxl.styles")
        styles_mod.Font = DummyStyle
        styles_mod.Alignment = DummyStyle
        styles_mod.PatternFill = DummyStyle

        buy_trade = make_trade()
        buy_trade.offer_user = SimpleNamespace(account_name="offer-owner")
        buy_trade.responder_user = SimpleNamespace(account_name="responder")

        sell_trade = make_trade()
        sell_trade.id = 2
        sell_trade.trade_number = 10002
        sell_trade.responder_user_id = 99
        sell_trade.trade_type = TradeType.BUY
        sell_trade.offer_user = SimpleNamespace(account_name="offer-owner")
        sell_trade.responder_user = SimpleNamespace(account_name="other-side")

        with patch.dict(sys.modules, {"openpyxl": openpyxl_mod, "openpyxl.styles": styles_mod}):
            filename = await generate_excel([buy_trade, sell_trade], None, SimpleNamespace(id=2))

        try:
            sheet = DummyWorkbook.last_instance.active
            headers = [sheet.cell(4, column).value for column in range(1, 9)]
            self.assertEqual(
                headers,
                ["ردیف", "شماره معامله", "تاریخ و ساعت", "طرف دیگر معامله", "نوع معامله", "کالا", "تعداد", "قیمت"],
            )
            self.assertEqual(sheet.cell(5, 4).value, "offer-owner")
            self.assertEqual(sheet.cell(6, 5).value, "فروش")
            self.assertEqual(sheet.cell(6, 4).value, "other-side")
        finally:
            if os.path.exists(filename):
                os.remove(filename)


if __name__ == "__main__":
    unittest.main()
