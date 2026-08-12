import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import TextOfferInferenceCandidateCallback
from bot.handlers.trade_create import (
    Trade,
    _text_offer_selection_observation,
    _text_offer_shadow_inference_summary,
    handle_text_offer,
    handle_text_offer_inference_choice,
)
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
            coin_inference_decision_key=None,
            coin_inference_selected_commodity_id=None,
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
            coin_inference_decision_key=None,
            coin_inference_selected_commodity_id=None,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_text_confirm)
        preview_text = message.answer.await_args.args[0]
        self.assertIn("پیش\u200cنمایش لفظ", preview_text)
        self.assertIn("خُرد [7, 5]", preview_text)

    async def test_omitted_name_never_enters_bot_offer_state_while_inference_is_shadow(self):
        user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None, id=1)
        parsed = SimpleNamespace(
            trade_type="buy",
            settlement_type="tomorrow",
            commodity_id=None,
            commodity_name=None,
            commodity_resolution="OMITTED",
            quantity=12,
            price=182700,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        message = SimpleNamespace(text="خ ف 12تا 182700", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with (
            patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)),
            patch("bot.handlers.trade_create.settings", SimpleNamespace(coin_intelligence_inference_preview_enabled=True)),
            patch("bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))) as parser,
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())

        parser.assert_awaited_once_with("خ ف 12تا 182700", capture_commodity_resolution=True)
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        self.assertIn("نام کالا در لفظ نیامده", message.answer.await_args.args[0])

    async def test_enabled_selector_auto_choice_enters_preview_with_audit_receipt(self):
        user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None, id=1)
        parsed = SimpleNamespace(
            trade_type="buy",
            settlement_type="tomorrow",
            commodity_id=None,
            commodity_name=None,
            commodity_resolution="OMITTED",
            low_date_hint=False,
            quantity=5,
            price=186800,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        selected = SimpleNamespace(commodity_id=71, commodity_name="امام")
        observation = SimpleNamespace(
            decision_key="a" * 64,
            decision=SimpleNamespace(status="AUTO_SELECT", candidates=(selected,)),
        )
        message = SimpleNamespace(text="خ ف 5تا 186800", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with (
            patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)),
            patch(
                "bot.handlers.trade_create.settings",
                SimpleNamespace(
                    coin_intelligence_inference_preview_enabled=False,
                    coin_intelligence_inference_selection_enabled=True,
                ),
            ),
            patch("bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))) as parser,
            patch(
                "bot.handlers.trade_create._text_offer_selection_observation",
                new=AsyncMock(return_value=observation),
            ),
            patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)),
            patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession([0])),
            ),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())

        parser.assert_awaited_once_with("خ ف 5تا 186800", capture_commodity_resolution=True)
        self.assertEqual((parsed.commodity_id, parsed.commodity_name), (71, "امام"))
        self.assertEqual(
            state.update_data.await_args.kwargs["coin_inference_decision_key"],
            "a" * 64,
        )
        self.assertEqual(state.update_data.await_args.kwargs["coin_inference_selected_commodity_id"], 71)
        self.assertIn("امام", message.answer.await_args.args[0])
        state.set_state.assert_awaited_once_with(Trade.awaiting_text_confirm)

    async def test_bot_selector_defaults_to_confirmation_only_rollout(self):
        parsed = SimpleNamespace(
            settlement_type="cash",
            low_date_hint=False,
            price=186_800,
        )
        session = SimpleNamespace(commit=AsyncMock())
        observation = SimpleNamespace()
        with (
            patch(
                "bot.handlers.trade_create.settings",
                SimpleNamespace(
                    coin_intelligence_inference_selection_enabled=True,
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
            result = await _text_offer_selection_observation(parsed)

        self.assertIs(result, observation)
        self.assertTrue(observe.await_args.kwargs["force_confirmation"])
        session.commit.assert_awaited_once()

    async def test_ambiguous_selector_accepts_only_the_candidate_stored_in_fsm(self):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "text_offer_inference_message_id": 91,
                "text_offer_inference_decision_key": "a" * 64,
                "text_offer_inference_candidates": [
                    {"commodity_id": 71, "commodity_name": "امام"},
                ],
                "text_offer_inference_draft": {
                    "trade_type": "buy",
                    "settlement_type": "cash",
                    "quantity": 5,
                    "price": 186800,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                },
            }),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(message=SimpleNamespace(message_id=91))
        user = SimpleNamespace(id=1)
        with (
            patch("bot.handlers.trade_create._show_text_offer_preview", new=AsyncMock(return_value=True)) as show,
            patch("bot.handlers.trade_create.answer_callback_query_via_runtime", new=AsyncMock()) as answer,
        ):
            await handle_text_offer_inference_choice(
                callback,
                TextOfferInferenceCandidateCallback(commodity_id=71),
                state,
                user,
            )

        self.assertEqual(show.await_args.args[3].commodity_name, "امام")
        self.assertEqual(show.await_args.kwargs["inference_selection"], {
            "decision_key": "a" * 64,
            "selected_commodity_id": 71,
        })
        answer.assert_awaited_once()

    async def test_bot_shadow_summary_audits_only_an_omitted_commodity(self):
        result = SimpleNamespace(
            commodity_resolution="OMITTED",
            settlement_type="cash",
            commodity_name=None,
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
        self.assertIn("نامشخص", summary or "")
        self.assertEqual(observe.await_args.kwargs["source_surface"], "TELEGRAM_BOT")
        self.assertEqual(observe.await_args.kwargs["settlement_term"], "CASH")
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
