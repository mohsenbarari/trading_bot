import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.methods import GetUpdates
from core.config import settings
from core.server_routing import SERVER_FOREIGN, normalize_server
from bot.handlers import (
    start, 
    panel, 
    trade_create, 
    trade_execute, 
    trade_manage,
    trade_history,
    admin,
    admin_broadcast,
    admin_commodities,
    admin_users,
    commodity_catalog,
    block_manage,
    link_account, # 👈 Added
    default
)
from core.application_writer_term import ApplicationWriterTermError
from core.db import (
    init_db,
    AsyncSessionLocal,
    require_application_writer_term,
    validate_application_writer_term_runtime_settings,
)
from core.events import setup_event_listeners
from bot.middlewares import (
    AuthMiddleware,
    StaleNavigationHandoffMiddleware,
    TradeContentionGateMiddleware,
    WriterTermMiddleware,
)
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.writer_readiness import (
    BotWriterReadinessError,
    clear_writer_ready_marker,
    configured_marker_path,
    write_writer_ready_marker,
)
from bot.utils.trade_suggestion_messages import listen_trade_suggestion_events
from core.logging_config import configure_logging
from core.offer_publication_worker import offer_telegram_publication_loop
from core.telegram_admin_broadcast_worker import telegram_admin_broadcast_delivery_loop
from core.telegram_notification_outbox_worker import telegram_notification_outbox_delivery_loop
from core.trade_delivery_worker import telegram_trade_delivery_loop

logger = logging.getLogger(__name__)


class BotRuntimeSurfaceError(RuntimeError):
    """Raised when the Telegram bot entrypoint is started on a forbidden surface."""


def _configured_service_name() -> str:
    return str(getattr(settings, "trading_bot_service", "") or "").strip().lower()


def assert_bot_runtime_surface() -> None:
    configured_server_mode = normalize_server(getattr(settings, "server_mode", None), default="")
    configured_service = _configured_service_name()
    reasons: list[str] = []

    if configured_server_mode != SERVER_FOREIGN:
        reasons.append("SERVER_MODE must be foreign for Telegram bot runtime")
    if configured_service != "bot":
        reasons.append("TRADING_BOT_SERVICE must be bot for Telegram bot runtime")
    if not settings.bot_token:
        reasons.append("BOT_TOKEN is required for Telegram bot runtime")

    if not reasons:
        return

    logger.critical(
        "Bot runtime surface guard refused startup",
        extra={
            "event": "bot.runtime_surface_refused",
            "configured_server_mode": configured_server_mode or None,
            "configured_service": configured_service or None,
            "telegram_credential_configured": bool(settings.bot_token),
            "reasons": reasons,
        },
    )
    raise BotRuntimeSurfaceError("; ".join(reasons))


def _writer_term_watch_interval_seconds() -> float:
    """Check often enough to stop before a term reaches its safety margin."""

    try:
        margin = float(settings.application_writer_term_safety_margin_seconds)
    except (TypeError, ValueError):
        margin = 1.0
    return max(0.2, min(1.0, margin / 2.0))


async def _watch_active_writer_term() -> None:
    """Raise promptly when the live root-owned term disappears or changes."""

    while True:
        require_application_writer_term()
        await asyncio.sleep(_writer_term_watch_interval_seconds())


def _writer_term_request_middleware():
    """Fence every Telegram API call, including worker-originated calls."""

    async def middleware(make_request, bot, method):
        require_application_writer_term()
        return await make_request(bot, method)

    return middleware


def _writer_readiness_request_middleware(marker_path):
    """Publish readiness only after a successful authenticated long poll."""

    async def middleware(make_request, bot, method):
        response = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            write_writer_ready_marker(marker_path)
        return response

    return middleware


