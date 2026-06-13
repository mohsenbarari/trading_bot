import json
import tempfile
import unittest
from pathlib import Path

from scripts import report_read_path_endpoint_attribution as attribution


class ReadPathEndpointAttributionTests(unittest.TestCase):
    def test_endpoint_rows_from_k6_summary_include_median_and_request_counts(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "k6-summary.json"
            path.write_text(
                json.dumps(
                    {
                        "metrics": {
                            "stage_l_endpoint_chat_conversations_duration": {
                                "avg": 12,
                                "med": 7,
                                "p(90)": 20,
                                "p(95)": 25,
                                "p(99)": 40,
                                "max": 100,
                            },
                            "stage_l_endpoint_chat_conversations_failed": {
                                "value": 0.1,
                                "passes": 2,
                                "fails": 18,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            rows = attribution.endpoint_rows_from_k6_summary(path, limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].endpoint, "chat_conversations")
        self.assertEqual(rows[0].request_count, 20)
        self.assertEqual(rows[0].successful_requests, 18)
        self.assertEqual(rows[0].failed_requests, 2)
        self.assertEqual(rows[0].p50_ms, 7)
        self.assertEqual(rows[0].p95_ms, 25)

    def test_build_report_compares_against_first_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            artifact_a = self._write_matrix_artifact(
                Path(root) / "a",
                endpoint="chat_conversations",
                p95=100,
                p99=200,
            )
            artifact_b = self._write_matrix_artifact(
                Path(root) / "b",
                endpoint="chat_conversations",
                p95=150,
                p99=260,
            )

            report = attribution.build_report([artifact_a, artifact_b], limit=10)

        self.assertEqual(report["run_count"], 2)
        self.assertEqual(report["comparisons"][0]["endpoint"], "chat_conversations")
        self.assertEqual(report["comparisons"][0]["p95_delta_ms"], 50)
        self.assertEqual(report["comparisons"][0]["p99_delta_ms"], 60)

    def _write_matrix_artifact(self, artifact: Path, *, endpoint: str, p95: float, p99: float) -> Path:
        load_dir = artifact / "stamp" / "load-realistic"
        load_dir.mkdir(parents=True)
        (load_dir / "k6-summary.json").write_text(
            json.dumps(
                {
                    "metrics": {
                        f"stage_l_endpoint_{endpoint}_duration": {
                            "avg": p95 / 2,
                            "med": p95 / 3,
                            "p(90)": p95 - 1,
                            "p(95)": p95,
                            "p(99)": p99,
                            "max": p99 + 10,
                        },
                        f"stage_l_endpoint_{endpoint}_failed": {
                            "value": 0,
                            "passes": 0,
                            "fails": 10,
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        artifact.mkdir(exist_ok=True)
        (artifact / "results.json").write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate": "workers=24 pool=10:4",
                            "summary": {
                                "status": "passed",
                                "artifact_dir": str(load_dir),
                                "k6": {"p95_ms": p95, "p99_ms": p99, "rps": 500},
                                "sampler": {},
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return artifact


if __name__ == "__main__":
    unittest.main()
