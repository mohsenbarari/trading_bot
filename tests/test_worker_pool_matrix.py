from __future__ import annotations

import unittest

from scripts.report_worker_http_benchmark import percentile, summarize_results, RequestResult
from scripts.run_worker_pool_matrix import (
    parse_docker_stats_lines,
    recommend_worker_count,
    summarize_docker_stats,
    update_env_content,
)


class WorkerPoolMatrixTests(unittest.TestCase):
    def test_update_env_content_replaces_and_appends_keys(self) -> None:
        content = "API_WORKERS=8\nDB_POOL_SIZE=8\n# comment\n"
        updated = update_env_content(content, {"API_WORKERS": "12", "NEW_KEY": "value"})

        self.assertIn("API_WORKERS=12", updated)
        self.assertIn("DB_POOL_SIZE=8", updated)
        self.assertIn("# comment", updated)
        self.assertTrue(updated.endswith("NEW_KEY=value\n"))

    def test_docker_stats_summary_parses_cpu_and_memory(self) -> None:
        samples = parse_docker_stats_lines(
            '{"CPUPerc":"12.50%","MemUsage":"256MiB / 4GiB"}\n'
            '{"CPUPerc":"33.00%","MemUsage":"1.5GiB / 4GiB"}\n'
        )
        summary = summarize_docker_stats(samples)

        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["peak_cpu_percent"], 33.0)
        self.assertEqual(summary["peak_memory_bytes"], 1610612736)

    def test_recommendation_keeps_baseline_when_higher_workers_are_flat(self) -> None:
        reports = [
            self._candidate(8, p95=100, p99=160, rps=200, db_safe=True),
            self._candidate(12, p95=104, p99=162, rps=207, db_safe=True),
        ]

        recommendation = recommend_worker_count(reports)

        self.assertEqual(recommendation["recommended_workers"], 8)
        self.assertEqual(recommendation["decision"], "keep_baseline")

    def test_recommendation_accepts_higher_worker_when_gate_clears(self) -> None:
        reports = [
            self._candidate(8, p95=100, p99=160, rps=200, db_safe=True),
            self._candidate(12, p95=90, p99=150, rps=215, db_safe=True),
        ]

        recommendation = recommend_worker_count(reports)

        self.assertEqual(recommendation["recommended_workers"], 12)
        self.assertEqual(recommendation["decision"], "increase_workers")

    def test_http_summary_reports_percentiles_and_failures(self) -> None:
        self.assertEqual(percentile([10, 20, 30], 0.5), 20.0)
        summary = summarize_results(
            [
                RequestResult("/api/config", 200, 10.0, True),
                RequestResult("/api/config", 503, 20.0, False, "http_503"),
            ]
        )

        self.assertEqual(summary["total_requests"], 2)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["by_endpoint"]["/api/config"]["error_kinds"], ["http_503"])

    @staticmethod
    def _candidate(workers: int, *, p95: float, p99: float, rps: float, db_safe: bool) -> dict:
        return {
            "workers": workers,
            "ok": True,
            "benchmark": {
                "ok": True,
                "runtime": {
                    "estimated_within_limit": db_safe,
                    "peak_connections_within_limit": db_safe,
                },
                "workload": {
                    "requests_per_second": rps,
                    "summary": {
                        "p95_ms": p95,
                        "p99_ms": p99,
                    },
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
