"""Actual temporary Git graphs, independent query oracles and no mutation proof."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from scripts import two_site_main_maintenance_git as subject
from scripts.two_site_main_maintenance_record import build_main_maintenance_record, serialize_main_maintenance_record
from scripts.two_site_maintenance_transition_git import _run_git_query
from scripts.two_site_protected_main_git_facts import verify_protected_main_git_locked

ERROR = "invalid main maintenance git proof"
MAIN, ORIGIN, CANDIDATE = subject.MAIN, subject.ORIGIN, subject.CANDIDATE
CONTROL = "scripts/two_site_control.py"
BRANCH = "refactor/two-site-architecture"


def git(root, *args):
    return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
        env={"PATH":"/usr/bin:/bin", "LANG":"C.UTF-8", "GIT_CONFIG_NOSYSTEM":"1",
             "GIT_TERMINAL_PROMPT":"0", "GIT_AUTHOR_NAME":"AE test", "GIT_AUTHOR_EMAIL":"ae@example.invalid",
             "GIT_COMMITTER_NAME":"AE test", "GIT_COMMITTER_EMAIL":"ae@example.invalid"},
        stderr=subprocess.DEVNULL, timeout=10).decode().strip()


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(",",":"), ensure_ascii=False) + "\n").encode()


def save_commit(root, path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    git(root, "add", "--", path)
    git(root, "commit", "-m", "bounded test fixture")
    return git(root, "rev-parse", "HEAD")


def actor(head, paths, index, **parents):
    return {"head":head, "paths":paths, "worker_run_id":f"worker-{index}",
        "reviewer_run_id":f"reviewer-{index}", "proposal_sha256":"8"*64,
        "test_evidence_sha256":"9"*64, **parents}


def fixture(tmp_path, pre=2, post=2, mode="regular"):
    root = (tmp_path / "repo").resolve()
    root.mkdir(mode=0o700)
    git(root, "init", "-b", "main")
    old = save_commit(root, CONTROL, "# stable control\n")
    git(root, "update-ref", ORIGIN, old)
    git(root, "update-ref", CANDIDATE, old)
    approved = save_commit(root, "core/approved.py", "# approved main\n")
    git(root, "switch", "-c", BRANCH, old)
    pre_entries, post_entries, current = [], [], old
    for index in range(pre):
        path = f"scripts/two_site_pre_{index}.py"
        head = save_commit(root, path, f"# pre {index}\n")
        pre_entries.append(actor(head, [path], index, parent=current))
        current = head
    git(root, "merge", "--no-ff", "-m", "reviewed fixture main integration", approved)
    merged = git(root, "rev-parse", "HEAD")
    main_entry = actor(merged, ["core/approved.py"], "main", parents=[current, approved])
    current = merged
    for index in range(post):
        path = f"scripts/two_site_post_{index}.py"
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "deleted" and index == post-1:
            path = pre_entries[0]["paths"][0]
            (root / path).unlink()
            git(root, "add", "--", path)
            git(root, "commit", "-m", "deleted fixture")
            head = git(root, "rev-parse", "HEAD")
        elif mode == "symlink" and index == post-1:
            target.symlink_to("not-a-regular-blob")
            git(root, "add", "--", path)
            git(root, "commit", "-m", "symlink fixture")
            head = git(root, "rev-parse", "HEAD")
        else:
            head = save_commit(root, path, f"# post {index}\n")
        post_entries.append(actor(head, [path], f"post-{index}", parent=current))
        current = head
    control = git(root, "rev-parse", old+":"+CONTROL)
    def repository(head, assignment, main):
        return {"head":head, "status":"", "protected_refs":{MAIN:main, ORIGIN:old, CANDIDATE:old},
            "control_blobs":{CONTROL:control}, "assignment_scope":{
                "assignment_id":assignment, "stage_id":"P1-00b", "sha256":"7"*64,
                "expires_at":"2026-10-30T23:59:00Z", "allowed_paths":["scripts/two_site_worker.py"], "forbidden_paths":[]}}
    prior = repository(old, "prior", old)
    new = repository(current, "new", approved)
    decision = {"schema_version":"1.0", "kind":"OWNER_APPROVED_PROTECTED_MAIN_CHANGE", "reference":MAIN,
        "prior_main_sha":old, "approved_main_sha":approved, "prior_checkpoint_sha256":"d"*64,
        "prior_head":old, "decision_id":"explicit-test-owner-decision"}
    owner = {"decision":decision, "raw_sha256":hashlib.sha256(wire(decision)).hexdigest()}
    bindings = {"prior_checkpoint_sha256":"d"*64, "prior_repository":prior, "owner_approval":owner}
    record = build_main_maintenance_record(**bindings, new_repository=new, pre_main_commits=pre_entries,
        main_integration=main_entry, post_main_commits=post_entries)
    return root, record, bindings


def check(root, record, bindings):
    frozen = copy.deepcopy([record, bindings])
    subject.verify_main_maintenance_git_chain(root, record, **bindings)
    assert [record, bindings] == frozen


def reject(root, record, bindings):
    frozen = copy.deepcopy([record, bindings])
    with pytest.raises(subject.MainMaintenanceGitProofError) as caught:
        subject.verify_main_maintenance_git_chain(root, record, **bindings)
    assert str(caught.value) == ERROR
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert [record, bindings] == frozen


@pytest.mark.parametrize("pre,post", [(0,0), (2,2), (15,16), (31,0), (0,31)])
def test_actual_complete_graph_and_canonical_wire(tmp_path, pre, post):
    root, record, bindings = fixture(tmp_path, pre, post)
    before = (git(root,"show-ref"), git(root,"status","--porcelain=v1","--untracked-files=all"),
              git(root,"rev-parse","HEAD"), sorted(p.relative_to(root).as_posix() for p in root.rglob("*")))
    check(root, record, bindings)
    check(root, serialize_main_maintenance_record(record, **bindings), bindings)
    after = (git(root,"show-ref"), git(root,"status","--porcelain=v1","--untracked-files=all"),
             git(root,"rev-parse","HEAD"), sorted(p.relative_to(root).as_posix() for p in root.rglob("*")))
    assert before == after


def test_actual_lower_executor_full_graph_forwarding(tmp_path):
    root, record, bindings = fixture(tmp_path)
    with (patch.object(subject, "_run_git_query", wraps=_run_git_query) as query,
          patch.object(subject, "verify_protected_main_git_locked", wraps=verify_protected_main_git_locked) as protected):
        check(root, record, bindings)
    assert protected.call_count == 2
    assert protected.call_args_list[0] == protected.call_args_list[1]
    entries = record["pre_main_commits"] + [record["main_integration"]] + record["post_main_commits"]
    argv = [call.args[1] for call in query.call_args_list]
    for entry in entries:
        parents = entry.get("parents", [entry.get("parent")])
        assert ["rev-list","--parents","-n","1",entry["head"]] in argv
        assert ["diff-tree","--no-commit-id","--name-only","--no-renames","-r","-z",parents[0],entry["head"],"--"] in argv
        assert ["ls-tree","--full-tree","-z",entry["head"],"--",*entry["paths"]] in argv
    assert all(call.args[0] == root and call.kwargs == {} for call in query.call_args_list)
    assert all(args[0] in {"rev-list","diff-tree","ls-tree"} for args in argv)


@pytest.mark.parametrize("position", ["pre", "main", "post"])
@pytest.mark.parametrize("command,fault", [("rev-list", b"wrong\n"), ("diff-tree", b"undeclared.py\0"),
    ("ls-tree", b"120000 blob "+b"0"*40+b"\tscripts/two_site_fake.py\0")])
def test_good_fault_good_exact_each_phase(tmp_path, position, command, fault):
    root, record, bindings = fixture(tmp_path)
    entry = {"pre":record["pre_main_commits"][0], "main":record["main_integration"],
             "post":record["post_main_commits"][0]}[position]
    check(root, record, bindings)
    def corrupt(repo, args):
        if args[0] == command and entry["head"] in args:
            return fault
        return _run_git_query(repo, args)
    with patch.object(subject, "_run_git_query", side_effect=corrupt):
        reject(root, record, bindings)
    check(root, record, bindings)


@pytest.mark.parametrize("fault", [b"", b"x", b"x\0\0", b"\xff\0", b"x"*65537, bytearray(b"x")])
def test_malformed_bounded_query_result(tmp_path, fault):
    root, record, bindings = fixture(tmp_path)
    with patch.object(subject, "_run_git_query", return_value=fault):
        reject(root, record, bindings)
    check(root, record, bindings)


@pytest.mark.parametrize("mode", ["deleted", "symlink"])
def test_actual_deleted_and_nonregular_blob_rejected(tmp_path, mode):
    root, record, bindings = fixture(tmp_path, mode=mode)
    reject(root, record, bindings)


@pytest.mark.parametrize("ref", [MAIN, ORIGIN, CANDIDATE])
def test_real_protected_reference_drift_restore(tmp_path, ref):
    root, record, bindings = fixture(tmp_path)
    check(root, record, bindings)
    old = git(root, "rev-parse", ref)
    git(root, "update-ref", ref, record["new_repository"]["head"])
    reject(root, record, bindings)
    git(root, "update-ref", ref, old)
    check(root, record, bindings)


def test_live_dirty_wrong_branch_and_head(tmp_path):
    root, record, bindings = fixture(tmp_path)
    loose = root / "untracked.txt"
    loose.write_text("dirty", encoding="utf-8")
    reject(root, record, bindings)
    loose.unlink()
    check(root, record, bindings)
    git(root, "switch", "--detach", record["new_repository"]["head"])
    reject(root, record, bindings)
    git(root, "switch", BRANCH)
    check(root, record, bindings)
    save_commit(root,"scripts/two_site_extra.py","# extra\n")
    reject(root, record, bindings)


@pytest.mark.parametrize("when", ["prior", "new"])
def test_actual_control_hash_not_hash_shaped_metadata(tmp_path, when):
    root, record, bindings = fixture(tmp_path)
    if when == "prior":
        bindings["prior_repository"]["control_blobs"][CONTROL] = "0"*40
        record["new_repository"]["control_blobs"][CONTROL] = "0"*40
    else:
        record["new_repository"]["control_blobs"][CONTROL] = "0"*40
        # Both endpoints claim the same fake object: codec accepts consistency.
        bindings["prior_repository"]["control_blobs"][CONTROL] = "0"*40
    reject(root, record, bindings)


def test_end_fence_drift_is_not_accepted(tmp_path):
    root, record, bindings = fixture(tmp_path)
    calls = 0
    def drift(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            git(root,"update-ref", ORIGIN, record["new_repository"]["head"])
        return verify_protected_main_git_locked(*args, **kwargs)
    original = git(root,"rev-parse",ORIGIN)
    with patch.object(subject, "verify_protected_main_git_locked", side_effect=drift):
        reject(root, record, bindings)
    assert calls == 2
    git(root,"update-ref",ORIGIN,original)
    check(root, record, bindings)


@pytest.mark.parametrize("value", [None, [], True, bytearray(b"{}"), b"", b"x"*65537])
def test_invalid_inputs_make_no_git_call(tmp_path, value):
    root, _, bindings = fixture(tmp_path)
    with (patch.object(subject, "_run_git_query") as query,
          patch.object(subject, "verify_protected_main_git_locked") as protected):
        reject(root, value, bindings)
    query.assert_not_called()
    protected.assert_not_called()


def test_oversized_commit_paths_rejected_before_queries(tmp_path):
    root, record, bindings = fixture(tmp_path)
    record["pre_main_commits"][0]["paths"] = [f"scripts/two_site_extra_{i:02}.py" for i in range(33)]
    with patch.object(subject, "verify_protected_main_git_locked") as protected:
        reject(root, record, bindings)
    protected.assert_not_called()


@pytest.mark.parametrize("exception", [RuntimeError("secret"), OSError("private"), ValueError("payload")])
def test_ordinary_lower_errors_are_fixed(tmp_path, exception):
    root, record, bindings = fixture(tmp_path)
    with patch.object(subject, "_run_git_query", side_effect=exception):
        reject(root, record, bindings)


@pytest.mark.parametrize("exception", [KeyboardInterrupt(), SystemExit(2), GeneratorExit()])
def test_base_exception_identity_is_preserved(tmp_path, exception):
    root, record, bindings = fixture(tmp_path)
    with patch.object(subject, "_run_git_query", side_effect=exception):
        with pytest.raises(type(exception)) as caught:
            subject.verify_main_maintenance_git_chain(root, record, **bindings)
    assert caught.value is exception


def test_root_symlink_relative_and_permissions(tmp_path):
    root, record, bindings = fixture(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for invalid in (alias, Path("relative"), str(root), root / "scripts"):
        reject(invalid, record, bindings)
    root.chmod(0o777)
    reject(root, record, bindings)
    root.chmod(0o700)
    check(root, record, bindings)


def test_declared_module_byte_limits():
    for path, cap in ((Path(__file__), 24000),
        (Path(subject.__file__), 12000)):
        with path.open("rb") as stream:
            raw = stream.read(cap+1)
        assert len(raw) == path.stat().st_size <= cap
