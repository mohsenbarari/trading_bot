import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_TEMPLATE_VERSION,
    build_telegram_admin_broadcast_payload,
    telegram_admin_broadcast_campaign_id,
    telegram_admin_broadcast_destination_key,
    telegram_admin_broadcast_source_natural_id,
    validate_admin_broadcast_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import User


NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FreshnessDB:
    def __init__(self, *, receipt, broadcast, user):
        self.receipt = receipt
        self.broadcast = broadcast
        self.user = user

    async def execute(self, _statement):
        return _ScalarResult(self.receipt)

    async def get(self, model, _object_id):
        if model is TelegramAdminBroadcast:
            return self.broadcast
        if model is User:
            return self.user
        return None


def _user(**overrides):
    values = {"id": 9, "telegram_id": 9001, "sync_version": 3}
    values.update(overrides)
    return SimpleNamespace(**values)


def _broadcast(**overrides):
    values = {
        "id": 51,
        "content": "پیام مدیریتی",
        "status": TelegramAdminBroadcastStatus.RUNNING,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _receipt(**overrides):
    values = {
        "id": 71,
        "broadcast_id": 51,
        "recipient_user_id": 9,
        "telegram_id_at_enqueue": 9001,
        "dedupe_key": "telegram-admin-broadcast:51:9",
        "status": TelegramAdminBroadcastReceiptStatus.PENDING,
        "telegram_message_id": None,
        "queue_job_id": 701,
        "queue_handed_off_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _job(*, receipt=None, broadcast=None, user=None, **overrides):
    receipt = receipt or _receipt()
    broadcast = broadcast or _broadcast()
    user = user or _user()
    payload = build_telegram_admin_broadcast_payload(broadcast, user)
    _, payload_hash = canonical_telegram_delivery_payload(payload)
    values = {
        "id": 701,
        "action_kind": TelegramDeliveryAction.ADMIN_BROADCAST,
        "feeder_kind": TelegramFeederKind.ADMIN_SYSTEM,
        "destination_class": TelegramDestinationClass.PRIVATE,
        "method": "sendMessage",
        "bot_identity": "primary",
        "template_version": ADMIN_BROADCAST_TEMPLATE_VERSION,
        "delivery_deadline_at": None,
        "freshness_deadline_at": None,
        "run_id": None,
        "source_natural_id": telegram_admin_broadcast_source_natural_id(
            receipt,
            broadcast,
        ),
        "source_version": user.sync_version,
        "destination_key": telegram_admin_broadcast_destination_key(
            receipt.recipient_user_id
        ),
        "campaign_id": telegram_admin_broadcast_campaign_id(broadcast.id),
        "payload": payload,
        "payload_hash": payload_hash,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramAdminBroadcastFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def _validate(self, *, receipt=None, broadcast=None, user=None, job=None):
        receipt = receipt or _receipt()
        broadcast = broadcast or _broadcast()
        user = user or _user()
        job = job or _job(receipt=receipt, broadcast=broadcast, user=user)
        with patch(
            "core.telegram_delivery_admin_broadcast_freshness.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ):
            return await validate_admin_broadcast_telegram_delivery_freshness(
                _FreshnessDB(receipt=receipt, broadcast=broadcast, user=user),
                job,
                NOW,
            )

    async def test_current_bound_receipt_is_sendable(self):
        decision = await self._validate()
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_current")

    async def test_content_or_recipient_version_change_reclassifies(self):
        receipt = _receipt()
        old_broadcast = _broadcast(content="متن قدیمی")
        job = _job(receipt=receipt, broadcast=old_broadcast)
        decision = await self._validate(
            receipt=receipt,
            broadcast=_broadcast(content="متن جدید"),
            job=job,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(decision.replacement_action, TelegramDeliveryAction.ADMIN_BROADCAST)

        user = _user(sync_version=4)
        decision = await self._validate(
            receipt=receipt,
            user=user,
            job=_job(receipt=receipt, user=_user(sync_version=3)),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_recipient_version_changed")

    async def test_terminal_and_unavailable_recipients_are_not_sent(self):
        sent = _receipt(
            status=TelegramAdminBroadcastReceiptStatus.SENT,
            telegram_message_id=444,
        )
        decision = await self._validate(receipt=sent, job=_job(receipt=sent))
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)

        user = _user(telegram_id=None)
        decision = await self._validate(
            user=user,
            job=_job(user=_user()),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_recipient_unlinked")

        relinked = _user(telegram_id=9002)
        decision = await self._validate(
            receipt=_receipt(telegram_id_at_enqueue=9001),
            user=relinked,
            job=_job(user=_user(telegram_id=9001)),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_recipient_relinked")

    async def test_binding_route_and_payload_tampering_quarantine(self):
        receipt = _receipt(queue_job_id=999)
        decision = await self._validate(
            receipt=receipt,
            job=_job(receipt=_receipt()),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_queue_owner_mismatch")

        job = _job()
        job.payload = {**job.payload, "text": "متن دستکاری‌شده"}
        decision = await self._validate(job=job)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(decision.reason, "admin_broadcast_freshness_payload_hash_mismatch")

    async def test_missing_receipt_waits_without_trusting_queue_payload(self):
        broadcast = _broadcast()
        user = _user()
        job = _job(broadcast=broadcast, user=user)
        with patch(
            "core.telegram_delivery_admin_broadcast_freshness.evaluate_bot_access",
            new=AsyncMock(),
        ):
            decision = await validate_admin_broadcast_telegram_delivery_freshness(
                _FreshnessDB(receipt=None, broadcast=broadcast, user=user),
                job,
                NOW,
            )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)


if __name__ == "__main__":
    unittest.main()
