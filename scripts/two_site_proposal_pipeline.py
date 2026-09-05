#!/usr/bin/env python3
"""Proposal-only coordinator for fresh Gemini Worker and Reviewer orchestration.

This pipeline is proposal-only and only removes orchestration repetition:
Codex still performs final exact task/source/proposal/review inspection,
exact-byte application, tests and all Git operations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl, json, os, re, stat, subprocess
from pathlib import Path

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT, MODEL, ProposalError, bounded_file_text, file_sha256, git,
        load_task, run as runner_run, unique_json, validate_correction_feedback,
        validate_result, write_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT, MODEL, ProposalError, bounded_file_text, file_sha256, git,
        load_task, run as runner_run, unique_json, validate_correction_feedback,
        validate_result, write_json,
    )

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refactor/two-site-architecture"
PROTECTED_REFS = (
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/candidate/wa-ir-standby-v1",
)
RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")


def ref_snapshot(root: Path) -> dict[str, str | None]:
    result = {}
    for ref in PROTECTED_REFS:
        try:
            result[ref] = git(root, "show-ref", "--verify", "--hash", ref)
        except subprocess.CalledProcessError:
            result[ref] = None
    return result


def check_state(root: Path, head: str, refs: dict[str, str | None], task_path: Path, task: dict, sources: dict) -> None:
    if git(root, "branch", "--show-current") != BRANCH:
        raise ProposalError(f"branch drift: expected {BRANCH}")
    if git(root, "rev-parse", "HEAD") != head:
        raise ProposalError(f"HEAD drift: expected {head}")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ProposalError("worktree drift: untracked or modified files detected")
    if ref_snapshot(root) != refs:
        raise ProposalError("protected refs drift detected")
    cur_task, cur_sources = load_task(root, task_path)
    if cur_task != task or cur_sources != sources:
        raise ProposalError("task or source files changed during execution")


@contextmanager
def pipeline_lock(root: Path):
    root = root.resolve(strict=True)
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    if pipeline_dir.resolve() != pipeline_dir and pipeline_dir.exists():
        raise ProposalError("pipeline storage cannot be a symlink")
    pipeline_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if pipeline_dir.resolve(strict=True) != pipeline_dir or not pipeline_dir.is_relative_to(root):
        raise ProposalError("pipeline storage cannot be a symlink")
    lock_path = pipeline_dir / ".pipeline.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProposalError("cannot safely open pipeline lock") from exc
    owned = False
    try:
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1 or lock_stat.st_uid != os.geteuid():
            raise ProposalError("pipeline lock is not a trusted regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProposalError("another proposal pipeline is already active") from exc
        owned = True
        yield pipeline_dir
    finally:
        if owned:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def normalize_finding(text: str) -> str:
    return " ".join(text.strip().lower().split())


def validate_run(
    root: Path, run_dir: Path, role: str, expected_run_id: str, expected_head: str,
    expected_task: dict, expected_sources: dict, seen_convs: set[str],
    prior_worker: tuple[str, str, str, str] | None = None, expected_feedback_sha: str | None = None,
) -> tuple[dict, dict, str, str]:
    status = unique_json(bounded_file_text(root, run_dir / "status.json", MAX_CONTEXT, f"{role} status"))
    snapshot = unique_json(bounded_file_text(root, run_dir / "task.json", 2 * MAX_CONTEXT, f"{role} task"))
    result_text = bounded_file_text(root, run_dir / "result.json", MAX_CONTEXT, f"{role} result")
    result = unique_json(result_text)
    validate_result(result, expected_task, expected_sources, role)
    result_sha = file_sha256(run_dir / "result.json")
    if (
        status.get("run_id") != expected_run_id or status.get("role") != role
        or status.get("head") != expected_head or status.get("model") != MODEL
        or status.get("result_sha256") != result_sha or snapshot.get("source_head") != expected_head
        or snapshot.get("task") != expected_task or snapshot.get("sources") != expected_sources
    ):
        raise ProposalError(f"metadata or snapshot mismatch in {expected_run_id}")
    if role == "worker":
        if status.get("state") != "PROPOSAL_READY" or result.get("status") != "PROPOSAL_READY":
            raise ProposalError(f"worker did not complete PROPOSAL_READY in {expected_run_id}")
    elif status.get("state") not in {"VERIFIED", "CHANGES_REQUIRED"} or result.get("status") != status.get("state"):
        raise ProposalError(f"reviewer returned invalid state {status.get('state')} in {expected_run_id}")
    if prior_worker is not None:
        prior_run_id, prior_sha, prior_text, prior_conv = prior_worker
        if (
            status.get("input_worker_run_id") != prior_run_id
            or status.get("input_proposal_sha256") != prior_sha
            or status.get("input_worker_conversation_id") != prior_conv
            or snapshot.get("proposal") != prior_text
        ):
            raise ProposalError(f"input worker binding mismatch in {expected_run_id}")
        if expected_feedback_sha is not None and status.get("input_feedback_sha256") != expected_feedback_sha:
            raise ProposalError(f"feedback SHA mismatch in {expected_run_id}")
    elif (
        status.get("input_worker_run_id") is not None
        or status.get("input_worker_conversation_id") is not None
        or status.get("input_proposal_sha256") is not None
    ):
        raise ProposalError(f"initial worker should not have input worker bindings in {expected_run_id}")
    conv_id = status.get("conversation_id")
    if not isinstance(conv_id, str) or not conv_id.strip():
        raise ProposalError(f"conversation_id missing or empty in {expected_run_id}")
    if conv_id in seen_convs:
        raise ProposalError(f"reused conversation_id in {expected_run_id}")
    seen_convs.add(conv_id)
    return result, status, result_sha, result_text


def run_pipeline(root: Path, task_path: Path, run_prefix: str, timeout: int, cli: Path | None, state: dict) -> dict:
    root = root.resolve(strict=True)
    task_candidate = Path(os.path.abspath(task_path if task_path.is_absolute() else root / task_path))
    if not task_candidate.is_relative_to(root):
        raise ProposalError("task path must be inside root")
    if not RUN_PREFIX_RE.fullmatch(run_prefix):
        raise ProposalError("invalid run-prefix: must be canonical identifier up to 60 chars")
    if not 10 <= timeout <= 900:
        raise ProposalError("timeout must be between 10 and 900 seconds")

    with pipeline_lock(root) as pipeline_dir:
        evidence_dir = pipeline_dir / run_prefix
        if evidence_dir.exists():
            raise ProposalError(f"pipeline evidence directory already exists: {run_prefix}")
        evidence_dir.mkdir(mode=0o700)

        if git(root, "branch", "--show-current") != BRANCH:
            raise ProposalError(f"pipeline requires branch {BRANCH}")
        initial_head = git(root, "rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", initial_head):
            raise ProposalError("invalid HEAD commit hash")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ProposalError("pipeline requires clean worktree")
        initial_refs = ref_snapshot(root)
        task, sources = load_task(root, task_candidate)

        state["task_id"] = task["task_id"]
        state["source_head"] = initial_head

        proposals_dir = root / ".local/two-site-refactor/proposals"
        seen_convs: set[str] = set()
        seen_norm_findings: set[str] = set()

        worker_run_id = f"{run_prefix}-worker-0"
        if (proposals_dir / worker_run_id).exists():
            raise ProposalError(f"worker run ID already exists: {worker_run_id}")

        check_state(root, initial_head, initial_refs, task_candidate, task, sources)
        worker_dir = runner_run(root, task_candidate, "worker", worker_run_id, timeout, cli=cli)
        worker_res, worker_st, worker_sha, worker_txt = validate_run(
            root, worker_dir, "worker", worker_run_id, initial_head, task, sources, seen_convs
        )
        current_worker = (worker_run_id, worker_sha, worker_txt, worker_st["conversation_id"])
        prior_changes = worker_res["changes"]
        state.update(
            final_worker_run_id=worker_run_id, proposal_sha256=worker_sha,
            changed_paths=[c["path"] for c in worker_res["changes"]],
            proposal_path=(worker_dir / "result.json").relative_to(root).as_posix(),
        )

        correction_count = 0
        while True:
            check_state(root, initial_head, initial_refs, task_candidate, task, sources)
            reviewer_run_id = f"{run_prefix}-reviewer-{correction_count}"
            if (proposals_dir / reviewer_run_id).exists():
                raise ProposalError(f"reviewer run ID already exists: {reviewer_run_id}")

            worker_result_path = worker_dir / "result.json"
            reviewer_dir = runner_run(
                root, task_candidate, "verifier", reviewer_run_id, timeout,
                proposal=worker_result_path, cli=cli
            )
            reviewer_res, _st, _sha, _txt = validate_run(
                root, reviewer_dir, "verifier", reviewer_run_id, initial_head, task, sources,
                seen_convs, prior_worker=current_worker
            )
            state.update(
                final_reviewer_run_id=reviewer_run_id,
                reviewer_path=(reviewer_dir / "result.json").relative_to(root).as_posix(),
                correction_count=correction_count,
            )
            check_state(root, initial_head, initial_refs, task_candidate, task, sources)

            if reviewer_res["status"] == "VERIFIED":
                if reviewer_res.get("findings"):
                    raise ProposalError("verified result cannot contain findings")
                state["state"] = "READY_FOR_CODEX"
                state["blockers"] = []
                break

            findings = reviewer_res.get("findings", [])
            recurring = [f for f in findings if normalize_finding(f) in seen_norm_findings]
            if recurring:
                raise ProposalError(f"recurring normalized findings: {recurring[0]}")
            for f in findings:
                seen_norm_findings.add(normalize_finding(f))

            if correction_count >= 2:
                raise ProposalError("correction exhaustion: maximum 2 corrections exceeded")

            correction_count += 1
            feedback_path = evidence_dir / f"feedback-correction-{correction_count}.json"
            if feedback_path.exists():
                raise ProposalError(f"feedback path already exists: {feedback_path}")
            feedback_payload = {
                "reviewer": "gemini", "reviewed_proposal_sha256": current_worker[1],
                "findings": findings, "test_evidence": [],
            }
            validate_correction_feedback(json.dumps(feedback_payload), current_worker[1])
            write_json(feedback_path, feedback_payload)
            feedback_sha = file_sha256(feedback_path)

            worker_run_id = f"{run_prefix}-worker-{correction_count}"
            if (proposals_dir / worker_run_id).exists():
                raise ProposalError(f"worker run ID already exists: {worker_run_id}")

            check_state(root, initial_head, initial_refs, task_candidate, task, sources)
            worker_dir = runner_run(
                root, task_candidate, "worker", worker_run_id, timeout,
                feedback=feedback_path, proposal=worker_result_path, cli=cli
            )
            worker_res, worker_st, worker_sha, worker_txt = validate_run(
                root, worker_dir, "worker", worker_run_id, initial_head, task, sources,
                seen_convs, prior_worker=current_worker, expected_feedback_sha=feedback_sha
            )
            if worker_sha == current_worker[1] or worker_res["changes"] == prior_changes:
                raise ProposalError("unchanged correction proposal")

            current_worker = (worker_run_id, worker_sha, worker_txt, worker_st["conversation_id"])
            prior_changes = worker_res["changes"]
            state.update(
                final_worker_run_id=worker_run_id, proposal_sha256=worker_sha,
                changed_paths=[c["path"] for c in worker_res["changes"]],
                proposal_path=(worker_dir / "result.json").relative_to(root).as_posix(),
            )

        check_state(root, initial_head, initial_refs, task_candidate, task, sources)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root directory")
    parser.add_argument("--task", type=Path, required=True, help="path to task definition JSON")
    parser.add_argument("--run-prefix", type=str, required=True, help="canonical unique prefix for run IDs")
    parser.add_argument("--timeout", type=int, default=300, help="timeout per model invocation in seconds")
    parser.add_argument("--cli", type=Path, default=None, help="path to existing Antigravity CLI executable")
    args = parser.parse_args()

    state: dict = {
        "state": "TASK_BLOCKED", "task_id": "", "source_head": "",
        "final_worker_run_id": None, "final_reviewer_run_id": None,
        "proposal_sha256": None, "changed_paths": [], "proposal_path": None,
        "reviewer_path": None, "correction_count": 0, "blockers": [],
    }
    try:
        run_pipeline(args.root, args.task, args.run_prefix, args.timeout, args.cli, state)
    except (ProposalError, OSError, subprocess.SubprocessError) as exc:
        msg = str(exc).strip() or type(exc).__name__
        state["state"], state["blockers"] = "TASK_BLOCKED", [msg[:200]]
    except Exception as exc:
        state["state"], state["blockers"] = "TASK_BLOCKED", [f"{type(exc).__name__}: {exc}"[:200]]

    if state["state"] == "READY_FOR_CODEX":
        state["blockers"] = []
    else:
        state["blockers"] = [b[:200].strip() for b in (state["blockers"] or ["task execution blocked"])[:20] if b.strip()]

    print(json.dumps(state, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0 if state["state"] == "READY_FOR_CODEX" else 1


if __name__ == "__main__":
    raise SystemExit(main())
