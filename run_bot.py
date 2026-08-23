import asyncio
import logging
import sys
import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from core.config import settings
from core.sms import validate_non_iran_sms_isolation
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
    offer_overtime_preference,
    offer_overtime_callbacks,
    default
)
from bot.handlers.telegram_publisher_b2b import (
    build_primary_b2b_router,
    build_publisher_b2b_router,
)
from bot.handlers.telegram_publisher_channel_callbacks import (
    build_publisher_channel_callback_router,
)
from core.db import init_db, AsyncSessionLocal
from core.events import setup_event_listeners
from bot.middlewares import (
    AuthMiddleware,
    CallbackReceiptMiddleware,
    StaleNavigationHandoffMiddleware,
    TradeContentionGateMiddleware,
)
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.middlewares.telegram_bot_identity import TelegramBotIdentityMiddleware
from bot.telegram_command_menu import configure_interactive_bot_command_menu
from bot.utils.trade_suggestion_messages import listen_trade_suggestion_events
from core.logging_config import configure_logging
from core.offer_publication_worker import offer_telegram_publication_loop
from core.telegram_admin_broadcast_worker import telegram_admin_broadcast_delivery_loop
from core.telegram_notification_outbox_worker import telegram_notification_outbox_delivery_loop
from core.telegram_market_notice_worker import telegram_market_notice_delivery_loop
from core.trade_delivery_worker import telegram_trade_delivery_loop
from core.telegram_delivery_queue_worker import telegram_delivery_queue_loop
from core.telegram_delivery_queue_limiter import (
    configured_redis_telegram_delivery_limiter,
)
from core.telegram_delivery_runtime_composition import (
    build_configured_telegram_delivery_runtime,
)
from core.services.telegram_otp_ephemeral_queue import (
    configured_telegram_otp_ephemeral_worker_factory,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.services.telegram_publisher_dispatch_service import (
    run_co_located_telegram_publisher_dispatch_cycle,
)
from core.metrics import registry as metrics_registry
from core.telegram_gateway import aclose_telegram_http_client
from core.telegram_bot_runtime_role import (
    TelegramBotRuntimeRoleError,
    assert_telegram_bot_runtime_role_compatible,
    assert_telegram_bot_runtime_role_plans_are_disjoint,
    resolve_telegram_bot_runtime_role,
    role_owns_otp_worker,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    role_owns_queue_executor,
    select_queue_execution_bot_identities,
)
from core.telegram_bot_runtime_topology import describe_telegram_bot_runtime_topology
from core.telegram_central_poller_owner import (
    TelegramCentralPollerAlreadyOwnedError,
    acquire_telegram_central_poller_owner,
)
from core.telegram_delivery_queue_owner import (
    TelegramDeliveryQueueAlreadyOwnedError,
    acquire_telegram_delivery_queue_owner,
    telegram_delivery_queue_owner_is_held,
)
from core.utils import utc_now

# Configure logging
configure_logging("bot")
logger = logging.getLogger(__name__)


class BotRuntimeSurfaceError(RuntimeError):
    """Raised when the Telegram bot entrypoint is started on a forbidden surface."""


class BotRuntimeTaskError(RuntimeError):
    """Raised when a required Bot child task exits before polling shuts down."""


def configured_publisher_b2b_lane_ids(settings_obj):
    """Return configured publisher bot ids when B2B dispatch is fully enabled."""
    if not (
        bool(getattr(settings_obj, "telegram_multi_publisher_enabled", False))
        and bool(getattr(settings_obj, "telegram_b2b_dispatch_enabled", False))
    ):
        return {}
    composition = build_configured_telegram_delivery_runtime(settings=settings_obj)
    return {
        identity: lane.expected_bot_id
        for identity, lane in composition.credential_registry.publisher_lanes.items()
    }


def configured_publisher_b2b_pollers(settings_obj):
    """Build isolated publisher pollers only for the all-or-nothing B2B mode."""
    publisher_ids = configured_publisher_b2b_lane_ids(settings_obj)
    if not publisher_ids:
        return (), {}, ()
    composition = build_configured_telegram_delivery_runtime(settings=settings_obj)
    primary_id = getattr(settings_obj, "telegram_delivery_queue_expected_primary_bot_id", None)
    if isinstance(primary_id, bool) or not isinstance(primary_id, int) or primary_id <= 0:
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_b2b_expected_primary_bot_id_missing"
        )
    pollers = []
    bots = []
    for identity, lane in composition.credential_registry.publisher_lanes.items():
        publisher_bot = Bot(token=lane.credential.token)
        publisher_dp = Dispatcher()
        publisher_dp.update.outer_middleware(TelegramBotIdentityMiddleware(identity))
        publisher_dp.include_router(
            build_publisher_b2b_router(
                identity=identity,
                expected_primary_bot_id=primary_id,
            )
        )
        publisher_dp.include_router(build_publisher_channel_callback_router())
        bots.append(publisher_bot)
        pollers.append(publisher_dp.start_polling(publisher_bot))
    return tuple(pollers), publisher_ids, tuple(bots)


