from datetime import datetime, timezone
import unittest

from core.telegram_delivery_offer_freshness import OFFER_FRESHNESS_ACTIONS
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_multi_publisher_contract import (
    TELEGRAM_B2B_PROTOCOL_VERSION,
    TELEGRAM_MULTI_PUBLISHER_METRIC_FIELDS,
    TELEGRAM_PUBLISHER_IDENTITIES,
    TELEGRAM_PUBLISHER_OWNER_REQUIRED_METHODS,
    TELEGRAM_PUBLISHER_OWNED_OFFER_ACTIONS,
    TelegramMultiPublisherContractError,
    TelegramPublisherB2BEnvelope,
    TelegramPublisherB2BMessageType,
    TelegramPublisherDispatchState,
    TelegramPublisherLifecycleOperation,
    classify_telegram_publisher_offer_action,
    is_allowed_telegram_publisher_dispatch_transition,
    parse_telegram_publisher_b2b_envelope,
    render_telegram_publisher_b2b_envelope,
)


COMMAND_ID = "80e08f66-4164-4d45-b3b9-53e64df6d3f7"
ENQUEUED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
ACK_SENT_AT = datetime(2026, 8, 11, 12, 0, 1, tzinfo=timezone.utc)


class TelegramMultiPublisherContractTests(unittest.TestCase):
    def test_five_allowlisted_publisher_lanes_and_owner_methods_are_fixed(self):
        self.assertEqual(
            TELEGRAM_PUBLISHER_IDENTITIES,
            ("publisher_1", "publisher_2", "publisher_3", "publisher_4", "publisher_5"),
        )
        self.assertEqual(
            TELEGRAM_PUBLISHER_OWNER_REQUIRED_METHODS,
            {
                "answerCallbackQuery",
                "deleteMessage",
                "editMessageReplyMarkup",
                "editMessageText",
                "sendMessage",
            },
        )

    def test_offer_action_inventory_exactly_covers_existing_channel_lifecycle(self):
        self.assertEqual(TELEGRAM_PUBLISHER_OWNED_OFFER_ACTIONS, OFFER_FRESHNESS_ACTIONS)
        self.assertEqual(
            classify_telegram_publisher_offer_action(
                TelegramDeliveryAction.OFFER_PUBLISH
            ),
            TelegramPublisherLifecycleOperation.PUBLISH,
        )
        self.assertEqual(
            classify_telegram_publisher_offer_action(
                TelegramDeliveryAction.TRADED_OFFER_EDIT
            ),
            TelegramPublisherLifecycleOperation.TERMINAL_EDIT,
        )
        self.assertEqual(
            classify_telegram_publisher_offer_action(
                TelegramDeliveryAction.RECONCILIATION_EDIT
            ),
            TelegramPublisherLifecycleOperation.RECONCILIATION_EDIT,
        )
        with self.assertRaisesRegex(
            TelegramMultiPublisherContractError,
            "offer_action_unsupported",
        ):
            classify_telegram_publisher_offer_action(
                TelegramDeliveryAction.CALLBACK_DEADLINE
            )

    def test_dispatch_envelope_is_short_versioned_and_payload_free(self):
        rendered = render_telegram_publisher_b2b_envelope(
            TelegramPublisherB2BEnvelope(
                message_type=TelegramPublisherB2BMessageType.DISPATCH,
                command_id=COMMAND_ID,
                sequence=31,
                enqueued_at=ENQUEUED_AT,
            )
        )
        self.assertEqual(
            rendered,
            f"{TELEGRAM_B2B_PROTOCOL_VERSION}|dispatch|{COMMAND_ID}|31|2026-08-11T12:00:00Z",
        )
        self.assertNotIn("offer", rendered.lower())
        self.assertNotIn("price", rendered.lower())
        self.assertEqual(
            parse_telegram_publisher_b2b_envelope(rendered),
            TelegramPublisherB2BEnvelope(
                message_type=TelegramPublisherB2BMessageType.DISPATCH,
                command_id=COMMAND_ID,
                sequence=31,
                enqueued_at=ENQUEUED_AT,
            ),
        )

    def test_ack_envelope_round_trip_and_rejects_extra_business_payload(self):
        rendered = render_telegram_publisher_b2b_envelope(
            TelegramPublisherB2BEnvelope(
                message_type=TelegramPublisherB2BMessageType.ACK,
                command_id=COMMAND_ID,
                sequence=31,
                enqueued_at=ENQUEUED_AT,
                ack_sent_at=ACK_SENT_AT,
            )
        )
        parsed = parse_telegram_publisher_b2b_envelope(rendered)
        self.assertEqual(parsed.ack_sent_at, ACK_SENT_AT)
        with self.assertRaisesRegex(
            TelegramMultiPublisherContractError,
            "envelope_shape_invalid",
        ):
            parse_telegram_publisher_b2b_envelope(f"{rendered}|offer-data")

    def test_envelope_rejects_unknown_protocol_or_nonrandom_command_id(self):
        with self.assertRaisesRegex(
            TelegramMultiPublisherContractError,
            "protocol_unsupported",
        ):
            parse_telegram_publisher_b2b_envelope(
                f"tbq0|dispatch|{COMMAND_ID}|1|2026-08-11T12:00:00Z"
            )
        with self.assertRaisesRegex(
            TelegramMultiPublisherContractError,
            "command_id_invalid",
        ):
            parse_telegram_publisher_b2b_envelope(
                "tbq1|dispatch|offer-123|1|2026-08-11T12:00:00Z"
            )

    def test_dispatch_state_machine_allows_recovery_but_not_terminal_reopen(self):
        self.assertTrue(
            is_allowed_telegram_publisher_dispatch_transition(
                TelegramPublisherDispatchState.PENDING,
                TelegramPublisherDispatchState.ACKNOWLEDGED,
            )
        )
        self.assertTrue(
            is_allowed_telegram_publisher_dispatch_transition(
                TelegramPublisherDispatchState.SENT,
                TelegramPublisherDispatchState.RETRY_DUE,
            )
        )
        self.assertFalse(
            is_allowed_telegram_publisher_dispatch_transition(
                TelegramPublisherDispatchState.ACKNOWLEDGED,
                TelegramPublisherDispatchState.RETRY_DUE,
            )
        )
        self.assertFalse(
            is_allowed_telegram_publisher_dispatch_transition(
                TelegramPublisherDispatchState.FAILED,
                TelegramPublisherDispatchState.SENT,
            )
        )

    def test_metric_schema_contains_only_operational_identifiers(self):
        self.assertTrue(
            {
                "lane",
                "command_id",
                "job_id",
                "destination_key",
                "method",
                "retry_after_seconds",
                "command_lag_ms",
                "receipt_lag_ms",
                "queue_depth",
                "health_state",
                "reason_code",
            }.issubset(TELEGRAM_MULTI_PUBLISHER_METRIC_FIELDS)
        )
        self.assertFalse(
            {"token", "offer_text", "price", "user_id"}.intersection(
                TELEGRAM_MULTI_PUBLISHER_METRIC_FIELDS
            )
        )


if __name__ == "__main__":
    unittest.main()
