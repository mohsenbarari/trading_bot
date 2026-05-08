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
        responder_user_id=2,
        trade_type=TradeType.BUY,
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


if __name__ == "__main__":
    unittest.main()