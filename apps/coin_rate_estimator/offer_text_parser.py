#!/usr/bin/env python3
"""Supervised, deterministic parser for manually reviewed Persian coin offers."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


TEHRAN = ZoneInfo("Asia/Tehran")
DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك",
    "01234567890123456789یک",
)
COMMODITIES = (
    "امام",
    "بهار",
    "نیم بهار",
    "ربع بهار",
    "نیم تاریخ پایین",
    "ربع تاریخ پایین",
    "یک گرمی",
)
DEFAULT_PRICE_CENTERS = {
    "امام": 180_000,
    "بهار": 175_000,
    "نیم بهار": 92_000,
    "ربع بهار": 51_000,
    "نیم تاریخ پایین": 88_000,
    "ربع تاریخ پایین": 44_000,
    "یک گرمی": 24_000,
}
PLAUSIBLE_PRICE_RANGES = {
    "امام": (120_000, 300_000),
    "بهار": (110_000, 280_000),
    "نیم بهار": (60_000, 160_000),
    "ربع بهار": (25_000, 100_000),
    "نیم تاریخ پایین": (55_000, 150_000),
    "ربع تاریخ پایین": (20_000, 90_000),
    "یک گرمی": (8_000, 70_000),
}
LOW_DATE_MARKERS = (
    "تاریخ پایین",
    "پایین",
    "قدیم",
    "قبل 86",
    "قبل86",
    "ا پ",
)
WORD_QUANTITIES = {
    "یک": 1,
    "یه": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "پنج": 5,
    "شش": 6,
    "هفت": 7,
    "هشت": 8,
    "نه": 9,
    "ده": 10,
    "یازده": 11,
    "دوازده": 12,
    "پانزده": 15,
    "بیست": 20,
    "سی": 30,
    "چهل": 40,
    "پنجاه": 50,
}
CASH_MARKERS = ("نقدی", "نفدی", "نغدی", "نقد", "امروز", "حاضر")
TOMORROW_MARKERS = ("فردا", "فردایی", "شب حساب", "شب ح", "ش ح")
PAPER_MARKERS = ("کاغذ", "حواله", "غیررسمی", "غیر رسمی")


@dataclass(frozen=True)
class LabeledOffer:
    offer_id: int
    text: str
    commodity: str
    settlement: str
    trade_form: str
    side: str
    price: int
    quantity: int | None
    occurred_at_utc: str
    created_at_utc: str
    is_live_at_entry: bool


@dataclass(frozen=True)
class SequenceContext:
    offer_id: int
    occurred_at_utc: str
    created_at_utc: str
    is_live_at_entry: bool

    @property
    def occurred_tehran(self) -> datetime:
        return parse_utc(self.occurred_at_utc).astimezone(TEHRAN)


@dataclass(frozen=True)
class ClockMatch:
    hour: int
    minute: int
    span: tuple[int, int]
    raw: str


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_text(value: str) -> str:
    normalized = value.translate(DIGIT_TRANSLATION)
    normalized = normalized.replace("\u200c", " ").replace("\u200f", " ")
    normalized = normalized.replace("ـ", "")
    return " ".join(normalized.replace("\r", " ").replace("\n", " ").split())


def marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:(?<=^)|(?<=\s)|(?<=\d)){re.escape(marker)}(?=\s|\d|$)"
    )


BUY_MARKER = marker_pattern("خ")
SELL_MARKER = marker_pattern("ف")
CASH_N_MARKER = marker_pattern("ن")


def find_clock(text: str) -> ClockMatch | None:
    patterns = (
        re.compile(
            r"(?:ساعت\s*)?(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?!\d)"
        ),
        re.compile(
            r"(?:^|\bساعت\s+)([01]?\d|2[0-3])\s*[./٫]\s*([0-5]\d)(?!\d)"
        ),
        re.compile(
            r"\bساعت\s+([01]?\d|2[0-3])\s+(?:و\s+)?([0-5]?\d)(?!\d)"
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return ClockMatch(
                hour=int(match.group(1)),
                minute=int(match.group(2)),
                span=match.span(),
                raw=match.group(0).strip(),
            )
    return None


def remove_span(text: str, span: tuple[int, int] | None) -> str:
    if span is None:
        return text
    return " ".join((text[: span[0]] + " " + text[span[1] :]).split())


def strip_clock_from_raw_offer(text: str) -> str:
    """Remove the operator-only clock while preserving the offer wording."""

    searchable = (
        text.translate(DIGIT_TRANSLATION)
        .replace("\u200c", " ")
        .replace("\u200f", " ")
        .replace("ـ", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    clock = find_clock(searchable)
    if clock is None:
        return text.strip()

    left = text[: clock.span[0]].rstrip()
    right = text[clock.span[1] :].lstrip()
    separators = r"[\-–—|،,؛;]+"
    left = re.sub(rf"\s*{separators}\s*$", "", left)
    right = re.sub(rf"^\s*{separators}\s*", "", right)
    return re.sub(
        r"[ \t]{2,}",
        " ",
        " ".join(part for part in (left, right) if part),
    ).strip()


def has_low_date_marker(text: str) -> bool:
    if any(marker in text for marker in LOW_DATE_MARKERS):
        return True
    return bool(
        re.search(r"(?:ربع|رب|بع|ریع|نیم)\s*(?:ت|پ)(?=\s|\d|$)", text)
        or re.search(r"(?:ربع|رب|بع|ریع|نیم)\s+ا\s+پ(?:\s|$)", text)
        or re.search(r"(?:بالا(?:ی)?\s*80)(?:\s|$)", text)
    )


def explicit_commodity(text: str) -> tuple[str | None, str | None]:
    low_date = has_low_date_marker(text)
    if (
        "یک گرمی" in text
        or re.search(r"(?:^|\s)1\s*گرمی", text)
        or re.search(r"(?:^|\s)گرمی(?:\s|$)", text)
    ):
        return "یک گرمی", "EXPLICIT_ONE_GRAM"
    if re.search(r"(?:^|\s)(?:ربع|رب|بع|ریع)(?=\s|\d|$)", text):
        return (
            ("ربع تاریخ پایین", "EXPLICIT_QUARTER_LOW_DATE")
            if low_date
            else ("ربع بهار", "EXPLICIT_QUARTER")
        )
    if "نیم" in text:
        return (
            ("نیم تاریخ پایین", "EXPLICIT_HALF_LOW_DATE")
            if low_date
            else ("نیم بهار", "EXPLICIT_HALF")
        )
    if "بهار" in text or low_date:
        return "بهار", "EXPLICIT_BAHAR_OR_LOW_DATE_FULL"
    if "امام" in text or "تمام" in text:
        return "امام", "EXPLICIT_IMAM"
    if re.search(r"(?:^|\s)پ(?:\s|$)", text):
        return "بهار", "EXPLICIT_BAHAR_SHORT"
    return None, None


def detect_side(text: str) -> tuple[str | None, str | None]:
    buy_word = any(
        marker in text
        for marker in ("خرید", "میخرم", "می خرم", "خریدم", "خریدار")
    )
    sell_word = any(
        marker in text
        for marker in ("فروش", "میفروشم", "می فروشم", "فروختم", "فروشنده")
    )
    has_buy_short = bool(
        BUY_MARKER.search(text)
        or re.search(r"تا\s*خ(?=\s|\d|$)", text)
        or re.search(r"خ\s*ن(?=\s|\d|$)", text)
        or re.search(r"خ(?=نیم|ربع|رب|بع|ریع|امام|بهار)", text)
        or re.search(r"خ{2,}(?=\s|\d|$)", text)
    )
    has_sell_short = bool(
        SELL_MARKER.search(text)
        or re.search(r"تا\s*ف(?=\s|\d|$)", text)
        or re.search(r"ف\s*ن(?=\s|\d|$)", text)
        or re.search(r"ف(?=نیم|ربع|رب|بع|ریع|امام|بهار)", text)
        or re.search(r"ف{2,}(?=\s|\d|$)", text)
    )
    if buy_word and not sell_word:
        return "BUY", "EXPLICIT_BUY_WORD"
    if sell_word and not buy_word:
        return "SELL", "EXPLICIT_SELL_WORD"
    if has_buy_short:
        # In group shorthand ``خ ... ف`` means a buy for tomorrow. The buy
        # marker owns the side and the second ``ف`` is a settlement hint.
        return "BUY", "EXPLICIT_BUY_SHORT"
    if has_sell_short:
        return "SELL", "EXPLICIT_SELL_SHORT"
    return None, None


def detect_settlement(text: str, side: str | None) -> tuple[str, str]:
    date_low_short = bool(re.search(r"(?:^|\s)ت\s*پ(?:\s|$)", text))
    cash_t_short = bool(
        # ``ت`` immediately after خ/ف is the reviewed group's compact
        # today/cash marker (for example ``خ ت``). A standalone ``ت`` between
        # quantity and side (``20 ت خ``) is only a clipped ``تا`` and must not
        # turn a tomorrow offer into cash.
        re.search(r"(?:^|\s)[خف]\s*ت(?=\s|$)", text)
        and not date_low_short
    )
    compact_future = bool(
        # Historical syntax: ``خ ن ف`` / ``ف ن ف``.  Current syntax:
        # ``خ ف`` / ``ف ف``.  The last ف is the delivery marker and therefore
        # wins over the older intermediate cash/payment marker.
        re.search(r"(?:^|\s)[خف]\s*(?:ن\s*)?ف(?=\s|\d|$)", text)
    )
    if any(marker in text for marker in TOMORROW_MARKERS) or compact_future:
        return "TOMORROW", "EXPLICIT_TOMORROW"
    if (
        any(marker in text for marker in CASH_MARKERS)
        or re.search(r"(?<![آ-ی])نق(?![آ-ی])", text)
        or CASH_N_MARKER.search(text)
        or re.search(r"[خف]\s*ن(?=\s|\d|$)", text)
        or cash_t_short
        or "تک حساب تک فیش" in text
    ):
        return "CASH", "EXPLICIT_CASH"
    # Current group syntax makes a single side marker cash (خ / ف).  Tomorrow
    # requires the second ف above.  Text without a reliable side remains on the
    # conservative historical default and is not promoted by this function.
    if side in {"BUY", "SELL"}:
        return "CASH", "CURRENT_SIDE_ONLY_CASH"
    return "TOMORROW", "GROUP_UNRESOLVED_DEFAULT_TOMORROW"


def detect_trade_form(text: str) -> tuple[str, str]:
    if any(marker in text for marker in PAPER_MARKERS):
        return "PAPER", "EXPLICIT_PAPER"
    return "PHYSICAL", "COIN_DEFAULT_PHYSICAL"


def quantity_match(text: str) -> tuple[int | None, tuple[int, int] | None, str | None]:
    if any(
        marker in text
        for marker in ("یدونه", "یه دونه", "یک دونه", "ادونه", "بدونه")
    ):
        return 1, None, "ONE_WORD"
    quantity_range = re.search(
        r"از\s*(\d{1,3})\s*تا\s*(\d{1,3})\s*تا",
        text,
    )
    if quantity_range:
        return (
            int(quantity_range.group(2)),
            quantity_range.span(),
            "EXPLICIT_QUANTITY_RANGE_MAXIMUM",
        )
    compact_pair = re.search(
        r"(?<!\d)(\d{1,6})\s*[خف]\s*(\d{1,3})(?!\d)",
        text,
    )
    if compact_pair:
        first = int(compact_pair.group(1))
        second = int(compact_pair.group(2))
        if first > 100 and second <= 100:
            return (
                second,
                compact_pair.span(2),
                "COMPACT_PRICE_SIDE_QUANTITY",
            )
        if first <= 100 and second > 100:
            return (
                first,
                compact_pair.span(1),
                "COMPACT_QUANTITY_SIDE_PRICE",
            )
    word_pattern = "|".join(
        sorted(map(re.escape, WORD_QUANTITIES), key=len, reverse=True)
    )
    patterns = (
        re.compile(r"(?<!\d)(\d{1,3})\s*(?:تا|عدد|دونه|دانه)"),
        re.compile(r"(?:تا|تعداد)\s*(\d{1,3})(?!\d)"),
        re.compile(rf"(?:^|\s)({word_pattern})\s*(?:تا|عدد|دونه|دانه)(?:\s|$)"),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            raw = match.group(1)
            quantity = (
                WORD_QUANTITIES[raw]
                if raw in WORD_QUANTITIES
                else int(raw)
            )
            return quantity, match.span(1), "EXPLICIT_QUANTITY"
    leading = re.match(r"^\s*(\d{1,3})(?!\d)\s*[خفن](?=\s|\d)", text)
    if leading and int(leading.group(1)) <= 100:
        trailing_numbers = numeric_tokens(text[leading.end() :])
        if any(number >= 1_000 for _start, _end, _raw, number in trailing_numbers):
            return (
                int(leading.group(1)),
                leading.span(1),
                "LEADING_QUANTITY_BEFORE_SIDE_AND_FULL_PRICE",
            )
    return None, None, None


def numeric_tokens(
    text: str,
    *,
    excluded_spans: Iterable[tuple[int, int]] = (),
) -> list[tuple[int, int, str, int]]:
    excluded = tuple(excluded_spans)
    result = []
    for match in re.finditer(r"(?<!\d)\d+(?:\s*[,٬،/.]\s*\d+)*(?!\d)", text):
        if any(match.start() < end and match.end() > start for start, end in excluded):
            continue
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits:
            result.append((match.start(), match.end(), raw, int(digits)))
    return result


def plausible_scaled_values(raw: str, number: int) -> set[int]:
    compact = re.sub(r"\s", "", raw)
    if any(separator in compact for separator in (",", "٬", "،", "/", ".")):
        if 8_000 <= number <= 500_000:
            return {number}
        if (
            8_000_000 <= number <= 500_000_000
            and number % 1_000 == 0
        ):
            return {number // 1_000}
        return set()
    if number >= 8_000:
        if number <= 500_000:
            candidates = {number}
            if 10_000 <= number <= 30_000:
                candidates.add(number * 10)
            return candidates
        return set()
    if 1_000 <= number < 8_000:
        return {
            candidate
            for candidate in (number * 10, number * 100)
            if 8_000 <= candidate <= 500_000
        }
    if 10 <= number <= 999:
        return {
            candidate
            for candidate in (number * 1_000, number * 100)
            if 8_000 <= candidate <= 500_000
        }
    return set()


class TokenClassifier:
    """Small multinomial token model used only when deterministic rules abstain."""

    def __init__(self, labels: Sequence[str]) -> None:
        self.labels = tuple(labels)
        self.label_docs: Counter[str] = Counter()
        self.token_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.total_tokens: Counter[str] = Counter()
        self.vocabulary: set[str] = set()

    @staticmethod
    def tokens(text: str) -> set[str]:
        without_numbers = re.sub(r"\d+", " <NUM> ", normalize_text(text))
        words = re.findall(r"[آ-ی]+|<NUM>|[خفن]", without_numbers)
        result = set(words)
        result.update(
            "PAIR:" + words[index] + "_" + words[index + 1]
            for index in range(len(words) - 1)
        )
        return result

    def add(self, text: str, label: str) -> None:
        if label not in self.labels:
            return
        tokens = self.tokens(text)
        self.label_docs[label] += 1
        for token in tokens:
            self.token_counts[label][token] += 1
            self.total_tokens[label] += 1
            self.vocabulary.add(token)

    def predict(self, text: str) -> tuple[str | None, float]:
        total_docs = sum(self.label_docs.values())
        if total_docs <= 0:
            return None, 0.0
        vocabulary_size = max(1, len(self.vocabulary))
        scores: dict[str, float] = {}
        for label in self.labels:
            prior = (self.label_docs[label] + 1) / (
                total_docs + len(self.labels)
            )
            denominator = self.total_tokens[label] + vocabulary_size
            score = math.log(prior)
            for token in self.tokens(text):
                score += math.log(
                    (self.token_counts[label][token] + 1) / denominator
                )
            scores[label] = score
        maximum = max(scores.values())
        probabilities = {
            label: math.exp(score - maximum) for label, score in scores.items()
        }
        denominator = sum(probabilities.values())
        best = max(probabilities, key=probabilities.get)
        return best, probabilities[best] / denominator if denominator else 0.0


class SupervisedOfferParser:
    def __init__(self, examples: Sequence[LabeledOffer]) -> None:
        self.examples = tuple(examples)
        self.price_profiles = self._build_price_profiles(self.examples)
        self.side_classifier = TokenClassifier(("BUY", "SELL"))
        self.commodity_classifier = TokenClassifier(COMMODITIES)
        for example in self.examples:
            # A structured side chosen while the raw text itself is silent
            # must not teach the text classifier. This prevents the form's
            # historical default value from becoming self-reinforcing.
            explicit_side, _method = detect_side(normalize_text(example.text))
            if explicit_side == example.side:
                self.side_classifier.add(example.text, example.side)
            self.commodity_classifier.add(example.text, example.commodity)

    @staticmethod
    def _build_price_profiles(
        examples: Sequence[LabeledOffer],
    ) -> dict[tuple[str, str | None], dict[str, float]]:
        buckets: dict[tuple[str, str | None], list[int]] = defaultdict(list)
        for example in examples:
            low, high = PLAUSIBLE_PRICE_RANGES.get(
                example.commodity, (8_000, 500_000)
            )
            if not low <= example.price <= high:
                continue
            buckets[(example.commodity, example.settlement)].append(example.price)
            buckets[(example.commodity, None)].append(example.price)
        profiles: dict[tuple[str, str | None], dict[str, float]] = {}
        for key, values in buckets.items():
            recent = values[-100:]
            center = float(median(recent))
            deviations = [abs(value - center) for value in recent]
            profiles[key] = {
                "median": center,
                "mad": float(median(deviations)) if deviations else 0.0,
                "count": float(len(recent)),
            }
        return profiles

    def center(self, commodity: str, settlement: str | None) -> float:
        profile = self.price_profiles.get((commodity, settlement))
        if profile is None:
            profile = self.price_profiles.get((commodity, None))
        if profile is not None:
            return float(profile["median"])
        return float(DEFAULT_PRICE_CENTERS[commodity])

    def choose_price_and_commodity(
        self,
        text: str,
        *,
        commodity: str | None,
        settlement: str,
        quantity_span: tuple[int, int] | None,
        clock_span: tuple[int, int] | None,
    ) -> tuple[int | None, str, str, list[str]]:
        warnings: list[str] = []
        excluded = [
            span for span in (quantity_span, clock_span) if span is not None
        ]
        raw_tokens = numeric_tokens(text, excluded_spans=excluded)
        scored: list[tuple[float, int, str, str]] = []
        for _start, _end, raw, number in raw_tokens:
            if number in {403, 404, 1403, 1404}:
                continue
            for value in plausible_scaled_values(raw, number):
                if commodity is not None:
                    candidate_commodities = (commodity,)
                elif value < 70_000:
                    candidate_commodities = ("ربع بهار",)
                elif value < 120_000:
                    candidate_commodities = ("نیم بهار",)
                else:
                    # Project/group convention: an unnamed full-coin offer is
                    # Imam. Bahar/low-date full coins require a textual marker;
                    # their price gap is not stable enough across days.
                    candidate_commodities = ("امام",)
                for candidate_commodity in candidate_commodities:
                    low, high = PLAUSIBLE_PRICE_RANGES[candidate_commodity]
                    if not low <= value <= high:
                        continue
                    center = self.center(candidate_commodity, settlement)
                    score = abs(math.log(max(1, value) / max(1, center)))
                    scored.append((score, value, candidate_commodity, raw))
        if not scored:
            fallback = commodity or "امام"
            return None, fallback, "NO_PRICE", warnings
        scored.sort(key=lambda item: (item[0], item[1]))
        best_score, price, inferred_commodity, _raw = scored[0]
        plausible_prices = {
            value
            for score, value, candidate_commodity, _ in scored
            if candidate_commodity == inferred_commodity and score <= best_score + 0.04
        }
        if len(plausible_prices) > 1:
            warnings.append("MULTIPLE_PLAUSIBLE_PRICES")
        commodity_method = (
            "EXPLICIT_COMMODITY"
            if commodity is not None
            else "LEARNED_RECENT_PRICE_BAND"
        )
        return price, inferred_commodity, commodity_method, warnings

    def parse(
        self,
        text: str,
        *,
        previous: SequenceContext | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_text(text)
        clock = find_clock(normalized)
        content = remove_span(normalized, clock.span if clock else None)
        side, side_method = detect_side(content)
        settlement, settlement_method = detect_settlement(content, side)
        trade_form, trade_form_method = detect_trade_form(content)
        commodity, explicit_commodity_method = explicit_commodity(content)
        quantity, quantity_span, quantity_method = quantity_match(content)
        price, inferred_commodity, commodity_method, warnings = (
            self.choose_price_and_commodity(
                content,
                commodity=commodity,
                settlement=settlement,
                quantity_span=quantity_span,
                clock_span=None,
            )
        )
        commodity = commodity or inferred_commodity
        commodity_method = explicit_commodity_method or commodity_method

        if quantity is None and price is not None:
            tokens = numeric_tokens(content)
            remaining = [
                number
                for _start, _end, raw, number in tokens
                if (
                    0 < number <= 100
                    and price not in plausible_scaled_values(raw, number)
                )
            ]
            if len(remaining) == 1:
                quantity = remaining[0]
                quantity_method = "INFERRED_SMALL_NON_PRICE_NUMBER"

        if side is None:
            # Text without an explicit buy/sell marker is not sufficient to
            # populate a trading side. A token classifier previously guessed
            # here, but audited examples showed that it can invert a real
            # traded offer. Keep the field empty and require operator review.
            warnings.append("SIDE_REQUIRES_REVIEW")

        event_time = infer_event_time(clock, previous=previous, now=now)
        warnings.extend(event_time["warnings"])
        field_sources = {
            "commodity": commodity_method,
            "settlement": settlement_method,
            "trade_form": trade_form_method,
            "side": side_method or "UNRESOLVED",
            "price": (
                "LEARNED_PRICE_TOKEN_AND_BAND" if price is not None else "UNRESOLVED"
            ),
            "quantity": quantity_method or "UNRESOLVED",
            "offer_time": event_time["method"],
        }
        return {
            "commodity": commodity,
            "settlement": settlement,
            "trade_form": trade_form,
            "side": side or "",
            "price": price,
            "quantity": quantity,
            "offer_time": event_time["offer_time"],
            "offer_live": 0 if clock else None,
            "time_detected": clock is not None,
            "time_text": clock.raw if clock else None,
            "time_confidence": event_time["confidence"],
            "time_method": event_time["method"],
            "previous_offer_id": previous.offer_id if previous else None,
            "warnings": sorted(set(warnings)),
            "field_sources": field_sources,
            "parser": "reviewed-offer-hybrid-v2",
            "training_examples": len(self.examples),
        }


def infer_event_time(
    clock: ClockMatch | None,
    *,
    previous: SequenceContext | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if clock is None:
        return {
            "offer_time": None,
            "confidence": None,
            "method": "NO_TEXT_CLOCK",
            "warnings": [],
        }
    current = now or datetime.now(timezone.utc)
    current_tehran = (
        current.astimezone(TEHRAN)
        if current.tzinfo is not None
        else current.replace(tzinfo=TEHRAN)
    )
    candidate_clock = time(clock.hour, clock.minute)
    warnings: list[str] = []
    if previous is None:
        candidate_date = current_tehran.date()
        method = "TEXT_CLOCK_CURRENT_TEHRAN_DATE_NO_PREVIOUS"
        confidence = 0.55
        candidate = datetime.combine(candidate_date, candidate_clock, TEHRAN)
        if candidate > current_tehran + timedelta(hours=2):
            candidate -= timedelta(days=1)
            method = "TEXT_CLOCK_PREVIOUS_DATE_FROM_FUTURE_GUARD"
            confidence = 0.5
            warnings.append("FIRST_CLOCK_DATE_INFERRED_AS_PREVIOUS_DAY")
    else:
        previous_tehran = previous.occurred_tehran
        candidate_date = previous_tehran.date()
        previous_minutes = previous_tehran.hour * 60 + previous_tehran.minute
        candidate_minutes = clock.hour * 60 + clock.minute
        method = "TEXT_CLOCK_PREVIOUS_OFFER_DATE"
        confidence = 0.93
        if candidate_minutes < previous_minutes:
            backwards = previous_minutes - candidate_minutes
            is_session_rollover = (
                previous_minutes >= 15 * 60
                and candidate_minutes <= 12 * 60
                and backwards >= 3 * 60
            )
            is_midnight_rollover = (
                previous_minutes >= 20 * 60 and candidate_minutes <= 6 * 60
            )
            if is_session_rollover or is_midnight_rollover or backwards >= 12 * 60:
                candidate_date += timedelta(days=1)
                method = "TEXT_CLOCK_SEQUENCE_DAY_ROLLOVER"
                confidence = 0.88
                warnings.append("DATE_ADVANCED_FROM_SEQUENCE_ROLLOVER")
            else:
                confidence = 0.72
                warnings.append("NON_MONOTONIC_CLOCK_KEPT_ON_PREVIOUS_DATE")
        candidate = datetime.combine(candidate_date, candidate_clock, TEHRAN)
    return {
        "offer_time": candidate.strftime("%Y-%m-%dT%H:%M"),
        "confidence": round(confidence, 2),
        "method": method,
        "warnings": warnings,
    }


def rows_to_examples(rows: Iterable[Mapping[str, Any]]) -> list[LabeledOffer]:
    examples = []
    for row in rows:
        text = str(row["raw_offer_text"] or "").strip()
        commodity = str(row["commodity"])
        if not text or commodity not in COMMODITIES:
            continue
        examples.append(
            LabeledOffer(
                offer_id=int(row["id"]),
                text=text,
                commodity=commodity,
                settlement=str(row["settlement"]),
                trade_form=str(row["trade_form"]),
                side=str(row["side"]),
                price=int(row["price"]),
                quantity=(
                    int(row["quantity"]) if row["quantity"] is not None else None
                ),
                occurred_at_utc=str(row["occurred_at_utc"]),
                created_at_utc=str(row["created_at_utc"]),
                is_live_at_entry=bool(row["is_live_at_entry"]),
            )
        )
    return examples


def load_examples(path: Path) -> list[LabeledOffer]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='manual_coin_offers'
            """
        ).fetchone()
        if table is None:
            return []
        rows = connection.execute(
            """
            SELECT id, raw_offer_text, commodity, settlement, trade_form, side,
                   price, quantity, occurred_at_utc, created_at_utc,
                   is_live_at_entry
            FROM manual_coin_offers
            WHERE raw_offer_text IS NOT NULL AND trim(raw_offer_text)<>''
            ORDER BY created_at_utc, id
            """
        ).fetchall()
        return rows_to_examples(rows)
    finally:
        connection.close()


