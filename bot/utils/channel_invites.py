from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from aiogram import Bot

from core.config import settings
from core.external_effect_execution_gate import EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT

logger = logging.getLogger(__name__)

JOIN_REQUEST_LINK_TTL = timedelta(days=1)


def _require_channel_join_request_effect_authorization() -> None:
    """Revalidate authority immediately before creating a Telegram invite.

    Most calls share the long-lived bot session, whose request middleware
    already fences Bot API effects.  Account-status reactivation deliberately
    creates a short-lived ``Bot`` instance, so this helper is also the
    boundary that prevents that exceptional path from bypassing the current
    Writer Witness term or an enabled no-resend authorization.
    """

    from core.db import require_external_effect_execution_authorization

    require_external_effect_execution_authorization(
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT
    )


async def create_channel_join_request_link(
    bot: Bot,
    *,
    user_id: int | None = None,
) -> str | None:
    if not settings.channel_id:
        return settings.channel_invite_link

    link_name = "channel-join"
    if user_id is not None:
        link_name = f"channel-join-{user_id}"[:32]

    # This must remain outside the provider-error fallback below.  A lost
    # writer term is an authority failure, not a reason to quietly continue
    # with a stale/static invitation path.
    _require_channel_join_request_effect_authorization()

    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=settings.channel_id,
            name=link_name,
            expire_date=int((datetime.now(timezone.utc) + JOIN_REQUEST_LINK_TTL).timestamp()),
            creates_join_request=True,
        )
    except Exception:
        logger.exception("Failed to create channel join-request link")
        return settings.channel_invite_link

    return invite_link.invite_link


async def build_channel_join_request_line(
    bot: Bot | None,
    *,
    user_id: int | None = None,
) -> str | None:
    link = await resolve_channel_join_request_link(bot, user_id=user_id)

    if not link:
        return None

    return f"🔗 [درخواست عضویت در کانال معاملات]({link})"


async def build_channel_join_request_text(
    bot: Bot | None,
    *,
    user_id: int | None = None,
) -> str | None:
    link = await resolve_channel_join_request_link(bot, user_id=user_id)

    if not link:
        return None

    return f"🔗 درخواست عضویت در کانال معاملات:\n{link}"


async def build_channel_access_text(
    bot: Bot | None,
    *,
    user_id: int | None = None,
) -> str | None:
    link = await resolve_channel_join_request_link(bot, user_id=user_id)

    if not link:
        return None

    return f"🔗 کانال معاملات:\n{link}"


async def resolve_channel_join_request_link(
    bot: Bot | None,
    *,
    user_id: int | None = None,
) -> str | None:
    if bot is None:
        return settings.channel_invite_link
    return await create_channel_join_request_link(bot, user_id=user_id)
