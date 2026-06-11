import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import report_final_release_gate as gate


class FinalReleaseGateTests(unittest.TestCase):
    def test_messenger_summary_accepts_ready_surfaces_and_records_context_debt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            messenger_dir = root / "tmp" / "messenger-benchmark"
            messenger_dir.mkdir(parents=True)
            (messenger_dir / "comparison-summary.json").write_text(
                """
                {
                  "surfaceCount": 2,
                  "measuredSurfaceCount": 2,
                  "blockedSurfaceCount": 0,
                  "performanceDeltas": [
                    {"scenarioId": "S01", "listReadyDeltaMs": -10, "chatReadyDeltaMs": -20, "contextMenuDeltaMs": 50, "domNodeDelta": -5, "heapDeltaMb": -0.1},
                    {"scenarioId": "S02", "listReadyDeltaMs": -15, "chatReadyDeltaMs": -25, "contextMenuDeltaMs": 40, "domNodeDelta": -6, "heapDeltaMb": 0.2}
                  ]
                }
                """,
                encoding="utf-8",
            )
            (messenger_dir / "surface-status.json").write_text(
                """
                [
                  {"id": "M01", "release_gate_status": "ready", "blockers": [], "exact_missing_items": []},
                  {"id": "M02", "release_gate_status": "ready", "blockers": [], "exact_missing_items": []}
                ]
                """,
                encoding="utf-8",
            )
            (messenger_dir / "performance-results.json").write_text(
                '{"accessToken":"eyJabc.def.ghi"}',
                encoding="utf-8",
            )

            with mock.patch.object(gate, "REPO_ROOT", root):
                summary = gate.summarize_messenger()

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["measured_surface_count"], 2)
        warning_ids = {item["id"] for item in summary["warnings"]}
        self.assertIn("messenger_context_menu_latency_debt", warning_ids)
        self.assertIn("messenger_raw_performance_artifact_contains_synthetic_tokens", warning_ids)

    def test_score_penalizes_only_accepted_warnings_when_required_checks_pass(self) -> None:
        stage_rows = [{"status": "passed"} for _ in range(11)]
        checks = {
            "manifest": {"status": "passed"},
            "live_checks": {"status": "passed"},
            "messenger": {"status": "passed"},
            "observability": {"status": "passed"},
            "backup_rollback": {"status": "passed"},
        }

        score = gate.build_score(stage_rows, checks, [{"id": "w1"}, {"id": "w2"}])

        self.assertEqual(score["required_checks"], 16)
        self.assertEqual(score["passed_required_checks"], 16)
        self.assertEqual(score["required_pass_rate"], 100.0)
        self.assertEqual(score["warning_adjusted_score"], 96.0)

    def test_sync_health_parser_accepts_make_directory_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = root / "sync.stdout.log"
            stdout.write_text(
                """
make[1]: Entering directory '/repo'
{"status":"ok","unsynced_change_log_count":0,"redis_queues":{"sync:outbound":0,"sync:retry":0}}
make[1]: Leaving directory '/repo'
                """,
                encoding="utf-8",
            )
            with mock.patch.object(gate, "REPO_ROOT", root):
                parsed = gate.parse_sync_health_from_command({"stdout_path": "sync.stdout.log"})

        self.assertEqual(parsed["status"], "passed")


if __name__ == "__main__":
    unittest.main()
