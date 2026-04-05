import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from api.routers import (
    auth, invitations, commodities, users, notifications, 
    trading_settings, offers, trades, realtime, users_public, chat, blocks, sync, sessions
)
from core.config import settings
from core.redis import init_redis, close_redis
from core.db import init_db
from core.events import setup_event_listeners
from core.connectivity import connectivity_monitor_loop
from core.offer_expiry import offer_expiry_loop
from core.session_expiry import session_expiry_loop
import asyncio
import schemas

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting up...")
    await init_db()
    await init_redis()
    setup_event_listeners()
    
    # Start connectivity monitor task (Iran only)
    if settings.server_mode == "iran":
        asyncio.create_task(connectivity_monitor_loop())
    
    # Start offer auto-expiry background task
    asyncio.create_task(offer_expiry_loop())
    asyncio.create_task(session_expiry_loop())
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    await close_redis()

app = FastAPI(title="Trading Bot API", lifespan=lifespan)

# -------------------------------------------------------
# 🔒 CORS Configuration
# -------------------------------------------------------
origins = [
    "http://localhost:5173",
    "http://localhost:8000",
    "https://mini-app.362514.ir",
    "https://coin.gold-trade.ir",
    "http://87.107.110.68"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # موقت برای توسعه
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# 🛣️ API Routers
# -------------------------------------------------------
api_router = APIRouter(prefix="/api")

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["Invitations"])
api_router.include_router(commodities.router, prefix="/commodities", tags=["Commodities"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(trading_settings.router, prefix="/trading-settings", tags=["Settings"])
api_router.include_router(offers.router, prefix="/offers", tags=["Offers"])
api_router.include_router(trades.router, prefix="/trades", tags=["Trades"])
api_router.include_router(realtime.router, prefix="/realtime", tags=["Realtime"])
api_router.include_router(users_public.router, prefix="/users-public", tags=["Public Users"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
api_router.include_router(blocks.router, prefix="/blocks", tags=["Blocks"])
api_router.include_router(sync.router, prefix="/sync", tags=["Sync"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])

app.include_router(api_router)

# -------------------------------------------------------
# 🌐 Public Config Endpoint
# -------------------------------------------------------
@app.get("/api/config")
async def get_public_config():
    """Public config endpoint — returns non-sensitive settings for frontend."""
    return {
        "bot_username": settings.bot_username,
        "frontend_url": settings.frontend_url,
    }

# -------------------------------------------------------
# 📂 Static Files & Frontend Serving
# -------------------------------------------------------
# مسیر بیلد شده Frontend (dist)
static_dir = Path("mini_app_dist")

if static_dir.exists():
    
    # Catch-all for SPA (Vue Router)
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # اگر درخواست API بود و هندل نشده بود -> 404 بده (به index.html نفرست)
        if full_path.startswith("api/"):
             return JSONResponse({"detail": "Not Found"}, status_code=404)
             
        # اگر فایل استاتیک بود و وجود داشت -> سرو کن (تمام asset ها و عکس ها)
        file_path = static_dir / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        
        # اگر کاربر یک فایل .js از نسخه قدیمی را درخواست کرد (PWA Cache stale):
        if full_path.startswith("assets/") and full_path.endswith(".js"):
             logger.warning(f"Old JS chunk requested: {full_path}. Forcing PWA reload on client.")
             js_fallback = "console.warn('Stale PWA chunk requested. Forcing hard reload...'); window.location.reload(true);"
             return Response(content=js_fallback, media_type="application/javascript")
             
        # در غیر این صورت -> index.html (برای Vue Router)
        return FileResponse(static_dir / "index.html")
else:
    logger.warning("⚠️ Frontend build directory not found. Run 'npm run build' first.")

@app.get("/")
async def root():
    if static_dir.exists():
        return FileResponse(static_dir / "index.html")
    return {"message": "Trading Bot API is running 🚀"}
