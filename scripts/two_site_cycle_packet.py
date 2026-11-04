"""Read-only proposal packet and patch helper. Never modifies repository files."""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from scripts import two_site_proposal_runner as runner
    from scripts import two_site_cycle_tools as ct
except ModuleNotFoundError:
    import two_site_proposal_runner as runner
    import two_site_cycle_tools as ct

MAX_OUTPUT_BYTES = 2 * runner.MAX_CONTEXT


def split_lf(text: str) -> list[str]:
    """Split text into lines preserving only LF line endings without splitting on unicode separators."""
    if not text:
        return []
    raw_lines = text.split("\n")
    if text.endswith("\n"):
        return [line + "\n" for line in raw_lines[:-1]]
    lines = [line + "\n" for line in raw_lines[:-1]]
    lines.append(raw_lines[-1])
    return lines


def _hunk_old_lines(hunk: list[str]) -> list[str]:
    """Return the exact old-file fragment represented by one unified-diff hunk."""
    return [line[1:] for line in hunk[1:] if line.startswith((" ", "-"))]


def _fragment_occurrences(lines: list[str], fragment: list[str]) -> int:
    if not fragment:
        return 1 if not lines else 0
    width = len(fragment)
    return sum(lines[index:index + width] == fragment for index in range(len(lines) - width + 1))


def _unique_apply_patch_hunks(old_lines: list[str], new_lines: list[str]) -> list[str]:
    """Produce apply_patch hunks whose old context has exactly one target.

    The apply_patch format does not retain unified-diff line numbers. Three
    lines of context can therefore select the wrong repeated block. Expand
    context geometrically, bounded by the already validated source size, until
    every hunk's old fragment is unique. If this is impossible, fail closed.
    """
    upper = max(3, len(old_lines))
    context = 3
    while True:
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, n=context))
        raw_hunks = diff_lines[2:]
        if not raw_hunks:
            return []
        hunks: list[list[str]] = []
        current: list[str] | None = None
        for line in raw_hunks:
            if line.startswith("@@"):
                current = ["@@\n"]
                hunks.append(current)
            elif current is None:
                raise runner.ProposalError("unified diff did not contain a hunk header")
            else:
                current.append(line)
        if all(_fragment_occurrences(old_lines, _hunk_old_lines(hunk)) == 1 for hunk in hunks):
            return [line for hunk in hunks for line in hunk]
        if context == upper:
            break
        context = min(upper, context * 2)
    raise runner.ProposalError("patch context is not uniquely applicable")


