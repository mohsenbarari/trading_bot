import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from core.config import settings
from core.server_routing import SERVER_FOREIGN, normalize_server
from bot.handlers import (
    start, 
    panel, 
    trade_create, 
    trade_execute, 
    trade_manage,
    trade_history,
    admin,
    admin_commodities,
    admin_users,
    block_manage,
    link_account, # 👈 Added
    default
)
from core.db import init_db, AsyncSessionLocal
from core.events import setup_event_listeners
from bot.middlewares import AuthMiddleware, TradeContentionGateMiddleware
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.utils.trade_suggestion_messages import listen_trade_suggestion_events
from core.logging_config import configure_logging

# Configure logging
configure_logging("bot")
logger = logging.getLogger(__name__)


class BotRuntimeSurfaceError(RuntimeError):
    """Raised when the Telegram bot entrypoint is started on a forbidden surface."""


def _configured_service_name() -> str:
    return str(getattr(settings, "trading_bot_service", "") or "").strip().lower()


def assert_bot_runtime_surface() -> None:
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

    # Initialize Database
    await init_db()

    # Register SQLAlchemy event listeners for sync & realtime events
    setup_event_listeners()

    bot = Bot(token=settings.bot_token)
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # Hot trade callbacks must fail fast before Auth opens a DB session.
    dp.update.outer_middleware(TradeContentionGateMiddleware())

    # Auth: inject user into handler data for ALL updates (must be before routers)
    auth_mw = AuthMiddleware(AsyncSessionLocal)
    dp.update.outer_middleware(auth_mw)
    dp.update.outer_middleware(BotLoggingContextMiddleware())

    # Include routers
    dp.include_router(start.router)
    dp.include_router(link_account.router) # 👈 Added (high priority)
    dp.include_router(panel.router)
    dp.include_router(trade_create.router)
    dp.include_router(trade_execute.router)
    dp.include_router(trade_manage.router)
    dp.include_router(trade_history.router)
    dp.include_router(admin.router)
    dp.include_router(admin_commodities.router)
    dp.include_router(admin_users.router)
    dp.include_router(block_manage.router)
    
    # Default router should be last
    dp.include_router(default.router)

    logger.info("🤖 Bot started...")
    suggestion_sync_task = asyncio.create_task(listen_trade_suggestion_events(bot))
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        suggestion_sync_task.cancel()
        try:
            await suggestion_sync_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BotRuntimeSurfaceError:
        raise SystemExit(78)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
