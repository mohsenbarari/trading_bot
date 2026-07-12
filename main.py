import logging
import ipaddress
import os
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from api.routers import (
    auth, invitations, commodities, users, notifications, 
    trading_settings, offers, trades, realtime, users_public, chat, blocks, sync, sessions, admin_messages
)
from api.routers import accountants
from api.routers import customers
from core.config import settings
from core.deployment_surface import allowed_cors_origins
from core.redis import init_redis, close_redis, get_redis_client
from core.db import AsyncSessionLocal, init_db
from core.events import setup_event_listeners
from core.server_routing import SERVER_FOREIGN, normalize_server
from core.background_job_authority import (
    BackgroundJobAuthorityDecision,
    filter_allowed_background_job_factories,
)
from core.connectivity import connectivity_monitor_loop
from core.market_schedule_loop import market_schedule_loop
from core.offer_expiry import offer_expiry_loop
from core.session_expiry import session_expiry_loop
from core.trade_delivery_worker import telegram_trade_delivery_loop, webapp_trade_delivery_loop
from core.telegram_registration_reconciliation_worker import (
    telegram_registration_reconciliation_loop,
)
from core.otp_sms_fallback_worker import otp_sms_fallback_loop
from core.services.otp_delivery_state_service import validate_otp_delivery_runtime_settings
from core.registration_feature_policy import registration_reconciliation_runtime_ready
from core.registration_observability import refresh_registration_job_metrics
from core.user_account_status_loop import user_account_status_loop
from core.services.chat_room_service import ensure_mandatory_channel_rollout
from core.production_test_isolation import (
    get_isolation_config,
    isolation_block_payload,
    user_matches_isolation_allowlist,
)
from models.user import User
import asyncio
import schemas
from core.logging_config import configure_logging
from core.metrics import metrics_response_body, registry, uptime_seconds
from core.audit_logger import audit_log
from core.security import constant_time_secret_equals
from core.request_logging import install_request_logging_middleware
from core.public_webapp_url import public_webapp_url_for_links

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
configure_logging("api")
logger = logging.getLogger(__name__)
_PROCESS_STARTED_AT = time.monotonic()
OBSERVABILITY_API_KEY_HEADER = "X-Observability-Api-Key"
FOREIGN_INTERNAL_EXACT_PATHS = {"/metrics"}
FOREIGN_INTERNAL_API_PREFIXES = (
    "/api/sync",
    "/api/sessions/internal",
    "/api/trades/internal",
    "/api/offers/internal",
    "/api/auth/internal/telegram-otp",
)
FOREIGN_LOOPBACK_INTERNAL_PATHS = {"/api/config"}
PRODUCTION_TEST_ISOLATION_PUBLIC_EXACT_PATHS = {
    "/api/config",
    "/api/auth/request-otp",
    "/api/auth/resend-otp-sms",
    "/api/auth/verify-otp",
    "/api/auth/webapp-login",
    "/api/auth/register-otp-request",
    "/api/auth/register-otp-verify",
    "/api/auth/register-complete",
    "/api/auth/refresh",
}
PRODUCTION_TEST_ISOLATION_PUBLIC_PREFIXES = (
    "/api/auth/pending-registration/",
    "/api/invitations/lookup/",
    "/api/invitations/validate/",
)
PRODUCTION_TEST_ISOLATION_INTERNAL_PREFIXES = (
    "/api/sync",
    "/api/sessions/internal",
    "/api/trades/internal",
    "/api/offers/internal",
    "/api/invitations/internal",
    "/api/auth/internal/telegram-registration",
    "/api/auth/internal/telegram-link",
    "/api/auth/internal/telegram-otp",
)
BACKGROUND_LEADER_LOCK_KEY = "trading_bot:api:background_leader"
BACKGROUND_LEADER_REFRESH_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
BACKGROUND_LEADER_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _background_leader_lock_key() -> str:
    return f"{BACKGROUND_LEADER_LOCK_KEY}:{normalize_server(settings.server_mode)}"


