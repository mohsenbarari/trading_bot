from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from importlib import import_module
from pathlib import Path
from typing import Sequence

from core.utils import to_jalali_str
from models.trade import TradeType


@dataclass(frozen=True)
class TradeHistoryExportRow:
    trade_number: int | None
    trade_type_label: str
    commodity_name: str
    quantity: int
    price: int
    date_label: str
    time_label: str


def _normalize_trade_type_value(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = getattr(value, "value")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def resolve_trade_type_label_for_perspective(trade: object, perspective_user_id: int) -> str:
    normalized_trade_type = _normalize_trade_type_value(getattr(trade, "trade_type", None))
    responder_user_id = getattr(trade, "responder_user_id", None)
    try:
        normalized_responder_user_id = int(responder_user_id)
    except (TypeError, ValueError):
        normalized_responder_user_id = None
    is_responder_perspective = normalized_responder_user_id == int(perspective_user_id)

    if is_responder_perspective:
        is_buy = normalized_trade_type == TradeType.BUY.value
    else:
        is_buy = normalized_trade_type != TradeType.BUY.value

    return "خرید" if is_buy else "فروش"


def _participant_display_name(user: object | None) -> str:
    return (
        getattr(user, "customer_management_name", None)
        or getattr(user, "account_name", None)
        or ""
    )


def resolve_counterparty_account_name_for_perspective(trade: object, perspective_user_id: int) -> str:
    responder_user_id = getattr(trade, "responder_user_id", None)
    try:
        normalized_responder_user_id = int(responder_user_id)
    except (TypeError, ValueError):
        normalized_responder_user_id = None

    if normalized_responder_user_id == int(perspective_user_id):
        return _participant_display_name(getattr(trade, "offer_user", None))
    return _participant_display_name(getattr(trade, "responder_user", None))


def build_trade_history_export_rows(trades: Sequence[object], perspective_user_id: int) -> list[TradeHistoryExportRow]:
    rows: list[TradeHistoryExportRow] = []
    for trade in trades:
        rows.append(
            TradeHistoryExportRow(
                trade_number=getattr(trade, "trade_number", None),
                trade_type_label=resolve_trade_type_label_for_perspective(trade, perspective_user_id),
                commodity_name=getattr(getattr(trade, "commodity", None), "name", "---"),
                quantity=int(getattr(trade, "quantity", 0) or 0),
                price=int(getattr(trade, "price", 0) or 0),
                date_label=to_jalali_str(getattr(trade, "created_at", None), "%Y/%m/%d") or "---",
                time_label=to_jalali_str(getattr(trade, "created_at", None), "%H:%M:%S") or "---",
            )
        )
    return rows


def _jalali_date_label(value: date) -> str:
    return to_jalali_str(datetime.combine(value, time.min), "%Y/%m/%d") or value.isoformat()


def build_trade_history_date_range_label(from_date: date | None, to_date: date | None) -> str:
    if from_date and to_date:
        return f"بازه زمانی: {_jalali_date_label(from_date)} تا {_jalali_date_label(to_date)}"
    if from_date:
        return f"بازه زمانی: از {_jalali_date_label(from_date)}"
    if to_date:
        return f"بازه زمانی: تا {_jalali_date_label(to_date)}"
    return "بازه زمانی: همه تاریخ‌ها"


def _shape_rtl_text(text: str) -> str:
    arabic_reshaper = import_module("arabic_reshaper")
    bidi_algorithm = import_module("bidi.algorithm")
    return bidi_algorithm.get_display(arabic_reshaper.reshape(text))


def _history_table_headers() -> list[str]:
    return [
        "ردیف",
        "شماره معامله",
        "نوع معامله",
        "کالا",
        "تعداد",
        "قیمت",
        "تاریخ",
        "ساعت",
    ]


def generate_trade_history_excel_file(
    *,
    subject_name: str,
    date_range_label: str,
    rows: Sequence[TradeHistoryExportRow],
) -> str:
    openpyxl = import_module("openpyxl")
    openpyxl_styles = import_module("openpyxl.styles")

    Workbook = openpyxl.Workbook
    Font = openpyxl_styles.Font
    Alignment = openpyxl_styles.Alignment
    PatternFill = openpyxl_styles.PatternFill

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Trade History"
    worksheet.sheet_view.rightToLeft = True

    title_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_fill = PatternFill(start_color="B45309", end_color="B45309", fill_type="solid")
    title_font = Font(bold=True, color="FFFFFF", size=13)
    header_font = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal="center")

    worksheet.cell(row=1, column=1, value=f"تاریخچه معاملات {subject_name}")
    worksheet.cell(row=2, column=1, value=date_range_label)
    for row_index in (1, 2):
        cell = worksheet.cell(row=row_index, column=1)
        cell.fill = title_fill
        cell.font = title_font
        cell.alignment = center

    headers = _history_table_headers()
    for column_index, header in enumerate(headers, start=1):
        cell = worksheet.cell(row=4, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center

    for row_index, row in enumerate(rows, start=5):
        row_values = [
            row_index - 4,
            row.trade_number or "-",
            row.trade_type_label,
            row.commodity_name,
            row.quantity,
            row.price,
            row.date_label,
            row.time_label,
        ]
        for column_index, value in enumerate(row_values, start=1):
            cell = worksheet.cell(row=row_index, column=column_index, value=value)
            cell.alignment = center

    if hasattr(worksheet, "column_dimensions"):
        widths = {"A": 8, "B": 18, "C": 14, "D": 18, "E": 12, "F": 18, "G": 16, "H": 14}
        for column_name, width in widths.items():
            worksheet.column_dimensions[column_name].width = width

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    workbook.save(temp_file.name)
    temp_file.close()
    return temp_file.name


def generate_trade_history_pdf_file(
    *,
    subject_name: str,
    date_range_label: str,
    rows: Sequence[TradeHistoryExportRow],
) -> str:
    reportlab_colors = import_module("reportlab.lib.colors")
    reportlab_pagesizes = import_module("reportlab.lib.pagesizes")
    reportlab_platypus = import_module("reportlab.platypus")
    reportlab_styles = import_module("reportlab.lib.styles")
    reportlab_pdfmetrics = import_module("reportlab.pdfbase.pdfmetrics")
    reportlab_ttfonts = import_module("reportlab.pdfbase.ttfonts")
    reportlab_enums = import_module("reportlab.lib.enums")

    font_path = Path(__file__).resolve().parents[2] / "fonts" / "Vazir.ttf"
    font_name = "Vazir"
    reportlab_pdfmetrics.registerFont(reportlab_ttfonts.TTFont(font_name, str(font_path)))

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_file.close()

    document = reportlab_platypus.SimpleDocTemplate(temp_file.name, pagesize=reportlab_pagesizes.A4)
    base_styles = reportlab_styles.getSampleStyleSheet()
    normal_parent = base_styles.get("Normal") if hasattr(base_styles, "get") else None
    title_style = reportlab_styles.ParagraphStyle(
        "TradeHistoryTitle",
        parent=normal_parent,
        fontName=font_name,
        fontSize=14,
        alignment=reportlab_enums.TA_CENTER,
    )
    body_style = reportlab_styles.ParagraphStyle(
        "TradeHistoryBody",
        parent=normal_parent,
        fontName=font_name,
        fontSize=10,
        alignment=reportlab_enums.TA_RIGHT,
    )

    table_data = [[_shape_rtl_text(header) for header in _history_table_headers()]]
    for index, row in enumerate(rows, start=1):
        table_data.append(
            [
                str(index),
                str(row.trade_number or "-"),
                _shape_rtl_text(row.trade_type_label),
                _shape_rtl_text(row.commodity_name),
                str(row.quantity),
                f"{row.price:,}",
                row.date_label,
                row.time_label,
            ]
        )

    table = reportlab_platypus.Table(
        table_data,
        colWidths=[34, 64, 58, 72, 48, 68, 72, 56],
    )
    table.setStyle(
        reportlab_platypus.TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), reportlab_colors.HexColor("#B45309")),
                ("TEXTCOLOR", (0, 0), (-1, 0), reportlab_colors.white),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, reportlab_colors.HexColor("#D1D5DB")),
            ]
        )
    )

    elements = [
        reportlab_platypus.Paragraph(_shape_rtl_text(f"تاریخچه معاملات {subject_name}"), title_style),
        reportlab_platypus.Spacer(1, 10),
        reportlab_platypus.Paragraph(_shape_rtl_text(date_range_label), body_style),
        reportlab_platypus.Spacer(1, 14),
        table,
    ]
    document.build(elements)
    return temp_file.name
