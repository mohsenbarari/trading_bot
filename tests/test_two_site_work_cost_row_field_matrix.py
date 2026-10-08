"""PARTIAL_REGRESSION_SEED_FULL607_MATRIX_PENDING"""

from __future__ import annotations

import json

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

_STRUCT_TYPES = (
    "native_none", "native_bool", "native_int", "native_float", "native_str",
    "native_list", "native_tuple", "foreign_dict", "foreign_list", "foreign_str",
    "foreign_int", "foreign_float", "foreign_class_all_hooks",
    "foreign_class_unhashable", "foreign_opaque",
)
_KEY_CONTEXTS = (
    "root", "provider_root", "top_semantic", "top_work",
    "nested_semantic", "nested_work", "full_token_map",
    "reachable_subtotal_token_map",
)
_KEY_FAULTS = (
    "native_nonstring_replaces_known",
    "foreign_string_replaces_known",
)
_STRUCT_BOUNDS = (
    "root64valid_pad45actual19",
    "root65malformed_pad46actual19",
    "provider32valid_pad25actual7",
    "provider33invalid_pad26actual7",
)

FROZEN_STRUCTURAL_50_IDS: tuple[str, ...] = tuple(
    [f"struct_type_root_{t}" for t in _STRUCT_TYPES]
    + [f"struct_type_provider_{t}" for t in _STRUCT_TYPES]
    + [f"struct_key_{ctx}_{flt}" for ctx in _KEY_CONTEXTS for flt in _KEY_FAULTS]
    + [f"struct_bound_{b}" for b in _STRUCT_BOUNDS]
)
FROZEN_CUMULATIVE_185_IDS: tuple[str, ...] = FROZEN_EXPECTED_IDS + FROZEN_STRUCTURAL_50_IDS

_ALIAS_PATHS = (
    "dimensions", "codex_usage", "reported_cycle_seconds",
    "worker_run_id", "reviewer_run_id",
    "provider_metrics.full_usage", "provider_metrics.observed_subtotal",
    "provider_metrics.attempt_pair",
)
_PAYLOAD_KINDS = ("native_dict", "native_list", "raising_foreign_bomb")
_EXTRA_PATHS = ("schema_version", "reasoning_output_tokens", "prompt_sha256")
_SAME_RUN_CASES = (
    "unknown_only_equal", "nested_validity_different",
    "model_different", "effort_different", "finished_at_different",
    "duration_different", "selected_cost_different",
)

FROZEN_A2A_41_IDS: tuple[str, ...] = tuple(
    [f"alias_{p.replace('.', '_')}_{k}" for p in _ALIAS_PATHS for k in _PAYLOAD_KINDS]
    + ["alias_crosscase_full_usage_120"]
    + [f"opaque_extra_{p}_{k}" for p in _EXTRA_PATHS for k in _PAYLOAD_KINDS]
    + [f"samerun_{c}" for c in _SAME_RUN_CASES]
)
FROZEN_CUMULATIVE_226_IDS: tuple[str, ...] = FROZEN_CUMULATIVE_185_IDS + FROZEN_A2A_41_IDS

FROZEN_A3_11_IDS: tuple[str, ...] = tuple(
    [f"ownership_{s}" for s in (
        "dimensions", "semantic_identity", "work_lineage",
        "nested_identities_semantic_value", "nested_identities_work_value",
        "codex_usage", "provider_usage_observed_subtotal",
        "provider_usage_full_usage", "provider_usage_attempt_pair",
    )]
    + ["guard_MAX_NUMERIC", "guard_actual_grouping_state_guard"]
)
FROZEN_CUMULATIVE_237_IDS: tuple[str, ...] = FROZEN_CUMULATIVE_226_IDS + FROZEN_A3_11_IDS


def validate_plan(plan: list, expected_ids: tuple[str, ...]) -> None:
    if len(expected_ids) not in (135, 185, 226, 237):
        raise ValueError(f"unsupported manifest length {len(expected_ids)}")
    if len(plan) != len(expected_ids):
        raise ValueError(f"plan length {len(plan)} != {len(expected_ids)}")
    plan_ids = [item[0] for item in plan]
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("plan has duplicate IDs")
    if set(plan_ids) != set(expected_ids):
        raise ValueError("plan ID set != expected ID set")
    if len(expected_ids) == 237:
        o, n, g = (
            core.test_nine_mutable_branches_isolation,
            core.test_counterproof_numeric_fault,
            core.test_counterproof_ok_grouping_fault,
        )
        for i in range(226):
            c, r, _ = plan[i]
            if c != expected_ids[i] or r is o or r is n or r is g:
                raise ValueError("ordinary plan item invalid")
        for i, fn in enumerate((o,) * 9 + (n, g), 226):
            c, f, e = plan[i]
            if c != expected_ids[i] or f is not fn or e is not None:
                raise ValueError("a3 plan item invalid")


