"""Private snapshot boundaries using disposable Git and complete byte oracles."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import traceback

import pytest

from scripts import two_site_accepted_work_snapshot as subject


REFS = ("refs/heads/main", "refs/remotes/origin/main",
        "refs/heads/candidate/wa-ir-standby-v1")
METRICS = ".local/two-site-refactor/autopilot/metrics.json"
TASKS = ".local/two-site-refactor/tasks"
FIXED_CODES = {
    "INVALID_ROOT", "INVALID_KEY", "UNSAFE_PATH", "UNSAFE_FILE", "MISSING_FILE",
    "CUSTODY_DRIFT", "BYTE_LIMIT", "COUNT_LIMIT", "TIME_LIMIT", "JSON_INVALID",
    "CLOSED_SNAPSHOT", "GIT_FAILURE", "UNSUPPORTED_COMMIT", "SNAPSHOT_DRIFT",
    "UNSUPPORTED_SOURCE",
}
PRIVATE = "fixture-private-739M-never-render"
GIT_ENV = {
    "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_NO_LAZY_FETCH": "1",
    "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
    "GIT_GRAFT_FILE": "/dev/null",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def oid(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fixed_error(code, operation):
    with pytest.raises(subject._EvidenceSnapshotError) as caught:
        operation()
    error = caught.value
    assert error.code == code and str(error) == code and code in FIXED_CODES
    assert error.__suppress_context__ is True
    assert PRIVATE not in "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return error


class Repository:
    """Fixture commands are independent of every Subject parser and resolver."""

    def __init__(self, root):
        self.root = root
        root.mkdir(mode=0o700)
        self.run("init", "--initial-branch=fixture", "--template=")
        self.base_files = {
            "src/offer.py": b"offer = 1\n", "src/deleted.py": b"old = True\n",
            "docs/memory/current.md": b"# current memory\n",
            "frontend/auth-security.js": b"export const enabled = true;\n",
            "config/user-auth.yaml": b"enabled: true\n",
        }
        for path, data in self.base_files.items():
            self.put(path, data)
        self.run("add", "--", *self.base_files)
        self.run("commit", "-m", "fixture root")
        self.base = self.run("rev-parse", "HEAD").strip().decode()
        self.child_files = dict(self.base_files)
        self.child_files["src/offer.py"] = b"offer = 2\n"
        del self.child_files["src/deleted.py"]
        self.child_files["scripts/runner.sh"] = b"#!/bin/sh\nexit 0\n"
        self.put("src/offer.py", self.child_files["src/offer.py"])
        (root / "src/deleted.py").unlink()
        self.put("scripts/runner.sh", self.child_files["scripts/runner.sh"], mode=0o700)
        self.run("add", "-A")
        self.run("commit", "-m", "fixture child")
        self.child = self.run("rev-parse", "HEAD").strip().decode()
        tree = self.run("rev-parse", "HEAD^{tree}").strip().decode()
        self.side = self.run("commit-tree", tree, "-p", self.base, input=b"side\n").strip().decode()
        self.merge = self.run("commit-tree", tree, "-p", self.child, "-p", self.side,
                              input=b"merge\n").strip().decode()
        self.refs = dict(zip(REFS, (self.base, self.child, self.side)))
        for ref, commit in self.refs.items():
            self.run("update-ref", ref, commit)
        self.commits = {commit: self.run("cat-file", "commit", commit)
                        for commit in (self.base, self.child, self.side, self.merge)}
        self.put(METRICS, b'[{"work":1},null,[true]]')
        self.put(f"{TASKS}/probe.json", b"private-artifact")

    def run(self, *args, input=None):
        env = dict(GIT_ENV, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                   GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
                   GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
        argv = ["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs", "-C", str(self.root),
                "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                *args]
        result = subprocess.run(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, check=False, timeout=10)
        assert result.returncode == 0, (args, result.returncode, result.stderr)
        return result.stdout

    def put(self, path, data, mode=0o600):
        leaf = self.root / path
        leaf.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        leaf.write_bytes(data)
        leaf.chmod(mode)
        return leaf

    def special(self, mode, path, data=b"unsafe"):
        blob = self.run("hash-object", "-w", "--stdin", input=data).strip().decode()
        kind = "commit" if mode == "160000" else "blob"
        target = self.base if kind == "commit" else blob
        components = path.split("/")
        tree = self.run("mktree", input=f"{mode} {kind} {target}\t{components[-1]}\n".encode()).strip().decode()
        for component in reversed(components[:-1]):
            tree = self.run("mktree", input=f"040000 tree {tree}\t{component}\n".encode()).strip().decode()
        return self.run("commit-tree", tree, "-p", self.base, input=b"special\n").strip().decode()


@contextmanager
def read_counter(monkeypatch):
    actual = os.read
    observed = []
    def reading(fd, count):
        result = actual(fd, count)
        observed.append((fd, count, result))
        return result
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.os, "read", reading)
        yield observed


def expected_metadata(repo, objects, files, bytes_read):
    return {"schema_version": 1, "head": repo.child, "protected_refs": repo.refs,
            "files": [{"path": path, "sha256": sha256(data), "size": len(data)}
                      for path, data in sorted(files.items())],
            "git_objects": [{"git_oid": key, "sha256": sha256(data), "size": len(data)}
                            for key, data in sorted(objects.items())],
            "bytes_read": bytes_read}


def whole_oracle(repo, snapshot, observed):
    """One full literal oracle reused unchanged for success/corruption/restore."""
    expected_objects = {repo.child: repo.commits[repo.child]}
    for path in ("src/offer.py", "scripts/runner.sh"):
        data = repo.child_files[path]
        expected_objects[oid("blob", data)] = data
    assert snapshot._git_commit(repo.child) == {"sha": repo.child, "parents": (repo.base,)}
    for path, mode in (("src/offer.py", "100644"), ("scripts/runner.sh", "100755")):
        data = repo.child_files[path]
        actual = snapshot._git_blob(repo.child, path)
        assert actual == {"commit": repo.child, "path": path, "mode": mode,
                          "git_oid": oid("blob", data), "sha256": sha256(data), "data": data}
        assert type(actual["data"]) is bytes
    assert snapshot._git_blob(repo.child, "src/absent.py") is None
    rows = [{"work": 1}, None, [True]]
    assert snapshot._json_list("metrics") == rows
    files = {METRICS: b'[{"work":1},null,[true]]'}
    expected = expected_metadata(repo, expected_objects, files, sum(len(row[2]) for row in observed))
    assert snapshot._metadata() == expected
    assert set(snapshot._metadata()) == {"schema_version", "head", "protected_refs", "files",
                                         "git_objects", "bytes_read"}
    return expected


def test_real_snapshot_whole_oracle_cache_detachment_and_same_mutation(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "repo")
    assert tuple(inspect.signature(subject._proof_snapshot).parameters) == ("root",)
    with read_counter(monkeypatch) as observed:
        with subject._proof_snapshot(repo.root) as snapshot:
            assert snapshot._metadata() == expected_metadata(repo, {}, {}, sum(len(r[2]) for r in observed))
            expected = whole_oracle(repo, snapshot, observed)
            command_count = snapshot._commands
            whole_oracle(repo, snapshot, observed)
            assert snapshot._commands == command_count
            detached = snapshot._metadata()
            detached["protected_refs"].clear()
            detached["files"][0]["path"] = PRIVATE
            detached["git_objects"].clear()
            rows = snapshot._json_list("metrics")
            rows[0]["work"] = 99
            assert whole_oracle(repo, snapshot, observed) == expected
            original = subject._ProofSnapshot._metadata
            def corrupt(instance):
                changed = original(instance)
                changed["git_objects"][0]["sha256"] = "0" * 64
                return changed
            with monkeypatch.context() as patcher:
                patcher.setattr(subject._ProofSnapshot, "_metadata", corrupt)
                with pytest.raises(AssertionError):
                    whole_oracle(repo, snapshot, observed)
            assert whole_oracle(repo, snapshot, observed) == expected
            assert snapshot._verify() is None
    assert snapshot._state == "CLOSED"
    assert not any((snapshot._objects, snapshot._lookups, snapshot._commits, snapshot._changes,
                    snapshot._ancestors, snapshot._files._handles, snapshot._files._dirs))
    for call in (snapshot._metadata, snapshot._verify, lambda: snapshot._git_commit(repo.child),
                 lambda: snapshot._git_blob(repo.child, "src/offer.py"),
                 lambda: snapshot._git_changes(repo.child),
                 lambda: snapshot._is_ancestor(repo.base, repo.child),
                 lambda: snapshot._read("metrics"), lambda: snapshot._json_list("metrics")):
        fixed_error("CLOSED_SNAPSHOT", call)


def test_real_git_changes_parent_order_ancestry_absence_and_source_fence(tmp_path):
    repo = Repository(tmp_path / "git")
    zero = "0" * 40
    expected = []
    for path, old_mode, new_mode in (("scripts/runner.sh", "000000", "100755"),
                                     ("src/deleted.py", "100644", "000000"),
                                     ("src/offer.py", "100644", "100644")):
        old_data, new_data = repo.base_files.get(path), repo.child_files.get(path)
        expected.append({"path": path, "old_mode": old_mode, "new_mode": new_mode,
                         "old_oid": zero if old_data is None else oid("blob", old_data),
                         "new_oid": zero if new_data is None else oid("blob", new_data)})
    specials = [(mode, repo.special(mode, "src/special.py")) for mode in ("120000", "160000")]
    forbidden = repo.special("100644", "secrets.json")
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._git_commit(repo.base) == {"sha": repo.base, "parents": ()}
        assert snapshot._git_commit(repo.merge) == {"sha": repo.merge, "parents": (repo.child, repo.side)}
        assert snapshot._git_changes(repo.child) == tuple(expected)
        for commit in (repo.base, repo.merge):
            fixed_error("UNSUPPORTED_COMMIT", lambda c=commit: snapshot._git_changes(c))
        assert snapshot._is_ancestor(repo.base, repo.child) is True
        assert snapshot._is_ancestor(repo.child, repo.base) is False
        assert snapshot._is_ancestor(repo.side, repo.child) is False
        assert snapshot._is_ancestor(repo.child, repo.child) is True
        for path in ("docs/memory/current.md", "frontend/auth-security.js", "config/user-auth.yaml"):
            data = repo.child_files[path]
            assert snapshot._git_blob(repo.child, path)["data"] == data
        for path in ("absent.py", "config/.env.example"):
            assert snapshot._git_blob(repo.child, path) is None
        for path in ("../src/x.py", "/src/x.py", "src//x.py", "src/./x.py", "src/../x.py",
                     "C:/x.py", "src\\x.py", "src/?.py", "src/*.py", "src/[x].py",
                     "src/\nx.py", "src/\0x.py", "src/.git/x.py", "-src/x.py", ""):
            fixed_error("UNSAFE_PATH", lambda p=path: snapshot._git_blob(repo.child, p))
        for path in (".env", "config/.env.production", "secrets.json", "private.py",
                     "credentials.txt", "dumps.sql", "data/x.json", "docs/memory/archive/x.md",
                     ".local/x.py", "logs/x.py", "node_modules/x.js", "state/x.json",
                     ".agents/x.md", ".codex/x.md", ".cursor/x.md", ".ssh/x.py",
                     ".aws/x.json", ".gnupg/x.txt", ".config/x.py", "media/x.png"):
            fixed_error("UNSUPPORTED_SOURCE", lambda p=path: snapshot._git_blob(repo.child, p))
        for value in ("A" * 40, "0" * 39, "0" * 41, "HEAD", "--help", b"0" * 40, None, True):
            fixed_error("INVALID_KEY", lambda v=value: snapshot._git_commit(v))
        for _, commit in specials:
            fixed_error("UNSUPPORTED_SOURCE", lambda c=commit: snapshot._git_blob(c, "src/special.py"))
            fixed_error("UNSUPPORTED_SOURCE", lambda c=commit: snapshot._git_changes(c))
        fixed_error("UNSUPPORTED_SOURCE", lambda: snapshot._git_changes(forbidden))
        assert snapshot._git_changes(repo.child) == tuple(expected)


def test_lifecycle_fixed_commands_hermetic_environment_and_no_constructor_io(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "lifecycle")
    real_popen = subprocess.Popen
    calls = []
    def no_io(*args, **kwargs):
        raise AssertionError("constructor performed I/O")
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.subprocess, "Popen", no_io)
        for args, options in (((repo.root,), {}), ((), {}), ((repo.root, object()), {"_permit": object()})):
            fixed_error("CLOSED_SNAPSHOT", lambda: subject._ProofSnapshot(*args, **options))
    prefix = ["/usr/bin/git", "--no-pager", "--no-replace-objects", "--literal-pathspecs", "-C", str(repo.root),
              "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
              "-c", "protocol.allow=never", "-c", "advice.graftFileDeprecated=false"]
    def guarded(argv, **options):
        assert argv[:len(prefix)] == prefix
        assert options["env"] == GIT_ENV
        assert options["shell"] is False
        assert options["start_new_session"] is True
        assert options["stdin"] == subprocess.DEVNULL
        assert options["stdout"] == options["stderr"] == subprocess.PIPE
        assert options["close_fds"] is True
        calls.append(tuple(argv[len(prefix):]))
        return real_popen(argv, **options)
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.subprocess, "Popen", guarded)
        context = subject._proof_snapshot(repo.root)
        with context as snapshot:
            fixed_error("CLOSED_SNAPSHOT", context.__enter__)
            assert calls == [("rev-parse", "--show-toplevel"), ("rev-parse", "--show-object-format"),
                             ("rev-parse", "--verify", "HEAD"),
                             *(("rev-parse", "--verify", ref) for ref in REFS)]
            snapshot._git_commit(repo.child)
            snapshot._git_blob(repo.child, "src/offer.py")
            snapshot._git_changes(repo.child)
            snapshot._is_ancestor(repo.base, repo.child)
        fixed_error("CLOSED_SNAPSHOT", context.__enter__)
    legal_starts = {"rev-parse", "cat-file", "ls-tree", "diff-tree", "merge-base"}
    assert all(call[0] in legal_starts for call in calls)
    assert ("cat-file", "-t", repo.child) in calls
    assert ("cat-file", "-s", repo.child) in calls
    assert ("cat-file", "commit", repo.child) in calls
    assert ("ls-tree", "-z", repo.child, "--", "src/offer.py") in calls
    assert ("diff-tree", "--no-commit-id", "--raw", "-z", "--no-renames", "--no-ext-diff",
            "--no-textconv", "--no-abbrev", "-r", repo.base, repo.child) in calls
    assert ("merge-base", "--is-ancestor", repo.base, repo.child) in calls


def test_parser_object_hash_and_git_failures_poison_without_private_output(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "parser")
    original_capture = subject._ProofSnapshot._capture
    blob = oid("blob", repo.child_files["src/offer.py"])
    faults = [
        (("cat-file", "-t", repo.child), b"tag\n", lambda s: s._git_commit(repo.child)),
        (("cat-file", "-s", repo.child), b"-1\n", lambda s: s._git_commit(repo.child)),
        (("cat-file", "commit", repo.child), PRIVATE.encode(), lambda s: s._git_commit(repo.child)),
        (("ls-tree", "-z", repo.child, "--", "src/offer.py"), b"malformed\0",
         lambda s: s._git_blob(repo.child, "src/offer.py")),
        (("cat-file", "-t", blob), b"tree\n", lambda s: s._git_blob(repo.child, "src/offer.py")),
        (("cat-file", "-s", blob), b"00010\n", lambda s: s._git_blob(repo.child, "src/offer.py")),
        (("cat-file", "blob", blob), b"offer = 9\n", lambda s: s._git_blob(repo.child, "src/offer.py")),
    ]
    diff_args = ("diff-tree", "--no-commit-id", "--raw", "-z", "--no-renames", "--no-ext-diff",
                 "--no-textconv", "--no-abbrev", "-r", repo.base, repo.child)
    faults.append((diff_args, b":100644 100644 broken\0", lambda s: s._git_changes(repo.child)))
    for target, corrupted, operation in faults:
        def poisoned_body():
            with subject._proof_snapshot(repo.root) as snapshot:
                def damaged(instance, arguments, allowed_returns=(0,)):
                    rc, output = original_capture(instance, arguments, allowed_returns)
                    return (rc, corrupted) if tuple(arguments) == target else (rc, output)
                with monkeypatch.context() as patcher:
                    patcher.setattr(subject._ProofSnapshot, "_capture", damaged)
                    # Parser rejection must retain its own fixed error as well as
                    # make subsequent calls fail closed; exit cannot erase failure.
                    fixed_error("GIT_FAILURE", lambda: operation(snapshot))
                    fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
        fixed_error("GIT_FAILURE", poisoned_body)
    def oversized():
        with subject._proof_snapshot(repo.root) as snapshot:
            def declared_large(instance, arguments, allowed_returns=(0,)):
                rc, output = original_capture(instance, arguments, allowed_returns)
                return (rc, b"2000001\n") if tuple(arguments) == ("cat-file", "-s", repo.child) else (rc, output)
            with monkeypatch.context() as patcher:
                patcher.setattr(subject._ProofSnapshot, "_capture", declared_large)
                fixed_error("BYTE_LIMIT", lambda: snapshot._git_commit(repo.child))
                fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    fixed_error("BYTE_LIMIT", oversized)


def test_shared_file_owner_negative_custody_and_total_deadline(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "shared")
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._files._deadline > 0
        assert snapshot._read("task", "probe") == b"private-artifact"
        assert snapshot._files._bytes_read == snapshot._metadata()["bytes_read"]
        fixed_error("MISSING_FILE", lambda: snapshot._read("task", "missing"))
        assert snapshot._json_list("metrics") == [{"work": 1}, None, [True]]
        assert len(snapshot._metadata()["files"]) == 2
    def missing_appears():
        with subject._proof_snapshot(repo.root) as snapshot:
            fixed_error("MISSING_FILE", lambda: snapshot._read("task", "missing"))
            repo.put(f"{TASKS}/missing.json", b"appeared")
            snapshot._verify()
    fixed_error("CUSTODY_DRIFT", missing_appears)
    def file_changes():
        with subject._proof_snapshot(repo.root) as snapshot:
            assert snapshot._read("task", "probe") == b"private-artifact"
            repo.put(f"{TASKS}/probe.json", b"changed-artifact")
            snapshot._git_commit(repo.child)
    fixed_error("CUSTODY_DRIFT", file_changes)
    repo.put(f"{TASKS}/probe.json", b"private-artifact")
    def expired():
        with subject._proof_snapshot(repo.root) as snapshot:
            deadline = snapshot._files._deadline
            with monkeypatch.context() as patcher:
                patcher.setattr(subject.time, "monotonic", lambda: deadline)
                fixed_error("TIME_LIMIT", lambda: snapshot._git_commit(repo.child))
                fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    fixed_error("TIME_LIMIT", expired)


def test_root_head_and_protected_ref_drift_preserves_original_body(tmp_path):
    repo = Repository(tmp_path / "drift")
    for ref, previous in (("refs/heads/fixture", repo.child), *repo.refs.items()):
        def drift():
            with subject._proof_snapshot(repo.root) as snapshot:
                snapshot._git_commit(repo.child)
                repo.run("update-ref", ref, repo.merge)
                snapshot._verify()
        try:
            fixed_error("SNAPSHOT_DRIFT", drift)
        finally:
            repo.run("update-ref", ref, previous)
    sentinel = RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError) as caught:
        with subject._proof_snapshot(repo.root) as snapshot:
            repo.run("update-ref", REFS[0], repo.merge)
            raise sentinel
    assert caught.value is sentinel and str(caught.value) == PRIVATE
    repo.run("update-ref", REFS[0], repo.base)
    repo.run("update-ref", "-d", REFS[0])
    try:
        fixed_error("GIT_FAILURE", lambda: subject._proof_snapshot(repo.root).__enter__())
    finally:
        repo.run("update-ref", REFS[0], repo.base)
    alias = tmp_path / "alias"
    alias.symlink_to(repo.root, target_is_directory=True)
    fixed_error("INVALID_ROOT", lambda: subject._proof_snapshot(alias).__enter__())


def test_real_linked_worktree_packed_refs_full_oracle_and_named_metadata_custody(tmp_path, monkeypatch):
    common = Repository(tmp_path / "common")
    common.run("pack-refs", "--all", "--prune")
    linked = copy.copy(common)
    linked.root = common.root / "linked"
    common.run("worktree", "add", "--detach", str(linked.root), common.child)
    linked.put(METRICS, b'[{"work":1},null,[true]]')
    # Every protected ref remains real in packed-refs, but its loose parent can
    # genuinely be absent. A guarded missing directory must not hide that ref.
    for directory in sorted((common.root / ".git/refs").rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    assert (linked.root / ".git").is_file()
    assert not (common.root / ".git/refs/remotes/origin").exists()
    with read_counter(monkeypatch) as observed:
        with subject._proof_snapshot(linked.root) as snapshot:
            whole_oracle(linked, snapshot, observed)
            assert snapshot._verify() is None
    gitdir = common.root / ".git/worktrees/linked"
    for leaf in (linked.root / ".git", gitdir / "commondir", common.root / ".git/config"):
        original = leaf.read_bytes()
        def replaced():
            with subject._proof_snapshot(linked.root) as snapshot:
                replacement = leaf.with_name(leaf.name + ".replacement")
                replacement.write_bytes(original)
                replacement.chmod(0o600)
                replacement.replace(leaf)
                snapshot._verify()
        try:
            fixed_error("SNAPSHOT_DRIFT", replaced)
        finally:
            leaf.write_bytes(original)
            leaf.chmod(0o600)
