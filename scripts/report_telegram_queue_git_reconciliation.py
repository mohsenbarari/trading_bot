#!/usr/bin/env python3
"""Emit reproducible patch-equivalence evidence for the Telegram queue branch."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any


class GitReconciliationError(RuntimeError):
    """Raised when the requested refs cannot be reconciled exactly."""


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitReconciliationError(
            f"git_command_failed:{args[0]}:{result.returncode}"
        )
    return result.stdout.strip()


def _commit_rows(repo: Path, base: str, ref: str) -> list[dict[str, Any]]:
    raw = _git(repo, "rev-list", "--reverse", f"{base}..{ref}")
    rows: list[dict[str, Any]] = []
    for commit in raw.splitlines():
        if not commit:
            continue
        rows.append(
            {
                "commit": commit,
                "subject": _git(repo, "show", "-s", "--format=%s", commit),
                "patch_id": _stable_patch_id(repo, commit),
                "affected_paths": sorted(
                    path
                    for path in _git(
                        repo,
                        "diff-tree",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        commit,
                    ).splitlines()
                    if path
                ),
            }
        )
    return rows


def _stable_patch_id(repo: Path, commit: str) -> str | None:
    patch = _git(repo, "show", "--pretty=format:", "--binary", commit)
    if not patch:
        return None
    rendered = _git(repo, "patch-id", "--stable", input_text=patch + "\n")
    return rendered.split()[0] if rendered else None


def build_report(
    repo: Path,
    *,
    candidate_ref: str,
    main_ref: str,
) -> dict[str, Any]:
    root = Path(repo).resolve()
    candidate_sha = _git(root, "rev-parse", candidate_ref)
    main_sha = _git(root, "rev-parse", main_ref)
    merge_base = _git(root, "merge-base", candidate_sha, main_sha)
    divergence_raw = _git(
        root,
        "rev-list",
        "--left-right",
        "--count",
        f"{candidate_sha}...{main_sha}",
    )
    candidate_unique_count, main_unique_count = (
        int(value) for value in divergence_raw.split()
    )
    candidate_rows = _commit_rows(root, merge_base, candidate_sha)
    main_rows = _commit_rows(root, merge_base, main_sha)
    candidate_by_patch: dict[str, list[str]] = {}
    candidate_by_commit = {row["commit"]: row for row in candidate_rows}
    for row in candidate_rows:
        patch_id = row["patch_id"]
        if patch_id:
            candidate_by_patch.setdefault(patch_id, []).append(row["commit"])

    mappings: list[dict[str, Any]] = []
    for row in main_rows:
        patch_id = row["patch_id"]
        counterparts = tuple(candidate_by_patch.get(str(patch_id), ())) if patch_id else ()
        mappings.append(
            {
                "main_commit": row["commit"],
                "main_subject": row["subject"],
                "stable_patch_id": patch_id,
                "candidate_counterparts": list(counterparts),
                "candidate_counterpart_subjects": [
                    candidate_by_commit[commit]["subject"] for commit in counterparts
                ],
                "affected_paths": row["affected_paths"],
                "patch_equivalent": bool(counterparts),
            }
        )

    raw_cherry = _git(root, "cherry", "-v", candidate_sha, main_sha)
    success = bool(mappings) and all(row["patch_equivalent"] for row in mappings)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "clean" if success else "blocked",
        "candidate_ref": candidate_ref,
        "candidate_sha": candidate_sha,
        "main_ref": main_ref,
        "main_sha": main_sha,
        "merge_base": merge_base,
        "divergence": {
            "candidate_unique": candidate_unique_count,
            "main_unique": main_unique_count,
        },
        "main_commit_count": len(main_rows),
        "candidate_commit_count": len(candidate_rows),
        "all_main_patches_present_in_candidate": success,
        "mappings": mappings,
        "git_cherry_command": f"git cherry -v {candidate_sha} {main_sha}",
        "git_cherry_raw": raw_cherry.splitlines(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(
            args.repo,
            candidate_ref=args.candidate_ref,
            main_ref=args.main_ref,
        )
    except GitReconciliationError as exc:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked",
            "error": str(exc),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("status") == "clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())
