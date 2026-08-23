"""Fail-closed ownership of Telegram bot runtime surfaces.

Central chat and publisher channel work can share one process (`all`) or run
as two processes (`primary` and `publishers`). Unknown roles refuse startup.
The two split roles own disjoint bot identities so a misconfigured pair cannot
honestly claim the same lane.
"""
from __future__ import annotations

from collections.abc import Sequence

from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


TELEGRAM_BOT_RUNTIME_ROLE_ALL = "all"
TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY = "primary"
TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS = "publishers"
TELEGRAM_BOT_RUNTIME_ROLES = frozenset(
    {
        TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
        TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS,
    }
)
TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES = frozenset({"primary", "channel_editor"})
TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES = frozenset(TELEGRAM_PUBLISHER_IDENTITIES)


class TelegramBotRuntimeRoleError(RuntimeError):
    """Raised when the bot process cannot take a safe runtime role."""


def resolve_telegram_bot_runtime_role(
    settings_obj=None,
    *,
    role: str | None = None,
) -> str:
    if role is not None:
        raw = role
    else:
        raw = getattr(settings_obj, "telegram_bot_runtime_role", TELEGRAM_BOT_RUNTIME_ROLE_ALL)
    value = str(raw or "").strip().lower()
    if value not in TELEGRAM_BOT_RUNTIME_ROLES:
        raise TelegramBotRuntimeRoleError("telegram_bot_runtime_role_unknown")
    return value


def assert_telegram_bot_runtime_role_plans_are_disjoint() -> None:
    overlap = (
        TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES
        & TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES
    )
    if overlap:
        raise TelegramBotRuntimeRoleError("telegram_bot_runtime_role_identity_overlap")


def role_owns_primary_surface(role: str) -> bool:
    return resolve_telegram_bot_runtime_role(role=role) in {
        TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
    }


def role_owns_publisher_surface(role: str) -> bool:
    return resolve_telegram_bot_runtime_role(role=role) in {
        TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS,
    }


def select_owned_bot_identities(
    role: str,
    available: Sequence[str],
) -> tuple[str, ...]:
    """Return this role's lanes from the already-composed identity set."""
    resolved = resolve_telegram_bot_runtime_role(role=role)
    identities = tuple(str(item) for item in available)
    if resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        return identities
    owned = (
        TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES
        if resolved == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
        else TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES
    )
    return tuple(item for item in identities if item in owned)


def assert_telegram_bot_runtime_role_compatible(
    *,
    settings_obj,
    runtime,
    role: str | None = None,
) -> str:
    """Refuse a split role that would start with the wrong workers or no lanes."""
    resolved = resolve_telegram_bot_runtime_role(settings_obj, role=role)
    assert_telegram_bot_runtime_role_plans_are_disjoint()
    if resolved != TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS:
        return resolved
    mode = getattr(getattr(runtime, "mode", None), "value", getattr(runtime, "mode", None))
    if str(mode or "").strip().lower() not in {"queue_v1", "queue-v1"}:
        raise TelegramBotRuntimeRoleError(
            "telegram_bot_runtime_publishers_require_queue_v1"
        )
    if not (
        bool(getattr(settings_obj, "telegram_multi_publisher_enabled", False))
        and bool(getattr(settings_obj, "telegram_b2b_dispatch_enabled", False))
    ):
        raise TelegramBotRuntimeRoleError(
            "telegram_bot_runtime_publishers_require_b2b"
        )
    return resolved
