import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import offer_expiry


def scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


class OfferExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_channel_buttons_skips_when_token_or_channel_missing(self):
        with patch.object(offer_expiry.settings, "bot_token", None), \
             patch.object(offer_expiry.settings, "channel_id", None), \
             patch("core.offer_expiry.os.getenv", return_value=None), \
             patch("httpx.AsyncClient") as async_client:
            await offer_expiry.remove_channel_buttons(10)

        async_client.assert_not_called()

    async def test_remove_channel_buttons_posts_expected_reply_markup_request(self):
        recorded = []

        class ClientSpy:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json, timeout):
                recorded.append((url, json, timeout))

        with patch.object(offer_expiry.settings, "bot_token", "bot-token"), \
             patch.object(offer_expiry.settings, "channel_id", -100123), \
               patch("httpx.AsyncClient", return_value=ClientSpy()):
            await offer_expiry.remove_channel_buttons(77)

        self.assertEqual(
            recorded,
            [(
                "https://api.telegram.org/botbot-token/editMessageReplyMarkup",
                {"chat_id": -100123, "message_id": 77},
                10,
            )],
        )

    async def test_remove_channel_buttons_logs_debug_on_request_failure(self):
        class FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json, timeout):
                raise RuntimeError('telegram down')

        with patch.object(offer_expiry.settings, "bot_token", "bot-token"), \
             patch.object(offer_expiry.settings, "channel_id", -100123), \
             patch("httpx.AsyncClient", return_value=FailingClient()), \
             patch.object(offer_expiry, "logger") as logger:
            await offer_expiry.remove_channel_buttons(88)

        logger.debug.assert_called_once()

    async def test_expire_stale_offers_returns_zero_when_disabled(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=0)

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)):
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 0)

    async def test_expire_stale_offers_returns_zero_when_no_stale_offers_found(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        session = SimpleNamespace(execute=AsyncMock(return_value=scalars_result([])), commit=AsyncMock())

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"):
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 0)
        session.commit.assert_not_awaited()

    async def test_expire_stale_offers_expires_offers_and_runs_side_effects(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        expired_offers = [
            SimpleNamespace(id=1, channel_message_id=101, user_id=11),
            SimpleNamespace(id=2, channel_message_id=None, user_id=11),
            SimpleNamespace(id=3, channel_message_id=303, user_id=22),
        ]
        session = SimpleNamespace(execute=AsyncMock(side_effect=[scalars_result(expired_offers), Mock()]), commit=AsyncMock())

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"), \
             patch("core.offer_expiry.remove_channel_buttons", AsyncMock()) as remove_channel_buttons, \
             patch("core.events.publish_event_sync") as publish_event_sync, \
             patch("core.cache.decr_active_offer_count", AsyncMock()) as decr_active_offer_count:
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 3)
        self.assertEqual(session.execute.await_count, 2)
        session.commit.assert_awaited_once()
        self.assertEqual(remove_channel_buttons.await_count, 2)
        remove_channel_buttons.assert_any_await(101)
        remove_channel_buttons.assert_any_await(303)
        self.assertEqual(publish_event_sync.call_count, 3)
        publish_event_sync.assert_any_call("offer:expired", {"id": 1})
        publish_event_sync.assert_any_call("offer:expired", {"id": 2})
        publish_event_sync.assert_any_call("offer:expired", {"id": 3})
        self.assertEqual(decr_active_offer_count.await_count, 3)
        decr_active_offer_count.assert_any_await(11)
        decr_active_offer_count.assert_any_await(22)

    async def test_expire_stale_offers_tolerates_realtime_and_cache_failures(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        expired_offers = [SimpleNamespace(id=5, channel_message_id=505, user_id=30)]
        session = SimpleNamespace(execute=AsyncMock(side_effect=[scalars_result(expired_offers), Mock()]), commit=AsyncMock())

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"), \
             patch("core.offer_expiry.remove_channel_buttons", AsyncMock()) as remove_channel_buttons, \
             patch("core.events.publish_event_sync", side_effect=RuntimeError("pubsub down")), \
             patch("core.cache.decr_active_offer_count", AsyncMock(side_effect=RuntimeError("redis down"))):
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 1)
        session.commit.assert_awaited_once()
        remove_channel_buttons.assert_awaited_once_with(505)

    async def test_offer_expiry_loop_logs_start_success_and_failure_cycles(self):
        sleep_calls = []

        async def stop_after_second_sleep(_delay):
            sleep_calls.append(_delay)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        with patch("core.offer_expiry.expire_stale_offers", AsyncMock(side_effect=[2, RuntimeError("boom")])), \
             patch("core.offer_expiry.asyncio.sleep", side_effect=stop_after_second_sleep), \
             patch.object(offer_expiry, "logger") as logger:
            with self.assertRaises(asyncio.CancelledError):
                await offer_expiry.offer_expiry_loop()

        logger.info.assert_any_call(f"⏰ Offer expiry loop started (check every {offer_expiry.CHECK_INTERVAL}s)")
        logger.info.assert_any_call("⏰ Expiry cycle: 2 offers expired")
        logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()