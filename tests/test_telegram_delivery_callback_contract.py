import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_callback_queue_service as queue_service
from core.services.telegram_callback_queue_feedback import (
    TelegramCallbackQueueLifecycleFeedback,
)
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_callback_contract import (
    CALLBACK_DEADLINE_TEMPLATE_VERSION,
    OFFER_EXPIRY_CALLBACK_TEMPLATE_VERSION,
    TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS,
    build_telegram_callback_answer_payload,
    telegram_callback_destination_key,
    telegram_callback_source_natural_id,
)
from core.telegram_delivery_callback_freshness import (
    validate_telegram_callback_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
    apply_gateway_result,
)
from core.telegram_gateway import TelegramGatewayResult
from core.telegram_delivery_runtime_composition import (
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)
from core.utils import utc_now


class TelegramDeliveryCallbackContractTests(unittest.IsolatedAsyncioTestCase):
    def _job(
        self,
        *,
        action=TelegramDeliveryAction.CALLBACK_DEADLINE,
        callback_query_id="callback-secret-id",
    ):
        now = utc_now()
        payload = build_telegram_callback_answer_payload(
            callback_query_id=callback_query_id,
            text="پاسخ عادی",
            show_alert=False,
        )
        _, payload_hash = canonical_telegram_delivery_payload(payload)
        is_expiry = action == TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK
        return SimpleNamespace(
            id=1,
            feeder_kind=(
                TelegramFeederKind.OFFER_CONTROL
                if is_expiry
                else TelegramFeederKind.DIRECT
            ),
            action_kind=action,
            bot_identity="primary",
            destination_key=telegram_callback_destination_key(
                callback_query_id
            ),
            destination_class=TelegramDestinationClass.PRIVATE,
            method="answerCallbackQuery",
            payload=payload,
            payload_hash=payload_hash,
            source_natural_id=telegram_callback_source_natural_id(
                callback_query_id
            ),
            source_version=1,
            template_version=(
                OFFER_EXPIRY_CALLBACK_TEMPLATE_VERSION
                if is_expiry
                else CALLBACK_DEADLINE_TEMPLATE_VERSION
            ),
            delivery_deadline_at=now
            + timedelta(seconds=TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS),
            eligible_at=None,
            freshness_deadline_at=None,
            campaign_id=None,
            run_id=None,
            created_at=now,
        )

    def test_payload_preserves_toast_semantics_and_limits_text(self):
        self.assertEqual(
            build_telegram_callback_answer_payload(
                callback_query_id="cb-1",
                text="پیام",
                show_alert=False,
            ),
            {
                "callback_query_id": "cb-1",
                "show_alert": False,
                "text": "پیام",
            },
        )
        with self.assertRaisesRegex(ValueError, "text_too_long"):
            build_telegram_callback_answer_payload(
                callback_query_id="cb-1",
                text="x" * 201,
            )

    def test_identity_does_not_expose_callback_query_id(self):
        source = telegram_callback_source_natural_id("callback-secret-id")
        destination = telegram_callback_destination_key("callback-secret-id")
        self.assertNotIn("callback-secret-id", source)
        self.assertNotIn("callback-secret-id", destination)
        self.assertEqual(source.split(":", 1)[1], destination.split(":", 1)[1])

    async def test_generic_and_offer_expiry_enter_main_queue_directly_at_m0(self):
        expected = SimpleNamespace(created=True)
        received_at = utc_now()
        with patch.object(
            queue_service,
            "enqueue_telegram_delivery_job",
            new=AsyncMock(return_value=expected),
        ) as enqueue:
            generic = await queue_service.enqueue_telegram_callback_answer(
                object(),
                current_server="foreign",
                callback_query_id="cb-generic",
                received_at=received_at,
                text="ok",
            )
            expiry = await queue_service.enqueue_telegram_callback_answer(
                object(),
                current_server="foreign",
                callback_query_id="cb-expiry",
                received_at=received_at,
                action=TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
            )
        self.assertIs(generic, expected)
        self.assertIs(expiry, expected)
        generic_call, expiry_call = enqueue.await_args_list
        self.assertEqual(
            generic_call.kwargs["feeder"],
            TelegramFeederKind.DIRECT,
        )
        self.assertEqual(
            generic_call.kwargs["action"],
            TelegramDeliveryAction.CALLBACK_DEADLINE,
        )
        self.assertEqual(
            expiry_call.kwargs["feeder"],
            TelegramFeederKind.OFFER_CONTROL,
        )
        self.assertEqual(
            expiry_call.kwargs["action"],
            TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
        )
        for call in (generic_call, expiry_call):
            self.assertEqual(call.kwargs["method"], "answerCallbackQuery")
            self.assertEqual(call.kwargs["bot_identity"], "primary")
            self.assertEqual(
                call.kwargs["delivery_deadline_at"],
                received_at
                + timedelta(seconds=TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS),
            )

    async def test_freshness_sends_before_deadline_and_expires_at_deadline(self):
        for action in (
            TelegramDeliveryAction.CALLBACK_DEADLINE,
            TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
        ):
            with self.subTest(action=action.value):
                job = self._job(action=action)
                before = await validate_telegram_callback_delivery_freshness(
                    object(),
                    job,
                    job.delivery_deadline_at - timedelta(microseconds=1),
                )
                expired = await validate_telegram_callback_delivery_freshness(
                    object(),
                    job,
                    job.delivery_deadline_at,
                )
                self.assertEqual(before.outcome, TelegramFreshnessOutcome.SEND)
                self.assertEqual(
                    expired.outcome,
                    TelegramFreshnessOutcome.EXPIRED_INTERACTION,
                )

    async def test_tampered_callback_route_is_quarantined_and_guarded(self):
        job = self._job()
        job.destination_key = "callback-query:tampered"
        decision = await validate_telegram_callback_delivery_freshness(
            object(),
            job,
            utc_now(),
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)
        feedback = TelegramCallbackQueueLifecycleFeedback()
        with self.assertRaisesRegex(RuntimeError, "dispatch_guard_rejected"):
            await feedback.assert_dispatchable(object(), job, utc_now())

    def test_provider_query_too_old_is_expired_interaction_not_failure(self):
        now = utc_now()
        callback_job = TelegramDeliveryJob(
            id=1,
            dedupe_key="callback-expired",
            feeder=TelegramFeederKind.DIRECT,
            feeder_rank=0,
            source_natural_id="callback-expired",
            source_version=1,
            action=TelegramDeliveryAction.CALLBACK_DEADLINE,
            destination_key="callback-query:expired",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="answerCallbackQuery",
            payload={"callback_query_id": "expired", "show_alert": False},
            created_sequence=1,
        )
        decision = apply_gateway_result(
            callback_job,
            TelegramGatewayResult(
                ok=False,
                method="answerCallbackQuery",
                status_code=400,
                response_json={
                    "ok": False,
                    "error_code": 400,
                    "description": (
                        "Bad Request: query is too old and response timeout "
                        "expired or query ID is invalid"
                    ),
                },
            ),
            now=now,
            retry_after_safety_seconds=0.1,
        )
        self.assertEqual(
            decision.outcome,
            TelegramDeliveryOutcome.EXPIRED_INTERACTION,
        )
        self.assertEqual(
            callback_job.state,
            TelegramDeliveryState.EXPIRED_INTERACTION,
        )

    def test_runtime_coverage_is_complete_after_scheduled_sources(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        self.assertTrue(freshness.complete)
        self.assertEqual(freshness.missing_actions, ())
        self.assertEqual(freshness.missing_actions, lifecycle.missing_actions)


if __name__ == "__main__":
    unittest.main()
