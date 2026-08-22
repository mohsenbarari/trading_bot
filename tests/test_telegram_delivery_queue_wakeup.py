import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import telegram_delivery_queue_wakeup as wakeup


class TelegramDeliveryQueueWakeupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        wakeup.reset_telegram_wakeup_events_for_test()

    def tearDown(self):
        wakeup.reset_telegram_wakeup_events_for_test()

    async def test_transactional_emitters_use_fixed_channels_and_safe_payloads(self):
        session = SimpleNamespace(execute=AsyncMock())

        await wakeup.emit_notification_outbox_wakeup(session)
        await wakeup.emit_delivery_queue_wakeup(
            session,
            bot_identity="primary",
        )

        self.assertEqual(session.execute.await_count, 2)
        first_parameters = session.execute.await_args_list[0].args[1]
        second_parameters = session.execute.await_args_list[1].args[1]
        self.assertEqual(
            first_parameters,
            {
                "channel": wakeup.TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL,
                "payload": "ready",
            },
        )
        self.assertEqual(
            second_parameters,
            {
                "channel": wakeup.TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
                "payload": "primary",
            },
        )

    async def test_committed_notifications_wake_only_the_intended_topic(self):
        outbox_event = wakeup.notification_outbox_wakeup_event()
        primary_event = wakeup.delivery_queue_wakeup_event("primary")
        publisher_event = wakeup.delivery_queue_wakeup_event("publisher_1")

        wakeup._handle_wakeup_notification(
            None,
            1,
            wakeup.TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL,
            "ready",
        )
        self.assertTrue(outbox_event.is_set())
        self.assertFalse(primary_event.is_set())

        wakeup._handle_wakeup_notification(
            None,
            1,
            wakeup.TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
            "publisher_1",
        )
        self.assertTrue(publisher_event.is_set())
        self.assertFalse(primary_event.is_set())

        wakeup._handle_wakeup_notification(
            None,
            1,
            wakeup.TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
            "unknown",
        )
        self.assertFalse(primary_event.is_set())

    async def test_wait_has_immediate_signal_and_bounded_timeout_paths(self):
        event = asyncio.Event()
        event.set()
        self.assertTrue(
            await wakeup.wait_for_telegram_wakeup(
                event,
                timeout_seconds=1.0,
            )
        )
        event.clear()
        self.assertFalse(
            await wakeup.wait_for_telegram_wakeup(
                event,
                timeout_seconds=0.001,
            )
        )

    async def test_unknown_delivery_identity_is_rejected_before_database_touch(self):
        session = SimpleNamespace(execute=AsyncMock())
        with self.assertRaisesRegex(
            ValueError,
            "telegram_delivery_wakeup_identity_invalid",
        ):
            await wakeup.emit_delivery_queue_wakeup(
                session,
                bot_identity="unknown",
            )
        session.execute.assert_not_awaited()

    async def test_listener_cleanup_is_best_effort_for_closed_driver(self):
        driver = SimpleNamespace(
            remove_listener=AsyncMock(side_effect=ValueError("closed")),
        )

        await wakeup._remove_listeners(driver)

        self.assertEqual(driver.remove_listener.await_count, 2)


if __name__ == "__main__":
    unittest.main()
