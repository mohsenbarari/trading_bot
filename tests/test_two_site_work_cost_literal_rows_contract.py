"""Independent contracts and sensitivity tests for literal-row fixtures."""

from __future__ import annotations

import copy
from typing import Any

from tests import test_two_site_work_cost_literal_rows as lr_mod
from tests.test_two_site_work_cost_witness_helpers import (
    _ERR_TYPED,
    ForeignClassAllHooks,
    ForeignOpaqueLeaf,
    ForeignStrKey,
    armed_hooks,
    assert_custody_unchanged,
    assert_exact_typed,
    capture_custody,
    get_total_hook_calls,
)

BASELINE_ORACLE: dict[str, Any] = {
    "schema_version": 1,
    "run_id": "fixture-run",
    "dimensions": {
        "model": "gpt-6.1-sol",
        "effort": "xhigh",
        "state": "CONTINUE",
        "finished_at": 5.0,
        "head_before": "a" * 40,
        "head_after": "b" * 40,
        "worker_run_id": "fixture-worker-0",
        "reviewer_run_id": "fixture-reviewer-0",
        "proposal_sha256": "c" * 64,
    },
    "semantic_identity": {
        "stage_id": "P1-00b",
        "scenario_key": "literal-fixture",
        "gap_reference": "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md#6",
        "source_sha256": "d" * 64,
        "fingerprint": "e" * 64,
    },
    "work_lineage": {
        "schema_version": 1,
        "fingerprint": "f" * 64,
    },
    "provider_state": "OBSERVED_CONTROLLER_ATTEMPTS",
    "nested_identities": {
        "semantic": {
            "state": "VALID",
            "value": {
                "stage_id": "P1-00b",
                "scenario_key": "literal-fixture",
                "gap_reference": "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md#6",
                "source_sha256": "d" * 64,
                "fingerprint": "e" * 64,
            },
        },
        "work": {
            "state": "VALID",
            "value": {
                "schema_version": 1,
                "fingerprint": "f" * 64,
            },
        },
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


def _make_independent_nine_expected() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(9):
        row = copy.deepcopy(BASELINE_ORACLE)
        if i == 0:
            row["dimensions"]["model"] = "changed-model"
        elif i == 1:
            row["semantic_identity"]["stage_id"] = "changed-stage"
        elif i == 2:
            row["work_lineage"]["schema_version"] = 2
        elif i == 3:
            row["nested_identities"]["semantic"]["value"]["stage_id"] = "changed-stage"
        elif i == 4:
            row["nested_identities"]["work"]["value"]["schema_version"] = 2
        elif i == 5:
            row["codex_usage"]["input_tokens"] = 101
        elif i == 6:
            row["provider_usage"]["observed_subtotal"]["input_tokens"] = 11
        elif i == 7:
            row["provider_usage"]["full_usage"]["input_tokens"] = 11
        elif i == 8:
            row["provider_usage"]["attempt_pair"] = [2, 1]
        rows.append(row)
    return rows


def _whole_witness(row: dict[str, Any]) -> None:
    assert_exact_typed(row, BASELINE_ORACLE)
    if (
        row["provider_usage"]["full_usage"]
        is row["provider_usage"]["observed_subtotal"]
    ):
        raise AssertionError("detachment failure: full_usage is observed_subtotal")
    if (
        row["semantic_identity"]
        is row["nested_identities"]["semantic"]["value"]
    ):
        raise AssertionError("detachment failure: semantic_identity is nested value")
    if (
        row["work_lineage"]
        is row["nested_identities"]["work"]["value"]
    ):
        raise AssertionError("detachment failure: work_lineage is nested value")


def test_full12_row_structure_and_types() -> None:
    row = lr_mod.make_full12_row()
    assert len(row) == 12
    assert set(row.keys()) == set(BASELINE_ORACLE.keys())
    assert_exact_typed(row, BASELINE_ORACLE)


def test_full12_row_detachment_and_independence() -> None:
    r1 = lr_mod.make_full12_row()
    r2 = lr_mod.make_full12_row()

    assert r1 is not r2
    assert r1["dimensions"] is not r2["dimensions"]
    assert r1["semantic_identity"] is not r2["semantic_identity"]
    assert r1["work_lineage"] is not r2["work_lineage"]
    assert r1["nested_identities"] is not r2["nested_identities"]
    assert (
        r1["nested_identities"]["semantic"]
        is not r2["nested_identities"]["semantic"]
    )
    assert (
        r1["nested_identities"]["semantic"]["value"]
        is not r2["nested_identities"]["semantic"]["value"]
    )
    assert (
        r1["nested_identities"]["work"]
        is not r2["nested_identities"]["work"]
    )
    assert (
        r1["nested_identities"]["work"]["value"]
        is not r2["nested_identities"]["work"]["value"]
    )
    assert r1["codex_usage"] is not r2["codex_usage"]
    assert r1["provider_usage"] is not r2["provider_usage"]
    assert (
        r1["provider_usage"]["observed_subtotal"]
        is not r2["provider_usage"]["observed_subtotal"]
    )
    assert (
        r1["provider_usage"]["full_usage"]
        is not r2["provider_usage"]["full_usage"]
    )
    assert (
        r1["provider_usage"]["attempt_pair"]
        is not r2["provider_usage"]["attempt_pair"]
    )

    assert (
        r1["semantic_identity"]
        is not r1["nested_identities"]["semantic"]["value"]
    )
    assert (
        r1["work_lineage"]
        is not r1["nested_identities"]["work"]["value"]
    )
    assert (
        r1["provider_usage"]["observed_subtotal"]
        is not r1["provider_usage"]["full_usage"]
    )

    r1["dimensions"]["model"] = "mutated-model"
    r1["provider_usage"]["observed_subtotal"]["input_tokens"] = 999
    r1["provider_usage"]["attempt_pair"][0] = 999

    r3 = lr_mod.make_full12_row()
    assert_exact_typed(r3, BASELINE_ORACLE)


def test_raw_caller_safe_custody_and_foreign_keys() -> None:
    caller = lr_mod.make_raw_caller()

    plain_kind_matches = [
        k for k in caller.keys() if type(k) is str and k == "kind"
    ]
    assert len(plain_kind_matches) == 0

    with armed_hooks():
        snap = capture_custody(caller)
        assert get_total_hook_calls() == 0
        assert_custody_unchanged(caller, snap)
        assert get_total_hook_calls() == 0

    assert caller["caller_id"] == "fixture-caller"
    assert caller["nested"] == {"n10": 10}

    foreign_keys = [k for k in caller.keys() if type(k) is ForeignStrKey]
    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert caller[fk] == "opaque-key-value"
    assert type(caller["opaque"]) is ForeignOpaqueLeaf
    assert caller["meta"] is ForeignClassAllHooks

    def _callback(target: Any) -> int:
        return id(target)

    assert _callback(caller) == id(caller)


def test_nine_expected_rows_and_mutators_independent_contract() -> None:
    expected_rows = _make_independent_nine_expected()
    actual_rows = lr_mod.make_nine_expected_rows()
    assert len(actual_rows) == 9

    for i, act_row in enumerate(actual_rows):
        assert_exact_typed(act_row, expected_rows[i])
        assert (
            act_row["semantic_identity"]
            is not act_row["nested_identities"]["semantic"]["value"]
        )
        assert (
            act_row["work_lineage"]
            is not act_row["nested_identities"]["work"]["value"]
        )
        assert (
            act_row["provider_usage"]["observed_subtotal"]
            is not act_row["provider_usage"]["full_usage"]
        )

    for i in range(9):
        for j in range(i + 1, 9):
            r_i = actual_rows[i]
            r_j = actual_rows[j]
            assert r_i is not r_j
            assert r_i["dimensions"] is not r_j["dimensions"]
            assert r_i["semantic_identity"] is not r_j["semantic_identity"]
            assert r_i["work_lineage"] is not r_j["work_lineage"]
            assert r_i["nested_identities"] is not r_j["nested_identities"]
            assert (
                r_i["nested_identities"]["semantic"]["value"]
                is not r_j["nested_identities"]["semantic"]["value"]
            )
            assert (
                r_i["nested_identities"]["work"]["value"]
                is not r_j["nested_identities"]["work"]["value"]
            )
            assert r_i["codex_usage"] is not r_j["codex_usage"]
            assert r_i["provider_usage"] is not r_j["provider_usage"]
            assert (
                r_i["provider_usage"]["observed_subtotal"]
                is not r_j["provider_usage"]["observed_subtotal"]
            )
            assert (
                r_i["provider_usage"]["full_usage"]
                is not r_j["provider_usage"]["full_usage"]
            )
            assert (
                r_i["provider_usage"]["attempt_pair"]
                is not r_j["provider_usage"]["attempt_pair"]
            )

    mutators = lr_mod.make_nine_mutators()
    assert len(mutators) == 9
    for i, mut in enumerate(mutators):
        fresh = lr_mod.make_full12_row()
        mut(fresh)
        assert_exact_typed(fresh, expected_rows[i])

    assert_exact_typed(lr_mod.make_full12_row(), BASELINE_ORACLE)


def test_fixture_contract_sensitivity_cached_tokens() -> None:
    orig_make_full12_row = lr_mod.make_full12_row
    call_count = 0

    def bad_tokens_row() -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        r = orig_make_full12_row()
        r["codex_usage"]["cached_input_tokens"] = 79
        return r

    try:
        lr_mod.make_full12_row = bad_tokens_row
        failed = False
        try:
            _whole_witness(lr_mod.make_full12_row())
        except AssertionError as exc:
            failed = True
            assert str(exc) == _ERR_TYPED
            assert call_count == 1
        assert failed, "Expected _whole_witness to fail on cached_input_tokens"
    finally:
        lr_mod.make_full12_row = orig_make_full12_row

    _whole_witness(lr_mod.make_full12_row())


def test_fixture_contract_sensitivity_full_usage_alias() -> None:
    orig_make_full12_row = lr_mod.make_full12_row
    alias_call_count = 0

    def bad_alias_row() -> dict[str, Any]:
        nonlocal alias_call_count
        alias_call_count += 1
        r = orig_make_full12_row()
        r["provider_usage"]["full_usage"] = r["provider_usage"]["observed_subtotal"]
        return r

    try:
        lr_mod.make_full12_row = bad_alias_row
        failed = False
        try:
            _whole_witness(lr_mod.make_full12_row())
        except AssertionError as exc:
            failed = True
            assert "detachment failure" in str(exc)
            assert alias_call_count == 1
        assert failed, "Expected _whole_witness to fail on full_usage alias"
    finally:
        lr_mod.make_full12_row = orig_make_full12_row

    _whole_witness(lr_mod.make_full12_row())
