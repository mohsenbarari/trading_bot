from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import scripts.two_site_proposal_pipeline as pipeline
from scripts.two_site_agent_sandbox import SandboxError
import tests.test_two_site_pipeline_pre_review_gate as gate
from tests.test_two_site_pipeline_pre_review_gate import DEFAULT_SELECTOR
from tests.test_two_site_work_cost_retained_call_frame import RetainedCallFrame, _ERR_OWNERSHIP
import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_factor_witness_contract import (
    build_expected_feedback,
    build_expected_payload,
    build_oracle_case,
    collapse_utf8_prefix_facts,
    hash_canonical_payload,
    run_retained_check,
)

LINE_BASE = "AssertionError: tests/test_budget.py exceeds 12000 byte limit: 15853 bytes"
PREFIX = "New-test trailing whitespace at sorted change indices: "
SUMMARY_0 = PREFIX + "0"
STDERR_GOOD_0 = LINE_BASE + "\n" + SUMMARY_0
P_SHA = "a" * 64

PINNED_GOOD0_FEEDBACK = {
    "reviewer": "test-gate",
    "reviewed_proposal_sha256": P_SHA,
    "findings": [
        "Pre-review candidate tests failed with exit code 1. Resolve every occurrence of the bounded root cause, not only the displayed line: "
        + LINE_BASE + " " + SUMMARY_0
    ],
    "test_evidence": [
        "returncode: 1",
        "selectors (count=1): tests/test_budget.py",
        'normalized_failure_facts: {"exceptions":["' + LINE_BASE + " " + SUMMARY_0 + '"],"statements":[],"tests":[]}',
        STDERR_GOOD_0,
    ],
}

INVALID_CASES: list[tuple[str, str]] = [
    ("34_byte_lines", "\n".join(f"AssertionError: tests/t{i:02d}.py exceeds 1000 byte limit: 2000 bytes" for i in range(34))),
    ("35_total", "\n".join(f"AssertionError: tests/t{i:02d}.py exceeds 1000 byte limit: 2000 bytes" for i in range(34)) + "\n" + SUMMARY_0),
    ("summary_no_bytes", SUMMARY_0),
    ("empty", LINE_BASE + "\n" + PREFIX),
    ("comma", LINE_BASE + "\n" + PREFIX + ","),
    ("-1", LINE_BASE + "\n" + PREFIX + "-1"),
    ("+1", LINE_BASE + "\n" + PREFIX + "+1"),
    ("32", LINE_BASE + "\n" + PREFIX + "32"),
    ("00", LINE_BASE + "\n" + PREFIX + "00"),
    ("01", LINE_BASE + "\n" + PREFIX + "01"),
    ("duplicate", LINE_BASE + "\n" + PREFIX + "0,0"),
    ("decreasing", LINE_BASE + "\n" + PREFIX + "1,0"),
    ("0,32", LINE_BASE + "\n" + PREFIX + "0,32"),
    ("ws_lead", LINE_BASE + "\n" + PREFIX + " 0"),
    ("ws_trail", LINE_BASE + "\n" + PREFIX + "0 "),
    ("ws_between", LINE_BASE + "\n" + PREFIX + "0, 1"),
    ("unknown_prefix", LINE_BASE + "\nUnknown prefix at sorted change indices: 0"),
    ("unknown_case", LINE_BASE + "\nnew-test trailing whitespace at sorted change indices: 0"),
    ("dup_summary", LINE_BASE + "\n" + SUMMARY_0 + "\n" + SUMMARY_0),
    ("nonlast_summary", LINE_BASE + "\n" + SUMMARY_0 + "\n" + LINE_BASE),
    ("truncated_summary", LINE_BASE + "\nNew-test trailing whitespace"),
    ("arabic", LINE_BASE + "\n" + PREFIX + "\u0660"),
    ("fullwidth", LINE_BASE + "\n" + PREFIX + "\uff10"),
    ("arabic_lz", LINE_BASE + "\n" + PREFIX + "\u06601"),
    ("superscript", LINE_BASE + "\n" + PREFIX + "\u00b2"),
    ("5000_digits", LINE_BASE + "\n" + PREFIX + ("1" * 5000)),
    ("path_namespace", "AssertionError: ../outside.py exceeds 12000 byte limit: 15853 bytes"),
    ("path_length", f"AssertionError: tests/{'x' * 130}.py exceeds 12000 byte limit: 15853 bytes"),
    ("path_wildcard", "AssertionError: tests/*.py exceeds 12000 byte limit: 15853 bytes"),
    ("duplicate_target", f"{LINE_BASE}\n{LINE_BASE}"),
    ("cap_ge_actual", "AssertionError: tests/test_budget.py exceeds 12000 byte limit: 12000 bytes"),
    ("cap_gt_160k", "AssertionError: tests/test_budget.py exceeds 160001 byte limit: 160002 bytes"),
]

