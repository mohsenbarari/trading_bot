"""Contract tests for two-site work cost row fixtures and all-outcome gateway."""

from __future__ import annotations

import copy
import pytest
from typing import Any

from scripts import two_site_cycle_metrics as metrics
import tests.test_two_site_work_cost_row_fixtures as fixtures
from tests.test_two_site_work_cost_retained_call_frame import (
    RetainedCallFrame,
    _ERR_OWNERSHIP,
)
import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_lineage_consumer import (
    FROZEN_PROJECTION_A,
    FROZEN_PROJECTION_B,
    FROZEN_WORK_LINEAGE_A,
    FROZEN_WORK_LINEAGE_B,
    FROZEN_WORK_SHA,
)


def _build_raw_oracle(revision: str) -> dict[str, Any]:
    sem = copy.deepcopy(FROZEN_PROJECTION_A if revision == "A" else FROZEN_PROJECTION_B)
    work = copy.deepcopy(FROZEN_WORK_LINEAGE_A if revision == "A" else FROZEN_WORK_LINEAGE_B)
    return {
        "run_id": "fixture-raw",
        "model": "gpt-6.1-sol",
        "effort": "xhigh",
        "state": "CONTINUE",
        "finished_at": 5.0,
        "head_before": "a" * 40,
        "head_after": "b" * 40,
        "gemini_worker_run_id": "fixture-worker-0",
        "gemini_reviewer_run_id": "fixture-reviewer-0",
        "proposal_sha256": "c" * 64,
        "usage_state": "OBSERVED_COMPLETED_TURNS",
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "uncached_input_tokens": 20,
        "output_tokens": 10,
        "duration_seconds": 5.0,
        "semantic_identity": copy.deepcopy(sem),
        "work_lineage": copy.deepcopy(work),
        "provider_metrics": {
            "state": "OBSERVED_CONTROLLER_ATTEMPTS",
            "attempts": 1,
            "usage_observed_attempts": 1,
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "observed_usage_subtotal": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "semantic_identity": copy.deepcopy(sem),
            "work_lineage": copy.deepcopy(work),
        },
    }


def _build_normal_oracle(revision: str) -> dict[str, Any]:
    sem = copy.deepcopy(FROZEN_PROJECTION_A if revision == "A" else FROZEN_PROJECTION_B)
    work = copy.deepcopy(FROZEN_WORK_LINEAGE_A if revision == "A" else FROZEN_WORK_LINEAGE_B)
    return {
        "schema_version": 1,
        "run_id": "fixture-raw",
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
        "semantic_identity": copy.deepcopy(sem),
        "work_lineage": copy.deepcopy(work),
        "provider_state": "OBSERVED_CONTROLLER_ATTEMPTS",
        "nested_identities": {
            "semantic": {"state": "VALID", "value": copy.deepcopy(sem)},
            "work": {"state": "VALID", "value": copy.deepcopy(work)},
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
            "observed_subtotal": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "full_usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "attempt_pair": [1, 1],
        },
        "reported_cycle_seconds": 5.0,
    }


def _build_unknown_oracle() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": None,
        "dimensions": {
            "model": None,
            "effort": None,
            "state": None,
            "finished_at": None,
            "head_before": None,
            "head_after": None,
            "worker_run_id": None,
            "reviewer_run_id": None,
            "proposal_sha256": None,
        },
        "semantic_identity": None,
        "work_lineage": None,
        "provider_state": None,
        "nested_identities": {
            "semantic": {"state": "UNAVAILABLE", "value": None},
            "work": {"state": "UNAVAILABLE", "value": None},
        },
        "group_eligible": False,
        "observed_candidate": False,
        "codex_usage": None,
        "provider_usage": {
            "observed_subtotal": None,
            "full_usage": None,
            "attempt_pair": None,
        },
        "reported_cycle_seconds": None,
    }


ORACLE_RAW_A = _build_raw_oracle("A")
ORACLE_RAW_B = _build_raw_oracle("B")
ORACLE_NORMAL_A = _build_normal_oracle("A")
ORACLE_NORMAL_B = _build_normal_oracle("B")
ORACLE_UNKNOWN = _build_unknown_oracle()

INVALID_DICT: dict[str, Any] = {"bad": "revision"}
INVALID_LIST: list[Any] = ["bad_revision"]

ALL_INVALID_CASES: list[Any] = [
    "C",
    "INVALID",
    "",
    1,
    0,
    True,
    False,
    None,
    1.0,
    INVALID_DICT,
    INVALID_LIST,
    h.ForeignDict({"rev": "A"}),
    h.ForeignList(["A"]),
    h.ForeignStrKey("A"),
    h.ForeignDuration(1.0),
    h.ForeignFinished(1),
    h.ForeignClassAllHooks,
    h.ForeignClassUnhashable,
    h.ForeignOpaqueLeaf(),
]


