"""Real-Git coverage and custody for independent canonical supplemental receipts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()
from scripts import two_site_cycle_tools as subject
from tests.test_two_site_canonical_evidence_inspect import _make_canonical_repo, _run_git

INV = "docs/refactor/two-server/section-1/inventory/"
MANIFEST = INV + "seed-handler-coverage.json"
ROOT = Path(__file__).resolve().parents[1]
API, BOT, WEB, JOB = ("API-DEMO-SEED-PROVEN", "BOT-DEMO-SEED-PARTIAL",
                      "WEB-DEMO-SEED-UNKNOWN", "JOB-DEMO-SEED-EXTRA")
PROVEN, PARTIAL, UNKNOWN = "PROVEN_FOR_REQUESTED_SCENARIO", "PARTIAL_OR_SEED_ONLY", "UNKNOWN"
PATHS = (
    INV + "recent-characterization-notification-mark-all-accounting-751.json",
    INV + "recent-characterization-trade-read-prequery-accounting-757.json",
    INV + "recent-characterization-business-766-a2.json",
    INV + "recent-characterization-future-business-b3.json",
)
KEYS = ("notification_mark_all_750_path", "trade_read_prequery_757_path",
        "business_766_a2_path", "future_business_b3_path")
clone, git = copy.deepcopy, _run_git


def _doc(repo, path):
    return json.loads((repo / path).read_bytes())


def _put(repo, changes):
    assert repo.name == "supplement-lab" and repo.resolve() == repo
    for path, value in changes.items():
        raw = value if isinstance(value, bytes) else (json.dumps(value, indent=2) + "\n").encode()
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
    git(repo, "add", "--", *changes)
    git(repo, "commit", "-m", "owned supplemental evidence fixture")


def _sha(repo, path):
    return hashlib.sha256((repo / path).read_bytes()).hexdigest()


def _link(repo, path):
    return {"path": path, "sha256": _sha(repo, path),
            "last_commit": git(repo, "log", "-1", "--format=%H", "HEAD", "--", path)}


def _pin(repo):
    files = {}
    for file in repo.rglob("*"):
        if ".git" in file.relative_to(repo).parts:
            continue
        if file.is_symlink():
            value = ("link", os.readlink(file))
        elif file.is_file():
            value = ("file", file.read_bytes())
        else:
            value = ("directory", None)
        files[file.relative_to(repo).as_posix()] = (file.lstat().st_mode, value)
    return (git(repo, "rev-parse", "HEAD"),
            git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(repo, "status", "--porcelain=v1", "--untracked-files=all"), files)


def _proofs(repo, records):
    bodies = {}
    for record in records:
        classes, functions = {}, []
        for selector in record["test_selectors"]:
            parts = selector.split("::")[1:]
            if len(parts) == 1:
                functions.append("def " + parts[0] + "(): pass\n")
            else:
                classes.setdefault(parts[0], []).append(" def " + parts[1] + "(self): pass\n")
        bodies[record["path"]] = ("".join(functions) + "".join(
            "class " + name + ":\n" + "".join(methods) for name, methods in classes.items())).encode()
    parent = git(repo, "rev-parse", "HEAD")
    _put(repo, bodies)
    commit = git(repo, "rev-parse", "HEAD")
    for record in records:
        record.update(sha256=_sha(repo, record["path"]), last_commit=commit)
        assert git(repo, "log", "-1", "--format=%H", "HEAD", "--", record["path"]) == commit
    return commit, parent


def _lab(tmp_path):
    repo = (tmp_path / "supplement-lab").resolve()
    _make_canonical_repo(repo)
    packets = [_doc(ROOT, path) for path in PATHS[:3]]
    # Full actual 751/757 and accepted A2 shapes, adapted solely inside this Git lab.
    packets.append({
        "schema_version": 1, "stage": "P1-00b", "assignment_id": "WA-FUTURE-BUSINESS-B3",
        "status": "PARTIAL_RECONCILIATION", "records": [{
            "path": "tests/test_future_b3.py", "seed_ids": ["FUTURE-B3-SEED"],
            "test_selectors": ["tests/test_future_b3.py::test_future"]}],
        "coverage_status": "PARTIAL", "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        "closures_authorized": False, "canonical_blockers": clone(packets[2]["canonical_blockers"]),
        "stages": {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
    })
    additions = []
    for packet in packets:
        commit, parent = _proofs(repo, packet["records"])
        if packet["status"] == "PARTIAL_CANONICAL_INTEGRATION":
            packet["source"].update(accepted_source_commit=commit, accepted_parent=parent)
            record = packet["records"][0]
            packet["source"]["accepted_file"].update(
                path=record["path"], sha256=record["sha256"],
                utf8_bytes=(repo / record["path"]).stat().st_size)
        else:
            packet["inspected_head"] = commit
        additions.extend(seed for record in packet["records"] for seed in record["seed_ids"])
    assert len(additions) == len(set(additions)) == 7
    surface = _doc(repo, INV + "surface-api.json")
    surface["entries"].extend({"behavior_id": seed} for seed in additions)
    scenario_path = "docs/refactor/two-server/section-1/scenarios/baseline.json"
    scenario = _doc(repo, scenario_path)
    scenario["seed_mapping"]["expected_seed_count"] = 11
    manifest = _doc(repo, MANIFEST)
    manifest["inventory"]["expected_seed_count"] = 11
    manifest["counts"] = {"baseline_evidenced_seed_count": 1, "reconciled_new_seed_count": 8,
                          "evidenced_seed_count": 9, "remaining_seed_count": 2}
    manifest["evidence_sources"].update(zip(KEYS, PATHS))
    _put(repo, {INV + "surface-api.json": surface, scenario_path: scenario,
                MANIFEST: manifest, **dict(zip(PATHS, packets))})
    sentinel = repo / ".local/authority-sentinel.json"
    sentinel.parent.mkdir()
    sentinel.write_text('{"checkpoint":"unchanged","epoch":7,"reservation":"retained"}\n')
    expected = {"status": "VALID", "expected_seed_count": 11, "seed_count": 11,
                "coverage": {API: PROVEN, BOT: PARTIAL, WEB: UNKNOWN, JOB: PARTIAL,
                             **dict.fromkeys(additions, PROVEN)}}
    return repo, expected


def _target(full, seed):
    state = full["coverage"].get(seed, "NO_MATCHING_EVIDENCE")
    return {"status": "VALID", "expected_seed_count": full["expected_seed_count"],
            "seed_count": full["seed_count"], "coverage": {seed: state} if seed in full["coverage"] else {},
            "requested_coverage": state}


def _page(full):
    coverage = dict(sorted(full["coverage"].items()))
    return {"schema_version": 1, "display_only": True, **full, "coverage": coverage,
            "total_entries": len(coverage), "offset": 0, "returned_entries": len(coverage),
            "omitted_entries": 0, "next_offset": None}


def _oracle(repo, expected, *, cli=False, presentation=False,
            targets=("FUTURE-B3-SEED", API, "ABSENT"), unrelated=None):
    before = _pin(repo)
    try:
        assert subject.inspect_canonical_evidence(repo) == expected
        error = expected["status"] != "VALID"
        for seed in dict.fromkeys(targets):
            target = (_target(unrelated, seed) if unrelated is not None and seed in (API, "ABSENT")
                      else expected if error else _target(expected, seed))
            assert subject.inspect_canonical_evidence(repo, seed) == target
        page = ({"schema_version": 1, "display_only": True, **expected}
                if error else _page(expected))
        if presentation:
            assert subject.inventory_view(repo) == page
        if cli:
            script = ROOT / "scripts/two_site_cycle_tools.py"
            result = subject._run_bounded_process(
                [sys.executable, str(script), "--root", str(repo), "inventory"],
                cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                               "APP_ENV_FILE": "config/unit-test.env.example"},
                timeout=30, output_limit=65536)
            assert result.returncode == (1 if error else 0)
            assert result.stderr == b"" and json.loads(result.stdout) == page
    finally:
        assert _pin(repo) == before


def _error(reason):
    return {"status": "INVENTORY_INCONSISTENT", "reason": reason}


def _envelope_error(path=PATHS[3]):
    return _error("independent supplemental receipt invalid: " + path)


def test_actual_751_757_a2_and_future_b3_whole_reader_and_cli(tmp_path):
    repo, healthy = _lab(tmp_path)
    _oracle(repo, healthy, cli=True, presentation=True,
            targets=(*healthy["coverage"], "ABSENT"))
    affected_a2 = tuple(seed for record in _doc(repo, PATHS[2])["records"]
                        for seed in record["seed_ids"]) + (API, "ABSENT")
    manifest = _doc(repo, MANIFEST)
    del manifest["evidence_sources"][KEYS[2]]
    _put(repo, {MANIFEST: manifest})
    unlisted = clone(healthy)
    for record in _doc(repo, PATHS[2])["records"]:
        for seed in record["seed_ids"]:
            unlisted["coverage"][seed] = PARTIAL
    _oracle(repo, unlisted, cli=True, presentation=True, targets=affected_a2)
    # Present, committed and structurally valid is insufficient without declaration.
    assert (repo / PATHS[2]).is_file()
    manifest["evidence_sources"][KEYS[2]] = PATHS[2]
    _put(repo, {MANIFEST: manifest})
    _oracle(repo, healthy, targets=affected_a2)


@pytest.mark.parametrize("key", [
    "continuation_0_path", "continuation_1_path", "continuation_01_path",
    "continuation_65_path", "continuation_future_path", "continuation__path",
    "continuation_2_extra_path", "unknown_manifest_key", "_future_path",
    "Future_path", "future__path",
])
def test_malformed_reserved_and_descriptive_keys_cannot_fall_through(tmp_path, key):
    repo, _ = _lab(tmp_path)
    manifest = _doc(repo, MANIFEST)
    manifest["evidence_sources"][key] = manifest["evidence_sources"].pop(KEYS[3])
    _put(repo, {MANIFEST: manifest})
    _oracle(repo, _error("canonical manifest inventory or sources invalid"),
            presentation=key == "continuation_0_path")


@pytest.mark.parametrize("path", [
    "../recent-characterization-future.json", "/tmp/recent-characterization-future.json",
    "docs/recent-characterization-future.json", INV + "recent-characterization.json",
    INV + "recent-characterization-continuation.json",
    INV + "recent-characterization-continuation-2.json",
    INV + "recent-characterization-continuation-02.json",
    INV + "recent-characterization-future/receipt.json",
    INV + "recent-characterization-future..json", None, 1, [],
])
def test_supplement_path_namespace_and_types(tmp_path, path):
    repo, _ = _lab(tmp_path)
    manifest = _doc(repo, MANIFEST)
    manifest["evidence_sources"][KEYS[3]] = path
    _put(repo, {MANIFEST: manifest})
    _oracle(repo, _error("canonical manifest inventory or sources invalid"))


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 1.0), ("schema_version", 2),
    ("stage", "P1-00"), ("assignment_id", ""), ("assignment_id", None),
    ("assignment_id", " WA-FUTURE "), ("status", ["PARTIAL_RECONCILIATION"]),
    ("status", "COMPLETE"), ("receipt_type", "PARTIAL_CANONICAL_INTEGRATION"),
    ("coverage_status", "COMPLETE"), ("seed_acceptance_status", "ACCEPTED"),
    ("closures_authorized", 0), ("closures_authorized", True),
    ("canonical_blockers", []), ("canonical_blockers", "OPEN"),
    ("canonical_blockers", [1]), ("stages", {"P1-00": "COMPLETE", "P1-00b": "COMPLETE"}),
    ("stages", None), ("records", {}), ("base_inventory", None),
    ("predecessor_inventory", {}), ("inspected_head", True), ("inspected_head", "HEAD"),
])
def test_strict_partial_envelope_even_for_unrelated_target(tmp_path, key, value):
    repo, _ = _lab(tmp_path)
    packet = _doc(repo, PATHS[3])
    packet[key] = value
    _put(repo, {PATHS[3]: packet})
    _oracle(repo, _envelope_error(), presentation=key == "schema_version" and value is True)


@pytest.mark.parametrize("raw,reason", [
    (b"true\n", "canonical document is not an object: " + PATHS[3]),
    (b"[]\n", "canonical document is not an object: " + PATHS[3]),
    (b'{"schema_version":1,"schema_version":1}\n', "duplicate JSON key"),
])
def test_supplement_json_object_and_duplicate_keys(tmp_path, raw, reason):
    repo, _ = _lab(tmp_path)
    _put(repo, {PATHS[3]: raw})
    _oracle(repo, _error(reason), presentation=raw != b"[]\n")


@pytest.mark.parametrize("seeds", [[True], ["ABSENT"], None])
def test_supplement_seed_ids_validate_before_target_filter(tmp_path, seeds):
    repo, _ = _lab(tmp_path)
    packet = _doc(repo, PATHS[3])
    packet["records"][0]["seed_ids"] = seeds
    _put(repo, {PATHS[3]: packet})
    _oracle(repo, _error("record seed IDs invalid"), presentation=seeds is None)


@pytest.mark.parametrize("field", ["receipt_type", "source", "accepted_source_commit",
                                   "accepted_parent", "inspected_head"])
def test_integration_envelope_requires_its_actual_ancestor_shape(tmp_path, field):
    repo, _ = _lab(tmp_path)
    packet = _doc(repo, PATHS[0])
    if field in ("accepted_source_commit", "accepted_parent"):
        packet["source"][field] = True
    elif field == "inspected_head":
        packet[field] = "0" * 40
    else:
        packet.pop(field)
    _put(repo, {PATHS[0]: packet})
    _oracle(repo, _envelope_error(PATHS[0]),
            targets=(*packet["records"][0]["seed_ids"], API, "ABSENT"))


def test_real_nonancestor_document_and_record_proofs(tmp_path):
    repo, healthy = _lab(tmp_path)
    good = _doc(repo, PATHS[3])
    parent = git(repo, "rev-parse", "HEAD")
    side = git(repo, "commit-tree", git(repo, "rev-parse", "HEAD^{tree}"),
               "-p", parent, "-m", "independent side commit")
    assert git(repo, "show", side + ":" + good["records"][0]["path"])
    packet = clone(good)
    packet["inspected_head"] = side
    _put(repo, {PATHS[3]: packet})
    _oracle(repo, _envelope_error())
    packet = clone(good)
    packet["records"][0]["last_commit"] = side
    _put(repo, {PATHS[3]: packet})
    # Exact legacy target filtering: unrelated and absent targets remain VALID.
    _oracle(repo, _error("historical record invalid"), unrelated=healthy, presentation=True)
    _put(repo, {PATHS[3]: good})
    _oracle(repo, healthy)


@pytest.mark.parametrize("fault", ["sha", "selector", "shape", "untracked", "mode", "syntax"])
def test_supplement_records_use_existing_proof_and_unknown_rules(tmp_path, fault):
    repo, healthy = _lab(tmp_path)
    packet = _doc(repo, PATHS[3])
    record = packet["records"][0]
    seed, path = record["seed_ids"][0], record["path"]
    if fault == "sha":
        record["sha256"] = "0" * 64
    elif fault == "selector":
        record["test_selectors"] = [path + "::test_missing"]
    elif fault == "shape":
        record["test_selectors"] = [True]
    elif fault == "untracked":
        record["path"] = "tests/untracked.py"
        record["test_selectors"] = ["tests/untracked.py::test_future"]
        (repo / record["path"]).write_text("def test_future(): pass\n")
    else:
        body = b"def test_future(:\n" if fault == "syntax" else b"def test_future(): pass\n"
        if fault == "syntax":
            _put(repo, {path: body})
        if fault == "mode":
            # Real Git symlink metadata, with safe ordinary live pathname as in legacy proofs.
            (repo / path).unlink()
            (repo / path).symlink_to("target.py")
            git(repo, "add", "--", path)
            git(repo, "commit", "-m", "owned unsupported proof mode")
            (repo / path).unlink()
            (repo / path).write_bytes(body)
        record.update(last_commit=git(repo, "log", "-1", "--format=%H", "HEAD", "--", path))
        raw = subject._git_read(repo, ("show", record["last_commit"] + ":" + path), output_limit=4096)
        record["sha256"] = hashlib.sha256(raw).hexdigest()
    _put(repo, {PATHS[3]: packet})
    unknown = clone(healthy)
    unknown["coverage"][seed] = UNKNOWN
    _oracle(repo, unknown, presentation=fault == "selector")


def test_current_duplicate_and_historical_provenance_are_not_silent_credit(tmp_path):
    repo, healthy = _lab(tmp_path)
    good = _doc(repo, PATHS[3])
    row = good["records"][0]
    duplicate = clone(good)
    duplicate["records"].append(clone(row))
    _put(repo, {PATHS[3]: duplicate})
    _oracle(repo, _error("conflicting current evidence: " + row["seed_ids"][0]),
            unrelated=healthy, presentation=True)
    _put(repo, {PATHS[3]: good})
    _put(repo, {row["path"]: b"def test_future(): pass\n# a later accepted file version\n"})
    historical = clone(healthy)
    historical["coverage"][row["seed_ids"][0]] = UNKNOWN
    _oracle(repo, historical)
    bad = clone(good)
    bad["records"][0]["sha256"] = "0" * 64
    _put(repo, {PATHS[3]: bad})
    _oracle(repo, _error("historical record invalid"), unrelated=healthy)
    _put(repo, {PATHS[3]: good})
    _oracle(repo, historical)


@pytest.mark.parametrize("fault", ["live_drift", "missing", "hardlink", "symlink", "duplicate_source"])
def test_supplement_document_custody_and_uniqueness(tmp_path, fault):
    repo, healthy = _lab(tmp_path)
    path, raw = repo / PATHS[3], (repo / PATHS[3]).read_bytes()
    alias = repo / ".local/alias.json"
    if fault == "duplicate_source":
        manifest = _doc(repo, MANIFEST)
        manifest["evidence_sources"]["another_future_path"] = PATHS[3]
        _put(repo, {MANIFEST: manifest})
        _oracle(repo, _error("canonical source paths conflict"), presentation=True)
        return
    try:
        if fault == "live_drift":
            path.write_bytes(raw + b" ")
            reason = "live canonical bytes differ from HEAD: " + PATHS[3]
        elif fault == "missing":
            path.unlink()
            reason = "canonical file read failed"
        elif fault == "hardlink":
            os.link(path, alias)
            reason = "canonical file read failed"
        else:
            alias.write_bytes(raw)
            path.unlink()
            path.symlink_to(alias)
            reason = "symlink or path escape"
        _oracle(repo, _error(reason), presentation=fault != "hardlink")
    finally:
        if path.is_symlink():
            path.unlink()
        if alias.exists():
            alias.unlink()
        path.write_bytes(raw)
    _oracle(repo, healthy)


def test_declared_missing_unread_supplement_never_credits(tmp_path):
    repo, healthy = _lab(tmp_path)
    path = INV + "recent-characterization-never-written.json"
    manifest = _doc(repo, MANIFEST)
    manifest["evidence_sources"]["future_unread_path"] = path
    _put(repo, {MANIFEST: manifest})
    command = ["/usr/bin/git", "--no-replace-objects", "-C", str(repo),
               "ls-files", "--error-unmatch", "--", path]
    _oracle(repo, _error(str(subprocess.CalledProcessError(1, command))), presentation=True)
    del manifest["evidence_sources"]["future_unread_path"]
    _put(repo, {MANIFEST: manifest})
    _oracle(repo, healthy)


def test_targeted_request_still_audits_complete_numbered_predecessor_graph(tmp_path):
    repo, _ = _lab(tmp_path)
    path = INV + "continuation-3.json"
    packet = _doc(repo, path)
    packet["predecessor_inventory"]["sha256"] = "0" * 64
    _put(repo, {path: packet})
    _oracle(repo, _error("continuation 3 invalid"), presentation=True)


def test_combined_64_allowance_includes_hidden_predecessors(tmp_path):
    repo, healthy = _lab(tmp_path)
    manifest, changes = _doc(repo, MANIFEST), {}
    empty = _doc(repo, PATHS[3])
    empty["records"] = []
    for number in range(57):
        path = INV + "recent-characterization-generic-%d.json" % number
        changes[path] = clone(empty)
        manifest["evidence_sources"]["generic_%d_path" % number] = path
    changes[MANIFEST] = manifest
    _put(repo, changes)
    assert len(manifest["evidence_sources"]) - 2 == 64
    _oracle(repo, healthy)
    hidden = INV + "recent-characterization-continuation-2.json"
    _put(repo, {hidden: _doc(repo, INV + "continuation-2.json")})
    third = _doc(repo, INV + "continuation-3.json")
    third["predecessor_inventory"] = _link(repo, hidden)
    del manifest["evidence_sources"]["continuation_2_path"]
    _put(repo, {INV + "continuation-3.json": third, MANIFEST: manifest})
    _oracle(repo, healthy)
    # Selected count64 fits, but its distinct hidden predecessor makes total65.
    extra = INV + "recent-characterization-one-more.json"
    manifest["evidence_sources"]["one_more_path"] = extra
    _put(repo, {extra: empty, MANIFEST: manifest})
    assert len(manifest["evidence_sources"]) - 2 == 64
    _oracle(repo, _error("canonical predecessor graph invalid"), presentation=True)
    manifest["evidence_sources"]["two_more_path"] = INV + "recent-characterization-two-more.json"
    _put(repo, {MANIFEST: manifest})
    _oracle(repo, _error("canonical manifest inventory or sources invalid"))


def _padded(packet, size):
    packet = clone(packet)
    packet["padding"] = ""
    raw = (json.dumps(packet, indent=2) + "\n").encode()
    assert len(raw) < size
    packet["padding"] = "x" * (size - len(raw))
    result = (json.dumps(packet, indent=2) + "\n").encode()
    assert len(result) == size
    return result


def test_real_supplement_per_file_and_total_content_boundaries(tmp_path):
    repo, healthy = _lab(tmp_path)
    assert subject.CANONICAL_READ_LIMIT_BYTES == 2_000_000
    assert subject.CANONICAL_TOTAL_BUDGET_BYTES == 8_000_000
    packet = _doc(repo, PATHS[3])
    _put(repo, {PATHS[3]: _padded(packet, 2_000_000)})
    _oracle(repo, healthy)
    _put(repo, {PATHS[3]: _padded(packet, 2_000_001)})
    _oracle(repo, _error("canonical file read failed"))
    _put(repo, {PATHS[3]: packet})
    _oracle(repo, healthy)
    _put(repo, {path: _padded(_doc(repo, path), 1_400_000) for path in PATHS[:3]})
    affected = tuple(seed for path in PATHS[:3] for record in _doc(repo, path)["records"]
                     for seed in record["seed_ids"]) + (API, "ABSENT")
    _oracle(repo, _error("bounded Git blob read failed"), targets=affected, presentation=True)


def test_same_whole_oracle_detects_reached_gate_mutant_then_restores(tmp_path, monkeypatch):
    repo, healthy = _lab(tmp_path)
    good = _doc(repo, PATHS[3])
    bad = clone(good)
    row = bad["records"][0]
    row["test_selectors"] = [row["path"] + "::test_missing"]
    negative = clone(healthy)
    negative["coverage"][row["seed_ids"][0]] = UNKNOWN
    original, reached = subject._resolve_selectors_in_source, []

    def bypass(cells, selectors, path):
        value = original(cells, selectors, path)
        if path == row["path"]:
            reached.append((path, clone(selectors), value))
            return True
        return value

    _oracle(repo, healthy)
    try:
        _put(repo, {PATHS[3]: bad})
        _oracle(repo, negative, presentation=True)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(subject, "_resolve_selectors_in_source", bypass)
                with pytest.raises(AssertionError):
                    _oracle(repo, negative, presentation=True)
                assert (row["path"], row["test_selectors"], False) in reached
        finally:
            assert subject._resolve_selectors_in_source is original
        _oracle(repo, negative, presentation=True)
    finally:
        _put(repo, {PATHS[3]: good})
        assert _doc(repo, PATHS[3]) == good
        assert subject._resolve_selectors_in_source is original
    _oracle(repo, healthy, cli=True, presentation=True)
