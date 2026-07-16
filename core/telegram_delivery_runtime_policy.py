"""Fail-closed ownership policy for legacy and queue Telegram executors."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.config import settings


LEGACY_TELEGRAM_EXECUTION_OWNER = "legacy"
QUEUE_V1_TELEGRAM_EXECUTION_OWNER = "queue-v1"

# Stage 3 foundation is deliberately not cutover-capable yet. This constant is
# code-owned so an accidental environment variable cannot activate an executor
# before limiter, domain revalidation, and producer handoff are complete.
TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = False


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
