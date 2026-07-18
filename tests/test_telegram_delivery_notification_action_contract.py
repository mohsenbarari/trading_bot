import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_notification_outbox_service as outbox_service
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTIONS,
    TELEGRAM_NOTIFICATION_ACTION_POLICIES,
    TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES,
    telegram_notification_action_policy,
    telegram_notification_action_policy_from_source,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFeederKind,
)
from core.telegram_delivery_runtime_composition import (
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)
from models.telegram_notification_outbox import TelegramNotificationOutbox


class TelegramDeliveryNotificationActionContractTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_only_private_send_message_actions_are_allowlisted(self):
        self.assertEqual(
            TELEGRAM_NOTIFICATION_ACTIONS,
            frozenset(
                {
                    TelegramDeliveryAction.ACCOUNT_STATUS,
                    TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
                    TelegramDeliveryAction.GENERAL_IMMEDIATE,
                    TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
                    TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE,
                    TelegramDeliveryAction.TRADE_ALTERNATIVE,
                    TelegramDeliveryAction.TRADE_NONCRITICAL,
                    TelegramDeliveryAction.TRADE_RESPONSE,
                    TelegramDeliveryAction.TRADE_UNAVAILABLE,
                }
            ),
        )
        for forbidden in (
            TelegramDeliveryAction.CALLBACK_DEADLINE,
            TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
            TelegramDeliveryAction.OFFER_SUCCESS,
            TelegramDeliveryAction.TEMPORARY_CLEANUP,
        ):
            with self.subTest(forbidden=forbidden.value):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    telegram_notification_action_policy(forbidden)

    def test_source_types_are_unique_bounded_and_reversible(self):
        self.assertEqual(
            len(TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES),
            len(TELEGRAM_NOTIFICATION_ACTIONS),
        )
        for action, policy in TELEGRAM_NOTIFICATION_ACTION_POLICIES.items():
            with self.subTest(action=action.value):
                self.assertLessEqual(len(policy.source_type), 80)
                self.assertIs(
                    telegram_notification_action_policy_from_source(
                        policy.source_type
                    ),
                    policy,
                )
        self.assertFalse(
            TELEGRAM_NOTIFICATION_ACTION_POLICIES[
                TelegramDeliveryAction.ACCOUNT_STATUS
            ].require_bot_access
        )
        handoff_index = next(
            index
            for index in TelegramNotificationOutbox.__table__.indexes
            if index.name == "ix_telegram_notification_outbox_queue_handoff"
        )
        predicate = str(handoff_index.dialect_options["postgresql"]["where"])
        for source_type in TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES:
            with self.subTest(source_type=source_type):
                self.assertIn(source_type, predicate)

    async def test_generic_enqueue_stamps_action_contract(self):
        expected = SimpleNamespace(outbox=object(), created=True)
        recipient = outbox_service.TelegramNotificationRecipient(
            user_id=7,
            telegram_id=7007,
        )
        with patch.object(
            outbox_service,
            "enqueue_telegram_notification_once",
            new=AsyncMock(return_value=expected),
        ) as enqueue:
            result = await outbox_service.enqueue_telegram_action_notification_once(
                object(),
                recipient=recipient,
                action=TelegramDeliveryAction.TRADE_ALTERNATIVE,
                source_id="trade:11:alternative:7",
                text="پیشنهاد جایگزین",
                user_sync_version=5,
                parse_mode="Markdown",
                reply_markup={"inline_keyboard": []},
            )
        self.assertIs(result, expected)
        self.assertEqual(
            enqueue.await_args.kwargs["source_type"],
            "queue_action:trade_alternative",
        )
        self.assertEqual(
            enqueue.await_args.kwargs["extra_payload"],
            {
                "queue_action": "trade_alternative",
                "reply_markup": {"inline_keyboard": []},
                "user_sync_version": 5,
            },
        )

    async def test_account_enqueue_requires_explicit_state_snapshot(self):
        recipient = outbox_service.TelegramNotificationRecipient(
            user_id=8,
            telegram_id=8008,
        )
        with self.assertRaisesRegex(ValueError, "requires_state_contract"):
            await outbox_service.enqueue_telegram_action_notification_once(
                object(),
                recipient=recipient,
                action=TelegramDeliveryAction.ACCOUNT_STATUS,
                source_id="account:8",
                text="وضعیت حساب",
                user_sync_version=1,
            )
        with patch.object(
            outbox_service,
            "enqueue_telegram_notification_once",
            new=AsyncMock(return_value=SimpleNamespace(created=True)),
        ) as enqueue:
            await outbox_service.enqueue_account_status_telegram_notification_once(
                object(),
                recipient=recipient,
                source_id="account:8:inactive",
                text="حساب غیرفعال شد",
                account_status="inactive",
                messenger_blocked=False,
                user_sync_version=9,
            )
        self.assertEqual(
            enqueue.await_args.kwargs["extra_payload"],
            {
                "account_status": "inactive",
                "messenger_blocked": False,
                "queue_action": "account_status",
                "user_sync_version": 9,
            },
        )

    def test_runtime_coverage_now_leaves_only_source_specific_actions_open(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        expected = {
            "callback_deadline",
            "cosmetic_cleanup",
            "delayed_restriction",
            "noncritical_market",
            "offer_expiry_callback",
            "temporary_cleanup",
            "timed_security",
        }
        self.assertEqual(
            {action.value for action in freshness.missing_actions},
            expected,
        )
        self.assertEqual(freshness.missing_actions, lifecycle.missing_actions)

    def test_feeder_mapping_matches_accepted_priority_families(self):
        self.assertEqual(
            telegram_notification_action_policy(
                TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE
            ).feeder,
            TelegramFeederKind.OFFER_CONTROL,
        )
        self.assertEqual(
            telegram_notification_action_policy(
                TelegramDeliveryAction.TRADE_RESPONSE
            ).feeder,
            TelegramFeederKind.TRADE,
        )
        self.assertEqual(
            telegram_notification_action_policy(
                TelegramDeliveryAction.GENERAL_IMMEDIATE
            ).feeder,
            TelegramFeederKind.DIRECT,
        )


if __name__ == "__main__":
    unittest.main()
