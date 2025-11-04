# run_bot.py (نسخه نهایی با روتر مدیریت کالاها)
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import settings
from core.db import AsyncSessionLocal
from bot.middlewares.auth import AuthMiddleware
# --- هندلرهای جدید را import کنید ---
from bot.handlers import start, panel, default, admin, admin_commodities

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """ربات تلگرام را مقداردهی اولیه کرده و به صورت دائمی اجرا می‌کند."""
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    auth_middleware = AuthMiddleware(session_pool=AsyncSessionLocal)
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    # ثبت روترها
    dp.include_router(start.router)
    dp.include_router(panel.router)
    dp.include_router(admin.router)
    # --- روتر جدید را ثبت کنید ---
    dp.include_router(admin_commodities.router)
    # --- پایان ثبت ---
    dp.include_router(default.router) # default باید آخرین روتر باشد

    logger.info("--> Starting Bot polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("--> Bot polling stopped.")