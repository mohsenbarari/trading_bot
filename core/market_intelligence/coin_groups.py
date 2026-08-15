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


COIN_GROUP_PARSER_VERSION = "coin-group-rules-v3-contextual-numbers"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
# Dot and slash are genuine thousands separators when they are attached to
# exactly three trailing digits.  Whitespace-delimited `/ 5 تا` remains two
# fields because it cannot match the grouped branch.
_NUMBER = re.compile(r"(?<!\d)(\d{1,3}(?:[٬،,./]\d{3})+|\d{2,7})(?!\d)")
_SMALL_NUMBER = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_QUANTITY = re.compile(r"(?<!\d)(\d{1,3})\s*(?:د?تا|عدد)")
_SIDE = re.compile(r"خرید|فروش|(?<![آ-ی])([خف]+)(?![آ-ی])")
_YEAR_TOKEN = re.compile(r"(?<!\d)(?:403|404|1403|1404)(?!\d)")
_THURSDAY = re.compile(r"پنج\s*شنبه|پنجشنبه|کشیک")
_CONDITIONAL = re.compile(
    r"فیش|شرط|مهلت|واریز|تسویه|چک|حساب|شب\s*ح(?:ساب)?|ش\s*ح(?:ساب)?|"
    r"تا\s*\d{1,2}(?::\d{2})?\s*(?:شب|ظهر|عصر)|توضیحات\s*[:：]"
)
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

_PERSIAN_UNITS = {
    "یه": 1,
    "یک": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "پنج": 5,
    "شش": 6,
    "هفت": 7,
    "هشت": 8,
    "نه": 9,
}
_PERSIAN_TENS = {
    "ده": 10,
    "بیست": 20,
    "سی": 30,
    "چهل": 40,
    "پنجاه": 50,
    "شصت": 60,
    "هفتاد": 70,
    "هشتاد": 80,
    "نود": 90,
}
_WORD_QUANTITY = re.compile(
    r"(?<![آ-ی])((?:"
    + "|".join((*_PERSIAN_TENS, *_PERSIAN_UNITS))
    + r")(?:\s+و\s+(?:"
    + "|".join(_PERSIAN_UNITS)
    + r"))?|صد)\s*(?:د?تا|عدد)(?![آ-ی])"
)


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
    normalized = str(value or "").translate(_DIGITS).translate(_ARABIC_LETTERS)
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
    compact_historical = re.search(
        r"(?:^|\s)([خف])ن(?:ف)?(?=\s|\d|$)", text
    )
    if compact_historical is not None:
        return "BUY" if compact_historical.group(1) == "خ" else "SELL"
    match = _SIDE.search(text)
    if match is not None:
        marker = match.group()
        return "BUY" if marker.startswith("خ") else "SELL"
    # Current group shorthand is frequently glued to a price or `تا`, e.g.
    # `189ف`, `188600خ 10`, or `2تاف94500`.
    attached = re.search(r"(?:^|\s|\d|تا)([خف])(?=\s|\d|$)", text)
    if attached is None:
        return None
    marker = attached.group(1)
    return "BUY" if marker.startswith("خ") else "SELL"


def _word_quantity(text: str) -> tuple[int | None, list[tuple[int, int]]]:
    match = _WORD_QUANTITY.search(text)
    if match is None:
        return None, []
    words = match.group(1).split()
    if words == ["صد"]:
        return 100, [match.span(1)]
    parts = [word for word in words if word != "و"]
    value = sum(_PERSIAN_TENS.get(word, _PERSIAN_UNITS.get(word, 0)) for word in parts)
    return (value if 1 <= value <= 100 else None), [match.span(1)]


def _explicit_quantity(text: str) -> tuple[int | None, list[tuple[int, int]]]:
    match = _QUANTITY.search(text)
    if match is not None:
        quantity = int(match.group(1))
        return (quantity if 1 <= quantity <= 100 else None), [match.span(1)]
    return _word_quantity(text)


