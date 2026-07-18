import unittest
from pathlib import Path

from scripts.audit_telegram_delivery_calls import (
    REMAINING_DISPOSITION_BUDGETS,
    build_inventory,
    disposition_counts,
    inventory_check_failures,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class TelegramDeliveryCallsiteInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = build_inventory(REPO_ROOT)

    def test_every_runtime_callsite_is_classified_and_remaining_counts_do_not_grow(self):
        self.assertEqual(inventory_check_failures(self.inventory), [])
        counts = disposition_counts(self.inventory)
        for disposition, budget in REMAINING_DISPOSITION_BUDGETS.items():
            self.assertLessEqual(counts.get(disposition, 0), budget)

    def test_final_legacy_publication_boundary_is_owner_guarded(self):
        matches = [
            item
            for item in self.inventory
            if item.path == "core/services/telegram_offer_publication_service.py"
            and item.scope == "publish_offer_to_telegram_channel_once"
            and item.callee == "send_offer_to_channel"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].disposition, "legacy_owner_guarded")

    def test_reconciled_stale_builder_responses_are_queue_guarded(self):
        expected_scopes = {
            "_send_stale_trade_builder_guidance",
            "_answer_stale_trade_creation_callback",
        }
        matches = [
            item
            for item in self.inventory
            if item.path == "bot/handlers/trade_create.py"
            and item.scope in expected_scopes
        ]
        self.assertEqual({item.scope for item in matches}, expected_scopes)
        self.assertTrue(
            all(item.disposition == "legacy_mode_guarded" for item in matches)
        )

    def test_otp_and_membership_mutations_are_not_misreported_as_shared_message_work(self):
        otp_calls = [
            item
            for item in self.inventory
            if item.path in {
                "api/routers/auth.py",
                "core/services/telegram_otp_delivery_service.py",
            }
            and item.disposition == "durable_exempt"
        ]
        membership_calls = [
            item
            for item in self.inventory
            if item.disposition == "non_message_control"
        ]
        self.assertEqual(len(otp_calls), 2)
        self.assertEqual(len(membership_calls), 2)
        strict_legacy_relay_scopes = {
            (item.path, item.scope)
            for item in self.inventory
            if item.disposition == "durable_exempt"
            and item.path in {
                "api/routers/sync.py",
                "core/notifications.py",
            }
        }
        self.assertEqual(
            strict_legacy_relay_scopes,
            {
                ("api/routers/sync.py", "receive_sync_data"),
                ("core/notifications.py", "send_telegram_message"),
            },
        )

    def test_business_delivery_boundaries_are_all_owned_or_strictly_exempt(self):
        remaining = [
            item
            for item in self.inventory
            if item.disposition == "remaining_business_direct"
        ]
        self.assertEqual(remaining, [])

        guarded_scopes = {
            (item.path, item.scope)
            for item in self.inventory
            if item.disposition == "legacy_mode_guarded"
        }
        self.assertTrue(
            {
                ("api/routers/users.py", "send_block_notification"),
                ("api/routers/users.py", "send_limitation_notification"),
                (
                    "core/services/user_deletion_service.py",
                    "delete_user_account",
                ),
                (
                    "api/routers/sync.py",
                    "_run_synced_deleted_user_telegram_effects",
                ),
            }.issubset(guarded_scopes)
        )

    def test_callback_inventory_excludes_new_private_messages(self):
        callback_calls = [
            item
            for item in self.inventory
            if item.disposition == "remaining_callback_direct"
        ]
        interactive_calls = [
            item
            for item in self.inventory
            if item.disposition == "remaining_interactive_direct"
        ]

        self.assertEqual(len(callback_calls), 118)
        self.assertFalse(
            any(item.callee.endswith(".message.answer") for item in callback_calls)
        )
        self.assertTrue(
            any(item.callee == "callback.message.answer" for item in interactive_calls)
        )


if __name__ == "__main__":
    unittest.main()
