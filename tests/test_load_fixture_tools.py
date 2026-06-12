import unittest

from scripts.load_fixture_worker import mobile_for, redact_auth_pool
from scripts.report_production_load_fixtures import build_scp_opts, build_ssh_opts, redact_payload


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


if __name__ == "__main__":
    unittest.main()
