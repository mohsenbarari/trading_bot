import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_offer_queue_feedback as feedback_module
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.utils import utc_now
from models.offer import OfferStatus


def make_offer(**overrides):
    values = {
        "id": 10,
        "offer_public_id": "ofr_feedback_10",
        "version_id": 3,
        "status": OfferStatus.ACTIVE,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_state(**overrides):
    values = {
        "telegram_message_id": None,
        "offer_version_id": 2,
        "last_attempt_at": None,
        "error_code": None,
        "error_message": None,
        "next_retry_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_job(action, **overrides):
    values = {
        "id": 20,
        "source_natural_id": "ofr_feedback_10",
        "source_version": 3,
        "action_kind": action,
        "telegram_message_id": None,
        "last_error_message": None,
        "freshness_deadline_at": None,
        "state": TelegramDeliveryState.LEASED,
        "next_retry_at": None,
        "outcome_reason": None,
        "terminal_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramOfferQueueFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(flush=AsyncMock())
        self.offer = make_offer()
        self.state = make_state()
        self.load = AsyncMock(return_value=(self.offer, self.state))
        self.feedback = feedback_module.TelegramOfferQueueLifecycleFeedback()

    async def test_dispatch_guard_requires_authoritative_send_decision(self):
        job = make_job(TelegramDeliveryAction.OFFER_PUBLISH)
        send = TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SEND,
            reason="current",
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ), patch.object(
            feedback_module,
            "validate_offer_telegram_delivery_freshness",
            new=AsyncMock(return_value=send),
        ) as validate, patch.object(
            feedback_module,
            "_configured_channel_id",
            return_value=-100,
        ):
            await self.feedback.assert_dispatchable(self.db, job, utc_now())

        validate.assert_awaited_once()
        self.load.assert_awaited_once()

        stale = TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="stale",
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=AsyncMock(return_value=(self.offer, self.state)),
        ), patch.object(
            feedback_module,
            "validate_offer_telegram_delivery_freshness",
            new=AsyncMock(return_value=stale),
        ), patch.object(
            feedback_module,
            "_configured_channel_id",
            return_value=-100,
        ):
            with self.assertRaisesRegex(
                feedback_module.TelegramOfferQueueFeedbackError,
                "dispatch_guard_rejected",
            ):
                await self.feedback.assert_dispatchable(self.db, job, utc_now())

    async def test_publish_success_records_canonical_message_identity(self):
        job = make_job(
            TelegramDeliveryAction.OFFER_PUBLISH,
            telegram_message_id=901,
        )
        decision = TelegramDeliveryDecision(
            TelegramDeliveryOutcome.SENT,
            reason="sent",
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ), patch.object(
            feedback_module,
            "_configured_channel_id",
            return_value=-100,
        ), patch.object(
            feedback_module,
            "mark_telegram_publication_success",
        ) as mark_success:
            await self.feedback.apply_delivery_result(
                self.db,
                job,
                decision,
                utc_now(),
            )

        mark_success.assert_called_once()
        self.assertEqual(mark_success.call_args.kwargs["message_id"], 901)
        self.assertEqual(mark_success.call_args.kwargs["chat_id"], -100)
        self.db.flush.assert_awaited_once()

    async def test_publish_success_without_message_id_rolls_back_feedback(self):
        job = make_job(TelegramDeliveryAction.OFFER_PUBLISH)
        decision = TelegramDeliveryDecision(TelegramDeliveryOutcome.SENT)
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ):
            with self.assertRaisesRegex(
                feedback_module.TelegramOfferQueueFeedbackError,
                "publish_message_missing",
            ):
                await self.feedback.apply_delivery_result(
                    self.db,
                    job,
                    decision,
                    utc_now(),
                )

        self.db.flush.assert_not_awaited()

    async def test_edit_success_advances_rendered_version(self):
        job = make_job(
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            feeder_kind="offer_edit",
            feeder_rank=0,
            eligible_at=utc_now(),
            created_at=utc_now(),
        )
        decision = TelegramDeliveryDecision(TelegramDeliveryOutcome.SENT)
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ), patch.object(
            feedback_module,
            "_mark_edit_success",
        ) as mark_edit, patch.object(
            feedback_module,
            "record_offer_edit_delivery_success",
            new=AsyncMock(return_value={0: 1}),
        ) as record_fairness:
            await self.feedback.apply_delivery_result(
                self.db,
                job,
                decision,
                utc_now(),
            )

        mark_edit.assert_called_once()
        record_fairness.assert_awaited_once_with(
            self.db,
            job,
            now=record_fairness.await_args.kwargs["now"],
        )
        self.db.flush.assert_awaited_once()

    async def test_rate_limit_holds_domain_state_for_same_job_retry(self):
        job = make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT)
        decision = TelegramDeliveryDecision(
            TelegramDeliveryOutcome.RETRY_PENDING,
            reason="telegram_rate_limited",
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ), patch.object(
            feedback_module,
            "_mark_edit_success",
        ) as mark_edit, patch.object(
            feedback_module,
            "_record_failure_evidence",
        ) as record_failure:
            await self.feedback.apply_delivery_result(
                self.db,
                job,
                decision,
                utc_now(),
            )

        mark_edit.assert_not_called()
        record_failure.assert_not_called()
        self.db.flush.assert_awaited_once()

    async def test_permanent_failure_records_evidence_without_advancing_version(self):
        job = make_job(
            TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            last_error_message="message cannot be edited",
        )
        decision = TelegramDeliveryDecision(
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            reason="telegram_message_uneditable",
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ):
            await self.feedback.apply_delivery_result(
                self.db,
                job,
                decision,
                utc_now(),
            )

        self.assertEqual(
            self.state.error_code,
            "telegram_queue:telegram_message_uneditable",
        )
        self.assertEqual(self.state.error_message, "message cannot be edited")
        self.assertEqual(self.state.offer_version_id, 2)

    async def test_reclassify_enqueues_replacement_and_supersedes_old_job(self):
        job = make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT)
        decision = TelegramFreshnessDecision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.TRADED_OFFER_EDIT,
            reason="partial_became_terminal",
        )
        enqueue_result = SimpleNamespace(
            queue_result=SimpleNamespace(created=True),
            skipped_reason=None,
        )
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=self.load,
        ), patch.object(
            feedback_module,
            "enqueue_current_offer_delivery",
            new=AsyncMock(return_value=enqueue_result),
        ) as enqueue, patch.object(
            feedback_module,
            "current_server",
            return_value="foreign",
        ), patch.object(
            feedback_module,
            "_configured_channel_id",
            return_value=-100,
        ):
            await self.feedback.apply_freshness(
                self.db,
                job,
                decision,
                utc_now(),
            )

        self.assertEqual(
            enqueue.await_args.kwargs["action"],
            TelegramDeliveryAction.TRADED_OFFER_EDIT,
        )
        self.assertEqual(job.state, TelegramDeliveryState.SUPERSEDED)
        self.assertIsNotNone(job.terminal_at)
        self.db.flush.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