PINNED_33_BYTE_LINES = [
    f"AssertionError: tests/test_file_{i:02d}.py exceeds {1000 + i} byte limit: {2000 + i} bytes"
    for i in range(32)
] + ["AssertionError: proposal total exceeds 50000 byte limit: 60000 bytes"]
PINNED_33_FACTORS = sorted(
    [f"tests/test_file_{i:02d}.py exceeds {1000 + i} byte limit" for i in range(32)]
    + ["proposal total exceeds 50000 byte limit"]
)
PINNED_ALL32_SUMMARY = PREFIX + ",".join(str(i) for i in range(32))
PINNED_33_STDERR = "\n".join(PINNED_33_BYTE_LINES)
PINNED_34_STDERR = PINNED_33_STDERR + "\n" + PINNED_ALL32_SUMMARY
PINNED_33_PAYLOAD = build_expected_payload(
    1,
    [DEFAULT_SELECTOR],
    collapse_utf8_prefix_facts("", PINNED_33_STDERR),
    byte_factors=PINNED_33_FACTORS,
)
PINNED_33_HASH = hash_canonical_payload(PINNED_33_PAYLOAD)


def test_pinned_literals_and_valid_summaries() -> None:
    base_factors = ["tests/test_budget.py exceeds 12000 byte limit"]
    c_good0, f_good0 = build_oracle_case(
        proposal_sha=P_SHA,
        stderr_tail=STDERR_GOOD_0,
        byte_factors=base_factors,
    )
    assert c_good0["whole_feedback"] == PINNED_GOOD0_FEEDBACK
    res_good0 = run_retained_check(f_good0, c_good0, pipeline)
    assert res_good0["feedback"] == PINNED_GOOD0_FEEDBACK

    valid_tails = [
        ("byte_only", LINE_BASE),
        ("summary_0", STDERR_GOOD_0),
        ("summary_0_1", LINE_BASE + "\n" + PREFIX + "0,1"),
        ("summary_0_3_31", LINE_BASE + "\n" + PREFIX + "0,3,31"),
        ("summary_31", LINE_BASE + "\n" + PREFIX + "31"),
        ("summary_all32", LINE_BASE + "\n" + PINNED_ALL32_SUMMARY),
    ]
    cases = [
        build_oracle_case(proposal_sha=P_SHA, stderr_tail=stderr, byte_factors=base_factors)
        for _, stderr in valid_tails
    ]
    for c, f in cases:
        res = run_retained_check(f, c, pipeline)
        assert res["factors"] == base_factors

    size_stderrs = [
        f"AssertionError: tests/test_budget.py exceeds 12000 byte limit: {size} bytes\n{SUMMARY_0}"
        for size in (15853, 15847, 15916)
    ]
    assert len(set(size_stderrs)) == 3
    size_cases = [
        build_oracle_case(proposal_sha=P_SHA, stderr_tail=stderr, byte_factors=base_factors)
        for stderr in size_stderrs
    ]
    size_results = [run_retained_check(f, c, pipeline) for c, f in size_cases]
    for r, stderr in zip(size_results, size_stderrs):
        assert r["factors"] == base_factors
        assert r["signature"] == size_results[0]["signature"]
        assert r["feedback"]["test_evidence"][-1] == stderr


def test_invalid_summary_and_boundary_cases_baseline_fault_baseline() -> None:
    base_factors = ["tests/test_budget.py exceeds 12000 byte limit"]
    preconstructed = []
    for name, fault_stderr in INVALID_CASES:
        c_base, f_base = build_oracle_case(
            proposal_sha=P_SHA,
            stderr_tail=LINE_BASE,
            byte_factors=base_factors,
        )
        c_fault, f_fault = build_oracle_case(
            proposal_sha=P_SHA,
            stderr_tail=fault_stderr,
            byte_factors=None,
        )
        preconstructed.append((name, c_base, f_base, c_fault, f_fault))

    for name, c_base, f_base, c_fault, f_fault in preconstructed:
        res_b1 = run_retained_check(f_base, c_base, pipeline)
        assert res_b1["factors"] == base_factors

        res_f = run_retained_check(f_fault, c_fault, pipeline)
        assert res_f["factors"] is None

        res_b2 = run_retained_check(f_base, c_base, pipeline)
        assert res_b2["factors"] == base_factors
        assert res_b2["signature"] == res_b1["signature"]


