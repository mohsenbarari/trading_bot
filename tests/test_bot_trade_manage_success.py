import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, offer=None):
        self.offer = offer
        self.commits = 0

    async def scalar(self, stmt):
        return 1

    async def get(self, model, offer_id, *args, **kwargs):
        return self.offer

    async def commit(self):
        self.commits += 1


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        session = self.sessions.pop(0)

        class _Context:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Context()


def make_callback():
    return SimpleNamespace(
        id="expiry-callback-1",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
    )


class BotTradeManageSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_expires_offer_and_removes_buttons(self):
        offer = SimpleNamespace(
            id=5,
            user_id=4,
            status=OfferStatus.ACTIVE,
            home_server="foreign",
            offer_public_id="ofr_bot_5",
            channel_message_id=77,
        )
        final_session = FakeSession(offer)
        factory = FakeSessionFactory(final_session)
        callback = make_callback()
        bot = SimpleNamespace()
        settings_obj = SimpleNamespace(channel_id=-100, offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=99)

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=1)
        ), patch("bot.utils.redis_helpers.track_daily_expire", new=AsyncMock(return_value={"count": 0})), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal", new=factory
        ), patch("bot.handlers.trade_manage.current_server", return_value="foreign"), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch("bot.handlers.trade_manage.apply_offer_channel_state", new=AsyncMock()) as apply_offer_channel_state, patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=SimpleNamespace(id=4), bot=bot)

        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(offer.expire_reason, "manual")
        self.assertEqual(offer.expired_by_user_id, 4)
        self.assertEqual(offer.expired_by_actor_user_id, 4)
        self.assertEqual(offer.expire_source_surface, "telegram_bot")
        self.assertEqual(offer.expire_source_server, "foreign")
        self.assertEqual(final_session.commits, 1)
        apply_offer_channel_state.assert_awaited_once_with(offer, reason="manual_expire", timeout=10)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        callback.answer.assert_awaited_with()
        callback.message.answer.assert_awaited_once()
        self.assertEqual(callback.message.answer.await_args.kwargs["reply_markup"], "MENU")

    async def test_handle_expire_offer_logs_channel_markup_failures_and_keeps_success_flow(self):
        offer = SimpleNamespace(id=5, user_id=4, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=77)
        final_session = FakeSession(offer)
        factory = FakeSessionFactory(final_session)
        callback = make_callback()
        bot = SimpleNamespace()
        settings_obj = SimpleNamespace(channel_id=-100, offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=99)

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=1)
        ), patch("bot.utils.redis_helpers.track_daily_expire", new=AsyncMock(return_value={"count": 0})), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal", new=factory
        ), patch("bot.handlers.trade_manage.current_server", return_value="foreign"), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_manage.apply_offer_channel_state",
            new=AsyncMock(side_effect=RuntimeError("edit failed")),
        ), patch("bot.handlers.trade_manage.logger") as logger, patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=SimpleNamespace(id=4), bot=bot)

        logger.debug.assert_called_once()
        callback.answer.assert_awaited_with()
        callback.message.answer.assert_awaited_once()

    async def test_queue_mode_commits_expiry_with_m0_callback_and_has_no_direct_answer(self):
        offer = SimpleNamespace(
            id=5,
            user_id=4,
            status=OfferStatus.ACTIVE,
            home_server="foreign",
            offer_public_id="ofr_bot_queue_5",
            channel_message_id=77,
        )
        final_session = FakeSession(offer)
        callback = make_callback()
        settings_obj = SimpleNamespace(
            channel_id=-100,
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=99,
        )

        with patch(
            "bot.handlers.trade_manage.get_trading_settings",
            return_value=settings_obj,
        ), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal",
            new=FakeSessionFactory(final_session),
        ), patch(
            "bot.handlers.trade_manage.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_manage.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="queue-v1"),
        ), patch(
            "bot.handlers.trade_manage.enqueue_telegram_callback_answer",
            new=AsyncMock(),
        ) as enqueue_callback, patch(
            "bot.handlers.trade_manage.apply_offer_channel_state",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await handle_expire_offer(
                callback,
                SimpleNamespace(offer_id=5),
                user=SimpleNamespace(id=4),
                bot=SimpleNamespace(),
            )

        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(final_session.commits, 1)
        enqueue_callback.assert_awaited_once()
        self.assertIs(enqueue_callback.await_args.args[0], final_session)
        self.assertEqual(
            enqueue_callback.await_args.kwargs["action"].value,
            "offer_expiry_callback",
        )
        callback.answer.assert_not_awaited()
        callback.message.edit_reply_markup.assert_awaited_once_with(
            reply_markup=None
        )
        callback.message.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
