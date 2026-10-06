"""Contract and counterproof verification for nine-branch witness."""

from __future__ import annotations

import copy
from typing import Any
import pytest

from tests import test_two_site_work_cost_literal_rows as lr
from tests.test_two_site_work_cost_literal_rows_contract import BASELINE_ORACLE
from tests.test_two_site_work_cost_nine_witness import (
    NineBranchBundle,
    run_nine_branch_witness,
)
import tests.test_two_site_work_cost_witness_helpers as h


class _Owner:
    def __init__(self, fn: Any) -> None:
        self.normalize_work_cost_row = fn


def _make_bundle() -> NineBranchBundle:
    caller = lr.make_raw_caller()
    baseline = lr.make_full12_row()
    expected = lr.make_nine_expected_rows()
    mutators = lr.make_nine_mutators()
    return NineBranchBundle(caller, baseline, expected, mutators)


def test_full_nine_pass_and_contract() -> None:
    bundle = _make_bundle()

    h.assert_exact_typed(bundle.baseline_oracle, BASELINE_ORACLE)

    plain_kind_matches = [
        k for k in bundle.caller.keys() if type(k) is str and k == "kind"
    ]
    assert len(plain_kind_matches) == 0

    foreign_keys = [k for k in bundle.caller.keys() if type(k) is h.ForeignStrKey]
    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert bundle.caller[fk] == "opaque-key-value"
    assert type(bundle.caller["opaque"]) is h.ForeignOpaqueLeaf
    assert bundle.caller["meta"] is h.ForeignClassAllHooks

    sub = bundle.baseline_oracle["provider_usage"]["observed_subtotal"]
    ful = bundle.baseline_oracle["provider_usage"]["full_usage"]
    assert sub == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert ful == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert sub is not ful

    cod = bundle.baseline_oracle["codex_usage"]
    assert cod == {
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "uncached_input_tokens": 20,
        "output_tokens": 10,
    }

    assert len(bundle.mutators) == 9
    assert len(bundle.expected_rows) == 9
    for i, mut in enumerate(bundle.mutators):
        fresh = lr.make_full12_row()
        mut(fresh)
        h.assert_exact_typed(fresh, bundle.expected_rows[i])

    call_records: list[bool] = []
    returned_rows: list[dict[str, Any]] = []

    def normal_cb(arg: Any) -> dict[str, Any]:
        call_records.append(arg is bundle.caller)
        row = lr.make_full12_row()
        returned_rows.append(row)
        return row

    owner = _Owner(normal_cb)
    run_nine_branch_witness(bundle, owner)

    assert len(call_records) == 9
    assert all(call_records)
    assert len(returned_rows) == 9
    assert len({id(r) for r in returned_rows}) == 9


def _run_single_counterproof(
    bundle: NineBranchBundle,
    owner: _Owner,
    boundary: int,
    target_idx: int,
) -> None:
    run_nine_branch_witness(bundle, owner)

    saved_cb = owner.normalize_work_cost_row
    call_count = 0

    def fault_cb(arg: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        res = lr.make_full12_row()
        if call_count == boundary + 1:
            if target_idx == 0:
                bundle.caller["nested"]["n10"] = 999
            elif target_idx == 1:
                bundle.baseline_oracle["group_eligible"] = False
            else:
                bundle.expected_rows[target_idx - 2]["group_eligible"] = False
        return res

    owner.normalize_work_cost_row = fault_cb
    try:
        failed = False
        try:
            run_nine_branch_witness(bundle, owner)
        except AssertionError as exc:
            failed = True
            assert str(exc) == h._ERR_CUSTODY
            assert call_count == boundary + 1
        assert failed, f"Expected custody failure at boundary {boundary}, target {target_idx}"
    finally:
        if target_idx == 0:
            bundle.caller["nested"]["n10"] = 10
        elif target_idx == 1:
            bundle.baseline_oracle["group_eligible"] = True
        else:
            bundle.expected_rows[target_idx - 2]["group_eligible"] = True
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)


@pytest.mark.parametrize("boundary", list(range(9)))
@pytest.mark.parametrize("target_idx", list(range(11)))
def test_all_boundaries_x_all_originals_custody_counterproofs(
    boundary: int, target_idx: int
) -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())
    _run_single_counterproof(bundle, owner, boundary, target_idx)


