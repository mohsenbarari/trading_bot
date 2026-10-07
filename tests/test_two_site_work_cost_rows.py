"""Core behavior, custody and counterproof tests for work cost normalization."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import sys

from scripts import two_site_cycle_metrics as cycle_metrics
from scripts import two_site_work_cost_rows as rows
from tests import test_two_site_work_cost_row_fixtures as fixtures
from tests import test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_cost_retained_call_frame import RetainedCallFrame
from tests.test_two_site_work_lineage_consumer import (
    FROZEN_AST_HASH,
    FROZEN_PROJECTION_A,
    FROZEN_PROJECTION_B,
    FROZEN_WORK_LINEAGE_A,
    FROZEN_WORK_LINEAGE_B,
)

__all__ = ["fixtures", "h", "run_case"]


def _preflight():
    assert type(rows.MAX_NUMERIC) is int and rows.MAX_NUMERIC == 1000000000000
    assert cycle_metrics.semantic_identity(FROZEN_PROJECTION_A) is not None
    assert cycle_metrics.semantic_identity(FROZEN_PROJECTION_B) is not None
    assert cycle_metrics.work_lineage(FROZEN_WORK_LINEAGE_A) is not None
    assert cycle_metrics.work_lineage(FROZEN_WORK_LINEAGE_B) is not None
    assert cycle_metrics._is_gemini_worker_run_id("fixture-worker-0")
    assert cycle_metrics._is_gemini_reviewer_run_id("fixture-reviewer-0")


def _call1(frame, r, e):
    with h.armed_hooks():
        h.reset_hook_counters()
        res = frame.call(rows, "normalize_work_cost_row", (r,), e)
        assert h.get_total_hook_calls() == 0
        return res


def _call3(frame, rg, eg, rf, ef):
    with h.armed_hooks():
        h.reset_hook_counters()
        r1 = frame.call(rows, "normalize_work_cost_row", (rg,), eg)
        r2 = frame.call(rows, "normalize_work_cost_row", (rf,), ef)
        r3 = frame.call(rows, "normalize_work_cost_row", (rg,), eg)
        assert h.get_total_hook_calls() == 0
        return r1, r2, r3


def run_case(rg, eg, rf, ef):
    frame = RetainedCallFrame([rg, eg, rf, ef], _preflight)
    try:
        return _call3(frame, rg, eg, rf, ef)
    finally:
        frame.assert_all_custody()


def test_twelve_row_cost_table():
    def _tok(i, o, t):
        return {"input_tokens": i, "output_tokens": o, "total_tokens": t}

    f120, s14, zero = _tok(100, 20, 120), _tok(10, 4, 14), _tok(0, 0, 0)
    bad, bad2 = _tok(10, 4, 999), _tok(1, 1, 999)
    obs = "OBSERVED_CONTROLLER_ATTEMPTS"

    def _pm(st, att, uatt, u=None, sub=None):
        d = {"state": st, "attempts": att, "usage_observed_attempts": uatt}
        if u is not None:
            d["usage"] = u
        if sub is not None:
            d["observed_usage_subtotal"] = sub
        return d

    cases = [
        (_pm(obs, 1, 1, f120, s14), obs, f120, f120, [1, 1], True, True),
        (_pm("PARTIAL", 1, 1, f120, s14), "PARTIAL", s14, None, [1, 1], False, False),
        (_pm("OK", 1, 1, f120, s14), "OK", f120, f120, [1, 1], False, False),
        (_pm("UNKNOWN", 1, 1, f120, s14), None, None, None, None, False, False),
        (_pm(obs, 1, 1, sub=s14), obs, s14, None, [1, 1], True, True),
        (_pm(obs, 1, 1, bad, s14), obs, s14, None, [1, 1], True, True),
        (_pm(obs, 1, 1, bad, bad2), obs, None, None, [1, 1], True, True),
        (_pm(obs, -1, -1, s14, s14), obs, s14, None, None, True, True),
        (_pm(obs, 1, 0, s14, s14), obs, s14, None, None, True, True),
        (_pm(obs, 0, 0, s14, s14), obs, s14, None, None, True, True),
        (_pm(obs, 1, 0, sub=zero), obs, zero, None, [1, 0], True, True),
        (_pm(obs, 0, 0, zero, zero), obs, zero, zero, [0, 0], True, True),
    ]
    assert len(cases) == 12

    for pm_override, exp_pstate, exp_sub, exp_full, exp_pair, exp_grp, exp_cand in cases:
        rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
        rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
        rf["provider_metrics"].update(copy.deepcopy(pm_override))
        if "usage" not in pm_override:
            rf["provider_metrics"].pop("usage", None)
        ef["provider_state"] = exp_pstate
        ef["provider_usage"] = {
            "observed_subtotal": copy.deepcopy(exp_sub),
            "full_usage": copy.deepcopy(exp_full),
            "attempt_pair": copy.deepcopy(exp_pair),
        }
        ef["group_eligible"], ef["observed_candidate"] = exp_grp, exp_cand
        run_case(rg, eg, rf, ef)


def test_nine_mutable_branches_isolation():
    rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    frame = RetainedCallFrame([rg, eg], _preflight)
    ret1 = _call1(frame, rg, eg)
    ret2 = _call1(frame, rg, eg)

    snap_raw, snap_exp, snap_ret2 = h.capture_custody(rg), h.capture_custody(eg), h.capture_custody(ret2)
    branches = [
        (ret1["dimensions"], "model", "mutated"),
        (ret1["semantic_identity"], "stage_id", "mutated"),
        (ret1["work_lineage"], "fingerprint", "0" * 64),
        (ret1["nested_identities"]["semantic"]["value"], "stage_id", "mutated"),
        (ret1["nested_identities"]["work"]["value"], "fingerprint", "0" * 64),
        (ret1["codex_usage"], "input_tokens", 999),
        (ret1["provider_usage"]["observed_subtotal"], "input_tokens", 999),
        (ret1["provider_usage"]["full_usage"], "input_tokens", 999),
        (ret1["provider_usage"]["attempt_pair"], 0, 99),
    ]
    all_snaps = [h.capture_custody(b[0]) for b in branches]
    for i, (target, key, val) in enumerate(branches):
        orig = target[key]
        try:
            target[key] = val
            h.assert_custody_unchanged(rg, snap_raw)
            h.assert_custody_unchanged(eg, snap_exp)
            h.assert_custody_unchanged(ret2, snap_ret2)
            for j, (p_tup, p_snap) in enumerate(zip(branches, all_snaps)):
                if j != i:
                    h.assert_custody_unchanged(p_tup[0], p_snap)
        finally:
            target[key] = orig
        frame.assert_all_custody()


def test_counterproof_numeric_fault():
    assert type(rows.MAX_NUMERIC) is int and rows.MAX_NUMERIC == 1000000000000
    rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf["duration_seconds"] = 1000000000001
    ef["reported_cycle_seconds"] = None

    frame = RetainedCallFrame([rg, eg, rf, ef], _preflight)
    try:
        _call3(frame, rg, eg, rf, ef)
        orig_max = rows.MAX_NUMERIC
        try:
            rows.MAX_NUMERIC = 1000000000001
            try:
                _call1(frame, rf, ef)
            except AssertionError as exc:
                assert str(exc) == h._ERR_TYPED
            else:
                raise AssertionError("did not raise")
        finally:
            rows.MAX_NUMERIC = orig_max
        _call3(frame, rg, eg, rf, ef)
    finally:
        frame.assert_all_custody()


def test_counterproof_ok_grouping_fault():
    rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf["provider_metrics"]["state"] = "OK"
    ef["provider_state"] = "OK"
    ef["group_eligible"] = False
    ef["observed_candidate"] = False

    frame = RetainedCallFrame([rg, eg, rf, ef], _preflight)
    try:
        _call3(frame, rg, eg, rf, ef)
        orig_group = rows.GROUPING_PROVIDER_STATES
        try:
            rows.GROUPING_PROVIDER_STATES = frozenset({
                "OBSERVED_CONTROLLER_ATTEMPTS",
                "OBSERVED_MATCHING_ATTEMPTS",
                "OK",
            })
            try:
                _call1(frame, rf, ef)
            except AssertionError as exc:
                assert str(exc) == h._ERR_TYPED
            else:
                raise AssertionError("did not raise")
        finally:
            rows.GROUPING_PROVIDER_STATES = orig_group
        _call3(frame, rg, eg, rf, ef)
    finally:
        frame.assert_all_custody()


def test_float_bounds_math_spy():
    rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf["finished_at"] = 1000000000001.0
    ef["dimensions"]["finished_at"] = None
    rf["duration_seconds"] = 1000000000001.0
    ef["reported_cycle_seconds"] = None

    frame = RetainedCallFrame([rg, eg, rf, ef], _preflight)
    orig_isfinite, orig_isnan = math.isfinite, math.isnan
    math_calls = 0

    def spy_fn(orig):
        def _spy(x):
            nonlocal math_calls
            f = sys._getframe(1)
            if (
                "two_site_work_cost_rows" in f.f_code.co_filename
                or f.f_globals.get("__name__") == "scripts.two_site_work_cost_rows"
            ):
                math_calls += 1
            return orig(x)
        return _spy

    try:
        math.isfinite, math.isnan = spy_fn(orig_isfinite), spy_fn(orig_isnan)
        _call1(frame, rg, eg)
        assert math_calls > 0
        math_calls = 0
        _call1(frame, rf, ef)
        assert math_calls == 0
        _call1(frame, rg, eg)
        assert math_calls > 0
    finally:
        math.isfinite, math.isnan = orig_isfinite, orig_isnan
        frame.assert_all_custody()
    assert math.isfinite is orig_isfinite
    assert math.isnan is orig_isnan


def test_foreign_hooks_and_privacy_sentinel():
    rg, eg = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf, ef = fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")
    rf["privacy_sentinel_field"] = "SUPER_SECRET_TOKEN_DO_NOT_LEAK_XYZ"
    rf["dimensions"] = h.ForeignDict({"bomb": 1})
    rf["codex_usage"] = h.ForeignList([1, 2, 3])
    rf["reported_cycle_seconds"] = h.ForeignDuration(5.0)
    rf["worker_run_id"] = h.ForeignStrKey("worker")
    rf["reviewer_run_id"] = h.ForeignStrKey("reviewer")
    rf["provider_metrics"]["full_usage"] = h.ForeignDict({"a": 1})
    rf["provider_metrics"]["observed_subtotal"] = h.ForeignList([1])
    rf["provider_metrics"]["attempt_pair"] = h.ForeignList([1, 1])

    _, ret_bomb, _ = run_case(rg, eg, rf, ef)
    assert "SUPER_SECRET_TOKEN_DO_NOT_LEAK_XYZ" not in json.dumps(ret_bomb)


def test_legacy_ast_hash():
    with open("scripts/two_site_cycle_metrics.py", "rb") as f:
        raw_bytes = f.read(50001)
    assert len(raw_bytes) <= 50000
    tree = ast.parse(raw_bytes.decode("utf-8"))
    fn_node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "summarize_efficiency_ledger"
    )
    digest = hashlib.sha256(ast.dump(fn_node).encode("utf-8")).hexdigest()
    assert digest == FROZEN_AST_HASH
