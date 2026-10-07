"""Contract test suite for RetainedCallFrame helper."""

from __future__ import annotations

import pytest
from typing import Any
import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_cost_retained_call_frame import (
    RetainedCallFrame,
    _ERR_OWNERSHIP,
    _inspect_graph,
)


class MockOwner:
    """Test double for subjects under test."""

    def __init__(self, fn: Any = None) -> None:
        self.call_count = 0
        self.fn = fn

    def normalize(self, arg: Any) -> Any:
        self.call_count += 1
        if self.fn is not None:
            return self.fn(arg)
        return {"status": "ok", "nested": {"val": 1}}


def test_preflight_lifecycle_and_counts() -> None:
    preflight_calls = 0

    def spy_preflight() -> None:
        nonlocal preflight_calls
        preflight_calls += 1

    caller = {"in": 1}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], spy_preflight)
    assert preflight_calls == 1

    owner = MockOwner()
    frame.call(owner, "normalize", (caller,), oracle)
    assert preflight_calls == 1
    assert owner.call_count == 1

    with pytest.raises(ValueError, match="preflight must be callable"):
        RetainedCallFrame([caller], "not_callable")  # type: ignore[arg-type]

    def failing_preflight() -> None:
        raise ValueError("preflight validation failed")

    with pytest.raises(ValueError, match="preflight validation failed"):
        RetainedCallFrame([caller], failing_preflight)

    def mutating_preflight() -> None:
        caller["in"] = 999
        raise RuntimeError("should be overridden by custody")

    try:
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            RetainedCallFrame([caller], mutating_preflight)
    finally:
        caller["in"] = 1


def test_fixed_objects_validations_and_bounds() -> None:
    with pytest.raises(ValueError, match="fixed_objects must be a list"):
        RetainedCallFrame("not_a_list", lambda: None)  # type: ignore[arg-type]

    too_many = [{"i": i} for i in range(65)]
    with pytest.raises(ValueError, match=h._ERR_BOUND):
        RetainedCallFrame(too_many, lambda: None)

    valid_boundary_64 = [{"i": i} for i in range(64)]
    frame_64 = RetainedCallFrame(valid_boundary_64, lambda: None)
    frame_64.assert_all_custody()

    node2048 = [1] * 2047
    f_2048 = RetainedCallFrame([node2048], lambda: None)
    f_2048.assert_all_custody()

    node2049 = [1] * 2048
    with pytest.raises(ValueError, match=h._ERR_BOUND):
        RetainedCallFrame([node2049], lambda: None)

    d16: Any = 1
    for _ in range(16):
        d16 = [d16]
    f_d16 = RetainedCallFrame([d16], lambda: None)
    f_d16.assert_all_custody()

    d17 = [d16]
    with pytest.raises(ValueError, match=h._ERR_BOUND):
        RetainedCallFrame([d17], lambda: None)


def test_call_method_and_args_validations() -> None:
    caller = {"in": 1}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], lambda: None)
    owner = MockOwner()

    with pytest.raises(ValueError, match="method must be a str"):
        frame.call(owner, 123, (caller,), oracle)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="args must be a tuple"):
        frame.call(owner, "normalize", [caller], oracle)  # type: ignore[arg-type]

    unregistered_arg = {"unregistered": True}
    with pytest.raises(ValueError, match="mutable args not in fixed objects"):
        frame.call(owner, "normalize", (unregistered_arg,), oracle)

    unregistered_oracle = {"unregistered": True}
    with pytest.raises(ValueError, match="mutable expected oracle not in fixed objects"):
        frame.call(owner, "normalize", (caller,), unregistered_oracle)


def test_capacity_64_and_65_boundary() -> None:
    oracles = [{"out": i, "sub": {"v": i}} for i in range(64)]
    frame = RetainedCallFrame(oracles, lambda: None)
    owner = MockOwner(fn=lambda arg: {"out": arg, "sub": {"v": arg}})

    for i in range(64):
        res = frame.call(owner, "normalize", (i,), oracles[i])
        assert res["out"] == i

    assert len(frame._returns) == 64
    assert owner.call_count == 64

    with pytest.raises(ValueError, match=h._ERR_BOUND):
        frame.call(owner, "normalize", (0,), oracles[0])

    assert owner.call_count == 64
    assert len(frame._returns) == 64
    frame.assert_all_custody()


