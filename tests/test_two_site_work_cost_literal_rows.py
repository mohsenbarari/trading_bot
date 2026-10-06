"""Canonical independent literal-row test fixture dependency."""

from __future__ import annotations

from typing import Any, Callable

from tests.test_two_site_work_cost_witness_helpers import (
    ForeignClassAllHooks,
    ForeignOpaqueLeaf,
    ForeignStrKey,
)

__all__ = [
    "make_full12_row",
    "make_nine_expected_rows",
    "make_nine_mutators",
    "make_raw_caller",
]


def _build_row(
    model: str = "gpt-6.1-sol",
    top_stage_id: str = "P1-00b",
    top_work_version: int = 1,
    nested_stage_id: str = "P1-00b",
    nested_work_version: int = 1,
    codex_in: int = 100,
    subtotal_in: int = 10,
    full_in: int = 10,
    pair_first: int = 1,
) -> dict[str, Any]:
    sem = {
        "stage_id": top_stage_id,
        "scenario_key": "literal-fixture",
        "gap_reference": "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md#6",
        "source_sha256": "d" * 64,
        "fingerprint": "e" * 64,
    }
    work = {
        "schema_version": top_work_version,
        "fingerprint": "f" * 64,
    }
    nested_sem = {
        "stage_id": nested_stage_id,
        "scenario_key": "literal-fixture",
        "gap_reference": "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md#6",
        "source_sha256": "d" * 64,
        "fingerprint": "e" * 64,
    }
    nested_work = {
        "schema_version": nested_work_version,
        "fingerprint": "f" * 64,
    }
    return {
        "schema_version": 1,
        "run_id": "fixture-run",
        "dimensions": {
            "model": model,
            "effort": "xhigh",
            "state": "CONTINUE",
            "finished_at": 5.0,
            "head_before": "a" * 40,
            "head_after": "b" * 40,
            "worker_run_id": "fixture-worker-0",
            "reviewer_run_id": "fixture-reviewer-0",
            "proposal_sha256": "c" * 64,
        },
        "semantic_identity": sem,
        "work_lineage": work,
        "provider_state": "OBSERVED_CONTROLLER_ATTEMPTS",
        "nested_identities": {
            "semantic": {"state": "VALID", "value": nested_sem},
            "work": {"state": "VALID", "value": nested_work},
        },
        "group_eligible": True,
        "observed_candidate": True,
        "codex_usage": {
            "input_tokens": codex_in,
            "cached_input_tokens": 80,
            "uncached_input_tokens": 20,
            "output_tokens": 10,
        },
        "provider_usage": {
            "observed_subtotal": {
                "input_tokens": subtotal_in,
                "output_tokens": 4,
                "total_tokens": 14,
            },
            "full_usage": {
                "input_tokens": full_in,
                "output_tokens": 4,
                "total_tokens": 14,
            },
            "attempt_pair": [pair_first, 1],
        },
        "reported_cycle_seconds": 5.0,
    }


def make_full12_row() -> dict[str, Any]:
    return _build_row()


def make_raw_caller() -> dict[Any, Any]:
    raw: dict[Any, Any] = {
        "caller_id": "fixture-caller",
        "nested": {"n10": 10},
    }
    raw[ForeignStrKey("kind")] = "opaque-key-value"
    raw["opaque"] = ForeignOpaqueLeaf()
    raw["meta"] = ForeignClassAllHooks
    return raw


def make_nine_expected_rows() -> list[dict[str, Any]]:
    return [
        _build_row(model="changed-model"),
        _build_row(top_stage_id="changed-stage"),
        _build_row(top_work_version=2),
        _build_row(nested_stage_id="changed-stage"),
        _build_row(nested_work_version=2),
        _build_row(codex_in=101),
        _build_row(subtotal_in=11),
        _build_row(full_in=11),
        _build_row(pair_first=2),
    ]


def _m0(row: dict[str, Any]) -> dict[str, Any]:
    row["dimensions"]["model"] = "changed-model"
    return row


def _m1(row: dict[str, Any]) -> dict[str, Any]:
    row["semantic_identity"]["stage_id"] = "changed-stage"
    return row


def _m2(row: dict[str, Any]) -> dict[str, Any]:
    row["work_lineage"]["schema_version"] = 2
    return row


def _m3(row: dict[str, Any]) -> dict[str, Any]:
    row["nested_identities"]["semantic"]["value"]["stage_id"] = "changed-stage"
    return row


def _m4(row: dict[str, Any]) -> dict[str, Any]:
    row["nested_identities"]["work"]["value"]["schema_version"] = 2
    return row


def _m5(row: dict[str, Any]) -> dict[str, Any]:
    row["codex_usage"]["input_tokens"] = 101
    return row


def _m6(row: dict[str, Any]) -> dict[str, Any]:
    row["provider_usage"]["observed_subtotal"]["input_tokens"] = 11
    return row


def _m7(row: dict[str, Any]) -> dict[str, Any]:
    row["provider_usage"]["full_usage"]["input_tokens"] = 11
    return row


def _m8(row: dict[str, Any]) -> dict[str, Any]:
    row["provider_usage"]["attempt_pair"][0] = 2
    return row


def make_nine_mutators() -> list[Callable[[dict[str, Any]], dict[str, Any]]]:
    return [_m0, _m1, _m2, _m3, _m4, _m5, _m6, _m7, _m8]


def test_literal_rows_module_exports() -> None:
    assert callable(make_full12_row)
    assert callable(make_raw_caller)
    assert callable(make_nine_expected_rows)
    assert callable(make_nine_mutators)