def load_previous_context(path: Path) -> SequenceContext | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='manual_coin_offers'
            """
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            """
            SELECT id, occurred_at_utc, created_at_utc, is_live_at_entry
            FROM manual_coin_offers
            ORDER BY created_at_utc DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return SequenceContext(
            offer_id=int(row["id"]),
            occurred_at_utc=str(row["occurred_at_utc"]),
            created_at_utc=str(row["created_at_utc"]),
            is_live_at_entry=bool(row["is_live_at_entry"]),
        )
    finally:
        connection.close()


_CACHE_LOCK = threading.Lock()
_PARSER_CACHE: dict[str, tuple[int, int, SupervisedOfferParser]] = {}


def trained_parser(path: Path) -> SupervisedOfferParser:
    resolved = path.resolve()
    stat = resolved.stat()
    key = str(resolved)
    signature = (stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _PARSER_CACHE.get(key)
        if cached and cached[:2] == signature:
            return cached[2]
    parser = SupervisedOfferParser(load_examples(resolved))
    with _CACHE_LOCK:
        _PARSER_CACHE[key] = (signature[0], signature[1], parser)
    return parser


def parse_reviewed_offer(
    text: str,
    *,
    conversation_db: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    parser = trained_parser(conversation_db)
    previous = load_previous_context(conversation_db)
    return parser.parse(text, previous=previous, now=now)
