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


CONDITION_TAXONOMY_VERSION = "coin-offer-condition-taxonomy-v1"
DEFAULT_MARKET_OPEN_MINUTE = 10 * 60
DEFAULT_MARKET_CLOSE_MINUTE = 15 * 60

CONDITION_FAMILIES = (
    "PAYMENT_DEADLINE",
    "PAYMENT_RAIL",
    "SETTLEMENT_PROCESS",
    "CREDIT_CHEQUE",
    "DELIVERY_HANDOFF",
    "IDENTITY_ACCOUNT",
    "QUANTITY_EXECUTION",
    "IMMEDIATE",
    "OTHER_EXPLICIT",
)

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})

_CLOCK = r"(?P<hour>[0-2]?\d)(?::(?P<minute>[0-5]\d))?"
_DAYPART = r"(?P<daypart>صبح|ظهر|عصر|شب)?"
_DEADLINE_RE = re.compile(
    rf"(?:فیش|واریز|پرداخت|تسویه|مهلت)\s*(?:تا|ساعت)?\s*{_CLOCK}\s*{_DAYPART}"
)

_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PAYMENT_DEADLINE",
        _DEADLINE_RE,
    ),
    (
        "PAYMENT_RAIL",
        re.compile(r"ساتنا|پایا|شبا|کارت\s*به\s*کارت|حواله|واریز"),
    ),
    (
        "SETTLEMENT_PROCESS",
        re.compile(
            r"تسویه|شب\s*ح(?:ساب)?|ش\s*ح(?:ساب)?|حساب\s*(?:شب|آخر\s*وقت)|"
            r"پای\s*حساب|تراز"
        ),
    ),
    (
        "CREDIT_CHEQUE",
        re.compile(r"چک|اعتبار|اعتباری|مدت\s*دار|سفته"),
    ),
    (
        "DELIVERY_HANDOFF",
        re.compile(r"تحویل|ارسال|باربری|درب\s*(?:مغازه|محل)|حضوری"),
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
            r"یکجا|همه\s*(?:با\s*هم)?|کامل|بدون\s*تک|تک\s*نمی|"
            r"حداقل\s*\d+|بخشی|پله(?:ای)?"
        ),
    ),
    (
        "IMMEDIATE",
        re.compile(r"فوری|همین\s*الان|لحظه(?:ای)?|آنی"),
    ),
    (
        "OTHER_EXPLICIT",
        re.compile(r"شرط|مشروط|توضیحات\s*[:：]"),
    ),
)

_MODEL_NUMBER_RE = re.compile(r"\d+(?::\d+)?")
_MODEL_SPACE_RE = re.compile(r"\s+")


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
    hour = int(match.group("hour"))
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
    if not matches:
        return None, None, "NO_DEADLINE", ()
    deadline = _canonical_deadline_minute(matches[0])
    if deadline is None:
        return None, None, "AMBIGUOUS", tuple(match.span() for match in matches)
    local = _tehran_time(event_time_utc)
    event_minute = local.hour * 60 + local.minute
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
]
