from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

import scripts.two_site_proposal_pipeline as pipeline
from scripts.two_site_pre_review_receipt import EXPECTED_RECEIPT_KEYS, validate_test_receipt
from scripts.two_site_agent_sandbox import SandboxError
import tests.test_two_site_pipeline_pre_review_gate as gate
from tests.test_two_site_pipeline_pre_review_gate import DEFAULT_SELECTOR
from tests.test_two_site_work_cost_retained_call_frame import RetainedCallFrame, _ERR_OWNERSHIP
import tests.test_two_site_work_cost_witness_helpers as h

SENTINEL_ERR = "forbidden module call during setup"
FORBIDDEN_METHODS = (
    "_byte_profile_failure_factors",
    "test_failure_facts",
    "test_failure_signature",
    "test_gate_feedback",
)


def collapse_utf8_prefix_facts(stdout_tail: str, stderr_tail: str) -> dict[str, list[str]]:
    combined = stdout_tail + "\n" + stderr_tail
    compact = " ".join(combined.split())
    raw = compact.encode("utf-8")[:2048].decode("utf-8", errors="ignore")
    exceptions = [raw] if raw else []
    return {"exceptions": exceptions, "statements": [], "tests": []}


def build_expected_payload(
    returncode: int,
    selectors: list[str],
    facts: dict[str, list[str]],
    byte_factors: list[str] | None = None,
) -> dict[str, Any]:
    if byte_factors is not None:
        return {
            "returncode": returncode,
            "selectors": list(selectors),
            "facts": {"byte_budget_factors": sorted(byte_factors)},
            "isolation": "static-byte-profile",
        }
    return {
        "returncode": returncode,
        "selectors": list(selectors),
        "facts": {k: sorted(v) for k, v in facts.items()},
    }


def hash_canonical_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().lower()


def build_expected_feedback(
    proposal_sha: str,
    returncode: int,
    selectors: list[str],
    facts: dict[str, list[str]],
    *,
    isolation: str = "test-double",
    raw_stderr: str = "",
) -> dict[str, Any]:
    n_sel = len(selectors)
    if n_sel <= 10:
        sel_sample = ", ".join(selectors)
    else:
        sel_sample = ", ".join(selectors[:10]) + f" ... ({n_sel - 10} more)"
    causes = (
        facts.get("exceptions", [])[:3]
        or facts.get("tests", [])[:3]
        or facts.get("statements", [])[:3]
    )
    cause_text = "; ".join(causes)
    finding = (
        f"Pre-review candidate tests failed with exit code {returncode}. "
        f"Resolve every occurrence of the bounded root cause, not only the displayed line: {cause_text}"
    )
    fb = finding.encode("utf-8")
    if len(fb) > 4096:
        finding = fb[:4096].decode("utf-8", errors="ignore")
    facts_text = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    evidence = [
        f"returncode: {returncode}",
        f"selectors (count={n_sel}): {sel_sample}",
        "normalized_failure_facts: " + facts_text,
    ]
    if isolation == "static-byte-profile":
        evidence.append(raw_stderr)
    return {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": proposal_sha,
        "findings": [finding],
        "test_evidence": evidence,
    }


@contextmanager
def forbidden_module_sentinel(module: Any):
    attempts: list[str] = []
    originals = {name: getattr(module, name) for name in FORBIDDEN_METHODS}

    def make_trap(name: str):
        def _trap(*args: Any, **kwargs: Any) -> Any:
            attempts.append(name)
            raise AssertionError(f"{SENTINEL_ERR}: {name}")
        return _trap

    for name in FORBIDDEN_METHODS:
        setattr(module, name, make_trap(name))
    try:
        yield attempts
    finally:
        for name, orig in originals.items():
            setattr(module, name, orig)
        if attempts:
            raise AssertionError(f"{SENTINEL_ERR}: {attempts[0]}")


