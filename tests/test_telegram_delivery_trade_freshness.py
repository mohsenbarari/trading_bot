import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_delivery_trade_freshness as freshness
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.services.trade_delivery_receipt_service import (
    trade_completed_receipt_dedupe_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_queue_receipt_worker_id,
)
from models.trade import TradeStatus
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceiptStatus,
)


COMMITTED_AT = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
NOW = COMMITTED_AT + timedelta(seconds=4)
TRADE_NUMBER = 10025
RECIPIENT_USER_ID = 20
TELEGRAM_ID = 900020


def make_receipt(**overrides):
    trade_number = overrides.get("trade_number", TRADE_NUMBER)
    recipient_user_id = overrides.get("recipient_user_id", RECIPIENT_USER_ID)
    recipient_role = overrides.get("recipient_role", "offer_owner")
    data = {
        "id": 71,
        "event_type": "trade_completed",
        "dedupe_key": trade_completed_receipt_dedupe_key(
            channel=TradeDeliveryChannel.TELEGRAM,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
        ),
        "trade_id": 501,
        "trade_number": trade_number,
        "offer_id": 77,
        "recipient_user_id": recipient_user_id,
        "recipient_role": recipient_role,
        "channel": TradeDeliveryChannel.TELEGRAM,
        "destination_server": "foreign",
        "status": TradeDeliveryReceiptStatus.PENDING,
        "worker_id": trade_result_queue_receipt_worker_id(801),
        "telegram_message_id": None,
        "next_retry_at": None,
        "event_created_at": COMMITTED_AT,
        "audit_payload": {
            "message": "🟢 خرید\nشماره معامله: 10025",
            "telegram_id_at_audience_build": TELEGRAM_ID,
            "extra_payload": {
                "trade_number": trade_number,
                "recipient_user_id": recipient_user_id,
                "recipient_role": recipient_role,
                "side": "offer_owner",
            },
        },
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_trade(**overrides):
    data = {
        "id": 501,
        "trade_number": TRADE_NUMBER,
        "offer_id": 77,
        "status": TradeStatus.COMPLETED,
        "created_at": COMMITTED_AT,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_user(**overrides):
    data = {
        "id": RECIPIENT_USER_ID,
        "telegram_id": TELEGRAM_ID,
        "sync_version": 8,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_job(*, receipt=None, user=None, **overrides):
    receipt = receipt or make_receipt()
    user = user or make_user()
    payload = freshness.build_trade_result_delivery_payload(receipt, user)
    _, payload_hash = canonical_telegram_delivery_payload(payload)
    data = {
        "id": 801,
        "action_kind": TelegramDeliveryAction.TRADE_RESULT,
        "feeder_kind": TelegramFeederKind.TRADE,
        "destination_class": TelegramDestinationClass.PRIVATE,
        "destination_key": freshness.telegram_private_user_destination_key(
            receipt.recipient_user_id
        ),
        "method": "sendMessage",
        "bot_identity": "primary",
        "template_version": freshness.TRADE_RESULT_TEMPLATE_VERSION,
        "source_natural_id": freshness.trade_result_source_natural_id(receipt),
        "source_version": user.sync_version,
        "delivery_deadline_at": freshness.trade_result_delivery_deadline(
            receipt.event_created_at
        ),
        "freshness_deadline_at": None,
        "payload": payload,
        "payload_hash": payload_hash,
    }
    data.update(overrides)
    if "payload" in overrides and "payload_hash" not in overrides:
        try:
            _, data["payload_hash"] = canonical_telegram_delivery_payload(
                data["payload"]
            )
        except Exception:
            data["payload_hash"] = "invalid"
    return SimpleNamespace(**data)


class TelegramDeliveryTradeFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def decide(
        self,
        job,
        *,
        receipt=None,
        trade=None,
        user=None,
        access_allowed=True,
        now=NOW,
    ):
        with patch.object(
            freshness,
            "_load_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            freshness,
            "_load_trade",
            new=AsyncMock(return_value=trade),
        ), patch.object(
            freshness,
            "_load_user",
            new=AsyncMock(return_value=user),
        ), patch.object(
            freshness,
            "evaluate_bot_access",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    allowed=access_allowed,
                    reason=None if access_allowed else "blocked",
                )
            ),
        ):
            return await freshness.validate_trade_result_telegram_delivery_freshness(
                object(),
                job,
                now,
            )

    def test_public_builders_are_strict_and_keep_private_destination_stable(self):
        self.assertEqual(
            freshness.telegram_private_user_destination_key(20),
            "private:user:20",
        )
        self.assertEqual(
            freshness.trade_result_delivery_deadline(COMMITTED_AT),
            COMMITTED_AT + timedelta(seconds=5),
        )
        self.assertEqual(freshness.trade_result_delivery_source_version(make_user()), 8)
        self.assertIn(
            ":snapshot-v1:",
            freshness.trade_result_source_natural_id(make_receipt()),
        )
        for invalid in (None, True, 0, -1, "20"):
            with self.subTest(destination=invalid), self.assertRaises(ValueError):
                freshness.telegram_private_user_destination_key(invalid)
        for invalid in (None, True, 0, "8"):
            with self.subTest(version=invalid), self.assertRaises(ValueError):
                freshness.trade_result_delivery_source_version(
                    make_user(sync_version=invalid)
                )

    async def test_pending_and_retry_pending_current_receipts_are_sendable(self):
        for status in (
            TradeDeliveryReceiptStatus.PENDING,
            TradeDeliveryReceiptStatus.RETRY_PENDING,
        ):
            with self.subTest(status=status):
                receipt = make_receipt(
                    status=status,
                    next_retry_at=(
                        COMMITTED_AT
                        if status == TradeDeliveryReceiptStatus.RETRY_PENDING
                        else None
                    ),
                )
                user = make_user()
                decision = await self.decide(
                    make_job(receipt=receipt, user=user),
                    receipt=receipt,
                    trade=make_trade(),
                    user=user,
                )
                self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
                self.assertEqual(decision.reason, "trade_freshness_current")

    async def test_active_receipt_must_be_owned_by_the_exact_queue_job(self):
        receipt = make_receipt(worker_id=None)
        decision = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
        )
        wrong_job = make_receipt(
            worker_id=trade_result_queue_receipt_worker_id(802)
        )
        wrong = await self.decide(
            make_job(receipt=wrong_job),
            receipt=wrong_job,
        )

        for item in (decision, wrong):
            self.assertEqual(item.outcome, TelegramFreshnessOutcome.QUARANTINED)
            self.assertEqual(
                item.reason,
                "trade_freshness_receipt_queue_owner_mismatch",
            )

    async def test_retry_pending_receipt_honors_existing_retry_after_window(self):
        receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            next_retry_at=NOW + timedelta(seconds=20),
        )
        decision = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
        )
        missing_retry_at = make_receipt(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            next_retry_at=None,
        )
        invalid = await self.decide(
            make_job(receipt=missing_retry_at),
            receipt=missing_retry_at,
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)
        self.assertEqual(decision.reason, "trade_freshness_receipt_retry_not_due")
        self.assertEqual(invalid.outcome, TelegramFreshnessOutcome.QUARANTINED)

    async def test_five_seconds_escalates_priority_but_never_expires_result(self):
        receipt = make_receipt()
        user = make_user()
        decision = await self.decide(
            make_job(receipt=receipt, user=user),
            receipt=receipt,
            trade=make_trade(),
            user=user,
            now=COMMITTED_AT + timedelta(hours=2),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_existing_opposite_server_outage_policy_is_preserved(self):
        receipt = make_receipt(
            audit_payload={
                **make_receipt().audit_payload,
                "offer_home_server": "iran",
            }
        )
        decision = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
            now=COMMITTED_AT + timedelta(seconds=121),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            decision.reason,
            "trade_freshness_expired_after_outage",
        )

    async def test_deadline_must_be_exact_and_freshness_ttl_is_forbidden(self):
        receipt = make_receipt()
        user = make_user()
        for job, reason in (
            (
                make_job(
                    receipt=receipt,
                    user=user,
                    delivery_deadline_at=None,
                ),
                "trade_freshness_delivery_deadline_missing",
            ),
            (
                make_job(
                    receipt=receipt,
                    user=user,
                    delivery_deadline_at=COMMITTED_AT + timedelta(seconds=6),
                ),
                "trade_freshness_delivery_deadline_mismatch",
            ),
            (
                make_job(
                    receipt=receipt,
                    user=user,
                    freshness_deadline_at=COMMITTED_AT + timedelta(minutes=1),
                ),
                "trade_freshness_deadline_forbidden",
            ),
        ):
            with self.subTest(reason=reason):
                decision = await self.decide(
                    job,
                    receipt=receipt,
                    trade=make_trade(),
                    user=user,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )
                self.assertEqual(decision.reason, reason)

    async def test_sent_receipt_is_noop_only_with_message_evidence(self):
        sent = make_receipt(
            status=TradeDeliveryReceiptStatus.SENT,
            telegram_message_id=991,
        )
        decision = await self.decide(make_job(receipt=sent), receipt=sent)
        missing_evidence = make_receipt(status=TradeDeliveryReceiptStatus.SENT)
        invalid = await self.decide(
            make_job(receipt=missing_evidence),
            receipt=missing_evidence,
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)
        self.assertEqual(invalid.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(invalid.reason, "trade_freshness_sent_evidence_missing")

    async def test_unsent_receipt_cannot_carry_message_evidence(self):
        receipt = make_receipt(telegram_message_id=991)
        decision = await self.decide(make_job(receipt=receipt), receipt=receipt)

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "trade_freshness_unsent_message_evidence_present",
        )

    async def test_terminal_receipts_supersede_and_processing_waits_for_legacy_owner(self):
        for status in (
            TradeDeliveryReceiptStatus.SKIPPED,
            TradeDeliveryReceiptStatus.NOT_REQUIRED,
            TradeDeliveryReceiptStatus.PERMANENT_FAILED,
        ):
            with self.subTest(status=status):
                receipt = make_receipt(status=status)
                decision = await self.decide(make_job(receipt=receipt), receipt=receipt)
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.SUPERSEDED,
                )
        processing = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING)
        decision = await self.decide(
            make_job(receipt=processing),
            receipt=processing,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)
        self.assertEqual(
            decision.reason,
            "trade_freshness_legacy_receipt_processing",
        )

    async def test_missing_synced_dependencies_wait_without_dispatch(self):
        job = make_job()
        missing_receipt = await self.decide(job, receipt=None)
        receipt = make_receipt()
        missing_trade = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
            trade=None,
        )

        self.assertEqual(
            missing_receipt.outcome,
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
        )
        self.assertEqual(
            missing_trade.outcome,
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
        )

    async def test_noncompleted_trade_supersedes_but_identity_tamper_quarantines(self):
        receipt = make_receipt()
        user = make_user()
        noncompleted = await self.decide(
            make_job(receipt=receipt, user=user),
            receipt=receipt,
            trade=make_trade(status=TradeStatus.CANCELLED),
            user=user,
        )
        wrong_time = await self.decide(
            make_job(receipt=receipt, user=user),
            receipt=receipt,
            trade=make_trade(created_at=COMMITTED_AT + timedelta(seconds=1)),
            user=user,
        )
        wrong_id = await self.decide(
            make_job(receipt=receipt, user=user),
            receipt=receipt,
            trade=make_trade(id=502),
            user=user,
        )

        self.assertEqual(noncompleted.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(wrong_time.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(wrong_id.outcome, TelegramFreshnessOutcome.QUARANTINED)

    async def test_recipient_current_state_is_revalidated(self):
        receipt = make_receipt()
        current_job = make_job(receipt=receipt)
        missing = await self.decide(
            current_job,
            receipt=receipt,
            trade=make_trade(),
            user=None,
        )
        unlinked_user = make_user(telegram_id=None)
        unlinked = await self.decide(
            make_job(receipt=receipt, user=make_user()),
            receipt=receipt,
            trade=make_trade(),
            user=unlinked_user,
        )
        denied = await self.decide(
            current_job,
            receipt=receipt,
            trade=make_trade(),
            user=make_user(),
            access_allowed=False,
        )

        for decision in (missing, unlinked, denied):
            self.assertEqual(
                decision.outcome,
                TelegramFreshnessOutcome.SUPERSEDED,
            )

    async def test_trade_result_never_follows_recipient_relink(self):
        receipt = make_receipt()
        original_user = make_user()
        relinked_user = make_user(telegram_id=TELEGRAM_ID + 1)
        decision = await self.decide(
            make_job(receipt=receipt, user=original_user),
            receipt=receipt,
            trade=make_trade(),
            user=relinked_user,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(decision.reason, "trade_freshness_recipient_relinked")

    async def test_recipient_version_drift_reconciles_each_recipient_independently(self):
        receipt = make_receipt()
        current = make_user(sync_version=9)
        stale = await self.decide(
            make_job(receipt=receipt, user=make_user(sync_version=8)),
            receipt=receipt,
            trade=make_trade(),
            user=current,
        )
        ahead = await self.decide(
            make_job(receipt=receipt, user=make_user(sync_version=10)),
            receipt=receipt,
            trade=make_trade(),
            user=current,
        )

        self.assertEqual(stale.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            stale.replacement_action,
            TelegramDeliveryAction.TRADE_RESULT,
        )
        self.assertEqual(ahead.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)

    async def test_route_tamper_is_quarantined(self):
        receipt = make_receipt()
        user = make_user()
        jobs = (
            make_job(receipt=receipt, user=user, action_kind=TelegramDeliveryAction.TRADE_RESPONSE),
            make_job(receipt=receipt, user=user, feeder_kind=TelegramFeederKind.DIRECT),
            make_job(receipt=receipt, user=user, destination_class=TelegramDestinationClass.CHANNEL),
            make_job(receipt=receipt, user=user, method="editMessageText"),
            make_job(receipt=receipt, user=user, bot_identity="channel_editor"),
            make_job(receipt=receipt, user=user, template_version="future"),
        )
        for job in jobs:
            with self.subTest(job=job):
                decision = await self.decide(job, receipt=receipt)
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )

    async def test_receipt_identity_and_snapshot_tamper_are_quarantined(self):
        valid_user = make_user()
        variants = (
            make_receipt(event_type="trade_cancelled"),
            make_receipt(channel=TradeDeliveryChannel.WEBAPP),
            make_receipt(destination_server="iran"),
            make_receipt(dedupe_key="wrong"),
            make_receipt(audit_payload=None),
            make_receipt(
                audit_payload={
                    "message": "message",
                    "extra_payload": {
                        "trade_number": TRADE_NUMBER + 1,
                        "recipient_user_id": RECIPIENT_USER_ID,
                        "recipient_role": "offer_owner",
                    },
                }
            ),
        )
        for receipt in variants:
            with self.subTest(receipt=receipt):
                try:
                    job = make_job(receipt=receipt, user=valid_user)
                except ValueError:
                    job = make_job(receipt=make_receipt(), user=valid_user)
                    job.source_natural_id = getattr(receipt, "dedupe_key", job.source_natural_id)
                decision = await self.decide(job, receipt=receipt)
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )

    async def test_receipt_message_change_reclassifies_to_a_new_source_snapshot(self):
        original = make_receipt()
        job = make_job(receipt=original)
        changed = make_receipt(
            audit_payload={
                **original.audit_payload,
                "message": "متن اصلاح‌شده نتیجه معامله",
            }
        )

        decision = await self.decide(job, receipt=changed)

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            decision.replacement_action,
            TelegramDeliveryAction.TRADE_RESULT,
        )
        self.assertNotEqual(
            job.source_natural_id,
            freshness.trade_result_source_natural_id(changed),
        )

    async def test_source_snapshot_fingerprint_cannot_be_forged(self):
        receipt = make_receipt()
        job = make_job(receipt=receipt)
        job.source_natural_id = (
            f"{receipt.dedupe_key}:snapshot-v1:{'0' * 24}"
        )

        decision = await self.decide(job, receipt=receipt)

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            decision.reason,
            "trade_freshness_source_snapshot_fingerprint_invalid",
        )

    async def test_payload_destination_and_hash_must_match_current_recipient(self):
        receipt = make_receipt()
        user = make_user()
        jobs = (
            make_job(
                receipt=receipt,
                user=user,
                destination_key="private:user:21",
            ),
            make_job(
                receipt=receipt,
                user=user,
                payload={
                    "chat_id": TELEGRAM_ID + 1,
                    "text": receipt.audit_payload["message"],
                    "parse_mode": "HTML",
                },
            ),
            make_job(receipt=receipt, user=user, payload_hash="0" * 64),
        )
        for job in jobs:
            with self.subTest(job=job):
                decision = await self.decide(
                    job,
                    receipt=receipt,
                    trade=make_trade(),
                    user=user,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )


class TradeResultTelegramDeliveryFreshnessValidatorTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_callable_delegates_to_authoritative_validator(self):
        validator = freshness.TradeResultTelegramDeliveryFreshnessValidator()
        job = make_job()
        expected = SimpleNamespace(outcome=TelegramFreshnessOutcome.SEND)
        with patch.object(
            freshness,
            "validate_trade_result_telegram_delivery_freshness",
            new=AsyncMock(return_value=expected),
        ) as validate:
            result = await validator(object(), job, NOW)

        self.assertIs(result, expected)
        validate.assert_awaited_once_with(unittest.mock.ANY, job, NOW)
