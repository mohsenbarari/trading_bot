import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts.report_production_realistic_load import (
    K6_SCRIPT,
    __file__ as REALISTIC_LOAD_SCRIPT,
    SCENARIO_CONTRACT,
    THRESHOLD_CONTRACT,
    build_contract,
    merge_k6_summaries,
    parse_duration_seconds,
    run_k6_command,
    sampler_command,
    shard_vus_plan,
    split_rps,
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
            load_runner_nofile=65535,
            load_runner_shards=1,
        )
        contract = build_contract(args, {"IRAN_SERVER_URL": "https://iran.example"})
        self.assertEqual(contract["base_url"], "https://iran.example")
        self.assertEqual(contract["target_rps"], 500)
        self.assertTrue(contract["include_media"])
        self.assertTrue(contract["include_mutations"])
        self.assertEqual(contract["load_runner_nofile"], 65535)
        self.assertEqual(contract["load_runner_shards"], 1)

    def test_split_rps_distributes_remainder_to_first_shards(self):
        self.assertEqual(split_rps(500, 2), [250, 250])
        self.assertEqual(split_rps(500, 3), [167, 167, 166])

    def test_shard_vus_plan_keeps_single_process_values_and_sizes_shards(self):
        self.assertEqual(
            shard_vus_plan(total_pre_allocated_vus=250, total_max_vus=2000, rps_by_shard=[500]),
            [{"pre_allocated_vus": 250, "max_vus": 2000}],
        )
        self.assertEqual(
            shard_vus_plan(total_pre_allocated_vus=250, total_max_vus=2000, rps_by_shard=[250, 250]),
            [
                {"pre_allocated_vus": 250, "max_vus": 1000},
                {"pre_allocated_vus": 250, "max_vus": 1000},
            ],
        )
        self.assertEqual(
            shard_vus_plan(total_pre_allocated_vus=250, total_max_vus=2000, rps_by_shard=[125, 125, 125, 125]),
            [
                {"pre_allocated_vus": 125, "max_vus": 500},
                {"pre_allocated_vus": 125, "max_vus": 500},
                {"pre_allocated_vus": 125, "max_vus": 500},
                {"pre_allocated_vus": 125, "max_vus": 500},
            ],
        )

    def test_k6_command_raises_load_runner_nofile_limit(self):
        args = argparse.Namespace(
            target_rps=500,
            duration="2m",
            load_profile="target",
            include_media=False,
            include_mutations=False,
            pre_allocated_vus=250,
            max_vus=2000,
            load_runner_nofile=65535,
            load_runner_shards=1,
        )
        contract = {"base_url": "https://iran.example"}
        command = run_k6_command(
            args,
            contract,
            {
                "artifact_dir": "/srv/load/artifacts/run",
                "script_dir": "/srv/load/scripts",
                "root": "/srv/load",
                "auth_pool": "/srv/load/auth/pool.json",
                "summary": "/srv/load/artifacts/run/k6-summary.json",
                "script": "/srv/load/scripts/k6_realistic_mix.js",
            },
        )
        self.assertIn("ulimit -n 65535 &&", command)
        self.assertIn("PRE_ALLOCATED_VUS=250", command)
        self.assertIn("MAX_VUS=2000", command)

    def test_k6_command_supports_parallel_load_runner_shards(self):
        args = argparse.Namespace(
            target_rps=500,
            duration="2m",
            load_profile="target",
            include_media=False,
            include_mutations=False,
            pre_allocated_vus=250,
            max_vus=2000,
            load_runner_nofile=65535,
            load_runner_shards=2,
        )
        command = run_k6_command(
            args,
            {"base_url": "https://iran.example"},
            {
                "artifact_dir": "/srv/load/artifacts/run",
                "script_dir": "/srv/load/scripts",
                "root": "/srv/load",
                "auth_pool": "/srv/load/auth/pool.json",
                "summary": "/srv/load/artifacts/run/k6-summary.json",
                "script": "/srv/load/scripts/k6_realistic_mix.js",
            },
        )
        self.assertIn("starting shard 1/2 at 250 RPS with 250/1000 VUs", command)
        self.assertIn("starting shard 2/2 at 250 RPS with 250/1000 VUs", command)
        self.assertIn("K6_SHARD_INDEX=1", command)
        self.assertIn("K6_SHARD_COUNT=2", command)
        self.assertIn("k6-summary-shard-1.json", command)
        self.assertIn("k6-summary-shard-2.json", command)
        self.assertIn('for pid in $pids', command)
        self.assertIn('exit "$rc"', command)
        self.assertNotIn("&;", command)

    def test_merge_k6_summaries_sums_counts_and_uses_conservative_percentiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_1 = root / "k6-summary-shard-1.json"
            shard_2 = root / "k6-summary-shard-2.json"
            output = root / "k6-summary.json"
            shard_1.write_text(
                json.dumps(
                    {
                        "root_group": {"name": ""},
                        "metrics": {
                            "http_reqs": {"count": 100, "rate": 50},
                            "http_req_failed": {"passes": 1, "fails": 99, "value": 0.01},
                            "http_req_duration": {"avg": 100, "min": 1, "med": 50, "p(90)": 150, "p(95)": 200, "p(99)": 300, "max": 400},
                            "dropped_iterations": {"count": 2, "rate": 1},
                            "stage_l_endpoint_chat_poll_duration": {"avg": 80, "min": 1, "p(95)": 180, "p(99)": 250, "max": 300},
                            "stage_l_endpoint_chat_poll_failed": {"passes": 0, "fails": 10, "value": 0},
                        },
                    }
                ),
                encoding="utf-8",
            )
            shard_2.write_text(
                json.dumps(
                    {
                        "metrics": {
                            "http_reqs": {"count": 200, "rate": 100},
                            "http_req_failed": {"passes": 0, "fails": 200, "value": 0},
                            "http_req_duration": {"avg": 200, "min": 2, "med": 60, "p(90)": 250, "p(95)": 500, "p(99)": 700, "max": 900},
                            "dropped_iterations": {"count": 3, "rate": 1.5},
                            "stage_l_endpoint_chat_poll_duration": {"avg": 120, "min": 2, "p(95)": 220, "p(99)": 350, "max": 500},
                            "stage_l_endpoint_chat_poll_failed": {"passes": 1, "fails": 19, "value": 0.05},
                        },
                    }
                ),
                encoding="utf-8",
            )

            merged = merge_k6_summaries([shard_1, shard_2], output)
            self.assertTrue(output.exists())

        metrics = merged["metrics"]
        self.assertEqual(metrics["http_reqs"]["count"], 300)
        self.assertEqual(metrics["http_reqs"]["rate"], 150)
        self.assertEqual(metrics["dropped_iterations"]["count"], 5)
        self.assertAlmostEqual(metrics["http_req_failed"]["value"], 1 / 300)
        self.assertAlmostEqual(metrics["http_req_duration"]["avg"], (100 * 100 + 200 * 200) / 300)
        self.assertEqual(metrics["http_req_duration"]["p(95)"], 500)
        self.assertEqual(metrics["stage_l_endpoint_chat_poll_duration"]["p(99)"], 350)

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
        self.assertIn("K6_SHARD_INDEX", source)
        self.assertIn("K6_SHARD_COUNT", source)
        self.assertIn("k6_offer_s${K6_SHARD_INDEX}_", source)

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
