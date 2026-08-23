from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from core import telegram_delivery_queue_worker as worker
from core.services import telegram_offer_queue_feedback as feedback_module
from core.telegram_delivery_queue_limiter import TelegramDeliveryDispatchAdmission
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_dispatch_latency_hot_path import (
    HOT_PATH_SCHEMA_VERSION,
    apply_freshness_reuses_session_rows,
    assert_dispatchable_revalidates_without_reload,
    current_successful_send_offer_state_reads,
    locked_telegram_dispatch_hot_path,
    previous_successful_send_offer_state_reads,
    validate_accepts_preloaded_rows,
    worker_explicit_freshness_check_count,
)
from core.utils import utc_now
from tests.test_telegram_delivery_offer_freshness import (
    CHANNEL_ID,
    NOW,
    freshness,
    make_job as make_freshness_job,
    make_offer as make_freshness_offer,
    make_state as make_freshness_state,
)
from tests.test_telegram_delivery_queue_worker import (
    _AllowLimiter,
    _NoopLifecycleFeedback,
    TelegramDeliveryQueueWorkerSafetyTests,
)
from tests.test_telegram_offer_queue_feedback import make_job, make_offer, make_state


class TelegramDispatchLatencyHotPathTests(unittest.IsolatedAsyncioTestCase):
    def test_successful_send_query_budget_drops_without_removing_guards(self):
        lock = locked_telegram_dispatch_hot_path()
        before = previous_successful_send_offer_state_reads()
        after = current_successful_send_offer_state_reads()

        self.assertEqual(lock.schema_version, HOT_PATH_SCHEMA_VERSION)
        self.assertEqual(lock.evidence_kind, "code_derived_hot_path_query_lock")
        self.assertFalse(lock.live_percentiles_collected)
        self.assertEqual(lock.worker_explicit_freshness_checks, 2)
        self.assertEqual(worker_explicit_freshness_check_count(), 2)
        self.assertTrue(lock.assert_dispatchable_revalidates)
        self.assertTrue(assert_dispatchable_revalidates_without_reload())
        self.assertTrue(validate_accepts_preloaded_rows())
        self.assertTrue(apply_freshness_reuses_session_rows())
        self.assertEqual(before.before_telegram, 8)
        self.assertEqual(after.before_telegram, 6)
        self.assertLess(after.before_telegram, before.before_telegram)
        self.assertEqual(after.assert_dispatchable_validate, 0)
        self.assertEqual(after.apply_delivery_result, before.apply_delivery_result)
        self.assertEqual(lock.before.before_telegram, 8)
        self.assertEqual(lock.after.before_telegram, 6)

    def test_hot_path_document_does_not_invent_live_percentiles(self):
        text = Path(
            "docs/TELEGRAM_DISPATCH_LATENCY_HOT_PATH_20260823.md"
        ).read_text(encoding="utf-8")

        self.assertIn("code_derived_hot_path_query_lock", text)
        self.assertIn("۸", text)
        self.assertIn("۶", text)
        self.assertIn("ادعای قابل‌ارسال بودن", text)
        self.assertNotIn("p50=", text.lower())

    async def test_validate_skips_loaders_when_rows_are_passed(self):
        offer = make_freshness_offer()
        state = make_freshness_state(telegram_message_id=None)
        with patch.object(
            freshness,
            "_load_offer",
            new=AsyncMock(side_effect=AssertionError("offer reloaded")),
        ), patch.object(
            freshness,
            "_load_publication_state",
            new=AsyncMock(side_effect=AssertionError("state reloaded")),
        ):
            decision = await freshness.validate_offer_telegram_delivery_freshness(
                object(),
                make_freshness_job(),
                NOW,
                expected_channel_id=CHANNEL_ID,
                offer=offer,
                publication_state=state,
            )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(decision.reason, "offer_freshness_current")

    async def test_dispatch_guard_passes_locked_rows_into_validator(self):
        job = make_job(TelegramDeliveryAction.OFFER_PUBLISH)
        offer = make_offer()
        state = make_state()
        send = TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SEND,
            reason="current",
        )
        load = AsyncMock(return_value=(offer, state))
        validate = AsyncMock(return_value=send)
        adapter = feedback_module.TelegramOfferQueueLifecycleFeedback()
        with patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=load,
        ), patch.object(
            feedback_module,
            "validate_offer_telegram_delivery_freshness",
            new=validate,
        ), patch.object(
            feedback_module,
            "_configured_channel_id",
            return_value=-100,
        ):
            await adapter.assert_dispatchable(SimpleNamespace(), job, utc_now())

        load.assert_awaited_once()
        validate.assert_awaited_once()
        self.assertIs(validate.await_args.kwargs["offer"], offer)
        self.assertIs(validate.await_args.kwargs["publication_state"], state)

    async def test_apply_freshness_reuses_session_rows_on_the_production_call(self):
        job = make_job(TelegramDeliveryAction.OFFER_PUBLISH)
        offer = make_offer()
        state = make_state(telegram_message_id=None)
        state.id = 3
        state.offer_public_id = offer.offer_public_id
        offer_result = MagicMock()
        offer_result.scalar_one_or_none.return_value = offer
        state_result = MagicMock()
        state_result.scalar_one_or_none.return_value = state
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=(offer_result, state_result)),
            flush=AsyncMock(),
        )
        decision = TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="offer_freshness_already_published",
        )
        load = AsyncMock(side_effect=AssertionError("public-id reload"))
        adapter = feedback_module.TelegramOfferQueueLifecycleFeedback()
        with patch.object(
            feedback_module,
            "_session_bound_offer_and_state",
            return_value=(offer, state),
        ), patch.object(
            feedback_module,
            "_load_offer_and_state_for_update",
            new=load,
        ), patch.object(
            feedback_module,
            "_mark_terminal_without_publication",
        ) as mark_terminal:
            await adapter.apply_freshness(db, job, decision, utc_now())

        load.assert_not_awaited()
        mark_terminal.assert_called_once()
        self.assertEqual(db.execute.await_count, 2)
        db.flush.assert_awaited_once()

    async def test_stale_job_is_rejected_before_channel_budget(self):
        harness = TelegramDeliveryQueueWorkerSafetyTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        job = SimpleNamespace(
            id=1101,
            lease_token=21,
            method="sendMessage",
            payload={"chat_id": -100, "text": "redacted"},
            dedupe_key="stale-before-limiter",
            bot_identity="primary",
            destination_key="channel:-100",
        )
        db = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=db)
        context.__aexit__ = AsyncMock(return_value=False)
        limiter = _AllowLimiter()
        limiter.acquire = AsyncMock(
            side_effect=AssertionError("channel budget consumed")
        )
        gateway = AsyncMock()
        mark = AsyncMock()
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=harness._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            return_value=context,
        ), patch(
            "core.telegram_delivery_queue_worker.claim_next_telegram_delivery_job",
            new=AsyncMock(return_value=job),
        ), patch(
            "core.telegram_delivery_queue_worker.apply_telegram_delivery_freshness_result",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_database_now",
            new=AsyncMock(return_value=worker.utc_now()),
        ), patch(
            "core.telegram_delivery_queue_worker.mark_telegram_delivery_dispatch_started",
            new=mark,
        ):
            report = await worker.run_telegram_delivery_queue_cycle(
                bot_identity="primary",
                freshness_validator=AsyncMock(
                    return_value=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="offer_freshness_not_publishable",
                    )
                ),
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=limiter,
                recover_leases=False,
                limit=1,
            )

        self.assertEqual(report.status_counts, {"superseded": 1})
        limiter.acquire.assert_not_awaited()
        mark.assert_not_awaited()
        gateway.assert_not_awaited()

    async def test_stale_behind_limiter_is_caught_before_telegram(self):
        harness = TelegramDeliveryQueueWorkerSafetyTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        job = SimpleNamespace(
            id=1102,
            lease_token=22,
            method="sendMessage",
            payload={"chat_id": -100, "text": "redacted"},
            dedupe_key="stale-after-limiter",
            bot_identity="primary",
            destination_key="channel:-100",
        )
        db = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=db)
        context.__aexit__ = AsyncMock(return_value=False)
        limiter = _AllowLimiter()
        limiter.acquire = AsyncMock(
            return_value=TelegramDeliveryDispatchAdmission(allowed=True)
        )
        gateway = AsyncMock()
        mark = AsyncMock()
        freshness_apply = AsyncMock(side_effect=(True, False))
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=harness._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            return_value=context,
        ), patch(
            "core.telegram_delivery_queue_worker.claim_next_telegram_delivery_job",
            new=AsyncMock(return_value=job),
        ), patch(
            "core.telegram_delivery_queue_worker.apply_telegram_delivery_freshness_result",
            new=freshness_apply,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_database_now",
            new=AsyncMock(return_value=worker.utc_now()),
        ), patch(
            "core.telegram_delivery_queue_worker._release_unused_rate_limit_probe",
            new=AsyncMock(),
        ), patch(
            "core.telegram_delivery_queue_worker.mark_telegram_delivery_dispatch_started",
            new=mark,
        ):
            report = await worker.run_telegram_delivery_queue_cycle(
                bot_identity="primary",
                freshness_validator=AsyncMock(
                    side_effect=(
                        TelegramFreshnessDecision(
                            TelegramFreshnessOutcome.SEND,
                            reason="current",
                        ),
                        TelegramFreshnessDecision(
                            TelegramFreshnessOutcome.SUPERSEDED,
                            reason="offer_freshness_publish_deadline_passed",
                        ),
                    )
                ),
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=limiter,
                recover_leases=False,
                limit=1,
            )

        self.assertEqual(report.status_counts, {"superseded": 1})
        limiter.acquire.assert_awaited_once()
        self.assertEqual(freshness_apply.await_count, 2)
        mark.assert_not_awaited()
        gateway.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
