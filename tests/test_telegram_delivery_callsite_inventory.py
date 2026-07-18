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

    def test_known_business_gaps_remain_visible_until_their_source_contracts_exist(self):
        remaining_scopes = {
            (item.path, item.scope)
            for item in self.inventory
            if item.disposition == "remaining_business_direct"
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
            }.issubset(remaining_scopes)
        )


if __name__ == "__main__":
    unittest.main()
