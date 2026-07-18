import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
)
from core.telegram_delivery_new_user_membership_freshness import (
    NEW_USER_MEMBERSHIP_TEMPLATE_VERSION,
    build_new_user_membership_payload,
    telegram_new_user_membership_campaign_id,
    telegram_new_user_membership_destination_key,
    telegram_new_user_membership_source_natural_id,
    validate_new_user_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FreshnessDB:
    def __init__(self, *, outbox, user):
        self.outbox = outbox
        self.user = user

    async def execute(self, _statement):
        return _ScalarResult(self.outbox)

    async def get(self, model, _object_id):
        if model is User:
            return self.user
        return None


def _user(**overrides):
    values = {"id": 7, "telegram_id": 7007, "sync_version": 3}
    values.update(overrides)
    return SimpleNamespace(**values)


def _outbox(**overrides):
    values = {
        "id": 91,
        "dedupe_key": "telegram-notification:project_user_joined:9:7",
        "source_type": TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
        "source_id": "9",
        "recipient_user_id": 7,
        "telegram_id_at_enqueue": 7007,
        "text": "کاربر جدید به لیست همکاران اضافه شدند.",
        "parse_mode": None,
        "status": TelegramNotificationOutboxStatus.PENDING,
        "telegram_message_id": None,
        "extra_payload": {"exclude_customers": True},
        "queue_job_id": 811,
        "queue_handed_off_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _job(*, outbox=None, user=None, **overrides):
    outbox = outbox or _outbox()
    user = user or _user()
    payload = build_new_user_membership_payload(outbox, user)
    _, payload_hash = canonical_telegram_delivery_payload(payload)
    values = {
        "id": 811,
        "action_kind": TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
        "feeder_kind": TelegramFeederKind.ADMIN_SYSTEM,
        "destination_class": TelegramDestinationClass.PRIVATE,
        "method": "sendMessage",
        "bot_identity": "primary",
        "template_version": NEW_USER_MEMBERSHIP_TEMPLATE_VERSION,
        "delivery_deadline_at": None,
        "freshness_deadline_at": None,
        "run_id": None,
        "source_natural_id": telegram_new_user_membership_source_natural_id(
            outbox
        ),
        "source_version": user.sync_version,
        "destination_key": telegram_new_user_membership_destination_key(
            outbox.recipient_user_id
        ),
        "campaign_id": telegram_new_user_membership_campaign_id(
            int(outbox.source_id)
        ),
        "payload": payload,
        "payload_hash": payload_hash,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramNewUserMembershipFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def _validate(self, *, outbox=None, user=None, job=None, customer=None):
        outbox = outbox or _outbox()
        user = user or _user()
        job = job or _job(outbox=outbox, user=user)
        with patch(
            "core.telegram_delivery_new_user_membership_freshness.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.telegram_delivery_new_user_membership_freshness."
            "get_active_customer_relation_for_user",
            new=AsyncMock(return_value=customer),
        ):
            return await validate_new_user_membership_telegram_delivery_freshness(
                _FreshnessDB(outbox=outbox, user=user),
                job,
                NOW,
            )

    async def test_current_bound_outbox_is_sendable(self):
        decision = await self._validate()
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(decision.reason, "new_user_membership_freshness_current")

    async def test_content_or_recipient_version_change_reclassifies(self):
        old = _outbox(text="متن قدیمی")
        job = _job(outbox=old)
        decision = await self._validate(
            outbox=_outbox(text="متن جدید"),
            job=job,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            decision.replacement_action,
            TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
        )

        decision = await self._validate(
            user=_user(sync_version=4),
            job=_job(user=_user(sync_version=3)),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_recipient_version_changed",
        )

    async def test_terminal_unlinked_and_customer_recipient_are_not_sent(self):
        sent = _outbox(
            status=TelegramNotificationOutboxStatus.SENT,
            telegram_message_id=444,
        )
        decision = await self._validate(outbox=sent, job=_job(outbox=sent))
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)

        decision = await self._validate(
            user=_user(telegram_id=None),
            job=_job(user=_user()),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_recipient_unlinked",
        )

        decision = await self._validate(customer=object())
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_customer_excluded",
        )

    async def test_binding_route_payload_and_source_tampering_quarantine(self):
        decision = await self._validate(
            outbox=_outbox(queue_job_id=999),
            job=_job(),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_queue_owner_mismatch",
        )

        job = _job()
        job.payload = {**job.payload, "text": "متن دستکاری‌شده"}
        decision = await self._validate(job=job)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_payload_hash_mismatch",
        )

        invalid = _outbox(source_type="unknown")
        decision = await self._validate(outbox=invalid, job=_job())
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "new_user_membership_freshness_source_contract_invalid",
        )

    async def test_missing_outbox_waits_without_trusting_queue_payload(self):
        job = _job()
        decision = await validate_new_user_membership_telegram_delivery_freshness(
            _FreshnessDB(outbox=None, user=_user()),
            job,
            NOW,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)


if __name__ == "__main__":
    unittest.main()
