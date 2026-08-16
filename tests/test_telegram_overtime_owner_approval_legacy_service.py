import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from core.services.bot_access_policy import BotAccessDecision
from core.services import telegram_overtime_owner_approval_legacy_service as service
from models.offer_request import OfferRequestStatus


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
REQUEST_ID = "req_f6OVHJ5aWsFKkoaNdAAzIZNb"


class FakeDB:
    def __init__(self, owner=None):
        self.owner = owner

    async def get(self, _model, _record_id):
        return self.owner


class LegacyOvertimeOwnerApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_persists_prompt_and_inline_decisions_in_active_legacy_outbox(self):
        owner = SimpleNamespace(id=7, telegram_id=7007)
        ledger = SimpleNamespace(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            offer_owner_user_id=7,
            request_public_id=REQUEST_ID,
            offer_public_id="ofr_test",
            requested_quantity=2,
        )
        offer = SimpleNamespace(is_wholesale=True)
        outbox = SimpleNamespace(id=81)
        enqueue = AsyncMock(return_value=SimpleNamespace(outbox=outbox, created=True))

        with patch.object(
            service,
            "evaluate_bot_access",
            new=AsyncMock(return_value=BotAccessDecision(True)),
        ), patch.object(
            service,
            "build_offer_channel_message",
            return_value="لفظ نمونه",
        ), patch.object(
            service,
            "overtime_owner_approval_delivery_deadline",
            return_value=NOW + timedelta(minutes=2),
        ), patch.object(
            service,
            "enqueue_telegram_notification_once",
            new=enqueue,
        ):
            result = await service.enqueue_legacy_overtime_owner_approval_delivery(
                FakeDB(owner),
                ledger=ledger,
                offer=offer,
                normal_lifetime_minutes=3,
            )

        self.assertTrue(result.enqueued)
        self.assertEqual(result.outbox_id, 81)
        kwargs = enqueue.await_args.kwargs
        self.assertEqual(kwargs["source_type"], service.LEGACY_OVERTIME_OWNER_APPROVAL_SOURCE)
        self.assertEqual(kwargs["source_id"], REQUEST_ID)
        buttons = kwargs["extra_payload"]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(
            {button["callback_data"] for button in buttons},
            {f"ota:{REQUEST_ID}:approve", f"ota:{REQUEST_ID}:reject"},
        )

    async def test_preflight_keeps_clock_stopped_and_returns_exact_markup(self):
        ledger = SimpleNamespace(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            request_home_server="foreign",
            offer_owner_user_id=7,
        )
        markup = service.build_overtime_owner_approval_reply_markup(
            request_public_id=REQUEST_ID
        )
        outbox = SimpleNamespace(
            source_type=service.LEGACY_OVERTIME_OWNER_APPROVAL_SOURCE,
            source_id=REQUEST_ID,
            recipient_user_id=7,
            extra_payload={
                "request_public_id": REQUEST_ID,
                "reply_markup": markup,
                "delivery_deadline_at": (NOW + timedelta(minutes=1)).isoformat(),
            },
        )
        with patch(
            "core.services.offer_overtime_request_service."
            "load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ):
            result = await service.prepare_legacy_overtime_owner_approval_dispatch(
                FakeDB(),
                outbox,
                now=NOW,
            )

        self.assertTrue(result.dispatchable)
        self.assertEqual(result.reply_markup, markup)
        self.assertFalse(hasattr(ledger, "presented_at"))

    async def test_sent_message_id_is_the_only_event_that_marks_presented(self):
        ledger = SimpleNamespace(result_status=OfferRequestStatus.OVERTIME_DELIVERING)
        outbox = SimpleNamespace(source_id=REQUEST_ID)
        mark = AsyncMock()
        with patch(
            "core.services.offer_overtime_request_service."
            "load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch(
            "core.services.offer_overtime_request_service.mark_presented",
            new=mark,
        ):
            await service.apply_legacy_overtime_owner_approval_sent(
                FakeDB(),
                outbox,
                telegram_message_id=901,
                now=NOW,
            )

        mark.assert_awaited_once_with(
            ANY,
            ledger,
            presented_at=NOW,
            telegram_message_id=901,
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()
