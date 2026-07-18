import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from core.services import telegram_offer_publication_service as publication_service
from core.services.offer_publication_state_service import (
    CanonicalPublicationIdentityError,
)
from models.offer import OfferStatus
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, state=None, flush_error=None, race_state=None):
        self.state = state
        self.race_state = race_state
        self.added = []
        self.execute_calls = []
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock()
        self.flush_error = flush_error

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return FakeExecuteResult(self.state)

    def begin_nested(self):
        return AsyncNullContext()

    async def _flush(self):
        if self.flush_error is not None:
            error = self.flush_error
            self.flush_error = None
            if self.race_state is not None:
                self.state = self.race_state
            raise error

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
    async def test_queue_owner_rejects_legacy_direct_publication_before_side_effects(self):
        offer = make_offer()
        db = FakeDB()
        send_mock = AsyncMock(return_value=777)

        with patch.object(
            publication_service,
            "configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            ),
        ), patch.object(
            publication_service,
            "current_server",
            return_value="foreign",
        ) as server_mock:
            with self.assertRaises(
                publication_service.TelegramDeliveryRuntimeConfigurationError
            ):
                await publication_service.publish_offer_to_telegram_channel_once(
                    db,
                    offer,
                    SimpleNamespace(id=1),
                    send_offer_to_channel=send_mock,
                )

        server_mock.assert_not_called()
        send_mock.assert_not_awaited()
        self.assertEqual(db.execute_calls, [])

    async def test_get_or_create_recovers_from_duplicate_insert_race(self):
        offer = make_offer()
        existing = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        existing.telegram_message_id = 555
        existing.telegram_chat_id = -100
        db = FakeDB(
            flush_error=IntegrityError("insert", {}, Exception("duplicate key")),
            race_state=existing,
        )

        state = await publication_service.get_or_create_telegram_publication_state(db, offer)

        self.assertIs(state, existing)
        self.assertEqual(state.telegram_message_id, 555)
        self.assertEqual(len(db.execute_calls), 2)

    async def test_duplicate_publish_attempt_reuses_sent_publication_state(self):
        offer = make_offer()
        state = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        state.telegram_message_id = 555
        state.telegram_chat_id = -100
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
        db.refresh.assert_awaited_once_with(
            offer,
            attribute_names=["channel_message_id", "status", "version_id"],
        )
        send_mock.assert_not_awaited()

    async def test_waiting_publisher_refreshes_stale_offer_before_reusing_sent_state(self):
        offer = make_offer(version_id=1)
        state = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        state.telegram_message_id = 556
        state.telegram_chat_id = -100
        state.surface_resource_id = "556"
        db = FakeDB(state=state)

        async def refresh_offer(refreshed_offer, *, attribute_names):
            self.assertEqual(attribute_names, ["channel_message_id", "status", "version_id"])
            refreshed_offer.version_id = 2
            refreshed_offer.channel_message_id = 556

        db.refresh.side_effect = refresh_offer
        send_mock = AsyncMock(return_value=999)

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertEqual(result.message_id, 556)
        self.assertEqual(result.skipped_reason, "already_published")
        self.assertEqual(offer.version_id, 2)
        send_mock.assert_not_awaited()

    def test_publication_state_message_identity_wins_over_legacy_offer_mirror(self):
        offer = make_offer(channel_message_id=999)
        state = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        state.telegram_chat_id = -100
        state.telegram_message_id = 555
        state.surface_resource_id = "555"

        message_id = publication_service.apply_existing_telegram_publication_to_offer(
            offer,
            state,
        )

        self.assertEqual(message_id, 555)
        self.assertEqual(offer.channel_message_id, 555)
        self.assertEqual(state.telegram_message_id, 555)

    def test_invalid_canonical_state_is_not_copied_to_offer(self):
        offer = make_offer(channel_message_id=None)
        state = publication_service.build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            status=OfferPublicationStatus.SENT,
        )
        state.publisher_bot_identity = "channel_editor"
        state.telegram_chat_id = -100
        state.telegram_message_id = 555

        with self.assertRaises(CanonicalPublicationIdentityError):
            publication_service.apply_existing_telegram_publication_to_offer(
                offer,
                state,
            )

        self.assertIsNone(offer.channel_message_id)

    async def test_stale_retry_preserves_successful_telegram_side_effect_without_resend(self):
        offer = make_offer(version_id=1, channel_message_id=557)
        db = FakeDB()

        async def refresh_offer(refreshed_offer, *, attribute_names):
            self.assertEqual(attribute_names, ["channel_message_id", "status", "version_id"])
            refreshed_offer.version_id = 2
            refreshed_offer.channel_message_id = None

        db.refresh.side_effect = refresh_offer
        send_mock = AsyncMock(return_value=999)

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

        self.assertEqual(result.message_id, 557)
        self.assertEqual(result.skipped_reason, "legacy_message_id_backfilled")
        self.assertEqual(offer.channel_message_id, 557)
        self.assertEqual(db.state.telegram_message_id, 557)
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

    async def test_publish_failure_preserves_classified_rate_limit_result(self):
        offer = make_offer()
        db = FakeDB()
        send_mock = AsyncMock(
            return_value=publication_service.TelegramOfferSendResult(
                message_id=None,
                response_class="429",
                status_code=429,
                retry_after_seconds=11,
                error_code="telegram_rate_limited",
                error_message="Too Many Requests",
            )
        )

        with patch("core.services.telegram_offer_publication_service.current_server", return_value="foreign"):
            result = await publication_service.publish_offer_to_telegram_channel_once(
                db,
                offer,
                SimpleNamespace(id=1),
                send_offer_to_channel=send_mock,
            )

        self.assertEqual(result.status, OfferPublicationStatus.FAILED)
        self.assertTrue(result.send_attempted)
        self.assertEqual(result.error_code, "telegram_rate_limited")
        self.assertEqual(result.response_class, "429")
        self.assertEqual(result.retry_after_seconds, 11)
        self.assertEqual(db.added[0].status, OfferPublicationStatus.FAILED)
        self.assertEqual(db.added[0].error_code, "telegram_rate_limited")
        self.assertEqual(db.added[0].error_message, "Too Many Requests")

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
