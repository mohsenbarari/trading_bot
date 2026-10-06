"""Tests for advisory proposal_existing_file_budgets under PROPOSAL_UTF8_BUDGET_V1."""
from __future__ import annotations

import json
import pytest

import scripts.two_site_task_context as tc
from tests import test_two_site_work_cost_witness_helpers as h

SHA_HEX = "0" * 64


def _entry(path: str, state: str, baseline: int | None, fcap: int, eff: int, growth: int | None) -> dict:
    return {
        "path": path,
        "state": state,
        "baseline_utf8_bytes": baseline,
        "file_limit_utf8_bytes": fcap,
        "effective_single_file_limit_utf8_bytes": eff,
        "max_net_growth_utf8_bytes": growth,
    }


def _budget(entries: list[dict], omitted: int = 0) -> dict:
    return {"version": 1, "entries": entries, "omitted_path_count": omitted}


def _make_task(limits: dict[str, int], total_limit: int, goal: str = "budget task") -> dict:
    profile = {
        "file_limits": dict(sorted(limits.items())),
        "total_limit": total_limit,
        "version": "1.0",
    }
    decl = "PROPOSAL_UTF8_BUDGET_V1:" + json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return {
        "task_id": "test-task",
        "goal": goal,
        "requirements": [decl],
        "allowed_files": sorted(limits.keys()),
    }


def _make_checked_call(task: dict, sources: dict, expected_budget: dict | None, expected_profile: dict | None = None):
    task_snap = h.capture_custody(task)
    sources_snap = h.capture_custody(sources)

    def checked_call() -> dict:
        try:
            res = tc.preparation_notes(task, sources)
        finally:
            h.assert_custody_unchanged(task, task_snap)
            h.assert_custody_unchanged(sources, sources_snap)
        out = res.get("output_contract", {})
        if expected_budget is not None:
            actual_budget = out.get("proposal_existing_file_budgets")
            h.assert_exact_typed(actual_budget, expected_budget)
        if expected_profile is not None:
            actual_profile = out.get("proposal_utf8_budget_profile")
            h.assert_exact_typed(actual_profile, expected_profile)
        return res

    return checked_call


def test_positive_budget_scenarios_and_counterproofs():
    limits = {
        "path_01_persian.py": 1000,
        "path_02_equal.py": 1000,
        "path_03_neg.py": 300,
        "path_04_empty.py": 1000,
        "path_05_newnull.py": 1000,
        "path_06_big.py": 137000,
        "path_07_boundary.py": 160000,
    }
    task = _make_task(limits, total_limit=160000, goal="verify positive file budgets")
    sources = {
        "path_01_persian.py": {"content": "معامله\u200cای", "sha256": SHA_HEX},
        "path_02_equal.py": {"content": "b" * 1000, "sha256": SHA_HEX},
        "path_03_neg.py": {"content": "c" * 350, "sha256": SHA_HEX},
        "path_04_empty.py": {"content": "", "sha256": SHA_HEX},
        "path_05_newnull.py": {"content": None, "sha256": None},
        "path_06_big.py": {"content": "a" * 131904, "sha256": SHA_HEX},
        "path_07_boundary.py": {"content": "x" * 160000, "sha256": SHA_HEX},
    }
    expected_entries = [
        _entry("path_01_persian.py", "EXISTING", 19, 1000, 1000, 981),
        _entry("path_02_equal.py", "EXISTING", 1000, 1000, 1000, 0),
        _entry("path_03_neg.py", "EXISTING", 350, 300, 300, -50),
        _entry("path_04_empty.py", "EXISTING", 0, 1000, 1000, 1000),
        _entry("path_05_newnull.py", "NEW_FILE", 0, 1000, 1000, 1000),
        _entry("path_06_big.py", "EXISTING", 131904, 137000, 137000, 5096),
        _entry("path_07_boundary.py", "EXISTING", 160000, 160000, 160000, 0),
    ]
    expected_budget = _budget(expected_entries, omitted=0)
    call_pos = _make_checked_call(task, sources, expected_budget)

    notes = call_pos()
    assert len(json.dumps(notes).encode("utf-8")) <= 8192

    orig_fn = tc._proposal_existing_file_budgets

    def wrap_extra(profile, src):
        res = orig_fn(profile, src)
        res["unsupported_extra"] = True
        return res

    try:
        tc._proposal_existing_file_budgets = wrap_extra
        with pytest.raises(AssertionError):
            call_pos()
    finally:
        tc._proposal_existing_file_budgets = orig_fn

    call_pos()

    def wrap_bad_scalar(profile, src):
        res = orig_fn(profile, src)
        res["version"] = True
        return res

    try:
        tc._proposal_existing_file_budgets = wrap_bad_scalar
        with pytest.raises(AssertionError):
            call_pos()
    finally:
        tc._proposal_existing_file_budgets = orig_fn

    call_pos()

    orig_goal = task["goal"]

    def wrap_mutate_task(profile, src):
        res = orig_fn(profile, src)
        task["goal"] = "mutated goal text"
        return res

    try:
        tc._proposal_existing_file_budgets = wrap_mutate_task
        with pytest.raises(AssertionError):
            call_pos()
    finally:
        task["goal"] = orig_goal
        tc._proposal_existing_file_budgets = orig_fn

    call_pos()

    orig_sha = sources["path_04_empty.py"]["sha256"]

    def wrap_mutate_sources(profile, src):
        res = orig_fn(profile, src)
        sources["path_04_empty.py"]["sha256"] = "mutated_sha_value"
        return res

    try:
        tc._proposal_existing_file_budgets = wrap_mutate_sources
        with pytest.raises(AssertionError):
            call_pos()
    finally:
        sources["path_04_empty.py"]["sha256"] = orig_sha
        tc._proposal_existing_file_budgets = orig_fn

    call_pos()


