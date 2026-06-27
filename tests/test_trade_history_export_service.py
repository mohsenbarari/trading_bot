import os
import sys
import unittest
from datetime import date, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from core.services.trade_history_export_service import (
    build_trade_history_date_range_label,
    build_trade_history_export_rows,
    generate_trade_history_excel_file,
    generate_trade_history_pdf_file,
    resolve_counterparty_account_name_for_perspective,
    resolve_trade_type_label_for_perspective,
)
from models.trade import TradeType


def make_trade():
    return SimpleNamespace(
        trade_number=10001,
        responder_user_id=2,
        trade_type=TradeType.BUY,
        offer_user=SimpleNamespace(account_name="offer-side"),
        responder_user=SimpleNamespace(account_name="responder-side"),
        commodity=SimpleNamespace(name="سکه"),
        quantity=3,
        price=150000,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )


class TradeHistoryExportServiceTests(unittest.TestCase):
    def test_resolve_trade_type_label_from_perspective(self):
        trade = make_trade()
        self.assertEqual(resolve_trade_type_label_for_perspective(trade, 2), "خرید")
        self.assertEqual(resolve_trade_type_label_for_perspective(trade, 99), "فروش")

    def test_build_trade_history_date_range_label(self):
        self.assertEqual(build_trade_history_date_range_label(None, None), "بازه زمانی: همه تاریخ‌ها")
        self.assertIn("بازه زمانی:", build_trade_history_date_range_label(date(2026, 5, 1), date(2026, 5, 31)))

    def test_build_trade_history_date_range_label_single_sided_ranges(self):
        self.assertIn("از", build_trade_history_date_range_label(date(2026, 5, 1), None))
        self.assertIn("تا", build_trade_history_date_range_label(None, date(2026, 5, 31)))

    def test_build_trade_history_export_rows(self):
        rows = build_trade_history_export_rows([make_trade()], 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].trade_number, 10001)
        self.assertEqual(rows[0].trade_type_label, "خرید")
        self.assertEqual(rows[0].counterparty_name, "offer-side")
        self.assertRegex(rows[0].date_time_label, r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
        self.assertEqual(rows[0].commodity_name, "سکه")

    def test_build_trade_history_export_rows_prefers_canonical_commodity_relation(self):
        trade = make_trade()
        trade.commodity_name = "ربع"
        trade.commodity = SimpleNamespace(name="ربع بهار")

        rows = build_trade_history_export_rows([trade], 2)

        self.assertEqual(rows[0].commodity_name, "ربع بهار")

    def test_resolve_counterparty_account_name_from_perspective(self):
        trade = make_trade()
        trade.offer_user = SimpleNamespace(account_name="offer-side")
        trade.responder_user = SimpleNamespace(account_name="responder-side")

        self.assertEqual(resolve_counterparty_account_name_for_perspective(trade, 2), "offer-side")
        self.assertEqual(resolve_counterparty_account_name_for_perspective(trade, 99), "responder-side")

    def test_trade_type_and_counterparty_fallback_with_invalid_responder_id(self):
        trade = make_trade()
        trade.trade_type = object()
        trade.responder_user_id = "invalid"
        trade.offer_user = SimpleNamespace(account_name="offer-side")
        trade.responder_user = SimpleNamespace(account_name="responder-side")

        self.assertEqual(resolve_trade_type_label_for_perspective(trade, 2), "خرید")
        self.assertEqual(resolve_counterparty_account_name_for_perspective(trade, 2), "responder-side")

    def test_generate_trade_history_excel_file_creates_xlsx(self):
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
                self.column_dimensions = {key: SimpleNamespace(width=None) for key in ["A", "B", "C", "D", "E", "F", "G", "H"]}

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

        with patch.dict(sys.modules, {"openpyxl": openpyxl_mod, "openpyxl.styles": styles_mod}):
            filename = generate_trade_history_excel_file(
                subject_name="owner",
                date_range_label="بازه زمانی: همه تاریخ‌ها",
                rows=build_trade_history_export_rows([make_trade()], 2),
            )

        try:
            self.assertTrue(filename.endswith(".xlsx"))
            self.assertTrue(os.path.exists(filename))
            sheet = DummyWorkbook.last_instance.active
            headers = [sheet.cell(4, column).value for column in range(1, 9)]
            self.assertEqual(
                headers,
                ["ردیف", "شماره معامله", "تاریخ و ساعت", "طرف دیگر معامله", "نوع معامله", "کالا", "تعداد", "قیمت"],
            )
            row_values = [sheet.cell(5, column).value for column in range(1, 9)]
            self.assertEqual(row_values[0:2], [1, 10001])
            self.assertRegex(row_values[2], r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
            self.assertEqual(row_values[3:5], ["offer-side", "خرید"])
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    def test_generate_trade_history_pdf_file_creates_pdf(self):
        class DummyDoc:
            def __init__(self, filename, **kwargs):
                self.filename = filename

            def build(self, elements):
                with open(self.filename, "wb") as handle:
                    handle.write(b"pdf")

        class DummyTable:
            last_instance = None

            def __init__(self, data, colWidths=None):
                self.data = data
                self.colWidths = colWidths
                DummyTable.last_instance = self

            def setStyle(self, style):
                self.style = style

        class DummyParagraph:
            def __init__(self, text, style):
                self.text = text
                self.style = style

        class DummySpacer:
            def __init__(self, *args):
                self.args = args

        class DummyStyle:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        class DummyTTFont:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        reportlab_mod = ModuleType("reportlab")
        lib_mod = ModuleType("reportlab.lib")
        colors_mod = ModuleType("reportlab.lib.colors")
        colors_mod.white = "white"
        colors_mod.HexColor = lambda value: value
        pagesizes_mod = ModuleType("reportlab.lib.pagesizes")
        pagesizes_mod.A4 = (595, 842)
        pagesizes_mod.landscape = lambda page_size: (page_size[1], page_size[0])
        platypus_mod = ModuleType("reportlab.platypus")
        platypus_mod.SimpleDocTemplate = DummyDoc
        platypus_mod.Table = DummyTable
        platypus_mod.TableStyle = lambda commands: commands
        platypus_mod.Paragraph = DummyParagraph
        platypus_mod.Spacer = DummySpacer
        styles_mod = ModuleType("reportlab.lib.styles")
        styles_mod.getSampleStyleSheet = lambda: {}
        styles_mod.ParagraphStyle = DummyStyle
        pdfbase_mod = ModuleType("reportlab.pdfbase")
        pdfmetrics_mod = ModuleType("reportlab.pdfbase.pdfmetrics")
        pdfmetrics_mod.registerFont = lambda font: None
        ttfonts_mod = ModuleType("reportlab.pdfbase.ttfonts")
        ttfonts_mod.TTFont = DummyTTFont
        enums_mod = ModuleType("reportlab.lib.enums")
        enums_mod.TA_CENTER = 1
        enums_mod.TA_RIGHT = 2
        arabic_mod = ModuleType("arabic_reshaper")
        arabic_mod.reshape = lambda text: text
        bidi_mod = ModuleType("bidi")
        bidi_algo_mod = ModuleType("bidi.algorithm")
        bidi_algo_mod.get_display = lambda text: text

        with patch.dict(
            sys.modules,
            {
                "reportlab": reportlab_mod,
                "reportlab.lib": lib_mod,
                "reportlab.lib.colors": colors_mod,
                "reportlab.lib.pagesizes": pagesizes_mod,
                "reportlab.platypus": platypus_mod,
                "reportlab.lib.styles": styles_mod,
                "reportlab.pdfbase": pdfbase_mod,
                "reportlab.pdfbase.pdfmetrics": pdfmetrics_mod,
                "reportlab.pdfbase.ttfonts": ttfonts_mod,
                "reportlab.lib.enums": enums_mod,
                "arabic_reshaper": arabic_mod,
                "bidi": bidi_mod,
                "bidi.algorithm": bidi_algo_mod,
            },
        ):
            filename = generate_trade_history_pdf_file(
                subject_name="owner",
                date_range_label="بازه زمانی: همه تاریخ‌ها",
                rows=build_trade_history_export_rows([make_trade()], 2),
            )

        try:
            self.assertTrue(filename.endswith(".pdf"))
            self.assertTrue(os.path.exists(filename))
            self.assertEqual(
                DummyTable.last_instance.data[0],
                ["قیمت", "تعداد", "کالا", "نوع معامله", "طرف دیگر معامله", "تاریخ و ساعت", "شماره معامله", "ردیف"],
            )
            self.assertEqual(DummyTable.last_instance.data[1][-1], "1")
            self.assertEqual(DummyTable.last_instance.data[1][-2], "10001")
        finally:
            if os.path.exists(filename):
                os.remove(filename)


if __name__ == "__main__":
    unittest.main()
