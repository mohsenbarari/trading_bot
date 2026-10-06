"""Nine-branch ownership dependency witness."""

from __future__ import annotations

from typing import Any, Callable
import tests.test_two_site_work_cost_witness_helpers as h

__all__ = ["NineBranchBundle", "run_nine_branch_witness"]


class NineBranchBundle:
    """Retains original caller, baseline oracle, expected rows and mutators."""

    def __init__(
        self,
        caller: Any,
        baseline_oracle: Any,
        expected_rows: list[dict[str, Any]],
        mutators: list[Callable[[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        if type(expected_rows) is not list or len(expected_rows) != 9:
            raise AssertionError(h._ERR_CUSTODY)
        if type(mutators) is not list or len(mutators) != 9:
            raise AssertionError(h._ERR_CUSTODY)
        self.caller = caller
        self.baseline_oracle = baseline_oracle
        self.expected_rows = expected_rows
        self.mutators = mutators
        self._orig_expected_rows = expected_rows
        self._orig_expected_items = list(expected_rows)
        self._orig_mutators = mutators
        self._orig_mutator_items = list(mutators)
        self._snap_caller = h.capture_custody(caller)
        self._snap_baseline = h.capture_custody(baseline_oracle)
        self._snap_expected = [h.capture_custody(row) for row in expected_rows]
        self.assert_all_custody()

    def assert_all_custody(self) -> None:
        """Assert lists and all eleven original trees match fixed snapshots."""
        if self.expected_rows is not self._orig_expected_rows:
            raise AssertionError(h._ERR_CUSTODY)
        if type(self.expected_rows) is not list or len(self.expected_rows) != 9:
            raise AssertionError(h._ERR_CUSTODY)
        for act, orig in zip(self.expected_rows, self._orig_expected_items):
            if act is not orig:
                raise AssertionError(h._ERR_CUSTODY)

        if self.mutators is not self._orig_mutators:
            raise AssertionError(h._ERR_CUSTODY)
        if type(self.mutators) is not list or len(self.mutators) != 9:
            raise AssertionError(h._ERR_CUSTODY)
        for act, orig in zip(self.mutators, self._orig_mutator_items):
            if act is not orig:
                raise AssertionError(h._ERR_CUSTODY)

        h.assert_custody_unchanged(self.caller, self._snap_caller)
        h.assert_custody_unchanged(self.baseline_oracle, self._snap_baseline)
        for i in range(9):
            h.assert_custody_unchanged(self.expected_rows[i], self._snap_expected[i])


def run_nine_branch_witness(bundle: NineBranchBundle, owner: Any) -> None:
    """Execute nine calls under one armed hooks context and verify custody/typing."""
    with h.armed_hooks():
        for i in range(9):
            fn = getattr(owner, "normalize_work_cost_row")
            result = fn(bundle.caller)
            if h.get_total_hook_calls() != 0:
                raise AssertionError("foreign hook executed")
            bundle.assert_all_custody()
            h.assert_exact_typed(result, bundle.baseline_oracle)
            mut = bundle.mutators[i]
            mut(result)
            h.assert_exact_typed(result, bundle.expected_rows[i])
            bundle.assert_all_custody()
            if h.get_total_hook_calls() != 0:
                raise AssertionError("foreign hook executed")