def test_total_limit_bounding_scenario():
    limits = {"path_bound.py": 50000}
    task = _make_task(limits, total_limit=30000, goal="verify total limit clamp")
    sources = {"path_bound.py": {"content": "d" * 10000, "sha256": SHA_HEX}}
    expected = _budget([_entry("path_bound.py", "EXISTING", 10000, 50000, 30000, 20000)], omitted=0)
    call = _make_checked_call(task, sources, expected)
    call()


def test_unknown_variants_batch_one():
    limits = {
        "u01_over_chars.py": 1000,
        "u02_over_utf8.py": 1000,
        "u03_surrogate.py": 1000,
        "u04_empty_dict.py": 1000,
        "u05_miss_sha.py": 1000,
        "u06_miss_content.py": 1000,
        "u07_mismatch_null1.py": 1000,
        "u08_mismatch_null2.py": 1000,
    }
    task = _make_task(limits, total_limit=10000, goal="unknown batch 1")
    sources = {
        "u01_over_chars.py": {"content": "a" * 160001, "sha256": SHA_HEX},
        "u02_over_utf8.py": {"content": "\u200c" * 54000, "sha256": SHA_HEX},
        "u03_surrogate.py": {"content": "\ud800", "sha256": SHA_HEX},
        "u04_empty_dict.py": {},
        "u05_miss_sha.py": {"content": None},
        "u06_miss_content.py": {"sha256": None},
        "u07_mismatch_null1.py": {"content": None, "sha256": SHA_HEX},
        "u08_mismatch_null2.py": {"content": "hello", "sha256": None},
    }
    expected = _budget([
        _entry("u01_over_chars.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u02_over_utf8.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u03_surrogate.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u04_empty_dict.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u05_miss_sha.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u06_miss_content.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u07_mismatch_null1.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u08_mismatch_null2.py", "UNKNOWN", None, 1000, 1000, None),
    ], omitted=0)
    call = _make_checked_call(task, sources, expected)
    call()


