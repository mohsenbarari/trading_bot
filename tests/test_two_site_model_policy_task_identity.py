"""Public model policy is a real source input for newly built Tasks only."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts import two_site_cycle_tools as tools
from scripts import two_site_proposal_runner as runner
from scripts.two_site_ai_config import AI_CONFIG_REL
from scripts.two_site_semantic_attempts import reserve_semantic_attempt, SemanticAttemptError
from tests.test_two_site_cycle_tools import repo, task


def build(root, suffix, context=None):
    value = task()
    value["task_id"] = "MODEL-POLICY-" + suffix
    if context is not None:
        value["context_files"] = context
    spec = root / ("spec-" + suffix + ".json")
    spec.write_text(json.dumps({"task": value, "snippets": [], "gap_reference": "gap.md#gap-a",
                                "scenario_key": "same_actual_outcome"}))
    before = spec.read_bytes()
    output = root / ".local/two-site-refactor/tasks" / (suffix + ".json")
    report = tools.build(root, spec, output)
    assert report["state"] == "TASK_READY" and spec.read_bytes() == before
    built, sources = runner.load_task(root, output)
    return output, built, sources, report


def test_policy_changes_new_identity_without_resetting_old_reservation(repo):
    root, git = repo
    policy_path = AI_CONFIG_REL.as_posix()
    first_path, first, sources, report = build(root, "first")
    old_task_bytes = first_path.read_bytes()
    assert first["context_files"] == ["gap.md", policy_path]
    assert sources[policy_path]["sha256"] == hashlib.sha256((root / AI_CONFIG_REL).read_bytes()).hexdigest()
    identity = json.loads(first["goal"].split(tools.MARKER, 1)[1])["semantic_identity"]
    assert identity["source_manifest"][policy_path] == sources[policy_path]["sha256"]
    pipeline = root / ".local/two-site-refactor/pipeline"
    pipeline.mkdir()
    reserve_semantic_attempt(root, pipeline, first, first["task_id"], "original-policy")
    old_records = {path: path.read_bytes() for path in pipeline.rglob("*") if path.is_file()}

    _duplicate_path, duplicate, _sources, duplicate_report = build(root, "duplicate")
    assert duplicate_report["semantic_task_fingerprint"] == report["semantic_task_fingerprint"]
    with pytest.raises(SemanticAttemptError, match="duplicate semantic task"):
        reserve_semantic_attempt(root, pipeline, duplicate, duplicate["task_id"], "same-policy")

    policy = json.loads((root / AI_CONFIG_REL).read_text())
    policy["roles"]["worker"]["model"] = "gemini-another-public-model"
    (root / AI_CONFIG_REL).write_text(json.dumps(policy, indent=2) + "\n")
    git("add", "--", policy_path)
    git("commit", "-m", "change public model policy")
    _new_path, changed, changed_sources, changed_report = build(root, "changed")
    assert changed_report["semantic_task_fingerprint"] != report["semantic_task_fingerprint"]
    assert changed_sources[policy_path]["sha256"] != sources[policy_path]["sha256"]
    reserve_semantic_attempt(root, pipeline, changed, changed["task_id"], "new-policy")
    assert first_path.read_bytes() == old_task_bytes
    assert all(path.read_bytes() == raw for path, raw in old_records.items())


def test_existing_policy_context_is_not_duplicated(repo):
    root, _git = repo
    policy_path = AI_CONFIG_REL.as_posix()
    _path, built, _sources, _report = build(root, "explicit", ["gap.md", policy_path])
    assert built["context_files"].count(policy_path) == 1


def test_full_context_capacity_is_rejected_without_output_or_truncation(repo):
    root, _git = repo
    original = [f"context-{number}.md" for number in range(32)]
    with pytest.raises(runner.ProposalError, match="model-policy source requires one context slot"):
        build(root, "full", original)
    assert not (root / ".local/two-site-refactor/tasks/full.json").exists()
    assert original == [f"context-{number}.md" for number in range(32)]


def test_legacy_repository_without_model_policy_keeps_original_task(repo):
    root, git = repo
    git("rm", "--", AI_CONFIG_REL.as_posix())
    git("commit", "-m", "legacy fixture without public model policy")
    _path, built, sources, _report = build(root, "legacy")
    assert built["context_files"] == ["gap.md"] and AI_CONFIG_REL.as_posix() not in sources
