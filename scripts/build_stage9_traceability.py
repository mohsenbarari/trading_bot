#!/usr/bin/env python3
"""Generate and verify Stage 9 ID, transition, result, coverage, and mutation evidence."""

from __future__ import annotations

import argparse
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
        tests = transition.get("tests")
        if not isinstance(tests, list) or not tests:
            raise TraceabilityError(f"transition_test_required:{transition.get('id')}")
        for reference in tests:
            _assert_file(str(reference))
    return configured


def validate_runtime_evidence(
    config: dict[str, object],
    *,
    results: dict[str, object],
    backend_coverage: dict[str, object],
    frontend_coverage: dict[str, object],
    mutation: dict[str, object],
) -> None:
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
        if not isinstance(result, dict) or result.get("status") != "passed":
            status = result.get("status") if isinstance(result, dict) else "invalid"
            raise TraceabilityError(f"required_test_not_passed:{scenario_id}:{status}")

    transition_results = results.get("transitions")
    if not isinstance(transition_results, dict):
        raise TraceabilityError("transition_results_required")
    expected_transitions = set(config["expected_transition_ids"])
    if set(transition_results) != expected_transitions:
        raise TraceabilityError("transition_result_set_mismatch")
    for transition_id, result in transition_results.items():
        if not isinstance(result, dict) or result.get("status") != "passed":
            raise TraceabilityError(f"transition_not_passed:{transition_id}")

    for label, evidence in (("backend", backend_coverage), ("frontend", frontend_coverage), ("mutation", mutation)):
        if evidence.get("passed") is not True:
            raise TraceabilityError(f"{label}_evidence_not_passed")


def build_artifact(config: dict[str, object], ids: set[str]) -> dict[str, object]:
    references = config.get("test_references", {})
    decision_records = config.get("decision_records", {})
    blockers = config.get("deferred_blockers", {})
    owners = config.get("stage_owners", {})
    rows = []
    for registry_id in sorted(ids):
        prefix = registry_id.split("-", 1)[0]
        rows.append(
            {
                "id": registry_id,
                "decision_record": decision_records.get(prefix),
                "test_evidence": references.get(prefix, []),
                "remaining_risk": blockers.get(prefix),
                "stage_owner": owners.get(prefix),
            }
        )
    return {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        "registry_rows": rows,
        "transitions": config["transitions"],
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
    args = parser.parse_args(argv)
    try:
        config = _read_json(args.config)
        ids = validate_configuration(config, Path(args.roadmap))
        if args.require_runtime_evidence:
            required_paths = (args.results, args.backend_coverage, args.frontend_coverage, args.mutation)
            if not all(required_paths):
                raise TraceabilityError("all_runtime_evidence_paths_required")
            validate_runtime_evidence(
                config,
                results=_read_json(args.results),
                backend_coverage=_read_json(args.backend_coverage),
                frontend_coverage=_read_json(args.frontend_coverage),
                mutation=_read_json(args.mutation),
            )
        artifact = build_artifact(config, ids)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, TraceabilityError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
