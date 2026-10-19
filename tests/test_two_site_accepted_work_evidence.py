"""Whole private-file oracles; no Git, acceptance, activation or savings proof."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
import copy
import hashlib
import inspect
import json
from pathlib import Path
import stat
import traceback

import pytest

from scripts import two_site_accepted_work_evidence as evidence
from scripts import two_site_work_cost_report as legacy_report


LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
METRICS = ".local/two-site-refactor/autopilot/metrics.json"
TASKS = ".local/two-site-refactor/tasks"
PROPOSALS = ".local/two-site-refactor/proposals"
PIPELINE = ".local/two-site-refactor/pipeline"
AUTOPILOT = ".local/two-site-refactor/autopilot"
INVENTORY = "docs/refactor/two-server/section-1/inventory"
FIXED_CODES = {
    "INVALID_ROOT", "INVALID_KEY", "UNSAFE_PATH", "UNSAFE_FILE", "MISSING_FILE",
    "CUSTODY_DRIFT", "BYTE_LIMIT", "COUNT_LIMIT", "TIME_LIMIT", "JSON_INVALID",
    "CLOSED_SNAPSHOT",
}
PRIVATE_SENTINEL = "fixture-" + "untrusted-738L-" + "must-not-render"
STAT_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size",
    "st_mtime_ns", "st_ctime_ns",
)


class ArtifactTree:
    """Actual, disposable files; expected paths do not call Subject resolvers."""

    def __init__(self, root):
        self.root = root
        root.mkdir(mode=0o700)
        for directory in (TASKS, PROPOSALS, PIPELINE, AUTOPILOT, INVENTORY):
            (root / directory).mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(body)
        path.chmod(0o600)
        return path


def digest(body):
    return hashlib.sha256(body).hexdigest()


def metadata_oracle(artifacts, *, bytes_read=None):
    """The literal public shape, derived only from independent fixture bytes."""
    return {
        "schema_version": 1,
        "files": [
            {"path": path, "sha256": digest(body), "size": len(body)}
            for path, body in sorted(artifacts.items())
        ],
        "bytes_read": sum(map(len, artifacts.values())) if bytes_read is None else bytes_read,
    }


def fixed_error(codes, operation):
    expected = {codes} if isinstance(codes, str) else set(codes)
    with pytest.raises(evidence._EvidenceFilesError) as caught:
        operation()
    error = caught.value
    assert error.code in expected <= FIXED_CODES
    assert str(error) == error.code
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert PRIVATE_SENTINEL not in rendered
    return error


def read_once(root, kind, key=None):
    with evidence._evidence_files(root) as snapshot:
        return snapshot._read(kind, key)


def json_once(root):
    with evidence._evidence_files(root) as snapshot:
        return snapshot._json_list("metrics")


def bounded_json_depth(depth):
    return ("[" * depth + "0" + "]" * depth).encode("ascii")


def test_private_file_api_schema_and_phase_boundary(tmp_path, monkeypatch):
    tree = ArtifactTree(tmp_path / "api")
    assert tuple(inspect.signature(evidence._evidence_files).parameters) == ("root",)
    def no_constructor_io(*args, **kwargs):
        raise AssertionError("direct construction attempted filesystem I/O")
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.os, "open", no_constructor_io)
        for arguments, options in (((tree.root,), {}), ((), {}),
                                   ((tree.root, object()), {"_permit": object()})):
            fixed_error("CLOSED_SNAPSHOT", lambda: evidence._EvidenceFiles(*arguments, **options))
    context = evidence._evidence_files(tree.root)
    with context as active:
        fixed_error("CLOSED_SNAPSHOT", context.__enter__)
        assert active._metadata() == {"schema_version": 1, "files": [], "bytes_read": 0}
    fixed_error("CLOSED_SNAPSHOT", context.__enter__)
    with evidence._evidence_files(tree.root) as snapshot:
        assert type(snapshot) is evidence._EvidenceFiles
        assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 0}
        assert snapshot._verify() is None
        public = {name for name in vars(type(snapshot)) if not name.startswith("_")}
        assert public == set()
        assert set(snapshot._metadata()) == {"schema_version", "files", "bytes_read"}
        fixed_error("INVALID_KEY", lambda: snapshot._json_list("ledger"))
    for method in (lambda: snapshot._read("metrics"), snapshot._metadata,
                   snapshot._verify, lambda: snapshot._json_list("metrics")):
        fixed_error("CLOSED_SNAPSHOT", method)
    module_tree = ast.parse(Path(evidence.__file__).read_bytes())
    imported = set()
    for node in ast.walk(module_tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "multiprocessing", "pty", "socket", "git"}
    assert not hasattr(evidence, "__all__")


def test_real_artifact_reads_shared_cache_and_metadata(tmp_path):
    tree = ArtifactTree(tmp_path / "families")
    rows = [{"ordinal": 0, "user_auth_security": "\u0641\u0627\u0631\u0633\u06cc"}, None,
            ["preserve", False], "not-proof", 17]
    fixtures = [
        ("ledger", None, LEDGER, b"# ordinary user/auth/security source labels\n"),
        ("metrics", None, METRICS, json.dumps(rows, ensure_ascii=False).encode("utf-8")),
        ("task", "Task-1._", f"{TASKS}/Task-1._.json", b"task-bytes"),
        ("proposal_snapshot", "snapshot", f"{PROPOSALS}/snapshot/task.json", b"snapshot"),
        ("worker_result", "worker", f"{PROPOSALS}/worker/result.json", b"worker"),
        ("reviewer_result", "reviewer", f"{PROPOSALS}/reviewer/result.json", b"reviewer"),
        ("proposal_status", "status", f"{PROPOSALS}/status/status.json", b"proposal-status"),
        ("native", ("native", 0), f"{PIPELINE}/native/pre-review-tests-0.json", b"native-zero"),
        ("native", ("native", 1999), f"{PIPELINE}/native/pre-review-tests-1999.json", b"native-last"),
        ("root_result", ("runs", "run"), f"{AUTOPILOT}/runs/run/result.json", b"root-run"),
        ("root_status", ("runs", "run"), f"{AUTOPILOT}/runs/run/status.json", b"run-status"),
        ("root_result", ("flat", "flat"), f"{AUTOPILOT}/flat/result.json", b"root-flat"),
        ("root_status", ("flat", "flat"), f"{AUTOPILOT}/flat/status.json", b"flat-status"),
        ("receipt", "auth-user_security.json", f"{INVENTORY}/auth-user_security.json", b"receipt"),
    ]
    artifacts = {path: body for _, _, path, body in fixtures}
    for path, body in artifacts.items():
        tree.put(path, body)
    before = {path: (tree.root / path).read_bytes() for path in artifacts}
    with evidence._evidence_files(tree.root) as snapshot:
        for kind, key, _, body in fixtures:
            assert snapshot._read(kind, key) == body
            assert type(snapshot._read(kind, key)) is bytes
        expected = metadata_oracle(artifacts)
        assert snapshot._metadata() == expected
        assert snapshot._json_list("metrics") == rows
        detached = snapshot._json_list("metrics")
        detached[0]["ordinal"] = 999
        detached.append({"new": True})
        changed = snapshot._metadata()
        changed["files"][0]["path"] = "tampered"
        changed["files"].clear()
        assert snapshot._json_list("metrics") == rows
        assert snapshot._metadata() == expected
        assert snapshot._verify() is None
    assert {path: (tree.root / path).read_bytes() for path in artifacts} == before

    # Both aliases really name result.json, but the smaller Reviewer cap still wins.
    tree.put(f"{PROPOSALS}/aliases/result.json", b"x" * 64001)
    with evidence._evidence_files(tree.root) as snapshot:
        assert snapshot._read("worker_result", "aliases") == b"x" * 64001
        fixed_error("BYTE_LIMIT", lambda: snapshot._read("reviewer_result", "aliases"))


def test_byte_count_path_and_strict_json_limits(tmp_path):
    kinds = [
        ("ledger", None, LEDGER, 2000000),
        ("metrics", None, METRICS, 2000000),
        ("receipt", "limit.json", f"{INVENTORY}/limit.json", 2000000),
        ("proposal_snapshot", "cap", f"{PROPOSALS}/cap/task.json", 480000),
        ("task", "cap", f"{TASKS}/cap.json", 160000),
        ("worker_result", "cap", f"{PROPOSALS}/cap/result.json", 160000),
        ("native", ("cap", 0), f"{PIPELINE}/cap/pre-review-tests-0.json", 160000),
        ("reviewer_result", "cap", f"{PROPOSALS}/cap/result.json", 64000),
        ("proposal_status", "cap", f"{PROPOSALS}/cap/status.json", 64000),
        ("root_result", ("flat", "cap"), f"{AUTOPILOT}/cap/result.json", 64000),
        ("root_status", ("runs", "cap"), f"{AUTOPILOT}/runs/cap/status.json", 64000),
    ]
    for number, (kind, key, path, cap) in enumerate(kinds):
        for extra in (0, 1):
            tree = ArtifactTree(tmp_path / f"raw-{number}-{extra}")
            # A non-ASCII fixture catches accidental character rather than byte caps.
            body = ("\u00e9" * (cap // 2)).encode("utf-8") + b"x" * (cap % 2 + extra)
            tree.put(path, body)
            if extra:
                fixed_error("BYTE_LIMIT", lambda: read_once(tree.root, kind, key))
            else:
                assert read_once(tree.root, kind, key) == body

    tree = ArtifactTree(tmp_path / "keys")
    bad_ids = [None, "", ".", "..", ".git", "-lead", "a/b", "a\\b", "/absolute", "C:drive",
               "a\x00b", "a\nb", "a\tb", "\u0641\u0627", "a" * 129, True, 0, ["x"]]
    for key in bad_ids:
        fixed_error({"INVALID_KEY", "UNSAFE_PATH"}, lambda key=key: read_once(tree.root, "task", key))
    for kind, key in [("unknown", None), (False, None), ("ledger", "extra"),
                      ("native", ("ok", True)), ("native", ("ok", -1)),
                      ("native", ("ok", 2000)), ("native", ["ok", 0]),
                      ("native", ("ok", 0, "extra")), ("root_result", ("other", "ok")),
                      ("root_status", ["runs", "ok"]), ("receipt", "../escape.json"),
                      ("receipt", "*wild.json"), ("receipt", "not-json.txt"),
                      ("receipt", "-leading.json")]:
        fixed_error({"INVALID_KEY", "UNSAFE_PATH"}, lambda kind=kind, key=key: read_once(tree.root, kind, key))

    valid_json = [b"[]", b"[null,false,0,\"row\",[],{}]", b"[" + b"0," * 1999 + b"0]",
                  bounded_json_depth(32)]
    invalid_json = [b"{}", b'{"records":[]}', b"[" + b"0," * 2000 + b"0]",
                    b'[{"outer":{"same":1,"same":2}}]', b"[NaN]", b"[Infinity]",
                    b"[-Infinity]", b"[1e999]", b"[\xff]", b'["\\ud800"]', b'[{"\\ud800":1}]',
                    b'["' + PRIVATE_SENTINEL.encode() + b'",]', bounded_json_depth(33),
                    b"[" + b"[" * 2000 + b"0" + b"]" * 2000 + b"]"]
    for index, body in enumerate(valid_json + invalid_json):
        tree = ArtifactTree(tmp_path / f"json-{index}")
        tree.put(METRICS, body)
        if index < len(valid_json):
            assert json_once(tree.root) == json.loads(body.decode("utf-8"))
        else:
            fixed_error("JSON_INVALID", lambda: json_once(tree.root))

    # METRICS alone derives its node ceiling from the unchanged2M wire cap.
    nodes = [[0] * 999998]
    assert 1 + 1 + len(nodes[0]) == 1000000
    compact = json.dumps(nodes, separators=(",", ":")).encode("ascii")
    assert len(compact) == 1999999
    tree = ArtifactTree(tmp_path / "dense-json-nodes")
    tree.put(METRICS, compact + b" ")
    assert json_once(tree.root) == nodes
    tree.put(METRICS, compact + b"  ")
    fixed_error("BYTE_LIMIT", lambda: json_once(tree.root))
    exact_json = b'["' + ("\u00e9" * 999998).encode("utf-8") + b'"]'
    assert len(exact_json) == 2000000
    tree.put(METRICS, exact_json)
    assert json_once(tree.root) == ["\u00e9" * 999998]
    tree.put(METRICS, exact_json[:-2] + b'x"]')
    fixed_error("BYTE_LIMIT", lambda: json_once(tree.root))

    tree = ArtifactTree(tmp_path / "shared-eight-million")
    bulk = {
        LEDGER: b"L" * 2000000,
        METRICS: b"M" * 2000000,
        f"{INVENTORY}/one.json": b"1" * 2000000,
        f"{INVENTORY}/two.json": b"2" * 2000000,
    }
    for path, body in bulk.items():
        tree.put(path, body)
    tree.put(f"{TASKS}/overflow.json", b"x")
    with evidence._evidence_files(tree.root) as snapshot:
        for kind, key in [("ledger", None), ("metrics", None),
                          ("receipt", "one.json"), ("receipt", "two.json")]:
            snapshot._read(kind, key)
        assert snapshot._metadata() == metadata_oracle(bulk, bytes_read=8000000)
        assert snapshot._read("ledger") == bulk[LEDGER]
        assert snapshot._metadata() == metadata_oracle(bulk, bytes_read=8000000)
        fixed_error("BYTE_LIMIT", lambda: snapshot._read("task", "overflow"))

    tree = ArtifactTree(tmp_path / "observation-count")
    with evidence._evidence_files(tree.root) as snapshot:
        for index in range(128):
            fixed_error("MISSING_FILE", lambda index=index: snapshot._read("task", f"missing-{index}"))
        for index in range(128):
            fixed_error("MISSING_FILE", lambda index=index: snapshot._read("task", f"missing-{index}"))
        assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 0}
        fixed_error("COUNT_LIMIT", lambda: snapshot._read("task", "missing-128"))
        assert snapshot._verify() is None

    # Actual <=255-byte receipt basenames reach metadata boundaries without changing caps.
    longest = [f"{index:03d}" + "x" * 247 + ".json" for index in range(117)]
    def charge(name):
        return len(f"{INVENTORY}/{name}".encode()) + len(json.dumps(name, separators=(",", ":")).encode())
    exact_names = longest + ["Z" + "x" * 154 + ".json"]
    plus_names = longest + ["A" + "x" * 62 + ".json", "B" + "x" * 63 + ".json"]
    assert sum(map(charge, exact_names)) == 65536
    assert sum(map(charge, plus_names)) == 65537
    for label, names in (("exact", exact_names), ("plus", plus_names)):
        tree = ArtifactTree(tmp_path / f"metadata-{label}")
        with evidence._evidence_files(tree.root) as snapshot:
            for index, name in enumerate(names):
                expected = "COUNT_LIMIT" if label == "plus" and index == len(names) - 1 else "MISSING_FILE"
                fixed_error(expected, lambda name=name: snapshot._read("receipt", name))
            assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 0}


def test_candidate_utf8_caps_and_unchanged_legacy_boundary():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "scripts/two_site_accepted_work_evidence.py",
        root / "tests/test_two_site_accepted_work_evidence.py",
        root / "tests/test_two_site_accepted_work_evidence_faults.py",
        root / "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md",
    ]
    total_bytes = 0
    for path in paths:
        body = path.read_bytes()
        assert 0 < len(body) <= (25000 if path in (paths[0], paths[3]) else 24000), path.name
        body.decode("utf-8", errors="strict")
        total_bytes += len(body)
    # Original four-file aggregate remains unchanged.
    assert total_bytes <= 96000
    assert not any(hasattr(evidence, name) for name in (
        "summarize_accepted_work", "summarize_work_lineage_ledger", "main", "__all__"))
    empty_costs = {
        "codex": {"observed_record_count": 0, "unknown_record_count": 0,
                  "observed_subtotal": {"input_tokens": 0, "cached_input_tokens": 0,
                                        "uncached_input_tokens": 0, "output_tokens": 0},
                  "full_usage": None},
        "provider": {"observed_record_count": 0, "unknown_record_count": 0,
                     "observed_subtotal": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                     "full_usage": None, "attempt_coverage_record_count": 0,
                     "unknown_attempt_coverage_record_count": 0, "reported_attempt_count": 0,
                     "observed_attempt_count": 0, "unknown_attempt_count": 0},
        "reported_cycle_seconds": {"observed_record_count": 0, "unknown_record_count": 0,
                                   "observed_subtotal": 0.0, "full_total": None},
    }
    assert legacy_report.summarize_work_lineage_ledger([]) == {
        "schema_version": 1, "state": "OBSERVATIONAL_ONLY", "input_record_count": 0,
        "distinct_record_count": 0, "duplicate_record_count": 0, "run_conflict_count": 0,
        "identity_conflict_work_count": 0, "logical_work_count": 0,
        "semantic_attempt_count": 0, "observed_acceptance_candidate_lineage_count": 0,
        "costs": copy.deepcopy(empty_costs), "lineages": [],
        "unattributed": {"record_count": 0, "conflict_record_count": 0,
                         "malformed_record_count": 0, "allocation_rule": "UNATTRIBUTED_NOT_ALLOCATED",
                         "costs": copy.deepcopy(empty_costs)},
        "accepted_behavior_count": None, "denominator_state": "UNVERIFIED",
        "token_per_accepted_behavior": None, "billing_savings": None,
        "value_state": "NOT_ESTABLISHED", "cohort_comparability": "NOT_ESTABLISHED",
    }


def test_deadline_close_detachment_and_sourcefree_errors(tmp_path, monkeypatch):
    from tests.test_two_site_accepted_work_evidence_faults import descriptors, task_witness
    tree = ArtifactTree(tmp_path / "lifecycle")
    tree.put(f"{TASKS}/probe.json", b"abcd")
    tree.put(METRICS, b'[{"nested":[1]}]')
    with monkeypatch.context() as patcher:
        with descriptors(patcher) as (opened, real_open, real_close, real_fstat):
            with evidence._evidence_files(tree.root) as snapshot:
                assert snapshot._read("task", "probe") == b"abcd"
                values = snapshot._json_list("metrics")
                values[0]["nested"].append(2)
                assert snapshot._json_list("metrics") == [{"nested": [1]}]
            assert snapshot._records == {} and snapshot._dirs == {} and snapshot._handles == set()
            assert opened

    # Construction and each operation share the same fixed120s clock, including cache hits/exit.
    for operation in ("construction", "read", "cache", "metadata", "json", "verify", "close"):
        now = [0.0]
        def expired():
            context = evidence._evidence_files(tree.root)
            if operation == "construction":
                now[0] = 120.0
            with context as snapshot:
                if operation == "cache":
                    snapshot._read("task", "probe")
                now[0] = 119.999
                assert snapshot._verify() is None
                now[0] = 120.0
                if operation in ("read", "cache"):
                    snapshot._read("task", "probe")
                elif operation == "metadata":
                    snapshot._metadata()
                elif operation == "json":
                    snapshot._json_list("metrics")
                elif operation == "verify":
                    snapshot._verify()
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.time, "monotonic", lambda: now[0])
            with descriptors(patcher):
                fixed_error("TIME_LIMIT", expired)

    for stage in ("construct-stat", "open-parent", "open-leaf", "read", "verify", "close"):
        with monkeypatch.context() as patcher:
            with descriptors(patcher) as (opened, real_open, real_close, real_fstat):
                real_read, real_stat = os.read, os.stat
                tripped = [False]
                def broken_fstat(descriptor):
                    if stage == "construct-stat":
                        raise OSError(PRIVATE_SENTINEL)
                    return real_fstat(descriptor)
                def broken_open(path, flags, *args, **kwargs):
                    if ((stage == "open-parent" and str(path) == "tasks") or
                            (stage == "open-leaf" and str(path) == "probe.json")):
                        raise PermissionError(PRIVATE_SENTINEL)
                    descriptor = real_open(path, flags, *args, **kwargs)
                    opened.append(descriptor)
                    return descriptor
                def broken_read(descriptor, amount):
                    if stage == "read":
                        raise OSError(PRIVATE_SENTINEL)
                    return real_read(descriptor, amount)
                def broken_stat(path, *args, **kwargs):
                    if stage == "verify" and str(path) == "probe.json":
                        raise PermissionError(PRIVATE_SENTINEL)
                    return real_stat(path, *args, **kwargs)
                def broken_close(descriptor):
                    real_close(descriptor)
                    if stage == "close" and not tripped[0]:
                        tripped[0] = True
                        raise OSError(PRIVATE_SENTINEL)
                patcher.setattr(evidence.os, "open", broken_open)
                patcher.setattr(evidence.os, "read", broken_read)
                patcher.setattr(evidence.os, "stat", broken_stat)
                patcher.setattr(evidence.os, "fstat", broken_fstat)
                patcher.setattr(evidence.os, "close", broken_close)
                expected = "UNSAFE_PATH" if stage in ("construct-stat", "open-parent") else (
                    "CUSTODY_DRIFT" if stage == "verify" else "UNSAFE_FILE")
                fixed_error(expected, lambda: task_witness(tree.root))

    class BodyFailure(RuntimeError):
        pass
    original = BodyFailure(PRIVATE_SENTINEL)
    for close_fails in (False, True):
        with monkeypatch.context() as patcher:
            with descriptors(patcher) as (_, _, real_close, _):
                failed = [False]
                def close_then_raise(descriptor):
                    real_close(descriptor)
                    if close_fails and not failed[0]:
                        failed[0] = True
                        raise OSError(PRIVATE_SENTINEL)
                patcher.setattr(evidence.os, "close", close_then_raise)
                original.__notes__ = []
                with pytest.raises(BodyFailure) as caught:
                    with evidence._evidence_files(tree.root) as snapshot:
                        snapshot._read("task", "probe")
                        raise original
                assert caught.value is original and str(caught.value) == PRIVATE_SENTINEL
                assert original.__notes__ == (["UNSAFE_FILE"] if close_fails else [])
                assert snapshot._records == {} and snapshot._handles == set()

    # The clock is checked inside the read loop, not only at entry/exit.
    tree.put(f"{TASKS}/probe.json", b"x" * 160000)
    now = [0.0]
    real_read = os.read
    def expire_after_first_chunk(descriptor, amount):
        result = real_read(descriptor, amount)
        if result:
            now[0] = 120.0
        return result
    def midread_timeout():
        with evidence._evidence_files(tree.root) as snapshot:
            fixed_error("TIME_LIMIT", lambda: snapshot._read("task", "probe"))
            assert snapshot._bytes_read == 65536
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.time, "monotonic", lambda: now[0])
        patcher.setattr(evidence.os, "read", expire_after_first_chunk)
        with descriptors(patcher):
            fixed_error("TIME_LIMIT", midread_timeout)
