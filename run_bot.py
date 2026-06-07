import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from core.config import settings
from core.logging_config import configure_logging

configure_logging("bot")

from bot.handlers import (  # noqa: E402
    admin,
    admin_commodities,
    admin_users,
    block_manage,
    default,
    link_account,
    panel,
    start,
    trade_create,
    trade_execute,
    trade_history,
    trade_manage,
)
from bot.middlewares import AuthMiddleware  # noqa: E402
from bot.utils.trade_suggestion_messages import listen_trade_suggestion_events  # noqa: E402
from core.db import AsyncSessionLocal, init_db  # noqa: E402
from core.events import setup_event_listeners  # noqa: E402

logger = logging.getLogger(__name__)


async def main():
    if not settings.bot_token:
        logger.error("BOT_TOKEN is not set", extra={"event": "bot.config_missing", "setting": "BOT_TOKEN"})
        return

    # Initialize Database
    await init_db()

    # Register SQLAlchemy event listeners for sync & realtime events
    setup_event_listeners()

    bot = Bot(token=settings.bot_token)
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # Auth: inject user into handler data for ALL updates (must be before routers)
    auth_mw = AuthMiddleware(AsyncSessionLocal)
    dp.update.outer_middleware(auth_mw)

    # Include routers
    dp.include_router(start.router)
    dp.include_router(link_account.router)  # high priority
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

    logger.info("Bot started", extra={"event": "bot.startup"})
    suggestion_sync_task = asyncio.create_task(listen_trade_suggestion_events(bot))
    try:
        await dp.start_polling(bot)
    except Exception:
        logger.exception("Bot polling failed", extra={"event": "bot.polling_failed"})
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
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped", extra={"event": "bot.shutdown"})
