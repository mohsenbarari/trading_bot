from __future__ import annotations

import contextlib
import json
from unittest import mock
import pytest

from scripts.two_site_proposal_runner import (
    MAX_CONTEXT,
    ProposalError,
    _validate_task_snapshot_budget,
    run,
    serialize_json,
    sha,
)


def test_validate_task_snapshot_budget_boundaries():
    overhead = len(serialize_json({"k": ""}))
    exact_pad = "a" * (2 * MAX_CONTEXT - overhead)
    _validate_task_snapshot_budget({"k": exact_pad})

    with pytest.raises(ProposalError, match="task snapshot exceeds context budget"):
        _validate_task_snapshot_budget({"k": exact_pad + "a"})

    with pytest.raises(ProposalError, match="task snapshot exceeds context budget"):
        _validate_task_snapshot_budget({"k": "م" * 160_000})

    with pytest.raises(ProposalError, match="task snapshot exceeds context budget"):
        _validate_task_snapshot_budget({"k": "\\" * 160_000})


def test_run_verifier_overbudget_snapshot_rejects_before_provider(tmp_path, monkeypatch):
    import scripts.two_site_proposal_runner as runner

    for var in (
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
        "TWO_SITE_ASSIGNMENT_ID",
        "TWO_SITE_ASSIGNMENT_SHA256",
        runner.AUTOPILOT_RUN_ID_ENV,
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(runner, "validate_runtime_contract", lambda root: {})
    src = "a = 100\n" * 19700
    task = {
        "task_id": "T1",
        "goal": "g",
        "context_files": ["f.py"],
        "allowed_files": ["f.py"],
        "requirements": ["r"],
        "test_selectors": ["tests/test_f.py"],
    }
    sources = {"f.py": {"sha256": sha(src.encode("utf-8")), "content": src}}
    monkeypatch.setattr(runner, "load_task", lambda root, tp: (task, sources))
    monkeypatch.setattr(runner, "enrich_repository_dependency_context", lambda r, t, s, n: {})
    monkeypatch.setattr(runner, "validate_task_readiness", lambda n: None)
    monkeypatch.setattr(runner, "git", lambda r, *args: "head123")

    prop = {
        "status": "PROPOSAL_READY",
        "summary": "s",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": "f.py",
                "original_sha256": sources["f.py"]["sha256"],
                "content": src + "b = 2\n",
            }
        ],
    }
    prop_text = json.dumps(prop)
    prop_sha = sha(prop_text.encode("utf-8"))
    monkeypatch.setattr(
        runner,
        "bounded_file_text",
        lambda r, p, l, k: prop_text if k == "proposal" else "PROMPT_TEMPLATE",
    )
    status = {
        "role": "worker",
        "state": "PROPOSAL_READY",
        "run_id": "w1",
        "conversation_id": "c1",
        "result_sha256": prop_sha,
    }
    monkeypatch.setattr(
        runner, "load_prior_worker", lambda r, p, pt, t, s, h: (prop, status, prop_sha)
    )

    cli_called = False

    @contextlib.contextmanager
    def trap_cli(r, c=None):
        nonlocal cli_called
        cli_called = True
        yield tmp_path, tmp_path / "cli", {}

    monkeypatch.setattr(runner, "proposal_cli_context", trap_cli)
    cli_id_mock = mock.Mock()
    monkeypatch.setattr(runner, "cli_identity", cli_id_mock)

    with pytest.raises(ProposalError, match="task snapshot exceeds context budget"):
        run(
            tmp_path,
            tmp_path / "task.json",
            "verifier",
            "run-1",
            30,
            proposal=tmp_path / "prop.json",
        )

    assert not cli_called
    cli_id_mock.assert_not_called()
    assert not list(tmp_path.glob("run-1*"))