async def main():
    assert_bot_runtime_surface()
    # Validate static invariant and the live Witness term before this process
    # opens its DB, initializes Redis-backed dispatch, or creates a Bot client.
    writer_term_policy = validate_application_writer_term_runtime_settings(
        expected_service="bot"
    )
    active_term = require_application_writer_term()
    # configure_logging can initialize an optional remote error sink.  Keep it
    # after the live term check, alongside the rest of bot process startup.
    configure_logging("bot")
    marker_path = configured_marker_path(
        getattr(settings, "bot_writer_ready_marker_path", None)
    )
    if marker_path is not None:
        if active_term is None:
            raise BotWriterReadinessError(
                "bot readiness marker requires enabled Writer Witness enforcement"
            )
        # Do not retain a readiness signal from a prior process before the
        # first successful GetUpdates response in this process.
        clear_writer_ready_marker(marker_path)

    # init_db performs the same term check before it can import metadata or
    # issue legacy create_all DDL.  A fenced profile has bootstrap disabled.
    await init_db()
    setup_event_listeners()

    bot = Bot(token=settings.bot_token)
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(
        storage=storage,
        events_isolation=storage.create_isolation(lock_kwargs={"timeout": 120}),
    )
    if writer_term_policy.enabled:
        # This also covers provider-visible calls made by background workers;
        # an expired term cannot merely be noticed between two worker loops.
        bot.session.middleware.register(_writer_term_request_middleware())
    if marker_path is not None:
        bot.session.middleware.register(
            _writer_readiness_request_middleware(marker_path)
        )

    # This must precede every Redis/DB-backed bot middleware.
    dp.update.outer_middleware(WriterTermMiddleware())
    # Hot trade callbacks must fail fast before Auth opens a DB session.
    dp.update.outer_middleware(TradeContentionGateMiddleware())

    # Auth: inject user into handler data for ALL updates (must be before routers)
    auth_mw = AuthMiddleware(AsyncSessionLocal)
    dp.update.outer_middleware(auth_mw)
    dp.update.outer_middleware(BotLoggingContextMiddleware())
    dp.update.outer_middleware(StaleNavigationHandoffMiddleware())

    # Include routers
    dp.include_router(start.router)
    dp.include_router(link_account.router) # 👈 Added (high priority)
    dp.include_router(panel.router)
    dp.include_router(trade_create.router)
    dp.include_router(trade_execute.router)
    dp.include_router(trade_manage.router)
    dp.include_router(trade_history.router)
    dp.include_router(commodity_catalog.router)
    dp.include_router(admin.router)
    dp.include_router(admin_broadcast.router)
    dp.include_router(admin_commodities.router)
    dp.include_router(admin_users.router)
    dp.include_router(block_manage.router)
    
    # Default router should be last
    dp.include_router(default.router)

    logger.info("Bot runtime initialized; waiting for the first successful Telegram long poll")
    suggestion_sync_task = asyncio.create_task(listen_trade_suggestion_events(bot))
    offer_publication_task = asyncio.create_task(offer_telegram_publication_loop())
    telegram_delivery_task = asyncio.create_task(telegram_trade_delivery_loop())
    telegram_admin_broadcast_task = asyncio.create_task(telegram_admin_broadcast_delivery_loop())
    telegram_notification_outbox_task = asyncio.create_task(telegram_notification_outbox_delivery_loop())
    writer_term_watchdog_task = (
        asyncio.create_task(_watch_active_writer_term())
        if writer_term_policy.enabled
        else None
    )
    polling_task = asyncio.create_task(dp.start_polling(bot))
    try:
        if writer_term_watchdog_task is None:
            await polling_task
        else:
            done, _pending = await asyncio.wait(
                {polling_task, writer_term_watchdog_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if writer_term_watchdog_task in done:
                # Propagate the fence failure so systemd records a failed
                # process rather than treating a lost writer term as a clean
                # bot shutdown.  The finally block cancels all workers.
                writer_term_watchdog_task.result()
            polling_task.result()
    except ApplicationWriterTermError:
        logger.critical(
            "Bot stopped because the local writer term is no longer valid",
            extra={"event": "bot.writer_term.runtime_refused"},
        )
        raise
    except BotWriterReadinessError:
        logger.critical(
            "Bot stopped because its fenced readiness marker could not be maintained",
            extra={"event": "bot.writer_term.readiness_refused"},
        )
        raise
    except Exception:
        logger.exception("Bot polling failed")
        raise
    finally:
        for task in (
            suggestion_sync_task,
            offer_publication_task,
            telegram_delivery_task,
            telegram_admin_broadcast_task,
            telegram_notification_outbox_task,
            polling_task,
            writer_term_watchdog_task,
        ):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            suggestion_sync_task,
            offer_publication_task,
            telegram_delivery_task,
            telegram_admin_broadcast_task,
            telegram_notification_outbox_task,
            polling_task,
            *([writer_term_watchdog_task] if writer_term_watchdog_task is not None else []),
            return_exceptions=True,
        )
        if marker_path is not None:
            try:
                clear_writer_ready_marker(marker_path)
            except BotWriterReadinessError:
                logger.critical(
                    "Bot readiness marker could not be removed during shutdown",
                    extra={"event": "bot.writer_term.readiness_cleanup_refused"},
                )
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BotRuntimeSurfaceError:
        raise SystemExit(78)
    except ApplicationWriterTermError:
        raise SystemExit(75)
    except BotWriterReadinessError:
        raise SystemExit(76)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
