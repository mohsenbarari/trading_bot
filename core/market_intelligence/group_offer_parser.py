#!/usr/bin/env python3
"""Conservatively extract structured fields from informal coin offers."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_INPUT = Path(os.environ.get("COIN_GROUP_EXPORT_JSON", "group_messages.json"))

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS + "يك",
    "0123456789" + "0123456789" + "یک",
)
PERSIAN_LETTERS = r"\u0600-\u06FF"
NUMBER_RE = re.compile(r"(?<!\d)\d+(?:\s*[٬،,./]\s*\d+)*(?!\d)")
SIDE_RE = re.compile(
    rf"خرید|فروش|(?<![{PERSIAN_LETTERS}])(?P<short>خ+|ف+)(?![{PERSIAN_LETTERS}])"
)
EXPLICIT_QUANTITY_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:د?تا|عدد)")
NON_OFFER_CONTEXT_RE = re.compile(
    r"(?:نحوه\s+گذاشتن|مثال|لینک\s+ارسالی|بات\s+تلگرامی|"
    r"شروع\s+معاملات|عضو\s+شدند|آماده\s*بکار|درخواست\s+دادند)"
)

WORD_QUANTITIES = {
    "یک": 1,
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
    "سیزده": 13,
    "چهارده": 14,
    "پانزده": 15,
    "شانزده": 16,
    "هفده": 17,
    "هجده": 18,
    "نوزده": 19,
    "بیست": 20,
    "بیس": 20,
    "بیست و پنج": 25,
    "سی": 30,
    "چهل": 40,
    "پنجاه": 50,
}
WORD_QUANTITY_RE = re.compile(
    rf"(?<![{PERSIAN_LETTERS}])({'|'.join(map(re.escape, sorted(WORD_QUANTITIES, key=len, reverse=True)))})\s*(?:د?تا|عدد)"
)

COMMODITY_CENTERS = {
    "امام": 183_000,
    "بهار": 178_000,
    "ربع بهار": 52_000,
    "نیم بهار": 94_000,
    "ربع تاریخ پایین": 43_000,
    "نیم تاریخ پایین": 87_000,
    "یک گرمی": 28_000,
}
COMMODITY_RANGES = {
    "امام": (135_000, 260_000),
    "بهار": (135_000, 250_000),
    "ربع بهار": (46_000, 70_000),
    "نیم بهار": (82_000, 110_000),
    "ربع تاریخ پایین": (35_000, 49_000),
    "نیم تاریخ پایین": (70_000, 100_000),
    "یک گرمی": (20_000, 36_000),
}


@dataclass(frozen=True)
class NumericToken:
    raw: str
    digits: str
    start: int
    end: int


@dataclass(frozen=True)
class PriceChoice:
    value: int
    token: NumericToken
    method: str
    reliability: float
    inferred_commodity: str | None


def normalize_text(value: str) -> str:
    value = value.translate(DIGIT_TRANSLATION)
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    return " ".join(value.split())


def numeric_tokens(text: str) -> list[NumericToken]:
    result: list[NumericToken] = []
    for match in NUMBER_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if digits:
            result.append(NumericToken(match.group(), digits, match.start(), match.end()))
    return result


def explicit_commodity(text: str) -> str | None:
    if re.search(r"(?:یک\s*گرمی|گرمی|مرکزی)", text):
        return "یک گرمی"

    quarter = re.search(r"ربع(?![آ-ی])", text)
    half = re.search(r"نیم(?![آ-ی])", text)
    low_date = re.search(
        r"(?:تاریخ\s*پایین|ت\s*\.?\s*پ|(?<![آ-ی])پایین(?![آ-ی])|"
        r"(?<![آ-ی])پ(?=\s|$)|بالا(?:ی)?\s*80)",
        text,
    )
    if quarter:
        return "ربع تاریخ پایین" if low_date else "ربع بهار"
    if half:
        return "نیم تاریخ پایین" if low_date else "نیم بهار"
    full_low_date = low_date or re.search(
        r"(?:تمام|تاریخ)\s*(?:زیر|پایین)",
        text,
    )
    if full_low_date:
        # The project's canonical full low-date coin is «بهار».  A bare
        # «پایین», «تاریخ پ», or «تمام زیر» must never be merged with Imam.
        return "بهار"
    if re.search(r"(?:بهار|آزادی)(?![آ-ی])", text):
        return "بهار"
    if re.search(
        r"(?:امام(?:ی)?|تمام|سکه\s+جدید|سکه\s+بانکی)(?![آ-ی])",
        text,
    ):
        return "امام"
    return None


def side_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in SIDE_RE.finditer(text)]


def offer_context(text: str) -> tuple[str, str, str]:
    """Return side, settlement, and physical/paper as independent dimensions."""
    explicit_tomorrow = bool(
        re.search(r"(?:فردا|فردایی|پنج\s*شنبه|(?<![آ-ی])شنبه(?![آ-ی]))", text)
        or re.search(r"(?<![آ-ی])(?:خ|ف)\s*ن\s*(?:ف|پ)(?![آ-ی])", text)
    )
    # In both observed coin trading groups, cash/physical settlement is marked
    # explicitly (نقد، نقدی، حاضر, or the standalone shorthand ن).  An offer
    # without a settlement marker belongs to the tomorrow book.  Defaulting an
    # unmarked offer to CASH merges two visibly different price clusters and
    # contaminates every linked trade because trades inherit their offer's
    # settlement.
    explicit_cash = bool(
        re.search(r"(?:نقدی|نفدی|نغدی|نقد|امروز|حاضر)", text)
        or re.search(r"(?:^|\s)ن(?=\s|\d|$)", text)
        or re.search(r"[خف]\s*ن(?=\s|\d|$)", text)
        or (
            re.search(r"(?:^|\s)[خف]\s*ت(?=\s|$)", text)
            and not re.search(r"(?:^|\s)ت\s*پ(?:\s|$)", text)
        )
        or "تک حساب تک فیش" in text
    )
    if "خرید" in text:
        side = "BUY"
    elif "فروش" in text:
        side = "SELL"
    else:
        match = SIDE_RE.search(text)
        marker = match.group() if match else ""
        side = "BUY" if marker.startswith("خ") else ("SELL" if marker else "UNKNOWN")
    paper = bool(re.search(r"(?:کاغذی|حواله|غیررسمی)", text))
    # A future marker is the actual delivery/settlement date and therefore
    # wins in phrases such as ``نقد فردا`` and the reviewed ``خ ن ف`` / ``ف ن
    # پ`` shorthands.  The cash word in those phrases describes payment, not a
    # same-day coin settlement.
    settlement = "TOMORROW" if explicit_tomorrow else ("CASH" if explicit_cash else "TOMORROW")
    return side, settlement, "PAPER" if paper else "PHYSICAL"


def span_distance(left: tuple[int, int], right: tuple[int, int]) -> int:
    if left[1] < right[0]:
        return right[0] - left[1]
    if right[1] < left[0]:
        return left[0] - right[1]
    return 0


def overlaps(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)


def explicit_quantity(text: str) -> tuple[int | None, list[tuple[int, int]], str | None]:
    matches = list(EXPLICIT_QUANTITY_RE.finditer(text))
    if matches:
        values = [int(match.group(1)) for match in matches]
        plausible = [(value, match.span(1)) for value, match in zip(values, matches) if 1 <= value <= 100]
        if plausible:
            return plausible[0][0], [item[1] for item in plausible], "explicit"

    word_match = WORD_QUANTITY_RE.search(text)
    if word_match:
        return WORD_QUANTITIES[word_match.group(1)], [word_match.span(1)], "explicit_word"
    return None, [], None


def price_variants(token: NumericToken) -> list[tuple[int, str, float]]:
    number = int(token.digits)
    length = len(token.digits)
    separated = bool(re.search(r"[٬،,./]", token.raw))
    variants: list[tuple[int, str, float]] = []

    if length in {5, 6}:
        variants.append((number, "full", 0.99 if separated else 0.97))
        if length == 5 and number < 20_000:
            variants.append((number * 10, "corrected_missing_zero", 0.62))
    elif length == 7 and number % 10 == 0:
        variants.append((number // 10, "corrected_extra_zero", 0.68))
    elif length == 4:
        variants.extend(
            [
                (number * 100, "expanded_shorthand", 0.86),
                (number * 10, "expanded_shorthand", 0.80),
            ]
        )
    elif length == 3:
        variants.extend(
            [
                (number * 1000, "expanded_shorthand", 0.90),
                (number * 100, "expanded_shorthand", 0.86),
            ]
        )
    elif length == 2:
        variants.append((number * 1000, "expanded_shorthand", 0.82))

    if length in {6, 7} and number > 260_000 and number % 10 == 0:
        variants.append((number // 10, "corrected_extra_zero", 0.62))

    unique: dict[int, tuple[int, str, float]] = {}
    for value, method, reliability in variants:
        if 20_000 <= value <= 260_000:
            current = unique.get(value)
            if current is None or reliability > current[2]:
                unique[value] = (value, method, reliability)
    return list(unique.values())


def inferred_commodity_for_price(value: int) -> tuple[str | None, float]:
    if 20_000 <= value <= 36_000:
        return "یک گرمی", 0.88
    if 36_001 <= value < 47_000:
        return "ربع تاریخ پایین", 0.86
    if 47_000 <= value <= 70_000:
        return "ربع بهار", 0.88
    if 70_001 <= value < 89_000:
        return "نیم تاریخ پایین", 0.72
    if 89_000 <= value <= 110_000:
        return "نیم بهار", 0.78
    if 135_000 <= value <= 260_000:
        # In this group an omitted full-coin name means the project's default Imam.
        return "امام", 0.84
    return None, 0.0


def token_has_non_price_context(text: str, token: NumericToken) -> bool:
    before = text[max(0, token.start - 14) : token.start]
    after = text[token.end : min(len(text), token.end + 14)]
    if re.search(r"(?:زیر|راس|ساعت|سال|ماه|بالا(?:ی)?)\s*$", before):
        return True
    if re.match(r"\s*(?:میلیارد|شنبه|تومن(?:ی|ی)?|تومان(?:ی)?)", after):
        return True
    return False


def fit_score(commodity: str, value: int) -> float | None:
    low, high = COMMODITY_RANGES[commodity]
    if not low <= value <= high:
        return None
    center = COMMODITY_CENTERS[commodity]
    half_width = max(center - low, high - center)
    return max(0.0, 1.0 - abs(value - center) / half_width)


def reconstruct_full_price(token: NumericToken, anchor: float | None) -> tuple[int, float] | None:
    """Expand a quoted full-coin tail such as 500 or 6800 near the local rate."""
    if anchor is None or len(token.digits) not in {3, 4}:
        return None
    tail = int(token.digits)
    if tail % 50 != 0:
        return None
    # Values around 90--99 thousand are conventional half-coin shorthand.
    if len(token.digits) == 3 and 890 <= tail <= 999:
        return None

    modulus = 10 ** len(token.digits)
    base = int(anchor // modulus) * modulus
    candidates = [base + tail, base - modulus + tail, base + modulus + tail]
    candidates = [value for value in candidates if 135_000 <= value <= 260_000]
    if not candidates:
        return None
    value = min(candidates, key=lambda candidate: (abs(candidate - anchor), candidate))
    if abs(value - anchor) > 6_000:
        return None
    reliability = 0.84 if len(token.digits) == 4 else 0.79
    return value, reliability


def choose_price(
    text: str,
    tokens: list[NumericToken],
    commodity: str | None,
    quantity_spans: list[tuple[int, int]],
    sides: list[tuple[int, int]],
    full_anchor: float | None = None,
) -> PriceChoice | None:
    ranked: list[tuple[float, PriceChoice]] = []
    for token in tokens:
        span = (token.start, token.end)
        if overlaps(span, quantity_spans) or token_has_non_price_context(text, token):
            continue
        nearest_side = min((span_distance(span, side) for side in sides), default=999)
        if commodity is None:
            reconstructed = reconstruct_full_price(token, full_anchor)
            if reconstructed is not None:
                value, reliability = reconstructed
                fit = fit_score("امام", value)
                if fit is not None:
                    score = reliability * 4.0 + fit * 1.8 + 0.84 + 2.0
                    if nearest_side <= 1:
                        score += 3.0
                    elif nearest_side <= 3:
                        score += 2.0
                    elif nearest_side <= 7:
                        score += 0.8
                    ranked.append(
                        (
                            score,
                            PriceChoice(
                                value,
                                token,
                                "contextual_tail",
                                reliability,
                                "امام",
                            ),
                        )
                    )
        for value, method, reliability in price_variants(token):
            inferred: str | None = None
            inference_reliability = 1.0
            if commodity is not None:
                fit = fit_score(commodity, value)
                if fit is None:
                    continue
            else:
                inferred, inference_reliability = inferred_commodity_for_price(value)
                if inferred is None:
                    continue
                fit = fit_score(inferred, value)
                if fit is None:
                    continue

            score = reliability * 4.0 + fit * 1.8 + inference_reliability
            if nearest_side <= 1:
                score += 3.0
            elif nearest_side <= 3:
                score += 2.0
            elif nearest_side <= 7:
                score += 0.8
            if len(token.digits) >= 5:
                score += 0.8
            if re.search(r"[٬،,./]", token.raw):
                score += 0.4
            ranked.append(
                (
                    score,
                    PriceChoice(value, token, method, reliability, inferred),
                )
            )

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best = ranked[0]
    if len(ranked) > 1:
        second_score, second = ranked[1]
        if second.value != best.value and best_score - second_score < 0.25:
            return None
    return best


def implicit_quantity(
    text: str,
    tokens: list[NumericToken],
    price_token: NumericToken,
    sides: list[tuple[int, int]],
) -> int | None:
    candidates: list[tuple[float, int]] = []
    for token in tokens:
        if token == price_token or re.search(r"[٬،,./]", token.raw):
            continue
        value = int(token.digits)
        if not 1 <= value <= 60 or token_has_non_price_context(text, token):
            continue
        span = (token.start, token.end)
        nearest_side = min((span_distance(span, side) for side in sides), default=999)
        score = 0.0
        if nearest_side <= 1:
            score += 3.0
        elif nearest_side <= 4:
            score += 2.0
        if token.start <= 2 or len(text) - token.end <= 2:
            score += 1.5
        if token.start < price_token.start:
            score += 0.4
        candidates.append((score, value))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if candidates[0][0] < 1.0:
        return None
    return candidates[0][1]


def confidence_score(
    *,
    commodity_was_explicit: bool,
    commodity: str,
    price_choice: PriceChoice,
    quantity_method: str | None,
) -> float:
    commodity_confidence = (
        0.98
        if commodity_was_explicit
        else inferred_commodity_for_price(price_choice.value)[1]
    )
    quantity_confidence = {
        "explicit": 0.99,
        "explicit_word": 0.96,
        "implicit": 0.80,
        None: 0.58,
    }[quantity_method]
    score = (
        0.42 * price_choice.reliability
        + 0.38 * commodity_confidence
        + 0.20 * quantity_confidence
    )
    if commodity in {"نیم بهار", "نیم تاریخ پایین"} and not commodity_was_explicit:
        score -= 0.04
    return round(max(0.0, min(1.0, score)), 2)


def extract_single_offer(text: str, full_anchor: float | None = None) -> dict[str, Any] | None:
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 260:
        return None

    commodity = explicit_commodity(normalized)
    sides = side_spans(normalized)
    tokens = numeric_tokens(normalized)
    quantity, quantity_spans, quantity_method = explicit_quantity(normalized)
    price = choose_price(
        normalized,
        tokens,
        commodity,
        quantity_spans,
        sides,
        full_anchor=full_anchor,
    )
    if price is None:
        return None

    if not sides and not (commodity is not None and quantity is not None):
        return None

    commodity_was_explicit = commodity is not None
    if commodity is None:
        commodity = price.inferred_commodity
    if commodity is None:
        return None

    # Product names are deliberately optional in the project's offer syntax.
    # A full-coin quote without an explicit product name has one business
    # default: Imam.  This is not a statistical commodity guess; it is the
    # user-facing contract.  Keeping a separate method lets the context layer
    # preserve that rule while still validating explicitly named products.
    commodity_method = "explicit" if commodity_was_explicit else "price_inference"
    if not commodity_was_explicit and commodity == "امام":
        commodity_method = "default_imam_omitted_commodity"

    if quantity is None:
        quantity = implicit_quantity(normalized, tokens, price.token, sides)
        if quantity is not None:
            quantity_method = "implicit"

    side, settlement, trade_form = offer_context(normalized)

    return {
        "commodity": commodity,
        "commodity_method": commodity_method,
        "price": price.value,
        "price_raw": price.token.raw,
        "price_method": price.method,
        "quantity": quantity,
        "quantity_method": quantity_method,
        "side": side,
        "settlement": settlement,
        "trade_form": trade_form,
        "confidence": confidence_score(
            commodity_was_explicit=commodity_was_explicit,
            commodity=commodity,
            price_choice=price,
            quantity_method=quantity_method,
        ),
    }


def _record_datetime(record: dict[str, Any]) -> datetime:
    value = record.get("date")
    if not isinstance(value, str):
        raise RuntimeError("Every message must contain an ISO date string")
    return datetime.fromisoformat(value)


def _prior_full_anchor(
    record: dict[str, Any],
    index: int,
    anchors_by_date: dict[str, list[tuple[float, int, int]]],
) -> float | None:
    when = _record_datetime(record)
    anchors = anchors_by_date.get(when.date().isoformat(), [])
    if not anchors:
        return None
    nearby = [
        item
        for item in anchors[-20:]
        if item[0] <= when.timestamp()
        and (
            when.timestamp() - item[0] <= 1800
            or 0 <= index - item[1] <= 60
        )
    ]
    if not nearby:
        return None
    nearby.sort(
        key=lambda item: (
            when.timestamp() - item[0],
            index - item[1],
        )
    )
    nearby = nearby[:8]
    weighted = [
        (1.0 / (30.0 + when.timestamp() - timestamp), price)
        for timestamp, _, price in nearby
    ]
    return sum(weight * price for weight, price in weighted) / sum(
        weight for weight, _ in weighted
    )


def extract_offers(text: str, full_anchor: float | None) -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    if not normalized or NON_OFFER_CONTEXT_RE.search(normalized):
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = [text]
    offers: list[dict[str, Any]] = []
    for line in lines:
        offer = extract_single_offer(line, full_anchor=full_anchor)
        if offer is not None:
            offers.append(offer)
    if len(offers) <= 1 and len(lines) > 1:
        whole_offer = extract_single_offer(text, full_anchor=full_anchor)
        if whole_offer is not None and (
            not offers
            or (
                offers[0]["quantity"] is None
                and whole_offer["quantity"] is not None
            )
            or whole_offer["confidence"] > offers[0]["confidence"]
        ):
            return [whole_offer]
    if offers:
        shared_side, shared_settlement, shared_trade_form = offer_context(normalized)
        for offer in offers:
            if offer["side"] == "UNKNOWN" and shared_side != "UNKNOWN":
                offer["side"] = shared_side
            if shared_settlement == "TOMORROW":
                offer["settlement"] = "TOMORROW"
            if shared_trade_form == "PAPER":
                offer["trade_form"] = "PAPER"
    return offers


def enrich_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors_by_date: dict[str, list[tuple[float, int, int]]] = {}
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        base = {"date": record.get("date"), "text": record.get("text", "")}
        anchor = _prior_full_anchor(record, index, anchors_by_date)
        offers = extract_offers(str(base["text"]), anchor)
        base["extracted_offers"] = offers
        result.append(base)
        when = _record_datetime(record)
        for offer in offers:
            # Only an explicit, trusted full Imam price may seed later
            # shorthand.  Reconstructed values never reinforce themselves.
            if (
                offer["commodity"] == "امام"
                and offer["price_method"] == "full"
                and offer["confidence"] >= 0.84
            ):
                anchors_by_date.setdefault(when.date().isoformat(), []).append(
                    (when.timestamp(), index, int(offer["price"]))
                )
    return result


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.input

    records = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise RuntimeError("Expected the group-export JSON root to be an array")
    enriched = enrich_records(records)
    write_json_atomic(output, enriched)

    detected = [row for row in enriched if row["extracted_offers"]]
    offers = [offer for row in detected for offer in row["extracted_offers"]]
    summary = {
        "output": str(output.resolve()),
        "messages": len(enriched),
        "messages_with_offers": len(detected),
        "messages_with_multiple_offers": sum(
            len(row["extracted_offers"]) > 1 for row in detected
        ),
        "offers_detected": len(offers),
        "offers_with_commodity": sum(row["commodity"] is not None for row in offers),
        "offers_with_price": sum(row["price"] is not None for row in offers),
        "offers_with_quantity": sum(row["quantity"] is not None for row in offers),
        "average_confidence": (
            round(sum(row["confidence"] for row in offers) / len(offers), 3)
            if offers
            else None
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
