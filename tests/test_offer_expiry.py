import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.orm.exc import StaleDataError

from core import offer_expiry
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode
from models.offer import OfferStatus


def scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


class OfferExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_channel_buttons_is_queue_owned_without_gateway_call(self):
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)
        with patch(
            "core.offer_expiry.current_server",
            return_value="foreign",
        ), patch(
            "core.offer_expiry.configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch(
            "core.telegram_gateway.edit_message_reply_markup",
            new=AsyncMock(),
        ) as gateway:
            await offer_expiry.remove_channel_buttons(10)

        gateway.assert_not_awaited()

    async def test_remove_channel_buttons_skips_when_token_or_channel_missing(self):
        with patch.object(offer_expiry.settings, "bot_token", None), \
             patch.object(offer_expiry.settings, "channel_id", None), \
             patch("core.telegram_gateway.os.getenv", return_value=None), \
             patch("core.telegram_gateway.httpx.AsyncClient") as async_client:
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
               patch("core.telegram_gateway.httpx.AsyncClient", return_value=ClientSpy()):
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
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=FailingClient()), \
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

    async def test_next_expiry_delay_uses_nearest_active_offer_deadline(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=2)
        created_at = offer_expiry.utc_now_naive() - timedelta(minutes=2) + timedelta(seconds=0.5)
        session = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(created_at)))

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"):
            delay = await offer_expiry.get_next_expiry_delay_seconds()

        self.assertGreaterEqual(delay, offer_expiry.MIN_DEADLINE_SLEEP_SECONDS)
        self.assertLess(delay, offer_expiry.CHECK_INTERVAL)

    async def test_expire_stale_offers_expires_offers_and_runs_side_effects(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        expired_offers = [
            SimpleNamespace(id=1, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=101, user_id=11),
            SimpleNamespace(id=2, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None, user_id=11),
            SimpleNamespace(id=3, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=303, user_id=22),
        ]
        session = SimpleNamespace(execute=AsyncMock(return_value=scalars_result(expired_offers)), commit=AsyncMock())

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"), \
             patch("core.services.offer_expiry_service.current_server", return_value="foreign"), \
             patch("core.offer_expiry.apply_remote_stale_channel_state", AsyncMock(return_value=0)), \
             patch("core.offer_expiry.apply_offer_channel_state", AsyncMock()) as apply_offer_channel_state, \
             patch("core.events.publish_event_sync") as publish_event_sync, \
             patch("core.cache.decr_active_offer_count", AsyncMock()) as decr_active_offer_count:
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 3)
        self.assertEqual(session.execute.await_count, 1)
        session.commit.assert_awaited_once()
        self.assertEqual([offer.status for offer in expired_offers], [OfferStatus.EXPIRED] * 3)
        self.assertEqual([offer.expire_reason for offer in expired_offers], ["time_limit"] * 3)
        self.assertEqual([offer.expire_source_surface for offer in expired_offers], ["system"] * 3)
        self.assertEqual([offer.expire_source_server for offer in expired_offers], ["foreign"] * 3)
        self.assertTrue(all(offer.expired_by_user_id is None for offer in expired_offers))
        self.assertTrue(all(offer.expired_by_actor_user_id is None for offer in expired_offers))
        self.assertEqual(apply_offer_channel_state.await_count, 3)
        applied_offer_ids = [call.args[0].id for call in apply_offer_channel_state.await_args_list]
        self.assertEqual(applied_offer_ids, [1, 2, 3])
        self.assertTrue(all(call.kwargs["reason"] == "auto_expire_time_limit" for call in apply_offer_channel_state.await_args_list))
        self.assertEqual(publish_event_sync.call_count, 3)
        publish_event_sync.assert_any_call("offer:expired", {"id": 1})
        publish_event_sync.assert_any_call("offer:expired", {"id": 2})
        publish_event_sync.assert_any_call("offer:expired", {"id": 3})
        self.assertEqual(decr_active_offer_count.await_count, 3)
        decr_active_offer_count.assert_any_await(11)
        decr_active_offer_count.assert_any_await(22)

    async def test_expire_stale_offers_retries_after_concurrent_expiry_stale_row_conflict(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        first_scan_offer = SimpleNamespace(id=10, status=OfferStatus.ACTIVE, home_server="foreign", user_id=10)
        retry_scan_offer = SimpleNamespace(id=11, status=OfferStatus.ACTIVE, home_server="foreign", user_id=11)
        expiry_result = SimpleNamespace(expired_count=1, expired_offers=(retry_scan_offer,))
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalars_result([first_scan_offer]), scalars_result([retry_scan_offer])]),
            rollback=AsyncMock(),
        )

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        expire_authoritatively = AsyncMock(side_effect=[StaleDataError("stale offer update"), expiry_result])

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"), \
             patch("core.offer_expiry.expire_offers_authoritatively", expire_authoritatively), \
             patch("core.offer_expiry.apply_remote_stale_channel_state", AsyncMock(return_value=0)), \
             patch("core.offer_expiry.apply_offer_channel_state", AsyncMock()) as apply_offer_channel_state, \
             patch("core.events.publish_event_sync"), \
             patch("core.cache.decr_active_offer_count", AsyncMock()):
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 1)
        self.assertEqual(session.execute.await_count, 2)
        session.rollback.assert_awaited_once()
        self.assertEqual(expire_authoritatively.await_count, 2)
        self.assertEqual(expire_authoritatively.await_args_list[0].args[1], [first_scan_offer])
        self.assertEqual(expire_authoritatively.await_args_list[1].args[1], [retry_scan_offer])
        apply_offer_channel_state.assert_awaited_once()
        self.assertEqual(apply_offer_channel_state.await_args.args[0].id, 11)

    async def test_expire_stale_offers_tolerates_realtime_and_cache_failures(self):
        settings_obj = SimpleNamespace(offer_expiry_minutes=15)
        expired_offers = [SimpleNamespace(id=5, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=505, user_id=30)]
        session = SimpleNamespace(execute=AsyncMock(return_value=scalars_result(expired_offers)), commit=AsyncMock())

        class SessionManager:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=settings_obj)), \
             patch("core.offer_expiry.AsyncSessionLocal", return_value=SessionManager()), \
             patch("core.offer_expiry.current_server", return_value="foreign"), \
             patch("core.services.offer_expiry_service.current_server", return_value="foreign"), \
             patch("core.offer_expiry.apply_remote_stale_channel_state", AsyncMock(return_value=0)), \
             patch("core.offer_expiry.apply_offer_channel_state", AsyncMock()) as apply_offer_channel_state, \
             patch("core.events.publish_event_sync", side_effect=RuntimeError("pubsub down")), \
             patch("core.cache.decr_active_offer_count", AsyncMock(side_effect=RuntimeError("redis down"))):
            count = await offer_expiry.expire_stale_offers()

        self.assertEqual(count, 1)
        session.commit.assert_awaited_once()
        apply_offer_channel_state.assert_awaited_once()
        self.assertEqual(apply_offer_channel_state.await_args.args[0].id, 5)

    async def test_remote_stale_channel_state_is_presentation_only_on_foreign(self):
        stale_offer = SimpleNamespace(
            id=77,
            offer_type="sell",
            commodity=SimpleNamespace(name="سکه"),
            quantity=40,
            remaining_quantity=40,
            price=142000,
            is_wholesale=True,
            lot_sizes=None,
            notes="شب میدم",
            status=offer_expiry.OfferStatus.ACTIVE,
            expire_reason=None,
            channel_message_id=707,
        )
        session = SimpleNamespace(execute=AsyncMock(return_value=scalars_result([stale_offer])))
        offer_expiry._remote_channel_expiry_presented_at.clear()

        with patch("core.offer_expiry.current_server", return_value="foreign"), patch(
            "core.offer_expiry.apply_offer_channel_state", AsyncMock(return_value=True)
        ) as apply_state_mock:
            count = await offer_expiry.apply_remote_stale_channel_state(session, datetime(2026, 1, 2, 12, 0, 0))

        self.assertEqual(count, 1)
        apply_state_mock.assert_awaited_once()
        presentation_offer = apply_state_mock.await_args.args[0]
        self.assertEqual(presentation_offer.id, 77)
        self.assertEqual(presentation_offer.status, offer_expiry.OfferStatus.EXPIRED)
        self.assertEqual(presentation_offer.expire_reason, "time_limit")
        self.assertEqual(stale_offer.status, offer_expiry.OfferStatus.ACTIVE)

        with patch("core.offer_expiry.current_server", return_value="foreign"), patch(
            "core.offer_expiry.apply_offer_channel_state", AsyncMock(return_value=True)
        ) as replay_apply_state_mock:
            replay_count = await offer_expiry.apply_remote_stale_channel_state(session, datetime(2026, 1, 2, 12, 0, 0))

        self.assertEqual(replay_count, 0)
        replay_apply_state_mock.assert_not_awaited()

    async def test_offer_expiry_loop_logs_start_success_and_failure_cycles(self):
        sleep_calls = []

        async def stop_after_second_sleep(_delay):
            sleep_calls.append(_delay)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        with patch("core.offer_expiry.expire_stale_offers", AsyncMock(side_effect=[2, RuntimeError("boom")])), \
             patch("core.offer_expiry.get_next_expiry_delay_seconds", AsyncMock(side_effect=[0.1, 0.2])), \
             patch("core.offer_expiry.asyncio.sleep", side_effect=stop_after_second_sleep), \
             patch.object(offer_expiry, "logger") as logger:
            with self.assertRaises(asyncio.CancelledError):
                await offer_expiry.offer_expiry_loop()

        logger.info.assert_any_call(f"⏰ Offer expiry loop started (deadline-aware, max sleep {offer_expiry.CHECK_INTERVAL}s)")
        logger.info.assert_any_call("⏰ Expiry cycle: 2 offers expired")
        logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
