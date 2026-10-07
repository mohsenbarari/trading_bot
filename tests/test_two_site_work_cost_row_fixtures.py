"""Canonical row fixtures for two-site work cost lineage."""

from __future__ import annotations

import copy
from typing import Any

import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_lineage_consumer import (
    FROZEN_PROJECTION_A,
    FROZEN_PROJECTION_B,
    FROZEN_WORK_LINEAGE_A,
    FROZEN_WORK_LINEAGE_B,
)

__all__ = [
    "h",
    "make_expected_row",
    "make_unknown_scalar_expected",
    "make_valid_raw",
]

_INVALID_REVISION = "invalid revision"


def make_valid_raw(revision: str = "A") -> dict[str, Any]:
    if type(revision) is not str or revision not in ("A", "B"):
        raise ValueError(_INVALID_REVISION)
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


def make_expected_row(revision: str = "A") -> dict[str, Any]:
    if type(revision) is not str or revision not in ("A", "B"):
        raise ValueError(_INVALID_REVISION)
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


def make_unknown_scalar_expected() -> dict[str, Any]:
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


def test_row_fixtures_exports() -> None:
    assert callable(make_valid_raw)
    assert callable(make_expected_row)
    assert callable(make_unknown_scalar_expected)
    assert h is not None