def validate_completed(completed: list[str], expected_ids: tuple[str, ...]) -> None:
    if len(expected_ids) not in (135, 185, 226):
        raise ValueError(f"unsupported completed manifest length {len(expected_ids)}")
    if len(completed) != len(expected_ids):
        raise ValueError(f"completed length {len(completed)} != {len(expected_ids)}")
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
            if f == "run_id":
                ef["run_id"] = None
            else:
                dim_k = "worker_run_id" if f == "gemini_worker_run_id" else ("reviewer_run_id" if f == "gemini_reviewer_run_id" else f)
                ef["dimensions"][dim_k] = None
                ef["observed_candidate"] = f in ("model", "effort")
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

    def _set_num(ef, f, val):
        if f == "duration_seconds":
            ef["reported_cycle_seconds"] = val
        else:
            ef["dimensions"]["finished_at"] = val

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
            _set_num(ef, f, None)
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
            _set_num(ef, f, float(v))
            plan.append((f"num_pos_{f}_{name}", rf, ef))

    for f in _NUM_FIELDS:
        rf, ef = _pair()
        rf.pop(f, None)
        _set_num(ef, f, None)
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


def _test_fault(target: list, idx: int, bad_val: object, check_fn, exp_ids: tuple[str, ...], req_len: bool = False) -> None:
    orig = target[idx]
    try:
        target[idx] = bad_val
        if req_len:
            assert len(target) == len(exp_ids)
        _expect_check_failure(check_fn, target, exp_ids)
    finally:
        target[idx] = orig
    check_fn(target, exp_ids)


def _run_counterproofs_and_exec(plan: list, expected_ids: tuple[str, ...]) -> None:
    validate_plan(plan, expected_ids)
    _test_fault(plan, 0, ("ALTERED_ID", plan[0][1], plan[0][2]), validate_plan, expected_ids)

    popped = plan.pop(0)
    try:
        _expect_check_failure(validate_plan, plan, expected_ids)
    finally:
        plan.insert(0, popped)
    validate_plan(plan, expected_ids)

    _test_fault(plan, 0, ("REPLACED_ID", plan[0][1], plan[0][2]), validate_plan, expected_ids, True)
    _test_fault(plan, 1, plan[0], validate_plan, expected_ids, True)

    dummy_completed = list(expected_ids)
    _test_fault(dummy_completed, 0, "WRONG_COMPLETION_ID", validate_completed, expected_ids, True)

    completed_ids: list[str] = []
    for cid, rf, ef in plan:
        rg, eg = _pair()
        r1, r2, r3 = core.run_case(rg, eg, rf, ef)
        assert r1 == eg and r2 == ef and r3 == eg
        assert "PRIVATE_PROBE_ONLY_IN_IGNORED_FIELDS" not in json.dumps(r2)
        if ef == eg:
            assert r2 == r1
        else:
            assert r2 != r1
        completed_ids.append(cid)

    validate_completed(completed_ids, expected_ids)


def test_a1_closed_manifest_execution_and_counterproofs():
    plan = _build_a1_plan()
    _run_counterproofs_and_exec(plan, FROZEN_EXPECTED_IDS)


def _make_provider_invalid_expected():
    ef = fixtures.make_expected_row("A")
    ef["provider_state"] = None
    ef["nested_identities"] = {
        "semantic": {"state": "UNAVAILABLE", "value": None},
        "work": {"state": "UNAVAILABLE", "value": None},
    }
    ef["provider_usage"] = {"observed_subtotal": None, "full_usage": None, "attempt_pair": None}
    ef["group_eligible"], ef["observed_candidate"] = False, False
    return ef


