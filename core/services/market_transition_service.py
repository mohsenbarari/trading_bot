from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import telegram_gateway
from core.config import settings
from core.events import publish_event_sync
from core.server_routing import current_server
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    expire_offers_authoritatively,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.trading_settings import get_trading_settings_async
from core.utils import utc_now
from models.market_runtime_state import MarketRuntimeState
from models.market_schedule_override import MarketScheduleOverride
from models.offer import Offer, OfferStatus

from .market_schedule_service import (
    MarketScheduleEvaluation,
    NEXT_TRANSITION_SEARCH_DAYS,
    evaluate_market_schedule,
    get_market_timezone_name,
)


logger = logging.getLogger(__name__)

MARKET_OPENED_CHANNEL_NOTICE = "🟢 شروع فعالیت بازار"
MARKET_CLOSED_CHANNEL_NOTICE = "🔴 پایان فعالیت بازار"
MARKET_RUNTIME_ADVISORY_LOCK_KEY = 202605220901
MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS = float(
    os.getenv("TRADING_BOT_MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS", "1.0")
)


@dataclass(slots=True)
class MarketTransitionResult:
    changed: bool
    transition: str | None
    state: MarketRuntimeState
    expired_offer_ids: tuple[int, ...] = ()


@dataclass(slots=True)
class MarketRuntimeView:
    is_open: bool
    active_web_notice_visible: bool
    offers_since_last_open: int
    last_transition_at: datetime | None
    next_transition_at: datetime | None


_market_runtime_view_cache: tuple[float, MarketRuntimeView] | None = None


def invalidate_market_runtime_view_cache() -> None:
    global _market_runtime_view_cache
    _market_runtime_view_cache = None


def _get_cached_market_runtime_view() -> MarketRuntimeView | None:
    if MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _market_runtime_view_cache
    if cached is None:
        return None
    cached_at, value = cached
    if (time.monotonic() - cached_at) <= MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS:
        return value
    invalidate_market_runtime_view_cache()
    return None


def _set_cached_market_runtime_view(value: MarketRuntimeView) -> None:
    global _market_runtime_view_cache
    if MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS <= 0:
        return
    _market_runtime_view_cache = (time.monotonic(), value)


def _coerce_utc_now(current_time: datetime | None = None) -> datetime:
    if current_time is None:
        return utc_now()
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=ZoneInfo("UTC"))
    return current_time.astimezone(ZoneInfo("UTC"))


def _build_market_event_payload(
    state: MarketRuntimeState,
    *,
    transition: str,
    notice_text: str | None,
) -> dict:
    return {
        "is_open": state.is_open,
        "active_web_notice_visible": state.active_web_notice_visible,
        "offers_since_last_open": state.offers_since_last_open,
        "last_transition_at": state.last_transition_at.isoformat() if state.last_transition_at else None,
        "transition": transition,
        "notice_text": notice_text,
    }


