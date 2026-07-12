#!/usr/bin/env python3
"""Verify exact critical-mutant evidence; survivors and missing results fail."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


class MutationEvidenceError(ValueError):
    pass


def validate_mutation_evidence(manifest: dict[str, object], evidence: dict[str, object]) -> None:
    if manifest.get("schema_version") != 1 or evidence.get("schema_version") != 1:
        raise MutationEvidenceError("unsupported_schema_version")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        raise MutationEvidenceError("mutation_targets_required")
    expected = {str(target.get("id")) for target in targets if isinstance(target, dict)}
    results = evidence.get("results")
    if not isinstance(results, dict):
        raise MutationEvidenceError("mutation_results_required")
    if set(results) != expected:
        raise MutationEvidenceError(
            f"mutation_result_set_mismatch:missing={sorted(expected-set(results))}:extra={sorted(set(results)-expected)}"
        )
    for mutant_id, result in results.items():
        if not isinstance(result, dict) or result.get("status") != "killed":
            status = result.get("status") if isinstance(result, dict) else "invalid"
            raise MutationEvidenceError(f"critical_mutant_not_killed:{mutant_id}:{status}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="config/stage9_mutation_manifest.json")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", default="tmp/stage9-mutation-verification.json")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        validate_mutation_evidence(manifest, evidence)
        report = {"schema_version": 1, "passed": True, "target_count": len(manifest["targets"])}
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, MutationEvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
