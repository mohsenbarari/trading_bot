import unittest
import json
from pathlib import Path

from scripts.load_fixture_worker import (
    SEQUENCE_ALIGNMENT_TABLES,
    SEQUENCE_PREPARE_SAFETY_GAP,
    mobile_for,
    normalize_record_ids,
    redact_auth_pool,
    sync_queue_item_matches_cleanup,
)
from scripts.report_production_load_fixtures import Runner, build_scp_opts, build_ssh_opts, redact_payload, sync_worker_compose_body


class LoadFixtureToolsTests(unittest.TestCase):
    def test_mobile_for_is_deterministic_synthetic_iranian_mobile(self):
        first = mobile_for("loadtest_20260612T000000Z_", 7)
        self.assertEqual(first, mobile_for("loadtest_20260612T000000Z_", 7))
        self.assertNotEqual(first, mobile_for("loadtest_20260612T000000Z_", 8))
        self.assertTrue(first.startswith("09"))
        self.assertEqual(len(first), 11)
        self.assertTrue(first.isdigit())

    def test_worker_redacts_auth_pool_tokens(self):
        payload = {
            "personas": {
                "market_watchers": [
                    {"user_id": 1, "access_token": "secret-token", "account_name": "loadtest_user"}
                ]
            }
        }
        redacted = redact_auth_pool(payload)
        self.assertEqual(redacted["personas"]["market_watchers"][0]["access_token"], "[REDACTED]")
        self.assertEqual(payload["personas"]["market_watchers"][0]["access_token"], "secret-token")

    def test_report_redaction_handles_nested_sensitive_keys(self):
        payload = {
            "auth_pool": {
                "personas": {
                    "chat_texters": [
                        {"access_token": "raw-token", "authorization": "Bearer raw-token"}
                    ]
                }
            }
        }
        redacted = redact_payload(payload)
        entry = redacted["auth_pool"]["personas"]["chat_texters"][0]
        self.assertEqual(entry["access_token"], "[REDACTED]")
        self.assertEqual(entry["authorization"], "[REDACTED]")

    def test_jump_host_options_are_rendered_for_ssh_and_scp(self):
        ssh_opts = build_ssh_opts(host_port="2222", jump_host="root@iran.example", jump_port="2200")
        scp_opts = build_scp_opts(host_port="2222", jump_host="root@iran.example", jump_port="2200")
        self.assertIn("-J", ssh_opts)
        self.assertIn("root@iran.example:2200", ssh_opts)
        self.assertIn("ProxyJump=root@iran.example:2200", scp_opts)

    def test_sequence_alignment_covers_fixture_integer_tables(self):
        for table_name in ("users", "chats", "chat_members", "messages", "conversations", "offers", "trades"):
            self.assertIn(table_name, SEQUENCE_ALIGNMENT_TABLES)

    def test_prepare_sequence_alignment_uses_safety_gap(self):
        self.assertGreaterEqual(SEQUENCE_PREPARE_SAFETY_GAP, 1000)

    def test_sync_worker_pause_resume_commands_are_scoped(self):
        self.assertEqual(sync_worker_compose_body("pause"), "stop sync_worker")
        self.assertEqual(sync_worker_compose_body("resume"), "up -d --no-deps sync_worker")

    def test_prepare_worker_disables_direct_sync_push(self):
        runner = Runner(
            settings={"IRAN_HOST": "iran.example", "IRAN_PROJECT_DIR": "/srv/trading-bot/current"},
            logs_dir=Path("/tmp"),
        )
        args = runner.worker_args("iran", "prepare", "--prefix", "loadtest_case_")
        command = " ".join(args)

        self.assertIn("env TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH=1", command)
        self.assertIn("load_fixture_worker.py prepare", command)

    def test_cleanup_worker_keeps_direct_sync_push_default(self):
        runner = Runner(
            settings={"IRAN_HOST": "iran.example", "IRAN_PROJECT_DIR": "/srv/trading-bot/current"},
            logs_dir=Path("/tmp"),
        )
        args = runner.worker_args("iran", "cleanup", "--prefix", "loadtest_case_")

        self.assertNotIn("TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH=1", " ".join(args))

    def test_sync_queue_cleanup_matches_by_prefix_or_table_record_id(self):
        cleanup_ids = normalize_record_ids({"chat_members": [11545], "users": [10051]})

        self.assertTrue(sync_queue_item_matches_cleanup("payload loadtest_case_ value", "loadtest_case_", cleanup_ids))
        self.assertTrue(
            sync_queue_item_matches_cleanup(
                json.dumps({"table": "chat_members", "id": 11545}),
                "loadtest_case_",
                cleanup_ids,
            )
        )
        self.assertFalse(
            sync_queue_item_matches_cleanup(
                json.dumps({"table": "chat_members", "id": 1515}),
                "loadtest_case_",
                cleanup_ids,
            )
        )


if __name__ == "__main__":
    unittest.main()
