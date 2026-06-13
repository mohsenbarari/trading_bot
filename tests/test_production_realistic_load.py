import argparse
import unittest
from pathlib import Path

from scripts.report_production_realistic_load import (
    K6_SCRIPT,
    __file__ as REALISTIC_LOAD_SCRIPT,
    SCENARIO_CONTRACT,
    THRESHOLD_CONTRACT,
    build_contract,
    parse_duration_seconds,
    sampler_command,
)


class ProductionRealisticLoadTests(unittest.TestCase):
    def test_scenario_weights_sum_to_100(self):
        self.assertEqual(sum(item["weight"] for item in SCENARIO_CONTRACT), 100)

    def test_contract_uses_manifest_iran_url(self):
        args = argparse.Namespace(
            base_url=None,
            target_rps=500,
            duration="10m",
            load_profile="realistic-mixed",
            include_media=True,
            include_mutations=True,
            pre_allocated_vus=250,
            max_vus=2000,
        )
        contract = build_contract(args, {"IRAN_SERVER_URL": "https://iran.example"})
        self.assertEqual(contract["base_url"], "https://iran.example")
        self.assertEqual(contract["target_rps"], 500)
        self.assertTrue(contract["include_media"])
        self.assertTrue(contract["include_mutations"])

    def test_restricted_endpoints_are_not_in_contract(self):
        joined = "\n".join(endpoint for item in SCENARIO_CONTRACT for endpoint in item["endpoints"])
        self.assertNotIn("/api/sync/receive", joined)
        self.assertNotIn("/api/sync/resync", joined)
        self.assertNotIn("/api/trades/internal/execute", joined)
        self.assertNotIn("/api/auth/verify-otp", joined)

    def test_threshold_contract_keeps_official_gates(self):
        self.assertIn("http_req_failed", THRESHOLD_CONTRACT)
        self.assertIn("http_req_duration", THRESHOLD_CONTRACT)
        self.assertIn("stage_l_request_failed", THRESHOLD_CONTRACT)
        self.assertIn("rate<0.02", THRESHOLD_CONTRACT["http_req_failed"])
        self.assertIn("p(95)<1000", THRESHOLD_CONTRACT["http_req_duration"])

    def test_k6_script_uses_runtime_flags_and_auth_pool(self):
        source = Path(K6_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("AUTH_POOL_PATH", source)
        self.assertIn("INCLUDE_MEDIA", source)
        self.assertIn("INCLUDE_MUTATIONS", source)
        self.assertIn("constant-arrival-rate", source)
        self.assertIn("stage_l_endpoint_", source)
        self.assertIn("new Trend(`stage_l_endpoint_${endpoint}_duration`, true)", source)
        self.assertIn("new Rate(`stage_l_endpoint_${endpoint}_failed`)", source)

    def test_k6_project_users_uses_self_profile_to_avoid_expected_403_noise(self):
        source = Path(K6_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("`/users-public/${entry.user_id}/project-users?limit=30`", source)
        self.assertNotIn("ownerId}/project-users", source)

    def test_parse_duration_seconds_supports_k6_style_units(self):
        self.assertEqual(parse_duration_seconds("30s"), 30)
        self.assertEqual(parse_duration_seconds("2m"), 120)
        self.assertEqual(parse_duration_seconds("1h"), 3600)
        self.assertEqual(parse_duration_seconds("45"), 45)

    def test_sampler_command_targets_stage_l4_sampler(self):
        args = argparse.Namespace(
            manifest="./deploy/production/online.env",
            sampler_interval_seconds=5,
        )
        command = sampler_command(args, Path("tmp/sampler"), 90)
        self.assertIn("./scripts/report_production_load_sampler.py", command)
        self.assertIn("--duration-seconds", command)
        self.assertIn("90", command)
        self.assertIn("--sample-count", command)
        self.assertIn("0", command)
        self.assertIn("--roles", command)
        self.assertIn("both", command)

    def test_realistic_load_emits_done_marker_for_teed_logs(self):
        source = Path(REALISTIC_LOAD_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("STAGE_L_REALISTIC_DONE", source)


if __name__ == "__main__":
    unittest.main()
