import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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

    def test_repeat_offer_direct_fallbacks_are_owner_guarded(self):
        expected_scopes = {
            "_send_repeat_offer_menu_refresh",
            "handle_repeat_offer_button",
        }
        matches = [
            item
            for item in self.inventory
            if item.path == "bot/handlers/trade_create.py"
            and item.scope in expected_scopes
            and item.callee in {"bot.send_message", "message.answer"}
        ]
        self.assertEqual({item.scope for item in matches}, expected_scopes)
        self.assertTrue(
            all(item.disposition == "legacy_owner_guarded" for item in matches)
        )

    def test_block_family_edits_use_the_queue_adapter_only(self):
        remaining_block_calls = [
            item
            for item in self.inventory
            if item.path == "bot/handlers/block_manage.py"
            and item.disposition.startswith("remaining_")
        ]
        self.assertEqual(remaining_block_calls, [])

        adapter_calls = [
            item
            for item in self.inventory
            if item.path == "bot/telegram_interaction_message.py"
            and item.scope == "edit_callback_message_via_runtime"
            and item.callee == "message.edit_text"
        ]
        self.assertEqual(len(adapter_calls), 1)
        self.assertEqual(adapter_calls[0].disposition, "legacy_mode_guarded")

    def test_admin_broadcast_text_edits_use_queue_adapter(self):
        remaining_admin_broadcast_calls = [
            item
            for item in self.inventory
            if item.path == "bot/handlers/admin_broadcast.py"
            and item.disposition.startswith("remaining_")
        ]
        self.assertEqual(len(remaining_admin_broadcast_calls), 1)
        self.assertEqual(
            remaining_admin_broadcast_calls[0].scope,
            "toggle_group_recipient",
        )
        self.assertEqual(
            remaining_admin_broadcast_calls[0].callee,
            "callback.message.edit_reply_markup",
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

        self.assertEqual(len(callback_calls), 0)
        self.assertFalse(
            any(item.callee.endswith(".message.answer") for item in callback_calls)
        )
        self.assertTrue(
            any(item.callee == "callback.message.answer" for item in interactive_calls)
        )

    def test_runtime_tokens_elsewhere_do_not_hide_an_unguarded_call(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bot").mkdir()
            (root / "bot" / "sample.py").write_text(
                """
async def unsafe(message):
    await message.answer('unguarded')
    if configured_telegram_delivery_runtime().mode == TelegramDeliveryRuntimeMode.QUEUE_V1:
        return
""",
                encoding="utf-8",
            )

            inventory = build_inventory(root)

        self.assertEqual(len(inventory), 1)
        self.assertEqual(
            inventory[0].disposition,
            "remaining_interactive_direct",
        )

    def test_only_a_dominating_queue_exit_marks_legacy_call_guarded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bot").mkdir()
            (root / "bot" / "sample.py").write_text(
                """
async def safe(message):
    queue_mode = configured_telegram_delivery_runtime().mode == TelegramDeliveryRuntimeMode.QUEUE_V1
    if queue_mode:
        return
    await message.answer('legacy')
""",
                encoding="utf-8",
            )

            inventory = build_inventory(root)

        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0].disposition, "legacy_mode_guarded")
        self.assertIn("queue branch exits", inventory[0].evidence)

    def test_answer_document_and_detached_enqueue_are_in_inventory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bot" / "utils").mkdir(parents=True)
            (root / "bot" / "sample.py").write_text(
                """
async def export(message):
    await message.answer_document('report')
""",
                encoding="utf-8",
            )
            (root / "bot" / "utils" / "trade_suggestion_messages.py").write_text(
                """
async def schedule():
    async def _enqueue():
        return None
    asyncio.create_task(_enqueue())
""",
                encoding="utf-8",
            )

            inventory = build_inventory(root)

        dispositions = {(item.callee, item.disposition) for item in inventory}
        self.assertIn(
            ("message.answer_document", "remaining_interactive_direct"),
            dispositions,
        )
        self.assertIn(
            ("asyncio.create_task(_enqueue())", "remaining_memory_timer"),
            dispositions,
        )


if __name__ == "__main__":
    unittest.main()
