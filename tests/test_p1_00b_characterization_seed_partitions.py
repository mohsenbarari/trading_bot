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
from tests.test_p1_00b_characterization_seed_slice import SLICE_EXECUTED_SEED_IDS

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
        await helper.test_channel_trade_handlers_deny_then_confirm_then_execute_injects()

    def test_sibling_family_membership_does_not_clear_unexecuted_seeds(self):
        payload = _load_json("scenario-records.json")
        seeds = _all_seeds()
        self.assertEqual(len(seeds), 465)
        unsupported = [seed["behavior_id"] for seed in seeds if seed["behavior_id"] not in EXECUTED_SEED_IDS]
        self.assertEqual(len(EXECUTED_SEED_IDS), 7)
        self.assertEqual(len(unsupported), REMAINING_HIGH_SEEDS)
        self.assertEqual(REMAINING_HIGH_SEEDS, 458)
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
        self.assertEqual(len(set(msg_crud) & EXECUTED_SEED_IDS), 1)
        self.assertEqual(len(set(trade_crud) & EXECUTED_SEED_IDS), 2)
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        item = registry["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"]
        self.assertEqual(item["disposition"], "BLOCKED_EXPLICIT")
        self.assertIn("458", item["reason"])
        self.assertIn("sibling", item["reason"].lower())
        for seed_id in sorted(EXECUTED_SEED_IDS):
            self.assertIn(seed_id, item["reason"])


if __name__ == "__main__":
    unittest.main()
