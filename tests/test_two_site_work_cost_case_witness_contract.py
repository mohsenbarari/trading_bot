"""Contract tests for CASE-ONLY ownership dependency."""

from __future__ import annotations

from typing import Any
import pytest

import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_cost_case_witness import CaseBundle, run_case_witness


class TrustedOwner:
    """Trusted test owner resolving normalize_work_cost_row without inspecting foreign keys."""

    def __init__(
        self,
        positive_raw: Any,
        fault_raw: Any,
        positive_oracle: Any,
        fault_oracle: Any,
    ) -> None:
        self.positive_raw = positive_raw
        self.fault_raw = fault_raw
        self.pos_out = positive_oracle
        self.flt_out = fault_oracle
        self.call_count = 0
        self.called_args: list[Any] = []
        self.normalize_work_cost_row = self._default_callback

    def _default_callback(self, row: Any) -> Any:
        self.call_count += 1
        self.called_args.append(row)
        if row is self.positive_raw:
            return self.pos_out
        if row is self.fault_raw:
            return self.flt_out
        raise ValueError("unrecognized caller row")


def make_fixture_bundle(colliding_key: bool = True) -> tuple[CaseBundle, h.ForeignStrKey]:
    fk = h.ForeignStrKey("kind")
    pos_caller: dict[Any, Any] = {
        "site": "alpha",
        "cost": {"n10": 10},
        "leaf": h.ForeignOpaqueLeaf(),
        "cls": h.ForeignClassAllHooks,
        "dur": h.ForeignDuration(1.23),
        "fin": h.ForeignFinished(42),
    }
    if colliding_key:
        pos_caller[fk] = h.ForeignClassUnhashable

    flt_caller: dict[Any, Any] = {
        "site": "beta",
        "cost": {"n10": 20},
        "leaf": h.ForeignOpaqueLeaf(),
        "cls": h.ForeignClassUnhashable,
        "dur": h.ForeignDuration(4.56),
        "fin": h.ForeignFinished(99),
    }
    if colliding_key:
        flt_caller[fk] = h.ForeignClassAllHooks

    pos_oracle = {"status": "ok", "summary": {"total": 100}}
    flt_oracle = {"status": "fault", "summary": {"total": 200}}
    bundle = CaseBundle(pos_caller, flt_caller, pos_oracle, flt_oracle)
    return bundle, fk


def test_normal_witness_passes_and_zero_hooks() -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    assert owner.called_args[0] is bundle.positive_caller
    assert owner.called_args[1] is bundle.fault_caller
    assert owner.called_args[2] is bundle.positive_caller
    assert h.get_total_hook_calls() == 0
    bundle.assert_all_custody()


@pytest.mark.parametrize("boundary", [0, 1, 2])
@pytest.mark.parametrize(
    "target_name",
    ["positive_caller", "fault_caller", "positive_oracle", "fault_oracle"],
)
def test_all_call_boundaries_x_all_objects_custody_mutation(
    boundary: int,
    target_name: str,
) -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    target_obj = getattr(bundle, target_name)
    if "cost" in target_obj:
        nested_dict = target_obj["cost"]
        field_key = "n10"
        orig_val = nested_dict[field_key]
        mut_val = 999
    else:
        nested_dict = target_obj["summary"]
        field_key = "total"
        orig_val = nested_dict[field_key]
        mut_val = 999

    def mutating_cb(row: Any) -> Any:
        res = orig_cb(row)
        if owner.call_count == boundary + 1:
            nested_dict[field_key] = mut_val
        return res

    owner.normalize_work_cost_row = mutating_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_CUSTODY
        assert owner.call_count == boundary + 1
    finally:
        nested_dict[field_key] = orig_val
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_future_fault_caller_mutation_undo_caught_first_call() -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    def mutating_cb(row: Any) -> Any:
        res = orig_cb(row)
        if owner.call_count == 1:
            bundle.fault_caller["cost"]["n10"] = 777
        elif owner.call_count == 2:
            bundle.fault_caller["cost"]["n10"] = 20
        return res

    owner.normalize_work_cost_row = mutating_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_CUSTODY
        assert owner.call_count == 1
    finally:
        bundle.fault_caller["cost"]["n10"] = 20
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_future_fault_oracle_mutation_undo_caught_first_call() -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    def mutating_cb(row: Any) -> Any:
        res = orig_cb(row)
        if owner.call_count == 1:
            bundle.fault_oracle["summary"]["total"] = 777
        elif owner.call_count == 2:
            bundle.fault_oracle["summary"]["total"] = 200
        return res

    owner.normalize_work_cost_row = mutating_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_CUSTODY
        assert owner.call_count == 1
    finally:
        bundle.fault_oracle["summary"]["total"] = 200
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_foreign_hook_counterproof_first_call() -> None:
    bundle, fk = make_fixture_bundle(colliding_key=True)
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    def hook_calling_cb(row: Any) -> Any:
        res = orig_cb(row)
        try:
            _ = fk == "kind"
        except RuntimeError:
            pass
        return res

    owner.normalize_work_cost_row = hook_calling_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == "foreign hook executed"
        assert owner.call_count == 1
        assert h.get_total_hook_calls() == 1
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    assert h.get_total_hook_calls() == 0


