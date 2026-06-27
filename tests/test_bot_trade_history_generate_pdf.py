import os
import sys
import unittest
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from bot.handlers.trade_history import generate_pdf
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


class BotTradeHistoryGeneratePdfTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_pdf_uses_shared_export_service_with_profile_subject_and_date_range(self):
        current_user = SimpleNamespace(id=2)
        trades = [make_trade()]

        with patch("bot.handlers.trade_history.build_trade_history_export_rows", return_value=["row"]) as rows_mock, patch(
            "bot.handlers.trade_history.build_trade_history_date_range_label",
            return_value="بازه زمانی: تست",
        ) as label_mock, patch(
            "bot.handlers.trade_history.generate_trade_history_pdf_file",
            return_value="/tmp/shared.pdf",
        ) as pdf_mock:
            filename = await generate_pdf(trades, None, current_user, months=6)

        self.assertEqual(filename, "/tmp/shared.pdf")
        rows_mock.assert_called_once_with(trades, 2)
        label_mock.assert_called_once()
        pdf_mock.assert_called_once_with(
            subject_name="پروفایل من",
            date_range_label="بازه زمانی: تست",
            rows=["row"],
        )

    async def test_generate_pdf_creates_pdf_file(self):
        class DummyDoc:
            def __init__(self, filename, **kwargs):
                self.filename = filename

            def build(self, elements):
                with open(self.filename, "wb") as handle:
                    handle.write(b"pdf")

        class DummyTable:
            def __init__(self, data, colWidths=None):
                self.data = data
                self.colWidths = colWidths

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

        dummy_doc = DummyDoc("/tmp/demo.pdf")
        dummy_doc.build([])
        self.assertTrue(os.path.exists(dummy_doc.filename))
        os.remove(dummy_doc.filename)

        dummy_table = DummyTable([[1]], colWidths=[42])
        dummy_table.setStyle([("LINEBELOW", 0, 0, -1, -1)])
        self.assertEqual(dummy_table.colWidths, [42])
        self.assertTrue(dummy_table.style)

        dummy_paragraph = DummyParagraph("text", "style")
        self.assertEqual(dummy_paragraph.text, "text")
        self.assertEqual(dummy_paragraph.style, "style")

        dummy_spacer = DummySpacer(1, 2)
        self.assertEqual(dummy_spacer.args, (1, 2))

        dummy_style = DummyStyle("name", fontName="Vazir")
        self.assertEqual(dummy_style.args, ("name",))
        self.assertEqual(dummy_style.kwargs["fontName"], "Vazir")

        dummy_font = DummyTTFont("Vazir", "fonts/Vazir.ttf")
        self.assertEqual(dummy_font.args, ("Vazir", "fonts/Vazir.ttf"))

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
            filename = await generate_pdf([make_trade()], SimpleNamespace(account_name="target"), SimpleNamespace(id=2))

        try:
            self.assertTrue(filename.endswith(".pdf"))
            self.assertTrue(os.path.exists(filename))
            self.assertGreater(os.path.getsize(filename), 0)
        finally:
            if os.path.exists(filename):
                os.remove(filename)


if __name__ == "__main__":
    unittest.main()