def build_oracle_case(
    *,
    proposal_sha: str = "a" * 64,
    returncode: int = 1,
    stdout_tail: str = "",
    stderr_tail: str = "AssertionError: tests/test_budget.py exceeds 12000 byte limit: 15853 bytes",
    selectors: list[str] | None = None,
    unsupported_specs: list[str] | None = None,
    snapshot_files: int = 0,
    snapshot_bytes: int = 0,
    isolation: str = "static-byte-profile",
    byte_factors: list[str] | None = None,
    module: Any = pipeline,
    facts_builder: Callable[[str, str], dict[str, list[str]]] = collapse_utf8_prefix_facts,
    payload_builder: Callable[..., dict[str, Any]] = build_expected_payload,
) -> tuple[dict[str, Any], RetainedCallFrame]:
    with forbidden_module_sentinel(module):
        if selectors is None:
            selectors = ["tests/test_budget.py"]
        if unsupported_specs is None:
            unsupported_specs = []
        receipt = gate.build_test_receipt(
            proposal_sha=proposal_sha,
            state="FAILED",
            returncode=returncode,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            selectors=list(selectors),
            unsupported_specs=list(unsupported_specs),
            snapshot_files=snapshot_files,
            snapshot_bytes=snapshot_bytes,
            isolation=isolation,
        )
        assert set(receipt.keys()) == EXPECTED_RECEIPT_KEYS
        validate_test_receipt(receipt, proposal_sha)
        facts = facts_builder(stdout_tail, stderr_tail)
        payload = payload_builder(returncode, receipt["selectors"], facts, byte_factors=byte_factors)
        payload_hash = hash_canonical_payload(payload)
        feedback = build_expected_feedback(
            proposal_sha,
            returncode,
            receipt["selectors"],
            facts,
            isolation=isolation,
            raw_stderr=stderr_tail,
        )
        factors_oracle: list[str] | None = list(byte_factors) if byte_factors is not None else None
        fixed_objects = [
            receipt,
            receipt["selectors"],
            factors_oracle,
            payload,
            payload_hash,
            feedback,
        ]
        frame = RetainedCallFrame(fixed_objects, preflight=lambda: None)
        case = {
            "proposal_sha": proposal_sha,
            "receipt": receipt,
            "selectors": receipt["selectors"],
            "factors": factors_oracle,
            "payload": payload,
            "payload_hash": payload_hash,
            "whole_feedback": feedback,
        }
        return case, frame


def run_retained_check(
    frame: RetainedCallFrame,
    case: dict[str, Any],
    module: Any = pipeline,
) -> dict[str, Any]:
    try:
        ret_factors = frame.call(
            module,
            "_byte_profile_failure_factors",
            (case["receipt"],),
            case["factors"],
        )
        ret_sig = frame.call(
            module,
            "test_failure_signature",
            (case["receipt"],),
            case["payload_hash"],
        )
        ret_fb = frame.call(
            module,
            "test_gate_feedback",
            (case["receipt"], case["proposal_sha"]),
            case["whole_feedback"],
        )
        return {
            "factors": ret_factors,
            "signature": ret_sig,
            "feedback": ret_fb,
        }
    finally:
        frame.assert_all_custody()


def assert_no_callable_aliases(source: Path | str) -> None:
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and "import " not in source):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = source
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_METHODS or alias.asname in FORBIDDEN_METHODS:
                    raise AssertionError(f"forbidden direct import: {alias.name}")


def test_pinned_literals_and_source_valid_cases() -> None:
    p_sha = "a" * 64
    sel = ["tests/test_budget.py"]
    raw_err = "AssertionError: tests/test_budget.py exceeds 12000 byte limit: 15853 bytes"
    pinned_byte_fb = {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": p_sha,
        "findings": [
            "Pre-review candidate tests failed with exit code 1. "
            f"Resolve every occurrence of the bounded root cause, not only the displayed line: {raw_err}"
        ],
        "test_evidence": [
            "returncode: 1",
            "selectors (count=1): tests/test_budget.py",
            f'normalized_failure_facts: {{"exceptions":["{raw_err}"],"statements":[],"tests":[]}}',
            raw_err,
        ],
    }
    case_byte, frame_byte = build_oracle_case(
        proposal_sha=p_sha,
        selectors=sel,
        stderr_tail=raw_err,
        byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
    )
    assert case_byte["whole_feedback"] == pinned_byte_fb
    res_byte = run_retained_check(frame_byte, case_byte, pipeline)
    assert res_byte["feedback"] == pinned_byte_fb

    raw_32 = raw_err + "\nNew-test trailing whitespace at sorted change indices: 32"
    cause_32 = raw_err + " New-test trailing whitespace at sorted change indices: 32"
    pinned_32_fb = {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": p_sha,
        "findings": [
            "Pre-review candidate tests failed with exit code 1. "
            f"Resolve every occurrence of the bounded root cause, not only the displayed line: {cause_32}"
        ],
        "test_evidence": [
            "returncode: 1",
            "selectors (count=1): tests/test_budget.py",
            f'normalized_failure_facts: {{"exceptions":["{cause_32}"],"statements":[],"tests":[]}}',
            raw_32,
        ],
    }
    case_32, frame_32 = build_oracle_case(
        proposal_sha=p_sha,
        selectors=sel,
        stderr_tail=raw_32,
        byte_factors=None,
    )
    assert case_32["whole_feedback"] == pinned_32_fb
    res_32 = run_retained_check(frame_32, case_32, pipeline)
    assert res_32["feedback"] == pinned_32_fb