def _build_initial_market_runtime_state(
    evaluation: MarketScheduleEvaluation,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeState:
    return MarketRuntimeState(
        id=1,
        is_open=evaluation.is_open,
        active_web_notice_visible=False,
        offers_since_last_open=0,
        last_transition_at=_coerce_utc_now(current_time),
    )


async def _acquire_market_runtime_lock(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": MARKET_RUNTIME_ADVISORY_LOCK_KEY},
    )


async def _send_market_channel_notice(text: str) -> None:
    channel_id = settings.channel_id
    if not channel_id:
        return

    result = await telegram_gateway.send_message(
        channel_id,
        text,
        idempotency_key=f"market-channel-notice:{text}",
    )
    if not result.ok:
        raise RuntimeError(f"Telegram market notice failed: {result.error or result.status_code}")


async def get_market_runtime_state(db: AsyncSession) -> MarketRuntimeState | None:
    result = await db.execute(
        select(MarketRuntimeState)
        .order_by(MarketRuntimeState.id.asc())
        .limit(1)
    )
    return result.scalars().first()


async def get_or_create_market_runtime_state(
    db: AsyncSession,
    *,
    evaluation: MarketScheduleEvaluation,
    current_time: datetime | None = None,
) -> tuple[MarketRuntimeState, bool]:
    state = await get_market_runtime_state(db)
    if state is not None:
        return state, False

    state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
    db.add(state)
    await db.commit()
    return state, True


async def load_market_schedule_overrides_window(
    db: AsyncSession,
    *,
    timezone_name: str,
    current_time: datetime | None = None,
    lookahead_days: int = NEXT_TRANSITION_SEARCH_DAYS,
) -> list[MarketScheduleOverride]:
    timezone_info = ZoneInfo(timezone_name)
    local_now = current_time.astimezone(timezone_info) if current_time and current_time.tzinfo else datetime.now(timezone_info)
    start_date = local_now.date()
    end_date = start_date + timedelta(days=lookahead_days)
    result = await db.execute(
        select(MarketScheduleOverride)
        .where(
            MarketScheduleOverride.date >= start_date,
            MarketScheduleOverride.date <= end_date,
        )
        .order_by(MarketScheduleOverride.date.asc(), MarketScheduleOverride.id.asc())
    )
    return list(result.scalars().all())


async def evaluate_current_market_schedule(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketScheduleEvaluation:
    trading_settings = await get_trading_settings_async()
    timezone_name = get_market_timezone_name(trading_settings)
    overrides = await load_market_schedule_overrides_window(
        db,
        timezone_name=timezone_name,
        current_time=current_time,
    )
    return evaluate_market_schedule(
        trading_settings,
        current_time=current_time,
        overrides=overrides,
    )


async def get_market_runtime_view(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeView:
    if current_time is None:
        cached_view = _get_cached_market_runtime_view()
        if cached_view is not None:
            return cached_view

    evaluation = await evaluate_current_market_schedule(db, current_time=current_time)
    state = await get_market_runtime_state(db)
    view = MarketRuntimeView(
        is_open=evaluation.is_open,
        active_web_notice_visible=bool(getattr(state, "active_web_notice_visible", False)),
        offers_since_last_open=int(getattr(state, "offers_since_last_open", 0) or 0),
        last_transition_at=getattr(state, "last_transition_at", None),
        next_transition_at=evaluation.next_transition_at,
    )
    if current_time is None:
        _set_cached_market_runtime_view(view)
    return view


async def register_market_offer_created(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeState:
    evaluation = await evaluate_current_market_schedule(db, current_time=current_time)
    await _acquire_market_runtime_lock(db)
    state = await get_market_runtime_state(db)
    if state is None:
        state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
        db.add(state)
    state.is_open = evaluation.is_open
    state.offers_since_last_open = int(state.offers_since_last_open or 0) + 1
    should_hide_notice = bool(
        state.active_web_notice_visible and state.offers_since_last_open >= 2
    )
    if should_hide_notice:
        state.active_web_notice_visible = False
    await db.commit()
    invalidate_market_runtime_view_cache()

    if should_hide_notice:
        try:
            publish_event_sync(
                "market:notice_hidden",
                _build_market_event_payload(
                    state,
                    transition="notice_hidden",
                    notice_text=None,
                ),
            )
        except Exception as exc:
            logger.warning("Failed to publish market:notice_hidden event: %s", exc)

    return state


async def _load_active_local_offers(db: AsyncSession) -> list[Offer]:
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server == current_server(),
        )
        .order_by(Offer.id.asc())
    )
    return list(result.scalars().all())


async def _apply_market_open_transition(
    db: AsyncSession,
    state: MarketRuntimeState,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    now = _coerce_utc_now(current_time)
    state.is_open = True
    state.active_web_notice_visible = True
    state.offers_since_last_open = 0
    state.last_transition_at = now
    await db.commit()
    invalidate_market_runtime_view_cache()

    notice_text = MARKET_OPENED_CHANNEL_NOTICE
    try:
        await _send_market_channel_notice(notice_text)
    except Exception as exc:
        logger.warning("Failed to publish market-open channel notice: %s", exc)

    try:
        publish_event_sync("market:opened", _build_market_event_payload(state, transition="opened", notice_text=notice_text))
    except Exception as exc:
        logger.warning("Failed to publish market:opened event: %s", exc)

    return MarketTransitionResult(changed=True, transition="opened", state=state)


async def _apply_market_closed_transition(
    db: AsyncSession,
    state: MarketRuntimeState,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    now = _coerce_utc_now(current_time)
    active_offers = await _load_active_local_offers(db)
    expired_offer_ids: list[int] = []
    expired_user_ids: list[int] = []

    expiry_result = await expire_offers_authoritatively(
        db,
        active_offers,
        OfferExpiryCommand(
            reason=OfferExpiryReason.MARKET_CLOSED,
            source_surface=OfferExpirySourceSurface.SYSTEM,
            source_server=current_server(),
            expired_by_user_id=None,
            expired_by_actor_user_id=None,
        ),
        commit=False,
        now=now,
    )

    for offer in expiry_result.expired_offers:
        expired_offer_ids.append(offer.id)
        if offer.user_id:
            expired_user_ids.append(int(offer.user_id))

    state.is_open = False
    state.active_web_notice_visible = True
    state.offers_since_last_open = 0
    state.last_transition_at = now
    await db.commit()
    invalidate_market_runtime_view_cache()

    for offer in expiry_result.expired_offers:
        try:
            await apply_offer_channel_state(offer, reason="market_close_expire")
        except Exception as exc:
            logger.warning("Failed to apply channel state for market-close expiry %s: %s", offer.id, exc)

    if expired_user_ids:
        try:
            from core.cache import decr_active_offer_count

            for user_id in expired_user_ids:
                await decr_active_offer_count(user_id)
        except Exception as exc:
            logger.warning("Failed to update active-offer cache after market close: %s", exc)

    notice_text = MARKET_CLOSED_CHANNEL_NOTICE
    try:
        await _send_market_channel_notice(notice_text)
    except Exception as exc:
        logger.warning("Failed to publish market-close channel notice: %s", exc)

    try:
        publish_event_sync("market:closed", _build_market_event_payload(state, transition="closed", notice_text=notice_text))
    except Exception as exc:
        logger.warning("Failed to publish market:closed event: %s", exc)

    return MarketTransitionResult(
        changed=True,
        transition="closed",
        state=state,
        expired_offer_ids=tuple(expired_offer_ids),
    )


async def apply_market_schedule_transition(
    db: AsyncSession,
    evaluation: MarketScheduleEvaluation,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    await _acquire_market_runtime_lock(db)
    state = await get_market_runtime_state(db)
    if state is None:
        state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
        db.add(state)
        await db.commit()
        invalidate_market_runtime_view_cache()
        return MarketTransitionResult(changed=False, transition=None, state=state)

    if state.is_open == evaluation.is_open:
        return MarketTransitionResult(changed=False, transition=None, state=state)

    if evaluation.is_open:
        return await _apply_market_open_transition(db, state, current_time=current_time)

    return await _apply_market_closed_transition(db, state, current_time=current_time)
