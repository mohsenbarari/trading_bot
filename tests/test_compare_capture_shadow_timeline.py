"""Fail-closed coverage tests for capture shadow comparison."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from scripts.compare_capture_shadow_timeline import _run


class CaptureShadowComparisonTests(unittest.TestCase):
    def test_missing_candidate_book_is_a_coverage_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text(
                json.dumps(
                    {
                        "engine_version": "coin-rate-engine-test",
                        "points": [
                            {
                                "as_of_utc": "2026-08-24T10:00:00Z",
                                "rates": [
                                    {
                                        "commodity_code": "IMAM",
                                        "settlement_term": "CASH",
                                        "status": "ESTIMATED",
                                        "estimated_project_price": 190_000,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            candidate.write_text(
                json.dumps(
                    {
                        "engine_version": "coin-rate-engine-test",
                        "input": {"records_seen": 1, "records_rejected": 0},
                        "points": [
                            {
                                "as_of_utc": "2026-08-24T10:00:00Z",
                                "rates": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                status = _run(Namespace(baseline=str(baseline), candidate=str(candidate)))
            report = json.loads(output.getvalue())
            self.assertEqual(status, 3)
            self.assertEqual(report["candidate_coverage_losses"], 1)
            self.assertEqual(report["recommendation"], "KEEP_SHADOW")

    def test_live_timeline_accepts_contract_counters_from_causal_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            input_report = root / "input-report.json"
            rate = {
                "commodity_code": "IMAM",
                "settlement_term": "CASH",
                "status": "ESTIMATED",
                "estimated_project_price": 190_000,
                "underlying_age_seconds": 30,
            }
            baseline.write_text(
                json.dumps(
                    {
                        "engine_version": "coin-rate-engine-test",
                        "points": [{"as_of_utc": "2026-08-25T06:20:00Z", "rates": [rate]}],
                    }
                ),
                encoding="utf-8",
            )
            candidate.write_text(
                json.dumps(
                    {
                        "engine_version": "coin-rate-engine-test",
                        "points": [{"as_of_utc": "2026-08-25T06:20:00Z", "rates": [rate]}],
                    }
                ),
                encoding="utf-8",
            )
            input_report.write_text(
                json.dumps(
                    {
                        "schema": "capture_shadow_replay",
                        "adapter_version": "capture-adapter-test",
                        "input": {"records_seen": 100, "records_rejected": 1},
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                status = _run(
                    Namespace(
                        baseline=str(baseline),
                        candidate=str(candidate),
                        candidate_input_report=str(input_report),
                    )
                )
            report = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(report["candidate_input_evidence"], "capture_shadow_replay")
            self.assertEqual(report["candidate_adapter_version"], "capture-adapter-test")
            self.assertEqual(report["candidate_input_rejection_rate"], 0.01)
            self.assertEqual(report["recommendation"], "PROMOTE_CAPTURE_INPUT")


if __name__ == "__main__":
    unittest.main()