def test_first_callback_expected_list_mutations_fail_custody() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_items = list(bundle.expected_rows)
    saved_cb = owner.normalize_work_cost_row

    mutations = [
        ("append", lambda lst: lst.append(copy.deepcopy(lst[0]))),
        ("delete", lambda lst: lst.pop()),
        ("order", lambda lst: lst.reverse()),
        ("copied_replacement", lambda lst: lst.__setitem__(0, copy.deepcopy(lst[0]))),
    ]

    for name, mutate_fn in mutations:
        call_count = 0

        def fault_cb(arg: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mutate_fn(bundle.expected_rows)
            return lr.make_full12_row()

        owner.normalize_work_cost_row = fault_cb
        try:
            failed = False
            try:
                run_nine_branch_witness(bundle, owner)
            except AssertionError as exc:
                failed = True
                assert str(exc) == h._ERR_CUSTODY
                assert call_count == 1
            assert failed, f"Expected custody failure on {name}"
        finally:
            bundle.expected_rows[:] = saved_items
            owner.normalize_work_cost_row = saved_cb

        run_nine_branch_witness(bundle, owner)


def test_first_callback_mutators_list_mutations_fail_custody() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_muts = list(bundle.mutators)
    saved_cb = owner.normalize_work_cost_row

    def alt_mut(row: dict[str, Any]) -> dict[str, Any]:
        return saved_muts[0](row)

    mutations = [
        ("append", lambda lst: lst.append(lambda r: r)),
        ("delete", lambda lst: lst.pop()),
        ("order", lambda lst: lst.reverse()),
        ("alt_replacement", lambda lst: lst.__setitem__(0, alt_mut)),
    ]

    for name, mutate_fn in mutations:
        call_count = 0

        def fault_cb(arg: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mutate_fn(bundle.mutators)
            return lr.make_full12_row()

        owner.normalize_work_cost_row = fault_cb
        try:
            failed = False
            try:
                run_nine_branch_witness(bundle, owner)
            except AssertionError as exc:
                failed = True
                assert str(exc) == h._ERR_CUSTODY
                assert call_count == 1
            assert failed, f"Expected mutator custody failure on {name}"
        finally:
            bundle.mutators[:] = saved_muts
            owner.normalize_work_cost_row = saved_cb

        run_nine_branch_witness(bundle, owner)


def test_first_callback_poisons_future_expected8_fails_call1() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_cb = owner.normalize_work_cost_row
    call_count = 0

    def poison_cb(arg: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            bundle.expected_rows[8]["group_eligible"] = False
        if call_count == 9:
            r = lr.make_full12_row()
            r["group_eligible"] = False
            return r
        return lr.make_full12_row()

    owner.normalize_work_cost_row = poison_cb
    try:
        failed = False
        try:
            run_nine_branch_witness(bundle, owner)
        except AssertionError as exc:
            failed = True
            assert str(exc) == h._ERR_CUSTODY
            assert call_count == 1
        assert failed, "Expected first call custody failure when poisoning expected8"
    finally:
        bundle.expected_rows[8]["group_eligible"] = True
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)


def test_genuine_alias_fault_fails_subtotal_call7() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_cb = owner.normalize_work_cost_row
    call_count = 0

    def alias_cb(arg: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        row = lr.make_full12_row()
        row["provider_usage"]["full_usage"] = row["provider_usage"]["observed_subtotal"]
        return row

    owner.normalize_work_cost_row = alias_cb
    try:
        failed = False
        try:
            run_nine_branch_witness(bundle, owner)
        except AssertionError as exc:
            failed = True
            assert str(exc) == h._ERR_TYPED
            assert call_count == 7
            bundle.assert_all_custody()
        assert failed, "Expected typed failure on call 7"
    finally:
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)
    bundle.assert_all_custody()


def test_foreign_hook_assertion_first_call_retains_hook_count() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    foreign_keys = [k for k in bundle.caller.keys() if type(k) is h.ForeignStrKey]
    assert len(foreign_keys) == 1
    fk = foreign_keys[0]

    saved_cb = owner.normalize_work_cost_row
    call_count = 0

    def hook_cb(arg: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        try:
            _ = fk == "probe"
        except RuntimeError:
            pass
        return lr.make_full12_row()

    owner.normalize_work_cost_row = hook_cb
    try:
        failed = False
        try:
            run_nine_branch_witness(bundle, owner)
        except AssertionError as exc:
            failed = True
            assert str(exc) == "foreign hook executed"
            assert call_count == 1
            assert h.get_total_hook_calls() == 1
        assert failed, "Expected foreign hook execution error"
    finally:
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)


def test_dynamic_lookup_switches_to_new_callable_remaining_eight_calls() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_cb = owner.normalize_work_cost_row
    first_count = 0
    second_count = 0
    second_callers: list[Any] = []

    def second_cb(arg: Any) -> dict[str, Any]:
        nonlocal second_count
        second_count += 1
        second_callers.append(arg)
        return lr.make_full12_row()

    def first_cb(arg: Any) -> dict[str, Any]:
        nonlocal first_count
        first_count += 1
        owner.normalize_work_cost_row = second_cb
        return lr.make_full12_row()

    owner.normalize_work_cost_row = first_cb
    try:
        run_nine_branch_witness(bundle, owner)
        assert first_count == 1
        assert second_count == 8
        assert len(second_callers) == 8
        for c in second_callers:
            assert c is bundle.caller
    finally:
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)


def test_deliberate_sentinel_callback_exception_restoration() -> None:
    bundle = _make_bundle()
    owner = _Owner(lambda arg: lr.make_full12_row())

    run_nine_branch_witness(bundle, owner)

    saved_cb = owner.normalize_work_cost_row
    call_count = 0

    class DeliberateSentinelError(Exception):
        pass

    def exc_cb(arg: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        raise DeliberateSentinelError("deliberate sentinel error")

    owner.normalize_work_cost_row = exc_cb
    try:
        failed = False
        try:
            run_nine_branch_witness(bundle, owner)
        except DeliberateSentinelError:
            failed = True
            assert call_count == 1
        assert failed, "Expected DeliberateSentinelError not swallowed"
    finally:
        owner.normalize_work_cost_row = saved_cb

    run_nine_branch_witness(bundle, owner)
