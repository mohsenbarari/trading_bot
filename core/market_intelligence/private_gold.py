"""Pure normalization for the private melted-gold offer feed.

The transport/staging layer may temporarily retain a message and its private
identifier for deduplication and edit reconciliation.  This module accepts that
transient input but emits only opaque-key, privacy-minimized Market Store facts.
It never starts a Telegram client or persists the input text itself.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
import re
import sqlite3
from statistics import median
from typing import Iterable, Mapping, Sequence

from .market_contracts import MarketObservation, derive_event_key, normalize_utc
from .market_store import upsert_observation


PRIVATE_GOLD_PARSER_VERSION = "private-gold-rules-v3-description-safe-price"
PRIVATE_GOLD_SOURCE_CODE = "PRIVATE_GOLD_CHANNEL"
PRIVATE_GOLD_MINUTE_SOURCE_CODE = "PRIVATE_GOLD_PAPER_MINUTE"
PRIVATE_GOLD_TRADE_WEIGHT = Decimal("3")
PRIVATE_GOLD_OFFER_WEIGHT = Decimal("1")
PRIVATE_GOLD_CONDITIONAL_REFERENCE_SECONDS = 10 * 60
PRIVATE_GOLD_CONDITIONAL_MIN_REFERENCE_COUNT = 2
PRIVATE_GOLD_CONDITIONAL_MIN_TOLERANCE_RELATIVE = 0.0045
PRIVATE_GOLD_CONDITIONAL_MAX_TOLERANCE_RELATIVE = 0.0125

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PRICE = re.compile(r"(?<!\d)(\d{2,3}(?:[,.]\d{3})+|\d{7,9})(?!\d)")
_QUANTITY = re.compile(r"(?<!\d)(\d{1,4})\s*(?:تا\b|عدد\b|تا\s*عدد)")
_DESCRIPTION = re.compile(r"(?:توضیحات?|شرایط?)\s*[:：]\s*(.+)", re.S)
_CONDITIONAL_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ONE_PAYMENT_SLIP", re.compile(r"یک\s*فقره|تک\s*فقره")),
    (
        "PAYMENT_DEADLINE",
        re.compile(
            r"(?:فیش|واریز|تسویه|چک).{0,32}(?:تا\s*(?:ساعت|[0-2]?\d\s*[:.]?\s*[0-5]\d)|مهلت)"
            r"|(?:تا\s*(?:ساعت|[0-2]?\d\s*[:.]?\s*[0-5]\d)|مهلت).{0,32}(?:فیش|واریز|تسویه|چک)"
        ),
    ),
    (
        "REQUIRED_PAYMENT",
        re.compile(
            r"(?:فقط|حتما|حتماً|باید|مشروط(?:\s+به)?|به\s*شرط|در\s*صورت).{0,32}(?:فیش|واریز|تسویه|چک)"
            r"|(?:فیش|واریز|تسویه|چک).{0,32}(?:فقط|حتما|حتماً|باید|الزامی)"
        ),
    ),
    ("EXCHANGE_CONDITION", re.compile(r"تعویض")),
    ("EXPLICIT_CONDITION", re.compile(r"شرط")),
)
_AMBIGUOUS_PAYMENT_NOTE = re.compile(r"فیش|واریز|تسویه|چک")


@dataclass(frozen=True, slots=True)
class PrivateGoldOfferInput:
    """Transient, transport-bound offer data; do not store this object.

    ``source_event_id`` and ``text`` are used only to derive opaque keys and
    economic fields. ``edited_at_utc`` is only the time of an independently
    verified trade; a generic edit is not transaction evidence.
    """

    source_event_id: str | int
    published_at_utc: datetime | str
    text: str
    available_at_utc: datetime | str
    edited_at_utc: datetime | str | None = None
    trade_detected_at_utc: datetime | str | None = None
    trade_status: str | None = None
    traded_quantity: int | None = None
    declared_side: str | None = None
    declared_quantity: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedPrivateGoldOffer:
    """No-raw-text result of parsing one private-gold source event."""

    price_toman: int
    quantity: int
    side: str
    settlement_term: str
    trade_form: str
    paper_variant: str | None
    is_conditional: bool
    conditional_reason: str | None
    condition_class: str
    has_description: bool
    trade_event_time_utc: str | None
    trade_quantity: int | None
    parse_confidence: float


def _normalized_text(value: str) -> str:
    return str(value or "").translate(_DIGITS).replace("\u200c", " ").replace("\u200f", " ")


def _strict_utc(value: datetime | str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return normalize_utc(value, field_name=field_name)


def _positive_int(value: object) -> int | None:
    try:
        normalized = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _side(text: str, declared_side: str | None) -> str | None:
    explicit = str(declared_side or "").strip().upper()
    if explicit in {"BUY", "SELL"}:
        return explicit
    if re.search(r"فروش|(?<!\w)ف(?!\w)", text):
        return "SELL"
    if re.search(r"خرید|(?<!\w)خ(?!\w)", text):
        return "BUY"
    return None


def _price_toman(text: str) -> int | None:
    # The verified channel template places the quoted unit price in the offer
    # header.  Free-form descriptions can contain totals, account balances, or
    # payment amounts that are not prices.  Never let those later numbers
    # override the headline quote.
    description = _DESCRIPTION.search(text)
    price_region = text[: description.start()] if description is not None else text
    values: list[int] = []
    for candidate in _PRICE.findall(price_region):
        try:
            parsed = int(re.sub(r"\D", "", candidate))
        except ValueError:
            continue
        # This feed expresses a mesghal price in Toman.  Small quantities and
        # clock values must never become prices.
        if 1_000_000 <= parsed <= 1_000_000_000:
            values.append(parsed)
    return values[0] if values else None


def _quantity(text: str, declared_quantity: int | None) -> int | None:
    declared = _positive_int(declared_quantity)
    if declared is not None:
        return declared
    match = _QUANTITY.search(text)
    return _positive_int(match.group(1)) if match else None


def _market_dimensions(text: str) -> tuple[str, str, str | None] | None:
    """Return form/settlement/variant without guessing physical from silence."""

    has_havale = bool(re.search(r"با\s*حواله", text))
    physical_today = bool(re.search(r"نقد\s*حاضر", text))
    physical_tomorrow = bool(re.search(r"بی\s*حواله|بدون\s*حواله", text))
    today = bool(re.search(r"روز", text))
    tomorrow = bool(re.search(r"فردا", text))
    if has_havale:
        return (
            "PAPER_" + _paper_variant(text),
            "TODAY" if today else "TOMORROW",
            _paper_variant(text),
        )
    if physical_today:
        return "PHYSICAL", "TODAY", None
    if physical_tomorrow:
        return "PHYSICAL", "TOMORROW", None
    # The verified source convention marks a bare "روز" as paper/today.  It
    # is intentionally not extended to unmarked text.
    if today:
        return "PAPER_" + _paper_variant(text), "TODAY", _paper_variant(text)
    if tomorrow:
        return "PAPER_" + _paper_variant(text), "TOMORROW", _paper_variant(text)
    return None


def _paper_variant(text: str) -> str:
    if "معکوس" in text:
        return "REVERSE"
    if "شنا" in text:
        return "SWIM"
    return "NORMAL"


def _condition_classification(text: str) -> tuple[str, str | None, bool]:
    """Classify economic conditions without retaining free-form text.

    A description field is common in physical offers and is not itself a
    condition. Clear payment/deadline/explicit-condition phrases are gated;
    a bare payment note is conservatively gated as ambiguous. Ordinary notes
    remain normal market observations.
    """

    description = _DESCRIPTION.search(text)
    has_description = bool(description and description.group(1).strip())
    for reason, marker in _CONDITIONAL_MARKERS:
        if marker.search(text):
            return "CONFIRMED", reason, has_description
    if has_description and _AMBIGUOUS_PAYMENT_NOTE.search(description.group(1)):
        return "AMBIGUOUS", "AMBIGUOUS_PAYMENT_NOTE", True
    return ("NON_CONDITIONAL_NOTE" if has_description else "NONE"), None, has_description


def _row_datetime(row: Mapping[str, object], key: str) -> datetime | None:
    try:
        value = row[key]
    except (IndexError, KeyError):
        return None
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _row_price(row: Mapping[str, object]) -> float | None:
    try:
        value = row["price_num"]
    except (IndexError, KeyError):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def filter_comparable_private_gold_physical_rows(
    rows: Sequence[sqlite3.Row],
) -> list[sqlite3.Row]:
    """Keep normal physical facts and only market-comparable gated facts.

    The caller must already limit rows to one physical book and one point in
    time. A condition is comparable only against at least two *earlier*,
    non-conditional facts known no later than that condition. This prevents
    both special settlement terms and future observations from moving a live
    price. The adaptive tolerance is bounded so a volatile minute is not a
    blanket permission for arbitrary conditional prices.
    """

    ordered = sorted(
        rows,
        key=lambda row: (
            _row_datetime(row, "event_time_utc") or datetime.min.replace(tzinfo=timezone.utc),
            int(row["id"]),
        ),
    )
    accepted: list[sqlite3.Row] = []
    normal_reference: list[sqlite3.Row] = []
    for row in ordered:
        price = _row_price(row)
        event_time = _row_datetime(row, "event_time_utc")
        available_at = _row_datetime(row, "available_at_utc")
        if price is None or event_time is None or available_at is None:
            continue
        if not bool(row["is_conditional"]):
            accepted.append(row)
            normal_reference.append(row)
            continue
        references = [
            item
            for item in normal_reference
            if (
                (reference_time := _row_datetime(item, "event_time_utc")) is not None
                and (reference_available := _row_datetime(item, "available_at_utc")) is not None
                and reference_time <= event_time
                and reference_available <= available_at
                and (event_time - reference_time).total_seconds()
                <= PRIVATE_GOLD_CONDITIONAL_REFERENCE_SECONDS
            )
        ]
        reference_prices = [
            candidate for item in references if (candidate := _row_price(item)) is not None
        ]
        if len(reference_prices) < PRIVATE_GOLD_CONDITIONAL_MIN_REFERENCE_COUNT:
            continue
        center = float(median(reference_prices))
        if center <= 0:
            continue
        mad = float(median([abs(candidate - center) for candidate in reference_prices]))
        tolerance = min(
            PRIVATE_GOLD_CONDITIONAL_MAX_TOLERANCE_RELATIVE,
            max(
                PRIVATE_GOLD_CONDITIONAL_MIN_TOLERANCE_RELATIVE,
                4.0 * mad / center,
            ),
        )
        if abs(price - center) / center <= tolerance:
            accepted.append(row)
    return sorted(
        accepted,
        key=lambda row: (
            _row_datetime(row, "event_time_utc") or datetime.min.replace(tzinfo=timezone.utc),
            int(row["id"]),
        ),
        reverse=True,
    )


def _trade_time_and_quantity(
    source: PrivateGoldOfferInput,
    *,
    quantity: int,
) -> tuple[str | None, int | None]:
    status = str(source.trade_status or "").strip().upper()
    # An explicit verifier result of no trade is authoritative.
    if status in {"NONE", "NO_TRADE"}:
        return None, None
    if status not in {"FULL", "PARTIAL", "COMPLETED", "TRADED"}:
        return None, None
    # The edit time wins only after an explicit verifier status; generic edits
    # include closure and correction events.
    trade_time = _strict_utc(source.edited_at_utc, field_name="private_gold_edited_at_utc")
    if trade_time is None:
        trade_time = _strict_utc(
            source.trade_detected_at_utc,
            field_name="private_gold_trade_detected_at_utc",
        )
    if trade_time is None:
        return None, None
    traded_quantity = _positive_int(source.traded_quantity)
    if traded_quantity is not None:
        return trade_time, min(traded_quantity, quantity)
    if status == "PARTIAL":
        # A partial confirmation without a quantity is preserved as offer
        # evidence only; inventing a full trade would overweight the price.
        return None, None
    if status in {"FULL", "COMPLETED", "TRADED"}:
        return trade_time, quantity
    return None, None


def parse_private_gold_offer(source: PrivateGoldOfferInput) -> ParsedPrivateGoldOffer | None:
    """Return a fully specified market event or abstain without a guess."""

    text = _normalized_text(source.text)
    dimensions = _market_dimensions(text)
    price_toman = _price_toman(text)
    quantity = _quantity(text, source.declared_quantity)
    side = _side(text, source.declared_side)
    if dimensions is None or price_toman is None or quantity is None or side is None:
        return None
    trade_form, settlement_term, paper_variant = dimensions
    condition_class, conditional_reason, has_description = _condition_classification(text)
    trade_time, trade_quantity = _trade_time_and_quantity(source, quantity=quantity)
    return ParsedPrivateGoldOffer(
        price_toman=price_toman,
        quantity=quantity,
        side=side,
        settlement_term=settlement_term,
        trade_form=trade_form,
        paper_variant=paper_variant,
        is_conditional=condition_class in {"CONFIRMED", "AMBIGUOUS"},
        conditional_reason=conditional_reason,
        condition_class=condition_class,
        has_description=has_description,
        trade_event_time_utc=trade_time,
        trade_quantity=trade_quantity,
        parse_confidence=(
            0.94
            if condition_class == "CONFIRMED"
            else 0.96 if condition_class == "AMBIGUOUS" else 0.98
        ),
    )


def _label(parsed: ParsedPrivateGoldOffer) -> str:
    if parsed.trade_form == "PHYSICAL":
        return "PRIVATE_GOLD_PHYSICAL"
    return "PRIVATE_GOLD_PAPER_" + str(parsed.paper_variant)


def _event_key(source_event_id: str | int, role: str) -> bytes:
    return derive_event_key("private-gold-event-v1", source_event_id, role)


def private_gold_observations(source: PrivateGoldOfferInput) -> list[MarketObservation]:
    """Create raw economic offer/trade facts, never a raw text record."""

    parsed = parse_private_gold_offer(source)
    if parsed is None:
        return []
    published_at = _strict_utc(source.published_at_utc, field_name="private_gold_published_at_utc")
    available_at = _strict_utc(source.available_at_utc, field_name="private_gold_available_at_utc")
    assert published_at is not None and available_at is not None
    price_store = Decimal(parsed.price_toman)
    root_offer_event_key = _event_key(source.source_event_id, "OFFER")
    attributes: dict[str, object] = {
        "paper_variant": parsed.paper_variant or "NOT_APPLICABLE",
        "conditional_reason": parsed.conditional_reason or "NONE",
        "condition_class": parsed.condition_class,
        "has_description": parsed.has_description,
        "requires_market_comparability": parsed.is_conditional,
        "root_offer_event_key": root_offer_event_key.hex(),
    }
    observations = [
        MarketObservation(
            event_key=root_offer_event_key,
            source_code=PRIVATE_GOLD_SOURCE_CODE,
            source_family="TELEGRAM_PRIVATE",
            event_time_utc=published_at,
            available_at_utc=available_at,
            instrument="MELTED_GOLD_PRIVATE",
            market_label=_label(parsed),
            settlement_term=parsed.settlement_term,
            trade_form=parsed.trade_form,
            event_type="OFFER",
            side=parsed.side,
            price=price_store,
            price_unit="TOMAN_PER_MESGHAL_750",
            currency="TOMAN",
            quantity=parsed.quantity,
            quantity_unit="LOT_COUNT",
            parse_confidence=parsed.parse_confidence,
            parser_version=PRIVATE_GOLD_PARSER_VERSION,
            quality_state="ELIGIBLE",
            quality_policy_version="private-gold-v1",
            is_conditional=parsed.is_conditional,
            attributes=attributes,
        )
    ]
    if parsed.trade_event_time_utc is not None and parsed.trade_quantity is not None:
        trade_available = max(
            _strict_utc(parsed.trade_event_time_utc, field_name="private_gold_trade_time") or published_at,
            available_at,
        )
        observations.append(
            MarketObservation(
                event_key=_event_key(source.source_event_id, "TRADE"),
                source_code=PRIVATE_GOLD_SOURCE_CODE,
                source_family="TELEGRAM_PRIVATE",
                event_time_utc=parsed.trade_event_time_utc,
                available_at_utc=trade_available,
                instrument="MELTED_GOLD_PRIVATE",
                market_label=_label(parsed),
                settlement_term=parsed.settlement_term,
                trade_form=parsed.trade_form,
                event_type="TRADE",
                side=parsed.side,
                price=price_store,
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
                quantity=parsed.trade_quantity,
                quantity_unit="LOT_COUNT",
                parse_confidence=0.99 if source.edited_at_utc else 0.96,
                parser_version=PRIVATE_GOLD_PARSER_VERSION,
                quality_state="ELIGIBLE",
                quality_policy_version="private-gold-v1",
                is_conditional=parsed.is_conditional,
                attributes=attributes,
            )
        )
    return observations


def ingest_private_gold_offer(
    connection: sqlite3.Connection,
    source: PrivateGoldOfferInput,
) -> list[int]:
    """Upsert one parsed offer and optional confirmed trade into Market Store."""

    return [upsert_observation(connection, item) for item in private_gold_observations(source)]


def _minute_end(value: datetime) -> datetime:
    return value.replace(second=59, microsecond=0)


def refresh_private_gold_paper_minute(
    connection: sqlite3.Connection,
    *,
    settlement_term: str,
    paper_variant: str,
    minute_utc: datetime | str,
    available_at_utc: datetime | str,
) -> int | None:
    """Materialize one weighted paper-price minute from raw private facts.

    Physical offers are intentionally never passed through this function.  A
    confirmed paper trade gets weight 3 and an offer weight 1.  The aggregate
    is a derived quote and carries no input IDs or raw text.
    """

    minute_start = _strict_utc(minute_utc, field_name="private_gold_minute_utc")
    assert minute_start is not None
    key = (
        str(settlement_term).upper(),
        str(paper_variant).upper(),
        minute_start[:16] + ":00Z",
    )
    return refresh_private_gold_paper_minutes(
        connection,
        minute_books=(key,),
        available_at_utc=available_at_utc,
    ).get(key)


def refresh_private_gold_paper_minutes(
    connection: sqlite3.Connection,
    *,
    minute_books: Iterable[tuple[str, str, datetime | str]],
    available_at_utc: datetime | str,
) -> dict[tuple[str, str, str], int]:
    """Materialize many paper minutes with one bounded source-fact read."""

    available_at = _strict_utc(
        available_at_utc,
        field_name="private_gold_minutes_available_at_utc",
    )
    assert available_at is not None
    available = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
    requested: set[tuple[str, str, str]] = set()
    starts: list[datetime] = []
    for settlement_term, paper_variant, minute_utc in minute_books:
        minute_start = _strict_utc(
            minute_utc,
            field_name="private_gold_minute_utc",
        )
        assert minute_start is not None
        start = datetime.fromisoformat(minute_start.replace("Z", "+00:00")).replace(
            second=0,
            microsecond=0,
        )
        if available < _minute_end(start):
            raise ValueError("private_gold_minute_not_closed")
        requested.add(
            (
                str(settlement_term).upper(),
                str(paper_variant).upper(),
                normalize_utc(start, field_name="private_gold_minute_start"),
            )
        )
        starts.append(start)
    if not requested:
        return {}
    earliest = min(starts)
    latest = _minute_end(max(starts))
    rows = connection.execute(
        """
        SELECT price_num,event_type,settlement_term,trade_form,event_time_utc
        FROM market_observations
        WHERE source_code = ?
          AND instrument = 'MELTED_GOLD_PRIVATE'
          AND trade_form IN ('PAPER_NORMAL','PAPER_REVERSE','PAPER_SWIM')
          AND event_type IN ('OFFER', 'TRADE')
          AND quality_state = 'ELIGIBLE'
          AND is_conditional = 0
          AND event_time_utc >= ?
          AND event_time_utc <= ?
          AND available_at_utc <= ?
        """,
        (
            PRIVATE_GOLD_SOURCE_CODE,
            normalize_utc(earliest, field_name="private_gold_minutes_start"),
            normalize_utc(latest, field_name="private_gold_minutes_end"),
            available_at,
        ),
    ).fetchall()
    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = (
            str(row["settlement_term"]),
            str(row["trade_form"]).removeprefix("PAPER_"),
            str(row["event_time_utc"])[:16] + ":00Z",
        )
        if key in requested:
            grouped[key].append(row)
    written: dict[tuple[str, str, str], int] = {}
    for key in sorted(requested):
        matching = grouped.get(key, [])
        if not matching:
            continue
        settlement_term, paper_variant, minute_start = key
        start = datetime.fromisoformat(minute_start.replace("Z", "+00:00"))
        event_time = normalize_utc(
            _minute_end(start),
            field_name="private_gold_minute_event_time",
        )
        weighted_total = Decimal("0")
        weights = Decimal("0")
        trade_count = 0
        for row in matching:
            weight = (
                PRIVATE_GOLD_TRADE_WEIGHT
                if row["event_type"] == "TRADE"
                else PRIVATE_GOLD_OFFER_WEIGHT
            )
            weighted_total += Decimal(str(row["price_num"])) * weight
            weights += weight
            trade_count += int(row["event_type"] == "TRADE")
        observation = MarketObservation(
            event_key=derive_event_key(
                "private-gold-paper-minute-v1",
                event_time,
                settlement_term,
                paper_variant,
            ),
            source_code=PRIVATE_GOLD_MINUTE_SOURCE_CODE,
            source_family="TELEGRAM_PRIVATE",
            event_time_utc=event_time,
            available_at_utc=available_at,
            instrument="MELTED_GOLD_PRIVATE",
            market_label="PRIVATE_GOLD_PAPER_" + paper_variant,
            settlement_term=settlement_term,
            trade_form="PAPER_" + paper_variant,
            event_type="QUOTE",
            side="MID",
            price=weighted_total / weights,
            price_unit="TOMAN_PER_MESGHAL_750",
            currency="TOMAN",
            parse_confidence=1.0,
            parser_version="private-gold-minute-v1",
            quality_state="ELIGIBLE",
            quality_policy_version="private-gold-minute-v1",
            attributes={
                "derived_quote": True,
                "input_count": len(matching),
                "trade_count": trade_count,
                "trade_weight": int(PRIVATE_GOLD_TRADE_WEIGHT),
            },
        )
        written[key] = upsert_observation(connection, observation)
    return written
