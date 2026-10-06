"""Foundation tests for two-site work cost witness with two-module helper layout."""

from __future__ import annotations

from typing import Any

import pytest

import tests.test_two_site_work_cost_witness_helpers as helpers
from tests.test_two_site_work_cost_witness_helpers import (
    _ERR_BOUND,
    _ERR_CUSTODY,
    _ERR_TYPED,
    ForeignClassAllHooks,
    ForeignClassUnhashable,
    ForeignDict,
    ForeignDuration,
    ForeignFinished,
    ForeignList,
    ForeignMetaAllHooks,
    ForeignMetaUnhashable,
    ForeignOpaqueLeaf,
    ForeignStrKey,
    armed_hooks,
    assert_custody_unchanged,
    assert_exact_typed,
    capture_custody,
    get_total_hook_calls,
    reset_hook_counters,
)


def make_12key_fixture() -> dict[str, Any]:
    sem = {
        "stage_id": "P1-00b",
        "scenario_key": "scen-ae00",
        "gap_reference": "gap-ref",
        "source_sha256": "ba2cf0c343ba25b840a22ecb62ec8d512b7a55657f3c88a9f1f71f041ddda2ad",
        "fingerprint": "a9d1cf3fc1706d2f452492ebbf96375312109da4e4193c7b646d3fcc4cca35b7",
    }
    wl = {
        "schema_version": 1,
        "fingerprint": "d0e17ded6edc6703f27ed277555ac713d8e3a802212abe7c72ac8004348c4ee9",
    }
    return {
        "schema_version": 1,
        "run_id": "run-test-20261006-001",
        "dimensions": {
            "model": "gemini-3.8-flash",
            "effort": "medium",
            "state": "COMPLETE",
            "finished_at": 1000.5,
            "head_before": "3beab068b4e641fcf2eb243e029d7cc330cf1540",
            "head_after": "0d951b6de9250d208acbea5e10cd51c2d324f94f",
            "worker_run_id": "worker-run-ae00-001",
            "reviewer_run_id": "reviewer-run-ae00-001",
            "proposal_sha256": "ba2cf0c343ba25b840a22ecb62ec8d512b7a55657f3c88a9f1f71f041ddda2ad",
        },
        "semantic_identity": dict(sem),
        "work_lineage": dict(wl),
        "provider_state": "OBSERVED_CONTROLLER_ATTEMPTS",
        "nested_identities": {
            "semantic": {"state": "VALID", "value": dict(sem)},
            "work": {"state": "VALID", "value": dict(wl)},
        },
        "group_eligible": True,
        "observed_candidate": True,
        "codex_usage": {
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "uncached_input_tokens": 20,
            "output_tokens": 10,
        },
        "provider_usage": {
            "observed_subtotal": {
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
            },
            "full_usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
            },
            "attempt_pair": [1, 1],
        },
        "reported_cycle_seconds": 5.0,
    }


def apply_delta(target: dict[str, Any], op: str, path: list[Any], val: Any) -> Any:
    curr: Any = target
    for p in path[:-1]:
        curr = curr[p]
    if op == "SET":
        orig = curr[path[-1]]
        curr[path[-1]] = val
        return orig
    elif op == "ADD":
        curr[path[-1]] = val
        return None
    elif op == "DEL":
        orig_items = list(curr.items())
        del curr[path[-1]]
        return orig_items
    elif op == "REPLACE_KEY":
        d = target
        for p in path:
            d = d[p]
        orig_items = list(d.items())
        old_k, new_k = val
        v = d.pop(old_k)
        d[new_k] = v
        return orig_items
    raise ValueError(f"Unknown op: {op}")


def revert_delta(
    target: dict[str, Any], op: str, path: list[Any], orig: Any, val: Any
) -> None:
    curr: Any = target
    for p in path[:-1]:
        curr = curr[p]
    if op == "SET":
        curr[path[-1]] = orig
    elif op == "ADD":
        del curr[path[-1]]
    elif op == "DEL":
        curr.clear()
        for k, v in orig:
            curr[k] = v
    elif op == "REPLACE_KEY":
        d = target
        for p in path:
            d = d[p]
        d.clear()
        for k, v in orig:
            d[k] = v


