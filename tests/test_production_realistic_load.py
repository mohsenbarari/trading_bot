import argparse
import unittest
from pathlib import Path

from scripts.report_production_realistic_load import (
    K6_SCRIPT,
    SCENARIO_CONTRACT,
    THRESHOLD_CONTRACT,
    build_contract,
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


if __name__ == "__main__":
    unittest.main()
