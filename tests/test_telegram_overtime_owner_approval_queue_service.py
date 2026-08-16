"""Foreign overtime owner-approval enqueue must not lazy-load relationships."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_overtime_owner_approval_queue_service import (
    _attach_offer_commodity,
    enqueue_overtime_owner_approval_delivery,
)
from models.offer_request import OfferRequestStatus


class OvertimeOwnerApprovalQueueServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_attach_offer_commodity_sets_async_loaded_row(self):
        commodity = SimpleNamespace(id=9, name="سکه")
        db = AsyncMock()
        db.get = AsyncMock(return_value=commodity)
        offer = SimpleNamespace(commodity_id=9, commodity=None)

        await _attach_offer_commodity(db, offer)

        db.get.assert_awaited_once()
        self.assertIs(offer.commodity, commodity)

    async def test_enqueue_attaches_commodity_before_channel_copy(self):
        offer = SimpleNamespace(
            commodity_id=9,
            commodity=None,
            overtime_minutes_snapshot=5,
            created_at=None,
            lot_sizes=None,
            is_wholesale=True,
        )
        ledger = SimpleNamespace(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            offer_owner_user_id=7,
            request_public_id="req_public_test_0001",
            requested_quantity=5,
        )
        owner = SimpleNamespace(id=7, telegram_id=9001, sync_version=1)
        db = AsyncMock()
        db.get = AsyncMock(side_effect=[owner, SimpleNamespace(id=9, name="سکه")])
        enqueue = AsyncMock(
            return_value=SimpleNamespace(job=SimpleNamespace(id=44), created=True)
        )
        with patch(
            "core.services.telegram_overtime_owner_approval_queue_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "overtime_owner_approval_delivery_deadline",
            return_value="deadline",
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "build_offer_channel_message",
            return_value="متن",
        ) as build_message, patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "enqueue_telegram_delivery_job",
            enqueue,
        ):
            outcome = await enqueue_overtime_owner_approval_delivery(
                db,
                current_server="foreign",
                ledger=ledger,
                offer=offer,
                normal_lifetime_minutes=25,
            )

        self.assertTrue(outcome.enqueued)
        self.assertEqual(outcome.job_id, 44)
        self.assertIsNotNone(offer.commodity)
        build_message.assert_called_once_with(offer)


if __name__ == "__main__":
    unittest.main()
