"""Pure state snapshots for M5 account-control Telegram notices."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


ACCOUNT_NOTICE_KIND_STATUS = "account_status"
ACCOUNT_NOTICE_KIND_RESTRICTION_ACTIVE = "restriction_active"
ACCOUNT_NOTICE_KIND_DELETED = "account_deleted"

RESTRICTION_KIND_BLOCK = "block"
RESTRICTION_KIND_LIMITATIONS = "limitations"
RESTRICTION_KINDS = frozenset(
    {RESTRICTION_KIND_BLOCK, RESTRICTION_KIND_LIMITATIONS}
)

_LIMITATION_FIELDS = (
    "max_daily_trades",
    "max_active_commodities",
    "max_daily_requests",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_datetime(value: Any, *, field: str) -> str:
    if not isinstance(value, datetime):
        raise ValueError(f"account_notice_{field}_invalid")
    return _utc(value).isoformat()


def _canonical_optional_datetime(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _canonical_datetime(value, field=field)


def _optional_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"account_notice_{field}_invalid")
    return value


def normalize_restriction_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in RESTRICTION_KINDS:
        raise ValueError("account_notice_restriction_kind_invalid")
    return normalized


def build_active_restriction_snapshot(
    user: Any,
    *,
    restriction_kind: str,
) -> dict[str, Any]:
    kind = normalize_restriction_kind(restriction_kind)
    if kind == RESTRICTION_KIND_BLOCK:
        return {
            "trading_restricted_until": _canonical_datetime(
                getattr(user, "trading_restricted_until", None),
                field="trading_restricted_until",
            )
        }

    snapshot = {
        field: _optional_nonnegative_int(
            getattr(user, field, None),
            field=field,
        )
        for field in _LIMITATION_FIELDS
    }
    if not any(value is not None for value in snapshot.values()):
        raise ValueError("account_notice_limitations_empty")
    snapshot["limitations_expire_at"] = _canonical_optional_datetime(
        getattr(user, "limitations_expire_at", None),
        field="limitations_expire_at",
    )
    return snapshot


def validate_active_restriction_snapshot(
    value: Any,
    *,
    restriction_kind: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("account_notice_restriction_snapshot_invalid")
    kind = normalize_restriction_kind(restriction_kind)
    if kind == RESTRICTION_KIND_BLOCK:
        if set(value) != {"trading_restricted_until"}:
            raise ValueError("account_notice_block_snapshot_invalid")
        return {
            "trading_restricted_until": _canonical_datetime(
                datetime.fromisoformat(
                    str(value["trading_restricted_until"]).replace("Z", "+00:00")
                ),
                field="trading_restricted_until",
            )
        }

    expected_fields = {*_LIMITATION_FIELDS, "limitations_expire_at"}
    if set(value) != expected_fields:
        raise ValueError("account_notice_limitations_snapshot_invalid")
    snapshot = {
        field: _optional_nonnegative_int(value.get(field), field=field)
        for field in _LIMITATION_FIELDS
    }
    if not any(item is not None for item in snapshot.values()):
        raise ValueError("account_notice_limitations_empty")
    raw_expiry = value.get("limitations_expire_at")
    if raw_expiry is None:
        snapshot["limitations_expire_at"] = None
    else:
        try:
            parsed_expiry = datetime.fromisoformat(
                str(raw_expiry).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("account_notice_limitations_expire_at_invalid") from exc
        snapshot["limitations_expire_at"] = _canonical_datetime(
            parsed_expiry,
            field="limitations_expire_at",
        )
    return snapshot


def active_restriction_snapshot_matches_user(
    snapshot: Mapping[str, Any],
    user: Any,
    *,
    restriction_kind: str,
    now: datetime | None = None,
) -> bool:
    try:
        kind = normalize_restriction_kind(restriction_kind)
        matches = validate_active_restriction_snapshot(
            snapshot,
            restriction_kind=kind,
        ) == build_active_restriction_snapshot(
            user,
            restriction_kind=kind,
        )
        if not matches:
            return False
        current_time = _utc(now or datetime.now(timezone.utc))
        if kind == RESTRICTION_KIND_BLOCK:
            restricted_until = getattr(user, "trading_restricted_until", None)
            return isinstance(restricted_until, datetime) and _utc(
                restricted_until
            ) > current_time
        expires_at = getattr(user, "limitations_expire_at", None)
        return expires_at is None or (
            isinstance(expires_at, datetime)
            and _utc(expires_at) > current_time
        )
    except (TypeError, ValueError):
        return False


def build_deleted_account_snapshot(user: Any) -> dict[str, str]:
    if not bool(getattr(user, "is_deleted", False)):
        raise ValueError("account_notice_user_not_deleted")
    if getattr(user, "telegram_id", None) is not None:
        raise ValueError("account_notice_deleted_route_not_cleared")
    return {
        "deleted_at": _canonical_datetime(
            getattr(user, "deleted_at", None),
            field="deleted_at",
        )
    }


def validate_deleted_account_snapshot(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"deleted_at"}:
        raise ValueError("account_notice_deleted_snapshot_invalid")
    try:
        parsed = datetime.fromisoformat(
            str(value["deleted_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("account_notice_deleted_at_invalid") from exc
    return {
        "deleted_at": _canonical_datetime(parsed, field="deleted_at")
    }


def deleted_account_snapshot_matches_user(
    snapshot: Mapping[str, Any],
    user: Any,
) -> bool:
    try:
        return validate_deleted_account_snapshot(snapshot) == (
            build_deleted_account_snapshot(user)
        )
    except (TypeError, ValueError):
        return False
