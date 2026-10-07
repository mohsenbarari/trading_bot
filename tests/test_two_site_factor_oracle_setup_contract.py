from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import scripts.two_site_proposal_pipeline as pipeline
from scripts.two_site_agent_sandbox import SandboxError
import tests.test_two_site_pipeline_pre_review_gate as gate
from tests.test_two_site_pipeline_pre_review_gate import DEFAULT_SELECTOR
from tests.test_two_site_work_cost_retained_call_frame import RetainedCallFrame
import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_factor_witness_contract import (
    FORBIDDEN_METHODS,
    SENTINEL_ERR,
    assert_no_callable_aliases,
    build_expected_payload,
    build_oracle_case,
    run_retained_check,
)


def test_setup_sentinel_negative_rejection_and_restoration() -> None:
    sdk_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    orig_build_test_receipt = gate.build_test_receipt

    def spy_build_test_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
        sdk_calls.append((args, kwargs))
        return orig_build_test_receipt(*args, **kwargs)

    gate.build_test_receipt = spy_build_test_receipt
    try:
        clean_case, clean_frame = build_oracle_case(
            proposal_sha="7" * 64,
            byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
        )
        assert len(sdk_calls) == 1
        call_args, call_kwargs = sdk_calls[0]
        assert call_kwargs["proposal_sha"] == "7" * 64
        assert call_kwargs["state"] == "FAILED"
        assert call_kwargs["returncode"] == 1
        assert call_kwargs["isolation"] == "static-byte-profile"
    finally:
        gate.build_test_receipt = orig_build_test_receipt

    run_retained_check(clean_frame, clean_case, pipeline)

    orig_callbacks = {name: getattr(pipeline, name) for name in FORBIDDEN_METHODS}
    base_receipt = dict(clean_case["receipt"])
    for forbidden_name in FORBIDDEN_METHODS:
        def faulty_payload_builder(*args: Any, **kwargs: Any) -> dict[str, Any]:
            getattr(pipeline, forbidden_name)(base_receipt)
            return build_expected_payload(*args, **kwargs)

        snap = h.capture_custody(base_receipt)
        with pytest.raises(AssertionError) as exc_info:
            build_oracle_case(
                proposal_sha="7" * 64,
                byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
                payload_builder=faulty_payload_builder,
            )
        err_msg = str(exc_info.value)
        assert SENTINEL_ERR in err_msg
        assert forbidden_name in err_msg
        h.assert_custody_unchanged(base_receipt, snap)

        for name in FORBIDDEN_METHODS:
            assert getattr(pipeline, name) is orig_callbacks[name]

    clean_case2, clean_frame2 = build_oracle_case(
        proposal_sha="7" * 64,
        byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
    )
    run_retained_check(clean_frame2, clean_case2, pipeline)

    bad_imports = [
        "from scripts.two_site_proposal_pipeline import test_failure_facts",
        "from scripts.two_site_proposal_pipeline import _byte_profile_failure_factors as bp",
        "from scripts.two_site_proposal_pipeline import test_failure_signature",
        "from scripts.two_site_proposal_pipeline import test_gate_feedback as tgf",
    ]
    for bad_code in bad_imports:
        with pytest.raises(AssertionError) as exc_info:
            assert_no_callable_aliases(bad_code)
        assert "forbidden direct import" in str(exc_info.value)

    assert_no_callable_aliases(Path(__file__))
    assert_no_callable_aliases(Path(__file__).parent / "test_two_site_factor_witness_contract.py")


