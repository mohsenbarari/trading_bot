"""Fail-closed Telegram split-runtime topology and health reporting."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
    TelegramBotRuntimeRoleError,
    resolve_telegram_bot_runtime_role,
    role_owns_local_ack_surface,
    role_owns_otp_worker,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    role_owns_queue_executor,
    select_polling_bot_identities,
    select_queue_execution_bot_identities,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES = (
    "primary",
    "channel_editor",
    *TELEGRAM_PUBLISHER_IDENTITIES,
)


@dataclass(frozen=True, slots=True)
class TelegramBotRuntimeTopologyReport:
    role: str
    split_enabled: bool
    polling_identities: tuple[str, ...]
    queue_execution_identities: tuple[str, ...]
    owns_central_polling: bool
    owns_publisher_polling: bool
    owns_queue_executor: bool
    owns_otp_worker: bool
    owns_local_ack: bool
    queue_owner_present: bool | None
    can_start: bool
    topology_complete: bool
    healthy: bool
    promotable: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["polling_identities"] = list(self.polling_identities)
        payload["queue_execution_identities"] = list(self.queue_execution_identities)
        payload["reasons"] = list(self.reasons)
        return payload


def describe_telegram_bot_runtime_topology(
    *,
    role: str,
    split_enabled: bool,
    available_identities: tuple[str, ...] = TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES,
    queue_owner_present: bool | None = None,
) -> TelegramBotRuntimeTopologyReport:
    resolved = resolve_telegram_bot_runtime_role(role=role)
    reasons: list[str] = []
    can_start = True
    if split_enabled and resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        can_start = False
        reasons.append("telegram_bot_split_rejects_combined_all")
    if not split_enabled and resolved != TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        can_start = False
        reasons.append("telegram_bot_split_required_for_role")

    polling = select_polling_bot_identities(resolved, available_identities)
    execution = select_queue_execution_bot_identities(resolved, available_identities)
    owns_queue = role_owns_queue_executor(resolved)
    topology_complete = can_start
    if (
        resolved == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
        and split_enabled
        and queue_owner_present is not True
    ):
        topology_complete = False
        reasons.append("telegram_bot_split_executor_missing")
    if resolved == TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR and queue_owner_present is False:
        topology_complete = False
        reasons.append("telegram_bot_split_queue_owner_missing")

    healthy = bool(can_start and topology_complete)
    return TelegramBotRuntimeTopologyReport(
        role=resolved,
        split_enabled=bool(split_enabled),
        polling_identities=polling,
        queue_execution_identities=execution,
        owns_central_polling=role_owns_primary_surface(resolved),
        owns_publisher_polling=role_owns_publisher_surface(resolved),
        owns_queue_executor=owns_queue,
        owns_otp_worker=role_owns_otp_worker(resolved),
        owns_local_ack=role_owns_local_ack_surface(resolved),
        queue_owner_present=queue_owner_present,
        can_start=can_start,
        topology_complete=topology_complete,
        healthy=healthy,
        promotable=healthy,
        reasons=tuple(reasons),
    )


def assert_telegram_bot_deploy_topology(
    *,
    split_enabled: bool,
    bot_role: str,
    executor_enabled: bool,
) -> TelegramBotRuntimeTopologyReport:
    resolved = resolve_telegram_bot_runtime_role(role=bot_role)
    if executor_enabled and resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        raise TelegramBotRuntimeRoleError("telegram_bot_all_plus_executor_forbidden")
    if executor_enabled and not split_enabled:
        raise TelegramBotRuntimeRoleError("telegram_bot_executor_requires_split")
    if split_enabled and resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
        raise TelegramBotRuntimeRoleError("telegram_bot_split_rejects_combined_all")
    if (
        not split_enabled
        and resolved != TELEGRAM_BOT_RUNTIME_ROLE_ALL
        and not executor_enabled
    ):
        raise TelegramBotRuntimeRoleError("telegram_bot_split_required_for_role")
    return describe_telegram_bot_runtime_topology(
        role=resolved,
        split_enabled=split_enabled,
        queue_owner_present=True if executor_enabled or resolved == TELEGRAM_BOT_RUNTIME_ROLE_ALL else False,
    )
