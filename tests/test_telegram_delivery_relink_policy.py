import unittest

from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_delivery_relink_policy import (
    TELEGRAM_DELIVERY_RELINK_POLICIES,
    TelegramRelinkBehavior,
    telegram_delivery_relink_behavior,
)


class TelegramDeliveryRelinkPolicyTests(unittest.TestCase):
    def test_every_action_has_exactly_one_explicit_policy(self):
        self.assertEqual(set(TELEGRAM_DELIVERY_RELINK_POLICIES), set(TelegramDeliveryAction))

    def test_sensitive_private_families_never_follow_a_new_route(self):
        for action in {
            TelegramDeliveryAction.TRADE_RESULT,
            TelegramDeliveryAction.ACCOUNT_STATUS,
            TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE,
            TelegramDeliveryAction.ADMIN_BROADCAST,
            TelegramDeliveryAction.OFFER_SUCCESS,
            TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
        }:
            with self.subTest(action=action.value):
                self.assertEqual(
                    telegram_delivery_relink_behavior(action),
                    TelegramRelinkBehavior.SUPPRESS_ON_RELINK,
                )

    def test_channel_and_provider_bound_targets_do_not_rebind_to_user_route(self):
        self.assertEqual(
            telegram_delivery_relink_behavior(TelegramDeliveryAction.OFFER_PUBLISH),
            TelegramRelinkBehavior.ROUTE_INDEPENDENT,
        )
        self.assertEqual(
            telegram_delivery_relink_behavior(
                TelegramDeliveryAction.CALLBACK_DEADLINE
            ),
            TelegramRelinkBehavior.PROVIDER_TARGET_BOUND,
        )


if __name__ == "__main__":
    unittest.main()
