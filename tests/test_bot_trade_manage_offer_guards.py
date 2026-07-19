import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, offer=None):
        self.offer = offer

    async def scalar(self, stmt):
        return 1

    async def get(self, model, offer_id, *args, **kwargs):
        return self.offer


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
        id="expiry-guard-callback",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock()),
    )


class BotTradeManageOfferGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.expiry_lease = SimpleNamespace(acquired=True, release=AsyncMock())
        self.expiry_gate_patcher = patch(
            "bot.handlers.trade_manage.try_acquire_offer_expiry_gate",
            new=AsyncMock(return_value=self.expiry_lease),
        )
        self.expiry_gate_patcher.start()
        self.addCleanup(self.expiry_gate_patcher.stop)

    async def test_handle_expire_offer_handles_not_found_not_owner_and_inactive(self):
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=99)
        user = SimpleNamespace(id=4)

        for offer, expected in [
            (None, "لفظ یافت نشد"),
            (SimpleNamespace(user_id=9, status=OfferStatus.ACTIVE), "مالک این لفظ نیستید"),
            (SimpleNamespace(user_id=4, status=OfferStatus.COMPLETED), "دیگر فعال نیست"),
        ]:
            callback = make_callback()
            factory = FakeSessionFactory(FakeSession(offer))
            with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
                "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=1)
            ), patch("bot.utils.redis_helpers.track_daily_expire", new=AsyncMock(return_value={"count": 0})), patch(
                "bot.handlers.trade_manage.AsyncSessionLocal", new=factory
            ):
                await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())
            self.assertIn(expected, callback.answer.await_args.args[0])

    async def test_remote_inactive_mirror_still_replays_authoritative_command(self):
        settings_obj = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=99,
        )
        offer = SimpleNamespace(
            id=5,
            user_id=4,
            status=OfferStatus.EXPIRED,
            home_server="iran",
            offer_public_id="ofr_bot_remote_retry_123456",
        )
        callback = make_callback()
        factory = FakeSessionFactory(FakeSession(offer))

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal",
            new=factory,
        ), patch(
            "bot.handlers.trade_manage.settings.offer_expiry_command_receipts_enabled",
            True,
        ), patch(
            "bot.handlers.trade_manage.is_remote_home",
            return_value=True,
        ), patch(
            "bot.handlers.trade_manage.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_manage.forward_offer_expiry_to_home_server",
            new=AsyncMock(return_value=(200, {"expired": True, "replayed": True})),
        ) as forward_mock, patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="MENU"),
        ):
            await handle_expire_offer(
                callback,
                SimpleNamespace(offer_id=5),
                user=SimpleNamespace(id=4),
                bot=SimpleNamespace(),
            )

        forward_mock.assert_awaited_once()
        payload = forward_mock.await_args.args[1]
        self.assertIn("command_id", payload)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        callback.answer.assert_awaited_once_with()
        self.assertIn("منقضی شد", callback.message.answer.await_args.args[0])

    async def test_queue_mode_not_found_is_a_durable_callback_without_direct_fallback(self):
        settings_obj = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=99,
        )
        callback = make_callback()
        session = FakeSession(None)
        session.commit = AsyncMock()

        with patch(
            "bot.handlers.trade_manage.get_trading_settings",
            return_value=settings_obj,
        ), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal",
            new=FakeSessionFactory(session),
        ), patch(
            "bot.handlers.trade_manage.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="queue-v1"),
        ), patch(
            "bot.handlers.trade_manage.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_manage.enqueue_telegram_callback_answer",
            new=AsyncMock(),
        ) as enqueue_callback:
            await handle_expire_offer(
                callback,
                SimpleNamespace(offer_id=5),
                user=SimpleNamespace(id=4),
                bot=SimpleNamespace(),
            )

        enqueue_callback.assert_awaited_once()
        self.assertIn("لفظ یافت نشد", enqueue_callback.await_args.kwargs["text"])
        session.commit.assert_awaited_once()
        callback.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