def _spans_overlap(first: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(first[0] < end and first[1] > start for start, end in spans)


def _price_candidates(
    text: str,
    excluded_spans: Iterable[tuple[int, int]],
) -> list[tuple[int, float, tuple[int, int]]]:
    candidates: dict[tuple[int, tuple[int, int]], float] = {}
    for match in _NUMBER.finditer(text):
        if _spans_overlap(match.span(1), excluded_spans):
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
                key = (value, match.span(1))
                candidates[key] = max(candidates.get(key, 0.0), score)
    return sorted(
        ((value, score, span) for (value, span), score in candidates.items()),
        key=lambda item: (-item[1], item[0], item[2]),
    )


def _price(
    text: str,
    excluded_spans: Iterable[tuple[int, int]],
    commodity: str | None,
) -> tuple[int | None, list[tuple[int, int]]]:
    candidates = _price_candidates(text, excluded_spans)
    if commodity is not None:
        low, high = _PRICE_BOUNDS[commodity]
        candidates = [item for item in candidates if low <= item[0] <= high]
    if not candidates:
        return None, []
    winner, score, span = candidates[0]
    if len(candidates) > 1 and candidates[1][0] != winner and score - candidates[1][1] < 0.08:
        return None, []
    return winner, [span]


def _bare_quantity(
    text: str,
    excluded_spans: Iterable[tuple[int, int]],
) -> tuple[int | None, list[tuple[int, int]]]:
    candidates = [
        match
        for match in _SMALL_NUMBER.finditer(text)
        if not _spans_overlap(match.span(1), excluded_spans)
        and 1 <= int(match.group(1)) <= 100
    ]
    if len(candidates) != 1:
        return None, []
    match = candidates[0]
    return int(match.group(1)), [match.span(1)]


def coin_group_settlement_markers(text: str) -> tuple[bool, bool]:
    """Return explicit cash/future markers for old and current group syntax."""

    normalized = _text(text)
    explicit_tomorrow = bool(
        re.search(r"فردا|فردایی", normalized)
        or re.search(
            r"(?:^|\s)[خف]\s*(?:ن\s*)?ف(?=\s|\d|$)", normalized
        )
    )
    explicit_cash = bool(
        re.search(
            r"نقدی|نقد|(?<![آ-ی])نق(?![آ-ی])|امروز|حاضر|(?:^|\s)ن(?=\s|\d|$)",
            normalized,
        )
        or re.search(r"[خف]\s*ن(?=\s|\d|$)", normalized)
    )
    return explicit_cash, explicit_tomorrow


def resolve_coin_group_settlement(text: str) -> str:
    """Resolve delivery book; future wins over the old intermediate ن."""

    normalized = _text(text)
    explicit_cash, explicit_tomorrow = coin_group_settlement_markers(normalized)
    if explicit_tomorrow:
        return "TOMORROW"
    if explicit_cash:
        return "CASH"
    # Current syntax: a single خ/ف (or full side word) is cash.
    return "CASH" if _side(normalized) is not None else "TOMORROW"


def coin_group_settlement_conflict_reason(text: str, settlement: str) -> str | None:
    """Return a deterministic exclusion reason for an opposite stored book."""

    normalized = _text(text)
    if not normalized:
        return None
    label = str(settlement or "").strip().upper()
    if label not in {"CASH", "TOMORROW"}:
        return None
    resolved = resolve_coin_group_settlement(normalized)
    if resolved == label:
        return None
    return (
        "SETTLEMENT_LABEL_CASH_BUT_TEXT_TOMORROW"
        if label == "CASH"
        else "SETTLEMENT_LABEL_TOMORROW_BUT_TEXT_CASH"
    )


def _dimensions(text: str) -> tuple[str, str]:
    paper = bool(re.search(r"کاغذی|حواله|غیررسمی", text))
    settlement = resolve_coin_group_settlement(text)
    tomorrow = settlement == "TOMORROW"
    if paper:
        if "معکوس" in text:
            return "PAPER_REVERSE", "TOMORROW" if tomorrow else "TODAY"
        if "شنا" in text:
            return "PAPER_SWIM", "TOMORROW" if tomorrow else "TODAY"
        return "PAPER_NORMAL", "TOMORROW" if tomorrow else "TODAY"
    return "PHYSICAL", settlement


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
        if _THURSDAY.search(text):
            continue
        commodity = _commodity(text)
        side = _side(text)
        year_spans = [match.span() for match in _YEAR_TOKEN.finditer(text)]
        quantity, quantity_spans = _explicit_quantity(text)
        price, price_spans = _price(text, (*quantity_spans, *year_spans), commodity)
        if quantity is None and price is not None:
            quantity, quantity_spans = _bare_quantity(
                text,
                (*year_spans, *price_spans),
            )
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
                currency="TOMAN",
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
