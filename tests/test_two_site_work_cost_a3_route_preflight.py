"""A3 route preflight and permutation tests."""

from __future__ import annotations

from tests import test_two_site_work_cost_row_field_matrix as field
from tests import test_two_site_work_cost_witness_helpers as h


def test_a3_fixture_route_preflight_and_permutations() -> None:
    plan = (
        field._build_a1_plan()
        + field._build_structural_plan()
        + field._build_a2a_plan()
        + field._build_a3_plan()
    )
    exp = field.FROZEN_CUMULATIVE_237_IDS
    orig = list(plan)
    snaps = [(h.capture_custody(r), h.capture_custody(e)) for _, r, e in orig]
    with h.armed_hooks():
        field.validate_plan(plan, exp)
        assert h.get_total_hook_calls() == 0

    def _chk() -> None:
        assert len(plan) == 237
        for it, o, (s1, s2) in zip(plan, orig, snaps):
            assert it is o and it[0] == o[0] and it[1] is o[1] and it[2] is o[2]
            h.assert_custody_unchanged(it[1], s1)
            h.assert_custody_unchanged(it[2], s2)
        with h.armed_hooks():
            field.validate_plan(plan, exp)
            assert h.get_total_hook_calls() == 0

    for i, j, cid in (
        (0, 236, 1), (226, 236, 1), (235, 236, 1),
        (0, 226, 0), (0, 236, 0), (235, 236, 0),
    ):
        oi, oj = orig[i], orig[j]
        try:
            plan[i] = (oj[0], oi[1], oi[2]) if cid else oj
            plan[j] = (oi[0], oj[1], oj[2]) if cid else oi
            assert {it[0] for it in plan} == set(exp)
            with h.armed_hooks():
                field._expect_check_failure(field.validate_plan, plan, exp)
                assert h.get_total_hook_calls() == 0
        finally:
            plan[i], plan[j] = oi, oj
            _chk()
