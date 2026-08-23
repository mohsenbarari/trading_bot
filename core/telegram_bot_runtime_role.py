"""Fail-closed ownership of Telegram bot runtime surfaces.

One process may still run everything (`all`). Split runtime uses `primary`
for central interaction and `executor` for the single Queue-v1 owner plus
worker-bot polling. The retired `publishers` name is rejected so two
services cannot claim the same contract.
"""
from __future__ import annotations

from collections.abc import Sequence

from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


TELEGRAM_BOT_RUNTIME_ROLE_ALL = "all"
TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY = "primary"
TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR = "executor"
TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS = "publishers"
TELEGRAM_BOT_RUNTIME_ROLES = frozenset(
    {
        TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
        TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    }
)
TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES = frozenset({"primary"})
TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES = frozenset(
    TELEGRAM_PUBLISHER_IDENTITIES
)
TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES = TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES
TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES = (
    TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES
)


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
        raw = getattr(
            settings_obj,
            "telegram_bot_runtime_role",
            TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        )
    value = str(raw or "").strip().lower()
    if value == TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS:
        raise TelegramBotRuntimeRoleError(
            "telegram_bot_runtime_role_publishers_retired_use_executor"
        )
    if value not in TELEGRAM_BOT_RUNTIME_ROLES:
        raise TelegramBotRuntimeRoleError("telegram_bot_runtime_role_unknown")
    return value


def telegram_bot_split_enabled(settings_obj=None) -> bool:
    return bool(getattr(settings_obj, "telegram_bot_split_enabled", False))


def assert_telegram_bot_runtime_role_plans_are_disjoint() -> None:
    overlap = (
        TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES
        & TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES
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
        TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    }


def role_owns_queue_executor(role: str) -> bool:
    return resolve_telegram_bot_runtime_role(role=role) in {
        TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    }


def role_owns_otp_worker(role: str) -> bool:
    return role_owns_queue_executor(role)


def role_owns_local_ack_surface(role: str) -> bool:
    return role_owns_primary_surface(role)


def select_polling_bot_identities(
    role: str,
    available: Sequence[str],
) -> tuple[str, ...]:
    resolved = resolve_telegram_bot_runtime_role(role=role)
    identities = tuple(str(item) for item in available)
    if resolved == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY:
        owned = TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES
    elif resolved == TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
        owned = TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES
    else:
        owned = (
            TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES
            | TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES
        )
    return tuple(item for item in identities if item in owned)


def select_queue_execution_bot_identities(
    role: str,
    available: Sequence[str],
) -> tuple[str, ...]:
    if not role_owns_queue_executor(role):
        return ()
    return tuple(str(item) for item in available)


def select_owned_bot_identities(
    role: str,
    available: Sequence[str],
) -> tuple[str, ...]:
    """Return this role's queue-execution identities."""
    return select_queue_execution_bot_identities(role, available)


def assert_telegram_bot_runtime_role_compatible(
    *,
    settings_obj,
    runtime,
    role: str | None = None,
) -> str:
    """Refuse a role that would start the wrong workers or violate split topology."""
    resolved = resolve_telegram_bot_runtime_role(settings_obj, role=role)
    assert_telegram_bot_runtime_role_plans_are_disjoint()
    split_enabled = telegram_bot_split_enabled(settings_obj)
    mode = getattr(getattr(runtime, "mode", None), "value", getattr(runtime, "mode", None))
    normalized_mode = str(mode or "").strip().lower().replace("-", "_")
    if split_enabled and resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        raise TelegramBotRuntimeRoleError("telegram_bot_split_rejects_combined_all")
    if not split_enabled and resolved != TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        raise TelegramBotRuntimeRoleError("telegram_bot_split_required_for_role")
    if normalized_mode == "legacy" and resolved != TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        raise TelegramBotRuntimeRoleError("telegram_bot_legacy_runtime_requires_all")
    if resolved != TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
        return resolved
    if normalized_mode not in {"queue_v1", "queuev1"}:
        raise TelegramBotRuntimeRoleError(
            "telegram_bot_runtime_executor_requires_queue_v1"
        )
    if not (
        bool(getattr(settings_obj, "telegram_multi_publisher_enabled", False))
        and bool(getattr(settings_obj, "telegram_b2b_dispatch_enabled", False))
    ):
        raise TelegramBotRuntimeRoleError("telegram_bot_runtime_executor_requires_b2b")
    return resolved
