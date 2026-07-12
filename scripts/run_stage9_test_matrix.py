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


def validate_parallel_isolation(
    manifest: dict[str, object], isolation: dict[str, object] | None
) -> tuple[bool, list[str]]:
    if isolation is None:
        return False, ["isolation_contract_missing"]
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
    return not failures, failures


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
    stage9_packages = str(REPO_ROOT / "tmp/stage9-site-packages")
    env["PYTHONPATH"] = stage9_packages + os.pathsep + env.get("PYTHONPATH", "")
    if resources:
        env.update(
            {
                "STAGE9_LANE": name,
                "STAGE9_DATABASE_RESOURCE": str(resources["database"]),
                "STAGE9_REDIS_NAMESPACE": str(resources["redis_namespace"]),
                "STAGE9_FIXTURE_PREFIX": str(resources["fixture_prefix"]),
                "STAGE9_LANE_PORT": str(resources["port"]),
                "STAGE9_ARTIFACT_DIR": str(resources["artifact_dir"]),
                "STAGE9_CLEANUP_OWNER": str(resources["cleanup_owner"]),
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
    status = "passed" if result.returncode == 0 and skipped == 0 else "failed"
    return {
        "lane": name,
        "status": status,
        "exit_code": result.returncode,
        "test_count": int(test_count_match.group(1)) if test_count_match else None,
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
            return isolation[name]  # type: ignore[return-value]
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
    passed = all(result["status"] == "passed" for result in lane_results.values())
    return {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        "evidence_scope": "local_composed_reference_modules",
        "execution_mode": "parallel" if parallel else "sequential",
        "lanes": lane_results,
        "market_rows": {
            row["id"]: {
                "status": "passed" if lane_results["market"]["status"] == "passed" else "failed",
                "evidence": "all_composed_reference_modules_passed_without_skip",
                "test_references": row["test_references"],
            }
            for row in manifest["market_rows"]
        },
        "market_special_cases": {
            case["id"]: {
                "status": "passed" if lane_results["market"]["status"] == "passed" else "failed",
                "evidence": "all_composed_reference_modules_passed_without_skip",
                "test_references": case["test_references"],
            }
            for case in manifest["market_special_cases"]
        },
        "passed": passed,
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