def test_current_owner_replacement_and_missing_method() -> None:
    caller = {"in": 1}
    ora1 = {"status": "ok", "nested": {"val": 1}}
    ora2 = {"status": "ok", "nested": {"val": 2}}
    frame = RetainedCallFrame([caller, ora1, ora2], lambda: None)

    class ReplaceableOwner:
        def __init__(self) -> None:
            self.impl = lambda arg: {"status": "ok", "nested": {"val": 1}}

        def normalize(self, arg: Any) -> Any:
            return self.impl(arg)

    owner = ReplaceableOwner()
    res1 = frame.call(owner, "normalize", (caller,), ora1)
    assert res1["nested"]["val"] == 1

    owner.impl = lambda arg: {"status": "ok", "nested": {"val": 2}}
    res2 = frame.call(owner, "normalize", (caller,), ora2)
    assert res2["nested"]["val"] == 2

    with pytest.raises(AttributeError):
        frame.call(owner, "missing_method", (caller,), ora1)
    frame.assert_all_custody()


def test_custody_first_fixed_oracle_equal_replacement_and_counterproof() -> None:
    caller = {"in": 1}
    fixed_oracle = {"status": "norm", "meta": {"tag": "v1"}}
    frame = RetainedCallFrame([caller, fixed_oracle], lambda: None)
    owner = MockOwner(fn=lambda arg: {"status": "norm", "meta": {"tag": "v1"}})

    orig_meta = fixed_oracle["meta"]
    try:
        fixed_oracle["meta"] = {"tag": "v1"}
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), fixed_oracle)
        assert owner.call_count == 0

        def counterfactual_check() -> None:
            _, exp_ids = _inspect_graph(fixed_oracle)
            if not exp_ids.issubset(frame._fixed_mutable_ids):
                raise ValueError("derived registration error")
            frame.assert_all_custody()

        with pytest.raises(ValueError, match="derived registration error"):
            counterfactual_check()
    finally:
        fixed_oracle["meta"] = orig_meta

    frame.assert_all_custody()
    res = frame.call(owner, "normalize", (caller,), fixed_oracle)
    assert res["status"] == "norm"
    assert owner.call_count == 1


def test_fixed_mutations_and_prior_output_mutations() -> None:
    caller = {"in": {"step": 1}}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], lambda: None)
    owner = MockOwner()

    orig_step = caller["in"]["step"]
    try:
        caller["in"]["step"] = 999
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
        assert owner.call_count == 0
    finally:
        caller["in"]["step"] = orig_step

    frame.assert_all_custody()
    res = frame.call(owner, "normalize", (caller,), oracle)
    assert owner.call_count == 1

    orig_val = res["nested"]["val"]
    try:
        res["nested"]["val"] = 999
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
        assert owner.call_count == 1
    finally:
        res["nested"]["val"] = orig_val

    frame.assert_all_custody()

    orig_nested = res["nested"]
    try:
        res["nested"] = {"val": 1}
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
        assert owner.call_count == 1
    finally:
        res["nested"] = orig_nested

    frame.assert_all_custody()


