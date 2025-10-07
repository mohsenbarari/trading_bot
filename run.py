# run.py (نسخه نهایی و قطعی)
import asyncio
import logging
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import settings
from main import app as fastapi_app

# وارد کردن تمام روترها
from bot.handlers.start import router as start_router
from bot.handlers.panel import router as panel_router
from bot.handlers.default import router as default_router

from bot.middlewares.auth import AuthMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    # --- بخش کلیدی اصلاح شده ---
    # میدل‌ور را مستقیماً روی روتر اصلی برای پیام‌ها و کلیک‌ها ثبت می‌کنیم
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    # --------------------------
    
    # ثبت روترها با اولویت مشخص
    dp.include_router(start_router)
    dp.include_router(panel_router)
    
    # روتر پیش‌فرض باید همیشه در آخر ثبت شود
    dp.include_router(default_router)
    
    config = uvicorn.Config(app=fastapi_app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    logger.info("Starting both Bot and API server...")
    await asyncio.gather(dp.start_polling(bot), server.serve())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot and API stopped.")