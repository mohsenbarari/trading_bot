import unittest
from datetime import datetime, timezone

from core.services.telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_QUEUE_METHODS,
)
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    apply_gateway_result,
)
from core.telegram_delivery_scheduled_operation_freshness import (
    SCHEDULED_OPERATION_FRESHNESS_ACTIONS,
    SCHEDULED_OPERATION_POLICIES,
)
from core.telegram_gateway import TelegramGatewayResult


class TelegramScheduledOperationContractTests(unittest.TestCase):
    def test_exact_five_source_actions_have_bounded_routes(self):
        self.assertEqual(
            SCHEDULED_OPERATION_FRESHNESS_ACTIONS,
            frozenset(
                {
                    TelegramDeliveryAction.NONCRITICAL_MARKET,
                    TelegramDeliveryAction.PREAUTH_INTERACTION,
                    TelegramDeliveryAction.PREAUTH_INTERACTION_EDIT,
                    TelegramDeliveryAction.TEMPORARY_CLEANUP,
                    TelegramDeliveryAction.COSMETIC_CLEANUP,
                }
            ),
        )
        self.assertEqual(
            SCHEDULED_OPERATION_POLICIES[
                TelegramDeliveryAction.NONCRITICAL_MARKET
            ].feeder,
            TelegramFeederKind.MARKET_STATUS,
        )
        self.assertEqual(
            SCHEDULED_OPERATION_POLICIES[
                TelegramDeliveryAction.TEMPORARY_CLEANUP
            ].method,
            "deleteMessage",
        )
        self.assertEqual(
            SCHEDULED_OPERATION_POLICIES[
                TelegramDeliveryAction.COSMETIC_CLEANUP
            ].method,
            "editMessageReplyMarkup",
        )
        self.assertEqual(
            SCHEDULED_OPERATION_POLICIES[
                TelegramDeliveryAction.PREAUTH_INTERACTION
            ].method,
            "sendMessage",
        )
        self.assertEqual(
            SCHEDULED_OPERATION_POLICIES[
                TelegramDeliveryAction.PREAUTH_INTERACTION_EDIT
            ].method,
            "editMessageText",
        )
        self.assertIn("deleteMessage", SUPPORTED_TELEGRAM_QUEUE_METHODS)

    def test_scheduled_source_table_is_foreign_local_no_sync(self):
        entry = get_sync_registry_entry("telegram_scheduled_operations")
        self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)

    def test_delete_of_already_absent_message_is_successful_noop(self):
        now = datetime.now(timezone.utc)
        job = TelegramDeliveryJob(
            id=1,
            dedupe_key="delete-1",
            feeder=TelegramFeederKind.TIMED_BOT,
            feeder_rank=3,
            source_natural_id="cleanup-1",
            source_version=1,
            destination_key="private:user:1",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="deleteMessage",
            payload={"chat_id": 7001, "message_id": 81},
            action=TelegramDeliveryAction.TEMPORARY_CLEANUP,
            created_sequence=1,
            state=TelegramDeliveryState.LEASED,
            worker_id="worker",
            lease_token=1,
            lease_until=now,
        )
        decision = apply_gateway_result(
            job,
            TelegramGatewayResult(
                ok=False,
                method="deleteMessage",
                status_code=400,
                response_json={
                    "ok": False,
                    "description": "Bad Request: message to delete not found",
                },
            ),
            now=now,
            retry_after_safety_seconds=0.1,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT_NOOP)
        self.assertEqual(job.state, TelegramDeliveryState.SENT_NOOP)


if __name__ == "__main__":
    unittest.main()
