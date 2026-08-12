import unittest
from unittest.mock import AsyncMock

from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)


class TelegramPublisherLaneRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _enqueue(self, db, **overrides):
        values = {
            "current_server": "foreign",
            "feeder": TelegramFeederKind.OFFER_CONTROL,
            "source_natural_id": "offer-public-id",
            "source_version": 1,
            "action": TelegramDeliveryAction.OFFER_PUBLISH,
            "bot_identity": "publisher_1",
            "destination_key": "channel:-100123",
            "destination_class": TelegramDestinationClass.CHANNEL,
            "method": "sendMessage",
            "payload": {"chat_id": -100123, "text": "redacted"},
            "template_version": "offer-channel-v1",
        }
        values.update(overrides)
        return await enqueue_telegram_delivery_job(db, **values)

    async def test_publisher_rejects_non_owner_methods_before_database_access(self):
        db = AsyncMock()
        with self.assertRaisesRegex(
            TelegramDeliveryQueueValidationError,
            "publisher_lane_route_not_allowlisted",
        ):
            await self._enqueue(db, method="sendDocument")
        db.execute.assert_not_awaited()

    async def test_publisher_rejects_private_or_non_offer_work_before_database_access(self):
        cases = (
            {"destination_class": TelegramDestinationClass.PRIVATE},
            {"action": TelegramDeliveryAction.ADMIN_BROADCAST},
        )
        for values in cases:
            db = AsyncMock()
            with self.subTest(values=values), self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "publisher_lane_route_not_allowlisted",
            ):
                await self._enqueue(db, **values)
            db.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