def _preflight_validation() -> None:
    assert metrics._is_gemini_worker_run_id("fixture-worker-0") is True
    assert metrics._is_gemini_reviewer_run_id("fixture-reviewer-0") is True
    assert metrics.semantic_identity(FROZEN_PROJECTION_A) == FROZEN_PROJECTION_A
    assert metrics.semantic_identity(FROZEN_PROJECTION_B) == FROZEN_PROJECTION_B
    assert metrics.work_lineage(FROZEN_WORK_LINEAGE_A) == FROZEN_WORK_LINEAGE_A
    assert metrics.work_lineage(FROZEN_WORK_LINEAGE_B) == FROZEN_WORK_LINEAGE_B

    assert FROZEN_PROJECTION_A["source_sha256"] == "0839c67cebc17d313e5941d64c36f79d4465b30a670f79b7d84f0344998a3582"
    assert FROZEN_PROJECTION_B["source_sha256"] == "c3121fb027ccdf450c06ee58378b691d790e7210efb6d8b6c7cb26c3b887d22f"
    assert FROZEN_PROJECTION_A["source_sha256"] != FROZEN_PROJECTION_B["source_sha256"]
    assert FROZEN_PROJECTION_A["fingerprint"] == "639b7736ec264101534ee98ae545005940156236a991d942cbd590cb370faf11"
    assert FROZEN_PROJECTION_B["fingerprint"] == "438c0424c9f9604b269d6be92419ac6e572a0a1bd27088901780231e510c96cf"
    assert FROZEN_PROJECTION_A["fingerprint"] != FROZEN_PROJECTION_B["fingerprint"]
    assert FROZEN_WORK_LINEAGE_A["fingerprint"] == FROZEN_WORK_SHA
    assert FROZEN_WORK_LINEAGE_B["fingerprint"] == FROZEN_WORK_SHA
    assert FROZEN_WORK_LINEAGE_A["fingerprint"] == "41de8c6f7d2f61cc10a9a71beb9ef4d33e06b52e6a3dfdffd02266d2a32c10e2"


def _create_frame() -> RetainedCallFrame:
    fixed_roots = [
        FROZEN_PROJECTION_A,
        FROZEN_PROJECTION_B,
        FROZEN_WORK_LINEAGE_A,
        FROZEN_WORK_LINEAGE_B,
        ORACLE_RAW_A,
        ORACLE_RAW_B,
        ORACLE_NORMAL_A,
        ORACLE_NORMAL_B,
        ORACLE_UNKNOWN,
        INVALID_DICT,
        INVALID_LIST,
    ]
    return RetainedCallFrame(fixed_roots, _preflight_validation)


def _invoke(frame: RetainedCallFrame, method: str, args: tuple = (), expected: Any = None) -> Any:
    return frame.call(fixtures, method, args, expected)


def _run_constructor_witness(frame: RetainedCallFrame) -> tuple[Any, Any, Any, Any, Any]:
    raw_a = _invoke(frame, "make_valid_raw", ("A",), ORACLE_RAW_A)
    raw_b = _invoke(frame, "make_valid_raw", ("B",), ORACLE_RAW_B)
    norm_a = _invoke(frame, "make_expected_row", ("A",), ORACLE_NORMAL_A)
    norm_b = _invoke(frame, "make_expected_row", ("B",), ORACLE_NORMAL_B)
    unk = _invoke(frame, "make_unknown_scalar_expected", (), ORACLE_UNKNOWN)
    return raw_a, raw_b, norm_a, norm_b, unk


