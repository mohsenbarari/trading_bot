import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routers import auth, invitations, commodities, users, notifications, trading_settings, offers, trades, realtime
from core.config import settings
from core.redis import init_redis, close_redis
import schemas

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "mini_app_dist"
API_PREFIX = "/api"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading_bot.main")


# -------------------------------------------------------
# 🔄 Lifespan - مدیریت Lifecycle (Startup/Shutdown)
# -------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """مدیریت startup و shutdown اپلیکیشن."""
    # Startup
    logger.info("🚀 Application startup...")
    await init_redis()
    
    yield  # اجرای اپلیکیشن
    
    # Shutdown
    logger.info("🛑 Application shutdown...")
    await close_redis()


app = FastAPI(title="Trading Bot Backend + Vue Frontend", lifespan=lifespan)

# -------------------------------------------------------
# 🧩 تنظیم CORS
# -------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# 🔌 ساخت و ثبت یک روتر اصلی برای تمام API ها
# -------------------------------------------------------
# 1. یک روتر اصلی برای API می‌سازیم
api_router = APIRouter(prefix=API_PREFIX)

# 2. روترهای دیگر را به این روتر اصلی اضافه می‌کنیم
api_router.include_router(auth.router)
api_router.include_router(invitations.router)
api_router.include_router(commodities.router)
api_router.include_router(users.router)
api_router.include_router(notifications.router)
api_router.include_router(trading_settings.router)
api_router.include_router(offers.router)
api_router.include_router(trades.router)
api_router.include_router(trades.router)
api_router.include_router(realtime.router)
from api.routers import users_public
api_router.include_router(users_public.router)
from api.routers import chat
api_router.include_router(chat.router)
from api.routers import blocks
api_router.include_router(blocks.router)

# 3. اندپوینت config را به همین روتر اصلی اضافه می‌کنیم
@api_router.get("/config", response_model=schemas.AppConfig)
async def get_app_config():
    """تنظیمات عمومی برنامه را برمی‌گرداند."""
    return {"bot_username": settings.bot_username}

# 4. در نهایت، روتر اصلی و کامل شده را به اپلیکیشن FastAPI اضافه می‌کنیم
app.include_router(api_router)
logger.info("All API routers are included under /api prefix.")

# -------------------------------------------------------
# 📁 سرو فایل‌های آپلود شده (مثل تصاویر چت)
# -------------------------------------------------------
UPLOADS_DIR = BASE_DIR / "uploads"
if not UPLOADS_DIR.exists():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
logger.info(f"Mounted uploads directory at /uploads")


# -------------------------------------------------------
# 🪄 سرو فایل‌های استاتیک Vue (مثل CSS و JS)
# -------------------------------------------------------
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")
    logger.info(f"Mounted Vue static assets from {DIST_DIR / 'assets'}")
else:
    logger.warning(f"⚠️ Vue build directory not found: {DIST_DIR}")

# -------------------------------------------------------
# 🌐 سرو فایل index.html برای هر مسیر دیگر (باید در انتها باشد)
# -------------------------------------------------------
@app.get("/{full_path:path}")
async def serve_vue_app(full_path: str):
    """
    سرو فایل‌های فرانت‌اند (SPA).
    ابتدا بررسی می‌کند فایل درخواست شده (مثلاً sw.js یا manifest.webmanifest) وجود دارد یا خیر.
    اگر نبود، index.html را برمی‌گرداند تا Vue Router در سمت کلاینت هندل کند.
    """
    # 1. Try to find the file directly in DIST_DIR (e.g., manifest.webmanifest, sw.js)
    requested_file = DIST_DIR / full_path
    if full_path and requested_file.is_file():
        return FileResponse(requested_file)

    # 2. Fallback to index.html for SPA routing
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    
    logger.error("index.html not found in Vue build output.")
    return {"error": "Frontend not built yet."}, 404

# -------------------------------------------------------
# 🚀 اجرای برنامه
# -------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info("--> Starting FastAPI app for Trading Bot...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)