def test_dynamic_callback_lookup_each_call() -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    orig_cb = owner.normalize_work_cost_row

    calls_cb1: list[Any] = []
    calls_cb2: list[Any] = []

    def cb2(row: Any) -> Any:
        calls_cb2.append(row)
        if row is bundle.fault_caller:
            return bundle.fault_oracle
        if row is bundle.positive_caller:
            return bundle.positive_oracle
        raise ValueError("unrecognized row")

    def cb1(row: Any) -> Any:
        calls_cb1.append(row)
        owner.normalize_work_cost_row = cb2
        return bundle.positive_oracle

    owner.normalize_work_cost_row = cb1
    try:
        run_case_witness(bundle, owner)
        assert len(calls_cb1) == 1
        assert calls_cb1[0] is bundle.positive_caller
        assert len(calls_cb2) == 2
        assert calls_cb2[0] is bundle.fault_caller
        assert calls_cb2[1] is bundle.positive_caller
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_unexpected_callback_exception_cleanup() -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    orig_cb = owner.normalize_work_cost_row

    class DeliberateSentinelError(Exception):
        pass

    def exploding_cb(row: Any) -> Any:
        raise DeliberateSentinelError("deliberate failure sentinel")

    owner.normalize_work_cost_row = exploding_cb
    caught_sentinel = False
    try:
        try:
            run_case_witness(bundle, owner)
        except DeliberateSentinelError as exc:
            assert str(exc) == "deliberate failure sentinel"
            caught_sentinel = True
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    assert caught_sentinel
    assert owner.normalize_work_cost_row is orig_cb
    run_case_witness(bundle, owner)
    assert owner.call_count == 3


@pytest.mark.parametrize(
    "bad_output",
    [
        {"status": "ok", "summary": {"total": 100}, "extra_key": 1},
        {"status": "ok"},
        {"status": "ok", "summary": {"total": "100"}},
        {"status": "ok", "summary": {"total": True}},
    ],
)
def test_typed_oracle_mismatch_rejections(bad_output: dict[str, Any]) -> None:
    bundle, _ = make_fixture_bundle()
    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    def bad_cb(row: Any) -> Any:
        orig_cb(row)
        return bad_output

    owner.normalize_work_cost_row = bad_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_TYPED
        assert owner.call_count == 1
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_fault_oracle_none_distinction() -> None:
    pos_caller = {"site": "s1", "cost": {"n10": 1}}
    flt_caller = {"site": "s2", "cost": {"n10": 2}}
    pos_oracle = {"status": "ok"}
    flt_oracle = None
    bundle = CaseBundle(pos_caller, flt_caller, pos_oracle, flt_oracle)
    owner = TrustedOwner(pos_caller, flt_caller, pos_oracle, None)

    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()
    orig_cb = owner.normalize_work_cost_row

    def bad_flt_cb(row: Any) -> Any:
        res = orig_cb(row)
        if row is flt_caller:
            return {"status": "fault"}
        return res

    owner.normalize_work_cost_row = bad_flt_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_TYPED
        assert owner.call_count == 2
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    owner.call_count = 0
    owner.called_args.clear()

    def bad_pos_cb(row: Any) -> Any:
        res = orig_cb(row)
        if row is pos_caller:
            return None
        return res

    owner.normalize_work_cost_row = bad_pos_cb
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_case_witness(bundle, owner)
        assert str(exc_info.value) == h._ERR_TYPED
        assert owner.call_count == 1
    finally:
        owner.normalize_work_cost_row = orig_cb
        owner.call_count = 0
        owner.called_args.clear()

    run_case_witness(bundle, owner)
    assert owner.call_count == 3


def test_colliding_foreign_str_key_safety() -> None:
    bundle, fk = make_fixture_bundle(colliding_key=True)
    pos_keys = list(bundle.positive_caller.keys())
    assert any(k is fk for k in pos_keys)
    for k in pos_keys:
        if type(k) is str and k is not fk and k == "kind":
            raise AssertionError("plain string kind key found")

    owner = TrustedOwner(
        bundle.positive_caller,
        bundle.fault_caller,
        bundle.positive_oracle,
        bundle.fault_oracle,
    )
    run_case_witness(bundle, owner)
    assert owner.call_count == 3
    assert h.get_total_hook_calls() == 0
    bundle.assert_all_custody()