def _build_structural_plan() -> list[tuple[str, object, object]]:
    plan: list[tuple[str, object, object]] = []
    wt = {
        "native_none": None, "native_bool": True, "native_int": 123,
        "native_float": 1.23, "native_str": "wrong_str", "native_list": [1, 2],
        "native_tuple": (1, 2), "foreign_dict": h.ForeignDict({"a": 1}),
        "foreign_list": h.ForeignList([1, 2]), "foreign_str": h.ForeignStrKey("foreign"),
        "foreign_int": h.ForeignFinished(123), "foreign_float": h.ForeignDuration(1.23),
        "foreign_class_all_hooks": h.ForeignClassAllHooks,
        "foreign_class_unhashable": h.ForeignClassUnhashable,
        "foreign_opaque": h.ForeignOpaqueLeaf(),
    }
    for t in _STRUCT_TYPES:
        plan.append((f"struct_type_root_{t}", wt[t], None))

    for t in _STRUCT_TYPES:
        rf = fixtures.make_valid_raw("A")
        rf["provider_metrics"] = wt[t]
        plan.append((f"struct_type_provider_{t}", rf, _make_provider_invalid_expected()))

    ctx_keys = {
        "root": "run_id", "provider_root": "state", "top_semantic": "stage_id",
        "top_work": "schema_version", "nested_semantic": "stage_id",
        "nested_work": "schema_version", "full_token_map": "input_tokens",
        "reachable_subtotal_token_map": "input_tokens",
    }
    for ctx in _KEY_CONTEXTS:
        orig_k = ctx_keys[ctx]
        for flt in _KEY_FAULTS:
            rf, ef = _pair()
            bad_k = 123 if flt == "native_nonstring_replaces_known" else h.ForeignStrKey(orig_k)
            if ctx == "root":
                rf[bad_k] = rf.pop(orig_k)
                ef = None
            elif ctx == "provider_root":
                rf["provider_metrics"][bad_k] = rf["provider_metrics"].pop(orig_k)
                ef = _make_provider_invalid_expected()
            elif ctx in ("top_semantic", "top_work", "nested_semantic", "nested_work"):
                ef["group_eligible"] = ef["observed_candidate"] = False
                if ctx == "top_semantic":
                    rf["semantic_identity"][bad_k] = rf["semantic_identity"].pop(orig_k)
                    ef["semantic_identity"] = None
                elif ctx == "top_work":
                    rf["work_lineage"][bad_k] = rf["work_lineage"].pop(orig_k)
                    ef["work_lineage"] = None
                elif ctx == "nested_semantic":
                    rf["provider_metrics"]["semantic_identity"][bad_k] = rf["provider_metrics"]["semantic_identity"].pop(orig_k)
                    ef["nested_identities"]["semantic"] = {"state": "INVALID", "value": None}
                else:
                    rf["provider_metrics"]["work_lineage"][bad_k] = rf["provider_metrics"]["work_lineage"].pop(orig_k)
                    ef["nested_identities"]["work"] = {"state": "INVALID", "value": None}
            elif ctx == "full_token_map":
                rf["provider_metrics"]["usage"][bad_k] = rf["provider_metrics"]["usage"].pop(orig_k)
                ef["provider_usage"]["full_usage"] = None
            elif ctx == "reachable_subtotal_token_map":
                rf["provider_metrics"].pop("usage", None)
                rf["provider_metrics"]["observed_usage_subtotal"][bad_k] = rf["provider_metrics"]["observed_usage_subtotal"].pop(orig_k)
                ef["provider_usage"]["observed_subtotal"] = None
                ef["provider_usage"]["full_usage"] = None
            plan.append((f"struct_key_{ctx}_{flt}", rf, ef))

    for name, target_len, is_root, exp in (
        ("root64valid_pad45actual19", 64, True, _pair()[1]),
        ("root65malformed_pad46actual19", 65, True, None),
        ("provider32valid_pad25actual7", 32, False, _pair()[1]),
        ("provider33invalid_pad26actual7", 33, False, _make_provider_invalid_expected()),
    ):
        rf, _ = _pair()
        d = rf if is_root else rf["provider_metrics"]
        for i in range(target_len - len(d)):
            d[f"_pad_{i}"] = 1
        plan.append((f"struct_bound_{name}", rf, exp))

    return plan


def test_cumulative_185_manifest_execution_and_counterproofs():
    plan = _build_a1_plan() + _build_structural_plan()
    assert set(FROZEN_EXPECTED_IDS).issubset(set(FROZEN_CUMULATIVE_185_IDS))
    assert len(set(FROZEN_EXPECTED_IDS) & set(FROZEN_STRUCTURAL_50_IDS)) == 0
    _run_counterproofs_and_exec(plan, FROZEN_CUMULATIVE_185_IDS)


