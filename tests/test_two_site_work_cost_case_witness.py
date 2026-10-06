"""CASE-ONLY ownership dependency witness."""

from __future__ import annotations

from typing import Any
import tests.test_two_site_work_cost_witness_helpers as h

__all__ = ["CaseBundle", "run_case_witness"]


class CaseBundle:
    """Retains original callers and oracles with initial custody snapshots."""

    def __init__(self,
        positive_caller: Any,
        fault_caller: Any,
        positive_oracle: Any,
        fault_oracle: Any,
    ) -> None:
        self.positive_caller = positive_caller
        self.fault_caller = fault_caller
        self.positive_oracle = positive_oracle
        self.fault_oracle = fault_oracle
        self._snap_pos_call = h.capture_custody(positive_caller)
        self._snap_flt_call = h.capture_custody(fault_caller)
        self._snap_pos_ora = h.capture_custody(positive_oracle)
        self._snap_flt_ora = h.capture_custody(fault_oracle)

    def assert_all_custody(self) -> None:
        """Assert all four original objects match fixed snapshots."""
        h.assert_custody_unchanged(self.positive_caller, self._snap_pos_call)
        h.assert_custody_unchanged(self.fault_caller, self._snap_flt_call)
        h.assert_custody_unchanged(self.positive_oracle, self._snap_pos_ora)
        h.assert_custody_unchanged(self.fault_oracle, self._snap_flt_ora)


def run_case_witness(bundle: CaseBundle, owner: Any) -> None:
    """Execute the three-call sequence under armed hooks and verify custody and typing."""
    calls = [
        (bundle.positive_caller, bundle.positive_oracle),
        (bundle.fault_caller, bundle.fault_oracle),
        (bundle.positive_caller, bundle.positive_oracle),
    ]
    with h.armed_hooks():
        for caller_arg, expected_oracle in calls:
            result = owner.normalize_work_cost_row(caller_arg)
            if h.get_total_hook_calls() != 0:
                raise AssertionError("foreign hook executed")
            bundle.assert_all_custody()
            h.assert_exact_typed(result, expected_oracle)
            bundle.assert_all_custody()
            if h.get_total_hook_calls() != 0:
                raise AssertionError("foreign hook executed")
