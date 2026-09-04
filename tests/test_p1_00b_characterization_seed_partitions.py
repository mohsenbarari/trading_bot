"""P1-00b: only exact executed seed ids may leave the seed High blocker."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import json
import unittest
from pathlib import Path

from tests.test_p1_00_characterization_admin_toggle_behavior import (
    FakeSession,
    P100AdminToggleBehaviorTests,
    TOGGLE_SEEDS,
    _callback,
    _target,
)
from tests.test_p1_00b_characterization_seed_slice import (
    SEED_EVIDENCE_NODES,
    SLICE_EXECUTED_SEED_IDS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
EXECUTED_SEED_IDS = set(TOGGLE_SEEDS) | set(SLICE_EXECUTED_SEED_IDS)
REMAINING_HIGH_SEEDS = 465 - len(EXECUTED_SEED_IDS)


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _all_seeds() -> list[dict]:
    seeds = []
    for name, section in (
        ("surface-api.json", "api"),
        ("surface-bot.json", "bot"),
        ("surface-web.json", "web"),
        ("surface-jobs.json", "jobs"),
    ):
        for entry in _load_json(name)["entries"]:
            item = dict(entry)
            item["_section"] = section
            seeds.append(item)
    return seeds


class P100bSeedPartitionExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_toggle_seed_commits_then_denies_without_write(self):
        """Live handle_admin_toggle_block remains evidenced."""
        helper = P100AdminToggleBehaviorTests()
        await helper.test_toggle_block_second_invoke_flips_again_and_has_no_command_identity()
        await helper.test_toggle_block_denied_paths_do_not_write()
        self.assertTrue(_target().can_block_users)
        self.assertTrue(callable(_callback))
        self.assertTrue(callable(FakeSession))
        self.assertTrue(set(TOGGLE_SEEDS).issubset(EXECUTED_SEED_IDS))

    async def test_first_seed_slice_executes_exact_handlers(self):
        from tests.test_p1_00b_characterization_seed_slice import P100bSeedSliceExecutionTests

        helper = P100bSeedSliceExecutionTests()
        await helper.test_account_status_seed_commits_then_denies_without_write()
        await helper.test_broadcast_toggle_group_recipient_flips_fsm_and_denies_injects()
        await helper.test_broadcast_toggle_selected_user_flips_fsm_and_denies_injects()
        await helper.test_send_message_persists_publishes_retries_and_fails_before_commit_injects()
        await helper.test_set_user_role_persists_then_denies_and_fails_before_commit_injects()
        await helper.test_user_unblock_clears_restriction_then_denies_without_write_injects()
        await helper.test_confirm_telegram_admin_broadcast_persists_then_replays_key_injects()
        await helper.test_user_unlimit_clears_caps_then_denies_without_write_injects()
        await helper.test_admin_max_block_set_persists_then_denies_injects()
        await helper.test_user_block_actions_persists_restriction_then_denies_injects()
        await helper.test_limit_confirm_persists_caps_then_denies_injects()
        await helper.test_update_message_edits_then_denies_and_fails_injects()
        await helper.test_delete_message_soft_deletes_then_denies_injects()
        await helper.test_process_custom_max_block_persists_then_denies_injects()
        await helper.test_create_commodity_persists_then_rejects_digits_and_conflict()
        await helper.test_update_commodity_name_persists_then_404s()
        await helper.test_delete_commodity_removes_then_404s_and_locks_imam()
        await helper.test_add_alias_to_commodity_persists_then_conflicts()
        await helper.test_update_alias_persists_then_404s()
        await helper.test_delete_alias_removes_then_404s()
        await helper.test_create_market_override_persists_then_rejects_duplicate()
        await helper.test_update_market_override_persists_then_404s()
        await helper.test_delete_market_override_removes_then_404s()
        await helper.test_bot_alias_add_name_persists_then_denies_without_user()
        await helper.test_bot_alias_edit_name_persists_then_denies_without_user()
        await helper.test_bot_alias_delete_yes_persists_then_denies_non_admin()
        await helper.test_bot_commodity_edit_name_persists_then_denies_without_user()
        await helper.test_bot_add_aliases_and_create_persists_then_denies_without_user()
        await helper.test_bot_delete_yes_removes_then_denies_non_admin()
        await helper.test_update_settings_persists_then_rejects_invalid_hours()
        await helper.test_reset_settings_restores_defaults_after_update()
        await helper.test_channel_trade_handlers_gateway_confirm_is_not_crud_evidence()

    def test_sibling_family_membership_does_not_clear_unexecuted_seeds(self):
        payload = _load_json("scenario-records.json")
        seeds = _all_seeds()
        self.assertEqual(len(seeds), 465)
        unsupported = [seed["behavior_id"] for seed in seeds if seed["behavior_id"] not in EXECUTED_SEED_IDS]
        self.assertEqual(len(EXECUTED_SEED_IDS), 32)
        self.assertEqual(len(unsupported), REMAINING_HIGH_SEEDS)
        self.assertEqual(REMAINING_HIGH_SEEDS, 433)
        msg_crud = [
            seed["behavior_id"]
            for seed in seeds
            if seed["family"] == "MSG" and seed["operation_kind"] == "CRUD_MUTATION"
        ]
        trade_crud = [
            seed["behavior_id"]
            for seed in seeds
            if seed["family"] == "TRADE" and seed["operation_kind"] == "CRUD_MUTATION"
        ]
        self.assertEqual(len(msg_crud), 37)
        self.assertEqual(len(trade_crud), 26)
        self.assertTrue((set(msg_crud) - EXECUTED_SEED_IDS).issubset(set(unsupported)))
        self.assertTrue((set(trade_crud) - EXECUTED_SEED_IDS).issubset(set(unsupported)))
        self.assertEqual(len(set(msg_crud) & EXECUTED_SEED_IDS), 3)
        self.assertEqual(len(set(trade_crud) & EXECUTED_SEED_IDS), 0)
        self.assertIn(
            "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-L810",
            unsupported,
        )
        self.assertIn(
            "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829",
            unsupported,
        )
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        item = registry["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"]
        self.assertEqual(item["disposition"], "BLOCKED_EXPLICIT")
        self.assertIn("433", item["reason"])
        self.assertIn("sibling", item["reason"].lower())
        for seed_id in sorted(EXECUTED_SEED_IDS):
            self.assertIn(seed_id, item["reason"])

    def test_executed_seeds_have_exact_inventory_test_nodes(self):
        seeds = {seed["behavior_id"]: seed for seed in _all_seeds()}
        self.assertEqual(set(EXECUTED_SEED_IDS), set(SEED_EVIDENCE_NODES))
        for seed_id, node in SEED_EVIDENCE_NODES.items():
            refs = seeds[seed_id]["test_refs"]
            self.assertIn(node, refs, seed_id)
            path = node.split("::", 1)[0]
            self.assertTrue((REPO_ROOT / path).exists(), node)


if __name__ == "__main__":
    unittest.main()
