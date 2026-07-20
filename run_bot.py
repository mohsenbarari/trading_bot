import asyncio
import logging
import sys
import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.server_routing import SERVER_FOREIGN, normalize_server
from bot.handlers import (
    start, 
    panel, 
    trade_create, 
    trade_execute, 
    trade_manage,
    trade_history,
    admin,
    admin_broadcast,
    admin_commodities,
    admin_users,
    commodity_catalog,
    block_manage,
    link_account, # 👈 Added
    default
)
from core.db import init_db, AsyncSessionLocal
from core.events import setup_event_listeners
from bot.middlewares import (
    AuthMiddleware,
    CallbackReceiptMiddleware,
    StaleNavigationHandoffMiddleware,
    TradeContentionGateMiddleware,
)
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.utils.trade_suggestion_messages import listen_trade_suggestion_events
from core.logging_config import configure_logging
from core.offer_publication_worker import offer_telegram_publication_loop
from core.telegram_admin_broadcast_worker import telegram_admin_broadcast_delivery_loop
from core.telegram_notification_outbox_worker import telegram_notification_outbox_delivery_loop
from core.trade_delivery_worker import telegram_trade_delivery_loop
from core.telegram_delivery_queue_worker import telegram_delivery_queue_loop
from core.telegram_delivery_queue_limiter import (
    configured_redis_telegram_delivery_limiter,
)
from core.telegram_delivery_runtime_composition import (
    build_configured_telegram_delivery_runtime,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)

# Configure logging
configure_logging("bot")
logger = logging.getLogger(__name__)


class BotRuntimeSurfaceError(RuntimeError):
    """Raised when the Telegram bot entrypoint is started on a forbidden surface."""


def configured_telegram_delivery_queue_worker_factory(settings_obj):
    """Return a zero-argument queue runner with all production dependencies bound."""

    composition = build_configured_telegram_delivery_runtime(settings=settings_obj)

    async def run_configured_telegram_delivery_queue() -> None:
        redis_client = redis.Redis.from_url(
            str(getattr(settings_obj, "redis_url", "") or ""),
            decode_responses=True,
        )
        limiter = configured_redis_telegram_delivery_limiter(
            redis_client,
            settings=settings_obj,
        )
        try:
            await telegram_delivery_queue_loop(
                freshness_validators=composition.freshness_validators,
                lifecycle_feedbacks=composition.lifecycle_feedbacks,
                credential_registry=composition.credential_registry,
                dispatch_limiter=limiter,
                bot_identities=composition.bot_identities,
            )
        finally:
            await redis_client.aclose()

    return run_configured_telegram_delivery_queue


def telegram_execution_worker_factories(
    runtime: TelegramDeliveryRuntimeDecision,
    *,
    settings_obj=settings,
):
    """Return exactly one ownership set without creating coroutine objects."""
    if runtime.mode == TelegramDeliveryRuntimeMode.LEGACY:
        if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
            raise TelegramDeliveryRuntimeConfigurationError(
                "inconsistent_legacy_runtime_decision"
            )
        return (
            offer_telegram_publication_loop,
            telegram_trade_delivery_loop,
            telegram_admin_broadcast_delivery_loop,
            telegram_notification_outbox_delivery_loop,
        )
    if runtime.mode == TelegramDeliveryRuntimeMode.QUEUE_V1:
        if runtime.legacy_workers_enabled or not runtime.queue_worker_enabled:
            raise TelegramDeliveryRuntimeConfigurationError(
                "inconsistent_queue_runtime_decision"
            )
        return (configured_telegram_delivery_queue_worker_factory(settings_obj),)
    raise TelegramDeliveryRuntimeConfigurationError("unknown_runtime_decision_mode")


def _configured_service_name() -> str:
    return str(getattr(settings, "trading_bot_service", "") or "").strip().lower()


def assert_bot_runtime_surface() -> None:
    assert_not_dark_standby("bot")
    configured_server_mode = normalize_server(getattr(settings, "server_mode", None), default="")
    configured_service = _configured_service_name()
    reasons: list[str] = []

    if configured_server_mode != SERVER_FOREIGN:
        reasons.append("SERVER_MODE must be foreign for Telegram bot runtime")
    if configured_service != "bot":
        reasons.append("TRADING_BOT_SERVICE must be bot for Telegram bot runtime")
    if not settings.bot_token:
        reasons.append("BOT_TOKEN is required for Telegram bot runtime")

    if not reasons:
        return

    logger.critical(
        "Bot runtime surface guard refused startup",
        extra={
            "event": "bot.runtime_surface_refused",
            "configured_server_mode": configured_server_mode or None,
            "configured_service": configured_service or None,
            "telegram_credential_configured": bool(settings.bot_token),
            "reasons": reasons,
        },
    )
    raise BotRuntimeSurfaceError("; ".join(reasons))


async def main():
    assert_bot_runtime_surface()
    telegram_runtime = configured_telegram_delivery_runtime()

    # Initialize Database
    await init_db()

    # Register SQLAlchemy event listeners for sync & realtime events
    setup_event_listeners()

    bot = Bot(token=settings.bot_token)
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(
        storage=storage,
        events_isolation=storage.create_isolation(lock_kwargs={"timeout": 120}),
    )

    # Capture the callback deadline origin before Auth or any other DB work.
    dp.update.outer_middleware(CallbackReceiptMiddleware())

    # Hot trade callbacks must fail fast before Auth opens a DB session.
    dp.update.outer_middleware(TradeContentionGateMiddleware())

    # Auth: inject user into handler data for ALL updates (must be before routers)
    auth_mw = AuthMiddleware(AsyncSessionLocal)
    dp.update.outer_middleware(auth_mw)
    dp.update.outer_middleware(BotLoggingContextMiddleware())
    dp.update.outer_middleware(StaleNavigationHandoffMiddleware())

    # Include routers
    dp.include_router(start.router)
    dp.include_router(link_account.router) # 👈 Added (high priority)
    dp.include_router(panel.router)
    dp.include_router(trade_create.router)
    dp.include_router(trade_execute.router)
    dp.include_router(trade_manage.router)
    dp.include_router(trade_history.router)
    dp.include_router(commodity_catalog.router)
    dp.include_router(admin.router)
    dp.include_router(admin_broadcast.router)
    dp.include_router(admin_commodities.router)
    dp.include_router(admin_users.router)
    dp.include_router(block_manage.router)
    
    # Default router should be last
    dp.include_router(default.router)

    logger.info("🤖 Bot started...")
    suggestion_sync_task = asyncio.create_task(listen_trade_suggestion_events(bot))
    telegram_execution_tasks = [
        asyncio.create_task(worker_factory())
        for worker_factory in telegram_execution_worker_factories(telegram_runtime)
    ]
    runtime_tasks = [suggestion_sync_task, *telegram_execution_tasks]
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        for task in runtime_tasks:
            task.cancel()
        await asyncio.gather(*runtime_tasks, return_exceptions=True)
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BotRuntimeSurfaceError:
        raise SystemExit(78)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
