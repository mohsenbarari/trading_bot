"""Fail-closed ownership policy for legacy and queue Telegram executors."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.config import settings
from core.server_routing import SERVER_FOREIGN, current_server


LEGACY_TELEGRAM_EXECUTION_OWNER = "legacy"
QUEUE_V1_TELEGRAM_EXECUTION_OWNER = "queue-v1"
PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER = "producer-only"
DEPLOYABLE_TELEGRAM_SERVICES = frozenset(
    {"api", "bot", "sync_worker", "load_runner", "webapp", "migration"}
)
TELEGRAM_PROVIDER_BOT_SERVICE = "bot"

# The integrated queue/DR reconciliation migration, least-privilege Bot grants,
# strict local-table guard, provider identity preflight, limiter, freshness
# validators, and rollback contract are now present in this release. Runtime
# activation still requires all three explicit operator controls below; this
# code capability alone can never switch ownership.
TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = True


class TelegramDeliveryRuntimeMode(str, Enum):
    LEGACY = LEGACY_TELEGRAM_EXECUTION_OWNER
    QUEUE_V1 = QUEUE_V1_TELEGRAM_EXECUTION_OWNER
    PRODUCER_ONLY = PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER


class TelegramDeliveryRuntimeConfigurationError(RuntimeError):
    """Raised before task creation when ownership controls are inconsistent."""


class TelegramProviderAuthorityError(RuntimeError):
    """Raised when a process attempts Telegram provider work without authority."""


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

    if owner == PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER:
        if queue_worker_enabled or cutover_ready:
            raise TelegramDeliveryRuntimeConfigurationError(
                "producer_only_rejects_execution_enablement"
            )
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.PRODUCER_ONLY,
            legacy_workers_enabled=False,
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
        owner = str(
            getattr(
                settings,
                "telegram_delivery_execution_owner",
                LEGACY_TELEGRAM_EXECUTION_OWNER,
            )
            or ""
        ).strip().lower()
        configured_mode = (
            QUEUE_V1_TELEGRAM_EXECUTION_OWNER
            if owner == PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER
            else owner
        )
    return resolve_telegram_delivery_producer_mode(producer_mode=configured_mode)


def configured_telegram_service_name() -> str:
    return str(getattr(settings, "trading_bot_service", "") or "").strip().lower()


def _explicit_test_provider_authority() -> bool:
    if not bool(getattr(settings, "telegram_provider_test_authority", False)):
        return False
    service = configured_telegram_service_name()
    if service in DEPLOYABLE_TELEGRAM_SERVICES:
        raise TelegramProviderAuthorityError(
            "telegram_provider_test_authority_forbidden_on_deployable_service"
        )
    return True


def assert_telegram_provider_execution_authority() -> None:
    """Fail closed unless this process is the authorized Telegram executor.

    Queue-v1 provider calls are allowed only on the foreign bot.  Legacy
    rollback keeps the foreign Legacy executor (bot, or the historical
    foreign API relay) but never authorizes Iran, Sync, WebApp, or
    producer-only API processes.
    """

    if _explicit_test_provider_authority():
        return
    if current_server() != SERVER_FOREIGN:
        raise TelegramProviderAuthorityError("telegram_provider_forbidden_outside_foreign")

    service = configured_telegram_service_name()
    producer = configured_telegram_delivery_producer_mode()
    owner = str(
        getattr(settings, "telegram_delivery_execution_owner", LEGACY_TELEGRAM_EXECUTION_OWNER)
        or ""
    ).strip().lower()

    if owner == PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER or (
        producer == TelegramDeliveryRuntimeMode.QUEUE_V1
        and service != TELEGRAM_PROVIDER_BOT_SERVICE
    ):
        raise TelegramProviderAuthorityError("producer_only_forbidden_provider_execution")

    if producer == TelegramDeliveryRuntimeMode.QUEUE_V1:
        if service != TELEGRAM_PROVIDER_BOT_SERVICE:
            raise TelegramProviderAuthorityError("queue_v1_provider_requires_foreign_bot")
        runtime = configured_telegram_delivery_runtime()
        if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1 or not runtime.queue_worker_enabled:
            raise TelegramProviderAuthorityError("queue_v1_provider_requires_queue_executor")
        return

    if owner != LEGACY_TELEGRAM_EXECUTION_OWNER:
        raise TelegramProviderAuthorityError("legacy_provider_requires_legacy_owner")
    if service not in {TELEGRAM_PROVIDER_BOT_SERVICE, "api", "app"}:
        raise TelegramProviderAuthorityError("legacy_provider_requires_foreign_executor")
    if service == TELEGRAM_PROVIDER_BOT_SERVICE:
        runtime = configured_telegram_delivery_runtime()
        if runtime.mode != TelegramDeliveryRuntimeMode.LEGACY or not runtime.legacy_workers_enabled:
            raise TelegramProviderAuthorityError("legacy_bot_provider_requires_legacy_executor")


def assert_legacy_bot_membership_authority() -> None:
    """Direct ban/unban helpers stay on the Legacy bot, never Queue-v1 API."""

    if _explicit_test_provider_authority():
        return
    assert_telegram_provider_execution_authority()
    if configured_telegram_delivery_producer_mode() == TelegramDeliveryRuntimeMode.QUEUE_V1:
        raise TelegramProviderAuthorityError(
            "queue_v1_membership_requires_saga_not_direct_helper"
        )
    if configured_telegram_service_name() != TELEGRAM_PROVIDER_BOT_SERVICE:
        raise TelegramProviderAuthorityError("legacy_membership_requires_foreign_bot")


def assert_queue_v1_non_bot_is_producer_only() -> None:
    """API/sync/load processes may produce Queue-v1 work but never execute it."""

    if configured_telegram_delivery_producer_mode() != TelegramDeliveryRuntimeMode.QUEUE_V1:
        return
    service = configured_telegram_service_name()
    if service == TELEGRAM_PROVIDER_BOT_SERVICE:
        return
    if service not in DEPLOYABLE_TELEGRAM_SERVICES and service not in {"app", ""}:
        return
    owner = str(
        getattr(settings, "telegram_delivery_execution_owner", LEGACY_TELEGRAM_EXECUTION_OWNER)
        or ""
    ).strip().lower()
    if owner != PRODUCER_ONLY_TELEGRAM_EXECUTION_OWNER:
        raise TelegramDeliveryRuntimeConfigurationError(
            "queue_v1_non_bot_requires_producer_only"
        )
