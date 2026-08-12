"""Stage 8: lifecycle starts the 30s clock only after message-id persistence."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_overtime_owner_approval_queue_feedback import (
    TelegramOvertimeOwnerApprovalQueueLifecycleFeedback,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
    build_overtime_owner_approval_payload,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.offer_request_identity import generate_offer_request_public_id
from models.offer_request import OfferRequestStatus


NOW = datetime(2026, 8, 5, 12, 30, tzinfo=timezone.utc)


def _job(*, request_public_id: str, message_id: int | None = 77):
    payload = build_overtime_owner_approval_payload(
        chat_id=9001,
        request_public_id=request_public_id,
        offer_text="🔴 فروش",
    )
    return SimpleNamespace(
        action_kind=TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL,
        feeder_kind=TelegramFeederKind.DIRECT,
        destination_class=TelegramDestinationClass.PRIVATE,
        method="sendMessage",
        bot_identity="primary",
        template_version=OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
        eligible_at=None,
        freshness_deadline_at=None,
        campaign_id=None,
        run_id=None,
        delivery_deadline_at=NOW,
        source_natural_id=f"overtime-owner-approval:{request_public_id}",
        destination_key="private:user:1",
        payload=payload,
        payload_hash="hash",
        telegram_message_id=message_id,
    )


class OvertimeOwnerApprovalQueueFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_sent_marks_presented_with_message_id(self):
        public_id = generate_offer_request_public_id()
        ledger = SimpleNamespace(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            telegram_message_id=None,
            presented_at=None,
            decision_deadline_at=None,
        )
        mark = AsyncMock(return_value=ledger)
        feedback = TelegramOvertimeOwnerApprovalQueueLifecycleFeedback()
        with patch(
            "core.services.telegram_overtime_owner_approval_queue_feedback."
            "validate_overtime_owner_approval_job_contract",
            return_value=None,
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_feedback."
            "load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_feedback."
            "mark_presented",
            mark,
        ):
            await feedback.apply_delivery_result(
                AsyncMock(),
                _job(request_public_id=public_id),
                TelegramDeliveryDecision(TelegramDeliveryOutcome.SENT),
                NOW,
            )
        mark.assert_awaited_once()
        self.assertEqual(mark.await_args.kwargs["telegram_message_id"], 77)
        self.assertEqual(mark.await_args.kwargs["presented_at"], NOW)
