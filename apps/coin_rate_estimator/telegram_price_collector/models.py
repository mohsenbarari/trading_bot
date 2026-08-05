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
class RawPost:
    message_id: int
    published_at_utc: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class ExternalMarketObservation:
    """One auditable raw market quote and its optional normalized equivalent."""

    source: str
    instrument: str
    symbol: str
    observed_at_utc: str
    quote_kind: str
    raw_price: Decimal
    raw_currency: str
    raw_unit: str
    normalized_price: Decimal | None
    normalized_currency: str | None
    normalized_unit: str | None
    interval_seconds: int = 0
    raw_fineness: Decimal | None = None
    raw_weight_gram: Decimal | None = None
    normalized_fineness: Decimal | None = None
    normalized_weight_gram: Decimal | None = None
    volume: Decimal | None = None
    conversion_formula: str = "identity"
