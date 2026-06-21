"""Manual offer-expiry abuse limits shared by WebApp, bot, and internal forwards."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.trading_settings import get_trading_settings
from models.offer import Offer


class OfferManualExpireLimitError(ValueError):
    """Base error for user-facing manual expiry limit rejections."""

    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class OfferExpireRateLimitExceeded(OfferManualExpireLimitError):
    status_code = 429


class OfferExpireDailyLimitExceeded(OfferManualExpireLimitError):
    status_code = 403


async def enforce_manual_offer_expire_limits(
    db: AsyncSession,
    *,
    owner_user_id: int,
    trading_settings: Any | None = None,
    today: date | None = None,
) -> None:
    """Consume manual-expiry limits after the target offer is known active.

    The caller must validate ownership and active state first. This keeps repeated
    clicks on an already-terminal offer from consuming rate quota or hiding the
    actual terminal-state rejection.
    """
    ts = trading_settings or get_trading_settings()

    from bot.utils.redis_helpers import track_daily_expire, track_expire_rate

    rate_count = await track_expire_rate(owner_user_id, window_seconds=60)
    if rate_count > ts.offer_expire_rate_per_minute:
        raise OfferExpireRateLimitExceeded(f"حداکثر {ts.offer_expire_rate_per_minute} منقضی در دقیقه مجاز است")

    day = today or date.today()
    start_of_day = datetime.combine(day, datetime.min.time())
    total_offers_today = await db.scalar(
        select(func.count(Offer.id)).where(
            Offer.user_id == owner_user_id,
            Offer.created_at >= start_of_day,
        )
    ) or 0

    daily_data = await track_daily_expire(owner_user_id, total_offers_today)
    threshold = ts.offer_expire_daily_limit_after_threshold
    if daily_data["count"] >= threshold:
        max_allowed = total_offers_today // 3
        if daily_data["count"] >= max_allowed:
            raise OfferExpireDailyLimitExceeded(
                f"شما امروز {daily_data['count']} لفظ منقضی کرده‌اید. "
                f"برای منقضی کردن بیشتر، باید لفظ‌های جدید ثبت کنید."
            )