async def supervise_pollers(*pollers) -> None:
    """Keep every polling surface alive as one required runtime task."""
    await asyncio.gather(*pollers)


def configured_publisher_dispatch_worker_factory(
    settings_obj,
    *,
    publisher_bot_ids=None,
):
    """Return the fail-closed central outbox dispatcher, or no worker."""
    if not (
        bool(getattr(settings_obj, "telegram_multi_publisher_enabled", False))
        and bool(getattr(settings_obj, "telegram_b2b_dispatch_enabled", False))
    ):
        return None
    composition = build_configured_telegram_delivery_runtime(settings=settings_obj)
    if publisher_bot_ids is None:
        publisher_bot_ids = {
            identity: lane.expected_bot_id
            for identity, lane in composition.credential_registry.publisher_lanes.items()
        }
    expected_publishers = set(composition.credential_registry.publisher_lanes)
    if set(publisher_bot_ids) != expected_publishers:
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_b2b_co_located_publisher_set_mismatch"
        )

    async def run_dispatcher() -> None:
        while True:
            cycle_started_at = asyncio.get_running_loop().time()
            batch_limit = max(
                1,
                int(getattr(settings_obj, "telegram_b2b_dispatch_batch_size", 8)),
            )
            report = await run_co_located_telegram_publisher_dispatch_cycle(
                session_factory=AsyncSessionLocal,
                current_server=SERVER_FOREIGN,
                limit=batch_limit,
                lease_seconds=float(getattr(settings_obj, "telegram_delivery_queue_worker_lease_seconds", 30.0)),
                now_factory=utc_now,
            )
            metrics_registry.counter(
                "telegram_publisher_b2b_dispatch_cycles_total",
                "Completed durable Telegram publisher B2B dispatch cycles.",
                result="sent" if report.sent_count else "idle",
            )
            metrics_registry.gauge(
                "telegram_publisher_b2b_dispatch_cycle_commands",
                "Commands claimed in the latest Telegram publisher B2B cycle.",
                report.claimed_count,
            )
            interval = float(
                getattr(settings_obj, "telegram_b2b_dispatch_interval_seconds", 0.5)
            )
            await asyncio.sleep(
                publisher_b2b_dispatch_cycle_sleep_seconds(
                    interval_seconds=interval,
                    claimed_count=report.claimed_count,
                    elapsed_seconds=(
                        asyncio.get_running_loop().time() - cycle_started_at
                    ),
                    batch_limit=batch_limit,
                )
            )

    return run_dispatcher


def publisher_b2b_dispatch_cycle_sleep_seconds(
    *,
    interval_seconds: float,
    claimed_count: int,
    elapsed_seconds: float,
    batch_limit: int = 1,
) -> float:
    """Keep idle B2B cadence, but drain immediately after a full batch."""
    interval = max(0.0, float(interval_seconds))
    elapsed = max(0.0, float(elapsed_seconds))
    claimed = max(0, int(claimed_count))
    limit = max(1, int(batch_limit))
    if claimed >= limit:
        return 0.0
    return max(0.0, interval - elapsed)


async def supervise_bot_runtime(
    *,
    polling_coro,
    child_coroutines,
) -> None:
    """Fail the process if any required worker stops unexpectedly."""

    polling_task = asyncio.create_task(polling_coro, name="telegram-polling")
    child_tasks = [
        asyncio.create_task(coro, name=f"bot-child-{index}")
        for index, coro in enumerate(child_coroutines, start=1)
    ]
    runtime_tasks = [polling_task, *child_tasks]
    try:
        done, _ = await asyncio.wait(
            runtime_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if polling_task in done:
            await polling_task
            return
        failed_task = next(task for task in child_tasks if task in done)
        try:
            await failed_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BotRuntimeTaskError(
                f"required Bot child task failed: {failed_task.get_name()}"
            ) from exc
        raise BotRuntimeTaskError(
            f"required Bot child task exited unexpectedly: {failed_task.get_name()}"
        )
    finally:
        for task in runtime_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*runtime_tasks, return_exceptions=True)