FAULT_TABLE: list[tuple[str, str, list[Any], Any]] = [
    ("ACTUAL", "SET", ["schema_version"], True),
    ("ACTUAL", "SET", ["schema_version"], 1.0),
    ("ACTUAL", "SET", ["work_lineage", "schema_version"], True),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair", 0], True),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair", 1], False),
    ("ACTUAL", "SET", ["codex_usage", "input_tokens"], True),
    ("ACTUAL", "SET", ["codex_usage", "cached_input_tokens"], 80.5),
    ("ACTUAL", "SET", ["provider_usage", "observed_subtotal", "input_tokens"], True),
    ("ACTUAL", "SET", ["provider_usage", "full_usage", "output_tokens"], True),
    ("ACTUAL", "SET", ["nested_identities", "work", "value", "schema_version"], True),
    ("ACTUAL", "SET", ["nested_identities", "semantic", "value", "stage_id"], 123),
    ("ACTUAL", "SET", ["dimensions"], ForeignDict({"model": "override"})),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair"], ForeignList([1, 1])),
    ("ACTUAL", "SET", ["run_id"], ForeignStrKey("run-test-20261006-001")),
    ("ACTUAL", "SET", ["schema_version"], ForeignFinished(1)),
    ("ACTUAL", "SET", ["reported_cycle_seconds"], ForeignDuration(5.0)),
    ("ACTUAL", "SET", ["dimensions", "model"], ForeignOpaqueLeaf()),
    ("ACTUAL", "SET", ["dimensions", "model"], ForeignClassUnhashable),
    ("ACTUAL", "ADD", ["extra_root_key"], 123),
    ("ACTUAL", "DEL", ["schema_version"], None),
    ("ACTUAL", "ADD", ["dimensions", "dim_extra"], "extra"),
    ("ACTUAL", "DEL", ["work_lineage", "fingerprint"], None),
    ("ACTUAL", "REPLACE_KEY", ["nested_identities"], ("semantic", ForeignStrKey("semantic"))),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair"], [2, 1]),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair"], [1]),
    ("ACTUAL", "SET", ["provider_usage", "attempt_pair"], [1, 1, 1]),
    ("ACTUAL", "SET", ["reported_cycle_seconds"], 99.9),
    ("ACTUAL", "SET", ["run_id"], "wrong-run-id"),
    ("ACTUAL", "SET", ["group_eligible"], False),
    ("EXPECTED", "REPLACE_KEY", [], ("run_id", ForeignStrKey("run_id"))),
    ("EXPECTED", "SET", ["schema_version"], ForeignFinished(1)),
    ("EXPECTED", "SET", ["dimensions", "finished_at"], ForeignDuration(1000.5)),
    ("EXPECTED", "SET", ["run_id"], ForeignStrKey("run-test-20261006-001")),
    ("EXPECTED", "SET", ["dimensions"], ForeignDict({"model": "override"})),
    ("EXPECTED", "SET", ["provider_usage", "attempt_pair"], ForeignList([1, 1])),
    ("EXPECTED", "SET", ["dimensions", "model"], ForeignOpaqueLeaf()),
    ("EXPECTED", "SET", ["dimensions", "model"], ForeignClassUnhashable),
    ("EXPECTED", "SET", ["dimensions", "model"], ForeignMetaUnhashable),
]


def run_matrix_witness(
    cmp_fn: Any,
    caller_pos: dict[str, Any],
    oracle_pos: dict[str, Any],
    caller_neg: dict[str, Any],
    oracle_neg: dict[str, Any],
    snap_c_pos: tuple[str, Any],
    snap_o_pos: tuple[str, Any],
    snap_c_neg: tuple[str, Any],
    snap_o_neg: tuple[str, Any],
) -> None:
    def _verify_pristine() -> None:
        assert get_total_hook_calls() == 0
        assert_custody_unchanged(caller_pos, snap_c_pos)
        assert_custody_unchanged(oracle_pos, snap_o_pos)
        assert_custody_unchanged(caller_neg, snap_c_neg)
        assert_custody_unchanged(oracle_neg, snap_o_neg)

    with armed_hooks():
        cmp_fn(caller_pos, oracle_pos)
    _verify_pristine()

    with armed_hooks():
        cmp_fn(caller_neg, oracle_neg)
    _verify_pristine()

    for side, op, path, val in FAULT_TABLE:
        target = caller_neg if side == "ACTUAL" else oracle_neg
        orig = apply_delta(target, op, path, val)
        try:
            snap_fault = capture_custody(target)
            with armed_hooks():
                with pytest.raises(AssertionError):
                    cmp_fn(caller_neg, oracle_neg)
            assert get_total_hook_calls() == 0
            if side == "ACTUAL":
                assert_custody_unchanged(caller_neg, snap_fault)
                assert_custody_unchanged(oracle_neg, snap_o_neg)
            else:
                assert_custody_unchanged(caller_neg, snap_c_neg)
                assert_custody_unchanged(oracle_neg, snap_fault)
            assert_custody_unchanged(caller_pos, snap_c_pos)
            assert_custody_unchanged(oracle_pos, snap_o_pos)
        finally:
            revert_delta(target, op, path, orig, val)

        _verify_pristine()
        with armed_hooks():
            cmp_fn(caller_pos, oracle_pos)
        _verify_pristine()
        with armed_hooks():
            cmp_fn(caller_neg, oracle_neg)
        _verify_pristine()


