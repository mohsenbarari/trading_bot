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
    require_external_effect_execution_authorization,
)
from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT,
    EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME,
    ExternalEffectExecutionGateError,
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

# Configure logging
configure_logging("bot")
logger = logging.getLogger(__name__)


class BotRuntimeSurfaceError(RuntimeError):
    """Raised when the Telegram bot entrypoint is started on a forbidden surface."""


# Only provider-visible Bot API methods belong here.  Read-only requests such
# as getUpdates/getMe remain outside this external-effect authorization; the
# method list is deliberately explicit rather than treating every future Bot
# API call as a hidden wildcard policy.
_EFFECTFUL_TELEGRAM_BOT_API_METHODS = frozenset(
    {
        "answerCallbackQuery",
        "answerInlineQuery",
        "approveChatJoinRequest",
        "banChatMember",
        "createChatInviteLink",
        "deleteMessage",
        "declineChatJoinRequest",
        "editMessageCaption",
        "editMessageLiveLocation",
        "editMessageMedia",
        "editMessageReplyMarkup",
        "editMessageText",
        "pinChatMessage",
        "restrictChatMember",
        "sendAnimation",
        "sendAudio",
        "sendChatAction",
        "sendContact",
        "sendDice",
        "sendDocument",
        "sendLocation",
        "sendMediaGroup",
        "sendMessage",
        "sendPhoto",
        "sendPoll",
        "sendSticker",
        "sendVenue",
        "sendVideo",
        "sendVideoNote",
        "sendVoice",
        "stopPoll",
        "unbanChatMember",
        "unpinChatMessage",
    }
)


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
    """Bound the fail-closed bot watchdog beneath the configured margin."""

    try:
        safety_margin = float(settings.application_writer_term_safety_margin_seconds)
    except (TypeError, ValueError):
        safety_margin = 1.0
    return max(0.2, min(1.0, safety_margin / 2.0))


async def _watch_active_writer_term() -> None:
    """Raise as soon as the locally mounted Writer Witness term is invalid."""

    while True:
        require_application_writer_term()
        await asyncio.sleep(_writer_term_watch_interval_seconds())


async def _watch_external_effect_execution_authorization() -> None:
    """Stop the bot when its short-lived no-resend authorization is lost.

    The background workers each recheck their own scope at concrete effect
    boundaries.  This process-level watcher additionally prevents an already
    running bot surface from continuing after the startup/runtime scope expires
    or no longer binds the active Witness term.
    """

    while True:
        # The runtime scope is an existing process-liveness fence.  The
        # narrower Bot API scope is intentionally checked only by the session
        # middleware at a concrete provider-visible method; do not turn a
        # read-only long-poll into an external-effect action.
        require_external_effect_execution_authorization(
            EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME
        )
        await asyncio.sleep(_writer_term_watch_interval_seconds())


def _writer_readiness_request_middleware(marker_path):
    """Mark the bot ready only after a successful real long-poll response.

    ``Dispatcher.start_polling`` runs startup hooks before Telegram auth and
    before its first ``GetUpdates`` request.  A session middleware is the
    earliest truthful point at which the bot has completed that request.
    """

    async def middleware(make_request, bot, method):
        response = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            write_writer_ready_marker(marker_path)
        return response

    return middleware


def _external_effect_request_middleware():
    """Fence concrete provider-visible aiogram Bot API requests.

    This covers direct handler ``message.answer``/edit/delete/callback-answer
    traffic and listener-originated calls that share this Bot session.  It is
    registered whenever either the Writer-term or no-resend gate is explicitly
    enforced; even then read-only Bot API methods are not classified as
    external effects.  The central wrapper revalidates the active Writer term
    even while the optional no-resend gate is dormant.
    """

    async def middleware(make_request, bot, method):
        method_name = str(getattr(method, "__api_method__", "") or "")
        if method_name in _EFFECTFUL_TELEGRAM_BOT_API_METHODS:
            require_external_effect_execution_authorization(
                EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT
            )
        return await make_request(bot, method)

    return middleware


async def main():
    assert_bot_runtime_surface()
    # Default-off compatibility: this is a no-op only when both Writer-term
    # and external-effect enforcement are disabled.  Otherwise refuse before
    # spawning polling or any outbound worker.
    require_external_effect_execution_authorization(
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME
    )
    marker_path = configured_marker_path(
        getattr(settings, "bot_writer_ready_marker_path", None)
    )
    if marker_path is not None:
        # Do not let a marker from an earlier container process survive until
        # the first successful authenticated long-poll response.
        clear_writer_ready_marker(marker_path)
        if require_application_writer_term() is None:
            raise BotWriterReadinessError(
                "bot readiness marker requires enabled Writer Witness enforcement"
            )

    # Initialize Database
    await init_db()

    # Register SQLAlchemy event listeners for sync & realtime events
    setup_event_listeners()

    bot = Bot(token=settings.bot_token)
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(
        storage=storage,
        events_isolation=storage.create_isolation(lock_kwargs={"timeout": 120}),
    )
    if (
        getattr(settings, "external_effect_execution_gate_enforced", False) is not False
        or getattr(settings, "application_writer_term_enforced", False) is not False
    ):
        bot.session.middleware.register(_external_effect_request_middleware())
    if marker_path is not None:
        bot.session.middleware.register(
            _writer_readiness_request_middleware(marker_path)
        )

    # This must precede all Redis/DB-backed bot middleware.
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
    writer_term_watchdog_task = None
    if settings.application_writer_term_enforced:
        writer_term_watchdog_task = asyncio.create_task(_watch_active_writer_term())
    external_effect_gate_watchdog_task = None
    if settings.external_effect_execution_gate_enforced:
        external_effect_gate_watchdog_task = asyncio.create_task(
            _watch_external_effect_execution_authorization()
        )
    polling_task = asyncio.create_task(dp.start_polling(bot))
    try:
        watchdog_tasks = {
            task
            for task in (writer_term_watchdog_task, external_effect_gate_watchdog_task)
            if task is not None
        }
        if not watchdog_tasks:
            await polling_task
        else:
            done, _ = await asyncio.wait(
                {polling_task, *watchdog_tasks},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for watchdog_task in watchdog_tasks:
                if watchdog_task in done:
                    # Do not keep the bot alive after a term or no-resend
                    # authorization loss.  The finally block cancels polling
                    # and every outbound worker immediately.
                    watchdog_task.result()
            polling_task.result()
    except ApplicationWriterTermError:
        logger.critical(
            "Bot stopped because the local writer term is no longer valid",
            extra={"event": "bot.writer_term.runtime_refused"},
        )
        raise
    except ExternalEffectExecutionGateError:
        logger.critical(
            "Bot stopped because its external-effect authorization is no longer valid",
            extra={"event": "bot.external_effect_gate.runtime_refused"},
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
            external_effect_gate_watchdog_task,
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
            *(
                [external_effect_gate_watchdog_task]
                if external_effect_gate_watchdog_task is not None
                else []
            ),
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
    except ExternalEffectExecutionGateError:
        raise SystemExit(77)
    except BotWriterReadinessError:
        raise SystemExit(76)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
