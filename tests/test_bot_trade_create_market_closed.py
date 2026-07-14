import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm, handle_trade_confirm
from core.services.market_transition_service import MarketOfferAdmissionClosedError
from core.services.offer_creation_service import OfferCreationLimitExceededError


class _Session:
    async def scalar(self, _stmt):
        return 0


class _SessionContext:
    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateMarketClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_wizard_and_text_confirm_paths_stop_when_market_is_closed(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock())

        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1), bot=SimpleNamespace())

        callback.message.edit_text.assert_awaited_once_with(
            "بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
        )
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()

        text_callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        text_state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock())

        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)):
            await handle_text_offer_confirm(text_callback, text_state, user=SimpleNamespace(id=1), bot=SimpleNamespace())

        text_callback.message.edit_text.assert_awaited_once_with(
            "بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
        )
        text_state.clear.assert_awaited_once()
        text_callback.answer.assert_awaited_once_with()

    async def test_text_confirm_rejects_final_close_without_publication_or_counter(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(
                return_value={
                    "quantity": 10,
                    "trade_type": "buy",
                    "settlement_type": "cash",
                    "commodity_id": 7,
                    "commodity_name": "امام",
                    "price": 123456,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        with patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=3),
        ), patch(
            "core.utils.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[_SessionContext(), _SessionContext(), _SessionContext()],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.create_authoritative_offer_with_outcome",
            new=AsyncMock(
                side_effect=MarketOfferAdmissionClosedError(
                    "market_closed_during_offer_admission"
                )
            ),
        ) as create_mock, patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100),
        ):
            await handle_text_offer_confirm(
                callback,
                state,
                user=SimpleNamespace(id=1, limitations_expire_at=None),
                bot=bot,
            )

        create_mock.assert_awaited_once()
        callback.message.edit_text.assert_awaited_once_with(
            "بعلت بسته بودن بازار درخواست شما ثبت نشد\n"
            "لطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
        )
        bot.send_message.assert_not_awaited()
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()

    async def test_text_confirm_maps_final_quota_rejection_without_publication(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(
                return_value={
                    "quantity": 10,
                    "trade_type": "buy",
                    "settlement_type": "cash",
                    "commodity_id": 7,
                    "commodity_name": "امام",
                    "price": 123456,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        quota_error = OfferCreationLimitExceededError(
            "offer_active_limit_exceeded",
            "شما حداکثر 1 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )

        with patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=1),
        ), patch(
            "core.utils.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[_SessionContext(), _SessionContext(), _SessionContext()],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.create_authoritative_offer_with_outcome",
            new=AsyncMock(side_effect=quota_error),
        ), patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100),
        ):
            await handle_text_offer_confirm(
                callback,
                state,
                user=SimpleNamespace(id=1, limitations_expire_at=None),
                bot=bot,
            )

        callback.message.edit_text.assert_awaited_once_with(
            f"⚠️ **محدودیت**\n\n{quota_error.detail}",
            parse_mode="Markdown",
        )
        bot.send_message.assert_not_awaited()
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
