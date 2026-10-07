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


_SCALAR_FIELDS = (
    "run_id", "model", "effort", "state", "gemini_worker_run_id", "gemini_reviewer_run_id",
)
_SCALAR_FAULTS = (
    "missing", "null", "bad_grammar_space", "empty", "newline",
    "wrong_native_int", "foreign_str", "length81",
)

_HEAD_FIELDS = ("head_before", "head_after", "proposal_sha256")
_HEAD_FAULTS = (
    "missing", "empty", "short", "long", "uppercase_same_width",
    "nonhex_same_width", "newline_replaces_final_digit", "null",
    "wrong_native_int", "foreign_str_same_width",
)
_HEAD_ADDITIONAL = (
    "valid_lower40_head_before", "valid_lower40_head_after",
    "valid_lower64_proposal", "known_head_after_equals_before",
)
_WIDTHS = {"head_before": 40, "head_after": 40, "proposal_sha256": 64}

_NUM_FIELDS = ("duration_seconds", "finished_at")
_NUM_FAULTS = (
    "negative_int", "negative_float", "bool_true", "bool_false", "null",
    "foreign_float", "foreign_int", "nan", "positive_infinity",
    "negative_infinity", "literal1000000000001", "native_int10_to_power400",
)
_NUM_POS_NAMES = ("int_0", "float_0", "int_5", "float_5", "int_MAX", "float_MAX")
_FINISHED_STR_NAMES = (
    "ASCII1_valid", "ASCII80_valid", "invalid_str_valid", "empty_invalid",
    "ASCII81_invalid", "nonASCII_invalid", "control_invalid", "newline_invalid",
)

FROZEN_EXPECTED_IDS: tuple[str, ...] = tuple(
    [f"scalar_{f}_{flt}" for f in _SCALAR_FIELDS for flt in _SCALAR_FAULTS]
    + [f"scalar_{f}_valid80" for f in _SCALAR_FIELDS]
    + [f"head_prop_{f}_{flt}" for f in _HEAD_FIELDS for flt in _HEAD_FAULTS]
    + [f"head_prop_{name}" for name in _HEAD_ADDITIONAL]
    + [f"num_fault_{f}_{flt}" for f in _NUM_FIELDS for flt in _NUM_FAULTS]
    + [f"num_pos_{f}_{name}" for f in _NUM_FIELDS for name in _NUM_POS_NAMES]
    + [f"num_missing_{f}" for f in _NUM_FIELDS]
    + ["num_duration_printable_string_invalid"]
    + [f"num_finished_str_{name}" for name in _FINISHED_STR_NAMES]
)


def validate_plan(plan: list, expected_ids: tuple[str, ...]) -> None:
    if len(plan) != 135:
        raise ValueError(f"plan length {len(plan)} != 135")
    plan_ids = [item[0] for item in plan]
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("plan has duplicate IDs")
    if set(plan_ids) != set(expected_ids):
        raise ValueError("plan ID set != expected ID set")


def validate_completed(completed: list[str], expected_ids: tuple[str, ...]) -> None:
    if len(completed) != 135:
        raise ValueError(f"completed length {len(completed)} != 135")
    if set(completed) != set(expected_ids):
        raise ValueError("completed ID set != expected ID set")


def _expect_check_failure(fn, *args):
    try:
        fn(*args)
    except ValueError:
        pass
    else:
        raise AssertionError("checker did not reject invalid manifest")


def _pair():
    return fixtures.make_valid_raw("A"), fixtures.make_expected_row("A")


