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


def test_symlink_alias_root_fails_even_with_canonical_pipeline(tmp_path):
    root, pipeline = dirs(tmp_path)
    alias_root = tmp_path / "root_alias"
    alias_root.symlink_to(root)
    val = identity()
    t = task(val)
    with pytest.raises(attempts.SemanticAttemptError, match="canonical"):
        attempts.read_existing_semantic_attempt(alias_root, pipeline, t, "run-1")
    with pytest.raises(attempts.SemanticAttemptError, match="canonical"):
        attempts.reserve_semantic_attempt(alias_root, pipeline, t, "T1", "run-1")


def test_read_existing_success_and_no_write(tmp_path):
    root, pipeline = dirs(tmp_path)
    val = identity()
    t = task(val)
    reserved = attempts.reserve_semantic_attempt(root, pipeline, t, "T1", "run-1", now=10.0)
    target = pipeline / "semantic-attempts" / f"{val['fingerprint']}.json"
    file_before = target.stat()
    dir_before = (pipeline / "semantic-attempts").stat()

    found = attempts.read_existing_semantic_attempt(root, pipeline, t, "run-1")
    assert found == reserved
    assert found is not reserved
    found["task_id"] = "mutated"
    assert json.loads(target.read_text())["task_id"] == "T1"

    file_after = target.stat()
    dir_after = (pipeline / "semantic-attempts").stat()
    assert file_before.st_mtime_ns == file_after.st_mtime_ns
    assert file_before.st_size == file_after.st_size
    assert dir_before.st_mtime_ns == dir_after.st_mtime_ns


def test_read_existing_missing_directory_never_creates(tmp_path):
    root, pipeline = dirs(tmp_path)
    attempts_dir = pipeline / "semantic-attempts"
    assert not attempts_dir.exists()
    val = identity()
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, task(val), "run-1")
    assert not attempts_dir.exists()


def test_read_existing_missing_target_record_fails(tmp_path):
    root, pipeline = dirs(tmp_path)
    attempts_dir = pipeline / "semantic-attempts"
    attempts_dir.mkdir(mode=0o700)
    val = identity()
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, task(val), "run-1")


def test_read_existing_detects_target_chmod_race(tmp_path, monkeypatch):
    root, pipeline = dirs(tmp_path)
    val = identity()
    t = task(val)
    attempts.reserve_semantic_attempt(root, pipeline, t, "T1", "run-1", now=1.0)
    real_validate = attempts._validate_existing

    def race_chmod(r, path, ident):
        res = real_validate(r, path, ident)
        path.chmod(0o644)
        return res

    monkeypatch.setattr(attempts, "_validate_existing", race_chmod)
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, t, "run-1")


def test_read_existing_detects_directory_chmod_race(tmp_path, monkeypatch):
    root, pipeline = dirs(tmp_path)
    val = identity()
    t = task(val)
    attempts.reserve_semantic_attempt(root, pipeline, t, "T1", "run-1", now=1.0)
    real_validate = attempts._validate_existing
    attempts_dir = pipeline / "semantic-attempts"

    def race_dir_chmod(r, path, ident):
        res = real_validate(r, path, ident)
        attempts_dir.chmod(0o755)
        return res

    monkeypatch.setattr(attempts, "_validate_existing", race_dir_chmod)
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, t, "run-1")


def test_read_existing_detects_mtime_drift_race(tmp_path, monkeypatch):
    root, pipeline = dirs(tmp_path)
    val = identity()
    t = task(val)
    attempts.reserve_semantic_attempt(root, pipeline, t, "T1", "run-1", now=1.0)
    real_validate = attempts._validate_existing

    def race_mtime(r, path, ident):
        res = real_validate(r, path, ident)
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        return res

    monkeypatch.setattr(attempts, "_validate_existing", race_mtime)
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, t, "run-1")


@pytest.mark.parametrize(
    "case",
    ["corrupt", "symlink", "hardlink", "wrong_task", "wrong_prefix", "oversize", "mode"],
)
def test_read_existing_untrusted_records_fail_closed(tmp_path, case):
    root, pipeline = dirs(tmp_path)
    val = identity()
    attempts.reserve_semantic_attempt(root, pipeline, task(val), "T1", "run-1", now=1)
    path = pipeline / "semantic-attempts" / f"{val['fingerprint']}.json"
    t = task(val)
    run_prefix = "run-1"
    if case == "corrupt":
        path.write_text("{bad")
    elif case == "symlink":
        path.unlink()
        outside = tmp_path / "outside"
        outside.write_text("{}")
        path.symlink_to(outside)
    elif case == "hardlink":
        os.link(path, pipeline / "semantic-attempts" / "alias.json")
    elif case == "wrong_task":
        t = copy.deepcopy(t)
        t["task_id"] = "T2"
    elif case == "wrong_prefix":
        run_prefix = "run-2"
    elif case == "oversize":
        path.write_text("x" * 5000)
    elif case == "mode":
        path.chmod(0o644)
    with pytest.raises(attempts.SemanticAttemptError):
        attempts.read_existing_semantic_attempt(root, pipeline, t, run_prefix)
