"""Real preparation/pipeline boundaries reject immutable malformed byte profiles."""
from __future__ import annotations

import copy
import json
import subprocess

import pytest

from scripts import two_site_proposal_byte_profile as profile
from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_task_preflight as preflight
from scripts.two_site_proposal_runner import ProposalError
from tests.two_site_preflight_support import make_scoped_task


def declaration(*, reverse=False):
    limits = {"b.py": 12000, "a.py": 8000} if reverse else {"a.py": 8000, "b.py": 12000}
    value = {"file_limits": limits, "total_limit": 20000, "version": "1.0"}
    # reverse=True keeps only the nested ordering invalid: the real incident.
    return profile.PREFIX + json.dumps(value, separators=(",", ":"))


def write(root, path, task):
    path.write_text(json.dumps(task), encoding="utf-8")
    return copy.deepcopy(task)


def fixture(tmp_path, monkeypatch, requirements):
    root, path, task = make_scoped_task(
        tmp_path, monkeypatch, allowed_files=["a.py", "b.py"],
        requirements=requirements,
    )
    return root, path, task


BAD = [
    [declaration(reverse=True)],
    [declaration(), declaration() + " "],
    [profile.PREFIX + '{"file_limits":{"a.py":8000},"total_limit":20000,"version":"1.0"}'],
    [profile.PREFIX + '{"file_limits":{"a.py":true,"b.py":12000},"total_limit":20000,"version":"1.0"}'],
    [profile.PREFIX + '{"file_limits":{"a.py":8000,"a.py":8000,"b.py":12000},"total_limit":20000,"version":"1.0"}'],
]


@pytest.mark.parametrize("requirements", BAD)
def test_preparation_rejects_before_context_enrichment(tmp_path, monkeypatch, requirements):
    root, path, task = fixture(tmp_path, monkeypatch, requirements)
    original = path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid profile must not reach enrichment or Provider")

    monkeypatch.setattr(preflight.runner, "task_preparation_notes", forbidden)
    monkeypatch.setattr(preflight.runner, "enrich_repository_dependency_context", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    report = preflight.preflight_task(root, path)
    assert report["state"] == "TASK_BLOCKED"
    assert "task_id" not in report
    assert report["issues"][0]["code"] == "TASK_SCHEMA_INVALID"
    assert path.read_bytes() == original
    assert not (root / ".local").exists()
    assert preflight.prepare_task(root, path) == report


@pytest.mark.parametrize("requirements", BAD)
def test_pipeline_task_boundary_rejects_with_fixed_error(tmp_path, monkeypatch, requirements):
    root, path, task = fixture(tmp_path, monkeypatch, requirements)
    original = path.read_bytes()
    with pytest.raises(ProposalError) as caught:
        pipeline.load_task(root, path)
    assert str(caught.value) == profile.ERROR
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert path.read_bytes() == original
    assert not (root / ".local").exists()


@pytest.mark.parametrize("requirements", [["ordinary legacy requirement"], [declaration()]])
def test_valid_and_legacy_tasks_are_unchanged(tmp_path, monkeypatch, requirements):
    root, path, task = fixture(tmp_path, monkeypatch, requirements)
    original = path.read_bytes()
    loaded, sources = pipeline.load_task(root, path)
    assert loaded == task
    assert set(sources) == {"a.py", "b.py"}
    assert sources["a.py"]["content"] == "# inert a\n"
    report = preflight.preflight_task(root, path)
    prepared = preflight.prepare_task(root, path)
    assert report["state"] == prepared["state"] == "TASK_READY"
    assert report["issues"] == []
    assert path.read_bytes() == original


def test_actual_pipeline_rejects_before_reservation_and_role(tmp_path, monkeypatch):
    root, path, task = fixture(tmp_path, monkeypatch, [declaration(reverse=True)])
    (root / ".gitignore").write_text(".local/\n", encoding="utf-8")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    git("init", "-b", pipeline.BRANCH)
    git("config", "user.name", "LAB profile boundary")
    git("config", "user.email", "lab@example.invalid")
    git("add", "a.py", "b.py", "task.json", ".gitignore")
    git("commit", "-m", "LAB immutable invalid declaration")
    for name in pipeline.PROTECTED_REFS:
        git("update-ref", name, "HEAD")
    original = path.read_bytes()
    head = git("rev-parse", "HEAD")
    real_popen = subprocess.Popen

    def only_git(command, *args, **kwargs):
        assert command[0] in {"git", "/usr/bin/git"}, "only read-only Git checks may spawn"
        return real_popen(command, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("no semantic reservation or Provider for invalid task")

    monkeypatch.setattr(subprocess, "Popen", only_git)
    monkeypatch.setattr(pipeline, "reserve_semantic_attempt", forbidden)
    monkeypatch.setattr(pipeline, "run_provider_session", forbidden)
    state = {}
    with pytest.raises(ProposalError, match="^" + profile.ERROR + "$"):
        pipeline.run_pipeline(root, path, "LAB-invalid-profile", 10, None, state)
    assert state == {}
    assert git("rev-parse", "HEAD") == head
    assert path.read_bytes() == original
    base = root / ".local/two-site-refactor"
    assert not (base / "pipeline/semantic-attempts").exists()
    assert not (base / "pipeline/LAB-invalid-profile").exists()
    assert not (base / "proposals").exists()


@pytest.mark.parametrize("subject", ["preflight", "pipeline"])
def test_base_exception_is_not_normalized_or_retried(tmp_path, monkeypatch, subject):
    root, path, task = fixture(tmp_path, monkeypatch, [declaration()])

    class Interrupt(BaseException):
        pass

    interrupted = Interrupt()

    def interrupt(*args):
        raise interrupted

    module = preflight if subject == "preflight" else pipeline
    monkeypatch.setattr(module, "parse_proposal_utf8_budget_profile", interrupt)
    with pytest.raises(Interrupt) as caught:
        module.preflight_task(root, path) if subject == "preflight" else module.load_task(root, path)
    assert caught.value is interrupted
    assert not (root / ".local").exists()
