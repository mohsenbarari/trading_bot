import logging
import ipaddress
from datetime import datetime, timedelta, timezone
import os
import re
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from api.routers import (
    auth, invitations, commodities, users, notifications, 
    trading_settings, offers, trades, realtime, users_public, chat, blocks, sync, sessions, admin_messages, dr_sync
)
from api.routers import accountants
from api.routers import customers
from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.deployment_surface import allowed_cors_origins
from core.redis import init_redis, close_redis, get_redis_client
from core.db import AsyncSessionLocal, get_db, init_db
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
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import (
    WriterControlError,
    WriterStateSnapshot,
    load_writer_snapshot,
    snapshot_is_local_active,
    validate_readiness_evidence,
)
from core.writer_fencing import (
    WriterFenceError,
    projection_fence_scope,
    writer_fence_scope,
)
from core.writer_witness_contract import witness_public_key_is_valid
from core.writer_witness_client import (
    writer_witness_client_configuration_reasons,
    writer_witness_renewal_loop,
)

# -------------------------------------------------------
# 📋 تنظیمات اولیه
# -------------------------------------------------------
configure_logging("api")
logger = logging.getLogger(__name__)
assert_not_dark_standby("api")
RUNTIME_IDENTITY = resolve_runtime_identity(settings)
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
WRITER_FENCE_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WRITER_FENCE_PROJECTION_PREFIXES = ("/api/sync", "/api/dr-sync")
GIT_RELEASE_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
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


def _request_requires_webapp_writer(request: Request) -> bool:
    if not RUNTIME_IDENTITY.is_webapp_authority:
        return False
    if request.method.upper() not in WRITER_FENCE_UNSAFE_METHODS:
        return False
    path = request.url.path
    if any(_path_matches_prefix(path, prefix) for prefix in WRITER_FENCE_PROJECTION_PREFIXES):
        return False
    return path.startswith("/api/")


def _writer_fenced_response(reasons: tuple[str, ...]) -> JSONResponse:
    return JSONResponse(
        {
            "detail": "این سرور در حال حاضر مجوز ثبت تغییرات وب‌اپ را ندارد.",
            "code": "webapp_writer_fenced",
            "reasons": list(reasons),
        },
        status_code=503,
        headers={"Cache-Control": "no-store", "X-WebApp-Writer-State": "fenced"},
    )


def _background_job_factories(writer_snapshot: WriterStateSnapshot | None = None):
    jobs = [
        ("connectivity_monitor", connectivity_monitor_loop),
        ("offer_expiry", offer_expiry_loop),
        ("market_schedule", market_schedule_loop),
        ("session_expiry", session_expiry_loop),
        ("user_account_status", user_account_status_loop),
        ("trade_webapp_delivery", webapp_trade_delivery_loop),
        ("trade_telegram_delivery", telegram_trade_delivery_loop),
    ]
    if settings.writer_witness_required and settings.writer_witness_auto_renew_enabled:
        jobs.append(("writer_witness_renewal", writer_witness_renewal_loop))
    if registration_reconciliation_runtime_ready(settings):
        jobs.append(
            (
                "telegram_registration_reconciliation",
                telegram_registration_reconciliation_loop,
            )
        )
    if settings.telegram_login_otp_enabled and settings.otp_sms_auto_fallback_enabled:
        jobs.append(("otp_sms_fallback", otp_sms_fallback_loop))
    if writer_snapshot is None:
        runtime_role = "active"
    else:
        writer_active, _ = snapshot_is_local_active(
            RUNTIME_IDENTITY,
            writer_snapshot,
            require_witness_lease=settings.writer_witness_required,
        )
        runtime_role = (
            writer_snapshot.local_runtime_role(RUNTIME_IDENTITY.physical_site)
            if writer_active
            else "fenced"
        )
    return filter_allowed_background_job_factories(
        jobs,
        server_mode=settings.server_mode,
        physical_site=RUNTIME_IDENTITY.physical_site,
        runtime_role=runtime_role,
        on_rejected=_log_background_job_authority_rejection,
    )


def _log_background_job_authority_rejection(decision: BackgroundJobAuthorityDecision) -> None:
    logger.info(
        "Skipping background job on this server by authority policy",
        extra=decision.as_log_extra(),
    )


async def _load_runtime_writer_snapshot() -> WriterStateSnapshot | None:
    if not RUNTIME_IDENTITY.is_webapp_authority:
        return None
    async with AsyncSessionLocal() as session:
        return await load_writer_snapshot(session)


