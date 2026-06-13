import json
import tempfile
import unittest
from pathlib import Path

import scripts.run_stage_l_pool_matrix as pool_matrix
from scripts.run_stage_l_pool_matrix import (
    apply_pool_script,
    collect_app_logs_script,
    dropped_iteration_ratio,
    parse_candidates,
    parse_worker_candidates,
    recommend,
    summarize_candidate,
)


class StageLPoolMatrixTests(unittest.TestCase):
    def test_parse_candidates_deduplicates_pool_pairs(self):
        candidates = parse_candidates("8:6,12:8,12:8")
        self.assertEqual([(item.pool_size, item.max_overflow) for item in candidates], [(8, 6), (12, 8)])
        self.assertEqual(candidates[1].label, "p12-o8")
        self.assertEqual(candidates[1].total_per_worker, 20)

    def test_parse_worker_candidates_defaults_to_current_api_workers(self):
        self.assertEqual(parse_worker_candidates(None, 8), [8])
        self.assertEqual(parse_worker_candidates("8,12,12,16", 8), [8, 12, 16])

    def test_apply_pool_script_sets_worker_and_pool_env(self):
        candidate = parse_candidates("16:8")[0]
        script = apply_pool_script("/srv/trading-bot/current", "stamp", candidate, 12)
        self.assertIn('"API_WORKERS": "12"', script)
        self.assertIn('"DB_POOL_SIZE": "16"', script)
        self.assertIn('"DB_MAX_OVERFLOW": "8"', script)
        self.assertIn('DB_POOL_SIZE=%s\\nDB_MAX_OVERFLOW=%s\\nAPI_WORKERS=%s', script)

    def test_collect_app_logs_script_captures_before_recreate(self):
        script = collect_app_logs_script("/srv/trading-bot/current", tail_lines=123)
        self.assertIn("logs --no-color --tail 123 app", script)
        self.assertIn("docker logs --tail 123 trading_bot_app", script)

    def test_dropped_iteration_ratio_uses_attempted_iterations(self):
        self.assertEqual(dropped_iteration_ratio(http_reqs=1000, dropped_iterations=0), 0)
        self.assertAlmostEqual(dropped_iteration_ratio(http_reqs=980, dropped_iterations=20), 0.02)

    def test_summarize_candidate_reads_k6_and_sampler_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root) / "stamp-l7pool-p12-o8" / "load-realistic"
            sampler_dir = run_dir / "sampler"
            sampler_dir.mkdir(parents=True)
            (run_dir / "results.json").write_text(json.dumps({"status": "failed", "error": "threshold"}), encoding="utf-8")
            (run_dir / "k6-summary.json").write_text(
                json.dumps(
                    {
                        "metrics": {
                            "http_reqs": {"count": 1000, "rate": 499.5},
                            "http_req_failed": {"value": 0.001, "passes": 1},
                            "checks": {"value": 0.999},
                            "dropped_iterations": {"count": 0},
                            "http_req_duration": {"avg": 100, "p(95)": 800, "p(99)": 1400},
                            "stage_l_endpoint_chat_conversations_duration": {
                                "avg": 90,
                                "p(90)": 600,
                                "p(95)": 1200,
                                "p(99)": 1600,
                                "max": 2000,
                            },
                            "stage_l_endpoint_chat_conversations_failed": {"value": 0.02, "passes": 2, "fails": 98},
                            "stage_l_endpoint_offers_list_duration": {
                                "avg": 50,
                                "p(90)": 100,
                                "p(95)": 200,
                                "p(99)": 300,
                                "max": 400,
                            },
                            "stage_l_endpoint_offers_list_failed": {"value": 0, "passes": 0, "fails": 100},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (sampler_dir / "results.json").write_text(
                json.dumps(
                    {
                        "status": "finished",
                        "summary": {
                            "max_postgres_connections": 140,
                            "max_nginx_5xx_delta_from_first_sample": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = summarize_candidate(root, "stamp-l7pool-p12-o8")

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["k6"]["http_reqs"], 1000)
        self.assertEqual(summary["k6"]["p95_ms"], 800)
        self.assertEqual(summary["k6"]["dropped_iteration_ratio"], 0)
        self.assertEqual(summary["sampler"]["max_postgres_connections"], 140)
        self.assertEqual(summary["endpoint_breakdown"][0]["endpoint"], "chat_conversations")
        self.assertEqual(summary["endpoint_breakdown"][0]["p95_ms"], 1200)
        self.assertEqual(summary["endpoint_breakdown"][0]["failed_requests"], 2)

    def test_recommend_prefers_clean_lowest_latency_candidate(self):
        report = {
            "candidates": [
                {
                    "candidate": "8:6",
                    "summary": {
                        "k6": {"rps": 370, "failure_rate": 0.012, "p95_ms": 19000, "p99_ms": 30000, "dropped_iterations": 10},
                        "sampler": {"max_postgres_connections": 119, "max_nginx_5xx_delta_from_first_sample": 120},
                    },
                },
                {
                    "candidate": "12:8",
                    "summary": {
                        "status": "passed",
                        "k6": {"rps": 490, "failure_rate": 0.001, "p95_ms": 900, "p99_ms": 1700, "dropped_iterations": 0},
                        "sampler": {"max_postgres_connections": 150, "max_nginx_5xx_delta_from_first_sample": 0},
                    },
                },
            ]
        }
        self.assertIn("12:8", recommend(report))

    def test_recommend_accepts_bounded_dropped_iterations_when_latency_is_clean(self):
        report = {
            "target_rps": 500,
            "candidates": [
                {
                    "candidate": "workers=24 pool=10:4",
                    "summary": {
                        "status": "passed",
                        "k6": {
                            "http_reqs": 59214,
                            "rps": 491.68,
                            "failure_rate": 0,
                            "p95_ms": 713.79,
                            "p99_ms": 1628.41,
                            "dropped_iterations": 789,
                        },
                        "sampler": {"max_postgres_connections": 253, "max_nginx_5xx_delta_from_first_sample": 0},
                    },
                }
            ],
        }
        recommendation = recommend(report)
        self.assertIn("workers=24 pool=10:4", recommendation)
        self.assertIn("bounded dropped iterations", recommendation)

    def test_recommend_keeps_l7_blocked_when_dropped_ratio_exceeds_budget(self):
        report = {
            "target_rps": 500,
            "candidates": [
                {
                    "candidate": "workers=24 pool=10:4",
                    "summary": {
                        "status": "passed",
                        "k6": {
                            "http_reqs": 56000,
                            "rps": 491,
                            "failure_rate": 0,
                            "p95_ms": 700,
                            "p99_ms": 1600,
                            "dropped_iterations": 4000,
                        },
                        "sampler": {"max_postgres_connections": 253, "max_nginx_5xx_delta_from_first_sample": 0},
                    },
                }
            ],
        }
        recommendation = recommend(report)
        self.assertIn("Stage L7 remains blocked", recommendation)

    def test_recommend_keeps_l7_blocked_when_candidates_fail_official_gates(self):
        report = {
            "target_rps": 500,
            "candidates": [
                {
                    "candidate": "workers=16 pool=16:8",
                    "summary": {
                        "status": "failed",
                        "k6": {
                            "rps": 483,
                            "failure_rate": 0.07,
                            "p95_ms": 1172,
                            "p99_ms": 1632,
                            "dropped_iterations": 1695,
                        },
                        "sampler": {"max_postgres_connections": 351, "max_nginx_5xx_delta_from_first_sample": 1037},
                    },
                }
            ],
        }
        recommendation = recommend(report)
        self.assertIn("Stage L7 remains blocked", recommendation)
        self.assertIn("official L7 gates", recommendation)

    def test_pool_matrix_emits_done_marker_for_teed_logs(self):
        source = Path(pool_matrix.__file__).read_text(encoding="utf-8")
        self.assertIn("STAGE_L_POOL_MATRIX_DONE", source)


if __name__ == "__main__":
    unittest.main()
