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
        await helper.test_bot_alias_add_start_sets_fsm_then_denies_non_admin()
        await helper.test_bot_alias_edit_start_sets_fsm_then_denies_non_admin()
        await helper.test_bot_alias_delete_start_sets_fsm_then_denies_non_admin()
        await helper.test_bot_commodity_edit_start_sets_fsm_then_denies_non_admin()
        await helper.test_bot_add_start_sets_fsm_then_denies_non_admin()
        await helper.test_bot_add_name_advances_fsm_then_denies_without_user()
        await helper.test_bot_delete_confirm_sets_fsm_then_denies_non_admin()
        await helper.test_bot_cancel_fsm_clears_then_denies_non_admin()
        await helper.test_start_invitation_creation_sets_fsm_then_denies_without_user()
        await helper.test_start_invitation_inline_sets_fsm_then_denies_without_user()
        await helper.test_process_invitation_account_name_advances_then_rejects_invalid()
        await helper.test_process_invitation_mobile_advances_then_rejects_invalid()
        await helper.test_cancel_invitation_creation_clears_then_denies_without_user()
        await helper.test_user_edit_role_prompts_then_denies_non_admin()
        await helper.test_user_delete_request_redirects_then_denies_non_admin()
        await helper.test_user_delete_confirm_redirects_then_denies_non_admin()
        await helper.test_limit_cancel_clears_fsm_then_denies_non_admin()
        await helper.test_user_block_settings_shows_then_denies_non_admin()
        await helper.test_admin_set_max_block_prompts_then_denies_non_admin()
        await helper.test_admin_max_block_custom_sets_fsm_then_denies_non_admin()
        await helper.test_create_market_message_persists_notifies_then_rejects_empty()
        await helper.test_clear_current_market_message_deactivates_then_noop_when_empty()
        await helper.test_start_telegram_admin_broadcast_opens_menu_then_denies_non_admin()
        await helper.test_cancel_telegram_admin_broadcast_clears_then_denies_non_admin()
        await helper.test_choose_all_recipients_asks_text_then_denies_non_admin()
        await helper.test_choose_group_recipients_opens_picker_then_denies_non_admin()
        await helper.test_finish_group_selection_asks_text_then_rejects_empty()
        await helper.test_choose_selected_recipients_asks_search_then_denies_non_admin()
        await helper.test_process_selected_recipient_search_stores_query_then_denies_non_admin()
        await helper.test_search_again_resets_query_state_then_denies_non_admin()
        await helper.test_finish_selected_recipients_asks_text_then_rejects_empty()
        await helper.test_process_broadcast_message_text_drafts_then_rejects_empty()
        await helper.test_user_limit_start_prompts_duration_then_stores_expiry()
        await helper.test_set_trades_sets_fsm_then_denies_non_admin()
        await helper.test_set_commodities_sets_fsm_then_denies_non_admin()
        await helper.test_set_requests_sets_fsm_then_denies_non_admin()
        await helper.test_process_limit_value_stores_then_rejects_invalid()
        await helper.test_start_search_user_sets_fsm_then_denies_non_admin()
        await helper.test_user_search_cancel_clears_then_denies_non_admin()
        await helper.test_process_search_query_finds_user_then_rejects_unknown()
        await helper.test_user_settings_shows_then_denies_non_admin()
        await helper.test_create_broadcast_persists_messages_then_rejects_empty()
        await helper.test_manage_commodities_lists_then_denies_without_user()
        await helper.test_back_to_list_clears_then_denies_non_admin()
        await helper.test_manage_aliases_lists_then_denies_non_admin()
        await helper.test_users_menu_stores_anchor_then_denies_non_admin()
        await helper.test_users_list_command_renders_then_denies_non_admin()
        await helper.test_users_pagination_renders_page_then_denies_non_admin()
        await helper.test_view_user_profile_edits_then_denies_non_admin()
        await helper.test_back_to_admin_clears_then_denies_non_admin()
        await helper.test_get_current_market_message_reads_row_then_none_when_empty()
        await helper.test_get_market_message_history_lists_then_empty()
        await helper.test_get_broadcast_history_lists_then_empty()
        await helper.test_read_commodity_returns_row_then_404s()
        await helper.test_read_all_commodities_lists_then_empty()
        await helper.test_get_settings_reads_persisted_quantity()
        await helper.test_get_market_state_returns_schedule_view()
        await helper.test_list_market_overrides_lists_then_empty()
        await helper.test_create_group_persists_then_fails_before_commit()
        await helper.test_patch_group_renames_then_404s()
        await helper.test_post_group_member_adds_then_404s()
        await helper.test_delete_group_member_removes_then_404s()
        await helper.test_promote_group_admin_promotes_then_404s()
        await helper.test_demote_group_admin_demotes_then_rejects_last_admin()
        await helper.test_post_group_leave_leaves_then_rejects_last_admin()
        await helper.test_pin_room_conversation_pins_then_404s()
        await helper.test_mute_room_conversation_mutes_then_404s()
        await helper.test_get_groups_lists_created_then_empty()
        await helper.test_get_group_detail_reads_then_404s()
        await helper.test_pin_direct_conversation_pins_then_404s()
        await helper.test_mute_direct_conversation_mutes_then_404s()
        await helper.test_delete_direct_conversation_hides_then_404s()
        await helper.test_reorder_room_pin_moves_then_404s()
        await helper.test_mark_room_unread_sets_flag_then_404s()
        await helper.test_create_channel_persists_then_404s_update_target()
        await helper.test_update_channel_renames_then_404s()
        await helper.test_bulk_invite_channel_members_adds_then_404s()
        await helper.test_patch_channel_member_promotes_then_404s()
        await helper.test_unfollow_channel_leaves_then_404s()
        await helper.test_send_room_message_persists_then_404s()
        await helper.test_mark_room_messages_read_clears_then_404s()
        await helper.test_toggle_message_reaction_adds_then_404s()
        await helper.test_toggle_message_pin_pins_then_404s()
        await helper.test_reorder_direct_pin_moves_then_404s()
        await helper.test_mark_direct_unread_sets_flag_then_404s()
        await helper.test_mark_messages_read_commits_then_404s()
        await helper.test_post_upload_batch_persists_then_rejects_unknown_target()
        await helper.test_post_upload_session_persists_then_404s()
        await helper.test_cancel_upload_session_cancels_then_404s()
        await helper.test_cancel_upload_batch_cancels_then_404s()
        await helper.test_get_group_member_candidates_lists_users()
        await helper.test_get_channels_lists_created_then_empty()
        await helper.test_get_channel_members_reads_then_404s()
        await helper.test_get_channel_invite_candidates_lists_users()
        await helper.test_get_upload_session_state_reads_then_404s()
        await helper.test_channel_trade_handlers_gateway_confirm_is_not_crud_evidence()

    def test_sibling_family_membership_does_not_clear_unexecuted_seeds(self):
        payload = _load_json("scenario-records.json")
        seeds = _all_seeds()
        self.assertEqual(len(seeds), 465)
        unsupported = [seed["behavior_id"] for seed in seeds if seed["behavior_id"] not in EXECUTED_SEED_IDS]
        self.assertEqual(len(EXECUTED_SEED_IDS), 127)
        self.assertEqual(len(unsupported), REMAINING_HIGH_SEEDS)
        self.assertEqual(REMAINING_HIGH_SEEDS, 338)
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
        self.assertEqual(len(set(msg_crud) & EXECUTED_SEED_IDS), 33)
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
        self.assertIn("338", item["reason"])
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
