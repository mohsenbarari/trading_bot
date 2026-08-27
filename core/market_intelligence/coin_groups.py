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
from statistics import median
from typing import Iterable, Mapping, Sequence

from .market_contracts import MarketObservation, derive_event_key, normalize_utc


COIN_GROUP_PARSER_VERSION = "coin-group-rules-v9-field-evidence"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
# Dot and slash are genuine thousands separators when they are attached to
# exactly three trailing digits.  Whitespace-delimited `/ 5 تا` remains two
# fields because it cannot match the grouped branch.
_NUMBER = re.compile(
    r"(?<!\d)(\d[٬،,./]\d{2}[٬،,./]\d{3}|"
    r"\d{1,3}(?:[٬،,./]\d{3})+|\d{2,3}[٬،,./]\d{1,2}|"
    r"\d{2,3}[٬،,./]\d{4,5}|\d{2,9})(?!\d)"
)
_SMALL_NUMBER = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_QUANTITY = re.compile(r"(?<!\d)(\d{1,3})\s*(?:د?تا|عدد|دونه|دانه)")
_SIDE = re.compile(r"خرید|فروش|(?<![آ-ی])([خف]+)(?![آ-ی])")
_YEAR_TOKEN = re.compile(r"(?<!\d)(?:403|404|1403|1404)(?!\d)")
_LOW_DATE_FLOOR_TOKEN = re.compile(r"بالا(?:ی)?\s*(80)(?!\d)")
_THURSDAY = re.compile(r"پنج\s*شنبه|پنجشنبه|کشیک")
_CONDITIONAL = re.compile(
    r"فیش|شرط|مهلت|واریز|تسویه|چک|حساب|شب\s*ح(?:ساب)?|ش\s*ح(?:ساب)?|"
    r"تا\s*\d{1,2}(?::\d{2})?\s*(?:شب|ظهر|عصر)|توضیحات\s*[:：]"
)
_NON_OFFER = re.compile(
    r"نحوه\s+گذاشتن|مثال|لینک\s+ارسالی|بات\s+تلگرامی|شروع\s+معاملات|"
    r"آماده\s*بکار|عضو\s+شدند"
)