def test_anomalies_start_from_valid_good_0() -> None:
    p_sha = "c" * 64
    base_factors = ["tests/test_budget.py exceeds 12000 byte limit"]
    anom_defs = [
        ("stdout", {"stdout_tail": "diagnostic stdout"}),
        ("snapshot_files", {"snapshot_files": 1}),
        ("snapshot_bytes", {"snapshot_bytes": 100}),
        ("isolation", {"isolation": "test-double"}),
        ("returncode", {"returncode": 2}),
    ]
    preconstructed = []
    for name, overrides in anom_defs:
        c_base, f_base = build_oracle_case(
            proposal_sha=p_sha,
            stderr_tail=STDERR_GOOD_0,
            byte_factors=base_factors,
        )
        kwargs: dict[str, Any] = {
            "proposal_sha": p_sha,
            "stderr_tail": STDERR_GOOD_0,
            "byte_factors": None,
        }
        kwargs.update(overrides)
        c_anom, f_anom = build_oracle_case(**kwargs)
        preconstructed.append((name, c_base, f_base, c_anom, f_anom, overrides))

    for name, c_base, f_base, c_anom, f_anom, overrides in preconstructed:
        res_b1 = run_retained_check(f_base, c_base, pipeline)
        assert res_b1["factors"] == base_factors

        res_anom = run_retained_check(f_anom, c_anom, pipeline)
        assert res_anom["factors"] is None
        expected_ev_count = 3 if overrides.get("isolation") == "test-double" else 4
        assert len(res_anom["feedback"]["test_evidence"]) == expected_ev_count

        res_b2 = run_retained_check(f_base, c_base, pipeline)
        assert res_b2["factors"] == base_factors
        assert res_b2["signature"] == res_b1["signature"]


def test_structurally_invalid_rc0_and_surrogate() -> None:
    p_sha = "d" * 64
    base_receipt = gate.build_test_receipt(
        proposal_sha=p_sha,
        state="FAILED",
        returncode=1,
        stdout_tail="",
        stderr_tail=STDERR_GOOD_0,
        selectors=["tests/test_budget.py"],
        snapshot_files=0,
        snapshot_bytes=0,
        isolation="static-byte-profile",
    )
    rc0_receipt = copy.deepcopy(base_receipt)
    rc0_receipt["returncode"] = 0
    surr_receipt = copy.deepcopy(base_receipt)
    surr_receipt["stderr_tail"] = "\ud800"

    fixed_objects = [rc0_receipt, surr_receipt, p_sha]
    frame = RetainedCallFrame(fixed_objects, preflight=lambda: None)
    try:
        res_rc0_dir = frame.call(pipeline, "_byte_profile_failure_factors", (rc0_receipt,), None)
        assert res_rc0_dir is None

        with pytest.raises(SandboxError) as exc_rc0_sig:
            frame.call(pipeline, "test_failure_signature", (rc0_receipt,), None)
        assert "receipt state FAILED requires nonzero returncode" in str(exc_rc0_sig.value)

        with pytest.raises(SandboxError) as exc_rc0_fb:
            frame.call(pipeline, "test_gate_feedback", (rc0_receipt, p_sha), None)
        assert "receipt state FAILED requires nonzero returncode" in str(exc_rc0_fb.value)

        res_surr_dir = frame.call(pipeline, "_byte_profile_failure_factors", (surr_receipt,), None)
        assert res_surr_dir is None

        with pytest.raises(SandboxError) as exc_surr_sig:
            frame.call(pipeline, "test_failure_signature", (surr_receipt,), None)
        assert "valid UTF-8" in str(exc_surr_sig.value)

        with pytest.raises(SandboxError) as exc_surr_fb:
            frame.call(pipeline, "test_gate_feedback", (surr_receipt, p_sha), None)
        assert "valid UTF-8" in str(exc_surr_fb.value)
    finally:
        frame.assert_all_custody()


