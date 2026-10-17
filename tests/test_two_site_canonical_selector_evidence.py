"""Complete public selector reports and real bounded forwarding with custody."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()
from tests import test_two_site_canonical_selector_support as s


def test_reports_and_detachment(tmp_path):
    repo = s._lab(tmp_path)
    full = s._raw()
    for seed in (*full["coverage"], "ABSENT", None):
        s._check(repo, full, seed)
    with s._custody(repo, full):
        first = s.subject.inspect_canonical_evidence(repo)
        previous = s.clone(first)
        second = s.subject.inventory_view(repo, limit=1)
        first["coverage"][s.BOT] = "mutated"
        second["coverage"].clear()
        assert previous == full
        s._check(repo, full)
        assert first["coverage"][s.BOT] == "mutated" and second["coverage"] == {}


STATIC = [
    (s.GOOD, "test_ok", True), ("async def test_ok(): pass\n", "test_ok", True),
    ("async def test_健全(): pass\n", "test_健全", True),
    ("class Suite:\n def test_ok(self): pass\n", "Suite::test_ok", True),
    ("class Suite:\n async def test_ok(self): pass\n", "Suite::test_ok", True),
    ("class Suite:\n global test_ok\n def test_ok(self): pass\n", "Suite::test_ok", False),
    ("global test_ok\n" + s.GOOD, "test_ok", True),
    ("class Suite:\n def test_ok(self):\n  global test_ok\n", "Suite::test_ok", True),
    (s.GOOD + "class Other:\n global test_ok\n test_ok = None\n", "test_ok", False),
    ("class Suite:\n def test_ok(self): pass\nclass Other:\n global Suite\n Suite = None\n",
     "Suite::test_ok", False),
    (s.GOOD + "class Outer:\n if True:\n  class Other:\n   global test_ok\n   test_ok = None\n",
     "test_ok", False),
    (s.GOOD + "class Other:\n def helper(self):\n  global test_ok\n", "test_ok", True),
    (s.GOOD + "test_ok.value = 1\ntest_ok[0] = 1\n", "test_ok", True),
    ("def outer():\n def test_ok(): pass\n", "test_ok", False),
    ("class Outer:\n class Suite:\n  def test_ok(self): pass\n", "Suite::test_ok", False),
    ("class Parent:\n def test_ok(self): pass\nclass Suite(Parent): pass\n", "Suite::test_ok", False),
    ("from other import test_ok\n", "test_ok", False),
    ("from other import *\n" + s.GOOD, "test_ok", True),
    (s.GOOD + "from other import *\n", "test_ok", False),
    ("test_ok = lambda: None\n", "test_ok", False), (s.GOOD + s.GOOD, "test_ok", False),
    ("class Suite: pass\nclass Suite:\n def test_ok(self): pass\n", "Suite::test_ok", False),
    ("class Suite:\n def test_ok(self): pass\n def test_ok(self): pass\n", "Suite::test_ok", False),
    ("class Suite: pass\n", "Suite::test_ok", False), (s.GOOD, "Missing::test_ok", False),
    ("def other(): pass\n", "test_ok", False), ("def test_ok(:\n", "test_ok", False),
    (b"# coding: latin-1\n# \xff\ndef test_ok(): pass\n", "test_ok", False),
]
BINDINGS = ["test_ok = 1", "test_ok: int = 1", "test_ok += 1", "del test_ok",
            "for test_ok in []: pass", "with manager as test_ok: pass", "if True: test_ok = 1",
            "try: pass\nexcept Exception as test_ok: pass", "(test_ok := 1)",
            "match item:\n case test_ok: pass"]
METADATA = ("path", "oid", "upper", "nul", "fragment", "end", "mode", "type", "stderr", "failure")


def test_static_current_and_historical(tmp_path):
    repo = s._lab(tmp_path)
    competitor = s._proof(repo, "test_competitor.py")
    rows = STATIC + [(s.GOOD + binding + "\n", "test_ok", False) for binding in BINDINGS]
    rows += [("class Suite:\n def test_ok(self): pass\n " + binding.replace("\n", "\n ") + "\n",
              "Suite::test_ok", False) for binding in BINDINGS]
    rows += [("class Suite:\n def test_ok(self): pass\n" + binding + "\n", "Suite::test_ok", False)
             for binding in ("Suite = 1", "del Suite", "if True: Suite = 1",
                "for Suite in []: pass", "with manager as Suite: pass",
                "import other as Suite", "from other import Suite")]
    multi = s.GOOD + "async def test_two(): pass\n"
    rows += [(multi, ["test_ok", name], name == "test_two") for name in
             ("test_two", "test_missing", "Suite::test_missing", "::test_ok", "test_ok::extra",
              "test_ok[param]", "not_a_test", "a::b::test_ok", "test-bad", "test_ok ")]
    rows += [(s.GOOD, "test_ok", mode in ("100644", "100755"), mode)
             for mode in ("100644", "100755", "120000", "040000", "160000")]
    for index, (body, selectors, supported, *modes) in enumerate(rows):
        name, mode = "test_case_%d.py" % index, modes[0] if modes else "100644"
        record = s._proof(repo, name, body, mode)
        record["test_selectors"] = [record["path"] + "::" + value for value in
                                    ([selectors] if isinstance(selectors, str) else selectors)]
        if mode == "120000":
            (repo / record["path"]).unlink()
            (repo / record["path"]).write_text(s.GOOD)
        s._row(repo, [record, competitor], s.PROVEN if supported else s.UNKNOWN)
        if mode == "160000":
            s.git(repo, "update-index", "--force-remove", "--", record["path"])
        s._proof(repo, name, "def test_new(): pass\n")
        s._row(repo, record, s.UNKNOWN if supported else s.HISTORICAL)
        s._row(repo, [record, competitor], s.PROVEN if supported else s.HISTORICAL)


def test_shapes_paths_and_canonical_dirt(tmp_path):
    repo = s._lab(tmp_path)
    old = s._proof(repo, "test_shape.py")
    current = s._proof(repo, "test_shape.py", "def test_new(): pass\n")
    for row in (old, current):
        for shape in ("missing", None, "bad", [], [""], [1], ["valid", None]):
            bad = s.clone(row)
            if shape == "missing":
                del bad["test_selectors"]
            else:
                bad["test_selectors"] = shape
            s._row(repo, bad, s.UNKNOWN)
    current = s._proof(repo, "test_paths.py")
    competitor = s._proof(repo, "test_path_competitor.py")
    selectors = ("tests/wrong.py::test_ok", "/tests/test_paths.py::test_ok",
                 "tests/../test_paths.py::test_ok", "tests\\test_paths.py::test_ok",
                 "tests/test_paths.py", "tests/test_paths.py::",
                 "tests//test_paths.py::test_ok", "tests/./test_paths.py::test_ok")
    for selector in selectors:
        s._row(repo, [{**current, "test_selectors": [selector]}, competitor], s.UNKNOWN)
    s._proof(repo, "test_paths.py", "def test_new(): pass\n")
    for selector in selectors:
        s._row(repo, [{**current, "test_selectors": [selector]}, competitor], s.HISTORICAL)
    current = competitor
    for path in ("../escape.py", "/tmp/escape.py", "tests/../escape.py"):
        s._row(repo, {**current, "path": path}, {"status": "INVENTORY_INCONSISTENT",
             "reason": "noncanonical file path" if not path.startswith("/") else "unsafe source path"})
    s._row(repo, {**current, "path": "tests/missing.py"}, s.UNKNOWN)
    (repo / "tests/untracked.py").write_text(s.GOOD)
    s._row(repo, {**current, "path": "tests/untracked.py"}, s.UNKNOWN)
    s._row(repo, {**current, "sha256": "0" * 64}, s.UNKNOWN)
    s._row(repo, {**old, "sha256": "0" * 64}, s.HISTORICAL)
    bad = {**old, "test_selectors": [old["path"] + "::test_new"]}
    s._records(repo, [bad])
    s._check(repo, s._target(s.API, s.PROVEN), s.API)
    s._row(repo, {**bad, "seed_ids": []}, s.PARTIAL)
    file, saved = repo / s.MANIFEST, (repo / s.MANIFEST).read_bytes()
    try:
        file.write_bytes(saved + b" ")
        error = {"status": "INVENTORY_INCONSISTENT", "reason": "live canonical bytes differ from HEAD: " + s.MANIFEST}
        s._check(repo, error)
    finally:
        file.write_bytes(saved)
    s._row(repo, [], s.PARTIAL)


def test_same_negative_witness_and_metadata_restoration(tmp_path, monkeypatch):
    repo = s._lab(tmp_path)
    good, other = s._proof(repo, "test_good.py"), s._proof(repo, "test_other.py")
    healthy, negative = s._raw(s.PROVEN), s._raw(s.UNKNOWN)
    records = [good, other]
    s._records(repo, records)
    original = {path: (repo / path).read_bytes() for path in (s.LEAF, s.MANIFEST)}
    original_files, original_facts = s._pin(repo)[3], s._phase(repo)
    s._check(repo, healthy)
    bad = {**s.clone(good), "test_selectors": [good["path"] + "::test_missing"]}
    real, reached = s.subject._resolve_selectors_in_source, []

    def bypass(cells, selectors, path):
        reached.append((path, s.clone(selectors)))
        return True

    def witness():
        s._check(repo, negative)

    try:
        s._records(repo, [bad, other])
        witness()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(s.subject, "_resolve_selectors_in_source", bypass)
                with pytest.raises(AssertionError):
                    witness()
                assert (good["path"], bad["test_selectors"]) in reached
        finally:
            assert s.subject._resolve_selectors_in_source is real
        witness()
    finally:
        s._records(repo, records)
        assert {path: (repo / path).read_bytes() for path in original} == original
        assert s._pin(repo)[3] == original_files and s._phase(repo) == original_facts
        assert json.loads((repo / s.LEAF).read_bytes())["records"] == records
    s._check(repo, healthy)
    file, saved = repo / good["path"], (repo / good["path"]).read_bytes()
    try:
        file.write_bytes(b"invalid Python !")
        s._check(repo, healthy)
    finally:
        file.write_bytes(saved)
    s._row(repo, [good, good], {"status": "INVENTORY_INCONSISTENT",
         "reason": "conflicting current evidence: " + s.BOT})
    s._proof(repo, "test_good.py", "def test_new(): pass\n")
    s._row(repo, records, s.PROVEN)
    s._row(repo, {**good, "test_selectors": [good["path"] + "::test_new"]}, s.HISTORICAL)


def test_native_c4_nonpython_and_sparse_closure(tmp_path, monkeypatch):
    repo, c0, sha0, c4, sha4 = s.continuation._make_continuation4_repo(tmp_path / "c4" / "selector-lab")
    for commit, digest in ((c0, sha0), (c4, sha4)):
        assert hashlib.sha256(s._bytes(repo, "show", commit + ":tests/test_sample.py")).hexdigest() == digest
    assert c0 != c4 and sha0 != sha4
    s._check(repo, s._target(s.API, s.PROVEN), s.API)
    repo = s._lab(tmp_path)
    row = s._proof(repo, "not_python.md")
    competitor = s._proof(repo, "test_md_competitor.py")
    s._row(repo, [row, competitor], s.UNKNOWN)
    s._proof(repo, "not_python.md", "def test_new(): pass\n")
    s._row(repo, [row, competitor], s.HISTORICAL)
    hidden = s.INV + "recent-characterization-continuation-4.json"
    s._put(repo, hidden, {"schema_version": 1, "stage": "P1-00b", "records": [row],
                       "predecessor_inventory": s._link(repo, s.INV + "continuation-3.json")})
    path = hidden
    for index in range(5, 65):
        following = s.INV + ("continuation-64.json" if index == 64 else
                           "recent-characterization-continuation-%d.json" % index)
        s._put(repo, following, {"schema_version": 1, "stage": "P1-00b", "records": [],
                              "predecessor_inventory": s._link(repo, path)})
        path = following
    manifest = json.loads((repo / s.MANIFEST).read_bytes())
    del manifest["evidence_sources"]["continuation_4_path"]
    manifest["evidence_sources"]["continuation_64_path"] = path
    s._put(repo, s.MANIFEST, manifest)
    expected = s._raw()
    with s._custody(repo, expected):
        with monkeypatch.context() as patch:
            log = s._watch(repo, patch)
            assert s.subject.inspect_canonical_evidence(repo) == expected
            s._closure_once(log)
            live = [path for kind, commit, path, limit, before in log["calls"] if kind == "live"]
            assert len([path for path in live if "continuation-" in path]) == 64
    s._check(repo, expected)


def test_actual_cache_metadata_and_debits(tmp_path, monkeypatch):
    repo = s._lab(tmp_path)
    body = s.GOOD + "async def test_two(): pass\n"
    row, later = s._proof(repo, "test_cache.py", body), s._proof(repo, "test_later.py")
    row["test_selectors"].append(row["path"] + "::test_two")
    later["seed_ids"] = [s.WEB]
    records = [row, {**s.clone(row), "seed_ids": [s.JOB]},
               {**s.clone(row), "seed_ids": [s.WEB], "sha256": "0" * 64}, later]
    s._records(repo, records)
    healthy = s._raw(s.PROVEN, s.PROVEN, s.PROVEN)
    for kind, action in [("meta", value) for value in (None, *METADATA)] + [("blob", "failure")]:
        fault = None if action is None else (kind, row["path"], action)
        state = s.PROVEN if action in (None, "stderr") else s.UNKNOWN
        expected = s._raw(state, state, s.PROVEN)
        total = 3_000_000 if kind == "blob" else 100_000
        with s._custody(repo, records, expected):
            with monkeypatch.context() as patch:
                patch.setattr(s.subject, "CANONICAL_TOTAL_BUDGET_BYTES", total)
                log = s._watch(repo, patch, fault, total=total)
                assert s.subject.inspect_canonical_evidence(repo) == expected
                head = s.git(repo, "rev-parse", "HEAD")
                assert len(s._once(log, "meta", head, row["path"])) == 1
                assert len(s._once(log, "blob", head, row["path"])) == (1 if state == s.PROVEN or kind == "blob" else 0)
                assert log["parsed"].count(((head, row["path"]), body)) == (1 if state == s.PROVEN else 0)
                last, = s._once(log, "blob", head, later["path"])
                assert last[3] < 2_000_000 and last[3] == total - last[4]
                s._closure_once(log)
                if action == "stderr":
                    event, = [event for event in log["events"] if event[:2] == ("meta", (head, row["path"]))]
                    assert event[3] == len(s._bytes(repo, "ls-tree", "-z", head, "--", row["path"])) + 37
                if action == "failure":
                    call, = s._once(log, kind, head, row["path"])
                    assert (kind, (head, row["path"]), call[4], call[3], "failure") in log["events"]
        s._check(repo, healthy)
    with s._custody(repo, records, healthy):
        with monkeypatch.context() as patch:
            baseline = s._watch(repo, patch)
            assert s.subject.inspect_canonical_evidence(repo) == healthy
    prefix = next(used for kind, commit, path, limit, used in baseline["calls"]
                  if kind == "meta" and path == row["path"])
    with s._custody(repo, records):
        with monkeypatch.context() as patch:
            patch.setattr(s.subject, "CANONICAL_TOTAL_BUDGET_BYTES", prefix + 17)
            log = s._watch(repo, patch, total=prefix + 17)
            assert s.subject.inspect_canonical_evidence(repo) == s._raw(s.UNKNOWN, s.UNKNOWN)
            assert log["calls"][-1] == ("meta", s.git(repo, "rev-parse", "HEAD"), row["path"], 17, prefix)
            assert log["used"] == prefix + 17 and log["events"][-1][-2:] == (17, "failure")
    s._check(repo, healthy)
    versions = [s._proof(repo, "test_history.py", s.GOOD + "# " + name + "\n")
                for name in ("old", "middle", "latest")]
    history = [versions[0], {**versions[0], "seed_ids": [s.JOB]},
               {**versions[1], "seed_ids": [s.WEB]}, versions[2], later]
    s._historical_cache(repo, monkeypatch, versions, history, METADATA, records, healthy)
    body = s.GOOD.encode() + b"#" + b"x" * (2_000_000 - len(s.GOOD.encode()) - 1)
    budget = [s._proof(repo, "test_budget_%d.py" % index, body) for index in range(4)]
    for row, seed in zip(budget, (s.BOT, s.JOB, s.BOT, s.WEB)):
        row["seed_ids"] = [seed]
    s._real_boundaries(repo, monkeypatch, body, budget, records, healthy)


@pytest.mark.parametrize("name", ["test_bounded_reads_and_cache_matrix",
    "test_boundary_fixtures_shared_budget_counter", "test_repeated_sandbox_error_consumes_conservatively_no_retry",
    "test_unexpected_exceptions_propagate"])
def test_supplied_executed_witnesses(tmp_path, monkeypatch, name):
    repo = s.canonical._make_canonical_repo((tmp_path / "selector-lab").resolve())
    with s._custody(repo):
        with monkeypatch.context() as patch:
            getattr(s.bounds, name)(repo, patch)
    s._check(repo, s._raw())


@pytest.mark.parametrize("seam,phase", [(seam, phase) for phase in ("current", "historical")
    for seam in ("blob", "meta", "parse")] + [("blob", "canonical"), ("live", "canonical")])
@pytest.mark.parametrize("error", [TypeError, RuntimeError, GeneratorExit, s.subject.SandboxError])
def test_typed_seams_restore(tmp_path, monkeypatch, seam, error, phase):
    repo = s._lab(tmp_path)
    row = s._proof(repo, "test_errors.py")
    if phase == "historical":
        s._proof(repo, "test_errors.py", "def test_changed(): pass\n")
    s._records(repo, [row])
    owner, name = {"blob": (s.subject, "_git_read"), "meta": (s.subject, "_run_bounded_process"),
                   "live": (s.subject.runner, "bounded_file_bytes"), "parse": (s.subject.ast, "parse")}[seam]
    real, reached = getattr(owner, name), []

    def fail(*args, **kwargs):
        value = real(*args, **kwargs)
        selected = (seam == "live" or seam == "parse" and args[0] == s.GOOD or
                    seam == "meta" and args[0][-1] == row["path"] or
                    seam == "blob" and args[1][-1].endswith(":" +
                        (s.MANIFEST if phase == "canonical" else row["path"])))
        if selected:
            reached.append(True)
            raise error("typed forwarded seam")
        return value

    with s._custody(repo, row, phase):
        with monkeypatch.context() as patch:
            patch.setattr(owner, name, fail)
            if error is s.subject.SandboxError:
                expected = s.HISTORICAL if phase == "historical" else s._target(s.BOT, s.UNKNOWN)
                if phase == "canonical":
                    expected = {"status": "INVENTORY_INCONSISTENT", "reason":
                        "canonical file read failed" if seam == "live" else "bounded Git blob read failed"}
                assert s.subject.inspect_canonical_evidence(repo, s.BOT) == expected
            else:
                with pytest.raises(error):
                    s.subject.inspect_canonical_evidence(repo, s.BOT)
            assert reached
        assert getattr(owner, name) is real
    s._check(repo, s._raw(s.UNKNOWN if phase == "historical" else s.PROVEN))
