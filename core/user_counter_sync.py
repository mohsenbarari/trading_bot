"""Transactional counter mutations and their dedicated sync event contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect as sa_inspect


USER_COUNTER_FIELDS = (
    "trades_count",
    "commodities_traded_count",
    "channel_messages_count",
)
USER_COUNTER_EPOCH_FIELD = "counter_epoch"
USER_COUNTER_SYNC_CONTRACT = "user_counter_event_v2"
USER_COUNTER_SYNC_CONTRACT_FIELD = "_sync_contract"
USER_COUNTER_EVENT_ID_FIELD = "_counter_event_id"
USER_COUNTER_EVENT_KIND_FIELD = "_counter_event_kind"
USER_COUNTER_EVENT_EPOCH_FIELD = "_counter_epoch"
USER_COUNTER_EVENT_DELTAS_FIELD = "_counter_deltas"
USER_COUNTER_EVENT_OCCURRED_AT_FIELD = "_counter_occurred_at"
USER_SYNC_IDENTITY_FIELD = "_sync_identity"
USER_SYNC_IDENTITY_FIELDS = ("account_name", "mobile_number", "telegram_id")


class InvalidUserCounterMutation(RuntimeError):
    """Raised when a counter write cannot be represented as a safe event."""


@dataclass(frozen=True)
class UserCounterEvent:
    event_id: str
    kind: str
    epoch: int
    deltas: dict[str, int]
    occurred_at: datetime


def normalize_counter_event_occurred_at(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("counter event occurred_at is invalid") from exc
    else:
        raise ValueError("counter event occurred_at is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("counter event occurred_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def user_counter_event_content_hash(
    *,
    source_server: str,
    event_id: object,
    kind: str,
    epoch: int,
    deltas: dict[str, int],
    occurred_at: object,
) -> str:
    canonical_time = normalize_counter_event_occurred_at(occurred_at).isoformat()
    encoded = json.dumps(
        {
            "source_server": str(source_server or "").strip().lower(),
            "event_id": str(event_id),
            "kind": str(kind),
            "epoch": int(epoch),
            "deltas": {str(key): int(value) for key, value in deltas.items()},
            "occurred_at": canonical_time,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def increment_user_counters(
    user: object,
    *,
    trades: int = 0,
    commodities: int = 0,
    channel_messages: int = 0,
) -> None:
    deltas = {
        "trades_count": int(trades),
        "commodities_traded_count": int(commodities),
        "channel_messages_count": int(channel_messages),
    }
    if any(value < 0 for value in deltas.values()):
        raise ValueError("counter increments must be non-negative")
    for field_name, delta in deltas.items():
        if delta:
            setattr(user, field_name, int(getattr(user, field_name, 0) or 0) + delta)


def reset_user_counters_in_memory(user: object) -> None:
    current_epoch = int(getattr(user, USER_COUNTER_EPOCH_FIELD, 1) or 1)
    setattr(user, USER_COUNTER_EPOCH_FIELD, current_epoch + 1)
    for field_name in USER_COUNTER_FIELDS:
        setattr(user, field_name, 0)


def build_user_sync_identity(user: object, *, include_previous: bool) -> dict[str, dict[str, Any]]:
    current = {
        field_name: getattr(user, field_name, None)
        for field_name in USER_SYNC_IDENTITY_FIELDS
        if getattr(user, field_name, None) is not None
    }
    previous: dict[str, Any] = {}
    if include_previous:
        try:
            state = sa_inspect(user)
            for field_name in USER_SYNC_IDENTITY_FIELDS:
                history = state.attrs[field_name].history
                if history.has_changes() and history.deleted:
                    previous[field_name] = history.deleted[0]
        except Exception:
            previous = {}
    return {"current": current, "previous": previous}


def _history_values(user: object, field_name: str) -> tuple[int, int, bool]:
    state = sa_inspect(user)
    history = state.attrs[field_name].history
    current = int(getattr(user, field_name, 0) or 0)
    if not history.has_changes():
        return current, current, False
    if not history.deleted:
        raise InvalidUserCounterMutation(f"counter history missing previous value: {field_name}")
    previous = int(history.deleted[0] or 0)
    return previous, current, True


def build_user_counter_event(
    user: object,
    changed_fields: set[str],
    *,
    occurred_at: datetime | None = None,
) -> UserCounterEvent | None:
    counter_changed = bool(set(USER_COUNTER_FIELDS) & changed_fields)
    epoch_changed = USER_COUNTER_EPOCH_FIELD in changed_fields
    if not counter_changed and not epoch_changed:
        return None

    previous_epoch, current_epoch, epoch_history_changed = _history_values(
        user,
        USER_COUNTER_EPOCH_FIELD,
    )
    if epoch_changed != epoch_history_changed:
        raise InvalidUserCounterMutation("counter epoch history is inconsistent")

    histories = {
        field_name: _history_values(user, field_name)
        for field_name in USER_COUNTER_FIELDS
    }
    event_time = normalize_counter_event_occurred_at(
        occurred_at or datetime.now(timezone.utc)
    )
    if epoch_changed:
        if current_epoch != previous_epoch + 1:
            raise InvalidUserCounterMutation("counter reset must increment epoch exactly once")
        if any(current != 0 for _, current, _ in histories.values()):
            raise InvalidUserCounterMutation("counter reset must zero every counter")
        return UserCounterEvent(
            event_id=str(uuid4()),
            kind="reset",
            epoch=current_epoch,
            deltas={},
            occurred_at=event_time,
        )

    deltas = {
        field_name: current - previous
        for field_name, (previous, current, changed) in histories.items()
        if changed
    }
    if any(delta < 0 for delta in deltas.values()):
        raise InvalidUserCounterMutation("counter decrease requires an epoch reset")
    deltas = {field_name: delta for field_name, delta in deltas.items() if delta > 0}
    if not deltas:
        return None
    return UserCounterEvent(
        event_id=str(uuid4()),
        kind="increment",
        epoch=current_epoch,
        deltas=deltas,
        occurred_at=event_time,
    )


def user_counter_event_payload(user: object, event: UserCounterEvent) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        USER_COUNTER_SYNC_CONTRACT_FIELD: USER_COUNTER_SYNC_CONTRACT,
        USER_COUNTER_EVENT_ID_FIELD: event.event_id,
        USER_COUNTER_EVENT_KIND_FIELD: event.kind,
        USER_COUNTER_EVENT_EPOCH_FIELD: event.epoch,
        USER_COUNTER_EVENT_DELTAS_FIELD: event.deltas,
        USER_COUNTER_EVENT_OCCURRED_AT_FIELD: event.occurred_at.isoformat(),
        USER_SYNC_IDENTITY_FIELD: build_user_sync_identity(user, include_previous=True),
    }


def is_user_counter_event_payload(data: object) -> bool:
    return bool(
        isinstance(data, dict)
        and data.get(USER_COUNTER_SYNC_CONTRACT_FIELD) == USER_COUNTER_SYNC_CONTRACT
    )