def test_r1_capture_and_custody_unchanged_builtins():
    data = {
        "text": "hello",
        "count": 42,
        "ratio": 3.14,
        "flag": True,
        "empty": None,
        "items": [1, "a", False, None],
        "sub": {"nested": 7},
        "nan_val": float("nan"),
        "inf_val": float("inf"),
    }
    snap = capture_custody(data)
    assert_custody_unchanged(data, snap)

    assert_custody_unchanged({"v": 1}, capture_custody({"v": 1}))
    with pytest.raises(AssertionError):
        assert_custody_unchanged({"v": True}, capture_custody({"v": 1}))

    data["items"][0] = 999
    with pytest.raises(AssertionError):
        assert_custody_unchanged(data, snap)
    data["items"][0] = 1
    assert_custody_unchanged(data, snap)


def test_r2_exact_typed_oracle_contract():
    actual = make_12key_fixture()
    expected = make_12key_fixture()
    assert_exact_typed(actual, expected)

    with pytest.raises(AssertionError):
        assert_exact_typed({"v": 10.0}, {"v": 10})
    with pytest.raises(AssertionError):
        assert_exact_typed({"v": 10}, {"v": 10.0})

    with pytest.raises(AssertionError):
        assert_exact_typed({"v": True}, {"v": 1})
    with pytest.raises(AssertionError):
        assert_exact_typed({"v": 1}, {"v": True})

    with pytest.raises(AssertionError):
        assert_exact_typed({1: "a"}, {"1": "a"})
    with pytest.raises(AssertionError):
        assert_exact_typed({"1": "a"}, {1: "a"})

    assert_exact_typed({"n": float("nan")}, {"n": float("nan")})
    with pytest.raises(AssertionError):
        assert_exact_typed({"n": float("nan")}, {"n": 0.0})

    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignDict({"a": 1}))
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignList([1, 2]))
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignFinished(1))
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignDuration(1.0))
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignStrKey("val"))
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignOpaqueLeaf())
        with pytest.raises(AssertionError):
            assert_exact_typed(actual, ForeignClassUnhashable)
    assert get_total_hook_calls() == 0


def test_r2_expected_defect_witness():
    caller_pos = make_12key_fixture()
    oracle_pos = make_12key_fixture()
    caller_neg = make_12key_fixture()
    oracle_neg = make_12key_fixture()

    snap_c_pos = capture_custody(caller_pos)
    snap_o_pos = capture_custody(oracle_pos)
    snap_c_neg = capture_custody(caller_neg)
    snap_o_neg = capture_custody(oracle_neg)

    run_matrix_witness(
        assert_exact_typed,
        caller_pos,
        oracle_pos,
        caller_neg,
        oracle_neg,
        snap_c_pos,
        snap_o_pos,
        snap_c_neg,
        snap_o_neg,
    )

    exp_mut_count = [0]

    def bad_expected_cmp(actual: Any, expected: Any, depth: int = 0) -> None:
        try:
            assert_exact_typed(actual, expected, depth=depth)
        except AssertionError:
            if (
                isinstance(expected, dict)
                and isinstance(expected.get("dimensions"), dict)
                and type(expected["dimensions"].get("finished_at")) is ForeignDuration
            ):
                exp_mut_count[0] += 1
                expected["provider_usage"]["observed_subtotal"]["input_tokens"] = 777
            raise

    failed_with_custody = False
    try:
        run_matrix_witness(
            bad_expected_cmp,
            caller_pos,
            oracle_pos,
            caller_neg,
            oracle_neg,
            snap_c_pos,
            snap_o_pos,
            snap_c_neg,
            snap_o_neg,
        )
    except AssertionError as exc:
        if str(exc) == _ERR_CUSTODY:
            failed_with_custody = True
        else:
            raise
    finally:
        if oracle_neg["provider_usage"]["observed_subtotal"]["input_tokens"] == 777:
            oracle_neg["provider_usage"]["observed_subtotal"]["input_tokens"] = 10
        if type(oracle_neg["dimensions"].get("finished_at")) is ForeignDuration:
            oracle_neg["dimensions"]["finished_at"] = 1000.5

    assert failed_with_custody is True
    assert exp_mut_count[0] == 1

    run_matrix_witness(
        assert_exact_typed,
        caller_pos,
        oracle_pos,
        caller_neg,
        oracle_neg,
        snap_c_pos,
        snap_o_pos,
        snap_c_neg,
        snap_o_neg,
    )
    assert_custody_unchanged(caller_pos, snap_c_pos)
    assert_custody_unchanged(oracle_pos, snap_o_pos)
    assert_custody_unchanged(caller_neg, snap_c_neg)
    assert_custody_unchanged(oracle_neg, snap_o_neg)
    assert get_total_hook_calls() == 0


