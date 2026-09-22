"""Bounded read-only preflight_task implementation for two-site tasks."""
from __future__ import annotations

import json
from pathlib import Path

if __package__:
    from scripts.two_site_assignment_scope import AssignmentFact, AssignmentScopeError
    import scripts.two_site_proposal_runner as runner
    from scripts.two_site_readiness_facts import _collect_session_readiness_issues
else:
    from two_site_assignment_scope import AssignmentFact, AssignmentScopeError
    import two_site_proposal_runner as runner
    from two_site_readiness_facts import _collect_session_readiness_issues

__all__ = ["preflight_task", "prepare_task"]

_MAX_PAYLOAD_BYTES = 16384


def _format_result(
    state: str,
    *,
    issues: list[dict[str, str]],
    task_id: str | None = None,
) -> dict:
    result: dict[str, object] = {
        "schema_version": 1,
        "state": state,
    }
    if task_id is not None:
        result["task_id"] = task_id
    result["issues"] = [dict(issue) for issue in issues]
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise runner.ProposalError("preflight result payload exceeds 16KiB limit")
    return result


def _inspect_task(
    root: Path,
    task_path: Path,
) -> tuple[dict, dict | None]:
    resolved_root = root.resolve(strict=True)
    assignment_facts: list[dict[str, str | None]] = []

    try:
        task, sources = runner._load_task_snapshot(
            resolved_root,
            task_path,
            assignment_facts=assignment_facts,
        )
    except runner._TaskBoundaryError as exc:
        issues = runner._normalize_readiness_issues([exc.to_fact()])
        return _format_result("TASK_BLOCKED", issues=issues), None
    except runner.ProposalError as exc:
        cause = exc.__cause__
        if (
            isinstance(cause, AssignmentScopeError)
            and isinstance(getattr(cause, "fact", None), AssignmentFact)
        ):
            fact = cause.fact
            issue = None
            try:
                issue = runner._readiness_issue(fact.code, fact.path)
            except (runner.ProposalError, runner._TaskBoundaryError):
                try:
                    issue = runner._readiness_issue(fact.code, None)
                except (runner.ProposalError, runner._TaskBoundaryError):
                    issue = None
            if issue is not None:
                return _format_result("TASK_BLOCKED", issues=[issue]), None
        raise exc

    notes = runner.task_preparation_notes(task, sources)
    enriched = runner.enrich_repository_dependency_context(
        resolved_root, task, sources, notes
    )

    dep_ctx = enriched.get("dependency_context") if isinstance(enriched, dict) else None
    model_facts = runner._collect_model_readiness_facts(dep_ctx)
    model_and_asg_issues = runner._normalize_readiness_issues(
        model_facts + assignment_facts
    )
    session_issues = _collect_session_readiness_issues(dep_ctx)

    all_issues = model_and_asg_issues + session_issues
    combined_by_key: dict[tuple[str, str | None], dict[str, str]] = {}
    for issue in all_issues:
        key = (issue["code"], issue.get("path"))
        if key not in combined_by_key:
            combined_by_key[key] = dict(issue)

    sorted_issues = sorted(
        combined_by_key.values(),
        key=lambda item: (
            item["code"],
            0 if item.get("path") is None else 1,
            item.get("path") or "",
        ),
    )
    final_issues = [dict(item) for item in sorted_issues[:16]]

    if final_issues:
        return (
            _format_result(
                "TASK_BLOCKED",
                task_id=task.get("task_id"),
                issues=final_issues,
            ),
            None,
        )

    runner.validate_task_readiness(enriched)
    snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
    ready_details = {
        "state": "TASK_READY",
        "task_id": task["task_id"],
        "source_file_count": len(sources),
        "byte_count": len(snapshot),
        "preparation_notes": enriched,
    }
    report = _format_result(
        "TASK_READY",
        task_id=task.get("task_id"),
        issues=[],
    )
    return report, ready_details


def preflight_task(root: Path, task_path: Path) -> dict:
    """Execute bounded read-only preflight readiness checks for a task."""
    report, _ = _inspect_task(root, task_path)
    return report


def prepare_task(root: Path, task_path: Path) -> dict:
    """Prepare a task returning legacy ready dict or public diagnostic report."""
    report, ready_details = _inspect_task(root, task_path)
    if ready_details is not None:
        return ready_details
    return report
