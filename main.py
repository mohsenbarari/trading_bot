import logging
from pathlib import Path
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routers import auth, invitations
from core.config import settings
import schemas

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "mini_app_dist"
API_PREFIX = "/api"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading_bot.main")

app = FastAPI(title="Trading Bot Backend + Vue Frontend")

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

# 3. اندپوینت config را به همین روتر اصلی اضافه می‌کنیم
@api_router.get("/config", response_model=schemas.AppConfig)
async def get_app_config():
    """تنظیمات عمومی برنامه را برمی‌گرداند."""
    return {"bot_username": settings.bot_username}

# 4. در نهایت، روتر اصلی و کامل شده را به اپلیکیشن FastAPI اضافه می‌کنیم
app.include_router(api_router)
logger.info("All API routers are included under /api prefix.")


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
    این مسیر به عنوان آخرین مسیر عمل می‌کند و هر درخواستی که با API ها
    یا فایل‌های استاتیک مطابقت نداشته باشد را به index.html هدایت می‌کند.
    """
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        logger.info(f"Request to '{full_path}' → serving index.html")
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