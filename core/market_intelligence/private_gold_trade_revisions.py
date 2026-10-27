"""Causal trade extraction from revisions of the private melted-gold feed.

The source keeps an offer open for two minutes.  During that interval an edit
may introduce or reduce an explicit remaining-quantity field.  That is trade
evidence; a generic edit is not.  A positive closure marker is explicit
no-trade/closed evidence and must not be interpreted as a fill.

This module is pure and transport-free.  Callers retain raw revisions only in
their bounded staging store and persist the returned economic decision without
raw text or Telegram identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Iterable

from .market_contracts import normalize_utc
from .private_gold import PrivateGoldOfferInput, ParsedPrivateGoldOffer, parse_private_gold_offer


PRIVATE_GOLD_TRADE_REVISION_VERSION = "private-gold-trade-revisions-v1"
PRIVATE_GOLD_OFFER_LIFETIME_SECONDS = 120

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_REMAINING_MARKER = re.compile(r"باقی|مانده|موند|مقدار")
_REMAINING_QUANTITY = (
    re.compile(r"(?:باقی|مانده|موند|مقدار)\D{0,16}(\d{1,4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,4})\D{0,16}(?:باقی|مانده|موند)"),
)
_NO_TRADE_CLOSURE = re.compile(r"[✅✔☑]")


@dataclass(frozen=True, slots=True)
class PrivateGoldRevision:
    event_id: str
    event_type: str
    published_at_utc: datetime | str
    available_at_utc: datetime | str
    text: str
    edited_at_utc: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class PrivateGoldTradeDecision:
    status: str
    reason: str
    finalized: bool
    published_at_utc: str
    finalize_after_utc: str
    event_time_utc: str | None = None
    available_at_utc: str | None = None
    traded_quantity: int | None = None
    remaining_quantity: int | None = None
    offer_quantity: int | None = None
    evidence_event_id: str | None = None


def _dt(value: datetime | str, *, field: str) -> datetime:
    normalized = normalize_utc(value, field_name=field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _remaining_quantity(text: str) -> int | None:
    normalized = str(text or "").translate(_DIGITS)
    for pattern in _REMAINING_QUANTITY:
        match = pattern.search(normalized)
        if match is None:
            continue
        quantity = int(match.group(1))
        if 0 <= quantity <= 10_000:
            return quantity
    return None


def _has_remaining_marker(text: str) -> bool:
    return bool(_REMAINING_MARKER.search(str(text or "")))


def _has_no_trade_closure(text: str) -> bool:
    return bool(_NO_TRADE_CLOSURE.search(str(text or "")))


def _parse(revision: PrivateGoldRevision) -> ParsedPrivateGoldOffer | None:
    return parse_private_gold_offer(
        PrivateGoldOfferInput(
            source_event_id="private-gold-revision",
            published_at_utc=revision.published_at_utc,
            available_at_utc=revision.available_at_utc,
            text=revision.text,
            trade_status="NONE",
        )
    )


def _same_book(baseline: ParsedPrivateGoldOffer, current: ParsedPrivateGoldOffer) -> bool:
    return (
        current.price_toman == baseline.price_toman
        and current.quantity == baseline.quantity
        and current.side == baseline.side
        and current.settlement_term == baseline.settlement_term
        and current.trade_form == baseline.trade_form
    )


def extract_private_gold_trade(
    revisions: Iterable[PrivateGoldRevision],
    *,
    as_of_utc: datetime | str,
) -> PrivateGoldTradeDecision:
    """Return one final cumulative trade decision or a fail-closed state.

    Partial evidence is finalized only after the 120-second offer window, so a
    later in-window correction can override it.  Remaining quantity zero and
    explicit no-trade closure are terminal and may finalize immediately.
    """

    ordered = sorted(
        tuple(revisions),
        key=lambda item: (
            _dt(item.edited_at_utc or item.published_at_utc, field="private_gold_revision_time"),
            _dt(item.available_at_utc, field="private_gold_revision_available"),
            item.event_id,
        ),
    )
    if not ordered:
        raise ValueError("private_gold_revisions_required")
    as_of = _dt(as_of_utc, field="private_gold_trade_as_of")
    visible = [
        item
        for item in ordered
        if _dt(item.available_at_utc, field="private_gold_revision_available") <= as_of
    ]
    if not visible:
        raise ValueError("private_gold_visible_revision_required")
    baseline_revision = visible[0]
    published = _dt(
        baseline_revision.published_at_utc,
        field="private_gold_revision_published",
    )
    deadline = published + timedelta(seconds=PRIVATE_GOLD_OFFER_LIFETIME_SECONDS)
    baseline = _parse(baseline_revision)
    pending = PrivateGoldTradeDecision(
        status="PENDING",
        reason="OFFER_WINDOW_OPEN",
        finalized=False,
        published_at_utc=_iso(published),
        finalize_after_utc=_iso(deadline),
        offer_quantity=(baseline.quantity if baseline is not None else None),
    )
    if baseline is None:
        return PrivateGoldTradeDecision(
            status="AMBIGUOUS",
            reason="BASELINE_UNRESOLVED",
            finalized=as_of >= deadline,
            published_at_utc=_iso(published),
            finalize_after_utc=_iso(deadline),
        )
    previous = baseline_revision
    previous_remaining = _remaining_quantity(previous.text)
    candidate: PrivateGoldTradeDecision | None = None
    ambiguous = False
    for revision in visible[1:]:
        if revision.event_type != "message_edited" or revision.edited_at_utc is None:
            previous = revision
            previous_remaining = _remaining_quantity(previous.text)
            continue
        edited = _dt(revision.edited_at_utc, field="private_gold_revision_edited")
        if edited < published or edited > deadline:
            continue
        available = _dt(revision.available_at_utc, field="private_gold_revision_available")
        current = _parse(revision)
        remaining = _remaining_quantity(revision.text)
        marker_added = _has_remaining_marker(revision.text) and not _has_remaining_marker(
            previous.text
        )
        remaining_decreased = (
            remaining is not None
            and previous_remaining is not None
            and remaining < previous_remaining
        )
        if _has_no_trade_closure(revision.text) and remaining is None:
            return PrivateGoldTradeDecision(
                status="NONE",
                reason="EXPLICIT_NO_TRADE_CLOSURE",
                finalized=True,
                published_at_utc=_iso(published),
                finalize_after_utc=_iso(deadline),
                event_time_utc=_iso(edited),
                available_at_utc=_iso(max(edited, available)),
                offer_quantity=baseline.quantity,
                evidence_event_id=revision.event_id,
            )
        if remaining is not None and (marker_added or remaining_decreased):
            if current is None or not _same_book(baseline, current):
                ambiguous = True
                candidate = None
            elif not 0 <= remaining < baseline.quantity:
                ambiguous = True
                candidate = None
            else:
                candidate = PrivateGoldTradeDecision(
                    status="FULL" if remaining == 0 else "PARTIAL",
                    reason="EXPLICIT_REMAINING_DELTA",
                    finalized=remaining == 0,
                    published_at_utc=_iso(published),
                    finalize_after_utc=_iso(deadline),
                    event_time_utc=_iso(edited),
                    available_at_utc=_iso(max(edited, available, deadline if remaining else edited)),
                    traded_quantity=baseline.quantity - remaining,
                    remaining_quantity=remaining,
                    offer_quantity=baseline.quantity,
                    evidence_event_id=revision.event_id,
                )
                ambiguous = False
                if remaining == 0:
                    return candidate
        elif remaining is not None and previous_remaining is not None and remaining > previous_remaining:
            ambiguous = True
            candidate = None
        previous = revision
        previous_remaining = remaining
    if as_of < deadline:
        return pending
    if candidate is not None:
        return PrivateGoldTradeDecision(
            status=candidate.status,
            reason=candidate.reason,
            finalized=True,
            published_at_utc=candidate.published_at_utc,
            finalize_after_utc=candidate.finalize_after_utc,
            event_time_utc=candidate.event_time_utc,
            available_at_utc=_iso(max(as_of, deadline)),
            traded_quantity=candidate.traded_quantity,
            remaining_quantity=candidate.remaining_quantity,
            offer_quantity=candidate.offer_quantity,
            evidence_event_id=candidate.evidence_event_id,
        )
    if ambiguous:
        return PrivateGoldTradeDecision(
            status="AMBIGUOUS",
            reason="REVISION_SEQUENCE_AMBIGUOUS",
            finalized=True,
            published_at_utc=_iso(published),
            finalize_after_utc=_iso(deadline),
            offer_quantity=baseline.quantity,
        )
    return PrivateGoldTradeDecision(
        status="NONE",
        reason="WINDOW_CLOSED_WITHOUT_TRADE_EVIDENCE",
        finalized=True,
        published_at_utc=_iso(published),
        finalize_after_utc=_iso(deadline),
        offer_quantity=baseline.quantity,
    )


__all__ = [
    "PRIVATE_GOLD_OFFER_LIFETIME_SECONDS",
    "PRIVATE_GOLD_TRADE_REVISION_VERSION",
    "PrivateGoldRevision",
    "PrivateGoldTradeDecision",
    "extract_private_gold_trade",
]
