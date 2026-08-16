"""Cross-surface freshness for Telegram-home overtime owner approvals."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.offer_request_identity import generate_offer_request_public_id
from core.telegram_delivery_overtime_owner_approval_contract import (
    OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
    build_overtime_owner_approval_payload,
)
from core.telegram_delivery_overtime_owner_approval_freshness import (
    validate_overtime_owner_approval_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from models.offer import Offer, OfferStatus
from models.offer_request import (
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.user import User


NOW = datetime(2026, 8, 16, 12, 30, tzinfo=timezone.utc)


def _job(*, request_public_id: str):
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
        delivery_deadline_at=NOW + timedelta(minutes=4),
        source_natural_id=f"overtime-owner-approval:{request_public_id}",
        destination_key="private:user:1",
        payload=build_overtime_owner_approval_payload(
            chat_id=9001,
            request_public_id=request_public_id,
            offer_text="🔴 فروش",
        ),
    )


def _ledger(*, request_public_id: str):
    return SimpleNamespace(
        request_public_id=request_public_id,
        workflow_kind=OfferRequestWorkflow.OVERTIME,
        result_status=OfferRequestStatus.OVERTIME_DELIVERING,
        request_source_surface=OfferRequestSourceSurface.WEBAPP,
        request_home_server="foreign",
        offer_owner_user_id=1,
        local_offer_id=7,
    )


def _offer(*, home_server: str = "foreign"):
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        home_server=home_server,
        created_at=NOW - timedelta(minutes=2, seconds=30),
        overtime_minutes_snapshot=5,
    )


class OvertimeOwnerApprovalFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def _validate(self, *, offer_home: str):
        request_public_id = generate_offer_request_public_id()
        ledger = _ledger(request_public_id=request_public_id)
        owner = SimpleNamespace(id=1, telegram_id=9001)
        offer = _offer(home_server=offer_home)
        db = AsyncMock()

        async def get(model, ident):
            if model is User and ident == 1:
                return owner
            if model is Offer and ident == 7:
                return offer
            return None

        db.get.side_effect = get
        with patch(
            "core.telegram_delivery_overtime_owner_approval_freshness."
            "load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch(
            "core.telegram_delivery_overtime_owner_approval_freshness."
            "evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ), patch(
            "core.telegram_delivery_overtime_owner_approval_freshness."
            "get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ):
            return await validate_overtime_owner_approval_delivery_freshness(
                db,
                _job(request_public_id=request_public_id),
                NOW,
            )

    async def test_webapp_request_for_telegram_home_offer_is_sendable(self):
        decision = await self._validate(offer_home="foreign")

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_telegram_job_for_webapp_home_offer_is_quarantined(self):
        decision = await self._validate(offer_home="iran")

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "overtime_owner_approval_freshness_surface_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
