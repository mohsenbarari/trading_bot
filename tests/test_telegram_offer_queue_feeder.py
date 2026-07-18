import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_offer_queue_feeder as feeder
from core.services.telegram_offer_queue_service import TelegramOfferQueueError
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.savepoint_entries = 0

    def begin_nested(self):
        session = self

        class Savepoint:
            async def __aenter__(self):
                session.savepoint_entries += 1
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Savepoint()


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def candidate(public_id):
    return SimpleNamespace(
        offer=SimpleNamespace(offer_public_id=public_id),
        state=SimpleNamespace(),
    )


class TelegramOfferQueueFeederTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_hands_off_publication_and_edit_candidates(self):
        session = FakeSession()
        runtime = SimpleNamespace(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            queue_worker_enabled=True,
        )
        publication_candidates = [candidate("ofr_publish")]
        edit_candidates = [candidate("ofr_edit")]
        results = [
            SimpleNamespace(
                queue_result=SimpleNamespace(created=True),
                skipped_reason=None,
            ),
            SimpleNamespace(
                queue_result=SimpleNamespace(created=False),
                skipped_reason=None,
            ),
        ]
        with patch.object(
            feeder,
            "assert_background_job_authority",
        ), patch.object(
            feeder,
            "configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch.object(
            feeder,
            "active_publication_is_gated",
            new=AsyncMock(return_value=False),
        ), patch.object(
            feeder,
            "get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch.object(
            feeder.settings,
            "channel_id",
            -100,
        ), patch.object(
            feeder,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch.object(
            feeder,
            "load_offer_publication_queue_candidates",
            new=AsyncMock(return_value=publication_candidates),
        ) as load_publication, patch.object(
            feeder,
            "load_offer_edit_queue_candidates",
            new=AsyncMock(return_value=edit_candidates),
        ) as load_edits, patch.object(
            feeder,
            "enqueue_current_offer_delivery",
            new=AsyncMock(side_effect=results),
        ) as enqueue:
            report = await feeder.run_telegram_offer_queue_handoff_cycle()

        self.assertEqual(report.publication_handoffs, 1)
        self.assertEqual(report.edit_handoffs, 0)
        self.assertEqual(report.deduplicated, 1)
        self.assertFalse(report.publication_gated)
        self.assertEqual(enqueue.await_count, 2)
        self.assertEqual(session.commit.await_count, 2)
        self.assertEqual(session.savepoint_entries, 2)
        session.rollback.assert_not_awaited()
        load_publication.assert_awaited_once()
        load_edits.assert_awaited_once()

    async def test_publication_gate_still_allows_independent_editor_feeder(self):
        session = FakeSession()
        runtime = SimpleNamespace(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            queue_worker_enabled=True,
        )
        with patch.object(
            feeder,
            "assert_background_job_authority",
        ), patch.object(
            feeder,
            "configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch.object(
            feeder,
            "active_publication_is_gated",
            new=AsyncMock(return_value=True),
        ), patch.object(
            feeder,
            "get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch.object(
            feeder.settings,
            "channel_id",
            -100,
        ), patch.object(
            feeder,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch.object(
            feeder,
            "load_offer_publication_queue_candidates",
            new=AsyncMock(),
        ) as load_publication, patch.object(
            feeder,
            "load_offer_edit_queue_candidates",
            new=AsyncMock(return_value=[candidate("ofr_edit")]),
        ) as load_edits, patch.object(
            feeder,
            "enqueue_current_offer_delivery",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    queue_result=SimpleNamespace(created=True),
                    skipped_reason=None,
                )
            ),
        ):
            report = await feeder.run_telegram_offer_queue_handoff_cycle()

        self.assertTrue(report.publication_gated)
        self.assertEqual(report.publication_handoffs, 0)
        self.assertEqual(report.edit_handoffs, 1)
        load_publication.assert_not_awaited()
        load_edits.assert_awaited_once()

    async def test_invalid_candidate_rolls_back_and_next_candidate_continues(self):
        session = FakeSession()
        candidates = [candidate("ofr_bad"), candidate("ofr_good")]
        enqueue = AsyncMock(
            side_effect=[
                TelegramOfferQueueError("unsafe"),
                SimpleNamespace(
                    queue_result=SimpleNamespace(created=True),
                    skipped_reason=None,
                ),
            ]
        )

        with patch.object(
            feeder,
            "enqueue_current_offer_delivery",
            new=enqueue,
        ):
            counts = await feeder._handoff_candidates(
                session,
                candidates,
                expected_channel_id=-100,
                offer_expiry_minutes=2,
            )

        self.assertEqual(counts, (1, 0, 0, 1))
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        self.assertEqual(session.savepoint_entries, 2)

    def test_runtime_owner_guard_rejects_legacy(self):
        runtime = SimpleNamespace(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            queue_worker_enabled=False,
        )
        with patch.object(
            feeder,
            "configured_telegram_delivery_runtime",
            return_value=runtime,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires_queue_owner"):
                feeder._assert_queue_runtime_owner()


if __name__ == "__main__":
    unittest.main()
