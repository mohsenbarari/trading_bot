"""Tests for provider-metrics session_gate aggregation."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

try:
    from scripts.two_site_provider_metrics import provider_metrics, unavailable
except ModuleNotFoundError:
    from two_site_provider_metrics import provider_metrics, unavailable

HEAD = "a" * 40
TASK_ID = "task-session-gate"
GAP_REF = "docs/refactor/two-server/PROPOSAL_AUTOMATION.md#live-session-context-gate"

UNKNOWN_GATE = {
    "state": "UNKNOWN",
    "activation_reason": None,
    "root_count": None,
    "required_symbol_count": None,
    "query_operation_count": None,
}

UNAVAILABLE_GATE = {
    "state": "UNAVAILABLE",
    "activation_reason": None,
    "root_count": None,
    "required_symbol_count": None,
    "query_operation_count": None,
}


def _make_task_snap(task_id: str = TASK_ID, head: str = HEAD) -> dict:
    return {
        "source_head": head,
        "task": {
            "task_id": task_id,
            "requirements": ["req-1", "req-2"],
            "test_selectors": ["tests/test_two_site_session_gate_metrics.py"],
            "goal": f"Task goal CANONICAL_SOURCE_EXCERPTS_JSON: {json.dumps({'gap_reference': GAP_REF})}",
        },
    }


def _write_proposal(
    root: Path,
    run_id: str,
    status: dict,
    task_snap: dict | None = None,
) -> Path:
    folder = root / ".local" / "two-site-refactor" / "proposals" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if task_snap is not None:
        (folder / "task.json").write_text(json.dumps(task_snap), encoding="utf-8")
    return folder


def _make_worker_status(
    run_id: str,
    gate: object = None,
    include_gate: bool = True,
    task_id: str = TASK_ID,
    head: str = HEAD,
    controller_run_id: str | None = None,
) -> dict:
    s = {
        "task_id": task_id,
        "head": head,
        "run_id": run_id,
        "role": "worker",
        "state": "PROPOSAL_READY",
        "duration_seconds": 12.0,
        "usage": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
    }
    if controller_run_id is not None:
        s["controller_run_id"] = controller_run_id
    if include_gate:
        s["session_gate"] = gate
    return s


def _make_reviewer_status(
    run_id: str,
    worker_run_id: str,
    gate: object = None,
    include_gate: bool = True,
    task_id: str = TASK_ID,
    head: str = HEAD,
    controller_run_id: str | None = None,
) -> dict:
    s = {
        "task_id": task_id,
        "head": head,
        "run_id": run_id,
        "role": "verifier",
        "state": "VERIFIED",
        "input_worker_run_id": worker_run_id,
        "duration_seconds": 8.0,
        "usage": {"input_tokens": 400, "output_tokens": 200, "total_tokens": 600},
    }
    if controller_run_id is not None:
        s["controller_run_id"] = controller_run_id
    if include_gate:
        s["session_gate"] = gate
    return s


class TestSessionGateMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _assert_common_baseline(self, result: dict, expected_state: str = "OBSERVED_MATCHING_ATTEMPTS") -> None:
        self.assertEqual(result["state"], expected_state)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["worker_attempts"], 1)
        self.assertEqual(result["reviewer_attempts"], 1)
        self.assertEqual(result["failed_attempts"], 0)
        self.assertEqual(result["successful_correction_rounds"], 0)
        self.assertIs(result["first_pass"], True)
        self.assertEqual(result["usage"], {"input_tokens": 1400, "output_tokens": 700, "total_tokens": 2100})
        self.assertEqual(result["observed_usage_subtotal"], {"input_tokens": 1400, "output_tokens": 700, "total_tokens": 2100})
        self.assertEqual(result["usage_observed_attempts"], 2)
        self.assertEqual(result["timing_coverage"], "MATCHING_PROVIDER_ATTEMPTS_ONLY")
        self.assertEqual(
            result["phase_seconds"],
            {"provider_reported": 20.0, "outside_provider_unattributed": 10.0},
        )
        self.assertIsNotNone(result["task_metadata"])
        self.assertEqual(result["task_metadata"]["task_id"], TASK_ID)
        self.assertEqual(result["task_metadata"]["gap_reference"], GAP_REF)

    def test_unavailable_schema(self) -> None:
        direct = unavailable()
        self.assertEqual(direct["session_gate"], UNAVAILABLE_GATE)
        self.assertEqual(direct["state"], "UNAVAILABLE")
        self.assertIsNone(direct["first_pass"])

        from_bad_head = provider_metrics(self.root, task_id=TASK_ID, head_before="invalid", final_worker_id="p1-worker-0")
        self.assertEqual(from_bad_head["session_gate"], UNAVAILABLE_GATE)
        self.assertEqual(from_bad_head["state"], "UNAVAILABLE")

        from_missing_dir = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0")
        self.assertEqual(from_missing_dir["session_gate"], UNAVAILABLE_GATE)
        self.assertEqual(from_missing_dir["state"], "UNAVAILABLE")

    def test_identical_explicit(self) -> None:
        gate = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "EXPLICIT_DECLARATION",
            "root_count": 2,
            "required_symbol_count": 12,
            "query_operation_count": 6,
        }
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=gate), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", gate=gate), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], gate)

    def test_identical_legacy(self) -> None:
        gate = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "LEGACY_HEURISTIC",
            "root_count": 1,
            "required_symbol_count": 5,
            "query_operation_count": 3,
        }
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=gate), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", gate=gate), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], gate)

    def test_identical_not_requested(self) -> None:
        gate = {
            "state": "NOT_REQUESTED",
            "activation_reason": "NOT_REQUESTED",
            "root_count": 0,
            "required_symbol_count": 0,
            "query_operation_count": 0,
        }
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=gate), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", gate=gate), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], gate)

    def test_historical_absence(self) -> None:
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", include_gate=False), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", include_gate=False), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], UNKNOWN_GATE)

    def test_mixed_presence(self) -> None:
        gate = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "EXPLICIT_DECLARATION",
            "root_count": 2,
            "required_symbol_count": 10,
            "query_operation_count": 4,
        }
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=gate, include_gate=True), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", include_gate=False), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], UNKNOWN_GATE)

    def test_conflicting_valid_attempts(self) -> None:
        gate1 = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "EXPLICIT_DECLARATION",
            "root_count": 2,
            "required_symbol_count": 10,
            "query_operation_count": 4,
        }
        gate2 = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "LEGACY_HEURISTIC",
            "root_count": 2,
            "required_symbol_count": 10,
            "query_operation_count": 4,
        }
        task_snap = _make_task_snap()
        _write_proposal(self.root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=gate1), task_snap)
        _write_proposal(self.root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", gate=gate2), task_snap)

        res = provider_metrics(self.root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
        self._assert_common_baseline(res)
        self.assertEqual(res["session_gate"], UNKNOWN_GATE)

    def test_malformed_exact_schema_variants(self) -> None:
        malformed_variants = [
            {
                "state": "NOT_REQUESTED",
                "activation_reason": "NOT_REQUESTED",
                "root_count": 0,
                "required_symbol_count": 0,
                "query_operation_count": 0,
                "extra": 1,
            },
            {
                "state": "NOT_REQUESTED",
                "activation_reason": "NOT_REQUESTED",
                "root_count": 0,
                "required_symbol_count": 0,
            },
            {
                "state": "NOT_REQUESTED",
                "activation_reason": "NOT_REQUESTED",
                "root_count": False,
                "required_symbol_count": 0,
                "query_operation_count": 0,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": True,
                "required_symbol_count": 10,
                "query_operation_count": 2,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 9,
                "required_symbol_count": 10,
                "query_operation_count": 2,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 2,
                "required_symbol_count": 49,
                "query_operation_count": 2,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 2,
                "required_symbol_count": 10,
                "query_operation_count": 25,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": -1,
                "required_symbol_count": 10,
                "query_operation_count": 2,
            },
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "UNKNOWN_REASON",
                "root_count": 2,
                "required_symbol_count": 10,
                "query_operation_count": 2,
            },
            {
                "state": "NOT_REQUESTED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 0,
                "required_symbol_count": 0,
                "query_operation_count": 0,
            },
            {
                "state": "UNKNOWN",
                "activation_reason": "NOT_REQUESTED",
                "root_count": 0,
                "required_symbol_count": 0,
                "query_operation_count": 0,
            },
            {
                "state": "NOT_REQUESTED",
                "activation_reason": "NOT_REQUESTED",
                "root_count": 1,
                "required_symbol_count": 0,
                "query_operation_count": 0,
            },
            "not_a_dict",
            12345,
            [1, 2, 3],
            None,
        ]

        task_snap = _make_task_snap()
        for idx, malformed in enumerate(malformed_variants):
            sub_root = self.root / f"case_{idx}"
            _write_proposal(sub_root, "p1-worker-0", _make_worker_status("p1-worker-0", gate=malformed), task_snap)
            _write_proposal(sub_root, "p1-reviewer-0", _make_reviewer_status("p1-reviewer-0", "p1-worker-0", gate=malformed), task_snap)

            res = provider_metrics(sub_root, task_id=TASK_ID, head_before=HEAD, final_worker_id="p1-worker-0", cycle_duration=30.0)
            self._assert_common_baseline(res)
            self.assertEqual(res["session_gate"], UNKNOWN_GATE, f"Failed for variant {malformed}")

    def test_controller_run_id_mode(self) -> None:
        ctrl_id = "ctrl-gate-run-42"
        gate = {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": "EXPLICIT_DECLARATION",
            "root_count": 3,
            "required_symbol_count": 15,
            "query_operation_count": 5,
        }
        task_snap = _make_task_snap()
        _write_proposal(
            self.root,
            "ctrl-worker-0",
            _make_worker_status("ctrl-worker-0", gate=gate, controller_run_id=ctrl_id),
            task_snap,
        )
        _write_proposal(
            self.root,
            "ctrl-reviewer-0",
            _make_reviewer_status("ctrl-reviewer-0", "ctrl-worker-0", gate=gate, controller_run_id=ctrl_id),
            task_snap,
        )

        res = provider_metrics(
            self.root,
            task_id=TASK_ID,
            head_before=HEAD,
            controller_run_id=ctrl_id,
            cycle_duration=30.0,
        )
        self._assert_common_baseline(res, expected_state="OBSERVED_CONTROLLER_ATTEMPTS")
        self.assertEqual(res["session_gate"], gate)


if __name__ == "__main__":
    unittest.main()