def _build_a1_plan() -> list[tuple[str, dict, dict]]:
    plan: list[tuple[str, dict, dict]] = []

    s_faults = {
        "null": None, "bad_grammar_space": "bad val", "empty": "",
        "newline": "\n", "wrong_native_int": 12345,
        "foreign_str": h.ForeignStrKey("foreign"), "length81": "a" * 81,
    }
    for f in _SCALAR_FIELDS:
        for flt in _SCALAR_FAULTS:
            rf, ef = _pair()
            if flt == "missing":
                rf.pop(f, None)
            else:
                rf[f] = s_faults[flt]
            if f in ("run_id", "model", "effort"):
                if f == "run_id":
                    ef["run_id"] = None
                else:
                    ef["dimensions"][f] = None
                ef["observed_candidate"] = True
            else:
                dim_k = "state" if f == "state" else ("worker_run_id" if f == "gemini_worker_run_id" else "reviewer_run_id")
                ef["dimensions"][dim_k] = None
                ef["observed_candidate"] = False
            plan.append((f"scalar_{f}_{flt}", rf, ef))

    for f in _SCALAR_FIELDS:
        rf, ef = _pair()
        if f == "gemini_worker_run_id":
            val, cand, dim_k = "worker_" + "a" * 73, True, "worker_run_id"
        elif f == "gemini_reviewer_run_id":
            val, cand, dim_k = "reviewer_" + "b" * 71, True, "reviewer_run_id"
        elif f == "state":
            val, cand, dim_k = "a" * 80, False, "state"
        else:
            val, cand, dim_k = "a" * 80, True, f
        rf[f] = val
        if dim_k == "run_id":
            ef["run_id"] = val
        else:
            ef["dimensions"][dim_k] = val
        ef["observed_candidate"] = cand
        plan.append((f"scalar_{f}_valid80", rf, ef))

    for f in _HEAD_FIELDS:
        w = _WIDTHS[f]
        h_faults = {
            "empty": "", "short": "a" * (w - 1), "long": "a" * (w + 1),
            "uppercase_same_width": "A" * w, "nonhex_same_width": "g" * w,
            "newline_replaces_final_digit": "a" * (w - 1) + "\n",
            "null": None, "wrong_native_int": 12345,
            "foreign_str_same_width": h.ForeignStrKey("a" * w),
        }
        for flt in _HEAD_FAULTS:
            rf, ef = _pair()
            if flt == "missing":
                rf.pop(f, None)
            else:
                rf[f] = h_faults[flt]
            ef["dimensions"][f] = None
            ef["observed_candidate"] = False
            plan.append((f"head_prop_{f}_{flt}", rf, ef))

    for name, f_k, v, cand in (
        ("valid_lower40_head_before", "head_before", "c" * 40, True),
        ("valid_lower40_head_after", "head_after", "d" * 40, True),
        ("valid_lower64_proposal", "proposal_sha256", "e" * 64, True),
        ("known_head_after_equals_before", "head_after", "a" * 40, False),
    ):
        rf, ef = _pair()
        rf[f_k] = v
        ef["dimensions"][f_k] = v
        ef["observed_candidate"] = cand
        plan.append((f"head_prop_{name}", rf, ef))

    num_faults = {
        "negative_int": -1, "negative_float": -1.0, "bool_true": True, "bool_false": False,
        "null": None, "foreign_float": h.ForeignDuration(5.0),
        "foreign_int": h.ForeignFinished(5), "nan": float("nan"),
        "positive_infinity": float("inf"), "negative_infinity": float("-inf"),
        "literal1000000000001": 1000000000001, "native_int10_to_power400": 10**400,
    }
    for f in _NUM_FIELDS:
        for flt in _NUM_FAULTS:
            rf, ef = _pair()
            rf[f] = num_faults[flt]
            if f == "duration_seconds":
                ef["reported_cycle_seconds"] = None
            else:
                ef["dimensions"]["finished_at"] = None
            plan.append((f"num_fault_{f}_{flt}", rf, ef))

    pos_vals = {
        "int_0": 0, "float_0": 0.0, "int_5": 5, "float_5": 5.0,
        "int_MAX": 1000000000000, "float_MAX": 1000000000000.0,
    }
    for f in _NUM_FIELDS:
        for name in _NUM_POS_NAMES:
            rf, ef = _pair()
            v = pos_vals[name]
            rf[f] = v
            if f == "duration_seconds":
                ef["reported_cycle_seconds"] = float(v)
            else:
                ef["dimensions"]["finished_at"] = float(v)
            plan.append((f"num_pos_{f}_{name}", rf, ef))

    for f in _NUM_FIELDS:
        rf, ef = _pair()
        rf.pop(f, None)
        if f == "duration_seconds":
            ef["reported_cycle_seconds"] = None
        else:
            ef["dimensions"]["finished_at"] = None
        plan.append((f"num_missing_{f}", rf, ef))

    rf_ds, ef_ds = _pair()
    rf_ds["duration_seconds"] = "invalid_str"
    ef_ds["reported_cycle_seconds"] = None
    plan.append(("num_duration_printable_string_invalid", rf_ds, ef_ds))

    finished_vals = {
        "ASCII1_valid": ("z", "z"),
        "ASCII80_valid": ("z" * 80, "z" * 80),
        "invalid_str_valid": ("invalid_str", "invalid_str"),
        "empty_invalid": ("", None),
        "ASCII81_invalid": ("z" * 81, None),
        "nonASCII_invalid": ("z\xe9", None),
        "control_invalid": ("z\x01y", None),
        "newline_invalid": ("z\n", None),
    }
    for name in _FINISHED_STR_NAMES:
        raw_v, exp_v = finished_vals[name]
        rf, ef = _pair()
        rf["finished_at"] = raw_v
        ef["dimensions"]["finished_at"] = exp_v
        plan.append((f"num_finished_str_{name}", rf, ef))

    return plan


