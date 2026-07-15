#!/usr/bin/env python3
"""Run Stage 9 registration and controlled-market lanes with fail-closed isolation."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "config/stage9_test_matrix.json"
DEFAULT_ISOLATION = REPO_ROOT / "config/stage9_test_matrix_isolation.json"
RAN_TESTS_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
SKIPPED_RE = re.compile(r"skipped=(\d+)")
TEST_START_RE = re.compile(
    r"^(?P<method>(?:test_[^( ]+|runTest)) \((?P<case>[^)]+)\) \.\.\.(?P<tail>.*)$"
)
DETERMINISTIC_FEATURE_ENV = {
    "SERVER_MODE": "foreign",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
    "TELEGRAM_LOGIN_OTP_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
    "INVITATION_CONTRACT_V2_ENABLED": "false",
    "REGISTRATION_SYNC_V2_ENABLED": "false",
}


class MatrixConfigurationError(ValueError):
    pass


def load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MatrixConfigurationError(f"object_required:{path}")
    return payload


def resolve_artifact_dir(path: Path, repo_root: Path = REPO_ROOT) -> Path:
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise MatrixConfigurationError(f"artifact_dir_outside_repository:{path}") from exc
    return resolved


def validate_manifest(manifest: dict[str, object], repo_root: Path = REPO_ROOT) -> None:
    if manifest.get("schema_version") != 1:
        raise MatrixConfigurationError("unsupported_schema_version")
    max_contenders = int(manifest.get("max_semantic_contenders", 0) or 0)
    if max_contenders != 2:
        raise MatrixConfigurationError("semantic_contenders_must_equal_two")
    forbidden = {str(value).lower() for value in manifest.get("forbidden_profiles", [])}
    required_forbidden = {"pressure", "saturation", "high_rps", "burst", "soak"}
    if not required_forbidden.issubset(forbidden):
        raise MatrixConfigurationError("forbidden_load_profiles_incomplete")

    lanes = manifest.get("lanes")
    if not isinstance(lanes, dict) or set(lanes) != {"registration", "market"}:
        raise MatrixConfigurationError("exactly_two_lanes_required")
    for lane_name, lane in lanes.items():
        if not isinstance(lane, dict) or not lane.get("test_modules"):
            raise MatrixConfigurationError(f"lane_tests_required:{lane_name}")
        if int(lane.get("minimum_test_count", 0) or 0) <= 0:
            raise MatrixConfigurationError(f"lane_minimum_test_count_required:{lane_name}")
        for module in lane["test_modules"]:
            module_path = repo_root / (str(module).replace(".", "/") + ".py")
            if not module_path.exists():
                raise MatrixConfigurationError(f"nonexistent_test_module:{module}")

    rows = manifest.get("market_rows")
    if not isinstance(rows, list):
        raise MatrixConfigurationError("market_rows_required")
    expected = {f"MKT-{index:03d}" for index in range(1, 11)}
    observed = {str(row.get("id")) for row in rows if isinstance(row, dict)}
    if observed != expected:
        raise MatrixConfigurationError(
            f"market_row_set_mismatch:missing={sorted(expected-observed)}:extra={sorted(observed-expected)}"
        )
    for row in rows:
        assert isinstance(row, dict)
        if not row.get("dimensions"):
            raise MatrixConfigurationError(f"market_dimensions_required:{row['id']}")
        if int(row.get("max_contenders", max_contenders)) > max_contenders:
            raise MatrixConfigurationError(f"market_contenders_unbounded:{row['id']}")
        serialized = json.dumps(row, sort_keys=True).lower()
        if any(profile in serialized for profile in forbidden):
            raise MatrixConfigurationError(f"forbidden_load_profile:{row['id']}")
        for reference in row.get("test_references", []):
            if not (repo_root / str(reference)).exists():
                raise MatrixConfigurationError(f"nonexistent_test_reference:{reference}")
        scenarios = row.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            raise MatrixConfigurationError(f"market_scenarios_required:{row['id']}")
        covered_dimensions: set[str] = set()
        scenario_ids: set[str] = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                raise MatrixConfigurationError(f"invalid_market_scenario:{row['id']}")
            scenario_id = str(scenario.get("id", "")).strip()
            evidence_stage = str(scenario.get("evidence_stage", "stage9")).strip()
            test_id = str(scenario.get("test_id", "")).strip()
            dimensions = scenario.get("dimensions")
            if not scenario_id or scenario_id in scenario_ids:
                raise MatrixConfigurationError(f"invalid_market_scenario_id:{row['id']}:{scenario_id}")
            if evidence_stage not in {"stage9", "stage10"}:
                raise MatrixConfigurationError(
                    f"invalid_market_scenario_stage:{row['id']}:{scenario_id}:{evidence_stage}"
                )
            if evidence_stage == "stage9" and not test_id:
                raise MatrixConfigurationError(
                    f"stage9_market_scenario_test_required:{row['id']}:{scenario_id}"
                )
            if evidence_stage == "stage10" and not str(scenario.get("blocker", "")).strip():
                raise MatrixConfigurationError(
                    f"stage10_market_scenario_blocker_required:{row['id']}:{scenario_id}"
                )
            if not isinstance(dimensions, list) or not dimensions:
                raise MatrixConfigurationError(f"incomplete_market_scenario:{row['id']}:{scenario_id}")
            scenario_ids.add(scenario_id)
            covered_dimensions.update(str(value) for value in dimensions)
        required_dimensions = {str(value) for value in row["dimensions"]}
        if covered_dimensions != required_dimensions:
            raise MatrixConfigurationError(
                f"market_dimension_mapping_mismatch:{row['id']}:"
                f"missing={sorted(required_dimensions-covered_dimensions)}:"
                f"extra={sorted(covered_dimensions-required_dimensions)}"
            )

    expected_special_cases = {
        "MKT-SPECIAL-WARNING-ACK",
        "MKT-SPECIAL-REPUBLISH-LINEAGE",
        "MKT-SPECIAL-TELEGRAM-PUBLICATION-REPAIR",
        "MKT-SPECIAL-CHANNEL-NOTICE-RECONCILIATION",
        "MKT-SPECIAL-DELIVERY-RECEIPTS",
        "MKT-SPECIAL-LOCAL-ONLY-FIELDS",
        "MKT-SPECIAL-TIMED-EXPIRY-METADATA",
    }
    special_cases = manifest.get("market_special_cases")
    if not isinstance(special_cases, list):
        raise MatrixConfigurationError("market_special_cases_required")
    observed_special_cases = {
        str(case.get("id")) for case in special_cases if isinstance(case, dict)
    }
    if observed_special_cases != expected_special_cases:
        raise MatrixConfigurationError(
            "market_special_case_set_mismatch:"
            f"missing={sorted(expected_special_cases-observed_special_cases)}:"
            f"extra={sorted(observed_special_cases-expected_special_cases)}"
        )
    for case in special_cases:
        assert isinstance(case, dict)
        references = case.get("test_references")
        if not isinstance(references, list) or not references:
            raise MatrixConfigurationError(f"market_special_case_tests_required:{case['id']}")
        for reference in references:
            if not (repo_root / str(reference)).exists():
                raise MatrixConfigurationError(f"nonexistent_test_reference:{reference}")
            module = str(reference).removesuffix(".py").replace("/", ".")
            market_modules = set(lanes["market"]["test_modules"])
            if module not in market_modules:
                raise MatrixConfigurationError(
                    f"market_special_case_not_executed:{case['id']}:{reference}"
                )
        if not str(case.get("test_id", "")).strip():
            raise MatrixConfigurationError(f"market_special_case_test_id_required:{case['id']}")


def validate_parallel_isolation(
    manifest: dict[str, object], isolation: dict[str, object] | None
) -> tuple[bool, list[str]]:
    if isolation is None:
        return False, ["isolation_contract_missing"]
    if isolation.get("schema_version") != 2:
        return False, ["isolation_schema_version_2_required"]
    if isolation.get("resource_mode") != "process_local_no_external_mutable_io":
        return False, ["unsupported_isolation_resource_mode"]
    lanes = [isolation.get("registration"), isolation.get("market")]
    if not all(isinstance(lane, dict) for lane in lanes):
        return False, ["both_lane_isolation_records_required"]
    failures: list[str] = []
    for key in manifest.get("isolation_keys", []):
        values = [str(lane.get(str(key), "")).strip() for lane in lanes]  # type: ignore[union-attr]
        if not all(values):
            failures.append(f"isolation_value_missing:{key}")
        elif values[0] == values[1]:
            failures.append(f"isolation_value_shared:{key}")
    for lane_name, lane_config in manifest.get("lanes", {}).items():
        if (
            not isinstance(lane_config, dict)
            or lane_config.get("external_mutable_service_io") is not False
        ):
            failures.append(f"lane_external_mutable_io_not_forbidden:{lane_name}")
    return not failures, failures


def _observed_test_results(output: str) -> dict[str, str]:
    observed: dict[str, str] = {}
    pending: str | None = None
    for line in output.splitlines():
        match = TEST_START_RE.match(line)
        if match:
            case = match.group("case")
            method = match.group("method")
            pending = case if case.endswith(f".{method}") else f"{case}.{method}"
            status = match.group("tail").strip()
            if status in {"ok", "FAIL", "ERROR"} or status.startswith("skipped "):
                observed[pending] = (
                    "passed"
                    if status == "ok"
                    else ("skipped" if status.startswith("skipped ") else "failed")
                )
                pending = None
            continue
        stripped = line.strip()
        if pending and (
            stripped == "ok"
            or stripped in {"FAIL", "ERROR"}
            or stripped.startswith("skipped ")
        ):
            observed[pending] = (
                "passed"
                if stripped == "ok"
                else ("skipped" if stripped.startswith("skipped ") else "failed")
            )
            pending = None
    return observed


def _run_lane(
    name: str,
    modules: list[str],
    artifact_dir: Path,
    resources: dict[str, object] | None = None,
) -> dict[str, object]:
    artifact_dir = resolve_artifact_dir(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    command = [sys.executable, "-m", "unittest", "-v", *modules]
    env = os.environ.copy()
    # Match the merge-gate defaults even when this runner is invoked from a
    # production-configured checkout. Feature-enabled behavior is covered by
    # tests that opt in explicitly.
    env.update(DETERMINISTIC_FEATURE_ENV)
    stage9_packages = str(REPO_ROOT / "tmp/stage9-site-packages")
    env["PYTHONPATH"] = stage9_packages + os.pathsep + env.get("PYTHONPATH", "")
    if resources:
        lane_tmp = resolve_artifact_dir(Path(str(resources["tmp_dir"])))
        lane_tmp.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "STAGE9_LANE": name,
                "STAGE9_DATABASE_RESOURCE": str(resources["database"]),
                "STAGE9_REDIS_NAMESPACE": str(resources["redis_namespace"]),
                "STAGE9_FIXTURE_PREFIX": str(resources["fixture_prefix"]),
                "STAGE9_LANE_PORT": str(resources["port"]),
                "STAGE9_ARTIFACT_DIR": str(resources["artifact_dir"]),
                "STAGE9_CLEANUP_OWNER": str(resources["cleanup_owner"]),
                "DATABASE_URL": str(resources["database_url"]),
                "SYNC_DATABASE_URL": str(resources["sync_database_url"]),
                "REDIS_URL": str(resources["redis_url"]),
                "TMPDIR": str(lane_tmp),
            }
        )
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path = artifact_dir / f"{name}.log"
    log_path.write_text(result.stdout, encoding="utf-8")
    test_count_match = RAN_TESTS_RE.search(result.stdout)
    skipped = sum(int(value) for value in SKIPPED_RE.findall(result.stdout))
    test_count = int(test_count_match.group(1)) if test_count_match else None
    observed_tests = _observed_test_results(result.stdout)
    minimum_test_count = int(resources.get("minimum_test_count", 1)) if resources else 1
    status = (
        "passed"
        if result.returncode == 0
        and skipped == 0
        and test_count is not None
        and test_count >= minimum_test_count
        and len(observed_tests) == test_count
        else "failed"
    )
    return {
        "lane": name,
        "status": status,
        "exit_code": result.returncode,
        "test_count": test_count,
        "minimum_test_count": minimum_test_count,
        "observed_tests": observed_tests,
        "skipped": skipped,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command": command,
        "log": str(log_path.relative_to(REPO_ROOT)),
    }


def run_matrix(
    manifest: dict[str, object],
    *,
    parallel: bool,
    artifact_dir: Path,
    isolation: dict[str, object] | None = None,
) -> dict[str, object]:
    artifact_dir = resolve_artifact_dir(artifact_dir)
    lanes = manifest["lanes"]
    assert isinstance(lanes, dict)

    def lane_artifact_dir(name: str) -> Path:
        if isolation and isinstance(isolation.get(name), dict):
            return Path(str(isolation[name]["artifact_dir"]))  # type: ignore[index]
        return artifact_dir

    def lane_resources(name: str) -> dict[str, object] | None:
        if isolation and isinstance(isolation.get(name), dict):
            resources = dict(isolation[name])  # type: ignore[arg-type]
            resources["minimum_test_count"] = lanes[name]["minimum_test_count"]
            return resources
        return None

    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                name: executor.submit(
                    _run_lane,
                    name,
                    list(lane["test_modules"]),
                    lane_artifact_dir(name),
                    lane_resources(name),
                )
                for name, lane in lanes.items()
            }
            lane_results = {name: future.result() for name, future in futures.items()}
    else:
        lane_results = {
            name: _run_lane(
                name,
                list(lane["test_modules"]),
                lane_artifact_dir(name),
                lane_resources(name),
            )
            for name, lane in lanes.items()
        }
    market_observed = lane_results["market"].get("observed_tests", {})
    assert isinstance(market_observed, dict)

    def scenario_result(scenario: dict[str, object]) -> dict[str, object]:
        evidence_stage = str(scenario.get("evidence_stage", "stage9"))
        if evidence_stage == "stage10":
            return {
                "status": "deferred_stage10_real_topology",
                "test_id": None,
                "dimensions": scenario["dimensions"],
                "blocker": scenario["blocker"],
            }
        test_id = str(scenario["test_id"])
        observed_status = market_observed.get(test_id, "missing")
        return {
            "status": observed_status,
            "test_id": test_id,
            "dimensions": scenario["dimensions"],
        }

    market_rows: dict[str, object] = {}
    for row in manifest["market_rows"]:
        scenarios = {
            str(scenario["id"]): scenario_result(scenario)
            for scenario in row["scenarios"]
        }
        scenario_statuses = {result["status"] for result in scenarios.values()}
        row_status = (
            "failed"
            if scenario_statuses - {"passed", "deferred_stage10_real_topology"}
            else (
                "deferred_stage10_real_topology"
                if "deferred_stage10_real_topology" in scenario_statuses
                else "passed"
            )
        )
        market_rows[str(row["id"])] = {
            "status": row_status,
            "scenarios": scenarios,
            "test_references": row["test_references"],
        }

    special_cases: dict[str, object] = {}
    for case in manifest["market_special_cases"]:
        test_id = str(case["test_id"])
        observed_status = market_observed.get(test_id, "missing")
        special_cases[str(case["id"])] = {
            "status": observed_status,
            "test_id": test_id,
            "test_references": case["test_references"],
        }

    stage9_passed = (
        all(result["status"] == "passed" for result in lane_results.values())
        and all(row["status"] in {"passed", "deferred_stage10_real_topology"} for row in market_rows.values())  # type: ignore[index]
        and all(case["status"] == "passed" for case in special_cases.values())  # type: ignore[index]
    )
    complete = stage9_passed and all(
        row["status"] == "passed" for row in market_rows.values()  # type: ignore[index]
    )
    return {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        "evidence_scope": "local_composed_reference_modules",
        "execution_mode": "parallel" if parallel else "sequential",
        "lanes": lane_results,
        "market_rows": market_rows,
        "market_special_cases": special_cases,
        "stage9_passed": stage9_passed,
        "complete": complete,
        "passed": stage9_passed,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--isolation", default=str(DEFAULT_ISOLATION))
    parser.add_argument("--parallel", choices=("auto", "required", "off"), default="auto")
    parser.add_argument("--artifact-dir", default="tmp/stage9-matrix")
    parser.add_argument("--output", default="tmp/stage9-matrix/results.json")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_json(Path(args.manifest))
        validate_manifest(manifest)
        isolation = load_json(Path(args.isolation)) if args.isolation else None
        isolated, isolation_failures = validate_parallel_isolation(manifest, isolation)
        if args.parallel == "required" and not isolated:
            raise MatrixConfigurationError("parallel_isolation_not_proven:" + ",".join(isolation_failures))
        use_parallel = isolated and args.parallel != "off"
    except (OSError, json.JSONDecodeError, MatrixConfigurationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    preflight = {
        "manifest_valid": True,
        "parallel_isolation_proven": isolated,
        "isolation_failures": isolation_failures,
        "selected_mode": "parallel" if use_parallel else "sequential",
    }
    if args.preflight_only:
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0

    results = run_matrix(
        manifest,
        parallel=use_parallel,
        artifact_dir=Path(args.artifact_dir),
        isolation=isolation,
    )
    results["preflight"] = preflight
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
