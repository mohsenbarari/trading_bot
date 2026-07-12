#!/usr/bin/env python3
"""Bind Stage 9 registry and transition rows to observed exact test results."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    from scripts.run_stage9_test_matrix import _observed_test_results
except ModuleNotFoundError:  # Direct script execution keeps only scripts/ on sys.path.
    from run_stage9_test_matrix import _observed_test_results


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^stage9_evidence_commit=([0-9a-f]{40})$", re.MULTILINE)
RAN_TESTS_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
SKIPPED_RE = re.compile(r"skipped=(\d+)")


class Stage9RuntimeEvidenceError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Stage9RuntimeEvidenceError(f"object_required:{path}")
    return payload


def _read_integration_log(path: Path, *, expected_commit: str) -> dict[str, object]:
    output = path.read_text(encoding="utf-8")
    commits = set(COMMIT_RE.findall(output))
    if commits != {expected_commit}:
        raise Stage9RuntimeEvidenceError(
            f"integration_log_commit_mismatch:{path}:expected={expected_commit}:observed={sorted(commits)}"
        )
    observed = _observed_test_results(output)
    count = sum(int(value) for value in RAN_TESTS_RE.findall(output))
    skipped = sum(int(value) for value in SKIPPED_RE.findall(output))
    if not observed or count <= 0:
        raise Stage9RuntimeEvidenceError(f"integration_log_has_no_tests:{path}")
    if len(observed) != count:
        raise Stage9RuntimeEvidenceError(
            f"integration_test_count_mismatch:{path}:declared={count}:observed={len(observed)}"
        )
    if skipped:
        raise Stage9RuntimeEvidenceError(f"integration_log_contains_skips:{path}:{skipped}")
    failed = sorted(test_id for test_id, status in observed.items() if status != "passed")
    if failed:
        raise Stage9RuntimeEvidenceError(f"integration_tests_not_passed:{path}:{failed}")
    return {
        "path": str(path),
        "commit": expected_commit,
        "test_count": count,
        "skipped": skipped,
        "observed_tests": observed,
    }


def _lane_observed(matrix: dict[str, object]) -> tuple[dict[str, str], dict[str, str]]:
    lanes = matrix.get("lanes")
    if not isinstance(lanes, dict):
        raise Stage9RuntimeEvidenceError("matrix_lanes_required")
    all_observed: dict[str, str] = {}
    sources: dict[str, str] = {}
    for lane_name, lane in lanes.items():
        if not isinstance(lane, dict) or lane.get("status") != "passed":
            raise Stage9RuntimeEvidenceError(f"matrix_lane_not_passed:{lane_name}")
        if int(lane.get("skipped", 0) or 0):
            raise Stage9RuntimeEvidenceError(f"matrix_lane_contains_skips:{lane_name}")
        observed = lane.get("observed_tests")
        if not isinstance(observed, dict) or not observed:
            raise Stage9RuntimeEvidenceError(f"matrix_lane_observed_tests_required:{lane_name}")
        for test_id, status in observed.items():
            normalized_id = str(test_id)
            normalized_status = str(status)
            if normalized_id in all_observed and all_observed[normalized_id] != normalized_status:
                raise Stage9RuntimeEvidenceError(f"conflicting_test_result:{normalized_id}")
            all_observed[normalized_id] = normalized_status
            sources[normalized_id] = f"matrix:{lane_name}"
    return all_observed, sources


def build_runtime_evidence(
    config: dict[str, object],
    matrix: dict[str, object],
    postgres: dict[str, object],
    redis: dict[str, object],
    *,
    commit: str,
) -> dict[str, object]:
    if matrix.get("commit") != commit:
        raise Stage9RuntimeEvidenceError("matrix_commit_mismatch")
    if matrix.get("stage9_passed") is not True:
        raise Stage9RuntimeEvidenceError("matrix_stage9_gate_not_passed")

    observed, sources = _lane_observed(matrix)
    source_sets: dict[str, set[str]] = {
        "matrix": set(observed),
        "postgres": set(postgres["observed_tests"]),
        "redis": set(redis["observed_tests"]),
    }
    for source_name, source in (("postgres", postgres), ("redis", redis)):
        for test_id, status in source["observed_tests"].items():
            if test_id in observed and observed[test_id] != status:
                raise Stage9RuntimeEvidenceError(f"conflicting_test_result:{test_id}")
            observed[test_id] = status
            sources[test_id] = source_name

    bindings = config.get("registry_bindings")
    if not isinstance(bindings, dict):
        raise Stage9RuntimeEvidenceError("registry_bindings_required")
    scenarios: dict[str, object] = {}
    for scenario_id, binding_value in sorted(bindings.items()):
        if not isinstance(binding_value, dict):
            raise Stage9RuntimeEvidenceError(f"invalid_registry_binding:{scenario_id}")
        stage = str(binding_value["evidence_stage"])
        if stage in {"stage10", "stage12", "matrix_stage10"}:
            scenarios[scenario_id] = {
                "status": "deferred",
                "evidence_stage": stage,
                "blocker": binding_value["blocker"],
            }
            if stage == "matrix_stage10":
                row = matrix["market_rows"].get(scenario_id)  # type: ignore[index]
                if not isinstance(row, dict) or row.get("status") != "deferred_stage10_real_topology":
                    raise Stage9RuntimeEvidenceError(
                        f"matrix_deferred_row_mismatch:{scenario_id}"
                    )
                scenarios[scenario_id]["matrix_row"] = scenario_id  # type: ignore[index]
                scenarios[scenario_id]["matrix_result"] = row  # type: ignore[index]
            continue
        if stage == "matrix":
            row = matrix["market_rows"].get(scenario_id)  # type: ignore[index]
            if not isinstance(row, dict) or row.get("status") != "passed":
                raise Stage9RuntimeEvidenceError(f"matrix_row_not_passed:{scenario_id}")
            scenarios[scenario_id] = {
                "status": "passed",
                "evidence_stage": stage,
                "matrix_row": scenario_id,
                "matrix_result": row,
            }
            continue

        test_ids = [str(value) for value in binding_value.get("test_ids", [])]
        missing = sorted(set(test_ids) - set(observed))
        not_passed = sorted(
            test_id for test_id in test_ids if observed.get(test_id) != "passed"
        )
        if missing:
            raise Stage9RuntimeEvidenceError(
                f"registry_tests_missing:{scenario_id}:{missing}"
            )
        if not_passed:
            raise Stage9RuntimeEvidenceError(
                f"registry_tests_not_passed:{scenario_id}:{not_passed}"
            )
        if stage == "stage9_postgres" and not (set(test_ids) & source_sets["postgres"]):
            raise Stage9RuntimeEvidenceError(f"postgres_evidence_required:{scenario_id}")
        if stage == "stage9_redis" and not (set(test_ids) & source_sets["redis"]):
            raise Stage9RuntimeEvidenceError(f"redis_evidence_required:{scenario_id}")
        scenarios[scenario_id] = {
            "status": "passed",
            "evidence_stage": stage,
            "test_ids": test_ids,
            "observed_sources": {test_id: sources[test_id] for test_id in test_ids},
        }

    transitions: dict[str, object] = {}
    for transition in config["transitions"]:  # type: ignore[index]
        transition_id = str(transition["id"])
        test_ids = [str(value) for value in transition["test_ids"]]
        missing = sorted(set(test_ids) - set(observed))
        not_passed = sorted(
            test_id for test_id in test_ids if observed.get(test_id) != "passed"
        )
        if missing or not_passed:
            raise Stage9RuntimeEvidenceError(
                f"transition_evidence_incomplete:{transition_id}:missing={missing}:failed={not_passed}"
            )
        transitions[transition_id] = {
            "status": "passed",
            "test_ids": test_ids,
            "observed_outcome": (
                "accepted" if transition["legal"] else "rejected_without_mutation"
            ),
            "observed_sources": {test_id: sources[test_id] for test_id in test_ids},
        }

    deferred = sorted(
        scenario_id
        for scenario_id, result in scenarios.items()
        if result["status"] == "deferred"  # type: ignore[index]
    )
    return {
        "schema_version": 2,
        "commit": commit,
        "stage9_passed": True,
        "complete": not deferred,
        "passed": True,
        "deferred_scenarios": deferred,
        "scenarios": scenarios,
        "transitions": transitions,
        "sources": {
            "matrix": {
                "execution_mode": matrix.get("execution_mode"),
                "registration_count": matrix["lanes"]["registration"]["test_count"],  # type: ignore[index]
                "market_count": matrix["lanes"]["market"]["test_count"],  # type: ignore[index]
            },
            "postgres": {key: value for key, value in postgres.items() if key != "observed_tests"},
            "redis": {key: value for key, value in redis.items() if key != "observed_tests"},
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/stage9_traceability.json")
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--postgres-log", required=True)
    parser.add_argument("--redis-log", required=True)
    parser.add_argument("--output", default="tmp/stage9-runtime-evidence.json")
    args = parser.parse_args(argv)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        config = _read_json(Path(args.config))
        matrix = _read_json(Path(args.matrix))
        postgres = _read_integration_log(
            Path(args.postgres_log), expected_commit=commit
        )
        redis = _read_integration_log(Path(args.redis_log), expected_commit=commit)
        evidence = build_runtime_evidence(
            config,
            matrix,
            postgres,
            redis,
            commit=commit,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, KeyError, Stage9RuntimeEvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
