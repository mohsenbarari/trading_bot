"""Offline tests for immutable semantic-attempt dedup evidence."""
from __future__ import annotations

import concurrent.futures
import copy
import json
import os
from pathlib import Path

import pytest

from scripts import two_site_semantic_attempts as attempts
from scripts.two_site_semantic_task import build_identity


def identity(stage="P1", gap="gap.md#g", scenario="s1", symbols=None, source="a"):
    return build_identity(
        stage, gap, scenario,
        [{"path": "code.py", "symbols": symbols or ["f"]}],
        {"code.py": source * 64},
    )


def task(value):
    package = {"semantic_identity": value}
    return {"task_id": "T1", "goal": "work" + attempts.MARKER + json.dumps(package)}


def dirs(tmp_path):
    root = tmp_path / "root"
    pipeline = root / ".local" / "pipeline"
    pipeline.mkdir(parents=True)
    return root, pipeline


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate", "forged"])
def test_extract_is_strict_and_detached(mutation):
    expected = identity()
    value = task(expected)
    if mutation == "missing":
        value["goal"] = "work"
    elif mutation == "duplicate":
        value["goal"] += attempts.MARKER + "{}"
    elif mutation == "forged":
        package = json.loads(value["goal"].split(attempts.MARKER)[1])
        package["semantic_identity"]["fingerprint"] = "0" * 64
        value["goal"] = "work" + attempts.MARKER + json.dumps(package)
    if mutation != "none":
        with pytest.raises(attempts.SemanticAttemptError):
            attempts.extract_semantic_identity(value)
        return
    actual = attempts.extract_semantic_identity(value)
    actual["source_manifest"]["code.py"] = "b" * 64
    assert expected["source_manifest"]["code.py"] == "a" * 64


def test_reserve_exact_and_duplicate(tmp_path):
    root, pipeline = dirs(tmp_path)
    original = identity()
    first = attempts.reserve_semantic_attempt(root, pipeline, task(original), "T1", "run-1", now=10)
    path = pipeline / "semantic-attempts" / f"{original['fingerprint']}.json"
    assert first["state"] == "RESERVED"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text()) == first
    first["task_id"] = "changed"
    assert json.loads(path.read_text())["task_id"] == "T1"
    with pytest.raises(attempts.SemanticAttemptError, match="duplicate semantic task"):
        attempts.reserve_semantic_attempt(root, pipeline, task(original), "T2", "run-2", now=11)


@pytest.mark.parametrize("field", ["stage", "gap", "scenario", "symbols", "source"])
def test_changed_semantics_get_distinct_reservation(tmp_path, field):
    root, pipeline = dirs(tmp_path)
    base = identity()
    attempts.reserve_semantic_attempt(root, pipeline, task(base), "T1", "run-1", now=1)
    args = {"stage": "P1", "gap": "gap.md#g", "scenario": "s1", "symbols": ["f"], "source": "a"}
    args[field] = {"stage": "P2", "gap": "gap.md#other", "scenario": "s2", "symbols": ["g"], "source": "b"}[field]
    changed = identity(**args)
    assert changed["fingerprint"] != base["fingerprint"]
    assert attempts.reserve_semantic_attempt(root, pipeline, task(changed), "T2", "run-2")["fingerprint"] == changed["fingerprint"]


@pytest.mark.parametrize("case", ["task", "prefix", "bool", "nan", "root", "pipeline"])
def test_bad_metadata_and_roots_fail(tmp_path, case):
    root, pipeline = dirs(tmp_path)
    values = {"root": root, "pipeline_dir": pipeline, "task": task(identity()), "task_id": "T1", "run_prefix": "run-1", "now": 1}
    if case == "task": values["task_id"] = " bad"
    elif case == "prefix": values["run_prefix"] = "-bad"
    elif case == "bool": values["now"] = True
    elif case == "nan": values["now"] = float("nan")
    elif case == "root": values["root"] = tmp_path / "missing"
    elif case == "pipeline": values["pipeline_dir"] = tmp_path
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.reserve_semantic_attempt(**values)


@pytest.mark.parametrize("case", ["symlink", "hardlink", "malformed", "oversize", "mode"])
def test_untrusted_existing_records_fail_closed(tmp_path, case):
    root, pipeline = dirs(tmp_path)
    value = identity()
    attempts.reserve_semantic_attempt(root, pipeline, task(value), "T1", "run-1", now=1)
    path = pipeline / "semantic-attempts" / f"{value['fingerprint']}.json"
    if case == "symlink":
        path.unlink(); outside = tmp_path / "outside"; outside.write_text("{}"); path.symlink_to(outside)
    elif case == "hardlink":
        os.link(path, pipeline / "semantic-attempts" / "alias.json")
    elif case == "malformed": path.write_text("{bad")
    elif case == "oversize": path.write_text("x" * 5000)
    elif case == "mode": path.chmod(0o644)
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.reserve_semantic_attempt(root, pipeline, task(value), "T2", "run-2")


def test_short_writes_and_concurrent_winner(tmp_path, monkeypatch):
    root, pipeline = dirs(tmp_path)
    real_write = os.write
    monkeypatch.setattr(attempts.os, "write", lambda fd, data: real_write(fd, data[:max(1, len(data) // 3)]))
    value = identity()
    results = []
    errors = []
    def run(index):
        try: results.append(attempts.reserve_semantic_attempt(root, pipeline, task(value), f"T{index}", f"run-{index}"))
        except attempts.SemanticAttemptError as exc: errors.append(exc)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(8)))
    assert len(results) == 1 and len(errors) == 7
    path = pipeline / "semantic-attempts" / f"{value['fingerprint']}.json"
    assert json.loads(path.read_text())["fingerprint"] == value["fingerprint"]
