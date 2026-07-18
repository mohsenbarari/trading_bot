import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_delivery_market_freshness as freshness
from core.services.market_transition_service import (
    MARKET_CLOSED_CHANNEL_NOTICE,
    MARKET_NOTICE_STATUS_FAILED,
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_STATUS_SENT,
    MARKET_NOTICE_STATUS_SKIPPED,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    MARKET_NOTICE_TRANSITION_CLOSED,
    MARKET_NOTICE_TRANSITION_OPENED,
    MARKET_OPENED_CHANNEL_NOTICE,
    market_channel_notice_dedupe_key,
    market_channel_notice_freshness_deadline,
)
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
TRANSITION_AT = NOW - timedelta(seconds=30)
CHANNEL_ID = -100123456


def make_receipt(**overrides):
    transition = overrides.get("transition", MARKET_NOTICE_TRANSITION_OPENED)
    transition_at = overrides.get("transition_at", TRANSITION_AT)
    text = overrides.get(
        "notice_text",
        (
            MARKET_OPENED_CHANNEL_NOTICE
            if transition == MARKET_NOTICE_TRANSITION_OPENED
            else MARKET_CLOSED_CHANNEL_NOTICE
        ),
    )
    data = {
        "id": 1,
        "dedupe_key": market_channel_notice_dedupe_key(
            transition=transition,
            transition_at=transition_at,
            notice_text=text,
        ),
        "transition": transition,
        "transition_at": transition_at,
        "notice_text": text,
        "channel_id": str(CHANNEL_ID),
        "status": MARKET_NOTICE_STATUS_PENDING,
        "telegram_message_id": None,
        "queue_job_id": 1,
        "queue_handed_off_at": NOW,
        "queue_reconciliation_required_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_state(**overrides):
    data = {
        "id": 1,
        "is_open": True,
        "last_transition_at": TRANSITION_AT,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_job(
    action=TelegramDeliveryAction.MARKET_TRANSITION,
    *,
    receipt=None,
    **overrides,
):
    receipt = receipt or make_receipt()
    payload = {
        "chat_id": CHANNEL_ID,
        "text": receipt.notice_text,
    }
    _, payload_hash = canonical_telegram_delivery_payload(payload)
    data = {
        "id": 1,
        "action_kind": action,
        "feeder_kind": TelegramFeederKind.MARKET_STATUS,
        "destination_class": TelegramDestinationClass.CHANNEL,
        "destination_key": f"channel:{CHANNEL_ID}",
        "method": "sendMessage",
        "bot_identity": "primary",
        "template_version": freshness.MARKET_NOTICE_TEMPLATE_VERSION,
        "campaign_id": freshness.MARKET_NOTICE_CAMPAIGN_ID,
        "delivery_deadline_at": None,
        "run_id": None,
        "source_natural_id": receipt.dedupe_key,
        "source_version": 1,
        "freshness_deadline_at": market_channel_notice_freshness_deadline(
            receipt.transition_at
        ),
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


class TelegramDeliveryMarketFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def decide(self, job, *, receipt=None, state=None, now=NOW):
        with patch.object(
            freshness,
            "_load_notice_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            freshness,
            "_load_market_runtime_state",
            new=AsyncMock(return_value=state),
        ):
            return await freshness.validate_market_telegram_delivery_freshness(
                object(),
                job,
                now,
                expected_channel_id=CHANNEL_ID,
            )

    def test_validator_requires_explicit_nonzero_integer_channel(self):
        for value in (None, True, 0, str(CHANNEL_ID)):
            with self.subTest(value=value), self.assertRaises(
                freshness.TelegramMarketFreshnessConfigurationError
            ):
                freshness.MarketTelegramDeliveryFreshnessValidator(value)

    async def test_current_pending_transition_is_sendable(self):
        receipt = make_receipt()
        decision = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
            state=make_state(),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(decision.reason, "market_freshness_current")

    async def test_failed_and_pending_receipts_reclassify_to_current_action(self):
        failed = make_receipt(status=MARKET_NOTICE_STATUS_FAILED)
        failed_decision = await self.decide(
            make_job(receipt=failed),
            receipt=failed,
            state=make_state(),
        )
        pending = make_receipt()
        pending_decision = await self.decide(
            make_job(
                TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
                receipt=pending,
            ),
            receipt=pending,
            state=make_state(),
        )

        self.assertEqual(failed_decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            failed_decision.replacement_action,
            TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
        )
        self.assertEqual(pending_decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            pending_decision.replacement_action,
            TelegramDeliveryAction.MARKET_TRANSITION,
        )

    async def test_failed_correction_is_sendable(self):
        receipt = make_receipt(status=MARKET_NOTICE_STATUS_FAILED)
        decision = await self.decide(
            make_job(
                TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
                receipt=receipt,
            ),
            receipt=receipt,
            state=make_state(),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_sent_receipt_is_noop_only_with_delivery_evidence(self):
        sent = make_receipt(
            status=MARKET_NOTICE_STATUS_SENT,
            telegram_message_id=771,
        )
        decision = await self.decide(make_job(receipt=sent), receipt=sent)
        invalid = make_receipt(status=MARKET_NOTICE_STATUS_SENT)
        invalid_decision = await self.decide(
            make_job(receipt=invalid),
            receipt=invalid,
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)
        self.assertEqual(
            invalid_decision.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_suppressed_or_skipped_receipt_is_superseded(self):
        for status in (
            MARKET_NOTICE_STATUS_SKIPPED,
            MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
        ):
            with self.subTest(status=status):
                receipt = make_receipt(status=status)
                decision = await self.decide(
                    make_job(receipt=receipt),
                    receipt=receipt,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.SUPERSEDED,
                )
        skipped_without_channel = make_receipt(
            status=MARKET_NOTICE_STATUS_SKIPPED,
            channel_id=None,
        )
        decision = await self.decide(
            make_job(receipt=skipped_without_channel),
            receipt=skipped_without_channel,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)

    async def test_newer_runtime_transition_supersedes_old_notice(self):
        receipt = make_receipt()
        decision = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
            state=make_state(
                is_open=False,
                last_transition_at=TRANSITION_AT + timedelta(seconds=20),
            ),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            decision.reason,
            "market_freshness_transition_no_longer_current",
        )

    async def test_effective_deadline_is_the_stricter_stored_or_current_policy(self):
        receipt = make_receipt()
        stale = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
            state=make_state(),
            now=TRANSITION_AT + timedelta(seconds=121),
        )
        later_job = make_job(
            receipt=receipt,
            freshness_deadline_at=TRANSITION_AT + timedelta(minutes=10),
        )
        later_deadline_now = await self.decide(
            later_job,
            receipt=receipt,
            state=make_state(),
        )
        later_deadline_stale = await self.decide(
            later_job,
            receipt=receipt,
            state=make_state(),
            now=TRANSITION_AT + timedelta(seconds=121),
        )
        earlier_job = make_job(
            receipt=receipt,
            freshness_deadline_at=TRANSITION_AT + timedelta(seconds=60),
        )
        earlier_deadline_now = await self.decide(
            earlier_job,
            receipt=receipt,
            state=make_state(),
        )
        earlier_deadline_stale = await self.decide(
            earlier_job,
            receipt=receipt,
            state=make_state(),
            now=TRANSITION_AT + timedelta(seconds=61),
        )
        missing_deadline = await self.decide(
            make_job(receipt=receipt, freshness_deadline_at=None),
            receipt=receipt,
        )

        self.assertEqual(stale.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(stale.reason, "market_freshness_deadline_passed")
        self.assertEqual(
            later_deadline_now.outcome,
            TelegramFreshnessOutcome.SEND,
        )
        self.assertEqual(
            later_deadline_stale.outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )
        self.assertEqual(earlier_deadline_now.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(
            earlier_deadline_stale.outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )
        self.assertEqual(
            missing_deadline.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_explicitly_disabled_staleness_requires_no_deadline(self):
        receipt = make_receipt()
        with patch.dict(
            "os.environ",
            {"TRADING_BOT_MARKET_NOTICE_STALENESS_SECONDS": "0"},
        ):
            job = make_job(receipt=receipt)
            decision = await self.decide(
                job,
                receipt=receipt,
                state=make_state(),
                now=TRANSITION_AT + timedelta(days=1),
            )

        self.assertIsNone(job.freshness_deadline_at)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_missing_receipt_or_runtime_state_never_sends(self):
        receipt = make_receipt()
        missing_receipt = await self.decide(make_job(receipt=receipt))
        missing_state = await self.decide(
            make_job(receipt=receipt),
            receipt=receipt,
        )

        self.assertEqual(
            missing_receipt.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(
            missing_state.outcome,
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
        )

    async def test_route_mismatches_are_quarantined_before_source_load(self):
        receipt = make_receipt()
        jobs = (
            make_job(receipt=receipt, feeder_kind=TelegramFeederKind.TRADE),
            make_job(receipt=receipt, destination_class=TelegramDestinationClass.PRIVATE),
            make_job(receipt=receipt, destination_key="channel:-100999"),
            make_job(receipt=receipt, method="editMessageText"),
            make_job(receipt=receipt, bot_identity="channel_editor"),
        )

        for job in jobs:
            with self.subTest(job=job):
                decision = await self.decide(job, receipt=receipt, state=make_state())
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )

    async def test_payload_hash_and_authoritative_text_are_enforced(self):
        receipt = make_receipt()
        decisions = (
            await self.decide(
                make_job(receipt=receipt, payload_hash="0" * 64),
                receipt=receipt,
            ),
            await self.decide(
                make_job(
                    receipt=receipt,
                    payload={"chat_id": CHANNEL_ID, "text": "stale"},
                ),
                receipt=receipt,
            ),
        )

        self.assertTrue(
            all(
                decision.outcome == TelegramFreshnessOutcome.QUARANTINED
                for decision in decisions
            )
        )

    async def test_receipt_identity_status_channel_and_version_are_enforced(self):
        valid = make_receipt()
        invalid_receipts = (
            make_receipt(dedupe_key="wrong"),
            make_receipt(transition="OPENED"),
            make_receipt(channel_id=None),
            make_receipt(channel_id="-100999"),
            make_receipt(status=" pending "),
            make_receipt(status="future_status"),
            make_receipt(telegram_message_id=42),
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt):
                decision = await self.decide(
                    make_job(receipt=receipt),
                    receipt=receipt,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )

        invalid_version = await self.decide(
            make_job(receipt=valid, source_version=2),
            receipt=valid,
        )
        self.assertEqual(
            invalid_version.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_template_change_supersedes_but_identity_tamper_quarantines(self):
        old_template = make_receipt(notice_text="old market notice")
        superseded = await self.decide(
            make_job(receipt=old_template),
            receipt=old_template,
        )
        tampered = make_receipt()
        tampered.notice_text = "tampered without new dedupe"
        quarantined = await self.decide(
            make_job(receipt=tampered),
            receipt=tampered,
        )
        sent_old_template = make_receipt(
            notice_text="old market notice",
            status=MARKET_NOTICE_STATUS_SENT,
            telegram_message_id=81,
        )
        sent_noop = await self.decide(
            make_job(receipt=sent_old_template),
            receipt=sent_old_template,
        )

        self.assertEqual(
            superseded.outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )
        self.assertEqual(
            superseded.reason,
            "market_freshness_notice_template_changed",
        )
        self.assertEqual(
            quarantined.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(sent_noop.outcome, TelegramFreshnessOutcome.SENT_NOOP)

    async def test_closed_transition_and_unsupported_action_are_explicit(self):
        closed = make_receipt(
            transition=MARKET_NOTICE_TRANSITION_CLOSED,
            notice_text=MARKET_CLOSED_CHANNEL_NOTICE,
        )
        closed_decision = await self.decide(
            make_job(receipt=closed),
            receipt=closed,
            state=make_state(is_open=False),
        )
        unsupported = await self.decide(
            make_job(TelegramDeliveryAction.NONCRITICAL_MARKET, receipt=closed),
        )

        self.assertEqual(closed_decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(
            unsupported.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )


if __name__ == "__main__":
    unittest.main()
