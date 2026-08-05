"""Explicit external-market normalization for USDT and IME quotations.

This is a transport-free adapter.  A later collector may fetch public market
data, but it must hand this module the real observation timestamp and an opaque
event identity.  The adapter never substitutes USDT for Herat and never stores
the endpoint payload, URL, response text, or provider session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from .market_contracts import MarketObservation, derive_event_key, normalize_utc


EXTERNAL_MARKETS_ADAPTER_VERSION = "external-markets-v1"
USDT_TOMAN_TO_IRT = Decimal("10")
MESGHAL_750_GRAMS = Decimal("4.3318")
IME_GOLD_BAR_FINENESS = Decimal("995")
STANDARD_GOLD_FINENESS = Decimal("750")
IME_GOLD_BAR_CERTIFICATE_GRAMS = Decimal("0.1")

ExternalQuoteKind = Literal["MID", "LAST", "CLOSE", "BID", "ASK"]


class ExternalMarketAdapterError(ValueError):
    """Raised for an ambiguous unit or malformed external market quote."""


@dataclass(frozen=True, slots=True)
class ExternalQuoteInput:
    """Transient provider data required for one normalized external quote."""

    source_code: str
    source_event_id: str | int
    observed_at_utc: datetime | str
    available_at_utc: datetime | str
    quote_kind: ExternalQuoteKind
    price: Decimal | int | float | str


def _decimal(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    try:
        normalized = Decimal(str(value).replace(",", "").replace("٬", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ExternalMarketAdapterError(f"{field_name}_invalid") from exc
    if not normalized.is_finite() or normalized <= 0:
        raise ExternalMarketAdapterError(f"{field_name}_must_be_positive")
    return normalized


def _quote_kind(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in {"MID", "LAST", "CLOSE", "BID", "ASK"}:
        raise ExternalMarketAdapterError("external_quote_kind_unsupported")
    return normalized


def _side_for_quote_kind(kind: str) -> str:
    return {"BID": "BUY", "ASK": "SELL"}.get(kind, "MID")


def _availability(source: ExternalQuoteInput) -> tuple[str, str]:
    observed = normalize_utc(source.observed_at_utc, field_name="external_observed_at_utc")
    available = normalize_utc(source.available_at_utc, field_name="external_available_at_utc")
    if available < observed:
        raise ExternalMarketAdapterError("external_available_before_observed")
    return observed, available


def _source_code(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ExternalMarketAdapterError("external_source_code_required")
    return normalized


def _base_observation(
    source: ExternalQuoteInput,
    *,
    instrument: str,
    price: Decimal,
    price_unit: str,
    currency: str,
    attributes: dict[str, object],
) -> MarketObservation:
    observed, available = _availability(source)
    quote_kind = _quote_kind(source.quote_kind)
    return MarketObservation(
        event_key=derive_event_key(
            "external-market-v1",
            _source_code(source.source_code),
            source.source_event_id,
            instrument,
            quote_kind,
        ),
        source_code=_source_code(source.source_code),
        source_family="EXTERNAL_MARKET",
        event_time_utc=observed,
        available_at_utc=available,
        instrument=instrument,
        market_label="EXTERNAL_REFERENCE",
        settlement_term="SPOT",
        trade_form="NOT_APPLICABLE",
        event_type="REFERENCE",
        side=_side_for_quote_kind(quote_kind),
        price=price,
        price_unit=price_unit,
        currency=currency,
        parse_confidence=1.0,
        parser_version=EXTERNAL_MARKETS_ADAPTER_VERSION,
        quality_state="ELIGIBLE",
        quality_policy_version="external-market-v1",
        attributes={"quote_kind": quote_kind, **attributes},
    )


def usdt_toman_quote_to_observation(source: ExternalQuoteInput) -> MarketObservation:
    """Normalize a provider quote explicitly expressed in Toman per USDT."""

    price_irt = _decimal(source.price, field_name="usdt_toman_price") * USDT_TOMAN_TO_IRT
    return _base_observation(
        source,
        instrument="USDT_IRT",
        price=price_irt,
        price_unit="IRT_PER_USDT",
        currency="IRT",
        attributes={
            "input_unit": "TOMAN_PER_USDT",
            "conversion": "toman_to_irt_x10",
        },
    )


def ime_gold_bar_irr_quote_to_observation(source: ExternalQuoteInput) -> MarketObservation:
    """Convert one 0.1g/995 IME certificate quote to IRR per 750 mesghal.

    ``raw_irr / 0.1g × 750/995 × 4.3318g`` yields the common 750-fineness
    mesghal unit.  No toman conversion occurs here: the output is canonical
    Iranian rial (IRT) by contract.
    """

    raw_irr = _decimal(source.price, field_name="ime_gold_bar_irr_price")
    normalized = (
        raw_irr
        / IME_GOLD_BAR_CERTIFICATE_GRAMS
        * STANDARD_GOLD_FINENESS
        / IME_GOLD_BAR_FINENESS
        * MESGHAL_750_GRAMS
    )
    return _base_observation(
        source,
        instrument="IME_GOLD_BAR",
        price=normalized,
        price_unit="IRT_PER_MESGHAL_750",
        currency="IRT",
        attributes={
            "input_unit": "IRR_PER_CERTIFICATE_0_1G_995",
            "input_fineness": int(IME_GOLD_BAR_FINENESS),
            "input_weight_gram": float(IME_GOLD_BAR_CERTIFICATE_GRAMS),
            "output_fineness": int(STANDARD_GOLD_FINENESS),
            "output_mesghal_gram": float(MESGHAL_750_GRAMS),
            "conversion": "irr_per_0_1g_995_to_irr_per_mesghal_750",
        },
    )


def ime_imam_coin_irr_quote_to_observation(source: ExternalQuoteInput) -> MarketObservation:
    """Normalize an IME Imam coin quote already expressed in IRR per coin."""

    return _base_observation(
        source,
        instrument="IME_GOLD_COIN_IMAM",
        price=_decimal(source.price, field_name="ime_imam_coin_irr_price"),
        price_unit="IRT_PER_COIN",
        currency="IRT",
        attributes={
            "input_unit": "IRR_PER_COIN",
            "conversion": "identity_irr_per_coin",
        },
    )