def test_r3_foreign_counterexamples_and_unhashable():
    f_str_key = ForeignStrKey("foreign_key_name")
    f_duration = ForeignDuration(1.23)
    f_finished = ForeignFinished(42)
    f_dict = ForeignDict({"sub": 1})
    f_list = ForeignList([10, 20])
    f_leaf = ForeignOpaqueLeaf()
    f_cls_unhashable = ForeignClassUnhashable
    f_cls_all_hooks = ForeignClassAllHooks
    f_meta_unhashable = ForeignMetaUnhashable
    f_meta_all_hooks = ForeignMetaAllHooks

    data = {
        f_str_key: "val_for_foreign_key",
        "duration": f_duration,
        "finished": f_finished,
        "dict_node": f_dict,
        "list_node": f_list,
        "leaf_node": f_leaf,
        "cls_unhashable": f_cls_unhashable,
        "cls_all_hooks": f_cls_all_hooks,
        "meta_unhashable": f_meta_unhashable,
        "meta_all_hooks": f_meta_all_hooks,
        "provider_usage": {
            "observed_subtotal": {"input_tokens": 14}
        },
    }

    with armed_hooks():
        snap = capture_custody(data)
        assert_custody_unchanged(data, snap)
    assert get_total_hook_calls() == 0

    mut_key_data = {
        (ForeignStrKey("foreign_key_name") if k is f_str_key else k): v
        for k, v in data.items()
    }
    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_custody_unchanged(mut_key_data, snap)
    assert get_total_hook_calls() == 0

    mut_leaf_data = dict(data)
    mut_leaf_data["leaf_node"] = ForeignOpaqueLeaf()
    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_custody_unchanged(mut_leaf_data, snap)
    assert get_total_hook_calls() == 0

    mut_cls_data = dict(data)
    mut_cls_data["cls_unhashable"] = ForeignClassAllHooks
    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_custody_unchanged(mut_cls_data, snap)
    assert get_total_hook_calls() == 0

    mut_meta_data = dict(data)
    mut_meta_data["meta_unhashable"] = type
    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_custody_unchanged(mut_meta_data, snap)
    assert get_total_hook_calls() == 0

    preferred_usage = 14
    data["provider_usage"]["observed_subtotal"]["input_tokens"] = 777
    with armed_hooks():
        with pytest.raises(AssertionError):
            assert_custody_unchanged(data, snap)
    assert preferred_usage == 14
    assert get_total_hook_calls() == 0

    data["provider_usage"]["observed_subtotal"]["input_tokens"] = 14
    with armed_hooks():
        assert_custody_unchanged(data, snap)
    assert get_total_hook_calls() == 0