# These are deliberately broad family-level safety envelopes, not live market
# ranges.  Exact scale and unnamed commodity resolution use strictly-prior,
# same-book context at the message timestamp.
_PRICE_BOUNDS = {
    "IMAM": (130_000, 350_000),
    "BAHAR": (130_000, 350_000),
    "QUARTER_BAHAR": (30_000, 100_000),
    "HALF_BAHAR": (70_000, 180_000),
    "QUARTER_LOW_DATE": (30_000, 100_000),
    "HALF_LOW_DATE": (70_000, 180_000),
    "ONE_GRAM": (15_000, 60_000),
}
_GLOBAL_PRICE_LOW = min(low for low, _high in _PRICE_BOUNDS.values())
_GLOBAL_PRICE_HIGH = max(high for _low, high in _PRICE_BOUNDS.values())
_CONTEXTUAL_PRICE_MAXIMUM_RELATIVE_DISTANCE = 0.08
_CONTEXTUAL_PRICE_MINIMUM_RUNNER_MARGIN = 0.002

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
    + r"))?|صد)\s*(?:د?تا|عدد|دونه|دانه)(?![آ-ی])"
)
_QUANTITY_ALIASES = re.compile(
    r"(?<![آ-ی])(?:ی\s*دونه|یه\s*دونه|یک\s*دونه|دونه|دانه|یکی)(?![آ-ی])|"
    r"(?<![آ-ی])بیستا(?![آ-ی])|(?<![آ-ی])ا\s*عدد(?![آ-ی])"
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
    # Recover common missing-space forms before tokenization.
    normalized = re.sub(
        r"((?:امام(?:ی)?|بهار|آزادی|ربع|رب|نیم)\s*40[34])(?=\d{5,6})",
        r"\1 ",
        normalized,
    )
    normalized = re.sub(
        r"(\d{2,3}[٬،,./]\d{3})(?=(?:0?[1-9]|[1-9]\d|100)\s*(?:د?تا|عدد|دونه|دانه))",
        r"\1 ",
        normalized,
    )
    return " ".join(normalized.split())


def _commodity(text: str) -> str | None:
    if re.search(r"(?:یک\s*گرمی|گرمی|مرکزی)", text):
        return "ONE_GRAM"
    low_date = bool(
        re.search(
            r"تاریخ\s*(?:پایین|پاین|پایبن)|ت\s*\.?\s*پ|"
            r"(?<![آ-ی])(?:پایین|پاین|پایبن|پ)(?![آ-ی])|"
            # Traders also describe old-date quarter/half coins by the
            # accepted mint-year floor, most commonly `ربع بالا 80`.
            # Restrict the alias to the literal year marker so an unrelated
            # adjective such as `قیمت بالا` cannot change the commodity.
            r"(?<![آ-ی])بالا(?:ی)?\s*80(?!\d)",
            text,
        )
    )
    if re.search(r"(?:ربع|(?<![آ-ی])رب)(?![آ-ی])", text):
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
    word_value, word_spans = _word_quantity(text)
    if word_value is not None:
        return word_value, word_spans
    alias = _QUANTITY_ALIASES.search(text)
    if alias is None:
        return None, []
    return (20 if "بیست" in alias.group() else 1), [alias.span()]


def _spans_overlap(first: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(first[0] < end and first[1] > start for start, end in spans)


def _price_candidates(
    text: str,
    excluded_spans: Iterable[tuple[int, int]],
    *,
    commodity: str | None = None,
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
        long_decimal = re.fullmatch(
            r"(\d{2,3})[٬،,./](\d{4,5})",
            match.group(1),
        )
        short_decimal = re.fullmatch(
            r"(\d{2,3})[٬،,./](\d{1,2})",
            match.group(1),
        )
        if (
            long_decimal is not None
            and set(long_decimal.group(2)[3:]) == {"0"}
        ):
            values.append(
                (
                    int(long_decimal.group(1)) * 1000
                    + int(long_decimal.group(2)[:3]),
                    0.82,
                )
            )
        elif short_decimal is not None:
            whole = int(short_decimal.group(1))
            fraction = int(short_decimal.group(2).ljust(3, "0"))
            values.append((whole * 1000 + fraction, 1.0))
        elif length in {8, 9} and raw % 1_000 == 0:
            # Some group clients paste the full Toman amount, e.g.
            # `188.750.000`; the project contract stores thousand Toman.
            values.append((raw // 1_000, 1.0 if separated else 0.92))
            if length == 8 and raw % 100 == 0:
                values.append((raw // 100, 0.72))
        elif length in {5, 6}:
            values.append((raw, 1.0 if separated else 0.96))
            # Missing/redundant terminal zeroes are common.  Keep the alternate
            # interpretation at a lower grammar score; an explicit family or
            # strictly-prior price context must make it unique.
            if length == 5:
                values.append((raw * 10, 0.80))
            elif raw % 10 == 0:
                values.append((raw // 10, 0.80))
        elif length == 7 and raw % 10 == 0:
            values.append((raw // 10, 0.72))
            if raw % 100 == 0 and commodity is not None:
                # A duplicated terminal zero also appears in otherwise
                # explicit low-price coin offers.  Keep this interpretation
                # weaker than the canonical scale so family bounds or causal
                # same-time context must disambiguate it.  An unnamed seven-
                # digit Imam quote must retain its canonical /10 scale rather
                # than becoming ambiguous with an unrelated low-price family.
                values.append((raw // 100, 0.70))
        elif length == 4:
            values.extend(((raw * 10, 0.72), (raw * 100, 0.70)))
        elif length == 3:
            values.append((raw * 1000, 0.90))
            # A named low-price coin may use a one-decimal shorthand such as
            # `458` for `45.8`; the explicit commodity band makes that scale
            # deterministic. Unnamed prices retain the canonical x1000 scale.
            values.append((raw * 100, 0.78))
        elif length == 2:
            values.append((raw * 1000, 0.78))
        for value, score in values:
            if _GLOBAL_PRICE_LOW <= value <= _GLOBAL_PRICE_HIGH:
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
    price_context: Mapping[str, Sequence[int]] | None = None,
) -> tuple[int | None, list[tuple[int, int]]]:
    candidates = _price_candidates(text, excluded_spans, commodity=commodity)
    context_codes = (commodity,) if commodity is not None else tuple(_PRICE_BOUNDS)
    context_centers = {
        code: float(median(tuple(int(item) for item in prices)))
        for code in context_codes
        if price_context is not None
        and (prices := price_context.get(code, ()))
    }
    # A bare ``500``/``100`` in price position can be the tail of the current
    # quote. Reconstruct it only against causal same-book context.
    for match in _NUMBER.finditer(text):
        if _spans_overlap(match.span(1), excluded_spans):
            continue
        digits = re.sub(r"\D", "", match.group(1))
        raw = int(digits)
        missing_hundreds = re.fullmatch(
            r"(\d{2})[٬،,./](\d{3})",
            match.group(1),
        )
        if missing_hundreds is not None:
            visible = (
                int(missing_hundreds.group(1)) * 1000
                + int(missing_hundreds.group(2))
            )
            for code, center in context_centers.items():
                base = int(center // 100_000) * 100_000
                for value in (
                    base - 100_000 + visible,
                    base + visible,
                    base + 100_000 + visible,
                ):
                    low, high = _PRICE_BOUNDS[code]
                    if low <= value <= high:
                        candidates.append((value, 0.70, match.span(1)))
        if len(digits) not in {3, 4} or raw % 50:
            continue
        for code, center in context_centers.items():
            scale = 1000 if len(digits) == 3 else 10_000
            base = int(center // scale) * scale
            for value in (base - scale + raw, base + raw, base + scale + raw):
                low, high = _PRICE_BOUNDS[code]
                if low <= value <= high:
                    candidates.append((value, 0.74, match.span(1)))
    if commodity is not None:
        low, high = _PRICE_BOUNDS[commodity]
        candidates = [item for item in candidates if low <= item[0] <= high]
    if not candidates:
        return None, []
    contextual: list[tuple[float, float, int, tuple[int, int]]] = []
    for value, grammar_score, span in candidates:
        codes = (commodity,) if commodity is not None else tuple(_PRICE_BOUNDS)
        centers = [
            float(median(prices))
            for code in codes
            if price_context is not None
            and (prices := tuple(int(item) for item in price_context.get(code, ())))
        ]
        if not centers:
            continue
        distance = min(abs(value - center) / center for center in centers if center > 0)
        if distance <= _CONTEXTUAL_PRICE_MAXIMUM_RELATIVE_DISTANCE:
            contextual.append((distance, -grammar_score, value, span))
    if contextual:
        contextual.sort()
        best = contextual[0]
        # Different representations that normalize to the same value are not
        # ambiguous.  Otherwise require a useful temporal-distance margin.
        runner = next((item for item in contextual[1:] if item[2] != best[2]), None)
        if (
            runner is None
            or runner[0] - best[0] >= _CONTEXTUAL_PRICE_MINIMUM_RUNNER_MARGIN
        ):
            return best[2], [best[3]]
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
    """Resolve the private-group book; marker-less offers default tomorrow."""

    normalized = _text(text)
    explicit_cash, explicit_tomorrow = coin_group_settlement_markers(normalized)
    if explicit_tomorrow:
        return "TOMORROW"
    if explicit_cash:
        return "CASH"
    # This feed historically omits a settlement marker for the dominant
    # tomorrow book.  Cash must be explicit (`ن`, `نق`, or a cash word); using
    # the side marker itself as cash mixed Imam-tomorrow into Bahar-cash.
    return "TOMORROW"


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


def coin_group_offer_field_evidence(
    text: str,
    parsed: ParsedCoinGroupOffer,
) -> dict[str, tuple[str, ...]]:
    """Return redacted, field-level provenance for one parsed offer.

    Evidence names describe the deterministic rule that supplied a field;
    they never contain the private message text, numeric token, sender, or
    Telegram identity.  Commodity resolution may be refined by the causal
    resolver before projection.
    """

    explicit_cash, explicit_tomorrow = coin_group_settlement_markers(text)
    settlement_evidence = (
        "EXPLICIT_TOMORROW_MARKER"
        if explicit_tomorrow
        else "EXPLICIT_CASH_MARKER"
        if explicit_cash
        else "DEFAULT_TOMORROW_BOOK"
    )
    return {
        "event_type": ("COMPLETE_OFFER_GRAMMAR",),
        "instrument": (
            "EXPLICIT_COMMODITY_TOKEN"
            if parsed.commodity_code is not None
            else "TEMPORAL_RESOLUTION_REQUIRED",
        ),
        "side": ("EXPLICIT_SIDE_MARKER",),
        "price": ("MESSAGE_NUMERIC_GRAMMAR",),
        "quantity": ("MESSAGE_QUANTITY_GRAMMAR",),
        "settlement": (settlement_evidence,),
        "trade_form": ("MESSAGE_FORM_GRAMMAR",),
        "conditional": ("MESSAGE_CONDITION_GRAMMAR",),
    }


def _offer_segments(value: str) -> list[str]:
    """Split repeated explicit quantity clauses without inventing terms."""

    normalized = _text(value)
    starts = [match.start() for match in _QUANTITY.finditer(normalized)]
    if len(starts) < 2:
        return [normalized]
    prefix = normalized[: starts[0]].strip()
    segments = [
        " ".join(part for part in (prefix, normalized[start:end]) if part).strip()
        for start, end in zip(starts, (*starts[1:], len(normalized)), strict=True)
    ]
    # A second quantity may describe a payment/lot condition inside one offer
    # (`100 تا نیم ... نهایت 2 تا حساب`).  Split only when every clause carries
    # its own side signal.
    return (
        segments
        if all(_side(segment) is not None for segment in segments)
        else [normalized]
    )


def _can_collapse_offer_lines(lines: Sequence[str]) -> bool:
    """Allow only one incomplete offer to span visual lines."""

    normalized = tuple(_text(line) for line in lines if _text(line))
    if len(normalized) < 2:
        return False
    side_lines = sum(_side(line) is not None for line in normalized)
    quantity_lines = sum(_explicit_quantity(line)[0] is not None for line in normalized)
    commodity_lines = sum(_commodity(line) is not None for line in normalized)
    return side_lines == 1 and quantity_lines == 1 and commodity_lines <= 1


def parse_coin_group_offers(
    source: CoinGroupMessageInput,
    *,
    price_context: Mapping[str, Sequence[int]] | None = None,
) -> list[ParsedCoinGroupOffer]:
    """Parse only self-contained offer lines; unrelated text yields no fact."""

    if int(source.group_number) not in {1, 2}:
        raise ValueError("coin_group_number_unsupported")
    whole = _text(source.text)
    if not whole or _NON_OFFER.search(whole):
        return []
    lines = [item for item in str(source.text).splitlines() if _text(item)] or [whole]
    batches = [[segment for line in lines for segment in _offer_segments(line)]]
    if _can_collapse_offer_lines(lines):
        # Some clients split one offer across visual lines (instrument/quantity
        # then price/side).  Preserve the established per-line interpretation
        # first and only collapse the message when it produced no offer at all.
        batches.append(_offer_segments(whole))
    for segments in batches:
        results: list[ParsedCoinGroupOffer] = []
        for text in segments:
            if _THURSDAY.search(text):
                continue
            commodity = _commodity(text)
            side = _side(text)
            year_spans = [match.span() for match in _YEAR_TOKEN.finditer(text)]
            year_spans.extend(
                match.span(1) for match in _LOW_DATE_FLOOR_TOKEN.finditer(text)
            )
            quantity, quantity_spans = _explicit_quantity(text)
            remaining_numbers = [
                match
                for match in _NUMBER.finditer(text)
                if not _spans_overlap(match.span(1), (*quantity_spans, *year_spans))
            ]
            if commodity is not None and len(remaining_numbers) >= 2:
                # `86` is a prevalent mint-year annotation.  Treat it as metadata
                # only beside an explicit commodity and another price token, so a
                # genuine shorthand quote of 86 is not discarded.
                year_spans.extend(
                    match.span(1)
                    for match in remaining_numbers
                    if re.sub(r"\D", "", match.group(1)) == "86"
                )
            price, price_spans = _price(
                text,
                (*quantity_spans, *year_spans),
                commodity,
                price_context,
            )
            if quantity is None and price is not None:
                quantity, quantity_spans = _bare_quantity(
                    text,
                    (*year_spans, *price_spans),
                )
            if side is None or quantity is None or price is None:
                continue
            trade_form, settlement = _dimensions(text)
            # A syntactically complete explicit offer is usable immediately.  The
            # resolver may still reject an explicit commodity when authoritative,
            # strictly-prior evidence proves a real price-book conflict, but a
            # missing market anchor is not itself ambiguity in a parsed message.
            quality_state = "ELIGIBLE" if commodity is not None else "PENDING_REVIEW"
            reason = (
                "UNNAMED_COMMODITY_REQUIRES_POINT_IN_TIME_PRICE_RESOLUTION"
                if commodity is None
                else "EXPLICIT_COMMODITY_PARSED_WITH_COMPLETE_REQUIRED_FIELDS"
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
        if results:
            return results
    return []


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
                    "field_evidence": coin_group_offer_field_evidence(
                        source.text,
                        parsed,
                    ),
                },
            )
        )
    return observations
