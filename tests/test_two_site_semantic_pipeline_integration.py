"""Focused integration checks for semantic dedup before provider execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import two_site_proposal_pipeline as pipeline
from scripts.two_site_proposal_runner import ProposalError
from scripts.two_site_semantic_attempts import MARKER, SemanticAttemptError
from scripts.two_site_semantic_task import build_identity

HEAD = "a" * 40


def fake_git(_root: Path, *args: str) -> str:
    command = " ".join(args)
    if "branch --show-current" in command:
        return pipeline.BRANCH
    if "rev-parse HEAD" in command:
        return HEAD
    if "status --porcelain" in command:
        return ""
    return "b" * 40 if "show-ref" in command else ""


def marked_task(task_id="T1", scenario="scenario_a", source="a"):
    identity = build_identity(
        stage_id="P1-00b",
        gap_reference="docs/refactor/two-server/PROPOSAL_AUTOMATION.md#semantic-task-dedup",
        scenario_key=scenario,
        symbol_groups=[],
        source_manifest={"src.py": source * 64},
    )
    package = {
        "head": HEAD,
        "gap_reference": identity["gap_reference"],
        "excerpts": [],
        "semantic_identity": identity,
    }
    task = {
        "task_id": task_id,
        "goal": "G" + MARKER + json.dumps(package),
        "context_files": ["src.py"],
        "allowed_files": ["src.py"],
        "requirements": ["R1"],
        "test_selectors": ["tests/test_a.py"],
    }
    return task, identity


def state():
    return {
        "state": "TASK_BLOCKED", "task_id": "", "source_head": "",
        "final_worker_run_id": None, "final_reviewer_run_id": None,
        "proposal_sha256": None, "changed_paths": [], "proposal_path": None,
        "reviewer_path": None, "correction_count": 0, "blockers": [],
        "pre_review_test_path": None, "pre_review_test_sha256": None,
        "pre_review_test_state": None, "semantic_task_fingerprint": None,
    }


def test_duplicate_is_reserved_before_provider_and_scenario_change_proceeds(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    task_path = root / "task.json"
    (root / "src.py").write_text("# orig\n")
    current = {"task": marked_task()[0]}
    task_path.write_text(json.dumps(current["task"]))
    sources = {"src.py": {"sha256": "a" * 64, "content": "# orig\n"}}
    calls = []

    monkeypatch.setattr(pipeline, "git", fake_git)
    monkeypatch.setattr(
        pipeline, "load_task",
        lambda *_args: (copy.deepcopy(current["task"]), copy.deepcopy(sources)),
    )

    def provider_sentinel(*_args, **_kwargs):
        calls.append("provider")
        raise ProposalError("provider sentinel")

    monkeypatch.setattr(pipeline, "runner_run", provider_sentinel)
    with pytest.raises(ProposalError, match="provider sentinel"):
        pipeline.run_pipeline(root, task_path, "run-first", 300, None, state())
    assert calls == ["provider"]
    assert (root / ".local/two-site-refactor/pipeline/run-first").is_dir()

    current["task"]["task_id"] = "T2"
    task_path.write_text(json.dumps(current["task"]))
    with pytest.raises(SemanticAttemptError, match="duplicate semantic task"):
        pipeline.run_pipeline(root, task_path, "run-duplicate", 300, None, state())
    assert calls == ["provider"]
    assert not (root / ".local/two-site-refactor/pipeline/run-duplicate").exists()

    current["task"], changed = marked_task("T3", scenario="scenario_b")
    task_path.write_text(json.dumps(current["task"]))
    with pytest.raises(ProposalError, match="provider sentinel"):
        pipeline.run_pipeline(root, task_path, "run-changed", 300, None, state())
    assert calls == ["provider", "provider"]
    record = root / ".local/two-site-refactor/pipeline/semantic-attempts" / f"{changed['fingerprint']}.json"
    assert record.is_file()


@pytest.mark.parametrize("kind", ["missing", "forged"])
def test_invalid_semantic_marker_stops_before_provider(tmp_path, monkeypatch, kind):
    root = tmp_path.resolve()
    task_path = root / "task.json"
    (root / "src.py").write_text("# orig\n")
    task, _ = marked_task()
    if kind == "missing":
        task["goal"] = "G"
    else:
        prefix, raw = task["goal"].split(MARKER)
        package = json.loads(raw)
        package["semantic_identity"]["fingerprint"] = "0" * 64
        task["goal"] = prefix + MARKER + json.dumps(package)
    task_path.write_text(json.dumps(task))
    calls = []
    monkeypatch.setattr(pipeline, "git", fake_git)
    monkeypatch.setattr(
        pipeline, "load_task",
        lambda *_args: (copy.deepcopy(task), {"src.py": {"sha256": "a" * 64, "content": "# orig\n"}}),
    )
    monkeypatch.setattr(pipeline, "runner_run", lambda *_a, **_k: calls.append("provider"))
    with pytest.raises(SemanticAttemptError):
        pipeline.run_pipeline(root, task_path, f"run-{kind}", 300, None, state())
    assert calls == []
