from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import PriceEvent


PARSER_VERSION = "rules-v7-repository-shadow"

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,9})(?!\d)")
_SOURCE_DATETIME_RE = re.compile(
    r"\[?(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\]?"
)
_SOURCE_TIME_RE = re.compile(r"(?:Just\s+In\s+Time\s+)?(\d{1,2}:\d{2}:\d{2})", re.I)


def normalize_text(value: str) -> str:
    value = value.translate(_DIGIT_TRANSLATION)
    value = value.replace("ي", "ی").replace("ك", "ک").replace("ـ", "")
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    normalized_lines = []
    for line in value.splitlines():
        normalized_lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return "\n".join(line for line in normalized_lines if line)


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _largest_price(line: str) -> Decimal | None:
    candidates = [_decimal(match) for match in _PRICE_RE.findall(line)]
    valid = [candidate for candidate in candidates if candidate is not None and candidate >= 1_000]
    return max(valid) if valid else None


def _movement(line: str) -> str:
    if "🔺" in line or "⬆" in line:
        return "UP"
    if "🔻" in line or "⬇" in line:
        return "DOWN"
    return "UNKNOWN"


def _source_datetime(text: str) -> str | None:
    match = _SOURCE_DATETIME_RE.search(text)
    if match:
        return match.group(1)
    match = _SOURCE_TIME_RE.search(text)
    return match.group(1) if match else None


def _trade_form(text: str, *, default: str = "PHYSICAL") -> str:
    """Classify paper/transfer markets independently from settlement time."""
    if re.search(r"(?:کاغذی|حواله|غیررسمی)", text):
        return "PAPER"
    if re.search(r"(?:نقدی|نقد|فیزیکی|تحویل)", text):
        return "PHYSICAL"
    return default


def _settlement(text: str) -> str:
    """Read settlement only from explicit today/tomorrow language.

    Cash/physical and paper/transfer describe the delivery form, not the
    settlement date.  Keeping those axes independent prevents a physical
    quote from being silently relabelled as a same-day quote.
    """
    if re.search(r"(?:فردا|فردایی)", text):
        return "TOMORROW"
    if re.search(r"(?:امروز|امروزی)", text):
        return "TODAY"
    return "UNKNOWN"


def _event_kind_and_side(text: str) -> tuple[str, str]:
    if "معامله" in text:
        return "TRADE", "UNKNOWN"
    if "خرید" in text:
        return "OFFER", "BUY"
    if "فروش" in text:
        return "OFFER", "SELL"
    return "QUOTE", "UNKNOWN"


