import unittest

from pathlib import Path

from core.telegram_delivery_cutover_contract import (
    api_env_updates,
    api_process_contract,
    bot_env_updates,
    bot_process_contract,
    executor_count,
    executor_overlap_forbidden,
    expected_channel_id_updates,
    missing_required_env,
    present_forbidden_tokens,
    upsert_env_lines,
)
from scripts.cutover_telegram_delivery_queue_staging import (
    StagingCutoverError,
    _assert_quiesced_snapshot,
    _require_clean_pushed_main,
    apply_cutover,
)


class TelegramDeliveryCutoverContractTests(unittest.TestCase):
    def test_api_contract_rejects_queue_worker_and_tokens(self):
        contract = api_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                },
                contract,
            ),
            (
                "TELEGRAM_DELIVERY_EXECUTION_OWNER",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
                "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
            ),
        )
        forbidden = contract.forbidden_token_keys[5]
        self.assertEqual(
            present_forbidden_tokens({forbidden: True}, contract),
            (forbidden,),
        )

    def test_bot_contract_requires_queue_owner_and_five_lane_parent_flags(self):
        contract = bot_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
                    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
                },
                contract,
            ),
            (),
        )
        self.assertFalse(contract.forbidden_token_keys)

    def test_legacy_and_queue_workers_may_not_overlap(self):
        self.assertTrue(
            executor_overlap_forbidden(
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            )
        )
        self.assertFalse(
            executor_overlap_forbidden(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            )
        )

    def test_upsert_env_lines_replaces_and_appends_without_touching_other_keys(self):
        updated = upsert_env_lines(
            "KEEP=1\nTELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n",
            {
                "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
            },
        )
        self.assertIn("KEEP=1\n", updated)
        self.assertIn("TELEGRAM_DELIVERY_PRODUCER_MODE=queue-v1\n", updated)
        self.assertIn("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED=true\n", updated)
        self.assertNotIn("legacy", updated)

    def test_expected_channel_id_is_copied_only_when_absent_and_matching(self):
        copied = expected_channel_id_updates("CHANNEL_ID=-100111\n")
        self.assertEqual(
            copied,
            {"TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100111"},
        )
        self.assertEqual(
            expected_channel_id_updates(
                "CHANNEL_ID=-100111\nTELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID=-100111\n"
            ),
            {},
        )
        with self.assertRaises(ValueError):
            expected_channel_id_updates(
                "CHANNEL_ID=-100111\nTELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID=-100222\n"
            )
        with self.assertRaises(ValueError):
            expected_channel_id_updates("TELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n")

    def test_process_role_env_updates_keep_tokens_off_api(self):
        api = api_env_updates()
        bot = bot_env_updates()
        forbidden = api_process_contract().forbidden_token_keys
        self.assertEqual(api["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "legacy")
        self.assertTrue(all(api[key] == "" for key in forbidden))
        self.assertEqual(bot["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "queue-v1")
        self.assertTrue(all(key not in bot for key in forbidden))

    def test_executor_count_is_zero_one_or_overlap_two(self):
        self.assertEqual(
            executor_count(
                bot_running=False,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
            0,
        )
        self.assertEqual(
            executor_count(
                bot_running=True,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
            1,
        )
        self.assertEqual(
            executor_count(
                bot_running=True,
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            ),
            2,
        )

    def test_open_delivery_residue_blocks_cutover(self):
        _assert_quiesced_snapshot(
            {
                "jobs_pending": 0,
                "jobs_leased": 0,
                "jobs_ambiguous": 0,
                "pending_outcomes": 0,
                "active_resume": 0,
                "active_gates": 0,
                "dispatch_open": 0,
                "outbox_open": 0,
            }
        )
        with self.assertRaises(StagingCutoverError):
            _assert_quiesced_snapshot({"jobs_pending": 1, "outbox_open": 0})

    def test_apply_rejects_wrong_confirmation_before_any_mutation(self):
        with self.assertRaises(StagingCutoverError) as ctx:
            apply_cutover(Path("/tmp/telegram-queue-cutover-staging"), confirm="no")
        self.assertEqual(str(ctx.exception), "cutover_confirmation_mismatch")

    def test_clean_pushed_main_is_required(self):
        with self.assertRaises(StagingCutoverError):
            _require_clean_pushed_main(
                {
                    "branch": "main",
                    "worktree": "dirty",
                    "head": "aaa",
                    "origin_main": "aaa",
                }
            )
        with self.assertRaises(StagingCutoverError):
            _require_clean_pushed_main(
                {
                    "branch": "main",
                    "worktree": "clean",
                    "head": "aaa",
                    "origin_main": "bbb",
                }
            )
        _require_clean_pushed_main(
            {
                "branch": "main",
                "worktree": "clean",
                "head": "aaa",
                "origin_main": "aaa",
            }
        )

    def test_staging_compose_isolates_api_tokens_from_shared_env(self):
        compose = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "staging"
            / "docker-compose.staging.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("x-api-telegram-isolation", compose)
        self.assertIn("<<: *api_telegram_isolation", compose)
        self.assertGreaterEqual(compose.count("<<: *api_telegram_isolation"), 5)


if __name__ == "__main__":
    unittest.main()
