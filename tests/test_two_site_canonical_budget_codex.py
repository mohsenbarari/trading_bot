"""Hermetic tests for canonical >64000 budget acceptance and strict gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from scripts import two_site_assignment_scope as scope_mod
from scripts import two_site_codex_autopilot as autopilot
from scripts import two_site_proposal_runner as runner
from tests.two_site_generation_fixture import seal_completion_fixture


@pytest.fixture
def canonical_env(tmp_path: Path):
    assignment_id = "ASSIGN-BUDGET-001"
    target_path = "tests/test_target.py"
    head = "a" * 40

    existing_content = "# " + ("C" * 70000) + "\nx = 0\n"
    existing_sha = hashlib.sha256(existing_content.encode("utf-8")).hexdigest()
    sources = {target_path: {"sha256": existing_sha, "content": existing_content}}

    task = {
        "task_id": "TEST-TASK-001",
        "goal": f"Execute task for {assignment_id} within scope.",
        "context_files": [],
        "allowed_files": [target_path],
        "requirements": ["1. Update x to 1."],
        "test_selectors": [target_path],
        "preparation_notes": {
            "output_contract": {
                "test_only_task": True,
                "max_test_only_transport_bytes": 64000,
                "max_new_test_file_utf8_bytes": 24000,
                "new_test_files": [],
            }
        },
    }

    raw_worker_result = {
        "status": "PROPOSAL_READY",
        "summary": "updated x to 1",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": target_path,
                "original_sha256": existing_sha,
                "content": None,
                "replacements": [{"old": "x = 0\n", "new": "x = 1\n"}],
            }
        ],
    }

    raw_stream = (
        json.dumps({
            "event": "init",
            "conversation_id": "wc-1",
            "init": {"model": runner.WORKER_MODEL, "conversation_id": "wc-1"},
        })
        + "\n"
        + json.dumps({
            "event": "result",
            "conversation_id": "wc-1",
            "result": {
                "conversation_id": "wc-1",
                "status": "SUCCESS",
                "structured_output": raw_worker_result,
            },
        })
        + "\n"
    )

    canonical_result, _ = runner._parse_stream(
        raw_stream, task, sources, "worker", expected_model=runner.WORKER_MODEL
    )
    canonical_len = len(runner.serialize_json(canonical_result))
    assert 64000 < canonical_len < runner.MAX_CONTEXT

    scope = scope_mod.AssignmentScope(
        assignment_id=assignment_id,
        stage_id="P1-00b",
        allowed_paths=[target_path],
        forbidden_paths=["forbidden.py"],
        expires_at="2030-01-01T00:00:00Z",
        sha256="0" * 64,
    )

    base = tmp_path / ".local/two-site-refactor/proposals"
    worker_dir = base / "w1"
    reviewer_dir = base / "r1"
    worker_dir.mkdir(parents=True)
    reviewer_dir.mkdir(parents=True)

    worker_result_text = json.dumps(canonical_result, ensure_ascii=False, indent=2) + "\n"
    (worker_dir / "result.json").write_text(worker_result_text)
    worker_digest = runner.file_sha256(worker_dir / "result.json")

    snapshot = {"source_head": head, "task": task, "sources": sources}
    runner.write_json(worker_dir / "task.json", snapshot)
    runner.write_json(
        worker_dir / "status.json",
        {
            "run_id": "w1",
            "role": "worker",
            "head": head,
            "model": runner.WORKER_MODEL,
            "state": "PROPOSAL_READY",
            "conversation_id": "wc-1",
            "result_sha256": worker_digest,
        },
    )

    reviewer_result = {
        "status": "VERIFIED",
        "summary": "verified cleanly",
        "covered_requirements": [1],
        "findings": [],
        "changes": [],
    }
    (reviewer_dir / "result.json").write_text(json.dumps(reviewer_result) + "\n")
    runner.write_json(
        reviewer_dir / "task.json",
        {**snapshot, "proposal": worker_result_text},
    )
    runner.write_json(
        reviewer_dir / "status.json",
        {
            "run_id": "r1",
            "role": "verifier",
            "head": head,
            "model": runner.MODEL,
            "state": "VERIFIED",
            "conversation_id": "rc-2",
            "result_sha256": runner.file_sha256(reviewer_dir / "result.json"),
            "input_worker_run_id": "w1",
            "input_proposal_sha256": worker_digest,
        },
    )

    seal_completion_fixture(tmp_path, worker_dir)
    seal_completion_fixture(tmp_path, reviewer_dir)
    cycle_result = {
        "gemini_worker_run_id": "w1",
        "gemini_reviewer_run_id": "r1",
        "proposal_sha256": worker_digest,
    }

    return {
        "root": tmp_path,
        "head": head,
        "task": task,
        "sources": sources,
        "scope": scope,
        "canonical_result": canonical_result,
        "cycle_result": cycle_result,
        "worker_dir": worker_dir,
        "reviewer_dir": reviewer_dir,
    }


def test_canonical_budget_accepted_scoped_and_unscoped(canonical_env):
    env = canonical_env
    res1 = autopilot.validate_gemini_chain(env["root"], env["head"], env["cycle_result"])
    assert res1 == env["canonical_result"]
    res2 = autopilot.validate_scoped_gemini_chain(
        env["root"], env["head"], env["cycle_result"], env["scope"]
    )
    assert res2 == env["canonical_result"]


def test_default_validate_result_rejects_raw_budget(canonical_env):
    env = canonical_env
    with pytest.raises(runner.ProposalError, match="test-only raw transport exceeds budget"):
        runner.validate_result(env["canonical_result"], env["task"], env["sources"], "worker")


def test_forbidden_path_scope_rejection(canonical_env):
    env = canonical_env
    task_forbidden = dict(env["task"])
    task_forbidden["allowed_files"] = ["forbidden.py"]
    runner.write_json(env["worker_dir"] / "task.json", {
        "source_head": env["head"],
        "task": task_forbidden,
        "sources": env["sources"],
    })
    with pytest.raises(autopilot.AutopilotError, match="assignment scope validation failed"):
        autopilot.validate_scoped_gemini_chain(
            env["root"], env["head"], env["cycle_result"], env["scope"]
        )


@pytest.mark.parametrize("mutation", [
    "digest", "head", "task", "source", "reviewer_session",
])
def test_binding_mutations_rejected(canonical_env, mutation):
    env = canonical_env
    root, head, cycle_res = env["root"], env["head"], dict(env["cycle_result"])
    if mutation == "digest":
        cycle_res["proposal_sha256"] = "0" * 64
    elif mutation == "head":
        head = "b" * 40
    elif mutation == "task":
        runner.write_json(env["reviewer_dir"] / "task.json", {
            "source_head": head,
            "task": {**env["task"], "requirements": ["1. Update", "2. Extra"]},
            "sources": env["sources"],
            "proposal": (env["worker_dir"] / "result.json").read_text(),
        })
    elif mutation == "source":
        runner.write_json(env["reviewer_dir"] / "task.json", {
            "source_head": head,
            "task": env["task"],
            "sources": {},
            "proposal": (env["worker_dir"] / "result.json").read_text(),
        })
    elif mutation == "reviewer_session":
        rev_status = json.loads((env["reviewer_dir"] / "status.json").read_text())
        rev_status["conversation_id"] = "wc-1"
        runner.write_json(env["reviewer_dir"] / "status.json", rev_status)

    with pytest.raises(autopilot.AutopilotError):
        autopilot.validate_gemini_chain(root, head, cycle_res)


def test_exceeds_max_context_rejected(canonical_env):
    env = canonical_env
    (env["worker_dir"] / "result.json").write_text(" " * (runner.MAX_CONTEXT + 10))
    with pytest.raises((runner.ProposalError, autopilot.AutopilotError)):
        autopilot.validate_gemini_chain(env["root"], env["head"], env["cycle_result"])