def parse_abshdh(text: str) -> list[PriceEvent]:
    normalized = normalize_text(text)
    events: list[PriceEvent] = []
    source_time = _source_datetime(normalized)
    lines = normalized.splitlines()

    # Union quote messages put their label and price on separate lines.
    for index, line in enumerate(lines):
        if not re.search(r"#\s*مظنه\s*اتحادیه", line):
            continue
        price_line = line
        if _largest_price(price_line) is None and index + 1 < len(lines):
            price_line = lines[index + 1]
        price = _largest_price(price_line)
        if price is None:
            continue
        events.append(
            PriceEvent(
                instrument="GOLD_UNION_QUOTE",
                market_label="مظنه اتحادیه",
                price=price,
                currency="IRT",
                price_unit="IRT_PER_MESGHAL_750",
                movement=_movement(f"{line} {price_line}"),
                source_datetime_text=source_time,
                parse_confidence=0.99,
                parser_version=PARSER_VERSION,
            )
        )

    for line in lines:
        compact = line.replace("_", " ").replace("-", " ")
        compact = re.sub(r"\s+", " ", compact)
        price = _largest_price(compact)
        if price is None:
            continue

        melted_match = re.search(
            (
                r"#\s*(?:آبشده|ابشده)\s*"
                r"(حواله|نقدی|رسمی|امروزی|فردایی|فردا|غیررسمی|جهانی)?"
            ),
            compact,
        )
        if melted_match:
            subtype = melted_match.group(1) or "نامشخص"
            if subtype == "فردا":
                subtype = "فردایی"
            # Melted-gold domain rule: only explicit cash/official quotes are
            # physical cash. Every other subtype is a paper market quote.
            trade_form = (
                "PHYSICAL" if subtype in {"نقدی", "رسمی"} else "PAPER"
            )
            settlement = _settlement(compact)
            event_type, side = _event_kind_and_side(compact)
            events.append(
                PriceEvent(
                    instrument="MELTED_GOLD",
                    market_label=f"آبشده {subtype}",
                    price=price,
                    currency="IRT",
                    price_unit="IRT_PER_MESGHAL_750",
                    settlement_term=settlement,
                    trade_form=trade_form,
                    event_type=event_type,
                    side=side,
                    movement=_movement(compact),
                    source_datetime_text=source_time,
                    parse_confidence=0.98,
                    parser_version=PARSER_VERSION,
                )
            )
            continue

        coin_match = re.search(r"#\s*سکه\s*(نقدی|حواله)?", compact)
        if coin_match:
            subtype = coin_match.group(1) or "نامشخص"
            event_type, side = _event_kind_and_side(compact)
            events.append(
                PriceEvent(
                    instrument="GOLD_COIN",
                    market_label=f"سکه {subtype}",
                    price=price,
                    currency="TOMAN",
                    price_unit="TOMAN_PER_COIN",
                    settlement_term=_settlement(compact),
                    trade_form=_trade_form(compact),
                    event_type=event_type,
                    side=side,
                    movement=_movement(compact),
                    source_datetime_text=source_time,
                    parse_confidence=0.99,
                    parser_version=PARSER_VERSION,
                )
            )
            continue

        if re.search(r"#\s*درهم\s*دبی", compact):
            events.append(
                PriceEvent(
                    instrument="AED_DUBAI",
                    market_label="درهم دبی",
                    price=price,
                    currency="TOMAN",
                    price_unit="TOMAN_PER_AED",
                    movement=_movement(compact),
                    source_datetime_text=source_time,
                    parse_confidence=0.99,
                    parser_version=PARSER_VERSION,
                )
            )
            continue

    return events


def parse_naghdp(text: str) -> list[PriceEvent]:
    """Parse the explicit paper-market flow feed without emitting gram prices."""
    normalized = normalize_text(text)
    first_line = normalized.splitlines()[0] if normalized else ""
    price = _largest_price(first_line)
    if price is None:
        return []

    if "امروز" in first_line:
        settlement = "TODAY"
        settlement_label = "امروز"
    elif re.search(r"(?:با\s*حواله|فردا|فردایی)", first_line):
        settlement = "TOMORROW"
        settlement_label = "فردا"
    else:
        return []

    if "معامله" in first_line:
        event_type = "TRADE"
        # The completed-trade post omits the side. A contextual enrichment
        # pass links it to the latest same-price/same-settlement offer.
        side = "UNKNOWN"
        confidence = 0.98
    elif "خرید" in first_line:
        event_type = "OFFER"
        side = "BUY"
        confidence = 0.995
    elif "فروش" in first_line:
        event_type = "OFFER"
        side = "SELL"
        confidence = 0.995
    else:
        return []

    return [
        PriceEvent(
            instrument="MELTED_GOLD_FLOW",
            market_label=f"جریان آبشده {settlement_label}",
            price=price,
            currency="IRT",
            price_unit="IRT_PER_MESGHAL_750",
            settlement_term=settlement,
            trade_form="PAPER",
            event_type=event_type,
            side=side,
            parse_confidence=confidence,
            parser_version=PARSER_VERSION,
        )
    ]


