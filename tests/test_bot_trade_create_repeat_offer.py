import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    _expire_offer_after_publication_failure,
    _handle_trade_confirm_core,
    _send_repeat_offer_menu_refresh,
    handle_repeat_offer_button,
)
from bot.repeat_offer import BotRepeatOfferCandidate
from core.services.offer_creation_service import OfferCreationAdmissionError
from core.services.offer_republish_service import OfferNotRepeatableError
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class FakeSessionContext:
    def __init__(self, session=None):
        self.session = session or SimpleNamespace()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScalarSession:
    def __init__(self, value=0):
        self.value = value

    async def scalar(self, stmt):
        return self.value


class FailingOfferLoadSession:
    async def get(self, *_args, **_kwargs):
        raise RuntimeError("synthetic_post_commit_offer_load_failure")


class BotTradeCreateRepeatOfferTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_uses_normal_preview_and_records_provenance(self):
        candidate = BotRepeatOfferCandidate(
            source_offer_id=41,
            source_offer_public_id="ofr_source_41",
            draft_text="ف ن ف ربع 25 عدد 178000 15 10: تحویل حضوری",
            button_text="🔁 ف ن ف ربع 25 عدد 178000 15 10: تحویل حضوری",
        )
        message = SimpleNamespace(
            text=candidate.button_text,
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        user = SimpleNamespace(id=9)

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(),
        ), patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new=AsyncMock(return_value=(candidate, False, None)),
        ), patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new=AsyncMock(return_value=True),
        ) as prepare:
            await handle_repeat_offer_button(message, state, user, message.bot)

        state.clear.assert_awaited_once_with()
        self.assertEqual(prepare.await_args.args[3], candidate.draft_text)
        provenance = state.update_data.await_args.kwargs
        self.assertEqual(provenance["republished_from_offer_public_id"], "ofr_source_41")
        self.assertEqual(provenance["republished_from_offer_id"], 41)
        self.assertTrue(provenance["republish_idempotency_key"].startswith("bot-repeat:"))

    async def test_stale_button_does_not_prepare_offer_and_refreshes_menu(self):
        message = SimpleNamespace(
            text="🔁 خ ن سکه 10 عدد 100000",
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        user = SimpleNamespace(id=9)

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(),
        ), patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new=AsyncMock(return_value=(None, True, "button_not_found")),
        ), patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new=AsyncMock(),
        ) as prepare, patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await handle_repeat_offer_button(message, state, user, message.bot)

        prepare.assert_not_awaited()
        state.update_data.assert_not_awaited()
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "MENU")
        self.assertIn("دکمه جدید را بزنید", message.answer.await_args.args[0])

    async def test_old_numbered_button_never_prepares_different_latest_offer(self):
        message = SimpleNamespace(
            text="🔁 ف ن ف ربع 25 عدد 178000 15 10: تحویل حضوری #225",
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        user = SimpleNamespace(id=9)

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(),
        ), patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new=AsyncMock(return_value=(None, True, "stale_button")),
        ), patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ), patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new=AsyncMock(),
        ) as prepare:
            await handle_repeat_offer_button(message, state, user, message.bot)

        prepare.assert_not_awaited()
        state.update_data.assert_not_awaited()
        self.assertIn("دکمه جدید را بزنید", message.answer.await_args.args[0])
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "MENU")

    async def test_stale_button_queue_owner_persists_response_without_direct_send(self):
        message = SimpleNamespace(
            text="🔁 خ ن سکه 10 عدد 100000",
            message_id=81,
            chat=SimpleNamespace(id=99),
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        user = SimpleNamespace(id=9, telegram_id=99)
        queued = SimpleNamespace(created=True)

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(),
        ), patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new=AsyncMock(return_value=(None, True, "stale_button")),
        ), patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new=AsyncMock(return_value=queued),
        ) as enqueue, patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(),
        ) as build_keyboard:
            await handle_repeat_offer_button(message, state, user, message.bot)

        message.answer.assert_not_awaited()
        build_keyboard.assert_not_awaited()
        self.assertEqual(
            enqueue.await_args.kwargs["source_id"],
            "stale-button:81",
        )

    async def test_stale_button_fails_closed_if_queue_handoff_returns_none(self):
        message = SimpleNamespace(
            text="🔁 خ ن سکه 10 عدد 100000",
            message_id=81,
            chat=SimpleNamespace(id=99),
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        user = SimpleNamespace(id=9, telegram_id=99)

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(),
        ), patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new=AsyncMock(return_value=(None, True, "stale_button")),
        ), patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1),
        ), patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(),
        ) as build_keyboard:
            with self.assertRaisesRegex(
                RuntimeError,
                "repeat_offer_direct_delivery_requires_legacy_owner",
            ):
                await handle_repeat_offer_button(message, state, user, message.bot)

        message.answer.assert_not_awaited()
        build_keyboard.assert_not_awaited()

    async def test_confirm_revalidates_source_and_creates_foreign_provenance(self):
        creation_session = SimpleNamespace()
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 25,
                    "trade_type": "sell",
                    "settlement_type": "tomorrow",
                    "commodity_id": 7,
                    "commodity_name": "ربع",
                    "price": 178000,
                    "is_wholesale": False,
                    "lot_sizes": [15, 10],
                    "notes": "تحویل حضوری",
                    "republished_from_offer_public_id": "ofr_source_41",
                    "republished_from_offer_id": 41,
                    "republish_idempotency_key": "bot-repeat:intent-1",
                }
            ),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=99),
        )
        user = SimpleNamespace(id=9, limitations_expire_at=None)
        source = SimpleNamespace(id=41)

        with patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=3),
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeScalarSession(0)),
                FakeSessionContext(SimpleNamespace()),
                FakeSessionContext(creation_session),
            ],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.acquire_market_offer_admission_fence",
            new=AsyncMock(),
        ) as fence, patch(
            "bot.handlers.trade_create.lock_repeatable_offer",
            new=AsyncMock(return_value=source),
        ) as lock_source, patch(
            "bot.handlers.trade_create.ensure_republish_payload_matches_source"
        ) as ensure_payload, patch(
            "bot.handlers.trade_create.create_authoritative_offer_with_outcome",
            new=AsyncMock(
                side_effect=OfferCreationAdmissionError("stop_after_command", "STOP")
            ),
        ) as create_offer, patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100),
        ):
            await _handle_trade_confirm_core(
                callback,
                state,
                user,
                SimpleNamespace(),
                check_user_limits_fn=lambda *_args: (True, None),
                to_jalali_str_fn=lambda *_args: "",
                success_message_text="OK",
                unexpected_error_prefix="ERR",
                warning_confirm_callback_data="confirm_warning",
                cancel_callback_data="cancel",
            )

        fence.assert_awaited_once_with(creation_session)
        self.assertEqual(lock_source.await_args.kwargs["replacement_home_server"], "foreign")
        self.assertEqual(lock_source.await_args.kwargs["offer_public_id"], "ofr_source_41")
        ensure_payload.assert_called_once_with(
            source,
            offer_type="sell",
            settlement_type="tomorrow",
            commodity_id=7,
            quantity=25,
            price=178000,
            is_wholesale=False,
            lot_sizes=[15, 10],
            notes="تحویل حضوری",
        )
        command = create_offer.await_args.args[1]
        self.assertEqual(command.republished_from_offer_public_id, "ofr_source_41")
        self.assertEqual(command.idempotency_key, "bot-repeat:intent-1")
        self.assertEqual(command.source_surface.value, "telegram_bot")
        self.assertFalse(create_offer.await_args.kwargs["validate_market"])
        self.assertIn("STOP", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once_with()

    async def test_failed_publication_releases_only_foreign_repeat_slot(self):
        offer = SimpleNamespace(
            republished_from_offer_public_id="ofr_source_41",
            home_server="foreign",
        )
        with patch(
            "bot.handlers.trade_create.expire_offer_authoritatively",
            new=AsyncMock(),
        ) as expire:
            await _expire_offer_after_publication_failure(SimpleNamespace(), offer, 9)

        self.assertIsNone(offer.republished_from_offer_public_id)
        expire.assert_awaited_once()

    async def test_queue_post_commit_failure_never_expires_or_reports_rejection(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 5,
                    "trade_type": "buy",
                    "settlement_type": "cash",
                    "commodity_id": 7,
                    "commodity_name": "ربع",
                    "price": 178000,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=SimpleNamespace(
                message_id=81,
                chat=SimpleNamespace(id=99),
                edit_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=99),
        )
        user = SimpleNamespace(id=9, limitations_expire_at=None, sync_version=3)
        offer = SimpleNamespace(
            id=55,
            offer_public_id="ofr_post_commit_55",
            version_id=1,
            offer_type=SimpleNamespace(value="buy"),
            settlement_type="cash",
            commodity_id=7,
            quantity=5,
            price=178000,
            notes=None,
        )
        creation_session = SimpleNamespace(
            flush=AsyncMock(),
            commit=AsyncMock(),
        )

        with patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=4),
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeScalarSession(0)),
                FakeSessionContext(SimpleNamespace()),
                FakeSessionContext(creation_session),
                FakeSessionContext(FailingOfferLoadSession()),
            ],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.create_authoritative_offer_with_outcome",
            new=AsyncMock(return_value=SimpleNamespace(offer=offer, created=True)),
        ), patch(
            "bot.handlers.trade_create.get_or_create_telegram_publication_state",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.trade_create._canonical_commodity_name_from_session",
            new=AsyncMock(return_value="ربع"),
        ), patch(
            "bot.handlers.trade_create.enqueue_offer_success_preview_notification_once",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.trade_create.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1),
        ), patch(
            "bot.handlers.trade_create._expire_offer_after_publication_failure",
            new=AsyncMock(),
        ) as expire, patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100),
        ):
            await _handle_trade_confirm_core(
                callback,
                state,
                user,
                SimpleNamespace(),
                check_user_limits_fn=lambda *_args: (True, None),
                to_jalali_str_fn=lambda *_args: "",
                success_message_text="OK",
                unexpected_error_prefix="ERR",
                warning_confirm_callback_data="confirm_warning",
                cancel_callback_data="cancel",
            )

        creation_session.commit.assert_awaited_once_with()
        expire.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
        state.clear.assert_awaited_once_with()
        callback.answer.assert_awaited_once_with()

    async def test_final_ineligible_repeat_refreshes_stale_keyboard(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 25,
                    "trade_type": "sell",
                    "settlement_type": "tomorrow",
                    "commodity_id": 7,
                    "commodity_name": "ربع",
                    "price": 178000,
                    "is_wholesale": False,
                    "lot_sizes": [15, 10],
                    "notes": "تحویل حضوری",
                    "republished_from_offer_public_id": "ofr_source_41",
                    "republished_from_offer_id": 41,
                }
            ),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=99),
        )
        user = SimpleNamespace(id=9, limitations_expire_at=None)
        bot = SimpleNamespace()

        with patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=3),
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeScalarSession(0)),
                FakeSessionContext(SimpleNamespace()),
                FakeSessionContext(SimpleNamespace()),
            ],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.acquire_market_offer_admission_fence",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.trade_create.lock_repeatable_offer",
            new=AsyncMock(side_effect=OfferNotRepeatableError("offer_ineligible")),
        ), patch(
            "bot.handlers.trade_create._send_repeat_offer_menu_refresh",
            new=AsyncMock(),
        ) as refresh, patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100),
        ):
            await _handle_trade_confirm_core(
                callback,
                state,
                user,
                bot,
                check_user_limits_fn=lambda *_args: (True, None),
                to_jalali_str_fn=lambda *_args: "",
                success_message_text="OK",
                unexpected_error_prefix="ERR",
                warning_confirm_callback_data="confirm_warning",
                cancel_callback_data="cancel",
            )

        self.assertIn("دیگر قابل تکرار نیست", callback.message.edit_text.await_args.args[0])
        refresh.assert_awaited_once_with(
            bot,
            chat_id=99,
            user=user,
            source_id="repeat-ineligible:ofr_source_41",
        )
        state.clear.assert_awaited_once_with()
        callback.answer.assert_awaited_once_with()

    async def test_menu_refresh_sends_current_keyboard_and_is_best_effort(self):
        user = SimpleNamespace(id=9)
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await _send_repeat_offer_menu_refresh(bot, chat_id=99, user=user)

        bot.send_message.assert_awaited_once_with(
            chat_id=99,
            text="منو با آخرین وضعیت به‌روزرسانی شد",
            reply_markup="MENU",
        )

        failing_bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=RuntimeError("telegram down"))
        )
        with patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await _send_repeat_offer_menu_refresh(failing_bot, chat_id=99, user=user)

    async def test_menu_refresh_queue_owner_never_calls_telegram_directly(self):
        user = SimpleNamespace(id=9)
        bot = SimpleNamespace(send_message=AsyncMock())
        queued = SimpleNamespace(created=True)
        with patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new=AsyncMock(return_value=queued),
        ) as enqueue, patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(),
        ) as build_keyboard:
            await _send_repeat_offer_menu_refresh(
                bot,
                chat_id=99,
                user=user,
                source_id="repeat-success:bot-repeat:intent-1",
            )

        bot.send_message.assert_not_awaited()
        build_keyboard.assert_not_awaited()
        self.assertEqual(
            enqueue.await_args.kwargs["source_id"],
            "repeat-success:bot-repeat:intent-1",
        )

    async def test_menu_refresh_fails_closed_if_queue_handoff_returns_none(self):
        user = SimpleNamespace(id=9)
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1),
        ), patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new=AsyncMock(),
        ) as build_keyboard:
            await _send_repeat_offer_menu_refresh(
                bot,
                chat_id=99,
                user=user,
                source_id="repeat-success:bot-repeat:intent-2",
            )

        bot.send_message.assert_not_awaited()
        build_keyboard.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