def test_exception_priorities_with_and_without_mutation() -> None:
    caller = {"in": 1}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], lambda: None)
    owner = MockOwner()

    def raise_value_error(arg: Any) -> Any:
        raise ValueError("source rejected input")

    owner.fn = raise_value_error
    with pytest.raises(ValueError, match="source rejected input"):
        frame.call(owner, "normalize", (caller,), oracle)
    frame.assert_all_custody()

    def raise_keyboard_interrupt(arg: Any) -> Any:
        raise KeyboardInterrupt("user interrupt")

    owner.fn = raise_keyboard_interrupt
    with pytest.raises(KeyboardInterrupt):
        frame.call(owner, "normalize", (caller,), oracle)
    frame.assert_all_custody()

    def raise_system_exit(arg: Any) -> Any:
        raise SystemExit(2)

    owner.fn = raise_system_exit
    with pytest.raises(SystemExit):
        frame.call(owner, "normalize", (caller,), oracle)
    frame.assert_all_custody()

    owner.fn = None
    res = frame.call(owner, "normalize", (caller,), oracle)

    def mutate_then_value_error(arg: Any) -> Any:
        res["nested"]["val"] = 888
        raise ValueError("mutation followed by error")

    owner.fn = mutate_then_value_error
    try:
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
    finally:
        res["nested"]["val"] = 1
    frame.assert_all_custody()

    def mutate_then_interrupt(arg: Any) -> Any:
        res["nested"]["val"] = 888
        raise KeyboardInterrupt()

    owner.fn = mutate_then_interrupt
    try:
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
    finally:
        res["nested"]["val"] = 1
    frame.assert_all_custody()

    def mutate_then_exit(arg: Any) -> Any:
        res["nested"]["val"] = 888
        raise SystemExit(1)

    owner.fn = mutate_then_exit
    try:
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(owner, "normalize", (caller,), oracle)
    finally:
        res["nested"]["val"] = 1
    frame.assert_all_custody()


def test_owner_getattr_mutation_covered_by_guard() -> None:
    caller = {"in": 1}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], lambda: None)
    owner = MockOwner()
    res = frame.call(owner, "normalize", (caller,), oracle)

    class MutatingAttrOwner:
        def __getattr__(self, name: str) -> Any:
            res["nested"]["val"] = 999
            raise RuntimeError("getter failure")

    mut_owner = MutatingAttrOwner()
    try:
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            frame.call(mut_owner, "normalize", (caller,), oracle)
    finally:
        res["nested"]["val"] = 1
    frame.assert_all_custody()


def test_output_typing_faults_separate_kinds() -> None:
    raw_ora = {"kind": "raw", "count": 80, "flag": True}
    norm_ora = {"kind": "norm", "data": {"a": 1, "b": 2}}
    unk_ora = {"kind": "unknown", "val": 10}
    caller = {"in": 1}
    frame = RetainedCallFrame([caller, raw_ora, norm_ora, unk_ora], lambda: None)
    owner = MockOwner()

    # Fault 1: 80 -> 79
    owner.fn = lambda arg: {"kind": "raw", "count": 79, "flag": True}
    with pytest.raises(AssertionError, match=h._ERR_TYPED):
        frame.call(owner, "normalize", (caller,), raw_ora)
    assert len(frame._returns) == 0
    owner.fn = lambda arg: {"kind": "raw", "count": 80, "flag": True}
    res_raw = frame.call(owner, "normalize", (caller,), raw_ora)
    assert len(frame._returns) == 1

    # Fault 2: True vs 1
    owner.fn = lambda arg: {"kind": "raw", "count": 80, "flag": 1}
    with pytest.raises(AssertionError, match=h._ERR_TYPED):
        frame.call(owner, "normalize", (caller,), raw_ora)
    assert len(frame._returns) == 1

    # Fault 3: wrong sibling
    owner.fn = lambda arg: {"kind": "norm", "data": {"a": 1, "b": 999}}
    with pytest.raises(AssertionError, match=h._ERR_TYPED):
        frame.call(owner, "normalize", (caller,), norm_ora)
    assert len(frame._returns) == 1
    owner.fn = lambda arg: {"kind": "norm", "data": {"a": 1, "b": 2}}
    res_norm = frame.call(owner, "normalize", (caller,), norm_ora)
    assert len(frame._returns) == 2

    # Fault 4: extra key
    owner.fn = lambda arg: {"kind": "unknown", "val": 10, "extra": "unexpected"}
    with pytest.raises(AssertionError, match=h._ERR_TYPED):
        frame.call(owner, "normalize", (caller,), unk_ora)
    assert len(frame._returns) == 2
    owner.fn = lambda arg: {"kind": "unknown", "val": 10}
    res_unk = frame.call(owner, "normalize", (caller,), unk_ora)
    assert len(frame._returns) == 3
    frame.assert_all_custody()


