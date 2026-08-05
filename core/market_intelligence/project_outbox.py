"""Transactional Offer/Trade -> market-intelligence outbox projection.

The SQLAlchemy hook only appends a small outbox row to the *same* transaction.
It never opens SQLite, calls a model, schedules a task, or can roll back a
business transaction after that transaction has committed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox
from models.offer import Offer, OfferStatus
from models.trade import Trade, TradeStatus, TradeType


_REGISTERED = False
_PENDING_KEY = "coin_intelligence_market_outbox_pending"
_SEEN_KEY = "coin_intelligence_market_outbox_seen"
OUTBOX_PAYLOAD_VERSION = 1


def _utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(timezone.utc)
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return candidate.replace(tzinfo=timezone.utc)
    return candidate.astimezone(timezone.utc)


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _settlement(value: Any) -> str:
    raw = _enum(value)
    return "TOMORROW" if raw == "tomorrow" else "CASH"


def _outbox_key(*parts: object) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _version(value: object) -> int:
    raw = getattr(value, "version_id", None)
    return int(raw) if isinstance(raw, int) and raw > 0 else 1


def _offer_event_kind(value: Offer, *, is_new: bool) -> str | None:
    if is_new:
        return "OFFER_OPENED"
    state = sa_inspect(value)
    status_history = state.attrs.status.history
    if status_history.has_changes():
        status = _enum(value.status)
        return {
            OfferStatus.COMPLETED.value: "OFFER_COMPLETED",
            OfferStatus.CANCELLED.value: "OFFER_CANCELLED",
            OfferStatus.EXPIRED.value: "OFFER_EXPIRED",
        }.get(status)
    if state.attrs.remaining_quantity.history.has_changes():
        if _enum(value.status) == OfferStatus.ACTIVE.value:
            return "OFFER_PARTIAL"
    return None


def _trade_became_completed(value: Trade, *, is_new: bool) -> bool:
    if _enum(value.status) != TradeStatus.COMPLETED.value:
        return False
    if is_new:
        return True
    history = sa_inspect(value).attrs.status.history
    return history.has_changes() and any(
        _enum(previous) != TradeStatus.COMPLETED.value
        for previous in history.deleted
    )


def _offer_payload(value: Offer) -> dict[str, object]:
    return {
        "version": OUTBOX_PAYLOAD_VERSION,
        "instrument": "PROJECT_COMMODITY",
        "commodity_id": int(value.commodity_id),
        "side": "BUY" if _enum(value.offer_type) == "buy" else "SELL",
        "settlement_term": _settlement(value.settlement_type),
        "trade_form": "PHYSICAL",
        "event_type": "OFFER",
        "price": int(value.price),
        "price_unit": "PROJECT_THOUSAND_TOMAN",
        "currency": "IRT",
        "quantity": int(value.quantity),
        "remaining_quantity": int(value.remaining_quantity or 0),
        "exclude_from_competitive_price": bool(value.exclude_from_competitive_price),
        "status": _enum(value.status).upper(),
    }


def _trade_payload(value: Trade) -> dict[str, object]:
    responder_side = _enum(value.trade_type)
    # TradeType is expressed from the responder's point of view.  Market side
    # is therefore its inverse and agrees with the originating offer's side.
    market_side = "SELL" if responder_side == TradeType.BUY.value else "BUY"
    return {
        "version": OUTBOX_PAYLOAD_VERSION,
        "instrument": "PROJECT_COMMODITY",
        "commodity_id": int(value.commodity_id),
        "side": market_side,
        "settlement_term": _settlement(value.settlement_type),
        "trade_form": "PHYSICAL",
        "event_type": "TRADE",
        "price": int(value.price),
        "price_unit": "PROJECT_THOUSAND_TOMAN",
        "currency": "IRT",
        "quantity": int(value.quantity),
        "status": _enum(value.status).upper(),
    }


def _occurred_at(value: object, event_kind: str) -> datetime:
    if event_kind == "TRADE_COMPLETED":
        return _utc(getattr(value, "completed_at", None) or getattr(value, "created_at", None))
    if event_kind == "OFFER_EXPIRED":
        return _utc(getattr(value, "expired_at", None) or getattr(value, "updated_at", None))
    return _utc(getattr(value, "updated_at", None) or getattr(value, "created_at", None))


def _append_outbox(
    session: Session,
    *,
    event_kind: str,
    subject_kind: str,
    subject_id: int,
    subject_version: int,
    occurred_at_utc: datetime,
    payload: dict[str, object],
) -> None:
    key = _outbox_key(
        "coin-intelligence-product-outbox-v1",
        subject_kind,
        subject_id,
        event_kind,
        subject_version,
    )
    seen = session.info.setdefault(_SEEN_KEY, set())
    if key in seen:
        return
    seen.add(key)
    session.add(
        CoinIntelligenceMarketOutbox(
            idempotency_key=key,
            event_kind=event_kind,
            subject_kind=subject_kind,
            subject_id=subject_id,
            occurred_at_utc=occurred_at_utc,
            payload=payload,
            status="PENDING",
            attempts=0,
            available_at_utc=occurred_at_utc,
            model_eligible=not bool(payload.get("exclude_from_competitive_price", False)),
        )
    )


def _capture_after_flush(session: Session, _context: object) -> None:
    """Append outbox rows while the originating transaction is still open."""

    if session.info.get(_PENDING_KEY):
        return
    session.info[_PENDING_KEY] = True
    try:
        new = set(session.new)
        for value in tuple(session.new) + tuple(session.dirty):
            if isinstance(value, Offer):
                event_kind = _offer_event_kind(value, is_new=value in new)
                if event_kind is None or value.id is None:
                    continue
                _append_outbox(
                    session,
                    event_kind=event_kind,
                    subject_kind="OFFER",
                    subject_id=int(value.id),
                    subject_version=_version(value),
                    occurred_at_utc=_occurred_at(value, event_kind),
                    payload=_offer_payload(value),
                )
            elif isinstance(value, Trade) and _trade_became_completed(
                value,
                is_new=value in new,
            ):
                if value.id is None:
                    continue
                _append_outbox(
                    session,
                    event_kind="TRADE_COMPLETED",
                    subject_kind="TRADE",
                    subject_id=int(value.id),
                    subject_version=_version(value),
                    occurred_at_utc=_occurred_at(value, "TRADE_COMPLETED"),
                    payload=_trade_payload(value),
                )
    finally:
        session.info.pop(_PENDING_KEY, None)


def _clear_session_state(session: Session, *_args: object) -> None:
    session.info.pop(_PENDING_KEY, None)
    session.info.pop(_SEEN_KEY, None)


def register_project_market_outbox_listeners() -> None:
    """Register once for sync sessions and AsyncSession's sync inner session."""

    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(Session, "after_flush", _capture_after_flush)
    event.listen(Session, "after_commit", _clear_session_state)
    event.listen(Session, "after_rollback", _clear_session_state)
    event.listen(Session, "after_soft_rollback", _clear_session_state)
    _REGISTERED = True
