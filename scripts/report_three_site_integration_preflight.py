#!/usr/bin/env python3
"""Read-only gate immediately before creating the main integration branch."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
from typing import Any


class IntegrationPreflightError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise IntegrationPreflightError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _migration_graph(repo: Path, ref: str) -> dict[str, Any]:
    files = [
        item for item in _git(repo, "ls-tree", "-r", "--name-only", ref, "migrations/versions").splitlines()
        if item.endswith(".py")
    ]
    revisions: dict[str, tuple[str, ...]] = {}
    for file_name in files:
        source = _git(repo, "show", f"{ref}:{file_name}")
        try:
            tree = ast.parse(source, filename=file_name)
        except SyntaxError as exc:
            raise IntegrationPreflightError(f"migration source is invalid at {ref}:{file_name}") from exc
        values: dict[str, Any] = {}
        for node in tree.body:
            name = None
            value_node = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value_node = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value_node = node.target.id, node.value
            if name in {"revision", "down_revision"} and value_node is not None:
                try:
                    values[name] = ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    pass
        revision = values.get("revision")
        raw_down = values.get("down_revision")
        if not isinstance(revision, str):
            continue
        if raw_down is None:
            downs = ()
        elif isinstance(raw_down, str):
            downs = (raw_down,)
        elif isinstance(raw_down, (tuple, list)) and all(isinstance(item, str) for item in raw_down):
            downs = tuple(raw_down)
        else:
            raise IntegrationPreflightError(
                f"migration down_revision is invalid at {ref}:{file_name}"
            )
        revisions[revision] = downs
    referenced = {down for downs in revisions.values() for down in downs}
    heads = sorted(set(revisions) - referenced)
    return {"revision_count": len(revisions), "heads": heads}


def _worktree_status(repo: Path, branch: str) -> dict[str, Any]:
    lines = _git(repo, "worktree", "list", "--porcelain").splitlines()
    current_path: Path | None = None
    current_branch: str | None = None
    matches: list[tuple[Path, str]] = []
    for line in lines + [""]:
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
            current_branch = None
        elif line.startswith("branch "):
            current_branch = line.split(" ", 1)[1].removeprefix("refs/heads/")
        elif not line and current_path is not None:
            if current_branch == branch:
                matches.append((current_path, current_branch))
            current_path = None
    if len(matches) > 1:
        raise IntegrationPreflightError(f"branch {branch} is checked out in multiple worktrees")
    if matches:
        path = matches[0][0]
        dirty = _git(path, "status", "--porcelain=v1").splitlines()
        return {"exists": True, "worktree": str(path), "dirty": bool(dirty), "dirty_count": len(dirty)}
    exists = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0
    return {"exists": exists, "worktree": None, "dirty": None, "dirty_count": None}


def build_report(repo: Path, *, main_ref: str, other_branch: str) -> dict[str, Any]:
    branch = _git(repo, "branch", "--show-current")
    feature_sha = _git(repo, "rev-parse", "HEAD")
    main_sha = _git(repo, "rev-parse", main_ref)
    merge_base = _git(repo, "merge-base", "HEAD", main_ref)
    feature_only, main_only = [
        int(value)
        for value in _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{main_ref}").split()
    ]
    dirty = _git(repo, "status", "--porcelain=v1").splitlines()
    main_files = set(_git(repo, "diff", "--name-only", f"{merge_base}..{main_ref}").splitlines())
    feature_files = set(_git(repo, "diff", "--name-only", f"{merge_base}..HEAD").splitlines())
    semantic_overlap = sorted(main_files & feature_files)
    main_graph = _migration_graph(repo, main_ref)
    feature_graph = _migration_graph(repo, "HEAD")
    other = _worktree_status(repo, other_branch)
    blockers: list[str] = []
    integration_tasks: list[str] = []
    if dirty:
        blockers.append("architecture_branch_worktree_dirty")
    if not other["exists"]:
        blockers.append("other_candidate_branch_missing")
    if other["dirty"] is True:
        blockers.append("other_candidate_branch_has_uncommitted_changes")
    if set(main_graph["heads"]) == set(feature_graph["heads"]):
        migration_merge_required = False
    else:
        migration_merge_required = True
        integration_tasks.append("alembic_semantic_merge_revision_required")
    if semantic_overlap:
        integration_tasks.append("semantic_overlap_requires_intentional_resolution")
    # Divergence and semantic overlap are expected; they are reported as work,
    # not resolved by mutating this branch with main.
    return {
        "schema": "three-site-integration-preflight-v1",
        "status": "ready" if not blockers else "blocked",
        "integration_branch_created": False,
        "architecture_branch": branch,
        "architecture_sha": feature_sha,
        "main_ref": main_ref,
        "main_sha": main_sha,
        "merge_base": merge_base,
        "main_only_commit_count": main_only,
        "architecture_only_commit_count": feature_only,
        "architecture_worktree_dirty_count": len(dirty),
        "other_branch": other_branch,
        "other_branch_state": other,
        "migration": {
            "main": main_graph,
            "architecture": feature_graph,
            "merge_revision_required": migration_merge_required,
        },
        "semantic_overlap_count": len(semantic_overlap),
        "semantic_overlap_files": semantic_overlap,
        "pre_branch_blockers": blockers,
        "expected_integration_tasks": integration_tasks,
        "next_action_only_after_blockers_clear": (
            "create integration branch from the then-current main; merge both completed branches; "
            "resolve migrations and semantic overlap; run one immutable-SHA Full Matrix"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--other-branch", default="candidate/telegram-offer-publition-queue")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = build_report(args.repo.resolve(), main_ref=args.main_ref, other_branch=args.other_branch)
    except IntegrationPreflightError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
