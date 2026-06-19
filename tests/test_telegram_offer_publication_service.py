import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_offer_publication_service as publication_service
from models.offer import OfferStatus
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, state=None):
        self.state = state
        self.added = []
        self.execute = AsyncMock(return_value=FakeExecuteResult(state))
        self.flush = AsyncMock()

    def add(self, item):
        self.added.append(item)
        self.state = item


def make_offer(**overrides):
    data = {
        "id": 8,
        "offer_public_id": "ofr_pub_8",
        "home_server": "iran",
        "version_id": 3,
        "status": OfferStatus.ACTIVE,
        "channel_message_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramOfferPublicationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_publish_attempt_reuses_sent_publication_state(self):
        offer = make_offer()
        state = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        state.telegram_message_id = 555
        state.surface_resource_id = "555"
        db = FakeDB(state=state)
        send_mock = AsyncMock(return_value=999)

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertEqual(result.message_id, 555)
        self.assertEqual(result.skipped_reason, "already_published")
        self.assertEqual(offer.channel_message_id, 555)
        send_mock.assert_not_awaited()

    async def test_publish_success_records_telegram_result_for_sync_back(self):
        offer = make_offer()
        db = FakeDB()
        send_mock = AsyncMock(return_value=777)

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"), patch.object(
            publication_service.settings,
            "channel_id",
            -100,
        ):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertTrue(result.sent_new_message)
        self.assertEqual(result.message_id, 777)
        self.assertEqual(offer.channel_message_id, 777)
        self.assertEqual(db.added[0].status, OfferPublicationStatus.SENT)
        self.assertEqual(db.added[0].telegram_chat_id, -100)
        self.assertEqual(db.added[0].telegram_message_id, 777)
        self.assertEqual(db.added[0].surface_resource_id, "777")
        self.assertEqual(db.added[0].dedupe_key, "offer-publication:telegram_channel:ofr_pub_8")

    async def test_publish_failure_is_recorded_as_retryable_failed_state(self):
        offer = make_offer()
        db = FakeDB()
        send_mock = AsyncMock(return_value=None)

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertEqual(result.status, OfferPublicationStatus.FAILED)
        self.assertEqual(result.error_code, "telegram_send_empty_result")
        self.assertIsNone(result.message_id)
        self.assertIsNone(offer.channel_message_id)
        self.assertEqual(db.added[0].status, OfferPublicationStatus.FAILED)
        self.assertEqual(db.added[0].error_code, "telegram_send_empty_result")
        self.assertIsNotNone(db.added[0].last_attempt_at)

    async def test_existing_legacy_message_id_backfills_publication_state_without_send(self):
        offer = make_offer(channel_message_id=444)
        db = FakeDB()
        send_mock = AsyncMock(return_value=888)

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"), patch.object(
            publication_service.settings,
            "channel_id",
            -100,
        ):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertEqual(result.message_id, 444)
        self.assertEqual(result.skipped_reason, "legacy_message_id_backfilled")
        self.assertEqual(db.added[0].status, OfferPublicationStatus.SENT)
        self.assertEqual(db.added[0].telegram_message_id, 444)
        send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