def _is_loopback_client(client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host in {"localhost"}


def _is_metrics_request_allowed(request: Request) -> bool:
    configured_key = getattr(settings, "observability_api_key", None)
    supplied_key = request.headers.get(OBSERVABILITY_API_KEY_HEADER)
    if constant_time_secret_equals(supplied_key, configured_key):
        return True
    return _is_loopback_client(request.client.host if request.client else None)


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _is_foreign_internal_path(path: str, request: Request) -> bool:
    if path in FOREIGN_INTERNAL_EXACT_PATHS:
        return True
    if any(_path_matches_prefix(path, prefix) for prefix in FOREIGN_INTERNAL_API_PREFIXES):
        return True
    if path in FOREIGN_LOOPBACK_INTERNAL_PATHS:
        return _is_loopback_client(request.client.host if request.client else None)
    return False


def _foreign_surface_guard_reason(request: Request) -> str | None:
    if normalize_server(getattr(settings, "server_mode", None)) != SERVER_FOREIGN:
        return None

    path = request.url.path
    if _is_foreign_internal_path(path, request):
        return None
    if _path_matches_prefix(path, "/api/chat"):
        return "foreign_chat_surface_blocked"
    if path.startswith("/api/"):
        return "foreign_webapp_api_blocked"
    return "foreign_frontend_surface_blocked"


def _foreign_surface_blocked_response() -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization") or request.headers.get("Authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _user_id_from_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
        return int(subject) if subject is not None else None
    except (JWTError, TypeError, ValueError):
        return None


def _is_production_test_isolation_public_path(path: str) -> bool:
    if path in PRODUCTION_TEST_ISOLATION_PUBLIC_EXACT_PATHS:
        return True
    return any(_path_matches_prefix(path, prefix.rstrip("/")) for prefix in PRODUCTION_TEST_ISOLATION_PUBLIC_PREFIXES)


def _is_production_test_isolation_internal_path(path: str) -> bool:
    if path == "/metrics":
        return True
    return any(_path_matches_prefix(path, prefix) for prefix in PRODUCTION_TEST_ISOLATION_INTERNAL_PREFIXES)


def _should_apply_production_test_isolation_to_path(path: str) -> bool:
    if _is_production_test_isolation_internal_path(path):
        return False
    if _is_production_test_isolation_public_path(path):
        return False
    return path.startswith("/api/")


def _background_job_factories():
    jobs = [
        ("connectivity_monitor", connectivity_monitor_loop),
        ("offer_expiry", offer_expiry_loop),
        ("market_schedule", market_schedule_loop),
        ("session_expiry", session_expiry_loop),
        ("user_account_status", user_account_status_loop),
        ("trade_webapp_delivery", webapp_trade_delivery_loop),
        ("trade_telegram_delivery", telegram_trade_delivery_loop),
    ]
    if registration_reconciliation_runtime_ready(settings):
        jobs.append(
            (
                "telegram_registration_reconciliation",
                telegram_registration_reconciliation_loop,
            )
        )
    if settings.telegram_login_otp_enabled and settings.otp_sms_auto_fallback_enabled:
        jobs.append(("otp_sms_fallback", otp_sms_fallback_loop))
    return filter_allowed_background_job_factories(
        jobs,
        on_rejected=_log_background_job_authority_rejection,
    )


def _log_background_job_authority_rejection(decision: BackgroundJobAuthorityDecision) -> None:
    logger.info(
        "Skipping background job on this server by authority policy",
        extra=decision.as_log_extra(),
    )


async def _cancel_background_jobs(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_background_leader(redis_client) -> None:
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    lock_key = _background_leader_lock_key()
    ttl_seconds = max(30, int(settings.background_leader_lock_ttl_seconds))
    refresh_seconds = max(5, int(settings.background_leader_lock_refresh_seconds))
    retry_seconds = max(1, int(settings.background_leader_retry_seconds))

    while True:
        tasks: list[asyncio.Task] = []
        acquired_lock = False
        try:
            acquired = await redis_client.set(
                lock_key,
                token,
                ex=ttl_seconds,
                nx=True,
            )
            if not acquired:
                await asyncio.sleep(retry_seconds)
                continue
            acquired_lock = True

            jobs = _background_job_factories()
            logger.info(
                "API worker acquired background leader lock",
                extra={
                    "event": "background.leader.acquired",
                    "worker_pid": os.getpid(),
                    "job_names": [name for name, _ in jobs],
                    "lock_key": lock_key,
                    "lock_ttl_seconds": ttl_seconds,
                },
            )
            tasks = [asyncio.create_task(factory()) for _, factory in jobs]

            while True:
                await asyncio.sleep(refresh_seconds)
                refreshed = await redis_client.eval(
                    BACKGROUND_LEADER_REFRESH_SCRIPT,
                    1,
                    lock_key,
                    token,
                    ttl_seconds,
                )
                if int(refreshed or 0) != 1:
                    logger.warning(
                        "API worker lost background leader lock; stopping singleton jobs",
                        extra={
                            "event": "background.leader.lost",
                            "worker_pid": os.getpid(),
                            "lock_key": lock_key,
                        },
                    )
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Background leader loop failed",
                extra={
                    "event": "background.leader.error",
                    "worker_pid": os.getpid(),
                },
            )
        finally:
            await _cancel_background_jobs(tasks)
            if acquired_lock:
                try:
                    await redis_client.eval(
                        BACKGROUND_LEADER_RELEASE_SCRIPT,
                        1,
                        lock_key,
                        token,
                    )
                except Exception:
                    logger.exception(
                        "Failed to release background leader lock",
                        extra={
                            "event": "background.leader.release_failed",
                            "worker_pid": os.getpid(),
                        },
                    )
        await asyncio.sleep(retry_seconds)


def _start_background_leader_task(redis_client) -> asyncio.Task:
    return asyncio.create_task(_run_background_leader(redis_client))


def _is_mandatory_channel_membership_race(exc: IntegrityError) -> bool:
    message = str(exc).lower()
    return (
        "ux_chat_members_active_membership" in message
        or (
            "duplicate key" in message
            and "chat_members" in message
            and "membership" in message
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting up...")
    validate_otp_delivery_runtime_settings(settings)
    if settings.invitation_contract_v2_enabled:
        public_webapp_url_for_links()
    await init_db()
    redis_client = await init_redis()
    setup_event_listeners()

    async with AsyncSessionLocal() as session:
        try:
            await ensure_mandatory_channel_rollout(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if not _is_mandatory_channel_membership_race(exc):
                raise
            logger.warning(
                "Mandatory channel rollout hit a concurrent active-membership insert; continuing startup.",
                extra={
                    "event": "mandatory_channel_rollout.membership_race_ignored",
                    "error_class": type(exc).__name__,
                },
            )
        except Exception:
            await session.rollback()
            raise
    background_leader_task = None
    if settings.background_jobs_enabled:
        background_leader_task = _start_background_leader_task(redis_client)
    else:
        logger.warning(
            "API background jobs are disabled by runtime configuration",
            extra={
                "event": "background.jobs.disabled",
                "server_mode": settings.server_mode,
            },
        )

    try:
        yield
    finally:
        # Shutdown
        logger.info("🛑 Shutting down...")
        if background_leader_task is not None:
            background_leader_task.cancel()
            await asyncio.gather(background_leader_task, return_exceptions=True)
        await close_redis()

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
origins = allowed_cors_origins(settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_foreign_surface_guard(request: Request, call_next):
    reason = _foreign_surface_guard_reason(request)
    if reason is not None:
        logger.warning(
            "Blocked foreign server public surface request",
            extra={
                "event": "foreign_surface.blocked",
                "path": request.url.path,
                "reason": reason,
                "client_host": request.client.host if request.client else None,
            },
        )
        return _foreign_surface_blocked_response()
    return await call_next(request)


@app.middleware("http")
async def enforce_production_test_isolation(request: Request, call_next):
    path = request.url.path
    if not _should_apply_production_test_isolation_to_path(path):
        return await call_next(request)

    config = await get_isolation_config()
    if not config.enabled:
        return await call_next(request)

    user_id = _user_id_from_token(_extract_bearer_token(request))
    user = None
    if user_id:
        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
    if user_matches_isolation_allowlist(user, config):
        return await call_next(request)

    logger.warning(
        "Blocked WebApp request during production test isolation",
        extra={
            "event": "production_test_isolation.http_blocked",
            "path": path,
            "user_id": user_id,
            "reason": config.reason,
            "client_host": request.client.host if request.client else None,
        },
    )
    return JSONResponse(
        isolation_block_payload(config.reason),
        status_code=503,
        headers={"Cache-Control": "no-store"},
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


@app.get("/metrics")
async def get_metrics(request: Request):
    """Prometheus-compatible metrics endpoint."""
    if not _is_metrics_request_allowed(request):
        if request.headers.get(OBSERVABILITY_API_KEY_HEADER):
            audit_log(
                "observability.metrics_access",
                target_type="metrics",
                result="denied",
                reason="invalid_observability_api_key",
                extra={"path": "/metrics", "status_code": 404},
            )
        raise HTTPException(status_code=404, detail="Not found")
    registry.gauge(
        "trading_bot_process_uptime_seconds",
        "Process uptime in seconds.",
        uptime_seconds(_PROCESS_STARTED_AT),
    )
    try:
        redis_client = get_redis_client()
    except RuntimeError:
        redis_client = None
    if redis_client is not None:
        try:
            await asyncio.wait_for(
                refresh_registration_job_metrics(redis_client),
                timeout=0.25,
            )
        except Exception:
            pass
    return Response(content=metrics_response_body(), media_type="text/plain; version=0.0.4; charset=utf-8")

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
