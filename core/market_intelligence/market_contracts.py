"""Immutable, privacy-minimized contract for normalized market observations.

This is the only write contract for the product Market Store.  Source adapters
may keep raw Telegram identities and text in short-lived private staging, but
they must derive an opaque ``event_key`` before constructing this object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import blake2b
import json
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .price_magnitude_policy import (
    PriceUnitPolicyError,
    assert_canonical_magnitude,
    forbid_irt_unit,
)


MARKET_STORE_CONTRACT_VERSION = 1
TEHRAN_TIMEZONE = ZoneInfo("Asia/Tehran")

SOURCE_FAMILIES = frozenset(
    {
        "PROJECT",
        "GROUP",
        "TELEGRAM_PUBLIC",
        "TELEGRAM_PRIVATE",
        "EXTERNAL_MARKET",
        "MANUAL_REVIEW",
    }
)
SETTLEMENT_TERMS = frozenset(
    {"CASH", "TODAY", "TOMORROW", "SPOT", "UNKNOWN"}
)
TRADE_FORMS = frozenset(
    {
        "PHYSICAL",
        "PAPER_NORMAL",
        "PAPER_REVERSE",
        "PAPER_SWIM",
        "NOT_APPLICABLE",
        "UNKNOWN",
    }
)
EVENT_TYPES = frozenset({"OFFER", "TRADE", "QUOTE", "REFERENCE"})
SIDES = frozenset({"BUY", "SELL", "MID", "UNKNOWN"})
QUALITY_STATES = frozenset(
    {"ELIGIBLE", "PENDING_REVIEW", "AMBIGUOUS", "IGNORED", "REJECTED"}
)

# The names, rather than locale-specific free text, are stored with an
# observation.  Conversions are explicit in later producers; this contract
# never silently turns one unit into another.
# Canonical domestic unit is toman.  PROJECT_THOUSAND_TOMAN is the product
# coin book (1 unit = 1,000 toman).  XAU remains USD.
PRICE_UNITS = frozenset(
    {
        "PROJECT_THOUSAND_TOMAN",
        "TOMAN_PER_COIN",
        "TOMAN_PER_MESGHAL_750",
        "TOMAN_PER_GRAM_750",
        "TOMAN_PER_USD",
        "TOMAN_PER_USDT",
        "USD_PER_TROY_OUNCE",
    }
)

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SENSITIVE_ATTRIBUTE_KEY_PARTS = frozenset(
    {
        "channel",
        "chat",
        "link",
        "message",
        "phone",
        "raw",
        "sender",
        "source_text",
        "telegram",
        "text",
        "user",
        "username",
    }
)


class MarketStoreContractError(ValueError):
    """Raised when an observation could make the price model unsafe."""


def derive_event_key(*identity_parts: str | int | bytes) -> bytes:
    """Return an opaque, deterministic event key without persisting identity.

    Callers pass private source identifiers only at the ingestion boundary.  A
    32-byte BLAKE2 digest is the sole representation that can cross into the
    normalized Market Store.
    """

    digest = blake2b(digest_size=32, person=b"market-store-v1")
    for part in identity_parts:
        if isinstance(part, bytes):
            encoded = part
        else:
            encoded = str(part).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def normalize_utc(value: datetime | str, *, field_name: str) -> str:
    """Require an aware timestamp and serialize it as canonical UTC seconds."""

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarketStoreContractError(f"{field_name}_invalid") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise MarketStoreContractError(f"{field_name}_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketStoreContractError(f"{field_name}_timezone_required")
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def tehran_dimensions(event_time_utc: str) -> tuple[str, str, str, int]:
    """Derive Tehran dimensions from the event instant, never ingest time."""

    parsed = datetime.fromisoformat(event_time_utc.replace("Z", "+00:00"))
    localized = parsed.astimezone(TEHRAN_TIMEZONE)
    return (
        localized.isoformat(timespec="seconds"),
        localized.date().isoformat(),
        localized.strftime("%H:%M"),
        localized.weekday(),
    )


def _normalized_code(
    value: str,
    *,
    field_name: str,
    allowed: frozenset[str] | None = None,
) -> str:
    normalized = str(value).strip().upper()
    if not _CODE_PATTERN.fullmatch(normalized):
        raise MarketStoreContractError(f"{field_name}_invalid")
    if allowed is not None and normalized not in allowed:
        raise MarketStoreContractError(f"{field_name}_unsupported")
    return normalized


def _positive_decimal(
    value: Decimal | int | float | str,
    *,
    field_name: str,
) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketStoreContractError(f"{field_name}_invalid") from exc
    if not normalized.is_finite() or normalized <= 0:
        raise MarketStoreContractError(f"{field_name}_must_be_positive")
    return normalized


def _json_attributes(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise MarketStoreContractError("attributes_mapping_required")
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if not normalized_key or any(
            token in normalized_key for token in _SENSITIVE_ATTRIBUTE_KEY_PARTS
        ):
            raise MarketStoreContractError("attributes_contains_private_identity")
        sanitized[str(key)] = item
    try:
        return json.dumps(
            sanitized,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise MarketStoreContractError("attributes_not_json_serializable") from exc


def _validate_instrument_unit(instrument: str, price_unit: str) -> None:
    expected_units = {
        "USD_HERAT": {"TOMAN_PER_USD"},
        "USDT_IRT": {"TOMAN_PER_USDT"},
        "XAUUSD": {"USD_PER_TROY_OUNCE"},
        "IME_GOLD_BAR": {"TOMAN_PER_MESGHAL_750"},
        "IME_GOLD_COIN_IMAM": {"TOMAN_PER_COIN"},
    }.get(instrument)
    if expected_units is not None and price_unit not in expected_units:
        raise MarketStoreContractError("instrument_price_unit_mismatch")
    if instrument.startswith("COIN_") and price_unit not in {
        "PROJECT_THOUSAND_TOMAN",
        "TOMAN_PER_COIN",
    }:
        raise MarketStoreContractError("instrument_price_unit_mismatch")
    if instrument.startswith("MELTED_GOLD") and price_unit != "TOMAN_PER_MESGHAL_750":
        raise MarketStoreContractError("instrument_price_unit_mismatch")


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """One normalized observation with explicit provenance and dimensions.

    ``event_key`` must already be an opaque digest.  It is intentionally bytes
    so a raw Telegram ID, URL, name, or source payload cannot accidentally be
    stored as the idempotency key.
    """

    event_key: bytes
    source_code: str
    source_family: str
    event_time_utc: datetime | str
    available_at_utc: datetime | str
    instrument: str
    market_label: str
    settlement_term: str
    trade_form: str
    event_type: str
    side: str
    price: Decimal | int | float | str
    price_unit: str
    currency: str = "IRT"
    quantity: Decimal | int | float | str | None = None
    quantity_unit: str | None = None
    parse_confidence: float = 1.0
    parser_version: str = "adapter-v1"
    quality_state: str = "ELIGIBLE"
    quality_policy_version: str = "quality-v1"
    is_conditional: bool = False
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> "NormalizedMarketObservation":
        if not isinstance(self.event_key, bytes) or not 16 <= len(self.event_key) <= 64:
            raise MarketStoreContractError("event_key_opaque_digest_required")
        source_code = _normalized_code(self.source_code, field_name="source_code")
        source_family = _normalized_code(
            self.source_family,
            field_name="source_family",
            allowed=SOURCE_FAMILIES,
        )
        event_time_utc = normalize_utc(self.event_time_utc, field_name="event_time_utc")
        available_at_utc = normalize_utc(
            self.available_at_utc,
            field_name="available_at_utc",
        )
        if available_at_utc < event_time_utc:
            raise MarketStoreContractError("available_at_before_event_time")
        instrument = _normalized_code(self.instrument, field_name="instrument")
        market_label = _normalized_code(self.market_label, field_name="market_label")
        settlement_term = _normalized_code(
            self.settlement_term,
            field_name="settlement_term",
            allowed=SETTLEMENT_TERMS,
        )
        trade_form = _normalized_code(
            self.trade_form,
            field_name="trade_form",
            allowed=TRADE_FORMS,
        )
        event_type = _normalized_code(
            self.event_type,
            field_name="event_type",
            allowed=EVENT_TYPES,
        )
        side = _normalized_code(self.side, field_name="side", allowed=SIDES)
        try:
            forbid_irt_unit(self.price_unit)
        except PriceUnitPolicyError as exc:
            raise MarketStoreContractError(str(exc)) from exc
        price_unit = _normalized_code(
            self.price_unit,
            field_name="price_unit",
            allowed=PRICE_UNITS,
        )
        _validate_instrument_unit(instrument, price_unit)
        currency = _normalized_code(self.currency, field_name="currency")
        if price_unit.startswith("TOMAN") or price_unit == "PROJECT_THOUSAND_TOMAN":
            if currency not in {"TOMAN", "IRT"}:
                raise MarketStoreContractError("toman_price_requires_toman_currency")
            # Canonical currency label for toman prices is TOMAN.
            currency = "TOMAN"
        if price_unit == "USD_PER_TROY_OUNCE" and currency != "USD":
            raise MarketStoreContractError("xau_requires_usd_currency")
        if currency == "IRT":
            raise MarketStoreContractError("irt_currency_forbidden_use_toman")
        price = _positive_decimal(self.price, field_name="price")
        try:
            assert_canonical_magnitude(price_unit=price_unit, price=price)
        except PriceUnitPolicyError as exc:
            raise MarketStoreContractError(str(exc)) from exc
        try:
            parse_confidence = float(self.parse_confidence)
        except (TypeError, ValueError) as exc:
            raise MarketStoreContractError("parse_confidence_invalid") from exc
        if not 0.0 <= parse_confidence <= 1.0:
            raise MarketStoreContractError("parse_confidence_out_of_range")
        parser_version = str(self.parser_version).strip()
        quality_policy_version = str(self.quality_policy_version).strip()
        if not parser_version or not quality_policy_version:
            raise MarketStoreContractError("parser_or_policy_version_required")
        quantity = (
            _positive_decimal(self.quantity, field_name="quantity")
            if self.quantity is not None
            else None
        )
        quantity_unit = (
            _normalized_code(self.quantity_unit, field_name="quantity_unit")
            if self.quantity_unit is not None
            else None
        )
        if quantity is not None and quantity_unit is None:
            raise MarketStoreContractError("quantity_unit_required")
        if quantity is None and quantity_unit is not None:
            raise MarketStoreContractError("quantity_required")
        if not isinstance(self.is_conditional, bool):
            raise MarketStoreContractError("is_conditional_boolean_required")
        tehran_datetime, tehran_date, tehran_minute, tehran_weekday = tehran_dimensions(
            event_time_utc
        )
        return NormalizedMarketObservation(
            event_key=self.event_key,
            source_code=source_code,
            source_family=source_family,
            event_time_utc=event_time_utc,
            available_at_utc=available_at_utc,
            tehran_datetime=tehran_datetime,
            tehran_date=tehran_date,
            tehran_minute=tehran_minute,
            tehran_weekday=tehran_weekday,
            instrument=instrument,
            market_label=market_label,
            settlement_term=settlement_term,
            trade_form=trade_form,
            event_type=event_type,
            side=side,
            price=price,
            price_unit=price_unit,
            currency=currency,
            quantity=quantity,
            quantity_unit=quantity_unit,
            parse_confidence=parse_confidence,
            parser_version=parser_version,
            quality_state=_normalized_code(
                self.quality_state,
                field_name="quality_state",
                allowed=QUALITY_STATES,
            ),
            quality_policy_version=quality_policy_version,
            is_conditional=self.is_conditional,
            attributes_json=_json_attributes(self.attributes),
        )


@dataclass(frozen=True, slots=True)
class NormalizedMarketObservation:
    """Storage-ready representation; contains no raw source identity or text."""

    event_key: bytes
    source_code: str
    source_family: str
    event_time_utc: str
    available_at_utc: str
    tehran_datetime: str
    tehran_date: str
    tehran_minute: str
    tehran_weekday: int
    instrument: str
    market_label: str
    settlement_term: str
    trade_form: str
    event_type: str
    side: str
    price: Decimal
    price_unit: str
    currency: str
    quantity: Decimal | None
    quantity_unit: str | None
    parse_confidence: float
    parser_version: str
    quality_state: str
    quality_policy_version: str
    is_conditional: bool
    attributes_json: str
