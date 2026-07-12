#!/usr/bin/env python3
"""Generate and verify Stage 9 ID, transition, result, coverage, and mutation evidence."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP_ID_RE = re.compile(
    r"`((?:DT|IT|DN|IN|MIG|INV|BOT|REG|SYN|OTP|SEC|MKT|E2E|OPS|STG)-\d+)`"
)


class TraceabilityError(ValueError):
    pass


def expand_id_ranges(id_ranges: dict[str, object]) -> set[str]:
    expanded: set[str] = set()
    for prefix, count_value in id_ranges.items():
        count = int(count_value)
        width = 3 if prefix in {"MIG", "INV", "BOT", "REG", "SYN", "OTP", "SEC", "MKT", "E2E", "OPS", "STG"} else 2
        expanded.update(f"{prefix}-{index:0{width}d}" for index in range(1, count + 1))
    return expanded


def roadmap_ids(path: Path) -> set[str]:
    return set(ROADMAP_ID_RE.findall(path.read_text(encoding="utf-8")))


def _assert_file(path_value: str) -> None:
    path = REPO_ROOT / path_value
    if not path.is_file():
        raise TraceabilityError(f"nonexistent_test_reference:{path_value}")


def _assert_test_id(test_id: str) -> None:
    parts = str(test_id).split(".")
    if len(parts) < 4 or parts[0] != "tests":
        raise TraceabilityError(f"invalid_test_id:{test_id}")
    module_path = REPO_ROOT / ("/".join(parts[:2]) + ".py")
    if not module_path.is_file():
        raise TraceabilityError(f"nonexistent_test_id_module:{test_id}")
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_name, method_name = parts[2], parts[3]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            if any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == method_name
                for child in node.body
            ):
                return
    raise TraceabilityError(f"nonexistent_test_id:{test_id}")


def validate_configuration(config: dict[str, object], roadmap: Path) -> set[str]:
    if config.get("schema_version") != 1:
        raise TraceabilityError("unsupported_schema_version")
    configured = expand_id_ranges(config.get("id_ranges", {}))
    documented = roadmap_ids(roadmap)
    if configured != documented:
        raise TraceabilityError(
            f"registry_set_mismatch:missing={sorted(documented-configured)}:extra={sorted(configured-documented)}"
        )
    prefixes = set(config.get("automated_prefixes", [])) | set(config.get("decision_prefixes", [])) | set(config.get("deferred_prefixes", []))
    if prefixes != set(config.get("id_ranges", {})):
        raise TraceabilityError("registry_prefix_classification_mismatch")

    references = config.get("test_references", {})
    if not isinstance(references, dict):
        raise TraceabilityError("test_reference_map_required")
    for prefix in config.get("automated_prefixes", []):
        prefix_refs = references.get(prefix)
        if not isinstance(prefix_refs, list) or not prefix_refs:
            raise TraceabilityError(f"test_reference_required:{prefix}")
        for reference in prefix_refs:
            _assert_file(str(reference))

    automated_prefixes = set(config.get("automated_prefixes", []))
    expected_automated_ids = {
        registry_id
        for registry_id in configured
        if registry_id.split("-", 1)[0] in automated_prefixes
    }
    bindings = config.get("registry_bindings")
    if not isinstance(bindings, dict) or set(bindings) != expected_automated_ids:
        observed = set(bindings) if isinstance(bindings, dict) else set()
        raise TraceabilityError(
            "registry_binding_set_mismatch:"
            f"missing={sorted(expected_automated_ids-observed)}:"
            f"extra={sorted(observed-expected_automated_ids)}"
        )
    supported_stages = {
        "stage9",
        "stage9_postgres",
        "stage9_redis",
        "matrix",
        "matrix_stage10",
        "stage10",
        "stage12",
    }
    for registry_id, binding in bindings.items():
        if not isinstance(binding, dict):
            raise TraceabilityError(f"invalid_registry_binding:{registry_id}")
        evidence_stage = str(binding.get("evidence_stage", ""))
        if evidence_stage not in supported_stages:
            raise TraceabilityError(
                f"invalid_registry_binding_stage:{registry_id}:{evidence_stage}"
            )
        if evidence_stage in {"stage9", "stage9_postgres", "stage9_redis"}:
            test_ids = binding.get("test_ids")
            if not isinstance(test_ids, list) or not test_ids:
                raise TraceabilityError(f"registry_test_ids_required:{registry_id}")
            for test_id in test_ids:
                _assert_test_id(str(test_id))
        elif evidence_stage in {"matrix", "matrix_stage10"}:
            if binding.get("matrix_row") != registry_id:
                raise TraceabilityError(f"registry_matrix_row_mismatch:{registry_id}")
        if evidence_stage in {"matrix_stage10", "stage10", "stage12"}:
            if not str(binding.get("blocker", "")).strip():
                raise TraceabilityError(f"registry_deferred_blocker_required:{registry_id}")

    expected_transitions = set(config.get("expected_transition_ids", []))
    transitions = config.get("transitions", [])
    if not isinstance(transitions, list):
        raise TraceabilityError("transitions_required")
    observed_transitions = {str(item.get("id")) for item in transitions if isinstance(item, dict)}
    if observed_transitions != expected_transitions:
        raise TraceabilityError(
            f"transition_set_mismatch:missing={sorted(expected_transitions-observed_transitions)}:extra={sorted(observed_transitions-expected_transitions)}"
        )
    for transition in transitions:
        if not isinstance(transition, dict) or not isinstance(transition.get("legal"), bool):
            raise TraceabilityError("transition_legality_required")
        test_ids = transition.get("test_ids")
        if not isinstance(test_ids, list) or not test_ids:
            raise TraceabilityError(f"transition_test_required:{transition.get('id')}")
        for test_id in test_ids:
            _assert_test_id(str(test_id))
    if {transition["legal"] for transition in transitions} != {False, True}:
        raise TraceabilityError("legal_and_illegal_transitions_required")
    return configured


def validate_runtime_evidence(
    config: dict[str, object],
    *,
    results: dict[str, object],
    backend_coverage: dict[str, object],
    frontend_coverage: dict[str, object],
    mutation: dict[str, object],
) -> None:
    current_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    for label, evidence in (
        ("results", results),
        ("backend", backend_coverage),
        ("frontend", frontend_coverage),
        ("mutation", mutation),
    ):
        if evidence.get("commit") != current_commit:
            raise TraceabilityError(
                f"runtime_evidence_commit_mismatch:{label}:"
                f"expected={current_commit}:observed={evidence.get('commit')}"
            )
    automated_prefixes = set(config["automated_prefixes"])
    expected_ids = {
        item
        for item in expand_id_ranges(config["id_ranges"])  # type: ignore[arg-type]
        if item.split("-", 1)[0] in automated_prefixes
    }
    scenario_results = results.get("scenarios")
    if not isinstance(scenario_results, dict):
        raise TraceabilityError("scenario_results_required")
    observed_ids = set(scenario_results)
    if observed_ids != expected_ids:
        raise TraceabilityError(
            f"scenario_result_set_mismatch:missing={sorted(expected_ids-observed_ids)}:extra={sorted(observed_ids-expected_ids)}"
        )
    for scenario_id, result in scenario_results.items():
        if not isinstance(result, dict):
            raise TraceabilityError(f"required_test_not_passed:{scenario_id}:invalid")
        binding = config["registry_bindings"][scenario_id]  # type: ignore[index]
        evidence_stage = binding["evidence_stage"]
        expected_status = (
            "deferred"
            if evidence_stage in {"matrix_stage10", "stage10", "stage12"}
            else "passed"
        )
        if result.get("status") != expected_status:
            status = result.get("status") if isinstance(result, dict) else "invalid"
            raise TraceabilityError(f"required_test_not_passed:{scenario_id}:{status}")
        if expected_status == "deferred":
            if result.get("blocker") != binding.get("blocker"):
                raise TraceabilityError(f"deferred_blocker_mismatch:{scenario_id}")
            continue
        if evidence_stage in {"stage9", "stage9_postgres", "stage9_redis"}:
            if set(result.get("test_ids", [])) != set(binding["test_ids"]):
                raise TraceabilityError(f"registry_test_result_mismatch:{scenario_id}")
        if evidence_stage == "matrix" and result.get("matrix_row") != scenario_id:
            raise TraceabilityError(f"registry_matrix_result_mismatch:{scenario_id}")

    transition_results = results.get("transitions")
    if not isinstance(transition_results, dict):
        raise TraceabilityError("transition_results_required")
    expected_transitions = set(config["expected_transition_ids"])
    if set(transition_results) != expected_transitions:
        raise TraceabilityError("transition_result_set_mismatch")
    transition_config = {
        str(item["id"]): item for item in config["transitions"]  # type: ignore[index]
    }
    for transition_id, result in transition_results.items():
        if not isinstance(result, dict) or result.get("status") != "passed":
            raise TraceabilityError(f"transition_not_passed:{transition_id}")
        expected_test_ids = set(transition_config[transition_id]["test_ids"])
        observed_test_ids = set(result.get("test_ids", []))
        if observed_test_ids != expected_test_ids:
            raise TraceabilityError(f"transition_test_result_mismatch:{transition_id}")
        expected_outcome = (
            "accepted" if transition_config[transition_id]["legal"] else "rejected_without_mutation"
        )
        if result.get("observed_outcome") != expected_outcome:
            raise TraceabilityError(f"transition_outcome_mismatch:{transition_id}")

    for label, evidence in (("backend", backend_coverage), ("frontend", frontend_coverage), ("mutation", mutation)):
        if evidence.get("passed") is not True:
            raise TraceabilityError(f"{label}_evidence_not_passed")


def build_artifact(
    config: dict[str, object],
    ids: set[str],
    *,
    results: dict[str, object],
    backend_coverage: dict[str, object],
    frontend_coverage: dict[str, object],
    mutation: dict[str, object],
) -> dict[str, object]:
    references = config.get("test_references", {})
    bindings = config.get("registry_bindings", {})
    decision_records = config.get("decision_records", {})
    blockers = config.get("deferred_blockers", {})
    owners = config.get("stage_owners", {})
    rows = []
    scenario_results = results["scenarios"]
    assert isinstance(scenario_results, dict)
    for registry_id in sorted(ids):
        prefix = registry_id.split("-", 1)[0]
        rows.append(
            {
                "id": registry_id,
                "decision_record": decision_records.get(prefix),
                "test_evidence": (
                    bindings.get(registry_id)
                    if prefix in set(config.get("automated_prefixes", []))
                    else references.get(prefix, [])
                ),
                "remaining_risk": blockers.get(prefix),
                "stage_owner": owners.get(prefix),
                "runtime_result": scenario_results.get(registry_id),
            }
        )
    return {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        "registry_rows": rows,
        "transitions": config["transitions"],
        "transition_results": results["transitions"],
        "evidence_commits": {
            "results": results["commit"],
            "backend_coverage": backend_coverage["commit"],
            "frontend_coverage": frontend_coverage["commit"],
            "mutation": mutation["commit"],
        },
    }


def _read_json(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TraceabilityError(f"object_required:{path}")
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/stage9_traceability.json")
    parser.add_argument("--roadmap", default="docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md")
    parser.add_argument("--output", default="tmp/stage9-traceability.json")
    parser.add_argument("--results")
    parser.add_argument("--backend-coverage")
    parser.add_argument("--frontend-coverage")
    parser.add_argument("--mutation")
    parser.add_argument("--require-runtime-evidence", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = _read_json(args.config)
        ids = validate_configuration(config, Path(args.roadmap))
        if args.validate_only:
            if args.require_runtime_evidence or any(
                (args.results, args.backend_coverage, args.frontend_coverage, args.mutation)
            ):
                raise TraceabilityError("validate_only_cannot_accept_runtime_evidence")
            return 0
        if not args.require_runtime_evidence:
            raise TraceabilityError("runtime_evidence_required")
        required_paths = (args.results, args.backend_coverage, args.frontend_coverage, args.mutation)
        if not all(required_paths):
            raise TraceabilityError("all_runtime_evidence_paths_required")
        results = _read_json(args.results)
        backend_coverage = _read_json(args.backend_coverage)
        frontend_coverage = _read_json(args.frontend_coverage)
        mutation = _read_json(args.mutation)
        validate_runtime_evidence(
            config,
            results=results,
            backend_coverage=backend_coverage,
            frontend_coverage=frontend_coverage,
            mutation=mutation,
        )
        artifact = build_artifact(
            config,
            ids,
            results=results,
            backend_coverage=backend_coverage,
            frontend_coverage=frontend_coverage,
            mutation=mutation,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, TraceabilityError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
