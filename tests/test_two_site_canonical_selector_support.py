"""Owned fixture operations, independent oracles and forwarding observations."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()
from scripts import two_site_cycle_tools as subject
from tests import test_two_site_canonical_evidence_inspect as canonical
from tests import test_two_site_canonical_evidence_continuation4 as continuation
from tests import test_two_site_canonical_evidence_read_bounds as bounds

INV = "docs/refactor/two-server/section-1/inventory/"
LEAF, MANIFEST = INV + "continuation-4.json", INV + "seed-handler-coverage.json"
API, BOT, WEB, JOB = ("API-DEMO-SEED-PROVEN", "BOT-DEMO-SEED-PARTIAL",
                      "WEB-DEMO-SEED-UNKNOWN", "JOB-DEMO-SEED-EXTRA")
PROVEN, PARTIAL, UNKNOWN = "PROVEN_FOR_REQUESTED_SCENARIO", "PARTIAL_OR_SEED_ONLY", "UNKNOWN"
HISTORICAL = {"status": "INVENTORY_INCONSISTENT", "reason": "historical record invalid"}
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
       "GIT_NO_LAZY_FETCH": "1", "GIT_CONFIG_NOSYSTEM": "1"}
GOOD = "def test_ok(): pass\n"
clone, git = copy.deepcopy, canonical._run_git


def _raw(bot=PARTIAL, job=PARTIAL, web=UNKNOWN):
    return {"status": "VALID", "expected_seed_count": 4, "seed_count": 4,
            "coverage": {API: PROVEN, BOT: bot, WEB: web, JOB: job}}


def _target(seed, state):
    return {"status": "VALID", "expected_seed_count": 4, "seed_count": 4,
            "coverage": {} if state == "NO_MATCHING_EVIDENCE" else {seed: state},
            "requested_coverage": state}


def _page(raw, offset=0):
    result = {"schema_version": 1, "display_only": True, **clone(raw)}
    if raw["status"] == "VALID":
        ids = sorted(raw["coverage"])
        selected = ids[offset:offset + 1]
        result.update(total_entries=len(ids), offset=offset, returned_entries=len(selected),
                      omitted_entries=len(ids) - len(selected),
                      next_offset=offset + 1 if selected and offset + 1 < len(ids) else None,
                      coverage={key: raw["coverage"][key] for key in selected})
    return result


def _bytes(repo, *args):
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *args],
                          env=ENV, capture_output=True, check=True).stdout


def _put(repo, path, value):
    assert repo.name == "selector-lab" and repo.resolve() == repo
    file = repo / path
    raw = value if isinstance(value, bytes) else (json.dumps(value, indent=2) + "\n").encode()
    if not file.exists() or file.read_bytes() != raw:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        git(repo, "add", "--", path)
        git(repo, "commit", "-m", "owned selector fixture")


def _link(repo, path):
    return {"path": path, "sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest(),
            "last_commit": git(repo, "log", "-1", "--format=%H", "HEAD", "--", path)}


def _records(repo, records):
    _put(repo, LEAF, {"schema_version": 1, "stage": "P1-00b", "records": clone(records),
                     "predecessor_inventory": _link(repo, INV + "continuation-3.json")})
    manifest = json.loads((repo / MANIFEST).read_bytes())
    if manifest["evidence_sources"].get("continuation_4_path") != LEAF:
        manifest["evidence_sources"]["continuation_4_path"] = LEAF
        _put(repo, MANIFEST, manifest)


def _lab(tmp_path):
    repo = (tmp_path / "selector-lab").resolve()
    canonical._make_canonical_repo(repo)
    _records(repo, [])
    return repo


def _proof(repo, name, body=GOOD, mode="100644"):
    path = "tests/" + name
    file = repo / path
    raw = body.encode() if isinstance(body, str) else body
    if file.is_symlink():
        file.unlink()
    elif file.is_dir():
        shutil.rmtree(file)  # Exact owned proof directory only.
    if mode == "120000":
        file.symlink_to(raw.decode())
    elif mode == "040000":
        file.mkdir()
        (file / "child.py").write_bytes(raw)
    elif mode != "160000":
        file.write_bytes(raw)
        file.chmod(0o755 if mode == "100755" else 0o644)
    if mode == "160000":
        git(repo, "update-index", "--add", "--cacheinfo",
            "160000," + git(repo, "rev-parse", "HEAD") + "," + path)
    else:
        git(repo, "add", "-A", "--", path)
    git(repo, "commit", "-m", "proof phase")
    commit = git(repo, "rev-parse", "HEAD")
    entry = _bytes(repo, "ls-tree", "-z", commit, "--", path)
    assert entry.split()[0].decode() == mode and entry.endswith(path.encode() + b"\0")
    raw = _bytes(repo, "show", commit + ":" + path)
    git(repo, "merge-base", "--is-ancestor", commit, "HEAD")
    assert git(repo, "log", "-1", "--format=%H", "HEAD", "--", path) == commit
    return {"seed_ids": [BOT], "path": path, "sha256": hashlib.sha256(raw).hexdigest(),
            "last_commit": commit, "test_selectors": [path + "::test_ok"]}


def _pin(repo):
    files = {}
    for path in repo.rglob("*"):
        if ".git" not in path.relative_to(repo).parts:
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes() if path.is_file() else None
            files[path.relative_to(repo).as_posix()] = (path.lstat().st_mode, data)
    return (git(repo, "rev-parse", "HEAD"), git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(repo, "status", "--porcelain=v1", "--untracked-files=all"), files)


def _phase(repo):
    head = git(repo, "rev-parse", "HEAD")
    facts = []
    for file in (repo / INV).glob("*.json"):
        doc = json.loads(file.read_bytes())
        rows = doc.get("records", []) + [doc.get("predecessor_inventory", {})]
        for row in rows:
            path, commit = row.get("path"), row.get("last_commit")
            if isinstance(path, str) and isinstance(commit, str) and len(commit) == 40:
                if path.startswith("/") or any(x in path for x in ("..", "\\", ":", "\0")):
                    continue
                entry = _bytes(repo, "ls-tree", "-z", commit, "--", path)
                raw = _bytes(repo, "show", commit + ":" + path) if entry else b""
                git(repo, "merge-base", "--is-ancestor", commit, head)
                facts.append((path, commit, entry, hashlib.sha256(raw).hexdigest(),
                              git(repo, "log", "-1", "--format=%H", head, "--", path)))
    return facts


@contextmanager
def _custody(repo, *inputs):
    before, facts, retained = _pin(repo), _phase(repo), clone(inputs)
    seams = [(subject, "_git_read"), (subject, "_run_bounded_process"),
             (subject.runner, "bounded_file_bytes"), (subject.ast, "parse"),
             (subject, "_resolve_selectors_in_source"), (subject, "_selector_bindings"),
             (subject, "CANONICAL_READ_LIMIT_BYTES"), (subject, "CANONICAL_TOTAL_BUDGET_BYTES")]
    saved = [getattr(owner, name) for owner, name in seams]
    try:
        yield
    finally:
        assert _pin(repo) == before and _phase(repo) == facts and inputs == retained
        assert [getattr(owner, name) for owner, name in seams] == saved


def _check(repo, full, seed=BOT, cli=True):
    targeted = "requested_coverage" in full
    expected = clone(full)
    if full["status"] == "VALID" and not targeted and seed is not None:
        state = full["coverage"].get(seed, "NO_MATCHING_EVIDENCE")
        expected = _target(seed, state)
    page = _page(expected)
    with _custody(repo, expected, full, page):
        assert subject.inspect_canonical_evidence(repo, seed) == expected
        assert subject.inventory_view(repo, seed, limit=1) == page
        if not targeted:
            assert subject.inspect_canonical_evidence(repo) == full
            for offset in (0, 1, 4):
                assert subject.inventory_view(repo, offset=offset, limit=1) == _page(full, offset)
        if cli:
            script = Path(__file__).resolve().parents[1] / "scripts/two_site_cycle_tools.py"
            args = [sys.executable, str(script), "--root", str(repo), "inventory", "--limit", "1"]
            if seed is not None:
                args += ["--behavior-id", seed]
            result = subject._run_bounded_process(args, cwd=script.parents[1], env={**os.environ,
                "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30, output_limit=65536)
            assert result.returncode == (0 if expected["status"] == "VALID" else 1)
            assert result.stderr == b"" and json.loads(result.stdout) == page


def _row(repo, records, state, job=PARTIAL, web=UNKNOWN):
    retained = clone(records)
    _records(repo, records if isinstance(records, list) else [records])
    _check(repo, state if isinstance(state, dict) else _raw(state, job, web))
    assert records == retained


def _watch(repo, patch, fault=None, *, total=8_000_000):
    """Observe exact calls against independently retained Git facts."""
    assert subject.CANONICAL_READ_LIMIT_BYTES == 2_000_000
    assert subject.CANONICAL_TOTAL_BUDGET_BYTES == total
    head = git(repo, "rev-parse", "HEAD")
    canonical_paths = {path.relative_to(repo).as_posix() for path in (repo / "docs").rglob("*.json")}
    facts = _phase(repo)
    metadata = {(head if commit == touch else commit, path)
                for path, commit, entry, digest, touch in facts if path.startswith("tests/")}
    allowed = {(head, path) for path in canonical_paths}
    allowed.update((commit, path) for path, commit, entry, digest, touch in facts)
    allowed.update(metadata)
    entries = {key: _bytes(repo, "ls-tree", "-z", key[0], "--", key[1]) for key in metadata}
    payloads = {key: _bytes(repo, "show", key[0] + ":" + key[1]) for key in allowed
                if _bytes(repo, "ls-tree", "-z", key[0], "--", key[1])}
    log = {"used": 0, "calls": [], "parsed": [], "events": [], "last_blob": None}

    def wrap(kind, real):
        def read(*args, **kw):
            if kind == "live":
                root, file, limit, label = args
                path, commit = file.relative_to(repo).as_posix(), None
                assert args == (repo, repo / path, limit, path) and kw == {}
                assert path in canonical_paths
            elif kind == "blob":
                root, arguments = args
                commit, path = arguments[-1].split(":", 1)
                limit = kw["output_limit"]
                assert args == (repo, ("show", commit + ":" + path))
                assert kw == {"output_limit": limit} and (commit, path) in allowed
            else:
                argv, = args
                commit, path, limit = argv[7], argv[-1], kw["output_limit"]
                assert argv == ["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
                                "--literal-pathspecs", "ls-tree", "-z", commit, "--", path]
                assert (commit, path) in metadata and kw == {"cwd": repo, "env": ENV,
                    "timeout": 30, "output_limit": limit}
            before = log["used"]
            assert limit == min(4096 if kind == "meta" else 2_000_000, total - before)
            key = (commit, path)
            log["calls"].append((kind, commit, path, limit, before))
            try:
                value = real(*args, **kw)
                if kind == "meta":
                    assert value.stdout == entries[key] and value.stderr == b"" and value.returncode == 0
                elif kind == "blob":
                    assert value == payloads[key]
                    log["last_blob"] = key
                else:
                    assert value == (repo / path).read_bytes()
                if fault and fault[:2] == (kind, path):
                    action = fault[2]
                    if action == "failure":
                        raise subject.SandboxError("failure after genuine bounded forwarding")
                    raw = value.stdout
                    header, name = raw[:-1].split(b"\t")
                    mode, type_, oid = header.split(b" ")
                    changed = {"path": header + b"\ttests/wrong.py\0",
                               "oid": mode + b" " + type_ + b" " + b"g" * 40 + b"\t" + name + b"\0",
                               "upper": mode + b" " + type_ + b" " + b"A" * 40 + b"\t" + name + b"\0",
                               "nul": raw + raw, "fragment": raw + b"fragment", "end": raw[:-1],
                               "mode": b"100600" + raw[6:], "type": raw.replace(b" blob ", b" tree ")}
                    value = subprocess.CompletedProcess(value.args, value.returncode,
                        changed.get(action, raw), value.stderr + b"x" * 37 if action == "stderr" else value.stderr)
            except (subject.SandboxError, subject.runner.ProposalError, OSError, subprocess.SubprocessError):
                log["used"] += limit
                log["events"].append((kind, key, before, limit, "failure"))
                raise
            debit = len(value.stdout) + len(value.stderr) if kind == "meta" else len(value)
            log["used"] += debit
            log["events"].append((kind, key, before, debit, "success"))
            return value
        return read

    real_parse = subject.ast.parse

    def parse(source, *args, **kwargs):
        key = log["last_blob"]
        assert key in metadata and source == payloads[key].decode("utf-8")
        log["parsed"].append((key, source))
        return real_parse(source, *args, **kwargs)

    for owner, name, kind in ((subject, "_git_read", "blob"), (subject.runner, "bounded_file_bytes", "live"),
                              (subject, "_run_bounded_process", "meta")):
        patch.setattr(owner, name, wrap(kind, getattr(owner, name)))
    patch.setattr(subject.ast, "parse", parse)
    return log


def _once(log, kind, commit, path):
    return [call for call in log["calls"] if call[:3] == (kind, commit, path)]


def _closure_once(log):
    keys = [(kind, commit, path) for kind, commit, path, limit, before in log["calls"]]
    assert len(keys) == len(set(keys))
    assert sum(event[3] for event in log["events"]) == log["used"]


def _historical_cache(repo, monkeypatch, versions, rows, actions, records, healthy):
    old, middle, latest = versions
    expected = _raw(PROVEN, UNKNOWN, PROVEN)
    _records(repo, rows)
    try:
        with _custody(repo, rows, expected):
            with monkeypatch.context() as patch:
                log = _watch(repo, patch)
                assert subject.inspect_canonical_evidence(repo) == expected
                head = git(repo, "rev-parse", "HEAD")
                keys = [(old["last_commit"], old["path"]),
                        (middle["last_commit"], middle["path"]), (head, latest["path"])]
                assert len(set(keys)) == 3
                for commit, path in keys:
                    assert len(_once(log, "meta", commit, path)) == 1
                    assert len(_once(log, "blob", commit, path)) == 1
                    assert sum(key == (commit, path) for key, source in log["parsed"]) == 1
                _closure_once(log)
        _check(repo, expected)
        for action in actions:
            oracle = expected if action == "stderr" else HISTORICAL
            with _custody(repo, rows, oracle):
                with monkeypatch.context() as patch:
                    log = _watch(repo, patch, ("meta", old["path"], action))
                    assert subject.inspect_canonical_evidence(repo) == oracle
                    assert len(_once(log, "meta", old["last_commit"], old["path"])) == 1
            _check(repo, expected)
    finally:
        _records(repo, records)
    _check(repo, healthy)


def _real_boundaries(repo, monkeypatch, body, budget_rows, records, healthy):
    assert len(body) == 2_000_000
    try:
        for size in (2_000_000, 2_000_001):
            proof = _proof(repo, "test_boundary.py", body if size == 2_000_000 else body + b"x")
            rows = [proof, {**proof, "seed_ids": [JOB], "sha256": "0" * 64}]
            expected = _raw(PROVEN if size == 2_000_000 else UNKNOWN, UNKNOWN)
            _records(repo, rows)
            with _custody(repo, rows, expected):
                with monkeypatch.context() as patch:
                    log = _watch(repo, patch)
                    assert subject.inspect_canonical_evidence(repo) == expected
                    head = git(repo, "rev-parse", "HEAD")
                    call, = _once(log, "blob", head, proof["path"])
                    assert call[3] == 2_000_000
                    assert sum(key == (head, proof["path"]) for key, source in log["parsed"]) == (size == 2_000_000)
                    _closure_once(log)
            _check(repo, expected)
        rows = budget_rows
        _records(repo, rows)
        expected = _raw(PROVEN, PROVEN)
        with _custody(repo, rows, expected):
            with monkeypatch.context() as patch:
                log = _watch(repo, patch)
                assert subject.inspect_canonical_evidence(repo) == expected
                head = git(repo, "rev-parse", "HEAD")
                for row in rows[:3]:
                    assert len(_once(log, "blob", head, row["path"])) == 1
                    assert ("blob", (head, row["path"]), 2_000_000, "success") in [
                        (kind, key, debit, state) for kind, key, before, debit, state in log["events"]]
                last, = _once(log, "blob", head, rows[-1]["path"])
                assert 0 < last[3] < 2_000_000 and last[3] == 8_000_000 - last[4]
                assert log["events"][-1] == ("blob", (head, rows[-1]["path"]), last[4], last[3], "failure")
                assert log["used"] == 8_000_000
                _closure_once(log)
        _check(repo, expected)
    finally:
        _records(repo, records)
    _check(repo, healthy)