def test_exact_canonical_schema_key_sets() -> None:
    raw_19_keys = [
        "run_id", "model", "effort", "state", "finished_at", "head_before",
        "head_after", "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256",
        "usage_state", "input_tokens", "cached_input_tokens", "uncached_input_tokens",
        "output_tokens", "duration_seconds", "semantic_identity", "work_lineage",
        "provider_metrics",
    ]
    for ora in (ORACLE_RAW_A, ORACLE_RAW_B):
        assert list(ora.keys()) == raw_19_keys

    expected_top = [
        "schema_version", "run_id", "dimensions", "semantic_identity",
        "work_lineage", "provider_state", "nested_identities", "group_eligible",
        "observed_candidate", "codex_usage", "provider_usage", "reported_cycle_seconds",
    ]
    expected_dimensions = [
        "model", "effort", "state", "finished_at", "head_before",
        "head_after", "worker_run_id", "reviewer_run_id", "proposal_sha256",
    ]
    expected_nested = ["semantic", "work"]
    expected_codex = ["input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens"]
    expected_provider_usage = ["observed_subtotal", "full_usage", "attempt_pair"]
    expected_cost_map = ["input_tokens", "output_tokens", "total_tokens"]

    for ora in (ORACLE_NORMAL_A, ORACLE_NORMAL_B):
        assert list(ora.keys()) == expected_top
        assert list(ora["dimensions"].keys()) == expected_dimensions
        assert list(ora["nested_identities"].keys()) == expected_nested
        assert list(ora["nested_identities"]["semantic"].keys()) == ["state", "value"]
        assert list(ora["nested_identities"]["work"].keys()) == ["state", "value"]
        assert list(ora["codex_usage"].keys()) == expected_codex
        assert list(ora["provider_usage"].keys()) == expected_provider_usage
        assert list(ora["provider_usage"]["observed_subtotal"].keys()) == expected_cost_map
        assert list(ora["provider_usage"]["full_usage"].keys()) == expected_cost_map

    assert list(ORACLE_UNKNOWN.keys()) == expected_top
    assert list(ORACLE_UNKNOWN["dimensions"].keys()) == expected_dimensions
    assert list(ORACLE_UNKNOWN["nested_identities"].keys()) == expected_nested
    assert list(ORACLE_UNKNOWN["provider_usage"].keys()) == expected_provider_usage

    forbidden = {"nested", "group", "candidate", "codex", "reported", "full", "sub", "pair"}
    assert forbidden.isdisjoint(set(expected_top))
    assert forbidden.isdisjoint(set(expected_dimensions))


def test_retained_call_frame_preflight_and_fixed_custody() -> None:
    frame = _create_frame()
    frame.assert_all_custody()
    assert len(frame._orig_fixed_items) <= 64


def test_baseline_constructors_and_detachment() -> None:
    frame = _create_frame()
    raw_a1, raw_b1, norm_a1, norm_b1, unk1 = _run_constructor_witness(frame)
    raw_a2, raw_b2, norm_a2, norm_b2, unk2 = _run_constructor_witness(frame)

    assert raw_a1 is not raw_a2
    assert raw_b1 is not raw_b2
    assert norm_a1 is not norm_a2
    assert norm_b1 is not norm_b2
    assert unk1 is not unk2

    assert raw_a1["semantic_identity"] is not raw_a1["provider_metrics"]["semantic_identity"]
    assert raw_a1["work_lineage"] is not raw_a1["provider_metrics"]["work_lineage"]
    assert raw_a1["provider_metrics"]["usage"] is not raw_a1["provider_metrics"]["observed_usage_subtotal"]

    assert norm_a1["semantic_identity"] is not norm_a1["nested_identities"]["semantic"]["value"]
    assert norm_a1["work_lineage"] is not norm_a1["nested_identities"]["work"]["value"]
    assert norm_a1["provider_usage"]["observed_subtotal"] is not norm_a1["provider_usage"]["full_usage"]

    frame.assert_all_custody()


def _is_in_tree(target: Any, root: Any) -> bool:
    if target is root:
        return True
    if type(root) is dict:
        return any(_is_in_tree(target, v) for v in root.values())
    if type(root) is list:
        return any(_is_in_tree(target, x) for x in root)
    return False


def test_mutation_isolation_experiment_batch() -> None:
    exp_frame = _create_frame()
    b_raw_a, b_raw_b, b_norm_a, b_norm_b, b_unk = _run_constructor_witness(exp_frame)

    batch_outputs = [b_raw_a, b_raw_b, b_norm_a, b_norm_b, b_unk]
    batch_oracles = [ORACLE_RAW_A, ORACLE_RAW_B, ORACLE_NORMAL_A, ORACLE_NORMAL_B, ORACLE_UNKNOWN]

    targets = [
        b_raw_a,
        b_raw_a["semantic_identity"],
        b_raw_a["work_lineage"],
        b_raw_a["provider_metrics"],
        b_raw_a["provider_metrics"]["usage"],
        b_raw_a["provider_metrics"]["observed_usage_subtotal"],
        b_raw_a["provider_metrics"]["semantic_identity"],
        b_raw_a["provider_metrics"]["work_lineage"],
        b_norm_a,
        b_norm_a["dimensions"],
        b_norm_a["semantic_identity"],
        b_norm_a["work_lineage"],
        b_norm_a["nested_identities"],
        b_norm_a["nested_identities"]["semantic"],
        b_norm_a["nested_identities"]["semantic"]["value"],
        b_norm_a["nested_identities"]["work"],
        b_norm_a["nested_identities"]["work"]["value"],
        b_norm_a["codex_usage"],
        b_norm_a["provider_usage"],
        b_norm_a["provider_usage"]["observed_subtotal"],
        b_norm_a["provider_usage"]["full_usage"],
        b_norm_a["provider_usage"]["attempt_pair"],
        b_unk,
        b_unk["dimensions"],
        b_unk["nested_identities"],
        b_unk["nested_identities"]["semantic"],
        b_unk["nested_identities"]["work"],
        b_unk["provider_usage"],
    ]

    fixed_objects = exp_frame._orig_fixed_items
    frozen_snaps = [h.capture_custody(obj) for obj in fixed_objects]

    for target in targets:
        target_root_idx = next(i for i, out in enumerate(batch_outputs) if _is_in_tree(target, out))
        target_root = batch_outputs[target_root_idx]
        target_oracle = batch_oracles[target_root_idx]

        is_list = type(target) is list
        if is_list:
            target.append(9999)
        else:
            target["__mutated_test_key__"] = True

        try:
            for i, (peer, oracle) in enumerate(zip(batch_outputs, batch_oracles)):
                if i != target_root_idx:
                    h.assert_exact_typed(peer, oracle)

            for obj, snap in zip(fixed_objects, frozen_snaps):
                h.assert_custody_unchanged(obj, snap)
        finally:
            if is_list:
                target.pop()
            else:
                del target["__mutated_test_key__"]

        h.assert_exact_typed(target_root, target_oracle)
        exp_frame.assert_all_custody()