def test_single_field_anomalies_baseline_anomaly_same_baseline() -> None:
    base_kwargs: dict[str, Any] = {
        "proposal_sha": "c" * 64,
        "returncode": 1,
        "stdout_tail": "",
        "stderr_tail": "AssertionError: tests/test_budget.py exceeds 12000 byte limit: 15853 bytes",
        "snapshot_files": 0,
        "snapshot_bytes": 0,
        "isolation": "static-byte-profile",
        "byte_factors": ["tests/test_budget.py exceeds 12000 byte limit"],
    }
    anomalies: list[dict[str, Any]] = [
        {"stdout_tail": "diagnostic stdout", "byte_factors": None},
        {"snapshot_files": 1, "byte_factors": None},
        {"snapshot_bytes": 100, "byte_factors": None},
        {"returncode": 2, "byte_factors": None},
        {"isolation": "test-double", "byte_factors": None},
    ]
    for anomaly_override in anomalies:
        c_base, f_base = build_oracle_case(**base_kwargs)
        run_retained_check(f_base, c_base, pipeline)

        merged = dict(base_kwargs)
        merged.update(anomaly_override)
        c_anom, f_anom = build_oracle_case(**merged)
        expected_ev_count = 3 if merged.get("isolation") == "test-double" else 4
        assert len(c_anom["whole_feedback"]["test_evidence"]) == expected_ev_count
        run_retained_check(f_anom, c_anom, pipeline)

        run_retained_check(f_base, c_base, pipeline)


def test_multibyte_prefix_empty_cause_and_selectors_sample() -> None:
    persian_text = (chr(0x200C) + "خطا" + chr(0x200D) + "گزارش") * 250
    c_mb, f_mb = build_oracle_case(
        proposal_sha="d" * 64,
        stderr_tail=persian_text,
        isolation="test-double",
        byte_factors=None,
    )
    run_retained_check(f_mb, c_mb, pipeline)

    c_empty, f_empty = build_oracle_case(
        proposal_sha="e" * 64,
        stdout_tail="",
        stderr_tail="   ",
        isolation="test-double",
        byte_factors=None,
    )
    assert c_empty["whole_feedback"]["findings"][0].endswith(": ")
    run_retained_check(f_empty, c_empty, pipeline)

    many_selectors = [f"tests/test_sample_{i}.py" for i in range(12)]
    c_many, f_many = build_oracle_case(
        proposal_sha="f" * 64,
        selectors=many_selectors,
        isolation="test-double",
        byte_factors=None,
    )
    assert "(2 more)" in c_many["whole_feedback"]["test_evidence"][1]
    run_retained_check(f_many, c_many, pipeline)


def test_counterproof_cached_recognizer_and_caller_owned_feedback() -> None:
    case, frame = build_oracle_case(
        proposal_sha="9" * 64,
        byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
    )
    run_retained_check(frame, case, pipeline)

    orig_factors = pipeline._byte_profile_failure_factors
    cached_result: Any = None

    def cached_factors(rcpt: Any) -> Any:
        nonlocal cached_result
        if cached_result is None:
            cached_result = orig_factors(rcpt)
        return cached_result

    pipeline._byte_profile_failure_factors = cached_factors
    try:
        run_retained_check(frame, case, pipeline)
        with pytest.raises(AssertionError) as exc_info:
            run_retained_check(frame, case, pipeline)
        assert str(exc_info.value) == _ERR_OWNERSHIP
    finally:
        pipeline._byte_profile_failure_factors = orig_factors
        frame.assert_all_custody()

    run_retained_check(frame, case, pipeline)

    orig_fb = pipeline.test_gate_feedback

    def caller_owned_feedback(rcpt: Any, p_sha: str) -> Any:
        _ = orig_fb(rcpt, p_sha)
        return case["whole_feedback"]

    pipeline.test_gate_feedback = caller_owned_feedback
    try:
        with pytest.raises(AssertionError) as exc_info:
            run_retained_check(frame, case, pipeline)
        assert str(exc_info.value) == _ERR_OWNERSHIP
    finally:
        pipeline.test_gate_feedback = orig_fb
        frame.assert_all_custody()

    run_retained_check(frame, case, pipeline)