def test_non_collapse_distinct_outputs() -> None:
    p_sha = "e" * 64
    base_factors = ["tests/test_budget.py exceeds 12000 byte limit"]
    c_base, f_base = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=STDERR_GOOD_0,
        byte_factors=base_factors,
    )
    c_path, f_path = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=f"AssertionError: tests/test_other.py exceeds 12000 byte limit: 15853 bytes\n{SUMMARY_0}",
        byte_factors=["tests/test_other.py exceeds 12000 byte limit"],
    )
    c_cap, f_cap = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=f"AssertionError: tests/test_budget.py exceeds 13000 byte limit: 15853 bytes\n{SUMMARY_0}",
        byte_factors=["tests/test_budget.py exceeds 13000 byte limit"],
    )
    c_sel, f_sel = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=STDERR_GOOD_0,
        selectors=["tests/test_other.py"],
        byte_factors=base_factors,
    )
    c_iso, f_iso = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=STDERR_GOOD_0,
        isolation="test-double",
        byte_factors=None,
    )

    res_base = run_retained_check(f_base, c_base, pipeline)
    for c_var, f_var in [(c_path, f_path), (c_cap, f_cap), (c_sel, f_sel), (c_iso, f_iso)]:
        res_var = run_retained_check(f_var, c_var, pipeline)
        assert res_var["signature"] != res_base["signature"]
        assert res_var["feedback"] != res_base["feedback"]


def test_counterproof_guard_bypass() -> None:
    p_sha = "f" * 64
    c_good, f_good = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=STDERR_GOOD_0,
        byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
    )
    c_inv32, f_inv32 = build_oracle_case(
        proposal_sha=p_sha,
        stderr_tail=LINE_BASE + "\n" + PREFIX + "32",
        byte_factors=None,
    )
    run_retained_check(f_good, c_good, pipeline)
    run_retained_check(f_inv32, c_inv32, pipeline)

    orig_guard = pipeline._is_canonical_new_test_whitespace_summary
    pipeline._is_canonical_new_test_whitespace_summary = lambda summary_text: True
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_retained_check(f_inv32, c_inv32, pipeline)
        assert str(exc_info.value) == h._ERR_TYPED
    finally:
        pipeline._is_canonical_new_test_whitespace_summary = orig_guard
        f_good.assert_all_custody()
        f_inv32.assert_all_custody()

    run_retained_check(f_good, c_good, pipeline)
    run_retained_check(f_inv32, c_inv32, pipeline)


def test_large_retained_witness_and_cached_mutant_counterproof() -> None:
    assert len(PINNED_33_FACTORS) == 33
    assert len(PINNED_33_STDERR.encode("utf-8")) <= 8192
    assert len(PINNED_34_STDERR.encode("utf-8")) <= 8192

    p_sha = "9" * 64
    c_large33, f_large33 = build_oracle_case(
        proposal_sha=p_sha,
        selectors=[DEFAULT_SELECTOR],
        stderr_tail=PINNED_33_STDERR,
        byte_factors=PINNED_33_FACTORS,
    )
    c_large34, f_large34 = build_oracle_case(
        proposal_sha=p_sha,
        selectors=[DEFAULT_SELECTOR],
        stderr_tail=PINNED_34_STDERR,
        byte_factors=PINNED_33_FACTORS,
    )

    assert c_large33["payload_hash"] == PINNED_33_HASH
    assert c_large34["payload_hash"] == PINNED_33_HASH

    res33_1 = run_retained_check(f_large33, c_large33, pipeline)
    assert res33_1["factors"] == PINNED_33_FACTORS
    assert res33_1["signature"] == PINNED_33_HASH
    res33_2 = run_retained_check(f_large33, c_large33, pipeline)
    assert res33_2["factors"] == PINNED_33_FACTORS
    assert res33_2["signature"] == PINNED_33_HASH

    res34_1 = run_retained_check(f_large34, c_large34, pipeline)
    assert res34_1["factors"] == PINNED_33_FACTORS
    assert res34_1["signature"] == PINNED_33_HASH
    res34_2 = run_retained_check(f_large34, c_large34, pipeline)
    assert res34_2["factors"] == PINNED_33_FACTORS
    assert res34_2["signature"] == PINNED_33_HASH

    orig_factors = pipeline._byte_profile_failure_factors
    cached_result: Any = None

    def cached_factors(rcpt: Any) -> Any:
        nonlocal cached_result
        if cached_result is None:
            cached_result = orig_factors(rcpt)
        return cached_result

    pipeline._byte_profile_failure_factors = cached_factors
    try:
        run_retained_check(f_large34, c_large34, pipeline)
        with pytest.raises(AssertionError) as exc_info:
            run_retained_check(f_large34, c_large34, pipeline)
        assert str(exc_info.value) == _ERR_OWNERSHIP
    finally:
        pipeline._byte_profile_failure_factors = orig_factors
        f_large34.assert_all_custody()

    res34_3 = run_retained_check(f_large34, c_large34, pipeline)
    assert res34_3["factors"] == PINNED_33_FACTORS
