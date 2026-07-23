"""Transport-independent Telegram collector contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PriceEvent:
    instrument: str
    market_label: str
    price: Decimal
    currency: str
    price_unit: str
    settlement_term: str = "UNKNOWN"
    trade_form: str = "UNKNOWN"
    event_type: str = "QUOTE"
    side: str = "UNKNOWN"
    quantity: Decimal | None = None
    quantity_unit: str | None = None
    movement: str = "UNKNOWN"
    source_datetime_text: str | None = None
    parse_method: str = "RULE"
    parse_confidence: float = 1.0
    parser_version: str = "rules-v1"


@dataclass(frozen=True, slots=True)
class CollectedMessage:
    message_id: int
    published_at_utc: str
    text: str
    is_forwarded: bool = False


@dataclass(frozen=True, slots=True)
class IngestResult:
    source_code: str
    parsed_event_count: int
    ignored: bool
    compact_bucket_replaced: bool = False