def _prepare_proposal(
    root: Path, task_path: Path, worker: str, reviewer: str, sha: str
) -> tuple[dict, dict, dict, dict, list[dict]]:
    """Validate proposal binding, bounded-read artifacts, recheck all states and return validated changes."""
    task_path = task_path if task_path.is_absolute() else (root / task_path)
    receipt = ct.review(root, task_path, worker, reviewer, sha, applied=False)

    worker_result_path = Path(receipt["worker_result"])
    reviewer_result_path = Path(receipt["reviewer_result"])
    worker_dir = worker_result_path.parent

    limit = runner.worker_result_limit(root, worker_dir)
    worker_text = runner.bounded_file_text(root, worker_result_path, limit, "worker result")
    reviewer_text = runner.bounded_file_text(root, reviewer_result_path, runner.MAX_CONTEXT, "reviewer result")
    snapshot_raw, snapshot = runner.read_task_snapshot(root, worker_dir / "task.json", "worker snapshot")

    worker_res = runner.unique_json(worker_text)
    reviewer_res = runner.unique_json(reviewer_text)

    worker_sha = runner.sha(worker_text.encode("utf-8"))
    if worker_sha != sha or worker_sha != receipt["proposal_sha256"]:
        raise runner.ProposalError("proposal digest mismatch or stale proposal")

    second_receipt = ct.review(root, task_path, worker, reviewer, sha, applied=False)
    if second_receipt != receipt:
        raise runner.ProposalError("stale receipt or binding drift during reading")

    current_head = runner.git(root, "rev-parse", "HEAD")
    if current_head != receipt["head"] or current_head != snapshot.get("source_head"):
        raise runner.ProposalError("HEAD changed after review")

    if runner.git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise runner.ProposalError("working directory is dirty")

    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        raise runner.ProposalError("invalid snapshot sources")

    maintenance = snapshot.get("maintenance_authorization") is not None
    for name, src in sources.items():
        target = (root / name if maintenance else runner.relative_file(root, name))
        if maintenance and (target.resolve(strict=False) != target or not target.is_relative_to(root)):
            raise runner.ProposalError("maintenance source path alias")
        src_sha = src.get("sha256")
        if src_sha is not None:
            disk_bytes = runner.bounded_file_bytes(root, target, limit, "source check")
            if runner.sha(disk_bytes) != src_sha:
                raise runner.ProposalError(f"source {name} modified after review")
        else:
            if os.path.lexists(target):
                raise runner.ProposalError(f"unexpected new source file {name} already exists")

    if not isinstance(worker_res, dict):
        raise runner.ProposalError("invalid worker result")
    for key in ("status", "summary", "covered_requirements", "findings", "changes"):
        if key not in worker_res:
            raise runner.ProposalError(f"worker result missing key: {key}")
    if not isinstance(worker_res["changes"], list):
        raise runner.ProposalError("worker changes must be list")
    if not isinstance(worker_res["covered_requirements"], list):
        raise runner.ProposalError("worker covered_requirements must be list")
    if not isinstance(worker_res["findings"], list):
        raise runner.ProposalError("worker findings must be list")

    if not isinstance(reviewer_res, dict):
        raise runner.ProposalError("invalid reviewer result")
    for key in ("status", "summary", "covered_requirements", "findings"):
        if key not in reviewer_res:
            raise runner.ProposalError(f"reviewer result missing key: {key}")
    if not isinstance(reviewer_res["covered_requirements"], list):
        raise runner.ProposalError("reviewer covered_requirements must be list")
    if not isinstance(reviewer_res["findings"], list):
        raise runner.ProposalError("reviewer findings must be list")

    materialized: list[dict] = []
    for change in worker_res["changes"]:
        if not isinstance(change, dict):
            raise runner.ProposalError("invalid change item")
        path_str = change.get("path")
        if not isinstance(path_str, str) or not path_str:
            raise runner.ProposalError("invalid change path")
        if any(ord(c) < 32 or ord(c) == 127 for c in path_str):
            raise runner.ProposalError(f"patch path contains control characters: {path_str}")
        if maintenance:
            if path_str not in snapshot["task"]["allowed_files"]:
                raise runner.ProposalError("unauthorized maintenance change")
        else:
            runner.relative_file(root, path_str)

        if path_str not in sources:
            raise runner.ProposalError(f"change path {path_str} not in sources")
        src = sources[path_str]
        orig_sha = src.get("sha256")
        orig_content = src.get("content")
        if change.get("original_sha256") != orig_sha:
            raise runner.ProposalError(f"original_sha256 mismatch for {path_str}")

        is_new = (orig_sha is None)
        new_content = change.get("content")
        if not isinstance(new_content, str):
            raise runner.ProposalError(f"content for {path_str} must be string")

        if "\0" in new_content or "\r" in new_content:
            raise runner.ProposalError(f"unrepresentable content in {path_str}")
        if orig_content is not None and ("\0" in orig_content or "\r" in orig_content):
            raise runner.ProposalError(f"unrepresentable content in original {path_str}")

        if new_content and not new_content.endswith("\n"):
            raise runner.ProposalError(f"{path_str} missing final newline")
        if orig_content is not None and orig_content and not orig_content.endswith("\n"):
            raise runner.ProposalError(f"original {path_str} missing final newline")

        materialized.append({
            "path": path_str,
            "is_new": is_new,
            "orig_sha": orig_sha,
            "orig_content": orig_content,
            "new_content": new_content,
        })

    return receipt, snapshot, worker_res, reviewer_res, materialized