def parse_ounce(text: str) -> list[PriceEvent]:
    normalized = normalize_text(text)
    match = re.search(r"(?<![\d.])(\d{3,5}\.\d{1,4})(?![\d.])", normalized)
    if not match:
        return []

    price = _decimal(match.group(1))
    if price is None:
        return []

    movement = "UNKNOWN"
    if "🔵" in normalized:
        movement = "UP"
    elif "🔴" in normalized:
        movement = "DOWN"

    return [
        PriceEvent(
            instrument="XAUUSD",
            market_label="اونس جهانی",
            price=price,
            currency="USD",
            price_unit="USD_PER_TROY_OUNCE",
            settlement_term="SPOT",
            trade_form="NOT_APPLICABLE",
            movement=movement,
            source_datetime_text=_source_datetime(normalized),
            parse_confidence=0.99,
            parser_version=PARSER_VERSION,
        )
    ]


def parse_dollar(text: str) -> list[PriceEvent]:
    normalized = normalize_text(text)
    events: list[PriceEvent] = []

    if re.search(
        r"(?:پایان\s+معاملات|شروع\s+معاملات|سقف\s+معاملات|کف\s*معاملات|آخرین\s*معامله)",
        normalized,
    ):
        return []

    for line in normalized.splitlines():
        if "هرات" not in line and "معامله" not in line:
            continue
        if not any(token in line for token in ("خرید", "فروش", "معامله")):
            continue

        price_match = _PRICE_RE.search(line)
        if not price_match:
            continue
        price = _decimal(price_match.group(1))
        if price is None:
            continue

        settlement = _settlement(line)
        if settlement == "TOMORROW":
            settlement_label = "فردایی"
        elif settlement == "TODAY":
            settlement_label = "امروزی"
        else:
            settlement_label = "نامشخص"

        # Dollar-market domain rule: only an explicit cash marker represents
        # physical cash. "Herat today" and "Herat tomorrow" are settlement
        # labels for the paper market, not proof that currency is delivered.
        trade_form = (
            "PHYSICAL"
            if re.search(r"(?:نقدی|نقد)", line)
            else "PAPER"
        )
        if trade_form == "PAPER":
            form_label = "کاغذی"
        else:
            form_label = "فیزیکی"

        if "معامله" in line:
            event_type = "TRADE"
            side = "UNKNOWN"
        elif "خرید" in line:
            event_type = "OFFER"
            side = "BUY"
        else:
            event_type = "OFFER"
            side = "SELL"

        quantity = None
        quantity_unit = None
        quantity_match = re.search(r"(\d+(?:\.\d+)?)\s*میلیارد", line)
        if quantity_match:
            quantity = _decimal(quantity_match.group(1))
            quantity_unit = "BILLION_TOMAN"

        events.append(
            PriceEvent(
                instrument="USD_HERAT",
                market_label=f"دلار هرات {settlement_label} {form_label}",
                price=price,
                currency="TOMAN",
                price_unit="TOMAN_PER_USD",
                settlement_term=settlement,
                trade_form=trade_form,
                event_type=event_type,
                side=side,
                quantity=quantity,
                quantity_unit=quantity_unit,
                parse_confidence=0.97,
                parser_version=PARSER_VERSION,
            )
        )

    return events


def parse_message(channel_username: str, text: str) -> list[PriceEvent]:
    username = channel_username.lstrip("@").lower()
    if username == "abshdh":
        return parse_abshdh(text)
    if username == "naghdp":
        return parse_naghdp(text)
    if username == "qheimat_ounce":
        return parse_ounce(text)
    if username in {"toofanharirodofficial", "toofan_harirod"}:
        return parse_dollar(text)
    return []


def should_ignore_message(
    channel_username: str,
    text: str,
    *,
    is_forwarded: bool = False,
) -> bool:
    if is_forwarded or not text.strip():
        return True
    username = channel_username.lstrip("@").lower()
    normalized = normalize_text(text)
    if username == "abshdh" and ("پیوت" in normalized or "#مرورنوسانات" in normalized):
        return True
    if username in {"toofanharirodofficial", "toofan_harirod"} and re.search(
        r"(?:پایان\s+معاملات|شروع\s+معاملات|سقف\s+معاملات|کف\s*معاملات|آخرین\s*معامله)",
        normalized,
    ):
        return True
    return False
