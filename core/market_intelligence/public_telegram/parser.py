"""Pure parsers for public market messages.

The functions return only economic fields.  They neither store the input text
nor accept private Telegram identities; the ingestion boundary derives an
opaque event key before the result reaches the Market Store.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from .sources import source_for_code


PARSER_VERSION = "public-market-rules-v1"

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,9})(?!\d)")
_OUNCE_RE = re.compile(r"(?<![\d.])(\d{3,5}\.\d{1,4})(?![\d.])")


@dataclass(frozen=True, slots=True)
class ParsedPublicEvent:
    instrument: str
    market_label: str
    settlement_term: str
    trade_form: str
    event_type: str
    side: str
    price: Decimal
    price_unit: str
    currency: str
    quantity: Decimal | None = None
    quantity_unit: str | None = None
    parse_confidence: float = 1.0
    attributes: dict[str, str | bool] | None = None


def normalize_text(value: str) -> str:
    """Normalize Persian/Arabic digits and spacing without retaining the input."""

    value = str(value).translate(_DIGIT_TRANSLATION)
    value = value.replace("ي", "ی").replace("ك", "ک").replace("ـ", "")
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    )


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _largest_price(line: str) -> Decimal | None:
    candidates = (_decimal(value) for value in _PRICE_RE.findall(line))
    valid = [candidate for candidate in candidates if candidate is not None and candidate >= 1_000]
    return max(valid) if valid else None


def _as_toman(value: Decimal) -> Decimal:
    """Public domestic feeds quote toman; the store keeps toman."""

    return value


def _settlement(text: str) -> str:
    if re.search(r"(?:فردا|فردایی)", text):
        return "TOMORROW"
    if re.search(r"(?:امروز|امروزی)", text):
        return "TODAY"
    return "UNKNOWN"


def _event_type_and_side(text: str) -> tuple[str, str]:
    if "معامله" in text:
        return "TRADE", "UNKNOWN"
    if "خرید" in text:
        return "OFFER", "BUY"
    if "فروش" in text:
        return "OFFER", "SELL"
    return "QUOTE", "UNKNOWN"


def should_ignore_public_message(
    source_code: str,
    text: str,
    *,
    is_forwarded: bool = False,
) -> bool:
    """Keep only source messages that can become normalized observations."""

    source = source_for_code(source_code)
    normalized = normalize_text(text)
    if is_forwarded or not normalized:
        return True
    if source.code == "MELTED_AGGREGATE" and (
        "پیوت" in normalized or "#مرورنوسانات" in normalized
    ):
        return True
    if source.code == "USD_HERAT" and re.search(
        r"(?:پایان\s+معاملات|شروع\s+معاملات|سقف\s+معاملات|کف\s*معاملات|آخرین\s*معامله)",
        normalized,
    ):
        return True
    return False


def _parse_melted_aggregate(text: str) -> list[ParsedPublicEvent]:
    events: list[ParsedPublicEvent] = []
    lines = normalize_text(text).splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"#\s*مظنه\s*اتحادیه", line):
            continue
        price = _largest_price(line)
        if price is None and index + 1 < len(lines):
            price = _largest_price(lines[index + 1])
        if price is not None:
            events.append(
                ParsedPublicEvent(
                    instrument="MELTED_GOLD_UNION",
                    market_label="UNION_QUOTE",
                    settlement_term="UNKNOWN",
                    trade_form="UNKNOWN",
                    event_type="QUOTE",
                    side="MID",
                    price=_as_toman(price),
                    price_unit="TOMAN_PER_MESGHAL_750",
                    currency="TOMAN",
                    parse_confidence=0.99,
                )
            )

    for line in lines:
        compact = re.sub(r"\s+", " ", line.replace("_", " ").replace("-", " "))
        match = re.search(
            r"#\s*(?:آبشده|ابشده)\s*(حواله|نقدی|رسمی|امروزی|فردایی|فردا|غیررسمی)?",
            compact,
        )
        if match is None:
            continue
        price = _largest_price(compact)
        if price is None:
            continue
        subtype = (match.group(1) or "نامشخص").strip()
        settlement = _settlement(compact)
        # Only the explicit markers are physical.  The absence of a cash marker
        # must not silently convert a paper quote to physical gold.
        trade_form = "PHYSICAL" if subtype in {"نقدی", "رسمی"} else "PAPER_NORMAL"
        event_type, side = _event_type_and_side(compact)
        events.append(
            ParsedPublicEvent(
                instrument="MELTED_GOLD_AGGREGATE",
                market_label=(
                    "MELTED_PHYSICAL" if trade_form == "PHYSICAL" else "MELTED_PAPER"
                ),
                settlement_term=settlement,
                trade_form=trade_form,
                event_type=event_type,
                side=side,
                price=_as_toman(price),
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
                parse_confidence=0.98,
                attributes={"subtype_explicit": subtype != "نامشخص"},
            )
        )
    return events


def _parse_melted_flow(text: str) -> list[ParsedPublicEvent]:
    normalized = normalize_text(text)
    first_line = normalized.splitlines()[0] if normalized else ""
    price = _largest_price(first_line)
    if price is None:
        return []
    if "امروز" in first_line:
        settlement = "TODAY"
    elif re.search(r"(?:با\s*حواله|باحواله|فردا|فردایی)", first_line):
        # NaghdP is a paper feed.  A transfer quote with no explicit day is
        # conventionally tomorrow, never physical cash.
        settlement = "TOMORROW"
    else:
        return []
    event_type, side = _event_type_and_side(first_line)
    if event_type == "QUOTE":
        return []
    return [
        ParsedPublicEvent(
            instrument="MELTED_GOLD_FLOW",
            market_label="MELTED_PAPER_FLOW",
            settlement_term=settlement,
            trade_form="PAPER_NORMAL",
            event_type=event_type,
            side=side,
            price=_as_toman(price),
            price_unit="TOMAN_PER_MESGHAL_750",
            currency="TOMAN",
            parse_confidence=0.98 if event_type == "TRADE" else 0.995,
        )
    ]


def _parse_usd_herat(text: str) -> list[ParsedPublicEvent]:
    events: list[ParsedPublicEvent] = []
    for line in normalize_text(text).splitlines():
        if "هرات" not in line and "معامله" not in line:
            continue
        event_type, side = _event_type_and_side(line)
        if event_type == "QUOTE":
            continue
        price = _largest_price(line)
        if price is None:
            continue
        settlement = _settlement(line)
        # Per the market policy, only an explicit "cash" marker represents
        # physical dollars.  Herat today/tomorrow remain paper observations.
        trade_form = "PHYSICAL" if re.search(r"(?:نقدی|نقد)", line) else "PAPER_NORMAL"
        quantity = None
        quantity_unit = None
        quantity_match = re.search(r"(\d+(?:\.\d+)?)\s*میلیارد", line)
        if quantity_match:
            quantity = _decimal(quantity_match.group(1))
            quantity_unit = "BILLION_TOMAN"
        events.append(
            ParsedPublicEvent(
                instrument="USD_HERAT",
                market_label=(
                    "HERAT_PHYSICAL" if trade_form == "PHYSICAL" else "HERAT_PAPER"
                ),
                settlement_term=settlement,
                trade_form=trade_form,
                event_type=event_type,
                side=side,
                price=_as_toman(price),
                price_unit="TOMAN_PER_USD",
                currency="TOMAN",
                quantity=quantity,
                quantity_unit=quantity_unit,
                parse_confidence=0.97,
            )
        )
    return events


def _parse_xauusd(text: str) -> list[ParsedPublicEvent]:
    normalized = normalize_text(text)
    match = _OUNCE_RE.search(normalized)
    if match is None:
        return []
    price = _decimal(match.group(1))
    if price is None:
        return []
    return [
        ParsedPublicEvent(
            instrument="XAUUSD",
            market_label="GLOBAL_SPOT",
            settlement_term="SPOT",
            trade_form="NOT_APPLICABLE",
            event_type="QUOTE",
            side="MID",
            price=price,
            price_unit="USD_PER_TROY_OUNCE",
            currency="USD",
            parse_confidence=0.99,
        )
    ]


def parse_public_message(source_code: str, text: str) -> list[ParsedPublicEvent]:
    """Parse an allowlisted source into canonical, raw-text-free event fields."""

    source = source_for_code(source_code)
    if should_ignore_public_message(source.code, text):
        return []
    if source.code == "MELTED_AGGREGATE":
        return _parse_melted_aggregate(text)
    if source.code == "MELTED_FLOW":
        return _parse_melted_flow(text)
    if source.code == "USD_HERAT":
        return _parse_usd_herat(text)
    if source.code == "XAUUSD":
        return _parse_xauusd(text)
    raise AssertionError("unreachable_public_market_source")