def packet(
    root: Path, task_path: Path, worker: str, reviewer: str, sha: str, full: bool = False
) -> str:
    """Emit concise metadata, requirements, reviewer summary and changed code once."""
    receipt, snapshot, worker_res, reviewer_res, materialized = _prepare_proposal(
        root, task_path, worker, reviewer, sha
    )
    task = snapshot.get("task", {})
    task_id = task.get("task_id", "unknown")
    requirements = task.get("requirements", [])

    out: list[str] = [
        "=" * 72 + "\n",
        "PROPOSAL PACKET\n",
        "=" * 72 + "\n",
        f"Task ID: {task_id}\n",
        f"Proposal SHA256: {sha}\n",
        f"Head: {receipt['head']}\n",
        f"Worker Run ID: {worker}\n",
        f"Reviewer Run ID: {reviewer}\n",
        "Semantic Review: REQUIRED_BY_CODEX\n",
        "Tests: NOT_RUN\n\n",
        "-" * 72 + "\n",
        "REQUIREMENTS\n",
        "-" * 72 + "\n",
    ]
    if requirements:
        for req in requirements:
            out.append(f"- {req}\n")
    else:
        out.append("None\n")
    out.append("\n")

    out.extend([
        "-" * 72 + "\n",
        "WORKER SUMMARY\n",
        "-" * 72 + "\n",
        f"Status: {worker_res['status']}\n",
        f"Summary: {worker_res['summary']}\n",
        f"Covered Requirements: {json.dumps(worker_res['covered_requirements'])}\n",
        f"Findings: {json.dumps(worker_res['findings'])}\n\n",
        "-" * 72 + "\n",
        "REVIEWER SUMMARY\n",
        "-" * 72 + "\n",
        f"Status: {reviewer_res['status']}\n",
        f"Summary: {reviewer_res['summary']}\n",
        f"Covered Requirements: {json.dumps(reviewer_res['covered_requirements'])}\n",
        f"Findings: {json.dumps(reviewer_res['findings'])}\n\n",
        "-" * 72 + "\n",
        "PROPOSED CODE CHANGES\n",
        "-" * 72 + "\n",
    ])

    for item in materialized:
        path = item["path"]
        is_new = item["is_new"]
        orig_sha = item["orig_sha"]
        new_content = item["new_content"]
        new_sha = runner.sha(new_content.encode("utf-8"))

        if is_new:
            out.append(f"--- New File: {path} (sha256: {new_sha}) ---\n")
            out.append(new_content)
        else:
            if full:
                out.append(f"--- File: {path} (full source, sha256: {new_sha}, original: {orig_sha}) ---\n")
                out.append(new_content)
            else:
                out.append(f"--- Modified File: {path} (diff, sha256: {new_sha}, original: {orig_sha}) ---\n")
                old_lines = split_lf(item["orig_content"])
                new_lines = split_lf(new_content)
                diff = "".join(difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"a/{path}", tofile=f"b/{path}",
                    n=3,
                ))
                out.append(diff)

    result = "".join(out)
    if len(result.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise runner.ProposalError("packet output exceeds safe context budget")
    return result


def patch(root: Path, task_path: Path, worker: str, reviewer: str, sha: str) -> str:
    """Emit only an apply_patch-compatible patch for the complete validated proposal."""
    receipt, snapshot, worker_res, reviewer_res, materialized = _prepare_proposal(
        root, task_path, worker, reviewer, sha
    )
    patch_lines: list[str] = ["*** Begin Patch\n"]
    for item in materialized:
        path = item["path"]
        is_new = item["is_new"]
        new_content = item["new_content"]
        orig_content = item["orig_content"]

        if is_new:
            patch_lines.append(f"*** Add File: {path}\n")
            for line in split_lf(new_content):
                patch_lines.append(f"+{line}")
        else:
            if new_content == orig_content:
                continue
            if new_content.endswith("\n\n") or (orig_content is not None and orig_content.endswith("\n\n")):
                raise runner.ProposalError(
                    f"unsupported update with trailing blank lines in {path}"
                )
            old_lines = split_lf(orig_content)
            new_lines = split_lf(new_content)
            hunks = _unique_apply_patch_hunks(old_lines, new_lines)
            if not hunks:
                continue
            patch_lines.append(f"*** Update File: {path}\n")
            patch_lines.extend(hunks)

    patch_lines.append("*** End Patch\n")
    result = "".join(patch_lines)
    if len(result.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise runner.ProposalError("patch output exceeds safe context budget")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=runner.ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    packet_p = sub.add_parser("packet")
    packet_p.add_argument("--task", type=Path, required=True)
    packet_p.add_argument("--worker", required=True)
    packet_p.add_argument("--reviewer", required=True)
    packet_p.add_argument("--sha", required=True)
    packet_p.add_argument("--full", action="store_true")

    patch_p = sub.add_parser("patch")
    patch_p.add_argument("--task", type=Path, required=True)
    patch_p.add_argument("--worker", required=True)
    patch_p.add_argument("--reviewer", required=True)
    patch_p.add_argument("--sha", required=True)

    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    task_path = args.task if args.task.is_absolute() else (root / args.task)

    try:
        if args.command == "packet":
            out = packet(root, task_path, args.worker, args.reviewer, args.sha, full=args.full)
        elif args.command == "patch":
            out = patch(root, task_path, args.worker, args.reviewer, args.sha)
        else:
            return 1
        sys.stdout.write(out)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
