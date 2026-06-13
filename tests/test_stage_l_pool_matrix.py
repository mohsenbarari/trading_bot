import json
import tempfile
import unittest
from pathlib import Path

import scripts.run_stage_l_pool_matrix as pool_matrix
from scripts.run_stage_l_pool_matrix import parse_candidates, recommend, summarize_candidate


class StageLPoolMatrixTests(unittest.TestCase):
    def test_parse_candidates_deduplicates_pool_pairs(self):
        candidates = parse_candidates("8:6,12:8,12:8")
        self.assertEqual([(item.pool_size, item.max_overflow) for item in candidates], [(8, 6), (12, 8)])
        self.assertEqual(candidates[1].label, "p12-o8")
        self.assertEqual(candidates[1].total_per_worker, 20)

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
                        "k6": {"rps": 490, "failure_rate": 0.001, "p95_ms": 900, "p99_ms": 1700, "dropped_iterations": 0},
                        "sampler": {"max_postgres_connections": 150, "max_nginx_5xx_delta_from_first_sample": 0},
                    },
                },
            ]
        }
        self.assertIn("12:8", recommend(report))

    def test_pool_matrix_emits_done_marker_for_teed_logs(self):
        source = Path(pool_matrix.__file__).read_text(encoding="utf-8")
        self.assertIn("STAGE_L_POOL_MATRIX_DONE", source)


if __name__ == "__main__":
    unittest.main()
