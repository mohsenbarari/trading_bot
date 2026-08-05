"""Conservative first-pass normalization for private coin trading groups.

The input text and source message identifier are transient.  Market facts hold
only economic fields plus an opaque event digest.  A second, point-in-time
commodity-resolution/trade-linking stage is intentionally required before an
unnamed or price-conflicting offer becomes model-eligible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from typing import Iterable

from .market_contracts import MarketObservation, derive_event_key, normalize_utc


COIN_GROUP_PARSER_VERSION = "coin-group-rules-v1"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
# `/` frequently separates price from quantity in group posts (for example
# `186,900 / 5 تا`), so it must never join the two numeric fields.  Thousand
# separators are accepted only in their unambiguous three-digit grouping form.
_NUMBER = re.compile(r"(?<!\d)(\d{1,3}(?:[٬،,]\d{3})+|\d{2,7})(?!\d)")
_QUANTITY = re.compile(r"(?<!\d)(\d{1,3})\s*(?:د?تا|عدد)\b")
_SIDE = re.compile(r"خرید|فروش|(?<![آ-ی])([خف]+)(?![آ-ی])")
_EXCLUDED_YEAR = re.compile(r"(?<!\d)(?:403|404|1403|1404)(?!\d)")
_THURSDAY = re.compile(r"پنج\s*شنبه|پنجشنبه|کشیک")
_CONDITIONAL = re.compile(r"فیش|شرط|مهلت|واریز|تسویه|چک|توضیحات\s*[:：]")
_NON_OFFER = re.compile(
    r"نحوه\s+گذاشتن|مثال|لینک\s+ارسالی|بات\s+تلگرامی|شروع\s+معاملات|"
    r"آماده\s*بکار|عضو\s+شدند"
)

# Broad static guards catch malformed zeros and obvious typos, but do not
# resolve the legitimate overlap of full Imam and low-date Bahar.  That needs
# a strictly-prior Snapshot in P2-C-B/P5.
_PRICE_BOUNDS = {
    "IMAM": (130_000, 260_000),
    "BAHAR": (130_000, 250_000),
    "QUARTER_BAHAR": (46_000, 70_000),
    "HALF_BAHAR": (82_000, 110_000),
    "QUARTER_LOW_DATE": (35_000, 49_000),
    "HALF_LOW_DATE": (70_000, 100_000),
    "ONE_GRAM": (20_000, 36_000),
}


@dataclass(frozen=True, slots=True)
class CoinGroupMessageInput:
    """Transient private-group message; never persist text/ID in Market Store."""

    group_number: int
    source_event_id: str | int
    published_at_utc: datetime | str
    available_at_utc: datetime | str
    text: str


@dataclass(frozen=True, slots=True)
class ParsedCoinGroupOffer:
    """Economic output only; commodity may deliberately remain unresolved."""

    commodity_code: str | None
    price_project_thousand_toman: int
    quantity: int
    side: str
    settlement_term: str
    trade_form: str
    is_conditional: bool
    quality_state: str
    resolution_reason: str


def _text(value: str) -> str:
    normalized = str(value or "").translate(_DIGITS)
    normalized = normalized.replace("\u200c", " ").replace("\u200f", " ")
    return " ".join(normalized.split())


def _commodity(text: str) -> str | None:
    if re.search(r"(?:یک\s*گرمی|گرمی|مرکزی)", text):
        return "ONE_GRAM"
    low_date = bool(
        re.search(r"تاریخ\s*پایین|ت\s*\.?\s*پ|(?<![آ-ی])پایین(?![آ-ی])", text)
    )
    if re.search(r"ربع(?![آ-ی])", text):
        return "QUARTER_LOW_DATE" if low_date else "QUARTER_BAHAR"
    if re.search(r"نیم(?![آ-ی])", text):
        return "HALF_LOW_DATE" if low_date else "HALF_BAHAR"
    if low_date:
        return "BAHAR"
    if re.search(r"(?:بهار|آزادی)(?![آ-ی])", text):
        return "BAHAR"
    if re.search(r"(?:امام(?:ی)?|تمام|سکه\s+جدید|سکه\s+بانکی)(?![آ-ی])", text):
        return "IMAM"
    return None


def _side(text: str) -> str | None:
    match = _SIDE.search(text)
    if match is None:
        return None
    marker = match.group()
    return "BUY" if marker.startswith("خ") else "SELL"


def _quantity(text: str) -> tuple[int | None, list[tuple[int, int]]]:
    match = _QUANTITY.search(text)
    if match is None:
        return None, []
    quantity = int(match.group(1))
    return (quantity if 1 <= quantity <= 100 else None), [match.span(1)]


def _spans_overlap(first: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(first[0] < end and first[1] > start for start, end in spans)


def _price_candidates(text: str, quantity_spans: Iterable[tuple[int, int]]) -> list[tuple[int, float]]:
    candidates: dict[int, float] = {}
    for match in _NUMBER.finditer(text):
        if _spans_overlap(match.span(1), quantity_spans):
            continue
        digits = re.sub(r"\D", "", match.group(1))
        if not digits:
            continue
        raw = int(digits)
        length = len(digits)
        separated = bool(re.search(r"[٬،,./]", match.group(1)))
        values: list[tuple[int, float]] = []
        if length in {5, 6}:
            values.append((raw, 1.0 if separated else 0.96))
        elif length == 7 and raw % 10 == 0:
            values.append((raw // 10, 0.72))
        elif length == 4:
            values.extend(((raw * 100, 0.86), (raw * 10, 0.80)))
        elif length == 3:
            values.extend(((raw * 1000, 0.90), (raw * 100, 0.84)))
        elif length == 2:
            values.append((raw * 1000, 0.78))
        for value, score in values:
            if 20_000 <= value <= 260_000:
                candidates[value] = max(candidates.get(value, 0.0), score)
    return sorted(candidates.items(), key=lambda item: item[1], reverse=True)


def _price(text: str, quantity_spans: Iterable[tuple[int, int]], commodity: str | None) -> int | None:
    candidates = _price_candidates(text, quantity_spans)
    if commodity is not None:
        low, high = _PRICE_BOUNDS[commodity]
        candidates = [item for item in candidates if low <= item[0] <= high]
    if not candidates:
        return None
    winner, score = candidates[0]
    if len(candidates) > 1 and candidates[1][0] != winner and score - candidates[1][1] < 0.08:
        return None
    return winner


def _dimensions(text: str) -> tuple[str, str]:
    paper = bool(re.search(r"کاغذی|حواله|غیررسمی", text))
    tomorrow = bool(re.search(r"فردا|فردایی", text))
    cash = bool(re.search(r"نقدی|نقد|امروز|حاضر|(?:^|\s)ن(?=\s|\d|$)", text))
    if paper:
        if "معکوس" in text:
            return "PAPER_REVERSE", "TOMORROW" if tomorrow else "TODAY" if cash else "TOMORROW"
        if "شنا" in text:
            return "PAPER_SWIM", "TOMORROW" if tomorrow else "TODAY" if cash else "TOMORROW"
        return "PAPER_NORMAL", "TOMORROW" if tomorrow else "TODAY" if cash else "TOMORROW"
    # Group policy: an absent settlement marker is tomorrow, never silently
    # cash.  This prevents mixing visibly different live price books.
    return "PHYSICAL", "TOMORROW" if tomorrow or not cash else "CASH"


def parse_coin_group_offers(source: CoinGroupMessageInput) -> list[ParsedCoinGroupOffer]:
    """Parse only self-contained offer lines; unrelated text yields no fact."""

    if int(source.group_number) not in {1, 2}:
        raise ValueError("coin_group_number_unsupported")
    whole = _text(source.text)
    if not whole or _NON_OFFER.search(whole):
        return []
    results: list[ParsedCoinGroupOffer] = []
    for line in [item for item in str(source.text).splitlines() if _text(item)] or [whole]:
        text = _text(line)
        if _EXCLUDED_YEAR.search(text) or _THURSDAY.search(text):
            continue
        commodity = _commodity(text)
        side = _side(text)
        quantity, quantity_spans = _quantity(text)
        price = _price(text, quantity_spans, commodity)
        if side is None or quantity is None or price is None:
            continue
        trade_form, settlement = _dimensions(text)
        # A name in a free-form group message is not enough to let an offer
        # influence a rate.  It may be a typo (for example, Imam where the
        # quoted price belongs to Bahar).  Static bands above merely reject
        # impossible text; P2-C-B must validate every surviving name against
        # strictly-prior, same-book price evidence before it becomes ELIGIBLE.
        quality_state = "PENDING_REVIEW"
        reason = (
            "UNNAMED_COMMODITY_REQUIRES_POINT_IN_TIME_PRICE_RESOLUTION"
            if commodity is None
            else "EXPLICIT_COMMODITY_REQUIRES_POINT_IN_TIME_PRICE_VALIDATION"
        )
        results.append(
            ParsedCoinGroupOffer(
                commodity_code=commodity,
                price_project_thousand_toman=price,
                quantity=quantity,
                side=side,
                settlement_term=settlement,
                trade_form=trade_form,
                is_conditional=bool(_CONDITIONAL.search(text)),
                quality_state=quality_state,
                resolution_reason=reason,
            )
        )
    return results


def coin_group_offer_observations(source: CoinGroupMessageInput) -> list[MarketObservation]:
    """Project parsed offer facts; unresolved products remain non-eligible."""

    published = normalize_utc(source.published_at_utc, field_name="coin_group_published_at_utc")
    available = normalize_utc(source.available_at_utc, field_name="coin_group_available_at_utc")
    if available < published:
        raise ValueError("coin_group_available_before_published")
    observations: list[MarketObservation] = []
    for index, parsed in enumerate(parse_coin_group_offers(source)):
        commodity = parsed.commodity_code or "UNRESOLVED"
        observations.append(
            MarketObservation(
                event_key=derive_event_key(
                    "coin-group-offer-v1",
                    source.group_number,
                    source.source_event_id,
                    index,
                ),
                source_code=f"GROUP_{int(source.group_number)}",
                source_family="GROUP",
                event_time_utc=published,
                available_at_utc=available,
                instrument="COIN_" + commodity,
                market_label="GROUP_COIN_" + commodity,
                settlement_term=parsed.settlement_term,
                trade_form=parsed.trade_form,
                event_type="OFFER",
                side=parsed.side,
                price=Decimal(parsed.price_project_thousand_toman),
                price_unit="PROJECT_THOUSAND_TOMAN",
                currency="IRT",
                quantity=parsed.quantity,
                quantity_unit="COIN_COUNT",
                parse_confidence=0.96 if parsed.commodity_code else 0.72,
                parser_version=COIN_GROUP_PARSER_VERSION,
                quality_state=parsed.quality_state,
                quality_policy_version="coin-group-first-pass-v1",
                is_conditional=parsed.is_conditional,
                attributes={
                    "group_number": int(source.group_number),
                    "commodity_resolution": "EXPLICIT" if parsed.commodity_code else "UNRESOLVED",
                    "resolution_reason": parsed.resolution_reason,
                },
            )
        )
    return observations