def configured_telegram_delivery_queue_worker_factory(
    settings_obj,
    *,
    runtime_role=None,
    process_owner_lease=None,
):
    """Return a zero-argument queue runner with all production dependencies bound."""

    role = resolve_telegram_bot_runtime_role(settings_obj, role=runtime_role)
    if not role_owns_queue_executor(role):
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_bot_runtime_primary_must_not_own_queue"
        )
    composition = build_configured_telegram_delivery_runtime(settings=settings_obj)
    owned_identities = select_queue_execution_bot_identities(
        role, composition.bot_identities
    )
    if not owned_identities:
        raise TelegramDeliveryRuntimeConfigurationError(
            "telegram_bot_runtime_role_has_no_queue_lanes"
        )
    owned_validators = {
        identity: validator
        for identity, validator in composition.freshness_validators.items()
        if identity in owned_identities
    }
    owned_feedbacks = {
        identity: feedback
        for identity, feedback in composition.lifecycle_feedbacks.items()
        if identity in owned_identities
    }

    async def run_configured_telegram_delivery_queue() -> None:
        redis_client = redis.Redis.from_url(
            str(getattr(settings_obj, "redis_url", "") or ""),
            decode_responses=True,
        )
        limiter = configured_redis_telegram_delivery_limiter(
            redis_client,
            settings=settings_obj,
        )
        try:
            await telegram_delivery_queue_loop(
                freshness_validators=owned_validators,
                lifecycle_feedbacks=owned_feedbacks,
                credential_registry=composition.credential_registry,
                dispatch_limiter=limiter,
                bot_identities=owned_identities,
                process_owner_lease=process_owner_lease,
            )
        finally:
            await redis_client.aclose()

    return run_configured_telegram_delivery_queue


def telegram_execution_worker_factories(
    runtime: TelegramDeliveryRuntimeDecision,
    *,
    settings_obj=settings,
    runtime_role=None,
    process_owner_lease=None,
):
    """Return exactly one ownership set without creating coroutine objects."""
    role = assert_telegram_bot_runtime_role_compatible(
        settings_obj=settings_obj,
        runtime=runtime,
        role=runtime_role,
    )
    if runtime.mode == TelegramDeliveryRuntimeMode.LEGACY:
        if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
            raise TelegramDeliveryRuntimeConfigurationError(
                "inconsistent_legacy_runtime_decision"
            )
        return (
            offer_telegram_publication_loop,
            telegram_trade_delivery_loop,
            telegram_admin_broadcast_delivery_loop,
            telegram_notification_outbox_delivery_loop,
            telegram_market_notice_delivery_loop,
        )
    if runtime.mode == TelegramDeliveryRuntimeMode.QUEUE_V1:
        if runtime.legacy_workers_enabled or not runtime.queue_worker_enabled:
            raise TelegramDeliveryRuntimeConfigurationError(
                "inconsistent_queue_runtime_decision"
            )
        if not role_owns_queue_executor(role):
            return ()
        workers = [
            configured_telegram_delivery_queue_worker_factory(
                settings_obj,
                runtime_role=role,
                process_owner_lease=process_owner_lease,
            )
        ]
        if role_owns_otp_worker(role):
            workers.append(
                configured_telegram_otp_ephemeral_worker_factory(settings_obj)
            )
        return tuple(workers)
    raise TelegramDeliveryRuntimeConfigurationError("unknown_runtime_decision_mode")


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
    try:
        resolve_telegram_bot_runtime_role(settings)
        assert_telegram_bot_runtime_role_plans_are_disjoint()
    except TelegramBotRuntimeRoleError as exc:
        reasons.append(str(exc))

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


