"""Post-commit project-event adapter for local Shadow evaluation.

The listener captures only opaque local row identities while the transaction
is in progress.  Model execution and Shadow persistence happen after commit in
an independent task, so they cannot alter or roll back a business transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from core.config import settings
from models.offer import Offer
from models.trade import Trade, TradeStatus


_REGISTERED = False
_PENDING_KEY = "_coin_intelligence_shadow_project_events"
_SEEN_KEY = "_coin_intelligence_shadow_project_event_keys"


@dataclass(frozen=True, slots=True)
class CommittedProjectMarketEvent:
    kind: str
    local_id: int
    occurred_at_utc: datetime | None
    observed_after_commit_at_utc: datetime | None = None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _trade_became_completed(value: Trade) -> bool:
    if _enum_value(value.status) != TradeStatus.COMPLETED.value:
        return False
    state = sa_inspect(value)
    history = state.attrs.status.history
    is_new = state.session is not None and value in state.session.new
    return is_new or (
        history.has_changes()
        and any(
            _enum_value(item) != TradeStatus.COMPLETED.value
            for item in history.deleted
        )
    )


def _capture_after_flush(session: Session, _context: Any) -> None:
    if not (
        settings.coin_intelligence_shadow_enabled
        and settings.coin_intelligence_shadow_project_events_enabled
    ):
        return
    pending = session.info.setdefault(_PENDING_KEY, [])
    seen = session.info.setdefault(_SEEN_KEY, set())
    for value in tuple(session.new) + tuple(session.dirty):
        if isinstance(value, Offer) and value in session.new:
            kind = "OFFER"
            occurred_at = value.created_at
        elif isinstance(value, Trade) and _trade_became_completed(value):
            kind = "TRADE"
            occurred_at = value.completed_at or value.created_at
        else:
            continue
        if value.id is None:
            continue
        key = (kind, int(value.id))
        if key in seen:
            continue
        seen.add(key)
        pending.append(
            CommittedProjectMarketEvent(
                kind=kind,
                local_id=int(value.id),
                occurred_at_utc=occurred_at,
            )
        )


def _clear(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
    session.info.pop(_SEEN_KEY, None)


def _dispatch_after_commit(session: Session) -> None:
    pending = tuple(session.info.get(_PENDING_KEY) or ())
    _clear(session)
    if not pending:
        return
    # Import lazily to keep ORM registration independent from model loading.
    from core.market_intelligence.shadow import schedule_project_market_event

    observed_after_commit_at = datetime.now(timezone.utc)
    for item in pending:
        schedule_project_market_event(
            replace(
                item,
                observed_after_commit_at_utc=observed_after_commit_at,
            )
        )


def register_project_market_event_listeners() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(Session, "after_flush", _capture_after_flush)
    event.listen(Session, "after_commit", _dispatch_after_commit)
    event.listen(Session, "after_rollback", _clear)
    event.listen(Session, "after_soft_rollback", lambda session, _: _clear(session))
    _REGISTERED = True
