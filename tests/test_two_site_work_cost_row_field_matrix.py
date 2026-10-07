"""PARTIAL_REGRESSION_SEED_FULL607_MATRIX_PENDING"""

from __future__ import annotations

from tests import test_two_site_work_cost_rows as core

fixtures = core.fixtures
h = core.h
run_case = core.run_case


def test_seeded_regressions_counterexamples():
    cases = [
        ("run_id", "fixture-raw\n", {"run_id": None}, True, True),
        ("model", "gpt-6.1-sol\n", {"dimensions.model": None}, True, True),
        ("effort", "xhigh\n", {"dimensions.effort": None}, True, True),
        ("state", "CONTINUE\n", {"dimensions.state": None}, True, False),
        ("gemini_worker_run_id", "fixture-worker-0\n", {"dimensions.worker_run_id": None}, True, False),
        ("gemini_reviewer_run_id", "fixture-reviewer-0\n", {"dimensions.reviewer_run_id": None}, True, False),
        ("head_before", "a" * 40 + "\n", {"dimensions.head_before": None}, True, False),
        ("head_after", "b" * 40 + "\n", {"dimensions.head_after": None}, True, False),
        ("proposal_sha256", "c" * 64 + "\n", {"dimensions.proposal_sha256": None}, True, False),
        ("usage_state", h.ForeignStrKey("OBSERVED_COMPLETED_TURNS"), {"codex_usage": None}, True, True),
        ("usage_state", h.ForeignOpaqueLeaf(), {"codex_usage": None}, True, True),
        ("usage_state", "OBSERVED_COMPLETED_TURNS\n", {"codex_usage": None}, True, True),
    ]

    for field, val, overrides, exp_grp, exp_cand in cases:
        rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
        rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
        rf[field] = val
        for k, v in overrides.items():
            (ef if "." not in k else ef[k.split(".")[0]])[k.split(".")[-1]] = v
        ef["group_eligible"], ef["observed_candidate"] = exp_grp, exp_cand
        run_case(rg, eg, rf, ef)