def _writer_snapshot_changed(
    previous: WriterStateSnapshot | None,
    current: WriterStateSnapshot | None,
) -> bool:
    if previous is None or current is None:
        return previous is not current
    return (
        previous.writer_epoch != current.writer_epoch
        or previous.transition_id != current.transition_id
        or previous.active_site != current.active_site
        or previous.control_state != current.control_state
        or previous.witness_lease_id != current.witness_lease_id
    )


def _writer_snapshot_is_eligible(snapshot: WriterStateSnapshot | None) -> bool:
    if snapshot is None:
        return True
    active, _ = snapshot_is_local_active(
        RUNTIME_IDENTITY,
        snapshot,
        require_witness_lease=settings.writer_witness_required,
    )
    return active


def _create_background_tasks(
    jobs,
    writer_snapshot: WriterStateSnapshot | None,
) -> list[asyncio.Task]:
    if writer_snapshot is None:
        return [asyncio.create_task(factory()) for _, factory in jobs]
    active, _ = snapshot_is_local_active(
        RUNTIME_IDENTITY,
        writer_snapshot,
        require_witness_lease=settings.writer_witness_required,
    )
    if not active:
        return [asyncio.create_task(factory()) for _, factory in jobs]
    with writer_fence_scope(
        RUNTIME_IDENTITY,
        writer_snapshot,
        source="background_job",
        require_witness_lease=settings.writer_witness_required,
    ):
        return [asyncio.create_task(factory()) for _, factory in jobs]


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

            writer_snapshot = await _load_runtime_writer_snapshot()
            writer_eligible_at_start = _writer_snapshot_is_eligible(writer_snapshot)
            jobs = _background_job_factories(writer_snapshot)
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
            tasks = _create_background_tasks(jobs, writer_snapshot)

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
                current_writer_snapshot = await _load_runtime_writer_snapshot()
                if (
                    _writer_snapshot_changed(writer_snapshot, current_writer_snapshot)
                    or _writer_snapshot_is_eligible(current_writer_snapshot)
                    != writer_eligible_at_start
                ):
                    logger.warning(
                        "WebApp writer state changed; restarting background authority set",
                        extra={
                            "event": "background.writer_state.changed",
                            "worker_pid": os.getpid(),
                            "physical_site": RUNTIME_IDENTITY.physical_site,
                            "previous_epoch": getattr(writer_snapshot, "writer_epoch", None),
                            "current_epoch": getattr(current_writer_snapshot, "writer_epoch", None),
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


async def _run_authorized_startup_mutations(
    writer_snapshot: WriterStateSnapshot | None,
) -> None:
    if writer_snapshot is not None:
        active, reasons = snapshot_is_local_active(
            RUNTIME_IDENTITY,
            writer_snapshot,
            require_witness_lease=settings.writer_witness_required,
        )
        if not active:
            logger.warning(
                "Skipping startup mutations because this WebApp site is not the active writer",
                extra={
                    "event": "startup.mutations.fenced",
                    "physical_site": RUNTIME_IDENTITY.physical_site,
                    "writer_epoch": writer_snapshot.writer_epoch,
                    "reasons": list(reasons),
                },
            )
            return

    async with AsyncSessionLocal() as session:
        try:
            if writer_snapshot is None:
                await ensure_mandatory_channel_rollout(session)
                await session.commit()
            else:
                with writer_fence_scope(
                    RUNTIME_IDENTITY,
                    writer_snapshot,
                    source="startup_mutation",
                    require_witness_lease=settings.writer_witness_required,
                ):
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting up...")
    validate_otp_delivery_runtime_settings(settings)
    if settings.invitation_contract_v2_enabled:
        public_webapp_url_for_links()
    await init_db()
    from core.db import verify_three_site_database_role_bindings

    await verify_three_site_database_role_bindings()
    redis_client = await init_redis()
    setup_event_listeners()
    writer_snapshot = await _load_runtime_writer_snapshot()
    await _run_authorized_startup_mutations(writer_snapshot)
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


@app.middleware("http")
async def enforce_webapp_writer_fence(request: Request, call_next):
    if (
        settings.three_site_dr_enabled
        and RUNTIME_IDENTITY.is_webapp_authority
        and any(_path_matches_prefix(request.url.path, prefix) for prefix in WRITER_FENCE_PROJECTION_PREFIXES)
    ):
        try:
            with projection_fence_scope(source="legacy_sync_receive"):
                return await call_next(request)
        except WriterFenceError:
            logger.exception(
                "Blocked sync projection outside its closed table/field capability",
                extra={"event": "writer.projection.rejected", "path": request.url.path},
            )
            return _writer_fenced_response(("projection_capability_rejected",))
    if not _request_requires_webapp_writer(request):
        return await call_next(request)
    try:
        writer_snapshot = await _load_runtime_writer_snapshot()
    except (WriterControlError, RuntimeError) as exc:
        logger.error(
            "Writer preflight could not load durable state",
            extra={
                "event": "writer.preflight.unavailable",
                "physical_site": RUNTIME_IDENTITY.physical_site,
                "error_type": type(exc).__name__,
            },
        )
        return _writer_fenced_response(("writer_state_unavailable",))
    if writer_snapshot is None:
        return _writer_fenced_response(("writer_state_missing",))
    active, reasons = snapshot_is_local_active(
        RUNTIME_IDENTITY,
        writer_snapshot,
        require_witness_lease=settings.writer_witness_required,
    )
    if not active:
        logger.warning(
            "Blocked mutation on a non-writer WebApp origin",
            extra={
                "event": "writer.preflight.rejected",
                "path": request.url.path,
                "method": request.method,
                "physical_site": RUNTIME_IDENTITY.physical_site,
                "writer_epoch": writer_snapshot.writer_epoch,
                "reasons": list(reasons),
            },
        )
        return _writer_fenced_response(reasons)
    try:
        with writer_fence_scope(
            RUNTIME_IDENTITY,
            writer_snapshot,
            source="http_request",
            require_witness_lease=settings.writer_witness_required,
        ):
            return await call_next(request)
    except WriterFenceError as exc:
        logger.warning(
            "Blocked mutation whose writer term changed before commit",
            extra={
                "event": "writer.commit.rejected",
                "path": request.url.path,
                "method": request.method,
                "physical_site": RUNTIME_IDENTITY.physical_site,
                "writer_epoch": writer_snapshot.writer_epoch,
                "error_type": type(exc).__name__,
            },
        )
        return _writer_fenced_response(("writer_term_changed",))

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
if not (settings.three_site_dr_enabled and settings.dr_event_protocol_strict):
    # Strict three-site ingress lives in the projection-only dr_receiver_app.
    # Keeping it out of the product API prevents an API compromise from
    # acquiring projection credentials.
    api_router.include_router(dr_sync.router, prefix="/dr-sync", tags=["DR Sync"])
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


async def _local_dependency_health(db: AsyncSession) -> tuple[bool, bool, tuple[str, ...]]:
    reasons: list[str] = []
    database_ok = False
    redis_ok = False
    try:
        await db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        reasons.append("database_unavailable")
    try:
        redis_client = get_redis_client()
        redis_ok = bool(await redis_client.ping())
        if not redis_ok:
            reasons.append("redis_unavailable")
    except Exception:
        reasons.append("redis_unavailable")
    return database_ok, redis_ok, tuple(dict.fromkeys(reasons))


async def _three_site_origin_readiness_reasons(
    db: AsyncSession,
    *,
    writer_epoch: int | None,
    require_global_convergence: bool,
    manifest_kind: str = "origin",
) -> tuple[str, ...]:
    if not settings.three_site_dr_enabled:
        return ()
    reasons: list[str] = []
    if not settings.dr_event_protocol_enabled or not settings.dr_event_protocol_strict:
        reasons.append("dr_event_protocol_not_strict")
    if settings.dark_standby_mode:
        reasons.append("dark_standby_mode_active")
    try:
        unresolved_conflicts = int(
            await db.scalar(text("SELECT count(*) FROM dr_conflict_quarantine WHERE resolved_at IS NULL"))
            or 0
        )
        if unresolved_conflicts:
            reasons.append("dr_conflicts_unresolved")
        unapplied = int(
            await db.scalar(
                text(
                    "SELECT count(*) FROM dr_stream_checkpoints "
                    "WHERE destination_site = :site "
                    "AND contiguous_applied_sequence <> contiguous_received_sequence"
                ),
                {"site": RUNTIME_IDENTITY.physical_site},
            )
            or 0
        )
        if unapplied:
            reasons.append("dr_projection_checkpoint_incomplete")
        blocked_receipts = int(
            await db.scalar(
                text(
                    "SELECT count(*) FROM dr_event_receipts WHERE destination_site = :site "
                    "AND status IN ('blocked_gap', 'quarantined')"
                ),
                {"site": RUNTIME_IDENTITY.physical_site},
            )
            or 0
        )
        if blocked_receipts:
            reasons.append("dr_receipt_gap_or_quarantine")
        ambiguous_effects = int(
            await db.scalar(
                text(
                    "SELECT count(*) FROM dr_effect_outbox WHERE executor_site = :site "
                    "AND status = 'ambiguous'"
                ),
                {"site": RUNTIME_IDENTITY.physical_site},
            )
            or 0
        )
        if ambiguous_effects:
            reasons.append("dr_effects_ambiguous")
        if require_global_convergence:
            undelivered = int(
                await db.scalar(
                    text(
                        "SELECT count(*) FROM dr_event_deliveries "
                        "WHERE status <> 'acknowledged'"
                    )
                )
                or 0
            )
            if undelivered:
                reasons.append("dr_destination_delivery_incomplete")
        from core.dr_blob_plane import DrBlobPlaneError, assert_blob_promotion_ready

        try:
            await assert_blob_promotion_ready(db)
        except DrBlobPlaneError:
            reasons.append("dr_blob_parity_incomplete")
        if settings.origin_readiness_require_recovery_manifest:
            from core.dr_blob_plane import current_verified_recovery_manifest_exists

            manifest_current = await current_verified_recovery_manifest_exists(
                db,
                physical_site=RUNTIME_IDENTITY.physical_site,
                writer_epoch=int(writer_epoch or 0),
                release_sha=str(settings.release_sha or ""),
                manifest_kind=manifest_kind,
            )
            if not manifest_current:
                reasons.append("dr_recovery_manifest_missing_or_stale")
    except Exception:
        reasons.append("dr_readiness_evidence_unavailable")
    return tuple(dict.fromkeys(reasons))


def _origin_readiness_request_allowed(request: Request) -> bool:
    configured_key = getattr(settings, "origin_readiness_api_key", None)
    supplied_key = request.headers.get("X-Origin-Readiness-Key")
    if constant_time_secret_equals(supplied_key, configured_key):
        return True
    return _is_loopback_client(request.client.host if request.client else None)


@app.get("/health/live")
async def get_health_live():
    return {
        "status": "ok",
        "physical_site": RUNTIME_IDENTITY.physical_site,
        "logical_authority": RUNTIME_IDENTITY.logical_authority,
    }


@app.get("/health/ready")
async def get_health_ready(db: AsyncSession = Depends(get_db)):
    database_ok, redis_ok, reasons = await _local_dependency_health(db)
    payload = {
        "ready": not reasons,
        "database_ok": database_ok,
        "redis_ok": redis_ok,
        "physical_site": RUNTIME_IDENTITY.physical_site,
        "reasons": list(reasons),
    }
    if reasons:
        return JSONResponse(payload, status_code=503, headers={"Cache-Control": "no-store"})
    return payload


@app.get("/health/sync")
async def get_health_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await sync.get_sync_health(request=request, db=db)


@app.get("/health/origin-ready")
async def get_health_origin_ready(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not _origin_readiness_request_allowed(request):
        raise HTTPException(status_code=404, detail="Not found")

    reasons: list[str] = []
    if not settings.writer_witness_required:
        reasons.append("writer_witness_not_enforced")
    elif not witness_public_key_is_valid(settings.writer_witness_public_key):
        reasons.append("writer_witness_public_key_invalid")
    if settings.writer_witness_required and not settings.writer_witness_auto_renew_enabled:
        reasons.append("writer_witness_auto_renew_disabled")
    if settings.writer_witness_required:
        reasons.extend(writer_witness_client_configuration_reasons(RUNTIME_IDENTITY))
    database_ok, redis_ok, dependency_reasons = await _local_dependency_health(db)
    reasons.extend(dependency_reasons)
    try:
        writer_snapshot = await load_writer_snapshot(db)
        active, writer_reasons = snapshot_is_local_active(
            RUNTIME_IDENTITY,
            writer_snapshot,
            require_readiness_evidence=True,
            require_witness_lease=True,
        )
        if not active:
            reasons.extend(writer_reasons)
    except WriterControlError:
        writer_snapshot = None
        reasons.append("writer_state_unavailable")

    release_sha = str(getattr(settings, "release_sha", None) or "").strip()
    if not GIT_RELEASE_SHA_RE.fullmatch(release_sha.lower()):
        reasons.append("release_sha_invalid")

    expected_revision = str(
        getattr(settings, "origin_expected_migration_revision", None) or ""
    ).strip()
    current_revision = None
    if not expected_revision:
        reasons.append("expected_migration_revision_missing")
    elif database_ok:
        try:
            current_revision = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            if current_revision != expected_revision:
                reasons.append("migration_revision_mismatch")
        except Exception:
            reasons.append("migration_revision_unavailable")

    if not settings.background_jobs_enabled:
        reasons.append("background_jobs_disabled")
    if not (Path("mini_app_dist") / "index.html").is_file():
        reasons.append("frontend_assets_missing")

    require_global_convergence = request.query_params.get("require_global_convergence") == "true"
    reasons.extend(
        await _three_site_origin_readiness_reasons(
            db,
            writer_epoch=writer_snapshot.writer_epoch if writer_snapshot is not None else None,
            require_global_convergence=require_global_convergence,
        )
    )
    reasons = list(dict.fromkeys(reasons))
    payload = {
        "origin_ready": not reasons,
        "physical_site": RUNTIME_IDENTITY.physical_site,
        "logical_authority": RUNTIME_IDENTITY.logical_authority,
        "runtime_role": (
            writer_snapshot.local_runtime_role(RUNTIME_IDENTITY.physical_site)
            if writer_snapshot is not None
            else "fenced"
        ),
        "writer_epoch": writer_snapshot.writer_epoch if writer_snapshot is not None else None,
        "transition_id": writer_snapshot.transition_id if writer_snapshot is not None else None,
        "witness_lease_id": (
            writer_snapshot.witness_lease_id if writer_snapshot is not None else None
        ),
        "witness_lease_expires_at": (
            writer_snapshot.witness_lease_expires_at.isoformat()
            if writer_snapshot is not None
            and writer_snapshot.witness_lease_expires_at is not None
            else None
        ),
        "readiness_evidence_id": (
            writer_snapshot.readiness_evidence_id if writer_snapshot is not None else None
        ),
        "release_sha": release_sha or None,
        "migration_revision": current_revision,
        "database_ok": database_ok,
        "redis_ok": redis_ok,
        "global_convergence_required": require_global_convergence,
        "reasons": reasons,
    }
    if reasons:
        return JSONResponse(payload, status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse(payload, status_code=200, headers={"Cache-Control": "no-store"})


@app.get("/health/promotion-ready")
async def get_health_promotion_ready(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Prove a fenced standby is ready for exactly the next Writer epoch."""

    if not _origin_readiness_request_allowed(request):
        raise HTTPException(status_code=404, detail="Not found")
    reasons: list[str] = []
    action = str(request.query_params.get("action") or "")
    if action not in {"promote_ir", "failback_fi"}:
        reasons.append("promotion_action_invalid")
    expected_target = "webapp_ir" if action == "promote_ir" else "webapp_fi"
    if action in {"promote_ir", "failback_fi"} and RUNTIME_IDENTITY.physical_site != expected_target:
        reasons.append("promotion_action_target_mismatch")
    try:
        expected_epoch = int(request.query_params.get("expected_writer_epoch", ""))
        if expected_epoch < 2:
            raise ValueError
    except (TypeError, ValueError):
        expected_epoch = 0
        reasons.append("expected_writer_epoch_invalid")

    if not settings.three_site_dr_enabled:
        reasons.append("three_site_dr_disabled")
    if not settings.writer_witness_required:
        reasons.append("writer_witness_not_enforced")
    elif not witness_public_key_is_valid(settings.writer_witness_public_key):
        reasons.append("writer_witness_public_key_invalid")
    if settings.writer_witness_required:
        reasons.extend(writer_witness_client_configuration_reasons(RUNTIME_IDENTITY))

    database_ok, redis_ok, dependency_reasons = await _local_dependency_health(db)
    reasons.extend(dependency_reasons)
    try:
        writer_snapshot = await load_writer_snapshot(db)
        if writer_snapshot.control_state != "fenced" or writer_snapshot.active_site is not None:
            reasons.append("promotion_target_not_locally_fenced")
        if expected_epoch and expected_epoch != writer_snapshot.writer_epoch + 1:
            reasons.append("promotion_epoch_not_exact_next")
    except WriterControlError:
        writer_snapshot = None
        reasons.append("writer_state_unavailable")

    release_sha = str(getattr(settings, "release_sha", None) or "").strip().lower()
    if not GIT_RELEASE_SHA_RE.fullmatch(release_sha):
        reasons.append("release_sha_invalid")
    expected_revision = str(
        getattr(settings, "origin_expected_migration_revision", None) or ""
    ).strip()
    current_revision = None
    if not expected_revision:
        reasons.append("expected_migration_revision_missing")
    elif database_ok:
        try:
            current_revision = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            if current_revision != expected_revision:
                reasons.append("migration_revision_mismatch")
        except Exception:
            reasons.append("migration_revision_unavailable")
    if not settings.background_jobs_enabled:
        reasons.append("background_jobs_disabled")
    if not (Path("mini_app_dist") / "index.html").is_file():
        reasons.append("frontend_assets_missing")
    reasons.extend(
        await _three_site_origin_readiness_reasons(
            db,
            writer_epoch=expected_epoch,
            require_global_convergence=action == "failback_fi",
            manifest_kind="promotion",
        )
    )
    if action == "failback_fi":
        expected_database_hash = str(
            request.query_params.get("expected_database_fingerprint_hash") or ""
        )
        expected_blob_hash = str(request.query_params.get("expected_blob_set_hash") or "")
        try:
            expected_database_rows = int(
                request.query_params.get("expected_database_row_count", "")
            )
            expected_blob_count = int(request.query_params.get("expected_blob_count", ""))
            if expected_database_rows < 0 or expected_blob_count < 0:
                raise ValueError
        except (TypeError, ValueError):
            expected_database_rows = -1
            expected_blob_count = -1
            reasons.append("failback_origin_counts_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_database_hash) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_blob_hash
        ):
            reasons.append("failback_origin_hashes_invalid")
        if (
            expected_epoch
            and expected_database_rows >= 0
            and expected_blob_count >= 0
            and re.fullmatch(r"[0-9a-f]{64}", expected_database_hash)
            and re.fullmatch(r"[0-9a-f]{64}", expected_blob_hash)
        ):
            try:
                barrier = (
                    await db.execute(
                        text(
                            "SELECT database_fingerprint_hash, database_row_count, "
                            "blob_set_hash, blob_count FROM dr_recovery_manifests "
                            "WHERE manifest_kind = 'promotion' AND physical_site = :site "
                            "AND writer_epoch = :epoch AND release_sha = :release_sha "
                            "AND status = 'verified' ORDER BY verified_at DESC LIMIT 1"
                        ),
                        {
                            "site": RUNTIME_IDENTITY.physical_site,
                            "epoch": expected_epoch,
                            "release_sha": release_sha,
                        },
                    )
                ).mappings().one_or_none()
                if barrier is None or (
                    barrier["database_fingerprint_hash"] != expected_database_hash
                    or int(barrier["database_row_count"]) != expected_database_rows
                    or barrier["blob_set_hash"] != expected_blob_hash
                    or int(barrier["blob_count"]) != expected_blob_count
                ):
                    reasons.append("failback_origin_target_parity_mismatch")
            except Exception:
                reasons.append("failback_origin_target_parity_unavailable")
    reasons = list(dict.fromkeys(reasons))
    evidence = None
    evidence_hash = None
    if not reasons:
        now = datetime.now(timezone.utc)
        lifetime = max(1, int(settings.origin_readiness_max_evidence_age_seconds))
        evidence = {
            "evidence_id": str(uuid.uuid4()),
            "target_site": RUNTIME_IDENTITY.physical_site,
            "writer_epoch": expected_epoch,
            "action": action,
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=lifetime)).isoformat(),
            "schema_compatible": True,
            "release_compatible": True,
            "database_ready": True,
            "storage_ready": True,
            "sync_checkpoint_ready": True,
            "no_critical_conflicts": True,
            "background_jobs_ready": True,
            "fencing_acknowledged": True,
        }
        evidence_hash = validate_readiness_evidence(
            evidence,
            target_site=RUNTIME_IDENTITY.physical_site,
            writer_epoch=expected_epoch,
            now=now,
        ).content_hash
    payload = {
        "promotion_ready": not reasons,
        "physical_site": RUNTIME_IDENTITY.physical_site,
        "writer_epoch": writer_snapshot.writer_epoch if writer_snapshot is not None else None,
        "expected_writer_epoch": expected_epoch or None,
        "action": action or None,
        "release_sha": release_sha or None,
        "migration_revision": current_revision,
        "database_ok": database_ok,
        "redis_ok": redis_ok,
        "readiness_evidence": evidence,
        "readiness_hash": evidence_hash,
        "reasons": reasons,
    }
    return JSONResponse(
        payload,
        status_code=200 if not reasons else 503,
        headers={"Cache-Control": "no-store"},
    )


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