def test_mutable_ownership_alias_rejections() -> None:
    caller = {"mode": "echo", "payload": {"count": 42}}
    caller_shaped_oracle = {"mode": "echo", "payload": {"count": 42}}
    fixed_ora = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, caller_shaped_oracle, fixed_ora], lambda: None)
    owner = MockOwner()

    # 1. CODEX caller-alias negative fixture
    owner.fn = lambda arg: caller
    with pytest.raises(AssertionError, match=_ERR_OWNERSHIP):
        frame.call(owner, "normalize", (caller,), caller_shaped_oracle)

    # 2. Return aliases fixed oracle
    owner.fn = lambda arg: fixed_ora
    with pytest.raises(AssertionError, match=_ERR_OWNERSHIP):
        frame.call(owner, "normalize", (caller,), fixed_ora)

    # 3. Positive baseline passes
    owner.fn = lambda arg: {"status": "ok", "nested": {"val": 1}}
    res1 = frame.call(owner, "normalize", (caller,), fixed_ora)
    assert res1 is not fixed_ora

    # 4. Return aliases previous return
    owner.fn = lambda arg: res1
    with pytest.raises(AssertionError, match=_ERR_OWNERSHIP):
        frame.call(owner, "normalize", (caller,), fixed_ora)

    # 5. Duplicate mutable container within returned tree
    dup_leaf = {"val": 1}
    dup_tree = {"status": "ok", "nested": dup_leaf, "extra": dup_leaf}
    dup_ora = {"status": "ok", "nested": {"val": 1}, "extra": {"val": 1}}
    frame_dup = RetainedCallFrame([caller, dup_ora], lambda: None)
    owner.fn = lambda arg: dup_tree
    with pytest.raises(AssertionError, match=_ERR_OWNERSHIP):
        frame_dup.call(owner, "normalize", (caller,), dup_ora)


def test_foreign_hooks_zero_calls() -> None:
    leaf = h.ForeignOpaqueLeaf()
    cls_hook = h.ForeignClassAllHooks
    key_hook = h.ForeignStrKey("k")
    dur_hook = h.ForeignDuration(1.5)
    fin_hook = h.ForeignFinished(10)
    dict_hook = h.ForeignDict({"a": 1})
    list_hook = h.ForeignList([1, 2])

    caller = {
        "leaf": leaf,
        "cls": cls_hook,
        "dur": dur_hook,
        "fin": fin_hook,
        "dict": dict_hook,
        "list": list_hook,
    }
    oracle = {"res": 1}

    with h.armed_hooks():
        frame = RetainedCallFrame([caller, oracle], lambda: None)
        owner = MockOwner(fn=lambda arg: {"res": 1})
        frame.call(owner, "normalize", (caller,), oracle)
        frame.assert_all_custody()
        assert h.get_total_hook_calls() == 0


def test_source_counterproof_assert_all_custody_noop() -> None:
    caller = {"in": 1}
    oracle = {"status": "ok", "nested": {"val": 1}}
    frame = RetainedCallFrame([caller, oracle], lambda: None)
    owner = MockOwner()

    res = frame.call(owner, "normalize", (caller,), oracle)
    assert owner.call_count == 1

    res["nested"]["val"] = 999
    with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
        frame.call(owner, "normalize", (caller,), oracle)
    assert owner.call_count == 1

    orig_method = frame.assert_all_custody
    wrong_pass_detected = False
    try:
        frame.assert_all_custody = lambda: None  # type: ignore[assignment]
        wrong_pass_res = frame.call(owner, "normalize", (caller,), oracle)
        assert wrong_pass_res is not None
        assert owner.call_count == 2
        wrong_pass_detected = True
    finally:
        frame.assert_all_custody = orig_method
        res["nested"]["val"] = 1

    assert wrong_pass_detected is True
    pass_res = frame.call(owner, "normalize", (caller,), oracle)
    assert pass_res["nested"]["val"] == 1
    assert owner.call_count == 3
