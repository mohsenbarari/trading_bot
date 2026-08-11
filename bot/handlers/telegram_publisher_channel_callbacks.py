"""Publisher-only adapters for callbacks attached to channel offer posts."""
from __future__ import annotations

from typing import Optional

from aiogram import Bot, Router
from aiogram.types import CallbackQuery

from bot.callbacks import ChannelTradeCallback, ChannelTradePublicCallback, ExpireOfferCallback
from bot.handlers.trade_execute import _handle_channel_trade
from bot.handlers.trade_manage import handle_expire_offer
from models.user import User


def build_publisher_channel_callback_router() -> Router:
    """Create a fresh router; aiogram routers cannot have multiple parents."""
    router = Router(name="publisher-channel-offer-callbacks")

    @router.callback_query(ChannelTradeCallback.filter())
    async def trade_callback(
        callback: CallbackQuery,
        callback_data: ChannelTradeCallback,
        user: Optional[User],
        bot: Bot,
        trade_contention_preconfirmed: bool = False,
        trade_contention_pre_gated: bool = False,
    ) -> None:
        await _handle_channel_trade(
            callback, callback_data, user, bot,
            trade_contention_preconfirmed=trade_contention_preconfirmed,
            trade_contention_pre_gated=trade_contention_pre_gated,
        )

    @router.callback_query(ChannelTradePublicCallback.filter())
    async def public_trade_callback(
        callback: CallbackQuery,
        callback_data: ChannelTradePublicCallback,
        user: Optional[User],
        bot: Bot,
        trade_contention_preconfirmed: bool = False,
        trade_contention_pre_gated: bool = False,
    ) -> None:
        await _handle_channel_trade(
            callback, callback_data, user, bot,
            trade_contention_preconfirmed=trade_contention_preconfirmed,
            trade_contention_pre_gated=trade_contention_pre_gated,
        )

    @router.callback_query(ExpireOfferCallback.filter())
    async def expiry_callback(
        callback: CallbackQuery,
        callback_data: ExpireOfferCallback,
        user: Optional[User],
        bot: Bot,
    ) -> None:
        await handle_expire_offer(callback, callback_data, user, bot)

    return router
