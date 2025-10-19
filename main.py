import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routers import auth, invitations  # ماژول‌های API اصلی

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "mini_app_dist"  # مسیر خروجی بیلد Vue
API_PREFIX = "/api"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading_bot.main")

app = FastAPI(title="Trading Bot Backend + Vue Frontend")

# -------------------------------------------------------
# 🧩 تنظیم CORS برای ارتباط فرانت و بک
# -------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # در محیط production بهتره خاص‌تر باشه
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# 🔌 ثبت API Routerها
# -------------------------------------------------------
app.include_router(auth.router, prefix=API_PREFIX)
logger.info("Included auth.router at /api")

app.include_router(invitations.router, prefix=API_PREFIX)
logger.info("Included invitations.router at /api")

# -------------------------------------------------------
# 🪄 سرو فایل‌های Vue Build شده
# -------------------------------------------------------
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")
    logger.info(f"Mounted Vue static assets from {DIST_DIR / 'assets'}")
else:
    logger.warning(f"⚠️ Vue build directory not found: {DIST_DIR}")

# -------------------------------------------------------
# 🌐 سرو index.html برای هر مسیر غیر API
# -------------------------------------------------------
@app.get("/{full_path:path}")
async def serve_vue(full_path: str):
    """
    هر مسیر غیر از API با فایل index.html پاسخ داده می‌شود
    (برای پشتیبانی از Vue Router در حالت history mode)
    """
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        logger.info(f"Request to '{full_path}' → serving index.html")
        return FileResponse(index_file)
    logger.error("index.html not found in Vue build output.")
    return {"error": "Frontend not built yet. Please run npm run build in frontend directory."}

# -------------------------------------------------------
# 🚀 اجرای برنامه
# -------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("--> Starting FastAPI app for Trading Bot...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