def test_public_exceptions_rc0_and_surrogate() -> None:
    p_sha = "8" * 64
    base = gate.build_test_receipt(
        proposal_sha=p_sha,
        state="FAILED",
        returncode=1,
        stdout_tail="",
        stderr_tail="AssertionError: tests/test_budget.py exceeds 12000 byte limit: 15853 bytes",
        selectors=["tests/test_budget.py"],
        snapshot_files=0,
        snapshot_bytes=0,
        isolation="static-byte-profile",
    )
    rc0_receipt = copy.deepcopy(base)
    rc0_receipt["returncode"] = 0
    surrogate_receipt = copy.deepcopy(base)
    surrogate_receipt["stderr_tail"] = "\ud800"

    fixed_objects = [rc0_receipt, surrogate_receipt, p_sha]
    frame = RetainedCallFrame(fixed_objects, preflight=lambda: None)

    res_rc0_dir = frame.call(pipeline, "_byte_profile_failure_factors", (rc0_receipt,), None)
    assert res_rc0_dir is None

    try:
        with pytest.raises(SandboxError) as exc_info:
            frame.call(pipeline, "test_failure_signature", (rc0_receipt,), None)
        assert "receipt state FAILED requires nonzero returncode" in str(exc_info.value)
    finally:
        frame.assert_all_custody()

    try:
        with pytest.raises(SandboxError) as exc_info:
            frame.call(pipeline, "test_gate_feedback", (rc0_receipt, p_sha), None)
        assert "receipt state FAILED requires nonzero returncode" in str(exc_info.value)
    finally:
        frame.assert_all_custody()

    res_surr_dir = frame.call(pipeline, "_byte_profile_failure_factors", (surrogate_receipt,), None)
    assert res_surr_dir is None

    try:
        with pytest.raises(SandboxError) as exc_info:
            frame.call(pipeline, "test_failure_signature", (surrogate_receipt,), None)
        assert "valid UTF-8" in str(exc_info.value)
    finally:
        frame.assert_all_custody()

    try:
        with pytest.raises(SandboxError) as exc_info:
            frame.call(pipeline, "test_gate_feedback", (surrogate_receipt, p_sha), None)
        assert "valid UTF-8" in str(exc_info.value)
    finally:
        frame.assert_all_custody()


def test_fixed_object_identity_and_custody_mutation_detection() -> None:
    case, frame = build_oracle_case(
        proposal_sha="5" * 64,
        byte_factors=["tests/test_budget.py exceeds 12000 byte limit"],
    )
    assert case["receipt"] is frame._fixed_objects[0]
    assert case["selectors"] is frame._fixed_objects[1]
    assert case["factors"] is frame._fixed_objects[2]
    assert case["payload"] is frame._fixed_objects[3]
    assert case["payload_hash"] is frame._fixed_objects[4]
    assert case["whole_feedback"] is frame._fixed_objects[5]

    case["receipt"]["returncode"] = 99
    with pytest.raises(AssertionError) as exc_info:
        frame.assert_all_custody()
    assert str(exc_info.value) == h._ERR_CUSTODY
    case["receipt"]["returncode"] = 1
    frame.assert_all_custody()

    case["selectors"].append("tests/test_extra.py")
    with pytest.raises(AssertionError) as exc_info:
        frame.assert_all_custody()
    assert str(exc_info.value) == h._ERR_CUSTODY
    case["selectors"].pop()
    frame.assert_all_custody()

    case["whole_feedback"]["findings"].append("extra finding")
    with pytest.raises(AssertionError) as exc_info:
        frame.assert_all_custody()
    assert str(exc_info.value) == h._ERR_CUSTODY
    case["whole_feedback"]["findings"].pop()
    frame.assert_all_custody()

    case["payload"]["selectors"].append("tests/test_extra.py")
    with pytest.raises(AssertionError) as exc_info:
        frame.assert_all_custody()
    assert str(exc_info.value) == h._ERR_CUSTODY
    case["payload"]["selectors"].pop()
    frame.assert_all_custody()

    res = run_retained_check(frame, case, pipeline)
    frame.assert_all_custody()
    res["factors"].append("mutated_factor")
    with pytest.raises(AssertionError) as exc_info:
        frame.assert_all_custody()
    assert str(exc_info.value) == h._ERR_CUSTODY
    res["factors"].pop()
    frame.assert_all_custody()