def test_r3_actual_mutation_counterproof():
    caller_pos = make_12key_fixture()
    oracle_pos = make_12key_fixture()
    caller_neg = make_12key_fixture()
    oracle_neg = make_12key_fixture()

    snap_c_pos = capture_custody(caller_pos)
    snap_o_pos = capture_custody(oracle_pos)
    snap_c_neg = capture_custody(caller_neg)
    snap_o_neg = capture_custody(oracle_neg)

    run_matrix_witness(
        assert_exact_typed,
        caller_pos,
        oracle_pos,
        caller_neg,
        oracle_neg,
        snap_c_pos,
        snap_o_pos,
        snap_c_neg,
        snap_o_neg,
    )

    mutation_count = [0]

    def bad_actual_cmp(actual: Any, expected: Any, depth: int = 0) -> None:
        try:
            assert_exact_typed(actual, expected, depth=depth)
        except AssertionError:
            if (
                isinstance(actual, dict)
                and isinstance(actual.get("work_lineage"), dict)
                and type(actual["work_lineage"].get("schema_version")) is bool
            ):
                mutation_count[0] += 1
                actual["provider_usage"]["observed_subtotal"]["input_tokens"] = 777
            raise

    failed_with_custody = False
    try:
        run_matrix_witness(
            bad_actual_cmp,
            caller_pos,
            oracle_pos,
            caller_neg,
            oracle_neg,
            snap_c_pos,
            snap_o_pos,
            snap_c_neg,
            snap_o_neg,
        )
    except AssertionError as exc:
        if str(exc) == _ERR_CUSTODY:
            failed_with_custody = True
        else:
            raise
    finally:
        if caller_neg["provider_usage"]["observed_subtotal"]["input_tokens"] == 777:
            caller_neg["provider_usage"]["observed_subtotal"]["input_tokens"] = 10
        if caller_neg["work_lineage"]["schema_version"] is True:
            caller_neg["work_lineage"]["schema_version"] = 1

    assert failed_with_custody is True
    assert mutation_count[0] == 1

    run_matrix_witness(
        assert_exact_typed,
        caller_pos,
        oracle_pos,
        caller_neg,
        oracle_neg,
        snap_c_pos,
        snap_o_pos,
        snap_c_neg,
        snap_o_neg,
    )
    assert_custody_unchanged(caller_pos, snap_c_pos)
    assert_custody_unchanged(oracle_pos, snap_o_pos)
    assert_custody_unchanged(caller_neg, snap_c_neg)
    assert_custody_unchanged(oracle_neg, snap_o_neg)
    assert get_total_hook_calls() == 0


def test_r4_comparator_all_depth_counterexamples_and_custody():
    caller_pos = make_12key_fixture()
    oracle_pos = make_12key_fixture()
    caller_neg = make_12key_fixture()
    oracle_neg = make_12key_fixture()

    snap_c_pos = capture_custody(caller_pos)
    snap_o_pos = capture_custody(oracle_pos)
    snap_c_neg = capture_custody(caller_neg)
    snap_o_neg = capture_custody(oracle_neg)

    run_matrix_witness(
        assert_exact_typed,
        caller_pos,
        oracle_pos,
        caller_neg,
        oracle_neg,
        snap_c_pos,
        snap_o_pos,
        snap_c_neg,
        snap_o_neg,
    )


def test_r4_snapshot_guard_defect_witness_failure():
    def _witness(cap_fn, chk_fn):
        data = {"provider_usage": {"observed_subtotal": {"input_tokens": 14}}}
        snap = cap_fn(data)
        data["provider_usage"]["observed_subtotal"]["input_tokens"] = 777
        detected = False
        try:
            chk_fn(data, snap)
        except AssertionError:
            detected = True
        if not detected:
            raise AssertionError("defect: nested mutation undetected")
        data["provider_usage"]["observed_subtotal"]["input_tokens"] = 14
        chk_fn(data, snap)

    _witness(capture_custody, assert_custody_unchanged)

    def shallow_cap(v):
        return ("SHALLOW", dict(v) if type(v) is dict else v)

    def shallow_chk(v, s):
        if type(s) is tuple and s[0] == "SHALLOW":
            if type(v) is not dict or set(v.keys()) != set(s[1].keys()):
                raise AssertionError("shallow mismatch")
            return
        if v != s:
            raise AssertionError("shallow mismatch")

    with pytest.raises(AssertionError, match="defect: nested mutation undetected"):
        _witness(shallow_cap, shallow_chk)

    _witness(capture_custody, assert_custody_unchanged)


def test_r5_snapshot_deep_detachment_immutability():
    original_data = {"dict_child": {"key": "value"}, "list_child": [1, [2, 3]]}
    data = {"dict_child": {"key": "value"}, "list_child": [1, [2, 3]]}
    snap = capture_custody(data)

    data["dict_child"]["key"] = "mutated"
    data["dict_child"]["new_key"] = "added"
    data["list_child"][0] = 999
    data["list_child"][1].append(4)

    with pytest.raises(AssertionError):
        assert_custody_unchanged(data, snap)
    assert_custody_unchanged(original_data, snap)


def test_r5_structural_bound_limits_preserved():
    node: Any = {"val": 1}
    for _ in range(15):
        node = {"child": node}
    snap = capture_custody(node)
    assert_custody_unchanged(node, snap)

    deep_node = {"child": node}
    with pytest.raises(ValueError, match="bound"):
        capture_custody(deep_node)

    list_2047 = list(range(2047))
    snap_2047 = capture_custody(list_2047)
    assert_custody_unchanged(list_2047, snap_2047)
    assert len(list_2047) == 2047

    list_2048 = list(range(2048))
    with pytest.raises(ValueError, match="bound"):
        capture_custody(list_2048)
    assert len(list_2048) == 2048
