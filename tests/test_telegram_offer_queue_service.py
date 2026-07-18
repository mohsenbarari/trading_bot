import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from core.services import telegram_offer_queue_service as service
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.utils import utc_now
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationSurface


def make_offer(**overrides):
    data = {
        "id": 10,
        "offer_public_id": "ofr_queue_10",
        "version_id": 3,
        "status": OfferStatus.ACTIVE,
        "offer_type": OfferType.BUY,
        "settlement_type": "cash",
        "commodity": SimpleNamespace(name="سکه"),
        "quantity": 20,
        "remaining_quantity": 20,
        "price": 100_000,
        "is_wholesale": True,
        "lot_sizes": None,
        "notes": None,
        "created_at": utc_now() - timedelta(seconds=10),
        "updated_at": utc_now() - timedelta(seconds=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_state(**overrides):
    data = {
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
        "publisher_bot_identity": "primary",
        "telegram_chat_id": -1001234567890,
        "telegram_message_id": 777,
        "surface_resource_id": "777",
        "offer_version_id": 2,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramOfferQueueServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_action_mapping_covers_publish_partial_and_terminal(self):
        self.assertEqual(
            service.offer_delivery_action(
                make_offer(),
                make_state(telegram_message_id=None, surface_resource_id=None),
            ),
            TelegramDeliveryAction.OFFER_PUBLISH,
        )
        self.assertEqual(
            service.offer_delivery_action(
                make_offer(remaining_quantity=10),
                make_state(),
            ),
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
        )
        self.assertEqual(
            service.offer_delivery_action(
                make_offer(status=OfferStatus.COMPLETED, remaining_quantity=0),
                make_state(),
            ),
            TelegramDeliveryAction.TRADED_OFFER_EDIT,
        )
        self.assertIsNone(
            service.offer_delivery_action(
                make_offer(status=OfferStatus.EXPIRED),
                make_state(telegram_message_id=None, surface_resource_id=None),
            )
        )

    def test_active_zero_remaining_fails_closed(self):
        with self.assertRaisesRegex(
            service.TelegramOfferQueueError,
            "active_zero_remaining",
        ):
            service.offer_delivery_action(
                make_offer(remaining_quantity=0),
                make_state(),
            )

    async def test_publish_is_primary_with_offer_deadline(self):
        offer = make_offer()
        state = make_state(telegram_message_id=None, surface_resource_id=None)
        enqueue = AsyncMock(
            return_value=SimpleNamespace(created=True, job=SimpleNamespace(id=1))
        )
        with patch.object(
            service,
            "enqueue_telegram_delivery_job",
            new=enqueue,
        ), patch.object(
            service,
            "_supersede_obsolete_offer_jobs",
            new=AsyncMock(return_value=0),
        ):
            result = await service.enqueue_current_offer_delivery(
                object(),
                current_server="foreign",
                offer=offer,
                state=state,
                expected_channel_id=-1001234567890,
                offer_expiry_minutes=2,
            )

        self.assertTrue(result.queue_result.created)
        kwargs = enqueue.await_args.kwargs
        self.assertEqual(kwargs["action"], TelegramDeliveryAction.OFFER_PUBLISH)
        self.assertEqual(kwargs["bot_identity"], "primary")
        self.assertEqual(kwargs["method"], "sendMessage")
        self.assertEqual(
            kwargs["freshness_deadline_at"],
            service.offer_publication_freshness_deadline(
                offer,
                offer_expiry_minutes=2,
            ),
        )
        self.assertNotIn("message_id", kwargs["payload"])

    async def test_edit_uses_editor_only_when_enabled(self):
        offer = make_offer(remaining_quantity=10)
        first_enqueued_at = utc_now() - timedelta(minutes=6)
        enqueue = AsyncMock(
            return_value=SimpleNamespace(created=True, job=SimpleNamespace(id=2))
        )
        with patch.object(
            service.settings,
            "telegram_delivery_queue_channel_editor_enabled",
            True,
        ), patch.object(
            service,
            "enqueue_telegram_delivery_job",
            new=enqueue,
        ), patch.object(
            service,
            "_supersede_obsolete_offer_jobs",
            new=AsyncMock(return_value=0),
        ), patch.object(
            service,
            "_offer_edit_first_enqueued_at",
            new=AsyncMock(return_value=first_enqueued_at),
        ):
            await service.enqueue_current_offer_delivery(
                object(),
                current_server="foreign",
                offer=offer,
                state=make_state(),
                expected_channel_id=-1001234567890,
                offer_expiry_minutes=2,
            )

        kwargs = enqueue.await_args.kwargs
        self.assertEqual(kwargs["action"], TelegramDeliveryAction.PARTIAL_OFFER_EDIT)
        self.assertEqual(kwargs["bot_identity"], "channel_editor")
        self.assertEqual(kwargs["method"], "editMessageText")
        self.assertEqual(kwargs["payload"]["message_id"], 777)
        self.assertEqual(kwargs["eligible_at"], first_enqueued_at)

    async def test_invalid_action_refresh_uses_trade_feeder_and_editor_lane(self):
        enqueue = AsyncMock(
            return_value=SimpleNamespace(created=True, job=SimpleNamespace(id=3))
        )
        with patch.object(
            service.settings,
            "telegram_delivery_queue_channel_editor_enabled",
            True,
        ), patch.object(
            service,
            "enqueue_telegram_delivery_job",
            new=enqueue,
        ), patch.object(
            service,
            "_supersede_obsolete_offer_jobs",
            new=AsyncMock(return_value=0),
        ):
            await service.enqueue_current_offer_delivery(
                object(),
                current_server="foreign",
                offer=make_offer(remaining_quantity=10),
                state=make_state(),
                expected_channel_id=-1001234567890,
                offer_expiry_minutes=None,
                action=TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
            )

        kwargs = enqueue.await_args.kwargs
        self.assertEqual(kwargs["feeder"].value, "trade")
        self.assertEqual(
            kwargs["action"],
            TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
        )
        self.assertEqual(kwargs["bot_identity"], "channel_editor")
        self.assertEqual(kwargs["method"], "editMessageText")

    async def test_expired_unpublished_offer_is_not_enqueued(self):
        enqueue = AsyncMock()
        supersede = AsyncMock(return_value=1)
        with patch.object(
            service,
            "enqueue_telegram_delivery_job",
            new=enqueue,
        ), patch.object(
            service,
            "_supersede_obsolete_offer_jobs",
            new=supersede,
        ):
            result = await service.enqueue_current_offer_delivery(
                object(),
                current_server="foreign",
                offer=make_offer(status=OfferStatus.EXPIRED),
                state=make_state(
                    telegram_message_id=None,
                    surface_resource_id=None,
                ),
                expected_channel_id=-1001234567890,
                offer_expiry_minutes=2,
            )

        self.assertEqual(
            result.skipped_reason,
            "offer_not_publishable_and_never_published",
        )
        enqueue.assert_not_awaited()
        supersede.assert_awaited_once()

    async def test_publication_after_deadline_is_not_enqueued(self):
        enqueue = AsyncMock()
        with patch.object(
            service,
            "enqueue_telegram_delivery_job",
            new=enqueue,
        ), patch.object(
            service,
            "_supersede_obsolete_offer_jobs",
            new=AsyncMock(return_value=0),
        ):
            result = await service.enqueue_current_offer_delivery(
                object(),
                current_server="foreign",
                offer=make_offer(created_at=utc_now() - timedelta(minutes=3)),
                state=make_state(
                    telegram_message_id=None,
                    surface_resource_id=None,
                ),
                expected_channel_id=-1001234567890,
                offer_expiry_minutes=2,
            )

        self.assertEqual(result.skipped_reason, "offer_publication_deadline_passed")
        enqueue.assert_not_awaited()

    async def test_edit_candidate_query_applies_internal_rank_then_newest_offer(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        )

        candidates = await service.load_offer_edit_queue_candidates(
            db,
            limit=25,
            catch_up_due_ranks=frozenset({0}),
        )

        self.assertEqual(candidates, [])
        statement = db.execute.await_args.args[0]
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("CASE WHEN", sql)
        self.assertIn("offers.remaining_quantity < offers.quantity", sql)
        self.assertIn("telegram_delivery_jobs_1.eligible_at", sql)
        self.assertIn("offers.created_at DESC", sql)
        self.assertLess(sql.index("CASE WHEN"), sql.index("offers.created_at DESC"))

    async def test_edit_success_counter_is_capped_and_stale_success_resets_rank(self):
        now = utc_now()
        feeder_state = SimpleNamespace(
            fresh_success_counts={"0": 19},
            updated_at=None,
        )
        result = SimpleNamespace(
            scalar_one_or_none=lambda: feeder_state,
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=result), flush=AsyncMock())
        fresh_job = SimpleNamespace(
            feeder_kind="offer_edit",
            feeder_rank=0,
            eligible_at=now - timedelta(seconds=30),
            created_at=now - timedelta(seconds=30),
        )

        counts = await service.record_offer_edit_delivery_success(
            db,
            fresh_job,
            now=now,
        )

        self.assertEqual(counts[0], 20)
        self.assertEqual(feeder_state.fresh_success_counts, {"0": 20})

        stale_job = SimpleNamespace(
            feeder_kind="offer_edit",
            feeder_rank=0,
            eligible_at=now - timedelta(minutes=6),
            created_at=now - timedelta(minutes=6),
        )
        counts = await service.record_offer_edit_delivery_success(
            db,
            stale_job,
            now=now,
        )

        self.assertEqual(counts[0], 0)
        self.assertEqual(feeder_state.fresh_success_counts, {"0": 0})


if __name__ == "__main__":
    unittest.main()