def test_a1_closed_manifest_execution_and_counterproofs():
    plan = _build_a1_plan()
    validate_plan(plan, FROZEN_EXPECTED_IDS)

    orig_0 = plan[0]
    try:
        plan[0] = ("ALTERED_ID", orig_0[1], orig_0[2])
        _expect_check_failure(validate_plan, plan, FROZEN_EXPECTED_IDS)
    finally:
        plan[0] = orig_0
    validate_plan(plan, FROZEN_EXPECTED_IDS)

    popped = plan.pop(0)
    try:
        _expect_check_failure(validate_plan, plan, FROZEN_EXPECTED_IDS)
    finally:
        plan.insert(0, popped)
    validate_plan(plan, FROZEN_EXPECTED_IDS)

    try:
        plan[0] = ("REPLACED_ID", orig_0[1], orig_0[2])
        assert len(plan) == 135
        _expect_check_failure(validate_plan, plan, FROZEN_EXPECTED_IDS)
    finally:
        plan[0] = orig_0
    validate_plan(plan, FROZEN_EXPECTED_IDS)

    orig_1 = plan[1]
    try:
        plan[1] = plan[0]
        assert len(plan) == 135
        _expect_check_failure(validate_plan, plan, FROZEN_EXPECTED_IDS)
    finally:
        plan[1] = orig_1
    validate_plan(plan, FROZEN_EXPECTED_IDS)

    dummy_completed = list(FROZEN_EXPECTED_IDS)
    orig_comp_0 = dummy_completed[0]
    try:
        dummy_completed[0] = "WRONG_COMPLETION_ID"
        _expect_check_failure(validate_completed, dummy_completed, FROZEN_EXPECTED_IDS)
    finally:
        dummy_completed[0] = orig_comp_0
    validate_completed(dummy_completed, FROZEN_EXPECTED_IDS)

    completed_ids: list[str] = []
    for cid, rf, ef in plan:
        rg, eg = _pair()
        core.run_case(rg, eg, rf, ef)
        completed_ids.append(cid)

    validate_completed(completed_ids, FROZEN_EXPECTED_IDS)


def test_supplemental_valid_empty_witness():
    rg, eg = _pair()
    rf: dict = {}
    ef = fixtures.make_unknown_scalar_expected()
    r1, r2, r3 = core.run_case(rg, eg, rf, ef)
    assert r2 == ef
