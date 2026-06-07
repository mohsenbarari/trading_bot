import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from core.config import settings
from core.logging_config import configure_logging, install_request_logging_middleware

configure_logging("api")

from api.routers import (  # noqa: E402
    admin_messages,
    auth,
    blocks,
    chat,
    commodities,
    invitations,
    notifications,
    offers,
    realtime,
    sessions,
    sync,
    trades,
    trading_settings,
    users,
    users_public,
)
from api.routers import accountants, customers  # noqa: E402
from core.connectivity import connectivity_monitor_loop  # noqa: E402
from core.db import AsyncSessionLocal, init_db  # noqa: E402
from core.events import setup_event_listeners  # noqa: E402
from core.market_schedule_loop import market_schedule_loop  # noqa: E402
from core.offer_expiry import offer_expiry_loop  # noqa: E402
from core.redis import close_redis, init_redis  # noqa: E402
from core.services.chat_room_service import ensure_mandatory_channel_rollout  # noqa: E402
from core.session_expiry import session_expiry_loop  # noqa: E402
from core.user_account_status_loop import user_account_status_loop  # noqa: E402
import schemas  # noqa: E402,F401

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up", extra={"event": "app.startup"})
    await init_db()
    await init_redis()
    setup_event_listeners()

    async with AsyncSessionLocal() as session:
        try:
            await ensure_mandatory_channel_rollout(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Mandatory channel rollout failed", extra={"event": "app.startup.rollout_failed"})
            raise

    # Start connectivity monitor task (Iran only)
    if settings.server_mode == "iran":
        asyncio.create_task(connectivity_monitor_loop())

    # Start offer auto-expiry background task
    asyncio.create_task(offer_expiry_loop())
    asyncio.create_task(market_schedule_loop())
    asyncio.create_task(session_expiry_loop())
    asyncio.create_task(user_account_status_loop())

    yield

    # Shutdown
    logger.info("Shutting down", extra={"event": "app.shutdown"})
    await close_redis()


def _normalize_origin(raw_value: str | None) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_allowed_origins() -> list[str]:
    allowed = {
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "https://mini-app.362514.ir",
        "https://coin.362514.ir",
        "https://coin.gold-trade.ir",
        "http://87.107.110.68",
    }

    for raw_candidate in (
        settings.frontend_url,
        settings.foreign_server_domain,
        settings.iran_server_domain,
    ):
        normalized = _normalize_origin(raw_candidate)
        if normalized:
            allowed.add(normalized)

    return sorted(allowed)


app = FastAPI(
    title="Trading Bot API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_request_logging_middleware(app)

# -------------------------------------------------------
# 🔒 CORS Configuration
# -------------------------------------------------------
origins = _build_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# 🛣️ API Routers
# -------------------------------------------------------
api_router = APIRouter(prefix="/api")

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(accountants.router, prefix="/accountants", tags=["Accountants"])
api_router.include_router(customers.router, prefix="/customers", tags=["Customers"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["Invitations"])
api_router.include_router(commodities.router, prefix="/commodities", tags=["Commodities"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(admin_messages.router, prefix="/admin-messages", tags=["Admin Messages"])
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
blocked_frontend_probe_paths = {
    "openapi.json",
    "docs",
    "redoc",
}

if static_dir.exists():

    # Catch-all for SPA (Vue Router)
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # اگر درخواست API بود و هندل نشده بود -> 404 بده (به index.html نفرست)
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        if full_path in blocked_frontend_probe_paths or full_path.startswith("docs/") or full_path.startswith("redoc/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # اگر فایل استاتیک بود و وجود داشت -> سرو کن (تمام asset ها و عکس ها)
        file_path = static_dir / full_path
        if file_path.is_file():
            return FileResponse(file_path)

        # اگر کاربر یک فایل .js از نسخه قدیمی را درخواست کرد (PWA Cache stale):
        if full_path.startswith("assets/") and full_path.endswith(".js"):
            logger.warning(
                "Old JS chunk requested. Forcing PWA reload on client.",
                extra={"event": "frontend.stale_chunk", "asset_path": full_path},
            )
            js_fallback = "console.warn('Stale PWA chunk requested. Forcing hard reload...'); window.location.reload(true);"
            return Response(content=js_fallback, media_type="application/javascript")

        # در غیر این صورت -> index.html (برای Vue Router)
        return FileResponse(static_dir / "index.html")
else:
    logger.warning(
        "Frontend build directory not found. Run 'npm run build' first.",
        extra={"event": "frontend.build_missing", "static_dir": str(static_dir)},
    )


@app.get("/")
async def root():
    if static_dir.exists():
        return FileResponse(static_dir / "index.html")
    return {"message": "Trading Bot API is running 🚀"}