async def main():
    assert_bot_runtime_surface()
    validate_non_iran_sms_isolation(settings)
    telegram_runtime = configured_telegram_delivery_runtime()
    try:
        runtime_role = assert_telegram_bot_runtime_role_compatible(
            settings_obj=settings,
            runtime=telegram_runtime,
        )
    except TelegramBotRuntimeRoleError as exc:
        raise BotRuntimeSurfaceError(str(exc)) from exc
    start_primary = role_owns_primary_surface(runtime_role)
    start_publishers = role_owns_publisher_surface(runtime_role)
    owns_queue = (
        role_owns_queue_executor(runtime_role)
        and telegram_runtime.mode == TelegramDeliveryRuntimeMode.QUEUE_V1
    )

    # Initialize Database
    await init_db()

    # Register SQLAlchemy event listeners for sync & realtime events
    setup_event_listeners()

    queue_owner_lease = None
    central_poller_lease = None
    queue_lease_handed_off = False
    bot = None
    dp = None
    publisher_bots = ()
    try:
        if owns_queue:
            try:
                queue_owner_lease = await acquire_telegram_delivery_queue_owner()
            except TelegramDeliveryQueueAlreadyOwnedError as exc:
                raise BotRuntimeSurfaceError(str(exc)) from exc
        if start_primary:
            try:
                central_poller_lease = await acquire_telegram_central_poller_owner()
            except TelegramCentralPollerAlreadyOwnedError as exc:
                raise BotRuntimeSurfaceError(str(exc)) from exc

        split_enabled = bool(getattr(settings, "telegram_bot_split_enabled", False))
        if owns_queue:
            queue_owner_present = True
        elif split_enabled and start_primary:
            queue_owner_present = await telegram_delivery_queue_owner_is_held()
        else:
            queue_owner_present = None
        topology = describe_telegram_bot_runtime_topology(
            role=runtime_role,
            split_enabled=split_enabled,
            queue_owner_present=queue_owner_present,
        )
        logger.info(
            "Telegram bot runtime topology",
            extra={
                "event": "bot.runtime_topology",
                **topology.as_dict(),
            },
        )

        if start_primary:
            bot = Bot(token=settings.bot_token)
            storage = RedisStorage.from_url(settings.redis_url)
            dp = Dispatcher(
                storage=storage,
                events_isolation=storage.create_isolation(lock_kwargs={"timeout": 120}),
            )

            # Capture the callback deadline origin before Auth or any other DB work.
            dp.update.outer_middleware(CallbackReceiptMiddleware())

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
            dp.include_router(offer_overtime_preference.router)
            dp.include_router(offer_overtime_callbacks.router)

        publisher_pollers, publisher_bot_ids, publisher_bots = (
            configured_publisher_b2b_pollers(settings)
            if start_publishers
            else ((), configured_publisher_b2b_lane_ids(settings), ())
        )
        if start_primary and publisher_bot_ids:
            dp.include_router(build_primary_b2b_router(publisher_bot_ids=publisher_bot_ids))
        publisher_dispatcher = (
            configured_publisher_dispatch_worker_factory(
                settings,
                publisher_bot_ids=publisher_bot_ids,
            )
            if start_primary
            else None
        )
        if start_primary:
            # Default router should be last
            dp.include_router(default.router)

        polling = []
        if start_primary:
            polling.append(dp.start_polling(bot))
        polling.extend(publisher_pollers)
        if not polling:
            raise BotRuntimeSurfaceError("telegram_bot_runtime_role_has_no_polling_surface")

        child_coroutines = []
        if start_primary:
            child_coroutines.append(listen_trade_suggestion_events(bot))
            if publisher_dispatcher is not None:
                child_coroutines.append(publisher_dispatcher())
            await configure_interactive_bot_command_menu(bot)
        worker_factories = telegram_execution_worker_factories(
            telegram_runtime,
            runtime_role=runtime_role,
            process_owner_lease=queue_owner_lease,
        )
        child_coroutines.extend(worker_factory() for worker_factory in worker_factories)
        if owns_queue:
            queue_lease_handed_off = True
        logger.info("🤖 Bot started...")
        await supervise_bot_runtime(
            polling_coro=supervise_pollers(*polling),
            child_coroutines=child_coroutines,
        )
    finally:
        if bot is not None:
            await bot.session.close()
        await aclose_telegram_http_client()
        await asyncio.gather(
            *(publisher_bot.session.close() for publisher_bot in publisher_bots),
            return_exceptions=True,
        )
        if central_poller_lease is not None:
            await central_poller_lease.close()
        if queue_owner_lease is not None and not queue_lease_handed_off:
            await queue_owner_lease.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BotRuntimeSurfaceError:
        raise SystemExit(78)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
