"""Privacy-bounded condition extraction for private coin-group offers.

The first stage is intentionally hybrid: deterministic rules extract explicit
deadlines and high-precision condition spans; an offline text classifier can
then learn spelling and phrasing variants from those weak labels.  Settlement,
trade form, market-session phase, and deadline horizon remain separate axes so
economically different offers never collapse into one flat condition label.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Iterable
from zoneinfo import ZoneInfo


CONDITION_TAXONOMY_VERSION = "coin-offer-condition-taxonomy-v2"
DEFAULT_MARKET_OPEN_MINUTE = 10 * 60
DEFAULT_MARKET_CLOSE_MINUTE = 15 * 60

CONDITION_FAMILIES = (
    "PAYMENT_DEADLINE",
    "PAYMENT_RAIL",
    "PAYMENT_ACCOUNT",
    "SETTLEMENT_PROCESS",
    "CREDIT_CHEQUE",
    "DELIVERY_HANDOFF",
    "IDENTITY_ACCOUNT",
    "QUANTITY_EXECUTION",
    "ITEM_QUALITY_PACKAGING",
    "IMMEDIATE",
    "OTHER_EXPLICIT",
)

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})

_HOUR_WORDS = {
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
}
_CLOCK = (
    r"(?:(?<!\d)(?P<hour>[01]?\d|2[0-3])"
    r"(?::(?P<minute>[0-5]\d))?(?![:\d])|"
    r"(?P<hour_word>یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده|یازده|دوازده))"
)
_DAYPART = r"(?P<daypart>صبح|ظهر|عصر|شب)?"
_DEADLINE_RE = re.compile(
    rf"(?:(?:فیش|واریز|پرداخت|تسویه|مهلت)\s*(?:(?:تا\s*)?(?:ساعت|راس)?\s*)?|"
    rf"راس\s*){_CLOCK}\s*{_DAYPART}"
)
_RELATIVE_DEADLINE_RE = re.compile(
    r"(?:فیش|واریز|پرداخت|تسویه|مهلت)\s*(?:تا\s*)?"
    r"(?:(?P<relative_hour>\d+)|"
    r"(?P<relative_word>یه|یک|دو|سه|چهار|پنج|شش|هفت|هشت))\s*"
    r"(?:ساعت|ساعته)"
)
_BANK_NAMES = r"ملت|ملی|سامان|تجارت|صادرات|پاسارگاد|پارسیان|رفاه|آینده|شهر|مرکزی"
_PHRASE_GAP = r"[\s.،,:؛]*"
_SINGLE_ACCOUNT_PATTERN = r"تک\s*(?:حساب|خساب|ح(?!\S)|فیش|ملت)"
_SINGLE_ACCOUNT_RE = re.compile(_SINGLE_ACCOUNT_PATTERN)
_NIGHT_ACCOUNT_PATTERN = (
    r"(?:شب\s*(?:ح(?:ساب)?|خساب)|"
    r"(?<!\S)(?:ش\s*ح(?:ساب)?|شح|ح\s*شب|ح\s*ش)(?!\S)|"
    r"(?:حساب|خساب)\s*(?:شب|امشب|آخر\s*وقت))"
)
_NIGHT_ACCOUNT_RE = re.compile(_NIGHT_ACCOUNT_PATTERN)

_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PAYMENT_RAIL",
        re.compile(r"ساتنا|پایا|شبا|کارت\s*به\s*کارت|حواله|واریز"),
    ),
    (
        "PAYMENT_ACCOUNT",
        re.compile(
            rf"{_SINGLE_ACCOUNT_PATTERN}|"
            r"(?:دو|سه|چند|چنتا)\s*(?:حساب|خساب)|"
            r"(?:دو\s*تا|دوتا|سه\s*تا|چندتا|چنتا)\s*(?:حساب|خساب)|"
            r"\d+\s*تا\s*(?:حساب|خساب)|"
            r"(?:با\s*)?(?:دو|سه|چند)\s*تا\s*(?:حساب|خساب)|"
            r"(?:با\s*)?(?:حساب|خساب)\s*\d+(?:\s*(?:تومنی|تمنی|میلیونی))?|"
            r"(?<!\S)ح\s*\d+\s*(?:تومنی|تمنی|میلیونی)(?!\S)|"
            r"(?:با\s*)?(?:حساب|خساب)\s*(?:\d+\s*)?"
            r"(?:زود|رود|سریع|فوری|درشت|آماده|پونصدی|میلیونی|تومنی|تمنی|شرکت)|"
            r"(?:حساب|خساب)\s*(?:کم|الان|درشت|(?:می\s*)?(?:خوام|خام))|کم\s*(?:حساب|خساب)|"
            rf"فیش{_PHRASE_GAP}(?:زود|فوری|درشت|خوب|راس|حتما|"
            r"بالا|(?:یه|یک)\s*قلم|(?:می\s*)?(?:خوام|خام|دم))|"
            r"(?<!\S)فیش(?!\S)|"
            r"(?<!\S)ح\s*(?:خیلی\s*)?(?:زود|فوری|درشت|آماده|ملتی|تومنی|تمنی)(?!\S)|"
            r"(?<!\S)(?:ملت|ملتی|ملی\s*زود)(?!\S)|سرمایه\s*(?:به|ب)\s*سرمایه|"
            rf"(?:با|از|به|فقط)\s*(?:بانک\s*)?(?:{_BANK_NAMES})|"
            rf"(?:نقد|نقدی|حاضر)\s*(?:{_BANK_NAMES})|"
            rf"(?:حساب|(?<!\S)ح)\s*(?:{_BANK_NAMES})(?!\S)|"
            r"ملی\s*(?:به|و|یا)\s*(?:ملی|ملت)|"
            r"ملت\s*(?:به|و|یا)\s*(?:ملت|ملی)|به\s*شرط\s*بانکی"
        ),
    ),
    (
        "SETTLEMENT_PROCESS",
        re.compile(
            rf"تسویه|{_NIGHT_ACCOUNT_PATTERN}|"
            r"پای\s*حساب|تراز"
        ),
    ),
    (
        "CREDIT_CHEQUE",
        re.compile(r"چک|اعتبار|اعتباری|مدت\s*دار|سفته"),
    ),
    (
        "DELIVERY_HANDOFF",
        re.compile(
            r"تحویل|ارسال|باربری|درب\s*(?:مغازه|محل)|حضوری|جنس\s*حاضر|"
            r"(?:امروز|فردا)\s*جنس\s*(?:می\s*)?(?:گیرم|میگیرم)|"
            r"جنس\s*(?:امروز|فردا)"
        ),
    ),
    (
        "IDENTITY_ACCOUNT",
        re.compile(
            r"کد\s*ملی|حساب\s*(?:خود|همنام)|هم\s*نام|شرکت|حقوقی|فاکتور\s*رسمی"
        ),
    ),
    (
        "QUANTITY_EXECUTION",
        re.compile(
            r"یک\s*جا|یکجا|بکجا|(?<!\S)یه\s*جا(?=\s|\d|$)|"
            r"(?<!\S)ی\s*جا(?=\s|\d|$)|"
            r"(?<!\S)یجا(?=\s|\d|$)|تا\s*یجا(?=\s|\d|$)|"
            r"همه\s*(?:با\s*هم)?|"
            r"با\s*هم|باهم|کامل|بدون\s*تک|تک\s*نمی|"
            r"حداقل\s*\d+|بخشی|پله(?:ای)?|پول\s*جا\s*ب?جا\s*نشه"
        ),
    ),
    (
        "ITEM_QUALITY_PACKAGING",
        re.compile(
            r"وکیوم|کارتی|تمیز|تمبز|سالم|یک\s*دست|یکدست|پک(?!\S)"
        ),
    ),
    (
        "IMMEDIATE",
        re.compile(
            r"فوری|همین\s*الان|لحظه(?:ای)?|آنی|"
            rf"(?:فیش|حساب|پول){_PHRASE_GAP}(?:زود|سریع)"
        ),
    ),
    (
        "OTHER_EXPLICIT",
        re.compile(r"شرط|مشروط|توضیحات\s*[:：]"),
    ),
)

_MODEL_NUMBER_RE = re.compile(r"\d+(?::\d+)?")
_MODEL_SPACE_RE = re.compile(r"\s+")
_REVIEW_FORMAT_CONTROL_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)


@dataclass(frozen=True, slots=True)
class OfferConditionAxes:
    taxonomy_version: str
    has_condition: bool
    condition_families: tuple[str, ...]
    settlement_term: str
    trade_form: str
    market_session_phase: str
    deadline_clock_minute: int | None
    deadline_horizon_minutes: int | None
    deadline_horizon_bucket: str
    composite_class: str
    extraction_confidence: str
    condition_text: str
    offer_core_text: str
    condition_spans: tuple[tuple[int, int], ...]

    def to_dict(self, *, include_text: bool = False) -> dict[str, object]:
        payload = asdict(self)
        if not include_text:
            payload.pop("condition_text", None)
            payload.pop("offer_core_text", None)
            payload.pop("condition_spans", None)
        return payload


def normalize_offer_text(value: str) -> str:
    normalized = str(value or "").translate(_DIGITS).translate(_ARABIC_LETTERS)
    normalized = normalized.replace("\u200c", " ").replace("\u200f", " ")
    return _MODEL_SPACE_RE.sub(" ", normalized).strip()


def masked_condition_model_text(value: str) -> str:
    """Return a bounded, number-redacted string suitable for research models."""

    normalized = normalize_offer_text(value)[:512]
    return _MODEL_SPACE_RE.sub(" ", _MODEL_NUMBER_RE.sub("<NUM>", normalized)).strip()


def semantic_condition_alias_spans(
    text: str,
    phrase: str,
    *,
    allowed_families: Iterable[str] = (),
) -> tuple[tuple[int, int], ...]:
    """Resolve a canonical review phrase to high-precision raw abbreviations.

    The returned offsets always point into normalized source text.  This is
    deliberately a closed alias contract rather than fuzzy matching: owner
    review may write the semantic phrase ``شب حساب`` while the offer contains
    ``ش ح``, but no unrelated paraphrase is allowed to manufacture a span.
    """

    normalized_phrase = normalize_offer_text(
        _REVIEW_FORMAT_CONTROL_RE.sub("", str(phrase or ""))
    )
    compact_phrase = normalized_phrase.replace(" ", "")
    aliases: dict[str, tuple[str, re.Pattern[str]]] = {
        "تک حساب": ("PAYMENT_ACCOUNT", _SINGLE_ACCOUNT_RE),
        "تک خساب": ("PAYMENT_ACCOUNT", _SINGLE_ACCOUNT_RE),
        "شب حساب": ("SETTLEMENT_PROCESS", _NIGHT_ACCOUNT_RE),
        "شبخساب": ("SETTLEMENT_PROCESS", _NIGHT_ACCOUNT_RE),
        "حساب شب": ("SETTLEMENT_PROCESS", _NIGHT_ACCOUNT_RE),
        "حساب امشب": ("SETTLEMENT_PROCESS", _NIGHT_ACCOUNT_RE),
    }
    alias = aliases.get(normalized_phrase)
    compact_aliases = {
        "تکحساب": aliases["تک حساب"],
        "تکخساب": aliases["تک خساب"],
        "شبحساب": aliases["شب حساب"],
    }
    if alias is None:
        alias = compact_aliases.get(compact_phrase)
    if alias is None:
        return ()
    family, pattern = alias
    allowed = {str(item).strip().upper() for item in allowed_families}
    if allowed and family not in allowed:
        return ()
    normalized_text = normalize_offer_text(text)[:512]
    return tuple(match.span() for match in pattern.finditer(normalized_text))


def _merge_spans(spans: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    ordered = sorted({(max(0, int(a)), max(0, int(b))) for a, b in spans if b > a})
    merged: list[list[int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _strip_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end in _merge_spans(spans):
        pieces.append(text[cursor:start])
        pieces.append(" ")
        cursor = end
    pieces.append(text[cursor:])
    return _MODEL_SPACE_RE.sub(" ", "".join(pieces)).strip(" -،,:؛")


def _condition_text(text: str, spans: Iterable[tuple[int, int]]) -> str:
    fragments = [text[start:end].strip(" -،,:؛") for start, end in _merge_spans(spans)]
    return " | ".join(fragment for fragment in fragments if fragment)


def _tehran_time(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError("offer_condition_event_time_invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo("Asia/Tehran"))


def market_session_phase(
    event_time_utc: datetime | str,
    *,
    market_open_minute: int = DEFAULT_MARKET_OPEN_MINUTE,
    market_close_minute: int = DEFAULT_MARKET_CLOSE_MINUTE,
) -> str:
    local = _tehran_time(event_time_utc)
    if local.weekday() in {3, 4}:  # Thursday/Friday in the project market calendar.
        return "OFF_DAY"
    minute = local.hour * 60 + local.minute
    if minute < market_open_minute:
        return "PRE_OPEN"
    if minute < market_open_minute + 60:
        return "OPENING_FIRST_HOUR"
    if minute < market_open_minute + 180:
        return "MID_SESSION"
    if minute < market_close_minute:
        return "LATE_SESSION"
    return "AFTER_HOURS"


def _canonical_deadline_minute(match: re.Match[str]) -> int | None:
    hour_text = match.group("hour")
    hour_word = str(match.group("hour_word") or "")
    hour = int(hour_text) if hour_text is not None else _HOUR_WORDS.get(hour_word)
    if hour is None:
        return None
    minute = int(match.group("minute") or 0)
    daypart = str(match.group("daypart") or "")
    if hour > 23:
        return None
    if daypart in {"ظهر", "عصر", "شب"} and hour < 12:
        hour += 12
    elif not daypart and 1 <= hour <= 7:
        # Coin traders commonly write `فیش تا 2` for 14:00.  Preserve the
        # interpreted clock as a separate auditable feature.
        hour += 12
    if hour == 24:
        hour = 0
    return hour * 60 + minute


def _deadline_features(
    text: str,
    event_time_utc: datetime | str,
) -> tuple[int | None, int | None, str, tuple[tuple[int, int], ...]]:
    matches = list(_DEADLINE_RE.finditer(text))
    relative_matches = list(_RELATIVE_DEADLINE_RE.finditer(text))
    if not matches and not relative_matches:
        return None, None, "NO_DEADLINE", ()
    local = _tehran_time(event_time_utc)
    event_minute = local.hour * 60 + local.minute
    if relative_matches and (
        not matches or relative_matches[0].start() < matches[0].start()
    ):
        match = relative_matches[0]
        relative_text = match.group("relative_hour")
        relative_word = str(match.group("relative_word") or "")
        hours = (
            int(relative_text)
            if relative_text is not None
            else 1
            if relative_word == "یه"
            else _HOUR_WORDS.get(relative_word)
        )
        if hours is None or not 1 <= hours <= 8:
            return None, None, "AMBIGUOUS", (match.span(),)
        horizon = hours * 60
        deadline = (event_minute + horizon) % (24 * 60)
        bucket = "LE_60_MIN" if horizon <= 60 else "BETWEEN_61_AND_180_MIN" if horizon <= 180 else "GT_180_MIN"
        return deadline, horizon, bucket, (match.span(),)
    deadline = _canonical_deadline_minute(matches[0])
    if deadline is None:
        return None, None, "AMBIGUOUS", tuple(match.span() for match in matches)
    horizon = deadline - event_minute
    if horizon < 0:
        bucket = "PAST_OR_AMBIGUOUS"
    elif horizon <= 60:
        bucket = "LE_60_MIN"
    elif horizon <= 180:
        bucket = "BETWEEN_61_AND_180_MIN"
    else:
        bucket = "GT_180_MIN"
    return deadline, horizon, bucket, tuple(match.span() for match in matches)


def extract_offer_conditions(
    text: str,
    *,
    event_time_utc: datetime | str,
    settlement_term: str,
    trade_form: str,
    market_open_minute: int = DEFAULT_MARKET_OPEN_MINUTE,
    market_close_minute: int = DEFAULT_MARKET_CLOSE_MINUTE,
) -> OfferConditionAxes:
    normalized = normalize_offer_text(text)
    settlement = str(settlement_term or "UNKNOWN").strip().upper()
    form = str(trade_form or "UNKNOWN").strip().upper()
    families: list[str] = []
    spans: list[tuple[int, int]] = []
    for family, pattern in _FAMILY_PATTERNS:
        matches = list(pattern.finditer(normalized))
        if not matches:
            continue
        families.append(family)
        spans.extend(match.span() for match in matches)
    deadline, horizon, deadline_bucket, deadline_spans = _deadline_features(
        normalized,
        event_time_utc,
    )
    if deadline_spans:
        families.append("PAYMENT_DEADLINE")
    spans.extend(deadline_spans)
    merged_spans = _merge_spans(spans)
    ordered_families = tuple(family for family in CONDITION_FAMILIES if family in families)
    has_condition = bool(ordered_families)
    phase = market_session_phase(
        event_time_utc,
        market_open_minute=market_open_minute,
        market_close_minute=market_close_minute,
    )
    family_key = "+".join(ordered_families) if ordered_families else "UNCONDITIONAL"
    composite = "|".join(
        (
            f"SETTLEMENT={settlement}",
            f"FORM={form}",
            f"SESSION={phase}",
            f"FAMILY={family_key}",
            f"DEADLINE={deadline_bucket}",
        )
    )
    confidence = (
        "HIGH"
        if has_condition and any(family != "OTHER_EXPLICIT" for family in ordered_families)
        else "REVIEW_REQUIRED"
        if has_condition
        else "NO_EXPLICIT_CONDITION"
    )
    return OfferConditionAxes(
        taxonomy_version=CONDITION_TAXONOMY_VERSION,
        has_condition=has_condition,
        condition_families=ordered_families,
        settlement_term=settlement,
        trade_form=form,
        market_session_phase=phase,
        deadline_clock_minute=deadline,
        deadline_horizon_minutes=horizon,
        deadline_horizon_bucket=deadline_bucket,
        composite_class=composite,
        extraction_confidence=confidence,
        condition_text=_condition_text(normalized, merged_spans),
        offer_core_text=_strip_spans(normalized, merged_spans),
        condition_spans=merged_spans,
    )


__all__ = [
    "CONDITION_FAMILIES",
    "CONDITION_TAXONOMY_VERSION",
    "DEFAULT_MARKET_CLOSE_MINUTE",
    "DEFAULT_MARKET_OPEN_MINUTE",
    "OfferConditionAxes",
    "extract_offer_conditions",
    "market_session_phase",
    "masked_condition_model_text",
    "normalize_offer_text",
    "semantic_condition_alias_spans",
]
