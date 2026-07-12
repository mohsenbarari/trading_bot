#!/usr/bin/env python3
"""Run pinned mutmut targets and emit machine-verifiable grouped evidence."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULT_RE = re.compile(r"^\s*(\S+):\s+(.+?)\s*$")
RUN_RESULT_RE = re.compile(r"^([🎉🙁⏰🤔])\s+(\S+)\s*$")
RUN_STATUS = {"🎉": "killed", "🙁": "survived", "⏰": "timeout", "🤔": "suspicious"}


def _test_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "SERVER_MODE": "foreign",
            "BOT_USERNAME": "stage9_test_bot",
            "POSTGRES_DB": "stage9",
            "POSTGRES_USER": "stage9",
            "POSTGRES_PASSWORD": "stage9",
            "DATABASE_URL": "postgresql+asyncpg://stage9:stage9@127.0.0.1:5432/stage9",
            "SYNC_DATABASE_URL": "postgresql+psycopg2://stage9:stage9@127.0.0.1:5432/stage9",
            "REDIS_URL": "redis://127.0.0.1:6379/15",
            "FRONTEND_URL": "http://127.0.0.1:4173",
            "DEV_API_KEY": "stage9_test",
            "JWT_SECRET_KEY": "stage9_test_secret",
        }
    )
    stage9_packages = str(REPO_ROOT / "tmp/stage9-site-packages")
    env["PYTHONPATH"] = stage9_packages + os.pathsep + env.get("PYTHONPATH", "")
    return env


def parse_mutmut_results(output: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for line in output.splitlines():
        match = RESULT_RE.match(line)
        if match:
            results[match.group(1)] = match.group(2).strip().lower()
    return results


def parse_mutmut_run_results(output: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for line in output.splitlines():
        match = RUN_RESULT_RE.match(line.strip())
        if match:
            results[match.group(2)] = RUN_STATUS[match.group(1)]
    return results


def grouped_evidence(manifest: dict[str, object], raw_results: dict[str, str]) -> dict[str, object]:
    equivalent_entries = manifest.get("equivalent_mutants", [])
    if not isinstance(equivalent_entries, list):
        raise ValueError("equivalent_mutants_must_be_a_list")
    equivalent = {}
    for entry in equivalent_entries:
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip() or not str(entry.get("reason", "")).strip():
            raise ValueError("equivalent_mutant_name_and_reason_required")
        equivalent[str(entry["name"])] = str(entry["reason"])
    target_matchers = [str(target["matcher"]) for target in manifest["targets"]]
    for name in equivalent:
        if sum(fnmatch.fnmatch(name, matcher) for matcher in target_matchers) != 1:
            raise ValueError(f"equivalent_mutant_target_mismatch:{name}")
    missing_equivalents = set(equivalent) - set(raw_results)
    if missing_equivalents:
        raise ValueError(f"equivalent_mutants_not_generated:{sorted(missing_equivalents)}")

    grouped: dict[str, object] = {}
    overall = True
    for target in manifest["targets"]:
        matcher = str(target["matcher"])
        matched = {name: status for name, status in raw_results.items() if fnmatch.fnmatch(name, matcher)}
        excluded = {name: equivalent[name] for name in matched if name in equivalent}
        evaluated = {name: status for name, status in matched.items() if name not in equivalent}
        statuses = set(evaluated.values())
        status = "killed" if evaluated and statuses == {"killed"} else ("missing" if not evaluated else "survived")
        grouped[str(target["id"])] = {
            "status": status,
            "matcher": matcher,
            "mutant_count": len(matched),
            "evaluated_mutant_count": len(evaluated),
            "equivalent_mutants": excluded,
            "raw_statuses": sorted(statuses),
        }
        overall = overall and status == "killed"
    return {"schema_version": 1, "passed": overall, "results": grouped}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="config/stage9_mutation_manifest.json")
    parser.add_argument("--output", default="tmp/stage9-mutation-evidence.json")
    parser.add_argument("--log", default="tmp/stage9-mutation.log")
    parser.add_argument("--max-children", type=int, default=1)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.fresh:
        mutants_dir = (REPO_ROOT / "mutants").resolve()
        if mutants_dir != (REPO_ROOT / "mutants"):
            raise RuntimeError("unsafe_mutants_path")
        shutil.rmtree(mutants_dir, ignore_errors=True)

    matchers = [str(target["matcher"]) for target in manifest["targets"]]
    command = [sys.executable, "-m", "mutmut", "run", *matchers, "--max-children", str(args.max_children)]
    run = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=_test_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(run.stdout, encoding="utf-8")

    results_run = subprocess.run(
        [sys.executable, "-m", "mutmut", "results", "--all", "true"],
        cwd=REPO_ROOT,
        env=_test_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    run_results = parse_mutmut_run_results(run.stdout)
    raw_results = run_results or parse_mutmut_results(results_run.stdout)
    evidence = grouped_evidence(manifest, raw_results)
    evidence["run_exit_code"] = run.returncode
    evidence["results_exit_code"] = results_run.returncode
    evidence["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if run.returncode != 0:
        print(f"mutmut_run_failed:{run.returncode}; see {log}", file=sys.stderr)
    if not evidence["passed"]:
        print("critical_mutation_evidence_failed", file=sys.stderr)
    return 0 if run.returncode == 0 and evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
