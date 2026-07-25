"""Fail-closed ownership policy for legacy and queue Telegram executors."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.config import settings


LEGACY_TELEGRAM_EXECUTION_OWNER = "legacy"
QUEUE_V1_TELEGRAM_EXECUTION_OWNER = "queue-v1"

# The integrated queue/DR reconciliation migration, least-privilege Bot grants,
# strict local-table guard, provider identity preflight, limiter, freshness
# validators, and rollback contract are now present in this release. Runtime
# activation still requires all three explicit operator controls below; this
# code capability alone can never switch ownership.
TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = True


class TelegramDeliveryRuntimeMode(str, Enum):
    LEGACY = LEGACY_TELEGRAM_EXECUTION_OWNER
    QUEUE_V1 = QUEUE_V1_TELEGRAM_EXECUTION_OWNER


class TelegramDeliveryRuntimeConfigurationError(RuntimeError):
    """Raised before task creation when ownership controls are inconsistent."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryRuntimeDecision:
    mode: TelegramDeliveryRuntimeMode
    legacy_workers_enabled: bool
    queue_worker_enabled: bool


def resolve_telegram_delivery_runtime(
    *,
    execution_owner: str,
    queue_worker_enabled: bool,
    cutover_ready: bool,
    implementation_ready: bool = TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY,
) -> TelegramDeliveryRuntimeDecision:
    owner = str(execution_owner or "").strip().lower()
    if owner == LEGACY_TELEGRAM_EXECUTION_OWNER:
        if queue_worker_enabled or cutover_ready:
            raise TelegramDeliveryRuntimeConfigurationError(
                "legacy_owner_rejects_queue_enablement"
            )
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            legacy_workers_enabled=True,
            queue_worker_enabled=False,
        )

    if owner != QUEUE_V1_TELEGRAM_EXECUTION_OWNER:
        raise TelegramDeliveryRuntimeConfigurationError("unknown_telegram_execution_owner")
    if not queue_worker_enabled:
        raise TelegramDeliveryRuntimeConfigurationError("queue_owner_requires_worker_enabled")
    if not cutover_ready:
        raise TelegramDeliveryRuntimeConfigurationError("queue_owner_requires_cutover_ready")
    if not implementation_ready:
        raise TelegramDeliveryRuntimeConfigurationError("queue_implementation_not_cutover_ready")
    return TelegramDeliveryRuntimeDecision(
        mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
        legacy_workers_enabled=False,
        queue_worker_enabled=True,
    )


def configured_telegram_delivery_runtime() -> TelegramDeliveryRuntimeDecision:
    return resolve_telegram_delivery_runtime(
        execution_owner=getattr(settings, "telegram_delivery_execution_owner", "legacy"),
        queue_worker_enabled=bool(
            getattr(settings, "telegram_delivery_queue_worker_enabled", False)
        ),
        cutover_ready=bool(
            getattr(settings, "telegram_delivery_queue_cutover_ready", False)
        ),
    )


def resolve_telegram_delivery_producer_mode(
    *,
    producer_mode: str,
    implementation_ready: bool = TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY,
) -> TelegramDeliveryRuntimeMode:
    """Resolve the non-secret producer contract without granting execution.

    API processes only need to know whether a business effect must be persisted
    for Queue-v1.  They must never receive worker enablement or provider
    credentials merely to make that decision.
    """

    mode = str(producer_mode or "").strip().lower()
    if mode == LEGACY_TELEGRAM_EXECUTION_OWNER:
        return TelegramDeliveryRuntimeMode.LEGACY
    if mode != QUEUE_V1_TELEGRAM_EXECUTION_OWNER:
        raise TelegramDeliveryRuntimeConfigurationError(
            "unknown_telegram_delivery_producer_mode"
        )
    if not implementation_ready:
        raise TelegramDeliveryRuntimeConfigurationError(
            "queue_implementation_not_cutover_ready"
        )
    return TelegramDeliveryRuntimeMode.QUEUE_V1


def configured_telegram_delivery_producer_mode() -> TelegramDeliveryRuntimeMode:
    configured_mode = getattr(settings, "telegram_delivery_producer_mode", None)
    if configured_mode is None or not str(configured_mode).strip():
        configured_mode = getattr(
            settings,
            "telegram_delivery_execution_owner",
            LEGACY_TELEGRAM_EXECUTION_OWNER,
        )
    return resolve_telegram_delivery_producer_mode(producer_mode=configured_mode)


def assert_telegram_provider_execution_authority() -> None:
    """Prevent tokenless three-site APIs from claiming or sending Telegram work."""

    if not bool(getattr(settings, "three_site_dr_enabled", False)):
        # Preserve the current two-server legacy deployment until its own
        # separately reviewed migration moves provider ownership to Bot-only.
        return
    service = str(getattr(settings, "trading_bot_service", "") or "").strip().lower()
    if service != "bot":
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_provider_execution_requires_bot_service"
        )
    from core.runtime_identity import resolve_runtime_identity

    if not resolve_runtime_identity(settings).is_bot_site:
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_provider_execution_requires_bot_fi_site"
        )