def test_constructor_fault1_cached_tokens_mismatch() -> None:
    frame = _create_frame()
    _run_constructor_witness(frame)

    orig_make_expected = fixtures.make_expected_row
    try:
        def faulty_make_expected_row(revision: str = "A") -> dict[str, Any]:
            res = orig_make_expected(revision)
            if revision == "A":
                res["codex_usage"]["cached_input_tokens"] = 79
            return res

        fixtures.make_expected_row = faulty_make_expected_row
        with pytest.raises(AssertionError, match=h._ERR_TYPED):
            _run_constructor_witness(frame)
    finally:
        fixtures.make_expected_row = orig_make_expected

    _run_constructor_witness(frame)
    frame.assert_all_custody()


def test_constructor_fault2_full_subtotal_alias() -> None:
    frame = _create_frame()
    _run_constructor_witness(frame)

    orig_make_expected = fixtures.make_expected_row
    try:
        def faulty_alias_make_expected_row(revision: str = "A") -> dict[str, Any]:
            res = orig_make_expected(revision)
            if revision == "A":
                res["provider_usage"]["full_usage"] = res["provider_usage"]["observed_subtotal"]
            return res

        fixtures.make_expected_row = faulty_alias_make_expected_row
        with pytest.raises(AssertionError, match=_ERR_OWNERSHIP):
            _run_constructor_witness(frame)
    finally:
        fixtures.make_expected_row = orig_make_expected

    _run_constructor_witness(frame)
    frame.assert_all_custody()


def test_constructor_fault3_mutating_prior_return() -> None:
    frame = _create_frame()
    raw_a, _, _, _, _ = _run_constructor_witness(frame)
    orig_model = raw_a["model"]

    orig_make_expected = fixtures.make_expected_row
    try:
        def faulty_mutating_constructor(revision: str = "A") -> dict[str, Any]:
            res = orig_make_expected(revision)
            if revision == "A":
                raw_a["model"] = "mutated_model"
            return res

        fixtures.make_expected_row = faulty_mutating_constructor
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            _run_constructor_witness(frame)
    finally:
        fixtures.make_expected_row = orig_make_expected
        raw_a["model"] = orig_model

    _run_constructor_witness(frame)
    frame.assert_all_custody()


def test_rejected_outcomes_counterproof_gateway() -> None:
    frame = _create_frame()
    _run_constructor_witness(frame)

    def _run_rejected_outcomes(frm: RetainedCallFrame) -> None:
        with h.armed_hooks():
            for bad_rev in ALL_INVALID_CASES:
                with pytest.raises(ValueError, match="invalid revision"):
                    _invoke(frm, "make_valid_raw", (bad_rev,), None)
                with pytest.raises(ValueError, match="invalid revision"):
                    _invoke(frm, "make_expected_row", (bad_rev,), None)
            assert h.get_total_hook_calls() == 0
            frm.assert_all_custody()

    _run_rejected_outcomes(frame)

    orig_make_valid_raw = fixtures.make_valid_raw
    orig_model = ORACLE_NORMAL_A["dimensions"]["model"]
    try:
        def faulty_make_valid_raw(revision: Any = "A") -> Any:
            ORACLE_NORMAL_A["dimensions"]["model"] = "mutated_by_rejected"
            raise ValueError("invalid revision")

        fixtures.make_valid_raw = faulty_make_valid_raw
        with pytest.raises(AssertionError, match=h._ERR_CUSTODY):
            _run_rejected_outcomes(frame)
    finally:
        fixtures.make_valid_raw = orig_make_valid_raw
        ORACLE_NORMAL_A["dimensions"]["model"] = orig_model

    _run_rejected_outcomes(frame)
    frame.assert_all_custody()