def _build_a2a_plan() -> list[tuple[str, dict, dict]]:
    plan: list[tuple[str, dict, dict]] = []
    sentinel = "PRIVATE_PROBE_ONLY_IN_IGNORED_FIELDS"

    def _payload(kind: str):
        if kind == "native_dict":
            return {"probe": sentinel}
        if kind == "native_list":
            return [sentinel]
        return h.ForeignOpaqueLeaf()

    for p in _ALIAS_PATHS:
        for k in _PAYLOAD_KINDS:
            rf, ef = _pair()
            val = _payload(k)
            if p.startswith("provider_metrics."):
                rf["provider_metrics"][p.split(".")[1]] = val
            else:
                rf[p] = val
            plan.append((f"alias_{p.replace('.', '_')}_{k}", rf, ef))

    rf_cross, ef_cross = _pair()
    rf_cross["provider_metrics"].pop("usage", None)
    rf_cross["provider_metrics"]["full_usage"] = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    ef_cross["provider_usage"]["full_usage"] = None
    plan.append(("alias_crosscase_full_usage_120", rf_cross, ef_cross))

    for p in _EXTRA_PATHS:
        for k in _PAYLOAD_KINDS:
            rf, ef = _pair()
            rf[p] = _payload(k)
            plan.append((f"opaque_extra_{p}_{k}", rf, ef))

    rf1, ef1 = _pair()
    rf1["unknown_opaque_field"] = {"probe": sentinel}
    plan.append(("samerun_unknown_only_equal", rf1, ef1))

    rf2, ef2 = _pair()
    rf2["provider_metrics"]["semantic_identity"] = None
    ef2["nested_identities"]["semantic"] = {"state": "INVALID", "value": None}
    ef2["group_eligible"] = ef2["observed_candidate"] = False
    plan.append(("samerun_nested_validity_different", rf2, ef2))

    diff_cases = (
        ("model_different", "model", "gpt-6.1-other", "dimensions", "model"),
        ("effort_different", "effort", "high", "dimensions", "effort"),
        ("finished_at_different", "finished_at", 6.0, "dimensions", "finished_at"),
        ("duration_different", "duration_seconds", 6.0, "reported_cycle_seconds", None),
    )
    for c_id, raw_k, raw_v, exp_p1, exp_p2 in diff_cases:
        rf, ef = _pair()
        rf[raw_k] = raw_v
        if exp_p2 is None:
            ef[exp_p1] = raw_v
        else:
            ef[exp_p1][exp_p2] = raw_v
        plan.append((f"samerun_{c_id}", rf, ef))

    rf7, ef7 = _pair()
    f120 = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    rf7["provider_metrics"]["usage"] = dict(f120)
    ef7["provider_usage"]["observed_subtotal"] = dict(f120)
    ef7["provider_usage"]["full_usage"] = dict(f120)
    plan.append(("samerun_selected_cost_different", rf7, ef7))

    return plan


def _build_a3_plan() -> list[tuple[str, object, object]]:
    core_fn = core.test_nine_mutable_branches_isolation
    fns = (core_fn,) * 9 + (
        core.test_counterproof_numeric_fault,
        core.test_counterproof_ok_grouping_fault,
    )
    return [(c, fn, None) for c, fn in zip(FROZEN_A3_11_IDS, fns)]


def test_cumulative_226_manifest_execution_and_counterproofs():
    plan = _build_a1_plan() + _build_structural_plan() + _build_a2a_plan()
    assert set(FROZEN_CUMULATIVE_185_IDS).issubset(set(FROZEN_CUMULATIVE_226_IDS))
    assert len(set(FROZEN_CUMULATIVE_185_IDS) & set(FROZEN_A2A_41_IDS)) == 0
    assert len(FROZEN_A2A_41_IDS) == 41
    assert len(FROZEN_CUMULATIVE_226_IDS) == 226
    _run_counterproofs_and_exec(plan, FROZEN_CUMULATIVE_226_IDS)


def test_supplemental_valid_empty_witness():
    rg, eg = _pair()
    rf: dict = {}
    ef = fixtures.make_unknown_scalar_expected()
    r1, r2, r3 = core.run_case(rg, eg, rf, ef)
    assert r2 == ef