def test_unknown_variants_batch_two():
    limits = {
        "u09_absent_item.py": 1000,
        "u10_malformed.py": 1000,
        "u11_foreign_dict.py": 1000,
        "u12_nonstr_content.py": 1000,
        "u13_content_str_subclass.py": 1000,
        "u14_nonhex_sha.py": 1000,
        "u15_newline_sha.py": 1000,
        "u16_sha_str_subclass.py": 1000,
    }
    task = _make_task(limits, total_limit=10000, goal="unknown batch 2")
    sources = {
        "u10_malformed.py": 12345,
        "u11_foreign_dict.py": h.ForeignDict({"content": "hi", "sha256": SHA_HEX}),
        "u12_nonstr_content.py": {"content": 100, "sha256": SHA_HEX},
        "u13_content_str_subclass.py": {"content": h.ForeignStrKey("hi"), "sha256": SHA_HEX},
        "u14_nonhex_sha.py": {"content": "hi", "sha256": "z" * 64},
        "u15_newline_sha.py": {"content": "hi", "sha256": ("0" * 63) + "\n"},
        "u16_sha_str_subclass.py": {"content": "hi", "sha256": h.ForeignStrKey(SHA_HEX)},
    }
    expected = _budget([
        _entry("u09_absent_item.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u10_malformed.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u11_foreign_dict.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u12_nonstr_content.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u13_content_str_subclass.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u14_nonhex_sha.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u15_newline_sha.py", "UNKNOWN", None, 1000, 1000, None),
        _entry("u16_sha_str_subclass.py", "UNKNOWN", None, 1000, 1000, None),
    ], omitted=0)
    call = _make_checked_call(task, sources, expected)
    call()


def test_valid_fault_sequence_and_custody_preservation():
    task_val = _make_task({"path_bound.py": 50000}, total_limit=30000, goal="valid sequence test")
    sources_val = {"path_bound.py": {"content": "d" * 10000, "sha256": SHA_HEX}}
    expected_val = _budget([_entry("path_bound.py", "EXISTING", 10000, 50000, 30000, 20000)], omitted=0)
    call_val = _make_checked_call(task_val, sources_val, expected_val)

    task_fault = _make_task({"u04_empty_dict.py": 1000}, total_limit=10000, goal="fault sequence test")
    sources_fault = {"u04_empty_dict.py": {}}
    expected_fault = _budget([_entry("u04_empty_dict.py", "UNKNOWN", None, 1000, 1000, None)], omitted=0)
    call_fault = _make_checked_call(task_fault, sources_fault, expected_fault)

    call_val()
    call_fault()
    call_val()


def test_32_reverse_paths_sorting_omission_and_profile_counterproof():
    paths_rev = [f"path_{i:02d}.py" for i in reversed(range(32))]
    limits = {p: 1000 for p in paths_rev}
    task = _make_task(limits, total_limit=100000, goal="32 reverse paths test")
    sources = {p: {"content": None, "sha256": None} for p in paths_rev}

    expected_entries = [
        _entry(f"path_{i:02d}.py", "NEW_FILE", 0, 1000, 1000, 1000)
        for i in range(8)
    ]
    expected_budget = _budget(expected_entries, omitted=24)
    expected_profile = {
        "version": "1.0",
        "file_limits": {f"path_{i:02d}.py": 1000 for i in range(32)},
        "total_limit": 100000,
    }

    call_32 = _make_checked_call(task, sources, expected_budget, expected_profile)
    call_32()

    orig_fn = tc._proposal_existing_file_budgets

    def wrap_mutate_profile(profile, src):
        res = orig_fn(profile, src)
        profile["file_limits"]["path_09.py"] = 999
        return res

    try:
        tc._proposal_existing_file_budgets = wrap_mutate_profile
        with pytest.raises(AssertionError):
            call_32()
    finally:
        tc._proposal_existing_file_budgets = orig_fn

    call_32()


def test_no_declaration_preserves_old_absence():
    task = {"task_id": "legacy-task", "goal": "legacy", "requirements": ["no budget declaration"]}
    sources = {"legacy_file.py": {"content": "print(1)\n", "sha256": SHA_HEX}}
    notes = tc.preparation_notes(task, sources)
    assert "proposal_existing_file_budgets" not in notes["output_contract"]
    assert "proposal_utf8_budget_profile" not in notes["output_contract"]
