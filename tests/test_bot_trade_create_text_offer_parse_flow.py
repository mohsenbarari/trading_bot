import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import Trade, _text_offer_shadow_inference_summary, handle_text_offer
from core.enums import UserRole


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateTextOfferParseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_text_offer_handles_non_offer_parse_error_active_cap_and_success(self):
        user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None, id=1)

        closed_message = SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock())
        closed_state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock()
        ) as parse_mock:
            await handle_text_offer(closed_message, closed_state, user=user, bot=SimpleNamespace())
        closed_message.answer.assert_awaited_once_with("بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید.")
        parse_mock.assert_not_awaited()

        message = SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(None, None))
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        message.answer.assert_not_awaited()

        error = SimpleNamespace(message="خطای قیمت")
        message = SimpleNamespace(text="خ ربع 30تا", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(None, error))
        ), patch("bot.handlers.trade_create._get_offer_suggestion", return_value="HINT"):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        self.assertIn("خطای قیمت", message.answer.await_args.args[0])
        self.assertIn("HINT", message.answer.await_args.args[0])

        parsed = SimpleNamespace(
            trade_type="buy",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        message = SimpleNamespace(text="خ سکه 12تا 123456", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))
        ), patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession([3])),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        self.assertIn("حداکثر 3 لفظ فعال", message.answer.await_args.args[0])

        parsed = SimpleNamespace(
            trade_type="sell",
            settlement_type="cash",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=False,
            lot_sizes=[7, 5],
            notes="فقط نقدی",
        )
        message = SimpleNamespace(text="ف ن سکه 12تا 123456 7 5", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))
        ), patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession([0])),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        state.update_data.assert_awaited_once_with(
            trade_type="sell",
            settlement_type="cash",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=False,
            lot_sizes=[7, 5],
            notes="فقط نقدی",
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_text_confirm)
        preview_text = message.answer.await_args.args[0]
        self.assertIn("پیش\u200cنمایش لفظ", preview_text)
        self.assertIn("خُرد [7, 5]", preview_text)

    async def test_implicit_name_shadow_is_visible_but_does_not_change_bot_offer_state(self):
        user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None, id=1)
        parsed = SimpleNamespace(
            trade_type="buy",
            settlement_type="tomorrow",
            commodity_id=7,
            commodity_name="امام",
            commodity_resolution="IMPLICIT_DEFAULT",
            quantity=12,
            price=182700,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        message = SimpleNamespace(text="خ ن ف 12تا 182700", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with (
            patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)),
            patch("bot.handlers.trade_create.settings", SimpleNamespace(coin_intelligence_inference_preview_enabled=True)),
            patch("bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))) as parser,
            patch("bot.handlers.trade_create._text_offer_shadow_inference_summary", new=AsyncMock(return_value=(
                "🔬 تشخیص آزمایشی کالا: مدل قیمت را نزدیک به «بهار» می‌بیند؛ "
                "کالای آفر همچنان «امام» است و ثبت آفر تغییری نمی‌کند."
            ))),
            patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)),
            patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession([0])),
            ),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())

        parser.assert_awaited_once_with("خ ن ف 12تا 182700", capture_commodity_resolution=True)
        state.update_data.assert_awaited_once_with(
            trade_type="buy",
            settlement_type="tomorrow",
            commodity_id=7,
            commodity_name="امام",
            quantity=12,
            price=182700,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        preview_text = message.answer.await_args.args[0]
        self.assertIn("تشخیص آزمایشی کالا", preview_text)
        self.assertIn("بهار", preview_text)
        self.assertIn("کالای آفر همچنان «امام»", preview_text)

    async def test_bot_shadow_summary_audits_only_an_implicit_commodity(self):
        result = SimpleNamespace(
            commodity_resolution="IMPLICIT_DEFAULT",
            settlement_type="cash",
            commodity_name="امام",
            price=182700,
        )
        session = SimpleNamespace(commit=AsyncMock())
        observation = SimpleNamespace(
            decision=SimpleNamespace(
                status="AUTO_SELECT",
                candidates=(SimpleNamespace(commodity_name="بهار"),),
            ),
        )
        with (
            patch(
                "bot.handlers.trade_create.settings",
                SimpleNamespace(
                    coin_intelligence_inference_preview_enabled=True,
                    coin_intelligence_inference_snapshot_path="/safe/snapshot.json",
                ),
            ),
            patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=FakeSessionContext(session),
            ),
            patch(
                "bot.handlers.trade_create.observe_coin_inference_shadow",
                new=AsyncMock(return_value=observation),
            ) as observe,
        ):
            summary = await _text_offer_shadow_inference_summary(result)

        self.assertIn("بهار", summary or "")
        self.assertIn("امام", summary or "")
        self.assertEqual(observe.await_args.kwargs["source_surface"], "TELEGRAM_BOT")
        self.assertEqual(observe.await_args.kwargs["settlement_term"], "CASH")
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
